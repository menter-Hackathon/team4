"""JST の現在時刻と ISO 8601 文字列の変換（仕様 §2: すべて +09:00 付きで保存）。

DB には時刻を文字列（例: "2026-11-16T20:45:01+09:00"）で保存する。
その文字列と Python の datetime を行き来するための小さな関数をまとめている。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone

# 日本時間（UTC より 9 時間進んでいる）
JST = timezone(timedelta(hours=9))

# 「呼ぶと現在時刻を返す関数」の型。本番では jst_now を渡し、
# テストでは決まった時刻を返す偽物（tests/fakes.py の FakeClock）を渡して、時間の進みを自由に操作する
Clock = Callable[[], datetime]


def jst_now() -> datetime:
    """今の日本時間を返す。"""
    return datetime.now(JST)


def iso(dt: datetime) -> str:
    """datetime を DB 保存用の文字列にする。

    例: datetime(2026, 11, 16, 20, 45, 1, tzinfo=JST) → "2026-11-16T20:45:01+09:00"
    他のタイムゾーンの時刻が来ても、日本時間に直してから文字列にする。秒より細かい部分は捨てる。
    """
    return dt.astimezone(JST).isoformat(timespec="seconds")


def parse_iso(s: str) -> datetime:
    """iso() で作った文字列を datetime に戻す。例: "2026-11-16T20:45:01+09:00" → datetime"""
    return datetime.fromisoformat(s)
