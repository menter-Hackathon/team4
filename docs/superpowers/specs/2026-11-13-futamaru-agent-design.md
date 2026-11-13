# ふたまる 音声AIエージェント 設計書

- 日付: 2026-11-13
- 対象: サーバー側の音声AIエージェントの骨組み（Python / LiveKit Agents）
- 企画: 「ふたまる」— 育て方によって個性が変わる、話せる観葉植物

## 1. 目的と成功基準

### 目的
観葉植物に取り付けた ESP32-S3 デバイスと音声で会話でき、日々の関わり方によって性格が変化するAIエージェントの骨組みを作る。

### ハッカソンでの成功基準
1. 実機（ESP32-S3）とリアルタイムに音声会話できる（割り込み対応）
2. 会話内容・ユーザーの好みを長期記憶し、次回以降の会話に反映できる
3. 性格パラメータが会話履歴に応じて変化し、話し方に違いが出る
4. 応答に合わせてディスプレイの表情が変わる
5. 同じ初期状態の2株を異なる会話履歴で育て、個性の違いをデモできる

### 範囲外
- ESP32 ファームウェア、3Dプリント筐体
- GPU サーバーへのモデルのデプロイ作業（ただし API 契約は本書 §7 で定義する）
- 話者識別（1株を誰が話しかけても同一ユーザーとして扱う）
- 時刻ベースのスケジュール発話

## 2. 決定事項

| 項目 | 決定 |
|---|---|
| 音声エージェントフレームワーク | LiveKit Agents (Python) |
| LiveKit サーバー | LiveKit Cloud |
| LLM | gemma4（GPU サーバー上の vLLM、OpenAI 互換 API） |
| STT | Whisper（GPU サーバー、OpenAI 互換 API） |
| TTS | kotoba 系 TTS（GPU サーバー、OpenAI 互換 API） |
| エージェント実行場所 | GPU サーバー（開発時はローカル PC） |
| 個体の単位 | 1デバイス = 1株。`plant_id` で識別 |
| 個性の表現 | 数値パラメータ（4軸） + 長期記憶 |
| 永続化 | SQLite |
| パッケージ管理 | uv |

## 3. 全体構成

```
[ESP32-S3 (植物)] ──WebRTC──> [LiveKit Cloud] <──── [Agent ワーカー (GPU サーバー)]
   マイク/スピーカー            ルーム: plant-<id>         │
   ディスプレイ <── RPC: 表情 ──────────────────────────┤
                                                       ├─> vLLM (gemma4)
                                                       ├─> Whisper API (STT)
                                                       ├─> kotoba TTS API
                                                       └─> SQLite (性格・記憶)
```

- ESP32 は `plant-<plant_id>` という名前のルームに参加する。
- エージェントはルーム名から `plant_id` を取り出し、その株の性格・記憶をロードする。
- 該当する `plant_id` が DB に無ければ、初期状態（全パラメータ 0.5）で新規作成する。

## 4. ディレクトリ構成

```
agent/
  main.py              エントリポイント。AgentSession の組み立て、イベント配線
  config.py            環境変数の読み込み（各 API の URL、DB パス等）
  plugins/
    whisper_stt.py     STT の生成（OpenAI 互換 STT プラグインに base_url を渡す薄いラッパー）
    kotoba_tts.py      TTS の生成（同上。互換でない場合はここに独自実装を置く）
  plant/
    store.py           SQLite の読み書き（plants / memories / sessions / trait_history）
    personality.py     性格パラメータの定義・クランプ・更新ルール
    prompt.py          性格 + 記憶 + 前回要約 → システムプロンプト
    reflection.py      会話終了時の記憶抽出・性格変化量の判定（LLM 呼び出し）
    expression.py      表情タグの解析・除去、RPC ペイロード生成
scripts/
  simulate.py          会話ログを流し込んで成長を早送りするデモ用スクリプト
tests/
```

### 境界のルール
- `plant/` 以下は LiveKit に依存しない純粋なロジックとする。LLM 呼び出しは関数（または小さなインターフェース）として外から注入し、テストでは偽物に差し替える。
- LiveKit に依存するのは `main.py` と `plugins/` のみ。
- 将来、記憶・性格を別 API サーバーとして切り出す場合は `plant/` をそのまま流用できること。

## 5. 性格と記憶

### 5.1 性格パラメータ
すべて 0.0〜1.0、初期値 0.5。

| 軸 | 低い | 高い | 主な判断材料 |
|---|---|---|---|
| `talkativeness` おしゃべり度 | 短く静かに返す | 長く話す・自分から話題を振る | 話しかけ頻度・会話の長さ |
| `affection` 甘え度 | 素っ気ない | 甘える・「会いたかった」 | 褒め言葉・優しい言葉の量 |
| `energy` 元気度 | 落ち着いた口調 | テンション高い・「！」多め | ユーザーの口調・話題の明るさ |
| `curiosity` 好奇心 | 相づち中心 | よく質問する | ユーザーが自分の話をする量 |

### 5.2 SQLite スキーマ

```sql
CREATE TABLE plants (
  plant_id       TEXT PRIMARY KEY,
  name           TEXT NOT NULL,
  created_at     TEXT NOT NULL,
  talkativeness  REAL NOT NULL DEFAULT 0.5,
  affection      REAL NOT NULL DEFAULT 0.5,
  energy         REAL NOT NULL DEFAULT 0.5,
  curiosity      REAL NOT NULL DEFAULT 0.5
);

CREATE TABLE memories (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  plant_id    TEXT NOT NULL REFERENCES plants(plant_id),
  content     TEXT NOT NULL,          -- 例: 「ユーザーはカレーが好き」
  kind        TEXT NOT NULL,          -- preference | event | habit
  importance  REAL NOT NULL,          -- 0.0〜1.0
  created_at  TEXT NOT NULL
);

CREATE TABLE sessions (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  plant_id        TEXT NOT NULL REFERENCES plants(plant_id),
  started_at      TEXT NOT NULL,
  ended_at        TEXT,
  user_turns      INTEGER NOT NULL DEFAULT 0,
  summary         TEXT,                -- reflection で生成
  reflected       INTEGER NOT NULL DEFAULT 0  -- 0: 未処理, 1: 処理済み
);

CREATE TABLE utterances (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id  INTEGER NOT NULL REFERENCES sessions(id),
  role        TEXT NOT NULL,           -- user | plant
  text        TEXT NOT NULL,
  created_at  TEXT NOT NULL
);

CREATE TABLE trait_history (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  plant_id       TEXT NOT NULL REFERENCES plants(plant_id),
  session_id     INTEGER REFERENCES sessions(id),
  talkativeness  REAL NOT NULL,
  affection      REAL NOT NULL,
  energy         REAL NOT NULL,
  curiosity      REAL NOT NULL,
  recorded_at    TEXT NOT NULL
);
```

会話ログは発話ごとに `utterances` へ即時保存する（セッション全文を最後にまとめて保存しない）。

### 5.3 性格の更新ルール（`reflection.py` + `personality.py`）
会話終了時（ESP32 退出 / セッション終了）に実行する。

1. そのセッションの発話ログを LLM に渡し、以下の JSON を返させる。
   ```json
   {
     "summary": "今日はテストの話をした。ユーザーは少し疲れていた。",
     "memories": [
       {"content": "ユーザーは明日数学のテストがある", "kind": "event", "importance": 0.8}
     ],
     "trait_deltas": {"talkativeness": 0.02, "affection": 0.03, "energy": -0.01, "curiosity": 0.0}
   }
   ```
2. 各 `trait_deltas` は **±0.05 にクランプ**し、適用後の値は 0.0〜1.0 にクランプする。
3. `talkativeness` は LLM の判定値と、行動統計からの目標値をブレンドする。
   - 行動目標値 `t_behavior = min(1.0, 直近7日のユーザー発話数 / 100)`
   - 最終変化量 `delta = clamp(0.5 * llm_delta + 0.5 * (t_behavior - 現在値) * 0.1, -0.05, 0.05)`
4. 更新後の値を `plants` に保存し、`trait_history` に1行追加、`sessions.summary` を保存して `reflected = 1` にする。
5. LLM の出力が JSON として不正な場合は1回だけ再試行し、それでも失敗したら `reflected = 0` のまま残す。
6. エージェント起動時に `reflected = 0` かつ `ended_at` があるセッションを再処理する。

### 5.4 プロンプト組み立て（`prompt.py`）
システムプロンプトは以下の順で連結する。

1. **ベース人格**: 「あなたは観葉植物の{name}。持ち主と話し言葉で短く話す。1回の返答は{長さ指示}。」
2. **出力形式**: 「返答の先頭に必ず表情タグを1つ付ける: [neutral] [happy] [sad] [surprised] [love] [sleepy]」
3. **性格指示**: 各軸の値を3段階に変換した指示文
   - 例: `affection` 0.0–0.33 → 「少し素っ気なく接する」、0.34–0.66 → 「普通に親しく接する」、0.67–1.0 → 「持ち主に甘えて親しげに話す」
   - `talkativeness` は長さ指示にも反映する（低: 1文、中: 1〜2文、高: 2〜3文で話題も振る）
4. **記憶**: `importance * 新しさ係数` の上位10件を箇条書き
   - 新しさ係数 = `0.5 ** (経過日数 / 7)`（1週間で半減）
5. **前回の会話**: 直近セッションの `summary`

## 6. 会話中の動作

### 6.1 表情（`expression.py`）
- LLM 応答の先頭タグ `[xxx]` を解析し、TTS に渡す前に除去する。
- タグが無い・不明な場合は `neutral` とする。
- ESP32 への送信は LiveKit RPC:
  - メソッド名: `set_expression`
  - ペイロード: `{"expression": "<name>"}`
  - 宛先: ルーム内の ESP32 参加者
- 表情一覧: `neutral` `happy` `sad` `surprised` `love` `sleepy`
- エージェントの状態変化に応じて以下も送る:
  - ユーザー発話中 → `listening`
  - LLM 応答待ち → `thinking`
- RPC の失敗（ESP32 未応答など）はログに残して無視し、会話は止めない。

### 6.2 自発的な発話
- **参加時の話しかけ**: ESP32 がルームに参加したら、記憶と前回要約をもとに植物から話し始める（例: 「昨日言ってたテスト、どうだった？」）。
- **沈黙時の話題振り**: ユーザーの沈黙が一定時間続いたら話題を振る。
  - 待ち時間 = `talkativeness >= 0.3` のとき `20 + (1.0 - talkativeness) * 60` 秒（0.9 → 26秒、0.5 → 50秒、0.3 → 62秒）
  - `talkativeness < 0.3` のときは話しかけない
  - 1回話しかけて反応が無ければ、次のユーザー発話まで再度は話しかけない

## 7. GPU サーバー API 契約

エージェントは以下の OpenAI 互換エンドポイントを前提とする。URL は環境変数で指定する。

| 用途 | 環境変数 | エンドポイント | 備考 |
|---|---|---|---|
| LLM | `LLM_BASE_URL`, `LLM_MODEL` | `POST {LLM_BASE_URL}/chat/completions` | vLLM で gemma4 を提供。ストリーミング対応必須 |
| STT | `STT_BASE_URL`, `STT_MODEL` | `POST {STT_BASE_URL}/audio/transcriptions` | multipart で音声ファイル、`language=ja` |
| TTS | `TTS_BASE_URL`, `TTS_MODEL`, `TTS_VOICE` | `POST {TTS_BASE_URL}/audio/speech` | JSON `{"model","input","voice","response_format":"pcm"}` → 24kHz 16bit mono PCM |

- kotoba TTS が OpenAI 互換で提供できない場合は、`plugins/kotoba_tts.py` に独自の TTS プラグインを実装し、他のモジュールには影響させない。
- 開発時は、ローカル PC 上のモデルまたはクラウド API に向けて同じ環境変数を設定して動かす。

その他の環境変数:
- `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`（LiveKit Cloud）
- `FUTAMARU_DB_PATH`（既定: `./data/futamaru.db`）

## 8. エラー処理

| 状況 | 振る舞い |
|---|---|
| LLM タイムアウト・エラー | 定型文「ん…ちょっとぼーっとしてた」を TTS で返す |
| TTS エラー | 表情 `sleepy` を送ってその応答をスキップ |
| STT エラー | その発話を無視して待機を継続 |
| reflection 失敗 | `reflected = 0` のまま残し、次回起動時に再処理 |
| RPC 失敗 | ログのみ |
| 回線切断 | LiveKit の再接続に任せる。セッションは ESP32 退出時に終了扱い |

タイムアウト値は LLM 15秒、TTS 10秒、STT 10秒を初期値とし、`config.py` で変更可能にする。

## 9. テスト

- **単体テスト（pytest）**: `plant/` 以下を中心に
  - `personality`: 変化量のクランプ（±0.05）、値域クランプ（0〜1）、talkativeness のブレンド計算
  - `prompt`: 性格値ごとの指示文、記憶の選択順（重要度 × 新しさ）、前回要約の有無
  - `store`: 一時ファイルの SQLite で CRUD、未処理セッションの検出
  - `reflection`: 偽の LLM で正常 JSON・不正 JSON・再試行
  - `expression`: タグ解析・除去、タグなし・不明タグの扱い
- **プラグイン**: モック HTTP サーバーに対して STT/TTS のリクエスト形式を確認
- **結合確認（手動）**: LiveKit Agents の `console` モードで PC のマイク・スピーカーを使い、ESP32 無しで会話・表情ログ・DB 更新を確認
- **デモ確認**: `scripts/simulate.py` で2株に異なる会話ログを流し、性格値とプロンプトが異なることを確認
