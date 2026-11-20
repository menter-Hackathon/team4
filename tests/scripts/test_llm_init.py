import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from scripts.llm_init import gpu_share, run

MODEL = "gemma4:e4b-it-q4_K_M"


class FakeOllama:
    """Ollama の代わりの HTTP サーバー。届いたリクエストを (パス, JSON) で記録する。

    pull_code: /api/pull に返す応答コード
    generate_code: /api/generate に返す応答コード
    size_vram: /api/ps で返す、GPU に載った量（モデル全体は 100）
    """

    def __init__(self, pull_code: int = 200, generate_code: int = 200, size_vram: int = 100):
        self.requests: list[tuple[str, dict | None]] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def _reply(self, code: int, body: object) -> None:
                self.send_response(code)
                self.end_headers()
                self.wfile.write(json.dumps(body).encode())

            def do_GET(self) -> None:
                fake.requests.append((self.path, None))
                if self.path == "/api/version":
                    self._reply(200, {"version": "0.40.16"})
                elif self.path == "/api/ps":
                    self._reply(
                        200, {"models": [{"name": MODEL, "size": 100, "size_vram": size_vram}]}
                    )
                else:
                    self._reply(404, {})

            def do_POST(self) -> None:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                fake.requests.append((self.path, body))
                if self.path == "/api/pull":
                    self._reply(pull_code, {"status": "success"})
                elif self.path == "/api/generate":
                    self._reply(generate_code, {"response": "", "done": True})
                else:
                    self._reply(404, {})

            def log_message(self, *args) -> None:
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def paths(self) -> list[str]:
        return [p for p, _ in self.requests]


@pytest.fixture
def ollama():
    servers: list[FakeOllama] = []

    def make(**kw):
        s = FakeOllama(**kw)
        servers.append(s)
        return s

    yield make
    for s in servers:
        s.server.shutdown()


def test_pulls_then_loads_the_model_and_keeps_it(ollama):
    s = ollama()
    assert run(s.url, MODEL, attempts=3, delay=0) == 0
    assert s.paths() == ["/api/version", "/api/pull", "/api/generate", "/api/ps"]
    assert s.requests[1][1] == {"model": MODEL, "stream": False}
    # 読み込んだモデルを外さない（keep_alive=-1）
    assert s.requests[2][1]["keep_alive"] == -1


def test_fails_when_the_model_cannot_be_downloaded(ollama):
    s = ollama(pull_code=500)
    assert run(s.url, MODEL, attempts=3, delay=0) == 1
    assert "/api/generate" not in s.paths()


def test_fails_when_the_model_cannot_be_loaded(ollama):
    s = ollama(generate_code=500)
    assert run(s.url, MODEL, attempts=3, delay=0) == 1


def test_partial_gpu_load_warns_but_succeeds(ollama, capsys):
    # GPU に一部しか載らなくても、遅いだけで動くので止めない。注意だけ出す
    s = ollama(size_vram=60)
    assert run(s.url, MODEL, attempts=3, delay=0) == 0
    assert "60%" in capsys.readouterr().out


def test_gives_up_when_ollama_never_starts():
    assert run("http://127.0.0.1:9", MODEL, attempts=2, delay=0) == 1


def test_gpu_share():
    ps = json.dumps({"models": [{"name": MODEL, "size": 200, "size_vram": 150}]}).encode()
    assert gpu_share(ps, MODEL) == 0.75
    assert gpu_share(ps, "other") is None
    assert gpu_share(b'{"models": []}', MODEL) is None
