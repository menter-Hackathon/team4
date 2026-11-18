# ふたまるの ESP32 ファームウェア

鉢の中の **Seeed XIAO ESP32S3 ＋ M5Stack Atomic Voice Base** で動くファームウェアです。
Wi-Fi につないで LiveKit のルーム `plant-<id>` に入り、マイクの音を送りっぱなしにして、エージェントの声をスピーカーで鳴らします（[仕様 §9](../docs/superpowers/specs/2026-11-13-futamaru-agent-design.md)）。
考える処理（ウェイクワード・会話・記憶）はすべてサーバー側（`agent/`）にあり、ここにはありません。

使っているもの: ESP-IDF v5.4.2、[LiveKit ESP32 SDK](https://github.com/livekit/client-sdk-esp32) 0.3.12（版を固定）、esp_codec_dev（ES8311 のドライバ）。部品を選んだ理由は [parts-selection.md](../docs/hardware/parts-selection.md) にあります。

## ファイル

| ファイル | 中身 |
|---|---|
| `main/main.c` | 入り口。設定の確認 → 音まわり → Wi-Fi → ルームの順に始める |
| `main/board.c` | I2C・I2S・アンプ・音声チップ ES8311 の初期化。**ピンの割り当てはここ** |
| `main/media.c` | マイクの音（エコーキャンセルつき）と、届いた声の再生 |
| `main/room.c` | LiveKit のルームに入る。切れたら間を空けて入り直す |
| `main/status_led.c` | XIAO の LED で状態を見せる |
| `main/token_info.c` | トークンからルーム名を読んでログに出す（2 台の取り違え防止） |
| `main/Kconfig.projbuild` | 設定項目（音量・マイクの増幅・AEC の有無など） |
| `sdkconfig.defaults` | 既定の設定（XIAO の Flash・PSRAM、SDK に必要な設定） |
| `sdkconfig.secrets.example` | 秘密情報（Wi-Fi・トークン）のひな形 |

## 配線

ジャンパー線で 9 本つなぎます。Voice Base の上面（Atom を差す面）のオスピンに、メスのジャンパー線を挿します。下面の 4 ピンの穴（G・5・SDA・SCL）は使いません。Voice Base の上面のシールには、AtomS3 の GPIO 番号（水色）が書いてあります。対応は SCL = G39、SDA = G38、SCLK = G8、LRCK = G6、DSDIN = G5、ASDOUT = G7 です。

| Voice Base | 役割 | XIAO の印字 | GPIO |
|---|---|---|---|
| SDA | I2C のデータ | D4 | 5 |
| SCL | I2C のクロック | D5 | 6 |
| SCLK | I2S のビットクロック | D0 | 1 |
| LRCK | I2S の左右の切り替え | D1 | 2 |
| DSDIN | スピーカーへの音（XIAO → Voice Base） | D2 | 3 |
| ASDOUT | マイクの音（Voice Base → XIAO） | D3 | 4 |
| 5V | 電源 | 5V | - |
| GND | GND | GND | - |
| 3V3 | 3.3V 電源（音声チップ用。Atom から受け取る作り） | 3V3 | - |

- 配線を変えたときは `main/board.c` の先頭の `PIN_...` を直します
- **XIAO には付属の Wi-Fi アンテナを必ず付けます**（U.FL コネクタ）
- **電源は XIAO の USB-C から入れます。** Voice Base のアンプは 5V で動き、XIAO の 5V ピンに電気が来るのは USB 給電のときだけです
- Voice Base のアンプは、基板上の IO エキスパンダ（PI4IOE5V6408、I2C 0x43）で ON にします。ファームウェアが起動時に行います

## 準備（最初の 1 回）

1. ESP-IDF v5.4.2 を入れる（[公式の手順](https://docs.espressif.com/projects/esp-idf/en/v5.4.2/esp32s3/get-started/)）

   ```bash
   mkdir -p ~/esp && cd ~/esp
   git clone -b v5.4.2 --recursive https://github.com/espressif/esp-idf.git esp-idf-v5.4.2
   cd esp-idf-v5.4.2 && ./install.sh esp32s3
   ```

   使うときは、ターミナルを開くたびに `. ~/esp/esp-idf-v5.4.2/export.sh` を実行します。

2. 秘密情報のファイルを作る

   ```bash
   cd firmware
   cp sdkconfig.secrets.example sdkconfig.secrets
   ```

   `sdkconfig.secrets` に Wi-Fi の SSID・パスワード、LiveKit の URL、トークンを書きます。
   トークンは株ごとに発行します（リポジトリの直下で `uv run python -m scripts.issue_token --plant-id a1`）。
   **`sdkconfig.secrets` は `.gitignore` 済みです。コミット・Issue・PR・チャンネルに貼らないでください。**

## ビルドと書き込み

```bash
cd firmware
idf.py set-target esp32s3   # 最初の 1 回だけ
idf.py build flash monitor
```

- ポートが見つからないときは、XIAO の **B（BOOT）ボタンを押しながら USB-C を挿し**、書き込みモードにしてから `idf.py -p <ポート> flash` を実行します
- `monitor` を抜けるのは `Ctrl+]` です
- **`sdkconfig.secrets` を書き換えたら（2 台目のトークンに替えるときなど）、先に `rm -f sdkconfig` を実行します。** 前の値が `sdkconfig` に残っていると、新しい値が使われません

### 2 台目を書き込むとき

1. `sdkconfig.secrets` のトークンを 2 台目のもの（例: `--plant-id b1`）に替える
2. `rm -f sdkconfig && idf.py build flash monitor`
3. ログに `This device joins room "plant-b1"` と出ることを確かめる

## 動いているかの見方

### ログ（`idf.py monitor`）

正しく動くと、だいたい次の順に出ます。

```text
I (...) futamaru: This device joins room "plant-a1"
I (...) board: I2C device found at 0x18        ← ES8311（音声チップ）
I (...) board: I2C device found at 0x43        ← アンプの ON/OFF 用のチップ
I (...) board: Speaker amp enabled
I (...) media: Media ready (AEC: on)
I (...) futamaru: Connecting to Wi-Fi "..."
I (...) room: Room state: Connected
I (...) room: Agent joined the room
```

| 出たログ | 疑うこと |
|---|---|
| `ES8311 (0x18) not found` | SDA と SCL の入れ違い、GND・5V のつなぎ忘れ |
| `I2C scan done: 0 device(s)` | 同上。Voice Base に電気が来ていない |
| Wi-Fi の `Retry` が続く | SSID・パスワードの間違い、5GHz だけの Wi-Fi |
| `Token was rejected` | トークンの期限切れ・コピーの欠け。発行し直して書き込み直す |
| `Connection failed: Unreachable` | URL の間違い、会場の Wi-Fi が WebRTC（UDP）を通さない |
| つながるのに `Agent joined` が出ない | PC 側のエージェントが動いていない（`uv run python -m agent.main dev`） |

### LED（XIAO の基板上）

| 光り方 | 状態 |
|---|---|
| 速い点滅 | Wi-Fi につないでいる |
| ゆっくり点滅 | LiveKit のルームにつないでいる |
| 2 秒に 1 回、短く光る | ルームに入った。エージェントを待っている |
| 点灯 | エージェントもいる。「ふたまる」と話しかけられる |
| 2 回ずつ点滅 | 止まっている（設定不足・配線の間違い）。ログを見る |

LED が逆に光る（点灯と消灯が入れ替わる）ときは、`main/status_led.c` の `LED_ON_LEVEL` を 1 にします。

## 実機で最初に確かめること

1. **I2C に 0x18 と 0x43 が見えるか**（配線の確認）
2. **声が鳴るか・音量は足りるか。** 小さければ `CONFIG_FUTAMARU_SPEAKER_VOLUME` を上げる（`idf.py menuconfig` の「Futamaru」）
3. **マイクの音が届くか。** サーバーのログで文字起こしが出るか。小さければ `CONFIG_FUTAMARU_MIC_GAIN_DB` を上げる
4. **エコーキャンセルが効くか。** 植物が話している間に話しかけ、植物が自分の声を拾って返事をし直したりしないか

### エコーキャンセル（AEC）がうまく動かないとき

ES8311 の「スピーカーに出した音を録音の右チャンネルに入れる」機能を使っていますが、この部品の組み合わせで LiveKit の SDK と一緒に動くかは実機で未確認です（[parts-selection.md](../docs/hardware/parts-selection.md)）。
だめなときは `sdkconfig.secrets` か `sdkconfig.defaults` に `CONFIG_FUTAMARU_USE_AEC=n` を書いてビルドし直し、サーバー側で割り込みを止めます（`agent/main.py` の `interruption={"enabled": False}`。エージェント担当と相談）。
