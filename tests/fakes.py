"""テスト用の偽物（LLM・時計）。"""

import asyncio
import json
from datetime import datetime, timedelta


class FakeCompleter:
    """呼ばれるたびに responses の次の要素を返す。Exception なら raise する。"""

    def __init__(self, responses, gate: asyncio.Event | None = None):
        self.responses = list(responses)
        self.calls = []
        self.gate = gate

    async def __call__(self, messages, schema):
        self.calls.append((messages, schema))
        if self.gate is not None:
            await self.gate.wait()
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeClock:
    def __init__(self, now: datetime):
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def reflection_json(
    summary="テストの話をした", new=None, updated=None, deltas=None, dialect="unknown"
) -> str:
    return json.dumps(
        {
            "summary": summary,
            "new_memories": new if new is not None else [],
            "updated_memories": updated if updated is not None else [],
            "trait_deltas": deltas if deltas is not None else {},
            "owner_dialect": dialect,
        },
        ensure_ascii=False,
    )


class FakeBridge:
    """中継のテスト用の、LiveKit に入ったふりをする DeviceBridge（agent/relay/bridge.py）。

    mic: push_mic で受け取った鉢の音（640 バイトずつ）
    say(frame): エージェントが話したことにして、plant_audio() からそのフレームを出す
    """

    def __init__(self, plant_id: str):
        self.plant_id = plant_id
        self.mic: list[bytes] = []
        self.closed = False
        self._out: asyncio.Queue[bytes | None] = asyncio.Queue()

    async def push_mic(self, frame: bytes) -> None:
        self.mic.append(frame)

    def say(self, frame: bytes) -> None:
        self._out.put_nowait(frame)

    async def plant_audio(self):
        while True:
            frame = await self._out.get()
            if frame is None:
                return
            yield frame

    async def close(self) -> None:
        if not self.closed:
            self.closed = True
            self._out.put_nowait(None)


class FakeBridgeFactory:
    """FakeBridge を作って覚えておく BridgeFactory。fail=True ならルームに入れなかったことにする。"""

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.bridges: list[FakeBridge] = []

    async def __call__(self, plant_id: str) -> FakeBridge:
        if self.fail:
            raise ConnectionError("livekit is down")
        bridge = FakeBridge(plant_id)
        self.bridges.append(bridge)
        return bridge


class FakePasswordPrompt:
    """getpass.getpass の偽物（scripts/accounts.py のテスト用）。

    呼ばれるたびに answers の次の要素を「打たれたパスワード」として返し、聞いた文を prompts に覚える。
    """

    def __init__(self, answers):
        self.answers = list(answers)
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.answers.pop(0)
