from agent.plant.dialect import dialect_prompt, dialect_tts, resolve_dialect


def test_override_wins():
    assert resolve_dialect("hakata", ["kansai"] * 5) == "hakata"


def test_majority_of_recent_five():
    # 6 件目以降（hakata）は数えない。先頭 5 件では kansai が 3 件で最多
    recent = ["kansai", "kansai", "hakata", "standard", "kansai", "hakata", "hakata"]
    assert resolve_dialect(None, recent) == "kansai"


def test_tie_prefers_newest():
    assert resolve_dialect(None, ["hakata", "kansai"]) == "hakata"


def test_no_judgement_is_standard():
    assert resolve_dialect(None, []) == "standard"


def test_standard_adds_nothing():
    assert dialect_prompt("standard") is None and dialect_tts("standard") is None
    assert "関西弁" in dialect_prompt("kansai")
    assert dialect_tts("kansai")
