"""1株ぶんの会話の進行: 起床・記録・睡眠・reflection の起動・表情のログ（仕様 §5, §7）。

LiveKit には依存しない。main.py が LiveKit のイベントをこのクラスのメソッドに配線する。

1 回の会話（セッション）の流れ:
    1. 眠っている。ユーザーの発話はウェイクワードが無ければ無視する（on_user_turn）
    2. 「ふたまる」で起きる。DB に新しいセッションを作り、その株のシステムプロンプトを作る（_wake）。
       この時点では相手が誰か分からないので、プロンプトには「名前を聞く」指示が入る
    3. 会話する。発話は 1 つずつ DB に記録する（_record）。
       相手が決まるまでは、発話ごとに名前探し（people.identify）をする（_identify_turn）。
       決まったらその人のプロンプト（性格・記憶・要約・方言・名前）に替える。
       3 回話しても名乗らなければ「お客さん」として扱い、名前探しをやめる
    4. しばらく誰も話さないと眠る（tick → _sleep）
    5. 眠ったら、その会話を振り返って記憶と性格を更新する（reflection.py。裏で実行）

LiveKit に依存させないのは、偽物の時計・偽物の LLM を渡してテストで動かせるようにするため。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from .dialect import resolve_dialect
from .expression import strip_tags
from .people import clean_name, identify
from .personality import nudge_delay
from .prompt import build_system_prompt, plant_status_text
from .reflection import JsonCompleter, reflect_session
from .store import Person, PlantStore
from .timeutil import Clock
from .wake import WakeController, detect_wake, normalize
from .weather import WeatherCache, describe_weather

logger = logging.getLogger(__name__)

# 起動時に再実行する未処理 reflection の上限。LLM が落ちていても起動を何分も止めないため
RECOVERY_LIMIT = 3
# 名前を聞くのはセッションの最初の何回のユーザー発話までか。これでも名乗らなければお客さんにする
MAX_NAME_TURNS = 3
# 名前探しの LLM を待つ最大の秒数。これを超えたら「名乗っていない」扱いにして返事を優先する
IDENTIFY_TIMEOUT = 3.0


@dataclass
class TurnDecision:
    """on_user_turn() の結果。main.py はこれを見て、返事する・挨拶する・何もしない を決める。"""

    kind: Literal["ignore", "reply", "greet"]
    text: str = ""  # reply のとき LLM に渡すユーザー発話
    # 新しいシステムプロンプト。起きたとき・相手が決まったとき・お客さんに決まったときだけ入る
    instructions: str | None = None
    # 話し相手の方言（例: "kansai"）。起きたとき（未確定なので "standard"）・相手が決まった・
    # お客さんに決まったときだけ入る。main.py が TTS（声の出し方）の指示を替えるのに使う
    dialect: str | None = None


class PlantConversation:
    """1 株ぶんの会話を進める。株（plant_id）ごとに 1 つ作る。

    引数（すべてキーワード指定）:
        store: 会話ログ・記憶・性格を保存する DB
        plant_id: どの株か（例: "a1"）
        complete_json: 振り返りで LLM に JSON を作らせる関数
        clock: 現在時刻を返す関数（テストでは偽物の時計）
        wake_words: ウェイクワードの一覧（例: ("ふたまる", "二丸")）
        sleep_after: 何秒だれも話さなければ眠るか
        weather: 天気を取って覚えておく WeatherCache。None なら天気を使わない（§6.7、#80）
        dry_humidity: 湿度がこの値（%）未満なら「乾燥している」と言う
    """

    def __init__(
        self,
        *,
        store: PlantStore,
        plant_id: str,
        complete_json: JsonCompleter,
        clock: Clock,
        wake_words: Sequence[str],
        sleep_after: float,
        weather: WeatherCache | None = None,
        dry_humidity: float = 40.0,
    ) -> None:
        self._store = store
        self._plant_id = plant_id
        self._complete_json = complete_json
        self._clock = clock
        self._wake_words = tuple(wake_words)
        self._weather = weather
        self._dry_humidity = dry_humidity
        self._controller = WakeController(
            sleep_after=sleep_after
        )  # 起きている/眠っている状態と無言タイマー
        self._session_id: int | None = None  # 今のセッションの DB 上の id。眠っている間は None
        # 裏で動いている振り返りのタスク。終了時に待つために覚えておく
        self._reflections: set[asyncio.Task] = set()
        # 振り返りを 1 つずつ順番に走らせるための鍵（#9）。2 つが同時に走ると、どちらも
        # 「既存の記憶」を読んでから書くので、同じ記憶が 2 件入ることがあるため
        self._reflect_lock = asyncio.Lock()
        # エージェントの終了処理に入ったか（#13）。入ったら、新しく起きない
        self._shutting_down = False
        # --- 今のセッションの話し相手（眠ったら _forget_person で消す） ---
        self._person: Person | None = None  # 決まった相手。未確定・お客さんの間は None
        # このセッションで名前探しができたユーザー発話の数（挨拶だけの起床と、名前探しが時間切れの回は数えない）
        self._user_turns = 0
        self._guest = False  # お客さんに決まったか。決まったら名前探しをやめる
        # 植物が最後に言ったこと（表情タグなし）。次のユーザー発話の名前探しに渡し、渡したら消す。
        # 「お名前は？」→「はなこ」のような、名前だけの答えを見分けやすくするため（最終レビュー I2）
        self._last_plant_utterance: str | None = None

    @property
    def awake(self) -> bool:
        """起きているか。"""
        return self._controller.state == "AWAKE"

    async def start(self) -> None:
        """エージェントが株のルームに入ったときに 1 回呼ぶ準備の処理。"""
        # 初めての株なら、初期の性格（全部 0.5）で DB に登録する
        self._store.get_or_create_plant(self._plant_id, self._clock())
        # 対象のセッションはここで確定し（起動後に終わるセッションを二重に処理しない）、
        # reflection 自体は裏で行ってウェイクワードの受付を待たせない（§6.3 手順8）
        self._store.repair_unfinished_sessions(self._plant_id)
        pending = self._store.unreflected_sessions(self._plant_id)[-RECOVERY_LIMIT:]
        if pending:
            self._track(self._reflect_all(pending, self._clock()))

    # --- ユーザー・植物の発話 ---

    async def on_user_turn(self, text: str) -> TurnDecision:
        """ユーザーが話し終えたときに呼ばれ、植物がどう応じるかを決める（仕様 §5）。

        引数:
            text: STT が文字起こししたユーザーの発話
        戻り値:
            TurnDecision。kind が
            - "ignore": 何もしない（眠っていてウェイクワードが無い、中身の無い発話など）
            - "greet":  ウェイクワードだけで起こされた → 植物から話しかける
            - "reply":  発話（text）に返事する

        例（眠っているとき）:
            "おはよう"                   → ignore（ウェイクワードが無い）
            "ふたまる"                   → greet
            "ふたまる、今日カレー食べた" → reply（text = "今日カレー食べた"）
        例（起きているとき）:
            "ただいま" → reply（text = "ただいま"）
            "。"       → ignore（#10）
        """
        text = text.strip()

        # 0. エージェントの終了処理中は、何を言われても起きない・返事しない（#13）。
        #    起きると新しいセッションと振り返りが始まり、閉じかけの DB に書いてしまうため
        if self._shutting_down:
            return TurnDecision("ignore")

        # 1. 中身の無い発話を捨てる。
        #    STT は雑音を「。」「…」などと文字起こしすることがある。normalize() は空白・句読点・記号を
        #    消すので、消して何も残らなければ中身の無い発話として、返事も記録もしない（#10）
        if not normalize(text):
            return TurnDecision("ignore")

        # 2. 眠っているとき: ウェイクワードがあるときだけ起きる
        if not self.awake:
            match = detect_wake(text, self._wake_words)
            if match is None:
                return TurnDecision("ignore")  # ウェイクワードが無い → 寝たまま
            # 起きる前に天気を取り直す（30 分以内に取っていれば何もしない、#80）。
            # 失敗・2 秒のタイムアウトでも例外は出ず、天気なしで続ける
            if self._weather is not None:
                await self._weather.refresh()
            # 起きる。新しいセッションを始め、「名前を聞く」入りのシステムプロンプトを受け取る
            instructions = self._wake()
            if match.rest:
                # 「ふたまる、今日カレー食べた」のように続きがある → その続きに返事する
                self._record("user", match.rest)
                # 続きの中で名乗っていれば（例:「ふたまる、たろうだよ」）、
                # 起きたときのプロンプトではなく、その人のプロンプトを 1 つだけ返す
                decided = await self._identify_turn(match.rest)
                if decided is not None:
                    return TurnDecision("reply", match.rest, decided[0], decided[1])
                # 名乗っていない → 相手は未確定なので、声は標準語に戻す（下の greet と同じ理由）
                return TurnDecision("reply", match.rest, instructions, "standard")
            # 「ふたまる」だけ → 植物から話しかける（挨拶なのでユーザー発話は記録しない）。
            # 名乗る言葉が無いので名前探しもしない。挨拶のプロンプトには名前を聞く指示が入っている。
            # dialect="standard" を返すのは、前のセッションの相手の方言（例: 関西弁）が
            # TTS の指示に残ったまま挨拶しないようにするため（最終レビュー I1）
            return TurnDecision("greet", instructions=instructions, dialect="standard")

        # 3. 起きているとき: 先頭の呼びかけ（「ふたまる、」）を外す（#68）。
        #    眠っているときに起こす発話（上の match.rest）と同じく、外した文で返事・記録・名前探しをする。
        #    元の「ふたまる太郎だよ。」のまま会話の LLM に渡すと、名前を「太郎」と登録していても、
        #    植物が「ふたまる太郎」と呼んでしまうため
        content = self._without_leading_wake_word(text)
        # 4. 発話を記録し、無言タイマーをリセットする
        self._record("user", content if content is not None else text)
        self._controller.on_user_activity(self._clock())
        if content is None:
            # 「ふたまる」とだけ呼ばれた → 返事はするが、名乗りではないので名前探しはしない
            # （名前を聞く 3 回にも数えない）
            return TurnDecision("reply", text)
        # 5. 相手が未確定なら名前探しをする。決まったらプロンプトと方言を替えて返事する
        decided = await self._identify_turn(content)
        if decided is not None:
            return TurnDecision("reply", content, decided[0], decided[1])
        return TurnDecision("reply", content)

    def _without_leading_wake_word(self, text: str) -> str | None:
        """起きている間の発話から、先頭の呼びかけ（ウェイクワードと敬称）を外す（#68）。

        起きている間に「ふたまる、たろうだよ」と言うと、STT が読点を落として「ふたまる太郎だよ。」と
        書くことがある。そのまま名前探しに渡すと、LLM が「ふたまる太郎」を名前だと思って登録してしまう。

        戻り値:
            先頭に呼びかけが無ければ text のまま。呼びかけの後ろに続きがあれば、その続き。
            呼びかけだけ（「ふたまる」「ふたまるちゃん」）なら None。

        例: "ふたまる太郎だよ。" → "太郎だよ。"、"フタマルちゃん、はなこです" → "はなこです"、
            "たろうだよ、ふたまるよろしく" → そのまま（先頭でなければ呼びかけではない）、"ふたまる" → None
        """
        # detect_wake は文の途中のウェイクワードでも見つける。途中のものまで外すと、その前に言った
        # 名前（「たろうだよ、ふたまる…」の「たろう」）が消えてしまうので、先頭にあるときだけ使う
        joined = normalize(text)
        words = [w for w in (normalize(word) for word in self._wake_words) if w]
        if not any(joined.startswith(w) for w in words):
            return text
        match = detect_wake(text, self._wake_words)
        if match is None or not match.rest:
            return None
        return match.rest

    def on_plant_utterance(self, text: str) -> None:
        """植物が話し終えたときに呼ばれる。起きている間だけ、表情タグを外して会話ログに記録する。"""
        text = strip_tags(text).strip()
        if self.awake and text:
            self._record("plant", text)
            self._last_plant_utterance = text

    async def _identify_turn(self, text: str) -> tuple[str, str] | None:
        """記録したユーザー発話 1 つぶん、話し相手の見分けを進める（設計: 話し相手の見分けと方言）。

        引数:
            text: いま記録したユーザー発話（例: "たろうだよ"）
        戻り値:
            プロンプトを替えるときは (新しいシステムプロンプト, 方言)。替えないときは None。

        例（1 回目から順に）:
            "ただいま"   → none → None（まだ聞き続ける）
            "たろうだよ" → known → (たろうのプロンプト, "kansai")
        例（3 回とも名乗らない）:
            1・2 回目 → None、3 回目 → (お客さんのプロンプト, "standard")、4 回目以降は名前探しをしない
        """
        # 0. 植物の直前の発言を取り出して消す。ユーザーが続けて話したとき、古い発言を使い回さないため
        previous_plant, self._last_plant_utterance = self._last_plant_utterance, None
        # 1. 相手が決まっている・お客さんに決まっているなら、もう何もしない（LLM も呼ばない）
        if self._person is not None or self._guest:
            return None
        # 2. 名前探し。遅すぎたら（IDENTIFY_TIMEOUT 秒）名乗っていない扱いにして返事を優先する。
        #    IDENTIFY_TIMEOUT はテストで書き換えられるよう、呼ぶたびにモジュールの値を読む
        known = self._store.get_people(self._plant_id)
        session_id = self._session_id  # 待っている間に眠ったかどうかを、後で確かめるため
        try:
            result = await asyncio.wait_for(
                identify(self._complete_json, text, known, previous_plant=previous_plant),
                IDENTIFY_TIMEOUT,
            )
            timed_out = False
        except TimeoutError:
            logger.warning("identification timed out (plant=%s)", self._plant_id)
            result = None
            timed_out = True
        # LLM を待っている間に眠った（close された）なら、もう別のセッションなので何もしない
        if self._session_id != session_id:
            return None
        # 3. 名前を聞けた回を数える（1 から数える）。時間切れの回は数えない。
        #    LLM が遅いだけで「たろうだよ」が聞き取れず、3 回でお客さんにされるのを防ぐため（#41 のレビュー）
        if timed_out:
            return None
        self._user_turns += 1
        # 4. 名乗った → その人に決める（初めての人なら登録する）
        if result is not None and result.kind == "known" and result.person_id is not None:
            return self._decide_person(self._store.get_person(result.person_id))
        if (
            result is not None
            and result.kind == "new"
            and result.name
            and not self._is_plant_name(result.name)
        ):
            return self._decide_person(
                self._store.add_person(self._plant_id, result.name, self._clock())
            )
        # 5. 名乗らないまま MAX_NAME_TURNS 回目 → お客さんにする
        if self._user_turns >= MAX_NAME_TURNS:
            return self._become_guest()
        return None

    def _is_plant_name(self, name: str) -> bool:
        """name が植物自身の名前かウェイクワードなら True（最終レビュー M6）。

        起きている間の「ふたまるって呼んでいい？」のような発話から、LLM が「ふたまる」を
        新しい人の名前として取り出すことがある。植物の名前を人として登録しないために使う。
        表記ゆれ（カタカナ・記号）は normalize() でそろえてから比べる。

        例: "フタマル" → True、"二丸" → True、"はなこ" → False
        """
        target = normalize(name)
        plant_name = self._store.get_plant(self._plant_id).name
        return target in {normalize(w) for w in (plant_name, *self._wake_words)}

    def _decide_person(self, person: Person) -> tuple[str, str]:
        """話し相手を person に決め、その人用のシステムプロンプトと方言を返す。"""
        self._person = person
        # 1. セッションに相手を記録する。これをしないと、振り返り（要約・方言の判定・性格）が
        #    お客さん側に入ってしまう（apply_reflection は person_id を書かない）
        assert self._session_id is not None
        self._store.set_session_person(self._session_id, person.id)
        # 2. 話題を振る秒数を、その人とのおしゃべり度で決め直す
        self._controller.set_nudge_delay(nudge_delay(person.traits.talkativeness))
        # 3. 方言: 手で固定した方言があればそれ、無ければその人との直近の判定の多数決
        dialect = resolve_dialect(person.dialect_override, self._store.recent_dialects(person.id))
        logger.info(
            "plant %s is talking with person %s (dialect=%s)", self._plant_id, person.id, dialect
        )
        # 4. その人の性格・記憶（その人の記憶だけ、#70）・その人との前回の要約でプロンプトを作る。
        #    他の人だけの記憶は get_memories が返さないので、ここには入らない
        prompt = build_system_prompt(
            self._store.get_plant(self._plant_id).name,
            person.traits,
            self._store.get_memories(self._plant_id, person.id),
            self._store.latest_summary(self._plant_id, person.id),
            self._clock(),
            dialect=dialect,
            # CLI で登録した名前も入るので、改行を消し・長さを切ってからプロンプトに入れる
            person_name=clean_name(person.name),
            ask_name=False,
            # その人と前に話してから何日か（ほかの人やお客さんとの会話は数えない）
            plant_status=self._plant_status(person.id),
        )
        return prompt, dialect

    def _become_guest(self) -> tuple[str, str]:
        """お客さんに決める。名前を聞かない・標準語のプロンプトと、方言 "standard" を返す。"""
        self._guest = True
        logger.info("plant %s is talking with a guest", self._plant_id)
        # 名前を聞く指示を外すだけで、中身（株の性格・お客さんの記憶・お客さんの要約）は起きたときと同じ
        return self._guest_prompt(ask_name=False), "standard"

    def _guest_prompt(self, *, ask_name: bool) -> str:
        """株の性格・お客さんの記憶・お客さんとの前回の要約でプロンプトを作る（相手が未確定・お客さん用）。"""
        plant = self._store.get_plant(self._plant_id)
        return build_system_prompt(
            plant.name,
            plant.traits,
            self._store.get_memories(self._plant_id),  # person_id なし = お客さんの記憶だけ
            self._store.latest_summary(self._plant_id),  # person_id なし = お客さんの要約
            self._clock(),
            ask_name=ask_name,
            # 相手がまだ分からないので、誰とでも一番最近の会話から何日かを使う
            plant_status=self._plant_status(None),
        )

    def _plant_status(self, person_id: int | None) -> str:
        """植物の状態の段落（天気・育った日数・前に話してからの日数）を作る（仕様 §6.7、#80・#82）。

        person_id が None なら、相手を問わず一番最近の会話からの日数を使う。
        天気は起きたときに取ったもの（WeatherCache が覚えているもの）を使う。
        """
        current = self._weather.current() if self._weather is not None else None
        weather = describe_weather(current, dry_humidity=self._dry_humidity) if current else None
        return plant_status_text(
            self._store.plant_created_at(self._plant_id),
            self._store.last_talk_at(self._plant_id, person_id),
            self._clock(),
            weather=weather,
        )

    def _forget_person(self) -> None:
        """話し相手・発話の回数・お客さんの印を消す。次に起きたら、また名前を聞くところから始める。"""
        self._person = None
        self._user_turns = 0
        self._guest = False
        self._last_plant_utterance = None

    def _record(self, role: str, text: str) -> None:
        """発話を 1 つ DB に保存する。role は "user"（持ち主）か "plant"（植物）。"""
        # 起きている（＝セッションがある）ときにしか呼ばれない前提。違ったらバグなので止める
        assert self._session_id is not None
        self._store.add_utterance(self._session_id, role, text, self._clock())

    # --- 状態 ---

    def on_user_speaking(self, speaking: bool) -> None:
        """ユーザーが話し始めた・話し終えた（LiveKit の音声検出から呼ばれる）。"""
        self._controller.set_user_speaking(speaking, self._clock())

    def on_agent_state(self, state: str) -> None:
        """植物（エージェント）の状態が変わった。"thinking"（考え中）・"speaking"（話し中）など。"""
        # 考え中・話し中は無言ではないので、無言タイマーを止める
        self._controller.set_plant_busy(state in ("thinking", "speaking"), self._clock())

    async def tick(self) -> Literal["none", "nudge", "slept"]:
        """main.py が 1 秒ごとに呼ぶ。無言が続いていたら眠る（"slept"）か、話題を振る（"nudge"）。"""
        result = self._controller.tick(self._clock())
        if result == "sleep":
            await self._sleep()
            return "slept"
        return result

    def _wake(self) -> str:
        """起きる。新しいセッションを始め、相手が未確定のときのシステムプロンプトを作って返す。

        まだ誰と話しているか分からないので、株の性格・お客さんの記憶・お客さんの要約を使い、
        「自然に名前を聞く」指示を足す（ask_name=True）。
        """
        now = self._clock()
        plant = self._store.get_plant(self._plant_id)
        self._forget_person()  # 念のため、前のセッションの相手を残さない
        self._session_id = self._store.start_session(self._plant_id, now)
        self._controller.wake(now)
        # おしゃべりな株ほど早く話題を振る（personality.nudge_delay）。相手が決まったら決め直す
        self._controller.set_nudge_delay(nudge_delay(plant.traits.talkativeness))
        logger.info("plant %s woke up (session=%s)", self._plant_id, self._session_id)
        return self._guest_prompt(ask_name=True)

    async def _sleep(self) -> None:
        """眠る。セッションを終わらせ、その会話の振り返りを裏で始める。"""
        self._controller.sleep()
        self._forget_person()  # 次に起きたら、また名前を聞くところから始める
        # 今のセッション id を取り出し、同時に「セッションなし」に戻す
        session_id, self._session_id = self._session_id, None
        if session_id is None:
            return
        now = self._clock()
        self._store.end_session(session_id, now)
        logger.info("plant %s fell asleep (session=%s)", self._plant_id, session_id)
        # 振り返りは LLM を呼ぶので数秒かかる。待たずに裏で走らせ、その間もウェイクワードを受け付ける
        self._track(
            reflect_session(self._store, self._plant_id, session_id, self._complete_json, now)
        )

    async def _reflect_all(self, session_ids: list[int], now) -> None:
        """前回の起動で振り返りが終わらなかったセッションを、古い順に 1 つずつ振り返る。"""
        for session_id in session_ids:
            await reflect_session(self._store, self._plant_id, session_id, self._complete_json, now)

    def _track(self, coro) -> None:
        """振り返りを裏で走らせるタスクとして登録する。終わったら一覧から自動で外れる。

        振り返りは鍵（_reflect_lock）を取ってから 1 つずつ走る（#9）。
        前の振り返りが終わるまで、次の振り返りは LLM を呼ばずに待つ。
        """
        task = asyncio.create_task(self._run_locked(coro))
        self._reflections.add(task)
        task.add_done_callback(self._reflections.discard)

    async def _run_locked(self, coro) -> None:
        """鍵を取ってから coro（振り返り）を実行する。"""
        try:
            async with self._reflect_lock:
                await coro
        finally:
            # 鍵を待っている間に止められた（cancel された）ときは、coro は一度も動いていない。
            # そのままだと「await されなかった」という警告が出るので、閉じておく
            coro.close()

    async def close(self) -> None:
        """会話を終える（ESP32 の退出時・エージェントの終了時）。起きていれば眠らせ、振り返りが終わるまで待つ。"""
        if self.awake:
            await self._sleep()
        await self.wait_reflections()

    async def shutdown(self, timeout: float) -> None:
        """エージェントの終了時に呼ぶ。新しく起きるのをやめ、振り返りを最大 timeout 秒だけ待つ（#13）。

        LiveKit は終了処理を一定時間（既定 10 秒）で打ち切るので、振り返りを待ちすぎると
        main.py の store.close() まで進めない。時間内に終わらなかった振り返りは止める。
        止めた振り返りのセッションは「未処理」のまま DB に残り、次の起動で振り返り直す
        （DB への書き込みは 1 回のトランザクションなので、途中まで書かれることはない）。
        """
        self._shutting_down = True
        if self.awake:
            await self._sleep()
        await self.wait_reflections(timeout)

    async def wait_reflections(self, timeout: float | None = None) -> None:
        """裏で動いている振り返りが全部終わるまで待つ。1 つが失敗しても他は待ち続ける。

        timeout を渡すと、その秒数で待つのをやめ、終わっていない振り返りを止める。
        """
        if not self._reflections:
            return
        # 1. 終わるのを待つ（timeout が None なら、全部終わるまで）
        _, pending = await asyncio.wait(set(self._reflections), timeout=timeout)
        if not pending:
            return
        # 2. 時間切れ: 残りを止めて、止まるのを待つ
        logger.warning(
            "stopping %d unfinished reflection(s) on shutdown (plant=%s)",
            len(pending),
            self._plant_id,
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

    # --- 表情 ---

    def express(self, name: str) -> None:
        """LLM の返事の先頭で決まった表情を、ログに残す（仕様 §7.1）。

        顔のディスプレイは付けないことにしたので（2026-11-16、#22）、ESP32 には送らない。
        ログに残すのは、console や docker compose logs で「どんな気持ちで返事したか」を確かめるため。

        例: express("happy") → ログに "expression=happy (plant=a1)" と出る
        """
        logger.info("expression=%s (plant=%s)", name, self._plant_id)
