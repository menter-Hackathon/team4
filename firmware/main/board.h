// ボード（XIAO ESP32S3 ＋ Atomic Voice Base）の音まわりの初期化（仕様 §9）
#pragma once

#include <stdbool.h>

#include "esp_codec_dev.h"

#ifdef __cplusplus
extern "C" {
#endif

/// I2C・I2S・アンプ・音声チップ ES8311 を使える状態にする。
///
/// 引数:
///   with_aec_reference: true なら、録音の右チャンネルに「スピーカーに出した音」を入れる
///                       （エコーキャンセル用の参照の音）。false なら右チャンネルは空。
/// 戻り値: 成功なら true。配線の間違いなどで ES8311 が見つからないと false。
bool board_init(bool with_aec_reference);

/// スピーカー（再生）側の ES8311 のハンドル。board_init() の前は NULL。
esp_codec_dev_handle_t board_get_playback_handle(void);

/// マイク（録音）側の ES8311 のハンドル。board_init() の前は NULL。
esp_codec_dev_handle_t board_get_record_handle(void);

#ifdef __cplusplus
}
#endif
