"""天気を植物の言葉にする・キャッシュする（仕様 §6.7、#80）。"""

import asyncio
from datetime import datetime

import pytest

from agent.plant.timeutil import JST
from agent.plant.weather import Weather, WeatherCache, describe_weather
from tests.fakes import FakeClock

T0 = datetime(2026, 11, 22, 10, 0, tzinfo=JST)


@pytest.mark.parametrize(
    "humidity,word",
    [(39, "乾燥している"), (40, None), (69, None), (70, "じめじめしている")],
)
def test_humidity_words_at_boundaries(humidity, word):
    text = describe_weather(Weather(20.0, humidity, None), dry_humidity=40)
    assert f"湿度 {humidity}%" in text
    for w in ("乾燥している", "じめじめしている"):
        assert (w in text) is (w == word)


@pytest.mark.parametrize(
    "temp,word", [(9.9, "さむい"), (10.0, None), (29.9, None), (30.0, "あつい")]
)
def test_temperature_words_at_boundaries(temp, word):
    text = describe_weather(Weather(temp, 50, None), dry_humidity=40)
    for w in ("さむい", "あつい"):
        assert (w in text) is (w == word)


def test_describe_weather_example():
    text = describe_weather(Weather(8.4, 30, "晴れ"), dry_humidity=40)
    assert text == (
        "今日の天気は晴れ。気温 8℃、湿度 30%。"
        "体の感じ: さむい（葉がちぢこまる）、乾燥している（葉がかさかさする）。"
    )


@pytest.mark.parametrize(
    "weather,text",
    [
        # 空の様子が取れなかった・どれにも当てはまらない日は「過ごしやすい」
        (
            Weather(20.0, 50, None),
            "気温 20℃、湿度 50%。体の感じ: 過ごしやすい（のびのびしている）。",
        ),
        # 湿度を測っていない観測所
        (Weather(8.4, None, None), "気温 8℃。体の感じ: さむい（葉がちぢこまる）。"),
        (Weather(None, None, "雨"), "今日の天気は雨。"),  # 予報だけ取れた
        (Weather(None, None, None), None),
    ],
)
def test_describe_weather_skips_missing_parts(weather, text):
    assert describe_weather(weather, dry_humidity=40) == text


class FakeFetch:
    def __init__(self, results):
        self.results = list(results)
        self.calls = 0

    async def __call__(self):
        self.calls += 1
        r = self.results.pop(0)
        if isinstance(r, Exception):
            raise r
        if r == "slow":
            await asyncio.sleep(10)
        return r


async def test_cache_reuses_for_30_minutes_then_refetches():
    clock = FakeClock(T0)
    fetch = FakeFetch([Weather(8, 30, "晴れ"), Weather(12, 60, "くもり")])
    cache = WeatherCache(fetch, clock, ttl=1800, timeout=2)
    await cache.refresh()
    clock.advance(1799)
    await cache.refresh()
    assert fetch.calls == 1 and cache.current() == Weather(8, 30, "晴れ")
    clock.advance(1)
    await cache.refresh()
    assert fetch.calls == 2 and cache.current() == Weather(12, 60, "くもり")


async def test_failure_keeps_nothing_and_does_not_raise(caplog):
    cache = WeatherCache(FakeFetch([RuntimeError("down")]), FakeClock(T0), ttl=1800, timeout=2)
    await cache.refresh()  # 例外を外に出さない
    assert cache.current() is None
    assert "weather" in caplog.text


async def test_failure_after_success_keeps_old_weather_until_it_is_too_old():
    clock = FakeClock(T0)
    fetch = FakeFetch([Weather(8, 30, "晴れ"), RuntimeError("down"), RuntimeError("down")])
    cache = WeatherCache(fetch, clock, ttl=1800, timeout=2)
    await cache.refresh()
    clock.advance(1800)
    await cache.refresh()  # 取り直しに失敗 → 少し古いが前の天気を使う
    assert cache.current() == Weather(8, 30, "晴れ")
    clock.advance(3 * 3600)  # 3 時間以上前の天気は、もう今の天気とは言えないので使わない
    await cache.refresh()
    assert cache.current() is None


async def test_slow_fetch_times_out():
    cache = WeatherCache(FakeFetch(["slow"]), FakeClock(T0), ttl=1800, timeout=0.01)
    await cache.refresh()
    assert cache.current() is None
