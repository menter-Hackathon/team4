# バックエンドの Docker 化 設計

2026-11-16 ／ ケーノ・Claude Code

更新 2026-11-18: 声（TTS）に AivisSpeech を使う場合に備えて、AivisSpeech Engine もコンテナに入れた。ダッシュボードもコンテナに入れた。LLM を vLLM から Ollama の圧縮版に替え、GPU を 2 枚に分けた（下の「2026-11-18 の追加」）

## 目的と理由

ふたまるの「脳」（エージェント・LLM・STT）を Docker にまとめ、**チームの誰かが NVIDIA の GPU がある PC で `docker compose up` するだけで、脳全体が立ち上がる**ようにする。

- オンライン開催で共有の GPU サーバーは無く、脳は誰かの PC で動かす（#23）。デプロイするのは、コードを書いた人とは別のメンバー
- 鉢（筐体）にはマイコン・マイク・スピーカーだけを置く。これは今の構成のままで、Docker 化で変わらない

## やること・やらないこと

- **やること**: エージェント・LLM（Gemma 4 E4B）・STT（kotoba-whisper）の 3 つを、1 つの compose ファイルで起動できるようにする。デプロイ手順書を書く
- **やらないこと**
  - LiveKit サーバーと TTS をコンテナに入れること。LiveKit は Cloud、TTS は OpenAI のまま（外部サービス）
  - GPU の無い PC・Mac 向けの構成
  - console モード（PC のマイクで試す）を Docker で動かすこと。コンテナからは PC のマイクに触れないため。console は今までどおり `uv run` で使う
- **失敗とみなすこと**: デプロイする人が手順書だけでは起動できない。秘密情報（キー・トークン）がイメージやリポジトリに入る

## 制約

- デプロイ先は NVIDIA の GPU がある Windows か Linux の PC（x86_64）。Windows は Docker Desktop＋WSL2、Linux は NVIDIA Container Toolkit が必要
- GPU メモリ: vLLM のレシピでは E4B は 24GB 以上の GPU 1 枚が目安。STT も同じ GPU に載せるので、**24GB 以上を前提**にする。足りない場合は LLM の使用率と最大文脈長を下げて調整する
- Gemma 4 のダウンロードには Hugging Face のトークン（`HF_TOKEN`）が要る。デプロイする人が用意して `.env` に書く
- 開発用の Mac には Docker が無いので、手元では起動を確かめられない。エージェントのイメージが作れることは GitHub Actions で確かめ、GPU を使った起動はデプロイする人の PC で初めて確かめる

## 作り方の候補（選んだのは 1）

1. **役割ごとに 3 つのコンテナに分け、docker compose でまとめて起動する（採用）**
   自分で作るのはエージェントの Dockerfile と compose ファイルだけで、LLM と STT は公式イメージをそのまま使う。どこが落ちたか分けて調べられる
2. 全部を 1 つのコンテナに詰める: イメージが巨大で、1 か所の不具合で全部止まる。見送り
3. エージェントだけ Docker にし、LLM・STT は PC に直接入れる: 「全部入れたい」に合わないので見送り

## 構成

```
docker compose up
 ├─ agent … このリポジトリから作るイメージ。`python -m agent.main start` で起動
 │          LLM_BASE_URL=http://llm:8000/v1、STT_BASE_URL=http://stt:8000/v1 を compose 側で設定
 ├─ llm   … vllm/vllm-openai（公式）。google/gemma-4-E4B-it を serve。GPU を使う
 └─ stt   … ghcr.io/speaches-ai/speaches（公式、CUDA 版）。kotoba-whisper を読み込む。GPU を使う
外部: LiveKit Cloud（ESP32 とつなぐ）、OpenAI（TTS の声）
```

- **ファイル**: `Dockerfile`（エージェント）、`compose.yaml`、`.dockerignore`、`docs/deploy.md`（デプロイ手順書）。CI にエージェントのイメージを作るジョブを足す
- **設定**: キー類（LiveKit・OpenAI・`HF_TOKEN`）は `.env` から読み込む。LLM・STT の URL とモデル名は compose ファイルに書き、`.env` の値より優先する（デプロイする人が URL を書き換えなくて済むように）
- **データの保存先**
  - 会話ログ・記憶の DB（`data/`）はホストのフォルダに残し、コンテナを作り直しても植物の記憶が消えないようにする
  - モデル（十数 GB）は名前付きボリュームに保存し、2 回目からはダウンロードしない
- **起動順**: LLM・STT が応答できるようになってからエージェントを起動する（ヘルスチェック）
- **GPU メモリの分け方**: vLLM の `--gpu-memory-utilization` を 0.8 前後にして、STT の分を残す。最大文脈長は 16384（レシピの値）から始める
- **STT の常駐**: Speaches は使わないモデルを 300 秒で外す。外れると次の発話が遅くなるので、外さない設定（`STT_MODEL_TTL=-1`）にする

## 作る人に任せること

- 各イメージのバージョン（タグ）の固定。vLLM は Gemma 4 対応の版（0.19 以上）にする
- kotoba-whisper を Speaches に読み込ませる方法（起動時の事前ダウンロードか、API での読み込みか）。公式ドキュメントで確かめて決める
- ヘルスチェックの具体的な URL と待ち時間
- エージェントのイメージで、VAD のモデルをビルド時に取り込むかどうか（起動を速くするため）

## 2026-11-18 の追加: 声（AivisSpeech）もコンテナに入れる

最初は「TTS は OpenAI のまま（外部サービス）」としていたが、次の理由で AivisSpeech Engine もコンテナに入れる。

- デモの PC がショーンさんの PC（NVIDIA の GPU、24GB 以上）に決まり、脳を全部その PC で動かすことになった（#37）
- 植物の声を、日本語が自然でキャラクターらしい AivisSpeech にする案がある（#56）。会場のネットに左右されない利点もある
- 2026-11-18 に Mac で測ると、短い文なら CPU でも約 0.7 秒で声になり、OpenAI（0.7〜1.5 秒）と変わらなかった

決めたこと:

- **`tts`**: AivisSpeech Engine 公式イメージの CPU 版（`cpu-1.2.0`）。GPU のメモリは LLM（8 割）と STT で使うので、声は CPU で作る
- **`tts-init`**: `stt-init` と同じ考え方で、起動時に一度だけ動く。`.env` の `AIVIS_MODELS` の声のモデルが入っていなければ AivisHub から入れ、`AIVIS_SPEAKER` の声で一度読み上げて読み込ませる。`TTS_PROVIDER` が `aivis` でなければ何もしない（OpenAI の声のときに、AivisHub につながらないせいで agent が止まらないように）
- 声のモデルは名前付きボリューム（`aivis-data`）に残す。公式イメージは `user` で動くが、ボリュームは root の持ち物で作られるため、起動前に持ち主を変える
- どちらの声を使うかは、今までどおり `.env` の `TTS_PROVIDER` で切り替える。会場で AivisSpeech が動かなければ、`TTS_PROVIDER=openai` に戻すだけで OpenAI の声で動く
- まだ確かめていないこと: GPU の PC で `tts` を `llm`・`stt` と一緒に動かしたときの速さ（CPU を取り合うか）。ショーンさんの PC で初めて確かめる

### ダッシュボードもコンテナに入れる

- 発表では、脳と同じショーンさんの PC で性格のダッシュボード（`scripts/dashboard.py`）を映す。`uv` を入れずに済むよう、エージェントと同じイメージで `dashboard` サービスとして動かす
- ダッシュボードは会話の記憶が見えるので、もともと 127.0.0.1 でしか待ち受けない（仕様 §6.6）。コンテナの中の 127.0.0.1 には PC のブラウザから届かないので、コンテナの中だけ `--host 0.0.0.0` にし、PC 側の公開を `127.0.0.1:8765` に絞る（ほかの PC からは開けないまま）
- 発表用に育てた株（1・5・30 日目の DB）は、`scripts/use_db.py` で `data/futamaru.db` に入れ替える。ファイルを上書きすると、SQLite の `-wal` が残って DB が壊れることがあるため、今の DB を `data/backup/` に残してから、SQLite の backup 機能で書き出す
- 当日の手順は `docs/demo-day.md` に 1 枚でまとめる（動かすのはショーンさん）

### LLM を Ollama の圧縮版にし、GPU を 2 枚に分ける

- デモの PC（ショーンさんの PC）の GPU は RTX 5070（12GB）と RTX 5060（8GB）で、24GB 以上の 1 枚は無い。上の「制約」の 24GB 前提は満たせない
- vLLM で圧縮していない Gemma 4 E4B（重み約 16GB）は 12GB に載らないので、**LLM を Ollama の圧縮版（`gemma4:e4b-it-q4_K_M`、約 6.6GB）に替える**。2026-11-18 に Mac の Ollama で、見分け・振り返り・天気・水やりが動くことを確かめた構成と同じ種類
- **LLM と STT に GPU を 1 枚ずつ割り当てる**（`.env` の `LLM_GPU`・`STT_GPU`、既定は 0 と 1）。メモリを取り合わないようにするため。LLM はメモリの大きい RTX 5070 にする
- Ollama の設定: モデルを外さない（`OLLAMA_KEEP_ALIVE=-1`）、同時に 2 つまで（`OLLAMA_NUM_PARALLEL=2`、振り返りと次の返事が重なっても待たせない）、文の長さ 8192（既定の 4096 だとプロンプトの頭が切れることがある）
- モデルの用意は `llm-init`（`scripts/llm_init.py`）が行う。`stt-init` と同じく、ダウンロード→GPU への読み込み→どれだけ GPU に載ったかをログに出す。全部載らなくても止めない（遅いが動く）
- Hugging Face のトークン（`HF_TOKEN`）は不要になった（Ollama のモデルは Ollama のサイトから取る）
- 思考モードは compose で `LLM_REASONING_EFFORT=none` にして切る（Ollama の Gemma 4 は既定で思考し、遅くなるため。#54）
- まだ確かめていないこと: NVIDIA の GPU で、この圧縮版が 12GB に全部載るか、話し方の質が Mac の版（MLX）と変わらないか。ショーンさんの PC でのリハーサルで確かめる


## 2026-11-19 の追加: 声（AivisSpeech）を GPU でも動かせるようにする

返事の遅さ（#39）を測ると、CPU 版の AivisSpeech が 1 文の声を作るのに 0.7〜1.7 秒かかり、いちばん大きな原因だった（ohsawa の Core Ultra 7）。GPU 版（`aivisspeech-engine:nvidia-1.2.0`）は同じ文が 0.16〜0.26 秒だった。

- **`compose.tts-gpu.yaml`**: `tts` を GPU 版にする上書きのファイル。`.env` の `COMPOSE_FILE=compose.yaml:compose.tts-gpu.yaml` で使う。`compose.yaml` は CPU 版のまま（GPU に空きの無い PC でも、今までどおり動くように）
- **GPU のメモリ**（RTX 5060 8GB、2026-11-19）: LLM 約 4.75GB、STT 約 2.35GB、声（GPU 版）約 1.4GB、その他約 0.4GB で、合わせると 8GB を超える
- **`DOCKER_STT_COMPUTE_TYPE`**: Speaches の `WHISPER__COMPUTE_TYPE`。`int8_float16` にすると STT が約 1GB 減り（7.5GB → 6.5GB）、聞き取り（「二丸、こんにちは」）と速さ（0.34 秒）は変わらなかった。これで 3 つ合わせて 7.6〜7.8GB に収まった
- 結果: 話し終わりから植物が喋り始めるまで、約 3.3〜3.5 秒 → 約 2.1〜2.3 秒（Cloudflare Tunnel 越し。1 文ずつ声にする #110 と、話し終わりの判定の短縮を含む）
- 残り 0.35GB ほどしか空きが無いので、LLM の文脈の長さ（8192）や同時に扱う数（2）を増やすときは、メモリが足りるか確かめる
