import json

from agent.plant.people import Identification, identify
from agent.plant.personality import Traits
from agent.plant.store import Person
from tests.fakes import FakeCompleter

KNOWN = [Person(1, "a", "たろう", Traits(), None)]


def js(**kw) -> str:
    return json.dumps(kw, ensure_ascii=False)


async def test_identifies_a_known_person():
    c = FakeCompleter([js(kind="known", person_id=1)])
    assert await identify(c, "たろうだよ", KNOWN) == Identification("known", 1)
    # 一覧の id と名前がプロンプトに入っている
    assert "たろう" in json.dumps(c.calls[0][0], ensure_ascii=False)


async def test_new_person_name_is_stripped():
    c = FakeCompleter([js(kind="new", name=" はなこ ")])
    assert await identify(c, "はなこです", KNOWN) == Identification("new", None, "はなこ")


async def test_fenced_json_is_accepted():
    c = FakeCompleter(["```json\n" + js(kind="known", person_id=1) + "\n```"])
    assert (await identify(c, "x", KNOWN)).kind == "known"


async def test_unknown_id_or_empty_name_is_none():
    for raw in [
        js(kind="known", person_id=9),
        js(kind="known", person_id="1"),
        js(kind="new", name="  "),
        js(kind="new"),
        js(kind="bogus"),
        "[1]",
        "not json",
    ]:
        assert (await identify(FakeCompleter([raw]), "x", KNOWN)).kind == "none", raw


async def test_completer_exception_is_none():
    assert (await identify(FakeCompleter([RuntimeError("boom")]), "x", KNOWN)).kind == "none"
