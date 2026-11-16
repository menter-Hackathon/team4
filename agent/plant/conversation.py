"""1株ぶんの会話の進行: 起床・記録・睡眠・reflection の起動・表情送信（仕様 §5, §7）。

LiveKit には依存しない。main.py が LiveKit のイベントをこのクラスのメソッドに配線する。

1 回の会話（セッション）の流れ:
    1. 眠っている。ユーザーの発話はウェイクワードが無ければ無視する（on_user_turn）
    2. 「ふたまる」で起きる。DB に新しいセッションを作り、その株のシステムプロンプトを作る（_wake）
    3. 会話する。発話は 1 つずつ DB に記録する（_record）
    4. しばらく誰も話さないと眠る（tick → _sleep）
    5. 眠ったら、その会話を振り返って記憶と性格を更新する（reflection.py。裏で実行）

LiveKit に依存させないのは、偽物の時計・偽物の LLM を渡してテストで動かせるようにするため。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Literal

from .expression import strip_tags
from .personality import nudge_delay
from .prompt import build_system_prompt
from .reflection import JsonCompleter, reflect_session
from .store import PlantStore
from .timeutil import Clock
from .wake import WakeController, detect_wake, normalize

logger = logging.getLogger(__name__)

# 表情を ESP32 に送る関数の型。main.py が LiveKit の RPC で送る関数を渡す（#22 でなくなる予定）
SendExpression = Callable[[str], Awaitable[None]]

# 起動時に再実行する未処理 reflection の上限。LLM が落ちていても起動を何分も止めないため
RECOVERY_LIMIT = 3


@dataclass
class TurnDecision:
    """on_user_turn() の結果。main.py はこれを見て、返事する・挨拶する・何もしない を決める。"""

    kind: Literal["ignore", "reply", "greet"]
    text: str = ""  # reply のとき LLM に渡すユーザー発話
    instructions: str | None = None  # 今まさに起きたときだけ、新しいシステムプロンプト


class PlantConversation:
    """1 株ぶんの会話を進める。株（plant_id）ごとに 1 つ作る。

    引数（すべてキーワード指定）:
        store: 会話ログ・記憶・性格を保存する DB
        plant_id: どの株か（例: "a1"）
        complete_json: 振り返りで LLM に JSON を作らせる関数
        send_expression: 表情を ESP32 に送る関数
        clock: 現在時刻を返す関数（テストでは偽物の時計）
        wake_words: ウェイクワードの一覧（例: ("ふたまる", "二丸")）
        sleep_after: 何秒だれも話さなければ眠るか
    """

    def __init__(
        self,
        *,
        store: PlantStore,
        plant_id: str,
        complete_json: JsonCompleter,
        send_expression: SendExpression,
        clock: Clock,
        wake_words: Sequence[str],
        sleep_after: float,
    ) -> None:
        self._store = store
        self._plant_id = plant_id
        self._complete_json = complete_json
        self._send_expression = send_expression
        self._clock = clock
        self._wake_words = tuple(wake_words)
        self._controller = WakeController(
            sleep_after=sleep_after
        )  # 起きている/眠っている状態と無言タイマー
        self._session_id: int | None = None  # 今のセッションの DB 上の id。眠っている間は None
        # 裏で動いている振り返りのタスク。終了時に待つために覚えておく
        self._reflections: set[asyncio.Task] = set()
        self._expression_tasks: set[asyncio.Task] = set()  # 送信中の表情のタスク

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
        self.express("sleepy")

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
            # 起きる。新しいセッションを始め、性格・記憶入りのシステムプロンプトを受け取る
            instructions = self._wake()
            if match.rest:
                # 「ふたまる、今日カレー食べた」のように続きがある → その続きに返事する
                self._record("user", match.rest)
                return TurnDecision("reply", match.rest, instructions)
            # 「ふたまる」だけ → 植物から話しかける（挨拶なのでユーザー発話は記録しない）
            return TurnDecision("greet", instructions=instructions)

        # 3. 起きているとき: 発話を記録し、無言タイマーをリセットして返事する
        self._record("user", text)
        self._controller.on_user_activity(self._clock())
        return TurnDecision("reply", text)

    def on_plant_utterance(self, text: str) -> None:
        """植物が話し終えたときに呼ばれる。起きている間だけ、表情タグを外して会話ログに記録する。"""
        text = strip_tags(text).strip()
        if self.awake and text:
            self._record("plant", text)

    def _record(self, role: str, text: str) -> None:
        """発話を 1 つ DB に保存する。role は "user"（持ち主）か "plant"（植物）。"""
        # 起きている（＝セッションがある）ときにしか呼ばれない前提。違ったらバグなので止める
        assert self._session_id is not None
        self._store.add_utterance(self._session_id, role, text, self._clock())

    # --- 状態 ---

    def on_user_speaking(self, speaking: bool) -> None:
        """ユーザーが話し始めた・話し終えた（LiveKit の音声検出から呼ばれる）。"""
        self._controller.set_user_speaking(speaking, self._clock())
        if speaking and self.awake:
            self.express("listening")

    def on_agent_state(self, state: str) -> None:
        """植物（エージェント）の状態が変わった。"thinking"（考え中）・"speaking"（話し中）など。"""
        # 考え中・話し中は無言ではないので、無言タイマーを止める
        self._controller.set_plant_busy(state in ("thinking", "speaking"), self._clock())
        if state == "thinking" and self.awake:
            self.express("thinking")

    async def tick(self) -> Literal["none", "nudge", "slept"]:
        """main.py が 1 秒ごとに呼ぶ。無言が続いていたら眠る（"slept"）か、話題を振る（"nudge"）。"""
        result = self._controller.tick(self._clock())
        if result == "sleep":
            await self._sleep()
            return "slept"
        return result

    def _wake(self) -> str:
        """起きる。新しいセッションを始め、この株用のシステムプロンプトを作って返す。"""
        now = self._clock()
        plant = self._store.get_plant(self._plant_id)
        self._session_id = self._store.start_session(self._plant_id, now)
        self._controller.wake(now)
        # おしゃべりな株ほど早く話題を振る（personality.nudge_delay）
        self._controller.set_nudge_delay(nudge_delay(plant.traits.talkativeness))
        logger.info("plant %s woke up (session=%s)", self._plant_id, self._session_id)
        return build_system_prompt(
            plant.name,
            plant.traits,
            self._store.get_memories(self._plant_id),
            self._store.latest_summary(self._plant_id),
            now,
        )

    async def _sleep(self) -> None:
        """眠る。セッションを終わらせ、その会話の振り返りを裏で始める。"""
        self._controller.sleep()
        self.express("sleepy")
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
        """振り返りを裏で走らせるタスクとして登録する。終わったら一覧から自動で外れる。"""
        task = asyncio.create_task(coro)
        self._reflections.add(task)
        task.add_done_callback(self._reflections.discard)

    async def close(self) -> None:
        """会話を終える（ESP32 の退出時・エージェントの終了時）。起きていれば眠らせ、振り返りが終わるまで待つ。"""
        if self.awake:
            await self._sleep()
        await self.wait_reflections()

    async def wait_reflections(self) -> None:
        """裏で動いている振り返りが全部終わるまで待つ。1 つが失敗しても他は待ち続ける。"""
        if self._reflections:
            await asyncio.gather(*self._reflections, return_exceptions=True)

    # --- 表情 ---

    def express(self, name: str) -> None:
        """表情をログに出し、ESP32 に送る（送り終わるのは待たない）。#22 で送信はなくなる予定。"""
        logger.info("expression=%s (plant=%s)", name, self._plant_id)
        task = asyncio.create_task(self._send(name))
        self._expression_tasks.add(task)
        task.add_done_callback(self._expression_tasks.discard)

    async def _send(self, name: str) -> None:
        """表情を送る。ESP32 が受け取れなくても会話は止めず、警告をログに残すだけにする。"""
        try:
            await self._send_expression(name)
        except Exception:
            logger.warning("failed to send expression %s", name, exc_info=True)
