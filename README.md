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

## 声（TTS）をローカルの AivisSpeech にする

ふたまるの声は、GPU の PC で動かす [AivisSpeech Engine](https://github.com/Aivis-Project/AivisSpeech-Engine) で作れます（仕様 §8.3）。OpenAI の API キーも料金も要りません。

1. エージェントを動かす PC で AivisSpeech Engine を起動する（既定では `http://127.0.0.1:10101` で待ち受けます）
2. 入っている声の一覧を出し、気になる声のサンプルを作って聞き比べる

```bash
uv run python -m scripts.aivis_voices --list                          # 声とスタイル ID の一覧
uv run python -m scripts.aivis_voices --speaker <ID> <ID> --pitch 0.05  # data/voice-samples/<ID>.wav ができる
```

3. `.env` に書く

```bash
TTS_PROVIDER=aivis
AIVIS_BASE_URL=http://127.0.0.1:10101   # AivisSpeech の URL（空ならこの値）
```

`AIVIS_SPEAKER` を書かなければ、聞き比べて決めた「まお」の「あまあま」（`888753762`）を使います。AivisSpeech には「話し方の指示」が無いので、方言は返事の文面（語尾など）だけで出ます。OpenAI に戻すときは `TTS_PROVIDER=openai` にします。

使っている外部の技術・素材:
- [AivisSpeech Engine](https://github.com/Aivis-Project/AivisSpeech-Engine)（音声合成エンジン、LGPL v3）
- 音声合成モデル「まお」（制作: オズチャット / Trippy / ねゆたろ、Aivis Common Model License (ACML) 1.0）

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

## ダッシュボード（性格・成長・記憶を見る）

```bash
uv run python -m scripts.dashboard --plants a1,b1                                    # 本番の DB（data/futamaru.db）
uv run python -m scripts.dashboard --db data/sim.db --plants sample-a,sample-b       # 早送りデモの結果
```

ブラウザで http://127.0.0.1:8765 を開くと、2株の性格のレーダーチャート・成長曲線・覚えていることが並びます。3 秒ごとに読み直すので、会話して眠らせると数秒後にグラフが動きます。DB は読むだけなので、エージェントと同時に動かして大丈夫です。会話の記憶が見えるため、この PC からしか開けません（仕様 §6.6）。グラフの描画には [Chart.js](https://www.chartjs.org/)（MIT ライセンス）を CDN から読み込んでいます。

## 人の一覧と方言の固定（デモ用）

```bash
uv run python -m scripts.people --plant-id a1                                  # 知っている人の一覧
uv run python -m scripts.people --plant-id a1 --person たろう --dialect kansai   # 方言を固定（未登録なら登録）
uv run python -m scripts.people --plant-id a1 --person タロウ --auto            # 固定を外して自動判定に戻す
```

名前はひらがな・カタカナの違いを無視して照合します。`--dialect` は `standard` / `kansai` / `hakata` / `hiroshima`。発表の前に人を登録し方言を固定しておくと、確実に見せられます。Docker では `docker compose exec agent python -m scripts.people ...` です。

## テスト

```bash
uv run pytest
```
