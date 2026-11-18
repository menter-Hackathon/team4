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
# WAKE_WORDS が .env に無いときに使うウェイクワード。STT がよく書く表記をまとめている。
# 「下丸」は「ふたまるくん」が「下丸くん」と聞き取られたことがあるので足した（2026-11-16、#2）
DEFAULT_WAKE_WORDS = ("ふたまる", "二丸", "ふた丸", "2丸", "下丸")
# 使える TTS の種類（TTS_PROVIDER）。openai: OpenAI TTS、aivis: ローカルの AivisSpeech（仕様 §8.3、#56）
TTS_PROVIDERS = ("openai", "aivis")
# AIVIS_BASE_URL が .env に無いときの AivisSpeech Engine の URL（AivisSpeech の既定のポートは 10101）
DEFAULT_AIVIS_BASE_URL = "http://127.0.0.1:10101"
# AIVIS_SPEAKER が .env に無いときの声: 「まお」の「あまあま」スタイル（2026-11-17 に聞き比べて決定、#56）。
# AivisSpeech に「まお」のモデルが入っている前提。入っていなければ --list で別の ID を調べて .env に書く
DEFAULT_AIVIS_SPEAKER = 888753762
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
    # LLM に送る reasoning_effort（例: "none"）。None なら送らない。
    # Ollama の Gemma 4 は思考モードが既定でオンなので、"none" にして切る（#1・#37）
    llm_reasoning_effort: str | None
    stt_base_url: str  # STT サーバーの URL
    stt_model: str  # STT のモデル名
    stt_api_key: str
    tts_model: str  # TTS のモデル名
    tts_voice: str  # 声の種類（例: coral）
    tts_base_url: str | None  # None なら本物の OpenAI
    tts_api_key: str
    tts_instructions: str  # 話し方の指示（OpenAI TTS だけ。AivisSpeech には無い）
    tts_provider: str  # "openai" か "aivis"（仕様 §8.3）
    aivis_base_url: str  # AivisSpeech Engine の URL
    aivis_speaker: int  # AivisSpeech の声のスタイル ID（既定: まお / あまあま）
    aivis_speed: float  # AivisSpeech の話す速さ（1.0 がふつう）
    aivis_pitch: float  # AivisSpeech の声の高さ（0.0 がふつう）
    db_path: str  # 会話ログ・記憶を保存する DB ファイルの場所
    wake_words: tuple[str, ...]  # ウェイクワードの一覧
    sleep_after: float  # 何秒だれも話さなければ眠るか
    llm_timeout: float  # 各サーバーの応答をこの秒数まで待つ
    stt_timeout: float
    tts_timeout: float
    dev_plant_id: str | None  # console モードで使う株の id（開発用）
    # --- 話し終わりの判定（#39）。短くするほど返事が早いが、言葉の途中の間で割り込みやすくなる ---
    vad_min_silence: float  # 声が途切れてから「話し終わった」とみなすまでの無音の秒数（VAD）
    endpointing_delay: float  # VAD が話し終わりと判定してから、返事を作り始めるまでの待ちの秒数
    # --- 天気（気象庁のデータ、仕様 §6.7、#80）。両方 None なら天気を使わない ---
    jma_amedas_station: str | None  # 気温・湿度を取るアメダスの観測所のコード（例: "44132" 東京）
    jma_forecast_area: str | None  # 空の様子を取る予報の地域コード（例: "130000" 東京都）
    weather_dry_humidity: float  # 湿度がこの値（%）未満なら「乾燥している」と言う
    water_remind_days: int  # 水をもらってからこの日数たつと「のどが渇いた」と言う（§6.7、#81）
    plant_species: str  # 植物の種類（例: ポトス）。育て方のメモをプロンプトに入れる（§6.7、#83）


def db_path_from_env(env: Mapping[str, str] = os.environ) -> str:
    """DB ファイルの場所（FUTAMARU_DB_PATH）だけを読む。未設定・空なら DEFAULT_DB_PATH。

    scripts/people.py のように DB だけを使うコマンド用。load_config() は LLM・STT の
    URL など DB に関係ない環境変数も必須にするので、それを使わずに済むよう分けている。
    例: {} → "./data/futamaru.db"、{"FUTAMARU_DB_PATH": "/data/x.db"} → "/data/x.db"
    """
    return env.get("FUTAMARU_DB_PATH", "").strip() or DEFAULT_DB_PATH


def aivis_base_url_from_env(env: Mapping[str, str] = os.environ) -> str:
    """AivisSpeech Engine の URL（AIVIS_BASE_URL）だけを読む。未設定・空なら DEFAULT_AIVIS_BASE_URL。

    声を選ぶコマンド（scripts/aivis_voices.py）用。db_path_from_env と同じく、
    LLM・STT の設定が無くても使えるように load_config() と分けている。
    """
    return env.get("AIVIS_BASE_URL", "").strip() or DEFAULT_AIVIS_BASE_URL


def _is_code(value: str, digits: int) -> bool:
    """value が digits 桁の数字だけでできているか。例: ("44132", 5) → True、("東京", 5) → False"""
    return len(value) == digits and value.isascii() and value.isdigit()


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

    # 1. どの TTS を使うか。既定は今までどおり OpenAI
    tts_provider = get("TTS_PROVIDER", "openai").lower()
    if tts_provider not in TTS_PROVIDERS:
        raise ConfigError(
            f"TTS_PROVIDER は {' / '.join(TTS_PROVIDERS)} のどれかにしてください: {tts_provider}"
        )

    # 2. TTS のキー: 本物の OpenAI を使う（OpenAI TTS で TTS_BASE_URL が無い）ならキーが必須。
    #    自前の TTS・AivisSpeech ならキーは要らないので、仮の値 "EMPTY" にする
    tts_base_url = get("TTS_BASE_URL")
    tts_api_key = get("OPENAI_API_KEY")
    if tts_api_key is None:
        if tts_provider == "openai" and tts_base_url is None:
            raise ConfigError("環境変数 OPENAI_API_KEY が設定されていません（TTS に必要）")
        tts_api_key = "EMPTY"

    # 3. AivisSpeech の声のスタイル ID。無ければ聞き比べて決めた声（まお / あまあま）
    aivis_speaker_raw = get("AIVIS_SPEAKER", str(DEFAULT_AIVIS_SPEAKER))
    try:
        aivis_speaker = int(aivis_speaker_raw)
    except ValueError as e:
        raise ConfigError(
            f"AIVIS_SPEAKER は数字（スタイル ID）にしてください: {aivis_speaker_raw}"
        ) from e

    # WAKE_WORDS はカンマ区切り（例: "ふたまる,二丸"）。空の項目は捨てる
    wake_words_raw = get("WAKE_WORDS")
    wake_words = (
        tuple(w.strip() for w in wake_words_raw.split(",") if w.strip())
        if wake_words_raw
        else DEFAULT_WAKE_WORDS
    )

    # 天気を取る場所（気象庁のコード、#80）。
    # 観測所は 5 桁（例: 東京 44132）、地域は 6 桁（例: 東京都 130000）の数字。
    # 桁数を間違えていると、気象庁から何も取れないまま「天気なし」で動き続け、気づきにくい。
    # そのため、起動するときに確かめて、違っていれば止める（どちらも空なら天気を使わない）
    jma_amedas_station = get("JMA_AMEDAS_STATION")
    if jma_amedas_station is not None and not _is_code(jma_amedas_station, 5):
        raise ConfigError(
            f"JMA_AMEDAS_STATION は 5 桁の数字（例: 44132）にしてください: {jma_amedas_station}"
        )
    jma_forecast_area = get("JMA_FORECAST_AREA")
    if jma_forecast_area is not None and not _is_code(jma_forecast_area, 6):
        raise ConfigError(
            f"JMA_FORECAST_AREA は 6 桁の数字（例: 130000）にしてください: {jma_forecast_area}"
        )

    # get(名前, 既定値): .env に無ければ既定値を使う。required(名前): .env に無ければ起動しない
    return Config(
        llm_base_url=required("LLM_BASE_URL"),
        llm_model=required("LLM_MODEL"),
        llm_api_key=get("LLM_API_KEY", "EMPTY"),
        llm_reasoning_effort=get("LLM_REASONING_EFFORT"),
        stt_base_url=required("STT_BASE_URL"),
        stt_model=get("STT_MODEL", "kotoba-tech/kotoba-whisper-v2.0-faster"),
        stt_api_key=get("STT_API_KEY", "EMPTY"),
        tts_model=get("TTS_MODEL", "gpt-4o-mini-tts"),
        tts_voice=get("TTS_VOICE", "coral"),
        tts_base_url=tts_base_url,
        tts_api_key=tts_api_key,
        tts_instructions=TTS_INSTRUCTIONS,
        tts_provider=tts_provider,
        aivis_base_url=aivis_base_url_from_env(env),
        aivis_speaker=aivis_speaker,
        aivis_speed=float(get("AIVIS_SPEED", "1.0")),
        aivis_pitch=float(get("AIVIS_PITCH", "0.0")),
        db_path=db_path_from_env(env),
        wake_words=wake_words,
        sleep_after=float(get("SLEEP_AFTER_SECONDS", "60")),
        llm_timeout=float(get("LLM_TIMEOUT", "15")),
        stt_timeout=float(get("STT_TIMEOUT", "10")),
        tts_timeout=float(get("TTS_TIMEOUT", "10")),
        dev_plant_id=get("DEV_PLANT_ID"),
        # 既定の値は LiveKit の既定と同じ（Silero VAD 0.55 秒、endpointing 0.5 秒）。#39 で測って決める
        vad_min_silence=float(get("VAD_MIN_SILENCE_SECONDS", "0.55")),
        endpointing_delay=float(get("ENDPOINTING_DELAY_SECONDS", "0.5")),
        jma_amedas_station=jma_amedas_station,
        jma_forecast_area=jma_forecast_area,
        weather_dry_humidity=float(get("WEATHER_DRY_HUMIDITY", "40")),
        water_remind_days=int(get("WATER_REMIND_DAYS", "3")),
        plant_species=get("PLANT_SPECIES", "ポトス"),
    )
