# ふたまるの ESP32 ファームウェア

鉢の中の **Seeed XIAO ESP32S3 ＋ M5Stack Atomic Voice Base** で動くファームウェアです。
Wi-Fi につないでサーバーの中継（relay）に WebSocket でつなぎ、マイクの音を送りっぱなしにして、届いた植物の声をスピーカーで鳴らします（[仕様 §3.2.1・§9](../docs/superpowers/specs/2026-11-13-futamaru-agent-design.md)）。
LiveKit には中継（サーバー側）が入るので、この端末は LiveKit に直接はつなぎません。
考える処理（ウェイクワード・会話・記憶）はすべてサーバー側（`agent/`）にあり、ここにはありません。

使っているもの: ESP-IDF v5.4.2、esp_websocket_client（中継との WebSocket）、esp_capture（エコーキャンセル。中で esp-sr の AEC を使う）、esp_codec_dev（ES8311 のドライバ）。部品の版は `main/idf_component.yml` で固定しています。部品を選んだ理由は [parts-selection.md](../docs/hardware/parts-selection.md) にあります。

### 中継とのやり取り（仕様 §3.2.1）

| 順番 | 向き | 中身 |
|---|---|---|
| 0 | 端末 → サーバー | `CONFIG_FUTAMARU_RELAY_URL` に接続。HTTP ヘッダ `Authorization: Bearer <CONFIG_FUTAMARU_DEVICE_KEY>` |
| 1 | 端末 → サーバー | つながってから 5 秒以内に文字で `{"type":"start","version":1,"plant_id":"a1","codec":"mulaw"}` |
| 2 | サーバー → 端末 | 文字で `{"type":"ready","sample_rate":16000,"codec":"mulaw","frame_bytes":320}`。これが来てからマイクの音を送り始める |
| 3 | 端末 → サーバー | バイナリでマイクの音を送り続ける |
| 4 | サーバー → 端末 | バイナリで植物の声。届いた順に鳴らす。無音は送られてこないので、届かないあいだは無音を鳴らす |

- 音は送りも受けも 16kHz・モノラル。既定（`CONFIG_FUTAMARU_MULAW=y`）では μ-law（G.711）で圧縮し、1 フレーム = 20ms = 320 バイト（16KB/秒）。`n` にすると 16 ビットの PCM のまま（640 バイト、32KB/秒）。iPhone のテザリングでは、PCM のままだと上りが足りず 1 割しか送れなかった（2026-11-19）
- マイクの音は 40ms 分ずつまとめて送ります。回線が詰まっても 2 秒分まではためておき、それを超えると古いものから捨てます（ログに `Network is slow; dropped mic audio`）
- サーバーは間違いがあると `{"type":"error","code":"...","message":"..."}` を送って閉じます（ログに出ます）
- 切れたら 5 秒待ってつなぎ直し、失敗が続くと待ち時間を倍にします（最長 60 秒）。ready まで進めたら 5 秒に戻します

## ファイル

| ファイル | 中身 |
|---|---|
| `main/main.c` | 入り口。設定の確認 → 音まわり → Wi-Fi → 中継の順に始める |
| `main/board.c` | I2C・I2S・アンプ・音声チップ ES8311 の初期化。**ピンの割り当てはここ** |
| `main/media.c` | マイクの音を 20ms ずつ取り出す（植物が話している間は無音にする）。届いた声をためて鳴らす（届かないあいだは無音） |
| `main/relay.c` | 中継との WebSocket。start → ready → 音の送り受け。切れたら間を空けてつなぎ直す |
| `main/status_led.c` | XIAO の LED で状態を見せる |
| `main/Kconfig.projbuild` | 設定項目（中継の URL・合言葉・株の ID・音量・マイクの増幅・AEC の有無） |
| `sdkconfig.defaults` | 既定の設定（XIAO の Flash・PSRAM、TLS の証明書など） |
| `sdkconfig.secrets.example` | 秘密情報（Wi-Fi・中継の URL・合言葉）のひな形 |

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

   `sdkconfig.secrets` に次の値を書きます。

   | 設定 | 中身 |
   |---|---|
   | `CONFIG_LK_EXAMPLE_WIFI_SSID` / `CONFIG_LK_EXAMPLE_WIFI_PASSWORD` | Wi-Fi（2.4GHz） |
   | `CONFIG_FUTAMARU_RELAY_URL` | 中継の URL。例: `wss://<ホスト>/relay`（手元の試験は `ws://<PC の IP>:<ポート>/relay`） |
   | `CONFIG_FUTAMARU_DEVICE_KEY` | 中継の合言葉（サーバー側と同じ値） |
   | `CONFIG_FUTAMARU_PLANT_ID` | 株の ID（英小文字・数字・`_`・`-` で 1〜32 文字）。既定は `a1` |

   **`sdkconfig.secrets` は `.gitignore` 済みです。コミット・Issue・PR・チャンネルに貼らないでください。**
   LiveKit 版のときの `CONFIG_FUTAMARU_SERVER_URL`・`CONFIG_FUTAMARU_TOKEN` は使わなくなりました。消してかまいません（残っていてもビルドの警告が出るだけです）。

## ビルドと書き込み

```bash
cd firmware
idf.py set-target esp32s3   # 最初の 1 回だけ
idf.py build flash monitor
```

- ポートが見つからないときは、XIAO の **B（BOOT）ボタンを押しながら USB-C を挿し**、書き込みモードにしてから `idf.py -p <ポート> flash` を実行します
- `monitor` を抜けるのは `Ctrl+]` です
- **`sdkconfig.secrets` を書き換えたら（2 台目の株の ID に替えるときなど）、先に `rm -f sdkconfig` を実行します。** 前の値が `sdkconfig` に残っていると、新しい値が使われません

### 2 台目を書き込むとき

1. `sdkconfig.secrets` の `CONFIG_FUTAMARU_PLANT_ID` を 2 台目のもの（例: `b1`）に替える
2. `rm -f sdkconfig && idf.py build flash monitor`
3. ログに `This device is plant "b1"` と出ることを確かめる

## 動いているかの見方

### ログ（`idf.py monitor`）

正しく動くと、だいたい次の順に出ます。

```text
I (...) futamaru: This device is plant "a1"
I (...) board: I2C device found at 0x18        ← ES8311（音声チップ）
I (...) board: I2C device found at 0x43        ← アンプの ON/OFF 用のチップ
I (...) board: Speaker amp enabled
I (...) media: Media ready (AEC: off, 16000 Hz, 640 bytes per frame)
I (...) futamaru: Connecting to Wi-Fi "..."
I (...) relay: Connecting to wss://.../relay
I (...) relay: WebSocket connected
I (...) relay: Sent start (plant_id "a1"), waiting for ready
I (...) relay: Relay is ready. Streaming the microphone
I (...) relay: Mic frames sent: 500 (muted 0), mic peak: 273, voice received: 0 bytes   ← 10 秒ごと
```

最後の行は 10 秒ごとに出ます。

- `Mic frames sent`: 送ったマイクのフレームの数（起動してからの合計）。増えていれば音は届いている
- `muted`: 植物が話していたので無音にしたフレームの数（起動してからの合計）
- `mic peak`: その 10 秒でいちばん大きかったマイクの音（0〜32767）。静かな部屋でも 50 以上あるのがふつう。**0 が続くならマイクの線（G7 → D3）がつながっていない**
- `voice received`: 中継から届いた声の量（起動してからの合計）

この行だけを見やすく出すツールがあります。配線を直しながら見るときに使います（リポジトリの一番上で実行。`idf.py monitor` は先に閉じる）。

```bash
uv run --with pyserial python -m scripts.mic_peak_monitor
```

```text
mic peak:    148  muted: 0
mic peak:  32767  muted: 0  ← 雑音（どこかの線の接触が悪い）
  E (3635) futamaru: Stopped: audio board not ready (check wiring)
```

USB が一瞬切れても止まらず、つながり直すのを待って読み続けます。線を 1 本ずつ指で押さえて、雑音の目印が消える線を探すと、接触の悪い線が見つかります。

| 出たログ | 疑うこと |
|---|---|
| `ES8311 (0x18) not found` | SDA と SCL の入れ違い、GND・5V のつなぎ忘れ |
| `I2C scan done: 0 device(s)` | 同上。Voice Base に電気が来ていない |
| Wi-Fi の `Retry` が続く | SSID・パスワードの間違い、5GHz だけの Wi-Fi |
| `WebSocket error (HTTP status 401 ...)` か `403` | 合言葉（`CONFIG_FUTAMARU_DEVICE_KEY`）の間違い |
| `WebSocket error (HTTP status 404 ...)` | URL の道（`/relay`）の間違い |
| `WebSocket error (HTTP status 0, TLS error ...)` | ホスト名の間違い、`wss://` と `ws://` の取り違え、トンネルが止まっている |
| `Relay error: code=invalid_start` | 株の ID の間違い（サーバーが知らない ID など） |
| `Relay error: code=livekit_unavailable` | サーバー側（LiveKit・エージェント）が動いていない。少し待つとつなぎ直す |
| `Relay did not send ready` | 中継が start に返事をしない。サーバー側のログを見る |
| `Reconnecting in N s` が続く | 上のどれか。N は 5 → 10 → 20 → 40 → 60 秒と伸びる |

### LED（XIAO の基板上）

| 光り方 | 状態 |
|---|---|
| 速い点滅 | Wi-Fi につないでいる |
| ゆっくり点滅 | 中継につないでいる（ready を待っている・つなぎ直しを待っている） |
| 点灯 | 中継から ready が来た。「ふたまる」と話しかけられる |
| 2 回ずつ点滅 | 止まっている（設定不足・配線の間違い）。ログを見る |

LED が逆に光る（点灯と消灯が入れ替わる）ときは、`main/status_led.c` の `LED_ON_LEVEL` を 1 にします。

## 実機で最初に確かめること

1. **I2C に 0x18 と 0x43 が見えるか**（配線の確認）
2. **声が鳴るか・音量は足りるか。** 小さければ `CONFIG_FUTAMARU_SPEAKER_VOLUME` を上げる（`idf.py menuconfig` の「Futamaru」）
3. **マイクの音が届くか。** サーバーのログで文字起こしが出るか（または上の偽物の中継で自分の声が返るか）。小さければ `CONFIG_FUTAMARU_MIC_GAIN_DB` を上げる
4. **植物が自分の声を拾わないか。** 植物が返事をしたあと、自分の声に返事をし直したりしないか。植物が話すたびに、ログの `muted` の数が増えていればマイクは止まっている

### 手元の PC だけで試す（偽物の中継）

サーバーの中継がまだ無いときは、次の偽物の中継で「つながる・音が往復する」ことを確かめられます。
マイクの音をそのまま送り返すので、話すと自分の声がスピーカーから返ってきます。

```python
# fake_relay.py — 手元の試験用の偽物の中継（実行: uv run --with websockets python fake_relay.py）
import asyncio
import json

from websockets.asyncio.server import serve


async def handler(ws):
    print("Authorization:", ws.request.headers.get("Authorization"))
    start = json.loads(await ws.recv())
    codec = start.get("codec", "pcm16")
    frame_bytes = 320 if codec == "mulaw" else 640
    await ws.send(
        json.dumps(
            {"type": "ready", "sample_rate": 16000, "codec": codec, "frame_bytes": frame_bytes}
        )
    )
    async for message in ws:
        if isinstance(message, bytes):
            await ws.send(message)  # マイクの音をそのまま返す


async def main():
    async with serve(handler, "0.0.0.0", 8765):
        await asyncio.Future()


asyncio.run(main())
```

`sdkconfig.secrets` の `CONFIG_FUTAMARU_RELAY_URL` を `ws://<PC の IP>:8765/relay` にし、合言葉は何か入れて書き込みます（PC と XIAO は同じ Wi-Fi に）。
返ってきた声を鳴らしている間はマイクを止めるので、自分の声は 1 回だけ返ってきます。何度も繰り返して聞こえる（ハウリングする）なら、マイクが止まっていません。音量に気をつけてください。

### 植物が自分の声を拾わないようにする仕組み（エコー対策）

既定では、**植物が話している間と、話し終えてから 0.3 秒の間は、マイクの音を無音にして送ります**（半二重。`main/media.c`、仕様 §9 の 4 の補足）。
植物が自分の声に返事をし直すことはありませんが、植物が話している途中で割り込むことはできません。

はじめはエコーキャンセル（AEC）を使う作りでした。ES8311 の「スピーカーに出した音を録音の右チャンネルに入れる」機能と、esp_capture（esp-sr）の AEC を組み合わせるものです。
ところが 2026-11-20 の実機では、AEC を通すと話しかけた人の声まで消えて、サーバーに何も届きませんでした。
AEC を通す前の音は正常でした（静かな部屋でマイクの大きさが 80 くらい、参照の音はスピーカーが無音のとき 0）。AEC の中で消えています。
そのため、今は AEC を使わないのが既定です（`sdkconfig.defaults` の `CONFIG_FUTAMARU_USE_AEC=n`）。

AEC を試すときは、`sdkconfig.secrets` に `CONFIG_FUTAMARU_USE_AEC=y` を書いてビルドし直します（`sdkconfig` を消してから）。
y のときはマイクを止めないので、AEC が効かないと植物が自分の声を拾います。
