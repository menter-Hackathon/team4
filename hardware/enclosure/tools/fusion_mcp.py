"""Fusion の MCP サーバーを呼んで、筐体のスクリプトを実行する小さな道具（#16）。

Autodesk Fusion に Fusion MCP（Claude Desktop の拡張機能）を入れると、Fusion を起動している間、
http://127.0.0.1:27182/mcp で MCP サーバーが動きます。この道具はそこへ直接 HTTP でつなぎ、
`futamaru_pot.py` を実行して、STL・f3d・画像・ログを `hardware/enclosure/out/` に書き出させます。
Fusion の画面を触らずに、コマンド 1 つで筐体を作り直せます。

使い方（Fusion を起動しておく）:
    python3 hardware/enclosure/tools/fusion_mcp.py build    # 筐体を作り直し、ログの OK 以外の行を表示
    python3 hardware/enclosure/tools/fusion_mcp.py list     # MCP サーバーが持つツールの一覧
    python3 hardware/enclosure/tools/fusion_mcp.py call fusion_mcp_read '{"...": "..."}'

標準ライブラリだけで動くので、`uv sync` は要りません。

MCP の決まりごと（ここでつまずいたので書いておく）:
    1. 最初に `initialize` を送ると、返事のヘッダーに `Mcp-Session-Id` が付いてくる
    2. それ以降の呼び出しには、毎回そのセッション ID をヘッダーに付ける
    3. 返事は JSON のことも、`data: {...}` の形（Server-Sent Events）のこともある
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

# Fusion MCP サーバーの場所。Fusion の拡張機能の既定の値
URL = os.environ.get("FUSION_MCP_URL", "http://127.0.0.1:27182/mcp")
# 筐体を作るのは数分かかるので、待つ時間を長めにしておく（秒）
TIMEOUT = int(os.environ.get("FUSION_MCP_TIMEOUT", "600"))

# このファイルから見た、筐体のフォルダ（hardware/enclosure）
ENCLOSURE_DIR = Path(__file__).resolve().parent.parent
SCRIPT_PATH = ENCLOSURE_DIR / "futamaru_pot" / "futamaru_pot.py"
OUT_DIR = ENCLOSURE_DIR / "out"
# 筐体のスクリプトの中にある、既定の書き出し先の行。MCP で送るときはここを書き換える
DEFAULT_OUT_LINE = '_DEFAULT_OUT = os.path.expanduser("~/Desktop/Lit/team4/hardware/enclosure/out")'


def _post(body: dict, session_id: str | None = None) -> tuple[dict, str | None]:
    """MCP サーバーに JSON-RPC を 1 つ送り、(返事の JSON, 返ってきたセッション ID) を返す。

    返事が `data: {...}` の形（Server-Sent Events）なら、最後の data の行を JSON として読む。
    """
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    req = urllib.request.Request(
        URL, data=json.dumps(body).encode(), headers=headers, method="POST"
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
        raw = res.read().decode()
        new_session_id = res.headers.get("Mcp-Session-Id")
    if raw.startswith(("event:", "data:")):
        data_lines = [line[5:].strip() for line in raw.splitlines() if line.startswith("data:")]
        raw = data_lines[-1] if data_lines else "{}"
    return (json.loads(raw) if raw.strip() else {}), new_session_id


def _open_session() -> str:
    """MCP のセッションを始め、セッション ID を返す（initialize → initialized の 2 段階）。"""
    # 1. initialize: サーバーにこちらの情報を伝える。返事のヘッダーにセッション ID が来る
    _, session_id = _post(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "futamaru-enclosure", "version": "1"},
            },
        }
    )
    if not session_id:
        raise RuntimeError("MCP サーバーがセッション ID を返しませんでした")
    # 2. initialized: 準備ができたことを知らせる（返事は無い）
    _post({"jsonrpc": "2.0", "method": "notifications/initialized"}, session_id)
    return session_id


def call(method: str, params: dict) -> dict:
    """MCP のメソッドを 1 回呼び、返事の JSON を返す。

    例: call("tools/list", {}) → {"result": {"tools": [...]}, ...}
    """
    session_id = _open_session()
    res, _ = _post({"jsonrpc": "2.0", "id": 2, "method": method, "params": params}, session_id)
    return res


def build_script_source(script_path: Path = SCRIPT_PATH, out_dir: Path = OUT_DIR) -> str:
    """MCP で送るために、筐体のスクリプトを少し書き換えた文字列を返す。

    1. `from __future__ import annotations` を外す。MCP 側でスクリプトが関数に包まれるので、
       この行がファイルの先頭でなくなり、Python がエラーにするため
    2. 書き出し先を、このリポジトリの `hardware/enclosure/out` にする。MCP から送ると
       `__file__` が無く、スクリプトは既定の場所（`~/Desktop/Lit/team4/...`）に書き出してしまうため
    """
    source = script_path.read_text(encoding="utf-8")
    source = source.replace("from __future__ import annotations\n", "", 1)
    if DEFAULT_OUT_LINE not in source:
        raise RuntimeError(f"{script_path} に既定の書き出し先の行が見つかりません")
    return source.replace(DEFAULT_OUT_LINE, f"_DEFAULT_OUT = {str(out_dir)!r}")


def build() -> int:
    """筐体を作り直し、ログのうち OK 以外の行（失敗・寸法・干渉チェック）を表示する。

    戻り値: 失敗（FAIL / FATAL）が無ければ 0、あれば 1。コマンドの終了コードに使う
    """
    # 1. Fusion でスクリプトを実行する（数分かかる）
    res = call(
        "tools/call",
        {
            "name": "fusion_mcp_execute",
            "arguments": {"featureType": "script", "object": {"script": build_script_source()}},
        },
    )
    print(json.dumps(res.get("result", res), ensure_ascii=False, indent=1)[:3000])

    # 2. スクリプトが書いたログを読む。OK の行は多いので省き、確認が要る行だけ出す
    log_path = OUT_DIR / "build_log.txt"
    lines = log_path.read_text(encoding="utf-8").splitlines()
    for line in lines:
        if not line.startswith("OK"):
            print(line)
    fails = [line for line in lines if line.startswith(("FAIL", "FATAL"))]
    print(f"手順 {len(lines)} 件、失敗 {len(fails)} 件（ログ: {log_path}）")
    return 1 if fails else 0


def main(argv: list[str]) -> int:
    """コマンドラインの入り口。使い方はファイルの先頭の docstring を参照。"""
    if len(argv) < 2 or argv[1] not in ("build", "list", "call"):
        print(__doc__)
        return 2
    if argv[1] == "build":
        return build()
    if argv[1] == "list":
        res = call("tools/list", {})
        for tool in res.get("result", {}).get("tools", []):
            print("##", tool["name"])
            print(tool.get("description", "")[:2000])
            print()
        return 0
    # call <ツール名> '<引数の JSON>'。引数を @ファイル名 にすると、その JSON ファイルを読む
    args_text = argv[3] if len(argv) > 3 else "{}"
    if args_text.startswith("@"):
        args = json.loads(Path(args_text[1:]).read_text(encoding="utf-8"))
    else:
        args = json.loads(args_text)
    res = call("tools/call", {"name": argv[2], "arguments": args})
    print(json.dumps(res.get("result", res), ensure_ascii=False, indent=1)[:20000])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
