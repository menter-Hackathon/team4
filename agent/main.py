"""ふたまる 音声エージェントのエントリポイント。PlantConversation を LiveKit に配線する（仕様 §3, §5, §7）。

起動: uv run python -m agent.main console|dev|start
    console: PC のマイク・スピーカーで試す（ESP32 も LiveKit Cloud のルームも不要）
    dev:     LiveKit Cloud につなぎ、ESP32 がルームに入ってくるのを待つ（開発用。コードの変更で再起動）
    start:   dev と同じで本番用

全体の流れ:
    ESP32（植物）が LiveKit のルーム "plant-<id>" に入る
    → LiveKit がこのエージェントを呼び、entrypoint() が動く
    → 持ち主の声を STT で文字に → FutamaruAgent が PlantConversation に判断を聞く
    → LLM が返事を作る → 表情タグを外して TTS で声にする → ESP32 のスピーカーから流れる

植物のロジック（起きる・眠る・記憶・性格）は agent/plant/ にあり、LiveKit に依存しない。
このファイルは、LiveKit の出来事（話した・退出した など）をそのロジックにつなぐ役目だけを持つ。
"""

from __future__ import annotations

import asyncio
import itertools
import logging
from collections.abc import AsyncIterable

from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    APIConnectOptions,
    ChatContext,
    ChatMessage,
    JobContext,
    JobProcess,
    ModelSettings,
    SessionConnectOptions,
    StopResponse,
    TurnHandlingOptions,
    cli,
    llm,
    metrics,
    room_io,
)
from livekit.agents.voice import SpeechHandle
from livekit.plugins import silero

# 言い終わりの判定モデル（#39）。import した時点で LiveKit に登録され、`download-files` でモデルが入る。
# LiveKit は新しい書き方（livekit.agents.inference.TurnDetector）を勧めているが、そちらは LiveKit Cloud の
# 推論を使う。ふたまるは LiveKit も自前なので、PC の中で動くこちら（ONNX のモデル）を使う
from livekit.plugins.turn_detector.multilingual import MultilingualModel

from agent.config import Config, load_config
from agent.plant.conversation import PlantConversation
from agent.plant.dialect import dialect_tts
from agent.plant.expression import ExpressionParser
from agent.plant.prompt import FALLBACK_REPLY, NUDGE_INSTRUCTION, WAKE_GREETING_INSTRUCTION
from agent.plant.store import PlantStore
from agent.plant.timeutil import jst_now
from agent.plant.weather import WeatherCache
from agent.plugins.aivis_tts import create_aivis_tts
from agent.plugins.filler_audio import trim_silence
from agent.plugins.jma_weather import fetch_jma_weather
from agent.plugins.llm import create_json_completer, create_llm
from agent.plugins.openai_tts import create_tts
from agent.plugins.sentence_stream import with_sentence_streaming
from agent.plugins.whisper_stt import create_stt

load_dotenv()
logger = logging.getLogger("futamaru")

# ESP32 の株が入るルームは "plant-<plant_id>"（仕様 §3）
ROOM_PREFIX = "plant-"
# 終了時に振り返りを待つ最大の秒数（#13）。LiveKit の終了の打ち切り（既定 10 秒）より短くする
SHUTDOWN_REFLECTION_TIMEOUT = 8.0
# LiveKit Agents の console モード（PC のマイク・スピーカーで試すモード）が使うルーム名
CONSOLE_ROOM = "console-room"
# 無言タイマーを確かめる間隔（秒）。この間隔で conv.tick() を呼ぶ
TICK_SECONDS = 1.0


class FutamaruAgent(Agent):
    """LiveKit の Agent を、ふたまる用に作り替えたもの。

    LiveKit の Agent は「声 → 文字（STT）→ 返事（LLM）→ 声（TTS）」の流れを自動で回す。
    途中のいくつかの場所（〜_node や on_user_turn_completed）を上書きして、ふたまるの動きを足している:
        on_user_turn_completed: 返事する前に、返事すべきか（起きているか）を PlantConversation に聞く。
                                返事するときは、先につなぎの声（相槌）を鳴らす
        llm_node:               LLM が落ちても、代わりの返事を返す
        tts_node:               読み上げる前に表情タグを外す
        transcription_node:     画面・ログに出す文字からも表情タグを外す
    """

    def __init__(
        self,
        conv: PlantConversation,
        tts_instructions: str = "",
        *,
        preemptive: bool = False,
    ) -> None:
        """引数:
        conv: 植物のロジック
        tts_instructions: TTS の基本の話し方の指示
        preemptive: 先読み（話し終わりが確定する前に返事を作り始める）を、起きている間だけ使うか（#39）
        """
        # システムプロンプト（instructions）は起きるたびに作るので、最初は空にしておく
        super().__init__(instructions="")
        self._conv = conv
        # TTS（声）の基本の話し方の指示。方言が決まったら、これの後ろに方言の指示を足す
        self._tts_instructions = tts_instructions
        self._preemptive = preemptive
        # つなぎの声（相槌）の作り置き。言葉 → 声のフレーム。prepare_fillers() で作る
        self._fillers: dict[str, list[rtc.AudioFrame]] = {}
        self._filler_order: itertools.cycle[str] | None = None
        # この発話の間に、つなぎの声をもう鳴らしたか（1 回の発話に 1 回だけ鳴らす）
        self._filler_played = False
        # 鳴らしている（鳴らした）つなぎの声。文字起こしが届いたら止めるために覚えておく
        self._filler_handle: SpeechHandle | None = None
        self.sync_preemptive()

    def sync_preemptive(self) -> None:
        """先読みを、植物が起きている間だけ有効にする（#39）。起きた・眠ったあとに呼ぶ。

        眠っている間も先読みを有効にすると、周りの会話が文字になるたびに LLM が返事を作り始め、
        GPU を無駄に使って本当の返事を遅らせる（眠っている間は、ウェイクワードが無ければ捨てるだけ）。

        LiveKit（1.8.12）は、先読みの設定を Agent の _turn_handling["preemptive_generation"] から
        毎回読む（voice/agent_activity.py の preemptive_generation_opts）。公開の書き換え方が無いので、
        この辞書を直接書き換えている。LiveKit の版を上げたら、ここが効いているか確かめる。
        preemptive_tts: 先読みのときに、声（TTS）まで作っておく（話し終わりが確定したらすぐ鳴らせる）
        """
        enabled = self._preemptive and self._conv.awake
        current = self._turn_handling.get("preemptive_generation", {}).get("enabled")
        if self._preemptive and current is not None and current != enabled:
            logger.info("preemptive generation %s", "on" if enabled else "off")
        self._turn_handling["preemptive_generation"] = {
            "enabled": enabled,
            "preemptive_tts": True,
        }

    async def prepare_fillers(self, words: tuple[str, ...]) -> None:
        """つなぎの声（例: 「うん」）を、今の TTS で先に作っておく（#39）。

        話し終わった直後に鳴らすので、その場で作ると間に合わない。起動時に 1 回だけ作る。
        作れなかった言葉は飛ばす（つなぎの声が無くても、会話はできる）。
        """
        for word in words:
            try:
                frames: list[rtc.AudioFrame] = []
                async with self.session.tts.synthesize(word) as stream:
                    async for audio in stream:
                        frames.append(audio.frame)
                # 前後の無音を切る（長いと、そのぶん本当の返事が待たされる。filler_audio.py）
                frames = trim_silence(frames)
                if frames:
                    self._fillers[word] = frames
            except Exception:
                logger.exception("could not prepare filler %r", word)
        if self._fillers:
            self._filler_order = itertools.cycle(list(self._fillers))
            logger.info("fillers ready: %s", ", ".join(self._fillers))

    def _play_filler(self) -> None:
        """作り置きのつなぎの声を 1 つ鳴らす。会話の履歴には残さない（LLM に「うん」を覚えさせないため）。

        1 回の発話につき 1 回だけ鳴らす（on_user_turn_completed の終わりで数え直す）。
        鳴らしたあとに、本当の返事が続く（LiveKit は声を順番に鳴らす）。
        持ち主がまた話し始めたら、つなぎの声は途中で止まる（allow_interruptions）。
        """
        if self._filler_order is None or self._filler_played:
            return
        self._filler_played = True
        word = next(self._filler_order)
        frames = self._fillers[word]

        async def audio() -> AsyncIterable[rtc.AudioFrame]:
            for frame in frames:
                yield frame

        self._filler_handle = self.session.say(
            word, audio=audio(), add_to_chat_ctx=False, allow_interruptions=True
        )

    def on_user_transcribed(self) -> None:
        """持ち主の発話が文字になった（確定した）ときに呼ばれる。まだ鳴っているつなぎの声を止める（#39）。

        LiveKit（1.8.12）は、何かを喋っている最中は先読みを始めない
        （voice/agent_activity.py の on_preemptive_generation）。文字起こしが届いたときに
        つなぎの声がまだ鳴っていると、先読みが始まらず、本当の返事が 0.5 秒ほど遅れた（2026-11-19 の ohsawa）。
        LiveKit は「文字起こしが届いた」出来事を出してから先読みを始めるかを決めるので、
        ここで止めれば先読みが始まれる。止めると「止まった」扱いになるのは、その場ですぐ。
        """
        handle = self._filler_handle
        if handle is not None and not handle.done():
            handle.interrupt()
        self._filler_handle = None

    def on_user_paused(self) -> None:
        """持ち主の声が途切れた瞬間（VAD）に呼ばれる。起きていれば、すぐにつなぎの声を鳴らす（#39）。

        話し終わりの確定（言い終わりの判定・文字起こし）を待つと約 0.9 秒かかるが、
        声が途切れた瞬間なら約 0.2 秒で鳴らせる。日本語の相槌と同じく、間で「うん」と返す形になる。
        まだ文字が無いので、眠っているとき（ウェイクワードかわからない）は鳴らさない。
        つなぎの声は短い（約 0.3 秒）ので、文字起こしが届く前に鳴り終わり、先読みの邪魔もしない。
        """
        if self._conv.awake:
            self._play_filler()

    def _apply_dialect(self, dialect: str) -> None:
        """話し相手の方言に合わせて、TTS の話し方の指示を差し替える。

        標準語（dialect_tts が None）のときは基本の指示だけに戻す。
        前の相手の方言が残らないようにするためで、必ず毎回まるごと渡し直す。
        TTS が無い・指示を替えられない種類のとき（テストなど）は、何もしない。
        """
        tts_obj = self.session.tts
        update_options = getattr(tts_obj, "update_options", None)
        if update_options is None:
            return
        extra = dialect_tts(dialect)
        instructions = f"{self._tts_instructions} {extra}" if extra else self._tts_instructions
        update_options(instructions=instructions)

    async def end_session(self) -> None:
        """ESP32 が退出したときの後始末（#5）。entrypoint の _on_leave から呼ばれる。

        1. 会話を閉じる。起きていれば眠らせ、そのセッションの振り返り（記憶・性格の更新）を走らせる
        2. LLM に渡す会話履歴（チャットコンテキスト）を空にする。黙って眠ったときはティッカーが空にするが、
           途中で切断したときはここで空にしないと、次に起きたとき前の会話が混ざる
        """
        await self._conv.close()
        await self.update_chat_ctx(ChatContext.empty())

    async def on_user_turn_completed(self, turn_ctx: ChatContext, new_message: ChatMessage) -> None:
        """持ち主が話し終えたとき、LLM が返事を作る前に LiveKit から呼ばれる。

        PlantConversation.on_user_turn の判断（ignore / greet / reply）に合わせて:
            ignore: 返事を作らせない（StopResponse を投げると、LiveKit は返事を作らずに止まる）
            greet:  植物から話しかける返事を別に作らせ、今回の発話への返事は止める
            reply:  そのまま返事させる。発話を書き換えた（「ふたまる、」を外したなど）なら差し替える
        """
        original = new_message.text_content or ""
        try:
            await self._handle_user_turn(turn_ctx, new_message, original)
        finally:
            # 次の発話では、またつなぎの声を鳴らせるようにする
            self._filler_played = False

    async def _handle_user_turn(
        self, turn_ctx: ChatContext, new_message: ChatMessage, original: str
    ) -> None:
        """on_user_turn_completed の中身（説明はそちら）。"""
        # 植物が応じそうなら、判断（起きるときは天気・名前探しで時間がかかる）を待たずに、
        # すぐにつなぎの声を鳴らす（#39）。声が途切れた瞬間（on_user_paused）に鳴らしていれば鳴らさない。
        # 応じない発話（眠っていてウェイクワードが無い）では鳴らさない
        if self._conv.will_respond(original):
            self._play_filler()
        decision = await self._conv.on_user_turn(original)
        # 起きた（または起きている）なら、次の発話から先読みを使う
        self.sync_preemptive()
        # 今まさに起きたなら、その株の性格・記憶入りのシステムプロンプトに入れ替える
        if decision.instructions is not None:
            await self.update_instructions(decision.instructions)
            # 今回の返答も新しいプロンプトで作るため、一時コンテキストも差し替える
            turn_ctx.items[:] = self.chat_ctx.copy().items
        # 話し相手（の方言）が決まったときだけ、声の出し方も方言に合わせる
        if decision.dialect is not None:
            self._apply_dialect(decision.dialect)
        if decision.kind == "ignore":
            raise StopResponse()  # ユーザー発話は履歴にも残らない
        if decision.kind == "greet":
            self.session.generate_reply(
                chat_ctx=direction_ctx(self.chat_ctx, WAKE_GREETING_INSTRUCTION)
            )
            raise StopResponse()
        # 「ふたまる、今日ね…」→「今日ね…」のように、LLM に渡す発話からウェイクワードを外す
        if decision.text != original:
            new_message.content = [decision.text]

    async def llm_node(
        self, chat_ctx: ChatContext, tools: list[llm.FunctionTool], model_settings: ModelSettings
    ) -> AsyncIterable[llm.ChatChunk | str]:
        """LLM に返事を作らせる。返事は少しずつ（chunk ごとに）届くので、届いた分から次へ流す。

        LLM が落ちている・時間切れのときは、まだ何も返していなければ FALLBACK_REPLY
        （「ん…ちょっとぼーっとしてた」）を代わりに返し、植物が黙り込まないようにする。
        """
        # 表情タグはここでは消さない。履歴にタグを残し、LLM がタグを付け続けるようにする
        emitted = False
        try:
            # Agent.default.llm_node: LiveKit 標準の処理（実際に LLM を呼ぶ）
            async for chunk in Agent.default.llm_node(self, chat_ctx, tools, model_settings):
                # 中身のある文字が 1 つでも届いたら、途中で落ちても代わりの返事は出さない（話が混ざるため）
                if isinstance(chunk, str) or (
                    isinstance(chunk, llm.ChatChunk) and chunk.delta and chunk.delta.content
                ):
                    emitted = True
                yield chunk
        except Exception:
            logger.exception("LLM error")
            if not emitted:
                yield FALLBACK_REPLY

    async def tts_node(
        self, text: AsyncIterable[str], model_settings: ModelSettings
    ) -> AsyncIterable[rtc.AudioFrame]:
        """返事の文字を声にする。その前に表情タグを外し、表情が決まったら PlantConversation に知らせる。"""

        async def cleaned() -> AsyncIterable[str]:
            """届いた文字から表情タグを外したものだけを、TTS に流す。"""
            parser = ExpressionParser()
            async for delta in text:
                parsed = parser.feed(delta)
                if parsed.expression is not None:
                    self._conv.express(parsed.expression)
                if parsed.text:
                    yield parsed.text
            # 返事が最後まで届いた。ためていた残りを出す
            tail = parser.flush()
            if tail.expression is not None:
                self._conv.express(tail.expression)
            if tail.text:
                yield tail.text

        # LiveKit 標準の TTS 処理に、タグを外した文字を渡す。できた音声（frame）をそのまま流す
        async for frame in Agent.default.tts_node(self, cleaned(), model_settings):
            yield frame

    async def transcription_node(
        self, text: AsyncIterable[str], model_settings: ModelSettings
    ) -> AsyncIterable[str]:
        # 画面・ログ用の文字起こしからもタグを除く（表情のログは tts_node で出す）
        parser = ExpressionParser()
        async for delta in text:
            out = parser.feed(delta).text
            if out:
                yield out
        tail = parser.flush().text
        if tail:
            yield tail


def direction_ctx(chat_ctx: ChatContext, direction: str) -> ChatContext:
    """挨拶・話題振り用に、ト書きをユーザー発話として足したコンテキストのコピーを作る。

    system メッセージを後ろに重ねると Gemma のチャットテンプレートが受け付けない場合があるため、
    ト書きは user ロールで渡す。元の履歴（chat_ctx）は変更しない。
    """
    ctx = chat_ctx.copy()
    ctx.add_message(role="user", content=f"（{direction}）")
    return ctx


def turn_handling(cfg: Config) -> TurnHandlingOptions:
    """話し終わりの判定と、返事の始め方の設定を作る（#39）。

    - turn_detection: 話し終わりの判定のしかた
        "vad": 無音が続いたかだけで判定する（TURN_DETECTOR=false、既定）
        MultilingualModel: 文字になった発話から「言い終わったか」も判定する（TURN_DETECTOR=true）。
          言い終わっていそうなら endpointing.min_delay 秒、途中なら最長 endpointing.max_delay 秒待つ。
          無音の待ち（VAD_MIN_SILENCE_SECONDS）を短くしても、話の途中の間で割り込みにくい
    - endpointing.min_delay / max_delay: 話し終わりと判定してから返事を作り始めるまでの待ち（.env で変える）
    - preemptive_generation: 話し終わる前に返事を作り始める先読み。ここでは切っておき、
      FutamaruAgent.sync_preemptive() が、起きている間だけ有効にする（PREEMPTIVE_GENERATION=true のとき）

    例: ENDPOINTING_DELAY_SECONDS=0.2 → {"turn_detection": "vad", "endpointing": {"min_delay": 0.2, ...}, ...}
    """
    return TurnHandlingOptions(
        turn_detection=MultilingualModel() if cfg.turn_detector else "vad",
        endpointing={"min_delay": cfg.endpointing_delay, "max_delay": cfg.endpointing_max_delay},
        preemptive_generation={"enabled": False},
    )


def room_options() -> room_io.RoomOptions:
    """ルームでの音声のやりとりの設定を作る。

    close_on_disconnect=False: ESP32 がルームを出ても、エージェントのセッションを閉じない。
    LiveKit の既定（True）だと、ESP32 が出た時点でセッションが閉じる。一方でエージェント自身は、
    ルームが空になるまで（約 20 秒）ルームに残る。その間に ESP32 が入り直すと、新しいエージェントは
    呼ばれず、閉じたセッションのまま何も聞かない（2026-11-17 の通し試験で、5 分以上黙ったままだった）。
    ESP32 が出たときの後始末（眠らせる・振り返り・会話履歴を空にする）は、_on_leave が行う（#5）。
    """
    return room_io.RoomOptions(close_on_disconnect=False)


def prewarm(proc: JobProcess) -> None:
    """エージェントのプロセスが起動したときに 1 回だけ呼ばれる下準備。"""
    # VAD（声が出ているかを判定するモデル）は読み込みに時間がかかるので、会話が始まる前に読んでおく
    proc.userdata["vad"] = silero.VAD.load()


# LiveKit のエージェントサーバー。cli.run_app(server) で起動し、ルームに呼ばれると entrypoint を動かす
server = AgentServer(setup_fnc=prewarm)


def create_weather_cache(cfg: Config) -> WeatherCache | None:
    """天気を取って覚えておく WeatherCache を作る（仕様 §6.7、#80）。

    .env に観測所（JMA_AMEDAS_STATION）も地域（JMA_FORECAST_AREA）も無ければ None を返す。
    None のとき、植物は天気の話をしない（PlantConversation に weather=None が渡る）。
    """
    station, area = cfg.jma_amedas_station, cfg.jma_forecast_area
    if station is None and area is None:
        return None

    # WeatherCache は「引数なしで呼ぶと天気が返る関数」を受け取る（weather.py）。
    # fetch_jma_weather は観測所と地域を引数に取るので、それを決めておいた小さな関数で包んで渡す。
    # こうしておくと、weather.py は気象庁のことを何も知らずに済む（テストで偽物に替えやすい）
    async def fetch():
        return await fetch_jma_weather(station, area)

    return WeatherCache(fetch, jst_now)


def resolve_plant_id(room_name: str, cfg: Config) -> str | None:
    """ルーム名から、どの株（plant_id）として会話するかを決める。None なら参加せず退出する。

    例:
        "plant-a1"                       → "a1"（ESP32 の株。本番・dev モード）
        "console-room"＋DEV_PLANT_ID=dev → "dev"（PC のマイクで試す console モード）
        "other-room"                     → None（DEV_PLANT_ID があっても退出。#11）
    """
    # 1. ESP32 の株: ルーム名が "plant-<id>" なら、"plant-" を外した残りが plant_id
    #    （"plant-" だけで id が空のときは株ではないので 2 以降へ）
    if room_name.startswith(ROOM_PREFIX) and len(room_name) > len(ROOM_PREFIX):
        return room_name[len(ROOM_PREFIX) :]
    # 2. console モード: ESP32 が無いので、.env の DEV_PLANT_ID を株として使う（未設定なら None）
    if room_name == CONSOLE_ROOM:
        return cfg.dev_plant_id
    # 3. それ以外のルーム: DEV_PLANT_ID があっても使わず退出する（仕様 §3・§10、#11）。
    #    dev / start モードで関係ないルームに呼ばれたとき、開発用の株として話し出さないように
    return None


@server.rtc_session()
async def entrypoint(ctx: JobContext) -> None:
    """ルームに呼ばれたときに LiveKit が動かす本体。1 つのルーム（＝1 株）につき 1 回動く。

    手順:
        1. ルーム名から株を決める（株のルームでなければ退出）
        2. DB と PlantConversation（植物のロジック）を用意する
        3. STT・LLM・TTS をつないだ AgentSession を作る
        4. LiveKit の出来事を PlantConversation につなぐ
        5. ルームに参加して会話を始める
        6. 1 秒ごとに無言タイマーを確かめる（眠る・話題を振る）
    """
    # --- 1. どの株として会話するか ---
    cfg = load_config()
    plant_id = resolve_plant_id(ctx.job.room.name, cfg)
    if plant_id is None:
        logger.info("room %s is not a plant room; leaving", ctx.job.room.name)
        return

    # --- 2. DB と植物のロジックを用意する ---
    device_identity = f"device-{plant_id}"  # この株の ESP32 がルームで名乗る名前（仕様 §3）
    store = PlantStore(cfg.db_path)

    conv = PlantConversation(
        store=store,
        plant_id=plant_id,
        complete_json=create_json_completer(cfg),
        clock=jst_now,
        wake_words=cfg.wake_words,
        sleep_after=cfg.sleep_after,
        weather=create_weather_cache(cfg),
        dry_humidity=cfg.weather_dry_humidity,
        water_remind_days=cfg.water_remind_days,
        species=cfg.plant_species,
        allow_new_people=cfg.allow_new_people,
    )

    # --- 3. 声 → 文字 → 返事 → 声 の流れ（AgentSession）を作る ---
    # 話し終わりと判定するまでの無音の長さを .env の値にする（#39）。
    # VAD は prewarm で読み込み済みなので、ここで設定だけ変える
    vad = ctx.proc.userdata["vad"]
    vad.update_options(min_silence_duration=cfg.vad_min_silence)
    session = AgentSession(
        stt=create_stt(cfg),
        llm=create_llm(cfg),
        # TTS は .env の TTS_PROVIDER で選ぶ。aivis ならローカルの AivisSpeech（仕様 §8.3、#56）
        # 返事を 1 文ずつ声にする（LLM が書き終わるのを待たない。返事の遅さ #39、sentence_stream.py）
        tts=with_sentence_streaming(
            create_aivis_tts(cfg) if cfg.tts_provider == "aivis" else create_tts(cfg)
        ),
        vad=vad,  # prewarm で読み込んでおいた VAD
        turn_handling=turn_handling(cfg),  # 話し終わりの判定（turn_handling の説明を参照）
        user_away_timeout=None,  # 黙っているときの扱いは、こちらの無言タイマーで行うので LiveKit 側は切る
        conn_options=SessionConnectOptions(
            # リトライしない（タイムアウトが何倍にもならないように）。STT/TTS の読み取り待ちは
            # プラグイン側で 30 秒固定、ここでの timeout は接続待ちに効く
            stt_conn_options=APIConnectOptions(max_retry=0, timeout=cfg.stt_timeout),
            llm_conn_options=APIConnectOptions(max_retry=0, timeout=cfg.llm_timeout),
            tts_conn_options=APIConnectOptions(max_retry=0, timeout=cfg.tts_timeout),
            # GPU サーバーが一時的に落ちても植物を終了させない
            max_unrecoverable_errors=1_000_000,
        ),
    )
    agent = FutamaruAgent(conv, cfg.tts_instructions, preemptive=cfg.preemptive_generation)

    # --- 4. LiveKit の出来事を、植物のロジックにつなぐ ---
    # @session.on("名前") / @ctx.room.on("名前") を付けた関数は、その出来事が起きるたびに LiveKit から呼ばれる

    # 段階ごとの時間（話し終わりの判定・STT・LLM の最初の文字・声の最初の音）をログに出す（#39）。
    # 返事の遅さを、変更の前後で比べるため
    @session.on("metrics_collected")
    def _on_metrics(ev) -> None:
        metrics.log_metrics(ev.metrics)

    # 持ち主の発話が文字になった。確定した文字なら、まだ鳴っているつなぎの声を止めて先読みを通す（#39）
    @session.on("user_input_transcribed")
    def _on_transcribed(ev) -> None:
        if ev.is_final:
            agent.on_user_transcribed()

    # 持ち主が話し始めた・話し終えた
    @session.on("user_state_changed")
    def _on_user_state(ev) -> None:
        conv.on_user_speaking(ev.new_state == "speaking")
        # 声が途切れた瞬間に、つなぎの声を鳴らす（#39。起きているときだけ）
        if ev.old_state == "speaking" and ev.new_state == "listening":
            agent.on_user_paused()

    # 植物が考え始めた・話し始めた・聞く状態に戻った
    @session.on("agent_state_changed")
    def _on_agent_state(ev) -> None:
        conv.on_agent_state(ev.new_state)

    # 会話の履歴に 1 つ追加された。植物の発話なら会話ログに記録する。
    #
    # TTS（声を作る処理）が失敗した返事について（#12）:
    #   ここで特別な処理はしなくてよい。LiveKit（1.8.12）が、実際に再生できた文字だけを履歴に入れるため。
    #   - 1 文字も声にならなかった → 履歴に何も追加されず、この関数は呼ばれない（会話ログに入らない）
    #   - 途中まで声になって失敗した → 再生できたところまでの文字が、interrupted=True の印付きで届く。
    #     持ち主には実際に聞こえた部分なので、そのまま記録してよい
    #   （LiveKit の voice/generation.py の _ForwardOutput.forwarded_text と、
    #     voice/agent_activity.py で assistant のメッセージを足すところで確認した）
    #   「TTS エラーの直後の返事を飛ばす」ような処理を足してはいけない。失敗した返事は履歴に来ないので、
    #   飛ばされるのは次の正常な返事になってしまう
    @session.on("conversation_item_added")
    def _on_item(ev) -> None:
        item = ev.item
        if isinstance(item, ChatMessage) and item.role == "assistant":
            conv.on_plant_utterance(item.text_content or "")

    # 誰かがルームから出たときに LiveKit から呼ばれる。
    # 出たのがこの株の ESP32 なら、セッションを終える（眠らせて振り返り、会話履歴も捨てる。#5）
    @ctx.room.on("participant_disconnected")
    def _on_leave(p: rtc.RemoteParticipant) -> None:
        if p.identity == device_identity:
            # このコールバックは async にできないので、終わるのを待たずにタスクとして走らせる
            asyncio.create_task(agent.end_session())

    async def shutdown() -> None:
        """エージェントが止まるときの後始末。振り返りを少し待ってから DB を閉じる（#13）。

        LiveKit は終了処理を 10 秒（shutdown_process_timeout の既定）で打ち切るので、
        振り返りは SHUTDOWN_REFLECTION_TIMEOUT 秒までしか待たず、必ず store.close() まで進める。
        """
        await conv.shutdown(SHUTDOWN_REFLECTION_TIMEOUT)
        store.close()

    ctx.add_shutdown_callback(shutdown)

    # --- 5. ルームに参加して会話を始める ---
    await ctx.connect()
    await conv.start()
    await session.start(agent=agent, room=ctx.room, room_options=room_options())
    # つなぎの声を作っておく（#39）。待たずに裏で作り、できるまでの返事はつなぎの声なしで進める
    if cfg.filler_words:
        filler_task = asyncio.create_task(agent.prepare_fillers(cfg.filler_words))
        ctx.add_shutdown_callback(lambda: _cancel(filler_task))

    # --- 6. 1 秒ごとに無言タイマーを確かめる ---
    async def ticker() -> None:
        """エージェントが止まるまで、1 秒ごとに conv.tick() を呼び続ける。"""
        while True:
            await asyncio.sleep(TICK_SECONDS)
            try:
                result = await conv.tick()
                if result == "nudge":
                    # 話題を振る。指示（ト書き）を足したコピーで返事を作らせ、元の履歴には残さない
                    session.generate_reply(
                        chat_ctx=direction_ctx(agent.chat_ctx, NUDGE_INSTRUCTION)
                    )
                elif result == "slept":
                    await agent.update_chat_ctx(ChatContext.empty())  # 眠ったら会話履歴を捨てる
                    agent.sync_preemptive()  # 眠ったので、先読みを止める
            except Exception:
                # 1 回失敗してもタイマーは止めない（止めると植物が眠らなくなる）
                logger.exception("tick failed")

    # 裏で動かし続け、エージェントが止まるときに一緒に止める
    ticker_task = asyncio.create_task(ticker())
    ctx.add_shutdown_callback(lambda: _cancel(ticker_task))


async def _cancel(task: asyncio.Task) -> None:
    """タスクを止める（終了時の後始末用）。"""
    task.cancel()


# `python -m agent.main console` などで直接起動されたときだけ動く（テストで import したときは動かない）
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    cli.run_app(server)
