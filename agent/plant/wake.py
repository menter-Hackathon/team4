"""ウェイクワード検出と、起きている/眠っている状態・無言タイマーの管理（仕様 §5）。

ふたまるは普段は眠っていて（SLEEPING）、「ふたまる」と呼ばれると起きる（AWAKE）。
起きている間は会話し、しばらく誰も話さないと眠る。このファイルは、
- 文字起こしに「ふたまる」が入っているかの判定（detect_wake）
- 起きている / 眠っている状態と、無言の時間を数えるタイマー（WakeController）
を担当する。実際に起こす・眠らせる処理は conversation.py が行う。
"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

# 植物の状態。SLEEPING: 眠っている（ウェイクワードだけ待つ）、AWAKE: 起きて会話している
State = Literal["SLEEPING", "AWAKE"]
# WakeController.tick() の結果。none: 何もしない、nudge: 話題を振る、sleep: 眠る
Tick = Literal["none", "nudge", "sleep"]

# ウェイクワードの後ろがこの文字数未満なら、話しかけではなく呼びかけだけとみなす
MIN_REST_CHARS = 2
# ウェイクワードにすぐ続く呼びかけの敬称。発話の中身ではないので取り除く（#6）。
# STT は「ふたまるくん」を「二丸君」のように漢字で書くこともあるので、漢字の「君」も入れる。
# 入れないと「二丸君ただいま」の続きが「君ただいま」になり、LLM にそのまま渡ってしまう（2026-11-17 の試験で確認）
HONORIFICS = ("ちゃん", "くん", "さん", "君")


def _normalize_char(ch: str) -> str:
    """1 文字を正規化する。消える文字なら ""、全角→半角などで複数文字になることもある。

    例: "フ" → "ふ"、"２" → "2"、"ﾌ" → "ふ"、"、" → ""、" " → ""
    """
    out = []
    # NFKC: 全角英数字・半角カタカナなど、見た目が同じで文字コードが違うものを 1 つの形にそろえる
    for c in unicodedata.normalize("NFKC", ch):
        # カタカナ（ァ〜ヶ）は、文字コードを 0x60 引くと同じ音のひらがなになる（"フ" → "ふ"）
        if "ァ" <= c <= "ヶ":
            c = chr(ord(c) - 0x60)
        # 文字の種類の頭文字が P（句読点）・S（記号）・Z（空白）・C（制御文字）なら捨てる
        if unicodedata.category(c)[0] in "PSZC":
            continue
        out.append(c)
    return "".join(out)


def normalize(text: str) -> str:
    """カタカナ→ひらがな、空白・句読点・記号の除去。

    STT は同じ言葉でも「フタマル」「ふたまる」「ふ た ま る。」のように書き方が揺れるので、
    比べる前にこの関数で同じ形にそろえる。
    例: "フタマル、 おはよう！" → "ふたまるおはよう"、"２丸" → "2丸"
    """
    return "".join(_normalize_char(ch) for ch in text)


@dataclass
class WakeMatch:
    """detect_wake() が「ウェイクワードあり」と判定したときの結果。"""

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

    使い方:
        conversation.py が 1 秒ごとに tick(現在時刻) を呼ぶ。
        tick は「最後に誰かが話し終えた時刻（_anchor）」からの経過秒数を見て、
        - sleep_after 秒（既定 60 秒）たったら "sleep"（眠る）
        - 話題を振る秒数（性格で決まる。personality.nudge_delay）たったら "nudge"（1 回だけ）
        を返す。植物やユーザーが話している最中は数えない。

    時刻は引数で受け取るので、テストでは時計を自由に進めて確かめられる。
    """

    def __init__(self, sleep_after: float = 60.0) -> None:
        self.sleep_after = sleep_after  # この秒数だれも話さなければ眠る
        self.state: State = "SLEEPING"
        self._anchor: datetime | None = None  # 無言の時間を数え始めた時刻
        self._plant_busy = False  # 植物が考え中・話し中か
        self._user_speaking = False  # ユーザーが話している最中か
        self._nudge_delay: float | None = None  # 話題を振るまでの秒数。None なら振らない
        self._nudged = False  # 今回の無言の間に、もう話題を振ったか

    def wake(self, now: datetime) -> None:
        """起きる。無言タイマーを今から数え直す。"""
        self.state = "AWAKE"
        self._anchor = now
        self._nudged = False

    def sleep(self) -> None:
        """眠る。"""
        self.state = "SLEEPING"

    def on_user_activity(self, now: datetime) -> None:
        """ユーザーが何か話した。無言タイマーを数え直し、また話題を振れるようにする。"""
        self._anchor = now
        self._nudged = False

    def set_user_speaking(self, speaking: bool, now: datetime) -> None:
        """ユーザーが話し始めた（True）／話し終えた（False）。話し終えた時刻から無言を数える。"""
        self._user_speaking = speaking
        if not speaking:
            self._anchor = now

    def set_plant_busy(self, busy: bool, now: datetime) -> None:
        """植物が考え始めた・話し始めた（True）／話し終えた（False）。"""
        # 話し終えた瞬間（True → False に変わったとき）から無言を数え直す
        if self._plant_busy and not busy:
            self._anchor = now
        self._plant_busy = busy

    def set_nudge_delay(self, seconds: float | None) -> None:
        """話題を振るまでの秒数を設定する。起きるたびに、その株の性格から決めて渡される。"""
        self._nudge_delay = seconds

    def tick(self, now: datetime) -> Tick:
        """今の時刻から、眠る・話題を振る・何もしない、のどれかを決める。

        例（sleep_after=60、話題を振るのは 25 秒）: 最後に話してから
        10 秒 → "none"、25 秒 → "nudge"、30 秒 → "none"（2 回目は振らない）、60 秒 → "sleep"
        """
        # 眠っている・誰かが話している最中は、無言ではないので何もしない
        if self.state != "AWAKE" or self._plant_busy or self._user_speaking or self._anchor is None:
            return "none"
        silence = (now - self._anchor).total_seconds()  # 無言が続いている秒数
        if silence >= self.sleep_after:
            self.sleep()
            return "sleep"
        # 話題を振るのは、無言が続く間に 1 回だけ（振ったあと黙っていたら、そのまま眠る）
        if self._nudge_delay is not None and not self._nudged and silence >= self._nudge_delay:
            self._nudged = True
            return "nudge"
        return "none"
