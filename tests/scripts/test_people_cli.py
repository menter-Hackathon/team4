from datetime import UTC, datetime

from agent.plant.store import PlantStore
from scripts.people import main

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)


def make_store(tmp_path):
    return PlantStore(str(tmp_path / "p.db"))


def test_pin_dialect_registers_the_person(tmp_path):
    store = make_store(tmp_path)
    code = main(["--plant-id", "a1", "--person", "たろう", "--dialect", "kansai"], store, NOW)
    assert code == 0
    people = store.get_people("a1")
    assert len(people) == 1
    assert people[0].name == "たろう"
    assert people[0].dialect_override == "kansai"


def test_katakana_finds_the_same_person(tmp_path):
    store = make_store(tmp_path)
    main(["--plant-id", "a1", "--person", "たろう", "--dialect", "hakata"], store, NOW)
    code = main(["--plant-id", "a1", "--person", "タロウ", "--auto"], store, NOW)
    assert code == 0
    people = store.get_people("a1")
    assert len(people) == 1
    assert people[0].dialect_override is None


def test_registered_name_is_cleaned_like_a_spoken_name(tmp_path):
    # 改行は空白にし、長さを切ってから登録する（プロンプトに改行入りの名前を入れないため）
    store = make_store(tmp_path)
    code = main(["--plant-id", "a1", "--person", " 山田\n太郎 ", "--dialect", "kansai"], store, NOW)
    assert code == 0
    assert [p.name for p in store.get_people("a1")] == ["山田 太郎"]
    long_name = "あ" * 30
    main(["--plant-id", "a1", "--person", long_name, "--dialect", "hakata"], store, NOW)
    assert store.get_people("a1")[1].name == "あ" * 20


def test_blank_name_is_an_error(tmp_path):
    store = make_store(tmp_path)
    code = main(["--plant-id", "a1", "--person", " \n ", "--dialect", "kansai"], store, NOW)
    assert code == 1
    assert store.get_people("a1") == []


def test_auto_on_unknown_name_is_an_error(tmp_path):
    store = make_store(tmp_path)
    code = main(["--plant-id", "a1", "--person", "はなこ", "--auto"], store, NOW)
    assert code == 1
    assert store.get_people("a1") == []


def test_person_id_of_another_plant_is_an_error(tmp_path):
    store = make_store(tmp_path)
    main(["--plant-id", "a1", "--person", "たろう", "--dialect", "kansai"], store, NOW)
    pid = store.get_people("a1")[0].id
    store.get_or_create_plant("b2", NOW)
    assert main(["--plant-id", "b2", "--person-id", str(pid), "--auto"], store, NOW) == 1
    assert main(["--plant-id", "a1", "--person-id", "999", "--auto"], store, NOW) == 1
    assert store.get_person(pid).dialect_override == "kansai"


def test_list_shows_traits_and_dialect(tmp_path, capsys):
    store = make_store(tmp_path)
    main(["--plant-id", "a1", "--person", "たろう", "--dialect", "hakata"], store, NOW)
    store.add_person("a1", "じろう", NOW)
    capsys.readouterr()
    assert main(["--plant-id", "a1"], store, NOW) == 0
    out = capsys.readouterr().out
    assert "たろう" in out and "0.50" in out
    assert "hakata" in out and "固定" in out
    assert "じろう" in out and "自動" in out


def test_list_with_no_people_says_so(tmp_path, capsys):
    store = make_store(tmp_path)
    assert main(["--plant-id", "a1"], store, NOW) == 0
    assert "まだ" in capsys.readouterr().out
