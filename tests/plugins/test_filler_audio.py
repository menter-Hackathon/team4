"""つなぎの声の前後の無音を切る処理（agent/plugins/filler_audio.py、#39）のテスト。"""

import array

from livekit import rtc

from agent.plugins.filler_audio import trim_silence

RATE = 24000


def frames_of(samples, size=240):
    """サンプルの並びを、size サンプルずつのフレームにする。"""
    out = []
    for i in range(0, len(samples), size):
        chunk = array.array("h", samples[i : i + size])
        out.append(rtc.AudioFrame(chunk.tobytes(), RATE, 1, len(chunk)))
    return out


def total_samples(frames):
    return sum(f.samples_per_channel for f in frames)


def test_cuts_silence_before_and_after_the_voice_keeping_a_small_margin():
    silence = [0] * int(0.25 * RATE)
    voice = [3000, -3000] * int(0.15 * RATE)  # 0.3 秒の声
    trimmed = trim_silence(frames_of(silence + voice + silence))
    # 声 0.3 秒 ＋ 前後 0.02 秒ずつ
    assert total_samples(trimmed) == len(voice) + 2 * int(0.02 * RATE)
    assert all(f.samples_per_channel <= int(0.01 * RATE) for f in trimmed)
    joined = array.array("h", b"".join(bytes(f.data) for f in trimmed))
    assert max(joined) == 3000 and min(joined) == -3000


def test_all_silent_audio_becomes_empty():
    assert trim_silence(frames_of([0, 5, -5] * 1000)) == []
    assert trim_silence([]) == []
