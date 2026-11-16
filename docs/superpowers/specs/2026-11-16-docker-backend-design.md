# バックエンドの Docker 化 設計

2026-11-16 ／ ケーノ・Claude Code

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
