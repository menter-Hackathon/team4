"""SQLite（WAL）への性格・記憶・会話ログの読み書き（仕様 §6.2）。

時刻はすべて JST の ISO 8601 文字列で保存するため、文字列比較で時刻順になる。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .personality import Traits
from .timeutil import iso

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

_TRAIT_COLS = "talkativeness, affection, energy, curiosity"


@dataclass
class Plant:
    plant_id: str
    name: str
    traits: Traits


@dataclass
class Memory:
    id: int
    content: str
    kind: str
    importance: float
    created_at: str
    updated_at: str


@dataclass
class Session:
    id: int
    plant_id: str
    started_at: str
    ended_at: str | None
    user_turns: int
    summary: str | None
    reflected: bool


@dataclass
class Utterance:
    role: str
    text: str
    created_at: str


@dataclass
class NewMemory:
    content: str
    kind: str
    importance: float


@dataclass
class MemoryUpdate:
    id: int
    content: str
    importance: float


@dataclass
class TraitSnapshot:
    session_id: int | None
    traits: Traits
    recorded_at: str


def _traits(row: sqlite3.Row) -> Traits:
    return Traits(row["talkativeness"], row["affection"], row["energy"], row["curiosity"])


class PlantStore:
    def __init__(self, path: str) -> None:
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA)

    def close(self) -> None:
        self._conn.close()

    # --- plants ---

    def get_or_create_plant(self, plant_id: str, now: datetime) -> Plant:
        with self._conn:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO plants (plant_id, created_at) VALUES (?, ?)",
                (plant_id, iso(now)),
            )
            if cur.rowcount == 1:
                t = Traits()
                self._insert_history(plant_id, None, t, now)
        return self.get_plant(plant_id)

    def get_plant(self, plant_id: str) -> Plant:
        row = self._conn.execute("SELECT * FROM plants WHERE plant_id = ?", (plant_id,)).fetchone()
        if row is None:
            raise KeyError(plant_id)
        return Plant(row["plant_id"], row["name"], _traits(row))

    def _insert_history(
        self, plant_id: str, session_id: int | None, t: Traits, now: datetime
    ) -> None:
        self._conn.execute(
            f"INSERT INTO trait_history (plant_id, session_id, {_TRAIT_COLS}, recorded_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (plant_id, session_id, t.talkativeness, t.affection, t.energy, t.curiosity, iso(now)),
        )

    def trait_history(self, plant_id: str) -> list[TraitSnapshot]:
        rows = self._conn.execute(
            "SELECT * FROM trait_history WHERE plant_id = ? ORDER BY id", (plant_id,)
        ).fetchall()
        return [TraitSnapshot(r["session_id"], _traits(r), r["recorded_at"]) for r in rows]

    # --- sessions / utterances ---

    def start_session(self, plant_id: str, now: datetime) -> int:
        with self._conn:
            cur = self._conn.execute(
                "INSERT INTO sessions (plant_id, started_at) VALUES (?, ?)", (plant_id, iso(now))
            )
        return int(cur.lastrowid)

    def add_utterance(self, session_id: int, role: str, text: str, now: datetime) -> None:
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
        with self._conn:
            self._conn.execute(
                "UPDATE sessions SET ended_at = ? WHERE id = ?", (iso(now), session_id)
            )

    def get_session(self, session_id: int) -> Session:
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
        rows = self._conn.execute(
            "SELECT role, text, created_at FROM utterances WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
        return [Utterance(r["role"], r["text"], r["created_at"]) for r in rows]

    def count_user_utterances_since(self, plant_id: str, since: datetime) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM utterances u JOIN sessions s ON u.session_id = s.id"
            " WHERE s.plant_id = ? AND u.role = 'user' AND u.created_at >= ?",
            (plant_id, iso(since)),
        ).fetchone()
        return int(row[0])

    def repair_unfinished_sessions(self, plant_id: str) -> int:
        """ended_at の無いセッションを、最後の発話時刻（無ければ started_at）で閉じる。"""
        with self._conn:
            cur = self._conn.execute(
                "UPDATE sessions SET ended_at = COALESCE("
                " (SELECT MAX(created_at) FROM utterances WHERE session_id = sessions.id), started_at)"
                " WHERE plant_id = ? AND ended_at IS NULL",
                (plant_id,),
            )
        return cur.rowcount

    def unreflected_sessions(self, plant_id: str) -> list[int]:
        rows = self._conn.execute(
            "SELECT id FROM sessions WHERE plant_id = ? AND ended_at IS NOT NULL AND reflected = 0"
            " ORDER BY id",
            (plant_id,),
        ).fetchall()
        return [r["id"] for r in rows]

    def mark_reflected(self, session_id: int) -> None:
        with self._conn:
            self._conn.execute("UPDATE sessions SET reflected = 1 WHERE id = ?", (session_id,))

    def latest_summary(self, plant_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT summary FROM sessions WHERE plant_id = ? AND reflected = 1 AND summary IS NOT NULL"
            " ORDER BY ended_at DESC, id DESC LIMIT 1",
            (plant_id,),
        ).fetchone()
        return row["summary"] if row else None

    # --- memories / reflection ---

    def get_memories(self, plant_id: str) -> list[Memory]:
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
        """reflection の結果を1トランザクションで反映する（仕様 §6.3 手順5）。"""
        ts = iso(now)
        with self._conn:
            self._conn.execute(
                "UPDATE plants SET talkativeness = ?, affection = ?, energy = ?, curiosity = ?"
                " WHERE plant_id = ?",
                (traits.talkativeness, traits.affection, traits.energy, traits.curiosity, plant_id),
            )
            for m in new_memories:
                self._conn.execute(
                    "INSERT INTO memories (plant_id, content, kind, importance, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (plant_id, m.content, m.kind, m.importance, ts, ts),
                )
            for u in updated_memories:
                self._conn.execute(
                    "UPDATE memories SET content = ?, importance = ?, updated_at = ?"
                    " WHERE id = ? AND plant_id = ?",
                    (u.content, u.importance, ts, u.id, plant_id),
                )
            self._insert_history(plant_id, session_id, traits, now)
            self._conn.execute(
                "UPDATE sessions SET summary = ?, reflected = 1 WHERE id = ?", (summary, session_id)
            )
