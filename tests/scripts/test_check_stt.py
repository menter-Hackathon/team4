import json
import threading
import wave
from http.server import BaseHTTPRequestHandler, HTTPServer
from io import BytesIO

from openai import OpenAI

from agent.config import DEFAULT_WAKE_WORDS
from scripts.check_stt import judge, noise_wav, run_checks, silence_wav, to_markdown

MODEL = "kotoba-tech/kotoba-whisper-v2.0-faster"


class FakeSTT:
    """OpenAI 互換の /audio/transcriptions の代わり。届いた順に、決めた文字を返す。"""

    def __init__(self, texts: list[str]) -> None:
        self.bodies: list[bytes] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                fake.bodies.append(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                text = texts.pop(0) if texts else ""
                if text == "500":
                    self.send_response(500)
                    self.end_headers()
                    return
                body = json.dumps({"text": text}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args) -> None:
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.client = OpenAI(
            base_url=f"http://127.0.0.1:{self.server.server_port}/v1", api_key="x", max_retries=0
        )


def test_generated_wavs_are_16khz_mono():
    for data in (silence_wav(1.0), noise_wav(1.0)):
        with wave.open(BytesIO(data)) as w:
            assert (w.getframerate(), w.getnchannels(), w.getnframes()) == (16000, 1, 16000)


def test_judge_finds_wake_word_and_hallucination():
    assert judge("a", "下丸くん元気?", 0.5, DEFAULT_WAKE_WORDS).wake
    r = judge("b", "ご視聴ありがとうございました。", 0.5, DEFAULT_WAKE_WORDS)
    assert not r.wake and r.hallucination == "ご視聴ありがとうございました"


def test_run_checks_sends_agent_params_and_keeps_going_after_error(tmp_path):
    rec = tmp_path / "futamaru.wav"
    rec.write_bytes(silence_wav(0.5))
    fake = FakeSTT(["", "500", "フタマル、今日ね"])
    results = run_checks(fake.client, MODEL, [rec], DEFAULT_WAKE_WORDS)
    assert results[0].hallucination is None  # 無音から何も出なければ問題なし

    assert [r.name for r in results] == ["無音 3 秒", "雑音 3 秒", "futamaru.wav"]
    assert results[1].error and results[2].wake and results[2].text == "フタマル、今日ね"
    # エージェントと同じ条件（日本語・prompt「ふたまる」・モデル名）で送っている
    body = fake.bodies[0].decode(errors="ignore")
    assert MODEL in body and 'name="language"' in body and "ふたまる" in body


def test_markdown_table_has_one_row_per_result():
    rows = to_markdown([judge("x.wav", "ふたまる", 1.234, DEFAULT_WAKE_WORDS)], MODEL)
    assert "| x.wav | ふたまる | あり | - | 1.23 |" in rows


def test_any_text_from_silence_counts_as_hallucination():
    # 無音から「ごめん」のような一覧に無い言葉が出ても、でっちあげとして報告する
    fake = FakeSTT(["ごめん", ""])
    results = run_checks(fake.client, MODEL, [], DEFAULT_WAKE_WORDS)
    assert results[0].hallucination == "ごめん" and results[1].hallucination is None
