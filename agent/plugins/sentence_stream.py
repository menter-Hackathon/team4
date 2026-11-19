"""返事を 1 文ずつ声にする（返事の遅さ #39、仕様 §8.3）。

なぜ要るか:
  AivisSpeech（と OpenAI の TTS）は「文字を全部もらってから声を作る」作りの TTS。
  LiveKit はこういう TTS を、文に区切って 1 文ずつ声にする仕組み（StreamAdapter）で包むが、
  その区切り（blingfire）は日本語の「。！？」で区切らず、20 文字より短い文も次の文とつなげる。
  そのため、LLM が返事を最後まで作り終えるまで、声を作り始めなかった
  （2026-11-19 の ohsawa のログ: 3 文の返事でも AivisSpeech の合成は 1 回だけだった）。

ここでは日本語の文の区切りで包み直し、1 文目ができた時点で声にし始める。
  例: 「こんにちは！今日からよろしくね。」→「こんにちは！」を声にしている間に、LLM が続きを作る
"""

from __future__ import annotations

import re
from typing import Any

from livekit.agents import tokenize, tts
from livekit.agents.tokenize import token_stream

# 文の終わりの印。「。！？」と、その半角・三点リーダー・改行
_TERMINATORS = "。！？!?…‥\n"
# 文の終わりの印のあとに続いてもよい閉じかっこ・引用符（「〜だよ。」の 」まで同じ文に入れる）
_CLOSERS = "」』）)】〉》\"'’”"
# 絵文字（LLM は「よろしくね😊 あなたは…」のように、絵文字を文の終わりに使うことが多い）
_EMOJI = "\U0001f300-\U0001faff☀-➿"

# 文の終わり:
#   1. 「。！？」などが 1 つ以上続き、そのあとに閉じかっこ・絵文字が 0 個以上（「ごちそう？😳」で 1 文）
#   2. 絵文字が 1 つ以上続き、そのあとに空白がある（文の途中の絵文字では区切らない）
_SENTENCE_END = re.compile(
    rf"[{re.escape(_TERMINATORS)}]+[{re.escape(_CLOSERS)}{_EMOJI}]*|[{_EMOJI}]+(?=\s)"
)

# これより短い文は、次の文とつなげてから声にする（「ね。」だけを 1 回の合成にしないため）
MIN_SENTENCE_CHARS = 2
# 1 文目を声にする前に、次の文がこの文字数だけ届いているのを待つ
# （「こんにちは！」の ！ のあとに 」 が続くかもしれないので、ほんの少しだけ待つ）
MIN_CONTEXT_CHARS = 2


def split_sentences(text: str) -> list[tuple[str, int, int]]:
    """日本語の文を区切り、(文, 始まりの位置, 終わりの位置) の並びを返す。

    最後の文に終わりの印が無くても（LLM が書いている途中でも）、残りを 1 つの文として返す。
    例: split_sentences("こんにちは！今日は") → [("こんにちは！", 0, 6), ("今日は", 6, 9)]
    """
    sentences: list[tuple[str, int, int]] = []
    start = 0
    for match in _SENTENCE_END.finditer(text):
        end = match.end()
        sentence = text[start:end].strip()
        if sentence:
            sentences.append((sentence, start, end))
        start = end
    # 終わりの印の無い残り（書いている途中の文）
    rest = text[start:].strip()
    if rest:
        sentences.append((rest, start, len(text)))
    return sentences


class JapaneseSentenceTokenizer(tokenize.SentenceTokenizer):
    """split_sentences で区切る、LiveKit の文の区切り（StreamAdapter に渡す）。"""

    def tokenize(self, text: str, *, language: str | None = None) -> list[str]:
        """文字を全部まとめて区切る。例: "うん。そうだね！" → ["うん。", "そうだね！"]"""
        return [sentence for sentence, _, _ in split_sentences(text)]

    def stream(self, *, language: str | None = None) -> tokenize.SentenceStream:
        """LLM から少しずつ届く文字を区切る。文が 1 つ完成するたびに出す。"""
        return token_stream.BufferedSentenceStream(
            tokenizer=split_sentences,
            min_token_len=MIN_SENTENCE_CHARS,
            min_ctx_len=MIN_CONTEXT_CHARS,
        )


class SentenceStreamingTTS(tts.StreamAdapter):
    """「全部もらってから声にする」TTS を包み、1 文ずつ声にする TTS。

    update_options は包んだ TTS にそのまま渡す。エージェントが方言の話し方の指示を
    session.tts.update_options(instructions=...) で変えているため（agent/main.py）。
    包むだけだとこの関数が無くなり、方言の指示が黙って捨てられてしまう。
    """

    def __init__(self, inner: tts.TTS) -> None:
        super().__init__(tts=inner, sentence_tokenizer=JapaneseSentenceTokenizer())
        self._inner = inner

    def update_options(self, **kwargs: Any) -> None:
        """包んだ TTS の update_options を呼ぶ。包んだ TTS に無ければ何もしない。"""
        inner_update = getattr(self._inner, "update_options", None)
        if inner_update is not None:
            inner_update(**kwargs)


def with_sentence_streaming(inner: tts.TTS) -> tts.TTS:
    """TTS を 1 文ずつ声にするものにして返す。もともと少しずつ声にできる TTS はそのまま返す。"""
    if inner.capabilities.streaming:
        return inner
    return SentenceStreamingTTS(inner)
