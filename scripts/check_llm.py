"""LLM（Gemma 4）がエージェントから使える状態かを確認する（仕様 §8.1、Issue #1）。

エージェント側のコードは完成していて、LLM は URL とモデル名を環境変数で受け取るだけ。
なので「teffy さんの PC で Gemma 4 が動いた」と言えるためには、サーバー単体で
次の 4 つを確認すればよい（Issue #1 の「完了の確認方法」そのまま）。

    1. 普通の会話がストリーミングで返る
    2. 構造化出力（response_format の json_schema）で JSON が返る（記憶・性格の更新に使う）
    3. system メッセージが 2 つ続くリクエストが通る（起こされたときの挨拶で使う形）
    4. 初回トークンまでの時間（TTFT）を測る。これは合否ではなくメモ用の項目。
       会話のテンポの目安は「発話から返事まで合計 1 秒以内」なので、その一部である
       TTFT がどれくらいかを Issue #1 に残す

使い方（Docker で動かす場合は、docs/deploy.md のとおり llm のポートを外に出してから）:

    LLM_BASE_URL=http://localhost:8000/v1 LLM_MODEL=google/gemma-4-E4B-it \
        uv run python -m scripts.check_llm

終了コード: 確認 1〜3 がすべて通れば 0、1 つでも失敗すれば 1。
結果はそのまま Issue #1 のコメントに貼れる形で表示する（API キーは表示しない）。

Python の標準ライブラリだけで書いているので、uv sync していない PC でも
`python3 scripts/check_llm.py` でそのまま動く。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

# 確認 1 回ごとの待ち時間の既定値（秒）。モデルの読み込み直後は遅いことがあるので長めに取る
DEFAULT_TIMEOUT = 120.0


@dataclass(frozen=True)
class CheckResult:
    """確認 1 件の結果。name は表示名、detail は Issue に貼るための補足（返答や時間など）。"""

    name: str
    ok: bool
    detail: str


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
    timeout: float,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[str, float | None]:
    """ストリーミングで会話を 1 回送り、(返事の全文, 初回トークンまでの秒数) を返す。

    ストリーミングの応答は Server-Sent Events 形式で、`data: {...}` の行が少しずつ届く。
    本文のかけら（choices[0].delta.content）を順につなぐと全文になる。
    初回トークンまでの時間は「本文が初めて届いた行」を受け取った時点で測る
    （role だけの最初の行は本文ではないので数えない）。
    """
    payload = {
        "model": model,
        "stream": True,
        "messages": [
            {"role": "system", "content": "あなたは観葉植物のふたまる。"},
            {"role": "user", "content": "ただいま"},
        ],
    }
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


def check_streaming(
    base_url: str, model: str, api_key: str, timeout: float
) -> tuple[CheckResult, float | None]:
    """確認 1: 普通の会話がストリーミングで返ること。ついでに確認 4 の TTFT も測る。"""
    try:
        text, ttft = _stream_chat(base_url, model, api_key, timeout)
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as e:
        return CheckResult("1. ストリーミングで会話が返る", False, f"エラー: {e}"), None
    if not text:
        return CheckResult("1. ストリーミングで会話が返る", False, "返事が空でした"), ttft
    return CheckResult("1. ストリーミングで会話が返る", True, f"返事: {text}"), ttft


def check_structured_output(base_url: str, model: str, api_key: str, timeout: float) -> CheckResult:
    """確認 2: json_schema の構造化出力で、summary（文字列）を含む JSON が返ること。"""
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": "今日の会話を要約して: 持ち主「明日テストなんだ」",
            }
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "r",
                "schema": {
                    "type": "object",
                    "properties": {"summary": {"type": "string"}},
                    "required": ["summary"],
                },
            },
        },
    }
    try:
        content = _content_of(_post_json(base_url, api_key, payload, timeout))
        parsed = json.loads(content)
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as e:
        return CheckResult("2. 構造化出力で JSON が返る", False, f"エラー: {e}")
    # JSON として読めても、形が違えば reflection（記憶・性格の更新）では使えない
    if not isinstance(parsed, dict) or not isinstance(parsed.get("summary"), str):
        return CheckResult(
            "2. 構造化出力で JSON が返る", False, f"summary（文字列）がありません: {content}"
        )
    return CheckResult("2. 構造化出力で JSON が返る", True, f"返事: {content}")


def check_double_system(base_url: str, model: str, api_key: str, timeout: float) -> CheckResult:
    """確認 3: system メッセージが 2 つ続くリクエストがエラーにならないこと。"""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "あなたは観葉植物のふたまる。"},
            {"role": "system", "content": "持ち主に呼ばれて起きた。短く話しかける。"},
        ],
    }
    try:
        content = _content_of(_post_json(base_url, api_key, payload, timeout))
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as e:
        return CheckResult("3. system メッセージが 2 つ続いても通る", False, f"エラー: {e}")
    if not content:
        return CheckResult("3. system メッセージが 2 つ続いても通る", False, "返事が空でした")
    return CheckResult("3. system メッセージが 2 つ続いても通る", True, f"返事: {content}")


def run(base_url: str, model: str, api_key: str = "EMPTY", timeout: float = DEFAULT_TIMEOUT) -> int:
    """確認 1〜4 を順に実行して結果を表示する。確認 1〜3 がすべて通れば 0、そうでなければ 1。

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

    # 2. 確認 1〜3 を実行する。1 つ失敗しても、残りの結果も Issue で必要なので続ける
    streaming, ttft = check_streaming(base_url, model, api_key, timeout)
    results = [
        streaming,
        check_structured_output(base_url, model, api_key, timeout),
        check_double_system(base_url, model, api_key, timeout),
    ]
    for result in results:
        mark = "OK" if result.ok else "NG"
        print(f"[{mark}] {result.name} — {result.detail}")

    # 3. 確認 4 は合否ではなくメモ。測れなかった（確認 1 が失敗した）ときはその旨を書く
    if ttft is None:
        print(
            "[--] 4. 初回トークンまでの時間 — 測れませんでした（確認 1 の失敗を確認してください）"
        )
    else:
        print(
            f"[OK] 4. 初回トークンまでの時間 — {ttft:.2f} 秒（メモ用。目安は合計 1 秒以内の一部）"
        )

    # 4. 終了コードで成否を返す（あとからスクリプトで呼び出すときにも分かるように）
    if all(result.ok for result in results):
        print("結果: すべて OK。LLM_BASE_URL と LLM_MODEL を Issue #1 に渡せます")
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
