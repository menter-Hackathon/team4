"""1株ぶんの会話の進行: 起床・記録・睡眠・reflection の起動・表情送信（仕様 §5, §7）。

LiveKit には依存しない。main.py が LiveKit のイベントをこのクラスのメソッドに配線する。
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
from .wake import WakeController, detect_wake

logger = logging.getLogger(__name__)

SendExpression = Callable[[str], Awaitable[None]]

# 起動時に再実行する未処理 reflection の上限。LLM が落ちていても起動を何分も止めないため
RECOVERY_LIMIT = 3


@dataclass
class TurnDecision:
    kind: Literal["ignore", "reply", "greet"]
    text: str = ""  # reply のとき LLM に渡すユーザー発話
    instructions: str | None = None  # 今まさに起きたときだけ、新しいシステムプロンプト


class PlantConversation:
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
        self._controller = WakeController(sleep_after=sleep_after)
        self._session_id: int | None = None
        self._reflections: set[asyncio.Task] = set()
        self._expression_tasks: set[asyncio.Task] = set()

    @property
    def awake(self) -> bool:
        return self._controller.state == "AWAKE"

    async def start(self) -> None:
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
        text = text.strip()
        if not text:
            return TurnDecision("ignore")

        if not self.awake:
            match = detect_wake(text, self._wake_words)
            if match is None:
                return TurnDecision("ignore")
            instructions = self._wake()
            if match.rest:
                self._record("user", match.rest)
                return TurnDecision("reply", match.rest, instructions)
            return TurnDecision("greet", instructions=instructions)

        self._record("user", text)
        self._controller.on_user_activity(self._clock())
        return TurnDecision("reply", text)

    def on_plant_utterance(self, text: str) -> None:
        text = strip_tags(text).strip()
        if self.awake and text:
            self._record("plant", text)

    def _record(self, role: str, text: str) -> None:
        assert self._session_id is not None
        self._store.add_utterance(self._session_id, role, text, self._clock())

    # --- 状態 ---

    def on_user_speaking(self, speaking: bool) -> None:
        self._controller.set_user_speaking(speaking, self._clock())
        if speaking and self.awake:
            self.express("listening")

    def on_agent_state(self, state: str) -> None:
        self._controller.set_plant_busy(state in ("thinking", "speaking"), self._clock())
        if state == "thinking" and self.awake:
            self.express("thinking")

    async def tick(self) -> Literal["none", "nudge", "slept"]:
        result = self._controller.tick(self._clock())
        if result == "sleep":
            await self._sleep()
            return "slept"
        return result

    def _wake(self) -> str:
        now = self._clock()
        plant = self._store.get_plant(self._plant_id)
        self._session_id = self._store.start_session(self._plant_id, now)
        self._controller.wake(now)
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
        self._controller.sleep()
        self.express("sleepy")
        session_id, self._session_id = self._session_id, None
        if session_id is None:
            return
        now = self._clock()
        self._store.end_session(session_id, now)
        logger.info("plant %s fell asleep (session=%s)", self._plant_id, session_id)
        self._track(reflect_session(self._store, self._plant_id, session_id, self._complete_json, now))

    async def _reflect_all(self, session_ids: list[int], now) -> None:
        for session_id in session_ids:
            await reflect_session(self._store, self._plant_id, session_id, self._complete_json, now)

    def _track(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._reflections.add(task)
        task.add_done_callback(self._reflections.discard)

    async def close(self) -> None:
        if self.awake:
            await self._sleep()
        await self.wait_reflections()

    async def wait_reflections(self) -> None:
        if self._reflections:
            await asyncio.gather(*self._reflections, return_exceptions=True)

    # --- 表情 ---

    def express(self, name: str) -> None:
        logger.info("expression=%s (plant=%s)", name, self._plant_id)
        task = asyncio.create_task(self._send(name))
        self._expression_tasks.add(task)
        task.add_done_callback(self._expression_tasks.discard)

    async def _send(self, name: str) -> None:
        try:
            await self._send_expression(name)
        except Exception:
            logger.warning("failed to send expression %s", name, exc_info=True)
