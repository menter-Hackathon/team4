// ボード（Seeed XIAO ESP32S3 ＋ M5Stack Atomic Voice Base）の音まわりの初期化（仕様 §9）
//
// Atomic Voice Base（旧名 Atomic Echo Base）にはマイコンが載っていないので、
// 本来 M5Stack の Atom を差す所に、XIAO をジャンパー線でつなぐ。
// Voice Base の上に載っている部品のうち、XIAO から操作するのは次の 2 つ。
//
//   - 音声チップ ES8311（I2C アドレス 0x18）: マイクの音を数字にし、数字をスピーカーの音に戻す
//   - IO エキスパンダ PI4IOE5V6408（I2C アドレス 0x43）: スピーカーのアンプ（NS4150B）の ON/OFF
//
// 手順（board_init()）:
//   1. I2C を始める（ES8311 と PI4IOE5V6408 の設定に使う）
//   2. I2C につながっている部品を探してログに出す（配線の確認用）
//   3. アンプを ON にする（PI4IOE5V6408）
//   4. I2S を始める（音そのものを送り受けする線）
//   5. ES8311 を「録音と再生の両方」で使えるようにする
//
// 参考にしたもの:
//   - LiveKit ESP32 SDK のサンプル custom_hardware（board.c）: I2C・I2S・ES8311 の初期化の流れ
//   - esp-webrtc-solution の codec_board（ATOMS3_ECHO_BASE の設定）: Voice Base は MCLK 線なし
//   - M5Stack の M5Atomic-EchoBase ライブラリ（M5EchoBase.cpp）: PI4IOE5V6408 でアンプを ON にする手順

#include "board.h"

#include "driver/gpio.h"
#include "driver/i2c_master.h"
#include "driver/i2s_std.h"
#include "esp_check.h"
#include "esp_codec_dev_defaults.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "sdkconfig.h"

static const char *TAG = "board";

// ---------------------------------------------------------------------------
// ピンの割り当て（docs/hardware/parts-selection.md の「配線」）
//
//   Voice Base | 役割                              | XIAO の印字 | GPIO
//   -----------+-----------------------------------+------------+------
//   SDA        | I2C のデータ                       | D4         | 5
//   SCL        | I2C のクロック                     | D5         | 6
//   SCLK       | I2S のビットクロック（BCLK）         | D0         | 1
//   LRCK       | I2S の左右の切り替え（WS）           | D1         | 2
//   DSDIN      | スピーカーへの音（XIAO → Voice Base）| D2         | 3
//   ASDOUT     | マイクの音（Voice Base → XIAO）      | D3         | 4
//   5V / GND   | 電源                              | 5V / GND   | -
//
// 配線を変えたときは、ここの GPIO 番号だけ直せばよい。
// XIAO の印字（D0 など）と GPIO 番号は違うので注意（D0 = GPIO1）。
// ---------------------------------------------------------------------------
#define PIN_I2C_SDA GPIO_NUM_5  // D4
#define PIN_I2C_SCL GPIO_NUM_6  // D5
#define PIN_I2S_BCLK GPIO_NUM_1 // D0（Voice Base の SCLK）
#define PIN_I2S_WS GPIO_NUM_2   // D1（Voice Base の LRCK）
#define PIN_I2S_DOUT GPIO_NUM_3 // D2（Voice Base の DSDIN）
#define PIN_I2S_DIN GPIO_NUM_4  // D3（Voice Base の ASDOUT）

// I2C のアドレス（7 ビット）
#define ADDR_ES8311 0x18
#define ADDR_PI4IOE 0x43

// PI4IOE5V6408 のレジスタ（M5EchoBase.cpp と同じ値）
#define PI4IOE_REG_IO_DIR 0x03    // 各ピンを入力にするか出力にするか
#define PI4IOE_REG_IO_OUT 0x05    // 出力ピンの High / Low
#define PI4IOE_REG_IO_PP 0x07     // 出力の形式
#define PI4IOE_REG_IO_PULLUP 0x0D // プルアップの有効化

// 音の形式。LiveKit SDK のサンプルと同じく 16kHz・16 ビット・2 チャンネル。
// 録音と再生で I2S の線（BCLK・WS）を共有するので、両方を同じ形式にそろえる。
#define AUDIO_SAMPLE_RATE 16000

static i2c_master_bus_handle_t s_i2c_bus;
static i2s_chan_handle_t s_i2s_tx;
static i2s_chan_handle_t s_i2s_rx;
static esp_codec_dev_handle_t s_play_dev;
static esp_codec_dev_handle_t s_rec_dev;

// ---------------------------------------------------------------------------
// 1. I2C
// ---------------------------------------------------------------------------

static esp_err_t init_i2c(void)
{
    i2c_master_bus_config_t cfg = {
        .clk_source = I2C_CLK_SRC_DEFAULT,
        .i2c_port = I2C_NUM_0,
        .scl_io_num = PIN_I2C_SCL,
        .sda_io_num = PIN_I2C_SDA,
        .glitch_ignore_cnt = 7,
        // Voice Base 側にもプルアップ抵抗があるはずだが、念のため内部のものも使う
        .flags.enable_internal_pullup = true,
    };
    return i2c_new_master_bus(&cfg, &s_i2c_bus);
}

// ---------------------------------------------------------------------------
// 2. I2C の部品探し（配線の確認用）
//
// 正しく配線できていれば「0x18（ES8311）」と「0x43（PI4IOE5V6408）」が見つかる。
// 何も見つからないときは、SDA と SCL の入れ違い・GND の付け忘れ・5V が来ていないことを疑う。
// ---------------------------------------------------------------------------

static bool i2c_has_device(uint8_t addr)
{
    return i2c_master_probe(s_i2c_bus, addr, 50) == ESP_OK;
}

/// SDA と SCL を入力にして、内部のプルアップかプルダウンを付けたときの値を読む。
static void read_i2c_lines(bool pull_up, int *sda, int *scl)
{
    gpio_config_t cfg = {
        .pin_bit_mask = (1ULL << PIN_I2C_SDA) | (1ULL << PIN_I2C_SCL),
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = pull_up ? GPIO_PULLUP_ENABLE : GPIO_PULLUP_DISABLE,
        .pull_down_en = pull_up ? GPIO_PULLDOWN_DISABLE : GPIO_PULLDOWN_ENABLE,
    };
    gpio_config(&cfg);
    vTaskDelay(pdMS_TO_TICKS(5)); // 電圧が落ち着くのを少し待つ
    *sda = gpio_get_level(PIN_I2C_SDA);
    *scl = gpio_get_level(PIN_I2C_SCL);
}

/// I2C を始める前に、SDA と SCL の線の状態を調べてログに出す（配線の確認用）。
///
/// 調べ方:
///   1. XIAO の内部のプルダウン（弱く Low に引く）を付けて読む。
///      Voice Base に電源が来ていて線がつながっていれば、Voice Base のプルアップ抵抗（強い）が勝って 1 になる。
///      0 なら、線が Voice Base まで届いていないか、Voice Base に電源（3V3）が来ていない。
///   2. 内部のプルアップを付けて読む。0 なら、線が GND などに挿さって Low に張り付いている。
/// 部品探しが全部タイムアウトしたとき（実機で起きた）、原因を絞るために足した。
static void i2c_check_lines(void)
{
    int sda_down, scl_down, sda_up, scl_up;
    read_i2c_lines(false, &sda_down, &scl_down);
    read_i2c_lines(true, &sda_up, &scl_up);
    ESP_LOGI(TAG, "I2C lines with pull-down: SDA(D4)=%d SCL(D5)=%d / with pull-up: SDA=%d SCL=%d (all should be 1)",
             sda_down, scl_down, sda_up, scl_up);
    if (sda_up == 0 || scl_up == 0) {
        ESP_LOGW(TAG, "An I2C line is stuck low. Check that G38->D4 and G39->D5 are not on GND");
    } else if (sda_down == 0 || scl_down == 0) {
        ESP_LOGW(TAG, "No pull-up from the Voice Base. Check the 3V3/5V/GND wires and that G38->D4, G39->D5");
    }
}

/// 0x08〜0x77 のアドレスを順に呼んで、返事をした部品をログに出す。
///
/// 戻り値: ES8311（0x18）が見つかれば true。
static bool i2c_scan(void)
{
    int found = 0;
    bool es8311_found = false;
    for (uint8_t addr = 0x08; addr < 0x78; addr++) {
        if (i2c_has_device(addr)) {
            ESP_LOGI(TAG, "I2C device found at 0x%02X", addr);
            found++;
            if (addr == ADDR_ES8311) {
                es8311_found = true;
            }
        }
    }
    ESP_LOGI(TAG, "I2C scan done: %d device(s) (expect 0x18 ES8311 and 0x43 PI4IOE5V6408)", found);
    return es8311_found;
}

// ---------------------------------------------------------------------------
// 3. アンプを ON にする（PI4IOE5V6408）
//
// Voice Base のアンプ NS4150B の ON/OFF は、ESP32 のピンではなく I2C の IO エキスパンダにつながっている。
// ここを ON にしないと、ES8311 が音を出してもスピーカーは鳴らない。
// 手順と値は M5Stack 公式ライブラリ（M5EchoBase::pi4ioe_init と setMute(false)）に合わせた。
// ---------------------------------------------------------------------------

static esp_err_t pi4ioe_write(i2c_master_dev_handle_t dev, uint8_t reg, uint8_t value)
{
    uint8_t buf[2] = {reg, value};
    return i2c_master_transmit(dev, buf, sizeof(buf), 100);
}

static esp_err_t enable_speaker_amp(void)
{
    if (!i2c_has_device(ADDR_PI4IOE)) {
        // エキスパンダが無い版の基板の可能性もあるので、止めずに続ける（音が出なければここを疑う）
        ESP_LOGW(TAG, "PI4IOE5V6408 (0x43) not found; speaker amp may stay off");
        return ESP_OK;
    }

    i2c_device_config_t cfg = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address = ADDR_PI4IOE,
        .scl_speed_hz = 100000,
    };
    i2c_master_dev_handle_t dev;
    ESP_RETURN_ON_ERROR(i2c_master_bus_add_device(s_i2c_bus, &cfg, &dev), TAG, "add PI4IOE failed");

    esp_err_t err = ESP_OK;
    err |= pi4ioe_write(dev, PI4IOE_REG_IO_PP, 0x00);     // 出力の形式（公式ライブラリと同じ値）
    err |= pi4ioe_write(dev, PI4IOE_REG_IO_PULLUP, 0xFF); // プルアップを有効にする
    err |= pi4ioe_write(dev, PI4IOE_REG_IO_DIR, 0x6F);    // アンプにつながるピンを出力にする
    err |= pi4ioe_write(dev, PI4IOE_REG_IO_OUT, 0xFF);    // High にしてアンプを ON（ミュート解除）
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "Failed to configure PI4IOE5V6408; speaker amp may stay off");
    } else {
        ESP_LOGI(TAG, "Speaker amp enabled");
    }
    return ESP_OK;
}

// ---------------------------------------------------------------------------
// 4. I2S
//
// 録音（RX）と再生（TX）を同じ I2S ポートで行う（ES8311 1 つで両方をするため）。
// Voice Base には MCLK の線が無いので、MCLK は使わない（ES8311 は BCLK から内部のクロックを作る）。
// ここでの形式は仮のもので、録音・再生を始めるときに esp_codec_dev が設定し直す。
// ---------------------------------------------------------------------------

static esp_err_t init_i2s(void)
{
    i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
    chan_cfg.auto_clear = true; // 再生する音が途切れたときは無音を出す（雑音を出さない）
    ESP_RETURN_ON_ERROR(i2s_new_channel(&chan_cfg, &s_i2s_tx, &s_i2s_rx), TAG, "i2s_new_channel failed");

    i2s_std_config_t std_cfg = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(AUDIO_SAMPLE_RATE),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_STEREO),
        .gpio_cfg = {
            .mclk = I2S_GPIO_UNUSED,
            .bclk = PIN_I2S_BCLK,
            .ws = PIN_I2S_WS,
            .dout = PIN_I2S_DOUT,
            .din = PIN_I2S_DIN,
        },
    };
    ESP_RETURN_ON_ERROR(i2s_channel_init_std_mode(s_i2s_tx, &std_cfg), TAG, "init I2S TX failed");
    ESP_RETURN_ON_ERROR(i2s_channel_init_std_mode(s_i2s_rx, &std_cfg), TAG, "init I2S RX failed");

    // ES8311 はクロックが来ていないと設定を受け付けないことがあるので、先に動かしておく
    i2s_channel_enable(s_i2s_tx);
    i2s_channel_enable(s_i2s_rx);
    return ESP_OK;
}

// ---------------------------------------------------------------------------
// 5. ES8311
//
// 1 つの ES8311 を「録音と再生の両方（BOTH）」で動かし、
// esp_codec_dev のハンドルを再生用と録音用の 2 つ作る（esp-webrtc-solution の codec_board と同じやり方）。
//
// エコーキャンセル（AEC）のための参照の音:
//   no_dac_ref = false にすると、2 チャンネルで録音したとき
//   「左 = マイクの音、右 = スピーカーに出した音」になる（ES8311 のドライバの機能）。
//   AEC はこの 2 つを引き算して、植物自身の声を消す（media.c）。
// ---------------------------------------------------------------------------

static esp_err_t init_es8311(bool with_aec_reference)
{
    if (!i2c_has_device(ADDR_ES8311)) {
        ESP_LOGE(TAG, "ES8311 (0x18) not found. Check SDA/SCL, 5V and GND wiring");
        return ESP_ERR_NOT_FOUND;
    }

    // ES8311 の設定に使う I2C（esp_codec_dev は 8 ビット形式のアドレスを受け取る: 0x18 << 1 = 0x30）
    audio_codec_i2c_cfg_t i2c_cfg = {
        .port = I2C_NUM_0,
        .addr = ES8311_CODEC_DEFAULT_ADDR,
        .bus_handle = s_i2c_bus,
    };
    const audio_codec_ctrl_if_t *ctrl_if = audio_codec_new_i2c_ctrl(&i2c_cfg);
    ESP_RETURN_ON_FALSE(ctrl_if, ESP_FAIL, TAG, "ES8311 I2C ctrl failed");

    const audio_codec_gpio_if_t *gpio_if = audio_codec_new_gpio();
    ESP_RETURN_ON_FALSE(gpio_if, ESP_FAIL, TAG, "codec GPIO if failed");

    es8311_codec_cfg_t es_cfg = {
        .ctrl_if = ctrl_if,
        .gpio_if = gpio_if,
        .codec_mode = ESP_CODEC_DEV_WORK_MODE_BOTH,
        .pa_pin = -1,     // アンプは PI4IOE5V6408 で ON にするので、ここでは使わない
        .use_mclk = false, // MCLK の線が無い
        .hw_gain = {.pa_gain = 6.0}, // codec_board の ATOMS3_ECHO_BASE と同じ値（音量の計算に使う）
        .no_dac_ref = !with_aec_reference,
    };
    const audio_codec_if_t *codec_if = es8311_codec_new(&es_cfg);
    ESP_RETURN_ON_FALSE(codec_if, ESP_FAIL, TAG, "ES8311 codec init failed");

    audio_codec_i2s_cfg_t i2s_cfg = {
        .port = I2S_NUM_0,
        .rx_handle = s_i2s_rx,
        .tx_handle = s_i2s_tx,
    };
    const audio_codec_data_if_t *data_if = audio_codec_new_i2s_data(&i2s_cfg);
    ESP_RETURN_ON_FALSE(data_if, ESP_FAIL, TAG, "I2S data if failed");

    // 再生用
    esp_codec_dev_cfg_t play_cfg = {
        .dev_type = ESP_CODEC_DEV_TYPE_OUT,
        .codec_if = codec_if,
        .data_if = data_if,
    };
    s_play_dev = esp_codec_dev_new(&play_cfg);
    ESP_RETURN_ON_FALSE(s_play_dev, ESP_FAIL, TAG, "playback dev failed");

    // 録音用（同じ ES8311 を使う）
    esp_codec_dev_cfg_t rec_cfg = {
        .dev_type = ESP_CODEC_DEV_TYPE_IN,
        .codec_if = codec_if,
        .data_if = data_if,
    };
    s_rec_dev = esp_codec_dev_new(&rec_cfg);
    ESP_RETURN_ON_FALSE(s_rec_dev, ESP_FAIL, TAG, "record dev failed");

    esp_codec_dev_set_out_vol(s_play_dev, CONFIG_FUTAMARU_SPEAKER_VOLUME);
    esp_codec_dev_set_in_gain(s_rec_dev, (float)CONFIG_FUTAMARU_MIC_GAIN_DB);
    return ESP_OK;
}

// ---------------------------------------------------------------------------
// 外から呼ぶ関数
// ---------------------------------------------------------------------------

bool board_init(bool with_aec_reference)
{
    ESP_LOGI(TAG, "Init XIAO ESP32S3 + Atomic Voice Base (AEC reference: %s)",
             with_aec_reference ? "on" : "off");

    // 1. I2C の線の状態を見てから、I2C を始めて部品を探す
    i2c_check_lines();
    if (init_i2c() != ESP_OK) {
        ESP_LOGE(TAG, "I2C init failed");
        return false;
    }
    // ES8311 が見つからないまま先へ進むと、ES8311 への書き込みで止まってしまう（実機で起きた）。
    // ここで失敗にして、main.c でエラーの LED を付けて止める。
    if (!i2c_scan()) {
        ESP_LOGE(TAG, "ES8311 (0x18) not found. Check SDA/SCL, 5V and GND wiring");
        return false;
    }
    // 2. アンプ → I2S → ES8311 の順に動かす
    enable_speaker_amp();
    if (init_i2s() != ESP_OK) {
        ESP_LOGE(TAG, "I2S init failed");
        return false;
    }
    if (init_es8311(with_aec_reference) != ESP_OK) {
        return false;
    }
    ESP_LOGI(TAG, "Audio ready (ES8311: speaker volume %d, mic gain %d dB)",
             CONFIG_FUTAMARU_SPEAKER_VOLUME, CONFIG_FUTAMARU_MIC_GAIN_DB);
    return true;
}

esp_codec_dev_handle_t board_get_playback_handle(void)
{
    return s_play_dev;
}

esp_codec_dev_handle_t board_get_record_handle(void)
{
    return s_rec_dev;
}
