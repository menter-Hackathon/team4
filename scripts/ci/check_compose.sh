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
}
got = {k: env.get(k) for k in want}
assert got == want, f"agent の環境変数が想定と違う: {got}"
print("ok: .env の開発用の設定より compose の設定が優先される")
PY

# 2. DOCKER_STT_MODEL で STT のモデルを切り替えられること（stt-init と agent の両方）
echo "DOCKER_STT_MODEL=Systran/faster-whisper-large-v3" >> .env
docker compose config --format json > /tmp/compose.json
python3 - <<'PY'
import json
s = json.load(open("/tmp/compose.json"))["services"]
assert s["agent"]["environment"]["STT_MODEL"] == "Systran/faster-whisper-large-v3", s["agent"]["environment"]
assert s["stt-init"]["command"][-1] == "http://stt:8000/v1/models/Systran/faster-whisper-large-v3", s["stt-init"]["command"]
print("ok: DOCKER_STT_MODEL で STT のモデルを切り替えられる")
PY

# 3. HF_TOKEN が空なら、起動前にエラーで止まること
cp .env.example .env
if docker compose config -q 2>/tmp/err.txt; then
  echo "NG: HF_TOKEN が空なのに compose config が通ってしまった" >&2
  exit 1
fi
grep -q "HF_TOKEN" /tmp/err.txt
echo "ok: HF_TOKEN が空ならエラーで止まる"

rm -f .env
