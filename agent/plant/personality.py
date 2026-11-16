"""性格パラメータ（4軸）の定義と更新ルール（仕様 §6.1, §6.3, §7.2）。

ふたまるの性格は 4 つの数値（0.0〜1.0、初期値 0.5）で表す。
会話が終わるたびに振り返り（reflection.py）が少しずつ値を動かし、性格が育っていく。

| 軸             | 低いと         | 高いと                         |
|----------------|----------------|--------------------------------|
| talkativeness  | 短く静かに返す | 長く話す・自分から話題を振る   |
| affection      | 素っ気ない     | 甘える                         |
| energy         | 落ち着いた口調 | テンション高い                 |
| curiosity      | 相づち中心     | よく質問する                   |
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from typing import Literal

# 4 軸の名前。DB の列名・LLM が返す JSON のキーと同じ
TRAIT_NAMES = ("talkativeness", "affection", "energy", "curiosity")
# 1 回の振り返りで動かせる最大の量。1 回の会話で性格が急に変わらないようにする（仕様 §6.3）
MAX_DELTA = 0.05

# 値を 3 段階に分けたもの。プロンプトの指示文を選ぶのに使う（prompt.py）
Level = Literal["low", "mid", "high"]


@dataclass(frozen=True)
class Traits:
    """1 株の性格（4 軸の値）。frozen なので値は書き換えられず、変えるときは新しく作る。"""

    talkativeness: float = 0.5  # おしゃべり度
    affection: float = 0.5  # 甘え度
    energy: float = 0.5  # 元気度
    curiosity: float = 0.5  # 好奇心

    def as_dict(self) -> dict[str, float]:
        """{"talkativeness": 0.5, "affection": 0.5, ...} の形にする。"""
        return asdict(self)


def clamp(x: float, lo: float, hi: float) -> float:
    """x を lo 以上 hi 以下に収める。例: clamp(1.2, 0.0, 1.0) → 1.0、clamp(-0.1, 0.0, 1.0) → 0.0"""
    return max(lo, min(hi, x))


def level(value: float) -> Level:
    """値を 3 段階に分ける（仕様 §6.4）。0.0〜0.33 → "low"、〜0.66 → "mid"、0.67〜1.0 → "high"。"""
    if value <= 0.33:
        return "low"
    if value < 0.67:
        return "mid"
    return "high"


def _as_delta(value: object) -> float:
    """LLM が返した変化量を、安全な数値に直す。

    LLM の出力は信用できないので、数値でないもの（文字列・None・True/False など）は 0.0（変化なし）とし、
    数値は ±MAX_DELTA の範囲に収める。例: 0.2 → 0.05、"abc" → 0.0
    """
    # bool は Python では int の仲間（True == 1）なので、先に弾かないと 1 として扱われてしまう
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return clamp(float(value), -MAX_DELTA, MAX_DELTA)


def apply_deltas(
    current: Traits, llm_deltas: Mapping[str, object], recent_user_turns: int
) -> Traits:
    """振り返りで LLM が決めた変化量を、今の性格に足して新しい性格を返す（仕様 §6.3）。

    引数:
        current: 今の性格
        llm_deltas: LLM が返した変化量。例: {"affection": 0.03, "energy": -0.01}
        recent_user_turns: 直近 7 日間にユーザーが話しかけた回数
    戻り値:
        新しい性格。各軸は 0.0〜1.0 に収める

    talkativeness（おしゃべり度）だけは、LLM の判断に加えて「実際にどれだけ話しかけられたか」も使う。
    よく話しかけてくれる持ち主の植物ほど、おしゃべりになっていく。
    """
    values = current.as_dict()
    for name in TRAIT_NAMES:
        delta = _as_delta(llm_deltas.get(name))  # LLM が返さなかった軸は 0.0
        if name == "talkativeness":
            # 行動から見た目標値。7 日で 100 回話しかけられたら最大（1.0）
            t_behavior = min(1.0, recent_user_turns / 100)
            # LLM の判断と、目標値に近づく動きを半分ずつ混ぜる
            delta = clamp(
                0.5 * delta + 0.5 * (t_behavior - values[name]) * 0.1, -MAX_DELTA, MAX_DELTA
            )
        values[name] = clamp(values[name] + delta, 0.0, 1.0)
    # replace は「一部の値だけ変えた新しい Traits」を作る（frozen なので直接は書き換えられない）
    return replace(current, **values)


def nudge_delay(talkativeness: float) -> float | None:
    """会話中に持ち主が黙ったとき、植物から話題を振るまでの秒数（仕様 §7.2）。

    おしゃべりな株ほど早く話題を振る。おしゃべり度が 0.3 未満の無口な株は振らない（None）。
    例: 1.0 → 10 秒、0.5 → 25 秒、0.3 → 31 秒、0.2 → None
    """
    if talkativeness < 0.3:
        return None
    return 10 + (1.0 - talkativeness) * 30
