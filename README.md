# team4 — ふたまる

育て方によって個性が変わる、話せる観葉植物「ふたまる」のサーバー側音声AIエージェントです。
設計は [`docs/superpowers/specs/2026-11-13-futamaru-agent-design.md`](docs/superpowers/specs/2026-11-13-futamaru-agent-design.md) を参照してください。
チーム開発のルール（ブランチ・レビュー・秘密情報など）は [`CONTRIBUTING.md`](CONTRIBUTING.md) にまとめています。

## セットアップ

```bash
uv sync
cp .env.example .env   # LiveKit・LLM・STT・OpenAI の値を埋める
```

`LLM_MODEL` は `google/gemma-4-E4B-it` です（仕様 §8.1）。LLM・STT は誰かの PC で動かします。準備ができるまでは、`.env.example` のコメントにあるとおり OpenAI に向けて試せます。

## 起動

```bash
# PC のマイク・スピーカーで試す（ESP32 不要）。.env に DEV_PLANT_ID=dev を入れておく
uv run python -m agent.main console

# LiveKit Cloud に接続して ESP32 からの接続を待つ
uv run python -m agent.main dev     # 開発用（ホットリロード）
uv run python -m agent.main start   # 本番
```

NVIDIA の GPU がある PC では、エージェント・LLM・STT をまとめて Docker で動かせます（`docker compose up -d`）。手順は [docs/deploy.md](docs/deploy.md) を見てください。

「ふたまる」と呼ぶと起き、60秒黙ると眠ります。眠るたびに会話から記憶と性格が更新されます（`data/futamaru.db`）。

## ESP32 用トークンの発行

```bash
uv run python -m scripts.issue_token --plant-id a1
```

identity `device-a1`、ルーム `plant-a1`、有効期限 30 日のトークンを1行で出力します。ファームウェアの設定に書き込んでください。

## 成長の早送りデモ

```bash
uv run python -m scripts.simulate scripts/samples/plant-a.json scripts/samples/plant-b.json --ask "ただいま"
```

同じ初期状態の2株に別々の会話ログを流し込み、性格値の推移・最終的なシステムプロンプト・返答の違いを表示します（LLM サーバーが必要）。

## テスト

```bash
uv run pytest
```
