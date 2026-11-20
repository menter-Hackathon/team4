"""ダッシュボードにログインするアカウントを作る・パスワードを変える・消す・一覧を見る（仕様 §6.6）。

uv run python -m scripts.accounts list
uv run python -m scripts.accounts add shion               # パスワードを 2 回聞く（打った文字は画面に出ない）
uv run python -m scripts.accounts add chamu --generate    # ランダムなパスワードを作って 1 回だけ表示する
uv run python -m scripts.accounts passwd shion            # パスワードを変える
uv run python -m scripts.accounts delete chamu

Docker では、コンテナの中で動かす（DB はコンテナの中の /app/data/futamaru.db）:
    docker compose exec agent python -m scripts.accounts add shion

パスワードを画面で打たずに渡したいとき（スクリプトから作るときなど）は --password-stdin を付けて、
標準入力の 1 行目にパスワードを書く。コマンドの引数には書かない（ほかの人に ps やシェルの履歴で見られるため）。

アカウントは DB の accounts テーブルに入る。パスワードそのものは保存せず、ハッシュだけを保存する
（agent/plant/account.py）。アカウントが 1 つも無いうちは、Docker のダッシュボードには誰もログインできない。
"""

from __future__ import annotations

import argparse
import getpass
import sys
from collections.abc import Callable
from datetime import datetime
from typing import TextIO

from dotenv import load_dotenv

from agent.config import db_path_from_env
from agent.plant.account import (
    AccountError,
    check_name,
    check_password,
    generate_password,
    hash_password,
)
from agent.plant.store import PlantStore

# パスワードを聞く関数の形: 聞くときの文（例: "パスワード: "）を受け取り、打たれた文字を返す。
# 本番は getpass.getpass（打った文字を画面に出さない）。テストでは偽物（tests/fakes.py）に差し替える
AskPassword = Callable[[str], str]


def _new_password(args: argparse.Namespace, ask: AskPassword, stdin: TextIO) -> tuple[str, bool]:
    """新しいパスワードを決める。戻り値は (パスワード, ランダムに作ったか)。

    1. --generate なら、ランダムに作る（決まりを満たす長さなので確かめない）
    2. --password-stdin なら、標準入力の 1 行目を使う
    3. どちらでもなければ、画面で 2 回聞いて、同じか確かめる（打ち間違いでログインできなくなるのを防ぐ）
    2・3 は決まり（12 文字以上）を満たすか確かめ、満たさなければ AccountError。
    """
    if args.generate:
        return generate_password(), True
    if args.password_stdin:
        # 末尾の改行だけを取る（パスワードの前後の空白は、打った人の意図として残す）
        password = stdin.readline().rstrip("\r\n")
    else:
        password = ask("パスワード: ")
        if ask("もう一度: ") != password:
            raise AccountError("2 回のパスワードが違います")
    return check_password(password), False


def _print_accounts(store: PlantStore) -> None:
    """アカウントを 1 つ 1 行で表示する（パスワードのハッシュは出さない）。"""
    accounts = store.list_accounts()
    if not accounts:
        print("アカウントはまだありません。add で作ってください")
        return
    for a in accounts:
        print(f"{a.name}  （{a.created_at} に作成）")


def main(
    argv: list[str],
    store: PlantStore,
    now: datetime,
    ask: AskPassword = getpass.getpass,
    stdin: TextIO = sys.stdin,
) -> int:
    """コマンドの本体。成功なら 0、エラーなら 1 を返す（テストから直接呼べる）。

    引数:
        argv: コマンドの引数（例: ["add", "shion", "--generate"]）
        store: 書き込む DB
        now: アカウントを作った時刻として保存する
        ask: パスワードを画面で聞く関数（テストでは偽物）
        stdin: --password-stdin のときに読む入力（テストでは io.StringIO）
    """
    parser = argparse.ArgumentParser(description="ダッシュボードにログインするアカウントの管理")
    # --db はどのコマンドでも使えるよう、各コマンドに付ける（例: add shion --db data/sim.db）。
    # DB を開くのは __main__ 側なので、ここでは受け付けるだけ
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", help="DB のパス（省略時は FUTAMARU_DB_PATH）")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="アカウントの一覧", parents=[common])
    for command, text in (("add", "アカウントを作る"), ("passwd", "パスワードを変える")):
        p = sub.add_parser(command, help=text, parents=[common])
        p.add_argument("name", help="アカウント名（英小文字・数字・_・-、32 文字まで）")
        how = p.add_mutually_exclusive_group()
        how.add_argument(
            "--generate", action="store_true", help="ランダムなパスワードを作って 1 回だけ表示する"
        )
        how.add_argument(
            "--password-stdin", action="store_true", help="標準入力の 1 行目をパスワードにする"
        )
    delete = sub.add_parser("delete", help="アカウントを消す", parents=[common])
    delete.add_argument("name")
    args = parser.parse_args(argv)

    if args.command == "list":
        _print_accounts(store)
        return 0

    try:
        name = check_name(args.name)
        # 1. 名前を確かめる（作るならまだ無いこと、変える・消すならあること）
        exists = store.get_account(name) is not None
        if args.command == "add" and exists:
            raise AccountError(
                f"アカウント {name} はもうあります。変えるなら passwd を使ってください"
            )
        if args.command != "add" and not exists:
            raise AccountError(f"アカウント {name} はありません")
        # 2. 消すだけならここで終わり
        if args.command == "delete":
            store.delete_account(name)
            print(f"アカウント {name} を消しました")
            return 0
        # 3. 新しいパスワードを決めて、ハッシュにして保存する
        password, generated = _new_password(args, ask, stdin)
    except AccountError as e:
        print(f"エラー: {e}")
        return 1
    if args.command == "add":
        store.add_account(name, hash_password(password), now)
        print(f"アカウント {name} を作りました")
    else:
        store.set_account_password(name, hash_password(password))
        print(f"アカウント {name} のパスワードを変えました")
    if generated:
        # ランダムに作ったパスワードは、ここで 1 回だけ見せる（DB にはハッシュしか残らないので、あとから見られない）
        print(f"パスワード: {password}")
        print("このパスワードは今しか表示されません。使う人に DM で渡してください")
    return 0


if __name__ == "__main__":
    load_dotenv()
    # --db だけ先に読み取って DB を開く（残りの引数は main が解釈する。scripts/people.py と同じ）
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--db")
    known, _ = pre.parse_known_args()
    # 環境変数は agent/config.py 経由でだけ読む（AGENTS.md）。要るのは DB の場所だけ
    db_path = known.db or db_path_from_env()
    raise SystemExit(main(sys.argv[1:], PlantStore(db_path), datetime.now().astimezone()))
