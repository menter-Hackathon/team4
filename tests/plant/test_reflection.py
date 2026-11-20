import json
from datetime import datetime, timedelta

import pytest

from agent.plant.reflection import (
    REFLECTION_SCHEMA,
    ReflectionError,
    parse_reflection,
    reflect_session,
)
from agent.plant.store import NewMemory, PlantStore
from agent.plant.timeutil import JST
from tests.fakes import FakeCompleter, reflection_json

NOW = datetime(2026, 11, 15, 20, 0, tzinfo=JST)
VALID = reflection_json(
    new=[{"content": "明日数学のテスト", "kind": "event", "importance": 0.8}],
    deltas={"affection": 0.03},
)


@pytest.fixture
def store(tmp_path):
    s = PlantStore(str(tmp_path / "r.db"))
    s.get_or_create_plant("a", NOW - timedelta(days=1))
    yield s
    s.close()


@pytest.fixture
def store_with_session(store):
    sid = store.start_session("a", NOW - timedelta(minutes=5))
    store.add_utterance(sid, "user", "明日テストなんだ", NOW - timedelta(minutes=4))
    store.add_utterance(sid, "plant", "がんばって！", NOW - timedelta(minutes=4))
    store.end_session(sid, NOW - timedelta(minutes=3))
    return store, sid


async def test_success_updates_everything(store_with_session):
    store, sid = store_with_session
    fake = FakeCompleter([VALID])
    assert await reflect_session(store, "a", sid, fake, NOW) is True
    assert store.get_plant("a").traits.affection == pytest.approx(0.53)
    assert store.latest_summary("a") == "テストの話をした" and store.get_session(sid).reflected
    assert [m.content for m in store.get_memories("a")] == ["明日数学のテスト"]
    msgs, schema = fake.calls[0]
    assert schema is REFLECTION_SCHEMA
    assert (
        "持ち主: 明日テストなんだ" in msgs[-1]["content"]
        and "ふたまる: がんばって！" in msgs[-1]["content"]
    )


async def test_talkativeness_uses_recent_user_turns(store_with_session):
    store, sid = store_with_session
    await reflect_session(store, "a", sid, FakeCompleter([VALID]), NOW)
    # 直近7日のユーザー発話1件: delta = 0 + 0.5*(0.01-0.5)*0.1 = -0.0245
    assert store.get_plant("a").traits.talkativeness == pytest.approx(0.4755)


async def test_existing_memories_are_listed_with_ids(store_with_session):
    store, sid = store_with_session
    seed = store.start_session("a", NOW - timedelta(days=1))
    store.end_session(seed, NOW - timedelta(days=1))
    store.apply_reflection(
        "a",
        seed,
        "s",
        [NewMemory("カレーが好き", "preference", 0.7)],
        [],
        store.get_plant("a").traits,
        NOW - timedelta(days=1),
    )
    mem_id = store.get_memories("a")[0].id
    fake = FakeCompleter(
        [
            reflection_json(
                updated=[{"id": mem_id, "content": "辛いカレーが好き", "importance": 0.8}]
            )
        ]
    )
    assert await reflect_session(store, "a", sid, fake, NOW)
    assert f"[id={mem_id}] カレーが好き (importance=0.7)" in fake.calls[0][0][-1]["content"]
    assert store.get_memories("a")[0].content == "辛いカレーが好き"


async def test_retry_once_then_success(store_with_session):
    store, sid = store_with_session
    fake = FakeCompleter(["not json", VALID])
    assert await reflect_session(store, "a", sid, fake, NOW) is True and len(fake.calls) == 2


@pytest.mark.parametrize(
    "first,second",
    [
        ("not json", "{}"),
        (RuntimeError("timeout"), "[]"),
        (json.dumps({"summary": "x", "new_memories": [], "updated_memories": []}), "not json"),
    ],
)
async def test_gives_up_after_two_failures(store_with_session, first, second):
    store, sid = store_with_session
    fake = FakeCompleter([first, second])
    assert await reflect_session(store, "a", sid, fake, NOW) is False
    assert not store.get_session(sid).reflected and len(fake.calls) == 2


async def test_zero_user_turns_skips_llm(store):
    sid = store.start_session("a", NOW)
    store.add_utterance(sid, "plant", "おかえり", NOW)
    store.end_session(sid, NOW)
    fake = FakeCompleter([])
    assert await reflect_session(store, "a", sid, fake, NOW) is True
    assert fake.calls == [] and store.get_session(sid).reflected


def test_parse_cleans_items():
    r = parse_reflection(
        "```json\n"
        + json.dumps(
            {
                "summary": "s",
                "new_memories": [
                    {"content": "a", "kind": "other", "importance": 0.5},
                    {"content": "b", "kind": "habit", "importance": 1.7},
                    {"content": "", "kind": "habit", "importance": 0.5},
                ],
                "updated_memories": [
                    {"id": "12", "content": "c", "importance": 0.4},
                    {"id": 3, "content": "d", "importance": "x"},
                ],
                "trait_deltas": {},
            }
        )
        + "\n```"
    )
    assert [(m.content, m.importance) for m in r.new_memories] == [("b", 1.0)]
    # 数字だけの文字列の id も整数として使う（#8）
    assert [(u.id, u.importance) for u in r.updated_memories] == [(12, 0.4), (3, 0.5)]


def test_parse_accepts_uppercase_kind_and_string_id():
    # LLM が少しだけ違う形で返しても、記憶を捨てない（#8）
    r = parse_reflection(
        json.dumps(
            {
                "summary": "s",
                "new_memories": [{"content": "テストがある", "kind": "Event", "importance": 0.6}],
                "updated_memories": [
                    {"id": "3", "content": "カレーが好き", "importance": 0.7},
                    {"id": "abc", "content": "x", "importance": 0.5},
                    {"id": True, "content": "y", "importance": 0.5},
                ],
                "trait_deltas": {},
            }
        )
    )
    assert [(m.content, m.kind) for m in r.new_memories] == [("テストがある", "event")]
    assert [u.id for u in r.updated_memories] == [3]


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-Infinity"])
def test_parse_treats_non_finite_importance_as_middle(bad):
    # json.loads は NaN / Infinity を読めてしまう。上限の 1.0 にせず、真ん中の 0.5 にする（#7）
    raw = (
        '{"summary": "s", "new_memories": [{"content": "a", "kind": "habit", "importance": '
        + bad
        + '}], "updated_memories": [{"id": 1, "content": "b", "importance": '
        + bad
        + '}], "trait_deltas": {}}'
    )
    r = parse_reflection(raw)
    assert r.new_memories[0].importance == 0.5 and r.updated_memories[0].importance == 0.5


@pytest.mark.parametrize(
    "raw",
    [
        "[]",
        '{"summary": 1, "new_memories": [], "updated_memories": [], "trait_deltas": {}}',
        '{"summary": "s", "new_memories": {}, "updated_memories": [], "trait_deltas": {}}',
    ],
)
def test_parse_rejects_bad_shapes(raw):
    with pytest.raises(ReflectionError):
        parse_reflection(raw)


async def test_prompt_lists_at_most_30_memories(store_with_session):
    store, sid = store_with_session
    seed = store.start_session("a", NOW - timedelta(days=1))
    store.end_session(seed, NOW - timedelta(days=1))
    store.apply_reflection(
        "a",
        seed,
        "s",
        [NewMemory(f"記憶{i}", "event", 0.5) for i in range(35)],
        [],
        store.get_plant("a").traits,
        NOW - timedelta(days=1),
    )
    fake = FakeCompleter([VALID])
    await reflect_session(store, "a", sid, fake, NOW)
    assert fake.calls[0][0][-1]["content"].count("[id=") == 30


def test_owner_dialect_is_parsed():
    assert parse_reflection(reflection_json(dialect="kansai")).owner_dialect == "kansai"


@pytest.mark.parametrize("dialect", [None, "osaka", 3])
def test_missing_or_invalid_dialect_is_unknown(dialect):
    data = json.loads(reflection_json())
    if dialect is None:
        del data["owner_dialect"]
    else:
        data["owner_dialect"] = dialect
    assert parse_reflection(json.dumps(data)).owner_dialect == "unknown"


def _person_session(store, person, text):
    """person の会話を 1 つ作って終了させ、セッション id を返す（person=None はゲスト）。"""
    sid = store.start_session("a", NOW - timedelta(minutes=5))
    if person is not None:
        store.set_session_person(sid, person.id)
    store.add_utterance(sid, "user", text, NOW - timedelta(minutes=4))
    store.end_session(sid, NOW - timedelta(minutes=3))
    return sid


async def test_reflection_shows_only_this_persons_memories(store):
    taro = store.add_person("a", "たろう", NOW)
    hanako = store.add_person("a", "はなこ", NOW)
    traits = store.get_plant("a").traits
    # お客さん（person_id なし）・たろう・はなこの記憶を 1 件ずつ作る
    for who, content in [
        (None, "この家は日当たりがいい"),
        (taro, "たろうはカレーが好き"),
        (hanako, "はなこはピアノが好き"),
    ]:
        store.apply_reflection(
            "a",
            _person_session(store, who, "覚えてね"),
            "s",
            [NewMemory(content, "preference", 0.5)],
            [],
            traits,
            NOW,
            person_id=who.id if who else None,
        )
    sid = _person_session(store, taro, "こんにちは")
    fake = FakeCompleter([VALID])
    await reflect_session(store, "a", sid, fake, NOW)
    prompt = fake.calls[0][0][-1]["content"]
    assert "たろうはカレーが好き" in prompt
    # ほかの人の記憶も、お客さんとの記憶も見せない（#70）
    assert "はなこはピアノが好き" not in prompt
    assert "この家は日当たりがいい" not in prompt
    assert prompt.count("[id=") == 1


async def test_person_session_updates_person_traits_and_dialect(store):
    taro = store.add_person("a", "たろう", NOW)
    plant_before = store.get_plant("a").traits
    sid = _person_session(store, taro, "今日はええ天気やなあ")
    fake = FakeCompleter([reflection_json(deltas={"affection": 0.05}, dialect="kansai")])
    await reflect_session(store, "a", sid, fake, NOW)
    assert store.get_person(taro.id).traits.affection == pytest.approx(0.55)
    assert store.get_plant("a").traits == plant_before
    assert store.recent_dialects(taro.id) == ["kansai"]


async def test_guest_session_updates_plant_traits(store):
    sid = _person_session(store, None, "こんにちは")
    await reflect_session(store, "a", sid, FakeCompleter([VALID]), NOW)
    assert store.get_plant("a").traits.affection == pytest.approx(0.53)


# --- 水やり（#81） ---
@pytest.mark.parametrize(
    "value,expected", [(True, True), (False, False), (None, False), ("yes", False)]
)
def test_watered_is_parsed(value, expected):
    data = json.loads(VALID)
    if value is not None:
        data["watered"] = value
    assert parse_reflection(json.dumps(data)).watered is expected


def test_schema_asks_for_watered():
    assert REFLECTION_SCHEMA["properties"]["watered"] == {"type": "boolean"}
    assert "watered" in REFLECTION_SCHEMA["required"]


async def test_reflection_records_missed_watering_at_session_end(store_with_session):
    store, sid = store_with_session
    data = json.loads(VALID) | {"watered": True}
    assert await reflect_session(store, "a", sid, FakeCompleter([json.dumps(data)]), NOW)
    # 振り返った時刻（NOW）ではなく、会話が終わった時刻で記録する
    assert store.last_watered_at("a") == NOW - timedelta(minutes=3)


async def test_reflection_does_not_double_count_watering(store_with_session):
    store, sid = store_with_session
    store.add_watering("a", None, sid, NOW - timedelta(minutes=4))  # 会話中にもう記録した
    data = json.loads(VALID) | {"watered": True}
    assert await reflect_session(store, "a", sid, FakeCompleter([json.dumps(data)]), NOW)
    assert store.last_watered_at("a") == NOW - timedelta(minutes=4)


# --- 世話の間隔（#83） ---
def _session(store, start, *, watered=False, person_id=None):
    sid = store.start_session("a", start)
    if person_id is not None:
        store.set_session_person(sid, person_id)
    store.add_utterance(sid, "user", "ただいま", start)
    if watered:
        store.add_watering("a", person_id, sid, start)
    store.end_session(sid, start + timedelta(minutes=5))
    return sid


async def test_reflection_input_has_care_intervals(store):
    _session(store, NOW - timedelta(days=5), watered=True)
    sid = _session(store, NOW - timedelta(days=2), watered=True)
    fake = FakeCompleter([VALID])
    await reflect_session(store, "a", sid, fake, NOW)
    user = fake.calls[0][0][-1]["content"]
    assert "## 世話の様子" in user
    assert (
        "前にこの相手と話してから3日（しばらく話しかけてもらえず、さみしかった" in user
    )  # 5 日前 → 2 日前
    assert "この会話で水をもらった（energy を少し上げてよい）" in user


async def test_reflection_input_first_talk_and_no_water(store):
    sid = _session(store, NOW - timedelta(hours=1))
    fake = FakeCompleter([VALID])
    await reflect_session(store, "a", sid, fake, NOW)
    user = fake.calls[0][0][-1]["content"]
    assert "この相手と話すのは初めて" in user
    # 育ち始めて 1 日なので、一度も水をもらっていなくても渇いていない
    assert "まだ一度も水をもらっていない（水は足りている" in user


async def test_reflection_talk_interval_is_per_person(store):
    taro = store.add_person("a", "たろう", NOW - timedelta(days=10))
    _session(store, NOW - timedelta(days=6), person_id=taro.id)
    _session(store, NOW - timedelta(days=1))  # お客さんとはきのう話した
    sid = _session(store, NOW - timedelta(hours=1), person_id=taro.id)
    fake = FakeCompleter([VALID])
    await reflect_session(store, "a", sid, fake, NOW)
    assert "前にこの相手と話してから6日" in fake.calls[0][0][-1]["content"]


async def test_daily_talk_without_water_is_not_judged_as_neglect(store):
    # 毎日話しかけ、2 日前に水をもらっている → 「水をもらっていない」を放っておかれたと読ませない
    _session(store, NOW - timedelta(days=2), watered=True)
    _session(store, NOW - timedelta(days=1))
    sid = _session(store, NOW - timedelta(hours=1))
    fake = FakeCompleter([VALID])
    await reflect_session(store, "a", sid, fake, NOW)
    user = fake.calls[0][0][-1]["content"]
    assert "前にこの相手と話してから1日（毎日のように話しかけてもらえている" in user
    assert "最後に水をもらってから2日（水は足りている。energy を下げる理由にはならない）" in user


async def test_no_water_for_three_days_is_thirsty(store):
    _session(store, NOW - timedelta(days=4), watered=True)
    sid = _session(store, NOW - timedelta(hours=1))
    fake = FakeCompleter([VALID])
    await reflect_session(store, "a", sid, fake, NOW)
    assert "最後に水をもらってから4日（のどが渇いて元気がない" in fake.calls[0][0][-1]["content"]


# --- 話しかけた時間帯（2026-11-20、ショーンさんの FB） ---
async def _care_text(store, start):
    """start に始まった会話を振り返り、LLM に渡した文を返す。"""
    sid = _session(store, start)
    fake = FakeCompleter([VALID])
    await reflect_session(store, "a", sid, fake, NOW)
    return fake.calls[0][0][-1]["content"]


async def test_late_night_talk_lowers_energy(store):
    user = await _care_text(store, datetime(2026, 11, 14, 23, 0, tzinfo=JST))
    assert "- 夜おそく（深夜）に話しかけてもらった（energy を少し下げる）。" in user


async def test_morning_talk_raises_energy(store):
    user = await _care_text(store, datetime(2026, 11, 15, 7, 30, tzinfo=JST))
    assert "- 朝に話しかけてもらった（energy を少し上げてよい）。" in user


async def test_daytime_talk_does_not_move_energy(store):
    user = await _care_text(store, datetime(2026, 11, 15, 14, 0, tzinfo=JST))
    assert "- 昼に話しかけてもらった（時間帯では energy を動かさない）。" in user


def test_system_message_explains_care_for_affection_and_energy():
    from agent.plant.reflection import SYSTEM_MESSAGE

    assert "世話の様子" in SYSTEM_MESSAGE and "（）に書いた判断に従う" in SYSTEM_MESSAGE
