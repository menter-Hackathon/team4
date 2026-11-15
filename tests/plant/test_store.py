import sqlite3
from datetime import datetime, timedelta

import pytest

from agent.plant.personality import Traits
from agent.plant.store import MemoryUpdate, NewMemory, PlantStore
from agent.plant.timeutil import JST, iso

T0 = datetime(2026, 11, 15, 10, 0, tzinfo=JST)


@pytest.fixture
def store(tmp_path):
    s = PlantStore(str(tmp_path / "sub" / "t.db"))
    yield s
    s.close()


def _reflected_session(store, plant_id, memories, now):
    """記憶を作るために、空のセッションを1つ reflection 済みにする。"""
    sid = store.start_session(plant_id, now)
    store.end_session(sid, now)
    store.apply_reflection(plant_id, sid, "seed", memories, [], store.get_plant(plant_id).traits, now)
    return sid


def test_wal_mode(store):
    assert store._conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_create_plant_once_with_initial_history(store):
    p = store.get_or_create_plant("a", T0)
    assert p.name == "ふたまる" and p.traits == Traits()
    store.get_or_create_plant("a", T0 + timedelta(days=1))
    h = store.trait_history("a")
    assert len(h) == 1 and h[0].session_id is None and h[0].traits == Traits()
    assert h[0].recorded_at == "2026-11-15T10:00:00+09:00"


def test_session_and_utterances(store):
    store.get_or_create_plant("a", T0)
    sid = store.start_session("a", T0)
    store.add_utterance(sid, "user", "ただいま", T0)
    store.add_utterance(sid, "plant", "おかえり！", T0 + timedelta(seconds=2))
    store.end_session(sid, T0 + timedelta(minutes=1))
    s = store.get_session(sid)
    assert s.user_turns == 1 and s.ended_at == "2026-11-15T10:01:00+09:00" and not s.reflected
    assert [u.role for u in store.get_utterances(sid)] == ["user", "plant"]
    assert store.get_utterances(sid)[1].text == "おかえり！"


def test_repair_unfinished(store):
    store.get_or_create_plant("a", T0)
    s1 = store.start_session("a", T0)
    store.add_utterance(s1, "user", "x", T0 + timedelta(minutes=5))
    s2 = store.start_session("a", T0 + timedelta(hours=1))
    assert store.repair_unfinished_sessions("a") == 2
    assert store.get_session(s1).ended_at == iso(T0 + timedelta(minutes=5))
    assert store.get_session(s2).ended_at == store.get_session(s2).started_at
    assert store.unreflected_sessions("a") == [s1, s2]


def test_unreflected_excludes_open_sessions(store):
    store.get_or_create_plant("a", T0)
    store.start_session("a", T0)
    assert store.unreflected_sessions("a") == []


def test_mark_reflected(store):
    store.get_or_create_plant("a", T0)
    sid = store.start_session("a", T0)
    store.end_session(sid, T0)
    store.mark_reflected(sid)
    assert store.get_session(sid).reflected and store.unreflected_sessions("a") == []


def test_apply_reflection(store):
    store.get_or_create_plant("a", T0)
    store.get_or_create_plant("b", T0)
    _reflected_session(store, "a", [NewMemory("カレーが好き", "preference", 0.6)], T0)
    _reflected_session(store, "b", [NewMemory("犬を飼っている", "habit", 0.5)], T0)
    a_mem_id = store.get_memories("a")[0].id
    b_mem_id = store.get_memories("b")[0].id

    later = T0 + timedelta(hours=1)
    sid = store.start_session("a", later)
    store.end_session(sid, later)
    store.apply_reflection(
        "a",
        sid,
        "テストの話をした",
        [NewMemory("明日テスト", "event", 0.8)],
        [
            MemoryUpdate(a_mem_id, "辛いカレーが好き", 0.7),
            MemoryUpdate(999, "x", 0.5),
            MemoryUpdate(b_mem_id, "乗っ取り", 0.9),
        ],
        Traits(affection=0.53),
        later,
    )
    assert store.get_plant("a").traits.affection == pytest.approx(0.53)
    mems = {m.id: m for m in store.get_memories("a")}
    assert mems[a_mem_id].content == "辛いカレーが好き" and mems[a_mem_id].importance == 0.7
    assert mems[a_mem_id].updated_at == iso(later)
    assert any(m.content == "明日テスト" and m.kind == "event" for m in mems.values())
    assert store.get_memories("b")[0].content == "犬を飼っている"  # 他株の記憶は更新しない
    assert store.get_session(sid).reflected and store.latest_summary("a") == "テストの話をした"
    assert store.trait_history("a")[-1].session_id == sid
    assert store.trait_history("a")[-1].traits.affection == pytest.approx(0.53)


def test_apply_reflection_is_atomic(store):
    store.get_or_create_plant("a", T0)
    sid = store.start_session("a", T0)
    store.end_session(sid, T0)
    with pytest.raises(sqlite3.IntegrityError):
        store.apply_reflection("a", sid, "s", [NewMemory("x", None, 0.5)], [], Traits(affection=0.9), T0)
    assert store.get_plant("a").traits == Traits()
    s = store.get_session(sid)
    assert not s.reflected and s.summary is None
    assert len(store.trait_history("a")) == 1 and store.get_memories("a") == []


def test_latest_summary_none_when_no_reflection(store):
    store.get_or_create_plant("a", T0)
    assert store.latest_summary("a") is None


def test_count_user_utterances_since(store):
    store.get_or_create_plant("a", T0)
    store.get_or_create_plant("b", T0)
    old = store.start_session("a", T0 - timedelta(days=8))
    store.add_utterance(old, "user", "むかし", T0 - timedelta(days=8))
    sid = store.start_session("a", T0)
    store.add_utterance(sid, "user", "1", T0)
    store.add_utterance(sid, "plant", "p", T0)
    store.add_utterance(sid, "user", "2", T0 + timedelta(minutes=1))
    other = store.start_session("b", T0)
    store.add_utterance(other, "user", "b", T0)
    assert store.count_user_utterances_since("a", T0 - timedelta(days=7)) == 2
