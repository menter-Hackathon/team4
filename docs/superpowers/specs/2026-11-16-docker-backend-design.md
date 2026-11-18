# バックエンドの Docker 化 設計

2026-11-16 ／ ケーノ・Claude Code

更新 2026-11-18: 声（TTS）に AivisSpeech を使う場合に備えて、AivisSpeech Engine もコンテナに入れた。ダッシュボードもコンテナに入れた（下の「2026-11-18 の追加」）

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

