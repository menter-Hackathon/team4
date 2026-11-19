import pytest

from agent.plant.stt_noise import is_stt_noise


@pytest.mark.parametrize(
    "text",
    [
        "ごめん",
        "ごめん。",
        "ゴメン",
        "ご視聴ありがとうございました",
        "ご視聴ありがとうございました。",
        "チャンネル登録よろしくお願いします",
    ],
)
def test_whole_utterance_hallucinations_are_noise(text):
    assert is_stt_noise(text)


@pytest.mark.parametrize("text", ["야호, 참다요.", "ちゃむだ요?", "잠을 자요.", "ﾡ"])
def test_text_with_hangul_is_noise(text):
    assert is_stt_noise(text)


@pytest.mark.parametrize(
    "text",
    [
        "ごめん、水忘れてた",
        "ありがとう",
        "ふたまる今日ね31食べたんだ",
        "ちゃむだよ",
        "",
    ],
)
def test_normal_speech_is_not_noise(text):
    assert not is_stt_noise(text)
