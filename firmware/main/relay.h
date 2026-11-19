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

/// マイクの音を読むタスクを始める。中継につながるまでは読んで捨て、ready のあとは中継へ送る。
/// media_init() のすぐあと（Wi-Fi につなぐ前）に呼ぶ。読まないでいると AEC の中に音がたまってあふれるため。
void relay_start_mic(void);

/// 中継への接続を受け持つタスクを始める。relay_start_mic() のあと、Wi-Fi につながってから呼ぶ。
/// 接続が失敗・切断されるたびに、5 秒から倍々に（最長 60 秒）間を空けてつなぎ直す。
void relay_start(void);

#ifdef __cplusplus
}
#endif
