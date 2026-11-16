"""LLM（Gemma 4）がエージェントから使える状態かを確認する（仕様 §8.1、Issue #1）。

エージェント側のコードは完成していて、LLM は URL とモデル名を環境変数で受け取るだけ。
なので「teffy さんの PC で Gemma 4 が動いた」と言えるためには、サーバー単体で
次の 4 つを確認すればよい（Issue #1 の「完了の確認方法」がもと）。

    1. 普通の会話がストリーミングで返り、返事の先頭に表情タグ（[happy] など）が付く
       （タグが無いと表情がいつも neutral になる。仕様 §7.1、PR #48 の指摘で追加）
    2. 構造化出力（response_format の json_schema）で、振り返り（reflection）の
       本物のスキーマどおりの JSON が返り、エージェント側の検査を通る形になっている
    3. 起こされたときの挨拶が作れる。2 つの形を確かめる
       3a. system メッセージが 2 つ続く形（Issue #1 で頼まれている形。参考）
       3b. system ＋ user のト書きの形（エージェントが今使っている形。こちらが必須）
    4. 初回トークンまでの時間（TTFT）を測る。これは合否ではなくメモ用の項目。
       会話のテンポの目安は「発話から返事まで合計 1 秒以内」なので、その一部である
       TTFT がどれくらいかを Issue #1 に残す

使い方（Docker で動かす場合は、docs/deploy.md のとおり llm のポートを外に出してから）:

    LLM_BASE_URL=http://localhost:8000/v1 LLM_MODEL=google/gemma-4-E4B-it \
        uv run python -m scripts.check_llm

終了コード: 必須の確認（1・2・3b）がすべて通れば 0、1 つでも失敗すれば 1。
結果はそのまま Issue #1 のコメントに貼れる形で表示する（API キーは表示しない）。

Python の標準ライブラリだけで書いているので、uv sync していない PC でも
`python3 scripts/check_llm.py` でそのまま動く。その代わり、エージェント側の定義
（プロンプト・スキーマ・検査）は import せずに写している。エージェント側が変わったら、
このファイルの「エージェント側の定義の写し」も合わせて直すこと。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

# 確認 1 回ごとの待ち時間の既定値（秒）。モデルの読み込み直後は遅いことがあるので長めに取る
DEFAULT_TIMEOUT = 120.0

# ---------------------------------------------------------------------------
# エージェント側の定義の写し（import せずに使うためのコピー。出典を各所に書いた）
# ---------------------------------------------------------------------------

# 会話のシステムプロンプト。agent/plant/prompt.py の build_system_prompt が、
# 初めての株（性格は全部まん中・記憶なし・前回の会話なし）に対して作る文と同じ
CONVERSATION_SYSTEM_PROMPT = (
    "あなたは観葉植物のふたまる。持ち主と話し言葉で短く話す。1回の返答は1〜2文。\n"
    "\n"
    "返答の先頭に必ず表情タグを1つ付ける: [neutral] [happy] [sad] [surprised] [love] [sleepy]\n"
    "\n"
    "ほどよく話す。\n"
    "普通に親しく接する。\n"
    "ふつうのテンションで話す。\n"
    "ときどき持ち主に質問する。"
)

# LLM が使ってよい表情（agent/plant/expression.py の LLM_EXPRESSIONS と同じ）
LLM_EXPRESSIONS = ("neutral", "happy", "sad", "surprised", "love", "sleepy")

# 起こされたときの挨拶のト書き（agent/plant/prompt.py の WAKE_GREETING_INSTRUCTION と同じ）
WAKE_GREETING_INSTRUCTION = "持ち主に呼ばれて起きた。記憶や前回の会話を踏まえて短く話しかける。"

# 振り返りの指示文（agent/plant/reflection.py の SYSTEM_MESSAGE と同じ）
REFLECTION_SYSTEM_MESSAGE = """あなたは観葉植物「ふたまる」の記憶係。持ち主との会話ログを読み、次の JSON だけを出力する。
{
  "summary": "会話の要約（1〜2文）",
  "new_memories": [{"content": "持ち主について覚えておくこと", "kind": "preference | event | habit", "importance": 0.0〜1.0}],
  "updated_memories": [{"id": 既存記憶の id, "content": "更新後の内容", "importance": 0.0〜1.0}],
  "trait_deltas": {"talkativeness": -0.05〜0.05, "affection": -0.05〜0.05, "energy": -0.05〜0.05, "curiosity": -0.05〜0.05}
}
- 既存の記憶と同じ内容は new_memories に入れない。内容が変わった・補強された場合は updated_memories でその id を更新する。
- trait_deltas は今回の会話で性格がどう変わるかの判定:
  talkativeness は話しかけ頻度・会話の長さ、affection は褒め言葉・優しい言葉の量、
  energy はユーザーの口調・話題の明るさ、curiosity はユーザーが自分の話をする量で判断する。"""

# 振り返りの JSON スキーマ（agent/plant/reflection.py の REFLECTION_SCHEMA と同じ）
REFLECTION_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "new_memories": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "content": {"type": "string"},
                    "kind": {"type": "string", "enum": ["preference", "event", "habit"]},
                    "importance": {"type": "number"},
                },
                "required": ["content", "kind", "importance"],
            },
        },
        "updated_memories": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "content": {"type": "string"},
                    "importance": {"type": "number"},
                },
                "required": ["id", "content", "importance"],
            },
        },
        "trait_deltas": {
            "type": "object",
            "properties": {
                name: {"type": "number"}
                for name in ("talkativeness", "affection", "energy", "curiosity")
            },
            "required": ["talkativeness", "affection", "energy", "curiosity"],
        },
    },
    "required": ["summary", "new_memories", "updated_memories", "trait_deltas"],
}

# 振り返りの確認に使う短い会話ログ（reflection.py の _build_messages と同じ形）
SAMPLE_REFLECTION_INPUT = (
    "## 会話ログ\n"
    "持ち主: ただいま。明日テストなんだ\n"
    "ふたまる: おかえり！何のテスト？\n"
    "持ち主: 数学。シーフードカレー食べて頑張る\n"
    "\n## 既存の記憶\n（なし）"
)


@dataclass(frozen=True)
class CheckResult:
    """確認 1 件の結果。required が False の確認は、NG でも全体の合否に影響しない（参考）。"""

    name: str
    ok: bool
    detail: str
    required: bool = True


def _headers(api_key: str) -> dict[str, str]:
    """OpenAI 互換 API へのリクエストのヘッダー。キーは自前サーバーでは "EMPTY" でよい。"""
    return {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}


def _post_json(base_url: str, api_key: str, payload: dict, timeout: float) -> dict:
    """POST {base_url}/chat/completions して、JSON の応答を辞書で返す（ストリーミングなし）。"""
    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload).encode(),
        headers=_headers(api_key),
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _content_of(response: dict) -> str:
    """OpenAI 互換の応答から、返事の本文（choices[0].message.content）を取り出す。"""
    choices = response.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    return message.get("content") or ""


def _stream_chat(
    base_url: str,
    model: str,
    api_key: str,
    messages: list[dict],
    timeout: float,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[str, float | None]:
    """ストリーミングで会話を 1 回送り、(返事の全文, 初回トークンまでの秒数) を返す。

    ストリーミングの応答は Server-Sent Events 形式で、`data: {...}` の行が少しずつ届く。
    本文のかけら（choices[0].delta.content）を順につなぐと全文になる。
    初回トークンまでの時間は「本文が初めて届いた行」を受け取った時点で測る
    （role だけの最初の行は本文ではないので数えない）。
    """
    payload = {"model": model, "stream": True, "messages": messages}
    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload).encode(),
        headers=_headers(api_key),
        method="POST",
    )
    started = clock()
    first_token_at: float | None = None
    parts: list[str] = []
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        for raw_line in resp:
            line = raw_line.decode(errors="replace").strip()
            if not line.startswith("data:"):
                continue  # 空行やコメント行は読み飛ばす
            data = line.removeprefix("data:").strip()
            if data == "[DONE]":  # ストリーミングの終了合図
                break
            chunk = json.loads(data)
            choices = chunk.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            text = delta.get("content") or ""
            if text:
                if first_token_at is None:
                    first_token_at = clock()
                parts.append(text)
    ttft = None if first_token_at is None else first_token_at - started
    return "".join(parts), ttft


# 先頭の表情タグ。[happy] の半角のほか、エージェント（expression.py）が受け付ける全角も認める
_TAG_RE = re.compile(r"^[［【\[]\s*([a-z]+)\s*[］】\]]")


def leading_expression(text: str) -> str | None:
    """返事の先頭の表情タグを取り出す。タグが無い・知らない名前なら None。

    例: "[happy]おかえり！" → "happy"、"おかえり！" → None、"[smile]やあ" → None
    """
    match = _TAG_RE.match(text.lstrip())
    if match and match.group(1) in LLM_EXPRESSIONS:
        return match.group(1)
    return None


def check_streaming(
    base_url: str, model: str, api_key: str, timeout: float
) -> tuple[CheckResult, float | None]:
    """確認 1: 本番のシステムプロンプトで会話が返り、先頭に表情タグが付くこと。

    ついでに確認 4 の TTFT も測る。タグが付かない場合は会話自体はできても
    表情がいつも neutral になってしまうので、NG として扱う（仕様 §7.1）。
    """
    messages = [
        {"role": "system", "content": CONVERSATION_SYSTEM_PROMPT},
        {"role": "user", "content": "ただいま"},
    ]
    name = "1. ストリーミングで会話が返り、表情タグが付く"
    try:
        text, ttft = _stream_chat(base_url, model, api_key, messages, timeout)
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as e:
        return CheckResult(name, False, f"エラー: {e}"), None
    if not text:
        return CheckResult(name, False, "返事が空でした"), ttft
    if leading_expression(text) is None:
        detail = f"返事は返ったが先頭に表情タグが無い（表情が neutral 固定になる）: {text}"
        return CheckResult(name, False, detail), ttft
    return CheckResult(name, True, f"返事: {text}"), ttft


def _reflection_problem(raw: str) -> str | None:
    """振り返りの返事が、エージェントの検査（reflection.py の parse_reflection）を通る形か。

    通れば None、通らなければ理由を返す。parse_reflection が「全体として使えない」と
    判断する条件（JSON でない・必須キーが無い・型が違う）だけを写している。
    記憶 1 件ごとの細かい捨て分けは、通る形かどうかには影響しないのでここでは見ない。
    """
    text = raw.strip()
    if text.startswith("```"):  # LLM が ```json ... ``` で囲んで返すことがある
        text = text.split("\n", 1)[1] if "\n" in text else ""
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    try:
        data = json.loads(text.strip())
    except json.JSONDecodeError as e:
        return f"JSON として不正: {e}"
    if not isinstance(data, dict):
        return "トップレベルがオブジェクトではない"
    for key in ("summary", "new_memories", "updated_memories", "trait_deltas"):
        if key not in data:
            return f"必須キー {key} が無い"
    if not isinstance(data["summary"], str):
        return "summary が文字列ではない"
    if not isinstance(data["new_memories"], list) or not isinstance(data["updated_memories"], list):
        return "new_memories / updated_memories が配列ではない"
    if not isinstance(data["trait_deltas"], dict):
        return "trait_deltas がオブジェクトではない"
    return None


def check_structured_output(base_url: str, model: str, api_key: str, timeout: float) -> CheckResult:
    """確認 2: 振り返りの本物のスキーマで頼み、エージェントの検査を通る JSON が返ること。

    summary だけの小さいスキーマだと、new_memories などの入れ子で崩れることに気づけないため、
    エージェントが実際に使う REFLECTION_SCHEMA と指示文をそのまま使う（PR #48 の指摘）。
    """
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": REFLECTION_SYSTEM_MESSAGE},
            {"role": "user", "content": SAMPLE_REFLECTION_INPUT},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "reflection", "schema": REFLECTION_SCHEMA},
        },
        "temperature": 0.3,  # エージェント側（plugins/llm.py）と同じ指定
    }
    name = "2. 構造化出力（振り返りのスキーマ）が通る"
    try:
        content = _content_of(_post_json(base_url, api_key, payload, timeout))
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as e:
        return CheckResult(name, False, f"エラー: {e}")
    problem = _reflection_problem(content)
    if problem is not None:
        return CheckResult(name, False, f"検査で不正: {problem}")
    return CheckResult(name, True, f"返事: {content}")


def check_greetings(base_url: str, model: str, api_key: str, timeout: float) -> list[CheckResult]:
    """確認 3: 起こされたときの挨拶を、2 つの形で作らせる。

    3a. system が 2 つ続く形: Issue #1 で頼まれている形。Gemma のチャットテンプレートが
        受け付けないことがあるため、エージェントはこの形を使っていない（参考扱い）
    3b. system ＋ user のト書き: エージェントが今使っている形（main.py の direction_ctx）。
        こちらが通らないと、呼ばれたときの挨拶ができない
    """
    forms = [
        (
            "3a. system メッセージが 2 つ続く形",
            [
                {"role": "system", "content": CONVERSATION_SYSTEM_PROMPT},
                {"role": "system", "content": WAKE_GREETING_INSTRUCTION},
            ],
            False,
        ),
        (
            "3b. system ＋ user のト書きの形（今の形）",
            [
                {"role": "system", "content": CONVERSATION_SYSTEM_PROMPT},
                {"role": "user", "content": f"（{WAKE_GREETING_INSTRUCTION}）"},
            ],
            True,
        ),
    ]
    results: list[CheckResult] = []
    for name, messages, required in forms:
        payload = {"model": model, "messages": messages}
        try:
            content = _content_of(_post_json(base_url, api_key, payload, timeout))
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as e:
            results.append(CheckResult(name, False, f"エラー: {e}", required))
            continue
        if not content:
            results.append(CheckResult(name, False, "返事が空でした", required))
        else:
            results.append(CheckResult(name, True, f"返事: {content}", required))
    return results


def run(base_url: str, model: str, api_key: str = "EMPTY", timeout: float = DEFAULT_TIMEOUT) -> int:
    """確認 1〜4 を順に実行して結果を表示する。必須の確認がすべて通れば 0、そうでなければ 1。

    引数:
        base_url: LLM サーバーの URL（例: "http://localhost:8000/v1"）。末尾の "/" は取り除く
        model: モデル名（例: "google/gemma-4-E4B-it"）
        api_key: API キー。自前サーバーでは "EMPTY" でよい（表示はしない）
        timeout: 確認 1 回ごとの待ち時間（秒）
    """
    base_url = base_url.rstrip("/")
    # 1. 結果を Issue #1 にそのまま貼れる形で出す（どのサーバーで確かめたかが分かるように）
    print("LLM の確認（Issue #1）")
    print(f"LLM_BASE_URL={base_url}")
    print(f"LLM_MODEL={model}")

    # 2. 確認を実行する。1 つ失敗しても、残りの結果も Issue で必要なので続ける
    streaming, ttft = check_streaming(base_url, model, api_key, timeout)
    results = [
        streaming,
        check_structured_output(base_url, model, api_key, timeout),
        *check_greetings(base_url, model, api_key, timeout),
    ]
    for result in results:
        mark = "OK" if result.ok else "NG"
        suffix = "" if result.required else "（参考）"
        print(f"[{mark}] {result.name}{suffix} — {result.detail}")

    # 3. 確認 4 は合否ではなくメモ。測れなかった（確認 1 が失敗した）ときはその旨を書く
    if ttft is None:
        print(
            "[--] 4. 初回トークンまでの時間 — 測れませんでした（確認 1 の失敗を確認してください）"
        )
    else:
        print(
            f"[OK] 4. 初回トークンまでの時間 — {ttft:.2f} 秒（メモ用。目安は合計 1 秒以内の一部）"
        )

    # 4. 終了コードで成否を返す（参考の確認 3a は合否に含めない）
    if all(result.ok for result in results if result.required):
        print("結果: 必須の確認はすべて OK。LLM_BASE_URL と LLM_MODEL を Issue #1 に渡せます")
        return 0
    print("結果: 失敗した確認があります。上のエラーを Issue #1 に貼ってください")
    return 1


def main() -> None:
    """環境変数（LLM_BASE_URL / LLM_MODEL / LLM_API_KEY）か引数から接続先を読んで確認する。"""
    parser = argparse.ArgumentParser(description="LLM（Gemma 4）の確認（Issue #1）")
    parser.add_argument("--base-url", default=os.environ.get("LLM_BASE_URL", ""))
    parser.add_argument("--model", default=os.environ.get("LLM_MODEL", ""))
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    args = parser.parse_args()
    if not args.base_url or not args.model:
        sys.exit("LLM_BASE_URL と LLM_MODEL を設定してください（.env か環境変数、または引数で）")
    api_key = os.environ.get("LLM_API_KEY", "EMPTY")
    sys.exit(run(args.base_url, args.model, api_key, args.timeout))


if __name__ == "__main__":
    main()
