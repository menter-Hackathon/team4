"""つなぎの声（相槌）の音を短くする（返事の遅さ #39）。

TTS（AivisSpeech）は、言葉の前後に無音を付けて声を作る。「うん」の作り置きは 0.73〜0.8 秒あったが、
声そのものは 0.3 秒ほどだった（2026-11-19、ohsawa のログ）。LiveKit は声を順番に鳴らすので、
つなぎの声が長いと、そのぶん本当の返事が待たされる。そこで前後の無音を切っておく。
"""

from __future__ import annotations

import array

from livekit import rtc

# これより小さい振幅は無音とみなす（16 ビットの最大は 32767。400 は小さな息の音くらい）
SILENCE_THRESHOLD = 400
# 声の前後に残す無音の長さ（秒）。ぴったり切ると、出だしと終わりがプツッと切れて聞こえるため
MARGIN_SECONDS = 0.02
# 作り直すフレームの長さ（秒）。LiveKit の音声は 10ms ずつ扱うことが多い
FRAME_SECONDS = 0.01


def trim_silence(frames: list[rtc.AudioFrame]) -> list[rtc.AudioFrame]:
    """フレームの並びの、前と後ろの無音を切る。全部が無音なら空のリストを返す。

    手順:
      1. フレームを 1 本の音（16 ビットのサンプルの並び）につなげる
      2. 振幅が SILENCE_THRESHOLD を超える最初と最後のサンプルを探す
      3. その間（前後に MARGIN_SECONDS ずつ足す）を切り出し、10ms ずつのフレームに作り直す
    例: 0.25 秒の無音 ＋ 0.3 秒の声 ＋ 0.25 秒の無音 → 0.34 秒（0.02 ＋ 0.3 ＋ 0.02）
    """
    if not frames:
        return []
    rate, channels = frames[0].sample_rate, frames[0].num_channels

    # 1. つなげる（チャンネルが複数なら、サンプルが交互に並んでいる）
    samples = array.array("h")
    for frame in frames:
        samples.frombytes(bytes(frame.data))

    # 2. 声のある範囲を探す
    loud = [i for i, s in enumerate(samples) if abs(s) > SILENCE_THRESHOLD]
    if not loud:
        return []
    margin = int(MARGIN_SECONDS * rate) * channels
    start = max(loud[0] - margin, 0)
    end = min(loud[-1] + 1 + margin, len(samples))
    start -= start % channels  # チャンネルの途中で切らない
    end -= (end - start) % channels

    # 3. 10ms ずつのフレームに作り直す
    trimmed = samples[start:end]
    step = int(FRAME_SECONDS * rate) * channels
    out: list[rtc.AudioFrame] = []
    for i in range(0, len(trimmed), step):
        chunk = trimmed[i : i + step]
        out.append(
            rtc.AudioFrame(
                data=chunk.tobytes(),
                sample_rate=rate,
                num_channels=channels,
                samples_per_channel=len(chunk) // channels,
            )
        )
    return out
