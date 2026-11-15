"""環境変数からエージェントの設定を読み込む（仕様 §8）。"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

TTS_INSTRUCTIONS = "やわらかく、少しゆっくり、のんびりした植物らしい話し方で話す。"
DEFAULT_WAKE_WORDS = ("ふたまる", "二丸", "ふた丸", "2丸")


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    llm_base_url: str
    llm_model: str
    llm_api_key: str
    stt_base_url: str
    stt_model: str
    stt_api_key: str
    tts_model: str
    tts_voice: str
    tts_base_url: str | None
    tts_api_key: str
    tts_instructions: str
    db_path: str
    wake_words: tuple[str, ...]
    sleep_after: float
    llm_timeout: float
    stt_timeout: float
    tts_timeout: float
    dev_plant_id: str | None


def load_config(env: Mapping[str, str] = os.environ) -> Config:
    def get(name: str, default: str | None = None) -> str | None:
        value = env.get(name, "").strip()
        return value or default

    def required(name: str) -> str:
        value = get(name)
        if value is None:
            raise ConfigError(f"環境変数 {name} が設定されていません")
        return value

    tts_base_url = get("TTS_BASE_URL")
    tts_api_key = get("OPENAI_API_KEY")
    if tts_api_key is None:
        if tts_base_url is None:
            raise ConfigError("環境変数 OPENAI_API_KEY が設定されていません（TTS に必要）")
        tts_api_key = "EMPTY"

    wake_words_raw = get("WAKE_WORDS")
    wake_words = (
        tuple(w.strip() for w in wake_words_raw.split(",") if w.strip())
        if wake_words_raw
        else DEFAULT_WAKE_WORDS
    )

    return Config(
        llm_base_url=required("LLM_BASE_URL"),
        llm_model=required("LLM_MODEL"),
        llm_api_key=get("LLM_API_KEY", "EMPTY"),
        stt_base_url=required("STT_BASE_URL"),
        stt_model=get("STT_MODEL", "kotoba-tech/kotoba-whisper-v2.0-faster"),
        stt_api_key=get("STT_API_KEY", "EMPTY"),
        tts_model=get("TTS_MODEL", "gpt-4o-mini-tts"),
        tts_voice=get("TTS_VOICE", "coral"),
        tts_base_url=tts_base_url,
        tts_api_key=tts_api_key,
        tts_instructions=TTS_INSTRUCTIONS,
        db_path=get("FUTAMARU_DB_PATH", "./data/futamaru.db"),
        wake_words=wake_words,
        sleep_after=float(get("SLEEP_AFTER_SECONDS", "60")),
        llm_timeout=float(get("LLM_TIMEOUT", "15")),
        stt_timeout=float(get("STT_TIMEOUT", "10")),
        tts_timeout=float(get("TTS_TIMEOUT", "10")),
        dev_plant_id=get("DEV_PLANT_ID"),
    )
