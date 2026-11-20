# team4 — ふたまる

**育て方によって個性が変わる、話せる観葉植物「ふたまる」**

鉢植えに小さなマイクとスピーカーを付け、植物と声で話せるようにします。話しかけ方によって、植物の性格（おしゃべり・甘えん坊・元気・好奇心旺盛）と覚えていることが少しずつ変わります。同じように作った2株でも、育てる人が違えば別の個性に育ちます。

このリポジトリは、植物の「脳」にあたるサーバー側の音声 AI エージェントです。

> **メンターの方へ:** 下の「[FB をいただきたいこと](#fb-をいただきたいこと)」に、チームで迷っている点をまとめています。どれか1つでもご意見をいただけると助かります。

## なぜ植物に話しかけるのか

### アレクサや、植物のない AI ではだめなのか

| | AI スピーカー（アレクサなど） | ふたまる |
|---|---|---|
| 関係 | 人が頼み、AI が役に立つ | 人が世話をし、植物が育つ |
| 話しかける理由 | 用事があるとき | 植物が気になるとき（寒くないか、乾いていないか、水は足りているか） |
| AI が話す内容 | 何でも話せる（植物でなくても成り立つ） | **鉢の中の本物の植物の状態**だけを元に話す |
| 放っておくと | 何も起きない | 本物の植物が弱り、ふたまるもすねる |

ふたまるの AI は、**本物の植物の通訳**です。植物は「寒い」「乾いている」「のどが渇いた」を自分で言えません。ふたまるは、天気（気温・湿度）、水をもらってからの日数、育った日数、その植物の種類の育て方をもとに、植物の代わりに言葉にします。AI が話す理由はいつも、目の前の植物にあります。この結びつきが、植物を抜いた AI との違いです。

育てる人は、植物を気にかけて話しかけます。「今日は寒いけど大丈夫？」「お水あげたよ」「大きくなったね」と声をかけます。話しかけてもらうと喜び、放っておかれるとすねます。世話のしかたと話しかけ方で、性格が育っていきます。顔の画面を付けないのも、植物らしさを残すためです。

## 体験の流れ

1. 鉢に向かって「ふたまる」と呼ぶと、植物が起きます。植物のほうから、自分の様子を話しかけてきます（例:「今日すごく乾燥してるね…葉っぱがパリパリしそう」「さむい！窓のそば、ちょっと冷えるかも」「3日ぶりだね、さみしかった」）。前に話したことも覚えています
2. 「お水あげたよ」と言うと、植物が水やりを覚えます。しばらく水をもらえないと、植物から「のど渇いた」と言います（土の水分センサーの代わりに、声で記録します）
3. そのまま普通に会話します。植物が話している途中でも割り込めます
4. 最初に「たろうだよ」のように名乗ると、相手を見分けます。性格・記憶・方言は人ごとに持ち、ある人の話をほかの人に話すことはありません
5. 60秒黙ると眠ります。眠るたびに、その会話から記憶と性格が更新されます
6. 世話と話しかけを続けると、性格が変わっていきます。こまめに水と声をかけると元気に、たくさん褒めると甘えん坊に、間があくとすねやすく、といった具合です
7. 関西弁・博多弁・広島弁で話しかけていると、植物もその方言で話すようになります

## 仕組み

```
[鉢: ESP32-S3 ＋ マイク・スピーカー] ──WebRTC──> [LiveKit Cloud] <── [チームの PC: エージェント]
                                                                      ├─ 文字起こし: kotoba-whisper
                                                                      ├─ 会話: Gemma 4 E4B
                                                                      ├─ 声: AivisSpeech（ローカル）
                                                                      └─ 性格・記憶: SQLite
```

- **鉢の中はマイコンとマイク・スピーカーだけ**にしています。考える処理はすべて PC 側で行います。鉢の中で LLM を動かす案（Jetson）も検討しましたが、値段と消費電力が見合わないため見送りました（[記録](docs/hardware/jetson-decision.md)）
- **性格は4つの数値（0〜1）で持ちます。** 会話のたびに LLM が会話を振り返り、変化量を決めます。数値を言葉の指示に変えてプロンプトに入れることで、同じ LLM でも株ごとに違う話し方になります
- **記憶は大事なものから10件と、前回の会話の要約だけ**をプロンプトに入れます。小さなモデルでも崩れにくく、返事も遅くなりにくい作りです

| 部分 | 使っているもの |
|---|---|
| 音声エージェント | LiveKit Agents（Python） |
| 会話の LLM | Gemma 4 E4B の圧縮版（Ollama の `gemma4:e4b-it-q4_K_M`）。チームの PC で動かす |
| 文字起こし | kotoba-whisper v2.0（日本語向けの Whisper） |
| 声 | AivisSpeech Engine（声のモデル「らせつん」）。`.env` で OpenAI `gpt-4o-mini-tts` にも切り替えられる |
| 鉢のハード | Seeed XIAO ESP32S3 ＋ M5Stack Atomic Voice Base（[部品の選び方](docs/hardware/parts-selection.md)） |

詳しい設計は [仕様書](docs/superpowers/specs/2026-11-13-futamaru-agent-design.md) にあります。

## 今どこまでできているか（2026-11-19）

| 状態 | 内容 |
|---|---|
| ✅ できた | 呼びかけで起きる・眠る、会話の記録、振り返りによる記憶と性格の更新 |
| ✅ できた | 人の見分け（名乗り）、人ごとの性格・記憶、方言（4種類）の判定と反映 |
| ✅ できた | 植物の状態を話す: 天気（気温・湿度）、声での水やり記録、育った日数・前回からの日数、世話のしかたを性格に反映、植物の種類の育て方（仕様 §6.7、#80〜#83） |
| ✅ できた | 性格を5段階にして、変化が話し方に出やすくした（#57） |
| ✅ できた | 2株の性格・成長曲線・記憶を並べて見せる Web 画面（ダッシュボード、#59） |
| ✅ できた | 成長の早送りデモと、発表用の「1日目・5日目・30日目」の株（#77） |
| ✅ できた | 声をローカルの AivisSpeech で作る（明るい女の子の声「らせつん」、#56・#106） |
| ✅ できた | Gemma 4 を PC で動かし、エージェントから使えることを確かめた（#1） |
| ✅ できた | GPU 2 枚（12GB と 8GB）の PC で、LLM・文字起こし・声・ダッシュボードをまとめて動かす Docker 構成（#37。本番の PC で動かすのはリハーサルで確かめる） |
| 🔧 作業中 | kotoba-whisper の確認（#2）。録音で「ふたまる」を聞き取れることは確かめた。本番の PC での速さはこれから |
| 🔧 作業中 | 本番と同じ形での通しのリハーサル（ショーンさんの PC、#105）、ESP32 の実機との通し確認（#3・#94） |
| 🔧 作業中 | 返事の速さ。今は話し終えてから約4〜5秒かかっていて、3秒くらいが目標（植物なので少し間があってよい、#39） |
| 🔧 作業中 | 鉢の筐体の設計（#16） |
| ⏸ 見送り | 土の水分センサーで「のど渇いた」と言わせる（印刷済みの筐体を作り直す必要があるため、#58）。代わりに声で水やりを記録する（仕様 §6.7、#81） |

## FB をいただきたいこと

1. **「育っている」と感じてもらえるか。** 性格は少しずつ変わるようにしているので、変化に気づきにくいおそれがあります。発表の短い時間で「育てた結果」を伝えるには、どう見せるのがよいでしょうか（今は、2株の性格と成長曲線をグラフで並べる画面を用意しています）
2. **植物らしさ。** 今は「育て方」が会話だけです。土の水分センサーで水やりを反映する案は、筐体の都合で今回は見送りました。ほかに「ただの AI スピーカーではない」と感じてもらえる工夫はあるでしょうか
3. **返事の速さ。** 今は約4〜5秒かかっています。どこまで速ければ会話として気にならないか、遅いときの間のつなぎ方（「んー…」と先に言うなど）について、ご意見をいただきたいです
4. **人の見分け方。** 今は毎回名乗ってもらう方式です。体験として不自然でないか、声で見分ける必要があるか迷っています
5. **誰に届けるか。** 一人暮らしの人、子ども、高齢の家族の見守りなど、どの人に向けると一番価値が伝わるでしょうか

ご意見は Issue でも、チームのチャンネルでも大丈夫です。

---

## クレジット

### 使っている技術

| 名前 | 使い道 | ライセンス |
|---|---|---|
| [LiveKit Agents](https://github.com/livekit/agents) | 音声エージェントの土台 | Apache 2.0 |
| [Gemma 4](https://huggingface.co/google/gemma-4-E4B-it)（Google） | 会話と振り返りの LLM | Apache 2.0 |
| [Ollama](https://github.com/ollama/ollama) | Gemma 4 を PC で動かす | MIT |
| [kotoba-whisper v2.0](https://huggingface.co/kotoba-tech/kotoba-whisper-v2.0)（Kotoba Technologies） | 声を文字にする | Apache 2.0 |
| [Speaches](https://github.com/speaches-ai/speaches) | kotoba-whisper を API として動かす | MIT |
| [AivisSpeech Engine](https://github.com/Aivis-Project/AivisSpeech-Engine) | 返事を声にする | LGPL v3 |
| [Chart.js](https://www.chartjs.org/) | ダッシュボードのグラフ | MIT |

### 素材・データ

- **声**: 音声合成モデル「らせつん」（制作: こずかた学園プロジェクト / Aivis Project、声: 上戸恵里さん、Aivis Common Model License (ACML) 1.0）
- **天気**: 出典: 気象庁ホームページ（アメダスの観測値と天気予報の JSON）。植物が話す言葉にするため、値を加工して使っています
- **性格タイプのイラスト**（`scripts/dashboard_assets/types/`）: ChatGPT（OpenAI）で生成しました

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

### 声（TTS）をローカルの AivisSpeech にする

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

`AIVIS_SPEAKER` を書かなければ、聞き比べて決めた「らせつん」の「標準２」（`893625030`、#106）を使います。「らせつん」は AivisSpeech に最初から入っていないので、Docker を使わない PC では AivisSpeech の画面から AivisHub の「らせつん」を入れておきます（入っていないと植物が話せません）。AivisSpeech には「話し方の指示」が無いので、方言は返事の文面（語尾など）だけで出ます。OpenAI に戻すときは `TTS_PROVIDER=openai` にします。

使っている外部の技術・素材:
- [AivisSpeech Engine](https://github.com/Aivis-Project/AivisSpeech-Engine)（音声合成エンジン、LGPL v3）
- 音声合成モデル「らせつん」（制作: こずかた学園プロジェクト / Aivis Project、声: 上戸恵里さん、Aivis Common Model License (ACML) 1.0）

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

### 発表用に「1日目・5日目・30日目」の株を作る（#77）

ショーン（毎日話しかけて褒め、こまめに水をあげる）とちゃむ（たまにしか話しかけず、水も少なめ）の 30 日分の会話ログを、`scripts/samples/demo-sean.json`・`demo-chamu.json` に用意しています。`--day` で「今日が何日目か」を決めると、その日の前日までの会話を流し込んだ DB を作ります。

```bash
uv run python -m scripts.simulate scripts/samples/demo-sean.json scripts/samples/demo-chamu.json --db data/demo-day1.db  --day 1
uv run python -m scripts.simulate scripts/samples/demo-sean.json scripts/samples/demo-chamu.json --db data/demo-day5.db  --day 5
uv run python -m scripts.simulate scripts/samples/demo-sean.json scripts/samples/demo-chamu.json --db data/demo-day30.db --day 30
```

- 日付は「今日が N 日目」になるようにずらして入れます。撮影や発表の日に作ると、植物が「育ち始めて30日目」と言います（別の日に合わせるときは `--today 2026-11-22`）
- ログの `person` の人（ショーン・ちゃむ）を株に登録し、その人の性格として育てます（仕様 §6.5）。1 つの株で 2 人を育てるときは `--plant-id a1`、持ち主を 1 人に決めて株の性格として育てるときは `--no-person` を付けます
- LLM で振り返るので、30 日目は数分かかります。同じ DB に 2 回流すと会話が二重に入るので、作り直すときは DB を消してからにしてください

2026-11-18 に OpenAI（`gpt-4o-mini`）で作ったときのタイプ（3 回作って 3 回とも同じ）:

| | 1日目 | 5日目 | 30日目 |
|---|---|---|---|
| ショーン（a1） | 🌱 ふたば | 🍀 クローバー | 🌻 ひまわり |
| ちゃむ（b1） | 🌱 ふたば | 🌱 ふたば | 🌵 サボテン |

作ったら、ダッシュボード（下）で `--db data/demo-day30.db --plants a1,b1` を開いて、タイプと記憶が映してよい内容か確かめてください（#79）。

### ダッシュボード（性格・成長・記憶を見る）

```bash
uv run python -m scripts.dashboard --plants a1,b1                                    # 本番の DB（data/futamaru.db）
uv run python -m scripts.dashboard --db data/sim.db --plants sample-a,sample-b       # 早送りデモの結果
```

ブラウザで http://127.0.0.1:8765 を開くと、2株の性格のタイプ（「〇〇ふたまる」の16タイプ、#64）・レーダーチャート・成長曲線・覚えていることが並びます。タイプのイラストは `scripts/dashboard_assets/types/` に置きます（ファイル名はそのフォルダの README を参照）。3 秒ごとに読み直すので、会話して眠らせると数秒後にグラフが動きます。DB は読むだけなので、エージェントと同時に動かして大丈夫です。会話の記憶が見えるため、この PC からしか開けません（仕様 §6.6）。グラフの描画には [Chart.js](https://www.chartjs.org/)（MIT ライセンス）を CDN から読み込んでいます。

人ごとの機能が入る前の古い DB だと「DB を読めません: no such column: person_id」と出ます。エージェントを一度起動すると DB が新しい形に直り、表示されるようになります。

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
# Ollama の Gemma 4（Mac など）: 思考モードを切って、エージェントと同じ条件で測る
LLM_BASE_URL=http://localhost:11434/v1 LLM_MODEL=gemma4:latest LLM_REASONING_EFFORT=none uv run python -m scripts.check_llm

# STT（#2）: 無音・雑音で変な文字が出ないか。録音したファイルを渡すと、ウェイクワードを聞き取れるかも確かめる
uv run python -m scripts.check_stt
uv run python -m scripts.check_stt futamaru1.wav futamaru2.m4a
```

`check_llm` は `.env` を読まないので、上のように環境変数か `--base-url`・`--model`・`--reasoning-effort` で指定します。Ollama の Gemma 4 は思考モードが既定でオンなので、`LLM_REASONING_EFFORT=none` を付けないと、エージェントで使うときより遅く測れてしまいます（#54）。`check_stt` は `.env` の `STT_BASE_URL`・`STT_MODEL` を使います。

### テスト

```bash
uv run pytest
```

PR を出す前に `uvx ruff format . && uvx ruff check . && uv run pytest` を通してください（CI と同じです）。
