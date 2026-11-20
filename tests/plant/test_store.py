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
    store.apply_reflection(
        plant_id, sid, "seed", memories, [], store.get_plant(plant_id).traits, now
    )
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
        store.apply_reflection(
            "a", sid, "s", [NewMemory("x", None, 0.5)], [], Traits(affection=0.9), T0
        )
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


# --- 人ごとの性格・記憶・方言（people-and-dialect） ---

# この機能より前のスキーマ（people も person_id / owner_dialect も無い）
OLD_SCHEMA = """
CREATE TABLE plants (
  plant_id TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT 'ふたまる', created_at TEXT NOT NULL,
  talkativeness REAL NOT NULL DEFAULT 0.5, affection REAL NOT NULL DEFAULT 0.5,
  energy REAL NOT NULL DEFAULT 0.5, curiosity REAL NOT NULL DEFAULT 0.5
);
CREATE TABLE memories (
  id INTEGER PRIMARY KEY AUTOINCREMENT, plant_id TEXT NOT NULL REFERENCES plants(plant_id),
  content TEXT NOT NULL, kind TEXT NOT NULL, importance REAL NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, plant_id TEXT NOT NULL REFERENCES plants(plant_id),
  started_at TEXT NOT NULL, ended_at TEXT, user_turns INTEGER NOT NULL DEFAULT 0,
  summary TEXT, reflected INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE utterances (
  id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER NOT NULL REFERENCES sessions(id),
  role TEXT NOT NULL, text TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE trait_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT, plant_id TEXT NOT NULL REFERENCES plants(plant_id),
  session_id INTEGER REFERENCES sessions(id), talkativeness REAL NOT NULL,
  affection REAL NOT NULL, energy REAL NOT NULL, curiosity REAL NOT NULL, recorded_at TEXT NOT NULL
);
"""


def test_migrates_an_old_database(tmp_path):
    path = str(tmp_path / "old.db")
    conn = sqlite3.connect(path)
    conn.executescript(OLD_SCHEMA)
    ts = iso(T0)
    conn.execute(
        "INSERT INTO plants (plant_id, created_at, affection) VALUES ('a1', ?, 0.6)", (ts,)
    )
    conn.execute(
        "INSERT INTO memories (plant_id, content, kind, importance, created_at, updated_at)"
        " VALUES ('a1', 'カレーが好き', 'preference', 0.6, ?, ?)",
        (ts, ts),
    )
    conn.execute(
        "INSERT INTO sessions (plant_id, started_at, ended_at, summary, reflected)"
        " VALUES ('a1', ?, ?, 'むかしの話', 1)",
        (ts, ts),
    )
    conn.commit()
    conn.close()

    s = PlantStore(path)  # 開き直すと migration が走る
    try:
        assert s.get_plant("a1").traits.affection == pytest.approx(0.6)
        mems = s.get_memories("a1")
        assert [m.content for m in mems] == ["カレーが好き"]
        assert s._conn.execute("SELECT person_id FROM memories").fetchone()[0] is None
        assert s.latest_summary("a1") == "むかしの話"
        sess = s.get_session(1)
        assert sess.person_id is None and sess.owner_dialect is None
        # 2 回目に開いても壊れない（列が既にあっても ALTER しない）
    finally:
        s.close()
    PlantStore(path).close()


def _two_people(store):
    store.get_or_create_plant("a1", T0)
    taro = store.add_person("a1", "たろう", T0)
    hanako = store.add_person("a1", "はなこ", T0)
    return taro, hanako


def _reflect(store, person_id, new=(), updates=(), traits=None, dialect="unknown", at=T0):
    """人（None ならお客さん）のセッションを作って振り返りを反映し、セッション id を返す。"""
    sid = store.start_session("a1", at)
    if person_id is not None:
        store.set_session_person(sid, person_id)
    store.end_session(sid, at)
    store.apply_reflection(
        "a1",
        sid,
        f"summary-{person_id}",
        list(new),
        list(updates),
        traits or Traits(),
        at,
        person_id=person_id,
        owner_dialect=dialect,
    )
    return sid


def test_people_have_their_own_traits_and_memories(store):
    taro, hanako = _two_people(store)
    assert [p.name for p in store.get_people("a1")] == ["たろう", "はなこ"]
    _reflect(store, taro.id, traits=Traits(affection=0.55))
    assert store.get_person(taro.id).traits.affection > 0.5
    assert store.get_person(hanako.id).traits == Traits()
    assert store.get_plant("a1").traits == Traits()
    # 履歴も人ごとに分かれる（初期の 1 行＋振り返りの 1 行）
    assert len(store.trait_history("a1", taro.id)) == 2
    assert len(store.trait_history("a1", hanako.id)) == 1
    assert len(store.trait_history("a1")) == 1


def test_memories_are_private_per_person(store):
    taro, hanako = _two_people(store)
    _reflect(store, taro.id, [NewMemory("たろうはカレー好き", "preference", 0.6)])
    _reflect(store, hanako.id, [NewMemory("はなこは犬好き", "preference", 0.6)])
    _reflect(store, None, [NewMemory("お客さんの話", "event", 0.5)])
    # 名乗った人にはその人の記憶だけ。お客さんの記憶も混ぜない（#70）
    assert [m.content for m in store.get_memories("a1", taro.id)] == ["たろうはカレー好き"]
    assert [m.content for m in store.get_memories("a1", hanako.id)] == ["はなこは犬好き"]
    # お客さんにはお客さんの記憶だけ
    assert [m.content for m in store.get_memories("a1")] == ["お客さんの話"]


def test_reflection_cannot_update_another_persons_memory(store):
    taro, hanako = _two_people(store)
    _reflect(store, hanako.id, [NewMemory("はなこは犬好き", "preference", 0.6)])
    _reflect(store, None, [NewMemory("お客さんの話", "event", 0.5)])
    hanako_mem = store.get_memories("a1", hanako.id)[0]
    guest_mem = store.get_memories("a1")[0]
    later = T0 + timedelta(hours=1)
    _reflect(
        store,
        taro.id,
        updates=[
            MemoryUpdate(hanako_mem.id, "乗っ取り", 0.9),
            MemoryUpdate(guest_mem.id, "お客さんの話（更新）", 0.7),
        ],
        at=later,
    )
    # ほかの人の記憶も、お客さんの記憶も、たろうとの振り返りでは書き換えられない（#70）
    assert store.get_memories("a1", hanako.id)[0].content == "はなこは犬好き"
    assert store.get_memories("a1")[0].content == "お客さんの話"


def test_latest_summary_is_per_person_and_guest(store):
    taro, _ = _two_people(store)
    _reflect(store, taro.id, at=T0)
    _reflect(store, None, at=T0 + timedelta(hours=1))
    assert store.latest_summary("a1", taro.id) == f"summary-{taro.id}"
    assert store.latest_summary("a1") == "summary-None"


def test_count_user_utterances_is_per_person(store):
    taro, _ = _two_people(store)
    s_taro = store.start_session("a1", T0)
    store.set_session_person(s_taro, taro.id)
    store.add_utterance(s_taro, "user", "x", T0)
    s_guest = store.start_session("a1", T0)
    store.add_utterance(s_guest, "user", "y", T0)
    store.add_utterance(s_guest, "user", "z", T0)
    since = T0 - timedelta(days=1)
    assert store.count_user_utterances_since("a1", since, taro.id) == 1
    assert store.count_user_utterances_since("a1", since) == 2


def test_recent_dialects_newest_first_without_unknown(store):
    taro, hanako = _two_people(store)
    for i, d in enumerate(["kansai", "unknown", "hakata", "standard"]):
        _reflect(store, taro.id, dialect=d, at=T0 + timedelta(hours=i))
    _reflect(store, hanako.id, dialect="hiroshima", at=T0 + timedelta(hours=9))
    assert store.recent_dialects(taro.id) == ["standard", "hakata", "kansai"]
    assert store.recent_dialects(taro.id, limit=2) == ["standard", "hakata"]
    assert store.recent_dialects(hanako.id) == ["hiroshima"]


def test_dialect_override_can_be_set_and_cleared(store):
    taro, _ = _two_people(store)
    assert taro.dialect_override is None
    store.set_dialect_override(taro.id, "hakata")
    assert store.get_person(taro.id).dialect_override == "hakata"
    store.set_dialect_override(taro.id, None)
    assert store.get_person(taro.id).dialect_override is None


# --- 育った日数・前回からの日数（#82） ---
def test_plant_created_at_is_the_first_creation_time(store):
    store.get_or_create_plant("a", T0)
    store.get_or_create_plant("a", T0 + timedelta(days=3))  # 2 回目は作り直さない
    assert store.plant_created_at("a") == T0


def test_last_talk_at_is_per_person_and_skips_unfinished(store):
    taro, hanako = _two_people(store)
    s1 = store.start_session("a1", T0)
    store.set_session_person(s1, taro.id)
    store.end_session(s1, T0 + timedelta(minutes=5))
    s2 = store.start_session("a1", T0 + timedelta(days=2))
    store.end_session(s2, T0 + timedelta(days=2, minutes=1))  # お客さん
    store.start_session("a1", T0 + timedelta(days=3))  # 今のセッション（まだ終わっていない）
    assert store.last_talk_at("a1", taro.id) == T0 + timedelta(minutes=5)
    assert store.last_talk_at("a1", hanako.id) is None
    assert store.last_talk_at("a1") == T0 + timedelta(days=2, minutes=1)  # 誰とでも、一番最近


# --- 水やり（#81） ---
def test_watering_is_recorded_once_per_session(store):
    store.get_or_create_plant("a", T0)
    sid = store.start_session("a", T0)
    assert store.last_watered_at("a") is None and not store.watered_in_session(sid)
    assert store.add_watering("a", None, sid, T0 + timedelta(minutes=1)) is True
    assert (
        store.add_watering("a", None, sid, T0 + timedelta(minutes=2)) is False
    )  # 同じ会話では 1 回
    assert store.watered_in_session(sid)
    assert store.last_watered_at("a") == T0 + timedelta(minutes=1)


def test_last_watered_at_is_per_plant_and_latest(store):
    store.get_or_create_plant("a", T0)
    store.get_or_create_plant("b", T0)
    s1 = store.start_session("a", T0)
    store.add_watering("a", None, s1, T0)
    s2 = store.start_session("a", T0 + timedelta(days=2))
    store.add_watering("a", None, s2, T0 + timedelta(days=2))
    assert store.last_watered_at("a") == T0 + timedelta(days=2)
    assert store.last_watered_at("b") is None


# --- 世話の間隔（#83）。振り返りでは「この会話より前」の時刻を見る ---
def test_last_talk_and_watering_before_a_time(store):
    store.get_or_create_plant("a", T0)
    s1 = store.start_session("a", T0)
    store.add_watering("a", None, s1, T0)
    store.end_session(s1, T0 + timedelta(minutes=5))
    s2 = store.start_session("a", T0 + timedelta(days=3))
    store.add_watering("a", None, s2, T0 + timedelta(days=3))
    store.end_session(s2, T0 + timedelta(days=3, minutes=5))
    before = T0 + timedelta(days=3)  # 2 回目の会話が始まった時刻
    assert store.last_talk_at("a", before=before) == T0 + timedelta(minutes=5)
    assert store.last_watered_at("a", before=before) == T0
    assert store.last_talk_at("a", before=T0) is None


def _talk(store, start, *, person_id=None, end=True):
    """start に始まる会話を 1 つ作る（end=False なら会話中のまま）。"""
    sid = store.start_session("a", start)
    if person_id is not None:
        store.set_session_person(sid, person_id)
    if end:
        store.end_session(sid, start + timedelta(minutes=5))
    return sid


def test_finished_sessions_are_per_person_and_skip_open_ones(store):
    store.get_or_create_plant("a", T0)
    taro = store.add_person("a", "たろう", T0)
    guest = _talk(store, T0)
    later = _talk(store, T0 + timedelta(days=2), person_id=taro.id)
    earlier = _talk(store, T0 + timedelta(days=1), person_id=taro.id)
    _talk(store, T0 + timedelta(days=3), person_id=taro.id, end=False)  # 会話中は数えない
    assert [s.id for s in store.finished_sessions("a", taro.id)] == [earlier, later]
    assert [s.id for s in store.finished_sessions("a", None)] == [guest]


def test_watering_count_is_per_person(store):
    store.get_or_create_plant("a", T0)
    taro = store.add_person("a", "たろう", T0)
    store.add_watering("a", None, _talk(store, T0), T0)
    for day in (1, 2):
        sid = _talk(store, T0 + timedelta(days=day), person_id=taro.id)
        store.add_watering("a", taro.id, sid, T0 + timedelta(days=day))
    assert store.watering_count("a", None) == 1
    assert store.watering_count("a", taro.id) == 2


def test_watering_before_giving_a_name_counts_for_that_person(store):
    # 「お水あげたよ」と言ってから名乗った → 水やりはその人のぶんとして数える（会話の相手で数える）
    store.get_or_create_plant("a", T0)
    taro = store.add_person("a", "たろう", T0)
    sid = store.start_session("a", T0)
    store.add_watering("a", None, sid, T0)  # まだ名乗っていないので person_id は NULL で記録される
    store.set_session_person(sid, taro.id)  # そのあと名乗った
    store.end_session(sid, T0 + timedelta(minutes=5))
    assert store.watering_count("a", taro.id) == 1
    assert store.watering_count("a", None) == 0


def test_watering_count_skips_conversations_in_progress(store):
    # 会話中の水やりは、話した回数（finished_sessions）と同じく、会話が終わるまで数えない
    store.get_or_create_plant("a", T0)
    sid = store.start_session("a", T0)
    store.add_watering("a", None, sid, T0)
    assert store.watering_count("a", None) == 0
