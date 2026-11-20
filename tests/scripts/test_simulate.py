import json
from pathlib import Path

from agent.plant.store import PlantStore
from scripts.simulate import simulate
from tests.fakes import FakeCompleter, reflection_json

UP = reflection_json(deltas={"affection": 0.05})


async def test_simulate_records_and_reflects(tmp_path):
    log = {
        "plant_id": "s",
        "sessions": [
            {
                "started_at": "2026-11-01T19:00:00+09:00",
                "turns": [
                    {"role": "user", "text": "ただいま"},
                    {"role": "plant", "text": "おかえり"},
                ],
            },
            {
                "started_at": "2026-11-02T19:00:00+09:00",
                "turns": [{"role": "user", "text": "かわいいね"}],
            },
        ],
    }
    store = PlantStore(str(tmp_path / "s.db"))
    hist = await simulate(store, log, FakeCompleter([UP, UP]))
    assert len(hist) == 3 and hist[-1].traits.affection > hist[0].traits.affection
    assert hist[1].recorded_at.startswith("2026-11-01T19:00")
    utt = store.get_utterances(1)
    assert [u.text for u in utt] == ["ただいま", "おかえり"] and utt[
        1
    ].created_at == "2026-11-01T19:00:10+09:00"


def test_samples_are_valid():
    for p in ["scripts/samples/plant-a.json", "scripts/samples/plant-b.json"]:
        log = json.loads(Path(p).read_text(encoding="utf-8"))
        assert len(log["sessions"]) == 7
        assert all(t["role"] in ("user", "plant") for s in log["sessions"] for t in s["turns"])
    a = json.loads(Path("scripts/samples/plant-a.json").read_text(encoding="utf-8"))
    b = json.loads(Path("scripts/samples/plant-b.json").read_text(encoding="utf-8"))
    assert a["plant_id"] != b["plant_id"]
    assert all(sum(t["role"] == "user" for t in s["turns"]) >= 5 for s in a["sessions"])
    assert all(1 <= sum(t["role"] == "user" for t in s["turns"]) <= 2 for s in b["sessions"])


async def test_simulate_records_watering_from_user_turns(tmp_path):
    # 成長の早送りでも、会話の中の「お水あげたよ」を水やりとして記録する（#83）
    log = {
        "plant_id": "w",
        "sessions": [
            {
                "started_at": "2026-11-01T19:00:00+09:00",
                "turns": [
                    {"role": "user", "text": "お水あげたよ"},
                    {"role": "plant", "text": "ありがとう"},
                    {"role": "user", "text": "水やりもしたよ"},  # 同じ会話の 2 回目は数えない
                ],
            },
        ],
    }
    store = PlantStore(str(tmp_path / "w.db"))
    await simulate(store, log, FakeCompleter([UP]))
    assert store.last_watered_at("w").isoformat() == "2026-11-01T19:00:00+09:00"


# --- 発表用の早送り（#77）: 「何日目か」で書いた会話ログと、人を指定した育て方 ---
from datetime import date  # noqa: E402

from scripts.simulate import resolve_sessions  # noqa: E402

DAY_LOG = {
    "plant_id": "a1",
    "person": "ショーン",
    "sessions": [
        {"day": 1, "time": "19:00", "turns": [{"role": "user", "text": "はじめまして"}]},
        {"day": 3, "time": "07:30", "turns": [{"role": "user", "text": "お水あげたよ"}]},
        {"day": 5, "time": "19:00", "turns": [{"role": "user", "text": "ただいま"}]},
    ],
}


def test_resolve_sessions_maps_days_so_that_today_is_day_n():
    # 今日（11/22）が 5 日目 → 1 日目は 11/18。5 日目の会話は「今日これから話す」ので入れない
    sessions, day1 = resolve_sessions(DAY_LOG, today=date(2026, 11, 22), day=5)
    assert day1.isoformat() == "2026-11-18"
    assert [s["started_at"] for s in sessions] == [
        "2026-11-18T19:00:00+09:00",
        "2026-11-20T07:30:00+09:00",
    ]


def test_resolve_sessions_day_one_has_no_sessions():
    sessions, day1 = resolve_sessions(DAY_LOG, today=date(2026, 11, 22), day=1)
    assert sessions == [] and day1.isoformat() == "2026-11-22"


def test_resolve_sessions_keeps_started_at_logs():
    log = {"plant_id": "s", "sessions": [{"started_at": "2026-11-01T19:00:00+09:00", "turns": []}]}
    sessions, day1 = resolve_sessions(log)
    assert sessions[0]["started_at"] == "2026-11-01T19:00:00+09:00" and day1 is None


async def test_simulate_grows_the_person_and_starts_on_day_one(tmp_path):
    store = PlantStore(str(tmp_path / "d.db"))
    await simulate(store, DAY_LOG, FakeCompleter([UP, UP]), today=date(2026, 11, 22), day=5)
    people = store.get_people("a1")
    assert [p.name for p in people] == ["ショーン"]
    # その人の性格が育ち、株（お客さん用）の性格は動かない
    assert people[0].traits.affection > 0.5 and store.get_plant("a1").traits.affection == 0.5
    # 株は 1 日目（11/18）の朝に育ち始め、水やりも記録される
    assert store.plant_created_at("a1").isoformat() == "2026-11-18T00:00:00+09:00"
    assert store.last_watered_at("a1").isoformat() == "2026-11-20T07:30:00+09:00"


async def test_simulate_on_day_one_only_registers(tmp_path):
    store = PlantStore(str(tmp_path / "d1.db"))
    await simulate(store, DAY_LOG, FakeCompleter([]), today=date(2026, 11, 22), day=1)
    assert [p.name for p in store.get_people("a1")] == ["ショーン"]
    assert store.plant_created_at("a1").isoformat() == "2026-11-22T00:00:00+09:00"


async def test_simulate_reuses_a_registered_person(tmp_path):
    store = PlantStore(str(tmp_path / "r.db"))
    await simulate(store, DAY_LOG, FakeCompleter([UP, UP]), today=date(2026, 11, 22), day=5)
    await simulate(store, DAY_LOG, FakeCompleter([UP, UP]), today=date(2026, 11, 22), day=5)
    assert [p.name for p in store.get_people("a1")] == ["ショーン"]  # 同じ名前の人を二重に作らない


def test_demo_logs_are_valid():
    for name, person in [("demo-sean.json", "ショーン"), ("demo-chamu.json", "ちゃむ")]:
        log = json.loads(Path(f"scripts/samples/{name}").read_text(encoding="utf-8"))
        assert log["person"] == person
        days = [s["day"] for s in log["sessions"]]
        assert days == sorted(days) and 1 <= days[0] and days[-1] < 30  # 30 日目はデモの当日
        assert all(t["role"] in ("user", "plant") for s in log["sessions"] for t in s["turns"])
