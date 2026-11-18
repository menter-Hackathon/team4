"""天気（気温・湿度）を植物の言葉にする（仕様 §6.7、#80）。

植物は「寒い」「乾燥している」を自分では言えない。ふたまるは天気 API の値を受け取り、
植物の代わりに言葉にする（AI を「本物の植物の通訳」にする、仕様 §1）。

このファイルは LiveKit にも HTTP にも依存しない。天気を取ってくる関数（本番は
agent/plugins/open_meteo.py）は引数で受け取り、テストでは偽物に差し替える。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

from .timeutil import Clock

logger = logging.getLogger(__name__)

# 湿度がこの値（%）以上なら「じめじめしている」と言葉にする。乾燥のほうは .env で変えられる（WEATHER_DRY_HUMIDITY）
HUMID_HUMIDITY = 70
# 気温（℃）がこれ未満なら「さむい」、これ以上なら「あつい」
COLD_TEMPERATURE = 10.0
HOT_TEMPERATURE = 30.0
# 取り直しに失敗したとき、前に取れた天気をいつまで使うか（秒）。3 時間より前の天気は「今の天気」と言えないので捨てる
STALE_LIMIT = 3 * 3600


@dataclass(frozen=True)
class Weather:
    """今の天気。temperature: 気温（℃）、humidity: 湿度（%）、code: WMO の天気コード（0 = 晴れ など）。"""

    temperature: float
    humidity: float
    code: int


# WMO の天気コード → 天気の言葉。(コードの最小, 最大, 言葉) の順に並べ、上から当てはめる。
# コードの意味: https://open-meteo.com/en/docs の「WMO Weather interpretation codes」
_WEATHER_LABELS = (
    (0, 1, "晴れ"),  # 0: 快晴、1: ほぼ晴れ
    (2, 2, "晴れときどきくもり"),
    (3, 3, "くもり"),
    (45, 48, "霧"),
    (51, 67, "雨"),  # 霧雨・雨・凍る雨
    (71, 77, "雪"),
    (80, 82, "にわか雨"),
    (85, 86, "にわか雪"),
    (95, 99, "雷雨"),
)


def weather_label(code: int) -> str | None:
    """天気コードを言葉にする。知らないコードなら None。例: 0 → "晴れ"、61 → "雨"。"""
    for low, high, label in _WEATHER_LABELS:
        if low <= code <= high:
            return label
    return None


def describe_weather(weather: Weather, *, dry_humidity: float) -> str:
    """天気を、プロンプトに入れる 1 文にする（仕様 §6.7）。

    数字だけを LLM に渡すより、「さむい」「乾燥している」と言葉にして渡したほうが、
    植物の言い方がぶれにくい（小さなモデルは数字から暑い・寒いを判断しにくいため）。

    引数:
        weather: 今の天気
        dry_humidity: 湿度がこの値（%）未満なら「乾燥している」と言う
    例:
        Weather(8.4, 30, 0) → "今日の天気は晴れ。気温 8℃（さむい）、湿度 30%（乾燥している）。"
    """
    # 1. 空の様子（知らないコードなら省く）
    label = weather_label(weather.code)
    sky = f"今日の天気は{label}。" if label else ""
    # 2. 気温。小数は四捨五入して整数で言う（「8.4℃」は話し言葉にしにくい）
    temp = f"気温 {round(weather.temperature)}℃"
    if weather.temperature < COLD_TEMPERATURE:
        temp += "（さむい）"
    elif weather.temperature >= HOT_TEMPERATURE:
        temp += "（あつい）"
    # 3. 湿度
    humidity = f"湿度 {round(weather.humidity)}%"
    if weather.humidity < dry_humidity:
        humidity += "（乾燥している）"
    elif weather.humidity >= HUMID_HUMIDITY:
        humidity += "（じめじめしている）"
    return f"{sky}{temp}、{humidity}。"


class WeatherCache:
    """天気を取って、しばらく覚えておく（仕様 §6.7）。

    起きるたびに天気 API を呼ぶと、返事が遅くなるうえ回数制限にも当たりやすいので、
    取った天気は ttl 秒（既定 30 分）使い回す。

    引数:
        fetch: 天気を取ってくる関数（本番は open_meteo.fetch_current_weather を包んだもの）
        clock: 現在時刻を返す関数（テストでは偽物の時計）
        ttl: 何秒まで同じ天気を使い回すか
        timeout: 天気 API を何秒まで待つか。待ちすぎると植物の第一声が遅れるので短くする
    """

    def __init__(
        self,
        fetch: Callable[[], Awaitable[Weather]],
        clock: Clock,
        *,
        ttl: float = 1800,
        timeout: float = 2,
    ) -> None:
        self._fetch = fetch
        self._clock = clock
        self._ttl = ttl
        self._timeout = timeout
        self._weather: Weather | None = None  # 最後に取れた天気
        self._fetched_at: datetime | None = None  # それを取った時刻

    def _age(self) -> float | None:
        """最後に取れた天気が何秒前のものか。まだ取れていなければ None。"""
        if self._fetched_at is None:
            return None
        return (self._clock() - self._fetched_at).total_seconds()

    async def refresh(self) -> None:
        """覚えている天気が古ければ取り直す。失敗しても例外は出さない（会話を止めないため、仕様 §6.7）。"""
        # 1. まだ新しければ何もしない
        age = self._age()
        if age is not None and age < self._ttl:
            return
        # 2. 取り直す。遅すぎるときは timeout 秒で諦める
        try:
            weather = await asyncio.wait_for(self._fetch(), self._timeout)
        except Exception as e:  # 通信エラー・タイムアウト・おかしな応答のどれでも、天気なしで続ける
            logger.warning("could not fetch weather: %r", e)
            # 3. 失敗したら、前に取れた天気が古すぎるときだけ捨てる（少し古いだけなら使い続ける）
            if age is not None and age >= STALE_LIMIT:
                self._weather = None
                self._fetched_at = None
            return
        self._weather = weather
        self._fetched_at = self._clock()

    def current(self) -> Weather | None:
        """覚えている天気。まだ取れていない・古すぎて捨てたなら None。"""
        return self._weather
