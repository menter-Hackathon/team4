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
import json
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
    tts,
)
from livekit.plugins import silero

from agent.config import Config, load_config
from agent.plant.conversation import PlantConversation
from agent.plant.expression import ExpressionParser
from agent.plant.prompt import FALLBACK_REPLY, NUDGE_INSTRUCTION, WAKE_GREETING_INSTRUCTION
from agent.plant.store import PlantStore
from agent.plant.timeutil import jst_now
from agent.plugins.llm import create_json_completer, create_llm
from agent.plugins.openai_tts import create_tts
from agent.plugins.whisper_stt import create_stt

load_dotenv()
logger = logging.getLogger("futamaru")

# ESP32 の株が入るルームは "plant-<plant_id>"（仕様 §3）
ROOM_PREFIX = "plant-"
# LiveKit Agents の console モード（PC のマイク・スピーカーで試すモード）が使うルーム名
CONSOLE_ROOM = "console-room"
# 無言タイマーを確かめる間隔（秒）。この間隔で conv.tick() を呼ぶ
TICK_SECONDS = 1.0


class FutamaruAgent(Agent):
    """LiveKit の Agent を、ふたまる用に作り替えたもの。

    LiveKit の Agent は「声 → 文字（STT）→ 返事（LLM）→ 声（TTS）」の流れを自動で回す。
    途中のいくつかの場所（〜_node や on_user_turn_completed）を上書きして、ふたまるの動きを足している:
        on_user_turn_completed: 返事する前に、返事すべきか（起きているか）を PlantConversation に聞く
        llm_node:               LLM が落ちても、代わりの返事を返す
        tts_node:               読み上げる前に表情タグを外す
        transcription_node:     画面・ログに出す文字からも表情タグを外す
    """

    def __init__(self, conv: PlantConversation) -> None:
        # システムプロンプト（instructions）は起きるたびに作るので、最初は空にしておく
        super().__init__(instructions="")
        self._conv = conv

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
        decision = await self._conv.on_user_turn(original)
        # 今まさに起きたなら、その株の性格・記憶入りのシステムプロンプトに入れ替える
        if decision.instructions is not None:
            await self.update_instructions(decision.instructions)
            # 今回の返答も新しいプロンプトで作るため、一時コンテキストも差し替える
            turn_ctx.items[:] = self.chat_ctx.copy().items
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
        # 画面・ログ用の文字起こしからもタグを除く（表情は tts_node から送る）
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


def prewarm(proc: JobProcess) -> None:
    """エージェントのプロセスが起動したときに 1 回だけ呼ばれる下準備。"""
    # VAD（声が出ているかを判定するモデル）は読み込みに時間がかかるので、会話が始まる前に読んでおく
    proc.userdata["vad"] = silero.VAD.load()


# LiveKit のエージェントサーバー。cli.run_app(server) で起動し、ルームに呼ばれると entrypoint を動かす
server = AgentServer(setup_fnc=prewarm)


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

    async def send_expression(name: str) -> None:
        """表情を ESP32 に送る（LiveKit の RPC で set_expression を呼ぶ）。#22 でなくなる予定。"""
        await ctx.room.local_participant.perform_rpc(
            destination_identity=device_identity,
            method="set_expression",
            payload=json.dumps({"expression": name}),
        )

    conv = PlantConversation(
        store=store,
        plant_id=plant_id,
        complete_json=create_json_completer(cfg),
        send_expression=send_expression,
        clock=jst_now,
        wake_words=cfg.wake_words,
        sleep_after=cfg.sleep_after,
    )

    # --- 3. 声 → 文字 → 返事 → 声 の流れ（AgentSession）を作る ---
    session = AgentSession(
        stt=create_stt(cfg),
        llm=create_llm(cfg),
        tts=create_tts(cfg),
        vad=ctx.proc.userdata["vad"],  # prewarm で読み込んでおいた VAD
        # 話し終わりは VAD（無音が続いたか）で判定する。
        # preemptive_generation: 話し終わる前に返事を作り始める機能。
        # 返事するかどうかは、話し終えてから on_user_turn_completed で決める作りなので使わない
        turn_handling=TurnHandlingOptions(
            turn_detection="vad", preemptive_generation={"enabled": False}
        ),
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
    agent = FutamaruAgent(conv)

    # --- 4. LiveKit の出来事を、植物のロジックにつなぐ ---
    # @session.on("名前") / @ctx.room.on("名前") を付けた関数は、その出来事が起きるたびに LiveKit から呼ばれる

    # 持ち主が話し始めた・話し終えた
    @session.on("user_state_changed")
    def _on_user_state(ev) -> None:
        conv.on_user_speaking(ev.new_state == "speaking")

    # 植物が考え始めた・話し始めた・聞く状態に戻った
    @session.on("agent_state_changed")
    def _on_agent_state(ev) -> None:
        conv.on_agent_state(ev.new_state)

    # 会話の履歴に 1 つ追加された。植物の発話なら会話ログに記録する
    @session.on("conversation_item_added")
    def _on_item(ev) -> None:
        item = ev.item
        if isinstance(item, ChatMessage) and item.role == "assistant":
            conv.on_plant_utterance(item.text_content or "")

    # STT・LLM・TTS のどこかでエラーが起きた。声が出せないときは眠そうな顔にする
    @session.on("error")
    def _on_error(ev) -> None:
        if isinstance(ev.source, tts.TTS):
            conv.express("sleepy")

    # 誰かがルームに入ってきた。この株の ESP32 なら、眠っている顔を出す
    @ctx.room.on("participant_connected")
    def _on_join(p: rtc.RemoteParticipant) -> None:
        if p.identity == device_identity:
            conv.express("sleepy")

    # 誰かがルームから出たときに LiveKit から呼ばれる。
    # 出たのがこの株の ESP32 なら、セッションを終える（眠らせて振り返り、会話履歴も捨てる。#5）
    @ctx.room.on("participant_disconnected")
    def _on_leave(p: rtc.RemoteParticipant) -> None:
        if p.identity == device_identity:
            # このコールバックは async にできないので、終わるのを待たずにタスクとして走らせる
            asyncio.create_task(agent.end_session())

    async def shutdown() -> None:
        """エージェントが止まるときの後始末。振り返りが終わるのを待ってから DB を閉じる。"""
        await conv.close()
        store.close()

    ctx.add_shutdown_callback(shutdown)

    # --- 5. ルームに参加して会話を始める ---
    await ctx.connect()
    await conv.start()
    await session.start(agent=agent, room=ctx.room)

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
