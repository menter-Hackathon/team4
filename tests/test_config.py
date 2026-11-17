import pytest

from agent.config import (
    TTS_INSTRUCTIONS,
    ConfigError,
    aivis_base_url_from_env,
    db_path_from_env,
    load_config,
)

BASE = {
    "LLM_BASE_URL": "http://gpu:8000/v1",
    "LLM_MODEL": "google/gemma-4-E4B-it",
    "STT_BASE_URL": "http://gpu:8001/v1",
    "OPENAI_API_KEY": "sk-test",
}


def test_defaults():
    c = load_config(BASE)
    assert c.stt_model == "kotoba-tech/kotoba-whisper-v2.0-faster"
    assert c.tts_model == "gpt-4o-mini-tts" and c.tts_voice == "coral" and c.tts_base_url is None
    assert c.tts_api_key == "sk-test" and c.llm_api_key == "EMPTY" and c.stt_api_key == "EMPTY"
    assert c.db_path == "./data/futamaru.db"
    assert c.wake_words == ("ふたまる", "二丸", "ふた丸", "2丸", "下丸")
    assert (c.sleep_after, c.llm_timeout, c.stt_timeout, c.tts_timeout) == (60.0, 15.0, 10.0, 10.0)
    assert c.tts_instructions == TTS_INSTRUCTIONS and c.dev_plant_id is None
    # 話し終わりの判定は、LiveKit の既定と同じ値から始める（#39）
    assert (c.vad_min_silence, c.endpointing_delay) == (0.55, 0.5)
    assert c.llm_reasoning_effort is None


def test_overrides():
    c = load_config(
        {
            **BASE,
            "WAKE_WORDS": "ふたまる, ふーちゃん",
            "SLEEP_AFTER_SECONDS": "30",
            "TTS_BASE_URL": "http://gpu:8002/v1",
            "DEV_PLANT_ID": "dev",
            "VAD_MIN_SILENCE_SECONDS": "0.35",
            "ENDPOINTING_DELAY_SECONDS": "0.2",
            "LLM_REASONING_EFFORT": "none",
        }
    )
    assert (c.vad_min_silence, c.endpointing_delay) == (0.35, 0.2)
    assert c.llm_reasoning_effort == "none"
    assert c.wake_words == ("ふたまる", "ふーちゃん") and c.sleep_after == 30.0
    assert c.tts_base_url == "http://gpu:8002/v1" and c.dev_plant_id == "dev"


def test_empty_optional_values_are_unset():
    c = load_config({**BASE, "TTS_BASE_URL": "", "DEV_PLANT_ID": ""})
    assert c.tts_base_url is None and c.dev_plant_id is None


@pytest.mark.parametrize("missing", ["LLM_BASE_URL", "LLM_MODEL", "STT_BASE_URL"])
def test_required(missing):
    env = dict(BASE)
    del env[missing]
    with pytest.raises(ConfigError, match=missing):
        load_config(env)


def test_openai_key_required_only_without_tts_base_url():
    env = dict(BASE)
    del env["OPENAI_API_KEY"]
    with pytest.raises(ConfigError, match="OPENAI_API_KEY"):
        load_config(env)
    assert load_config({**env, "TTS_BASE_URL": "http://gpu:8002/v1"}).tts_api_key == "EMPTY"


def test_db_path_from_env_needs_no_other_variables():
    assert db_path_from_env({}) == "./data/futamaru.db"
    assert db_path_from_env({"FUTAMARU_DB_PATH": "  "}) == "./data/futamaru.db"
    assert db_path_from_env({"FUTAMARU_DB_PATH": "/data/x.db"}) == "/data/x.db"
    assert load_config({**BASE, "FUTAMARU_DB_PATH": "/data/x.db"}).db_path == "/data/x.db"


# --- TTS の種類と AivisSpeech（仕様 §8.3、#56） ---
def test_tts_provider_defaults_to_openai_with_aivis_defaults():
    c = load_config(BASE)
    assert c.tts_provider == "openai" and c.aivis_speaker == 888753762  # まお / あまあま
    assert (c.aivis_base_url, c.aivis_speed, c.aivis_pitch) == ("http://127.0.0.1:10101", 1.0, 0.0)


def test_aivis_reads_its_settings_and_needs_no_openai_key():
    env = {k: v for k, v in BASE.items() if k != "OPENAI_API_KEY"}
    c = load_config(
        {
            **env,
            "TTS_PROVIDER": "Aivis",
            "AIVIS_BASE_URL": "http://gpu:10101",
            "AIVIS_SPEAKER": "888753760",
            "AIVIS_SPEED": "1.1",
            "AIVIS_PITCH": "0.05",
        }
    )
    assert c.tts_provider == "aivis" and c.tts_api_key == "EMPTY"
    assert (c.aivis_base_url, c.aivis_speaker) == ("http://gpu:10101", 888753760)
    assert (c.aivis_speed, c.aivis_pitch) == (1.1, 0.05)


def test_aivis_speaker_must_be_numeric():
    assert load_config({**BASE, "TTS_PROVIDER": "aivis"}).aivis_speaker == 888753762
    with pytest.raises(ConfigError, match="AIVIS_SPEAKER"):
        load_config({**BASE, "TTS_PROVIDER": "aivis", "AIVIS_SPEAKER": "anneli"})


def test_unknown_tts_provider_is_rejected():
    with pytest.raises(ConfigError, match="TTS_PROVIDER"):
        load_config({**BASE, "TTS_PROVIDER": "voicevox"})


def test_aivis_base_url_from_env_needs_no_other_variables():
    assert aivis_base_url_from_env({}) == "http://127.0.0.1:10101"
    assert aivis_base_url_from_env({"AIVIS_BASE_URL": "http://gpu:10101"}) == "http://gpu:10101"
