"""性格パラメータ（4軸）の定義と更新ルール（仕様 §6.1, §6.3, §7.2）。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from typing import Literal

TRAIT_NAMES = ("talkativeness", "affection", "energy", "curiosity")
MAX_DELTA = 0.05

Level = Literal["low", "mid", "high"]


@dataclass(frozen=True)
class Traits:
    talkativeness: float = 0.5
    affection: float = 0.5
    energy: float = 0.5
    curiosity: float = 0.5

    def as_dict(self) -> dict[str, float]:
        return asdict(self)


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def level(value: float) -> Level:
    if value <= 0.33:
        return "low"
    if value < 0.67:
        return "mid"
    return "high"


def _as_delta(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return clamp(float(value), -MAX_DELTA, MAX_DELTA)


def apply_deltas(
    current: Traits, llm_deltas: Mapping[str, object], recent_user_turns: int
) -> Traits:
    """reflection の変化量を適用する。talkativeness は行動統計とブレンドする。"""
    values = current.as_dict()
    for name in TRAIT_NAMES:
        delta = _as_delta(llm_deltas.get(name))
        if name == "talkativeness":
            t_behavior = min(1.0, recent_user_turns / 100)
            delta = clamp(
                0.5 * delta + 0.5 * (t_behavior - values[name]) * 0.1, -MAX_DELTA, MAX_DELTA
            )
        values[name] = clamp(values[name] + delta, 0.0, 1.0)
    return replace(current, **values)


def nudge_delay(talkativeness: float) -> float | None:
    """会話中の沈黙で話題を振るまでの秒数。0.3 未満なら振らない。"""
    if talkativeness < 0.3:
        return None
    return 10 + (1.0 - talkativeness) * 30
