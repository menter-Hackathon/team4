"""環境変数からエージェントの設定を読み込む（仕様 §8）。

設定は .env ファイル（見本は .env.example）に書き、起動時に環境変数として読み込まれる。
URL・モデル名・キーを .env で切り替えるだけで、コードを変えずに
「OpenAI で試す」「自分の PC の Gemma 4 で動かす」などを切り替えられる。

環境変数を読むのはこのファイルだけ。他のファイルは load_config() が返す Config を使う。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

# TTS に渡す話し方の指示（gpt-4o-mini-tts が対応。自前の TTS では無視される）
TTS_INSTRUCTIONS = "やわらかく、少しゆっくり、のんびりした植物らしい話し方で話す。"
# WAKE_WORDS が .env に無いときに使うウェイクワード。STT がよく書く表記をまとめている
DEFAULT_WAKE_WORDS = ("ふたまる", "二丸", "ふた丸", "2丸")
# FUTAMARU_DB_PATH が .env に無いときの DB ファイルの場所
DEFAULT_DB_PATH = "./data/futamaru.db"


class ConfigError(Exception):
    """必要な環境変数が無いときのエラー。起動時に止めて、何が足りないかを知らせる。"""


@dataclass(frozen=True)
class Config:
    """エージェントの設定一式。frozen なので、起動後に書き換えられない。"""

    llm_base_url: str  # LLM サーバーの URL（例: http://localhost:8000/v1）
    llm_model: str  # LLM のモデル名（例: google/gemma-4-E4B-it）
    llm_api_key: str  # 自前のサーバーなら何でもよい（"EMPTY"）
    stt_base_url: str  # STT サーバーの URL
    stt_model: str  # STT のモデル名
    stt_api_key: str
    tts_model: str  # TTS のモデル名
    tts_voice: str  # 声の種類（例: coral）
    tts_base_url: str | None  # None なら本物の OpenAI
    tts_api_key: str
    tts_instructions: str  # 話し方の指示
    db_path: str  # 会話ログ・記憶を保存する DB ファイルの場所
    wake_words: tuple[str, ...]  # ウェイクワードの一覧
    sleep_after: float  # 何秒だれも話さなければ眠るか
    llm_timeout: float  # 各サーバーの応答をこの秒数まで待つ
    stt_timeout: float
    tts_timeout: float
    dev_plant_id: str | None  # console モードで使う株の id（開発用）


def db_path_from_env(env: Mapping[str, str] = os.environ) -> str:
    """DB ファイルの場所（FUTAMARU_DB_PATH）だけを読む。未設定・空なら DEFAULT_DB_PATH。

    scripts/people.py のように DB だけを使うコマンド用。load_config() は LLM・STT の
    URL など DB に関係ない環境変数も必須にするので、それを使わずに済むよう分けている。
    例: {} → "./data/futamaru.db"、{"FUTAMARU_DB_PATH": "/data/x.db"} → "/data/x.db"
    """
    return env.get("FUTAMARU_DB_PATH", "").strip() or DEFAULT_DB_PATH


def load_config(env: Mapping[str, str] = os.environ) -> Config:
    """環境変数を読んで Config を作る。必須の値が無ければ ConfigError。

    env を引数にしているのは、テストで好きな値の辞書を渡して確かめられるようにするため。
    """

    def get(name: str, default: str | None = None) -> str | None:
        """環境変数を読む。未設定・空・空白だけなら default を返す。"""
        value = env.get(name, "").strip()
        return value or default

    def required(name: str) -> str:
        """必ず設定が要る環境変数を読む。無ければ ConfigError。"""
        value = get(name)
        if value is None:
            raise ConfigError(f"環境変数 {name} が設定されていません")
        return value

    # TTS のキー: 本物の OpenAI を使う（TTS_BASE_URL が無い）ならキーが必須。
    # 自前の TTS を使うならキーは要らないので、仮の値 "EMPTY" にする
    tts_base_url = get("TTS_BASE_URL")
    tts_api_key = get("OPENAI_API_KEY")
    if tts_api_key is None:
        if tts_base_url is None:
            raise ConfigError("環境変数 OPENAI_API_KEY が設定されていません（TTS に必要）")
        tts_api_key = "EMPTY"

    # WAKE_WORDS はカンマ区切り（例: "ふたまる,二丸"）。空の項目は捨てる
    wake_words_raw = get("WAKE_WORDS")
    wake_words = (
        tuple(w.strip() for w in wake_words_raw.split(",") if w.strip())
        if wake_words_raw
        else DEFAULT_WAKE_WORDS
    )

    # get(名前, 既定値): .env に無ければ既定値を使う。required(名前): .env に無ければ起動しない
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
        db_path=db_path_from_env(env),
        wake_words=wake_words,
        sleep_after=float(get("SLEEP_AFTER_SECONDS", "60")),
        llm_timeout=float(get("LLM_TIMEOUT", "15")),
        stt_timeout=float(get("STT_TIMEOUT", "10")),
        tts_timeout=float(get("TTS_TIMEOUT", "10")),
        dev_plant_id=get("DEV_PLANT_ID"),
    )
