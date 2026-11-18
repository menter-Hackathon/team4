#!/usr/bin/env bash
# compose.yaml の設定が正しく組み立てられるかを確かめる（CI 用。docs/deploy.md）
# Docker が使える環境で、リポジトリの一番上から実行する
set -euo pipefail

# このスクリプトは .env を作り直して最後に消す。手元で動かすと自分の .env（キー）が消えるので、CI でだけ動かす
if [ -z "${CI:-}" ]; then
  echo "このスクリプトは CI 専用です（.env を上書きして消すため）。手元では動かさないでください" >&2
  exit 1
fi

# 1. 開発用の .env（LLM・STT を OpenAI に向けた設定）を使っても、
#    コンテナ内の LLM・STT が使われること
cp .env.example .env
cat >> .env <<'ENV'
HF_TOKEN=dummy
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o-mini
STT_BASE_URL=https://api.openai.com/v1
STT_MODEL=gpt-4o-mini-transcribe
DEV_PLANT_ID=dev
TTS_PROVIDER=aivis
AIVIS_BASE_URL=http://127.0.0.1:10101
AIVIS_SPEAKER=893625024
ENV
docker compose config --format json > /tmp/compose.json
python3 - <<'PY'
import json
env = json.load(open("/tmp/compose.json"))["services"]["agent"]["environment"]
want = {
    "LLM_BASE_URL": "http://llm:8000/v1",
    "LLM_MODEL": "google/gemma-4-E4B-it",
    "STT_BASE_URL": "http://stt:8000/v1",
    "STT_MODEL": "kotoba-tech/kotoba-whisper-v2.0-faster",
    "DEV_PLANT_ID": "",
    # 開発用に手元の AivisSpeech（127.0.0.1）を向けていても、コンテナの tts が使われる
    "AIVIS_BASE_URL": "http://tts:10101",
}
got = {k: env.get(k) for k in want}
assert got == want, f"agent の環境変数が想定と違う: {got}"
print("ok: .env の開発用の設定より compose の設定が優先される")
PY

# 1b. tts-init が .env の声の設定（TTS_PROVIDER・AIVIS_SPEAKER）を受け取り、コンテナの tts につなぐこと
python3 - <<'PY'
import json
env = json.load(open("/tmp/compose.json"))["services"]["tts-init"]["environment"]
assert env["TTS_PROVIDER"] == "aivis", env
assert env["AIVIS_SPEAKER"] == "893625024", env
assert env["AIVIS_URL"] == "http://tts:10101", env
assert env["AIVIS_MODELS"], env
print("ok: tts-init が .env の声の設定を受け取る")
PY

# 2. DOCKER_STT_MODEL で STT のモデルを切り替えられること（stt-init と agent の両方）
echo "DOCKER_STT_MODEL=Systran/faster-whisper-large-v3" >> .env
docker compose config --format json > /tmp/compose.json
python3 - <<'PY'
import json
s = json.load(open("/tmp/compose.json"))["services"]
assert s["agent"]["environment"]["STT_MODEL"] == "Systran/faster-whisper-large-v3", s["agent"]["environment"]
assert s["stt-init"]["environment"]["STT_MODEL"] == "Systran/faster-whisper-large-v3", s["stt-init"]["environment"]
print("ok: DOCKER_STT_MODEL で STT のモデルを切り替えられる")
PY

# 3. HF_TOKEN は任意（Gemma 4 E4B は同意不要の公開モデル）。空でも compose の設定が組み立てられること
cp .env.example .env
docker compose config -q
echo "ok: HF_TOKEN が空でも起動できる"

# 4. 声の設定が空（.env.example のまま）なら、tts-init は OpenAI の声として何もしない設定になり、
#    声のスタイル ID は agent と同じ既定（まお / あまあま）になること
docker compose config --format json > /tmp/compose.json
python3 - <<'PY'
import json
env = json.load(open("/tmp/compose.json"))["services"]["tts-init"]["environment"]
assert env["TTS_PROVIDER"] == "openai", env
assert env["AIVIS_SPEAKER"] == "888753762", env
print("ok: 声の設定が空なら tts-init は既定の値で動く")
PY

rm -f .env
