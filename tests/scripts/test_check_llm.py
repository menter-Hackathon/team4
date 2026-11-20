import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from scripts.check_llm import WAKE_GREETING_INSTRUCTION, leading_expression, parse_args, run

MODEL = "google/gemma-4-E4B-it"

# 振り返りの確認で、LLM が返したことにする JSON（エージェントの検査を通る形）
REFLECTION_JSON = json.dumps(
    {
        "summary": "持ち主は明日、数学のテストがある",
        "new_memories": [
            {"content": "持ち主は明日数学のテストがある", "kind": "event", "importance": 0.8}
        ],
        "updated_memories": [],
        "trait_deltas": {"talkativeness": 0.0, "affection": 0.01, "energy": 0.0, "curiosity": 0.02},
    },
    ensure_ascii=False,
)


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


def _is_double_system(payload: dict) -> bool:
    """system メッセージが 2 つ続くリクエストかどうか（確認 3a の形）。"""
    messages = payload.get("messages") or []
    return len(messages) == 2 and all(m.get("role") == "system" for m in messages)


class FakeLLM:
    """LLM サーバーの代わりに、リクエストの形で応答を切り替え、届いた内容を記録する HTTP サーバー。

    structured_content: 構造化出力の確認に返す本文（None なら検査を通る JSON を返す）
    stream_chunks: ストリーミングの確認に返す本文のかけら（None なら表情タグ付きの返事）
    fail_double_system: True なら、system が 2 つ続く形にだけ 400 を返す
    error_code: これを指定すると、すべてのリクエストにこのエラーコードを返す
    """

    def __init__(
        self,
        structured_content: str | None = None,
        stream_chunks: list[str] | None = None,
        fail_double_system: bool = False,
        error_code: int | None = None,
    ) -> None:
        self.requests: list[dict] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                fake.requests.append(payload)
                if error_code is not None or (fail_double_system and _is_double_system(payload)):
                    self.send_response(error_code if error_code is not None else 400)
                    self.end_headers()
                    return
                self.send_response(200)
                if payload.get("stream"):
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    chunks = (
                        stream_chunks
                        if stream_chunks is not None
                        else ["[happy]おかえり", "なさい"]
                    )
                    self.wfile.write(_sse(chunks))
                else:
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    if "response_format" in payload:
                        content = (
                            structured_content
                            if structured_content is not None
                            else REFLECTION_JSON
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


def test_leading_expression_reads_the_tag_at_the_start():
    assert leading_expression("[happy]おかえり！") == "happy"
    assert leading_expression("［sleepy］ねむい") == "sleepy"  # 全角の括弧も受け付ける
    assert leading_expression("おかえり！") is None
    assert leading_expression("[smile]やあ") is None  # 知らない名前はタグとして認めない
    assert leading_expression("やあ [happy]") is None  # 先頭でなければタグではない


def test_run_passes_all_checks(llm, capsys):
    s = llm()
    assert run(s.url, MODEL, timeout=5) == 0
    out = capsys.readouterr().out
    assert "[OK] 1. ストリーミングで会話が返り、表情タグが付く" in out
    assert "[happy]おかえりなさい" in out  # ストリーミングのかけらがつながっている
    assert "[OK] 2. 構造化出力（振り返りのスキーマ）が通る" in out
    assert "[OK] 3a. system メッセージが 2 つ続く形（参考）" in out
    assert "[OK] 3b. system ＋ user のト書きの形（今の形）" in out
    assert "4. 初回トークンまでの時間" in out


def test_requests_match_agent_shapes(llm):
    s = llm()
    assert run(s.url, MODEL, timeout=5) == 0
    streaming, structured, double_system, greeting = s.requests
    # 確認 1: ストリーミングで、本番のプロンプト（表情タグの指示入り）と「ただいま」を送る
    assert streaming["stream"] is True
    assert "表情タグ" in streaming["messages"][0]["content"]
    assert streaming["messages"][1] == {"role": "user", "content": "ただいま"}
    # 確認 2: 振り返りのスキーマ（4 つの必須キー、入れ子の new_memories）で頼む
    assert structured["response_format"]["type"] == "json_schema"
    assert structured["response_format"]["json_schema"]["name"] == "reflection"
    schema = structured["response_format"]["json_schema"]["schema"]
    assert schema["required"] == ["summary", "new_memories", "updated_memories", "trait_deltas"]
    assert schema["properties"]["new_memories"]["items"]["required"] == [
        "content",
        "kind",
        "importance",
    ]
    assert "記憶係" in structured["messages"][0]["content"]
    # 確認 3a: system が 2 つ続き、2 つ目が挨拶のト書き
    assert [m["role"] for m in double_system["messages"]] == ["system", "system"]
    assert double_system["messages"][1]["content"] == WAKE_GREETING_INSTRUCTION
    # 確認 3b: ト書きは user ロールで、丸括弧で囲んで渡す（main.py の direction_ctx と同じ）
    assert [m["role"] for m in greeting["messages"]] == ["system", "user"]
    assert greeting["messages"][1]["content"] == f"（{WAKE_GREETING_INSTRUCTION}）"


def test_reasoning_effort_is_not_sent_by_default(llm):
    # 何も指定しなければ、今までどおり reasoning_effort を送らない（vLLM・OpenAI の gpt-4o-mini 向け）
    s = llm()
    assert run(s.url, MODEL, timeout=5) == 0
    assert all("reasoning_effort" not in payload for payload in s.requests)


def test_reasoning_effort_is_sent_in_every_check_when_set(llm, capsys):
    # Ollama の Gemma 4 は思考モードが既定でオン。エージェントと同じく、すべての確認で切って測る（#54）
    s = llm()
    assert run(s.url, MODEL, timeout=5, reasoning_effort="none") == 0
    assert len(s.requests) == 4
    assert all(payload["reasoning_effort"] == "none" for payload in s.requests)
    # どの設定で測ったかが Issue に貼った結果からわかるように、表示にも出す
    assert "LLM_REASONING_EFFORT=none" in capsys.readouterr().out


def test_reasoning_effort_is_read_from_the_environment(monkeypatch):
    # main() と同じく、引数が無ければ環境変数 LLM_REASONING_EFFORT を使う。空なら送らない
    monkeypatch.setenv("LLM_REASONING_EFFORT", "none")
    assert parse_args(["--base-url", "http://x/v1", "--model", MODEL]).reasoning_effort == "none"
    monkeypatch.setenv("LLM_REASONING_EFFORT", "  ")
    assert parse_args(["--base-url", "http://x/v1", "--model", MODEL]).reasoning_effort is None
    monkeypatch.delenv("LLM_REASONING_EFFORT")
    args = parse_args(["--base-url", "http://x/v1", "--model", MODEL, "--reasoning-effort", "low"])
    assert args.reasoning_effort == "low"


def test_run_fails_when_reply_has_no_expression_tag(llm, capsys):
    s = llm(stream_chunks=["おかえり", "なさい"])
    assert run(s.url, MODEL, timeout=5) == 1
    out = capsys.readouterr().out
    assert "[NG] 1. ストリーミングで会話が返り、表情タグが付く" in out
    assert "neutral 固定" in out


def test_run_passes_even_if_double_system_form_is_rejected(llm, capsys):
    # 3a は参考なので、サーバーがこの形を断っても全体は成功になる（3b が通れば会話はできる）
    s = llm(fail_double_system=True)
    assert run(s.url, MODEL, timeout=5) == 0
    out = capsys.readouterr().out
    assert "[NG] 3a. system メッセージが 2 つ続く形（参考）" in out
    assert "[OK] 3b. system ＋ user のト書きの形（今の形）" in out


def test_run_fails_when_structured_output_is_not_json(llm, capsys):
    s = llm(structured_content="要約できません")
    assert run(s.url, MODEL, timeout=5) == 1
    assert "[NG] 2. 構造化出力（振り返りのスキーマ）が通る" in capsys.readouterr().out


def test_run_fails_when_structured_output_misses_reflection_keys(llm, capsys):
    # summary だけの JSON では、振り返りの検査を通らない
    s = llm(structured_content=json.dumps({"summary": "要約だけ"}))
    assert run(s.url, MODEL, timeout=5) == 1
    assert "[NG] 2. 構造化出力（振り返りのスキーマ）が通る" in capsys.readouterr().out


def test_run_fails_when_stream_is_empty(llm, capsys):
    s = llm(stream_chunks=[])
    assert run(s.url, MODEL, timeout=5) == 1
    out = capsys.readouterr().out
    assert "[NG] 1. ストリーミングで会話が返り、表情タグが付く" in out
    assert "測れませんでした" in out  # 初回トークンまでの時間も測れない


def test_run_fails_when_server_returns_error(llm, capsys):
    s = llm(error_code=500)
    assert run(s.url, MODEL, timeout=5) == 1
    out = capsys.readouterr().out
    assert "[NG] 1. ストリーミングで会話が返り、表情タグが付く" in out
    assert "[NG] 2. 構造化出力（振り返りのスキーマ）が通る" in out
    assert "[NG] 3b. system ＋ user のト書きの形（今の形）" in out


def test_run_fails_when_server_is_unreachable(capsys):
    # 何も待ち受けていないポートでは、必須の確認が失敗して 1 を返す
    assert run("http://127.0.0.1:9/v1", MODEL, timeout=5) == 1
    assert "結果: 失敗した確認があります" in capsys.readouterr().out
