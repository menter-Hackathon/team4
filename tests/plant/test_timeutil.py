from datetime import datetime, timezone

from agent.plant.timeutil import JST, iso, jst_now, parse_iso


def test_iso_is_jst_seconds():
    dt = datetime(2026, 11, 15, 1, 0, 0, 123456, tzinfo=timezone.utc)
    assert iso(dt) == "2026-11-15T10:00:00+09:00"
    assert parse_iso("2026-11-15T10:00:00+09:00") == dt.replace(microsecond=0)


def test_jst_now_is_aware_jst():
    assert jst_now().utcoffset() == JST.utcoffset(None)
