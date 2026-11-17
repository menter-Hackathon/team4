import json
import sqlite3
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta

import pytest

from agent.plant.personality import Traits
from agent.plant.store import NewMemory, PlantStore
from agent.plant.timeutil import JST
from scripts.dashboard import HOST, dashboard_state, make_server, plant_state

T0 = datetime(2026, 11, 15, 10, 0, tzinfo=JST)


def _reflect(store, plant_id, traits, now, *, memories=(), summary="要約", person_id=None):
    """空のセッションを 1 つ作って振り返り済みにする（成長曲線に 1 点・記憶・要約が増える）。"""
    sid = store.start_session(plant_id, now)
    if person_id is not None:
        store.set_session_person(sid, person_id)  # 会話中に名乗ったのと同じ状態にする
    store.end_session(sid, now)
    store.apply_reflection(
        plant_id, sid, summary, list(memories), [], traits, now, person_id=person_id
    )
    return sid


@pytest.fixture
def db(tmp_path):
    """2 回振り返った株 a1 と、知っている人たろうがいる DB のパス。"""
    path = str(tmp_path / "f.db")
    store = PlantStore(path)
    store.get_or_create_plant("a1", T0)
    _reflect(
        store,
        "a1",
        Traits(0.55, 0.5, 0.5, 0.5),
        T0 + timedelta(hours=1),
        memories=[NewMemory("カレーが好き", "preference", 0.9)],
        summary="カレーの話をした",
    )
    _reflect(store, "a1", Traits(0.6, 0.45, 0.5, 0.5), T0 + timedelta(hours=2), summary="二回目")
    taro = store.add_person("a1", "たろう", T0)
    store.set_dialect_override(taro.id, "kansai")
    _reflect(
        store,
        "a1",
        Traits(0.5, 0.55, 0.5, 0.5),
        T0 + timedelta(hours=3),
        memories=[NewMemory("サッカー部", "event", 0.8)],
        summary="部活の話",
        person_id=taro.id,
    )
    store.close()
    return path


def test_readonly_store_does_not_create_a_missing_file(tmp_path):
    path = tmp_path / "none.db"
    with pytest.raises(sqlite3.OperationalError):
        PlantStore(str(path), readonly=True)
    assert not path.exists()


def test_readonly_store_rejects_writes(db):
    store = PlantStore(db, readonly=True)
    with pytest.raises(sqlite3.OperationalError):
        store.start_session("a1", T0)
    store.close()


def test_plant_state_has_traits_history_memories_and_summary(db):
    store = PlantStore(db, readonly=True)
    state = plant_state(store, "a1", T0 + timedelta(hours=4))
    store.close()
    assert state["found"] and state["name"] == "ふたまる"
    guest = state["views"][0]
    assert guest["key"] == "guest" and guest["person_id"] is None
    assert guest["traits"]["talkativeness"] == 0.6
    # 初期値 + 振り返り 2 回 = 3 点。人の振り返りは株の成長曲線に入らない
    assert [h["traits"]["talkativeness"] for h in guest["history"]] == [0.5, 0.55, 0.6]
    assert guest["history"][0]["session_id"] is None
    # お客さんには共通の記憶だけ。人だけの記憶（サッカー部）は見せない
    assert [m["content"] for m in guest["memories"]] == ["カレーが好き"]
    assert guest["last_summary"] == "二回目"
    assert guest["dialect"] == "standard"


def test_person_view_uses_their_traits_memories_and_dialect(db):
    store = PlantStore(db, readonly=True)
    state = plant_state(store, "a1", T0 + timedelta(hours=4))
    store.close()
    taro = state["views"][1]
    assert taro["label"] == "たろう" and taro["key"] == f"person-{taro['person_id']}"
    assert taro["traits"]["affection"] == 0.55
    assert len(taro["history"]) == 2  # 登録した時点 + 振り返り 1 回
    by_content = {m["content"]: m for m in taro["memories"]}
    assert by_content["サッカー部"]["shared"] is False
    assert by_content["カレーが好き"]["shared"] is True
    assert taro["last_summary"] == "部活の話"
    assert taro["dialect"] == "kansai"


def test_memories_follow_prompt_ranking_and_limit(tmp_path):
    path = str(tmp_path / "m.db")
    store = PlantStore(path)
    store.get_or_create_plant("a1", T0)
    # 重要度の違う記憶を 12 個。古いもの（3 週間前）は重要度が高くても下がる
    old = [NewMemory("むかしの大事なこと", "event", 1.0)]
    _reflect(store, "a1", Traits(), T0 - timedelta(days=21), memories=old)
    new = [NewMemory(f"記憶{i}", "event", 0.3 + i * 0.05) for i in range(12)]
    _reflect(store, "a1", Traits(), T0, memories=new)
    state = plant_state(store, "a1", T0)
    store.close()
    contents = [m["content"] for m in state["views"][0]["memories"]]
    assert len(contents) == 10
    assert contents[0] == "記憶11"  # 新しくて一番重要
    assert "むかしの大事なこと" not in contents  # 1.0 × 0.125 = 0.125 で圏外


def test_unknown_plant_is_reported_as_not_found(db):
    store = PlantStore(db, readonly=True)
    assert plant_state(store, "zz", T0) == {"plant_id": "zz", "found": False}
    store.close()


def test_dashboard_state_reports_a_missing_db(tmp_path):
    state = dashboard_state(str(tmp_path / "none.db"), ["a1"], T0)
    assert "error" in state and "plants" not in state


def test_dashboard_state_lists_plants_in_the_given_order(db):
    state = dashboard_state(db, ["zz", "a1"], T0)
    assert [p["plant_id"] for p in state["plants"]] == ["zz", "a1"]


def test_server_listens_on_localhost_and_serves_html_and_json(db):
    server = make_server(db, ["a1"], 0)  # 0 = 空いているポート
    assert server.server_address[0] == HOST == "127.0.0.1"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://{HOST}:{server.server_address[1]}"
    try:
        with urllib.request.urlopen(base + "/") as res:
            assert "ふたまる" in res.read().decode("utf-8")
        with urllib.request.urlopen(base + "/api/state") as res:
            state = json.loads(res.read())
        assert state["plants"][0]["plant_id"] == "a1"
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(base + "/nope")
        assert e.value.code == 404
    finally:
        server.shutdown()
        server.server_close()
