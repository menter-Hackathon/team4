import pytest

from agent.plant.account import (
    MIN_PASSWORD_LENGTH,
    AccountError,
    check_name,
    check_password,
    generate_password,
    hash_password,
    verify_login,
    verify_password,
)

PASSWORD = "correct horse battery"


def test_hash_then_verify_accepts_the_right_password():
    stored = hash_password(PASSWORD)
    assert stored.startswith("scrypt$16384$8$1$")
    assert PASSWORD not in stored  # パスワードそのものは入らない
    assert verify_password(PASSWORD, stored)


def test_verify_rejects_a_wrong_password():
    stored = hash_password(PASSWORD)
    assert not verify_password("correct horse batterY", stored)
    assert not verify_password("", stored)


def test_same_password_gets_a_different_hash_each_time():
    # ソルトが毎回違うので、同じパスワードでもハッシュは違う（DB を見ても同じパスワードの人が分からない）
    first, second = hash_password(PASSWORD), hash_password(PASSWORD)
    assert first != second
    assert verify_password(PASSWORD, first) and verify_password(PASSWORD, second)


@pytest.mark.parametrize(
    "stored",
    [
        "",
        "plain-text-password",
        "md5$1$2$3$c2FsdA==$a2V5",  # 知らない方式
        "scrypt$16384$8$1$not-base64!$a2V5",
        "scrypt$x$8$1$c2FsdA==$a2V5",  # 強さが数字でない
        "scrypt$3$8$1$c2FsdA==$a2V5",  # scrypt が受け付けない n（2 のべき乗でない）
    ],
)
def test_verify_rejects_a_malformed_hash(stored):
    assert not verify_password(PASSWORD, stored)


def test_verify_login_without_an_account_is_false():
    assert verify_login(PASSWORD, None) is False
    assert verify_login(PASSWORD, hash_password(PASSWORD)) is True


@pytest.mark.parametrize("name", ["shion", "a", "team4_dash-1", "x" * 32])
def test_check_name_accepts_lowercase_digits_underscore_and_hyphen(name):
    assert check_name(name) == name


@pytest.mark.parametrize("name", ["", "Shion", "shion:x", "しおん", "a b", "x" * 33])
def test_check_name_rejects_other_names(name):
    with pytest.raises(AccountError):
        check_name(name)


def test_check_password_requires_twelve_characters():
    assert check_password("x" * MIN_PASSWORD_LENGTH) == "x" * MIN_PASSWORD_LENGTH
    with pytest.raises(AccountError):
        check_password("x" * (MIN_PASSWORD_LENGTH - 1))


def test_generated_password_meets_the_rules_and_differs_each_time():
    first, second = generate_password(), generate_password()
    assert check_password(first) and first != second
