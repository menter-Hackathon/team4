// マイクの音を送る仕組み（capture）と、届いた声を鳴らす仕組み（render）（仕様 §9 の 2〜4）
//
// LiveKit SDK のサンプル（voice_agent・custom_hardware の media.c）を元にしている。
// 違いはエコーキャンセル（AEC）のチャンネルの数だけ:
//   - サンプルのボードは録音用チップ ES7210 で 4 チャンネル録り、そのうち 2 つ（マイク＋参照）を使う
//   - Voice Base は ES8311 1 つなので 2 チャンネル（左 = マイク、右 = スピーカーに出した音）を全部使う

#include "media.h"

#include "av_render_default.h"
#include "esp_audio_dec_default.h"
#include "esp_audio_enc_default.h"
#include "esp_capture_defaults.h"
#include "esp_capture_sink.h"
#include "esp_check.h"
#include "esp_log.h"

#include "board.h"

static const char *TAG = "media";

static esp_capture_handle_t s_capturer;
static av_render_handle_t s_renderer;

// ---------------------------------------------------------------------------
// マイク → LiveKit
// ---------------------------------------------------------------------------

static bool build_capturer(bool use_aec)
{
    esp_codec_dev_handle_t rec = board_get_record_handle();
    ESP_RETURN_ON_FALSE(rec, false, TAG, "no record device");

    esp_capture_audio_src_if_t *src = NULL;
    if (use_aec) {
        // "MR" = 1 チャンネル目がマイク（M）、2 チャンネル目が参照（R: スピーカーに出した音）。
        // ES8311 の録音の並び（左 = マイク、右 = 参照。board.c の no_dac_ref）に合わせる。
        esp_capture_audio_aec_src_cfg_t cfg = {
            .record_handle = rec,
            .channel = 2,
            .channel_mask = 0, // 0 = 2 チャンネルとも使う
            .mic_layout = "MR",
        };
        src = esp_capture_new_audio_aec_src(&cfg);
    } else {
        // AEC なし: マイクの音をそのまま送る（植物の声もマイクに入るので、サーバー側で割り込みを止める）
        esp_capture_audio_dev_src_cfg_t cfg = {
            .record_handle = rec,
        };
        src = esp_capture_new_audio_dev_src(&cfg);
    }
    ESP_RETURN_ON_FALSE(src, false, TAG, "failed to create audio source");

    esp_capture_cfg_t cfg = {
        .sync_mode = ESP_CAPTURE_SYNC_MODE_AUDIO,
        .audio_src = src,
    };
    esp_capture_open(&cfg, &s_capturer);
    ESP_RETURN_ON_FALSE(s_capturer, false, TAG, "failed to open capture");
    return true;
}

// ---------------------------------------------------------------------------
// LiveKit → スピーカー
// ---------------------------------------------------------------------------

static bool build_renderer(void)
{
    esp_codec_dev_handle_t play = board_get_playback_handle();
    ESP_RETURN_ON_FALSE(play, false, TAG, "no playback device");

    i2s_render_cfg_t i2s_cfg = {
        .play_handle = play,
    };
    audio_render_handle_t audio_render = av_render_alloc_i2s_render(&i2s_cfg);
    ESP_RETURN_ON_FALSE(audio_render, false, TAG, "failed to create I2S renderer");

    // バッファの大きさは SDK のサンプルと同じ
    av_render_cfg_t render_cfg = {
        .audio_render = audio_render,
        .audio_raw_fifo_size = 8 * 4096,
        .audio_render_fifo_size = 100 * 1024,
        .allow_drop_data = false,
    };
    s_renderer = av_render_open(&render_cfg);
    ESP_RETURN_ON_FALSE(s_renderer, false, TAG, "failed to open renderer");

    // 再生の形式を録音と同じ 16kHz・16 ビット・2 チャンネルに固定する。
    // 録音と再生で I2S の線を共有しているので、形式がずれると片方の音がおかしくなる（board.c）。
    av_render_audio_frame_info_t frame_info = {
        .sample_rate = 16000,
        .channel = 2,
        .bits_per_sample = 16,
    };
    av_render_set_fixed_frame_info(s_renderer, &frame_info);
    return true;
}

// ---------------------------------------------------------------------------
// 外から呼ぶ関数
// ---------------------------------------------------------------------------

bool media_init(bool use_aec)
{
    // Opus などの音の圧縮・展開の部品を登録する（LiveKit は Opus で音を送受信する）
    esp_audio_enc_register_default();
    esp_audio_dec_register_default();

    if (!build_capturer(use_aec)) {
        return false;
    }
    if (!build_renderer()) {
        return false;
    }
    ESP_LOGI(TAG, "Media ready (AEC: %s)", use_aec ? "on" : "off");
    return true;
}

esp_capture_handle_t media_get_capturer(void)
{
    return s_capturer;
}

av_render_handle_t media_get_renderer(void)
{
    return s_renderer;
}
