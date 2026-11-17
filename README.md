# team4 — ふたまる

**育て方によって個性が変わる、話せる観葉植物「ふたまる」**

鉢植えに小さなマイクとスピーカーを付け、植物と声で話せるようにします。話しかけ方によって、植物の性格（おしゃべり・甘えん坊・元気・好奇心旺盛）と覚えていることが少しずつ変わります。同じように作った2株でも、育てる人が違えば別の個性に育ちます。

このリポジトリは、植物の「脳」にあたるサーバー側の音声 AI エージェントです。

> **メンターの方へ:** 下の「[FB をいただきたいこと](#fb-をいただきたいこと)」に、チームで迷っている点をまとめています。どれか1つでもご意見をいただけると助かります。

## 体験の流れ

1. 鉢に向かって「ふたまる」と呼ぶと、植物が起きます。前に話したことを覚えていて、植物のほうから話しかけてきます（例:「あ、おかえり！きのう言ってたテスト、どうだった？」）
2. そのまま普通に会話します。植物が話している途中でも割り込めます
3. 最初に「たろうだよ」のように名乗ると、相手を見分けます。性格・記憶・方言は人ごとに持ち、ある人の話をほかの人に話すことはありません
4. 60秒黙ると眠ります。眠るたびに、その会話から記憶と性格が更新されます
5. 話しかけ方を続けると、性格が変わっていきます。たくさん褒めると甘えん坊に、たくさん話すとおしゃべりに、といった具合です
6. 関西弁・博多弁・広島弁で話しかけていると、植物もその方言で話すようになります

## 仕組み

```
[鉢: ESP32-S3 ＋ マイク・スピーカー] ──WebRTC──> [LiveKit Cloud] <── [チームの PC: エージェント]
                                                                      ├─ 文字起こし: kotoba-whisper
                                                                      ├─ 会話: Gemma 4 E4B
                                                                      ├─ 声: OpenAI TTS（クラウド）
                                                                      └─ 性格・記憶: SQLite
```

- **鉢の中はマイコンとマイク・スピーカーだけ**にしています。考える処理はすべて PC 側で行います。鉢の中で LLM を動かす案（Jetson）も検討しましたが、値段と消費電力が見合わないため見送りました（[記録](docs/hardware/jetson-decision.md)）
- **性格は4つの数値（0〜1）で持ちます。** 会話のたびに LLM が会話を振り返り、変化量を決めます。数値を言葉の指示に変えてプロンプトに入れることで、同じ LLM でも株ごとに違う話し方になります
- **記憶は大事なものから10件と、前回の会話の要約だけ**をプロンプトに入れます。小さなモデルでも崩れにくく、返事も遅くなりにくい作りです

| 部分 | 使っているもの |
|---|---|
| 音声エージェント | LiveKit Agents（Python） |
| 会話の LLM | Gemma 4 E4B（`google/gemma-4-E4B-it`）。チームの PC で動かす |
| 文字起こし | kotoba-whisper v2.0（日本語向けの Whisper） |
| 声 | OpenAI `gpt-4o-mini-tts` |
| 鉢のハード | Seeed XIAO ESP32S3 ＋ M5Stack Atomic Voice Base（[部品の選び方](docs/hardware/parts-selection.md)） |

詳しい設計は [仕様書](docs/superpowers/specs/2026-11-13-futamaru-agent-design.md) にあります。

## 今どこまでできているか（2026-11-17）

| 状態 | 内容 |
|---|---|
| ✅ できた | 呼びかけで起きる・眠る、会話の記録、振り返りによる記憶と性格の更新 |
| ✅ できた | 人の見分け（名乗り）、人ごとの性格・記憶、方言（4種類）の判定と反映 |
| ✅ できた | 成長の早送りデモ（2株に別々の会話ログを流して、性格の違いを出す） |
| ✅ できた | GPU のある PC でまとめて動かす Docker 構成 |
| ✅ できた | 性格を5段階にして、変化が話し方に出やすくした（#57） |
| 🔧 作業中 | PC で Gemma 4 と kotoba-whisper を動かす（#1・#2）、ESP32 の実機との通し確認（#3） |
| 🔧 作業中 | 鉢の筐体の設計（#16） |
| 🔧 作業中 | 返事の速さ。今は話し終えてから約4〜5秒かかっていて、1秒以内が目標（#39） |
| 📝 これから | 性格・成長曲線を見せる Web 画面（#59）、土の水分センサーで「のど渇いた」と言わせる（#58） |
| 📝 これから | 声を明るいキャラクターの声にする（#56） |

## FB をいただきたいこと

1. **「育っている」と感じてもらえるか。** 性格は少しずつ変わるようにしているので、変化に気づきにくいおそれがあります。発表の短い時間で「育てた結果」を伝えるには、どう見せるのがよいでしょうか（今は、2株の性格をグラフで並べる画面を作る予定です）
2. **植物らしさ。** 今は「育て方」が会話だけです。土の水分センサーで水やりを反映する予定ですが、ほかに「ただの AI スピーカーではない」と感じてもらえる工夫はあるでしょうか
3. **返事の速さ。** 今は約4〜5秒かかっています。どこまで速ければ会話として気にならないか、遅いときの間のつなぎ方（「んー…」と先に言うなど）について、ご意見をいただきたいです
4. **人の見分け方。** 今は毎回名乗ってもらう方式です。体験として不自然でないか、声で見分ける必要があるか迷っています
5. **誰に届けるか。** 一人暮らしの人、子ども、高齢の家族の見守りなど、どの人に向けると一番価値が伝わるでしょうか

ご意見は Issue でも、チームのチャンネルでも大丈夫です。

---

## 開発者向け

チーム開発のルール（ブランチ・レビュー・秘密情報など）は [`CONTRIBUTING.md`](CONTRIBUTING.md)、コードの書き方は [`AGENTS.md`](AGENTS.md) にあります。

### セットアップ

```bash
uv sync
cp .env.example .env   # LiveKit・LLM・STT・OpenAI の値を埋める
```

`LLM_MODEL` は `google/gemma-4-E4B-it` です（仕様 §8.1）。LLM・STT は誰かの PC で動かします。準備ができるまでは、`.env.example` のコメントにあるとおり OpenAI に向けて試せます。

### 起動

```bash
# PC のマイク・スピーカーで試す（ESP32 不要）。.env に DEV_PLANT_ID=dev を入れておく
uv run python -m agent.main console

# LiveKit Cloud に接続して ESP32 からの接続を待つ
uv run python -m agent.main dev     # 開発用（ホットリロード）
uv run python -m agent.main start   # 本番
```

NVIDIA の GPU がある PC では、エージェント・LLM・STT をまとめて Docker で動かせます（`docker compose up -d`）。手順は [docs/deploy.md](docs/deploy.md) を見てください。

会話・記憶・性格は `data/futamaru.db` に保存されます。

### ESP32 用トークンの発行

```bash
uv run python -m scripts.issue_token --plant-id a1
```

identity `device-a1`、ルーム `plant-a1`、有効期限 30 日のトークンを1行で出力します。

- **ESP32 の担当者に渡し、ファームウェアの設定に書き込んでもらいます。** 一緒に渡すのはサーバーの URL（`.env` の `LIVEKIT_URL`）だけです。`LIVEKIT_API_KEY` と `LIVEKIT_API_SECRET` は渡しません。
- **株ごとに別のトークンを使います。** 2台目には `--plant-id b1` などで発行してください。同じトークンを2台に入れると、同じ株として扱われます。
- **期限が切れたら、同じコマンドで発行し直して書き込み直します。**
- **トークンは秘密情報です。** 持っている人は誰でもその株のルームに入れて、マイクの音を聞けます。Issue・PR・チャンネルには貼らず、DM で渡してください。

### 成長の早送りデモ

```bash
uv run python -m scripts.simulate scripts/samples/plant-a.json scripts/samples/plant-b.json --ask "ただいま"
```

同じ初期状態の2株に別々の会話ログを流し込み、性格値の推移・最終的なシステムプロンプト・返答の違いを表示します（LLM サーバーが必要）。

結果は本番の DB とは別の `data/sim.db` に、株 `sample-a` / `sample-b` として保存します。保存先は `--db` で変えられます。このデモは人を見分けず、株の性格だけで動きます。

### 人の一覧と方言の固定（デモ用）

```bash
uv run python -m scripts.people --plant-id a1                                  # 知っている人の一覧
uv run python -m scripts.people --plant-id a1 --person たろう --dialect kansai   # 方言を固定（未登録なら登録）
uv run python -m scripts.people --plant-id a1 --person タロウ --auto            # 固定を外して自動判定に戻す
```

名前はひらがな・カタカナの違いを無視して照合します。`--dialect` は `standard` / `kansai` / `hakata` / `hiroshima`。発表の前に人を登録し方言を固定しておくと、確実に見せられます。Docker では `docker compose exec agent python -m scripts.people ...` です。

### LLM・STT の確認

モデルを動かす PC の準備ができたら、エージェントから使える状態かを確かめます。結果はそのまま Issue に貼れる形で表示されます。

```bash
# LLM（#1）: 会話・表情タグ・振り返りの JSON・起こされたときの挨拶・初回トークンまでの時間
LLM_BASE_URL=http://<pc>:8000/v1 LLM_MODEL=google/gemma-4-E4B-it uv run python -m scripts.check_llm

# STT（#2）: 無音・雑音で変な文字が出ないか。録音したファイルを渡すと、ウェイクワードを聞き取れるかも確かめる
uv run python -m scripts.check_stt
uv run python -m scripts.check_stt futamaru1.wav futamaru2.m4a
```

`check_llm` は `.env` を読まないので、上のように環境変数か `--base-url` と `--model` で指定します。`check_stt` は `.env` の `STT_BASE_URL`・`STT_MODEL` を使います。

### テスト

```bash
uv run pytest
```

PR を出す前に `uvx ruff format . && uvx ruff check . && uv run pytest` を通してください（CI と同じです）。
