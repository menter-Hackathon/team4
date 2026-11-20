"""Docker の起動時に、STT（Speaches）に音声認識のモデルを用意させる（docs/deploy.md、compose.yaml の stt-init）。

やること:
    1. モデルのダウンロードを頼む（POST /v1/models/<モデル名>）。Speaches が起動するまで待ちながら再試行する
    2. 1 秒の無音を一度だけ文字起こしさせる（POST /v1/audio/transcriptions）
       Speaches はモデルを「最初の文字起こしのとき」に GPU へ読み込む。ここで先に読み込ませておくと、
       植物への最初の「ふたまる」が読み込み待ちで時間切れになるのを防げる。
       読み込みに失敗するモデルなら、ここでエラーにして agent を起動させない（植物が黙ったままになるのを防ぐ）

終了コード: 0 なら成功（agent が起動する）、1 なら失敗。

Speaches のイメージの中で、Python の標準ライブラリだけで動くように書いている（追加のインストール不要）。
"""

from __future__ import annotations

import io
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
import wave

# Speaches が起動するまで、何回・何秒おきに試すか（5 秒 × 120 回 = 最大 10 分）
DEFAULT_ATTEMPTS = 120
DEFAULT_DELAY = 5.0
# モデルのダウンロード（数百 MB〜1GB 程度）を待つ最大秒数
DOWNLOAD_TIMEOUT = 1800
# 最初の文字起こし（GPU への読み込みを含む）を待つ最大秒数
WARM_UP_TIMEOUT = 300


def silent_wav(seconds: float = 1.0, rate: int = 16000) -> bytes:
    """無音の WAV（16kHz・モノラル・16bit）を作る。読み込み確認の文字起こしに使う。"""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)  # 16bit = 2 バイト
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(seconds * rate))
    return buf.getvalue()


def _post(url: str, body: bytes, content_type: str | None, timeout: float) -> int:
    """POST して応答コードを返す。4xx・5xx も例外にせずコードとして返す。"""
    req = urllib.request.Request(url, data=body, method="POST")
    if content_type:
        req.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code


def _multipart(fields: dict[str, str], file_name: str, file_bytes: bytes) -> tuple[bytes, str]:
    """フォームの値と 1 つのファイルを、multipart/form-data の本文にする（OpenAI 互換の文字起こし API の形）。"""
    boundary = uuid.uuid4().hex
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        )
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{file_name}"\r\n'
        "Content-Type: audio/wav\r\n\r\n".encode()
        + file_bytes
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def run(
    base_url: str, model: str, attempts: int = DEFAULT_ATTEMPTS, delay: float = DEFAULT_DELAY
) -> int:
    """モデルのダウンロードと読み込みを行う。成功なら 0、失敗なら 1 を返す。

    引数:
        base_url: Speaches の URL（例: "http://stt:8000"）
        model: 音声認識のモデル名（例: "kotoba-tech/kotoba-whisper-v2.0-faster"）
        attempts: Speaches が応答するまで試す最大回数
        delay: 試す間隔（秒）
    """
    # 1. ダウンロードを頼む。つながらない・5xx（起動中）なら待って再試行、4xx（モデル名の誤りなど）はすぐ失敗
    for attempt in range(1, attempts + 1):
        try:
            code = _post(f"{base_url}/v1/models/{model}", b"", None, DOWNLOAD_TIMEOUT)
        except OSError as e:  # まだ起動していない（接続拒否など）
            print(f"stt-init: Speaches の起動を待っています（{attempt}/{attempts}）: {e}")
            time.sleep(delay)
            continue
        if code in (200, 201):  # 200: ダウンロードした、201: もうあった
            print(f"stt-init: モデル {model} を用意しました（{code}）")
            break
        if 400 <= code < 500:
            print(
                f"stt-init: モデル {model} をダウンロードできません（{code}）。.env の DOCKER_STT_MODEL を確認してください"
            )
            return 1
        print(f"stt-init: Speaches が準備中です（{code}、{attempt}/{attempts}）")
        time.sleep(delay)
    else:
        print("stt-init: Speaches が応答しませんでした。docker compose logs stt を確認してください")
        return 1

    # 2. 無音を一度文字起こしさせて、モデルを GPU に読み込ませる
    body, content_type = _multipart({"model": model, "language": "ja"}, "silence.wav", silent_wav())
    try:
        code = _post(f"{base_url}/v1/audio/transcriptions", body, content_type, WARM_UP_TIMEOUT)
    except OSError as e:
        print(f"stt-init: 読み込み確認の文字起こしに失敗しました: {e}")
        return 1
    if code != 200:
        print(
            f"stt-init: モデル {model} を読み込めません（{code}）。docker compose logs stt を確認してください"
        )
        return 1
    print(f"stt-init: モデル {model} を読み込みました。準備完了")
    return 0


if __name__ == "__main__":
    sys.exit(run(os.environ.get("STT_URL", "http://stt:8000"), os.environ["STT_MODEL"]))
