from livekit.agents import Agent, ChatContext, ChatMessage, llm

from agent import main
from agent.config import load_config
from agent.plant.conversation import TurnDecision
from agent.plant.dialect import dialect_tts
from agent.plant.prompt import FALLBACK_REPLY

CFG = {
    "LLM_BASE_URL": "http://x/v1",
    "LLM_MODEL": "m",
    "STT_BASE_URL": "http://x/v1",
    "OPENAI_API_KEY": "k",
}


class FakeConv:
    def __init__(self):
        self.expressions = []
        self.closed = False

    def express(self, name):
        self.expressions.append(name)

    async def close(self):
        self.closed = True


def test_resolve_plant_id():
    cfg = load_config(CFG)
    assert main.resolve_plant_id("plant-a1", cfg) == "a1"
    assert main.resolve_plant_id("console-room", cfg) is None
    assert main.resolve_plant_id("plant-", cfg) is None
    assert (
        main.resolve_plant_id("console-room", load_config({**CFG, "DEV_PLANT_ID": "dev"})) == "dev"
    )


def test_dev_plant_id_is_ignored_outside_console_room():
    # dev / start モードで plant- 以外のルームに呼ばれたら、DEV_PLANT_ID があっても退出する（#11）
    cfg = load_config({**CFG, "DEV_PLANT_ID": "dev"})
    assert main.resolve_plant_id("other-room", cfg) is None
    assert main.resolve_plant_id("plant-", cfg) is None
    assert main.resolve_plant_id("plant-a1", cfg) == "a1"


def chunk(text):
    return llm.ChatChunk(id="c", delta=llm.ChoiceDelta(role="assistant", content=text))


async def collect(agent):
    out = []
    async for c in agent.llm_node(ChatContext.empty(), [], None):
        out.append(c if isinstance(c, str) else c.delta.content)
    return "".join(out)


async def test_llm_node_fallback_on_error(monkeypatch):
    async def fake(self, chat_ctx, tools, model_settings):
        raise RuntimeError("gpu down")
        yield  # pragma: no cover

    monkeypatch.setattr(Agent.default, "llm_node", fake)
    assert await collect(main.FutamaruAgent(FakeConv())) == FALLBACK_REPLY


# --- final review I1 / I3 ---
def test_direction_ctx_adds_user_turn_without_touching_history():
    ctx = ChatContext.empty()
    ctx.add_message(role="system", content="あなたは観葉植物のふたまる。")
    out = main.direction_ctx(ctx, "持ち主に呼ばれて起きた。")
    roles = [i.role for i in out.items]
    assert (
        roles == ["system", "user"] and out.items[-1].text_content == "（持ち主に呼ばれて起きた。）"
    )
    assert [i.role for i in ctx.items] == ["system"]


async def test_llm_node_keeps_tags_for_history(monkeypatch):
    async def fake(self, chat_ctx, tools, model_settings):
        yield chunk("[happy]やった")

    monkeypatch.setattr(Agent.default, "llm_node", fake)
    assert await collect(main.FutamaruAgent(FakeConv())) == "[happy]やった"


async def test_tts_node_strips_tags_and_sends_expression(monkeypatch):
    spoken = []

    async def fake_tts(self, text, model_settings):
        async for t in text:
            spoken.append(t)
        return
        yield  # pragma: no cover

    async def stream():
        for t in ["[hap", "py]やっ", "た！[love]"]:
            yield t

    monkeypatch.setattr(Agent.default, "tts_node", fake_tts)
    conv = FakeConv()
    async for _ in main.FutamaruAgent(conv).tts_node(stream(), None):
        pass
    assert "".join(spoken) == "やった！" and conv.expressions == ["happy"]


async def test_transcription_node_strips_tags():
    async def stream():
        for t in ["[sad]そっ", "か"]:
            yield t

    conv = FakeConv()
    out = [t async for t in main.FutamaruAgent(conv).transcription_node(stream(), None)]
    assert "".join(out) == "そっか" and conv.expressions == []


# --- #5 ---
async def test_end_session_closes_conversation_and_forgets_history():
    # 会話の途中（履歴が 2 件ある状態）で ESP32 が切断したときを再現する
    conv = FakeConv()
    agent = main.FutamaruAgent(conv)
    history = ChatContext.empty()
    history.add_message(role="user", content="明日テストなんだ")
    history.add_message(role="assistant", content="がんばって！")
    await agent.update_chat_ctx(history)

    await agent.end_session()

    # 会話が閉じられ（＝振り返りが走る）、次に起きたとき前の会話が混ざらないよう履歴が空になる
    assert conv.closed
    assert agent.chat_ctx.items == []


def test_turn_handling_uses_endpointing_delay_from_config():
    # 返事を作り始めるまでの待ちを .env で変えられる（#39）。先回りの返事は使わないまま
    cfg = load_config({**CFG, "ENDPOINTING_DELAY_SECONDS": "0.2"})
    opts = main.turn_handling(cfg)
    assert opts["turn_detection"] == "vad"
    assert opts["endpointing"] == {"min_delay": 0.2}
    assert opts["preemptive_generation"] == {"enabled": False}


def test_room_options_keep_session_when_device_leaves():
    # ESP32 が出て入り直しても、同じセッションで話を続けられるようにする
    assert main.room_options().close_on_disconnect is False


# --- 方言を TTS の指示に反映する ---
class FakeTTS:
    def __init__(self):
        self.calls = []

    def update_options(self, *, instructions):
        self.calls.append(instructions)


class FakeSession:
    def __init__(self, tts):
        self.tts = tts


class DecidingConv(FakeConv):
    """on_user_turn が決まった TurnDecision を返す偽の会話。"""

    def __init__(self, decision):
        super().__init__()
        self._decision = decision

    async def on_user_turn(self, text):
        return self._decision


async def run_turn(monkeypatch, dialect, session_tts, base="やさしく話す。"):
    decision = TurnDecision(kind="reply", text="こんにちは", dialect=dialect)
    agent = main.FutamaruAgent(DecidingConv(decision), tts_instructions=base)
    monkeypatch.setattr(
        main.FutamaruAgent, "session", property(lambda self: FakeSession(session_tts))
    )
    msg = ChatMessage(role="user", content=["こんにちは"])
    await agent.on_user_turn_completed(ChatContext.empty(), msg)


async def test_dialect_updates_tts_instructions(monkeypatch):
    fake = FakeTTS()
    await run_turn(monkeypatch, "hakata", fake)
    assert len(fake.calls) == 1
    assert fake.calls[0].startswith("やさしく話す。")
    assert fake.calls[0] == "やさしく話す。 " + dialect_tts("hakata")


async def test_standard_dialect_resets_tts_to_base_instructions(monkeypatch):
    fake = FakeTTS()
    await run_turn(monkeypatch, "standard", fake)
    assert fake.calls == ["やさしく話す。"]


async def test_no_dialect_leaves_tts_untouched(monkeypatch):
    fake = FakeTTS()
    await run_turn(monkeypatch, None, fake)
    assert fake.calls == []


async def test_missing_tts_is_skipped_silently(monkeypatch):
    await run_turn(monkeypatch, "hakata", None)
    await run_turn(monkeypatch, "hakata", object())


def test_weather_cache_only_when_location_is_set():
    # 緯度・経度が無ければ天気を使わない（#80）
    assert main.create_weather_cache(load_config(CFG)) is None
    assert main.create_weather_cache(load_config({**CFG, "JMA_AMEDAS_STATION": "44132"}))
    assert main.create_weather_cache(load_config({**CFG, "JMA_FORECAST_AREA": "130000"}))
