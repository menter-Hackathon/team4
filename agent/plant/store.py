"""SQLite（WAL）への性格・記憶・会話ログの読み書き（仕様 §6.2）。

時刻はすべて JST の ISO 8601 文字列で保存するため、文字列比較で時刻順になる。

DB は 1 つの SQLite ファイル（既定は data/futamaru.db）。テーブルは 6 つ:
    plants:        株ごとの名前と、お客さん（まだ誰か分からない相手）用の性格（4 軸）
    people:        株が知っている人。人ごとの性格（4 軸）と、手で固定した方言
    memories:      株が覚えていること。person_id があればその人の記憶、NULL ならお客さんの記憶（#70）
    sessions:      起きてから眠るまでの 1 回の会話。振り返りの要約・相手の人・判定した方言もここに入る
    utterances:    会話の発話 1 つずつ（持ち主・植物）
    trait_history: 性格の移り変わりの記録（成長グラフ用）。person_id が NULL なら株（お客さん）の分

SQL を書くのはこのファイルだけにして、他のファイルは PlantStore のメソッドを呼ぶ。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .personality import Traits
from .timeutil import iso, parse_iso

# テーブルを作る SQL。IF NOT EXISTS なので、すでにあるテーブルはそのまま使われる（データは消えない）。
# 注意: すでにあるテーブルには新しい列が足されない。古い DB への列の追加は _migrate() が行う
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
CREATE TABLE IF NOT EXISTS people (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  plant_id         TEXT NOT NULL REFERENCES plants(plant_id),
  name             TEXT NOT NULL,
  dialect_override TEXT,
  created_at       TEXT NOT NULL,
  talkativeness    REAL NOT NULL DEFAULT 0.5,
  affection        REAL NOT NULL DEFAULT 0.5,
  energy           REAL NOT NULL DEFAULT 0.5,
  curiosity        REAL NOT NULL DEFAULT 0.5
);
CREATE TABLE IF NOT EXISTS memories (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  plant_id    TEXT NOT NULL REFERENCES plants(plant_id),
  person_id   INTEGER REFERENCES people(id),
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
  reflected   INTEGER NOT NULL DEFAULT 0,
  person_id   INTEGER REFERENCES people(id),
  owner_dialect TEXT
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
  person_id      INTEGER REFERENCES people(id),
  talkativeness  REAL NOT NULL,
  affection      REAL NOT NULL,
  energy         REAL NOT NULL,
  curiosity      REAL NOT NULL,
  recorded_at    TEXT NOT NULL
);

-- 声で記録した水やり（仕様 §6.7、#81）。「お水あげたよ」と言われるたびに 1 行足す。
-- 1 回の会話（セッション）で 1 行まで（add_watering が確かめる）。
-- 新しいテーブルなので、古い DB でも起動時の CREATE TABLE IF NOT EXISTS で作られる（列の追加は要らない）
CREATE TABLE IF NOT EXISTS waterings (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  plant_id    TEXT NOT NULL REFERENCES plants(plant_id),  -- どの株に
  person_id   INTEGER REFERENCES people(id),              -- 誰が（NULL = お客さん・名乗る前）
  session_id  INTEGER REFERENCES sessions(id),            -- どの会話で
  watered_at  TEXT NOT NULL                               -- いつ（JST の ISO 8601 文字列）
);
"""

# 古い DB に足す列: {テーブル名: [(列名, 型と制約), ...]}。SCHEMA の新しい列と同じ内容にする。
# どれも NULL を許す（既存の行は「お客さん・判定なし」になる）
_NEW_COLUMNS = {
    "memories": [("person_id", "INTEGER REFERENCES people(id)")],
    "sessions": [
        ("person_id", "INTEGER REFERENCES people(id)"),
        ("owner_dialect", "TEXT"),
    ],
    "trait_history": [("person_id", "INTEGER REFERENCES people(id)")],
}

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
class Person:
    """people テーブルの 1 行（株が知っている人 1 人）。dialect_override は手で固定した方言（無ければ None）。"""

    id: int
    plant_id: str
    name: str
    traits: Traits
    dialect_override: str | None


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
    person_id: int | None = None  # 話し相手（None はお客さん）
    owner_dialect: str | None = None  # 振り返りで判定した相手の方言（未判定は None）


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


def _person(row: sqlite3.Row) -> Person:
    """DB の people の行から Person を作る。"""
    return Person(row["id"], row["plant_id"], row["name"], _traits(row), row["dialect_override"])


class PlantStore:
    """DB への読み書きをまとめたクラス。

    メソッドの中の `with self._conn:` は「トランザクション」。ブロックの中の書き込みは、
    全部成功したら保存され、途中でエラーになったら全部取り消される（半端な状態が残らない）。
    """

    def __init__(self, path: str, *, readonly: bool = False) -> None:
        """DB ファイルを開く（無ければ作る）。path が ":memory:" ならファイルを作らずメモリ上だけで使う（テスト用）。

        readonly=True なら読むだけで開く（ダッシュボード用、仕様 §6.6）。
        ファイルが無ければ作らずに sqlite3.OperationalError になり、テーブルの作成・列の追加もしない。
        エージェントが同じ DB に書き込んでいる最中に開いても、邪魔をしないようにするため。
        """
        if readonly:
            # mode=ro: SQLite に「読み取り専用で開く」と伝える URI の書き方。書き込もうとするとエラーになる
            uri = f"{Path(path).resolve().as_uri()}?mode=ro"
            self._conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            return
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
        self._migrate()

    def _migrate(self) -> None:
        """古い DB に足りない列を足す。新しい DB では何も起きない。

        1. テーブルごとに、今ある列の名前を PRAGMA table_info で調べる
        2. 足りない列だけ ALTER TABLE ... ADD COLUMN で足す（既存の行のデータは消えない）
        何回開いても同じ結果になる（すでにある列は足さない）。
        """
        with self._conn:
            for table, columns in _NEW_COLUMNS.items():
                have = {r["name"] for r in self._conn.execute(f"PRAGMA table_info({table})")}
                for name, definition in columns:
                    if name not in have:
                        self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

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

    def plant_created_at(self, plant_id: str) -> datetime:
        """株を作った（育ち始めた）時刻。「育ち始めて何日目か」に使う（仕様 §6.7、#82）。

        株は、エージェントが初めてその株のルームに入ったときに作られる（get_or_create_plant）。
        デモで「30 日目」と言わせたいときは、この created_at を 30 日前の時刻に書き換えておく。
        株が無ければ KeyError。
        """
        row = self._conn.execute(
            "SELECT created_at FROM plants WHERE plant_id = ?", (plant_id,)
        ).fetchone()
        if row is None:
            raise KeyError(plant_id)
        return parse_iso(row["created_at"])

    def get_plant(self, plant_id: str) -> Plant:
        """株を取り出す。無ければ KeyError。"""
        row = self._conn.execute("SELECT * FROM plants WHERE plant_id = ?", (plant_id,)).fetchone()
        if row is None:
            raise KeyError(plant_id)
        return Plant(row["plant_id"], row["name"], _traits(row))

    def _insert_history(
        self,
        plant_id: str,
        session_id: int | None,
        t: Traits,
        now: datetime,
        person_id: int | None = None,
    ) -> None:
        """性格の移り変わりを 1 行記録する（トランザクションは呼び出し側で張る）。person_id が None なら株の分。"""
        self._conn.execute(
            f"INSERT INTO trait_history (plant_id, session_id, person_id, {_TRAIT_COLS}, recorded_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                plant_id,
                session_id,
                person_id,
                t.talkativeness,
                t.affection,
                t.energy,
                t.curiosity,
                iso(now),
            ),
        )

    def trait_history(self, plant_id: str, person_id: int | None = None) -> list[TraitSnapshot]:
        """その株（person_id があればその人）の性格の記録を、古い順に全部返す（成長グラフ用）。"""
        # `person_id IS ?` は、値が NULL（None）のときも普通の値のときも使える比較
        rows = self._conn.execute(
            "SELECT * FROM trait_history WHERE plant_id = ? AND person_id IS ? ORDER BY id",
            (plant_id, person_id),
        ).fetchall()
        return [TraitSnapshot(r["session_id"], _traits(r), r["recorded_at"]) for r in rows]

    # --- people ---

    def add_person(self, plant_id: str, name: str, now: datetime) -> Person:
        """株が知っている人を登録する。性格は初期値（全部 0.5）で、成長グラフの起点も記録する。"""
        with self._conn:
            cur = self._conn.execute(
                "INSERT INTO people (plant_id, name, created_at) VALUES (?, ?, ?)",
                (plant_id, name, iso(now)),
            )
            person_id = int(cur.lastrowid)
            self._insert_history(plant_id, None, Traits(), now, person_id)
        return self.get_person(person_id)

    def get_person(self, person_id: int) -> Person:
        """人を取り出す。無ければ KeyError。"""
        row = self._conn.execute("SELECT * FROM people WHERE id = ?", (person_id,)).fetchone()
        if row is None:
            raise KeyError(person_id)
        return _person(row)

    def get_people(self, plant_id: str) -> list[Person]:
        """その株が知っている人を、登録した順に全部返す。"""
        rows = self._conn.execute(
            "SELECT * FROM people WHERE plant_id = ? ORDER BY id", (plant_id,)
        ).fetchall()
        return [_person(r) for r in rows]

    def set_dialect_override(self, person_id: int, dialect: str | None) -> None:
        """方言を手で固定する。None にすると固定を外して、会話からの自動判定に戻る。"""
        with self._conn:
            self._conn.execute(
                "UPDATE people SET dialect_override = ? WHERE id = ?", (dialect, person_id)
            )

    def recent_dialects(self, person_id: int, limit: int = 5) -> list[str]:
        """その人のセッションで判定した方言を、新しい順に最大 limit 件返す。

        判定できなかったもの（NULL と "unknown"）は数えない。多数決は dialect.resolve_dialect が行う。
        """
        rows = self._conn.execute(
            "SELECT owner_dialect FROM sessions"
            " WHERE person_id = ? AND owner_dialect IS NOT NULL AND owner_dialect != 'unknown'"
            " ORDER BY started_at DESC, id DESC LIMIT ?",
            (person_id, limit),
        ).fetchall()
        return [r["owner_dialect"] for r in rows]

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
            r["person_id"],
            r["owner_dialect"],
        )

    def set_session_person(self, session_id: int, person_id: int) -> None:
        """セッションの話し相手が誰か分かったときに記録する（始めはお客さん = NULL）。"""
        with self._conn:
            self._conn.execute(
                "UPDATE sessions SET person_id = ? WHERE id = ?", (person_id, session_id)
            )

    def get_utterances(self, session_id: int) -> list[Utterance]:
        """そのセッションの発話を、話した順に全部返す。"""
        rows = self._conn.execute(
            "SELECT role, text, created_at FROM utterances WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
        return [Utterance(r["role"], r["text"], r["created_at"]) for r in rows]

    def count_user_utterances_since(
        self, plant_id: str, since: datetime, person_id: int | None = None
    ) -> int:
        """since 以降に、その人（None ならお客さん）がその株に話しかけた回数。おしゃべり度の計算に使う（仕様 §6.3）。"""
        # 発話（utterances）には plant_id が無いので、セッション（sessions）とつないで株と人を絞る
        row = self._conn.execute(
            "SELECT COUNT(*) FROM utterances u JOIN sessions s ON u.session_id = s.id"
            " WHERE s.plant_id = ? AND s.person_id IS ? AND u.role = 'user' AND u.created_at >= ?",
            (plant_id, person_id, iso(since)),
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

    def last_talk_at(self, plant_id: str, person_id: int | None = None) -> datetime | None:
        """前に話した会話が終わった時刻。「何日ぶりか」に使う（仕様 §6.7、#82）。

        引数:
            person_id: その人との会話だけを見る。None なら相手を問わず（お客さんも含めて）一番最近の会話。
                起きた直後はまだ相手が分からないので None で呼ばれ、名乗ったあとはその人の id で呼ばれる
        戻り値:
            一番最近に終わったセッションの ended_at。まだ一度も話していなければ None
        今のセッションはまだ終わっていない（ended_at が NULL）ので、ここには入らない。
        つまり「今の会話」ではなく「前の会話」の時刻が返る。
        """
        # 1. まず「この株の、終わった会話」を全部対象にする SQL を作る
        sql = "SELECT MAX(ended_at) AS t FROM sessions WHERE plant_id = ? AND ended_at IS NOT NULL"
        args: tuple = (plant_id,)
        # 2. 人が決まっていれば、その人との会話だけに絞る条件を足す
        if person_id is not None:
            sql += " AND person_id = ?"
            args = (plant_id, person_id)
        row = self._conn.execute(sql, args).fetchone()
        # 時刻はすべて +09:00 付きの同じ形の文字列なので、MAX（文字列の大小）で一番新しいものが取れる
        return parse_iso(row["t"]) if row["t"] else None

    def latest_summary(self, plant_id: str, person_id: int | None = None) -> str | None:
        """その人（None ならお客さん）との、一番最近に終わった会話の要約。「前回の会話」に使う。無ければ None。"""
        row = self._conn.execute(
            "SELECT summary FROM sessions WHERE plant_id = ? AND person_id IS ?"
            " AND reflected = 1 AND summary IS NOT NULL"
            " ORDER BY ended_at DESC, id DESC LIMIT 1",
            (plant_id, person_id),
        ).fetchone()
        return row["summary"] if row else None

    # --- waterings（水やり、#81） ---

    def add_watering(
        self, plant_id: str, person_id: int | None, session_id: int, now: datetime
    ) -> bool:
        """水やりを 1 回記録する。同じ会話（session_id）でもう記録していれば何もしない。

        引数:
            plant_id: どの株に
            person_id: 誰が（相手が未確定・お客さんなら None）
            session_id: どの会話で
            now: 水をあげた時刻
        戻り値: 記録したら True、同じ会話ですでに記録済みなら False。
        「お水あげたよ」「水やりもしたよ」と 1 回の会話で 2 回言われても、1 回の水やりとして数えるため。
        会話中（conversation.py）と、眠ったあとの振り返り（reflection.py）の両方から呼ばれるが、
        このしくみのおかげで、両方が同じ水やりを見つけても二重に数えない。
        """
        # with self._conn: の中は 1 つのまとまり（トランザクション）として書き込まれる
        with self._conn:
            if self.watered_in_session(session_id):
                return False
            self._conn.execute(
                "INSERT INTO waterings (plant_id, person_id, session_id, watered_at)"
                " VALUES (?, ?, ?, ?)",
                (plant_id, person_id, session_id, iso(now)),
            )
        return True

    def watered_in_session(self, session_id: int) -> bool:
        """その会話（session_id）で水やりを記録済みなら True。

        プロンプトに「この会話で水をもらった」を入れるかどうか、と
        add_watering が 2 回目を記録しないための確認に使う。
        """
        row = self._conn.execute(
            "SELECT 1 FROM waterings WHERE session_id = ? LIMIT 1", (session_id,)
        ).fetchone()
        return row is not None

    def last_watered_at(self, plant_id: str) -> datetime | None:
        """その株が最後に水をもらった時刻。一度ももらっていなければ None。

        誰があげたかは問わない（植物ののどの渇きは、誰があげても同じなので）。
        「のどが渇いた」と言うかどうかを決めるのに使う（prompt.py の _watering_line）。
        時刻は同じ形の文字列で保存しているので、MAX（文字列の一番大きいもの）が一番新しい時刻になる。
        """
        row = self._conn.execute(
            "SELECT MAX(watered_at) AS t FROM waterings WHERE plant_id = ?", (plant_id,)
        ).fetchone()
        return parse_iso(row["t"]) if row["t"] else None

    # --- memories / reflection ---

    def get_memories(self, plant_id: str, person_id: int | None = None) -> list[Memory]:
        """その株の記憶のうち、person_id の人の記憶だけを返す（#70）。

        person_id が None ならお客さん（名乗らなかった人）の記憶（person_id が NULL）だけ。
        名乗った人には、ほかの人の記憶もお客さんの記憶も返さない。お客さんと話した内容を
        混ぜると、植物が「ピザのこと考えてるんだって？」と、その人が話していない話題を振るため。
        並べ替えと絞り込みは prompt.rank_memories で行う。
        """
        # SQLite の「IS」は、NULL どうしも等しいとみなす（「=」だと NULL = NULL は真にならない）。
        # これで person_id が None のときも、数字のときも、同じ 1 つの条件で書ける
        rows = self._conn.execute(
            "SELECT * FROM memories WHERE plant_id = ? AND person_id IS ? ORDER BY id",
            (plant_id, person_id),
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
        *,
        person_id: int | None = None,
        owner_dialect: str = "unknown",
    ) -> None:
        """reflection の結果を1トランザクションで反映する（仕様 §6.3 手順5）。

        Args:
            person_id: 話し相手。None ならお客さん（性格は株に、記憶はお客さんの記憶になる）
            owner_dialect: このセッションで判定した相手の方言（判断できなければ "unknown"）

        5 つの書き込みを「全部成功するか、全部取り消すか」にまとめる。
        途中で落ちても「記憶は増えたのに振り返り済みになっていない」のような半端な状態にならない。
        """
        ts = iso(now)
        with self._conn:
            # 1. 性格を新しい値にする。人がいればその人の性格、いなければ株（お客さん用）の性格
            cols = (traits.talkativeness, traits.affection, traits.energy, traits.curiosity)
            if person_id is not None:
                self._conn.execute(
                    "UPDATE people SET talkativeness = ?, affection = ?, energy = ?, curiosity = ?"
                    " WHERE id = ?",
                    (*cols, person_id),
                )
            else:
                self._conn.execute(
                    "UPDATE plants SET talkativeness = ?, affection = ?, energy = ?, curiosity = ?"
                    " WHERE plant_id = ?",
                    (*cols, plant_id),
                )
            # 2. 新しい記憶を追加する（person_id 付き。None ならお客さんの記憶）
            for m in new_memories:
                self._conn.execute(
                    "INSERT INTO memories (plant_id, person_id, content, kind, importance,"
                    " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (plant_id, person_id, m.content, m.kind, m.importance, ts, ts),
                )
            # 3. 既存の記憶を書き換える。id が存在しない・別の株や別の人の記憶なら、1 行も変わらず無視される
            #    （書き換えてよいのは、この人の記憶だけ。お客さんならお客さんの記憶だけ、#70）
            for u in updated_memories:
                self._conn.execute(
                    "UPDATE memories SET content = ?, importance = ?, updated_at = ?"
                    " WHERE id = ? AND plant_id = ? AND person_id IS ?",
                    (u.content, u.importance, ts, u.id, plant_id, person_id),
                )
            # 4. 性格の移り変わりを記録する
            self._insert_history(plant_id, session_id, traits, now, person_id)
            # 5. 要約と方言の判定を保存し、振り返り済みにする
            self._conn.execute(
                "UPDATE sessions SET summary = ?, owner_dialect = ?, reflected = 1 WHERE id = ?",
                (summary, owner_dialect, session_id),
            )
