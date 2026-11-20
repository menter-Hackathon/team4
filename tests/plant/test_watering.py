"""「お水あげたよ」を見分ける（仕様 §6.7、#81）。"""

import pytest

from agent.plant.watering import mentions_watering


@pytest.mark.parametrize(
    "text",
    [
        "お水あげたよ",
        "水あげた",
        "さっき水やったよ",
        "水やりしたよ",
        "お水をあげました",
        "水やりしといた",
        "お水あげといたよ",
        "水を入れてあげたよ",
        "今日は水上げたよ",
        "水あげたけど、元気ないね",  # 後ろに「ない」があっても、あげたことは言っている
    ],
)
def test_detects_watering(text):
    assert mentions_watering(text)


@pytest.mark.parametrize(
    "text",
    [
        "お水あげようか？",  # これから（まだあげていない）
        "水あげてない",
        "水あげなかった",
        "お水あげる？",
        "水飲んだ",
        "水たまりがあった",
        "今日は雨だったよ",
        "",
    ],
)
def test_ignores_other_talk(text):
    assert not mentions_watering(text)
