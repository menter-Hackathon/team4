"""LLM 応答の表情タグの解析・除去（ストリーミング対応、仕様 §7.1）。"""

from __future__ import annotations

from dataclasses import dataclass

LLM_EXPRESSIONS = frozenset({"neutral", "happy", "sad", "surprised", "love", "sleepy"})
ALL_EXPRESSIONS = LLM_EXPRESSIONS | {"listening", "thinking"}

MAX_TAG_LEN = 16  # "[" と "]" を含む


@dataclass
class ParsedChunk:
    expression: str | None  # 表情が確定したその1回だけ値が入る
    text: str  # TTS に流してよい文字列（タグ除去済み）


class ExpressionParser:
    """先頭の表情タグを取り出し、本文中の表情タグも除去する。"""

    def __init__(self) -> None:
        self._expression: str | None = None
        self._buf = ""  # "[" から始まる未確定部分
        self._skip_ws = True  # 先頭、およびタグ直後の空白を読み飛ばす

    def feed(self, chunk: str) -> ParsedChunk:
        decided: str | None = None
        out: list[str] = []
        for ch in chunk:
            if not self._buf:
                if self._skip_ws and ch.isspace():
                    continue
                self._skip_ws = False
                if ch == "[":
                    self._buf = ch
                    continue
                if self._expression is None:
                    decided = self._decide("neutral")
                out.append(ch)
                continue

            self._buf += ch
            if ch == "]":
                raw, self._buf = self._buf, ""
                name = raw[1:-1].strip().lower()
                if self._expression is None:
                    decided = self._decide(name if name in LLM_EXPRESSIONS else "neutral")
                    self._skip_ws = True
                elif name in LLM_EXPRESSIONS:
                    self._skip_ws = True
                else:
                    out.append(raw)
            elif len(self._buf) >= MAX_TAG_LEN:
                if self._expression is None:
                    decided = self._decide("neutral")
                out.append(self._buf)
                self._buf = ""
        return ParsedChunk(decided, "".join(out))

    def flush(self) -> ParsedChunk:
        decided = self._decide("neutral") if self._expression is None else None
        text, self._buf = self._buf, ""
        return ParsedChunk(decided, text)

    def _decide(self, name: str) -> str:
        self._expression = name
        return name
