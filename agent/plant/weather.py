"""天気（気温・湿度・空の様子）を、植物の言葉にする（仕様 §6.7、#80）。

なぜ天気を使うのか:
    植物は「寒い」「乾燥している」を自分では言えない。ふたまるは天気のデータを受け取り、
    植物の代わりに言葉にする。AI を「本物の植物の通訳」にするための仕組み（仕様 §1）。
    天気を話すことで、「ただの AI スピーカー」ではなく、目の前の植物とつながった AI になる。

全体の流れ（起きるたびに、上から順に動く）:
    1. conversation.py が、植物が起きる直前に WeatherCache.refresh() を呼ぶ
    2. WeatherCache は、30 分以内に取った天気があればそれを使う。古ければ取り直す
       （取り直す関数の中身は agent/plugins/jma_weather.py。気象庁のデータを読む）
    3. conversation.py が WeatherCache.current() で今の天気（Weather）を受け取る
    4. describe_weather() で、プロンプトに入れる 1 文にする
       例: 「今日の天気は晴れ。気温 8℃、湿度 30%。体の感じ: さむい（葉がちぢこまる）、…」
    5. prompt.py が、その 1 文を「植物の状態」の段落に入れる → LLM が植物として話す

このファイルは LiveKit にもインターネットにも依存しない。
天気を取ってくる関数は「引数で受け取る」形にしているので、テストでは決まった天気を返す
偽物の関数に差し替えられる（本物の気象庁につながなくてもテストできる）。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

from .timeutil import Clock

logger = logging.getLogger(__name__)

# --- 「さむい」「乾燥している」などと言葉にする境目 ---
# 湿度がこの値（%）以上なら「じめじめしている」と言う。
# 乾燥のほうの境目（既定 40%）は .env の WEATHER_DRY_HUMIDITY で変えられるので、ここには置かない
HUMID_HUMIDITY = 70
# 気温（℃）がこれ未満なら「さむい」
COLD_TEMPERATURE = 10.0
# 気温（℃）がこれ以上なら「あつい」
HOT_TEMPERATURE = 30.0
# 天気の取り直しに失敗したとき、前に取れた天気を何秒まで使い続けるか（3 時間）。
# 少し古いだけなら使い続けたほうが会話が自然。でも 3 時間より前の天気は「今の天気」とは言えないので捨てる
STALE_LIMIT = 3 * 3600


# @dataclass: 「値をまとめて持つだけのクラス」を短く書くための書き方。
# frozen=True にすると、作ったあとで中身を書き換えられない（うっかり書き換えるバグを防ぐ）。
# 同じ値どうしは == で等しくなるので、テストで Weather(8, 30, "晴れ") と比べられる
@dataclass(frozen=True)
class Weather:
    """今の天気。取れなかった項目は None。

    temperature: 気温（℃）。アメダス（気象庁の観測所）が実際に測った値
    humidity: 湿度（%）。アメダスの実測値。湿度を測っていない観測所では None
    sky: 空の様子。「晴れ」「くもり」「雨」「雪」のどれか。気象庁の今日の予報から

    例: Weather(temperature=17.8, humidity=68.0, sky="晴れ")
    """

    temperature: float | None
    humidity: float | None
    sky: str | None


def describe_weather(weather: Weather, *, dry_humidity: float) -> str | None:
    """天気を、プロンプトに入れる 1 文にする（仕様 §6.7）。何も取れていなければ None。

    なぜ数字を言葉にするのか:
        「気温 8℃」と数字だけを LLM に渡すと、小さなモデルは寒いのか暑いのかを判断しにくく、
        「8℃のいい天気だね」と数字を読み上げるだけになりやすい。
        そこで「さむい（葉がちぢこまる）」のように、植物の体の感じ方まで言葉にしてから渡す。

    引数:
        weather: 今の天気
        dry_humidity: 湿度がこの値（%）未満なら「乾燥している」と言う（.env の WEATHER_DRY_HUMIDITY）
        （引数の * より後ろは、呼ぶときに dry_humidity=40 のように名前を付けて渡す決まり）
    戻り値の例:
        Weather(8.4, 30, "晴れ")
            → "今日の天気は晴れ。気温 8℃、湿度 30%。体の感じ: さむい（葉がちぢこまる）、乾燥している（葉がかさかさする）。"
        Weather(20, 50, None)
            → "気温 20℃、湿度 50%。体の感じ: 過ごしやすい（のびのびしている）。"
        Weather(None, None, None) → None（何も取れていないので、天気の行ごと省く）
    """
    # 1. 空の様子。取れていなければ空の文字列にして、文から省く
    sky = f"今日の天気は{weather.sky}。" if weather.sky else ""

    # 2. 気温・湿度の数字（measures）と、植物の体の感じ方（feelings）を、それぞれリストに集める。
    #    数字は四捨五入して整数にする（「8.4℃」は話し言葉にしにくいため。round(8.4) → 8）
    measures: list[str] = []
    # 体の感じ方は、その日に当てはまるものだけを書く。
    # 最初は「さむいと葉がちぢこまる、じめじめしていると根がむずむずする」のような例を全部まとめて
    # 指示に書いていたが、LLM が当てはまらない日にも「じめじめしてる」と言ってしまった（#80 で確認）。
    # そのため、当てはまったものだけを渡すようにしている
    feelings: list[str] = []

    # 2a. 気温。10℃ 未満なら「さむい」、30℃ 以上なら「あつい」、その間なら何も足さない
    if weather.temperature is not None:
        measures.append(f"気温 {round(weather.temperature)}℃")
        if weather.temperature < COLD_TEMPERATURE:
            feelings.append("さむい（葉がちぢこまる）")
        elif weather.temperature >= HOT_TEMPERATURE:
            feelings.append("あつい（葉がぐったりする）")

    # 2b. 湿度。dry_humidity 未満なら「乾燥している」、70% 以上なら「じめじめしている」
    if weather.humidity is not None:
        measures.append(f"湿度 {round(weather.humidity)}%")
        if weather.humidity < dry_humidity:
            feelings.append("乾燥している（葉がかさかさする）")
        elif weather.humidity >= HUMID_HUMIDITY:
            feelings.append("じめじめしている（根がむずむずする）")

    # 3. さむくもあつくもなく、乾燥もじめじめもしていない日は「過ごしやすい」と伝える。
    #    何も書かないより、植物が「今日は気持ちいいね」と話しやすくなるため
    if measures and not feelings:
        feelings.append("過ごしやすい（のびのびしている）")

    # 4. 文をつなげる。"、".join(["気温 8℃", "湿度 30%"]) → "気温 8℃、湿度 30%"
    text = sky
    if measures:
        text += "、".join(measures) + "。"
        text += "体の感じ: " + "、".join(feelings) + "。"
    # 空の様子も数字も無ければ text は "" のまま。"" or None → None を返す
    return text or None


class WeatherCache:
    """天気を取って、しばらく覚えておく（キャッシュする）（仕様 §6.7）。

    なぜ覚えておくのか:
        植物が起きるたびに気象庁に取りに行くと、
        - 毎回 0.数秒〜2 秒待つことになり、植物の第一声が遅れる
        - 気象庁のデータは公式に公開された API ではないので、何度もアクセスするのは迷惑になる
        アメダスの観測値はどうせ 10 分ごとにしか変わらないので、取った天気は 30 分使い回す。

    時間の流れの例（ttl=1800 秒 = 30 分）:
        10:00 起きる → まだ天気が無いので取りに行く（取れた）
        10:20 起きる → 20 分前の天気がある。30 分以内なので、そのまま使う（取りに行かない）
        10:31 起きる → 31 分前の天気は古いので、取り直す
        11:05 起きる → 取り直しに失敗した → 34 分前の天気をそのまま使う（3 時間以内なので）
        14:10 起きる → また失敗した → 3 時間以上前の天気は古すぎるので捨てる（天気なしで話す）

    引数:
        fetch: 天気を取ってくる関数。本番は jma_weather.fetch_jma_weather を包んだもの（main.py）。
            型の Callable[[], Awaitable[Weather]] は「引数なしで呼ぶと、await すれば Weather が
            返ってくる関数」という意味。つまり async def fetch() -> Weather: のような関数を渡す
        clock: 現在時刻を返す関数（テストでは、時間を自由に進められる偽物の時計を渡す）
        ttl: 何秒まで同じ天気を使い回すか（既定 1800 秒 = 30 分）
        timeout: 気象庁を何秒まで待つか（既定 2 秒）。待ちすぎると植物の第一声が遅れるので短くする
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
        # 最後に取れた天気。まだ一度も取れていない・古すぎて捨てたときは None
        self._weather: Weather | None = None
        # その天気を取った時刻。_weather と必ずセットで変える
        self._fetched_at: datetime | None = None

    def _age(self) -> float | None:
        """最後に取れた天気が何秒前のものか。まだ取れていなければ None。

        例: 10:00 に取って、今が 10:20 → 1200.0（秒）
        """
        if self._fetched_at is None:
            return None
        return (self._clock() - self._fetched_at).total_seconds()

    async def refresh(self) -> None:
        """覚えている天気が古ければ取り直す。

        失敗しても例外は外に出さない。天気が取れないくらいで、植物が話せなくなるのは困るため
        （仕様 §6.7）。失敗したときはログに警告を残し、天気なし（または少し古い天気）で会話を続ける。
        """
        # 1. 覚えている天気がまだ新しい（ttl 秒以内）なら、何もしない
        age = self._age()
        if age is not None and age < self._ttl:
            return

        # 2. 取り直す。asyncio.wait_for(処理, 秒数) は「秒数を過ぎたら待つのをやめて
        #    TimeoutError を出す」書き方。気象庁が遅いときに、植物の第一声まで待たせないため
        try:
            weather = await asyncio.wait_for(self._fetch(), self._timeout)
        except Exception as e:
            # 通信エラー・タイムアウト・おかしな応答のどれでも、ここに来る。
            # %r は「例外の種類と中身」をそのままログに出す書き方（例: TimeoutError()）
            logger.warning("could not fetch weather: %r", e)
            # 3. 失敗したとき、前に取れた天気が古すぎる（3 時間以上前）なら捨てる。
            #    少し古いだけなら捨てずに使い続ける（上の「時間の流れの例」の 11:05 と 14:10）
            if age is not None and age >= STALE_LIMIT:
                self._weather = None
                self._fetched_at = None
            return

        # 4. 取れたので覚えておく。取った時刻も一緒に覚え、次の refresh() で古さを判断する
        self._weather = weather
        self._fetched_at = self._clock()

    def current(self) -> Weather | None:
        """覚えている天気を返す。まだ取れていない・古すぎて捨てたなら None。

        refresh() と違って、取りに行くことはしない（すぐに返る）。
        conversation.py がプロンプトを作るたびに呼ぶ。
        """
        return self._weather
