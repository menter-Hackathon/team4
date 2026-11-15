import json
from datetime import datetime, timedelta

import pytest

from agent.plant.reflection import REFLECTION_SCHEMA, parse_reflection, recover, reflect_session, ReflectionError
from agent.plant.store import NewMemory, PlantStore
from agent.plant.timeutil import JST
from tests.fakes import FakeCompleter, reflection_json

NOW = datetime(2026, 11, 15, 20, 0, tzinfo=JST)
VALID = reflection_json(
    new=[{"content": "明日数学のテスト", "kind": "event", "importance": 0.8}], deltas={"affection": 0.03}
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
    assert "持ち主: 明日テストなんだ" in msgs[-1]["content"] and "ふたまる: がんばって！" in msgs[-1]["content"]


async def test_talkativeness_uses_recent_user_turns(store_with_session):
    store, sid = store_with_session
    await reflect_session(store, "a", sid, FakeCompleter([VALID]), NOW)
    # 直近7日のユーザー発話1件: delta = 0 + 0.5*(0.01-0.5)*0.1 = -0.0245
    assert store.get_plant("a").traits.talkativeness == pytest.approx(0.4755)


async def test_existing_memories_are_listed_with_ids(store_with_session):
    store, sid = store_with_session
    seed = store.start_session("a", NOW - timedelta(days=1))
    store.end_session(seed, NOW - timedelta(days=1))
    store.apply_reflection("a", seed, "s", [NewMemory("カレーが好き", "preference", 0.7)], [],
                           store.get_plant("a").traits, NOW - timedelta(days=1))
    mem_id = store.get_memories("a")[0].id
    fake = FakeCompleter([reflection_json(updated=[{"id": mem_id, "content": "辛いカレーが好き", "importance": 0.8}])])
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
    assert [(u.id, u.importance) for u in r.updated_memories] == [(3, 0.5)]


@pytest.mark.parametrize("raw", ["[]", '{"summary": 1, "new_memories": [], "updated_memories": [], "trait_deltas": {}}',
                                 '{"summary": "s", "new_memories": {}, "updated_memories": [], "trait_deltas": {}}'])
def test_parse_rejects_bad_shapes(raw):
    with pytest.raises(ReflectionError):
        parse_reflection(raw)


async def test_prompt_lists_at_most_30_memories(store_with_session):
    store, sid = store_with_session
    seed = store.start_session("a", NOW - timedelta(days=1))
    store.end_session(seed, NOW - timedelta(days=1))
    store.apply_reflection("a", seed, "s", [NewMemory(f"記憶{i}", "event", 0.5) for i in range(35)], [],
                           store.get_plant("a").traits, NOW - timedelta(days=1))
    fake = FakeCompleter([VALID])
    await reflect_session(store, "a", sid, fake, NOW)
    assert fake.calls[0][0][-1]["content"].count("[id=") == 30


async def test_recover_repairs_and_reflects(store):
    sid = store.start_session("a", NOW - timedelta(hours=1))
    store.add_utterance(sid, "user", "ただいま", NOW - timedelta(minutes=50))
    await recover(store, "a", FakeCompleter([VALID]), NOW)
    s = store.get_session(sid)
    assert s.ended_at is not None and s.reflected
