"""JST の現在時刻と ISO 8601 文字列の変換（仕様 §2: すべて +09:00 付きで保存）。

DB には時刻を文字列（例: "2026-11-16T20:45:01+09:00"）で保存する。
その文字列と Python の datetime を行き来するための小さな関数をまとめている。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Literal

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


# 1 日を 5 つに分けた「時間帯」。ダッシュボードの「よく話しかけてくれた時間帯」（仕様 §6.6）と、
# 振り返りで時間帯を性格に反映するとき（仕様 §6.7）に使う
TimeBand = Literal["朝", "昼", "夕方", "夜", "深夜"]
# 並び順にも意味がある: 同じ回数の時間帯があったとき、先に並んでいるほうを選ぶ（dashboard.py）
TIME_BANDS: tuple[TimeBand, ...] = ("朝", "昼", "夕方", "夜", "深夜")


def time_band(dt: datetime) -> TimeBand:
    """時刻を、日本時間の時間帯に分ける。

    朝 5〜10 時、昼 10〜16 時、夕方 16〜19 時、夜 19〜22 時、深夜 22〜5 時
    （それぞれ始まりの時刻を含み、終わりの時刻を含まない）。
    例: 7:30 → "朝"、21:59 → "夜"、22:00 → "深夜"、4:59 → "深夜"
    外国の時刻（UTC など）で渡されても、日本時間に直してから分ける。
    """
    hour = dt.astimezone(JST).hour
    if 5 <= hour < 10:
        return "朝"
    if 10 <= hour < 16:
        return "昼"
    if 16 <= hour < 19:
        return "夕方"
    if 19 <= hour < 22:
        return "夜"
    return "深夜"  # 22〜23 時と 0〜4 時
