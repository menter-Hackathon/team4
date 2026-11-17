# ふたまる 音声AIエージェント 設計書

- 日付: 2026-11-13
- 更新: 2026-11-16 ディスプレイをやめた（表情の表示と `set_expression` RPC をなくした）
- 更新: 2026-11-17 TTS をローカルの AivisSpeech でも動かせるようにした（§8.3、#56）
- 更新: 2026-11-17 性格・成長・記憶を見せるダッシュボードを足した（§6.6、#59）
- 更新: 2026-11-17 性格の段階を 3 段階から 5 段階にした（§6.4、#57）
- 更新: 2026-11-17 ダッシュボードに性格の 16 タイプ（「〇〇ふたまる」）を出す（§6.6、#64）
- 更新: 2026-11-17 「共通の記憶」をやめ、名乗った人にはその人の記憶だけを見せる（§6.3〜§6.6、#70）
- 対象: サーバー側の音声AIエージェントの骨組み（Python / LiveKit Agents）
- 企画: 「ふたまる」— 育て方によって個性が変わる、話せる観葉植物

## 1. 目的と成功基準

### 目的
観葉植物に取り付けた ESP32-S3 デバイスと音声で会話でき、日々の関わり方によって性格が変化するAIエージェントの骨組みを作る。

### ハッカソンでの成功基準
1. 「ふたまる」と呼びかけると植物が起き、実機（ESP32-S3）とリアルタイムに音声会話できる（割り込み対応）
2. 会話内容・ユーザーの好みを長期記憶し、次回以降の会話に反映できる
3. 性格パラメータが会話履歴に応じて変化し、話し方に違いが出る
4. 同じ初期状態の2株を異なる会話履歴で育て、個性の違いをデモできる

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
| LiveKit サーバー | LiveKit Cloud |
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
[ESP32-S3 (植物)] ──WebRTC──> [LiveKit Cloud] <──── [Agent ワーカー (GPU サーバー)]
   マイク(常時送信)            ルーム: plant-<id>         │
   スピーカー                                           │
                                                       ├─> vLLM (Gemma 4)          [GPU サーバー]
                                                       ├─> Speaches (kotoba-whisper) [GPU サーバー]
                                                       ├─> OpenAI TTS (gpt-4o-mini-tts) [外部 API]
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
     "owner_dialect": "kansai"
   }
   ```
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
7. **話し相手の名前**: 相手が決まっていれば「今話している相手は{名前}」を足す。相手が未確定の間は、「まだ相手の名前がわからないので、自然に名前を聞く」を足す（ウェイクワードだけで起こされたときの挨拶も同様）。聞くのは最初の 3 回の発話まで（§6.5）

性格指示（3）と記憶（4）は、相手が決まっていればその人の性格と、その人の記憶の中の上位 10 件を使う。お客さんなら株の性格とお客さんの記憶だけを使う。

### 6.5 話し相手と方言
一つの株に複数の人が話しかけるため、今話している人を見分け、その人ごとの性格・記憶・方言で接する。見分けは名乗り（名前）による。声での見分けはしない。

**前提**
- 一人ずつ話す。1 セッション（起きてから眠るまで）に話し相手は 1 人で、途中で替わらない。替わるときは一度眠らせてから次の人が起こす。
- 方言は標準語・関西弁・博多弁・広島弁の 4 つ（値は `standard` / `kansai` / `hakata` / `hiroshima`）。

**話し相手の見分け**
- 相手が未確定の間、ユーザーの発話ごとに、返事の前に LLM で名前を探す。「ふたまる、たろうだよ」の「たろうだよ」も対象にする。
  - 知っている人（`people`）の一覧を渡し、「一覧のこの人」「新しい人（名前）」「名乗っていない」のどれかを構造化出力（JSON スキーマ）で返させる。STT は「たろう」「太郎」「タロウ」と書き方が揺れるため、文字の一致では比べない。
  - 時間切れは 3.0 秒。切れたら名乗っていない扱いにして返事を優先する。時間切れの回は、下の「最初の 3 回」に数えない（LLM が遅いだけで、名乗った人がお客さんにされないようにするため）。
  - 新しい人なら `people` に登録する（性格は 4 軸とも 0.5 から始める）。
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
- **起こされたとき**: ウェイクワードだけで呼ばれた場合、記憶と前回要約をもとに植物から話し始める（例: 「あ、おかえり！昨日言ってたテスト、どうだった？」）。
  - LLM には「持ち主に呼ばれて起きた。記憶や前回の会話を踏まえて短く話しかける」という指示を追加して生成する。
- **会話中の沈黙**: `AWAKE` 中にユーザーの沈黙が続いたら、睡眠（60秒）の前に1回だけ話題を振る。
  - 待ち時間 = `talkativeness >= 0.3` のとき `10 + (1.0 - talkativeness) * 30` 秒（0.9 → 13秒、0.5 → 25秒、0.3 → 31秒）
  - `talkativeness < 0.3` のときは話題を振らない
  - 話題を振った後もユーザーが話さなければ、60秒ルールで眠る（植物の発話終了時点から数え直す、§5.2）

## 8. モデル API 契約

URL・モデル名はすべて環境変数で指定する。開発時はローカル PC 上のモデルやクラウド API（OpenAI など）に向けて同じ環境変数を設定して動かす。

### 8.1 LLM: Gemma 4 on vLLM（PC）

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
- `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`（LiveKit Cloud）
- `FUTAMARU_DB_PATH`（既定: `./data/futamaru.db`）
- `WAKE_WORDS`（カンマ区切り、既定は §5.3）
- `SLEEP_AFTER_SECONDS`（既定: 60）
- `LLM_TIMEOUT` / `STT_TIMEOUT` / `TTS_TIMEOUT`（秒、既定: 15 / 10 / 10）
- `VAD_MIN_SILENCE_SECONDS` / `ENDPOINTING_DELAY_SECONDS`（秒、既定: 0.55 / 0.5）: 話し終わりの判定の待ち。声が途切れてから、この 2 つを足した時間で返事を作り始める。返事の遅さ（§11 の 1 秒以内）の調整に使う（#39）
- TTS 関連（`OPENAI_API_KEY`, `TTS_MODEL`, `TTS_VOICE`, `TTS_BASE_URL`）は §8.3

## 9. ESP32 側への要件（ハード担当との取り決め）

ESP32 ファームウェアは LiveKit 公式の ESP32 SDK（`livekit/client-sdk-esp32`）で作る前提とする。この SDK は ESP32-S3 に対応し、Opus 音声の双方向送受信とエコーキャンセル（AEC）を備えている。ただし現在は Developer Preview で API が変わる可能性があるため、使う版を固定する。

エージェントが正しく動くために、ESP32 ファームウェアに以下を求める。

1. 起動時に Wi-Fi に接続し、書き込まれたトークンで `plant-<plant_id>` ルームに参加する。切断時は自動で再接続する。
2. マイク音声を常時 publish する（ウェイクワードはサーバー側で検出するため）。
3. エージェントの音声トラックを subscribe してスピーカーで再生する。
4. **エコーキャンセル（AEC）を有効にする**（SDK の AEC 機能を使う）。植物自身の声をマイクで拾うと、割り込みやウェイクワードの誤検出が起きるため。

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
- OpenAI TTS の初回音声までの遅延（会場回線）。会話のテンポを保つ目安は 1 秒以内
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
