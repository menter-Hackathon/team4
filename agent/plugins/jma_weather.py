"""気象庁のデータから、今の天気（気温・湿度・空の様子）を取る（仕様 §6.7、#80）。

何をしているか:
    気象庁のホームページ（https://www.jma.go.jp）が画面を表示するために使っている JSON を読む。
    API キーは要らない。取った値は agent/plant/weather.py の Weather にまとめて返す。

注意（必ず読む）:
    - この JSON は**公式に仕様が公開された API ではない**。気象庁のサイトが変わると、
      URL や形が予告なく変わることがある。そのため、取れなくても会話は止めない作りにしている
      （取れなかったときの扱いは agent/plant/weather.py の WeatherCache が決める）
    - アメダスの観測値は 10 分ごとにしか更新されない。何度も取りに行くのは迷惑なので、
      取りに行くのは 30 分に 1 回くらいにしている（WeatherCache が使い回す）

取ってくるもの（2 種類。同時に取りに行く）:
    1. 気温・湿度 … アメダス（気象庁の観測所）が実際に測った値
       ① 最新の観測時刻を読む
          URL: {base}/bosai/amedas/data/latest_time.txt
          中身の例: "2026-11-18T11:00:00+09:00"
       ② その時刻の、全国の観測所の値をまとめて読む
          URL: {base}/bosai/amedas/data/map/20261118110000.json（時刻をつなげたファイル名）
          中身の例: {"44132": {"temp": [17.8, 0], "humidity": [68, 0], ...}, "44136": {...}, ...}
          "44132" は観測所のコード（東京）。値は [値, 品質フラグ] の 2 つ組になっている
       観測所のコードの一覧: {base}/bosai/amedas/const/amedastable.json
    2. 空の様子 … 気象庁の今日の予報
          URL: {base}/bosai/forecast/data/forecast/130000.json（130000 は東京都の地域コード）
          data[0]["timeSeries"][0]["areas"][0]["weatherCodes"][0] が今日の天気コード（例: "101"）
       地域コードの一覧: {base}/bosai/common/const/area.json（"offices" の中）

使う観測所・地域は .env の JMA_AMEDAS_STATION・JMA_FORECAST_AREA で決める（agent/config.py）。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

import aiohttp

from agent.plant.weather import Weather

logger = logging.getLogger(__name__)

# 気象庁のホームページの URL。テストでは、手元で動かす偽のサーバーの URL に差し替える
JMA_BASE_URL = "https://www.jma.go.jp"

# 気象庁の天気コード（3 桁）の、百の位 → 空の様子。
# 天気コードの例: "100" 晴れ、"101" 晴れ時々くもり、"200" くもり、"313" 雨のち曇、"400" 雪
# 百の位がいちばん主な天気を表しているので、そこだけを見る。
# 「時々」「のち」まで植物に伝えると文が長くなり、小さなモデルが言い間違えやすくなるため
_SKY_BY_HUNDREDS = {"1": "晴れ", "2": "くもり", "3": "雨", "4": "雪"}


def sky_from_code(code: str) -> str | None:
    """気象庁の天気コードを「晴れ」「くもり」「雨」「雪」にする。知らないコードなら None。

    例: "100" → "晴れ"、"101" → "晴れ"、"313" → "雨"、"999" → None、"" → None
    """
    # code[:1] は「最初の 1 文字」（"313" → "3"）。.get() は辞書に無ければ None を返す
    return _SKY_BY_HUNDREDS.get(code[:1]) if code else None


def _value(station: dict, key: str) -> float | None:
    """アメダスの 1 項目（[値, 品質フラグ] の 2 つ組）から、値だけを取り出す。

    観測所によって測っていない項目がある（例: 湿度を測っていない観測所）。
    また、機械の点検などで値が null（欠測）のこともある。どちらも None を返す。

    例:
        {"temp": [17.8, 0]} と "temp"     → 17.8
        {"temp": [17.8, 0]} と "humidity" → None（その項目が無い）
        {"temp": [None, 5]} と "temp"     → None（欠測）
    """
    pair = station.get(key)
    if not pair or pair[0] is None:
        return None
    return float(pair[0])


async def _fetch_amedas(
    session: aiohttp.ClientSession, base_url: str, station: str
) -> tuple[float | None, float | None]:
    """アメダスの観測所 station の、今の（気温, 湿度）を取る。

    例: "44132"（東京）→ (17.8, 68.0)
    失敗したとき（通信エラー・観測所が見つからない など）は、例外をそのまま投げる。
    """
    # 1. 最新の観測時刻を読み、ファイル名の形（年月日時分秒をつなげたもの）にする。
    #    例: "2026-11-18T11:00:00+09:00" → "20261118110000"
    #    async with ... as resp: は「通信して、終わったら接続を片付ける」書き方
    async with session.get(f"{base_url}/bosai/amedas/data/latest_time.txt") as resp:
        resp.raise_for_status()  # 404 や 500 などのエラーなら、ここで例外を出す
        latest = (await resp.text()).strip()  # 末尾の改行を消す
    stamp = datetime.fromisoformat(latest).strftime("%Y%m%d%H%M%S")

    # 2. その時刻の、全国の観測値を読む。
    #    content_type=None は「応答の種類（Content-Type）が JSON と名乗っていなくても JSON として読む」指定。
    #    気象庁のサーバーが付ける種類に左右されないようにするため
    async with session.get(f"{base_url}/bosai/amedas/data/map/{stamp}.json") as resp:
        resp.raise_for_status()
        data = await resp.json(content_type=None)

    # 3. 全国の中から、使う観測所の行だけを取り出す。
    #    観測所のコードを間違えていて見つからないときは KeyError（呼ぶ側で失敗として扱う）
    row = data[station]
    return _value(row, "temp"), _value(row, "humidity")


async def _fetch_sky(session: aiohttp.ClientSession, base_url: str, area: str) -> str | None:
    """地域 area の、今日の予報の空の様子を取る。

    例: "130000"（東京都）→ "晴れ"
    失敗したとき（通信エラー・形が違う など）は、例外をそのまま投げる。
    """
    async with session.get(f"{base_url}/bosai/forecast/data/forecast/{area}.json") as resp:
        resp.raise_for_status()
        data = await resp.json(content_type=None)
    # 予報の JSON は入れ子が深いので、順に説明する:
    #   data[0]                 … 3 日間の予報（data[1] は週間予報なので使わない）
    #   ["timeSeries"][0]       … 天気の予報（[1] は降水確率、[2] は気温なので使わない）
    #   ["areas"][0]            … 地域の中の最初の区域（東京都なら「東京地方」。伊豆諸島などは使わない）
    #   ["weatherCodes"][0]     … 最初の日（今日）の天気コード（例: "101"）
    return sky_from_code(data[0]["timeSeries"][0]["areas"][0]["weatherCodes"][0])


async def fetch_jma_weather(
    station: str | None,
    area: str | None,
    *,
    base_url: str = JMA_BASE_URL,
) -> Weather:
    """気象庁のデータから、今の天気を取る。

    引数:
        station: アメダスの観測所のコード（例: "44132" 東京）。None なら気温・湿度を取らない
        area: 予報の地域コード（例: "130000" 東京都）。None なら空の様子を取らない
        base_url: 気象庁の URL（テストでだけ変える）
    戻り値:
        Weather。片方だけ取れたら、取れたほうだけ入れて返す
        例: 両方取れた           → Weather(17.8, 68.0, "晴れ")
            予報だけ失敗した     → Weather(17.8, 68.0, None)
            観測値だけ失敗した   → Weather(None, None, "晴れ")
    どちらも取れなければ RuntimeError を投げる。
    何秒まで待つかは、呼ぶ側の WeatherCache が決める（ここでは待ち時間の上限を付けない）。
    """

    async def none() -> None:
        """何もしない処理。station・area が None のとき、取りに行く処理の代わりに使う。"""
        return None

    # 1. 観測値と予報を、同時に取りに行く。
    #    asyncio.gather(A, B) は A と B を同時に進めて、両方の結果を順に返す（1 つずつより速い）。
    #    return_exceptions=True にすると、片方が失敗しても例外を投げず、結果の代わりに例外を返す。
    #    これで「片方が失敗しても、もう片方の結果は使う」ことができる
    async with aiohttp.ClientSession() as session:
        amedas, sky = await asyncio.gather(
            _fetch_amedas(session, base_url, station) if station else none(),
            _fetch_sky(session, base_url, area) if area else none(),
            return_exceptions=True,
        )

    # 2. 失敗したほう（結果が例外になっているほう）は、ログに警告を残して None にする
    if isinstance(amedas, BaseException):
        logger.warning("could not fetch AMeDAS weather for %s: %r", station, amedas)
        amedas = None
    if isinstance(sky, BaseException):
        logger.warning("could not fetch JMA forecast for %s: %r", area, sky)
        sky = None

    # 3. Weather にまとめる。観測値が取れなかったときは、気温も湿度も None にする
    temperature, humidity = amedas if amedas else (None, None)
    weather = Weather(temperature, humidity, sky)

    # 4. 何も取れなかったら、失敗として知らせる。
    #    WeatherCache はこの例外を受けて「前に取れた天気を使うか、天気なしにするか」を決める
    if weather == Weather(None, None, None):
        raise RuntimeError("no weather data from JMA")
    return weather
