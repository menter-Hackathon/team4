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
