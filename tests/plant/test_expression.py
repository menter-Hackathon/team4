import pytest

from agent.plant.expression import ExpressionParser, ParsedChunk


def run(chunks):
    p = ExpressionParser()
    exprs, text = [], ""
    for out in [p.feed(c) for c in chunks] + [p.flush()]:
        if out.expression is not None:
            exprs.append(out.expression)
        text += out.text
    return exprs, text


@pytest.mark.parametrize(
    "chunks,exprs,text",
    [
        (["[happy]こんにちは"], ["happy"], "こんにちは"),
        (["[ha", "ppy]こん", "にちは"], ["happy"], "こんにちは"),
        (["[", "h", "a", "p", "p", "y", "]", "や", "あ"], ["happy"], "やあ"),
        (["こんにちは"], ["neutral"], "こんにちは"),
        (["[angry]やだ"], ["neutral"], "やだ"),
        (["[listening]うん"], ["neutral"], "うん"),
        (["[Happy] やった"], ["happy"], "やった"),
        ([" [love] すき"], ["love"], "すき"),
        ([" ", "[sad]", "そっか"], ["sad"], "そっか"),
        (["[" + "あ" * 20], ["neutral"], "[" + "あ" * 20),
        (["[hap"], ["neutral"], "[hap"),
        (["[happy]うん！[lo", "ve]だいすき"], ["happy"], "うん！だいすき"),
        (["[happy]答えは[1]番"], ["happy"], "答えは[1]番"),
        (["[happy]ねえ[", "あ" * 20], ["happy"], "ねえ[" + "あ" * 20),
        (["[happy]え、[sur"], ["happy"], "え、[sur"),
        ([], ["neutral"], ""),
    ],
)
def test_parse(chunks, exprs, text):
    assert run(chunks) == (exprs, text)


def test_plain_text_is_not_delayed():
    assert ExpressionParser().feed("こんにちは") == ParsedChunk("neutral", "こんにちは")


def test_text_after_tag_streams_immediately():
    p = ExpressionParser()
    assert p.feed("[happy]") == ParsedChunk("happy", "")
    assert p.feed("やった") == ParsedChunk(None, "やった")


# --- final review I4: 全角・隅付き括弧、本文中の英字タグ ---
@pytest.mark.parametrize(
    "chunks,exprs,text",
    [
        (["［happy］こんにちは"], ["happy"], "こんにちは"),
        (["【love】すき"], ["love"], "すき"),
        (["やあ[smile]"], ["neutral"], "やあ"),
        (["[happy][excited]やった"], ["happy"], "やった"),
        (["[happy]うん[thinking]えっと"], ["happy"], "うんえっと"),
        (["[happy]答えは[1]番"], ["happy"], "答えは[1]番"),
    ],
)
def test_bracket_variants_never_spoken(chunks, exprs, text):
    assert run(chunks) == (exprs, text)


def test_strip_tags():
    from agent.plant.expression import strip_tags

    assert strip_tags("[happy]やった！[love]") == "やった！"
    assert strip_tags("こんにちは") == "こんにちは"
