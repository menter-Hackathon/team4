import asyncio
import json
from datetime import datetime, timedelta

import pytest

from agent.plant import conversation as conversation_module
from agent.plant.conversation import PlantConversation
from agent.plant.prompt import ASK_NAME_INSTRUCTION
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
    return FakeCompleter(
        [reflection_json(summary=f"会話{i}", deltas={"affection": 0.05}) for i in range(10)]
    )


def make_conv(store, clock, completer, send):
    return PlantConversation(
        store=store,
        plant_id="a",
        complete_json=completer,
        send_expression=send,
        clock=clock,
        wake_words=WAKE_WORDS,
        sleep_after=60.0,
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


@pytest.mark.parametrize("text", ["。", "…", "！？", "、。 "])
async def test_awake_punctuation_only_is_ignored_and_not_recorded(conv, store, text):
    await conv.on_user_turn("ふたまる")
    await conv.on_user_turn("ただいま")
    assert (await conv.on_user_turn(text)).kind == "ignore"
    assert [u.text for u in store.get_utterances(current_sid(store))] == ["ただいま"]


async def test_plant_utterance_only_recorded_when_awake(conv, store):
    conv.on_plant_utterance("ねむい")
    assert session_ids(store) == []
    await conv.on_user_turn("ふたまる")
    conv.on_plant_utterance("おかえり！")
    conv.on_plant_utterance("")
    assert [(u.role, u.text) for u in store.get_utterances(current_sid(store))] == [
        ("plant", "おかえり！")
    ]


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


async def test_next_wake_uses_updated_summary(conv, clock, completer):
    completer.responses.insert(0, identify_json())  # 名前探しの応答（名乗っていない）
    await conv.on_user_turn("ふたまる、テストだった")
    clock.advance(60)
    await conv.tick()
    await conv.wait_reflections()
    clock.advance(3600)
    d = await conv.on_user_turn("ふたまる")
    assert "前回の会話: 会話0" in d.instructions


async def test_wake_while_reflection_pending(store, clock, sent, monkeypatch):
    # 名前探しも gate で止まるので、すぐ時間切れにして「名乗っていない」扱いにする
    monkeypatch.setattr(conversation_module, "IDENTIFY_TIMEOUT", 0.01)
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
    assert (
        len(completer.calls) == 2
    )  # 名前探し（時間切れ）の後、reflection が LLM 待ちで止まっている
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
    completer = FakeCompleter([identify_json()] + [reflection_json() for _ in range(5)])

    async def send(name):
        sent.append(name)

    c = make_conv(store, clock, completer, send)
    await c.start()
    await c.on_user_turn("ふたまる、ただいま")
    clock.advance(60)
    await c.tick()
    await c.close()
    assert len(completer.calls) == 2  # 名前探し 1 回 + reflection 1 回（二重に振り返らない）


# --- 話し相手の見分け（Task 5） ---
def identify_json(kind="none", person_id=None, name=None) -> str:
    """名前探し（people.identify）の LLM 応答を作る。"""
    return json.dumps({"kind": kind, "person_id": person_id, "name": name}, ensure_ascii=False)


async def make_started(store, clock, completer):
    async def send(name):
        pass

    c = make_conv(store, clock, completer, send)
    await c.start()
    await settle()
    return c


async def test_wake_prompt_asks_for_a_name(store, clock):
    c = await make_started(store, clock, FakeCompleter([]))
    d = await c.on_user_turn("ふたまる")
    assert d.kind == "greet" and ASK_NAME_INSTRUCTION in d.instructions
    assert d.dialect is None
    assert c._complete_json.calls == []  # 挨拶だけでは名前探しをしない
    await c.close()


async def test_named_wake_switches_to_that_persons_prompt(store, clock):
    store.get_or_create_plant("a", T0)
    taro = store.add_person("a", "たろう", T0)
    store.set_dialect_override(taro.id, "kansai")
    completer = FakeCompleter([identify_json("known", taro.id), reflection_json()])
    c = await make_started(store, clock, completer)
    d = await c.on_user_turn("ふたまる、たろうだよ")
    assert (d.kind, d.text) == ("reply", "たろうだよ")
    assert "関西弁" in d.instructions and "今話している相手は たろう。" in d.instructions
    assert ASK_NAME_INSTRUCTION not in d.instructions
    assert d.dialect == "kansai"
    assert store.get_session(current_sid(store)).person_id == taro.id
    await c.close()


async def test_new_person_is_registered(store, clock):
    completer = FakeCompleter([identify_json("new", name="はなこ"), reflection_json()])
    c = await make_started(store, clock, completer)
    await c.on_user_turn("ふたまる")
    d = await c.on_user_turn("はなこです")
    people = store.get_people("a")
    assert [p.name for p in people] == ["はなこ"]
    assert store.get_session(current_sid(store)).person_id == people[0].id
    assert "今話している相手は はなこ。" in d.instructions and d.dialect == "standard"
    await c.close()


async def test_becomes_guest_after_three_unnamed_turns_and_stops_identifying(store, clock):
    completer = FakeCompleter([identify_json() for _ in range(3)] + [reflection_json()])
    c = await make_started(store, clock, completer)
    d1 = await c.on_user_turn("ふたまる、ただいま")
    assert ASK_NAME_INSTRUCTION in d1.instructions and d1.dialect is None
    d2 = await c.on_user_turn("今日は晴れ")
    assert (d2.instructions, d2.dialect) == (None, None)
    d3 = await c.on_user_turn("カレー食べた")
    assert d3.instructions and ASK_NAME_INSTRUCTION not in d3.instructions
    assert d3.dialect == "standard"
    assert len(completer.calls) == 3
    d4 = await c.on_user_turn("ぼくはたろう")
    assert (d4.kind, d4.instructions, d4.dialect) == ("reply", None, None)
    assert len(completer.calls) == 3  # お客さんになったら名前探しを呼ばない
    assert store.get_session(current_sid(store)).person_id is None
    await c.close()


async def test_slow_identification_does_not_block_reply(store, clock, monkeypatch):
    monkeypatch.setattr(conversation_module, "IDENTIFY_TIMEOUT", 0.05)
    gate = asyncio.Event()
    completer = FakeCompleter([identify_json(), reflection_json()], gate=gate)
    c = await make_started(store, clock, completer)
    await c.on_user_turn("ふたまる")
    d = await asyncio.wait_for(c.on_user_turn("ただいま"), timeout=1)
    assert (d.kind, d.text) == ("reply", "ただいま")
    gate.set()
    await c.close()


async def test_sleep_forgets_the_person(store, clock):
    store.get_or_create_plant("a", T0)
    taro = store.add_person("a", "たろう", T0)
    completer = FakeCompleter([identify_json("known", taro.id), reflection_json()])
    c = await make_started(store, clock, completer)
    d = await c.on_user_turn("ふたまる、たろうだよ")
    assert ASK_NAME_INSTRUCTION not in d.instructions
    clock.advance(60)
    assert await c.tick() == "slept"
    await c.wait_reflections()
    d = await c.on_user_turn("ふたまる")
    assert ASK_NAME_INSTRUCTION in d.instructions and "たろう" not in d.instructions
    assert "テストの話をした" not in d.instructions  # たろうとの要約はお客さんに見せない
    assert c._person is None and c._user_turns == 0 and not c._guest
    await c.close()
