# AGENTS.md — AI コーディングツール共通のルール

このリポジトリでは Claude Code・ChatGPT（Codex）など複数の AI ツールを使います。
**どのツールを使っても同じ書き方になるよう、ルールはこのファイル1つにまとめます。**
`CLAUDE.md` はこのファイルを読み込むだけです。ルールを変えるときはこのファイルを PR で直してください。

ChatGPT の Web 画面など、このファイルを自動で読まないツールを使うときは、最初にこのファイルの内容を貼り付けてください。

## プロジェクト

- 「ふたまる」: 育て方で個性が変わる、話せる観葉植物のサーバー側音声 AI エージェント（Python / LiveKit Agents）
- 仕様: `docs/superpowers/specs/2026-11-13-futamaru-agent-design.md`。**仕様と違う動きにするときは、先に仕様書を直す**
- チームのルール（ブランチ・PR・秘密情報・モデルを動かす PC）: `CONTRIBUTING.md`

| 場所 | 中身 |
|---|---|
| `agent/main.py` | エントリーポイント（LiveKit のセッション組み立て） |
| `agent/config.py` | 環境変数の読み込み |
| `agent/plant/` | 植物のロジック（ウェイクワード・会話・記憶・性格・プロンプト）。LiveKit に依存させない |
| `agent/plugins/` | LLM / STT / TTS の OpenAI 互換クライアントと、気象庁の天気の取得 |
| `agent/relay/` | 中継。鉢（ESP32）と WebSocket で音声をやりとりし、鉢の代わりに LiveKit のルームに入る（`python -m agent.relay`） |
| `firmware/` | 鉢の ESP32 のファームウェア（C、ESP-IDF）。ビルドは `firmware/README.md` |
| `scripts/` | ダッシュボード・成長シミュレーション・人の一覧と方言の固定・LLM / STT の確認などの道具 |
| `tests/` | `agent/` と同じ構成でテストを置く。偽物は `tests/fakes.py` |

## コマンド

```bash
uv sync                    # 依存のインストール
uv run pytest              # テスト（CI と同じ）
uvx ruff format .          # 整形
uvx ruff check --fix .     # lint（import の並び替え含む）
```

**作業の最後に必ず `uvx ruff format . && uvx ruff check . && uv run pytest` を通す。** CI でも同じものを確認します。

## コードの書き方

- Python 3.12。`agent/`・`scripts/` の各ファイルの先頭は `from __future__ import annotations`
- 型ヒントを付ける。`X | None`、`list[str]` の書き方（`Optional` / `List` は使わない）
- 設定値は `@dataclass(frozen=True)`。環境変数は `agent/config.py` 経由でだけ読む。新しい環境変数は `.env.example` に値なしで足す
- ログは `logging.getLogger(__name__)`。`print` はスクリプト（`scripts/`）でだけ使う
- 時刻・LLM など外から来るものは引数で受け取り、テストで偽物に差し替えられるようにする
- **docstring・コメントは日本語**。モジュールの docstring には対応する仕様の節（例: `（仕様 §5）`）を書く
- **チームには初学者が多いので、コメントは多めに書く。** 初めて読む人が、コードを上から読んで説明できることを目指す
  - 関数・クラスには docstring を付け、「何をするか」「引数と戻り値」を書く。入力と結果の例があるとよい
  - 処理が何段階かに分かれるときは、`# 1. …` `# 2. …` のように手順ごとにコメントを書く
  - 「なぜそうしたか」（別のやり方を選ばなかった理由、Issue 番号）も書く
  - Python やライブラリの基本の書き方（`for` 文の意味など）までは説明しなくてよい
- 新しいライブラリを足すときは `uv add <名前>` を使い、PR に理由を書く。`uv.lock` もコミットする

## テスト

- 振る舞いを変えたら、テストを先に書くか一緒に直す。テストのない変更は出さない
- テスト関数名は英語の snake_case で、何を確かめるかがわかる名前にする（例: `test_empty_optional_values_are_unset`）
- 外部サービス（LLM・STT・TTS・LiveKit）はテストで呼ばない。`tests/fakes.py` の偽物を使う
- 非同期テストは `async def` で書くだけでよい（`asyncio_mode = "auto"`）

## Git と PR

- ブランチ: `feature/…` `fix/…` `docs/…`。コミット: `feat:` `fix:` `docs:` `test:` `chore:` から始める（英語で短く）
- **AI は main に push しない・PR をマージしない**。PR の作成までを行い、人間が確認してマージする
- **PR の本文は必ずテンプレート（`.github/pull_request_template.md`）を使う**。自分で別の形式を作らない
  - 見出しはすべて残し、順番も変えない。書くことがない見出しは「なし」と書く
  - 「イシュー番号」に `close #番号` を書く。「確認したこと」には実行したコマンドと結果（例: `147 passed`）を書く。「確認しました」だけで終わらせない
  - 「AIの利用」は「使用あり」にチェックし、使ったツールと任せた範囲を書く。**「人間が確認したこと」のチェックは AI が付けない**（人間が付ける）
  - `gh pr create` を使うときは、テンプレートを埋めたファイルを `--body-file` で渡す（`--fill` はテンプレートを使わないので使わない）
- 1つの PR では1つのことだけをする。ついでのリファクタリングや整形は別の PR にする

## やってはいけないこと

- `.env`・API キー・トークン・`data/`（会話ログ）をコミットしたり、コードやログに書いたりしない
- 頼まれていないファイルの書き換え・大きな作り直しをしない
- テストを消したり `skip` にしたりして CI を通さない
