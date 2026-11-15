"""ウェイクワード検出と、起きている/眠っている状態・無言タイマーの管理（仕様 §5）。"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

State = Literal["SLEEPING", "AWAKE"]
Tick = Literal["none", "nudge", "sleep"]

MIN_REST_CHARS = 2


def _normalize_char(ch: str) -> str:
    out = []
    for c in unicodedata.normalize("NFKC", ch):
        if "ァ" <= c <= "ヶ":
            c = chr(ord(c) - 0x60)
        if unicodedata.category(c)[0] in "PSZC":
            continue
        out.append(c)
    return "".join(out)


def normalize(text: str) -> str:
    """カタカナ→ひらがな、空白・句読点・記号の除去。"""
    return "".join(_normalize_char(ch) for ch in text)


@dataclass
class WakeMatch:
    rest: str  # ウェイクワードの後ろに続く文。"" はウェイクワードだけ


def detect_wake(text: str, wake_words: Sequence[str]) -> WakeMatch | None:
    # 正規化後の各文字が、元の文字列の何文字目から来たかを記録する
    normalized: list[str] = []
    origin: list[int] = []
    for i, ch in enumerate(text):
        for c in _normalize_char(ch):
            normalized.append(c)
            origin.append(i)
    joined = "".join(normalized)

    best: tuple[int, int] | None = None  # (開始位置, 終了位置) 正規化後
    for word in wake_words:
        w = normalize(word)
        if not w:
            continue
        pos = joined.find(w)
        if pos >= 0 and (best is None or pos < best[0]):
            best = (pos, pos + len(w))
    if best is None:
        return None

    rest = text[origin[best[1] - 1] + 1 :]
    i = 0
    while i < len(rest) and not _normalize_char(rest[i]):
        i += 1
    rest = rest[i:].rstrip()
    if len(normalize(rest)) < MIN_REST_CHARS:
        rest = ""
    return WakeMatch(rest)


class WakeController:
    """SLEEPING / AWAKE の状態と、無言時間による話題振り・睡眠の判定。

    無言時間は「植物が話していない・ユーザーが話していない時間」で数える（§5.2）。
    """

    def __init__(self, sleep_after: float = 60.0) -> None:
        self.sleep_after = sleep_after
        self.state: State = "SLEEPING"
        self._anchor: datetime | None = None
        self._plant_busy = False
        self._user_speaking = False
        self._nudge_delay: float | None = None
        self._nudged = False

    def wake(self, now: datetime) -> None:
        self.state = "AWAKE"
        self._anchor = now
        self._nudged = False

    def sleep(self) -> None:
        self.state = "SLEEPING"

    def on_user_activity(self, now: datetime) -> None:
        self._anchor = now
        self._nudged = False

    def set_user_speaking(self, speaking: bool, now: datetime) -> None:
        self._user_speaking = speaking
        if not speaking:
            self._anchor = now

    def set_plant_busy(self, busy: bool, now: datetime) -> None:
        if self._plant_busy and not busy:
            self._anchor = now
        self._plant_busy = busy

    def set_nudge_delay(self, seconds: float | None) -> None:
        self._nudge_delay = seconds

    def tick(self, now: datetime) -> Tick:
        if self.state != "AWAKE" or self._plant_busy or self._user_speaking or self._anchor is None:
            return "none"
        silence = (now - self._anchor).total_seconds()
        if silence >= self.sleep_after:
            self.sleep()
            return "sleep"
        if self._nudge_delay is not None and not self._nudged and silence >= self._nudge_delay:
            self._nudged = True
            return "nudge"
        return "none"
