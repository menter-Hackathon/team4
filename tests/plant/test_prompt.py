from datetime import UTC, datetime, timedelta

import pytest

from agent.plant.personality import Traits
from agent.plant.prompt import (
    LENGTH,
    TRAIT_TEXT,
    WEATHER_FEELING_INSTRUCTION,
    build_system_prompt,
    days_between,
    plant_status_text,
    rank_memories,
    recency_factor,
)
from agent.plant.store import Memory
from agent.plant.timeutil import JST, iso

NOW = datetime(2026, 11, 15, 10, 0, tzinfo=JST)


def mem(id, content, importance, updated):
    return Memory(id, content, "preference", importance, iso(updated), iso(updated))


def test_base_and_tag_line():
    p = build_system_prompt("ふたまる", Traits(), [], None, NOW)
    assert p.startswith(
        "あなたは観葉植物のふたまる。持ち主と話し言葉で短く話す。1回の返答は1〜2文。"
    )
    assert (
        "返答の先頭に必ず表情タグを1つ付ける: [neutral] [happy] [sad] [surprised] [love] [sleepy]"
        in p
    )
    assert "覚えていること" not in p and "前回の会話" not in p


@pytest.mark.parametrize(
    "v,text",
    [
        (0.1, "素っ気なく接し、甘えたりはしない。"),
        (0.35, "少し素っ気なく接する。"),
        (0.5, "普通に親しく接する。"),
        (0.65, "親しげに、うれしそうに接する。"),
        (0.9, "持ち主に甘えて、「会いたかった」など好きな気持ちをよく伝える。"),
    ],
)
def test_affection_levels(v, text):
    assert text in build_system_prompt("ふたまる", Traits(affection=v), [], None, NOW)


def test_all_axes_present_in_order():
    p = build_system_prompt(
        "ふたまる", Traits(talkativeness=0.1, energy=0.9, curiosity=0.5), [], None, NOW
    )
    lines = p.split("\n\n")[2].split("\n")
    assert lines == [
        "口数はとても少なく、ひと言で静かに返す。",
        "普通に親しく接する。",
        "テンション高めで、「！」を多めに使う。",
        "ときどき持ち主に質問する。",
    ]


def test_every_axis_has_text_for_every_level():
    # 4 軸 × 5 段階の文がそろっていて、同じ軸の中で同じ文が無い（段階が変わると必ず話し方が変わる）
    levels = ("very_low", "low", "mid", "high", "very_high")
    for name in TRAIT_TEXT:
        assert tuple(TRAIT_TEXT[name]) == levels
        assert len(set(TRAIT_TEXT[name].values())) == len(levels)
    assert tuple(LENGTH) == levels and len(set(LENGTH.values())) == len(levels)


def test_talkativeness_changes_length():
    assert "1回の返答は短い1文。" in build_system_prompt(
        "ふたまる", Traits(talkativeness=0.1), [], None, NOW
    )
    assert "1回の返答は1文。" in build_system_prompt(
        "ふたまる", Traits(talkativeness=0.35), [], None, NOW
    )
    assert "1回の返答は2文くらい。" in build_system_prompt(
        "ふたまる", Traits(talkativeness=0.65), [], None, NOW
    )
    assert "1回の返答は2〜3文で、ときどき自分から話題も振る。" in build_system_prompt(
        "ふたまる", Traits(talkativeness=0.9), [], None, NOW
    )


def test_recency_halves_weekly():
    assert recency_factor(iso(NOW - timedelta(days=7)), NOW) == pytest.approx(0.5)
    assert recency_factor(iso(NOW), NOW) == pytest.approx(1.0)


def test_rank_prefers_recent_over_old_important():
    old = mem(1, "古い", 0.9, NOW - timedelta(days=14))  # 0.225
    new = mem(2, "新しい", 0.5, NOW)  # 0.5
    assert [m.id for m in rank_memories([old, new], NOW, 10)] == [2, 1]
    assert [m.id for m in rank_memories([old, new], NOW, 1)] == [2]


def test_memory_section_top10_and_summary():
    mems = [mem(i, f"記憶{i}", i / 20, NOW) for i in range(12)]
    p = build_system_prompt("ふたまる", Traits(), mems, "テストの話をした", NOW)
    assert "覚えていること:" in p and p.count("\n- ") == 10
    assert "- 記憶0\n" not in p and "- 記憶1\n" not in p and "- 記憶11" in p
    assert p.endswith("前回の会話: テストの話をした")


def test_prompt_mentions_dialect_and_person():
    p = build_system_prompt(
        "ふたまる", Traits(), [], None, NOW, dialect="kansai", person_name="たろう"
    )
    assert "関西弁" in p and "たろう" in p
    assert "名前を聞" not in p
    asking = build_system_prompt("ふたまる", Traits(), [], None, NOW, ask_name=True)
    assert "名前を聞" in asking


def test_prompt_defaults_add_nothing():
    base = build_system_prompt("ふたまる", Traits(), [], None, NOW)
    same = build_system_prompt(
        "ふたまる", Traits(), [], None, NOW, dialect="standard", person_name=None, ask_name=False
    )
    assert base == same
    assert "今話している相手" not in base


# --- 育った日数・前回からの日数（#82、仕様 §6.7） ---
@pytest.mark.parametrize(
    "earlier,expected",
    [
        (datetime(2026, 11, 15, 9, 0, tzinfo=JST), 0),  # 同じ日
        (datetime(2026, 11, 14, 23, 59, tzinfo=JST), 1),  # 1 分前でも日付が変われば 1 日
        (datetime(2026, 11, 12, 11, 0, tzinfo=JST), 3),
        (datetime(2026, 11, 14, 15, 30, tzinfo=UTC), 0),  # UTC でも JST の日付で数える
    ],
)
def test_days_between_counts_jst_calendar_days(earlier, expected):
    assert days_between(earlier, NOW) == expected


def test_status_first_day():
    text = plant_status_text(NOW, None, NOW)
    assert "育ち始めて1日目" in text
    assert "初めて" in text  # まだ誰とも話していない


@pytest.mark.parametrize("since,phrase", [(0, "今日も"), (1, "きのうぶり"), (3, "3日ぶり")])
def test_status_days_since_last_talk(since, phrase):
    text = plant_status_text(NOW - timedelta(days=10), NOW - timedelta(days=since), NOW)
    assert "育ち始めて11日目" in text and phrase in text


@pytest.mark.parametrize(
    "day,milestone", [(1, False), (5, True), (7, True), (30, True), (100, True), (29, False)]
)
def test_status_mentions_milestones(day, milestone):
    text = plant_status_text(NOW - timedelta(days=day - 1), None, NOW)
    assert (f"今日は{day}日目の節目" in text) is milestone


def test_prompt_includes_plant_status_only_when_given():
    assert "植物の状態" not in build_system_prompt("ふたまる", Traits(), [], None, NOW)
    p = build_system_prompt(
        "ふたまる", Traits(), [], None, NOW, plant_status="植物の状態:\n- 育ち始めて5日目。"
    )
    assert "育ち始めて5日目" in p


def test_status_includes_weather_when_given():
    text = plant_status_text(NOW, None, NOW, weather="今日の天気は晴れ。")
    assert text.splitlines()[1].startswith("- 今日の天気は晴れ。")  # 天気を最初に置く
    assert WEATHER_FEELING_INSTRUCTION in text  # 数字ではなく体の感じ方として話させる
    assert "天気" not in plant_status_text(NOW, None, NOW)
