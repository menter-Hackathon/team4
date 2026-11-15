"""LLM: Gemma 4 on vLLM（OpenAI 互換、仕様 §8.1）。会話用 LLM と reflection 用の JSON 補完関数。"""

from __future__ import annotations

from livekit.plugins import openai
from openai import AsyncOpenAI

from agent.config import Config
from agent.plant.reflection import JsonCompleter


def _client(cfg: Config) -> AsyncOpenAI:
    return AsyncOpenAI(base_url=cfg.llm_base_url, api_key=cfg.llm_api_key, timeout=cfg.llm_timeout)


def create_llm(cfg: Config) -> openai.LLM:
    # 思考モードは使わない（enable_thinking を指定しない）
    return openai.LLM(model=cfg.llm_model, client=_client(cfg))


def create_json_completer(cfg: Config) -> JsonCompleter:
    client = _client(cfg)

    async def complete_json(messages: list[dict], schema: dict) -> str:
        resp = await client.chat.completions.create(
            model=cfg.llm_model,
            messages=messages,
            response_format={"type": "json_schema", "json_schema": {"name": "reflection", "schema": schema}},
            temperature=0.3,
        )
        return resp.choices[0].message.content or ""

    return complete_json
