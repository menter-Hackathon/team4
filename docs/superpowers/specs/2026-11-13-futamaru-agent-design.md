# ふたまる 音声AIエージェント 設計書

- 日付: 2026-11-13
- 対象: サーバー側の音声AIエージェントの骨組み（Python / LiveKit Agents）
- 企画: 「ふたまる」— 育て方によって個性が変わる、話せる観葉植物

## 1. 目的と成功基準

### 目的
観葉植物に取り付けた ESP32-S3 デバイスと音声で会話でき、日々の関わり方によって性格が変化するAIエージェントの骨組みを作る。

### ハッカソンでの成功基準
1. 「ふたまる」と呼びかけると植物が起き、実機（ESP32-S3）とリアルタイムに音声会話できる（割り込み対応）
2. 会話内容・ユーザーの好みを長期記憶し、次回以降の会話に反映できる
3. 性格パラメータが会話履歴に応じて変化し、話し方に違いが出る
4. 応答に合わせてディスプレイの表情が変わる
5. 同じ初期状態の2株を異なる会話履歴で育て、個性の違いをデモできる

### 範囲外
- ESP32 ファームウェア、3Dプリント筐体（ただし ESP32 側に求める要件は §9 で定義する）
- GPU サーバーへのモデルのデプロイ作業（ただし API 契約は §8 で定義する）
- 話者識別（1株を誰が話しかけても同一ユーザーとして扱う）
- 眠っている間の自発的な発話、時刻ベースのスケジュール発話
- 性格・記憶を可視化するダッシュボード（デモでは `scripts/simulate.py` の出力を使う）

## 2. 決定事項

| 項目 | 決定 |
|---|---|
| 音声エージェントフレームワーク | LiveKit Agents (Python) |
| LiveKit サーバー | LiveKit Cloud |
| LLM | gemma4（GPU サーバー上の vLLM、OpenAI 互換 API） |
| STT | Whisper（GPU サーバー、OpenAI 互換 API） |
| TTS | kotoba 系 TTS（GPU サーバー、OpenAI 互換 API） |
| VAD | Silero VAD（エージェント内で実行） |
| エージェント実行場所 | GPU サーバー（開発時はローカル PC） |
| 個体の単位 | 1デバイス = 1株。`plant_id` で識別 |
| 植物の名前 | 全株共通で「ふたまる」（ウェイクワードと同じ） |
| 起動方法 | ウェイクワード「ふたまる」。サーバー側で文字起こし結果から検出 |
| 会話の区切り | 最後のユーザー発話から 60 秒無言で終了（眠る） |
| 個性の表現 | 数値パラメータ（4軸） + 長期記憶 |
| 永続化 | SQLite（WAL モード） |
| 時刻 | すべて JST（`+09:00` 付き ISO 8601 文字列）で保存 |
| パッケージ管理 | uv |

## 3. 全体構成

```
[ESP32-S3 (植物)] ──WebRTC──> [LiveKit Cloud] <──── [Agent ワーカー (GPU サーバー)]
   マイク(常時送信)            ルーム: plant-<id>         │
   スピーカー                                           │
   ディスプレイ <── RPC: 表情 ──────────────────────────┤
                                                       ├─> vLLM (gemma4)
                                                       ├─> Whisper API (STT)
                                                       ├─> kotoba TTS API
                                                       └─> SQLite (性格・記憶)
```

### 3.1 接続とディスパッチ
- 各 ESP32 は `scripts/issue_token.py --plant-id <id>` で発行した長期トークン（有効期限 30 日）をファームウェアの設定に書き込んで使う。
  - identity: `device-<plant_id>`
  - ルーム名: `plant-<plant_id>`
  - 権限: ルーム参加、音声の publish / subscribe
- エージェントは LiveKit Cloud の自動ディスパッチ（新しいルームすべてに参加）を使う。
- エージェントはルーム名が `plant-` で始まらない場合は即座に退出する。
- ルーム名から `plant_id` を取り出し、その株の性格・記憶をロードする。該当する `plant_id` が DB に無ければ、初期状態（全パラメータ 0.5）で新規作成する。
- 1つのワーカープロセスが複数の株（ルーム）を同時に扱う。DB は SQLite を WAL モードで使い、書き込みは株ごとに独立した行に対して行う。

## 4. ディレクトリ構成

```
agent/
  main.py              エントリポイント。AgentSession の組み立て、イベント配線
  config.py            環境変数の読み込み（各 API の URL、DB パス、タイムアウト等）
  plugins/
    whisper_stt.py     STT の生成（OpenAI 互換 STT プラグインに base_url を渡す薄いラッパー）
    kotoba_tts.py      TTS の生成（同上。互換でない場合はここに独自実装を置く）
  plant/
    store.py           SQLite の読み書き（plants / memories / sessions / utterances / trait_history）
    personality.py     性格パラメータの定義・クランプ・更新ルール
    prompt.py          性格 + 記憶 + 前回要約 → システムプロンプト
    reflection.py      会話終了時の記憶抽出・性格変化量の判定（LLM 呼び出し）
    expression.py      表情タグの解析・除去（ストリーミング対応）
    wake.py            ウェイクワード検出・正規化、起きている/眠っている状態の管理
scripts/
  issue_token.py       ESP32 用の LiveKit トークン発行
  simulate.py          会話ログを流し込んで成長を早送りするデモ用スクリプト
tests/
```

### 境界のルール
- `plant/` 以下は LiveKit に依存しない純粋なロジックとする。LLM 呼び出しと現在時刻は関数（または小さなインターフェース）として外から注入し、テストでは偽物に差し替える。
- LiveKit に依存するのは `main.py` と `plugins/` のみ。
- 将来、記憶・性格を別 API サーバーとして切り出す場合は `plant/` をそのまま流用できること。

## 5. 起床・会話・睡眠の状態遷移

### 5.1 状態

| 状態 | STT | 応答 | 記録 | 表情 |
|---|---|---|---|---|
| `SLEEPING` 眠っている | 動く（VAD で検出した発話のみ） | しない | しない（文字起こし結果は破棄） | `sleepy` |
| `AWAKE` 起きている | 動く | する | `utterances` に即時保存 | 応答に応じて変化 |

起動時・ESP32 参加時の初期状態は `SLEEPING`。

### 5.2 遷移

```
SLEEPING --(発話に「ふたまる」を含む)--> AWAKE      : セッション開始
AWAKE    --(最後のユーザー発話から60秒無言)--> SLEEPING : セッション終了 → reflection
AWAKE    --(ESP32 退出)--> (終了)                   : セッション終了 → reflection
```

- 無言の判定は「植物が話していない時間」で数える。植物の発話中・応答生成中はタイマーを止め、植物の発話が終わった時点から数え直す。

### 5.3 ウェイクワード検出（`wake.py`）
- `SLEEPING` 中のユーザー発話の文字起こし結果を正規化して判定する。
  - 正規化: カタカナ→ひらがな、空白・句読点・記号の除去
  - 一致条件: 正規化後の文字列が `WAKE_WORDS` のいずれかを含む
  - 既定の `WAKE_WORDS`: `ふたまる`, `二丸`, `ふた丸`, `2丸`（環境変数で上書き可能）
- Whisper の認識精度を上げるため、STT リクエストの `prompt` に「ふたまる」を含める。
- ウェイクワードの後ろに続く文（例:「ふたまる、今日ね…」→「今日ね…」）が2文字以上あれば、それを最初のユーザー発話として扱い、そのまま応答する。
- ウェイクワードだけの場合は、植物から話しかける（§7.2）。
- LiveKit Agents 上では、`Agent.on_user_turn_completed` で状態を確認し、`SLEEPING` 中かつウェイクワードを含まない発話は `StopResponse` で応答を止め、チャット履歴にも残さない。

### 5.4 セッションの終了処理
1. 表情 `sleepy` を送る。
2. `sessions.ended_at` を記録する。
3. reflection（§6.3）を非同期で実行する。reflection の完了を待たずに `SLEEPING` に戻り、次のウェイクワードを受け付ける。
4. 次のセッションの開始時点で reflection が終わっていなければ、更新前の性格・記憶でプロンプトを組み立てる。

## 6. 性格と記憶

### 6.1 性格パラメータ
すべて 0.0〜1.0、初期値 0.5。

| 軸 | 低い | 高い | 主な判断材料 |
|---|---|---|---|
| `talkativeness` おしゃべり度 | 短く静かに返す | 長く話す・自分から話題を振る | 話しかけ頻度・会話の長さ |
| `affection` 甘え度 | 素っ気ない | 甘える・「会いたかった」 | 褒め言葉・優しい言葉の量 |
| `energy` 元気度 | 落ち着いた口調 | テンション高い・「！」多め | ユーザーの口調・話題の明るさ |
| `curiosity` 好奇心 | 相づち中心 | よく質問する | ユーザーが自分の話をする量 |

### 6.2 SQLite スキーマ

```sql
CREATE TABLE plants (
  plant_id       TEXT PRIMARY KEY,
  name           TEXT NOT NULL DEFAULT 'ふたまる',
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
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);

CREATE TABLE sessions (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  plant_id    TEXT NOT NULL REFERENCES plants(plant_id),
  started_at  TEXT NOT NULL,
  ended_at    TEXT,
  user_turns  INTEGER NOT NULL DEFAULT 0,
  summary     TEXT,                    -- reflection で生成
  reflected   INTEGER NOT NULL DEFAULT 0  -- 0: 未処理, 1: 処理済み
);

CREATE TABLE utterances (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id  INTEGER NOT NULL REFERENCES sessions(id),
  role        TEXT NOT NULL,           -- user | plant
  text        TEXT NOT NULL,           -- 植物側は表情タグ除去後の文
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

- 会話ログは `AWAKE` 中の発話ごとに `utterances` へ即時保存する。
- 植物を作成した時点で、初期値の行を `trait_history` に1行入れる（成長グラフの起点）。

### 6.3 reflection：記憶と性格の更新（`reflection.py` + `personality.py`）
セッション終了時（§5.4）に実行する。

1. 以下を LLM に渡す。
   - そのセッションの発話ログ
   - 既存の記憶（重要度 × 新しさの上位 30 件、`id` 付き）
2. 以下の JSON を返させる。
   ```json
   {
     "summary": "今日はテストの話をした。ユーザーは少し疲れていた。",
     "new_memories": [
       {"content": "ユーザーは明日数学のテストがある", "kind": "event", "importance": 0.8}
     ],
     "updated_memories": [
       {"id": 12, "content": "ユーザーは辛いカレーが好き", "importance": 0.7}
     ],
     "trait_deltas": {"talkativeness": 0.02, "affection": 0.03, "energy": -0.01, "curiosity": 0.0}
   }
   ```
   - 既存の記憶と同じ内容は `new_memories` に入れず、内容が変わった・補強された場合は `updated_memories` で更新するよう指示する。
   - `updated_memories` の `id` が既存の記憶に無ければ無視する。
3. 各 `trait_deltas` は **±0.05 にクランプ**し、適用後の値は 0.0〜1.0 にクランプする。
4. `talkativeness` は LLM の判定値と、行動統計からの目標値をブレンドする。
   - 行動目標値 `t_behavior = min(1.0, 直近7日の AWAKE 中のユーザー発話数 / 100)`
   - 最終変化量 `delta = clamp(0.5 * llm_delta + 0.5 * (t_behavior - 現在値) * 0.1, -0.05, 0.05)`
5. 1つのトランザクションで、`plants` の性格値更新、`memories` の追加・更新、`trait_history` への1行追加、`sessions.summary` の保存と `reflected = 1` を行う。
6. LLM の出力が JSON として不正、または必須キーが無い場合は1回だけ再試行し、それでも失敗したら `reflected = 0` のまま残す。
7. ユーザー発話が0件のセッション（ウェイクワードだけで終わった等）は LLM を呼ばず、`reflected = 1` にするだけとする。
8. エージェント起動時に以下を処理する。
   - `ended_at` が無いセッション（クラッシュ等で残ったもの）: 最後の発話時刻（発話が無ければ `started_at`）を `ended_at` にする
   - `reflected = 0` のセッション: reflection を再実行する

### 6.4 プロンプト組み立て（`prompt.py`）
システムプロンプトは以下の順で連結する。

1. **ベース人格**: 「あなたは観葉植物の{name}。持ち主と話し言葉で短く話す。1回の返答は{長さ指示}。」
2. **出力形式**: 「返答の先頭に必ず表情タグを1つ付ける: [neutral] [happy] [sad] [surprised] [love] [sleepy]」
3. **性格指示**: 各軸の値を3段階に変換した指示文
   - 段階の境界: 低 0.0–0.33 / 中 0.34–0.66 / 高 0.67–1.0
   - 例: `affection` 低 →「少し素っ気なく接する」、中 →「普通に親しく接する」、高 →「持ち主に甘えて親しげに話す」
   - `talkativeness` は長さ指示にも反映する（低: 1文、中: 1〜2文、高: 2〜3文で話題も振る）
   - 4軸×3段階の文言はすべて `prompt.py` 内の定数として持つ
4. **記憶**: `importance * 新しさ係数` の上位10件を箇条書き
   - 新しさ係数 = `0.5 ** (最終更新からの経過日数 / 7)`（1週間で半減）
5. **前回の会話**: 直近の `reflected = 1` のセッションの `summary`（無ければ省略）

## 7. 会話中の動作

### 7.1 表情（`expression.py`）

**表情一覧**（ESP32 が表示できる必要があるもの、計8種）

| 名前 | 送るタイミング |
|---|---|
| `neutral` `happy` `sad` `surprised` `love` | LLM 応答の表情タグ |
| `sleepy` | LLM 応答の表情タグ / `SLEEPING` 中 |
| `listening` | `AWAKE` 中にユーザーが話し始めたとき |
| `thinking` | `AWAKE` 中にユーザー発話が終わり、応答を待っている間 |

**表情タグの解析**
- LLM 応答はストリーミングで届くため、先頭から `]` が出るまで（最大 16 文字）バッファする。
  - 先頭が `[` でない場合は、バッファせずそのまま TTS に流し、表情は `neutral` とする。
  - 16 文字以内に `]` が出ない場合は、バッファをそのまま TTS に流し、表情は `neutral` とする。
  - 不明なタグ名は `neutral` とする。
- タグは TTS に渡す前に除去する。
- LLM が 6 種以外（`listening` / `thinking`）を返した場合も `neutral` とする。

**ESP32 への送信**
- LiveKit RPC
  - メソッド名: `set_expression`
  - ペイロード: `{"expression": "<name>"}`
  - 宛先: identity `device-<plant_id>`
- RPC の失敗（ESP32 未応答など）はログに残して無視し、会話は止めない。

### 7.2 植物から話しかける
- **起こされたとき**: ウェイクワードだけで呼ばれた場合、記憶と前回要約をもとに植物から話し始める（例: 「あ、おかえり！昨日言ってたテスト、どうだった？」）。
  - LLM には「持ち主に呼ばれて起きた。記憶や前回の会話を踏まえて短く話しかける」という指示を追加して生成する。
- **会話中の沈黙**: `AWAKE` 中にユーザーの沈黙が続いたら、睡眠（60秒）の前に1回だけ話題を振る。
  - 待ち時間 = `talkativeness >= 0.3` のとき `10 + (1.0 - talkativeness) * 30` 秒（0.9 → 13秒、0.5 → 25秒、0.3 → 31秒）
  - `talkativeness < 0.3` のときは話題を振らない
  - 話題を振った後もユーザーが話さなければ、60秒ルールで眠る（植物の発話終了時点から数え直す、§5.2）

## 8. GPU サーバー API 契約

エージェントは以下の OpenAI 互換エンドポイントを前提とする。URL は環境変数で指定する。

| 用途 | 環境変数 | エンドポイント | 備考 |
|---|---|---|---|
| LLM | `LLM_BASE_URL`, `LLM_MODEL` | `POST {LLM_BASE_URL}/chat/completions` | vLLM で gemma4 を提供。ストリーミング対応必須。reflection 用に JSON 出力（`response_format`）に対応していること |
| STT | `STT_BASE_URL`, `STT_MODEL` | `POST {STT_BASE_URL}/audio/transcriptions` | multipart で音声ファイル、`language=ja`、`prompt` パラメータ対応 |
| TTS | `TTS_BASE_URL`, `TTS_MODEL`, `TTS_VOICE` | `POST {TTS_BASE_URL}/audio/speech` | JSON `{"model","input","voice","response_format":"pcm"}` → 24kHz 16bit mono PCM |

- kotoba TTS が OpenAI 互換で提供できない場合は、`plugins/kotoba_tts.py` に独自の TTS プラグインを実装し、他のモジュールには影響させない。
- 開発時は、ローカル PC 上のモデルまたはクラウド API に向けて同じ環境変数を設定して動かす。

その他の環境変数:
- `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`（LiveKit Cloud）
- `FUTAMARU_DB_PATH`（既定: `./data/futamaru.db`）
- `WAKE_WORDS`（カンマ区切り、既定は §5.3）
- `SLEEP_AFTER_SECONDS`（既定: 60）
- `LLM_TIMEOUT` / `STT_TIMEOUT` / `TTS_TIMEOUT`（秒、既定: 15 / 10 / 10）

## 9. ESP32 側への要件（ハード担当との取り決め）

エージェントが正しく動くために、ESP32 ファームウェアに以下を求める。

1. 起動時に Wi-Fi に接続し、書き込まれたトークンで `plant-<plant_id>` ルームに参加する。切断時は自動で再接続する。
2. マイク音声を常時 publish する（ウェイクワードはサーバー側で検出するため）。
3. エージェントの音声トラックを subscribe してスピーカーで再生する。
4. **エコーキャンセル（AEC）を有効にする**。植物自身の声をマイクで拾うと、割り込みやウェイクワードの誤検出が起きるため（ESP-IDF の esp-sr / AFE などを想定）。
5. RPC メソッド `set_expression` を登録し、§7.1 の8種の表情を表示する。未知の表情名は `neutral` として表示する。
6. 起動直後の表示は `sleepy` とする。

## 10. エラー処理

| 状況 | 振る舞い |
|---|---|
| LLM タイムアウト・エラー（会話中） | 定型文「ん…ちょっとぼーっとしてた」を TTS で返す |
| TTS エラー | 表情 `sleepy` を送ってその応答をスキップ |
| STT エラー | その発話を無視して待機を継続（`SLEEPING` 中はウェイクワード判定もスキップ） |
| reflection 失敗 | `reflected = 0` のまま残し、次回起動時に再処理 |
| RPC 失敗 | ログのみ |
| 回線切断 | LiveKit の再接続に任せる。ESP32 退出時はセッション終了扱い（`AWAKE` だった場合） |
| ルーム名が `plant-` で始まらない | 即座に退出 |

## 11. テスト

- **単体テスト（pytest）**: `plant/` 以下を中心に
  - `personality`: 変化量のクランプ（±0.05）、値域クランプ（0〜1）、talkativeness のブレンド計算
  - `prompt`: 性格値ごとの指示文（段階の境界値）、記憶の選択順（重要度 × 新しさ）、前回要約の有無
  - `store`: 一時ファイルの SQLite で CRUD、未終了・未処理セッションの検出と修復
  - `reflection`: 偽の LLM で正常 JSON・不正 JSON・再試行・存在しない記憶 id・発話0件セッション
  - `expression`: タグ解析・除去、タグなし・不明タグ・閉じ括弧なし・チャンク分割されたタグ
  - `wake`: 正規化（カタカナ・記号・空白）、各表記ゆれでの検出、ウェイクワード後の文の切り出し、状態遷移とタイマー（偽の時計で検証）
- **プラグイン**: モック HTTP サーバーに対して STT/TTS のリクエスト形式を確認
- **結合確認（手動）**: LiveKit Agents の `console` モードで PC のマイク・スピーカーを使い、ESP32 無しで以下を確認
  - 「ふたまる」と言うまで反応しないこと
  - 会話・表情ログ・DB への記録
  - 60秒無言で眠り、reflection で性格・記憶が更新されること
- **デモ確認**: `scripts/simulate.py` で2株に異なる会話ログを流し、性格値・プロンプト・応答の違いを確認
