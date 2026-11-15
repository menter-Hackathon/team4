import asyncio
from datetime import datetime, timedelta

import pytest

from agent.plant.conversation import PlantConversation
from agent.plant.store import PlantStore
from agent.plant.timeutil import JST
from tests.fakes import FakeClock, FakeCompleter, reflection_json

T0 = datetime(2026, 11, 15, 19, 0, tzinfo=JST)
WAKE_WORDS = ("ふたまる", "二丸", "ふた丸", "2丸")


async def settle():
    for _ in range(5):
        await asyncio.sleep(0)


@pytest.fixture
def store(tmp_path):
    s = PlantStore(str(tmp_path / "c.db"))
    yield s
    s.close()


@pytest.fixture
def clock():
    return FakeClock(T0)


@pytest.fixture
def sent():
    return []


@pytest.fixture
def completer():
    return FakeCompleter([reflection_json(summary=f"会話{i}", deltas={"affection": 0.05}) for i in range(10)])


def make_conv(store, clock, completer, send):
    return PlantConversation(
        store=store, plant_id="a", complete_json=completer, send_expression=send, clock=clock,
        wake_words=WAKE_WORDS, sleep_after=60.0,
    )


@pytest.fixture
async def conv(store, clock, completer, sent):
    async def send(name):
        sent.append(name)

    c = make_conv(store, clock, completer, send)
    await c.start()
    await settle()
    yield c
    await c.close()


def session_ids(store):
    return [r[0] for r in store._conn.execute("SELECT id FROM sessions ORDER BY id").fetchall()]


def current_sid(store):
    return session_ids(store)[-1]


async def test_start_creates_plant_recovers_and_sleepy(store, clock, completer, sent):
    store.get_or_create_plant("a", T0 - timedelta(days=1))
    old = store.start_session("a", T0 - timedelta(hours=2))
    store.add_utterance(old, "user", "ただいま", T0 - timedelta(hours=2))

    async def send(name):
        sent.append(name)

    c = make_conv(store, clock, completer, send)
    await c.start()
    await settle()
    assert store.get_session(old).reflected and sent == ["sleepy"] and not c.awake


async def test_start_creates_new_plant(conv, store):
    assert store.get_plant("a").traits.affection == 0.5


async def test_sleeping_ignores_non_wake(conv, store):
    assert (await conv.on_user_turn("おはよう")).kind == "ignore"
    assert not conv.awake and session_ids(store) == []


async def test_wake_only_greets(conv, store):
    d = await conv.on_user_turn("フタマル！")
    assert d.kind == "greet" and "あなたは観葉植物のふたまる" in d.instructions and conv.awake
    assert store.get_utterances(current_sid(store)) == []


async def test_wake_with_rest_replies_and_records(conv, store):
    d = await conv.on_user_turn("ふたまる、今日カレー食べた")
    assert (d.kind, d.text) == ("reply", "今日カレー食べた") and d.instructions
    assert [u.text for u in store.get_utterances(current_sid(store))] == ["今日カレー食べた"]


async def test_awake_reply_has_no_instructions_and_blank_is_ignored(conv, store):
    await conv.on_user_turn("ふたまる")
    d = await conv.on_user_turn("ただいま")
    assert (d.kind, d.text, d.instructions) == ("reply", "ただいま", None)
    assert (await conv.on_user_turn("  ")).kind == "ignore"
    assert [u.text for u in store.get_utterances(current_sid(store))] == ["ただいま"]


async def test_plant_utterance_only_recorded_when_awake(conv, store):
    conv.on_plant_utterance("ねむい")
    assert session_ids(store) == []
    await conv.on_user_turn("ふたまる")
    conv.on_plant_utterance("おかえり！")
    conv.on_plant_utterance("")
    assert [(u.role, u.text) for u in store.get_utterances(current_sid(store))] == [("plant", "おかえり！")]


async def test_silence_sleeps_and_reflects(conv, store, clock, sent):
    await conv.on_user_turn("ふたまる、ただいま")
    conv.on_plant_utterance("おかえり！")
    clock.advance(60)
    assert await conv.tick() == "slept"
    await conv.wait_reflections()
    await settle()
    s = store.get_session(current_sid(store))
    assert s.ended_at and s.reflected and sent[-1] == "sleepy" and not conv.awake


async def test_tick_none_while_sleeping(conv, clock):
    clock.advance(600)
    assert await conv.tick() == "none"


async def test_next_wake_uses_updated_summary(conv, clock):
    await conv.on_user_turn("ふたまる、テストだった")
    clock.advance(60)
    await conv.tick()
    await conv.wait_reflections()
    clock.advance(3600)
    d = await conv.on_user_turn("ふたまる")
    assert "前回の会話: 会話0" in d.instructions


async def test_wake_while_reflection_pending(store, clock, sent):
    gate = asyncio.Event()
    completer = FakeCompleter([reflection_json()], gate=gate)

    async def send(name):
        sent.append(name)

    c = make_conv(store, clock, completer, send)
    await c.start()
    await c.on_user_turn("ふたまる、ただいま")
    clock.advance(60)
    assert await c.tick() == "slept"
    await settle()
    assert len(completer.calls) == 1  # reflection が LLM 待ちで止まっている
    d = await c.on_user_turn("ふたまる")
    assert d.kind == "greet" and "前回の会話" not in d.instructions
    gate.set()
    await c.close()


async def test_nudge_from_tick(conv, clock):
    await conv.on_user_turn("ふたまる")
    clock.advance(25)  # talkativeness 0.5 → 25秒
    assert await conv.tick() == "nudge"
    clock.advance(1)
    assert await conv.tick() == "none"


async def test_agent_busy_pauses_sleep(conv, clock):
    await conv.on_user_turn("ふたまる")
    conv.on_agent_state("speaking")
    clock.advance(120)
    assert await conv.tick() == "none"
    conv.on_agent_state("listening")
    clock.advance(24)
    assert await conv.tick() == "none"  # 植物の発話終了から数え直している
    clock.advance(35)
    assert await conv.tick() == "nudge"
    clock.advance(1)
    assert await conv.tick() == "slept"


async def test_listening_and_thinking_only_when_awake(conv, sent):
    conv.on_user_speaking(True)
    conv.on_agent_state("thinking")
    await settle()
    assert sent == ["sleepy"]
    await conv.on_user_turn("ふたまる")
    conv.on_user_speaking(True)
    conv.on_agent_state("thinking")
    await settle()
    assert sent[-2:] == ["listening", "thinking"]


async def test_close_while_awake_ends_and_reflects(conv, store):
    await conv.on_user_turn("ふたまる、またね")
    await conv.close()
    s = store.get_session(current_sid(store))
    assert s.ended_at and s.reflected and not conv.awake


async def test_failing_send_does_not_raise(store, clock, completer):
    async def send(name):
        raise RuntimeError("device not connected")

    c = make_conv(store, clock, completer, send)
    await c.start()
    c.express("happy")
    await settle()
    await c.close()


# --- final review I3 / I5 ---
async def test_plant_utterance_is_stored_without_tags(conv, store):
    await conv.on_user_turn("ふたまる")
    conv.on_plant_utterance("[happy]おかえり！")
    assert [u.text for u in store.get_utterances(current_sid(store))] == ["おかえり！"]


async def test_start_does_not_wait_for_recovery(store, clock, sent):
    store.get_or_create_plant("a", T0 - timedelta(days=1))
    old = store.start_session("a", T0 - timedelta(hours=2))
    store.add_utterance(old, "user", "ただいま", T0 - timedelta(hours=2))
    gate = asyncio.Event()
    completer = FakeCompleter([reflection_json()], gate=gate)

    async def send(name):
        sent.append(name)

    c = make_conv(store, clock, completer, send)
    await asyncio.wait_for(c.start(), timeout=1)  # LLM が止まっていても起動は待たない
    assert (await c.on_user_turn("ふたまる")).kind == "greet"
    gate.set()
    await c.close()
    assert store.get_session(old).reflected


async def test_recovery_is_capped(store, clock, sent):
    store.get_or_create_plant("a", T0 - timedelta(days=1))
    for i in range(5):
        sid = store.start_session("a", T0 - timedelta(hours=10 - i))
        store.add_utterance(sid, "user", f"発話{i}", T0 - timedelta(hours=10 - i))
    completer = FakeCompleter([reflection_json() for _ in range(10)])

    async def send(name):
        sent.append(name)

    c = make_conv(store, clock, completer, send)
    await c.start()
    await c.wait_reflections()
    assert len(completer.calls) == 3 and len(store.unreflected_sessions("a")) == 2


async def test_session_ending_right_after_start_is_reflected_once(store, clock, sent):
    completer = FakeCompleter([reflection_json() for _ in range(5)])

    async def send(name):
        sent.append(name)

    c = make_conv(store, clock, completer, send)
    await c.start()
    await c.on_user_turn("ふたまる、ただいま")
    clock.advance(60)
    await c.tick()
    await c.close()
    assert len(completer.calls) == 1
