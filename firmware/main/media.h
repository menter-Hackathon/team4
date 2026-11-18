// マイクの音を送る仕組み（capture）と、届いた声を鳴らす仕組み（render）（仕様 §9 の 2〜4）
#pragma once

#include <stdbool.h>

#include "av_render.h"
#include "esp_capture.h"

#ifdef __cplusplus
extern "C" {
#endif

/// 録音と再生の仕組みを作る。board_init() のあとに呼ぶ。
///
/// 引数:
///   use_aec: true ならエコーキャンセル（AEC）を通した音を送る
/// 戻り値: 成功なら true
bool media_init(bool use_aec);

/// LiveKit のルームにマイクの音を渡すためのハンドル
esp_capture_handle_t media_get_capturer(void);

/// LiveKit のルームから届いた声を鳴らすためのハンドル
av_render_handle_t media_get_renderer(void);

#ifdef __cplusplus
}
#endif
