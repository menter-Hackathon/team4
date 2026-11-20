import sqlite3
from datetime import datetime

import pytest

from agent.plant.timeutil import JST
from scripts.use_db import use_db

NOW = datetime(2026, 11, 20, 19, 30, tzinfo=JST)


def make_db(path, value: str, *, keep_wal: bool = False) -> sqlite3.Connection | None:
    """値を 1 つだけ持つ WAL 方式の DB を作る。keep_wal なら、書き込みを -wal に残したまま開いておく。"""
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "PRAGMA wal_autocheckpoint=0"
    )  # -wal を本体に書き戻さない（書き込みを -wal に残す）
    conn.execute("CREATE TABLE t (v TEXT)")
    conn.execute("INSERT INTO t VALUES (?)", (value,))
    conn.commit()
    if keep_wal:
        return conn
    conn.close()
    return None


def read(path) -> list[str]:
    with sqlite3.connect(path) as conn:
        rows = [r[0] for r in conn.execute("SELECT v FROM t")]
    conn.close()
    return rows


def test_replaces_the_db_and_backs_up_the_old_one(tmp_path):
    src, dst = tmp_path / "demo-day30.db", tmp_path / "futamaru.db"
    make_db(src, "30日目")
    make_db(dst, "ふだん")
    saved = use_db(src, dst, tmp_path / "backup", NOW)
    assert read(dst) == ["30日目"]
    assert saved == tmp_path / "backup" / "futamaru-20261120-193000.db"
    assert read(saved) == ["ふだん"]
    # 入れ替え先の DB は変えない
    assert read(src) == ["30日目"]


def test_writes_only_in_the_wal_are_kept_in_the_backup(tmp_path):
    # 今の DB の書き込みが -wal にだけある状態でも、保存したファイルに含まれる
    src, dst = tmp_path / "demo.db", tmp_path / "futamaru.db"
    make_db(src, "demo")
    open_conn = make_db(dst, "walだけ", keep_wal=True)
    assert (tmp_path / "futamaru.db-wal").stat().st_size > 0
    saved = use_db(src, dst, tmp_path / "backup", NOW)
    open_conn.close()
    assert read(saved) == ["walだけ"]


def test_stale_wal_files_are_removed(tmp_path):
    src, dst = tmp_path / "demo.db", tmp_path / "futamaru.db"
    make_db(src, "demo")
    make_db(dst, "old")
    # 古い -wal・-shm が残っている状態を作る
    (tmp_path / "futamaru.db-wal").write_bytes(b"stale")
    (tmp_path / "futamaru.db-shm").write_bytes(b"stale")
    use_db(src, dst, tmp_path / "backup", NOW)
    assert not (tmp_path / "futamaru.db-shm").exists()
    assert read(dst) == ["demo"]


def test_works_when_there_is_no_current_db(tmp_path):
    src, dst = tmp_path / "demo.db", tmp_path / "data" / "futamaru.db"
    make_db(src, "demo")
    assert use_db(src, dst, tmp_path / "backup", NOW) is None
    assert read(dst) == ["demo"]


def test_missing_source_is_an_error_and_keeps_the_current_db(tmp_path):
    dst = tmp_path / "futamaru.db"
    make_db(dst, "ふだん")
    with pytest.raises(FileNotFoundError):
        use_db(tmp_path / "nope.db", dst, tmp_path / "backup", NOW)
    assert read(dst) == ["ふだん"]


def test_same_file_is_an_error(tmp_path):
    dst = tmp_path / "futamaru.db"
    make_db(dst, "ふだん")
    with pytest.raises(ValueError):
        use_db(dst, dst, tmp_path / "backup", NOW)
