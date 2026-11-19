"""中継のプロトコルの決まりごと（agent/relay/protocol.py、仕様 §3.2.1）のテスト。"""

import json

import pytest

from agent.relay.bridge import device_token
from agent.relay.protocol import (
    ERROR_INVALID_AUDIO,
    ERROR_INVALID_START,
    FRAME_BYTES,
    FrameChunker,
    ProtocolError,
    error_message,
    is_authorized,
    is_silent,
    parse_start,
    ready_message,
)


def test_frame_is_20ms_of_16khz_mono_pcm():
    assert FRAME_BYTES == 640


def test_authorized_only_with_the_right_bearer_key():
    assert is_authorized("Bearer secret-key", "secret-key")
    assert not is_authorized("Bearer wrong", "secret-key")
    assert not is_authorized("secret-key", "secret-key")  # Bearer が無い
    assert not is_authorized(None, "secret-key")


def test_parse_start_returns_plant_id():
    assert parse_start('{"type":"start","version":1,"plant_id":"a1"}') == "a1"


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        '{"type":"hello","version":1,"plant_id":"a1"}',
        '{"type":"start","version":2,"plant_id":"a1"}',
        '{"type":"start","version":1}',
        '{"type":"start","version":1,"plant_id":"A1"}',  # 大文字は使えない
        '{"type":"start","version":1,"plant_id":"a/1"}',
        '{"type":"start","version":1,"plant_id":""}',
        "[1, 2]",
    ],
)
def test_parse_start_rejects_bad_messages(text):
    with pytest.raises(ProtocolError) as e:
        parse_start(text)
    assert e.value.code == ERROR_INVALID_START


def test_ready_and_error_messages_are_json():
    assert json.loads(ready_message()) == {
        "type": "ready",
        "sample_rate": 16000,
        "frame_bytes": 640,
    }
    assert json.loads(error_message("invalid_audio", "長さが変")) == {
        "type": "error",
        "code": "invalid_audio",
        "message": "長さが変",
    }


def test_chunker_splits_into_640_byte_frames_and_keeps_the_rest():
    chunker = FrameChunker()
    assert list(chunker.push(b"\x01" * 1000)) == [b"\x01" * 640]
    # 残り 360 バイト ＋ 300 バイト = 660 バイト → 640 バイトが 1 つ
    frames = list(chunker.push(b"\x02" * 300))
    assert frames == [b"\x01" * 360 + b"\x02" * 280]
    # 残り 20 バイト ＋ 600 バイト = 620 バイト → まだ 1 フレームに足りない
    assert list(chunker.push(b"\x03" * 600)) == []
    assert list(chunker.push(b"\x04" * 20)) == [b"\x02" * 20 + b"\x03" * 600 + b"\x04" * 20]


def test_chunker_returns_several_frames_from_one_message():
    frames = list(FrameChunker().push(b"\x00\x01" * 960))  # 1920 バイト = 3 フレーム
    assert len(frames) == 3 and all(len(f) == 640 for f in frames)


def test_chunker_rejects_odd_length_audio():
    with pytest.raises(ProtocolError) as e:
        list(FrameChunker().push(b"\x00" * 641))
    assert e.value.code == ERROR_INVALID_AUDIO


def test_tiny_noise_counts_as_silence_but_speech_does_not():
    import array

    assert is_silent(bytes(640))
    # LiveKit から届く振幅 1〜2 のごく小さな音は無音として扱う
    assert is_silent(array.array("h", [2, -2, 1] * 106 + [0, 0]).tobytes())
    assert not is_silent(array.array("h", [0] * 319 + [1000]).tobytes())
    assert not is_silent(array.array("h", [0] * 319 + [-1000]).tobytes())


def test_device_token_joins_the_plant_room_as_the_device():
    from livekit import api

    token = device_token("a1", "key", "secret-that-is-long-enough-0123456789")
    claims = api.TokenVerifier("key", "secret-that-is-long-enough-0123456789").verify(token)
    assert claims.identity == "device-a1"
    assert claims.video.room == "plant-a1"
    assert claims.video.room_join and claims.video.can_publish and claims.video.can_subscribe
