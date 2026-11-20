# ふたまる診断の結果画面 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ダッシュボードを、育てた人ごとの「ふたまる診断の結果画面」＋「こんなふうに育てられた」にし（PR 1）、話しかけた時間帯を energy に反映する（PR 2）。

**Architecture:** PR 1 は読むだけ。`store.py` に集計用の読み取りメソッド、`timeutil.py` に時間帯の関数、`plant_type.py` に長い説明文を足し、`dashboard.py` が `/api/state` の各ビューに `growth` を足す。`dashboard.html` はその JSON で結果画面を描く。PR 2 は `reflection._care_lines` に時間帯の 1 行を足す。

**Tech Stack:** Python 3.12 標準ライブラリ（http.server・sqlite3）、Chart.js 4.4.1（CDN、成長曲線だけ）、Google Fonts（丸ゴシック）、pytest。

**Spec:** `docs/superpowers/specs/2026-11-20-dashboard-result-screen-design.md`（モック: https://claude.ai/artifact/GE8BJCY9nxzfH3miGU3uUQ）

## Global Constraints

- 新しいライブラリを足さない（`uv add` しない）。Chart.js 4.4.1 はそのまま
- ダッシュボードは 127.0.0.1 で待ち受け、DB は `readonly=True` で読むだけ
- LLM が作った文（記憶・要約）は `textContent` で入れる。`innerHTML` に入れない
- タイプの名前・説明はオリジナル。「MBTI」「ラブタイプ」などの名前や文を使わない
- コメント・docstring は日本語で多め（AGENTS.md）。各ファイル先頭は `from __future__ import annotations`
- 時間帯の区切り（JST、会話を始めた時刻）: 朝 5〜10 時、昼 10〜16 時、夕方 16〜19 時、夜 19〜22 時、深夜 22〜5 時（下限を含み上限を含まない）
- PR 1 の画面では時間帯・間隔を「事実」として並べるだけ。「→ だからこう育った」と原因のように書かない
- 作業の最後に `uvx ruff format . && uvx ruff check . && uv run pytest` を通す
- AI は PR 作成まで。PR の本文は `.github/pull_request_template.md`、人間の確認欄にはチェックを付けない

## Review Focus

1. 登録したばかりで会話 0 回の人 → `growth` の時間帯・間隔・平均は `null`、回数は 0。画面は「まだお話ししていません」と出して崩れない（Task 3 のテスト）
2. 会話中（`ended_at` が NULL）の会話は数えない。エージェントが動いている最中に開いても数が揺れない（Task 1 のテスト）
3. お客さんのビューは person_id が NULL の会話・水やりだけで数え、名乗った人のぶんを混ぜない（Task 1・3 のテスト）
4. 時間帯の境目（4:59 → 深夜、5:00 → 朝、21:59 → 夜、22:00 → 深夜）と、UTC で渡された時刻（Task 1 のテスト）
5. 性格が一度も動いていない（履歴が 1 行・差が全部 0）→ `biggest_change` は `null`（Task 3 のテスト）

---

## PR 1: 結果画面（ブランチ `feature/dashboard-futamaru-style`、設計メモのコミットが載っている）

### Task 1: 集計の材料（時間帯の関数・会話と水やりの読み取り）

**Files:**
- Modify: `agent/plant/timeutil.py`
- Modify: `agent/plant/store.py`（sessions / waterings の節）
- Test: `tests/plant/test_timeutil.py`、`tests/plant/test_store.py`

**Interfaces:**
- Produces:
  - `TimeBand = Literal["朝", "昼", "夕方", "夜", "深夜"]`、`TIME_BANDS: tuple[TimeBand, ...] = ("朝", "昼", "夕方", "夜", "深夜")`（timeutil.py）
  - `time_band(dt: datetime) -> TimeBand`（timeutil.py。JST に直してから時で判定）
  - `PlantStore.finished_sessions(plant_id: str, person_id: int | None) -> list[Session]`（`ended_at` があるものだけ、`started_at` の古い順。person_id は `IS ?` で比べ、None ならお客さんだけ）
  - `PlantStore.watering_count(plant_id: str, person_id: int | None) -> int`（同じく `IS ?`）

- [ ] **Step 1: 失敗するテストを書く**

```python
# tests/plant/test_timeutil.py
@pytest.mark.parametrize("hour,band", [
    (4, "深夜"), (5, "朝"), (9, "朝"), (10, "昼"), (15, "昼"), (16, "夕方"),
    (18, "夕方"), (19, "夜"), (21, "夜"), (22, "深夜"), (23, "深夜"), (0, "深夜"),
])
def test_time_band_boundaries(hour, band):
    assert time_band(datetime(2026, 11, 20, hour, 0, tzinfo=JST)) == band

def test_time_band_uses_japan_time():
    # UTC 13:30 = JST 22:30
    assert time_band(datetime(2026, 11, 20, 13, 30, tzinfo=timezone.utc)) == "深夜"
```

```python
# tests/plant/test_store.py
def test_finished_sessions_are_per_person_and_skip_open_ones(store):
    # お客さんの会話 1・たろうの会話 2（終わった）・たろうの会話中 1
    # → finished_sessions(..., taro.id) は 2 件で started_at の古い順、None は 1 件

def test_watering_count_is_per_person(store):
    # お客さん 1 回・たろう 2 回 → 1 と 2
```

- [ ] **Step 2: 失敗を確かめる** — Run: `uv run pytest tests/plant/test_timeutil.py tests/plant/test_store.py -q` / Expected: ImportError か AttributeError で FAIL
- [ ] **Step 3: 上の Interfaces の 4 つを実装する**（SQL は既存の `latest_summary` と同じく `person_id IS ?`）
- [ ] **Step 4: 通ることを確かめる** — 同じコマンドで PASS
- [ ] **Step 5: コミット** — `git commit -m "feat: read finished sessions, waterings and time of day for the dashboard"`

### Task 2: タイプの長い説明文

**Files:**
- Modify: `agent/plant/plant_type.py`
- Test: `tests/plant/test_plant_type.py`

**Interfaces:**
- Produces: `PlantType.detail: str`（5 つ目のフィールド。2〜3 文）。`SPROUT` と 16 タイプ全部に入れる

- [ ] **Step 1: 失敗するテストを書く**

```python
def test_every_type_has_a_detail_of_two_or_three_sentences():
    for t in ALL_TYPES:
        assert 40 <= len(t.detail) <= 140, t.key
        assert 2 <= t.detail.count("。") <= 3, t.key

def test_type_texts_do_not_borrow_other_test_names():
    for t in ALL_TYPES:
        for word in ("MBTI", "ラブタイプ", "16Personalities"):
            assert word not in t.name + t.description + t.detail
```

- [ ] **Step 2: 失敗を確かめる** — `uv run pytest tests/plant/test_plant_type.py -q` / FAIL（`detail` が無い）
- [ ] **Step 3: `detail` を足し、17 タイプぶん書く。** 4 軸の高低（タイプのキー）と今の `description` に合う内容にし、植物の一人称ではなく「〇〇な子」と紹介する文にする。例（ひまわり）: 「話しかけられるのが大好きで、うれしい気持ちをそのまま口にする子。まっすぐに甘えてきて、あなたが来るとすぐに元気になります。毎日声をかけてもらったぶんだけ、明るく育ちました。」 ふたばは「まだどんな子になるか決まっていない」内容にする。PR でちゃむさん・ショーンさんに見てもらう下書き
- [ ] **Step 4: 通ることを確かめる**（`test_plant_type.py` 全体）
- [ ] **Step 5: コミット** — `git commit -m "feat: add a longer description to each plant type"`

### Task 3: `/api/state` に育て方の記録を足す

**Files:**
- Modify: `scripts/dashboard.py`（`_type_json`・`view_state`）
- Test: `tests/scripts/test_dashboard.py`

**Interfaces:**
- Consumes: Task 1 の `time_band`・`TIME_BANDS`・`finished_sessions`・`watering_count`、Task 2 の `PlantType.detail`
- Produces: 各ビューの `"growth"` と `"type"["detail"]`、`"days"`（株を作った日を 1 日目とした今日の日数、`prompt.days_between(created_at, now) + 1`）:

```python
"growth": {
    "talks": int,                 # finished_sessions の件数
    "waterings": int,             # watering_count
    "time_band": str | None,      # 会話を始めた時刻の時間帯で一番多いもの。同数は TIME_BANDS の順で先のもの。0 回なら None
    "interval_days": float | None,# 隣り合う会話の started_at の差（時間/24）の平均、小数 1 桁。2 回未満なら None
    "avg_turns": float | None,    # user_turns の平均、小数 1 桁。0 回なら None
    "biggest_change": {"trait": str, "delta": float} | None,
        # history の最初と最後の差の絶対値が一番大きい軸（同じなら TRAIT_NAMES の順で先）。delta は符号付き・小数 3 桁。
        # 履歴が 1 行、または差が全部 0 なら None
}
```

- 新しい関数 `growth_json(store: PlantStore, plant_id: str, person_id: int | None, history: list[TraitSnapshot]) -> dict` を `dashboard.py` に置き、`view_state` から呼ぶ

- [ ] **Step 1: 失敗するテストを書く**（fixture `db` はたろうの会話が 1 回、お客さんの会話が 2 回）

```python
def test_growth_counts_talks_waterings_and_time_band(tmp_path):
    # たろうと 3 回: 1・3・5 日目の 22:30 に開始、user_turns 2・4・6、水やり 1 回
    # → talks 3, waterings 1, time_band "深夜", interval_days 2.0, avg_turns 4.0

def test_growth_for_a_person_without_talks_is_empty(tmp_path):
    # 登録だけした人 → {"talks": 0, "waterings": 0, "time_band": None, "interval_days": None,
    #                   "avg_turns": None, "biggest_change": None}

def test_guest_growth_does_not_count_named_people(db):
    # お客さんのビューの talks は 2（たろうの 1 回を混ぜない）

def test_biggest_change_is_the_axis_that_moved_most(db):
    # fixture のお客さん: 初期 0.5 → (0.6, 0.45, 0.5, 0.5) → talkativeness, delta 0.1

def test_type_json_has_detail_and_view_has_days(db):
    # view["type"]["detail"] が空でない、plant["views"][0]["days"] >= 1
```

- [ ] **Step 2: 失敗を確かめる** — `uv run pytest tests/scripts/test_dashboard.py -q` / FAIL
- [ ] **Step 3: `growth_json` を実装し、`view_state` と `_type_json` に足す**（既存のキーは変えない。`days` は `store.plant_created_at` を使う）
- [ ] **Step 4: 通ることを確かめる**（`test_dashboard.py` 全体。既存のテストも PASS）
- [ ] **Step 5: コミット** — `git commit -m "feat: add how each futamaru was raised to the dashboard data"`

### Task 4: 結果画面の HTML

**Files:**
- Modify: `scripts/dashboard.html`（`<style>` と描画の JS。`refresh`・タブの決め方・ちらつき防止の仕組みは残す）
- Test: `tests/scripts/test_dashboard.py`

**Interfaces:**
- Consumes: Task 3 の JSON（`views[].type.detail`・`views[].growth`・`views[].days`）

- [ ] **Step 1: 失敗するテストを書く**

```python
def test_html_has_the_result_screen_sections():
    html = dashboard.HTML_PATH.read_text(encoding="utf-8")
    for label in ("育てた結果", "せいかく", "こんなふうに育てられた", "くわしく"):
        assert label in html
    assert "innerHTML" not in html  # LLM の文を HTML として入れない
```

- [ ] **Step 2: 失敗を確かめる**
- [ ] **Step 3: モック（GE8BJCY9nxzfH3miGU3uUQ）の見た目で書き直す。** カードの中は上から: 結果（「〇〇さんのふたまる」— お客さんは「お客さんのふたまる」、「N 日育てた結果…」、イラスト、タイプ名、キャッチコピー）→ せいかく（4 本のゲージ。0.5 から伸びる棒、伸びた側の名前に色: おしゃべり⟷ひかえめ／あまえんぼ⟷クール／げんき⟷まったり／しりたがり⟷ききじょうず。0.5 ちょうどは両方とも色なし）→ `detail` → こんなふうに育てられた（数字 3 つ: 話した回数・水をもらった回数・時間帯。箇条書き: 間隔「だいたい N 日おきに会いに来てくれた」（1.5 日以下は「ほぼ毎日」）、「1 回に平均 N 回話しかけてくれた」、「いちばん伸びたのは〇〇」（delta が負なら反対側の名前、例: affection −0.3 → クール）。talks 0 なら「まだお話ししていません」だけ）→ くわしく（成長曲線の折れ線、覚えていること、前回の会話、方言、振り返った回数）。レーダーチャートは消す。フォントは Google Fonts の丸ゴシック＋`"Hiragino Maru Gothic ProN", "Meiryo", sans-serif`。2 株の差し色は暖色と緑の 2 つ
- [ ] **Step 4: 通ることを確かめる** — `uv run pytest -q` で全部 PASS
- [ ] **Step 5: 目で確かめる** — `uv run python -m scripts.dashboard --db data/demo-day30.db --plants a1,b1` を起動し、Playwright で 1280px と 400px の幅のスクリーンショットを撮る。ひまわり（ショーン）・サボテン（ちゃむ）が出る、タブを押すと切り替わる、3 秒待っても崩れない・ちらつかない、`demo-day1.db` でも「ふたば」で崩れないこと
- [ ] **Step 6: コミット** — `git commit -m "feat: show each futamaru as a personality result card"`

### Task 5: 仕上げと PR 1

- [ ] **Step 1:** 仕様書 `docs/superpowers/specs/2026-11-13-futamaru-agent-design.md` の §6.6 に、結果画面と `growth` の 1〜2 行と更新履歴を足す
- [ ] **Step 2:** `uvx ruff format . && uvx ruff check . && uv run pytest` — 全部 PASS（件数を控える）
- [ ] **Step 3:** コミット → `git push -u origin feature/dashboard-futamaru-style`
- [ ] **Step 4:** テンプレートを埋めた本文ファイルで `gh pr create --body-file`。イシュー番号は #59、AI の利用は「使用あり」、人間の確認欄は空のまま。タイプの説明文をちゃむさん・ショーンさんに見てほしいと書く

---

## PR 2: 時間帯を energy に反映する（ブランチ `feature/time-of-day-energy`、PR 1 のブランチから切る）

### Task 6: 振り返りの「世話の様子」に時間帯の行を足す

**Files:**
- Modify: `docs/superpowers/specs/2026-11-13-futamaru-agent-design.md`（§6.3 の energy の判断材料、§6.7、更新履歴）— 先に直す
- Modify: `agent/plant/reflection.py`（`_care_lines`）
- Test: `tests/plant/test_reflection.py`

**Interfaces:**
- Consumes: Task 1 の `time_band`
- Produces: `_care_lines` の戻り値が 3 行になる（間隔・水やり・時間帯）

- [ ] **Step 1: 失敗するテストを書く**（既存の `_session` ヘルパーを使い、開始時刻だけ変える）

```python
async def test_late_night_talk_lowers_energy(store):
    # 開始 23:00（JST）→ "- 夜おそく（深夜）に話しかけてもらった（energy を少し下げる）。"

async def test_morning_talk_raises_energy(store):
    # 開始 7:30 → "- 朝に話しかけてもらった（energy を少し上げてよい）。"

async def test_daytime_talk_does_not_move_energy(store):
    # 開始 14:00 → "- 昼に話しかけてもらった（時間帯では energy を動かさない）。"
```

- [ ] **Step 2: 失敗を確かめる** — `uv run pytest tests/plant/test_reflection.py -q`
- [ ] **Step 3: `_care_lines` に 3 行目を足す。** 時刻は `session.started_at`。行の文はテストの 3 つの形に合わせ、夕方・夜も昼と同じ「時間帯では energy を動かさない」。docstring の例と「なぜ」（ショーンさんの FB、2026-11-20、energy だけ動かす）を書く
- [ ] **Step 4: 通ることを確かめる**（`test_reflection.py` 全体。既存の「世話の様子」テストも PASS）
- [ ] **Step 5: コミット** — `git commit -m "feat: let the time of day shape energy in reflection"`

### Task 7: デモ用 DB の作り直しと PR 2

- [ ] **Step 1:** `uvx ruff format . && uvx ruff check . && uv run pytest` — 全部 PASS
- [ ] **Step 2:** コミット → push → テンプレートで PR 作成。本文に「マージ後、LLM が動く PC で `docs/demo-day.md` のとおり 1・5・30 日目の DB を `scripts.simulate`（`demo-sean.json`・`demo-chamu.json`、`--day 1/5/30`）で作り直す必要がある」「境目（朝 5〜10 時・深夜 22〜5 時）はショーンさんに確認」と書く
- [ ] **Step 3:** DB の作り直しは人間に任せる（LLM が要る・`data/` はコミットしない）。作り直した `demo-day30.db` でダッシュボードを開き、ちゃむさんの元気度が前より下がっていることを確かめてもらう
