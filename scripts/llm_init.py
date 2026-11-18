"""Docker の起動時に、LLM（Ollama）に Gemma 4 を用意させる（docs/deploy.md、compose.yaml の llm-init）。

なぜ必要か:
    Ollama の Docker イメージには、モデルが 1 つも入っていない。起動するたびに
    「入っていなければダウンロードし、GPU に読み込ませておく」をここで行う。
    ダウンロードしたモデルはボリュームに残るので、2 回目からはダウンロードしない。

やること（上から順に）:
    1. Ollama が起動するまで待つ（GET /api/version）
    2. モデルをダウンロードさせる（POST /api/pull）。もうあれば、すぐに終わる
    3. 空の文で一度呼んで、モデルを GPU に読み込ませる（POST /api/generate、keep_alive=-1 で外さない）
       最初の「ふたまる」が読み込み待ちで遅れないように
    4. どれだけ GPU に載ったかを確かめる（GET /api/ps）。一部しか載っていなければ警告を出す
       （GPU のメモリが足りないと、残りを CPU で計算するので返事が遅くなる。止めはしない）

終了コード: 0 なら成功（agent が起動する）、1 なら失敗。

コンテナの python:3.12-slim で動かすので、Python の標準ライブラリだけで書いている（追加のインストール不要）。
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

# Ollama が起動するまで、何回・何秒おきに試すか（5 秒 × 120 回 = 最大 10 分）
DEFAULT_ATTEMPTS = 120
DEFAULT_DELAY = 5.0
# モデルのダウンロード（約 7GB）を待つ最大秒数
PULL_TIMEOUT = 3600
# GPU への読み込みを待つ最大秒数
LOAD_TIMEOUT = 600


def _request(url: str, *, body: dict | None = None, timeout: float = 10) -> tuple[int, bytes]:
    """HTTP で呼んで (応答コード, 本文) を返す。body があれば JSON で POST する。

    4xx・5xx も例外にせず、コードとして返す。つながらないときは OSError をそのまま投げる。
    """
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if body is not None else "GET")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def gpu_share(ps_json: bytes, model: str) -> float | None:
    """GET /api/ps の応答から、model のうち GPU に載っている割合（0〜1）を返す。読み込まれていなければ None。

    応答の形: {"models": [{"name": "gemma4:e4b-it-q4_K_M", "size": 8000, "size_vram": 8000, …}]}
    例: size 8000・size_vram 6000 → 0.75（4 分の 1 は CPU で計算している）
    """
    for m in json.loads(ps_json).get("models", []):
        if m.get("name") == model or m.get("model") == model:
            size = m.get("size") or 0
            return (m.get("size_vram") or 0) / size if size else None
    return None


def run(
    base_url: str, model: str, attempts: int = DEFAULT_ATTEMPTS, delay: float = DEFAULT_DELAY
) -> int:
    """モデルをダウンロードして GPU に読み込ませる。成功なら 0、失敗なら 1 を返す。

    引数:
        base_url: Ollama の URL（例: "http://llm:11434"）
        model: モデル名（例: "gemma4:e4b-it-q4_K_M"）
        attempts: Ollama が応答するまで試す最大回数
        delay: 試す間隔（秒）
    """
    # 1. Ollama が起動するまで待つ
    for attempt in range(1, attempts + 1):
        try:
            code, _ = _request(f"{base_url}/api/version")
        except OSError as e:
            print(f"llm-init: Ollama の起動を待っています（{attempt}/{attempts}）: {e}")
            time.sleep(delay)
            continue
        if code == 200:
            break
        print(f"llm-init: Ollama が準備中です（{code}、{attempt}/{attempts}）")
        time.sleep(delay)
    else:
        print("llm-init: Ollama が応答しませんでした。docker compose logs llm を確認してください")
        return 1

    # 2. モデルをダウンロードさせる。stream=False なので、終わるまで 1 回の応答を待つ
    print(f"llm-init: モデル {model} を用意します（初回はダウンロードで数分〜十数分かかります）")
    try:
        code, text = _request(
            f"{base_url}/api/pull", body={"model": model, "stream": False}, timeout=PULL_TIMEOUT
        )
    except OSError as e:
        print(
            f"llm-init: ダウンロードが途中で止まりました: {e}。もう一度 docker compose up -d してください"
        )
        return 1
    if code != 200:
        print(
            f"llm-init: モデル {model} をダウンロードできません（{code}）: {text[:300]!r}\n"
            "  .env の DOCKER_LLM_MODEL と、この PC からインターネットにつながるかを確認してください"
        )
        return 1

    # 3. 空の文で一度呼んで、GPU に読み込ませる。keep_alive=-1 で、使っていなくても外さない
    try:
        code, text = _request(
            f"{base_url}/api/generate",
            body={"model": model, "prompt": "", "keep_alive": -1, "stream": False},
            timeout=LOAD_TIMEOUT,
        )
    except OSError as e:
        print(f"llm-init: モデルの読み込みが途中で止まりました: {e}")
        return 1
    if code != 200:
        print(
            f"llm-init: モデル {model} を読み込めません（{code}）: {text[:300]!r}\n"
            "  docker compose logs llm を確認してください（GPU のメモリ不足など）"
        )
        return 1

    # 4. GPU にどれだけ載ったかを確かめる。全部載っていなくても止めない（遅いが動くため）
    code, text = _request(f"{base_url}/api/ps")
    share = gpu_share(text, model) if code == 200 else None
    if share is None:
        print("llm-init: GPU に載った量を確かめられませんでした（動作には影響しません）")
    elif share < 0.99:
        print(
            f"llm-init: 注意: モデルの {share:.0%} しか GPU に載っていません。残りは CPU で計算するので返事が遅くなります。\n"
            "  .env の LLM_GPU が、メモリの大きい GPU（RTX 5070）の番号になっているか確かめてください（nvidia-smi）"
        )
    else:
        print("llm-init: モデルが全部 GPU に載りました")
    print(f"llm-init: モデル {model} を読み込みました。準備完了")
    return 0


if __name__ == "__main__":
    sys.exit(run(os.environ.get("OLLAMA_URL", "http://llm:11434"), os.environ["LLM_MODEL"]))
