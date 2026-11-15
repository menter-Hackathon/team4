import pytest

from agent.plant.personality import Traits, apply_deltas, level, nudge_delay


def test_traits_default_all_half():
    assert Traits().as_dict() == {
        "talkativeness": 0.5,
        "affection": 0.5,
        "energy": 0.5,
        "curiosity": 0.5,
    }


@pytest.mark.parametrize(
    "v,lv",
    [(0.0, "low"), (0.33, "low"), (0.34, "mid"), (0.66, "mid"), (0.67, "high"), (1.0, "high")],
)
def test_level_boundaries(v, lv):
    assert level(v) == lv


def test_deltas_clamped_to_005():
    t = apply_deltas(
        Traits(), {"affection": 0.2, "energy": -0.3, "curiosity": 0.01}, recent_user_turns=50
    )
    assert t.affection == pytest.approx(0.55) and t.energy == pytest.approx(0.45)
    assert t.curiosity == pytest.approx(0.51)


def test_values_clamped_to_unit_range():
    t = apply_deltas(Traits(affection=0.98, energy=0.02), {"affection": 0.05, "energy": -0.05}, 50)
    assert t.affection == 1.0 and t.energy == 0.0


def test_talkativeness_blend():
    # delta = 0.5*0.04 + 0.5*(1.0-0.5)*0.1 = 0.045
    assert apply_deltas(Traits(), {"talkativeness": 0.04}, 100).talkativeness == pytest.approx(
        0.545
    )
    assert apply_deltas(Traits(), {"talkativeness": 0.04}, 300).talkativeness == pytest.approx(
        0.545
    )
    # delta = 0 + 0.5*(0-0.5)*0.1 = -0.025
    assert apply_deltas(Traits(), {}, 0).talkativeness == pytest.approx(0.475)
    # llm_delta は先に ±0.05 にクランプ: 0.5*0.05 + 0.025 = 0.05
    assert apply_deltas(Traits(), {"talkativeness": 0.5}, 100).talkativeness == pytest.approx(0.55)


def test_bad_delta_values_are_zero():
    t = apply_deltas(
        Traits(), {"energy": "high", "curiosity": None, "affection": True, "unknown": 0.05}, 50
    )
    assert t.energy == 0.5 and t.curiosity == 0.5 and t.affection == 0.5


@pytest.mark.parametrize("t,expected", [(0.9, 13.0), (0.5, 25.0), (0.3, 31.0), (0.29, None)])
def test_nudge_delay(t, expected):
    result = nudge_delay(t)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected)
