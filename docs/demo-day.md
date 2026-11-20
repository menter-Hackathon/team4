# 発表の日の手順（ショーンさんの PC）

ふたまるの脳を、ショーンさんの PC の Docker で動かして発表するための手順です。
初めて準備するときの詳しい説明は [`deploy.md`](deploy.md) にあります。ここには、**準備ができたあと、当日にすること**だけを書きます。

コマンドは、すべて WSL2 の Ubuntu のターミナルで、`team4` フォルダの中で実行します（`cd ~/team4`）。

## 前の日までに（1 回だけ）

1. [`deploy.md`](deploy.md) の 1〜4 のとおりに、一度 `docker compose up -d` まで済ませる。**初回はモデルのダウンロードで 30 分ほどかかる**ので、当日にはしない
   - GPU は 2 枚使う（LLM に RTX 5070、STT に RTX 5060）。`nvidia-smi` の番号を見て、`.env` の `LLM_GPU`・`STT_GPU` を決める（deploy.md の 2）
2. `.env` に、デモ用の設定を書く（キーは Discord の DM で受け取る）
   ```
   TTS_PROVIDER=aivis
   AIVIS_SPEAKER=893625024
   JMA_AMEDAS_STATION=44132
   JMA_FORECAST_AREA=130000
   ```
   - `AIVIS_SPEAKER` は植物の声（`893625024` =「らせつん / 標準」）。声が決まったら変える（#56）
   - `JMA_…` は天気を取る場所（例は東京）。発表する場所に合わせて変える
   - デモでは登録したショーンさん・ちゃむさんだけを話し相手にするので、#96 がマージされたら `ALLOW_NEW_PEOPLE=false` も書く
3. 発表用に育てた株の DB（`demo-day1.db`・`demo-day5.db`・`demo-day30.db`）を Discord の DM で受け取り、`team4/data/` に置く
   - Windows のエクスプローラーからは、アドレス欄に `\\wsl$\Ubuntu\home\<ユーザー名>\team4\data` と入れると開ける
4. ダッシュボードにログインするアカウントを作る（[`deploy.md`](deploy.md) の 6.6）。発表で画面を映す人の分を作り、パスワードは DM で渡す
   ```bash
   docker compose exec agent python -m scripts.accounts add <名前>
   ```
5. 下の「当日」の 1〜4 を一度通して、植物が返事をし、ダッシュボードが映ることを確かめる（リハーサル）

## 当日

### 1. 起動する

```bash
docker compose up -d
docker compose ps -a
```

`llm-init`・`stt-init`・`tts-init` が `exited (0)`、ほかが `running` になれば準備完了です。`docker compose logs llm-init` に「モデルが全部 GPU に載りました」と出ていれば、返事も速く返ります。

### 2. 見せたい日の株に入れ替える

発表で見せる株（1 日目・5 日目・30 日目）に、DB を入れ替えます。**エージェントとダッシュボードを止めてから**入れ替えます（動いている最中に入れ替えると DB が壊れることがあるため）。

```bash
docker compose stop agent dashboard
docker compose run --rm --no-deps agent python -m scripts.use_db data/demo-day30.db
docker compose up -d
```

- `demo-day30.db` のところを、見せたい日の DB に変える（`demo-day1.db`・`demo-day5.db`）
- 入れ替える前の DB は `data/backup/` に日時付きで残るので、あとで元に戻せる

### 3. ダッシュボードを開く

この PC のブラウザで **http://127.0.0.1:8765** を開きます（ほかの PC からは `https://futamaru.<チームのドメイン>`。[`deploy.md`](deploy.md) の 3.6）。アカウント名とパスワードを聞かれるので、前の日に作ったアカウントでログインします。株の性格のタイプ・レーダーチャート・成長曲線・覚えていることが並びます。
会話の記憶が見えるので、ログインした人しか開けないようにしてあります。画面共有で見せてください。

### 4. 話しかける

鉢（ESP32）の電源を入れ、「ふたまる」と呼びかけます。植物が話し始めたら成功です。
話し終えて 60 秒たつと植物は眠り、そのあとダッシュボードの性格のグラフが少し動きます。

### 5. 終わったら

```bash
docker compose down
```

モデルも植物の記憶も残ります。

## 困ったとき

| 症状 | まず試すこと |
|---|---|
| 植物が返事をしない | `docker compose logs --tail 50 agent` を見て、エラーの行を Discord に貼る（**キーが含まれていないか確認してから**） |
| 声が出ない | `docker compose logs --tail 50 tts` を見る。急ぐときは `.env` を `TTS_PROVIDER=openai` に変えて `docker compose up -d`（OpenAI の声になる） |
| ダッシュボードが開かない | `docker compose ps -a` で `dashboard` が `running` か見る。止まっていれば `docker compose up -d` |
| ダッシュボードにログインできない | `docker compose exec agent python -m scripts.accounts list` でアカウントがあるか見る。パスワードを忘れたら `docker compose exec agent python -m scripts.accounts passwd <名前>` で変える |
| ダッシュボードに株が出ない | 2 の入れ替えをしたか確かめる。並べる株を変えるなら `.env` の `DASHBOARD_PLANTS`（既定 `a1,b1`） |
| どうしても動かない | `docker compose down` → `docker compose up -d` でやり直す。それでもだめなら [`deploy.md`](deploy.md) の「困ったとき」 |
