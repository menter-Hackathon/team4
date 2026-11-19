"""μ-law（G.711）の圧縮と展開（仕様 §3.2.1）。

16 ビットの音 1 サンプルを 8 ビットにする。鉢と中継の間で送る量が半分（32KB/秒 → 16KB/秒）になる。
電話と同じ方式で、声の聞き取り（STT）にはほとんど影響しない。
鉢のファーム（firmware/main/mulaw.c）と同じ計算をする。

Python 3.12 の audioop にも同じ機能があるが、3.13 で消えるので自分で書いている。
"""

from __future__ import annotations

import array

BIAS = 0x84  # μ-law の決まりの値（132）。小さい音の段の切れ目をそろえるために足す
CLIP = 32635  # これより大きい音は、ここで頭打ちにする（BIAS を足しても 16 ビットに収まるように）


def _encode_sample(sample: int) -> int:
    """16 ビットの 1 サンプル（-32768〜32767）を、μ-law の 1 バイトにする。

    手順:
      1. 符号を取り出し、大きさ（絶対値）にする
      2. BIAS を足し、どの段（exponent、0〜7）に入るかを決める
      3. 段の中の細かい位置（mantissa、4 ビット）を取り出す
      4. 符号・段・位置を 1 バイトにまとめ、全部のビットを反転する（G.711 の決まり）
    例: 0 → 0xFF、32767 → 0x80、-32768 → 0x00
    """
    # 1. 符号と大きさ
    sign = 0x80 if sample < 0 else 0
    magnitude = min(-sample if sample < 0 else sample, CLIP)
    # 2. 段を決める（magnitude + BIAS の上のほうのビットの位置）
    magnitude += BIAS
    exponent = max((magnitude >> 7).bit_length() - 1, 0)
    # 3. 段の中の位置
    mantissa = (magnitude >> (exponent + 3)) & 0x0F
    # 4. まとめて反転
    return ~(sign | (exponent << 4) | mantissa) & 0xFF


def _decode_byte(code: int) -> int:
    """μ-law の 1 バイトを、16 ビットの 1 サンプルに戻す（_encode_sample の逆）。例: 0xFF → 0"""
    code = ~code & 0xFF
    sign = code & 0x80
    exponent = (code >> 4) & 0x07
    mantissa = code & 0x0F
    magnitude = (((mantissa << 3) + BIAS) << exponent) - BIAS
    return -magnitude if sign else magnitude


# 毎回計算すると遅いので、起動時に表を作っておく（展開は 256 通り、圧縮は 65536 通り）
_DECODE_TABLE = [_decode_byte(code) for code in range(256)]
_ENCODE_TABLE = bytes(_encode_sample(s) for s in range(-32768, 32768))


def encode(pcm: bytes) -> bytes:
    """16 ビットの PCM（リトルエンディアン）を μ-law にする。長さは半分になる。

    例: encode(bytes(640)) → b"\\xff" * 320（無音 20ms）
    """
    samples = array.array("h", pcm)
    return bytes(_ENCODE_TABLE[s + 32768] for s in samples)


def decode(data: bytes) -> bytes:
    """μ-law を 16 ビットの PCM（リトルエンディアン）に戻す。長さは倍になる。"""
    return array.array("h", [_DECODE_TABLE[b] for b in data]).tobytes()
