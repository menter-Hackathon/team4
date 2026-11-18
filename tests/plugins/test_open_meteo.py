"""Open-Meteo から今の天気を取る（仕様 §6.7、#80）。偽の Open-Meteo に向けて確かめる。"""

import aiohttp
import pytest
from aiohttp import web

from agent.plant.weather import Weather
from agent.plugins.open_meteo import fetch_current_weather


@pytest.fixture
async def fake_open_meteo():
    seen: list[dict] = []

    async def forecast(request: web.Request) -> web.Response:
        seen.append(dict(request.query))
        if request.query.get("latitude") == "0":
            return web.Response(status=500, text="boom")
        return web.json_response(
            {"current": {"temperature_2m": 8.4, "relative_humidity_2m": 30, "weather_code": 3}}
        )

    app = web.Application()
    app.router.add_get("/v1/forecast", forecast)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}", seen
    await runner.cleanup()


async def test_fetch_current_weather(fake_open_meteo):
    base, seen = fake_open_meteo
    w = await fetch_current_weather(35.68, 139.76, base_url=base)
    assert w == Weather(8.4, 30, 3)
    q = seen[0]
    assert (q["latitude"], q["longitude"], q["timezone"]) == ("35.68", "139.76", "Asia/Tokyo")
    assert q["current"] == "temperature_2m,relative_humidity_2m,weather_code"


async def test_fetch_raises_on_http_error(fake_open_meteo):
    base, _ = fake_open_meteo
    with pytest.raises(aiohttp.ClientResponseError):
        await fetch_current_weather(0, 0, base_url=base)
