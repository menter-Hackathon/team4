"""LLM: Gemma 4 on vLLM（OpenAI 互換、仕様 §8.1）。会話用 LLM と reflection 用の JSON 補完関数。

「OpenAI 互換」とは、OpenAI の API と同じ形で呼べるということ。vLLM・Ollama などで動かした
Gemma 4 も、本物の OpenAI も、同じコードで呼べる。どこに繋ぐかは .env の LLM_BASE_URL で切り替える。

LLM の使い道は 2 つある:
    1. 会話の返事を作る（create_llm）: LiveKit のセッションが、返事を少しずつ受け取りながら読み上げる
    2. 会話の振り返り（create_json_completer）: 決まった形の JSON を 1 回で返させる（reflection.py）
"""

from __future__ import annotations

from livekit.agents import NOT_GIVEN
from livekit.plugins import openai
from openai import AsyncOpenAI

from agent.config import Config
from agent.plant.reflection import JsonCompleter


def _client(cfg: Config) -> AsyncOpenAI:
    """LLM サーバーにつなぐクライアントを作る。"""
    # max_retries=0: 失敗しても自動で再試行しない。再試行すると待ち時間が何倍にも延び、
    # 植物が黙り込んでしまうため。失敗したら main.py の llm_node が代わりの返事（FALLBACK_REPLY）を返す
    return AsyncOpenAI(
        base_url=cfg.llm_base_url, api_key=cfg.llm_api_key, timeout=cfg.llm_timeout, max_retries=0
    )


def create_llm(cfg: Config) -> openai.LLM:
    """会話の返事を作る LLM（LiveKit のプラグインの形）を作る。"""
    # 思考モードは使わない。考える分だけ返事が遅くなるため。
    # vLLM の Gemma 4 は何も指定しなければ思考しない（enable_thinking を指定しない）。
    # Ollama の Gemma 4 は既定で思考するので、LLM_REASONING_EFFORT=none のときだけ送って切る
    return openai.LLM(
        model=cfg.llm_model,
        client=_client(cfg),
        reasoning_effort=cfg.llm_reasoning_effort or NOT_GIVEN,
    )


def create_json_completer(cfg: Config) -> JsonCompleter:
    """振り返り用の「JSON を返させる関数」を作って返す（reflection.py の JsonCompleter の形）。"""
    client = _client(cfg)

    # 思考モードを切る指定（LLM_REASONING_EFFORT）。設定されていないときは送らない。
    # Ollama の Gemma 4 で思考したままだと、振り返り 1 回に CPU で 1 分以上かかった（#1）
    extra = {"reasoning_effort": cfg.llm_reasoning_effort} if cfg.llm_reasoning_effort else {}

    async def complete_json(messages: list[dict], schema: dict) -> str:
        """messages を LLM に送り、schema の形の JSON 文字列を返す。"""
        resp = await client.chat.completions.create(
            model=cfg.llm_model,
            messages=messages,
            # 構造化出力: schema の形に沿った JSON だけを返させる（vLLM が Gemma 4 で対応）
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "reflection", "schema": schema},
            },
            temperature=0.3,  # 低めにして、毎回の出力を安定させる（会話ほど面白さは要らない）
            **extra,
        )
        # 返事の本文。万一空なら "" を返し、reflection.py の検査で「不正」として再試行させる
        return resp.choices[0].message.content or ""

    return complete_json
