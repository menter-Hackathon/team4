"""考え中のつなぎ（「んー…」）の確かめ（#39、メンター FB 3）。"""

import asyncio

import pytest
from livekit import rtc

from agent.filler import FillerAudio, trim_trailing_silence, with_filler


async def frames(items, *, wait=0.0, fail=False):
    """本物の TTS の代わり。wait 秒待ってから items を 1 つずつ出す。"""
    await asyncio.sleep(wait)
    for x in items:
        yield x
    if fail:
        raise RuntimeError("tts failed")


async def collect(gen):
    return [x async for x in gen]


async def test_fast_reply_has_no_filler():
    out = await collect(with_filler(frames(["a", "b"]), lambda: ["f"], delay=0.05))
    assert out == ["a", "b"]


async def test_slow_reply_starts_with_filler():
    out = await collect(with_filler(frames(["a", "b"], wait=0.1), lambda: ["f1", "f2"], delay=0.02))
    assert out == ["f1", "f2", "a", "b"]


async def test_no_filler_ready_means_just_wait():
    out = await collect(with_filler(frames(["a"], wait=0.05), lambda: None, delay=0.01))
    assert out == ["a"]


async def test_delay_zero_turns_filler_off():
    out = await collect(with_filler(frames(["a"], wait=0.05), lambda: ["f"], delay=0))
    assert out == ["a"]


async def test_errors_from_tts_are_raised():
    with pytest.raises(RuntimeError):
        await collect(with_filler(frames(["a"], fail=True), lambda: ["f"], delay=1))


async def test_cancelling_stops_the_inner_stream():
    stopped = asyncio.Event()

    async def endless():
        try:
            while True:
                await asyncio.sleep(0.01)
                yield "a"
        finally:
            stopped.set()

    gen = with_filler(endless(), lambda: None, delay=1)
    assert await gen.__anext__() == "a"
    await gen.aclose()  # 持ち主が割り込んだときなど、LiveKit が途中で止める
    await asyncio.wait_for(stopped.wait(), 1)


class FakeStream:
    def __init__(self, text, fail):
        self.text, self.fail = text, fail

    async def __aenter__(self):
        if self.fail:
            raise RuntimeError("down")
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        async def gen():
            for i in range(2):
                yield type("Ev", (), {"frame": f"{self.text}-{i}"})()

        return gen()


class FakeTTS:
    def __init__(self, fail=False):
        self.fail = fail
        self.texts = []

    def synthesize(self, text):
        self.texts.append(text)
        return FakeStream(text, self.fail)


async def test_prepare_synthesizes_each_phrase_once():
    tts = FakeTTS()
    filler = FillerAudio(("んー…", "えっとね…"), choose=lambda xs: xs[-1], trim=None)
    assert filler.pick() is None  # まだ用意できていない
    await filler.prepare(tts)
    assert tts.texts == ["んー…", "えっとね…"]
    assert filler.pick() == ["えっとね…-0", "えっとね…-1"]


async def test_prepare_failure_leaves_no_filler(caplog):
    filler = FillerAudio(("んー…",))
    await filler.prepare(FakeTTS(fail=True))  # 例外を外に出さない
    assert filler.pick() is None
    assert "filler" in caplog.text


def frame(level):
    """大きさ level の音が 240 サンプル続く、0.01 秒のフレーム。"""
    import array

    return rtc.AudioFrame(array.array("h", [level] * 240).tobytes(), 24000, 1, 240)


def test_trim_trailing_silence():
    voice, quiet = frame(3000), frame(100)
    assert trim_trailing_silence([quiet, voice, quiet, voice, quiet, quiet]) == [
        quiet,
        voice,
        quiet,
        voice,
    ]  # 声の間の無音と、先頭の無音は残す
    assert trim_trailing_silence([quiet, quiet]) == []
    assert trim_trailing_silence([]) == []
