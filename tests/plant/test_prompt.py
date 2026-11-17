from datetime import datetime, timedelta

import pytest

from agent.plant.personality import Traits
from agent.plant.prompt import build_system_prompt, rank_memories, recency_factor
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
        (0.2, "少し素っ気なく接する。"),
        (0.5, "普通に親しく接する。"),
        (0.8, "持ち主に甘えて親しげに話す。"),
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
        "口数は少なめで、静かに返す。",
        "普通に親しく接する。",
        "テンション高めで、「！」を多めに使う。",
        "ときどき持ち主に質問する。",
    ]


def test_talkativeness_changes_length():
    assert "1回の返答は1文。" in build_system_prompt(
        "ふたまる", Traits(talkativeness=0.1), [], None, NOW
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
