"""会話終了時の記憶抽出と性格変化量の判定（仕様 §6.3）。"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from .personality import TRAIT_NAMES, apply_deltas, clamp
from .prompt import rank_memories
from .store import MemoryUpdate, NewMemory, PlantStore

logger = logging.getLogger(__name__)

JsonCompleter = Callable[[list[dict], dict], Awaitable[str]]

MEMORY_KINDS = ("preference", "event", "habit")
REFLECTION_MEMORY_LIMIT = 30
MAX_ATTEMPTS = 2

REFLECTION_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "new_memories": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "content": {"type": "string"},
                    "kind": {"type": "string", "enum": list(MEMORY_KINDS)},
                    "importance": {"type": "number"},
                },
                "required": ["content", "kind", "importance"],
            },
        },
        "updated_memories": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "content": {"type": "string"},
                    "importance": {"type": "number"},
                },
                "required": ["id", "content", "importance"],
            },
        },
        "trait_deltas": {
            "type": "object",
            "properties": {name: {"type": "number"} for name in TRAIT_NAMES},
            "required": list(TRAIT_NAMES),
        },
    },
    "required": ["summary", "new_memories", "updated_memories", "trait_deltas"],
}

SYSTEM_MESSAGE = """あなたは観葉植物「ふたまる」の記憶係。持ち主との会話ログを読み、次の JSON だけを出力する。
{
  "summary": "会話の要約（1〜2文）",
  "new_memories": [{"content": "持ち主について覚えておくこと", "kind": "preference | event | habit", "importance": 0.0〜1.0}],
  "updated_memories": [{"id": 既存記憶の id, "content": "更新後の内容", "importance": 0.0〜1.0}],
  "trait_deltas": {"talkativeness": -0.05〜0.05, "affection": -0.05〜0.05, "energy": -0.05〜0.05, "curiosity": -0.05〜0.05}
}
- 既存の記憶と同じ内容は new_memories に入れない。内容が変わった・補強された場合は updated_memories でその id を更新する。
- trait_deltas は今回の会話で性格がどう変わるかの判定:
  talkativeness は話しかけ頻度・会話の長さ、affection は褒め言葉・優しい言葉の量、
  energy はユーザーの口調・話題の明るさ、curiosity はユーザーが自分の話をする量で判断する。"""


class ReflectionError(Exception):
    pass


@dataclass
class ReflectionResult:
    summary: str
    new_memories: list[NewMemory]
    updated_memories: list[MemoryUpdate]
    trait_deltas: dict[str, object]


def _importance(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.5
    return clamp(float(value), 0.0, 1.0)


def _strip_fence(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    return text.strip()


def parse_reflection(raw: str) -> ReflectionResult:
    try:
        data = json.loads(_strip_fence(raw))
    except json.JSONDecodeError as e:
        raise ReflectionError(f"JSON として不正: {e}") from e
    if not isinstance(data, dict):
        raise ReflectionError("トップレベルがオブジェクトではない")
    for key in ("summary", "new_memories", "updated_memories", "trait_deltas"):
        if key not in data:
            raise ReflectionError(f"必須キー {key} が無い")
    if not isinstance(data["summary"], str):
        raise ReflectionError("summary が文字列ではない")
    if not isinstance(data["new_memories"], list) or not isinstance(data["updated_memories"], list):
        raise ReflectionError("new_memories / updated_memories が配列ではない")
    if not isinstance(data["trait_deltas"], dict):
        raise ReflectionError("trait_deltas がオブジェクトではない")

    new_memories = [
        NewMemory(m["content"], m["kind"], _importance(m.get("importance")))
        for m in data["new_memories"]
        if isinstance(m, dict)
        and isinstance(m.get("content"), str)
        and m["content"].strip()
        and m.get("kind") in MEMORY_KINDS
    ]
    updated_memories = [
        MemoryUpdate(m["id"], m["content"], _importance(m.get("importance")))
        for m in data["updated_memories"]
        if isinstance(m, dict)
        and isinstance(m.get("id"), int)
        and not isinstance(m.get("id"), bool)
        and isinstance(m.get("content"), str)
        and m["content"].strip()
    ]
    return ReflectionResult(data["summary"], new_memories, updated_memories, data["trait_deltas"])


def _build_messages(store: PlantStore, plant_id: str, session_id: int, now: datetime) -> list[dict]:
    lines = [
        f"{'持ち主' if u.role == 'user' else 'ふたまる'}: {u.text}"
        for u in store.get_utterances(session_id)
    ]
    memories = rank_memories(store.get_memories(plant_id), now, REFLECTION_MEMORY_LIMIT)
    memory_lines = [f"[id={m.id}] {m.content} (importance={m.importance:g})" for m in memories]
    user = (
        "## 会話ログ\n"
        + "\n".join(lines)
        + "\n\n## 既存の記憶\n"
        + ("\n".join(memory_lines) or "（なし）")
    )
    return [{"role": "system", "content": SYSTEM_MESSAGE}, {"role": "user", "content": user}]


async def reflect_session(
    store: PlantStore, plant_id: str, session_id: int, complete_json: JsonCompleter, now: datetime
) -> bool:
    """1セッションぶんの reflection。成功（または不要）なら True。"""
    if store.get_session(session_id).user_turns == 0:
        store.mark_reflected(session_id)
        return True

    messages = _build_messages(store, plant_id, session_id, now)
    result: ReflectionResult | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            result = parse_reflection(await complete_json(messages, REFLECTION_SCHEMA))
            break
        except Exception:
            logger.warning(
                "reflection failed (session=%s, attempt=%d)", session_id, attempt, exc_info=True
            )
    if result is None:
        return False

    recent = store.count_user_utterances_since(plant_id, now - timedelta(days=7))
    traits = apply_deltas(store.get_plant(plant_id).traits, result.trait_deltas, recent)
    store.apply_reflection(
        plant_id,
        session_id,
        result.summary,
        result.new_memories,
        result.updated_memories,
        traits,
        now,
    )
    return True


async def recover(
    store: PlantStore, plant_id: str, complete_json: JsonCompleter, now: datetime
) -> None:
    """起動時の修復: 閉じていないセッションを閉じ、未処理の reflection を再実行する（§6.3 手順8）。"""
    repaired = store.repair_unfinished_sessions(plant_id)
    if repaired:
        logger.info("repaired %d unfinished sessions for %s", repaired, plant_id)
    for session_id in store.unreflected_sessions(plant_id):
        await reflect_session(store, plant_id, session_id, complete_json, now)
