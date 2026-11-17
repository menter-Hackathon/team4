# パーツ選定の記録（ESP32 まわり）

- 日付: 2026-11-16（ハード担当 syon.oba の調査。Claude Code と一緒に調べた）
- 関係するもの: 仕様書 [§9 ESP32 への要件](../superpowers/specs/2026-11-13-futamaru-agent-design.md)、買い物リスト [#35](https://github.com/menter-Hackathon/team4/issues/35)、筐体 [#16](https://github.com/menter-Hackathon/team4/issues/16)

ここには「なぜこの部品にしたか」を残します。買うものと数は #35 のチェックリストが正です。

## 結論

| 役割 | 部品 | 数 |
|---|---|---|
| マイコン | **Seeed XIAO ESP32S3**（Sense ではない方） | 2 |
| マイク・スピーカー・アンプ | **M5Stack Atomic Voice Base（A149、旧名 Atomic Echo Base）** | 2 |
| 配線 | ジャンパー線（Voice Base のピンソケットに挿す） | 1 袋 |
| 給電・書き込み | データ通信ができる USB-C ケーブル、モバイルバッテリー | 2・1 |

1 台あたり約 2,600 円です（スイッチサイエンスの掲載価格。店頭では違うことがあります）。

**ディスプレイは使いません。** 2026-11-16 にチャムさんと話し、目は筐体の形で表すことにしました（PR #21、#22）。

## 決めるときの条件

1. **仕様 §9 の要件**: ESP32-S3、LiveKit 公式の [`client-sdk-esp32`](https://github.com/livekit/client-sdk-esp32) を使う、マイクは送りっぱなし、スピーカーで返事を再生する、Wi-Fi でつなぐ
2. **エコーキャンセル（AEC）がほしい**: 植物が自分の声を拾うと、ウェイクワードの誤検出や、自分の声への割り込みが起きる
3. **11/18（水）に秋葉原の店頭で全部買える**: ネット注文は届かないと間に合わないので使わない
4. **鉢に入る大きさ**

## 調べてわかったこと

### SDK（`livekit/client-sdk-esp32` v0.3.12）

- Developer Preview なので API が変わることがある。**版を固定する**（`idf.py add-dependency "livekit/livekit=0.3.12"`）。ESP-IDF 5.4 以上が必要
- サンプルの設定は Flash 16MB・Octal PSRAM が前提。**PSRAM が無いボードは使えない**
- ESP32-S3 の Wi-Fi は **2.4GHz だけ**。会場の Wi-Fi が 5GHz だけだとつながらない
- サンプルの AEC は、音声チップから「マイクの音」と「スピーカーに出した音（参照）」を一緒に受け取って消す仕組み。**参照の音を戻す経路が無い部品の組み合わせでは、そのままでは AEC が使えない**

### AEC はサーバー側ではできない

AEC は「スピーカーから出した音」と「マイクで拾った音」を、数ミリ秒の精度で重ねて引き算する処理です。サーバーには、ESP32 のスピーカーで実際に鳴った時刻がわかりません。ネットワーク・バッファ・デコードの遅れが毎回変わるためです。また、LiveKit のサーバーは音を中継するだけで、エコー除去はしません。

サーバー側でできるのは、**植物が話している間はマイクの音を捨てる**ことだけです。`agent/main.py` の `TurnHandlingOptions` に `interruption={"enabled": False}` を足します。代わりに、話の途中で割り込めなくなります。

### 候補と、選ばなかった理由

| 候補 | よい点 | 選ばなかった理由 |
|---|---|---|
| ESP32-S3-BOX-3 | SDK の設定 1 行で動く。AEC あり | 箱型で大きく高い。秋葉原の在庫が見つからない |
| Waveshare ESP32-S3-Touch-LCD-1.83 | 公式の custom_hardware サンプルの対象。AEC あり。小さい | 国内の店で扱いが見つからず、海外発送では間に合わない |
| M5Stack AtomS3R ＋ Atomic Echo Base | SDK の対応一覧（`ATOMS3_ECHO_BASE`）とピンが一致する | 素の ESP の開発ボードで組みたかった（ハード担当の希望） |
| M5Stack CoreS3 | 店頭でよく見る | SDK の対応一覧に無い。AEC の参照の音が取れるか不明 |
| ESP32-S3-DevKitC-1（N8R8）＋ Voice Base | Espressif 純正 | 幅が広く、鉢に入れにくい |
| XIAO ESP32S3 Sense だけ | 小さい。マイク付き | マイク（PDM）だけで、音声チップもスピーカー出力も無い。AEC が使えない |
| ReSpeaker Lite ＋ XIAO | ボード上の XMOS チップで AEC をする | SDK のサンプルに無い構成。I2S のファームウェア書き込みや 48kHz の扱いが要る |
| **XIAO ESP32S3 ＋ Atomic Voice Base（採用）** | 小さい。Voice Base に ES8311・マイク・アンプ・スピーカーが全部載っている。ジャンパー線だけでつながる。店頭で買える | AEC は「たぶん使える」で、実機では未確認（下記） |

### XIAO ESP32S3 について

- Flash 8MB・Octal PSRAM 8MB。サンプルのアプリ領域は 3MB なので、sdkconfig の Flash サイズを 8MB に直せば収まる
- 使える GPIO は 11 本。Voice Base で 6 本使い、残りは 5 本（D6〜D10）。状態を見せたければ LED を 1 つ足せる
- **Sense 版は不要**。マイクは Voice Base のものを使う。Sense の拡張ボードはピンを取り合うので付けない
- **Wi-Fi のアンテナは箱に入っていて、自分で基板の U.FL コネクタにはめる**。付けないと Wi-Fi が弱い。真上から強く押さず、外すときは片側から持ち上げる。筐体には、金属や手で覆われない位置にアンテナの場所を作る
- ピンヘッダのはんだ付けが要ることが多い
- **電源は USB から入れる**。Voice Base のアンプは 5V で動くが、XIAO の 5V ピンに電気が来るのは USB 給電のときだけ。裏の電池端子に LiPo をつないだだけでは 5V が出ない

### Atomic Voice Base について

- **マイコンは載っていない**。本来は M5Stack の Atom を上に差して使う「台座」。今回は Atom の代わりに XIAO をジャンパー線でつなぐ
- 載っているもの: 音声チップ ES8311、マイク MSM381A3729H9BPC（S/N 比 65dB 以上）、D 級アンプ NS4150B、1W・8Ω の箱入りスピーカー（約 20×14mm）
- 大きさ: **24.0 × 24.0 × 14.1 mm、6.5g**（[M5Stack 公式](https://docs.m5stack.com/ja/atom/Atomic%20Echo%20Base)）
- 音質: 第三者のレビューは見つからなかった。旧製品の Atom Echo は「音量がかなり小さい」というレビューがあった。**音量は控えめかもしれない**。ソフトで上げられる（公式サンプルの `setSpeakerVolume`）

### AEC が使えるかどうか（未確認）

- **使えそうな根拠**: Espressif の ES8311 ドライバに `no_dac_ref` という設定がある（既定は false）。false のとき、2 チャンネルで録音すると右チャンネルにスピーカーへの出力（参照の音）が入る。SDK が使う `codec_board` はこの設定を変えていない
- **やること**: サンプルの `media.c` の AEC の設定を、4 チャンネル（ES7210 用）から 2 チャンネル（マイク＋参照）に直す
- **訂正**: 調査の途中で「xiaozhi-esp32 はこの組み合わせで AEC を使っている」と書いたが、誤りだった。設定ファイルには `AUDIO_INPUT_REFERENCE true` とあるが、実際の `Es8311AudioCodec` は参照を使わない値が固定で入っている
- **実機が届いたら最初に試す**: 植物が話している間に話しかけて、植物の声を拾わないか
- **だめだったとき**: Voice Base はマイク・スピーカーとしてはそのまま使える。サーバー側を `interruption={"enabled": False}` にすればデモは成り立つ（割り込みはできなくなるので、ケーノさんと相談）

### 個別部品で組む案との比較

| | 個別部品（ICS-43434 ＋ MAX98357A ＋ スピーカー） | Atomic Voice Base |
|---|---|---|
| 値段（1 台） | 約 2,960 円 | **約 1,230 円** |
| マイクの性能 | S/N 比 約 65dB、24bit | S/N 比 65dB 以上、ES8311 で 24bit → **ほぼ同じ** |
| アンプ | 最大約 3W（4Ω） | 最大約 3W（4Ω）→ ほぼ同じ |
| スピーカー | 自由に選べる。大きくすれば声がはっきりする | 内蔵の小型。取り替えにくい |
| AEC | **使えない**（参照の音を戻す経路が無い） | **使える見込み**（実機で未確認） |
| 使うピン | 4〜5 本 | 6 本 |
| 組む手間 | 3 部品、はんだ付けあり | ジャンパー線だけ |

音声認識の精度は、マイクの性能がほぼ同じなので変わらない見込みです。精度に効くのはむしろ AEC の有無です。会場がうるさくて音量が足りないときの保険として、MAX98357A と大きめのスピーカーを 1 セット買っておく案があります（#35 の「保険」）。

## 配線（案）

Voice Base 側の信号名は M5Stack のピン配置図で、**買ったあとに実物で確認すること**。

| Voice Base | 役割 | XIAO |
|---|---|---|
| SDA / SCL | 音声チップの設定（I2C） | D4 / D5 |
| SCLK / LRCK | 音のクロック（I2S） | D0 / D1 |
| DSDIN | スピーカーへの音（XIAO → Voice Base） | D2 |
| ASDOUT | マイクの音（Voice Base → XIAO） | D3 |
| 5V / GND | 電源 | 5V / GND |

参考: `codec_board` の `ATOMS3_ECHO_BASE` の設定は「i2c sda 38 / scl 39、i2s bclk 8 / ws 6 / dout 5 / din 7、ES8311、pa なし」。XIAO 用にはピン番号だけ書き換えます。

## まだ確かめていないこと

- 秋葉原のどの店に在庫があるか（店のサイトが読めなかった。行く前に電話で確認）
- ES8311 の参照の音を使った AEC が、LiveKit の SDK で実際に動くか
- Voice Base のスピーカーの音量が会場で足りるか
- XIAO に内蔵アンテナが無いか（無い前提で、必ず付属のアンテナを付ける）

## 参考にした資料

- [livekit/client-sdk-esp32](https://github.com/livekit/client-sdk-esp32)
- [codec_board の対応ボード一覧（board_cfg.txt）](https://github.com/espressif/esp-webrtc-solution/blob/65d13427dd83c37264b6cff966d60af0f84f649c/components/codec_board/board_cfg.txt)
- [M5Stack: Atomic Echo Base](https://docs.m5stack.com/ja/atom/Atomic%20Echo%20Base)
- [M5Stack: AtomS3R AI Chatbot](https://docs.m5stack.com/en/core/AtomS3R-AI%20Chatbot)
- [スイッチサイエンス: XIAO ESP32S3](https://www.switch-science.com/products/8968)・[Atomic Voice Base](https://www.switch-science.com/products/10193)・[ICS-43434](https://www.switch-science.com/products/10051)・[MAX98357A](https://www.switch-science.com/products/2749)
- [ESP-SR の AEC](https://docs.espressif.com/projects/esp-sr/en/latest/esp32s3/acoustic_echo_cancellation/README.html)
- [Seeed: ReSpeaker Lite](https://wiki.seeedstudio.com/ja/xiao_respeaker/)
- [xiaozhi-esp32 の M5Stack ボード設定](https://github.com/78/xiaozhi-esp32/tree/main/main/boards/m5stack)
- [LiveKit: custom ESP32 hardware のブログ](https://livekit.com/blog/esp32-custom-hardware-quickstart)
