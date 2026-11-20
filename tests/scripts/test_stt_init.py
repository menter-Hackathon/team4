import io
import threading
import wave
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from scripts.stt_init import run, silent_wav

MODEL = "kotoba-tech/kotoba-whisper-v2.0-faster"


class FakeSpeaches:
    """Speaches の代わりに、決めた順番で応答を返し、届いたリクエストを記録する HTTP サーバー。

    responses: パスごとの応答コードの列。列を使い切ったら最後の値を返し続ける
    """

    def __init__(self, responses: dict[str, list[int]]) -> None:
        self.requests: list[tuple[str, bytes]] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                fake.requests.append((self.path, body))
                codes = responses.get(self.path, [404])
                code = codes.pop(0) if len(codes) > 1 else codes[0]
                self.send_response(code)
                self.end_headers()
                self.wfile.write(b'{"text": ""}')

            def log_message(self, *args) -> None:
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def paths(self) -> list[str]:
        return [p for p, _ in self.requests]


@pytest.fixture
def speaches():
    servers: list[FakeSpeaches] = []

    def make(responses):
        s = FakeSpeaches(responses)
        servers.append(s)
        return s

    yield make
    for s in servers:
        s.server.shutdown()


def test_silent_wav_is_one_second_16khz_mono_16bit():
    with wave.open(io.BytesIO(silent_wav()), "rb") as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (16000, 1, 2)
        assert w.getnframes() == 16000


def test_downloads_the_model_then_transcribes_once(speaches):
    s = speaches({f"/v1/models/{MODEL}": [200], "/v1/audio/transcriptions": [200]})
    assert run(s.url, MODEL, attempts=3, delay=0) == 0
    assert s.paths() == [f"/v1/models/{MODEL}", "/v1/audio/transcriptions"]
    # 文字起こしのリクエストに、読み込ませたいモデル名が入っている
    assert MODEL.encode() in s.requests[1][1]


def test_already_downloaded_model_is_fine(speaches):
    s = speaches({f"/v1/models/{MODEL}": [201], "/v1/audio/transcriptions": [200]})
    assert run(s.url, MODEL, attempts=3, delay=0) == 0


def test_retries_while_speaches_is_starting(speaches):
    s = speaches({f"/v1/models/{MODEL}": [503, 503, 200], "/v1/audio/transcriptions": [200]})
    assert run(s.url, MODEL, attempts=5, delay=0) == 0
    assert s.paths().count(f"/v1/models/{MODEL}") == 3


def test_retries_when_speaches_is_not_up_yet():
    # 何も待ち受けていないポートでは、試す回数を使い切ってから失敗する
    assert run("http://127.0.0.1:9", MODEL, attempts=2, delay=0) == 1


def test_unknown_model_fails_fast_without_transcribing(speaches):
    s = speaches({f"/v1/models/{MODEL}": [404]})
    assert run(s.url, MODEL, attempts=5, delay=0) == 1
    assert s.paths() == [f"/v1/models/{MODEL}"]


def test_fails_when_the_model_cannot_be_loaded(speaches):
    s = speaches({f"/v1/models/{MODEL}": [200], "/v1/audio/transcriptions": [500]})
    assert run(s.url, MODEL, attempts=3, delay=0) == 1
