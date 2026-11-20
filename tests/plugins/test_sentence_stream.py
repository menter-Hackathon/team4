"""返事を 1 文ずつ声にする仕組み（agent/plugins/sentence_stream.py、#39）のテスト。"""

from livekit.agents import tts

from agent.plugins.sentence_stream import (
    JapaneseSentenceTokenizer,
    SentenceStreamingTTS,
    split_sentences,
    with_sentence_streaming,
)


def test_splits_japanese_sentences_at_punctuation():
    assert JapaneseSentenceTokenizer().tokenize("うん。そうだね！元気？") == [
        "うん。",
        "そうだね！",
        "元気？",
    ]


def test_keeps_closing_brackets_and_trailing_emoji_with_the_sentence():
    assert JapaneseSentenceTokenizer().tokenize("「お水ちょうだい。」って言ったの。") == [
        "「お水ちょうだい。」",
        "って言ったの。",
    ]
    assert JapaneseSentenceTokenizer().tokenize("え、ごちそう？😳 何食べたの？") == [
        "え、ごちそう？😳",
        "何食べたの？",
    ]


def test_emoji_followed_by_space_ends_a_sentence():
    # 2026-11-19 に実際に来た返事。絵文字のあとの空白で区切る
    text = "こんにちは！今日からよろしくね😊 あなたのお名前はなんて言うの？"
    assert JapaneseSentenceTokenizer().tokenize(text) == [
        "こんにちは！",
        "今日からよろしくね😊",
        "あなたのお名前はなんて言うの？",
    ]


def test_unfinished_text_is_returned_as_the_last_sentence_with_positions():
    assert split_sentences("こんにちは！今日は") == [("こんにちは！", 0, 6), ("今日は", 6, 9)]
    assert split_sentences("") == []


async def test_stream_emits_the_first_sentence_before_the_reply_is_finished():
    stream = JapaneseSentenceTokenizer().stream()
    # LLM から少しずつ届くふり。1 文目の「！」のあとに次の文字が来た時点で、1 文目が出る
    for delta in ["こんに", "ちは！", "今日", "は晴れ", "だね。"]:
        stream.push_text(delta)
    stream.end_input()
    tokens = [data.token async for data in stream]
    await stream.aclose()
    assert tokens == ["こんにちは！", "今日は晴れだね。"]


class _FakeChunkedTTS(tts.TTS):
    """全部もらってから声にする TTS のふり（update_options の呼ばれ方だけ見る）。"""

    def __init__(self, with_update: bool = True):
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False), sample_rate=24000, num_channels=1
        )
        self.options: list[dict] = []
        if not with_update:
            self.update_options = None  # update_options の無い TTS（AivisSpeech など）

    def update_options(self, **kwargs):
        self.options.append(kwargs)

    def synthesize(self, text, *, conn_options=None):
        raise NotImplementedError


def test_chunked_tts_is_wrapped_and_keeps_update_options():
    inner = _FakeChunkedTTS()
    wrapped = with_sentence_streaming(inner)
    assert isinstance(wrapped, SentenceStreamingTTS) and wrapped.capabilities.streaming
    # 方言の指示（agent/main.py）が、包んだ TTS まで届く
    wrapped.update_options(instructions="博多弁で")
    assert inner.options == [{"instructions": "博多弁で"}]


def test_update_options_is_ignored_when_the_inner_tts_has_none():
    wrapped = with_sentence_streaming(_FakeChunkedTTS(with_update=False))
    wrapped.update_options(instructions="博多弁で")  # 何も起きない（エラーにならない）


def test_streaming_tts_is_left_as_is():
    class _Streaming(_FakeChunkedTTS):
        def __init__(self):
            tts.TTS.__init__(
                self,
                capabilities=tts.TTSCapabilities(streaming=True),
                sample_rate=24000,
                num_channels=1,
            )

    inner = _Streaming()
    assert with_sentence_streaming(inner) is inner
