# チーム開発のルール

3人で迷わず・壊さずに進めるための決めごとです。変えたいときは PR で提案してください。

## 1. ブランチとコミット

- main に直接 push しない。作業は必ずブランチを切る
- ブランチ名: `feature/<内容>`（機能）、`fix/<内容>`（修正）、`docs/<内容>`（文書）
  - 例: `feature/wake-honorifics`、`fix/punctuation-only-transcript`
- コミットメッセージは種類から始める: `feat:` / `fix:` / `docs:` / `test:` / `chore:`
  - 例: `fix: ignore punctuation-only transcripts`
- マージ後もブランチは消さずに残す

## 2. PR とレビュー

- PR は `.github/pull_request_template.md` の項目を埋める（「AIの利用」欄も）
- 本文に `close #番号` を書いて Issue と紐づける
- **自分以外の1人**が確認したらマージしてよい
- **AI（Claude など）はマージしない**。PR の作成までを行い、人間が確認してマージする
- レビューを頼まれたら、その日のうちに一度は目を通す

## 3. main を壊さない

- PR を出すと GitHub Actions が自動でテスト（`uv run pytest`）を回す
- **テストが落ちている PR はマージしない**
- PR では整形と lint（ruff）も確認する。手元でも push 前に `uvx ruff format . && uvx ruff check . && uv run pytest` を回す

## 4. AI ツールの使い方

- Claude Code・ChatGPT（Codex）など、使う AI ツールは自由
- **コーディングのルールは [`AGENTS.md`](AGENTS.md) に一本化**。Codex は自動で読み、Claude Code は `CLAUDE.md` 経由で読む
- ChatGPT の Web 画面など自動で読まないツールでは、最初に `AGENTS.md` を貼り付ける
- ルールを変えたいときは `AGENTS.md` を PR で直す（`CLAUDE.md` には書き足さない）

## 5. 秘密情報

- `.env`、APIキー、LiveKit のトークン、`data/`（会話ログの DB）は**コミットしない**（`.gitignore` 済み）
- 新しい環境変数を足したら、`.env.example` に**値なし**で書き足す
- キー本体はチームの Discord の DM で渡す。Issue・PR・コミットには書かない
- ログやエラーを Issue に貼るときは、キーが含まれていないか確認する

## 6. 共有リソース

| リソース | 決めごと |
|---|---|
| GPU サーバー | ポートは LLM（vLLM）が `8000`、STT（Speaches）が `8001`、自前 TTS を使う場合は `8002`。起動・停止・モデル変更をしたらチームの Discord で一言伝える |
| GPU メモリ | LLM と STT で分け合う。`--gpu-memory-utilization` を変えるときは相手の担当に相談する |
| LiveKit Cloud | プロジェクトは1つを共有。株ごとのルームは `plant-<id>` |
| OpenAI | キーは1つを共有。使いすぎに注意（目安は仕様書 §8.3） |

## 7. 進捗と連絡

- やることは必ず Issue にし、担当者（Assignees）を入れる（テンプレート: 作業 / バグ報告）
- **30分詰まったら**チームの Discord で共有する
- 決めたことは Issue か仕様書（`docs/superpowers/specs/`）に残す。チャットだけで終わらせない

## 開発の始め方

```bash
uv sync
cp .env.example .env   # 値はチームの Discord で受け取る
uv run pytest
```

詳しい起動方法は [README](README.md) を参照してください。
