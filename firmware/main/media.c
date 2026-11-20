// マイクの音を 20ms ずつ取り出す仕組みと、届いた声を鳴らす仕組み（仕様 §3.2.1・§9 の 2〜4）
//
// 音の流れ:
//
//   マイク → ES8311 → I2S →（AEC）→ media_read_mic_frame() → relay.c が中継へ送る
//   中継 → relay.c の受信 → media_play() → 待ち行列（PSRAM）→ 再生のタスク → I2S → ES8311 → スピーカー
//
// 録音と再生は同じ I2S の線（BCLK・WS）を使うので、両方とも「16kHz・16 ビット・2 チャンネル」で開く。
// 形式をそろえないと、片方の音がおかしくなる（board.c）。
//   - 録音の 2 チャンネル: 左 = マイク、右 = スピーカーに出した音（AEC の参照。board.c の no_dac_ref）
//   - 再生の 2 チャンネル: 届いたモノラルの声を左右に同じものを入れる
//
// エコーキャンセル（AEC）のやり方:
//   Espressif の esp_capture の「AEC つきの音源」（esp_capture_new_audio_aec_src）を使う。
//   中で esp-sr の AEC を動かし、マイクの音から参照の音（植物自身の声）を引いて、モノラル 1 チャンネルにしてくれる。
//   esp-sr の AFE を直接使うより、こちらを選んだ理由:
//     - 前の LiveKit 版（media.c）で同じ設定（channel=2・mic_layout="MR"）がビルドできていて、手直しが少ない
//     - 録音のタスク・AEC の処理の単位（16ms など）と 20ms の差・16kHz への変換を esp_capture が受け持ってくれる
//   esp_capture は本来「音を圧縮して取り出す」部品だが、出口（sink）の形式を PCM にすれば、生の音がそのまま取れる。
//
//   AEC を使わない（CONFIG_FUTAMARU_USE_AEC=n）ときは esp_capture を通さず、ES8311 から直接読んで左（マイク）だけ使う。
//
// AEC を使わないときのエコー対策（半二重。仕様 §9 の 4 の補足）:
//   植物が話している間と、話し終えてから MIC_MUTE_TAIL_MS の間は、マイクの音を無音（全部 0）にする。
//   植物は自分の声を拾わなくなるが、植物が話している途中で割り込むことはできない。
//   2026-11-20 の実機では、AEC を通すと話しかけた人の声まで消えてしまったので、今はこちらが既定。
//   （AEC を通す前のマイクの音と参照の音は正常だった。AEC の中で消えている）

#include "media.h"

#include <string.h>

#include "esp_audio_enc_default.h"
#include "esp_capture.h"
#include "esp_capture_defaults.h"
#include "esp_capture_sink.h"
#include "esp_check.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/stream_buffer.h"
#include "freertos/task.h"

#include "board.h"

static const char *TAG = "media";

// 1 フレームのサンプル数（20ms × 16kHz = 320）
#define FRAME_SAMPLES (MEDIA_FRAME_BYTES / 2)
// 録音・再生の I2S のチャンネル数（左右）
#define I2S_CHANNELS 2

// 再生の待ち行列の大きさ: 2 秒分（64KB）。PSRAM に置く。
// 中継は実時間の速さで声を送ってくるので、普段はほとんどたまらない。ネットワークの揺れを吸収するための余裕。
#define PLAY_BUFFER_BYTES (MEDIA_FRAME_BYTES * 50 * 2)
// 鳴らし始める前にためる量（3 フレーム = 60ms）。少しためてから鳴らすと、途中で途切れにくい
#define PLAY_PREBUFFER_BYTES (MEDIA_FRAME_BYTES * 3)
// ためる量に届かなくても、この回数（× 20ms）待ったら鳴らし始める（短い返事を取り残さないため）
#define PLAY_PREBUFFER_MAX_WAIT 3
// 待ち行列がいっぱいのとき、空くのを待つ最長の時間
#define PLAY_PUSH_TIMEOUT_MS 500
// AEC を使わないとき、植物が話し終えてからもマイクを無音にしておく時間。
// 話し終えた直後も、まだ植物の声がマイクに入ってくるので、少し長めに止める:
//   - I2S の送り出しの待ち行列（6 × 240 サンプル = 約 90ms）に残っている声が、あとから鳴る
//   - マイク側の I2S の待ち行列にも同じくらいの遅れがある
//   - 部屋の響きが 100ms くらい残る
// 合わせて 300ms。長くしすぎると、植物の返事の直後に話した言葉の頭が欠ける
#define MIC_MUTE_TAIL_MS 300

static bool s_use_aec;
// 再生のタスクが最後に声（無音ではない、中継から届いた音）を鳴らした時刻。
// 再生のタスクが書き、マイクのタスクが読むので volatile にする
static volatile TickType_t s_last_voice_tick;
static volatile bool s_voice_played; // 起動してから一度でも声を鳴らしたか
static volatile uint32_t s_mic_muted_frames;

// --- 録音（AEC あり）: esp_capture ---
static esp_capture_handle_t s_capture;
static esp_capture_sink_handle_t s_capture_sink;
// esp_capture から出てくる音の大きさは 20ms ちょうどとは限らないので、ここにためてから 640 バイトずつ切り出す
static uint8_t s_mic_fifo[4096];
static size_t s_mic_fifo_len;

// --- 再生 ---
static StreamBufferHandle_t s_play_buffer;
static StaticStreamBuffer_t s_play_buffer_struct;

// ---------------------------------------------------------------------------
// esp_capture の中で作られるタスクの設定
//
// 名前ごとにスタックの大きさ・優先度・使う CPU を決める（値は LiveKit SDK の system.c と同じ）。
//   AUD_SRC:   録音して AEC を通すタスク。AEC はスタックをたくさん使う
//   buffer_in: AEC 用に録音をためるタスク
//   aenc_0:    出口の形式に変換するタスク（今回は PCM なので変換はほぼしない）
// スタックは PSRAM に置いて、内部 RAM（Wi-Fi・TLS が使う）を空けておく。
// ---------------------------------------------------------------------------

static void capture_scheduler(const char *name, esp_capture_thread_schedule_cfg_t *cfg)
{
    if (strcmp(name, "AUD_SRC") == 0) {
        cfg->stack_size = 40 * 1024;
        cfg->priority = 15;
    } else if (strcmp(name, "buffer_in") == 0) {
        cfg->stack_size = 6 * 1024;
        cfg->priority = 10;
        cfg->core_id = 0;
    } else if (strcmp(name, "aenc_0") == 0) {
        cfg->stack_size = 16 * 1024;
        cfg->priority = 10;
        cfg->core_id = 1;
    }
    cfg->stack_in_ext = true;
}

// ---------------------------------------------------------------------------
// マイク（AEC あり）: esp_capture で AEC を通した音を取り出す
// ---------------------------------------------------------------------------

static bool start_capture_with_aec(void)
{
    esp_codec_dev_handle_t rec = board_get_record_handle();
    ESP_RETURN_ON_FALSE(rec, false, TAG, "no record device");

    // 1. esp_capture の部品の準備（出口の形式 PCM を使えるように、圧縮の部品を登録する）
    esp_audio_enc_register_default();
    esp_capture_set_thread_scheduler(capture_scheduler);

    // 2. AEC つきの音源。
    //    "MR" = 1 チャンネル目がマイク（M）、2 チャンネル目が参照（R: スピーカーに出した音）。
    //    ES8311 の録音の並び（左 = マイク、右 = 参照。board.c の no_dac_ref）に合わせる。
    esp_capture_audio_aec_src_cfg_t src_cfg = {
        .record_handle = rec,
        .channel = I2S_CHANNELS,
        .channel_mask = 0, // 0 = 2 チャンネルとも使う
        .mic_layout = "MR",
    };
    esp_capture_audio_src_if_t *src = esp_capture_new_audio_aec_src(&src_cfg);
    ESP_RETURN_ON_FALSE(src, false, TAG, "failed to create AEC audio source");

    // 3. esp_capture を開く
    esp_capture_cfg_t cfg = {
        .sync_mode = ESP_CAPTURE_SYNC_MODE_AUDIO,
        .audio_src = src,
    };
    esp_capture_open(&cfg, &s_capture);
    ESP_RETURN_ON_FALSE(s_capture, false, TAG, "failed to open capture");

    // 4. 出口（sink）: 16kHz・16 ビット・モノラルの PCM（中継に送る形式そのまま）
    esp_capture_sink_cfg_t sink_cfg = {
        .audio_info = {
            .format_id = ESP_CAPTURE_FMT_ID_PCM,
            .sample_rate = MEDIA_SAMPLE_RATE,
            .channel = 1,
            .bits_per_sample = 16,
        },
    };
    ESP_RETURN_ON_FALSE(esp_capture_sink_setup(s_capture, 0, &sink_cfg, &s_capture_sink) == ESP_CAPTURE_ERR_OK,
                        false, TAG, "failed to setup capture sink");
    esp_capture_sink_enable(s_capture_sink, ESP_CAPTURE_RUN_MODE_ALWAYS);

    // 5. 録音を始める（ここで ES8311 の録音側が 2 チャンネルで開かれる）
    ESP_RETURN_ON_FALSE(esp_capture_start(s_capture) == ESP_CAPTURE_ERR_OK, false, TAG, "failed to start capture");
    return true;
}

/// esp_capture から音を取り出して、640 バイトそろうまで s_mic_fifo にためる
static bool read_mic_frame_with_aec(uint8_t *frame)
{
    while (s_mic_fifo_len < MEDIA_FRAME_BYTES) {
        // 1. 次の音のかたまりを待って受け取る
        esp_capture_stream_frame_t got = {
            .stream_type = ESP_CAPTURE_STREAM_TYPE_AUDIO,
        };
        if (esp_capture_sink_acquire_frame(s_capture_sink, &got, false) != ESP_CAPTURE_ERR_OK) {
            return false;
        }
        // 2. ためる（入りきらない分は捨てる。ふつうは起きない）
        size_t space = sizeof(s_mic_fifo) - s_mic_fifo_len;
        size_t n = (size_t)got.size < space ? (size_t)got.size : space;
        if (n < (size_t)got.size) {
            ESP_LOGW(TAG, "Mic frame too large (%d bytes), dropped %d bytes", got.size, (int)(got.size - n));
        }
        memcpy(s_mic_fifo + s_mic_fifo_len, got.data, n);
        s_mic_fifo_len += n;
        // 3. 受け取ったかたまりを esp_capture に返す（返さないと次が来ない）
        esp_capture_sink_release_frame(s_capture_sink, &got);
    }
    // 4. 先頭の 640 バイトを渡し、残りを前に詰める
    memcpy(frame, s_mic_fifo, MEDIA_FRAME_BYTES);
    s_mic_fifo_len -= MEDIA_FRAME_BYTES;
    memmove(s_mic_fifo, s_mic_fifo + MEDIA_FRAME_BYTES, s_mic_fifo_len);
    return true;
}

// ---------------------------------------------------------------------------
// マイク（AEC なし）: ES8311 から直接読む
// ---------------------------------------------------------------------------

static bool start_capture_plain(void)
{
    esp_codec_dev_handle_t rec = board_get_record_handle();
    ESP_RETURN_ON_FALSE(rec, false, TAG, "no record device");

    // 再生と同じ形式（16kHz・16 ビット・2 チャンネル）で開く。使うのは左（マイク）だけ
    esp_codec_dev_sample_info_t fs = {
        .bits_per_sample = 16,
        .channel = I2S_CHANNELS,
        .sample_rate = MEDIA_SAMPLE_RATE,
    };
    ESP_RETURN_ON_FALSE(esp_codec_dev_open(rec, &fs) == ESP_CODEC_DEV_OK, false, TAG, "failed to open record device");
    return true;
}

static bool read_mic_frame_plain(uint8_t *frame)
{
    // 20ms 分の左右の音（320 サンプル × 2 チャンネル）を読み、左だけを取り出す
    static int16_t stereo[FRAME_SAMPLES * I2S_CHANNELS];
    if (esp_codec_dev_read(board_get_record_handle(), stereo, sizeof(stereo)) != ESP_CODEC_DEV_OK) {
        return false;
    }
    int16_t *mono = (int16_t *)frame;
    for (int i = 0; i < FRAME_SAMPLES; i++) {
        mono[i] = stereo[i * I2S_CHANNELS]; // 左 = マイク
    }
    return true;
}

// ---------------------------------------------------------------------------
// 再生: 待ち行列から 20ms ずつ取り出してスピーカーへ
//
// 声が届いていないあいだも無音を書き続ける。
//   - I2S が止まらないので、次の声がすぐ鳴る
//   - AEC の参照（右チャンネル）も途切れずに届く
// esp_codec_dev_write() は I2S が音を出し終わるまで待つので、このループは自然に 20ms ごとに回る。
// ---------------------------------------------------------------------------

static void playback_task(void *arg)
{
    esp_codec_dev_handle_t play = board_get_playback_handle();
    static uint8_t mono[MEDIA_FRAME_BYTES];
    static int16_t stereo[FRAME_SAMPLES * I2S_CHANNELS];
    bool playing = false; // いま声を鳴らしている途中か
    int wait_count = 0;   // 鳴らし始める前に待った回数

    for (;;) {
        size_t available = xStreamBufferBytesAvailable(s_play_buffer);

        // 1. 鳴らし始めるかを決める（少したまるのを待つ。短い返事は 60ms 待ったら鳴らす）
        if (!playing && available > 0) {
            wait_count++;
            if (available >= PLAY_PREBUFFER_BYTES || wait_count >= PLAY_PREBUFFER_MAX_WAIT) {
                playing = true;
            }
        }

        // 2. 1 フレームそろっていれば取り出す。足りなければ無音（待ち行列が空 = 声が途切れた）
        if (playing && available >= MEDIA_FRAME_BYTES) {
            xStreamBufferReceive(s_play_buffer, mono, MEDIA_FRAME_BYTES, 0);
            // 声を鳴らした時刻を覚えておく（AEC を使わないとき、マイクを止める目安。plant_is_speaking()）
            s_last_voice_tick = xTaskGetTickCount();
            s_voice_played = true;
        } else {
            if (playing) {
                playing = false;
                wait_count = 0;
            }
            memset(mono, 0, sizeof(mono));
        }

        // 3. モノラルを左右に同じものを入れて、スピーカーへ
        const int16_t *in = (const int16_t *)mono;
        for (int i = 0; i < FRAME_SAMPLES; i++) {
            stereo[i * 2] = in[i];
            stereo[i * 2 + 1] = in[i];
        }
        if (esp_codec_dev_write(play, stereo, sizeof(stereo)) != ESP_CODEC_DEV_OK) {
            // 書けなかったとき、すぐにやり直すと CPU を使い切るので 20ms 休む
            vTaskDelay(pdMS_TO_TICKS(20));
        }
    }
}

static bool start_playback(void)
{
    esp_codec_dev_handle_t play = board_get_playback_handle();
    ESP_RETURN_ON_FALSE(play, false, TAG, "no playback device");

    // 1. 待ち行列（中身は PSRAM、管理用の小さな構造体は内部 RAM）
    uint8_t *storage = heap_caps_malloc(PLAY_BUFFER_BYTES + 1, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    ESP_RETURN_ON_FALSE(storage, false, TAG, "no memory for play buffer");
    s_play_buffer = xStreamBufferCreateStatic(PLAY_BUFFER_BYTES, 1, storage, &s_play_buffer_struct);
    ESP_RETURN_ON_FALSE(s_play_buffer, false, TAG, "failed to create play buffer");

    // 2. 再生側の ES8311 を録音と同じ形式（16kHz・16 ビット・2 チャンネル）で開く。
    //    音量は board.c で設定した値（CONFIG_FUTAMARU_SPEAKER_VOLUME）を esp_codec_dev が開くときに入れ直す
    esp_codec_dev_sample_info_t fs = {
        .bits_per_sample = 16,
        .channel = I2S_CHANNELS,
        .sample_rate = MEDIA_SAMPLE_RATE,
    };
    ESP_RETURN_ON_FALSE(esp_codec_dev_open(play, &fs) == ESP_CODEC_DEV_OK, false, TAG,
                        "failed to open playback device");

    // 3. 再生のタスク（音が途切れないように優先度を高めにする）
    BaseType_t ok = xTaskCreatePinnedToCore(playback_task, "playback", 4096, NULL, 12, NULL, 1);
    ESP_RETURN_ON_FALSE(ok == pdPASS, false, TAG, "failed to start playback task");
    return true;
}

// ---------------------------------------------------------------------------
// 外から呼ぶ関数
// ---------------------------------------------------------------------------

bool media_init(bool use_aec)
{
    s_use_aec = use_aec;

    // 再生を先に始める（AEC が動き始めたときに参照の音が流れているように）
    if (!start_playback()) {
        return false;
    }
    bool ok = use_aec ? start_capture_with_aec() : start_capture_plain();
    if (!ok) {
        return false;
    }
    ESP_LOGI(TAG, "Media ready (AEC: %s, %d Hz, %d bytes per frame)", use_aec ? "on" : "off", MEDIA_SAMPLE_RATE,
             MEDIA_FRAME_BYTES);
    return true;
}

/// 植物が話している途中か（話し終えてから MIC_MUTE_TAIL_MS の間も含む）。
///
/// 次のどちらかなら true:
///   - 再生の待ち行列に、これから鳴らす声が残っている
///   - 最後に声を鳴らしてから MIC_MUTE_TAIL_MS たっていない
static bool plant_is_speaking(void)
{
    if (xStreamBufferBytesAvailable(s_play_buffer) > 0) {
        return true;
    }
    // TickType_t の引き算は、時刻の数字が一周しても正しい差になる
    return s_voice_played && (xTaskGetTickCount() - s_last_voice_tick) < pdMS_TO_TICKS(MIC_MUTE_TAIL_MS);
}

bool media_read_mic_frame(uint8_t *frame)
{
    // 1. AEC を使うときは、AEC を通した音をそのまま渡す（植物自身の声は AEC が消す）
    if (s_use_aec) {
        return read_mic_frame_with_aec(frame);
    }
    // 2. AEC を使わないときは、まず読む（読まないと I2S の待ち行列があふれる）
    if (!read_mic_frame_plain(frame)) {
        return false;
    }
    // 3. 植物が話している間は、植物自身の声が入っているので無音にする（半二重）。
    //    フレームを送らないのではなく無音を送るのは、中継とのやりとりの形を変えないため
    //    （中継は無音に近いフレームを LiveKit に流さない。仕様 §3.2.1）
    if (plant_is_speaking()) {
        memset(frame, 0, MEDIA_FRAME_BYTES);
        s_mic_muted_frames++;
    }
    return true;
}

uint32_t media_mic_muted_frames(void)
{
    return s_mic_muted_frames;
}

void media_play(const uint8_t *data, size_t len)
{
    if (s_play_buffer == NULL || len == 0) {
        return;
    }
    // 空くまで少し待つ。それでも入りきらなければ残りを捨てる（受信を長く止めると接続が切れるため）
    size_t sent = xStreamBufferSend(s_play_buffer, data, len, pdMS_TO_TICKS(PLAY_PUSH_TIMEOUT_MS));
    if (sent < len) {
        ESP_LOGW(TAG, "Play buffer full, dropped %d bytes", (int)(len - sent));
    }
}
