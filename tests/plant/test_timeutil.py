from datetime import UTC, datetime

import pytest

from agent.plant.timeutil import JST, iso, jst_now, parse_iso, time_band


def test_iso_is_jst_seconds():
    dt = datetime(2026, 11, 15, 1, 0, 0, 123456, tzinfo=UTC)
    assert iso(dt) == "2026-11-15T10:00:00+09:00"
    assert parse_iso("2026-11-15T10:00:00+09:00") == dt.replace(microsecond=0)


def test_jst_now_is_aware_jst():
    assert jst_now().utcoffset() == JST.utcoffset(None)


@pytest.mark.parametrize(
    "hour,band",
    [
        (4, "深夜"),
        (5, "朝"),
        (9, "朝"),
        (10, "昼"),
        (15, "昼"),
        (16, "夕方"),
        (18, "夕方"),
        (19, "夜"),
        (21, "夜"),
        (22, "深夜"),
        (23, "深夜"),
        (0, "深夜"),
    ],
)
def test_time_band_boundaries(hour, band):
    assert time_band(datetime(2026, 11, 20, hour, 0, tzinfo=JST)) == band


def test_time_band_edges_inside_the_hour():
    # 4:59 はまだ深夜、21:59 はまだ夜
    assert time_band(datetime(2026, 11, 20, 4, 59, tzinfo=JST)) == "深夜"
    assert time_band(datetime(2026, 11, 20, 21, 59, tzinfo=JST)) == "夜"


def test_time_band_uses_japan_time():
    # UTC 13:30 は日本時間 22:30（深夜）
    assert time_band(datetime(2026, 11, 20, 13, 30, tzinfo=UTC)) == "深夜"
