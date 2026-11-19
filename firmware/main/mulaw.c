// μ-law（G.711）の圧縮と展開。サーバー側の agent/relay/mulaw.py と同じ計算（仕様 §3.2.1）
#include "mulaw.h"

#define MULAW_BIAS 0x84  // μ-law の決まりの値（132）。小さい音の段の切れ目をそろえるために足す
#define MULAW_CLIP 32635 // これより大きい音は頭打ちにする（BIAS を足しても 16 ビットに収まるように）

/// 1 サンプルを μ-law の 1 バイトにする。
///
/// 手順:
///   1. 符号を取り出し、大きさ（絶対値）にする
///   2. BIAS を足し、どの段（exponent、0〜7）に入るかを決める
///   3. 段の中の細かい位置（mantissa、4 ビット）を取り出す
///   4. 符号・段・位置を 1 バイトにまとめ、全部のビットを反転する（G.711 の決まり）
static uint8_t encode_sample(int16_t sample)
{
    // 1. 符号と大きさ（-32768 も扱えるよう、int で計算する）
    int value = sample;
    uint8_t sign = 0;
    if (value < 0) {
        sign = 0x80;
        value = -value;
    }
    if (value > MULAW_CLIP) {
        value = MULAW_CLIP;
    }
    // 2. 段を決める: (value >> 7) のいちばん上の 1 のビットの位置（0〜7）
    value += MULAW_BIAS;
    int exponent = 0;
    for (int top = value >> 8; top > 0; top >>= 1) {
        exponent++;
    }
    // 3. 段の中の位置
    int mantissa = (value >> (exponent + 3)) & 0x0F;
    // 4. まとめて反転
    return (uint8_t)~(sign | (exponent << 4) | mantissa);
}

/// μ-law の 1 バイトを 1 サンプルに戻す（encode_sample の逆）
static int16_t decode_byte(uint8_t code)
{
    code = (uint8_t)~code;
    int sign = code & 0x80;
    int exponent = (code >> 4) & 0x07;
    int mantissa = code & 0x0F;
    int magnitude = (((mantissa << 3) + MULAW_BIAS) << exponent) - MULAW_BIAS;
    return (int16_t)(sign ? -magnitude : magnitude);
}

void mulaw_encode(const int16_t *pcm, size_t n, uint8_t *out)
{
    for (size_t i = 0; i < n; i++) {
        out[i] = encode_sample(pcm[i]);
    }
}

void mulaw_decode(const uint8_t *data, size_t n, int16_t *out)
{
    for (size_t i = 0; i < n; i++) {
        out[i] = decode_byte(data[i]);
    }
}
