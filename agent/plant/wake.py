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
# ウェイクワードにすぐ続く呼びかけの敬称。発話の中身ではないので取り除く（#6）
HONORIFICS = ("ちゃん", "くん", "さん")


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
    """文字起こし `text` にウェイクワードがあれば、その後ろに続く文（rest）を返す（仕様 §5.3）。

    ウェイクワードが無ければ None。ウェイクワードだけ（後ろが 2 文字未満）なら rest は ""。
    敬称と繰り返したウェイクワードは rest に含めない（#6）。

    例:
        "ふたまる、今日カレー食べた" → rest = "今日カレー食べた"
        "ふたまるちゃん"             → rest = ""（挨拶する）
        "ふたまるくん元気？"         → rest = "元気？"
        "ふたまる、ふたまる、聞いて" → rest = "聞いて"
        "ふたまる、さんぽ行こう"     → rest = "さんぽ行こう"（区切りの後ろの「さん」は敬称ではない）
        "おはよう"                   → None

    手順:
        1. 表記ゆれ（カタカナ・全角・記号）をなくした文字列 joined を作り、そこでウェイクワードを探す
        2. ウェイクワードの直後にある敬称と、繰り返しのウェイクワードを読み飛ばす
        3. 読み飛ばした位置から後ろを、元の text から切り出して rest にする
    """
    # --- 1. 表記ゆれをなくした文字列 joined を作る ---
    # 例: text = "フタマル、元気？" → joined = "ふたまる元気"（カタカナ→ひらがな、記号は消える）
    # joined は記号を消した分だけ文字の位置が text とずれる。最後に rest を元の表記のまま
    # 切り出したいので、joined の各文字が text の何文字目から来たかを origin に記録しておく。
    # 例: text = "フタマル、元気？" → origin = [0, 1, 2, 3, 5, 6]（4 文字目の「、」は消えたので飛ぶ）
    normalized: list[str] = []
    origin: list[int] = []
    for i, ch in enumerate(text):
        for c in _normalize_char(ch):
            normalized.append(c)
            origin.append(i)
    joined = "".join(normalized)

    # ウェイクワードの一覧も同じ方法で正規化する（"フタマル" と書いても "ふたまる" で探せるように）
    words = [w for w in (normalize(word) for word in wake_words) if w]

    # joined の中でウェイクワードを探す。複数見つかったら、一番前にあるものを使う
    best: tuple[int, int] | None = None  # (開始位置, 終了位置)。joined の中での位置
    for w in words:
        pos = joined.find(w)  # 見つからなければ -1
        if pos >= 0 and (best is None or pos < best[0]):
            best = (pos, pos + len(w))
    if best is None:
        return None  # ウェイクワードが無い → 起こされていない

    # --- 2. ウェイクワードの直後にある敬称と、繰り返しのウェイクワードを読み飛ばす（#6） ---
    # end は joined の中で「ここから後ろが発話の中身」になる位置。読み飛ばすたびに後ろへ進める。
    # 例: "ふたまるちゃん、ふたまるちゃん、ただいま"
    #     → 「ちゃん」→「ふたまる」→「ちゃん」の順に読み飛ばし、end は「ただいま」の手前になる
    end = best[1]
    while True:
        # 敬称は、ウェイクワードに区切り（読点・空白など）を挟まず続くときだけ取る。
        # 「ふたまる、さんぽ行こう」の「さん」は「さんぽ」の一部なので取ってはいけない。
        # origin が 1 つずつ並んでいれば、text の上でも間に何も挟まっていない（＝続いている）
        adjacent = end < len(joined) and origin[end] == origin[end - 1] + 1

        suffix: str | None = None  # 今回読み飛ばす文字列
        if adjacent:
            for h in HONORIFICS:
                if joined.startswith(h, end):  # end の位置から h が始まっているか
                    suffix = h
                    break
        if suffix is None:
            # 「ふたまる、ふたまる」のような繰り返しは、区切りを挟んでいても読み飛ばす
            for w in words:
                if joined.startswith(w, end):
                    suffix = w
                    break
        if suffix is None:
            break  # もう読み飛ばすものが無い
        end += len(suffix)

    # --- 3. 読み飛ばした位置から後ろを、元の text から切り出す ---
    # origin[end - 1] は「読み飛ばした最後の文字」が text の何文字目か。その次の文字から後ろが rest
    rest = text[origin[end - 1] + 1 :]
    # 先頭の読点・空白など（正規化すると消える文字）を飛ばし、末尾の空白も落とす
    i = 0
    while i < len(rest) and not _normalize_char(rest[i]):
        i += 1
    rest = rest[i:].rstrip()
    # 「ふたまる、ね」の「ね」のような短すぎる残りは、話しかけではなく呼びかけとして扱う
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
