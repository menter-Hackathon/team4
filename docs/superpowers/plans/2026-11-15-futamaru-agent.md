# ふたまる 音声AIエージェント Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ESP32-S3 の植物と LiveKit 経由で音声会話し、会話履歴から性格（4軸）と長期記憶が育つエージェントの骨組みを作る。

**Architecture:** `agent/plant/` に LiveKit 非依存の純粋ロジック（性格・記憶・プロンプト・表情タグ・ウェイクワード・会話の進行）を置き、時刻と LLM 呼び出しは注入する。`agent/main.py` と `agent/plugins/` だけが LiveKit に依存し、`PlantConversation`（Task 8）を LiveKit のイベントとノードに配線する。永続化は SQLite（WAL）。

**Tech Stack:** Python 3.12 / uv / `livekit-agents[openai,silero]~=1.8` / `livekit-api` / `openai`（AsyncOpenAI）/ `python-dotenv` / pytest + pytest-asyncio + respx

**Spec:** `docs/superpowers/specs/2026-11-13-futamaru-agent-design.md`（以下「仕様」）。各タスクは仕様の該当節と合わせて読むこと。

## Global Constraints

- 時刻はすべて JST、`+09:00` 付き ISO 8601 文字列（秒精度）で保存する（仕様 §2）。
- `agent/plant/` 以下は `livekit` を import しない。LLM 呼び出しと現在時刻は引数で注入する（仕様 §4 境界のルール）。
- LiveKit に依存するのは `agent/main.py` と `agent/plugins/` のみ。
- 性格値は 0.0〜1.0、初期値 0.5。1回の reflection での変化量は ±0.05 にクランプ（仕様 §6.1, §6.3）。
- SQLite は WAL モード。スキーマは仕様 §6.2 をそのまま使う。
- 植物の名前は全株共通で「ふたまる」。ルーム名 `plant-<plant_id>`、デバイス identity `device-<plant_id>`。
- 環境変数のみで URL・モデル名を切り替える。`LLM_MODEL` は既定値を持たない（GPU サイズ未決、仕様 §13）。
- テストは `uv run pytest`。pytest-asyncio は `asyncio_mode = "auto"`。
- コミットは各タスクの最後に `main` へローカルコミットする。push はユーザーが行う。

## Review Focus

1. **チャンク境界をまたぐ表情タグ・応答途中のタグ**: `"[ha" + "ppy]こん"` や `"[happy]うん！[love]だいすき"` でもタグが音声に読み上げられないこと → Task 6 のテスト。
2. **ウェイクワードの表記ゆれ・前置き**: `"ねえ、フタマル！"`、全角 `"２丸"` でも起きること → Task 7 のテスト。
3. **reflection の壊れた出力**: 他の株の記憶 id、範囲外の importance、文字列の trait_delta、```json フェンス付き出力でも落ちずに正しく反映されること → Task 3, Task 5 のテスト。
4. **ユーザーが長く話し続けている間の睡眠**: ユーザー発話中は無言タイマーが止まり、話の途中で眠らないこと → Task 7 のテスト。
5. **空・空白だけの文字起こし**: `AWAKE` 中に `"  "` が来ても記録・応答しないこと → Task 8 のテスト。

---

## File Structure

```
pyproject.toml                 依存・pytest 設定
.env.example                   環境変数の雛形（LLM_MODEL は 80GB/24GB の2案をコメントで併記）
agent/__init__.py
agent/config.py                Config dataclass と load_config()
agent/main.py                  LiveKit 配線（FutamaruAgent, entrypoint）
agent/plugins/__init__.py
agent/plugins/whisper_stt.py   create_stt(cfg)
agent/plugins/openai_tts.py    create_tts(cfg)
agent/plugins/llm.py           create_llm(cfg), create_json_completer(cfg)
agent/plant/__init__.py
agent/plant/timeutil.py        JST, Clock, jst_now(), iso(), parse_iso()
agent/plant/personality.py     Traits, level(), apply_deltas(), nudge_delay()
agent/plant/store.py           PlantStore とデータクラス
agent/plant/prompt.py          rank_memories(), build_system_prompt(), 定型文
agent/plant/reflection.py      REFLECTION_SCHEMA, parse_reflection(), reflect_session(), recover()
agent/plant/expression.py      ExpressionParser
agent/plant/wake.py            normalize(), detect_wake(), WakeController
agent/plant/conversation.py    PlantConversation, TurnDecision
scripts/__init__.py
scripts/issue_token.py         build_token(), CLI
scripts/simulate.py            会話ログ JSON → reflection の早送り、CLI
scripts/samples/plant-a.json   甘えん坊に育つ会話ログ
scripts/samples/plant-b.json   素っ気なく育つ会話ログ
tests/...                      各モジュールに対応
README.md                      セットアップ・起動手順（Task 10 で更新）
```

---

### Task 1: プロジェクト雛形と設定読み込み

**Files:**
- Create: `pyproject.toml`, `.env.example`, `.gitignore`, `agent/__init__.py`, `agent/plant/__init__.py`, `agent/plugins/__init__.py`, `scripts/__init__.py`, `agent/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces:
  - `class ConfigError(Exception)`
  - `@dataclass(frozen=True) class Config`: `llm_base_url: str`, `llm_model: str`, `llm_api_key: str`, `stt_base_url: str`, `stt_model: str`, `stt_api_key: str`, `tts_model: str`, `tts_voice: str`, `tts_base_url: str | None`, `tts_api_key: str`, `tts_instructions: str`, `db_path: str`, `wake_words: tuple[str, ...]`, `sleep_after: float`, `llm_timeout: float`, `stt_timeout: float`, `tts_timeout: float`, `dev_plant_id: str | None`
  - `load_config(env: Mapping[str, str] = os.environ) -> Config`
  - 定数 `TTS_INSTRUCTIONS = "やわらかく、少しゆっくり、のんびりした植物らしい話し方で話す。"`

- [ ] **Step 1: 雛形を作る**

`uv init --package` は使わず手で `pyproject.toml` を書く。`requires-python = ">=3.12"`、依存: `livekit-agents[openai,silero]~=1.8`, `livekit-api`, `openai`, `python-dotenv`。dev 依存: `pytest`, `pytest-asyncio`, `respx`。`[tool.pytest.ini_options] asyncio_mode = "auto"`, `testpaths = ["tests"]`。パッケージは `agent` と `scripts`（hatchling の `packages = ["agent", "scripts"]`）。`.gitignore` に `.venv/`, `data/`, `.env`, `__pycache__/`。`uv sync` が通ること。

`.env.example` には仕様 §8 の全環境変数を書く。`LLM_MODEL` は `# 80GB: google/gemma-4-26B-A4B-it` / `# 24GB: google/gemma-4-E4B-it` の2行をコメントで併記し、値は空。`DEV_PLANT_ID`（console モード用、後述 Task 10）も載せる。

- [ ] **Step 2: 失敗するテストを書く**

```python
BASE = {
    "LLM_BASE_URL": "http://gpu:8000/v1",
    "LLM_MODEL": "google/gemma-4-E4B-it",
    "STT_BASE_URL": "http://gpu:8001/v1",
    "OPENAI_API_KEY": "sk-test",
}


def test_defaults():
    c = load_config(BASE)
    assert c.stt_model == "kotoba-tech/kotoba-whisper-v2.0-faster"
    assert c.tts_model == "gpt-4o-mini-tts" and c.tts_voice == "coral" and c.tts_base_url is None
    assert c.tts_api_key == "sk-test" and c.llm_api_key == "EMPTY" and c.stt_api_key == "EMPTY"
    assert c.db_path == "./data/futamaru.db"
    assert c.wake_words == ("ふたまる", "二丸", "ふた丸", "2丸")
    assert (c.sleep_after, c.llm_timeout, c.stt_timeout, c.tts_timeout) == (60.0, 15.0, 10.0, 10.0)
    assert c.tts_instructions == TTS_INSTRUCTIONS and c.dev_plant_id is None


def test_overrides():
    c = load_config(
        {
            **BASE,
            "WAKE_WORDS": "ふたまる, ふーちゃん",
            "SLEEP_AFTER_SECONDS": "30",
            "TTS_BASE_URL": "http://gpu:8002/v1",
            "DEV_PLANT_ID": "dev",
        }
    )
    assert c.wake_words == ("ふたまる", "ふーちゃん") and c.sleep_after == 30.0
    assert c.tts_base_url == "http://gpu:8002/v1" and c.dev_plant_id == "dev"


@pytest.mark.parametrize("missing", ["LLM_BASE_URL", "LLM_MODEL", "STT_BASE_URL"])
def test_required(missing):
    env = dict(BASE)
    del env[missing]
    with pytest.raises(ConfigError, match=missing):
        load_config(env)


def test_openai_key_required_only_without_tts_base_url():
    env = dict(BASE)
    del env["OPENAI_API_KEY"]
    with pytest.raises(ConfigError, match="OPENAI_API_KEY"):
        load_config(env)
    assert load_config({**env, "TTS_BASE_URL": "http://gpu:8002/v1"}).tts_api_key == "EMPTY"
```

- [ ] **Step 3: 失敗を確認** — `uv run pytest tests/test_config.py -v` → ImportError で FAIL

- [ ] **Step 4: `load_config` を実装** — 環境変数名は仕様 §8 の通り（`LLM_API_KEY`, `STT_API_KEY` は既定 `"EMPTY"`、`FUTAMARU_DB_PATH`, `SLEEP_AFTER_SECONDS`）。`WAKE_WORDS` はカンマ区切りで前後空白を除去。

- [ ] **Step 5: 通過を確認** — `uv run pytest tests/test_config.py -v` → PASS

- [ ] **Step 6: コミット**

```bash
git add pyproject.toml uv.lock .env.example .gitignore agent scripts tests
git commit -m "feat: project scaffold and config loading"
```

---

### Task 2: 性格パラメータ（`personality.py`）と時刻ユーティリティ

**Files:**
- Create: `agent/plant/timeutil.py`, `agent/plant/personality.py`
- Test: `tests/plant/test_personality.py`, `tests/plant/test_timeutil.py`

**Interfaces:**
- Produces (timeutil):
  - `JST = timezone(timedelta(hours=9))`, `Clock = Callable[[], datetime]`
  - `jst_now() -> datetime`、`iso(dt: datetime) -> str`（JST に変換し `isoformat(timespec="seconds")`、例 `"2026-11-15T10:00:00+09:00"`）、`parse_iso(s: str) -> datetime`
- Produces (personality):
  - `TRAIT_NAMES = ("talkativeness", "affection", "energy", "curiosity")`
  - `@dataclass(frozen=True) class Traits`: 4軸 `float = 0.5`、`as_dict() -> dict[str, float]`
  - `Level = Literal["low", "mid", "high"]`、`level(value: float) -> Level`
  - `apply_deltas(current: Traits, llm_deltas: Mapping[str, object], recent_user_turns: int) -> Traits`
  - `nudge_delay(talkativeness: float) -> float | None`

- [ ] **Step 1: 失敗するテストを書く**

```python
def test_iso_is_jst_seconds():
    dt = datetime(2026, 11, 15, 1, 0, 0, 123456, tzinfo=timezone.utc)
    assert iso(dt) == "2026-11-15T10:00:00+09:00"
    assert parse_iso("2026-11-15T10:00:00+09:00") == dt.replace(microsecond=0)


@pytest.mark.parametrize(
    "v,lv",
    [(0.0, "low"), (0.33, "low"), (0.34, "mid"), (0.66, "mid"), (0.67, "high"), (1.0, "high")],
)
def test_level_boundaries(v, lv):
    assert level(v) == lv


def test_deltas_clamped_to_005():
    t = apply_deltas(
        Traits(), {"affection": 0.2, "energy": -0.3, "curiosity": 0.01}, recent_user_turns=50
    )
    assert t.affection == pytest.approx(0.55) and t.energy == pytest.approx(0.45)
    assert t.curiosity == pytest.approx(0.51)


def test_values_clamped_to_unit_range():
    t = apply_deltas(Traits(affection=0.98, energy=0.02), {"affection": 0.05, "energy": -0.05}, 50)
    assert t.affection == 1.0 and t.energy == 0.0


def test_talkativeness_blend():
    # delta = 0.5*0.04 + 0.5*(1.0-0.5)*0.1 = 0.045
    assert apply_deltas(Traits(), {"talkativeness": 0.04}, 100).talkativeness == pytest.approx(
        0.545
    )
    assert apply_deltas(Traits(), {"talkativeness": 0.04}, 300).talkativeness == pytest.approx(
        0.545
    )
    # delta = 0 + 0.5*(0-0.5)*0.1 = -0.025
    assert apply_deltas(Traits(), {}, 0).talkativeness == pytest.approx(0.475)
    # llm_delta は先に ±0.05 にクランプ: 0.5*0.05 + 0.025 = 0.05
    assert apply_deltas(Traits(), {"talkativeness": 0.5}, 100).talkativeness == pytest.approx(0.55)


def test_bad_delta_values_are_zero():
    t = apply_deltas(Traits(), {"energy": "high", "curiosity": None, "unknown": 0.05}, 50)
    assert t.energy == 0.5 and t.curiosity == 0.5


@pytest.mark.parametrize("t,expected", [(0.9, 13.0), (0.5, 25.0), (0.3, 31.0), (0.29, None)])
def test_nudge_delay(t, expected):
    assert nudge_delay(t) == (pytest.approx(expected) if expected else None)
```

- [ ] **Step 2: 失敗を確認** — `uv run pytest tests/plant -v` → FAIL

- [ ] **Step 3: 実装** — 仕様 §6.3 の手順3・4、§7.2 の式。`level` は `v <= 0.33` → low、`v < 0.67` → mid、それ以外 high。`bool` は数値扱いしない（`isinstance(x, (int, float)) and not isinstance(x, bool)`）。

- [ ] **Step 4: 通過を確認** — `uv run pytest tests/plant -v` → PASS

- [ ] **Step 5: コミット** — `git commit -m "feat: personality traits and JST time utils"`

---

### Task 3: SQLite ストア（`store.py`）

**Files:**
- Create: `agent/plant/store.py`
- Test: `tests/plant/test_store.py`

**Interfaces:**
- Consumes: `Traits`（Task 2）, `iso`/`parse_iso`（Task 2）
- Produces（すべて `agent/plant/store.py`、時刻引数は `datetime`、戻りの時刻は ISO 文字列）:
  - `@dataclass Plant(plant_id: str, name: str, traits: Traits)`
  - `@dataclass Memory(id: int, content: str, kind: str, importance: float, created_at: str, updated_at: str)`
  - `@dataclass Session(id: int, plant_id: str, started_at: str, ended_at: str | None, user_turns: int, summary: str | None, reflected: bool)`
  - `@dataclass Utterance(role: str, text: str, created_at: str)`
  - `@dataclass NewMemory(content: str, kind: str, importance: float)`、`@dataclass MemoryUpdate(id: int, content: str, importance: float)`
  - `@dataclass TraitSnapshot(session_id: int | None, traits: Traits, recorded_at: str)`
  - `class PlantStore(path: str)`（接続は属性 `_conn`）: `get_or_create_plant(plant_id, now) -> Plant`、`get_plant(plant_id) -> Plant`、`start_session(plant_id, now) -> int`、`add_utterance(session_id, role, text, now) -> None`（role が `"user"` なら `user_turns += 1`）、`end_session(session_id, now) -> None`、`get_session(session_id) -> Session`、`get_utterances(session_id) -> list[Utterance]`、`get_memories(plant_id) -> list[Memory]`、`latest_summary(plant_id) -> str | None`、`count_user_utterances_since(plant_id, since: datetime) -> int`、`repair_unfinished_sessions(plant_id) -> int`、`unreflected_sessions(plant_id) -> list[int]`、`mark_reflected(session_id) -> None`、`apply_reflection(plant_id, session_id, summary, new_memories: list[NewMemory], updated_memories: list[MemoryUpdate], traits: Traits, now) -> None`、`trait_history(plant_id) -> list[TraitSnapshot]`、`close() -> None`

- [ ] **Step 1: 失敗するテストを書く**（`store` フィクスチャ = `PlantStore(str(tmp_path / "t.db"))`、`T0 = datetime(2026,11,15,10,0,tzinfo=JST)`）

```python
def test_wal_mode(store):
    assert store._conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_create_plant_once_with_initial_history(store):
    p = store.get_or_create_plant("a", T0)
    assert p.name == "ふたまる" and p.traits == Traits()
    store.get_or_create_plant("a", T0 + timedelta(days=1))
    h = store.trait_history("a")
    assert len(h) == 1 and h[0].session_id is None and h[0].traits == Traits()
    assert h[0].recorded_at == "2026-11-15T10:00:00+09:00"


def test_session_and_utterances(store):
    store.get_or_create_plant("a", T0)
    sid = store.start_session("a", T0)
    store.add_utterance(sid, "user", "ただいま", T0)
    store.add_utterance(sid, "plant", "おかえり！", T0 + timedelta(seconds=2))
    store.end_session(sid, T0 + timedelta(minutes=1))
    s = store.get_session(sid)
    assert s.user_turns == 1 and s.ended_at == "2026-11-15T10:01:00+09:00" and not s.reflected
    assert [u.role for u in store.get_utterances(sid)] == ["user", "plant"]


def test_repair_unfinished(store):
    store.get_or_create_plant("a", T0)
    s1 = store.start_session("a", T0)
    store.add_utterance(s1, "user", "x", T0 + timedelta(minutes=5))
    s2 = store.start_session("a", T0 + timedelta(hours=1))
    assert store.repair_unfinished_sessions("a") == 2
    assert store.get_session(s1).ended_at == iso(T0 + timedelta(minutes=5))
    assert store.get_session(s2).ended_at == store.get_session(s2).started_at
    assert store.unreflected_sessions("a") == [s1, s2]


def test_unreflected_excludes_open_sessions(store):
    store.get_or_create_plant("a", T0)
    store.start_session("a", T0)
    assert store.unreflected_sessions("a") == []


def test_apply_reflection(store):
    store.get_or_create_plant("a", T0)
    store.get_or_create_plant("b", T0)
    # 既存記憶: a に1件、b に1件（テスト用に apply_reflection で作る）
    ...
    store.apply_reflection(
        "a",
        sid,
        "テストの話をした",
        [NewMemory("明日テスト", "event", 0.8)],
        [
            MemoryUpdate(a_mem_id, "辛いカレーが好き", 0.7),
            MemoryUpdate(999, "x", 0.5),
            MemoryUpdate(b_mem_id, "乗っ取り", 0.9),
        ],
        Traits(affection=0.53),
        T0 + timedelta(hours=1),
    )
    assert store.get_plant("a").traits.affection == pytest.approx(0.53)
    mems = {m.id: m for m in store.get_memories("a")}
    assert mems[a_mem_id].content == "辛いカレーが好き" and mems[a_mem_id].updated_at == iso(
        T0 + timedelta(hours=1)
    )
    assert any(m.content == "明日テスト" for m in mems.values())
    assert store.get_memories("b")[0].content != "乗っ取り"  # 他株の記憶は更新しない
    assert store.get_session(sid).reflected and store.latest_summary("a") == "テストの話をした"
    assert store.trait_history("a")[-1].session_id == sid


def test_apply_reflection_is_atomic(
    store,
): ...  # NewMemory("x", None, 0.5)（kind NOT NULL 違反）で例外 → traits・summary・reflected・trait_history が変わっていない


def test_count_user_utterances_since(
    store,
): ...  # a の user 2件(期間内) + user 1件(8日前) + plant 1件、b の user 1件 → count("a", T0 - 7日) == 2
```

`...` の部分はテスト作者がデータを組む（期待値は書いてある通り）。

- [ ] **Step 2: 失敗を確認** — `uv run pytest tests/plant/test_store.py -v` → FAIL

- [ ] **Step 3: 実装** — `sqlite3.connect(path, check_same_thread=False)`、親ディレクトリを `mkdir(parents=True, exist_ok=True)`、`PRAGMA journal_mode=WAL` と `PRAGMA foreign_keys=ON`、`CREATE TABLE IF NOT EXISTS` で仕様 §6.2 のスキーマ。`apply_reflection` は `with self._conn:` の1トランザクションで、更新は `WHERE id = ? AND plant_id = ?`。`latest_summary` は `reflected = 1 AND summary IS NOT NULL` を `ended_at DESC` で1件。`unreflected_sessions` は `ended_at IS NOT NULL AND reflected = 0` を id 昇順。

- [ ] **Step 4: 通過を確認** — PASS

- [ ] **Step 5: コミット** — `git commit -m "feat: SQLite plant store"`

---

### Task 4: プロンプト組み立て（`prompt.py`）

**Files:**
- Create: `agent/plant/prompt.py`
- Test: `tests/plant/test_prompt.py`

**Interfaces:**
- Consumes: `Traits`, `level`（Task 2）, `Memory`（Task 3）, `parse_iso`
- Produces:
  - `recency_factor(updated_at: str, now: datetime) -> float`（`0.5 ** (経過日数 / 7)`、経過日数は秒から小数で計算）
  - `rank_memories(memories: list[Memory], now: datetime, limit: int) -> list[Memory]`（`importance * recency_factor` 降順）
  - `build_system_prompt(name: str, traits: Traits, memories: list[Memory], last_summary: str | None, now: datetime) -> str`
  - 定数 `WAKE_GREETING_INSTRUCTION = "持ち主に呼ばれて起きた。記憶や前回の会話を踏まえて短く話しかける。"`、`NUDGE_INSTRUCTION = "持ち主がしばらく黙っている。記憶や前回の会話を踏まえて、短く話題を振る。"`、`FALLBACK_REPLY = "ん…ちょっとぼーっとしてた"`

プロンプト本文（この順に `\n\n` で連結。4・5 は該当が無ければ節ごと省略）:
1. `あなたは観葉植物の{name}。持ち主と話し言葉で短く話す。1回の返答は{LENGTH[level(talkativeness)]}。`
2. `返答の先頭に必ず表情タグを1つ付ける: [neutral] [happy] [sad] [surprised] [love] [sleepy]`
3. 4軸の指示文を軸の順に1行ずつ
4. `覚えていること:` の後に上位10件を `- {content}` で
5. `前回の会話: {summary}`

定数（文言は確定）:

| | low | mid | high |
|---|---|---|---|
| `LENGTH` | `1文` | `1〜2文` | `2〜3文で、ときどき自分から話題も振る` |
| talkativeness | `口数は少なめで、静かに返す。` | `ほどよく話す。` | `おしゃべりで、自分からもよく話す。` |
| affection | `少し素っ気なく接する。` | `普通に親しく接する。` | `持ち主に甘えて親しげに話す。` |
| energy | `落ち着いた口調で話す。` | `ふつうのテンションで話す。` | `テンション高めで、「！」を多めに使う。` |
| curiosity | `質問はあまりせず、相づち中心にする。` | `ときどき持ち主に質問する。` | `持ち主のことをよく質問する。` |

- [ ] **Step 1: 失敗するテストを書く**

```python
def test_base_and_tag_line():
    p = build_system_prompt("ふたまる", Traits(), [], None, NOW)
    assert p.startswith(
        "あなたは観葉植物のふたまる。持ち主と話し言葉で短く話す。1回の返答は1〜2文。"
    )
    assert (
        "返答の先頭に必ず表情タグを1つ付ける: [neutral] [happy] [sad] [surprised] [love] [sleepy]"
        in p
    )
    assert "覚えていること" not in p and "前回の会話" not in p


@pytest.mark.parametrize(
    "v,text",
    [
        (0.2, "少し素っ気なく接する。"),
        (0.5, "普通に親しく接する。"),
        (0.8, "持ち主に甘えて親しげに話す。"),
    ],
)
def test_affection_levels(v, text):
    assert text in build_system_prompt("ふたまる", Traits(affection=v), [], None, NOW)


def test_talkativeness_changes_length():
    assert "1回の返答は1文。" in build_system_prompt(
        "ふたまる", Traits(talkativeness=0.1), [], None, NOW
    )
    assert "1回の返答は2〜3文で、ときどき自分から話題も振る。" in build_system_prompt(
        "ふたまる", Traits(talkativeness=0.9), [], None, NOW
    )


def test_recency_halves_weekly():
    assert recency_factor(iso(NOW - timedelta(days=7)), NOW) == pytest.approx(0.5)


def test_rank_prefers_recent_over_old_important():
    old = mem(1, "古い", 0.9, NOW - timedelta(days=14))  # 0.225
    new = mem(2, "新しい", 0.5, NOW)  # 0.5
    assert [m.id for m in rank_memories([old, new], NOW, 10)] == [2, 1]


def test_memory_section_top10_and_summary():
    mems = [mem(i, f"記憶{i}", i / 20, NOW) for i in range(12)]
    p = build_system_prompt("ふたまる", Traits(), mems, "テストの話をした", NOW)
    assert "覚えていること:" in p and p.count("\n- ") == 10 and "- 記憶0" not in p
    assert p.endswith("前回の会話: テストの話をした")
```

（`mem(id, content, importance, updated)` はテスト内ヘルパー。）

- [ ] **Step 2: 失敗を確認** → FAIL
- [ ] **Step 3: 実装**（上記の定数と順序）
- [ ] **Step 4: 通過を確認** → PASS
- [ ] **Step 5: コミット** — `git commit -m "feat: system prompt builder"`

---

### Task 5: reflection（`reflection.py`）

**Files:**
- Create: `agent/plant/reflection.py`
- Test: `tests/plant/test_reflection.py`

**Interfaces:**
- Consumes: `PlantStore`, `NewMemory`, `MemoryUpdate`（Task 3）, `apply_deltas`, `TRAIT_NAMES`（Task 2）, `rank_memories`（Task 4）
- Produces:
  - `JsonCompleter = Callable[[list[dict], dict], Awaitable[str]]`（messages, json_schema → 生テキスト）
  - `REFLECTION_SCHEMA: dict`（仕様 §6.3 の JSON 形。4つのキーすべて required、`kind` は enum `preference|event|habit`）
  - `class ReflectionError(Exception)`
  - `@dataclass ReflectionResult(summary: str, new_memories: list[NewMemory], updated_memories: list[MemoryUpdate], trait_deltas: dict[str, object])`
  - `parse_reflection(raw: str) -> ReflectionResult`
  - `async reflect_session(store: PlantStore, plant_id: str, session_id: int, complete_json: JsonCompleter, now: datetime) -> bool`
  - `async recover(store: PlantStore, plant_id: str, complete_json: JsonCompleter, now: datetime) -> None`

挙動:
- `parse_reflection`: 先頭・末尾の ```` ```json ```` フェンスを除去して `json.loads`。トップレベルが dict でない、または4キーのどれかが無い・`summary` が str でない → `ReflectionError`。`new_memories` の要素は `content` が空でない str かつ `kind` が enum 内のものだけ残す。`updated_memories` は `id` が int のものだけ残す。importance は数値でなければ 0.5、0.0〜1.0 にクランプ。
- `reflect_session`: ユーザー発話0件なら `mark_reflected` して True（LLM 呼ばない）。それ以外はメッセージを組み立て（system: 役割と出力形式の説明・重複記憶は updated_memories に入れる指示、user: 発話ログを `持ち主: …` / `ふたまる: …` の行で、既存記憶を `rank_memories(..., limit=30)` で `[id=12] 内容 (importance=0.7)` の行で）、`complete_json(messages, REFLECTION_SCHEMA)` → `parse_reflection`。例外（`ReflectionError` も LLM 側例外も）なら1回だけ再試行、2回とも失敗なら False（ログを残す）。成功時は `apply_deltas(現在値, trait_deltas, count_user_utterances_since(plant_id, now - 7日))` を `apply_reflection` に渡して True。
- `recover`: `repair_unfinished_sessions` → `unreflected_sessions` を順に `reflect_session`。

- [ ] **Step 1: 失敗するテストを書く**（`FakeCompleter(responses)` は呼ばれるたびに次の要素を返し、要素が Exception なら raise、`calls` に `(messages, schema)` を記録）

```python
VALID = json.dumps(
    {
        "summary": "テストの話をした",
        "new_memories": [{"content": "明日数学のテスト", "kind": "event", "importance": 0.8}],
        "updated_memories": [],
        "trait_deltas": {"affection": 0.03},
    },
    ensure_ascii=False,
)


async def test_success_updates_everything(
    store_with_session,
):  # 発話: user「明日テストなんだ」plant「がんばって！」
    store, sid = store_with_session
    fake = FakeCompleter([VALID])
    assert await reflect_session(store, "a", sid, fake, NOW) is True
    assert store.get_plant("a").traits.affection == pytest.approx(0.53)
    assert store.latest_summary("a") == "テストの話をした" and store.get_session(sid).reflected
    msgs, schema = fake.calls[0]
    assert schema is REFLECTION_SCHEMA and "持ち主: 明日テストなんだ" in msgs[-1]["content"]


async def test_retry_once_then_success(store_with_session):
    store, sid = store_with_session
    fake = FakeCompleter(["not json", VALID])
    assert await reflect_session(store, "a", sid, fake, NOW) is True and len(fake.calls) == 2


@pytest.mark.parametrize(
    "first,second",
    [
        ("not json", "{}"),
        (RuntimeError("timeout"), "[]"),
        (json.dumps({"summary": "x", "new_memories": [], "updated_memories": []}), "not json"),
    ],
)
async def test_gives_up_after_two_failures(store_with_session, first, second):
    store, sid = store_with_session
    fake = FakeCompleter([first, second])
    assert await reflect_session(store, "a", sid, fake, NOW) is False
    assert not store.get_session(sid).reflected and len(fake.calls) == 2


async def test_zero_user_turns_skips_llm(
    store,
): ...  # 発話なしセッション → True、fake.calls == []、reflected


def test_parse_cleans_items():
    r = parse_reflection(
        "```json\n"
        + json.dumps(
            {
                "summary": "s",
                "new_memories": [
                    {"content": "a", "kind": "other", "importance": 0.5},
                    {"content": "b", "kind": "habit", "importance": 1.7},
                ],
                "updated_memories": [
                    {"id": "12", "content": "c", "importance": 0.4},
                    {"id": 3, "content": "d", "importance": "x"},
                ],
                "trait_deltas": {},
            }
        )
        + "\n```"
    )
    assert [(m.content, m.importance) for m in r.new_memories] == [("b", 1.0)]
    assert [(u.id, u.importance) for u in r.updated_memories] == [(3, 0.5)]


async def test_prompt_lists_at_most_30_memories(
    store_with_35_memories,
): ...  # fake.calls[0][0][-1]["content"].count("[id=") == 30


async def test_recover_repairs_and_reflects(
    store,
): ...  # ended_at 無しで発話1件のセッション → recover 後 ended_at あり・reflected
```

- [ ] **Step 2: 失敗を確認** → FAIL
- [ ] **Step 3: 実装**
- [ ] **Step 4: 通過を確認** → PASS
- [ ] **Step 5: コミット** — `git commit -m "feat: reflection of memories and traits"`

---

### Task 6: 表情タグの解析（`expression.py`）

**Files:**
- Create: `agent/plant/expression.py`
- Test: `tests/plant/test_expression.py`

**Interfaces:**
- Produces:
  - `LLM_EXPRESSIONS = frozenset({"neutral","happy","sad","surprised","love","sleepy"})`、`ALL_EXPRESSIONS = LLM_EXPRESSIONS | {"listening","thinking"}`
  - `@dataclass ParsedChunk(expression: str | None, text: str)`
  - `class ExpressionParser`: `feed(chunk: str) -> ParsedChunk`、`flush() -> ParsedChunk`。`expression` は表情が確定したその1回だけ値が入り、以降は None。

規則（仕様 §7.1 + Review Focus 1）:
- 先頭の空白は読み飛ばす。最初の非空白文字が `[` でなければ即座に `neutral` を確定し、以降そのまま流す。
- `[` から `]` までを最大16文字（`[` と `]` を含む）バッファ。`]` が来たら中身を `strip().lower()` し、`LLM_EXPRESSIONS` にあればその名前、なければ `neutral`。タグとその直後の空白は捨てる。16文字以内に `]` が無ければ `neutral` でバッファをそのまま流す。
- 表情確定後の本文中に現れる `[<LLM_EXPRESSIONS の名前>]` も除去する（表情は変えない）。名前に一致しない括弧（`[1]` など）は残す。本文中でも `[` からの最大16文字だけバッファする。
- `flush()` はバッファ中の残りを流す。表情未確定なら `neutral` を確定する。

- [ ] **Step 1: 失敗するテストを書く**（ヘルパー `run(chunks) -> (expressions: list[str], text: str)` は全 feed と flush の結果を集める）

```python
@pytest.mark.parametrize(
    "chunks,exprs,text",
    [
        (["[happy]こんにちは"], ["happy"], "こんにちは"),
        (["[ha", "ppy]こん", "にちは"], ["happy"], "こんにちは"),
        (["こんにちは"], ["neutral"], "こんにちは"),
        (["[angry]やだ"], ["neutral"], "やだ"),
        (["[listening]うん"], ["neutral"], "うん"),
        (["[Happy] やった"], ["happy"], "やった"),
        ([" [love] すき"], ["love"], "すき"),
        (["[" + "あ" * 20], ["neutral"], "[" + "あ" * 20),
        (["[hap"], ["neutral"], "[hap"),
        (["[happy]うん！[lo", "ve]だいすき"], ["happy"], "うん！だいすき"),
        (["[happy]答えは[1]番"], ["happy"], "答えは[1]番"),
        ([], ["neutral"], ""),
    ],
)
def test_parse(chunks, exprs, text):
    assert run(chunks) == (exprs, text)


def test_plain_text_is_not_delayed():
    assert ExpressionParser().feed("こんにちは") == ParsedChunk("neutral", "こんにちは")
```

- [ ] **Step 2: 失敗を確認** → FAIL
- [ ] **Step 3: 実装**
- [ ] **Step 4: 通過を確認** → PASS
- [ ] **Step 5: コミット** — `git commit -m "feat: streaming expression tag parser"`

---

### Task 7: ウェイクワードと起床状態（`wake.py`）

**Files:**
- Create: `agent/plant/wake.py`
- Test: `tests/plant/test_wake.py`

**Interfaces:**
- Produces:
  - `normalize(text: str) -> str`
  - `@dataclass WakeMatch(rest: str)`（`rest == ""` はウェイクワードだけ）
  - `detect_wake(text: str, wake_words: Sequence[str]) -> WakeMatch | None`
  - `State = Literal["SLEEPING", "AWAKE"]`、`Tick = Literal["none", "nudge", "sleep"]`
  - `class WakeController(sleep_after: float = 60.0)`: `state: State`、`wake(now) -> None`、`sleep() -> None`、`on_user_activity(now) -> None`、`set_user_speaking(speaking: bool, now) -> None`、`set_plant_busy(busy: bool, now) -> None`、`set_nudge_delay(seconds: float | None) -> None`、`tick(now) -> Tick`

規則:
- `normalize`: 1文字ずつ NFKC → カタカナ（U+30A1–U+30F6）をひらがなへ（-0x60）→ Unicode カテゴリが P・S・Z・C のものを除去。
- `detect_wake`: 元の文字列の各文字を正規化しながら「正規化後の位置 → 元の位置」の対応表を作り、正規化済みのウェイクワード（これも `normalize` する）を `find` する。最初に見つかった一致の末尾に対応する元の位置より後ろを `rest` とし、先頭の空白・句読点（`、。,.!！?？ 　`）を除く。`normalize(rest)` が2文字未満なら `rest = ""`。ウェイクワードより前の文は捨てる。
- `WakeController`: 初期状態 `SLEEPING`。無言時間 = `now - anchor`。`anchor` は `wake`・`on_user_activity`・`set_user_speaking(False)`・`set_plant_busy(False)` の時刻に更新。ユーザー発話中・植物が busy の間は `tick` は `"none"`。`tick` は無言が `sleep_after` 以上なら状態を `SLEEPING` にして `"sleep"` を返す（1回だけ）。そうでなく nudge_delay があり、まだ振っていなくて無言が nudge_delay 以上なら `"nudge"`（`on_user_activity` まで再発しない）。`SLEEPING` 中は常に `"none"`。`wake` で振った済みフラグもリセットする。

- [ ] **Step 1: 失敗するテストを書く**

```python
def test_normalize():
    assert normalize("フタマル、 おはよう！") == "ふたまるおはよう"
    assert normalize("２丸") == "2丸"


@pytest.mark.parametrize(
    "text",
    ["ふたまる", "フタマル", "ふた丸", "二丸", "2丸", "２丸", "ねえ、フタマル！", "ふ た ま る"],
)
def test_detects_variants(text):
    assert detect_wake(text, DEFAULT) is not None


@pytest.mark.parametrize("text", ["おはよう", "ふたりまるい", ""])
def test_no_wake(text):
    assert detect_wake(text, DEFAULT) is None


@pytest.mark.parametrize(
    "text,rest",
    [
        ("ふたまる、今日ね、テストだった", "今日ね、テストだった"),
        ("フタマル。", ""),
        ("ふたまる、ね", ""),
        ("ねえふたまる カレー食べた", "カレー食べた"),
    ],
)
def test_rest(text, rest):
    assert detect_wake(text, DEFAULT).rest == rest


def test_sleeps_after_60s_of_silence():
    c = WakeController()
    assert c.tick(T0) == "none"
    c.wake(T0)
    assert c.tick(T0 + s(59)) == "none" and c.tick(T0 + s(60)) == "sleep"
    assert c.state == "SLEEPING" and c.tick(T0 + s(61)) == "none"


def test_plant_busy_pauses_and_restarts():
    c = WakeController()
    c.wake(T0)
    c.set_plant_busy(True, T0 + s(10))
    assert c.tick(T0 + s(90)) == "none"
    c.set_plant_busy(False, T0 + s(100))
    assert c.tick(T0 + s(159)) == "none" and c.tick(T0 + s(160)) == "sleep"


def test_user_speaking_long_does_not_sleep():
    c = WakeController()
    c.wake(T0)
    c.set_user_speaking(True, T0 + s(5))
    assert c.tick(T0 + s(120)) == "none"
    c.set_user_speaking(False, T0 + s(130))
    assert c.tick(T0 + s(189)) == "none"


def test_nudge_once_per_silence():
    c = WakeController()
    c.wake(T0)
    c.set_nudge_delay(25)
    assert (
        c.tick(T0 + s(24)) == "none"
        and c.tick(T0 + s(25)) == "nudge"
        and c.tick(T0 + s(26)) == "none"
    )
    c.on_user_activity(T0 + s(30))
    assert c.tick(T0 + s(55)) == "nudge"


def test_no_nudge_when_none():
    c = WakeController()
    c.wake(T0)
    c.set_nudge_delay(None)
    assert c.tick(T0 + s(40)) == "none"
```

- [ ] **Step 2: 失敗を確認** → FAIL
- [ ] **Step 3: 実装**
- [ ] **Step 4: 通過を確認** → PASS
- [ ] **Step 5: コミット** — `git commit -m "feat: wake word detection and sleep timer"`

---

### Task 8: 1株ぶんの会話の進行（`conversation.py`）

**Files:**
- Create: `agent/plant/conversation.py`
- Test: `tests/plant/test_conversation.py`

**Interfaces:**
- Consumes: Task 2〜7 のすべて
- Produces:
  - `@dataclass TurnDecision(kind: Literal["ignore", "reply", "greet"], text: str = "", instructions: str | None = None)` — `instructions` は今まさに起きたときだけ、新しいシステムプロンプトが入る。`reply` の `text` は LLM に渡すユーザー発話（ウェイクワード後の残り文を含む）。
  - `class PlantConversation`（キーワード引数）: `store: PlantStore`, `plant_id: str`, `complete_json: JsonCompleter`, `send_expression: Callable[[str], Awaitable[None]]`, `clock: Clock`, `wake_words: Sequence[str]`, `sleep_after: float`
    - `async start() -> None` — 植物を取得/作成、`recover` を実行、`sleepy` を送る
    - `awake: bool`（プロパティ）
    - `async on_user_turn(text: str) -> TurnDecision`
    - `on_plant_utterance(text: str) -> None` — `AWAKE` 中のみ `utterances` に role `plant` で保存
    - `on_user_speaking(speaking: bool) -> None` — タイマーへ反映。`AWAKE` かつ話し始めたら `listening`
    - `on_agent_state(state: str) -> None` — `state in ("thinking", "speaking")` を busy としてタイマーへ反映。`AWAKE` かつ `"thinking"` なら `thinking`
    - `express(name: str) -> None` — `send_expression` をタスクとして起動し、例外はログに残して握りつぶす（会話を止めない）。送った表情は INFO ログに出す
    - `async tick() -> Literal["none", "nudge", "slept"]` — `"sleep"` のときは `_sleep()` まで行って `"slept"`
    - `async close() -> None` — `AWAKE` なら `_sleep()`、その後バックグラウンドの reflection を待つ
    - `async wait_reflections() -> None`

挙動:
- `on_user_turn`: `text.strip()` が空なら `ignore`。`SLEEPING` 中: `detect_wake` が None → `ignore`（何も記録しない）。一致したら起床処理（`start_session`、`WakeController.wake`、`set_nudge_delay(nudge_delay(現在の talkativeness))`、最新の性格・記憶・前回要約で `build_system_prompt`）を行い、`rest` があれば記録して `reply(rest, instructions)`、無ければ `greet(instructions)`。`AWAKE` 中: 記録し `on_user_activity`、`reply(text)`。
- `_sleep()`: `sleepy` を送る → `end_session` → `reflect_session` をバックグラウンドタスクで起動（参照を保持）→ `SLEEPING`。reflection を待たずに次の起床を受け付ける。

- [ ] **Step 1: 失敗するテストを書く**（`FakeClock`（`now` を進められる）、Task 5 の `FakeCompleter`、`sent: list[str]` フィクスチャに記録する `send_expression`、`conv` フィクスチャは `start()` 済み）

```python
async def test_start_creates_plant_recovers_and_sleepy(
    tmp_path,
): ...  # 事前に ended_at 無し・発話1件のセッションを入れる → start() 後 reflected、sent == ["sleepy"]


async def test_sleeping_ignores_non_wake(conv, store):
    assert (await conv.on_user_turn("おはよう")).kind == "ignore"
    assert not conv.awake and store.unreflected_sessions("a") == [] and no_sessions(store)


async def test_wake_only_greets(conv):
    d = await conv.on_user_turn("フタマル！")
    assert d.kind == "greet" and "あなたは観葉植物のふたまる" in d.instructions and conv.awake


async def test_wake_with_rest_replies_and_records(conv, store):
    d = await conv.on_user_turn("ふたまる、今日カレー食べた")
    assert (d.kind, d.text) == ("reply", "今日カレー食べた") and d.instructions
    assert [u.text for u in store.get_utterances(current_sid(store))] == ["今日カレー食べた"]


async def test_awake_reply_has_no_instructions_and_blank_is_ignored(conv, store):
    await conv.on_user_turn("ふたまる")
    d = await conv.on_user_turn("ただいま")
    assert (d.kind, d.text, d.instructions) == ("reply", "ただいま", None)
    assert (await conv.on_user_turn("  ")).kind == "ignore"
    assert [u.text for u in store.get_utterances(current_sid(store))] == ["ただいま"]


async def test_plant_utterance_only_recorded_when_awake(conv, store): ...


async def test_silence_sleeps_and_reflects(conv, store, clock):
    await conv.on_user_turn("ふたまる、ただいま")
    conv.on_plant_utterance("おかえり！")
    clock.advance(60)
    assert await conv.tick() == "slept"
    await conv.wait_reflections()
    s = store.get_session(current_sid(store))
    assert s.ended_at and s.reflected and sent[-1] == "sleepy" and not conv.awake


async def test_next_wake_uses_updated_summary(
    conv, clock
): ...  # 1回目のセッション → slept → wait_reflections → 再度「ふたまる」→ instructions に "前回の会話: テストの話をした"


async def test_wake_while_reflection_pending(
    conv, clock
): ...  # reflection 用 completer を asyncio.Event で止めたまま再度「ふたまる」→ greet が返る（古い性格で組み立て、例外なし）


async def test_nudge_from_tick(conv, clock):
    await conv.on_user_turn("ふたまる")
    clock.advance(25)
    assert await conv.tick() == "nudge"  # talkativeness 0.5 → 25秒


async def test_listening_and_thinking_only_when_awake(conv):
    conv.on_user_speaking(True)
    conv.on_agent_state("thinking")
    await settle()
    assert sent == ["sleepy"]
    await conv.on_user_turn("ふたまる")
    conv.on_user_speaking(True)
    conv.on_agent_state("thinking")
    await settle()
    assert sent[-2:] == ["listening", "thinking"]


async def test_close_while_awake_ends_and_reflects(conv, store): ...


async def test_failing_send_does_not_raise(
    tmp_path,
): ...  # send_expression が RuntimeError を投げても start() / express() は例外を出さない
```

（`settle()` は `await asyncio.sleep(0)` を数回。）

- [ ] **Step 2: 失敗を確認** → FAIL
- [ ] **Step 3: 実装**
- [ ] **Step 4: 通過を確認** — `uv run pytest -v`（全体）→ PASS
- [ ] **Step 5: コミット** — `git commit -m "feat: per-plant conversation flow"`

---

### Task 9: モデル API プラグイン（`plugins/`）

**Files:**
- Create: `agent/plugins/whisper_stt.py`, `agent/plugins/openai_tts.py`, `agent/plugins/llm.py`
- Test: `tests/plugins/test_plugins.py`

**Interfaces:**
- Consumes: `Config`（Task 1）, `JsonCompleter`（Task 5）
- Produces:
  - `create_stt(cfg: Config) -> openai.STT` — `livekit.plugins.openai.STT(model=cfg.stt_model, language="ja", prompt="ふたまる", use_realtime=False, client=AsyncOpenAI(base_url=cfg.stt_base_url, api_key=cfg.stt_api_key, timeout=cfg.stt_timeout))`
  - `create_tts(cfg: Config) -> openai.TTS` — `openai.TTS(model=cfg.tts_model, voice=cfg.tts_voice, instructions=cfg.tts_instructions, response_format="pcm", client=AsyncOpenAI(base_url=cfg.tts_base_url or None, api_key=cfg.tts_api_key, timeout=cfg.tts_timeout))`
  - `create_llm(cfg: Config) -> openai.LLM` — `openai.LLM(model=cfg.llm_model, client=AsyncOpenAI(base_url=cfg.llm_base_url, api_key=cfg.llm_api_key, timeout=cfg.llm_timeout))`
  - `create_json_completer(cfg: Config) -> JsonCompleter` — `chat.completions.create(model=cfg.llm_model, messages=..., response_format={"type": "json_schema", "json_schema": {"name": "reflection", "schema": schema}}, temperature=0.3)` の `choices[0].message.content` を返す。`enable_thinking` は渡さない（仕様 §8.1）

`client=` を渡すと各プラグインの `base_url` 引数は不要。`client` の受け取りはインストールされた版の `livekit/plugins/openai/{stt,tts,llm}.py` の `__init__` で確認すること（2026-11 時点の main では3つとも `client` 引数あり）。

- [ ] **Step 1: 失敗するテストを書く**（respx で httpx をモック。仕様 §11「プラグイン: リクエスト形式を確認」）

```python
async def test_stt_request(respx_mock, cfg):
    route = respx_mock.post("http://gpu:8001/v1/audio/transcriptions").respond(
        json={"text": "ふたまる"}
    )
    frame = rtc.AudioFrame(
        b"\0\0" * 1600, sample_rate=16000, num_channels=1, samples_per_channel=1600
    )
    ev = await create_stt(cfg).recognize(frame)
    body = route.calls.last.request.content
    assert b'name="language"\r\n\r\nja' in body and "ふたまる".encode() in body
    assert (
        b"kotoba-tech/kotoba-whisper-v2.0-faster" in body and ev.alternatives[0].text == "ふたまる"
    )


async def test_tts_request(respx_mock, cfg):
    route = respx_mock.post("https://api.openai.com/v1/audio/speech").respond(
        content=b"\0\0" * 2400
    )
    async with create_tts(cfg).synthesize("こんにちは") as stream:
        async for _ in stream:
            pass
    req = json.loads(route.calls.last.request.content)
    assert req["model"] == "gpt-4o-mini-tts" and req["voice"] == "coral"
    assert (
        req["response_format"] == "pcm"
        and req["instructions"] == TTS_INSTRUCTIONS
        and req["input"] == "こんにちは"
    )


async def test_json_completer_request(respx_mock, cfg):
    route = respx_mock.post("http://gpu:8000/v1/chat/completions").respond(
        json=chat_completion('{"ok": true}')
    )
    out = await create_json_completer(cfg)([{"role": "user", "content": "x"}], {"type": "object"})
    req = json.loads(route.calls.last.request.content)
    assert out == '{"ok": true}' and req["response_format"]["type"] == "json_schema"
    assert req["model"] == "google/gemma-4-E4B-it" and "enable_thinking" not in json.dumps(req)
```

（`chat_completion(content)` は OpenAI の ChatCompletion 形の dict を返すテスト内ヘルパー。）

- [ ] **Step 2: 失敗を確認** → FAIL
- [ ] **Step 3: 実装**
- [ ] **Step 4: 通過を確認** → PASS（`recognize` / `synthesize` の呼び出し方が版で違えば、テスト側を installed 版の API に合わせる。検証するアサーションは変えない）
- [ ] **Step 5: コミット** — `git commit -m "feat: STT/TTS/LLM plugin factories"`

---

### Task 10: LiveKit 配線（`main.py`）と起動手順

**Files:**
- Create: `agent/main.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: `load_config`（Task 1）, `PlantStore`（Task 3）, `ExpressionParser`（Task 6）, `PlantConversation`, `TurnDecision`（Task 8）, `create_*`（Task 9）, `WAKE_GREETING_INSTRUCTION`, `NUDGE_INSTRUCTION`, `FALLBACK_REPLY`（Task 4）
- Produces: `uv run python -m agent.main console|dev|start`

この タスクは単体テストを持たない（仕様 §11 の通り console モードで手動確認）。組み立て:

- 起動: `load_dotenv()`、`server = AgentServer()`、`server.setup_fnc` で `proc.userdata["vad"] = silero.VAD.load()`、`@server.rtc_session()` の `entrypoint(ctx: JobContext)`、`cli.run_app(server)`。`agent_name` は指定しない（自動ディスパッチ）。
- ルーム判定: `ctx.job.room.name` が `plant-` で始まれば残りを `plant_id`。始まらない場合、`cfg.dev_plant_id` があればそれを使い（console モード用）、無ければ何もせず return（即退出）。
- `PlantConversation` の `send_expression`: `ctx.room.local_participant.perform_rpc(destination_identity=f"device-{plant_id}", method="set_expression", payload=json.dumps({"expression": name}))`。
- `AgentSession(stt=create_stt, llm=create_llm, tts=create_tts, vad=prewarm の VAD, turn_detection="vad", user_away_timeout=None, preemptive_generation=False, conn_options=...)`。`conn_options` で LLM の `max_retry=0`（タイムアウトは client 側の `LLM_TIMEOUT`）。`SessionConnectOptions` / `APIConnectOptions` の import 元はインストール版で確認する。
- `class FutamaruAgent(Agent)`（`instructions=""` で開始）:
  - `on_user_turn_completed(turn_ctx, new_message)`: `d = await conv.on_user_turn(new_message.text_content or "")`。`d.instructions` があれば `await self.update_instructions(d.instructions)` し、`turn_ctx.items[:] = self.chat_ctx.copy().items`（今回の返答も新しいプロンプトで作るため）。`ignore` → `raise StopResponse()`（LiveKit は StopResponse 時にユーザー発話を履歴に入れない）。`greet` → `self.session.generate_reply(instructions=WAKE_GREETING_INSTRUCTION)` を呼んでから `raise StopResponse()`。`reply` で `d.text != new_message.text_content` なら `new_message.content = [d.text]`。
  - `llm_node`: `Agent.default.llm_node` の出力の `chunk.delta.content`（または str）を `ExpressionParser` に通し、確定した表情を `conv.express`、タグ除去後の文字列を yield。終了時に `flush`。例外時は、まだ1文字も yield していなければ `FALLBACK_REPLY` を yield してログ。
- イベント: `user_state_changed` → `conv.on_user_speaking(ev.new_state == "speaking")`、`agent_state_changed` → `conv.on_agent_state(ev.new_state)`、`conversation_item_added` で `ev.item.role == "assistant"` → `conv.on_plant_utterance(ev.item.text_content)`、`error` で `ev.source` が TTS → `conv.express("sleepy")`。
- ティッカー: 1秒ごとに `await conv.tick()`。`"nudge"` → `session.generate_reply(instructions=NUDGE_INSTRUCTION)`、`"slept"` → `await agent.update_chat_ctx(ChatContext.empty())`（眠ったら会話履歴を捨てる）。
- 参加者: `participant_connected` で identity が `device-<plant_id>` なら `conv.express("sleepy")`、`participant_disconnected` で同 identity なら `conv.close()` をタスク起動。`ctx.add_shutdown_callback(conv.close)`。
- `await conv.start()` → `await session.start(agent=agent, room=ctx.room)` → ティッカー開始。

- [ ] **Step 1: `agent/main.py` を実装**
- [ ] **Step 2: 起動確認** — `.env` に LiveKit・LLM・STT・OpenAI の値と `DEV_PLANT_ID=dev` を入れて `uv run python -m agent.main console`。期待: エラーなく起動し、ログに `expression=sleepy`。
- [ ] **Step 3: 仕様 §11 の結合確認（手動）**
  - 「おはよう」→ 応答なし。「ふたまる」→ 植物から話しかける。「ふたまる、今日カレー食べた」→ その内容に応答する
  - 応答のたびに表情ログ（listening → thinking → タグの表情）が出て、音声にタグが読まれない
  - 60秒黙ると `sleepy` で眠り、`sqlite3 data/futamaru.db "select summary, reflected from sessions"` で summary と `1`、`select * from trait_history` に行が増える
  - talkativeness 0.5 のとき 25秒黙ると1回だけ話題を振る
  - OpenAI TTS の初回音声までの遅延をログのメトリクスで確認（目安 1 秒以内、仕様 §12）
- [ ] **Step 4: `README.md` を更新** — `uv sync`、`.env.example` のコピー、`console` / `dev` での起動、`scripts/issue_token.py`、`scripts/simulate.py` の使い方を簡潔に
- [ ] **Step 5: 全テスト** — `uv run pytest` → PASS
- [ ] **Step 6: コミット** — `git commit -m "feat: wire plant conversation into LiveKit agent"`

---

### Task 11: ESP32 用トークン発行（`scripts/issue_token.py`）

**Files:**
- Create: `scripts/issue_token.py`
- Test: `tests/scripts/test_issue_token.py`

**Interfaces:**
- Produces: `build_token(plant_id: str, api_key: str, api_secret: str, ttl_days: int = 30) -> str`。CLI: `uv run python -m scripts.issue_token --plant-id <id>`（`LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` を env / `.env` から読み、トークンを標準出力に1行）

- [ ] **Step 1: 失敗するテストを書く**

```python
def test_token_claims():
    tok = build_token("a1", "key", "secret" * 6)
    claims = api.TokenVerifier("key", "secret" * 6).verify(tok)
    assert claims.identity == "device-a1"
    assert claims.video.room == "plant-a1" and claims.video.room_join
    assert claims.video.can_publish and claims.video.can_subscribe
    exp = jwt_payload(tok)["exp"]
    assert 29.9 * 86400 < exp - time.time() <= 30 * 86400
```

（`jwt_payload` は base64 で payload を読むテスト内ヘルパー。）

- [ ] **Step 2: 失敗を確認** → FAIL
- [ ] **Step 3: 実装** — `api.AccessToken(key, secret).with_identity(...).with_grants(api.VideoGrants(room_join=True, room=..., can_publish=True, can_subscribe=True)).with_ttl(timedelta(days=ttl_days)).to_jwt()`
- [ ] **Step 4: 通過を確認** → PASS
- [ ] **Step 5: コミット** — `git commit -m "feat: ESP32 token issuing script"`

---

### Task 12: 成長の早送りデモ（`scripts/simulate.py`）

**Files:**
- Create: `scripts/simulate.py`, `scripts/samples/plant-a.json`, `scripts/samples/plant-b.json`
- Test: `tests/scripts/test_simulate.py`

**Interfaces:**
- Consumes: `PlantStore`（Task 3）, `reflect_session`（Task 5）, `build_system_prompt`, `rank_memories`（Task 4）, `create_json_completer`（Task 9）。`--ask` の返答は `AsyncOpenAI(base_url=cfg.llm_base_url)` の `chat.completions.create` を1回呼ぶ
- Produces:
  - 入力 JSON: `{"plant_id": str, "sessions": [{"started_at": ISO, "turns": [{"role": "user"|"plant", "text": str}]}]}`
  - `async simulate(store: PlantStore, log: dict, complete_json: JsonCompleter) -> list[TraitSnapshot]` — 各セッションを `started_at` を基準にした時刻（発話ごとに +10秒）で `start_session` / `add_utterance` / `end_session` し、`now = 最後の発話時刻` で `reflect_session`。最後に `trait_history` を返す
  - CLI: `uv run python -m scripts.simulate scripts/samples/plant-a.json scripts/samples/plant-b.json [--db data/sim.db] [--ask "ただいま"]` — 株ごとに性格値の推移表、最終システムプロンプト、`--ask` があればその発話への返答を出力

サンプル: plant-a は褒め言葉・優しい言葉・自分の話が多い毎日の会話（7日分、1日1セッション、各5往復以上）、plant-b は短い用件だけの会話（同じ7日分、各1〜2往復）。同じ初期状態から affection・talkativeness・curiosity に差が出ることを狙う（仕様 §1 成功基準5）。

- [ ] **Step 1: 失敗するテストを書く**

```python
async def test_simulate_records_and_reflects(tmp_path):
    log = {
        "plant_id": "s",
        "sessions": [
            {
                "started_at": "2026-11-01T19:00:00+09:00",
                "turns": [
                    {"role": "user", "text": "ただいま"},
                    {"role": "plant", "text": "おかえり"},
                ],
            },
            {
                "started_at": "2026-11-02T19:00:00+09:00",
                "turns": [{"role": "user", "text": "かわいいね"}],
            },
        ],
    }
    store = PlantStore(str(tmp_path / "s.db"))
    hist = await simulate(store, log, FakeCompleter([VALID_AFFECTION_UP, VALID_AFFECTION_UP]))
    assert len(hist) == 3 and hist[-1].traits.affection > hist[0].traits.affection
    assert hist[1].recorded_at.startswith("2026-11-01T19:00")


def test_samples_are_valid():
    for p in ["scripts/samples/plant-a.json", "scripts/samples/plant-b.json"]:
        log = json.loads(Path(p).read_text(encoding="utf-8"))
        assert len(log["sessions"]) == 7 and all(
            t["role"] in ("user", "plant") for s in log["sessions"] for t in s["turns"]
        )
```

- [ ] **Step 2: 失敗を確認** → FAIL
- [ ] **Step 3: 実装とサンプル作成**
- [ ] **Step 4: 通過を確認** → `uv run pytest` 全体 PASS
- [ ] **Step 5: 実 LLM で確認（任意・GPU サーバーがある場合）** — `uv run python -m scripts.simulate scripts/samples/plant-a.json scripts/samples/plant-b.json --ask "ただいま"`。期待: 2株の affection に差が出て、返答の口調が違う
- [ ] **Step 6: コミット** — `git commit -m "feat: growth simulation demo script"`
