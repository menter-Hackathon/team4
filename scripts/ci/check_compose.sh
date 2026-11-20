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
LIVEKIT_URL=wss://example.livekit.cloud
LIVEKIT_API_KEY=testkey
LIVEKIT_API_SECRET=testsecret
RELAY_DEVICE_KEY=test-device-key-0123456789
CLOUDFLARE_TUNNEL_TOKEN=test-tunnel-token
RELAY_PUBLIC_HOSTNAME=relay.example.com
DASHBOARD_PUBLIC_HOSTNAME=dashboard.example.com
ENV
docker compose --profile tunnel config --format json > /tmp/compose.json
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

# 1c. LiveKit サーバー・中継・トンネル（仕様 §3.2）
#     - LiveKit サーバーは .env のキーで動き、PC の外には公開しない
#     - エージェントと中継は、.env の LiveKit Cloud の URL ではなく、同じ compose の LiveKit サーバーにつなぐ
#     - 中継も PC の外には公開せず、トンネルが .env のトークンで動く
python3 - <<'PY'
import json
s = json.load(open("/tmp/compose.json"))["services"]
assert s["agent"]["environment"]["LIVEKIT_URL"] == "ws://livekit:7880", s["agent"]["environment"]
assert "livekit" in s["agent"]["depends_on"], s["agent"]["depends_on"]
env = s["livekit"]["environment"]
assert env["LIVEKIT_KEYS"] == "testkey: testsecret", env
assert "udp_port: 7882" in env["LIVEKIT_CONFIG"] and "tcp_port: 7881" in env["LIVEKIT_CONFIG"], env
assert not s["livekit"].get("ports"), s["livekit"].get("ports")
relay = s["relay"]
assert relay["command"] == ["python", "-m", "agent.relay"], relay["command"]
assert relay["environment"]["LIVEKIT_URL"] == "ws://livekit:7880", relay["environment"]
assert relay["environment"]["RELAY_PORT"] == "8780", relay["environment"]
assert not relay.get("ports"), relay.get("ports")
assert s["tunnel"]["environment"]["TUNNEL_TOKEN"] == "test-tunnel-token", s["tunnel"]["environment"]
print("ok: LiveKit と中継は外に公開せず、トンネルだけが外とつながる")
PY

# 1d. トンネルの ingress: .env の RELAY_PUBLIC_HOSTNAME を中継（relay:8780）に届け、ほかは 404 にすること
python3 - <<'PY'
import json
s = json.load(open("/tmp/compose.json"))
tunnel = s["services"]["tunnel"]
assert tunnel["command"][-1] == "run" and "/etc/cloudflared/config.yml" in tunnel["command"], tunnel
assert tunnel["configs"][0]["target"] == "/etc/cloudflared/config.yml", tunnel["configs"]
content = s["configs"]["tunnel-ingress"]["content"]
assert "hostname: relay.example.com" in content and "service: http://relay:8780" in content, content
assert content.rstrip().endswith("service: http_status:404"), content
print("ok: トンネルは RELAY_PUBLIC_HOSTNAME を中継に届け、ほかは 404 にする")
# ダッシュボード（仕様 §6.6）: DASHBOARD_PUBLIC_HOSTNAME をダッシュボードに届ける。最後の 404 より前に置く
dash = "- hostname: dashboard.example.com\n    service: http://dashboard:8765"
assert dash in content, content
assert content.index(dash) < content.index("service: http_status:404"), content
print("ok: トンネルは DASHBOARD_PUBLIC_HOSTNAME をダッシュボードに届ける")
PY
# トンネルは --profile tunnel を付けたときだけ動く（トークンの無い PC で失敗し続けないように）
docker compose config --services | grep -qx tunnel && { echo "tunnel が profile なしで動いてしまう" >&2; exit 1; }
echo "ok: トンネルは --profile tunnel のときだけ動く"

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
#    声のスタイル ID は agent と同じ既定（まお / あまあま）になること
python3 - <<'PY'
import json
env = json.load(open("/tmp/compose.json"))["services"]["tts-init"]["environment"]
assert env["TTS_PROVIDER"] == "openai", env
assert env["AIVIS_SPEAKER"] == "888753762", env
print("ok: 声の設定が空なら tts-init は既定の値で動く")
PY

# 5. ダッシュボードは、PC 側では 127.0.0.1 にだけ公開され（外からはトンネルだけ、1d）、既定で a1,b1 を並べること。
#    DASHBOARD_PLANTS で株を変えられること
python3 - <<'PY'
import json
d = json.load(open("/tmp/compose.json"))["services"]["dashboard"]
ports = d["ports"]
assert len(ports) == 1 and ports[0]["host_ip"] == "127.0.0.1" and str(ports[0]["published"]) == "8765", ports
assert "a1,b1" in d["command"], d["command"]
print("ok: ダッシュボードは PC 側では 127.0.0.1 にだけ公開される（外からはトンネルだけ）")
PY
echo "DASHBOARD_PLANTS=a1" >> .env
docker compose config --format json > /tmp/compose.json
python3 - <<'PY'
import json
cmd = json.load(open("/tmp/compose.json"))["services"]["dashboard"]["command"]
assert cmd[cmd.index("--plants") + 1] == "a1", cmd
print("ok: DASHBOARD_PLANTS で並べる株を変えられる")
PY

rm -f .env
