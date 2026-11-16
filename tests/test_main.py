from livekit.agents import Agent, ChatContext, llm

from agent import main
from agent.config import load_config
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

    def express(self, name):
        self.expressions.append(name)


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
