"""SQLite（WAL）への性格・記憶・会話ログの読み書き（仕様 §6.2）。

時刻はすべて JST の ISO 8601 文字列で保存するため、文字列比較で時刻順になる。

DB は 1 つの SQLite ファイル（既定は data/futamaru.db）。テーブルは 5 つ:
    plants:        株ごとの名前と今の性格（4 軸）
    memories:      株が覚えていること（「持ち主はカレーが好き」など）
    sessions:      起きてから眠るまでの 1 回の会話。振り返りの要約もここに入る
    utterances:    会話の発話 1 つずつ（持ち主・植物）
    trait_history: 性格の移り変わりの記録（成長グラフ用）

SQL を書くのはこのファイルだけにして、他のファイルは PlantStore のメソッドを呼ぶ。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .personality import Traits
from .timeutil import iso

# テーブルを作る SQL。IF NOT EXISTS なので、すでにあるテーブルはそのまま使われる（データは消えない）
SCHEMA = """
CREATE TABLE IF NOT EXISTS plants (
  plant_id       TEXT PRIMARY KEY,
  name           TEXT NOT NULL DEFAULT 'ふたまる',
  created_at     TEXT NOT NULL,
  talkativeness  REAL NOT NULL DEFAULT 0.5,
  affection      REAL NOT NULL DEFAULT 0.5,
  energy         REAL NOT NULL DEFAULT 0.5,
  curiosity      REAL NOT NULL DEFAULT 0.5
);
CREATE TABLE IF NOT EXISTS memories (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  plant_id    TEXT NOT NULL REFERENCES plants(plant_id),
  content     TEXT NOT NULL,
  kind        TEXT NOT NULL,
  importance  REAL NOT NULL,
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  plant_id    TEXT NOT NULL REFERENCES plants(plant_id),
  started_at  TEXT NOT NULL,
  ended_at    TEXT,
  user_turns  INTEGER NOT NULL DEFAULT 0,
  summary     TEXT,
  reflected   INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS utterances (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id  INTEGER NOT NULL REFERENCES sessions(id),
  role        TEXT NOT NULL,
  text        TEXT NOT NULL,
  created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS trait_history (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  plant_id       TEXT NOT NULL REFERENCES plants(plant_id),
  session_id     INTEGER REFERENCES sessions(id),
  talkativeness  REAL NOT NULL,
  affection      REAL NOT NULL,
  energy         REAL NOT NULL,
  curiosity      REAL NOT NULL,
  recorded_at    TEXT NOT NULL
);
"""

# 性格 4 軸の列名。SQL の中で何度も使うのでまとめておく
_TRAIT_COLS = "talkativeness, affection, energy, curiosity"

# --- DB から読み出した 1 行を表すクラスたち（テーブルの列とほぼ同じ） ---


@dataclass
class Plant:
    """plants テーブルの 1 行（1 株）。"""

    plant_id: str
    name: str
    traits: Traits


@dataclass
class Memory:
    """memories テーブルの 1 行（記憶 1 つ）。kind は preference / event / habit。"""

    id: int
    content: str
    kind: str
    importance: float
    created_at: str
    updated_at: str


@dataclass
class Session:
    """sessions テーブルの 1 行（1 回の会話）。ended_at が None ならまだ会話中（か異常終了）。"""

    id: int
    plant_id: str
    started_at: str
    ended_at: str | None
    user_turns: int
    summary: str | None
    reflected: bool


@dataclass
class Utterance:
    """utterances テーブルの 1 行（発話 1 つ）。role は "user"（持ち主）か "plant"（植物）。"""

    role: str
    text: str
    created_at: str


@dataclass
class NewMemory:
    """振り返りで新しく覚える記憶（まだ DB に無いので id が無い）。"""

    content: str
    kind: str
    importance: float


@dataclass
class MemoryUpdate:
    """振り返りで書き換える記憶。id で、どの記憶を書き換えるかを指定する。"""

    id: int
    content: str
    importance: float


@dataclass
class TraitSnapshot:
    """trait_history テーブルの 1 行（ある時点の性格）。株を作った直後の 1 行は session_id が None。"""

    session_id: int | None
    traits: Traits
    recorded_at: str


def _traits(row: sqlite3.Row) -> Traits:
    """DB の行から、性格 4 軸の列を取り出して Traits にする。"""
    return Traits(row["talkativeness"], row["affection"], row["energy"], row["curiosity"])


class PlantStore:
    """DB への読み書きをまとめたクラス。

    メソッドの中の `with self._conn:` は「トランザクション」。ブロックの中の書き込みは、
    全部成功したら保存され、途中でエラーになったら全部取り消される（半端な状態が残らない）。
    """

    def __init__(self, path: str) -> None:
        """DB ファイルを開く（無ければ作る）。path が ":memory:" ならファイルを作らずメモリ上だけで使う（テスト用）。"""
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)  # data/ フォルダが無ければ作る
        # check_same_thread=False: LiveKit が別スレッドから呼んでも使えるようにする
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row  # 行を row["列名"] で読めるようにする
        # WAL: 書き込み中でも読み出しを待たせない方式。会話ログを書きながら記憶を読むため
        self._conn.execute("PRAGMA journal_mode=WAL")
        # 存在しない株・セッションを指す行を入れられないようにする（SQLite は既定でオフ）
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA)

    def close(self) -> None:
        """DB を閉じる。"""
        self._conn.close()

    # --- plants ---

    def get_or_create_plant(self, plant_id: str, now: datetime) -> Plant:
        """株を取り出す。初めての株なら、初期の性格（全部 0.5）で作ってから返す。"""
        with self._conn:
            # INSERT OR IGNORE: 同じ plant_id がもうあれば何もしない（エラーにしない）
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO plants (plant_id, created_at) VALUES (?, ?)",
                (plant_id, iso(now)),
            )
            # rowcount == 1 なら今まさに作った → 成長グラフの起点として初期の性格を記録する
            if cur.rowcount == 1:
                t = Traits()
                self._insert_history(plant_id, None, t, now)
        return self.get_plant(plant_id)

    def get_plant(self, plant_id: str) -> Plant:
        """株を取り出す。無ければ KeyError。"""
        row = self._conn.execute("SELECT * FROM plants WHERE plant_id = ?", (plant_id,)).fetchone()
        if row is None:
            raise KeyError(plant_id)
        return Plant(row["plant_id"], row["name"], _traits(row))

    def _insert_history(
        self, plant_id: str, session_id: int | None, t: Traits, now: datetime
    ) -> None:
        """性格の移り変わりを 1 行記録する（トランザクションは呼び出し側で張る）。"""
        self._conn.execute(
            f"INSERT INTO trait_history (plant_id, session_id, {_TRAIT_COLS}, recorded_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (plant_id, session_id, t.talkativeness, t.affection, t.energy, t.curiosity, iso(now)),
        )

    def trait_history(self, plant_id: str) -> list[TraitSnapshot]:
        """その株の性格の記録を、古い順に全部返す（成長シミュレーション・グラフ用）。"""
        rows = self._conn.execute(
            "SELECT * FROM trait_history WHERE plant_id = ? ORDER BY id", (plant_id,)
        ).fetchall()
        return [TraitSnapshot(r["session_id"], _traits(r), r["recorded_at"]) for r in rows]

    # --- sessions / utterances ---

    def start_session(self, plant_id: str, now: datetime) -> int:
        """新しいセッション（会話）を始め、その id を返す。植物が起きたときに呼ぶ。"""
        with self._conn:
            cur = self._conn.execute(
                "INSERT INTO sessions (plant_id, started_at) VALUES (?, ?)", (plant_id, iso(now))
            )
        return int(cur.lastrowid)

    def add_utterance(self, session_id: int, role: str, text: str, now: datetime) -> None:
        """発話を 1 つ保存する。持ち主の発話なら、セッションの発話数（user_turns）も 1 増やす。"""
        with self._conn:
            self._conn.execute(
                "INSERT INTO utterances (session_id, role, text, created_at) VALUES (?, ?, ?, ?)",
                (session_id, role, text, iso(now)),
            )
            if role == "user":
                self._conn.execute(
                    "UPDATE sessions SET user_turns = user_turns + 1 WHERE id = ?", (session_id,)
                )

    def end_session(self, session_id: int, now: datetime) -> None:
        """セッションの終了時刻を記録する。植物が眠ったときに呼ぶ。"""
        with self._conn:
            self._conn.execute(
                "UPDATE sessions SET ended_at = ? WHERE id = ?", (iso(now), session_id)
            )

    def get_session(self, session_id: int) -> Session:
        """セッションを取り出す。無ければ KeyError。"""
        r = self._conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if r is None:
            raise KeyError(session_id)
        return Session(
            r["id"],
            r["plant_id"],
            r["started_at"],
            r["ended_at"],
            r["user_turns"],
            r["summary"],
            bool(r["reflected"]),
        )

    def get_utterances(self, session_id: int) -> list[Utterance]:
        """そのセッションの発話を、話した順に全部返す。"""
        rows = self._conn.execute(
            "SELECT role, text, created_at FROM utterances WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
        return [Utterance(r["role"], r["text"], r["created_at"]) for r in rows]

    def count_user_utterances_since(self, plant_id: str, since: datetime) -> int:
        """since 以降に、持ち主がその株に話しかけた回数。おしゃべり度の計算に使う（仕様 §6.3）。"""
        # 発話（utterances）には plant_id が無いので、セッション（sessions）とつないで株を絞る
        row = self._conn.execute(
            "SELECT COUNT(*) FROM utterances u JOIN sessions s ON u.session_id = s.id"
            " WHERE s.plant_id = ? AND u.role = 'user' AND u.created_at >= ?",
            (plant_id, iso(since)),
        ).fetchone()
        return int(row[0])

    def repair_unfinished_sessions(self, plant_id: str) -> int:
        """ended_at の無いセッションを、最後の発話時刻（無ければ started_at）で閉じる。

        エージェントが落ちるなどして眠る処理が走らなかったセッションを、起動時に片付ける（仕様 §6.3 手順8）。
        戻り値は閉じたセッションの数。
        """
        # COALESCE(A, B): A が NULL（発話が 1 つも無い）なら B（started_at）を使う
        with self._conn:
            cur = self._conn.execute(
                "UPDATE sessions SET ended_at = COALESCE("
                " (SELECT MAX(created_at) FROM utterances WHERE session_id = sessions.id), started_at)"
                " WHERE plant_id = ? AND ended_at IS NULL",
                (plant_id,),
            )
        return cur.rowcount

    def unreflected_sessions(self, plant_id: str) -> list[int]:
        """終わっているのに、まだ振り返りが済んでいないセッションの id を古い順に返す。"""
        rows = self._conn.execute(
            "SELECT id FROM sessions WHERE plant_id = ? AND ended_at IS NOT NULL AND reflected = 0"
            " ORDER BY id",
            (plant_id,),
        ).fetchall()
        return [r["id"] for r in rows]

    def mark_reflected(self, session_id: int) -> None:
        """振り返り済みにする（振り返る必要がないセッション用。普通は apply_reflection が付ける）。"""
        with self._conn:
            self._conn.execute("UPDATE sessions SET reflected = 1 WHERE id = ?", (session_id,))

    def latest_summary(self, plant_id: str) -> str | None:
        """一番最近に終わった会話の要約。プロンプトの「前回の会話」に使う。まだ無ければ None。"""
        row = self._conn.execute(
            "SELECT summary FROM sessions WHERE plant_id = ? AND reflected = 1 AND summary IS NOT NULL"
            " ORDER BY ended_at DESC, id DESC LIMIT 1",
            (plant_id,),
        ).fetchone()
        return row["summary"] if row else None

    # --- memories / reflection ---

    def get_memories(self, plant_id: str) -> list[Memory]:
        """その株の記憶を全部返す（並べ替えと絞り込みは prompt.rank_memories で行う）。"""
        rows = self._conn.execute(
            "SELECT * FROM memories WHERE plant_id = ? ORDER BY id", (plant_id,)
        ).fetchall()
        return [
            Memory(
                r["id"], r["content"], r["kind"], r["importance"], r["created_at"], r["updated_at"]
            )
            for r in rows
        ]

    def apply_reflection(
        self,
        plant_id: str,
        session_id: int,
        summary: str,
        new_memories: list[NewMemory],
        updated_memories: list[MemoryUpdate],
        traits: Traits,
        now: datetime,
    ) -> None:
        """reflection の結果を1トランザクションで反映する（仕様 §6.3 手順5）。

        5 つの書き込みを「全部成功するか、全部取り消すか」にまとめる。
        途中で落ちても「記憶は増えたのに振り返り済みになっていない」のような半端な状態にならない。
        """
        ts = iso(now)
        with self._conn:
            # 1. 株の性格を新しい値にする
            self._conn.execute(
                "UPDATE plants SET talkativeness = ?, affection = ?, energy = ?, curiosity = ?"
                " WHERE plant_id = ?",
                (traits.talkativeness, traits.affection, traits.energy, traits.curiosity, plant_id),
            )
            # 2. 新しい記憶を追加する
            for m in new_memories:
                self._conn.execute(
                    "INSERT INTO memories (plant_id, content, kind, importance, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (plant_id, m.content, m.kind, m.importance, ts, ts),
                )
            # 3. 既存の記憶を書き換える。id が存在しない・別の株の記憶なら、1 行も変わらず無視される
            for u in updated_memories:
                self._conn.execute(
                    "UPDATE memories SET content = ?, importance = ?, updated_at = ?"
                    " WHERE id = ? AND plant_id = ?",
                    (u.content, u.importance, ts, u.id, plant_id),
                )
            # 4. 性格の移り変わりを記録する
            self._insert_history(plant_id, session_id, traits, now)
            # 5. 要約を保存し、振り返り済みにする
            self._conn.execute(
                "UPDATE sessions SET summary = ?, reflected = 1 WHERE id = ?", (summary, session_id)
            )
