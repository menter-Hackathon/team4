# バックエンドの Docker 化 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** NVIDIA の GPU がある PC で `docker compose up` するだけで、エージェント・LLM（Gemma 4 E4B）・STT（kotoba-whisper）が立ち上がるようにする。

**Architecture:** エージェントはこのリポジトリから作るイメージ、LLM は `vllm/vllm-openai`、STT は `ghcr.io/speaches-ai/speaches` の CUDA 版。1 つの `compose.yaml` で起動し、エージェントは LLM の準備完了と STT のモデル読み込み完了を待ってから起動する。

**Tech Stack:** Docker / Docker Compose、uv、vLLM、Speaches、GitHub Actions

**Spec:** `docs/superpowers/specs/2026-11-16-docker-backend-design.md`

## Global Constraints

- デプロイ先は NVIDIA の GPU（24GB 以上）がある x86_64 の Windows（Docker Desktop＋WSL2）か Linux（NVIDIA Container Toolkit）
- LLM は `google/gemma-4-E4B-it`、vLLM は 0.19 以上。思考モードは使わない（`enable_thinking` を渡さない。仕様 §8.1）
- vLLM: `--max-model-len 16384`、`--gpu-memory-utilization 0.8`（STT の分を残す）
- STT は `kotoba-tech/kotoba-whisper-v2.0-faster`、`STT_MODEL_TTL=-1`（常駐）
- キー類（LiveKit・OpenAI・`HF_TOKEN`）は `.env` から。イメージ・リポジトリに入れない
- LLM・STT の URL は compose 側で設定し、`.env` の値より優先する
- 会話 DB はホストの `./data`、モデルは名前付きボリュームに保存する
- 開発用の Mac に Docker は無い。イメージのビルドと compose ファイルの検証は GitHub Actions で行う
- コメントは初学者向けに多めに書く（AGENTS.md）

## Review Focus

- GPU メモリが足りない（24GB 未満）→ vLLM がメモリ不足で落ちる。手順書に、使用率と最大文脈長を下げる方法を書く（Task 3）
- `HF_TOKEN` が無い・Gemma のライセンス未同意 → LLM のダウンロードが 401/403 で失敗する。手順書に症状と対処を書く（Task 3）
- Speaches が kotoba-whisper のリポジトリを受け付けない → `stt-init` が失敗し、エージェントが起動しない。`.env` の `STT_MODEL` で別のモデルに切り替えられるようにし、手順書に書く（Task 2・3）
- 開発用の `.env`（LLM を OpenAI に向けた設定）をそのまま使う → compose の `environment` が URL・モデル名を上書きし、コンテナ内の LLM・STT を使う。CI で `docker compose config` の結果を確かめる（Task 2）
- 初回のモデルのダウンロードが長い（十数 GB）→ エージェントが先に起動して失敗しないよう、ヘルスチェックの待ち時間を長く取る（Task 2）

---

### Task 1: エージェントのイメージ

**Files:**
- Create: `Dockerfile`, `.dockerignore`
- Modify: `.github/workflows/test.yml`（ジョブ `docker` を追加）

**Interfaces:**
- Produces: イメージの中の作業フォルダは `/app`、起動コマンドは `python -m agent.main start`。DB の既定の場所は `/app/data/futamaru.db`（`FUTAMARU_DB_PATH`）

TDD の例外: Dockerfile は設定ファイルなので単体テストは書かず、CI のジョブで「イメージが作れる」「中でエージェントを読み込める」ことを確かめる。

- [ ] **Step 1: `Dockerfile` を書く**
  - ベースは `python:3.12-slim`、`uv` は `ghcr.io/astral-sh/uv` イメージからコピーする（バージョン固定）
  - 依存は `uv sync --locked --no-dev` で入れる。`pyproject.toml`・`uv.lock` を先にコピーして依存の層をキャッシュさせる
  - VAD などのモデルはビルド時に `python -m agent.main download-files` で取り込む（起動を速くするため）
  - `CMD ["python", "-m", "agent.main", "start"]`
- [ ] **Step 2: `.dockerignore` を書く**: `.venv`・`data`・`.env`・`.git`・キャッシュ類・`docs`・`tests` を除く
- [ ] **Step 3: CI にジョブ `docker` を足す**: `docker build -t futamaru-agent .` のあと `docker run --rm futamaru-agent python -c "import agent.main"` が通ること
- [ ] **Step 4: push して CI を確認する**
  Expected: ジョブ `docker` が成功。`.env` やキーがイメージに入っていないこと（`docker run --rm futamaru-agent ls -a /app` に `.env` が無い）もジョブで確かめる
- [ ] **Step 5: Commit**: `feat: add a Docker image for the agent`

### Task 2: compose ファイル

**Files:**
- Create: `compose.yaml`
- Modify: `.env.example`（`HF_TOKEN=` を値なしで追加）、`.github/workflows/test.yml`（ジョブ `docker` に compose の検証を追加）

**Interfaces:**
- Consumes: Task 1 のイメージ（`build: .`）
- Produces: サービス名 `agent`・`llm`・`stt`・`stt-init`。エージェントからは `http://llm:8000/v1`・`http://stt:8000/v1`

- [ ] **Step 1: `compose.yaml` を書く**
  - `llm`: `vllm/vllm-openai` のタグを固定（0.19 以上で Gemma 4 対応のもの。Docker Hub で確かめる）。コマンドは `google/gemma-4-E4B-it --max-model-len 16384 --gpu-memory-utilization 0.8`。`HF_TOKEN` を `.env` から渡す。`--ipc=host` 相当（`ipc: host`）。モデルは名前付きボリューム `hf-cache` を `/root/.cache/huggingface` に。GPU は `deploy.resources.reservations.devices`（`driver: nvidia`, `count: all`, `capabilities: [gpu]`）。ヘルスチェックは `/health`（初回ダウンロードを見込み、合計 60 分程度待つ）
  - `stt`: `ghcr.io/speaches-ai/speaches` の CUDA 版のタグを固定。`STT_MODEL_TTL=-1`。モデルは名前付きボリューム `speaches-cache` を `/home/ubuntu/.cache/huggingface/hub` に。GPU は `llm` と同じ書き方
  - `stt-init`: 一度だけ動くコンテナ（`curlimages/curl`）。`stt` が起動するまで再試行しながら `POST http://stt:8000/v1/models/${STT_MODEL}` でモデルをダウンロードさせ、成功したら終わる。`STT_MODEL` の既定値は `kotoba-tech/kotoba-whisper-v2.0-faster`
  - `agent`: `build: .`、`env_file: .env`。`environment` で `LLM_BASE_URL=http://llm:8000/v1`・`LLM_MODEL=google/gemma-4-E4B-it`・`LLM_API_KEY=EMPTY`・`STT_BASE_URL=http://stt:8000/v1`・`STT_MODEL=${STT_MODEL:-kotoba-tech/kotoba-whisper-v2.0-faster}`・`STT_API_KEY=EMPTY`・`FUTAMARU_DB_PATH=/app/data/futamaru.db`・`DEV_PLANT_ID=`（空）を上書きする。`./data:/app/data`。`depends_on`: `llm`（`service_healthy`）、`stt-init`（`service_completed_successfully`）。`restart: unless-stopped`
- [ ] **Step 2: `.env.example` に `HF_TOKEN=` を足す**（コメントで「Docker で LLM を動かすときだけ必要。Hugging Face で Gemma 4 のライセンスに同意してから発行」）
- [ ] **Step 3: CI のジョブ `docker` に検証を足す**: `cp .env.example .env` のあと `docker compose config -q` が通ること。さらに、`.env` の `LLM_BASE_URL` を `https://api.openai.com/v1` にしても、`docker compose config` の `agent` の `LLM_BASE_URL` が `http://llm:8000/v1` のままであること（Review Focus 4）
- [ ] **Step 4: push して CI を確認する**。Expected: ジョブ `docker` が成功
- [ ] **Step 5: Commit**: `feat: add compose file for agent, vLLM, and Speaches`

### Task 3: デプロイ手順書

**Files:**
- Create: `docs/deploy.md`
- Modify: `README.md`（起動の節に Docker での起動と `docs/deploy.md` へのリンクを足す）、仕様書 §2 の「エージェント実行場所」に Docker で動かすことを 1 行足す

- [ ] **Step 1: `docs/deploy.md` を書く**。中身:
  1. 必要なもの: NVIDIA の GPU（24GB 以上）、Windows なら Docker Desktop＋WSL2、Linux なら Docker＋NVIDIA Container Toolkit、GPU がコンテナから見えるかの確認コマンド（`docker run --rm --gpus all nvidia/cuda:<タグ> nvidia-smi`）
  2. Hugging Face: Gemma 4 E4B のページでライセンスに同意し、読み取り用トークンを発行する
  3. `.env` の作り方（`cp .env.example .env`、LiveKit・OpenAI のキーは Discord の DM で受け取る、`HF_TOKEN` を書く）
  4. 起動・停止・ログの見方（`docker compose up -d`、`docker compose logs -f agent`、`docker compose down`）。初回はダウンロードで時間がかかること
  5. 動いたかの確かめ方: `agent` のログに LiveKit への接続が出る。ESP32 が `plant-<id>` に入ると会話できる
  6. 困ったとき: GPU メモリ不足（`--gpu-memory-utilization`・`--max-model-len` を下げる）、401/403（`HF_TOKEN` とライセンス）、`stt-init` の失敗（`.env` に `STT_MODEL=Systran/faster-whisper-large-v3` などを書いて切り替える）
  7. できないこと: console モード（PC のマイクで試す）は Docker では使えない。console は今までどおり `uv run python -m agent.main console`
  8. データ: 植物の記憶は `./data` に残る。`docker compose down -v` はモデルのボリュームを消す（再ダウンロードになる）が、`./data` は消えない
- [ ] **Step 2: README と仕様書を直す**
- [ ] **Step 3: 全体の確認**: `uvx ruff format . && uvx ruff check . && uv run pytest` と CI のジョブすべてが成功
- [ ] **Step 4: Commit と PR**: `docs: add a deployment guide for the Docker setup`。PR 本文はテンプレートを使い、「GPU を使った起動はデプロイ担当の PC で未確認」と明記する
