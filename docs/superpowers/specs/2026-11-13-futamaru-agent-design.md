# ふたまる 音声AIエージェント 設計書

- 日付: 2026-11-13
- 更新: 2026-11-16 ディスプレイをやめた（表情の表示と `set_expression` RPC をなくした）
- 更新: 2026-11-17 TTS をローカルの AivisSpeech でも動かせるようにした（§8.3、#56）
- 更新: 2026-11-17 性格・成長・記憶を見せるダッシュボードを足した（§6.6、#59）
- 更新: 2026-11-17 性格の段階を 3 段階から 5 段階にした（§6.4、#57）
- 更新: 2026-11-17 ダッシュボードに性格の 16 タイプ（「〇〇ふたまる」）を出す（§6.6、#64）
- 更新: 2026-11-17 「共通の記憶」をやめ、名乗った人にはその人の記憶だけを見せる（§6.3〜§6.6、#70）
- 更新: 2026-11-18 登録済みの人だけにする設定を足した（§6.5、#72）
- 更新: 2026-11-18 植物の状態（天気・湿度・水やり・日数・種類）を話すようにする方針を足した（§1、§6.7、§7.2）。返事の速さの目標を 3 秒くらいにした（§11）
- 対象: サーバー側の音声AIエージェントの骨組み（Python / LiveKit Agents）
- 企画: 「ふたまる」— 育て方によって個性が変わる、話せる観葉植物

## 1. 目的と成功基準

### 目的
観葉植物に取り付けた ESP32-S3 デバイスと音声で会話でき、日々の関わり方によって性格が変化するAIエージェントの骨組みを作る。

AI スピーカーとの違いは、**AI が本物の植物の通訳になる**ことにする（2026-11-18、メンター・チームの FB から）。植物に話しかけるのは、その植物を気にかけるときである。そこで植物は、天気（気温・湿度）、水をもらってからの日数、育った日数、その種類の育て方をもとに、自分の状態を話す（§6.7）。人の近況を聞くだけの会話にはしない。

### ハッカソンでの成功基準
1. 「ふたまる」と呼びかけると植物が起き、実機（ESP32-S3）とリアルタイムに音声会話できる（割り込み対応）
2. 会話内容・ユーザーの好みを長期記憶し、次回以降の会話に反映できる
3. 性格パラメータが会話履歴に応じて変化し、話し方に違いが出る
4. 同じ初期状態の2株を異なる会話履歴で育て、個性の違いをデモできる
5. 植物が天気・水やり・日数に合わせて自分の状態を話し、発表で「1日目 → 5日目 → 30日目」の変化を声で見せられる（§6.7）

### 範囲外
- ESP32 ファームウェア、3Dプリント筐体（ただし ESP32 側に求める要件は §9 で定義する）
- 各 PC へのモデルの導入作業（ただし API 契約は §8 で定義する）
- 話者識別（1株を誰が話しかけても同一ユーザーとして扱う）
- 眠っている間の自発的な発話、時刻ベースのスケジュール発話
- ディスプレイによる表情の表示（目は筐体の造形で表す。2026-11-16 決定、§2）

## 2. 決定事項

| 項目 | 決定 |
|---|---|
| 音声エージェントフレームワーク | LiveKit Agents (Python) |
| LiveKit サーバー | 自前の LiveKit サーバー（`livekit/livekit-server`）。脳と同じ PC の Docker の中だけで動かす（2026-11-19 決定。それまでは LiveKit Cloud） |
| 鉢と脳のつなぎ方 | 鉢は WebSocket で音声（PCM）を送り合う。脳の PC の中継（relay）が鉢の代わりに LiveKit のルームに入る。鉢から中継までは Cloudflare Tunnel を通す（§3.2、2026-11-19 決定） |
| LLM | Gemma 4 E4B（`google/gemma-4-E4B-it`）。チームの誰かの PC 上で OpenAI 互換 API として動かす（vLLM ≥ 0.19 など、§8.1） |
| STT | kotoba-whisper v2.0（誰かの PC 上の Speaches、OpenAI 互換 API） |
| TTS | ローカルの AivisSpeech Engine（GPU の PC 上、2026-11-17 #56）。`.env` の `TTS_PROVIDER` で OpenAI TTS（`gpt-4o-mini-tts`）にも戻せる |
| VAD | Silero VAD（エージェント内で実行） |
| エージェント実行場所 | 誰かの PC（デモでは決めた 1 台）。オンライン開催で共有の GPU サーバーは無い。鉢の中はマイコン・マイク・スピーカーだけにする（#23） |
| 個体の単位 | 1デバイス = 1株。`plant_id` で識別 |
| 植物の名前 | 全株共通で「ふたまる」（ウェイクワードと同じ） |
| 起動方法 | ウェイクワード「ふたまる」。サーバー側で文字起こし結果から検出 |
| 会話の区切り | 最後のユーザー発話から 60 秒無言で終了（眠る） |
| 個性の表現 | 数値パラメータ（4軸） + 長期記憶 |
| デバイスの役割 | 話すだけ（マイクとスピーカー）。ディスプレイは付けず、目は筐体の造形で表す。実装の手間を減らすため、2026-11-16 にハード担当で決定 |
| 永続化 | SQLite（WAL モード） |
| 時刻 | すべて JST（`+09:00` 付き ISO 8601 文字列）で保存 |
| パッケージ管理 | uv |

## 3. 全体構成

```
[ESP32-S3 (植物)] ──WebSocket(PCM)──> [Cloudflare Tunnel] ──> [GPU サーバーの Docker]
   マイク(常時送信)                                            ├─ 中継 relay（鉢の代わりにルームに入る）
   スピーカー                                                  │      │ WebRTC（ルーム: plant-<id>）
                                                              ├─ LiveKit サーバー
                                                              │      │
                                                              └─ Agent ワーカー
                                                                     ├─> Ollama (Gemma 4)
                                                                     ├─> Speaches (kotoba-whisper)
                                                                     ├─> AivisSpeech（声）
                                                                     └─> SQLite (性格・記憶)
```

### 3.1 接続とディスパッチ
- 鉢（ESP32）は LiveKit には直接入らない。中継（relay、§3.2）が鉢の代わりに、次の名前でルームに入る。トークンは中継がその場で作る（`scripts/issue_token.py` は、LiveKit に直接入る機器を試すときに使う）。
  - identity: `device-<plant_id>`
  - ルーム名: `plant-<plant_id>`
  - 権限: ルーム参加、音声の publish / subscribe
- エージェントは LiveKit サーバーの自動ディスパッチ（新しいルームすべてに参加）を使う。名前を付けずに登録したワーカーに配る仕組みで、LiveKit Cloud でも自前のサーバーでも同じように動く。
- 鉢と PC は同じ LAN に置かなくてよい。鉢はインターネットにつながれば、Cloudflare Tunnel を通って中継に届く（§3.2）。
- エージェントはルーム名が `plant-` で始まらない場合は即座に退出する。
- ルーム名から `plant_id` を取り出し、その株の性格・記憶をロードする。該当する `plant_id` が DB に無ければ、初期状態（全パラメータ 0.5）で新規作成する。
- 1つのワーカープロセスが複数の株（ルーム）を同時に扱う。DB は SQLite を WAL モードで使い、書き込みは株ごとに独立した行に対して行う。

### 3.2 鉢と脳のつなぎ方: WebSocket の中継（relay）（2026-11-19）
- 理由
  - 脳の PC（ohsawa）は大学のネットワークの中にあり、外から入る通信を受けられない。鉢と同じ LAN にも置けない。
  - LiveKit の音声（WebRTC）は UDP なので、Cloudflare Tunnel を通らない（#3）。
  - WebSocket（TCP）なら Tunnel を通る。チームの別のプロジェクト（monban）が、XIAO から WebSocket で PCM を送る方式で、Tunnel 越しに動いている。
- 構成
  - 中継は `agent/relay/`（`python -m agent.relay`）。compose の `relay` サービス。
  - 鉢 1 台につき、中継が LiveKit のルーム `plant-<plant_id>` に `device-<plant_id>` として入る。鉢のマイクの音を publish し、エージェントの声を subscribe して鉢へ送る。
  - エージェントから見ると、今までと同じ「ESP32 がルームにいる」状態になる。エージェントのコードは変えない。
  - LiveKit サーバーは compose の中だけで使い、PC の外には公開しない。
  - Cloudflare Tunnel（compose の `tunnel`）が、公開の URL（`https://<ホスト名>/relay`）を中継（`http://relay:8780`）に届ける。

#### 3.2.1 中継のプロトコル（バージョン 1）
- 接続: `wss://<ホスト名>/relay`。HTTP ヘッダー `Authorization: Bearer <RELAY_DEVICE_KEY>` を付ける。
  - キーは `.env` の `RELAY_DEVICE_KEY`（全株で共通、24 文字以上）。違えば接続を断る（WebSocket にする前に HTTP 401）。
- 音の形式: 16kHz・16 ビット・モノラルの PCM（リトルエンディアン）。1 フレーム = 20ms = 320 サンプル = **640 バイト**。
- 手順
  1. 鉢 → 中継（テキスト）: 接続から 5 秒以内に `{"type":"start","version":1,"plant_id":"a1"}`
     - `plant_id` は英小文字・数字・`_`・`-` の 1〜32 文字。
  2. 中継 → 鉢（テキスト）: LiveKit のルームに入れたら `{"type":"ready","sample_rate":16000,"frame_bytes":640}`
  3. 鉢 → 中継（バイナリ）: マイクの音を送り続ける。長さは 2 バイトの倍数なら何でもよい（640 バイトずつを勧める）。中継が 20ms ずつに区切り直して LiveKit に流す。
  4. 中継 → 鉢（バイナリ）: エージェントの声を 640 バイトずつ、届いた順に送る。無音のフレーム（どのサンプルも振幅 8 以下）は送らない。鉢は届いた分を順に再生し、足りないときは無音を鳴らす。
- エラー: 中継は `{"type":"error","code":"<コード>","message":"<説明>"}` を送ってから閉じる。
  - `invalid_start`（start が来ない・形が違う、閉じるコード 1008）
  - `invalid_audio`（バイナリの長さが奇数、1008）
  - `livekit_unavailable`（ルームに入れない、1013 = あとでやり直してほしい）
- 生きているかの確認: 中継は 15 秒ごとに WebSocket の ping を送る。鉢から 30 秒何も届かなければ閉じる。
- 同じ株の鉢が新しくつないだら、古い接続を閉じる（閉じるコード 1000、`replaced`）。
- 接続が閉じたら、中継はルームから出る。エージェントは今までどおり「ESP32 が出た」として会話を終える（§3.1、#5）。
- 鉢は接続が切れたら、5 秒から 60 秒まで間を倍に延ばしながら、つなぎ直す。

#### 3.2.2 エコー（植物が自分の声を聞いてしまう）
- 鉢の中で消す（ESP32 の AEC）。スピーカーに出した音を参照にして、マイクの音から引く。中継とエージェントは何もしない。

## 4. ディレクトリ構成

```
agent/
  main.py              エントリポイント。AgentSession の組み立て、イベント配線
  config.py            環境変数の読み込み（各 API の URL、DB パス、タイムアウト等）
  plugins/
    whisper_stt.py     STT の生成（LiveKit の OpenAI 互換 STT プラグインに base_url を渡す薄いラッパー）
    openai_tts.py      TTS の生成（LiveKit の OpenAI TTS プラグインに model / voice / instructions を渡す薄いラッパー）
    aivis_tts.py       TTS: ローカルの AivisSpeech Engine を呼ぶ小さな TTS（§8.3）
    llm.py             会話用 LLM の生成と、reflection 用の JSON 補完関数（OpenAI 互換クライアント）
  plant/
    store.py           SQLite の読み書き（plants / memories / sessions / utterances / trait_history）
    personality.py     性格パラメータの定義・クランプ・更新ルール
    prompt.py          性格 + 記憶 + 前回要約 → システムプロンプト
    reflection.py      会話終了時の記憶抽出・性格変化量の判定（LLM 呼び出し）
    expression.py      表情タグの解析・除去（ストリーミング対応）
    wake.py            ウェイクワード検出・正規化、起きている/眠っている状態の管理
    conversation.py    1株ぶんの会話の進行（起床・記録・睡眠・reflection の起動）。LiveKit 非依存
    timeutil.py        JST の現在時刻と ISO 8601 変換
scripts/
  issue_token.py       ESP32 用の LiveKit トークン発行
  simulate.py          会話ログを流し込んで成長を早送りするデモ用スクリプト
  aivis_voices.py      AivisSpeech の声の一覧とサンプル作り（声を選ぶ用、§8.3）
  dashboard.py         性格・成長・記憶を見せるダッシュボード（§6.6）。dashboard.html と組
tests/
```

### 境界のルール
- `plant/` 以下は LiveKit に依存しない純粋なロジックとする。LLM 呼び出しと現在時刻は関数（または小さなインターフェース）として外から注入し、テストでは偽物に差し替える。
- LiveKit に依存するのは `main.py` と `plugins/` のみ。
- 将来、記憶・性格を別 API サーバーとして切り出す場合は `plant/` をそのまま流用できること。

## 5. 起床・会話・睡眠の状態遷移

### 5.1 状態

| 状態 | STT | 応答 | 記録 |
|---|---|---|---|
| `SLEEPING` 眠っている | 動く（VAD で検出した発話のみ） | しない | しない（文字起こし結果は破棄） |
| `AWAKE` 起きている | 動く | する | `utterances` に即時保存 |

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
  - 既定の `WAKE_WORDS`: `ふたまる`, `二丸`, `ふた丸`, `2丸`, `下丸`（環境変数で上書き可能）
    - `下丸` は、STT が「ふたまるくん」を「下丸くん」と書いたことがあるため入れている（2026-11-16、#2）。STT の確認で別の聞き間違いが見つかったら、ここと `.env.example` に足す
- Whisper の認識精度を上げるため、STT リクエストの `prompt` に「ふたまる」を含める。
- ウェイクワードの後ろに続く文（例:「ふたまる、今日ね…」→「今日ね…」）が2文字以上あれば、それを最初のユーザー発話として扱い、そのまま応答する。
- ウェイクワードだけの場合は、植物から話しかける（§7.2）。
- LiveKit Agents 上では、`Agent.on_user_turn_completed` で状態を確認し、`SLEEPING` 中かつウェイクワードを含まない発話は `StopResponse` で応答を止め、チャット履歴にも残さない。

### 5.4 セッションの終了処理
1. `sessions.ended_at` を記録する。
2. reflection（§6.3）を非同期で実行する。reflection の完了を待たずに `SLEEPING` に戻り、次のウェイクワードを受け付ける。
3. 次のセッションの開始時点で reflection が終わっていなければ、更新前の性格・記憶でプロンプトを組み立てる。

## 6. 性格と記憶

### 6.1 性格パラメータ
すべて 0.0〜1.0、初期値 0.5。

| 軸 | 低い | 高い | 主な判断材料 |
|---|---|---|---|
| `talkativeness` おしゃべり度 | 短く静かに返す | 長く話す・自分から話題を振る | 話しかけ頻度・会話の長さ |
| `affection` 甘え度 | 素っ気ない | 甘える・「会いたかった」 | 褒め言葉・優しい言葉の量、話しかける間隔（§6.7） |
| `energy` 元気度 | 落ち着いた口調 | テンション高い・「！」多め | ユーザーの口調・話題の明るさ、水やりの間隔（§6.7） |
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

CREATE TABLE people (            -- 株ごとの話し相手（§6.5）
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  plant_id         TEXT NOT NULL REFERENCES plants(plant_id),
  name             TEXT NOT NULL,
  talkativeness    REAL NOT NULL DEFAULT 0.5,   -- 人ごとの性格（§6.1 と同じ 4 軸）
  affection        REAL NOT NULL DEFAULT 0.5,
  energy           REAL NOT NULL DEFAULT 0.5,
  curiosity        REAL NOT NULL DEFAULT 0.5,
  dialect_override TEXT,                        -- NULL = 自動、値は §6.5 の方言
  created_at       TEXT NOT NULL
);

CREATE TABLE memories (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  plant_id    TEXT NOT NULL REFERENCES plants(plant_id),
  person_id   INTEGER REFERENCES people(id),  -- NULL = お客さん（名乗らなかった人）との記憶
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
  person_id   INTEGER REFERENCES people(id),  -- NULL = お客さん（相手が未確定）
  owner_dialect TEXT,                  -- standard | kansai | hakata | hiroshima | unknown、NULL = 未判定
  reflected   INTEGER NOT NULL DEFAULT 0  -- 0: 未処理, 1: 処理済み
);

CREATE TABLE utterances (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id  INTEGER NOT NULL REFERENCES sessions(id),
  role        TEXT NOT NULL,           -- user | plant
  text        TEXT NOT NULL,           -- 植物側は表情タグ除去後の文
  created_at  TEXT NOT NULL
);

CREATE TABLE waterings (         -- 声で記録した水やり（§6.7）
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  plant_id    TEXT NOT NULL REFERENCES plants(plant_id),
  person_id   INTEGER REFERENCES people(id),  -- NULL = お客さん
  session_id  INTEGER REFERENCES sessions(id),
  watered_at  TEXT NOT NULL
);

CREATE TABLE trait_history (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  plant_id       TEXT NOT NULL REFERENCES plants(plant_id),
  session_id     INTEGER REFERENCES sessions(id),
  person_id      INTEGER REFERENCES people(id),  -- NULL = 株の性格
  talkativeness  REAL NOT NULL,
  affection      REAL NOT NULL,
  energy         REAL NOT NULL,
  curiosity      REAL NOT NULL,
  recorded_at    TEXT NOT NULL
);
```

- 会話ログは `AWAKE` 中の発話ごとに `utterances` へ即時保存する。
- 植物を作成した時点で、初期値の行を `trait_history` に1行入れる（成長グラフの起点）。
- 既存の DB には、起動時に無いテーブル・列だけを足す（§6.5「DB の引き継ぎ」）。

### 6.3 reflection：記憶と性格の更新（`reflection.py` + `personality.py`）
セッション終了時（§5.4）に実行する。

1. 以下を LLM に渡す。
   - そのセッションの発話ログ
   - 既存の記憶（重要度 × 新しさの上位 30 件、`id` 付き）。**そのセッションの相手の記憶だけ**を見せる（お客さんならお客さんの記憶だけ）。ほかの人の記憶も、お客さんとの記憶も見せない（#70）
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
     "trait_deltas": {"talkativeness": 0.02, "affection": 0.03, "energy": -0.01, "curiosity": 0.0},
     "owner_dialect": "kansai",
     "watered": false
   }
   ```
   - `watered` は、持ち主がこの会話でふたまるに水をあげたと言ったときだけ true（§6.7、#81）。
   - 既存の記憶と同じ内容は `new_memories` に入れず、内容が変わった・補強された場合は `updated_memories` で更新するよう指示する。
   - `updated_memories` の `id` が既存の記憶に無ければ無視する。LLM に見せていない記憶（ほかの人の記憶）の `id` が返ってきた場合も無視する。
   - `owner_dialect` はそのセッションの相手の話し方の判定で、`standard` / `kansai` / `hakata` / `hiroshima` / `unknown` のどれか。発話が短い・少なくて判断できないときは `unknown`。`sessions.owner_dialect` に保存する（§6.5）。
3. 各 `trait_deltas` は **±0.05 にクランプ**し、適用後の値は 0.0〜1.0 にクランプする。
4. `talkativeness` は LLM の判定値と、行動統計からの目標値をブレンドする。
   - 行動目標値 `t_behavior = min(1.0, 直近7日の AWAKE 中のユーザー発話数 / 100)`
   - 最終変化量 `delta = clamp(0.5 * llm_delta + 0.5 * (t_behavior - 現在値) * 0.1, -0.05, 0.05)`
   - 人ごとの性格を使うときは、`t_behavior` の発話数もその人との AWAKE 中の発話だけで数える（§6.5）。
5. 1つのトランザクションで、性格値の更新、`memories` の追加・更新、`trait_history` への1行追加、`sessions.summary` と `sessions.owner_dialect` の保存と `reflected = 1` を行う。人ごとの書き分けは以下のとおり。
   - 相手が決まっているセッション: 性格値は `people` のその人の行を更新し、`trait_history` の行にも `person_id` を入れる。新しい記憶はその人の記憶（`memories.person_id`）として保存する
   - お客さんのセッション（`person_id` が NULL）: 性格値は `plants` を更新し、`trait_history.person_id` は NULL。新しい記憶はお客さんの記憶（`person_id` が NULL）として保存する
   - `updated_memories` で書き換えられるのは、そのセッションの相手の記憶だけ（お客さんならお客さんの記憶だけ）。ほかの人の記憶の `id` は無視する
6. LLM の出力が JSON として不正、または必須キーが無い場合は1回だけ再試行し、それでも失敗したら `reflected = 0` のまま残す。
7. ユーザー発話が0件のセッション（ウェイクワードだけで終わった等）は LLM を呼ばず、`reflected = 1` にするだけとする。
8. エージェント起動時に以下を処理する。
   - `ended_at` が無いセッション（クラッシュ等で残ったもの）: 最後の発話時刻（発話が無ければ `started_at`）を `ended_at` にする
   - `reflected = 0` のセッション: reflection を再実行する

### 6.4 プロンプト組み立て（`prompt.py`）
システムプロンプトは以下の順で連結する。

1. **ベース人格**: 「あなたは観葉植物の{name}。持ち主と話し言葉で短く話す。1回の返答は{長さ指示}。」
2. **出力形式**: 「返答の先頭に必ず表情タグを1つ付ける: [neutral] [happy] [sad] [surprised] [love] [sleepy]」
3. **性格指示**: 各軸の値を5段階に変換した指示文（3 段階では変化が話し方に出にくかったため 5 段階にした、#57）
   - 段階の境界: かなり低い 0.0–0.2 / やや低い 0.2 より上–0.4 / ふつう 0.4 より上–0.6 未満 / やや高い 0.6–0.8 未満 / かなり高い 0.8–1.0
   - 真ん中の 0.5 から 0.1 以上離れると「やや」、0.3 以上離れると「かなり」。境目の値は 0.5 から遠いほうの段階に入れる。1 回の会話で動くのは最大 ±0.05（§6.3）なので、初期値から最短 2 回の会話で段階が変わる
   - 小数の誤差（0.6 が 0.5999999… になるなど）で段階がずれないよう、小数 6 桁に丸めてから比べる
   - 例: `affection` かなり低い →「素っ気なく接し、甘えたりはしない」、ふつう →「普通に親しく接する」、かなり高い →「持ち主に甘えて、「会いたかった」など好きな気持ちをよく伝える」
   - `talkativeness` は長さ指示にも反映する（かなり低い: 短い1文、やや低い: 1文、ふつう: 1〜2文、やや高い: 2文くらい、かなり高い: 2〜3文で話題も振る）
   - 4軸×5段階の文言はすべて `prompt.py` 内の定数として持つ
   - 話題を振るまでの待ち時間（§7.2）は段階ではなく値そのもので決めるので、段階の数を変えても変わらない
4. **記憶**: `importance * 新しさ係数` の上位10件を箇条書き
   - 新しさ係数 = `0.5 ** (最終更新からの経過日数 / 7)`（1週間で半減）
5. **前回の会話**: 直近の `reflected = 1` のセッションの `summary`（無ければ省略）。相手が決まっていればその人のセッション、お客さんならお客さんのセッション（`person_id` が NULL）の要約を使う（§6.5）
6. **方言の指示**: 相手の方言（§6.5）が標準語以外なら 1 段落足す（例:「関西弁で話す（例: 〜やで、〜やん、ほんまに）」）。標準語なら足さない。方言ごとの文面と語尾の例は `prompt.py` の定数にする
7. **植物の状態**（§6.7）: 天気・湿度、水をもらってからの日数、育った日数、前回からの日数、種類の育て方を 1 段落にまとめて足す。取れなかった項目は省く
8. **話し相手の名前**: 相手が決まっていれば「今話している相手は{名前}」を足す。相手が未確定の間は、「まだ相手の名前がわからないので、自然に名前を聞く」を足す（ウェイクワードだけで起こされたときの挨拶も同様）。聞くのは最初の 3 回の発話まで（§6.5）

性格指示（3）と記憶（4）は、相手が決まっていればその人の性格と、その人の記憶の中の上位 10 件を使う。お客さんなら株の性格とお客さんの記憶だけを使う。

### 6.5 話し相手と方言
一つの株に複数の人が話しかけるため、今話している人を見分け、その人ごとの性格・記憶・方言で接する。見分けは名乗り（名前）による。声での見分けはしない。

**前提**
- 一人ずつ話す。1 セッション（起きてから眠るまで）に話し相手は 1 人で、途中で替わらない。替わるときは一度眠らせてから次の人が起こす。
- 方言は標準語・関西弁・博多弁・広島弁の 4 つ（値は `standard` / `kansai` / `hakata` / `hiroshima`）。

**話し相手の見分け**
- 相手が未確定の間、ユーザーの発話ごとに、返事の前に LLM で名前を探す。「ふたまる、たろうだよ」の「たろうだよ」も対象にする。
  - 起きている間の発話も、先頭に呼びかけ（ウェイクワードと敬称、§5.3）があれば外してから名前探しに渡す。STT が読点を落として「ふたまる太郎だよ。」と書くと、「ふたまる太郎」が名前として登録されてしまうため（#68）。返事と会話ログにも外した文を使う（眠っているときに起こす発話と同じ。元の文のまま会話の LLM に渡すと、植物が「ふたまる太郎」と呼んでしまうため）。文の途中のウェイクワードは外さない（その前の名前まで消えるため）
  - 起きている間に呼びかけだけ（「ふたまる」「ふたまるちゃん」）を言った場合は、名乗りではないので名前探しをせず、下の「最初の 3 回」にも数えない
  - 名前探しの指示にも「先頭の『ふたまる』は植物への呼びかけで、名前に含めない」と書く
  - 知っている人（`people`）の一覧を渡し、「一覧のこの人」「新しい人（名前）」「名乗っていない」のどれかを構造化出力（JSON スキーマ）で返させる。STT は「たろう」「太郎」「タロウ」と書き方が揺れるため、文字の一致では比べない。
  - 時間切れは 3.0 秒。切れたら名乗っていない扱いにして返事を優先する。時間切れの回は、下の「最初の 3 回」に数えない（LLM が遅いだけで、名乗った人がお客さんにされないようにするため）。
  - 新しい人なら `people` に登録する（性格は 4 軸とも 0.5 から始める）。
  - **登録済みの人だけにする設定**（`ALLOW_NEW_PEOPLE=false`、デモ用、#72）: 新しい人は登録しない。STT が名前を聞き間違えると（「じろう」→「チロ」）、間違った名前の人が作られ、そのセッションの間は直せないため。名前探しの指示を「一覧の人と読みが似ている名前（「ション」と「ショーン」、「サム」と「チャム」）はその人とみなす。誰とも似ていなければ名乗っていない扱い」に替える。LLM が新しい人と答えても受け取らない。名乗っていない扱いになるので、植物はもう一度名前を聞き、最初の 3 回で決まらなければお客さんになる（今までと同じ）。デモでは話す人を `scripts/people.py` で先に登録しておく
  - 相手が決まったあとの名乗り直し（「〇〇じゃなくて△△だよ」で相手を付け替える）は、今はしない。将来の課題（#92）
- 相手が決まったら、その人の性格・記憶・前回の会話・方言でシステムプロンプトを作り直し、TTS の指示も方言に合わせて替える。話題を振るまでの秒数（§7.2）も、その人の性格で決め直す。
- 名前を聞くのは、セッションの最初の 3 回のユーザー発話まで。それでも名乗らなければ「お客さん」として扱い、それ以上は聞かない。お客さんに決まったら、そのセッションでは名前探しもやめる（返事の遅れを増やさないため。途中で名乗っても、そのセッションはお客さんのまま）。
- 名前探しで返事が遅れるのは、相手が確定するまでの最初の数回だけ。

**人ごとに分けるもの**

| もの | 相手が決まっている | お客さん（`person_id` が NULL） |
|---|---|---|
| 性格（4 軸） | `people` のその人の値。振り返りでその人の値だけが動く | 株の性格（`plants`）。お客さんとの振り返りで動く |
| 記憶 | その人の記憶だけ | お客さんの記憶だけ。新しい記憶もお客さんの記憶として保存 |
| 前回の会話 | その人の直近のセッションの要約 | `person_id` が NULL のセッションの直近の要約（名前のわかった人の会話は見せない） |
| 方言 | 下記の決め方 | 標準語 |

- **ある人の記憶は、ほかの人との会話では使わない。** 一人ひとりと別々の関係を育て、人に話されたくないことをほかの人にばらさないため。振り返りで LLM に見せる記憶も、その相手の記憶だけにする（§6.3）。
- **お客さんと話した内容も、名乗った人には見せない**（#70）。以前は「共通の記憶」として全員に見せていたが、記憶は「持ち主は〜」と書かれるので、植物は目の前の人が言ったことだと思い込み、その人が話していない話題（「ピザのこと考えてるんだって？」）を振ってしまった
- 今ある記憶・前回の会話（すべて `person_id` が NULL）は、お客さんの記憶・お客さん側の要約として扱う。今ある性格は株の性格（お客さん用）になる。
- 成長の早送りデモ（`scripts/simulate.py`）は人を使わず、今までどおり株の性格で動く。
- おしゃべり度の行動目標値（直近 7 日の話しかけ回数 ÷ 100）は、相手が決まっていればその人の話しかけ回数で数える。
- セッションの相手は `sessions.person_id` に記録する。

**方言の決め方**
1. `people.dialect_override` があれば、判定に関係なくその方言で話す（手で固定できる。NULL なら自動）。手で固定するコマンド（`scripts/people.py`）で登録する名前も、会話で名乗った名前と同じく、改行を空白にして長さを切ってから保存する。
2. 無ければ、その人との直近 5 回のセッションの `owner_dialect`（`unknown` を除く）の多数決。同数なら新しいほうを優先する。
3. 判定が 1 つも無ければ標準語。

- 判定は振り返り（§6.3）の `owner_dialect` で行い、セッションごとに保存する。
- 話し方への反映は、システムプロンプトの方言の指示（§6.4）と、TTS の指示（`TTS_INSTRUCTIONS`）の後ろに足す方言のイントネーションの指示。標準語なら足さない。TTS 側で違和感があれば TTS の指示だけ外す。AivisSpeech（§8.3）には話し方の指示が無いので、TTS の方言の指示は使わない。

**DB の引き継ぎ**
- 起動時に、無いテーブル（`people`）と無い列（`memories.person_id`・`sessions.person_id`・`sessions.owner_dialect`・`trait_history.person_id`）を足す。今あるデータは消さない。

### 6.6 ダッシュボード（`scripts/dashboard.py`）
2株の個性の違いと成長を、グラフで見せる Web ページ。エージェントを動かす PC で開き、発表では画面共有で見せる（#59）。

**表示するもの**（株ごとにカードを作り、左右に並べる）
- 性格のタイプ: 今の性格の 16 タイプ（「〇〇ふたまる」）のイラスト・名前・ひとこと説明（下の「性格の 16 タイプ」）
- 今の性格（4 軸）のレーダーチャートと、その数値
- 成長曲線: `trait_history` の 4 軸の折れ線。横軸は「初期（株を作った・人を登録した時点）」から振り返り 1 回ごとに 1 点
- 覚えていること: §6.4 と同じ「重要度 × 新しさ係数」の上位 10 件（`prompt.rank_memories` を使う）。種類と重要度も出す
- 前回の会話の要約・話し方（方言）・振り返った会話の回数
- 切り替え: 「お客さん（株の性格）」と、知っている人（`people`）ごとに表示を切り替えられる。人を選ぶと、その人の性格・成長曲線・記憶（その人の記憶だけ）・前回の会話・方言（§6.5 の決め方）を出す。お客さんにはお客さんの記憶だけを出す（§6.5 と同じく、ほかの見え方の記憶は混ぜない）
- 最初に開くタブ: 登録されている人が 1 人だけの株は、その人のタブを開く。それ以外（0 人・2 人以上）はお客さんのタブ。デモでは 1 株に 1 人（a1 はショーン、b1 はちゃむ）が育てるので、開いた直後からその人が育てた結果を見せるため。タブを押したあとは、自動では切り替えない

**動き**
- 起動: `uv run python -m scripts.dashboard --db <DB のパス> --plants <plant_id をカンマ区切り>`（既定の DB は `data/futamaru.db`、ポートは 8765）。`simulate.py` の結果は `--db data/sim.db --plants sample-a,sample-b` で見る
- ページは 3 秒ごとに `/api/state`（JSON）を読み直す。会話して眠らせると、振り返りが終わった数秒後に成長曲線へ点が増える
- DB は読み取り専用（SQLite の `mode=ro`）でリクエストごとに開き直し、書き込まない。エージェントが動いている最中でも開ける
- 会話の記憶が見えるので、`127.0.0.1` でだけ待ち受ける（`0.0.0.0` にはしない）
- グラフは Chart.js（CDN から版を固定して読む）。Python 側は標準ライブラリ（`http.server`・`sqlite3`）だけで、依存は増やさない

**性格の 16 タイプ**（`agent/plant/plant_type.py`、#64）
- 4 軸それぞれの「高い／低い」の組み合わせ（2×2×2×2 = 16 通り）に、植物の名前を付けたタイプ。話しかけ方によって、ふたまるにどんな花が咲いたかとして見せる（例: 🌻 ひまわりふたまる、🌵 サボテンふたまる）
- 決め方
  1. 4 軸とも 5 段階（§6.4）の「ふつう」（0.4 より上・0.6 未満）なら、まだタイプが決まっていない「🌱 ふたば」。最初はみんな 0.5 なのでここから始まり、最短 2 回の会話でタイプが決まる。少しの変化でタイプが入れ替わらないよう、すぐには決めない
  2. 1 つでも「ふつう」を出たら、4 軸それぞれを 0.5 以上（高い）か未満（低い）かで分け、その組み合わせのタイプにする。小数の誤差でずれないよう、小数 6 桁に丸めてから比べる
- 人のタブを選ぶと、その人の性格でのタイプを出す（相手ごとにタイプが違う）。お客さんは株の性格のタイプ
- 使うのはダッシュボードだけ。会話のプロンプトには入れない
- 名前と説明はオリジナル（ラブタイプ16診断・MBTI などの名前は使わない）。一覧は `plant_type.py` の定数で持つ

| おしゃべり | 甘え | 元気 | 好奇心 | タイプ（英語名） |
|---|---|---|---|---|
| 高 | 高 | 高 | 高 | ひまわり（`sunflower`） |
| 高 | 高 | 高 | 低 | チューリップ（`tulip`） |
| 高 | 高 | 低 | 高 | カモミール（`chamomile`） |
| 高 | 高 | 低 | 低 | 藤（`wisteria`） |
| 高 | 低 | 高 | 高 | ミント（`mint`） |
| 高 | 低 | 高 | 低 | トウガラシ（`chili`） |
| 高 | 低 | 低 | 高 | ローズマリー（`rosemary`） |
| 高 | 低 | 低 | 低 | 竹（`bamboo`） |
| 低 | 高 | 高 | 高 | クローバー（`clover`） |
| 低 | 高 | 高 | 低 | デイジー（`daisy`） |
| 低 | 高 | 低 | 高 | すみれ（`violet`） |
| 低 | 高 | 低 | 低 | 月見草（`primrose`） |
| 低 | 低 | 高 | 高 | オジギソウ（`mimosa`） |
| 低 | 低 | 高 | 低 | アザミ（`thistle`） |
| 低 | 低 | 低 | 高 | サボテン（`cactus`） |
| 低 | 低 | 低 | 低 | コケ（`moss`） |

- イラスト: `scripts/dashboard_assets/types/<英語名>.png`（正方形・背景透過）。ダッシュボードは `/types/<英語名>.png` で返す。返すのは 17 タイプ（ふたば＋16）の英語名と一致するファイルだけで、それ以外の名前（`../` を含むものなど）は 404 にする。ファイルがまだ無いタイプは絵文字で出す

### 6.7 植物の状態（世話のきっかけ）
植物に話しかける動機を「植物を気にかけること」にするため、植物が自分の状態を話せるようにする（§1）。どれもハードを足さずに、PC 側だけで作る。天気は #80、水やりは #81、日数は #82、性格への反映と種類は #83。土の水分センサー（#58）は筐体の都合で見送ったので、水やりは声で記録する。

| 状態 | 取り方 | プロンプトに入れる例 | 植物の言い方の例 |
|---|---|---|---|
| 天気・気温・湿度 | 気象庁のホームページの JSON（キー不要、公式に公開された API ではない）。気温・湿度はアメダスの実測値（観測所 `JMA_AMEDAS_STATION`）、空の様子は今日の予報（地域 `JMA_FORECAST_AREA`） | 「今日は晴れ。気温 8℃、湿度 30%（乾燥している）」 | 「今日すごく乾燥してるね…葉っぱがパリパリしそう」 |
| 水をもらってからの日数 | `waterings` の最新の行 | 「最後に水をもらったのは 4 日前。のどが渇いている」 | 「そろそろお水ほしいな…」 |
| 育った日数 | `plants.created_at` | 「育って 30 日目」 | 「きょうで30日目！」 |
| 前回からの日数 | 直近に終わったセッションの `ended_at`。相手が決まっていればその人との会話だけ、未確定・お客さんなら相手を問わず一番最近の会話 | 「この人と話すのは 3 日ぶり」 | 「3日ぶりだね、さみしかった」 |
| 種類の育て方 | `PLANT_SPECIES`（例: ポトス）と、`prompt.py` の定数の育て方メモ（適温・水やりの間隔・湿度の好み） | 「あなたはポトス。寒さと乾燥が苦手」 | 「ポトスは寒いの苦手なんだ」 |

- **天気の取得**: 起きたとき（§5.2）に取り、30 分はキャッシュする。失敗・タイムアウト（2 秒）のときは、前に取れた天気が 3 時間以内ならそれを使い、それより古い・一度も取れていなければ天気の項目を省く。どちらでも会話は止めない。湿度は `WEATHER_DRY_HUMIDITY`（既定 40%）未満で「乾燥」、70% 以上で「じめじめ」と言葉にしてから渡す。気温も「さむい（10℃ 未満）」「あつい（30℃ 以上）」と言葉にする。LLM に数字だけ渡すより、言い方がぶれにくいため
- **天気の言葉**: 気象庁の天気コードの百の位で「晴れ」「くもり」「雨」「雪」にする（「時々」「のち」は使わない）。取れなかった項目（湿度を測っていない観測所など）は省く。気温・湿度の数字は四捨五入して整数で言う。その日に当てはまる体の感じ方だけを添え（さむい→葉がちぢこまる、乾燥→葉がかさかさする など。どれにも当てはまらなければ「過ごしやすい」）、数字は言わず体の感じ方として話すよう指示する。例:「今日の天気は晴れ。気温 8℃、湿度 30%。体の感じ: さむい（葉がちぢこまる）、乾燥している（葉がかさかさする）。」（#80）
- **水やりの記録**: 会話中に「お水あげたよ」などを言われたら、その場で `waterings` に 1 行足し、その返事のプロンプトを「この会話で水をもらった。まだお礼を言っていなければお礼を言う」入りで作り直す（`watering.py`）。判定は文字起こしの文字の並び（「水」＋「あげた／やった／入れた／水やりした」など）で行い、LLM は呼ばない（返事を遅くしないため）。「あげてない」「あげようか」などは数えない。名前探しより先に記録する（その発話で名乗っていても、その人のプロンプトに入るように）。言い換えの取りこぼしは、reflection（§6.3）の JSON に `watered`（true / false）を足して拾い、会話が終わった時刻で記録する。同じセッションで 2 回目以降は足さない
- **のどの渇き**: 水をもらってから `WATER_REMIND_DAYS`（既定 3 日）以上たったら、起きたときの最初の発話で渇きを伝えるよう指示する。一度も水をもらっていなければ、育ち始めた日から数える。渇いていなければ「最後に水をもらったのは今日／きのう／N日前」だけ伝える
- **性格への反映**: reflection（§6.3）の入力に「## 世話の様子」を足し、「前にこの相手と話してから N 日」「この会話で水をもらったか・その前の水やりから N 日」を渡す。日数はこの会話が始まった時刻から数える（振り返りが遅れても変わらないように）。日数だけでなく、コードで判断した言葉を（）で添える（例:「前にこの相手と話してから1日（毎日のように話しかけてもらえている。affection を少し上げてよい）」「最後に水をもらってから2日（水は足りている。energy を下げる理由にはならない）」）。事実だけを渡すと、LLM が「この会話では水をもらっていない」を放っておかれたと読み、こまめに世話された株でも毎回 affection と energy を下げたため。判断: 話しかけの間隔が 1 日以内なら affection を少し上げてよい、3 日以上なら少し下げる。この会話で水をもらったら energy を少し上げてよい、3 日以上もらっていなければ少し下げる。それ以外の理由で世話の様子から下げない。変化量の上限（±0.05）はほかと同じ（#83）
- **種類**: `PLANT_SPECIES`（既定: ポトス）を「植物の状態」の最初の行で伝える（例:「あなたはポトス。寒さ（10℃より下）と乾燥が苦手。…」）。育て方のメモはポトス・モンステラ・パキラ・サンスベリア・ガジュマルの 5 種類を `prompt.py` の定数に持ち、それ以外は名前だけ伝える（#83）
- **成長の早送り**: `scripts/simulate.py` も、会話ログの「お水あげたよ」を水やりとして記録する（#83）
- **日数の数え方**: 時刻ではなく日本時間の日付で数える（夜に話して翌朝話しても「きのうぶり」）。株を作った日を 1 日目とする。5・7・30・50・100 日目は節目として、そのことに触れさせる（#82）
- **デモ**: 前もって育てておく株（#77）は、`plants.created_at` と `waterings` も 30 日分さかのぼって入れ、「30 日目」と話せるようにする。発表では「1 日目 → 5 日目 → 30 日目」の 3 時点で、実際の声を聞かせる

## 7. 会話中の動作

### 7.1 表情（`expression.py`）

デバイスにディスプレイが無いため（§2）、表情はどこにも表示しない。表情タグの仕組み（§6.4 の出力形式と本節の解析）は変えずに残す。表情をログに残せば会話の確認に使えるうえ、仕組みを外すとプロンプト・解析・テストの作り直しが要るため。

**表情一覧**（LLM が返すもの、計6種）: `neutral` `happy` `sad` `surprised` `love` `sleepy`

**表情タグの解析**
- LLM 応答はストリーミングで届くため、先頭から `]` が出るまで（最大 16 文字）バッファする。
  - 先頭が `[` でない場合は、バッファせずそのまま TTS に流し、表情は `neutral` とする。
  - 16 文字以内に `]` が出ない場合は、バッファをそのまま TTS に流し、表情は `neutral` とする。
  - 不明なタグ名は `neutral` とする。
- タグは TTS に渡す前に除去する。
- LLM が 6 種以外を返した場合も `neutral` とする。

**表情の扱い**
- 確定した表情はログ（`expression=<name>`）に残すだけにする。
- ESP32 には送らない。以前の LiveKit RPC `set_expression` は使わない。

### 7.2 植物から話しかける
- **起こされたとき**: ウェイクワードだけで呼ばれた場合、植物から話し始める。**まず植物の状態（§6.7）を話し**、記憶と前回要約はそのあとに使う（例: 「さむい！今日は乾燥してるね…あ、きのう言ってたテスト、どうだった？」）。のどが渇いているときは、それを最初に言う。
  - LLM には「持ち主に呼ばれて起きた。自分の状態（天気・水・日数）を一言伝え、記憶や前回の会話も踏まえて短く話しかける」という指示を追加して生成する。
- **会話中の沈黙**: `AWAKE` 中にユーザーの沈黙が続いたら、睡眠（60秒）の前に1回だけ話題を振る。
  - 待ち時間 = `talkativeness >= 0.3` のとき `10 + (1.0 - talkativeness) * 30` 秒（0.9 → 13秒、0.5 → 25秒、0.3 → 31秒）
  - `talkativeness < 0.3` のときは話題を振らない
  - 話題を振った後もユーザーが話さなければ、60秒ルールで眠る（植物の発話終了時点から数え直す、§5.2）

## 8. モデル API 契約

URL・モデル名はすべて環境変数で指定する。開発時はローカル PC 上のモデルやクラウド API（OpenAI など）に向けて同じ環境変数を設定して動かす。

### 8.1 LLM: Gemma 4 on vLLM（PC）

**更新（2026-11-18、#37）: デモでは Ollama の圧縮版（`gemma4:e4b-it-q4_K_M`）を使う。** デモの PC の GPU が 12GB と 8GB で、下の vLLM（BF16、24GB 以上）が載らないため。Docker の構成は `docs/superpowers/specs/2026-11-16-docker-backend-design.md` の「2026-11-18 の追加」。下の表は検討時の記録として残す。

| 項目 | 内容 |
|---|---|
| 環境変数 | `LLM_BASE_URL`, `LLM_MODEL`, `LLM_REASONING_EFFORT`（任意。Ollama の Gemma 4 では `none` にして思考モードを切る） |
| エンドポイント | `POST {LLM_BASE_URL}/chat/completions`（OpenAI 互換、ストリーミング） |
| vLLM バージョン | 0.19 以上（Gemma 4 のネイティブ実装が入った版） |
| 起動例 | `vllm serve google/gemma-4-E4B-it --max-model-len 32768 --gpu-memory-utilization 0.80`（STT の分を残す） |
| 思考モード | 使わない（`enable_thinking` を指定しない）。応答遅延を抑えるため |

**決定（2026-11-16、#23）: `google/gemma-4-E4B-it` を使う。** 下の表は検討時の参考（BF16 の目安、vLLM 公式レシピより）。NVIDIA の GPU が無い PC では、Ollama・LM Studio などの OpenAI 互換 API で動かす。

| GPU メモリ | モデル | 備考 |
|---|---|---|
| 80GB（A100/H100 等） | `google/gemma-4-26B-A4B-it`（MoE、アクティブ 4B） | 日本語の会話品質と速度のバランスが良いが、GPU サーバーが無いため見送り |
| 40GB 以上 | `google/gemma-4-12B-it` | 12B は stable 版 vLLM 未対応（nightly のみ）のため、使う場合は版を固定して事前検証する |
| 24GB 以上 | `google/gemma-4-E4B-it` | **採用**。性格の演じ分けの品質はデモ前に要確認 |

**reflection の JSON 出力**: vLLM の構造化出力（`response_format: {"type": "json_schema", ...}`）で Gemma 4 がサポートされている。reflection では §6.3 の JSON スキーマを `json_schema` として渡し、加えてシステムメッセージにも出力形式を書く（公式レシピの推奨）。構造化出力を使っても、必須キー欠落などに備えて §6.3 の検証と1回再試行は残す。

### 8.2 STT: kotoba-whisper on Speaches（PC）

| 項目 | 内容 |
|---|---|
| 環境変数 | `STT_BASE_URL`, `STT_MODEL` |
| エンドポイント | `POST {STT_BASE_URL}/audio/transcriptions`（OpenAI 互換、multipart） |
| パラメータ | `language=ja`、`prompt=ふたまる`（ウェイクワードの認識補助、§5.3） |
| サーバー | Speaches（旧 faster-whisper-server）。faster-whisper ベースで OpenAI 互換 API を提供 |
| モデル | `kotoba-tech/kotoba-whisper-v2.0-faster`（kotoba-whisper v2.0 の CTranslate2 変換版、MIT ライセンス、日本語特化） |
| 代替 | Speaches で上記モデルが読めない場合は `vllm serve kotoba-tech/kotoba-whisper-v2.0`（vLLM の Transcriptions API、`vllm[audio]` が必要）、それも不可なら Speaches 標準の `large-v3` 系 Whisper |

### 8.3 TTS: AivisSpeech（ローカル）または OpenAI TTS（外部 API）

`.env` の `TTS_PROVIDER` で選ぶ。`aivis` ならローカルの AivisSpeech Engine、`openai`（既定）なら OpenAI TTS。

**AivisSpeech（`TTS_PROVIDER=aivis`、2026-11-17 #56 で決定）**

OpenAI の声（`coral`）は低く男性っぽく聞こえ、`nova`・`sage` でも「幼い女の子の声」に寄せきれなかった。また会話のたびに外部 API の料金と会場回線への依存がある。そこで、GPU の PC で動かす AivisSpeech Engine（Style-Bert-VITS2 ベースの日本語 TTS、VOICEVOX 互換の API）を使う。

| 項目 | 内容 |
|---|---|
| 環境変数 | `TTS_PROVIDER=aivis`, `AIVIS_BASE_URL`（既定: `http://127.0.0.1:10101`）, `AIVIS_SPEAKER`（声のスタイル ID。既定: `888753762`）, `AIVIS_SPEED`（話す速さ、既定 1.0）, `AIVIS_PITCH`（声の高さ、既定 0.0）。`OPENAI_API_KEY` は要らない |
| エンドポイント | 1. `POST {AIVIS_BASE_URL}/audio_query?text=…&speaker=…` で読み方（JSON）を作る 2. 速さ・高さ・出力の形式（24kHz・モノラル）を書き換える 3. `POST {AIVIS_BASE_URL}/synthesis?speaker=…` に渡して WAV を受け取る |
| 出力 | 24kHz 16bit mono（WAV） |
| 声の選び方 | 声はモデルとスタイルで決まる（OpenAI のような話し方の指示は無い）。`scripts/aivis_voices.py` で入っている声の一覧とスタイル ID を出し、サンプルを作って聞き比べて `AIVIS_SPEAKER` を決める。明るく幼い女の子の声のモデルを選ぶ。高さは `AIVIS_PITCH` で少し上げられる。**2026-11-17 に聞き比べて、「まお」の「あまあま」スタイル（`888753762`）に決めた**（候補はラボPCに入っていた「まお」「コハク」の各スタイル） |
| 声のモデル | 「まお」（制作: オズチャット / Trippy / ねゆたろ、ライセンス: Aivis Common Model License 1.0）。本人・公式と誤解させる使い方や、話者を貶める使い方は禁止されている |
| 方言 | 話し方の指示が無いので、TTS の方言のイントネーションの指示（§6.5）は使わない。方言は LLM の文面（語尾など）だけで出す |
| 料金 | かからない（自分の PC の GPU で動く） |
| 速さ（2026-11-17 実測） | 1 文の合成は 0.2〜0.3 秒（Tailscale 越しでも）。最初の音が出るまで約 0.3 秒 |

- LiveKit のプラグインには AivisSpeech 用が無いので、`agent/plugins/aivis_tts.py` に小さな TTS を作る（文 1 つごとに上の 1〜3 を行う。ストリーミングはしない）。
- エージェントから `AIVIS_BASE_URL` に届く必要がある。AivisSpeech と同じ PC でエージェントを動かすのが簡単。
- AivisSpeech が落ちている・遅いときは、その応答をスキップする（§10 の TTS エラーと同じ）。

**OpenAI TTS（`TTS_PROVIDER=openai`）**

LiveKit 公式の OpenAI プラグイン（`livekit-plugins-openai` の `openai.TTS`）をそのまま使う。独自の TTS プラグインは実装しない。

| 項目 | 内容 |
|---|---|
| 環境変数 | `OPENAI_API_KEY`, `TTS_MODEL`（既定: `gpt-4o-mini-tts`）, `TTS_VOICE`（既定: `coral`）, `TTS_BASE_URL`（任意。未設定なら OpenAI 本家） |
| エンドポイント | `POST {TTS_BASE_URL}/audio/speech`（`{"model","input","voice","instructions","response_format":"pcm"}`、ストリーミング受信） |
| 出力 | 24kHz 16bit mono PCM |
| 声の調子 | `gpt-4o-mini-tts` の `instructions` で「やわらかく、ゆっくり、植物らしいのんびりした話し方」など話し方を指定する。文言は `config.py` の定数とし、性格値では変えない（話し方の違いは LLM の文面で出す） |
| 料金 | 目安 1 分の音声あたり約 $0.015。30 文字程度の応答なら 1,000 応答で数ドル程度 |

- 会場ネットワークから OpenAI API への接続が必要になる。デモ前に会場回線で初回音声までの遅延を測る。
- 代替: OpenAI に繋がらない・遅い場合は、`TTS_BASE_URL` を OpenAI 互換の自前 TTS（例: PC 上の MioTTS）に向けるだけで切り替える。この場合 `instructions` は無視される前提で動くようにする。

### 8.4 その他の環境変数
- `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`（自前の LiveKit サーバー、§3.2。Docker ではエージェントと中継の `LIVEKIT_URL` を compose が `ws://livekit:7880` にする）
- `RELAY_DEVICE_KEY`（鉢が中継につなぐときのキー、§3.2.1）、`RELAY_PORT`（中継が待ち受けるポート、既定 8780）
- `CLOUDFLARE_TUNNEL_TOKEN`（Docker の Cloudflare Tunnel のトークン、§3.2）
- `FUTAMARU_DB_PATH`（既定: `./data/futamaru.db`）
- `WAKE_WORDS`（カンマ区切り、既定は §5.3）
- `SLEEP_AFTER_SECONDS`（既定: 60）
- `LLM_TIMEOUT` / `STT_TIMEOUT` / `TTS_TIMEOUT`（秒、既定: 15 / 10 / 10）
- `VAD_MIN_SILENCE_SECONDS` / `ENDPOINTING_DELAY_SECONDS`（秒、既定: 0.55 / 0.5）: 話し終わりの判定の待ち。声が途切れてから、この 2 つを足した時間で返事を作り始める。返事の遅さ（§11 の 3 秒くらい）の調整に使う（#39）
- `PLANT_SPECIES`（既定: ポトス）、`JMA_AMEDAS_STATION`（アメダスの観測所、5 桁）/ `JMA_FORECAST_AREA`（予報の地域、6 桁）（両方空なら天気を使わない）、`WEATHER_DRY_HUMIDITY`（既定: 40）、`WATER_REMIND_DAYS`（既定: 3）（§6.7）
- TTS 関連（`OPENAI_API_KEY`, `TTS_MODEL`, `TTS_VOICE`, `TTS_BASE_URL`）は §8.3

## 9. ESP32 側への要件（ハード担当との取り決め）

ESP32 ファームウェアは、中継（§3.2）と WebSocket で音声をやりとりする（2026-11-19 に変更。それまでは LiveKit 公式の ESP32 SDK で直接ルームに入る前提だった）。LiveKit の SDK は使わない。

エージェントが正しく動くために、ESP32 ファームウェアに以下を求める。

1. 起動時に Wi-Fi に接続し、書き込まれた URL・キー・`plant_id` で中継につなぐ（§3.2.1 の手順）。切断時は自動で再接続する（5 秒から 60 秒まで間を倍に延ばす）。
2. マイク音声を常時送る（ウェイクワードはサーバー側で検出するため）。
3. 中継から届いた声をスピーカーで再生する。
4. **エコーキャンセル（AEC）を有効にする**（ESP32 の中で、スピーカーに出した音を参照にして消す）。植物自身の声をマイクで拾うと、割り込みやウェイクワードの誤検出が起きるため。

ディスプレイは付けないため（§2）、表情の表示と RPC メソッドの登録は求めない。

## 10. エラー処理

| 状況 | 振る舞い |
|---|---|
| LLM タイムアウト・エラー（会話中） | 定型文「ん…ちょっとぼーっとしてた」を TTS で返す |
| TTS エラー | その応答をスキップ |
| STT エラー | その発話を無視して待機を継続（`SLEEPING` 中はウェイクワード判定もスキップ） |
| reflection 失敗 | `reflected = 0` のまま残し、次回起動時に再処理 |
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
- `care`（§6.7）: 湿度・気温の言葉への変換の境界、水やりキーワードの判定、渇きの日数の境界、天気 API の失敗時に項目を省くこと（偽の HTTP で確認）
- **返事の速さ**: 話し終えてから植物が話し始めるまで 3 秒くらいを目安にする（#39）
- **デモ確認**: `scripts/simulate.py` で2株に異なる会話ログを流し、性格値・プロンプト・応答の違いを確認

## 12. 前提の調査結果（2026-11-13 時点）

| # | 確認した前提 | 結果 | 仕様への反映 |
|---|---|---|---|
| 1 | LiveKit ESP32 SDK が RPC と AEC に対応しているか | **対応**。ESP32-S3 / P4 対応、Opus 双方向、AEC、RPC メソッド登録あり。Developer Preview（API 変更の可能性あり）。既知の問題として「リモート参加者の退出で切断される場合がある」 | §9。表情はエージェントからの RPC 呼び出しで送る方式のまま（2026-11-16 にディスプレイをやめたため、RPC は使わなくなった） |
| 2a | kotoba TTS を OpenAI 互換で自前ホストできるか | **不可**。Kotoba TTS v2 は fal.ai 上のホスト型 API のみで、重みは非公開。なお企画メモの「kotoba」は STT の kotoba-whisper を指していたと確認（2026-11-15） | §8.3。TTS は OpenAI TTS に決定。`TTS_BASE_URL` で OpenAI 互換の自前 TTS へ切り替え可能 |
| 2b | Whisper を OpenAI 互換で立てられるか | **可能**。Speaches（faster-whisper ベース）や vLLM の Transcriptions API が OpenAI 互換。日本語特化の `kotoba-whisper-v2.0-faster`（CTranslate2 版、MIT）がある | §8.2 |
| 3 | vLLM の Gemma 4 で JSON 出力が使えるか | **使える**。vLLM ≥ 0.19 で Gemma 4 をネイティブ対応。公式レシピで `response_format` の `json_schema` による構造化出力がサポートされている | §8.1、§6.3 |

### 実装の最初に実機で確かめること
- Speaches が `kotoba-tech/kotoba-whisper-v2.0-faster` を読み込めるか（読めなければ §8.2 の代替）
- OpenAI TTS の初回音声までの遅延（会場回線）。会話のテンポを保つ目安は 3 秒くらい（植物なので少し間があってよい。2026-11-18 の FB で 1 秒以内から緩めた、#39）
- ESP32 SDK の AEC を有効にした状態で、植物の発話中に割り込み・ウェイクワードの誤検出が起きないか
- ESP32 SDK の既知の問題（参加者の退出で切断）が、エージェントの再ディスパッチ時に影響しないか

### 参考
- LiveKit ESP32 SDK: https://github.com/livekit/client-sdk-esp32
- OpenAI Text to speech: https://platform.openai.com/docs/guides/text-to-speech
- LiveKit OpenAI TTS プラグイン: https://docs.livekit.io/agents/integrations/tts/openai/
- kotoba-whisper v2.0 faster: https://huggingface.co/kotoba-tech/kotoba-whisper-v2.0-faster
- Speaches: https://speaches-ai.github.io/speaches
- vLLM Speech-to-Text: https://docs.vllm.ai/en/v0.32.0/serving/online_serving/speech_to_text/
- vLLM Gemma 4 レシピ: https://docs.vllm.ai/projects/recipes/en/latest/Google/Gemma4.html
- vLLM Gemma 4 発表: https://vllm.ai/blog/2026-04-02-gemma4
- vLLM 構造化出力: https://docs.vllm.ai/en/latest/features/structured_outputs/

## 13. 未決事項（チームで相談中）

| # | 事項 | 現在の仮置き | 決まったら変わる箇所 |
|---|---|---|---|
| 1 | GPU サーバーのメモリ容量 | **決定済み（2026-11-16、#23）**: GPU サーバーは使わず誰かの PC で動かす。モデルは `gemma-4-E4B-it` | §2、§8.1 に反映済み |
