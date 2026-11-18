"""植物の DB（data/futamaru.db）を、発表用に育てた DB に入れ替える（docs/demo-day.md、#77）。

なぜ必要か:
    Docker のエージェントは、いつも data/futamaru.db を使う（compose.yaml で決めている）。
    発表では「1 日目・5 日目・30 日目」の株を見せたいので、前もって育てた DB（scripts.simulate で作る）を
    data/futamaru.db の場所に置き換える。

なぜファイルをコピーするだけではだめなのか:
    SQLite（WAL 方式）は、DB 本体の横に「-wal」「-shm」というファイルを作り、書き込みの途中をそこに置く。
    本体だけを上書きして古い -wal が残っていると、次に開いたときに古い書き込みが混ざって DB が壊れることがある。
    そこでこのスクリプトは、
        1. 今の DB を、data/backup/ に日時付きで丸ごと保存する（-wal の中身も含めて 1 つのファイルにする）
        2. 今の DB と -wal・-shm を消す
        3. 入れ替え先の DB を、SQLite の backup 機能で 1 つのファイルとして書き出す
    の順で入れ替える。

使い方（Docker。エージェントとダッシュボードは止めてから）:
    docker compose stop agent dashboard
    docker compose run --rm --no-deps agent python -m scripts.use_db data/demo-day30.db
    docker compose up -d

使い方（Docker を使わない PC）:
    uv run python -m scripts.use_db data/demo-day30.db
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from agent.config import db_path_from_env
from agent.plant.timeutil import JST

# WAL 方式の SQLite が DB 本体の横に作るファイルの末尾
SIDE_SUFFIXES = ("-wal", "-shm")


def _copy_db(src: Path, dst: Path) -> None:
    """src の DB を、dst に 1 つのファイルとして書き出す（-wal の中身も含める）。

    SQLite の backup 機能を使うと、-wal にだけある書き込みも含めた「今の中身」を写せる。
    src は読むだけで開くので、src 自体は変えない。
    """
    with (
        sqlite3.connect(f"file:{src}?mode=ro", uri=True) as source,
        sqlite3.connect(dst) as target,
    ):
        source.backup(target)
    # with を抜けても接続は閉じないので、明示的に閉じる（閉じないと -wal などが残ることがある）
    source.close()
    target.close()


def use_db(src: Path, dst: Path, backup_dir: Path, now: datetime) -> Path | None:
    """dst（今の DB）を src の中身に入れ替える。

    引数:
        src: 入れ替え先の DB（例: data/demo-day30.db）
        dst: エージェントが使う DB（例: data/futamaru.db）
        backup_dir: 今の DB を保存するフォルダ（例: data/backup）
        now: 保存するファイル名に付ける日時
    戻り値:
        今の DB を保存したファイル。今の DB が無かったときは None
    例:
        use_db(Path("data/demo-day30.db"), Path("data/futamaru.db"), Path("data/backup"), now)
        → data/backup/futamaru-20261120-193000.db に今の DB を残し、data/futamaru.db が 30 日目の株になる
    """
    if not src.exists():
        raise FileNotFoundError(f"入れ替え先の DB がありません: {src}")
    if src.resolve() == dst.resolve():
        raise ValueError("入れ替え先と今の DB が同じファイルです")

    # 1. 今の DB があれば、日時付きで保存する（発表のあとで元に戻せるように）
    saved = None
    if dst.exists():
        backup_dir.mkdir(parents=True, exist_ok=True)
        saved = backup_dir / f"{dst.stem}-{now.strftime('%Y%m%d-%H%M%S')}.db"
        _copy_db(dst, saved)

    # 2. 今の DB と、その横の -wal・-shm を消す（古い -wal が新しい DB に混ざらないように）
    for path in [dst, *(dst.with_name(dst.name + s) for s in SIDE_SUFFIXES)]:
        path.unlink(missing_ok=True)

    # 3. 入れ替え先の DB を書き出す
    dst.parent.mkdir(parents=True, exist_ok=True)
    _copy_db(src, dst)
    return saved


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="植物の DB を、発表用に育てた DB に入れ替える（エージェントを止めてから実行する）"
    )
    parser.add_argument("src", help="入れ替え先の DB（例: data/demo-day30.db）")
    parser.add_argument(
        "--db",
        default=None,
        help="エージェントが使う DB（既定: .env の FUTAMARU_DB_PATH。Docker では data/futamaru.db）",
    )
    args = parser.parse_args(argv)
    load_dotenv()  # Docker を使わない PC では、.env の FUTAMARU_DB_PATH を読む
    dst = Path(args.db or db_path_from_env())
    saved = use_db(Path(args.src), dst, dst.parent / "backup", datetime.now(JST))
    if saved:
        print(f"今の DB を保存しました: {saved}")
    print(f"{dst} を {args.src} の中身に入れ替えました。エージェントを起動し直してください")


if __name__ == "__main__":
    main()
