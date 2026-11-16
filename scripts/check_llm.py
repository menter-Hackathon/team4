"""LLM（Gemma 4 など）の確認をまとめて行い、#1 に貼れる表を出す（仕様 §6・§8.1）。

#1 の「完了の確認方法」の 1〜4 を、コマンド 1 つで確かめるためのスクリプト。

    uv run python -m scripts.check_llm

LLM の場所は .env の LLM_BASE_URL・LLM_MODEL・LLM_API_KEY を使う（--base-url などで上書きできる）。
curl の例より本番に近い形で確かめる。エージェントが実際に作るシステムプロンプトと、
振り返り（reflection）の JSON スキーマをそのまま使う。

確かめること:
    1. 会話: ストリーミングで返事が返るか。返事の先頭に表情タグ（[happy] など）が付くか
    2. 構造化出力: 振り返りの JSON スキーマで返させ、エージェントの検査（parse_reflection）を通るか
    3. system メッセージが 2 つ続く形が通るか（#1 で頼まれている形）。あわせて、
       エージェントが今使っている「system ＋ user のト書き」の形も確かめる
    4. 最初のトークンまでの秒数（目標は会話全体で 1 秒以内、仕様 §11）
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime

from dotenv import load_dotenv
from openai import OpenAI

from agent.plant.expression import LLM_EXPRESSIONS
from agent.plant.personality import Traits
from agent.plant.prompt import WAKE_GREETING_INSTRUCTION, build_system_prompt
from agent.plant.reflection import (
    REFLECTION_SCHEMA,
    SYSTEM_MESSAGE,
    ReflectionError,
    parse_reflection,
)
from agent.plant.timeutil import JST

# 振り返りの確認に使う、短い会話ログ（reflection.py の _build_messages と同じ形）
SAMPLE_REFLECTION_INPUT = (
    "## 会話ログ\n"
    "持ち主: ただいま。明日テストなんだ\n"
    "ふたまる: おかえり！何のテスト？\n"
    "持ち主: 数学。シーフードカレー食べて頑張る\n"
    "\n## 既存の記憶\n（なし）"
)


@dataclass
class CheckResult:
    """1 つの確認の結果。"""

    name: str  # 確認の名前
    ok: bool  # 期待どおりだったか
    detail: str  # 返ってきた中身など（表に出す）
    seconds: float = 0.0  # かかった秒数
    first_token: float | None = None  # 最初のトークンまでの秒数（ストリーミングのときだけ）
    required: bool = True  # False なら参考。NG でもコマンドの結果は失敗にしない


def _stream(client: OpenAI, model: str, messages: list[dict]) -> tuple[str, float | None, float]:
    """ストリーミングで返事を受け取り、(返事の全文, 最初のトークンまでの秒数, 全体の秒数) を返す。"""
    start = time.perf_counter()
    first: float | None = None
    parts: list[str] = []
    for chunk in client.chat.completions.create(model=model, messages=messages, stream=True):
        delta = chunk.choices[0].delta.content if chunk.choices else None
        if delta:
            if first is None:
                first = time.perf_counter() - start
            parts.append(delta)
    return "".join(parts), first, time.perf_counter() - start


def has_expression_tag(text: str) -> bool:
    """返事の先頭に、プロンプトで指示した表情タグが付いているか。

    例: "[happy]おかえり！" → True、"おかえり！" → False、"[smile]やあ" → False
    """
    head = text.lstrip()
    if not head.startswith("["):
        return False
    tag = head[1 : head.find("]")].strip().lower() if "]" in head else ""
    return tag in LLM_EXPRESSIONS


def check_chat(client: OpenAI, model: str, system_prompt: str) -> CheckResult:
    """確認 1・4: エージェントのシステムプロンプトで「ただいま」に返事させる。"""
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "ただいま"},
    ]
    text, first, total = _stream(client, model, messages)
    ok = bool(text.strip()) and has_expression_tag(text)
    note = "" if has_expression_tag(text) else "（先頭に表情タグが無い）"
    return CheckResult("1. 会話（ストリーミング）", ok, text.strip() + note, total, first)


def check_structured(client: OpenAI, model: str) -> CheckResult:
    """確認 2: 振り返りのスキーマで JSON を返させ、エージェントの検査を通るか。"""
    start = time.perf_counter()
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_MESSAGE},
            {"role": "user", "content": SAMPLE_REFLECTION_INPUT},
        ],
        # llm.py の create_json_completer と同じ指定
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "reflection", "schema": REFLECTION_SCHEMA},
        },
        temperature=0.3,
    )
    seconds = time.perf_counter() - start
    raw = resp.choices[0].message.content or ""
    try:
        result = parse_reflection(raw)
    except ReflectionError as e:
        return CheckResult("2. 構造化出力（振り返り）", False, f"検査で不正: {e}", seconds)
    detail = f"要約: {result.summary} / 新しい記憶 {len(result.new_memories)} 件"
    return CheckResult("2. 構造化出力（振り返り）", True, detail, seconds)


def check_greeting_forms(client: OpenAI, model: str, system_prompt: str) -> list[CheckResult]:
    """確認 3: 起こされたときの挨拶を、2 つの形で作らせる。

    - system が 2 つ続く形: #1 で確かめるよう頼まれている形（参考。Gemma のチャットテンプレートが
      受け付けないことがあるので、エージェントはこの形を使っていない）
    - system ＋ user のト書き: エージェントが今使っている形（main.py の direction_ctx）
    """
    forms = {
        "3a. system が 2 つ続く形": [
            {"role": "system", "content": system_prompt},
            {"role": "system", "content": WAKE_GREETING_INSTRUCTION},
        ],
        "3b. system ＋ user のト書き（今の形）": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"（{WAKE_GREETING_INSTRUCTION}）"},
        ],
    }
    results = []
    for name, messages in forms.items():
        try:
            text, first, total = _stream(client, model, messages)
            result = CheckResult(name, bool(text.strip()), text.strip(), total, first)
        except Exception as e:  # この形を受け付けないサーバーもある。エラーも結果として残す
            result = CheckResult(name, False, f"エラー: {e}")
        # 3a はエージェントが使っていない形なので参考。通らなくても会話はできる
        result.required = name.startswith("3b")
        results.append(result)
    return results


def run_checks(client: OpenAI, model: str, now: datetime) -> list[CheckResult]:
    """確認をすべて行い、結果の一覧を返す。1 つが失敗しても、残りは続ける。"""
    # 初めての株と同じ条件のプロンプト（性格は全部 0.5、記憶なし、前回の会話なし）
    system_prompt = build_system_prompt("ふたまる", Traits(), [], None, now)
    results: list[CheckResult] = []
    for name, check in (
        ("1. 会話（ストリーミング）", lambda: [check_chat(client, model, system_prompt)]),
        ("2. 構造化出力（振り返り）", lambda: [check_structured(client, model)]),
        ("3. 挨拶の形", lambda: check_greeting_forms(client, model, system_prompt)),
    ):
        try:
            results.extend(check())
        except Exception as e:
            results.append(CheckResult(name, False, f"エラー: {e}"))
    return results


def to_markdown(results: list[CheckResult], model: str) -> str:
    """#1 にそのまま貼れる Markdown の表にする。"""
    lines = [
        f"LLM のモデル: `{model}`",
        "",
        "| 確認 | 結果 | 最初のトークン（秒） | 全体（秒） | 中身 |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        first = f"{r.first_token:.2f}" if r.first_token is not None else "-"
        detail = r.detail.replace("\n", " ").replace("|", "／")[:120]
        verdict = ("OK" if r.ok else "NG") + ("" if r.required else "（参考）")
        lines.append(f"| {r.name} | {verdict} | {first} | {r.seconds:.2f} | {detail} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """コマンドの入り口。必須の確認に NG が 1 つでもあれば 1 を返す。"""
    load_dotenv()
    parser = argparse.ArgumentParser(description="LLM の確認をまとめて行う（#1）")
    parser.add_argument("--base-url", default=os.environ.get("LLM_BASE_URL"))
    parser.add_argument("--model", default=os.environ.get("LLM_MODEL"))
    parser.add_argument("--api-key", default=os.environ.get("LLM_API_KEY", "EMPTY"))
    args = parser.parse_args(argv)
    if not args.base_url or not args.model:
        print("LLM_BASE_URL と LLM_MODEL を .env に書くか、引数で渡してください", file=sys.stderr)
        return 2

    client = OpenAI(base_url=args.base_url, api_key=args.api_key, timeout=60, max_retries=0)
    results = run_checks(client, args.model, datetime.now(JST))
    print(to_markdown(results, args.model))
    return 0 if all(r.ok for r in results if r.required) else 1


if __name__ == "__main__":
    sys.exit(main())
