"""AivisSpeech の TTS と、声を選ぶコマンドを、偽の AivisSpeech Engine で確かめる（仕様 §8.3、#56）。"""

import io
import json
import wave
from argparse import Namespace
from dataclasses import replace

import aiohttp
import pytest
from aiohttp import web
from livekit.agents import APIConnectionError, APIConnectOptions, APIStatusError

from agent.config import load_config
from agent.plugins.aivis_tts import AivisOptions, AivisTTS, create_aivis_tts
from scripts.aivis_voices import list_voices, make_samples, run

NO_RETRY = APIConnectOptions(max_retry=0, timeout=2)


def make_wav(seconds: float, rate: int = 24000) -> bytes:
    """無音の WAV（16bit・モノラル）を作る。"""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\0\0" * int(rate * seconds))
    return buf.getvalue()


class FakeAivis:
    """AivisSpeech Engine の代わり。受け取ったリクエストを覚えておく。"""

    def __init__(self, fail_synthesis: bool = False):
        self.fail_synthesis = fail_synthesis
        self.queries: list[dict] = []  # /audio_query のクエリ
        self.synthesis: list[tuple[dict, dict]] = []  # /synthesis の（クエリ, 本文）

    async def audio_query(self, request: web.Request) -> web.Response:
        self.queries.append(dict(request.query))
        # 本物の AivisSpeech が返す設計図の一部（既定の出力は 44.1kHz・ステレオなし）
        return web.json_response(
            {
                "accent_phrases": [],
                "speedScale": 1.0,
                "pitchScale": 0.0,
                "outputSamplingRate": 44100,
            }
        )

    async def synthesize(self, request: web.Request) -> web.Response:
        self.synthesis.append((dict(request.query), json.loads(await request.read())))
        if self.fail_synthesis:
            return web.Response(status=500, text="boom")
        return web.Response(body=make_wav(0.5), content_type="audio/wav")

    async def speakers(self, request: web.Request) -> web.Response:
        return web.json_response(
            [
                {
                    "name": "Anneli",
                    "speaker_uuid": "u1",
                    "styles": [{"name": "ノーマル", "id": 100}, {"name": "喜び", "id": 101}],
                },
                {"name": "まい", "speaker_uuid": "u2", "styles": [{"name": "ノーマル", "id": 200}]},
            ]
        )


async def start(fake: FakeAivis) -> tuple[web.AppRunner, str]:
    app = web.Application()
    app.router.add_post("/audio_query", fake.audio_query)
    app.router.add_post("/synthesis", fake.synthesize)
    app.router.add_get("/speakers", fake.speakers)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, f"http://127.0.0.1:{port}"


@pytest.fixture
async def aivis():
    fake = FakeAivis()
    runner, fake.base = await start(fake)
    yield fake
    await runner.cleanup()


async def collect(tts: AivisTTS, text: str) -> list:
    """synthesize の音声フレームを全部集める。"""
    frames = []
    async with tts.synthesize(text, conn_options=NO_RETRY) as stream:
        async for ev in stream:
            frames.append(ev.frame)
    return frames


async def test_synthesize_sends_query_then_synthesis_and_returns_24k_audio(aivis):
    tts = AivisTTS(AivisOptions(base_url=aivis.base, speaker=100, speed=1.1, pitch=0.05))
    frames = await collect(tts, "こんにちは")
    await tts.aclose()
    # 1. 読み方の設計図を、文とスタイル ID で頼んでいる
    assert aivis.queries == [{"text": "こんにちは", "speaker": "100"}]
    # 2. 速さ・高さ・出力の形式を書き換えてから、声を作らせている
    query, body = aivis.synthesis[0]
    assert query == {"speaker": "100"}
    assert body["speedScale"] == 1.1 and body["pitchScale"] == 0.05
    assert body["outputSamplingRate"] == 24000 and body["outputStereo"] is False
    assert "accent_phrases" in body  # 設計図の残りはそのまま渡す
    # 3. 返ってきた WAV（0.5 秒）が 24kHz・モノラルの音声になっている
    assert all(f.sample_rate == 24000 and f.num_channels == 1 for f in frames)
    assert sum(f.samples_per_channel for f in frames) == 12000


async def test_server_error_becomes_api_status_error(aivis):
    aivis.fail_synthesis = True
    tts = AivisTTS(AivisOptions(base_url=aivis.base, speaker=100))
    with pytest.raises(APIStatusError) as e:
        await collect(tts, "こんにちは")
    await tts.aclose()
    assert e.value.status_code == 500


async def test_unreachable_engine_becomes_connection_error():
    # 何も待ち受けていないポートにつなぐ
    tts = AivisTTS(AivisOptions(base_url="http://127.0.0.1:9", speaker=100))
    with pytest.raises(APIConnectionError):
        await collect(tts, "こんにちは")
    await tts.aclose()


def test_create_aivis_tts_uses_the_settings():
    cfg = load_config(
        {
            "LLM_BASE_URL": "http://gpu:8000/v1",
            "LLM_MODEL": "m",
            "STT_BASE_URL": "http://gpu:8001/v1",
            "TTS_PROVIDER": "aivis",
            "AIVIS_SPEAKER": "200",
            "AIVIS_PITCH": "0.03",
            "TTS_TIMEOUT": "7",
        }
    )
    tts = create_aivis_tts(cfg)
    assert tts.opts == AivisOptions(
        base_url="http://127.0.0.1:10101", speaker=200, speed=1.0, pitch=0.03, timeout=7.0
    )
    assert tts.sample_rate == 24000 and tts.num_channels == 1
    with pytest.raises(ValueError):
        create_aivis_tts(replace(cfg, aivis_speaker=None))


# --- 声を選ぶコマンド（scripts/aivis_voices.py） ---
async def test_list_voices_flattens_styles(aivis):
    async with aiohttp.ClientSession() as session:
        voices = await list_voices(session, aivis.base)
    assert voices == [
        (100, "Anneli", "ノーマル"),
        (101, "Anneli", "喜び"),
        (200, "まい", "ノーマル"),
    ]


async def test_make_samples_writes_one_wav_per_speaker(aivis, tmp_path):
    async with aiohttp.ClientSession() as session:
        paths = await make_samples(session, aivis.base, [100, 200], "やあ", tmp_path, pitch=0.05)
    assert [p.name for p in paths] == ["100.wav", "200.wav"]
    assert all(p.read_bytes().startswith(b"RIFF") for p in paths)
    assert [q["speaker"] for q in aivis.queries] == ["100", "200"]
    assert aivis.synthesis[0][1]["pitchScale"] == 0.05


async def test_run_reports_an_unreachable_engine(capsys):
    args = Namespace(list=True, speaker=[], text="", out="", speed=1.0, pitch=0.0)
    assert await run(args, "http://127.0.0.1:9") == 1
    assert "つながりません" in capsys.readouterr().out
