# 話し相手の見分けと方言 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ふたまるが名乗りから話し相手を見分け、その人ごとの性格・記憶・前回の会話・方言（標準語・関西弁・博多弁・広島弁）で話すようにする。

**Architecture:** DB に「人」（`people`）を足し、性格・記憶・セッションを人に結び付ける。相手が未確定の間、ユーザーの発話ごとに LLM の構造化出力で名前を探し、決まったらシステムプロンプトと TTS の指示を作り直す。方言は振り返りでセッションごとに判定し、人ごとに直近 5 回の多数決で決める（手で固定も可）。

**Tech Stack:** Python 3.12、SQLite、LiveKit Agents、OpenAI 互換 API（JSON スキーマの構造化出力）、pytest

**Spec:** `docs/superpowers/specs/2026-11-17-people-and-dialect-design.md`

## Global Constraints

- 方言の値は `standard` / `kansai` / `hakata` / `hiroshima`。判定できないときは `unknown`（保存はするが多数決には数えない）
- 人の方言 = 手で固定（`people.dialect_override`）があればそれ。無ければその人との直近 5 回の判定（`unknown` を除く）の多数決、同数なら新しいほう。判定が無ければ `standard`
- 名前を聞くのはセッションの最初の 3 回のユーザー発話まで。3 回で決まらなければお客さん。お客さんに決まったら名前探しもやめる
- ある人の記憶は、ほかの人との会話・振り返りで見せない。振り返りで書き換えられるのは見せた記憶だけ。共通の記憶（`person_id` NULL）は書き換えても共通のまま。お客さんの新しい記憶は共通
- 性格は人ごと（新しい人は全部 0.5 から）。お客さんとこの機能より前のデータは株の性格（`plants` の 4 軸）
- 前回の会話: 人ならその人との直近の要約。お客さんならお客さんのセッション（`person_id` NULL）の直近の要約だけ
- 今ある DB（`data/futamaru.db`）は起動時に列・テーブルを足すだけで、データを消さない
- 名前探しは返事の前に行い、`IDENTIFY_TIMEOUT = 3.0` 秒で打ち切って「名乗っていない」扱いにする
- `agent/plant/` は LiveKit に依存させない。コメントは初学者向けに多めに（AGENTS.md）。仕様と違う動きにするので、先に仕様書を直す
- 作業の最後に `uvx ruff format . && uvx ruff check . && uv run pytest`

## Review Focus

- 今ある DB（この機能より前に作った `data/futamaru.db`）で起動する → 列が足され、記憶・会話・性格がそのまま残る（Task 2 のテスト）
- 名前探しの LLM が遅い・落ちている → 3 秒で諦めて普通に返事する。会話は止まらない（Task 5 のテスト）
- LLM が一覧に無い人の id や空の名前を返す → 「名乗っていない」扱い（Task 4 のテスト）
- 振り返りの LLM がほかの人の記憶の id を返す → 書き換えない（Task 3 のテスト）
- 手で登録した「たろう」に、STT が「タロウ」と書いた名前でコマンドを打つ → 同じ人として見つかる（Task 7 のテスト）

## 着手前

PR #32・#33（コメント追加）と同じファイルを大きく触るので、**それらのマージ後に `feature/dialect` を main に追従させてから** Task 1 を始める（`git rebase origin/main`）。

---

### Task 1: 仕様書に節を足す

**Files:**
- Modify: `docs/superpowers/specs/2026-11-13-futamaru-agent-design.md`（§6 に「6.5 話し相手と方言」を追加、§6.2 のスキーマに追加の列・テーブル、§6.3 の振り返りの JSON に `owner_dialect`、§6.4 のプロンプトに方言・相手の名前・名前を聞く指示）

- [ ] **Step 1: 設計書の「決めたこと」を、仕様書の該当節に書き写す**（性格・記憶・前回の会話・方言が人ごと、お客さん、3 回、5 回の多数決、DB の引き継ぎ）
- [ ] **Step 2: Commit**: `docs: add speakers and dialects to the agent spec`

### Task 2: 方言の決め方と DB

**Files:**
- Create: `agent/plant/dialect.py`, `tests/plant/test_dialect.py`
- Modify: `agent/plant/store.py`, `tests/plant/test_store.py`

**Interfaces:**
- Produces（`dialect.py`）:
  - `DIALECTS = ("standard", "kansai", "hakata", "hiroshima")`、`UNKNOWN = "unknown"`
  - `resolve_dialect(override: str | None, recent: list[str]) -> str` — `recent` は新しい順
  - `dialect_prompt(dialect: str) -> str | None`（標準語なら None）、`dialect_tts(dialect: str) -> str | None`（標準語なら None）
- Produces（`store.py`）:
  - `@dataclass Person(id: int, plant_id: str, name: str, traits: Traits, dialect_override: str | None)`
  - `Session` に `person_id: int | None`、`owner_dialect: str | None` を追加
  - `add_person(plant_id: str, name: str, now: datetime) -> Person`、`get_person(person_id: int) -> Person`、`get_people(plant_id: str) -> list[Person]`、`set_dialect_override(person_id: int, dialect: str | None) -> None`
  - `set_session_person(session_id: int, person_id: int) -> None`
  - `recent_dialects(person_id: int, limit: int = 5) -> list[str]` — その人のセッションの `owner_dialect`（NULL と `unknown` を除く）を新しい順に
  - 既存メソッドに `person_id: int | None = None` を追加（None はお客さん・共通）: `get_memories(plant_id, person_id=None)`（共通＋その人の記憶）、`latest_summary(plant_id, person_id=None)`（`person_id` が一致するセッションだけ。None なら `IS NULL`）、`count_user_utterances_since(plant_id, since, person_id=None)`（同じく一致するセッションだけ）、`trait_history(plant_id, person_id=None)`
  - `apply_reflection(..., now, *, person_id: int | None = None, owner_dialect: str = "unknown")` — 性格は `person_id` があれば `people`、無ければ `plants` に書く。新しい記憶は `person_id` 付き。更新は `person_id` が一致するか NULL の記憶だけ。`trait_history` に `person_id`。`sessions.owner_dialect` を保存

- [ ] **Step 1: `tests/plant/test_dialect.py` を書く**
  - `test_override_wins`: `resolve_dialect("hakata", ["kansai"]*5) == "hakata"`
  - `test_majority_of_recent_five`: `resolve_dialect(None, ["kansai","kansai","hakata","standard","kansai","hakata","hakata"]) == "kansai"`（6 件目以降は数えない）
  - `test_tie_prefers_newest`: `resolve_dialect(None, ["hakata","kansai"]) == "hakata"`
  - `test_no_judgement_is_standard`: `resolve_dialect(None, []) == "standard"`
  - `test_standard_adds_nothing`: `dialect_prompt("standard") is None and dialect_tts("standard") is None`、`"関西弁" in dialect_prompt("kansai")`
- [ ] **Step 2: `tests/plant/test_store.py` に足す**
  - `test_migrates_an_old_database`: この機能より前のスキーマ（今の `SCHEMA` をテスト内に文字列で持つ）で一時ファイルの DB を作り、記憶 1 件・セッション 1 件・性格を入れてから `PlantStore` で開き直す → 記憶・要約・性格が同じ、`get_memories("a1")` に記憶が残り `person_id` 相当が共通
  - `test_people_have_their_own_traits_and_memories`: `add_person` で 2 人、たろうの振り返り（`apply_reflection(..., person_id=taro.id)`）で `affection` を上げる → `get_person(taro.id).traits.affection > 0.5`、`get_person(hanako.id).traits == Traits()`、`get_plant("a1").traits == Traits()`
  - `test_memories_are_private_per_person`: たろうの記憶・はなこの記憶・共通の記憶 → `get_memories("a1", taro.id)` はたろう＋共通、`get_memories("a1")` は共通だけ
  - `test_reflection_cannot_update_another_persons_memory`: はなこの記憶の id を、たろうの振り返りの更新に渡す → はなこの記憶は変わらない。共通の記憶の更新は反映され、`person_id` は NULL のまま
  - `test_latest_summary_is_per_person_and_guest`: たろう・お客さんのセッションに要約 → それぞれ自分の分だけ返る
  - `test_recent_dialects_newest_first_without_unknown`
- [ ] **Step 3: 実行して失敗を確認**: `uv run pytest tests/plant/test_dialect.py tests/plant/test_store.py -q` → `ImportError` と新しいテストの FAIL
- [ ] **Step 4: 実装**: `dialect.py`（多数決は `recent[:5]` を数え、最多が複数なら `recent` で先に出てくるもの）。`store.py` は `SCHEMA` に `people` を足し、`__init__` で `_migrate()` を呼ぶ（`PRAGMA table_info` で列の有無を見て `ALTER TABLE ... ADD COLUMN`）
- [ ] **Step 5: 実行して成功を確認**: 同じコマンドと `uv run pytest -q` が全部 PASS
- [ ] **Step 6: Commit**: `feat: store people, per-person traits/memories, and dialect votes`

### Task 3: 振り返りを人ごとにする

**Files:**
- Modify: `agent/plant/reflection.py`, `tests/plant/test_reflection.py`, `tests/fakes.py`

**Interfaces:**
- Consumes: Task 2 の `store` の `person_id` 付きメソッド、`Session.person_id`
- Produces: `ReflectionResult.owner_dialect: str`（`DIALECTS` か `UNKNOWN`）。`REFLECTION_SCHEMA` に `owner_dialect`（enum: 4 方言＋`unknown`）を必須で追加。`reflection_json(..., dialect="unknown")`

- [ ] **Step 1: テストを書く**
  - `test_owner_dialect_is_parsed`、`test_missing_or_invalid_dialect_is_unknown`（キーが無い・`"osaka"` → `"unknown"`。エラーにはしない）
  - `test_reflection_shows_only_this_persons_and_shared_memories`: たろうのセッションの振り返りで LLM に渡すメッセージに、はなこの記憶の内容が含まれない
  - `test_person_session_updates_person_traits_and_dialect`: たろうのセッションを振り返る → たろうの性格が動き、株の性格は変わらず、`recent_dialects(taro.id) == ["kansai"]`
  - `test_guest_session_updates_plant_traits`: `person_id` 無しのセッション → 株の性格が動く（今の動き）
- [ ] **Step 2: 失敗を確認**: `uv run pytest tests/plant/test_reflection.py -q`
- [ ] **Step 3: 実装**: `reflect_session` で `session.person_id` を読み、記憶・性格の元・話しかけ回数・保存先をその人（なければ株）にする。指示文（`SYSTEM_MESSAGE`）に方言の判定を足す
- [ ] **Step 4: 成功を確認**: `uv run pytest -q`
- [ ] **Step 5: Commit**: `feat: reflect per person and judge the owner's dialect`

### Task 4: 名前探しとプロンプト

**Files:**
- Create: `agent/plant/people.py`, `tests/plant/test_people.py`
- Modify: `agent/plant/prompt.py`, `tests/plant/test_prompt.py`

**Interfaces:**
- Consumes: `store.Person`、`reflection.JsonCompleter`、`dialect.dialect_prompt`
- Produces:
  - `@dataclass Identification(kind: Literal["known", "new", "none"], person_id: int | None = None, name: str | None = None)`
  - `async identify(complete_json: JsonCompleter, utterance: str, known: list[Person]) -> Identification` — 一覧（id と名前）と発話を渡し、`{"kind": ..., "person_id": int|null, "name": str|null}` を JSON スキーマで返させる
  - `build_system_prompt(name, traits, memories, last_summary, now, *, dialect: str = "standard", person_name: str | None = None, ask_name: bool = False) -> str`

- [ ] **Step 1: テストを書く**
  - `test_identifies_a_known_person`: 一覧に `たろう(id=1)`、LLM が `{"kind":"known","person_id":1}` → `Identification("known", 1)`
  - `test_new_person_name_is_stripped`: `{"kind":"new","name":" はなこ "}` → `("new", None, "はなこ")`
  - `test_unknown_id_or_empty_name_is_none`: 一覧に無い id 9、空の名前、壊れた JSON → すべて `kind == "none"`
  - `test_prompt_mentions_dialect_and_person`: `dialect="kansai", person_name="たろう"` → 関西弁の指示と「たろう」を含む。`ask_name=True` → 名前を聞く指示を含む。既定値 → 今と同じ文字列（既存のテストがそのまま通る）
- [ ] **Step 2: 失敗を確認**: `uv run pytest tests/plant/test_people.py tests/plant/test_prompt.py -q`
- [ ] **Step 3: 実装**: 不正な出力はすべて `none`（例外を外に出さない）
- [ ] **Step 4: 成功を確認**: `uv run pytest -q`
- [ ] **Step 5: Commit**: `feat: identify the speaker by name and add dialect to the prompt`

### Task 5: 会話の流れ

**Files:**
- Modify: `agent/plant/conversation.py`, `tests/plant/test_conversation.py`

**Interfaces:**
- Consumes: Task 2〜4 のすべて
- Produces:
  - `TurnDecision` に `dialect: str | None = None`（相手が決まった・お客さんに決まったときだけ。main が TTS の指示を替えるのに使う）
  - 定数 `MAX_NAME_TURNS = 3`、`IDENTIFY_TIMEOUT = 3.0`
  - 起きたとき（`_wake`）のプロンプトは株の性格・共通の記憶・お客さんの要約・`ask_name=True`
  - 相手が未確定・お客さんでない間、記録したユーザー発話ごとに `identify` を `asyncio.wait_for(..., IDENTIFY_TIMEOUT)` で呼ぶ。`known`/`new` なら人を決め（`new` は `add_person`）、`set_session_person`、話題を振る秒数をその人のおしゃべり度で設定し、その人のプロンプト（性格・記憶・要約・方言・名前）を `instructions` に、方言を `dialect` に入れて返す。3 回目でも決まらなければお客さんにし、`ask_name=False` のプロンプトと `dialect="standard"` を返す
  - 眠ったら相手・回数・お客さんの印を消す

- [ ] **Step 1: テストを書く**（`FakeCompleter` に名前探しと振り返りの応答を順に積む）
  - `test_wake_prompt_asks_for_a_name`
  - `test_named_wake_switches_to_that_persons_prompt`: 登録済みのたろう（方言を `kansai` に固定）→「ふたまる、たろうだよ」→ `decision.instructions` に関西弁の指示、`decision.dialect == "kansai"`、セッションの `person_id` がたろう
  - `test_new_person_is_registered`
  - `test_becomes_guest_after_three_unnamed_turns_and_stops_identifying`: 3 回 `none` → 3 回目で `ask_name` の無いプロンプト、4 回目は名前探しを呼ばない（`FakeCompleter.calls` の数で確認）
  - `test_slow_identification_does_not_block_reply`: 名前探しを `gate` で止めたまま → `IDENTIFY_TIMEOUT` を `monkeypatch` で 0.05 にして、`reply` が返る
  - `test_sleep_forgets_the_person`: 眠って起き直すと、また名前を聞くプロンプト
- [ ] **Step 2: 失敗を確認**: `uv run pytest tests/plant/test_conversation.py -q`
- [ ] **Step 3: 実装**
- [ ] **Step 4: 成功を確認**: `uv run pytest -q`（既存の会話テストは、名前探しの応答を積むよう必要な分だけ直す）
- [ ] **Step 5: Commit**: `feat: tell speakers apart within a session`

### Task 6: LiveKit への配線（TTS の方言）

**Files:**
- Modify: `agent/main.py`, `tests/test_main.py`

**Interfaces:**
- Consumes: `TurnDecision.dialect`、`dialect.dialect_tts`、`Config.tts_instructions`
- Produces: `FutamaruAgent(conv, tts_instructions: str = "")`。`on_user_turn_completed` で `decision.dialect` があれば `self.session.tts.update_options(instructions=tts_instructions + 方言の指示)`

- [ ] **Step 1: テストを書く**: `test_dialect_updates_tts_instructions`（偽の `session.tts` に `update_options` を記録させ、`dialect="hakata"` の決定で博多弁の指示が付く。`"standard"` なら基本の指示だけ）
- [ ] **Step 2: 失敗を確認** → **Step 3: 実装** → **Step 4: `uv run pytest -q` が PASS**
- [ ] **Step 5: Commit**: `feat: speak with the person's dialect in TTS`

### Task 7: 手で操作するコマンドと文書

**Files:**
- Create: `scripts/people.py`, `tests/scripts/test_people_cli.py`
- Modify: `README.md`, `docs/deploy.md`

**Interfaces:**
- Consumes: Task 2 の `store`
- Produces: `main(argv: list[str], store: PlantStore, now: datetime) -> int`（テストで直接呼ぶ）。引数は `--plant-id`（必須）、`--person NAME` か `--person-id N`、`--dialect {standard,kansai,hakata,hiroshima}` か `--auto`、`--db`（既定は `FUTAMARU_DB_PATH`）
- `--person` の一致は `wake.normalize` でそろえて比べる

- [ ] **Step 1: テストを書く**
  - `test_pin_dialect_registers_the_person`: `--person たろう --dialect kansai` → 人が登録され `dialect_override == "kansai"`
  - `test_katakana_finds_the_same_person`: `たろう` を登録後に `--person タロウ --auto` → 新しい人を作らず、固定が外れる
  - `test_list_shows_traits_and_dialect`: 一覧の出力に名前・4 軸・方言（固定か自動か）が出る（`capsys`）
- [ ] **Step 2: 失敗を確認** → **Step 3: 実装** → **Step 4: PASS**
- [ ] **Step 5: README と `docs/deploy.md` に使い方を足す**（デモでは事前に人を登録・方言を固定する／`SLEEP_AFTER_SECONDS` を短くして人を替える）
- [ ] **Step 6: 全体の確認**: `uvx ruff format . && uvx ruff check . && uv run pytest`
- [ ] **Step 7: Commit**: `feat: add a command to list people and pin dialects`
