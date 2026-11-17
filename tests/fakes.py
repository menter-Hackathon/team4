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
