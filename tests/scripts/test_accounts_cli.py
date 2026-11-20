import io
from datetime import UTC, datetime

import pytest

from agent.plant.account import verify_password
from agent.plant.store import PlantStore
from scripts.accounts import main
from tests.fakes import FakePasswordPrompt

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
PASSWORD = "correct horse battery"


@pytest.fixture
def store(tmp_path):
    s = PlantStore(str(tmp_path / "a.db"))
    yield s
    s.close()


def run(store, *argv, answers=(), stdin=""):
    """コマンドを動かして、(終了コード, 聞かれた文) を返す。"""
    ask = FakePasswordPrompt(answers)
    code = main(list(argv), store, NOW, ask=ask, stdin=io.StringIO(stdin))
    return code, ask.prompts


def test_add_asks_twice_and_stores_only_a_hash(store):
    code, prompts = run(store, "add", "shion", answers=[PASSWORD, PASSWORD])
    assert code == 0
    assert len(prompts) == 2  # 打ち間違いを防ぐため 2 回聞く
    account = store.get_account("shion")
    assert account.password_hash != PASSWORD and PASSWORD not in account.password_hash
    assert verify_password(PASSWORD, account.password_hash)


def test_add_fails_when_the_two_passwords_differ(store, capsys):
    code, _ = run(store, "add", "shion", answers=[PASSWORD, PASSWORD + "x"])
    assert code == 1
    assert "違います" in capsys.readouterr().out
    assert store.get_account("shion") is None


def test_add_rejects_a_short_password(store, capsys):
    code, _ = run(store, "add", "shion", answers=["short", "short"])
    assert code == 1
    assert "12 文字以上" in capsys.readouterr().out
    assert store.get_account("shion") is None


def test_add_rejects_a_name_outside_the_rules(store, capsys):
    code, prompts = run(store, "add", "Shion", answers=[PASSWORD, PASSWORD])
    assert code == 1
    assert prompts == []  # 名前がだめなら、パスワードを聞く前に止める
    assert store.list_accounts() == []


def test_add_with_generate_prints_a_password_that_works(store, capsys):
    code, prompts = run(store, "add", "chamu", "--generate")
    assert code == 0 and prompts == []
    out = capsys.readouterr().out
    shown = next(line for line in out.splitlines() if line.startswith("パスワード: "))
    password = shown.removeprefix("パスワード: ")
    assert verify_password(password, store.get_account("chamu").password_hash)


def test_add_reads_the_password_from_stdin(store):
    code, prompts = run(store, "add", "shion", "--password-stdin", stdin=PASSWORD + "\n")
    assert code == 0 and prompts == []
    assert verify_password(PASSWORD, store.get_account("shion").password_hash)


def test_add_refuses_an_existing_name(store, capsys):
    run(store, "add", "shion", answers=[PASSWORD, PASSWORD])
    code, prompts = run(store, "add", "shion", answers=["other password", "other password"])
    assert code == 1 and prompts == []
    assert "passwd" in capsys.readouterr().out
    assert verify_password(PASSWORD, store.get_account("shion").password_hash)


def test_passwd_changes_the_password(store):
    run(store, "add", "shion", answers=[PASSWORD, PASSWORD])
    code, _ = run(store, "passwd", "shion", answers=["a brand new one", "a brand new one"])
    assert code == 0
    stored = store.get_account("shion").password_hash
    assert verify_password("a brand new one", stored)
    assert not verify_password(PASSWORD, stored)


@pytest.mark.parametrize("command", ["passwd", "delete"])
def test_passwd_and_delete_need_an_existing_account(store, command, capsys):
    code, prompts = run(store, command, "nobody", answers=[PASSWORD, PASSWORD])
    assert code == 1 and prompts == []
    assert "ありません" in capsys.readouterr().out


def test_delete_removes_the_account(store):
    run(store, "add", "shion", answers=[PASSWORD, PASSWORD])
    code, _ = run(store, "delete", "shion")
    assert code == 0
    assert store.get_account("shion") is None


def test_list_shows_names_but_not_hashes(store, capsys):
    assert run(store, "list")[0] == 0
    assert "まだありません" in capsys.readouterr().out
    run(store, "add", "shion", answers=[PASSWORD, PASSWORD])
    capsys.readouterr()
    run(store, "list")
    out = capsys.readouterr().out
    assert "shion" in out
    assert "scrypt" not in out
