// マイクの音を 20ms ずつ取り出す仕組みと、届いた声を鳴らす仕組み（仕様 §3.2.1・§9 の 2〜4）
//
// 音の形式は中継（relay）と同じ「16kHz・16 ビット・モノラル・リトルエンディアンの PCM」。
// 1 フレーム = 20ms = 320 サンプル = 640 バイト。
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/// 1 フレーム（20ms）の音の大きさ（バイト）。中継とやり取りする単位。
#define MEDIA_FRAME_BYTES 640
/// 音のサンプリング周波数（Hz）
#define MEDIA_SAMPLE_RATE 16000

/// 録音と再生の仕組みを作って動かし始める。board_init() のあとに 1 回だけ呼ぶ。
///
/// 再生のタスクが動き始め、届いた声が無いあいだは無音をスピーカーに出し続ける。
///
/// 引数:
///   use_aec: true ならエコーキャンセル（AEC）を通したマイクの音を出す
/// 戻り値: 成功なら true
bool media_init(bool use_aec);

/// マイクの音を 1 フレーム（640 バイト）取り出す。20ms 分たまるまで待つ。
///
/// 1 つのタスクからだけ呼ぶこと（relay.c のマイクのタスク）。
///
/// 引数:
///   frame: 640 バイト以上の置き場所。16kHz・16 ビット・モノラルの PCM が入る
/// 戻り値: 取り出せたら true。録音の仕組みが壊れていたら false
bool media_read_mic_frame(uint8_t *frame);

/// 届いた声（16kHz・16 ビット・モノラルの PCM）を、再生の待ち行列に足す。
///
/// 1 つのタスクからだけ呼ぶこと（WebSocket の受信の処理）。
/// 待ち行列がいっぱいのときは少し待ち、それでも空かなければ入りきらない分を捨てる。
///
/// 引数:
///   data: 音のバイト列
///   len:  バイト数（640 の倍数でなくてもよい）
void media_play(const uint8_t *data, size_t len);

#ifdef __cplusplus
}
#endif
