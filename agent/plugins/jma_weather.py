"""気象庁のデータから今の天気（気温・湿度・空の様子）を取る（仕様 §6.7、#80）。

気象庁のホームページが使っている JSON を読む。API キーは要らない。
**公式に仕様が公開された API ではない**ので、URL や形が予告なく変わることがある。
そのため、失敗しても会話は止めない作りにしている（agent/plant/weather.py の WeatherCache）。
アメダスの更新は 10 分ごとなので、取りに行くのは 30 分に 1 回くらいに抑える。

1. 気温・湿度（アメダスの実測値）
   - 最新の観測時刻: {base}/bosai/amedas/data/latest_time.txt
     例: "2026-11-18T11:00:00+09:00"
   - 全国の観測値: {base}/bosai/amedas/data/map/{YYYYMMDDHHmmss}.json
     例: {"44132": {"temp": [17.8, 0], "humidity": [68, 0], ...}, ...}
     値は [値, 品質フラグ] の形。観測所のコードは
     {base}/bosai/amedas/const/amedastable.json で調べる（例: 東京 44132）
2. 空の様子（今日の予報）
   - {base}/bosai/forecast/data/forecast/{地域コード}.json（例: 東京都 130000）
     data[0]["timeSeries"][0]["areas"][0]["weatherCodes"][0] が今日の天気コード（例: "101"）
     地域コードは {base}/bosai/common/const/area.json で調べる
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

import aiohttp

from agent.plant.weather import Weather

logger = logging.getLogger(__name__)

# 気象庁のホームページの URL（テストでは偽のサーバーの URL を渡す）
JMA_BASE_URL = "https://www.jma.go.jp"

# 気象庁の天気コードの百の位 → 空の様子。例: "100" 晴れ、"101" 晴れ時々くもり、"313" 雨のち曇
# 細かい言い方（「時々」「のち」）までは使わず、いちばん主な天気だけを植物に伝える
_SKY_BY_HUNDREDS = {"1": "晴れ", "2": "くもり", "3": "雨", "4": "雪"}


def sky_from_code(code: str) -> str | None:
    """気象庁の天気コードを「晴れ」「くもり」「雨」「雪」にする。知らないコードなら None。

    例: "100" → "晴れ"、"313" → "雨"、"" → None
    """
    return _SKY_BY_HUNDREDS.get(code[:1]) if code else None


def _value(station: dict, key: str) -> float | None:
    """アメダスの 1 項目（[値, 品質フラグ]）から値を取り出す。無い・値が null なら None。

    例: {"temp": [17.8, 0]} と "temp" → 17.8
    """
    pair = station.get(key)
    if not pair or pair[0] is None:
        return None
    return float(pair[0])


async def _fetch_amedas(
    session: aiohttp.ClientSession, base_url: str, station: str
) -> tuple[float | None, float | None]:
    """アメダスの観測所 station の、今の（気温, 湿度）を取る。"""
    # 1. 最新の観測時刻を取り、ファイル名の形（YYYYMMDDHHmmss）にする
    async with session.get(f"{base_url}/bosai/amedas/data/latest_time.txt") as resp:
        resp.raise_for_status()
        latest = (await resp.text()).strip()
    stamp = datetime.fromisoformat(latest).strftime("%Y%m%d%H%M%S")
    # 2. その時刻の全国の観測値から、観測所の行を取り出す（無ければ KeyError）
    async with session.get(f"{base_url}/bosai/amedas/data/map/{stamp}.json") as resp:
        resp.raise_for_status()
        data = await resp.json(content_type=None)
    row = data[station]
    return _value(row, "temp"), _value(row, "humidity")


async def _fetch_sky(session: aiohttp.ClientSession, base_url: str, area: str) -> str | None:
    """地域 area の、今日の予報の空の様子を取る。"""
    async with session.get(f"{base_url}/bosai/forecast/data/forecast/{area}.json") as resp:
        resp.raise_for_status()
        data = await resp.json(content_type=None)
    # 地域の中の最初の区域（東京都なら「東京地方」）の、最初の日（今日）の天気コード
    return sky_from_code(data[0]["timeSeries"][0]["areas"][0]["weatherCodes"][0])


async def fetch_jma_weather(
    station: str | None,
    area: str | None,
    *,
    base_url: str = JMA_BASE_URL,
) -> Weather:
    """気象庁のデータから今の天気を取る。

    引数:
        station: アメダスの観測所のコード（例: "44132"）。None なら気温・湿度を取らない
        area: 予報の地域コード（例: "130000"）。None なら空の様子を取らない
    戻り値:
        Weather。片方だけ取れたら、取れたほうだけ入れて返す
    どちらも取れなければ RuntimeError（待ち時間の上限は、呼ぶ側の WeatherCache が決める）。
    """

    async def none() -> None:
        return None

    async with aiohttp.ClientSession() as session:
        # 1. 観測値と予報を同時に取りに行く。片方が失敗しても、もう片方は使う
        amedas, sky = await asyncio.gather(
            _fetch_amedas(session, base_url, station) if station else none(),
            _fetch_sky(session, base_url, area) if area else none(),
            return_exceptions=True,
        )
    # 2. 失敗したほうはログに残して None にする
    if isinstance(amedas, BaseException):
        logger.warning("could not fetch AMeDAS weather for %s: %r", station, amedas)
        amedas = None
    if isinstance(sky, BaseException):
        logger.warning("could not fetch JMA forecast for %s: %r", area, sky)
        sky = None
    temperature, humidity = amedas if amedas else (None, None)
    weather = Weather(temperature, humidity, sky)
    # 3. 何も取れなかったら失敗として知らせる（WeatherCache が前の天気を使うか決める）
    if weather == Weather(None, None, None):
        raise RuntimeError("no weather data from JMA")
    return weather
