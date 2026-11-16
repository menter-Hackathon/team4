# ふたまるの「脳」を Docker で動かす（デプロイ手順）

NVIDIA の GPU がある PC で、ふたまるの脳（エージェント・LLM・STT）をまとめて動かす手順です。
鉢（ESP32）にはマイクとスピーカーだけがあり、LiveKit Cloud を通してこの PC の脳とつながります。

```
鉢（ESP32: マイク・スピーカー） ──> LiveKit Cloud <── この PC の Docker
                                                      ├─ agent（エージェント）
                                                      ├─ llm（Gemma 4 E4B）
                                                      └─ stt（kotoba-whisper）
                                   声（TTS）は OpenAI を使う
```

設計: `docs/superpowers/specs/2026-11-16-docker-backend-design.md`

## 1. 必要なもの

- **NVIDIA の GPU（メモリ 24GB 以上）がある PC**（x86_64）。LLM と STT を同じ GPU に載せます
- Docker
  - **Windows**: Docker Desktop（WSL2 を使う設定）と、最新の NVIDIA ドライバ
  - **Linux**: Docker Engine と NVIDIA Container Toolkit
- ディスクの空き 40GB 以上（モデルとイメージで十数〜数十 GB）

**GPU がコンテナから見えるか**を先に確かめます。GPU の名前とメモリが表示されれば OK です。

```bash
docker run --rm --gpus all nvidia/cuda:12.9.1-base-ubuntu24.04 nvidia-smi
```

## 2. Hugging Face のトークンを用意する

Gemma 4 はダウンロードの前にライセンスへの同意が必要です。

1. https://huggingface.co にログインし、[google/gemma-4-E4B-it](https://huggingface.co/google/gemma-4-E4B-it) のページでライセンスに同意する
2. Settings → Access Tokens で、**Read** のトークンを作る（`hf_...`）

## 3. リポジトリと `.env` を用意する

```bash
git clone https://github.com/menter-Hackathon/team4.git
cd team4
cp .env.example .env
```

`.env` に次を書きます。**キーはチームの Discord の DM で受け取り、Issue・PR・チャンネルには書かないでください。**

| 項目 | 書くもの |
|---|---|
| `LIVEKIT_URL` / `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` | チームの LiveKit Cloud の値 |
| `OPENAI_API_KEY` | チームの OpenAI のキー（植物の声に使う） |
| `HF_TOKEN` | 2 で作った Hugging Face のトークン |

`LLM_BASE_URL`・`LLM_MODEL`・`STT_BASE_URL`・`STT_MODEL` は書かなくて大丈夫です。Docker では `compose.yaml` の設定（コンテナ内の LLM・STT）が使われます。開発用に OpenAI を向けた値が書いてあっても、Docker では無視されます。

## 4. 起動する

```bash
docker compose up -d
```

**初回はモデルのダウンロードで 10〜30 分ほどかかります。** 進み具合はログで見られます。

```bash
docker compose ps                # 各サービスの状態
docker compose logs -f llm       # LLM のダウンロード・起動のログ
docker compose logs stt-init     # STT のモデルのダウンロード結果
docker compose logs -f agent     # エージェントのログ（Ctrl+C で抜ける。止まりはしない）
```

起動の順番は自動で決まります。`llm` の準備ができて `stt-init` が終わると、`agent` が起動します。それまで `agent` は「待機中」のままです。

## 5. 動いたか確かめる

1. `docker compose ps` で `llm` が `healthy`、`stt-init` が `exited (0)`、`agent` が `running` になっている
2. `docker compose logs agent` に `registered worker` などの、LiveKit Cloud へ接続したログが出ている
3. 鉢（ESP32）の電源を入れる。ESP32 がルーム `plant-<id>` に入ると、エージェントが参加する
4. 「ふたまる」と呼びかけて、植物から話しかけてくれば成功

## 6. 止める・作り直す

```bash
docker compose down                 # 止める（モデルも植物の記憶も残る）
git pull && docker compose up -d --build   # コードを更新して起動し直す
```

- **植物の記憶**（会話ログ・性格）は、この PC の `data/futamaru.db` に残ります。コンテナを作り直しても消えません
- `docker compose down -v` はモデルの保存場所も消します（次の起動で再ダウンロード）。植物の記憶（`data/`）は消えません

## 7. 困ったとき

| 症状 | 原因と対処 |
|---|---|
| 起動時に `.env に HF_TOKEN を書いてください` と出る | `.env` の `HF_TOKEN` が空。2 のトークンを書く |
| `llm` のログに `401` / `403` / `gated` | トークンが違うか、Gemma 4 のライセンスに同意していない。2 をやり直す |
| `llm` のログに `out of memory` / `CUDA error` | GPU のメモリ不足。`compose.yaml` の `--gpu-memory-utilization` を `0.7` に、`--max-model-len` を `8192` に下げて `docker compose up -d` |
| `could not select device driver "nvidia"` | Docker から GPU が見えていない。1 の `nvidia-smi` の確認からやり直す（Windows は Docker Desktop の WSL2 設定、Linux は NVIDIA Container Toolkit） |
| `stt-init` が `exited (22)` などで終わり、`agent` が起動しない | STT のモデルを読み込めなかった（`stt-init` は最大 10 分ほど再試行してから止まる）。`docker compose logs stt-init` を確認し、`.env` に `DOCKER_STT_MODEL=Systran/faster-whisper-large-v3` を書いて `docker compose up -d` |
| `agent` がすぐ止まる | `docker compose logs agent` を見る。`環境変数 … が設定されていません` なら `.env` の書き漏れ |

解決しないときは、`docker compose logs` の該当部分を Discord に貼ってください（**キーが含まれていないか確認してから**）。

## できないこと

- **console モード（PC のマイクで試す）は Docker では使えません。** コンテナからは PC のマイク・スピーカーに触れないためです。console は今までどおり `uv run python -m agent.main console` で使います（README）
- GPU の無い PC・Mac では、この構成は動きません
