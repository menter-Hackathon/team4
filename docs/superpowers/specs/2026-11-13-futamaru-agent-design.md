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
| LLM | Gemma 4（GPU サーバー上の vLLM ≥ 0.19、OpenAI 互換 API）。モデルサイズは §8.1 |
| STT | kotoba-whisper v2.0（GPU サーバー上の Speaches、OpenAI 互換 API） |
| TTS | Kotoba TTS v2（fal.ai のホスト型 API。自前ホスト不可） |
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
                                                       ├─> vLLM (Gemma 4)          [GPU サーバー]
                                                       ├─> Speaches (kotoba-whisper) [GPU サーバー]
                                                       ├─> fal.ai (Kotoba TTS v2)   [外部 API]
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
    whisper_stt.py     STT の生成（LiveKit の OpenAI 互換 STT プラグインに base_url を渡す薄いラッパー）
    kotoba_tts.py      Kotoba TTS v2（fal.ai WebSocket）を呼ぶ独自 TTS プラグイン
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

## 8. モデル API 契約

URL・モデル名はすべて環境変数で指定する。開発時はローカル PC 上のモデルやクラウド API に向けて同じ環境変数を設定して動かす。

### 8.1 LLM: Gemma 4 on vLLM（GPU サーバー）

| 項目 | 内容 |
|---|---|
| 環境変数 | `LLM_BASE_URL`, `LLM_MODEL` |
| エンドポイント | `POST {LLM_BASE_URL}/chat/completions`（OpenAI 互換、ストリーミング） |
| vLLM バージョン | 0.19 以上（Gemma 4 のネイティブ実装が入った版） |
| 起動例 | `vllm serve google/gemma-4-26B-A4B-it --max-model-len 32768 --gpu-memory-utilization 0.90` |
| 思考モード | 使わない（`enable_thinking` を指定しない）。応答遅延を抑えるため |

モデルサイズは GPU メモリで決める（BF16 の目安、vLLM 公式レシピより）。

| GPU メモリ | モデル | 備考 |
|---|---|---|
| 80GB（A100/H100 等） | `google/gemma-4-26B-A4B-it`（MoE、アクティブ 4B） | **推奨**。日本語の会話品質と速度のバランスが良い |
| 40GB 以上 | `google/gemma-4-12B-it` | 12B は stable 版 vLLM 未対応（nightly のみ）のため、使う場合は版を固定して事前検証する |
| 24GB 以上 | `google/gemma-4-E4B-it` | 最小構成。性格の演じ分けの品質はデモ前に要確認 |

**reflection の JSON 出力**: vLLM の構造化出力（`response_format: {"type": "json_schema", ...}`）で Gemma 4 がサポートされている。reflection では §6.3 の JSON スキーマを `json_schema` として渡し、加えてシステムメッセージにも出力形式を書く（公式レシピの推奨）。構造化出力を使っても、必須キー欠落などに備えて §6.3 の検証と1回再試行は残す。

### 8.2 STT: kotoba-whisper on Speaches（GPU サーバー）

| 項目 | 内容 |
|---|---|
| 環境変数 | `STT_BASE_URL`, `STT_MODEL` |
| エンドポイント | `POST {STT_BASE_URL}/audio/transcriptions`（OpenAI 互換、multipart） |
| パラメータ | `language=ja`、`prompt=ふたまる`（ウェイクワードの認識補助、§5.3） |
| サーバー | Speaches（旧 faster-whisper-server）。faster-whisper ベースで OpenAI 互換 API を提供 |
| モデル | `kotoba-tech/kotoba-whisper-v2.0-faster`（kotoba-whisper v2.0 の CTranslate2 変換版、MIT ライセンス、日本語特化） |
| 代替 | Speaches で上記モデルが読めない場合は `vllm serve kotoba-tech/kotoba-whisper-v2.0`（vLLM の Transcriptions API、`vllm[audio]` が必要）、それも不可なら Speaches 標準の `large-v3` 系 Whisper |

### 8.3 TTS: Kotoba TTS v2 on fal.ai（外部 API）

Kotoba TTS v2 は Kotoba Technologies が fal.ai 上で提供するホスト型 API であり、モデルの重みは公開されておらず自前の GPU サーバーには置けない。OpenAI 互換でもないため、`plugins/kotoba_tts.py` に独自の LiveKit TTS プラグインを実装する。

| 項目 | 内容 |
|---|---|
| 環境変数 | `FAL_KEY`, `TTS_SPEAKER_ID`（既定: `female-01`） |
| 接続方式 | WebSocket ストリーミング `wss://fal.run/Kotoba-Technologies/kotoba-tts/v2/tts/ws`（音声を生成しながら base64 チャンクで受信）。一括取得の REST `https://fal.run/kotoba-technologies/kotoba-tts/v2/tts` もある |
| 入力 | `text`（最大 400 文字）、`language`（`ja` のみ）、`speaker_id`（`male-01` / `female-01`） |
| 出力 | 24kHz 16bit mono PCM（ストリーミング既定） |
| 料金 | 入力テキストの UTF-8 1,000 バイトあたり $0.015。日本語は1文字約3バイトのため、30文字の応答で約 $0.0014、1,000 応答で約 $1.4 |

- 1回の応答は 400 文字を超えない（§6.4 の長さ指示で最大 2〜3 文に抑えている）。万一超えた場合は句点で分割して順に送る。
- 会場ネットワークから fal.ai への接続が必要になる。デモ前に会場回線で初回音声までの遅延を測る。
- 代替: fal.ai が使えない・遅い場合に備え、`kotoba_tts.py` の生成関数を差し替えるだけで OpenAI 互換の自前 TTS（例: MioTTS、Kokoro-FastAPI）に切り替えられるようにする（環境変数 `TTS_PROVIDER=kotoba|openai`）。

### 8.4 その他の環境変数
- `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`（LiveKit Cloud）
- `FUTAMARU_DB_PATH`（既定: `./data/futamaru.db`）
- `WAKE_WORDS`（カンマ区切り、既定は §5.3）
- `SLEEP_AFTER_SECONDS`（既定: 60）
- `LLM_TIMEOUT` / `STT_TIMEOUT` / `TTS_TIMEOUT`（秒、既定: 15 / 10 / 10）
- `TTS_PROVIDER`（`kotoba` | `openai`、既定: `kotoba`）。`openai` のときは `TTS_BASE_URL`, `TTS_MODEL`, `TTS_VOICE` を使い、`POST {TTS_BASE_URL}/audio/speech` に `{"model","input","voice","response_format":"pcm"}` を送る

## 9. ESP32 側への要件（ハード担当との取り決め）

ESP32 ファームウェアは LiveKit 公式の ESP32 SDK（`livekit/client-sdk-esp32`）で作る前提とする。この SDK は ESP32-S3 に対応し、Opus 音声の双方向送受信、エコーキャンセル（AEC）、RPC メソッドの登録（エージェントから呼び出される側）を備えている。ただし現在は Developer Preview で API が変わる可能性があるため、使う版を固定する。

エージェントが正しく動くために、ESP32 ファームウェアに以下を求める。

1. 起動時に Wi-Fi に接続し、書き込まれたトークンで `plant-<plant_id>` ルームに参加する。切断時は自動で再接続する。
2. マイク音声を常時 publish する（ウェイクワードはサーバー側で検出するため）。
3. エージェントの音声トラックを subscribe してスピーカーで再生する。
4. **エコーキャンセル（AEC）を有効にする**（SDK の AEC 機能を使う）。植物自身の声をマイクで拾うと、割り込みやウェイクワードの誤検出が起きるため。
5. RPC メソッド `set_expression` を登録し（`livekit_room_rpc_register`）、§7.1 の8種の表情を表示する。未知の表情名は `neutral` として表示する。
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

## 12. 前提の調査結果（2026-11-13 時点）

| # | 確認した前提 | 結果 | 仕様への反映 |
|---|---|---|---|
| 1 | LiveKit ESP32 SDK が RPC と AEC に対応しているか | **対応**。ESP32-S3 / P4 対応、Opus 双方向、AEC、RPC メソッド登録あり。Developer Preview（API 変更の可能性あり）。既知の問題として「リモート参加者の退出で切断される場合がある」 | §9。表情はエージェントからの RPC 呼び出しで送る方式のまま |
| 2a | kotoba TTS を OpenAI 互換で自前ホストできるか | **不可**。Kotoba TTS v2 は fal.ai 上のホスト型 API のみで、重みは非公開。独自の REST / WebSocket 形式 | §8.3。独自 TTS プラグインを実装し、OpenAI 互換の自前 TTS への切り替え口も用意 |
| 2b | Whisper を OpenAI 互換で立てられるか | **可能**。Speaches（faster-whisper ベース）や vLLM の Transcriptions API が OpenAI 互換。日本語特化の `kotoba-whisper-v2.0-faster`（CTranslate2 版、MIT）がある | §8.2 |
| 3 | vLLM の Gemma 4 で JSON 出力が使えるか | **使える**。vLLM ≥ 0.19 で Gemma 4 をネイティブ対応。公式レシピで `response_format` の `json_schema` による構造化出力がサポートされている | §8.1、§6.3 |

### 実装の最初に実機で確かめること
- Speaches が `kotoba-tech/kotoba-whisper-v2.0-faster` を読み込めるか（読めなければ §8.2 の代替）
- Kotoba TTS v2（fal.ai）の初回音声までの遅延。会話のテンポを保つ目安は 1 秒以内
- ESP32 SDK の AEC を有効にした状態で、植物の発話中に割り込み・ウェイクワードの誤検出が起きないか
- ESP32 SDK の既知の問題（参加者の退出で切断）が、エージェントの再ディスパッチ時に影響しないか

### 参考
- LiveKit ESP32 SDK: https://github.com/livekit/client-sdk-esp32
- Kotoba TTS v2（fal.ai）: https://fal.ai/models/kotoba-technologies/kotoba-tts/v2/tts/llms.txt
- kotoba-whisper v2.0 faster: https://huggingface.co/kotoba-tech/kotoba-whisper-v2.0-faster
- Speaches: https://speaches-ai.github.io/speaches
- vLLM Speech-to-Text: https://docs.vllm.ai/en/v0.32.0/serving/online_serving/speech_to_text/
- vLLM Gemma 4 レシピ: https://docs.vllm.ai/projects/recipes/en/latest/Google/Gemma4.html
- vLLM Gemma 4 発表: https://vllm.ai/blog/2026-04-02-gemma4
- vLLM 構造化出力: https://docs.vllm.ai/en/latest/features/structured_outputs/
