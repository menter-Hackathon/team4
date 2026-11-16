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
  - **24GB 未満の GPU では動きません。** LLM（Gemma 4 E4B）の重みだけで約 16GB あり、設定の調整では収まりません
- **NVIDIA ドライバ 580 以上**（CUDA 13.0 対応）。LLM のイメージが CUDA 13.0 で作られているためです
- Docker
  - **Windows**: Docker Desktop（WSL2 を使う設定）
  - **Linux**: Docker Engine と NVIDIA Container Toolkit
- ディスクの空き 40GB 以上（モデルとイメージで十数〜数十 GB）

**GPU とドライバがコンテナから使えるか**を先に確かめます。

```bash
docker run --rm --gpus all nvidia/cuda:13.0.1-base-ubuntu24.04 nvidia-smi
```

表示された表の右上の **`CUDA Version` が 13.0 以上**で、GPU のメモリ（`MiB` の右側）が 24GB（約 24000MiB）以上なら OK です。CUDA Version が 12.x なら、NVIDIA のサイトからドライバを更新してください。

## 2. （任意）Hugging Face のトークンを用意する

Gemma 4 E4B（[google/gemma-4-E4B-it](https://huggingface.co/google/gemma-4-E4B-it)）は同意不要の公開モデル（Apache 2.0）なので、**トークンが無くてもダウンロードできます。** ダウンロードが回数制限で失敗するときだけ、https://huggingface.co の Settings → Access Tokens で **Read** のトークン（`hf_...`）を作って `.env` の `HF_TOKEN` に書いてください。

## 3. リポジトリと `.env` を用意する

**Windows の場合は、WSL2 の Ubuntu の中にクローンしてください**（スタートメニューから「Ubuntu」を開き、そのターミナルで下のコマンドを実行する）。`C:\` の下に置くと、植物の記憶の DB（SQLite）が Windows と WSL2 の間のファイル共有を通るため、壊れたり「database is locked」になったりすることがあります。以降のコマンドも、すべてこの Ubuntu のターミナルで実行します。

```bash
git clone https://github.com/menter-Hackathon/team4.git
cd team4
cp .env.example .env
mkdir -p data
```

`mkdir -p data` は、植物の記憶の DB を置くフォルダを先に作っておくためです。作らずに起動すると、Docker が `root` の持ち物として作るので、あとで自分のユーザーから中を消したり見たりしにくくなります（#40）。

`.env` に次を書きます。**キーはチームの Discord の DM で受け取り、Issue・PR・チャンネルには書かないでください。**

| 項目 | 書くもの |
|---|---|
| `LIVEKIT_URL` / `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` | チームの LiveKit Cloud の値 |
| `OPENAI_API_KEY` | チームの OpenAI のキー（植物の声に使う） |
| `HF_TOKEN` | （任意）2 で作った Hugging Face のトークン |

`LLM_BASE_URL`・`LLM_MODEL`・`STT_BASE_URL`・`STT_MODEL` は書かなくて大丈夫です。Docker では `compose.yaml` の設定（コンテナ内の LLM・STT）が使われます。開発用に OpenAI を向けた値が書いてあっても、Docker では無視されます。

## 4. 起動する

```bash
docker compose up -d
```

**初回はモデルのダウンロードで 10〜30 分ほどかかり、その間このコマンドは終わらずに待ち続けます（固まったように見えます）。** `llm` の準備ができて `stt-init` が終わると、最後に `agent` を起動してから終わります。

- **途中で Ctrl+C を押したり、ターミナルを閉じたりしないでください。** `agent` が起動しないままになります。もし止めてしまったら、もう一度 `docker compose up -d` を実行すれば続きから進みます
- 最後に `dependency failed to start` や `unhealthy` と出て終わった場合も、まず `docker compose up -d` をもう一度実行してください（ダウンロードが 60 分を超えたときなど）。それでも同じなら 7 を見てください

進み具合は、**別のターミナルを開いて**ログで見られます。

```bash
docker compose ps                # 各サービスの状態
docker compose logs -f llm       # LLM のダウンロード・起動のログ（Ctrl+C で抜ける。止まりはしない）
docker compose logs stt-init     # STT のモデルのダウンロード・読み込みの結果
docker compose logs -f agent     # エージェントのログ
```

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
- **PC を再起動したとき**は、コンテナは自動で起動し直します（`restart: unless-stopped`）。ただし、このときは起動の順番が守られず、`agent` が `llm` より先に動き出します。`docker compose ps` で `llm` が `healthy` になるまで（モデルはダウンロード済みなので 1〜2 分）待ってから話しかけてください。それまでに話しかけると、植物は「ん…ちょっとぼーっとしてた」と返します（#40）
- DB を読み書きするコマンド（`scripts/` の下のもの）は、コンテナの中で `docker compose exec agent python -m scripts.<名前>` として動かします。Linux では DB のファイルが `root` の持ち物になるので、PC 側から直接書き換えないでください

## 7. 困ったとき

| 症状 | 原因と対処 |
|---|---|
| `llm` のログに `401` / `403` | `.env` の `HF_TOKEN` が間違っているか期限切れ。正しいトークンに直すか、空にする（このモデルはトークン無しでも取れる） |
| `llm` のログに `CUDA driver version is insufficient` | ドライバが古い（CUDA 13.0 に未対応）。1 の確認をして、ドライバを 580 以上に更新する |
| `llm` のログに `less than desired GPU memory utilization` | ほかのプログラム（ゲーム・ブラウザ・`stt` など）が GPU メモリを使っている。ほかのアプリを閉じ、だめなら `compose.yaml` の `--gpu-memory-utilization` を `0.75` に**下げて** `docker compose up -d` |
| `llm` のログに `No available memory for the cache blocks` か `larger than the available KV cache memory` | LLM の作業用メモリが足りない。`compose.yaml` の `--max-model-len` を `8192`（だめなら `4096`）に**下げる**。ほかのアプリを閉じられるなら `--gpu-memory-utilization` を `0.85` に**上げる**のも効く |
| GPU が 24GB 未満 | この構成では動きません（1 を参照）。チームで 24GB 以上の GPU がある PC を探してください |
| `could not select device driver "nvidia"` | Docker から GPU が見えていない。1 の `nvidia-smi` の確認からやり直す（Windows は Docker Desktop の WSL2 設定、Linux は NVIDIA Container Toolkit） |
| `stt-init` が `exited (1)` で終わり、`agent` が起動しない | STT のモデルを用意・読み込みできなかった。`docker compose logs stt-init` と `docker compose logs stt` を確認し、`.env` に `DOCKER_STT_MODEL=Systran/faster-whisper-large-v3` を書いて `docker compose up -d` |
| `agent` は `running` なのに、植物が返事をしない | `docker compose logs agent` に `STT` や `LLM` のエラーが無いか、`docker compose logs stt`・`docker compose logs llm` にエラーが無いかを見る。ほかのメンバーが同じ LiveKit のキーで `uv run python -m agent.main dev` を動かしていると、そちらに植物を取られることがあるので止めてもらう |
| `agent` がすぐ止まる | `docker compose logs agent` を見る。`環境変数 … が設定されていません` なら `.env` の書き漏れ |

解決しないときは、`docker compose logs` の該当部分を Discord に貼ってください（**キーが含まれていないか確認してから**）。

## できないこと

- **console モード（PC のマイクで試す）は Docker では使えません。** コンテナからは PC のマイク・スピーカーに触れないためです。console は今までどおり `uv run python -m agent.main console` で使います（README）
- GPU の無い PC・Mac では、この構成は動きません
