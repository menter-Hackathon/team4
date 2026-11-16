from datetime import datetime, timedelta

import pytest

from agent.plant.wake import WakeController, detect_wake, normalize

DEFAULT = ("ふたまる", "二丸", "ふた丸", "2丸")
T0 = datetime(2026, 11, 15, 10, 0)


def s(n):
    return timedelta(seconds=n)


def test_normalize():
    assert normalize("フタマル、 おはよう！") == "ふたまるおはよう"
    assert normalize("２丸") == "2丸"
    assert normalize("ﾌﾀﾏﾙ") == "ふたまる"


@pytest.mark.parametrize(
    "text",
    [
        "ふたまる",
        "フタマル",
        "ふた丸",
        "二丸",
        "2丸",
        "２丸",
        "ねえ、フタマル！",
        "ふ た ま る",
        "ふたまる。",
    ],
)
def test_detects_variants(text):
    assert detect_wake(text, DEFAULT) is not None


@pytest.mark.parametrize("text", ["おはよう", "ふたりまるい", "", "   "])
def test_no_wake(text):
    assert detect_wake(text, DEFAULT) is None


@pytest.mark.parametrize(
    "text,rest",
    [
        ("ふたまる、今日ね、テストだった", "今日ね、テストだった"),
        ("フタマル。", ""),
        ("ふたまる、ね", ""),
        ("ねえふたまる カレー食べた", "カレー食べた"),
        ("ふたまる！！ カレー", "カレー"),
        ("ふ た ま る、元気？", "元気？"),
        # 敬称と、繰り返したウェイクワードは発話に含めない（#6）
        ("ふたまるちゃん", ""),
        ("フタマルちゃん！", ""),
        ("ふたまるくん元気？", "元気？"),
        ("ふたまるさん、カレー食べた", "カレー食べた"),
        ("ふたまるふたまる", ""),
        ("ふたまる、ふたまる、聞いて", "聞いて"),
        ("ふたまるちゃん、ふたまるちゃん、ただいま", "ただいま"),
        # 区切りの後ろにある「さん」「くん」などは敬称ではない
        ("ふたまる、さんぽ行こう", "さんぽ行こう"),
        ("ふたまる くんせい食べた", "くんせい食べた"),
    ],
)
def test_rest(text, rest):
    assert detect_wake(text, DEFAULT).rest == rest


def test_custom_wake_words_are_normalized():
    assert detect_wake("ふーちゃん", ("フーチャン",)) is not None


def test_sleeps_after_60s_of_silence():
    c = WakeController()
    assert c.state == "SLEEPING" and c.tick(T0) == "none"
    c.wake(T0)
    assert c.state == "AWAKE"
    assert c.tick(T0 + s(59)) == "none" and c.tick(T0 + s(60)) == "sleep"
    assert c.state == "SLEEPING" and c.tick(T0 + s(61)) == "none"


def test_custom_sleep_after():
    c = WakeController(sleep_after=30)
    c.wake(T0)
    assert c.tick(T0 + s(30)) == "sleep"


def test_plant_busy_pauses_and_restarts():
    c = WakeController()
    c.wake(T0)
    c.set_plant_busy(True, T0 + s(10))
    assert c.tick(T0 + s(90)) == "none"
    c.set_plant_busy(False, T0 + s(100))
    assert c.tick(T0 + s(159)) == "none" and c.tick(T0 + s(160)) == "sleep"


def test_user_speaking_long_does_not_sleep():
    c = WakeController()
    c.wake(T0)
    c.set_user_speaking(True, T0 + s(5))
    assert c.tick(T0 + s(120)) == "none"
    c.set_user_speaking(False, T0 + s(130))
    assert c.tick(T0 + s(189)) == "none" and c.tick(T0 + s(190)) == "sleep"


def test_user_activity_resets_timer():
    c = WakeController()
    c.wake(T0)
    c.on_user_activity(T0 + s(50))
    assert c.tick(T0 + s(100)) == "none" and c.tick(T0 + s(110)) == "sleep"


def test_nudge_once_per_silence():
    c = WakeController()
    c.wake(T0)
    c.set_nudge_delay(25)
    assert (
        c.tick(T0 + s(24)) == "none"
        and c.tick(T0 + s(25)) == "nudge"
        and c.tick(T0 + s(26)) == "none"
    )
    c.on_user_activity(T0 + s(30))
    assert c.tick(T0 + s(55)) == "nudge"


def test_nudge_flag_resets_on_wake():
    c = WakeController()
    c.wake(T0)
    c.set_nudge_delay(25)
    assert c.tick(T0 + s(25)) == "nudge"
    c.sleep()
    c.wake(T0 + s(100))
    assert c.tick(T0 + s(125)) == "nudge"


def test_no_nudge_when_none():
    c = WakeController()
    c.wake(T0)
    c.set_nudge_delay(None)
    assert c.tick(T0 + s(40)) == "none"
