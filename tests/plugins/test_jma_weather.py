"""気象庁のデータから今の天気を取る（仕様 §6.7、#80）。偽の気象庁サーバーに向けて確かめる。"""

import pytest
from aiohttp import web

from agent.plant.weather import Weather
from agent.plugins.jma_weather import fetch_jma_weather, sky_from_code

# 本物の応答（2026-11-18 11:00）を短くしたもの
AMEDAS = {
    "44132": {"temp": [17.8, 0], "humidity": [68, 0], "wind": [1.7, 0]},
    "44136": {"temp": [16.2, 0], "wind": [2.0, 0]},  # 湿度を測っていない観測所
    "44999": {"temp": [None, 5]},  # 欠測
}
FORECAST = [
    {
        "timeSeries": [
            {
                "timeDefines": ["2026-11-18T11:00:00+09:00"],
                "areas": [
                    {"area": {"name": "東京地方", "code": "130010"}, "weatherCodes": ["101"]},
                    {"area": {"name": "伊豆諸島北部", "code": "130020"}, "weatherCodes": ["300"]},
                ],
            }
        ]
    }
]


@pytest.fixture
async def jma():
    seen: list[str] = []
    state = {"fail_forecast": False, "fail_amedas": False}

    async def latest(request: web.Request) -> web.Response:
        seen.append(request.path)
        if state["fail_amedas"]:
            return web.Response(status=500)
        return web.Response(text="2026-11-18T11:00:00+09:00\n")

    async def amedas_map(request: web.Request) -> web.Response:
        seen.append(request.path)
        return web.json_response(AMEDAS)

    async def forecast(request: web.Request) -> web.Response:
        seen.append(request.path)
        if state["fail_forecast"]:
            return web.Response(status=404)
        return web.json_response(FORECAST)

    app = web.Application()
    app.router.add_get("/bosai/amedas/data/latest_time.txt", latest)
    app.router.add_get("/bosai/amedas/data/map/20261118110000.json", amedas_map)
    app.router.add_get("/bosai/forecast/data/forecast/130000.json", forecast)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}", seen, state
    await runner.cleanup()


@pytest.mark.parametrize(
    "code,sky",
    [
        ("100", "晴れ"),
        ("101", "晴れ"),
        ("200", "くもり"),
        ("313", "雨"),
        ("400", "雪"),
        ("999", None),
        ("", None),
    ],
)
def test_sky_from_code(code, sky):
    assert sky_from_code(code) == sky


async def test_fetch_temperature_humidity_and_sky(jma):
    base, seen, _ = jma
    w = await fetch_jma_weather("44132", "130000", base_url=base)
    assert w == Weather(17.8, 68.0, "晴れ")  # 予報は最初の区域（東京地方）を使う
    assert "/bosai/amedas/data/map/20261118110000.json" in seen  # 最新の観測時刻のファイルを読む


async def test_station_without_humidity(jma):
    base, _, _ = jma
    assert await fetch_jma_weather("44136", None, base_url=base) == Weather(16.2, None, None)


async def test_missing_value_is_none_and_sky_still_used(jma):
    base, _, _ = jma
    assert await fetch_jma_weather("44999", "130000", base_url=base) == Weather(None, None, "晴れ")


async def test_forecast_failure_keeps_amedas(jma):
    base, _, state = jma
    state["fail_forecast"] = True
    assert await fetch_jma_weather("44132", "130000", base_url=base) == Weather(17.8, 68.0, None)


async def test_amedas_failure_keeps_forecast(jma):
    base, _, state = jma
    state["fail_amedas"] = True
    assert await fetch_jma_weather("44132", "130000", base_url=base) == Weather(None, None, "晴れ")


async def test_nothing_fetched_raises(jma):
    base, _, state = jma
    state["fail_amedas"] = state["fail_forecast"] = True
    with pytest.raises(RuntimeError):
        await fetch_jma_weather("44132", "130000", base_url=base)


async def test_unknown_station_raises(jma):
    base, _, _ = jma
    with pytest.raises(RuntimeError):
        await fetch_jma_weather("00000", None, base_url=base)
