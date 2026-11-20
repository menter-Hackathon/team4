import json
import socket
import sqlite3
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta

import pytest

from agent.plant.personality import Traits
from agent.plant.store import NewMemory, PlantStore
from agent.plant.timeutil import JST
from scripts import dashboard
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
    # お客さんにはお客さんの記憶だけ。人の記憶（サッカー部）は見せない
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
    # たろうにはたろうの記憶だけ。お客さんとの記憶（カレーが好き）は混ぜない（#70）
    assert [m["content"] for m in taro["memories"]] == ["サッカー部"]
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


def test_server_host_can_be_changed_for_docker(db):
    # Docker のコンテナの中では 0.0.0.0 で待ち受ける（PC 側の公開は compose.yaml で 127.0.0.1 に絞る）
    server = make_server(db, ["a1"], 0, host="0.0.0.0")
    try:
        assert server.server_address[0] == "0.0.0.0"
    finally:
        server.server_close()


# --- 起動を楽にするオプション（--day・--open・短いコマンド） ---
def test_day_option_picks_the_demo_db():
    args = dashboard.parse_args(["--day", "30"])
    assert args.db == "data/demo-day30.db"
    assert args.plants == "a1,b1"  # 省略したら a1,b1


def test_db_defaults_to_the_live_db():
    assert dashboard.parse_args([]).db == "data/futamaru.db"


def test_day_and_db_cannot_be_used_together():
    with pytest.raises(SystemExit):
        dashboard.parse_args(["--day", "1", "--db", "x.db"])


def test_short_command_name_runs_the_dashboard():
    # uv run futamaru-dashboard で起動できる（pyproject.toml の [project.scripts]）
    import tomllib
    from pathlib import Path

    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["scripts"]["futamaru-dashboard"] == "scripts.dashboard:main"


def test_wsl_opens_the_windows_browser():
    url = "http://127.0.0.1:8765"
    # WSL の中では Python からブラウザを開けないので、Windows の explorer.exe に開かせる
    assert dashboard.browser_command(url, "5.15.153.1-microsoft-standard-WSL2") == [
        "explorer.exe",
        url,
    ]
    # Mac・Windows・ふつうの Linux は Python の webbrowser に任せる（None）
    assert dashboard.browser_command(url, "24.1.0") is None


def test_busy_port_gives_a_short_message(db, capsys):
    busy = socket.socket()
    busy.bind((HOST, 0))  # 空いている番号を 1 つ使っておく
    busy.listen()
    try:
        with pytest.raises(SystemExit) as e:
            dashboard.main(["--db", db, "--port", str(busy.getsockname()[1])])
    finally:
        busy.close()
    assert e.value.code == 1
    assert "もう使われています" in capsys.readouterr().out


def test_open_flag_opens_the_browser(db, monkeypatch):
    opened = []
    monkeypatch.setattr(dashboard, "open_browser", opened.append)

    class FakeServer:
        server_address = (HOST, 8765)

        def serve_forever(self):
            raise KeyboardInterrupt  # すぐに Ctrl+C されたことにする

        def server_close(self):
            pass

    monkeypatch.setattr(dashboard, "make_server", lambda *a, **k: FakeServer())
    dashboard.main(["--db", db, "--open"])
    assert opened == ["http://127.0.0.1:8765"]


def test_views_include_the_plant_type(db, monkeypatch, tmp_path):
    # イラストがまだ無いフォルダを指す（絵文字で出す状態）
    monkeypatch.setattr(dashboard, "TYPE_IMAGE_DIR", tmp_path / "types")
    store = PlantStore(db, readonly=True)
    state = plant_state(store, "a1", T0 + timedelta(hours=4))
    store.close()
    guest, taro = state["views"]
    # 株の性格（0.6, 0.45, 0.5, 0.5）: おしゃべり度が「やや高い」に出たのでタイプが決まる
    assert guest["type"]["key"] == "mint"
    assert guest["type"]["name"] == "ミントふたまる"
    assert guest["type"]["emoji"] and guest["type"]["description"]
    assert guest["type"]["image"] is None  # イラストが無いので、画面は絵文字を出す
    # たろうの性格（0.5, 0.55, 0.5, 0.5）は 4 軸とも「ふつう」なので、まだふたば
    assert taro["type"]["key"] == "sprout"


def test_type_image_is_listed_when_the_file_exists(db, monkeypatch, tmp_path):
    images = tmp_path / "types"
    images.mkdir()
    (images / "mint.png").write_bytes(b"\x89PNG fake")
    monkeypatch.setattr(dashboard, "TYPE_IMAGE_DIR", images)
    store = PlantStore(db, readonly=True)
    guest = plant_state(store, "a1", T0 + timedelta(hours=4))["views"][0]
    store.close()
    assert guest["type"]["image"] == "/types/mint.png"


def test_server_serves_type_images_only_for_known_types(db, monkeypatch, tmp_path):
    images = tmp_path / "types"
    images.mkdir()
    (images / "mint.png").write_bytes(b"\x89PNG fake")
    (images / "secret.png").write_bytes(b"not a type")  # 17 タイプ以外の名前のファイル
    monkeypatch.setattr(dashboard, "TYPE_IMAGE_DIR", images)
    server = make_server(db, ["a1"], 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://{HOST}:{server.server_address[1]}"
    try:
        with urllib.request.urlopen(base + "/types/mint.png") as res:
            assert res.headers["Content-Type"] == "image/png"
            assert res.read() == b"\x89PNG fake"
        # 一覧に無い名前・まだ描かれていないタイプ・フォルダの外を指す道は、どれも 404
        for path in (
            "/types/secret.png",
            "/types/sunflower.png",
            "/types/../dashboard.py",
            "/types/%2e%2e/dashboard.py",
            "/types/mint.PNG",
        ):
            with pytest.raises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(base + path)
            assert e.value.code == 404, path
    finally:
        server.shutdown()
        server.server_close()


# --- こんなふうに育てられた（育て方の記録、仕様 §6.6） ---
def test_growth_counts_talks_waterings_and_time_band(tmp_path):
    path = str(tmp_path / "g.db")
    store = PlantStore(path)
    store.get_or_create_plant("a1", T0)
    taro = store.add_person("a1", "たろう", T0)
    # たろうと 3 回。どれも 22:30 に始まり（深夜）、2 日おき。持ち主の発話は 2・4・6 回
    for i, turns in enumerate((2, 4, 6)):
        start = datetime(2026, 11, 15 + 2 * i, 22, 30, tzinfo=JST)
        sid = store.start_session("a1", start)
        store.set_session_person(sid, taro.id)
        for _ in range(turns):
            store.add_utterance(sid, "user", "ねえ", start)
        if i == 0:
            store.add_watering("a1", taro.id, sid, start)
        store.end_session(sid, start + timedelta(minutes=5))
    view = plant_state(store, "a1", datetime(2026, 11, 20, 12, 0, tzinfo=JST))["views"][1]
    store.close()
    growth = view["growth"]
    assert growth["talks"] == 3
    assert growth["waterings"] == 1
    assert growth["time_band"] == "深夜"
    assert growth["interval_days"] == 2.0
    assert growth["avg_turns"] == 4.0


def test_growth_for_a_person_without_talks_is_empty(tmp_path):
    path = str(tmp_path / "g.db")
    store = PlantStore(path)
    store.get_or_create_plant("a1", T0)
    store.add_person("a1", "はなこ", T0)  # 登録しただけで、まだ話していない
    view = plant_state(store, "a1", T0 + timedelta(hours=1))["views"][1]
    store.close()
    assert view["growth"] == {
        "talks": 0,
        "waterings": 0,
        "time_band": None,
        "interval_days": None,
        "avg_turns": None,
        "biggest_change": None,
    }


def test_guest_growth_does_not_count_named_people(db):
    store = PlantStore(db, readonly=True)
    guest, taro = plant_state(store, "a1", T0 + timedelta(hours=4))["views"]
    store.close()
    assert guest["growth"]["talks"] == 2  # たろうとの 1 回は混ぜない
    assert taro["growth"]["talks"] == 1


def test_biggest_change_is_the_axis_that_moved_most(db):
    store = PlantStore(db, readonly=True)
    guest = plant_state(store, "a1", T0 + timedelta(hours=4))["views"][0]
    store.close()
    # 初期 0.5 → (0.55, 0.5, 0.5, 0.5) → (0.6, 0.45, 0.5, 0.5)。おしゃべり度が +0.1 でいちばん動いた
    assert guest["growth"]["biggest_change"] == {"trait": "talkativeness", "delta": 0.1}


def test_biggest_change_keeps_the_sign_when_a_trait_went_down(tmp_path):
    path = str(tmp_path / "g.db")
    store = PlantStore(path)
    store.get_or_create_plant("a1", T0)
    _reflect(store, "a1", Traits(0.5, 0.2, 0.55, 0.5), T0 + timedelta(hours=1))
    guest = plant_state(store, "a1", T0 + timedelta(hours=2))["views"][0]
    store.close()
    assert guest["growth"]["biggest_change"] == {"trait": "affection", "delta": -0.3}


def test_type_json_has_detail_and_view_has_days(db):
    store = PlantStore(db, readonly=True)
    plant = plant_state(store, "a1", T0 + timedelta(days=2))
    store.close()
    guest = plant["views"][0]
    assert guest["type"]["detail"]
    assert guest["days"] == 3  # 株を作った日が 1 日目


def test_html_has_the_result_screen_sections():
    html = dashboard.HTML_PATH.read_text(encoding="utf-8")
    for label in ("育てた結果", "せいかく", "こんなふうに育てられた", "くわしく"):
        assert label in html
    # 記憶・要約は LLM が作った文なので、HTML として入れない（textContent で入れる）
    assert "innerHTML" not in html


def test_days_is_at_least_one_when_the_plant_is_dated_in_the_future(db):
    # 発表用の DB は日付をずらして作るので、株を作った時刻が今より先になることがある。
    # そのときも「-1 日育てた」ではなく 1 日目と出す
    store = PlantStore(db, readonly=True)
    plant = plant_state(store, "a1", T0 - timedelta(days=3))
    store.close()
    assert plant["views"][0]["days"] == 1
