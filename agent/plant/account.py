"""ダッシュボードにログインするアカウントの決まりと、パスワードのハッシュ（仕様 §6.6、2026-11-20）。

パスワードそのものは DB に保存しない。scrypt という「わざと計算に時間とメモリがかかる」方法で
ハッシュ（元に戻せない文字列）にして保存し、ログインのたびに同じ計算をして比べる。
DB のファイルが漏れても、元のパスワードを総当たりで探すのに時間がかかるようにするため。

例:
    stored = hash_password("correct horse battery")  # → "scrypt$16384$8$1$<ソルト>$<ハッシュ>"
    verify_password("correct horse battery", stored)  # → True
    verify_password("まちがい", stored)                # → False

標準ライブラリ（hashlib・hmac・secrets）だけで作り、依存を増やさない。
使うのはダッシュボード（scripts/dashboard.py）とアカウントの管理（scripts/accounts.py）だけ。
DB への読み書き（accounts テーブル）は store.py が受け持つ。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets

# アカウント名に使える文字: 英小文字・数字・_・-（1〜32 文字）。
# Basic 認証では「名前:パスワード」をつなげて送るので「:」は使えない。
# 日本語の名前はブラウザによって送り方（文字コード）が違い、ログインできないことがあるので使わない
NAME_PATTERN = re.compile(r"[a-z0-9_-]{1,32}")
# パスワードの最短の長さ。インターネットから開けるページなので、推測されにくい長さにする
MIN_PASSWORD_LENGTH = 12
# scrypt の強さ。n=2**14・r=8・p=1 で、1 回の計算に約 16MB のメモリと数十ミリ秒かかる。
# ダッシュボードは 3 秒ごとに読みに来て、そのたびにこの計算をするので、これより重くはしない（仕様 §6.6）
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
# ソルト（アカウントごとのランダムな値）とハッシュの長さ（バイト）
SALT_BYTES = 16
KEY_BYTES = 32
# 保存する文字列の先頭に付ける、ハッシュの方式の名前
_ALGORITHM = "scrypt"

# 無いアカウント名でログインされたときに、比べる相手にするハッシュ（_dummy_hash() が 1 回だけ作る）
_dummy: str | None = None


class AccountError(ValueError):
    """アカウント名やパスワードが決まりに合わないときのエラー。メッセージはそのまま画面に出す。"""


def check_name(name: str) -> str:
    """アカウント名が決まりに合うか確かめて、そのまま返す。合わなければ AccountError。

    例: check_name("shion") → "shion"、check_name("Shion") → AccountError（大文字は使えない）
    """
    if not NAME_PATTERN.fullmatch(name):
        raise AccountError("アカウント名は英小文字・数字・_・- で 32 文字までにしてください")
    return name


def check_password(password: str) -> str:
    """パスワードが十分に長いか確かめて、そのまま返す。短ければ AccountError。"""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AccountError(f"パスワードは {MIN_PASSWORD_LENGTH} 文字以上にしてください")
    return password


def generate_password() -> str:
    """ランダムなパスワードを作る（英数字と - _ の 16 文字。12 バイト = 96 ビットの乱数）。"""
    return secrets.token_urlsafe(12)


def _b64(data: bytes) -> str:
    """バイト列を、DB に文字として保存できる base64 にする。"""
    return base64.b64encode(data).decode("ascii")


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    """scrypt でパスワードからハッシュ（KEY_BYTES バイト）を作る。"""
    # maxmem: scrypt が使ってよいメモリの上限。n・r が大きい値でも計算できるよう、必要な分（128*n*r）より多めにする
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        maxmem=256 * n * r,
        dklen=KEY_BYTES,
    )


def hash_password(password: str) -> str:
    """パスワードをハッシュにして、DB に保存する文字列を返す。

    戻り値の形: "scrypt$<n>$<r>$<p>$<ソルトの base64>$<ハッシュの base64>"
    強さ（n・r・p）も一緒に保存するので、あとで強さを変えても、前に作ったアカウントでログインできる。
    ソルトは毎回ランダムなので、同じパスワードでも呼ぶたびに違う文字列になる。
    """
    salt = secrets.token_bytes(SALT_BYTES)
    key = _scrypt(password, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)
    return "$".join(
        [_ALGORITHM, str(SCRYPT_N), str(SCRYPT_R), str(SCRYPT_P), _b64(salt), _b64(key)]
    )


def verify_password(password: str, stored: str) -> bool:
    """password が、保存したハッシュ stored を作ったときと同じパスワードなら True。

    1. stored を「方式・n・r・p・ソルト・ハッシュ」に分ける（形がおかしければ False）
    2. 同じソルト・強さで password のハッシュを計算する
    3. 保存したハッシュと比べる。hmac.compare_digest は、どこまで一致したかで比べる時間が変わらない
       （普通の == だと、時間の差から少しずつ正解を当てられるおそれがある）
    """
    try:
        algorithm, n, r, p, salt_b64, key_b64 = stored.split("$")
        if algorithm != _ALGORITHM:
            return False
        salt = base64.b64decode(salt_b64, validate=True)
        expected = base64.b64decode(key_b64, validate=True)
        actual = _scrypt(password, salt, int(n), int(r), int(p))
    except ValueError:
        # 区切りの数が違う・数字でない・base64 でない・scrypt が受け付けない強さ（binascii.Error も ValueError の仲間）
        return False
    return hmac.compare_digest(actual, expected)


def _dummy_hash() -> str:
    """比べる相手のいないときに使う、ダミーのハッシュ（初めて呼ばれたときに 1 回だけ作る）。"""
    global _dummy
    if _dummy is None:
        _dummy = hash_password(generate_password())
    return _dummy


def verify_login(password: str, stored: str | None) -> bool:
    """ログインのパスワードを確かめる。stored が None（そのアカウント名が無い）なら必ず False。

    アカウント名が無いときも、ダミーのハッシュで同じだけ計算してから False を返す。
    すぐに返すと、応答の速さの違いから「その名前のアカウントはある」と知られてしまうため。
    """
    if stored is None:
        verify_password(password, _dummy_hash())
        return False
    return verify_password(password, stored)
