"""LLM 応答の表情タグの解析・除去（ストリーミング対応、仕様 §7.1）。

LLM には「返答の先頭に表情タグを 1 つ付ける」よう指示している（prompt.py）。
例: "[happy]おかえり！今日はどうだった？"

このファイルは、その返答から
- 表情（"happy"）を取り出す
- タグを取り除いた本文（"おかえり！今日はどうだった？"）だけを TTS に読み上げさせる
を行う。

LLM の返答は 1 文字〜数文字ずつ細切れに届く（ストリーミング）。"[hap" と "py]おかえり" のように
タグが途中で切れて届くこともあるので、確定するまで手元にためておく仕組みになっている。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# LLM に使わせる表情（プロンプトで指示している 6 種）
LLM_EXPRESSIONS = frozenset({"neutral", "happy", "sad", "surprised", "love", "sleepy"})
# エージェントが自分で出す表情も含めた全種類（聞いている・考えている）
ALL_EXPRESSIONS = LLM_EXPRESSIONS | {"listening", "thinking"}

# タグとみなす最大の長さ（"[" と "]" を含む）。これより長い括弧書きは、タグではなく本文として扱う
MAX_TAG_LEN = 16
# LLM が全角の括弧でタグを書いてくることもあるので、全角も受け付ける
OPEN_BRACKETS = "[［【"
CLOSE_BRACKETS = "]］】"
# 本文中では、英字だけの括弧（[smile] [thinking] など）はタグとみなして読み上げない
_TAG_LIKE = re.compile(r"[a-z_ ]{1,14}")


@dataclass
class ParsedChunk:
    """feed() 1 回分の結果。"""

    expression: str | None  # 表情が確定したその1回だけ値が入る（それ以外は None）
    text: str  # TTS に流してよい文字列（タグ除去済み）


class ExpressionParser:
    """先頭の表情タグを取り出し、本文中の表情タグも除去する。

    使い方（LLM の返答が細切れに届く場合）:
        parser = ExpressionParser()
        parser.feed("[hap")            → ParsedChunk(expression=None,    text="")
        parser.feed("py]おかえり")     → ParsedChunk(expression="happy", text="おかえり")
        parser.feed("！[smile]元気？") → ParsedChunk(expression=None,    text="！元気？")
        parser.flush()                 → ParsedChunk(expression=None,    text="")

    先頭にタグが無い・知らない表情名のときは "neutral"（ふつうの顔）にする。
    1 つの返答ごとに新しい ExpressionParser を作って使う。
    """

    def __init__(self) -> None:
        self._expression: str | None = None  # 確定した表情。決まるまでは None
        self._buf = ""  # "[" から始まる未確定部分（タグかどうかまだわからない文字をためておく）
        self._skip_ws = True  # 先頭、およびタグ直後の空白を読み飛ばす

    def feed(self, chunk: str) -> ParsedChunk:
        """LLM から届いた文字列 chunk を処理し、読み上げてよい部分と、確定した表情を返す。"""
        decided: str | None = None  # この呼び出しで表情が確定したら入れる
        out: list[str] = []  # 読み上げてよい文字
        for ch in chunk:
            # --- 括弧の外にいるとき ---
            if not self._buf:
                if self._skip_ws and ch.isspace():
                    continue  # 先頭やタグ直後の空白は読み上げない
                self._skip_ws = False
                if ch in OPEN_BRACKETS:
                    # タグかもしれない。閉じ括弧が来るまでためておく
                    self._buf = ch
                    continue
                if self._expression is None:
                    # タグより先に本文が来た → タグ無しの返答なので "neutral"
                    decided = self._decide("neutral")
                out.append(ch)
                continue

            # --- 括弧の中にいるとき（"[" の後ろ） ---
            self._buf += ch
            if ch in CLOSE_BRACKETS:
                # 括弧が閉じた。中身を取り出して、タグかどうか判定する
                raw, self._buf = self._buf, ""
                name = raw[1:-1].strip().lower()  # "[Happy]" → "happy"
                if self._expression is None:
                    # 先頭のタグ → 表情として使う（知らない名前なら "neutral"）
                    decided = self._decide(name if name in LLM_EXPRESSIONS else "neutral")
                    self._skip_ws = True
                elif _TAG_LIKE.fullmatch(name):
                    # 本文の途中に出てきた英字だけの括弧（[smile] など）→ タグなので読み上げない
                    self._skip_ws = True
                else:
                    # 「[注意]」のような普通の括弧書き → 本文としてそのまま読み上げる
                    out.append(raw)
            elif len(self._buf) >= MAX_TAG_LEN:
                # 長すぎてタグではない → ためていた文字を本文として出す
                if self._expression is None:
                    decided = self._decide("neutral")
                out.append(self._buf)
                self._buf = ""
        return ParsedChunk(decided, "".join(out))

    def flush(self) -> ParsedChunk:
        """返答が最後まで届いたときに呼ぶ。ためていた残りを出し、表情が未確定なら "neutral" にする。"""
        decided = self._decide("neutral") if self._expression is None else None
        text, self._buf = self._buf, ""
        return ParsedChunk(decided, text)

    def _decide(self, name: str) -> str:
        """表情を確定する。1 つの返答で表情が決まるのは 1 回だけ。"""
        self._expression = name
        return name


def strip_tags(text: str) -> str:
    """ストリーミングでない文字列から表情タグを除去する（会話ログの保存用）。

    例: "[happy]おかえり！" → "おかえり！"
    """
    parser = ExpressionParser()
    return parser.feed(text).text + parser.flush().text
