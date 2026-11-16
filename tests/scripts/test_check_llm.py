import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from scripts.check_llm import run

MODEL = "google/gemma-4-E4B-it"


def _completion(content: str) -> bytes:
    """OpenAI 互換の（ストリーミングでない）応答 JSON を作る。"""
    body = {"choices": [{"message": {"role": "assistant", "content": content}}]}
    return json.dumps(body, ensure_ascii=False).encode()


def _sse(chunks: list[str]) -> bytes:
    """OpenAI 互換のストリーミング応答（Server-Sent Events）を作る。

    最初に role だけの行を入れるのは、本物の vLLM がそう返すため
    （本文が無い行を初回トークンとして数えないことを確かめる意味もある）。
    """
    lines = ['data: {"choices": [{"delta": {"role": "assistant"}}]}']
    for chunk in chunks:
        body = {"choices": [{"delta": {"content": chunk}}]}
        lines.append(f"data: {json.dumps(body, ensure_ascii=False)}")
    lines.append("data: [DONE]")
    return ("\n\n".join(lines) + "\n\n").encode()


class FakeLLM:
    """LLM サーバーの代わりに、リクエストの形で応答を切り替え、届いた内容を記録する HTTP サーバー。

    structured_content: 構造化出力の確認に返す本文（None なら正しい JSON を返す）
    stream_chunks: ストリーミングの確認に返す本文のかけら
    error_code: これを指定すると、すべてのリクエストにこのエラーコードを返す
    """

    def __init__(
        self,
        structured_content: str | None = None,
        stream_chunks: list[str] | None = None,
        error_code: int | None = None,
    ) -> None:
        self.requests: list[dict] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                fake.requests.append(payload)
                if error_code is not None:
                    self.send_response(error_code)
                    self.end_headers()
                    return
                self.send_response(200)
                if payload.get("stream"):
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    chunks = stream_chunks if stream_chunks is not None else ["おかえり", "なさい"]
                    self.wfile.write(_sse(chunks))
                else:
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    if "response_format" in payload:
                        content = (
                            structured_content
                            if structured_content is not None
                            else json.dumps({"summary": "明日テストがある"}, ensure_ascii=False)
                        )
                        self.wfile.write(_completion(content))
                    else:
                        self.wfile.write(_completion("やあ、起きたよ"))

            def log_message(self, *args) -> None:
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@pytest.fixture
def llm():
    servers: list[FakeLLM] = []

    def make(**kwargs):
        s = FakeLLM(**kwargs)
        servers.append(s)
        return s

    yield make
    for s in servers:
        s.server.shutdown()


def test_run_passes_all_checks(llm, capsys):
    s = llm()
    assert run(s.url, MODEL, timeout=5) == 0
    out = capsys.readouterr().out
    assert "[OK] 1. ストリーミングで会話が返る" in out
    assert "おかえりなさい" in out  # ストリーミングのかけらがつながっている
    assert "[OK] 2. 構造化出力で JSON が返る" in out
    assert "[OK] 3. system メッセージが 2 つ続いても通る" in out
    assert "4. 初回トークンまでの時間" in out


def test_requests_match_issue_1_shapes(llm):
    s = llm()
    assert run(s.url, MODEL, timeout=5) == 0
    streaming, structured, double_system = s.requests
    # 確認 1: ストリーミングで、仕様の system と「ただいま」を送る
    assert streaming["stream"] is True
    assert [m["role"] for m in streaming["messages"]] == ["system", "user"]
    assert streaming["messages"][1]["content"] == "ただいま"
    # 確認 2: json_schema で summary（文字列・必須）を求める
    schema = structured["response_format"]["json_schema"]["schema"]
    assert structured["response_format"]["type"] == "json_schema"
    assert schema["required"] == ["summary"]
    assert schema["properties"]["summary"]["type"] == "string"
    # 確認 3: system メッセージが 2 つ続く
    assert [m["role"] for m in double_system["messages"]] == ["system", "system"]


def test_run_fails_when_structured_output_is_not_json(llm, capsys):
    s = llm(structured_content="要約できません")
    assert run(s.url, MODEL, timeout=5) == 1
    assert "[NG] 2. 構造化出力で JSON が返る" in capsys.readouterr().out


def test_run_fails_when_structured_output_lacks_summary(llm, capsys):
    s = llm(structured_content=json.dumps({"other": "x"}))
    assert run(s.url, MODEL, timeout=5) == 1
    assert "[NG] 2. 構造化出力で JSON が返る" in capsys.readouterr().out


def test_run_fails_when_stream_is_empty(llm, capsys):
    s = llm(stream_chunks=[])
    assert run(s.url, MODEL, timeout=5) == 1
    out = capsys.readouterr().out
    assert "[NG] 1. ストリーミングで会話が返る" in out
    assert "測れませんでした" in out  # 初回トークンまでの時間も測れない


def test_run_fails_when_server_returns_error(llm, capsys):
    s = llm(error_code=500)
    assert run(s.url, MODEL, timeout=5) == 1
    out = capsys.readouterr().out
    assert "[NG] 1. ストリーミングで会話が返る" in out
    assert "[NG] 2. 構造化出力で JSON が返る" in out
    assert "[NG] 3. system メッセージが 2 つ続いても通る" in out


def test_run_fails_when_server_is_unreachable(capsys):
    # 何も待ち受けていないポートでは、すべての確認が失敗して 1 を返す
    assert run("http://127.0.0.1:9/v1", MODEL, timeout=5) == 1
    assert "結果: 失敗した確認があります" in capsys.readouterr().out
