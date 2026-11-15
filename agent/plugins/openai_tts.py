"""TTS: OpenAI gpt-4o-mini-tts（仕様 §8.3）。TTS_BASE_URL で OpenAI 互換の自前 TTS に切り替えられる。"""

from __future__ import annotations

from livekit.plugins import openai
from openai import AsyncOpenAI

from agent.config import Config


def create_tts(cfg: Config) -> openai.TTS:
    client = AsyncOpenAI(
        base_url=cfg.tts_base_url or None,
        api_key=cfg.tts_api_key,
        timeout=cfg.tts_timeout,
        max_retries=0,
    )
    return openai.TTS(
        model=cfg.tts_model,
        voice=cfg.tts_voice,
        instructions=cfg.tts_instructions,
        response_format="pcm",
        client=client,
    )
