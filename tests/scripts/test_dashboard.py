import base64
import json
import sqlite3
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta

import pytest

from agent.plant.account import hash_password
from agent.plant.personality import Traits
from agent.plant.store import NewMemory, PlantStore
from agent.plant.timeutil import JST
from scripts import dashboard
from scripts.dashboard import (
    HOST,
    LoginBusyError,
    LoginGuard,
    check_login,
    dashboard_state,
    make_server,
    needs_login,
    parse_basic_auth,
    plant_state,
)

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


# --- ログイン（仕様 §6.6、2026-11-20） ---

PASSWORD = "correct horse battery"


def basic(name: str, password: str) -> str:
    """ブラウザが送るのと同じ Authorization ヘッダーを作る。"""
    return "Basic " + base64.b64encode(f"{name}:{password}".encode()).decode("ascii")


@pytest.fixture
def db_with_account(db):
    """db に、アカウント shion（パスワード PASSWORD）を足したもの。"""
    store = PlantStore(db)
    store.add_account("shion", hash_password(PASSWORD), T0)
    store.close()
    return db


def get(url: str, authorization: str | None = None):
    """GET して (ステータス, ヘッダー, 本文) を返す。4xx でも例外にしない。"""
    request = urllib.request.Request(url)
    if authorization:
        request.add_header("Authorization", authorization)
    try:
        with urllib.request.urlopen(request) as res:
            return res.status, res.headers, res.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


@pytest.fixture
def login_server(db_with_account):
    """ログインを求めるダッシュボードを動かし、その URL を返す。"""
    server = make_server(db_with_account, ["a1"], 0, require_login=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://{HOST}:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("127.0.0.1", False),
        ("localhost", False),
        ("::1", False),
        ("0.0.0.0", True),  # Docker。トンネルからも届く
        ("192.168.1.10", True),
        ("my-pc.local", True),  # 何を指すか分からない名前は、外から届くとみなす
    ],
)
def test_needs_login_everywhere_except_this_pc(host, expected):
    assert needs_login(host) is expected


def test_parse_basic_auth_reads_name_and_password():
    assert parse_basic_auth(basic("shion", "a:b:c")) == ("shion", "a:b:c")  # 「:」入りのパスワード
    assert parse_basic_auth(basic("shion", "")) == ("shion", "")
    for header in (
        None,
        "",
        "Bearer abc",
        "Basic !!!",
        "Basic " + "bm9jb2xvbg==",
    ):  # 最後は「:」無し
        assert parse_basic_auth(header) is None, header


def test_parse_basic_auth_rejects_non_ascii_headers():
    # http.server はヘッダーを latin-1 で読むので、ASCII 以外の文字が届くことがある（#117 のレビュー）
    assert parse_basic_auth("Basic é") is None
    assert parse_basic_auth("Basic c2hpb246cGFzcw==あ") is None


def test_check_login_accepts_only_the_right_password(db_with_account):
    assert check_login(db_with_account, basic("shion", PASSWORD)) == "shion"
    assert check_login(db_with_account, basic("shion", PASSWORD + "x")) is None
    assert check_login(db_with_account, basic("nobody", PASSWORD)) is None
    assert check_login(db_with_account, None) is None


def test_check_login_fails_closed_without_a_usable_db(tmp_path):
    # DB が無い
    assert check_login(str(tmp_path / "none.db"), basic("shion", PASSWORD)) is None
    # accounts テーブルの無い古い DB（エージェントをまだ起動し直していない）
    old = tmp_path / "old.db"
    conn = sqlite3.connect(old)
    conn.execute("CREATE TABLE plants (plant_id TEXT)")
    conn.close()
    assert check_login(str(old), basic("shion", PASSWORD)) is None


def test_login_server_returns_401_for_every_path_without_login(login_server):
    for path in ("/", "/api/state", "/types/mint.png", "/nope"):
        for auth in (None, basic("shion", "wrong password"), basic("nobody", PASSWORD)):
            status, headers, body = get(login_server + path, auth)
            assert status == 401, (path, auth)
            assert headers["WWW-Authenticate"].startswith("Basic ")  # ブラウザがログイン画面を出す
            assert "カレー" not in body.decode("utf-8")  # 記憶は返さない


def test_login_server_returns_401_for_a_non_ascii_header(login_server):
    # 前は例外で接続が切れていた（Cloudflare 越しでは 502）。ログインしていないのと同じく 401 にする
    status, headers, _ = get(login_server + "/api/state", "Basic é")
    assert status == 401
    assert headers["WWW-Authenticate"].startswith("Basic ")


class FakeClock:
    """LoginGuard に渡す、進め方を決められる時計（秒）。"""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_login_guard_remembers_a_successful_login_for_a_while(db_with_account, monkeypatch):
    calls = []
    real_check = dashboard.check_login

    def counting_check(db_path, header):
        calls.append(header)
        return real_check(db_path, header)

    monkeypatch.setattr(dashboard, "check_login", counting_check)
    clock = FakeClock()
    guard = LoginGuard(db_with_account, clock=clock)
    auth = basic("shion", PASSWORD)

    # 1. 最初は計算する。60 秒の間は、同じヘッダーなら計算し直さない
    assert guard.check(auth) == "shion"
    clock.now += 59
    assert guard.check(auth) == "shion"
    assert len(calls) == 1
    # 2. 60 秒たったら、もう一度計算する
    clock.now += 2
    assert guard.check(auth) == "shion"
    assert len(calls) == 2
    # 3. 失敗は覚えない（毎回計算して、毎回 None）
    wrong = basic("shion", "wrong password")
    assert guard.check(wrong) is None
    assert guard.check(wrong) is None
    assert len(calls) == 4


def test_login_guard_limits_concurrent_password_checks(monkeypatch):
    # 外から大量に試されても、scrypt を同時に max_concurrent 個までしか計算しない（#117 のレビュー）
    lock = threading.Lock()
    running = 0
    peak = 0

    def slow_check(db_path, header):
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        threading.Event().wait(0.05)
        with lock:
            running -= 1
        return None

    monkeypatch.setattr(dashboard, "check_login", slow_check)
    guard = LoginGuard("unused.db", max_concurrent=2, wait_seconds=5)
    threads = [
        threading.Thread(target=guard.check, args=(basic(f"user{i}", "x" * 12),)) for i in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert peak == 2


def test_login_guard_reports_busy_when_no_slot_frees_up(monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def blocking_check(db_path, header):
        started.set()
        release.wait(5)
        return None

    monkeypatch.setattr(dashboard, "check_login", blocking_check)
    guard = LoginGuard("unused.db", max_concurrent=1, wait_seconds=0.05)
    holder = threading.Thread(target=guard.check, args=(basic("a", "x" * 12),))
    holder.start()
    started.wait(5)
    try:
        with pytest.raises(LoginBusyError):
            guard.check(basic("b", "x" * 12))
    finally:
        release.set()
        holder.join()


def test_login_server_returns_503_while_busy(db_with_account, monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def blocking_check(db_path, header):
        started.set()
        release.wait(5)
        return None

    monkeypatch.setattr(dashboard, "check_login", blocking_check)
    guard = LoginGuard(db_with_account, max_concurrent=1, wait_seconds=0.05)
    server = make_server(db_with_account, ["a1"], 0, require_login=True, login_guard=guard)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://{HOST}:{server.server_address[1]}"
    holder = threading.Thread(target=get, args=(base + "/api/state", basic("a", "x" * 12)))
    holder.start()
    started.wait(5)
    try:
        status, headers, body = get(base + "/api/state", basic("b", "x" * 12))
        assert status == 503
        assert headers["Retry-After"] == "1"
        assert "カレー" not in body.decode("utf-8")  # 記憶は返さない
    finally:
        release.set()
        holder.join()
        server.shutdown()
        server.server_close()


def test_login_server_serves_pages_after_login(login_server):
    auth = basic("shion", PASSWORD)
    status, _, body = get(login_server + "/", auth)
    assert status == 200 and "ふたまる" in body.decode("utf-8")
    status, _, body = get(login_server + "/api/state", auth)
    assert status == 200
    assert json.loads(body)["plants"][0]["plant_id"] == "a1"
    assert get(login_server + "/nope", auth)[0] == 404


def test_server_on_all_interfaces_requires_login_by_default(db_with_account):
    # Docker と同じ 0.0.0.0 で待ち受けると、何も指定しなくてもログインを求める
    server = make_server(db_with_account, ["a1"], 0, host="0.0.0.0")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        assert get(base + "/api/state")[0] == 401
        assert get(base + "/api/state", basic("shion", PASSWORD))[0] == 200
    finally:
        server.shutdown()
        server.server_close()


def test_server_on_localhost_needs_no_login_by_default(db):
    # uv run で 127.0.0.1 だけで待ち受けるときは、今までどおりログインなし（この PC からしか開けない）
    server = make_server(db, ["a1"], 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        assert get(f"http://{HOST}:{server.server_address[1]}/api/state")[0] == 200
    finally:
        server.shutdown()
        server.server_close()
