import pytest
from livekit.agents import Agent, ChatContext, llm

from agent import main
from agent.config import load_config
from agent.plant.prompt import FALLBACK_REPLY

CFG = {"LLM_BASE_URL": "http://x/v1", "LLM_MODEL": "m", "STT_BASE_URL": "http://x/v1", "OPENAI_API_KEY": "k"}


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
    assert main.resolve_plant_id("console-room", load_config({**CFG, "DEV_PLANT_ID": "dev"})) == "dev"


def chunk(text):
    return llm.ChatChunk(id="c", delta=llm.ChoiceDelta(role="assistant", content=text))


async def collect(agent):
    out = []
    async for c in agent.llm_node(ChatContext.empty(), [], None):
        out.append(c if isinstance(c, str) else c.delta.content)
    return "".join(out)


async def test_llm_node_strips_tags_and_sends_expression(monkeypatch):
    async def fake(self, chat_ctx, tools, model_settings):
        for t in ["[hap", "py]やっ", "た！[love]"]:
            yield chunk(t)

    monkeypatch.setattr(Agent.default, "llm_node", fake)
    conv = FakeConv()
    assert await collect(main.FutamaruAgent(conv)) == "やった！"
    assert conv.expressions == ["happy"]


async def test_llm_node_fallback_on_error(monkeypatch):
    async def fake(self, chat_ctx, tools, model_settings):
        raise RuntimeError("gpu down")
        yield  # pragma: no cover

    monkeypatch.setattr(Agent.default, "llm_node", fake)
    assert await collect(main.FutamaruAgent(FakeConv())) == FALLBACK_REPLY
