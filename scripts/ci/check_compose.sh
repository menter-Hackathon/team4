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
    "LLM_BASE_URL": "http://llm:11434/v1",
    "LLM_MODEL": "gemma4:e4b-it-q4_K_M",
    "LLM_REASONING_EFFORT": "none",
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

# 2b. GPU の割り当てを .env の LLM_GPU・STT_GPU で変えられること、llm-init と agent が同じモデルを使うこと
cat >> .env <<'ENV'
LLM_GPU=1
STT_GPU=0
DOCKER_LLM_MODEL=gemma4:e4b-it-q8_0
ENV
docker compose config --format json > /tmp/compose.json
python3 - <<'PY'
import json
s = json.load(open("/tmp/compose.json"))["services"]
gpu = lambda name: s[name]["deploy"]["resources"]["reservations"]["devices"][0]["device_ids"]
assert gpu("llm") == ["1"] and gpu("stt") == ["0"], (gpu("llm"), gpu("stt"))
assert s["llm-init"]["environment"]["LLM_MODEL"] == "gemma4:e4b-it-q8_0", s["llm-init"]["environment"]
assert s["agent"]["environment"]["LLM_MODEL"] == "gemma4:e4b-it-q8_0", s["agent"]["environment"]
print("ok: LLM_GPU・STT_GPU・DOCKER_LLM_MODEL で切り替えられる")
PY

# 3. .env.example のままでも compose の設定が組み立てられ、LLM は 0 番・STT は 1 番の GPU を使うこと
cp .env.example .env
docker compose config -q
docker compose config --format json > /tmp/compose.json
python3 - <<'PY'
import json
s = json.load(open("/tmp/compose.json"))["services"]
gpu = lambda name: s[name]["deploy"]["resources"]["reservations"]["devices"][0]["device_ids"]
assert gpu("llm") == ["0"] and gpu("stt") == ["1"], (gpu("llm"), gpu("stt"))
print("ok: 既定では LLM が 0 番、STT が 1 番の GPU を使う")
PY

# 4. 声の設定が空（.env.example のまま）なら、tts-init は OpenAI の声として何もしない設定になり、
#    声のスタイル ID は agent と同じ既定（らせつん / 標準２）になること
python3 - <<'PY'
import json
env = json.load(open("/tmp/compose.json"))["services"]["tts-init"]["environment"]
assert env["TTS_PROVIDER"] == "openai", env
assert env["AIVIS_SPEAKER"] == "893625030", env
print("ok: 声の設定が空なら tts-init は既定の値で動く")
PY

# 5. ダッシュボードは、この PC（127.0.0.1）にだけ公開され、既定で a1,b1 を並べること。
#    DASHBOARD_PLANTS で株を変えられること
python3 - <<'PY'
import json
d = json.load(open("/tmp/compose.json"))["services"]["dashboard"]
ports = d["ports"]
assert len(ports) == 1 and ports[0]["host_ip"] == "127.0.0.1" and str(ports[0]["published"]) == "8765", ports
assert "a1,b1" in d["command"], d["command"]
print("ok: ダッシュボードはこの PC からだけ開ける")
PY
echo "DASHBOARD_PLANTS=a1" >> .env
docker compose config --format json > /tmp/compose.json
python3 - <<'PY'
import json
cmd = json.load(open("/tmp/compose.json"))["services"]["dashboard"]["command"]
assert cmd[cmd.index("--plants") + 1] == "a1", cmd
print("ok: DASHBOARD_PLANTS で並べる株を変えられる")
PY

# 6. 声（tts）は既定では CPU 版で、STT は Speaches の既定の持ち方であること（docs/deploy.md の 2.5）
python3 - <<'PY'
import json
s = json.load(open("/tmp/compose.json"))["services"]
assert s["tts"]["image"].endswith(":cpu-1.2.0"), s["tts"]["image"]
assert "deploy" not in s["tts"], s["tts"].get("deploy")
assert s["stt"]["environment"]["WHISPER__COMPUTE_TYPE"] == "default", s["stt"]["environment"]
print("ok: 既定では声は CPU、STT は既定の持ち方")
PY

# 7. .env の COMPOSE_FILE で compose.tts-gpu.yaml を足すと、声が GPU 版になり、TTS_GPU の GPU を使うこと。
#    DOCKER_STT_COMPUTE_TYPE で STT を 8 ビットにできること（GPU 1 枚に全部載せるとき）
cat >> .env <<'ENV'
COMPOSE_FILE=compose.yaml:compose.tts-gpu.yaml
TTS_GPU=1
DOCKER_STT_COMPUTE_TYPE=int8_float16
ENV
docker compose config --format json > /tmp/compose.json
python3 - <<'PY'
import json
s = json.load(open("/tmp/compose.json"))["services"]
tts = s["tts"]
assert tts["image"].endswith(":nvidia-1.2.0"), tts["image"]
assert "--use_gpu" in " ".join(tts["entrypoint"]), tts["entrypoint"]
assert tts["deploy"]["resources"]["reservations"]["devices"][0]["device_ids"] == ["1"], tts["deploy"]
# 声のモデルのボリュームは CPU 版と同じものを使う（入れた声を GPU 版でも使える）
assert any(v["source"] == "aivis-data" for v in tts["volumes"]), tts["volumes"]
assert s["stt"]["environment"]["WHISPER__COMPUTE_TYPE"] == "int8_float16", s["stt"]["environment"]
print("ok: compose.tts-gpu.yaml で声が GPU 版になり、STT を 8 ビットにできる")
PY

rm -f .env
