import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

import pytest

from scripts.tts_init import main, parse_models, run, style_ids

RASETSUN = "9f36ec0d-8dac-42dc-aff4-149c5f99faad"
MAO = "a59cb814-0083-4369-8542-f51a29e72af7"
SPEAKERS = [
    {"name": "らせつん", "styles": [{"name": "標準", "id": 893625024}]},
    {"name": "まお", "styles": [{"name": "あまあま", "id": 888753762}]},
]


class FakeAivis:
    """AivisSpeech Engine の代わりの HTTP サーバー。届いたリクエストを (メソッド, パス, 本文) で記録する。

    installed: 最初から入っているモデルの UUID。install が成功すると増える
    install_code: /aivm_models/install に返す応答コード
    synthesis_code: /synthesis に返す応答コード
    """

    def __init__(self, installed: set[str], install_code: int = 200, synthesis_code: int = 200):
        self.requests: list[tuple[str, str, bytes]] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def _reply(self, code: int, body: object = None) -> None:
                self.send_response(code)
                self.end_headers()
                self.wfile.write(json.dumps(body).encode() if body is not None else b"")

            def do_GET(self) -> None:
                path = urlparse(self.path).path
                fake.requests.append(("GET", path, b""))
                if path == "/version":
                    self._reply(200, "1.2.0")
                elif path.startswith("/aivm_models/"):
                    self._reply(200 if path.rsplit("/", 1)[1] in installed else 404, {})
                elif path == "/speakers":
                    self._reply(200, SPEAKERS)
                else:
                    self._reply(404)

            def do_POST(self) -> None:
                path = urlparse(self.path).path
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                fake.requests.append(("POST", path, body))
                if path == "/aivm_models/install":
                    self._reply(install_code)
                elif path == "/audio_query":
                    self._reply(200, {"accent_phrases": []})
                elif path == "/synthesis":
                    self._reply(synthesis_code)
                else:
                    self._reply(404)

            def log_message(self, *args) -> None:
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def calls(self) -> list[str]:
        return [f"{m} {p}" for m, p, _ in self.requests]


@pytest.fixture
def aivis():
    servers: list[FakeAivis] = []

    def make(**kw):
        s = FakeAivis(**kw)
        servers.append(s)
        return s

    yield make
    for s in servers:
        s.server.shutdown()


def test_parse_models_drops_blanks():
    assert parse_models(f" {RASETSUN}, {MAO},, ") == [RASETSUN, MAO]
    assert parse_models("") == []


def test_style_ids_collects_every_style():
    assert style_ids(json.dumps(SPEAKERS).encode()) == {893625024, 888753762}


def test_installs_missing_models_then_warms_up(aivis):
    s = aivis(installed={MAO})
    url = "https://example.invalid/{uuid}.aivmx"
    assert run(s.url, [RASETSUN, MAO], 893625024, attempts=3, delay=0, download_url=url) == 0
    # らせつんだけを入れる（まおは入っている）。そのあと声の一覧を見て、一度読み上げる
    assert s.calls() == [
        "GET /version",
        f"GET /aivm_models/{RASETSUN}",
        "POST /aivm_models/install",
        f"GET /aivm_models/{MAO}",
        "GET /speakers",
        "POST /audio_query",
        "POST /synthesis",
    ]
    # install には、そのモデルの UUID 入りのダウンロード URL を渡している
    install_body = s.requests[2][2].decode()
    assert f"https://example.invalid/{RASETSUN}.aivmx" in install_body


def test_installed_models_are_not_downloaded_again(aivis):
    s = aivis(installed={RASETSUN, MAO})
    assert run(s.url, [RASETSUN, MAO], 893625024, attempts=3, delay=0) == 0
    assert "POST /aivm_models/install" not in s.calls()


def test_fails_when_install_fails(aivis):
    s = aivis(installed=set(), install_code=500)
    assert run(s.url, [RASETSUN], 893625024, attempts=3, delay=0) == 1
    # 失敗したら、読み上げまで進まない
    assert "POST /synthesis" not in s.calls()


def test_fails_when_the_speaker_id_is_not_installed(aivis):
    s = aivis(installed={RASETSUN})
    assert run(s.url, [RASETSUN], 123, attempts=3, delay=0) == 1
    assert "POST /audio_query" not in s.calls()


def test_fails_when_synthesis_fails(aivis):
    s = aivis(installed={RASETSUN}, synthesis_code=500)
    assert run(s.url, [RASETSUN], 893625024, attempts=3, delay=0) == 1


def test_gives_up_when_the_engine_never_starts():
    # 何も待ち受けていないポートでは、試す回数を使い切ってから失敗する
    assert run("http://127.0.0.1:9", [RASETSUN], 893625024, attempts=2, delay=0) == 1


def test_main_does_nothing_unless_tts_provider_is_aivis():
    # 声に OpenAI を使うときは、AivisSpeech につなぎに行かずに成功で終わる（agent の起動を止めない）
    assert main({"TTS_PROVIDER": "openai", "AIVIS_URL": "http://127.0.0.1:9"}) == 0
    assert main({"AIVIS_URL": "http://127.0.0.1:9"}) == 0
