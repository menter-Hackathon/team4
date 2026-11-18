"""Open-Meteo から今の天気（気温・湿度・天気コード）を取る（仕様 §6.7、#80）。

Open-Meteo は API キーの要らない無料の天気 API。緯度・経度を渡すと、その場所の今の天気を返す。
リクエストの例:
    GET https://api.open-meteo.com/v1/forecast?latitude=35.68&longitude=139.76
        &current=temperature_2m,relative_humidity_2m,weather_code&timezone=Asia/Tokyo
応答の例（使うところだけ）:
    {"current": {"temperature_2m": 8.4, "relative_humidity_2m": 30, "weather_code": 3}}
"""

from __future__ import annotations

import aiohttp

from agent.plant.weather import Weather

# Open-Meteo の URL（テストでは偽のサーバーの URL を渡す）
OPEN_METEO_BASE_URL = "https://api.open-meteo.com"
# 取ってくる項目: 気温（℃）・湿度（%）・天気コード
CURRENT_FIELDS = "temperature_2m,relative_humidity_2m,weather_code"


async def fetch_current_weather(
    latitude: float,
    longitude: float,
    *,
    base_url: str = OPEN_METEO_BASE_URL,
) -> Weather:
    """緯度・経度の場所の、今の天気を取る。

    失敗したら aiohttp の例外（ClientResponseError など）や KeyError をそのまま投げる。
    待ち時間の上限と失敗時の扱いは、呼ぶ側（agent/plant/weather.py の WeatherCache）が決める。
    呼ぶのは 30 分に 1 回くらいなので、HTTP の接続は毎回作って閉じる。
    """
    params = {
        "latitude": str(latitude),
        "longitude": str(longitude),
        "current": CURRENT_FIELDS,
        "timezone": "Asia/Tokyo",
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{base_url}/v1/forecast", params=params) as resp:
            resp.raise_for_status()  # 4xx・5xx なら ClientResponseError
            data = await resp.json()
    current = data["current"]
    return Weather(
        temperature=float(current["temperature_2m"]),
        humidity=float(current["relative_humidity_2m"]),
        code=int(current["weather_code"]),
    )
