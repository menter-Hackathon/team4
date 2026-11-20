from scripts.mic_peak_monitor import describe

STATS = "I (14338) relay: Mic frames sent: 500 (muted {muted}), mic peak: {peak}, voice received: 0 bytes"


def test_mic_peak_line_shows_peak_and_muted() -> None:
    assert describe(STATS.format(muted=12, peak=148)) == "mic peak:    148  muted: 12"


def test_zero_peak_is_marked_as_silent() -> None:
    text = describe(STATS.format(muted=0, peak=0))
    assert text is not None
    assert "無音" in text


def test_near_max_peak_is_marked_as_noise() -> None:
    text = describe(STATS.format(muted=0, peak=32767))
    assert text is not None
    assert "雑音" in text


def test_loud_voice_below_noise_level_has_no_mark() -> None:
    # 話しかけたときの大きさ（数千）は、雑音とみなさない
    assert describe(STATS.format(muted=0, peak=7500)) == "mic peak:   7500  muted: 0"


def test_log_without_muted_shows_dash() -> None:
    # 2026-11-20 より前のファームウェアは muted を出さない
    line = "I (24338) relay: Mic frames sent: 1000, mic peak: 90, voice received: 0 bytes"
    assert describe(line) == "mic peak:     90  muted: -"


def test_status_lines_are_shown() -> None:
    line = "E (3635) futamaru: Stopped: audio board not ready (check wiring)\r\n"
    assert describe(line) == "  E (3635) futamaru: Stopped: audio board not ready (check wiring)"


def test_other_lines_are_hidden() -> None:
    assert describe("I (869) wifi:connected with iPhone, aid = 25, channel 6\r\n") is None
    assert describe("") is None
