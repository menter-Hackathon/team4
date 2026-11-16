import json
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

from openai import OpenAI

from agent.plant.timeutil import JST
from scripts.check_llm import has_expression_tag, run_checks, to_markdown

NOW = datetime(2026, 11, 17, 22, 0, tzinfo=JST)
REFLECTION = {
    "summary": "持ち主は明日数学のテスト。",
    "new_memories": [{"content": "持ち主は明日数学のテスト", "kind": "event", "importance": 0.7}],
    "updated_memories": [],
    "trait_deltas": {"affection": 0.02},
}


class FakeLLM:
    """OpenAI 互換の /chat/completions の代わり。

    - stream=True: reply を 2 つに分けて SSE で返す
    - response_format あり: reflection の JSON を返す
    - system が 2 つ続く: reject_double_system なら 400 を返す（Gemma のテンプレートの再現）
    """

    def __init__(self, reply: str = "[happy]おかえり！", reject_double_system: bool = False):
        self.requests: list[dict] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.requests.append(req)
                roles = [m["role"] for m in req["messages"]]
                if reject_double_system and roles[:2] == ["system", "system"]:
                    self._send(400, {"error": {"message": "System role not supported"}})
                elif req.get("stream"):
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    for piece in (reply[:3], reply[3:]):
                        chunk = {
                            "id": "c",
                            "object": "chat.completion.chunk",
                            "created": 0,
                            "model": req["model"],
                            "choices": [{"index": 0, "delta": {"content": piece}}],
                        }
                        self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                    self.wfile.write(b"data: [DONE]\n\n")
                else:
                    message = {"role": "assistant", "content": json.dumps(REFLECTION)}
                    self._send(
                        200,
                        {
                            "id": "c",
                            "object": "chat.completion",
                            "created": 0,
                            "model": req["model"],
                            "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
                        },
                    )

            def _send(self, code: int, body: dict) -> None:
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args) -> None:
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.client = OpenAI(
            base_url=f"http://127.0.0.1:{self.server.server_port}/v1", api_key="x", max_retries=0
        )


def test_has_expression_tag():
    assert has_expression_tag("[happy]おかえり")
    assert has_expression_tag(" [Sleepy] ねむい")
    assert not has_expression_tag("おかえり")
    assert not has_expression_tag("[smile]やあ")


def test_all_checks_pass_against_a_good_server():
    fake = FakeLLM()
    results = run_checks(fake.client, "gemma", NOW)
    assert [r.ok for r in results] == [True, True, True, True]
    assert results[0].first_token is not None
    # 振り返りはエージェントと同じスキーマで頼んでいる
    structured = [r for r in fake.requests if "response_format" in r][0]
    assert structured["response_format"]["json_schema"]["name"] == "reflection"


def test_missing_tag_and_double_system_are_reported():
    fake = FakeLLM(reply="おかえり！", reject_double_system=True)
    results = run_checks(fake.client, "gemma", NOW)
    by_name = {r.name: r for r in results}
    assert not by_name["1. 会話（ストリーミング）"].ok
    assert "表情タグが無い" in by_name["1. 会話（ストリーミング）"].detail
    double = by_name["3a. system が 2 つ続く形"]
    assert not double.ok and not double.required  # 参考なので、コマンドの失敗にはしない
    assert by_name["3b. system ＋ user のト書き（今の形）"].ok
    assert "NG（参考）" in to_markdown(results, "gemma")
