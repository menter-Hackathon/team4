// サーバーの中継（relay）と WebSocket でつながり、音をやり取りする（仕様 §3.2.1）
#pragma once

#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

/// plant_id が中継の決まり（英小文字・数字・_・- だけで 1〜32 文字）に合っているかを調べる。
///
/// 例: "a1" → true、"A1" → false、"" → false
bool relay_plant_id_is_valid(const char *plant_id);

/// 中継への接続を受け持つタスクと、マイクの音を送るタスクを始める。
/// Wi-Fi につながり、media_init() が終わってから呼ぶ。
/// 接続が失敗・切断されるたびに、5 秒から倍々に（最長 60 秒）間を空けてつなぎ直す。
void relay_start(void);

#ifdef __cplusplus
}
#endif
