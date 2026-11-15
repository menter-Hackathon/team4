"""JST の現在時刻と ISO 8601 文字列の変換（仕様 §2: すべて +09:00 付きで保存）。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))
Clock = Callable[[], datetime]


def jst_now() -> datetime:
    return datetime.now(JST)


def iso(dt: datetime) -> str:
    return dt.astimezone(JST).isoformat(timespec="seconds")


def parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s)
