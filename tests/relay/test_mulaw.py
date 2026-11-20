"""μ-law の圧縮と展開（agent/relay/mulaw.py）のテスト。"""

import array
import math

from agent.relay import mulaw


def test_silence_and_extremes_use_the_standard_codes():
    assert mulaw.encode(bytes(2)) == b"\xff"  # 無音は 0xFF
    assert mulaw.encode(array.array("h", [32767, -32768]).tobytes()) == b"\x80\x00"
    assert mulaw.decode(b"\xff") == bytes(2)


def test_compressed_audio_is_half_the_size():
    pcm = bytes(640)
    assert len(mulaw.encode(pcm)) == 320
    assert len(mulaw.decode(mulaw.encode(pcm))) == 640


def test_round_trip_keeps_speech_close_to_the_original():
    # 振幅 12000 の音を圧縮して戻すと、誤差は 1 段（大きい音ほど粗い）以内に収まる
    original = array.array("h", [int(12000 * math.sin(i / 7)) for i in range(320)])
    restored = array.array("h", mulaw.decode(mulaw.encode(original.tobytes())))
    assert max(abs(a - b) for a, b in zip(original, restored, strict=True)) <= 512


def test_decode_matches_the_g711_table_for_every_code():
    # 展開は G.711 の表どおり（Python 3.12 の audioop.ulaw2lin と同じ値）。端の値で確かめる
    values = array.array("h", mulaw.decode(bytes(range(256))))
    assert values[0x00] == -32124 and values[0x80] == 32124
    assert values[0x7F] == 0 and values[0xFF] == 0
