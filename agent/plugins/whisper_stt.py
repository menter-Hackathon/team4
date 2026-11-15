"""STT: kotoba-whisper on Speaches（OpenAI 互換、仕様 §8.2）。"""

from __future__ import annotations

from livekit.plugins import openai
from openai import AsyncOpenAI

from agent.config import Config

STT_PROMPT = "ふたまる"  # ウェイクワードの認識補助（仕様 §5.3）


def create_stt(cfg: Config) -> openai.STT:
    client = AsyncOpenAI(
        base_url=cfg.stt_base_url, api_key=cfg.stt_api_key, timeout=cfg.stt_timeout, max_retries=0
    )
    return openai.STT(
        model=cfg.stt_model, language="ja", prompt=STT_PROMPT, use_realtime=False, client=client
    )
