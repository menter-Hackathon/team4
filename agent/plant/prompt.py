"""性格 + 記憶 + 前回要約 → システムプロンプト（仕様 §6.4, §7.2）。"""

from __future__ import annotations

from datetime import datetime

from .personality import TRAIT_NAMES, Traits, level
from .store import Memory
from .timeutil import parse_iso

WAKE_GREETING_INSTRUCTION = "持ち主に呼ばれて起きた。記憶や前回の会話を踏まえて短く話しかける。"
NUDGE_INSTRUCTION = "持ち主がしばらく黙っている。記憶や前回の会話を踏まえて、短く話題を振る。"
FALLBACK_REPLY = "ん…ちょっとぼーっとしてた"

PROMPT_MEMORY_LIMIT = 10

LENGTH = {
    "low": "1文",
    "mid": "1〜2文",
    "high": "2〜3文で、ときどき自分から話題も振る",
}

TRAIT_TEXT = {
    "talkativeness": {
        "low": "口数は少なめで、静かに返す。",
        "mid": "ほどよく話す。",
        "high": "おしゃべりで、自分からもよく話す。",
    },
    "affection": {
        "low": "少し素っ気なく接する。",
        "mid": "普通に親しく接する。",
        "high": "持ち主に甘えて親しげに話す。",
    },
    "energy": {
        "low": "落ち着いた口調で話す。",
        "mid": "ふつうのテンションで話す。",
        "high": "テンション高めで、「！」を多めに使う。",
    },
    "curiosity": {
        "low": "質問はあまりせず、相づち中心にする。",
        "mid": "ときどき持ち主に質問する。",
        "high": "持ち主のことをよく質問する。",
    },
}

EXPRESSION_TAG_LINE = "返答の先頭に必ず表情タグを1つ付ける: [neutral] [happy] [sad] [surprised] [love] [sleepy]"


def recency_factor(updated_at: str, now: datetime) -> float:
    days = (now - parse_iso(updated_at)).total_seconds() / 86400
    return 0.5 ** (max(days, 0.0) / 7)


def rank_memories(memories: list[Memory], now: datetime, limit: int) -> list[Memory]:
    ranked = sorted(memories, key=lambda m: m.importance * recency_factor(m.updated_at, now), reverse=True)
    return ranked[:limit]


def build_system_prompt(
    name: str, traits: Traits, memories: list[Memory], last_summary: str | None, now: datetime
) -> str:
    values = traits.as_dict()
    parts = [
        f"あなたは観葉植物の{name}。持ち主と話し言葉で短く話す。"
        f"1回の返答は{LENGTH[level(traits.talkativeness)]}。",
        EXPRESSION_TAG_LINE,
        "\n".join(TRAIT_TEXT[t][level(values[t])] for t in TRAIT_NAMES),
    ]
    top = rank_memories(memories, now, PROMPT_MEMORY_LIMIT)
    if top:
        parts.append("覚えていること:\n" + "\n".join(f"- {m.content}" for m in top))
    if last_summary:
        parts.append(f"前回の会話: {last_summary}")
    return "\n\n".join(parts)
