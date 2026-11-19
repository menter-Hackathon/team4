# ふたまるの「脳」を Docker で動かす（デプロイ手順）

NVIDIA の GPU が 2 枚ある PC で、ふたまるの脳（エージェント・LLM・STT・声）と、性格を見せるダッシュボードをまとめて動かす手順です。
準備ができたあとの、発表の日の手順は [`demo-day.md`](demo-day.md) にまとめています。
鉢（ESP32）にはマイクとスピーカーだけがあり、同じ LAN（Wi-Fi）の中で、この PC の LiveKit サーバーを通して脳とつながります。

```
鉢（ESP32: マイク・スピーカー） ──同じ LAN──> この PC の Docker
                                                ├─ livekit（LiveKit サーバー。鉢とエージェントをつなぐ）
                                                      ├─ agent（エージェント）
                                                      ├─ llm（Gemma 4 E4B の圧縮版、Ollama。GPU 1 枚目）
                                                      ├─ stt（kotoba-whisper。GPU 2 枚目）
                                                      ├─ tts（AivisSpeech。TTS_PROVIDER=aivis のとき）
                                                      └─ dashboard（http://127.0.0.1:8765）
                                   TTS_PROVIDER=openai（既定）のときは、声に OpenAI を使う
```

設計: `docs/superpowers/specs/2026-11-16-docker-backend-design.md`

## 1. 必要なもの

- **NVIDIA の GPU が 2 枚ある PC**（x86_64）。LLM と STT に 1 枚ずつ使います（デモの PC: RTX 5070 と RTX 5060）
  - **LLM 用に 12GB 以上**の GPU。Gemma 4 E4B の圧縮版（`gemma4:e4b-it-q4_K_M`、約 6.6GB）と、会話の作業用メモリを載せます
  - **STT 用に 8GB 以上**の GPU。kotoba-whisper を載せます
  - 画面（モニター）は、STT 用の GPU につないでおくと安心です。画面の表示にも GPU のメモリを使うため、LLM 用の GPU の余裕が減ります
- **NVIDIA ドライバ 570 以上**（RTX 50 シリーズに必要）
- Docker
  - **Windows**: Docker Desktop（WSL2 を使う設定）
  - **Linux**: Docker Engine と NVIDIA Container Toolkit
- ディスクの空き 40GB 以上（モデルとイメージで十数〜数十 GB）
- **鉢と同じ LAN（Wi-Fi）につながっていること**。鉢はこの PC の LAN の IP に直接つなぎます。ESP32 は 2.4GHz の Wi-Fi にしかつながらないので、PC は有線でも、同じルーターの下にあれば大丈夫です
- インターネット接続（初回のモデルのダウンロード。声を AivisSpeech にするときは、声のモデルを AivisHub から取る）

**GPU とドライバがコンテナから使えるか**を先に確かめます。

```bash
docker run --rm --gpus all nvidia/cuda:12.8.1-base-ubuntu24.04 nvidia-smi
```

表示された表で、次の 3 つを確かめます。

- 右上の **`CUDA Version` が 12.8 以上**（足りなければ、NVIDIA のサイトからドライバを更新する）
- GPU が **2 枚**並んでいる
- 各 GPU の**左端の番号（0 か 1）**と、メモリ（`MiB` の右側）。メモリの大きいほう（12GB ≒ 12000MiB）が LLM 用です

## 2. どの GPU を何に使うか決める

1 で見た番号を、3 で作る `.env` に書きます。**LLM にはメモリの大きい GPU** を使います。

```
LLM_GPU=0
STT_GPU=1
```

書かなければ、LLM が 0 番、STT が 1 番を使います。1 の表で 0 番がメモリの小さい GPU（8GB）なら、`LLM_GPU=1`・`STT_GPU=0` と書いてください。

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
| `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` | この PC の LiveKit サーバーのキー。**自分で決めます**（下の作り方）。シークレットは 32 文字以上 |
| `LIVEKIT_NODE_IP` | この PC の LAN の IP（例: `192.168.1.20`）。調べ方は 3.5 |
| `OPENAI_API_KEY` | チームの OpenAI のキー（声を OpenAI にするときだけ。`TTS_PROVIDER=aivis` なら空でよい） |
| `LLM_GPU` / `STT_GPU` | 2 で決めた GPU の番号（空なら LLM が 0、STT が 1） |
| `TTS_PROVIDER` | 植物の声。`aivis`（AivisSpeech）か `openai`（空なら openai） |
| `AIVIS_SPEAKER` | `aivis` のときの声のスタイル ID（空なら「まお / あまあま」）。例: `893625024` =「らせつん / 標準」 |
| `AIVIS_MODELS` | （任意）AivisHub から入れる声のモデルの UUID（カンマ区切り。空ならせつん） |
| `JMA_AMEDAS_STATION` / `JMA_FORECAST_AREA` | （任意）天気を取る場所。例: 東京なら `44132` / `130000`（`.env.example` の説明を参照） |

LiveKit のキーは、次のコマンドで作れます（出てきた文字列を `.env` に書く）。

```bash
openssl rand -hex 8    # LIVEKIT_API_KEY に書く
openssl rand -hex 32   # LIVEKIT_API_SECRET に書く
```

`LIVEKIT_URL` は書かなくて大丈夫です。Docker では、エージェントは同じ compose の LiveKit サーバー（`ws://livekit:7880`）につなぎます。

`LLM_BASE_URL`・`LLM_MODEL`・`LLM_REASONING_EFFORT`・`STT_BASE_URL`・`STT_MODEL`・`AIVIS_BASE_URL` は書かなくて大丈夫です。Docker では `compose.yaml` の設定（コンテナ内の LLM・STT・AivisSpeech）が使われます。開発用に OpenAI や手元の AivisSpeech を向けた値が書いてあっても、Docker では無視されます。

## 3.5 鉢から PC に届くようにする（ネットワーク）

鉢は、この PC の 3 つのポートにつなぎます: **7880/TCP**（接続の受付）、**7881/TCP**（音声の予備）、**7882/UDP**（音声）。

### PC の LAN の IP を調べる

- **Windows**: PowerShell で `ipconfig` を実行し、Wi-Fi か イーサネット の「IPv4 アドレス」（例: `192.168.1.20`）。Tailscale の `100.` で始まる IP ではありません
- **Linux**: `hostname -I` の最初の値

この IP を `.env` の `LIVEKIT_NODE_IP` に書きます。IP が変わると鉢がつながらなくなるので、ルーターの設定で固定（DHCP の予約）しておくと安心です。

### Windows（WSL2 の Ubuntu で Docker を動かす場合）

WSL2 の中の Docker には、そのままでは LAN からつながりません。次の 2 つを 1 回だけ行います。

1. WSL のネットワークを、Windows と同じ IP を使う「mirrored」にします。`C:\Users\<ユーザー名>\.wslconfig` に次を書き（無ければ作る）、PowerShell で `wsl --shutdown` してから Ubuntu を開き直します

```ini
[wsl2]
networkingMode=mirrored
```

2. **管理者の PowerShell** で、3 つのポートへの受信を許可します（WSL の中への通信は、Hyper-V のファイアウォールで決まります）

```powershell
New-NetFirewallHyperVRule -Name futamaru-livekit-tcp -DisplayName "futamaru LiveKit TCP" -Direction Inbound -VMCreatorId '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}' -Protocol TCP -LocalPorts 7880,7881 -Action Allow
New-NetFirewallHyperVRule -Name futamaru-livekit-udp -DisplayName "futamaru LiveKit UDP" -Direction Inbound -VMCreatorId '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}' -Protocol UDP -LocalPorts 7882 -Action Allow
```

ネットワークの種類が「パブリック」だと通らないことがあります。Windows の設定 → ネットワークとインターネット で、つないでいる Wi-Fi・イーサネットを「プライベート」にしてください。

### Linux

ファイアウォール（ufw）を使っているときだけ、3 つのポートを開けます。

```bash
sudo ufw allow 7880:7881/tcp
sudo ufw allow 7882/udp
```

## 4. 起動する

```bash
docker compose up -d
```

**初回はモデルのダウンロードで 10〜30 分ほどかかり、その間このコマンドは終わらずに待ち続けます（固まったように見えます）。** `llm-init`（Gemma 4 のダウンロード・読み込み）と `stt-init`・`tts-init` が終わると、最後に `agent` を起動してから終わります。

- **途中で Ctrl+C を押したり、ターミナルを閉じたりしないでください。** `agent` が起動しないままになります。もし止めてしまったら、もう一度 `docker compose up -d` を実行すれば続きから進みます
- 最後に `dependency failed to start` と出て終わった場合も、まず `docker compose up -d` をもう一度実行してください（ダウンロードが途中で止まったときなど）。それでも同じなら 7 を見てください

進み具合は、**別のターミナルを開いて**ログで見られます。

```bash
docker compose ps                # 各サービスの状態
docker compose logs -f llm-init  # Gemma 4 のダウンロード・GPU への読み込みの結果（Ctrl+C で抜ける。止まりはしない）
docker compose logs stt-init     # STT のモデルのダウンロード・読み込みの結果
docker compose logs tts-init     # 声のモデルのダウンロード・読み込みの結果（aivis のとき）
docker compose logs -f agent     # エージェントのログ
```

## 5. 動いたか確かめる

1. `docker compose ps -a` で `llm-init`・`stt-init`・`tts-init` が `exited (0)`、ほかが `running` になっている
   - `docker compose logs llm-init` に「モデルが全部 GPU に載りました」と出ている。「注意: モデルの …% しか GPU に載っていません」と出たら、`.env` の `LLM_GPU` がメモリの大きい GPU の番号か確かめる（2 を参照）
   - `TTS_PROVIDER=aivis` なら、`docker compose logs tts-init` の最後に `準備完了` と出ている（openai なら「準備はしません」と出るだけでよい）
2. `docker compose logs livekit` に `starting LiveKit server` と、`.env` の `LIVEKIT_NODE_IP` の IP が出ている。`docker compose logs agent` に `registered worker` などの、LiveKit サーバーへ接続したログが出ている
3. 鉢のトークンを作り、ファームの `firmware/sdkconfig.secrets` に書いて書き込む（`firmware/README.md`）

```bash
docker compose exec agent python -m scripts.issue_token --plant-id a1   # 出てきた文字列が CONFIG_FUTAMARU_TOKEN
```

   `CONFIG_FUTAMARU_SERVER_URL` は `ws://<LIVEKIT_NODE_IP の値>:7880`（`wss` ではなく `ws`）。LiveKit のキーを変えたら、トークンも作り直す

4. 鉢（ESP32）の電源を入れる。ESP32 がルーム `plant-<id>` に入ると、エージェントが参加する
5. 「ふたまる」と呼びかけて、植物から話しかけてくれば成功
6. この PC のブラウザで http://127.0.0.1:8765 を開くと、ダッシュボードが映る（並べる株は `.env` の `DASHBOARD_PLANTS`、既定 `a1,b1`）

## 6. 止める・作り直す

```bash
docker compose down                 # 止める（モデルも植物の記憶も残る）
git pull && docker compose up -d --build   # コードを更新して起動し直す
```

- **植物の記憶**（会話ログ・性格）は、この PC の `data/futamaru.db` に残ります。コンテナを作り直しても消えません
- `docker compose down -v` はモデルの保存場所も消します（次の起動で再ダウンロード）。植物の記憶（`data/`）は消えません
- **PC を再起動したとき**は、コンテナは自動で起動し直します（`restart: unless-stopped`）。ただし、`llm-init` は動かないので、Gemma 4 は最初の返事のときに GPU へ読み込まれます。モデルはダウンロード済みなので、最初の返事が数秒〜十数秒遅れるだけです。気になるときは `docker compose up -d` をもう一度実行すると、`llm-init` が先に読み込ませます
- DB を読み書きするコマンド（`scripts/` の下のもの）は、コンテナの中で `docker compose exec agent python -m scripts.<名前>` として動かします。Linux では DB のファイルが `root` の持ち物になるので、PC 側から直接書き換えないでください

## 6.4 デモ用：育てた株の DB に入れ替える

発表用に育てた DB（`scripts.simulate` で作ったもの、README「発表用に…」）を `data/` に置き、エージェントとダッシュボードを止めてから入れ替えます。

```bash
docker compose stop agent dashboard
docker compose run --rm --no-deps agent python -m scripts.use_db data/demo-day30.db
docker compose up -d
```

- `data/futamaru.db` を、指定した DB の中身に入れ替えます。入れ替える前の DB は `data/backup/` に日時付きで残ります
- ファイルを直接コピーして上書きしないでください。SQLite の `-wal` ファイルが残っていると、DB が壊れることがあります（`scripts/use_db.py` はそれを避けて入れ替えます）

## 6.5 デモ用：人の一覧と方言の固定

```bash
docker compose exec agent python -m scripts.people --plant-id a1
docker compose exec agent python -m scripts.people --plant-id a1 --person たろう --dialect hakata
docker compose exec agent python -m scripts.people --plant-id a1 --person タロウ --auto
```

- 一覧には名前・性格 4 軸・今の方言（固定か自動か）が出ます。名前はひらがな・カタカナの違いを無視して照合します
- 人を替えるには、植物を一度眠らせる必要があります。デモでは `.env` の `SLEEP_AFTER_SECONDS` を短く（例: `20`）して `docker compose up -d` し直してください

## 7. 困ったとき

| 症状 | 原因と対処 |
|---|---|
| `llm-init` が `exited (1)` で終わり、`agent` が起動しない | `docker compose logs llm-init` を見る。「ダウンロードできません」ならインターネット接続か `.env` の `DOCKER_LLM_MODEL`、「読み込めません」なら `docker compose logs llm` で GPU のエラーを見る |
| `llm` のログに `CUDA driver version is insufficient` | ドライバが古い。1 の確認をして、ドライバを更新する（570 以上） |
| 返事がとても遅い（10 秒以上） | `docker compose logs llm-init` で、モデルが全部 GPU に載ったか見る。一部しか載っていなければ、`LLM_GPU` を 12GB の GPU にする。ゲームやブラウザなど、GPU を使うアプリを閉じる |
| GPU が 1 枚しかない | LLM と STT を同じ GPU に載せる必要がある。`.env` に `LLM_GPU=0`・`STT_GPU=0` と書くと試せるが、12GB の GPU 1 枚では足りないことがある。Discord で相談してください |
| `could not select device driver "nvidia"` | Docker から GPU が見えていない。1 の `nvidia-smi` の確認からやり直す（Windows は Docker Desktop の WSL2 設定、Linux は NVIDIA Container Toolkit） |
| `stt-init` が `exited (1)` で終わり、`agent` が起動しない | STT のモデルを用意・読み込みできなかった。`docker compose logs stt-init` と `docker compose logs stt` を確認し、`.env` に `DOCKER_STT_MODEL=Systran/faster-whisper-large-v3` を書いて `docker compose up -d` |
| `tts-init` が `exited (1)` で終わり、`agent` が起動しない | 声のモデルを用意できなかった。`docker compose logs tts-init` を見る。「入れられませんでした」ならインターネット接続か `AIVIS_MODELS` の UUID、「スタイル ID … が見つかりません」なら `AIVIS_SPEAKER` を直す（ログに入っている ID の一覧が出る）。急ぐときは `.env` を `TTS_PROVIDER=openai` にして `docker compose up -d` すれば、OpenAI の声で動く |
| 植物の声が出ない（aivis のとき） | `docker compose logs tts` にエラーが無いか見る。CPU が足りず遅いときは、ほかのアプリを閉じる |
| `agent` は `running` なのに、植物が返事をしない | `docker compose logs agent` に `STT` や `LLM` のエラーが無いか、`docker compose logs stt`・`docker compose logs llm` にエラーが無いかを見る。ほかのメンバーが同じ LiveKit のキーで `uv run python -m agent.main dev` を動かしていると、そちらに植物を取られることがあるので止めてもらう |
| 鉢が LiveKit につながらない（ESP32 のログに接続の失敗が出る） | 鉢と PC が同じ LAN か、`CONFIG_FUTAMARU_SERVER_URL` が `ws://<PC の LAN の IP>:7880` か確かめる。同じ LAN のスマホのブラウザで `http://<PC の LAN の IP>:7880` を開き、`OK` と出なければ 3.5 のネットワークの設定を見直す |
| 鉢はつながるのに声が届かない・返事が聞こえない | 音声の UDP（7882）が通っていない。`.env` の `LIVEKIT_NODE_IP` が今の PC の IP か、3.5 のファイアウォールの設定（UDP 7882）を確かめる |
| `docker compose logs livekit` に `secret is too short` | `.env` の `LIVEKIT_API_SECRET` を 32 文字以上にする（3 の `openssl rand -hex 32`） |
| `agent` がすぐ止まる | `docker compose logs agent` を見る。`環境変数 … が設定されていません` なら `.env` の書き漏れ |

解決しないときは、`docker compose logs` の該当部分を Discord に貼ってください（**キーが含まれていないか確認してから**）。

## できないこと

- **console モード（PC のマイクで試す）は Docker では使えません。** コンテナからは PC のマイク・スピーカーに触れないためです。console は今までどおり `uv run python -m agent.main console` で使います（README）
- GPU の無い PC・Mac では、この構成は動きません
- AivisSpeech は CPU で動かします（GPU のメモリは LLM と STT で使い切るため）。声のモデルは `aivis-data` ボリュームに残るので、2 回目の起動からはダウンロードしません
