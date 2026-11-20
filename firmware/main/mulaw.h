// μ-law（G.711）の圧縮と展開（仕様 §3.2.1 の codec "mulaw"）
//
// 16 ビットの音 1 サンプルを 8 ビットにする。中継へ送る量が半分（32KB/秒 → 16KB/秒）になる。
// サーバー側の agent/relay/mulaw.py と同じ計算をする。
#pragma once

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/// 16 ビットの PCM を n サンプル、μ-law にする（out は n バイト）。例: 0 → 0xFF
void mulaw_encode(const int16_t *pcm, size_t n, uint8_t *out);

/// μ-law を n バイト、16 ビットの PCM に戻す（out は n サンプル）。例: 0xFF → 0
void mulaw_decode(const uint8_t *data, size_t n, int16_t *out);

#ifdef __cplusplus
}
#endif
