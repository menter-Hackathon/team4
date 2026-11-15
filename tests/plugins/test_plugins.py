"""モック HTTP サーバーに対して STT/TTS/LLM のリクエスト形式を確認する（仕様 §11）。"""

import json
from dataclasses import replace

import pytest
from aiohttp import web
from livekit import rtc

from agent.config import TTS_INSTRUCTIONS, load_config
from agent.plugins.llm import create_json_completer, create_llm
from agent.plugins.openai_tts import create_tts
from agent.plugins.whisper_stt import create_stt


class MockServer:
    def __init__(self):
        self.requests: dict[str, list[tuple[dict, bytes]]] = {}

    async def handle(self, request: web.Request) -> web.StreamResponse:
        body = await request.read()
        self.requests.setdefault(request.path, []).append((dict(request.headers), body))
        if request.path.endswith("/audio/transcriptions"):
            return web.json_response({"text": "ふたまる"})
        if request.path.endswith("/audio/speech"):
            return web.Response(body=b"\0\0" * 2400, content_type="application/octet-stream")
        if request.path.endswith("/chat/completions"):
            return web.json_response(
                {
                    "id": "c1", "object": "chat.completion", "created": 0, "model": "m",
                    "choices": [{"index": 0, "finish_reason": "stop",
                                 "message": {"role": "assistant", "content": '{"ok": true}'}}],
                }
            )
        return web.Response(status=404)


@pytest.fixture
async def server():
    mock = MockServer()
    app = web.Application()
    app.router.add_route("POST", "/{tail:.*}", mock.handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    mock.base = f"http://127.0.0.1:{port}/v1"
    yield mock
    await runner.cleanup()


@pytest.fixture
def cfg(server):
    return load_config(
        {
            "LLM_BASE_URL": server.base,
            "LLM_MODEL": "google/gemma-4-E4B-it",
            "STT_BASE_URL": server.base,
            "OPENAI_API_KEY": "sk-test",
            "TTS_BASE_URL": server.base,
        }
    )


async def test_stt_request(server, cfg):
    frame = rtc.AudioFrame(b"\0\0" * 1600, sample_rate=16000, num_channels=1, samples_per_channel=1600)
    ev = await create_stt(cfg).recognize(frame)
    _, body = server.requests["/v1/audio/transcriptions"][-1]
    assert b'name="language"\r\n\r\nja' in body and "ふたまる".encode() in body
    assert b"kotoba-tech/kotoba-whisper-v2.0-faster" in body and ev.alternatives[0].text == "ふたまる"


async def test_tts_request(server, cfg):
    async with create_tts(cfg).synthesize("こんにちは") as stream:
        async for _ in stream:
            pass
    headers, body = server.requests["/v1/audio/speech"][-1]
    req = json.loads(body)
    assert req["model"] == "gpt-4o-mini-tts" and req["voice"] == "coral"
    assert req["response_format"] == "pcm" and req["instructions"] == TTS_INSTRUCTIONS
    assert req["input"] == "こんにちは" and headers["Authorization"] == "Bearer sk-test"


def test_tts_defaults_to_openai(cfg):
    tts = create_tts(replace(cfg, tts_base_url=None))
    assert str(tts._client.base_url).startswith("https://api.openai.com/v1")


async def test_json_completer_request(server, cfg):
    out = await create_json_completer(cfg)([{"role": "user", "content": "x"}], {"type": "object"})
    _, body = server.requests["/v1/chat/completions"][-1]
    req = json.loads(body)
    assert out == '{"ok": true}' and req["response_format"]["type"] == "json_schema"
    assert req["response_format"]["json_schema"]["schema"] == {"type": "object"}
    assert req["model"] == "google/gemma-4-E4B-it" and "enable_thinking" not in json.dumps(req)


def test_create_llm_model(cfg):
    assert create_llm(cfg).model == "google/gemma-4-E4B-it"


# --- final review I2: SDK の自動リトライでタイムアウトが何倍にもならない ---
def test_clients_do_not_retry(cfg):
    assert create_stt(cfg)._client.max_retries == 0
    assert create_tts(cfg)._client.max_retries == 0
    assert create_llm(cfg)._client.max_retries == 0


def test_json_completer_client_does_not_retry(cfg, monkeypatch):
    import agent.plugins.llm as mod

    seen = {}
    real = mod.AsyncOpenAI

    def spy(**kw):
        seen.update(kw)
        return real(**kw)

    monkeypatch.setattr(mod, "AsyncOpenAI", spy)
    create_json_completer(cfg)
    assert seen["max_retries"] == 0
