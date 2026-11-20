"""TTS: OpenAI gpt-4o-mini-tts（仕様 §8.3）。TTS_BASE_URL で OpenAI 互換の自前 TTS に切り替えられる。

TTS（Text To Speech）は、植物の返事の文字を声にする部分。
"""

from __future__ import annotations

from livekit.plugins import openai
from openai import AsyncOpenAI

from agent.config import Config


def create_tts(cfg: Config) -> openai.TTS:
    """声を作る TTS（LiveKit のプラグインの形）を作る。"""
    client = AsyncOpenAI(
        # None なら本物の OpenAI。URL が入っていれば、そこにある OpenAI 互換の TTS を使う
        base_url=cfg.tts_base_url or None,
        api_key=cfg.tts_api_key,
        timeout=cfg.tts_timeout,
        max_retries=0,  # 再試行しない（待ち時間を延ばさないため。llm.py と同じ）
    )
    return openai.TTS(
        model=cfg.tts_model,
        voice=cfg.tts_voice,
        instructions=cfg.tts_instructions,  # 話し方の指示（config.py の TTS_INSTRUCTIONS）
        # pcm: 圧縮していない音声。届いた先からすぐ流せるので、喋り出しが早い
        response_format="pcm",
        client=client,
    )
