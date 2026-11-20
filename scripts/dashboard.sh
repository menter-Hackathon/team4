#!/usr/bin/env bash
# ふたまるのダッシュボードを起動して、ブラウザで開く（仕様 §6.6）。Mac・WSL のどちらでも動く。
#
# 使い方（team4 のフォルダで）:
#   ./scripts/dashboard.sh        → 30 日目のデモ用 DB（data/demo-day30.db）
#   ./scripts/dashboard.sh 1      → 1 日目（5 / 30 も同じ）
#   ./scripts/dashboard.sh live   → 本番の DB（data/futamaru.db）
# 止めるときは Ctrl+C
#
# 中身は uv run futamaru-dashboard を呼ぶだけ（処理は scripts/dashboard.py にまとめてある）。
# ブラウザの開き方（Mac・Windows・WSL の違い）も dashboard.py の --open が面倒を見る。

set -euo pipefail
# どのフォルダから実行しても、team4 のフォルダで動かす（このファイルの 1 つ上）
cd "$(dirname "$0")/.."

case "${1:-30}" in
  1 | 5 | 30) exec uv run futamaru-dashboard --day "${1:-30}" --open ;;
  live) exec uv run futamaru-dashboard --open ;;
  *)
    echo "使い方: ./scripts/dashboard.sh [1|5|30|live]"
    exit 1
    ;;
esac
