"""Docker の起動時に、TTS（AivisSpeech Engine）に声のモデルを用意させる（docs/deploy.md、compose.yaml の tts-init）。

なぜ必要か:
    AivisSpeech Engine の Docker イメージには、声のモデルが 1 つも入っていない。
    ふたまるの声（らせつん・まおなど）は AivisHub で配られているので、起動するたびに
    「入っていなければ入れる」をここで行う。入れたモデルはボリュームに残るので、2 回目からはダウンロードしない。

やること（上から順に）:
    0. TTS_PROVIDER が aivis でなければ、何もせずに終わる（声に OpenAI を使うときは AivisSpeech が要らないため）
    1. AivisSpeech Engine が起動するまで待つ（GET /version）
    2. AIVIS_MODELS に書いたモデルを 1 つずつ確かめ、入っていなければ AivisHub の URL から入れる
       （GET /aivm_models/<UUID> が 404 なら、POST /aivm_models/install）
    3. AIVIS_SPEAKER（声のスタイル ID）が、入っている声の中にあるか確かめる。無ければ失敗にする
       （ID を間違えたまま起動すると、植物が毎回声を出せずに黙ってしまうため）
    4. 短い文を一度声にさせて、モデルを読み込ませておく。最初の返事が読み込み待ちで遅れないように

終了コード: 0 なら成功（agent が起動する）、1 なら失敗。

コンテナの python:3.12-slim で動かすので、Python の標準ライブラリだけで書いている（追加のインストール不要）。
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

# AivisSpeech Engine が起動するまで、何回・何秒おきに試すか（5 秒 × 120 回 = 最大 10 分）
DEFAULT_ATTEMPTS = 120
DEFAULT_DELAY = 5.0
# モデル 1 つのダウンロードとインストールを待つ最大秒数（1 つ数百 MB）
INSTALL_TIMEOUT = 900
# 最初の音声合成（モデルの読み込みを含む）を待つ最大秒数
WARM_UP_TIMEOUT = 300
# AivisHub からモデル（AIVMX 形式）をダウンロードする URL。{uuid} はモデルの UUID
AIVISHUB_DOWNLOAD_URL = (
    "https://api.aivis-project.com/v1/aivm-models/{uuid}/download?model_type=AIVMX"
)
# 読み込み確認に読ませる文
WARM_UP_TEXT = "ふたまるです。"


def _request(
    url: str,
    *,
    method: str = "GET",
    body: bytes | None = None,
    content_type: str | None = None,
    timeout: float = 10,
) -> tuple[int, bytes]:
    """HTTP で呼んで (応答コード, 本文) を返す。4xx・5xx も例外にせず、コードとして返す。

    つながらない（まだ起動していないなど）ときは OSError をそのまま投げる。
    """
    req = urllib.request.Request(url, data=body, method=method)
    if content_type:
        req.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _multipart(fields: dict[str, str]) -> tuple[bytes, str]:
    """フォームの値を multipart/form-data の本文にする（/aivm_models/install はこの形しか受け付けない）。

    戻り値: (本文, Content-Type ヘッダーの値)
    """
    boundary = uuid.uuid4().hex
    parts = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'
        for name, value in fields.items()
    ]
    parts.append(f"--{boundary}--\r\n")
    return "".join(parts).encode(), f"multipart/form-data; boundary={boundary}"


def parse_models(raw: str) -> list[str]:
    """AIVIS_MODELS（カンマ区切りの UUID）を一覧にする。空白と空の項目は捨てる。

    例: " 9f36ec0d-…, a59cb814-…," → ["9f36ec0d-…", "a59cb814-…"]
    """
    return [m.strip() for m in raw.split(",") if m.strip()]


def style_ids(speakers_json: bytes) -> set[int]:
    """GET /speakers の応答から、入っている声のスタイル ID をすべて集める。

    応答の形: [{"name": "らせつん", "styles": [{"name": "標準", "id": 893625024}, …]}, …]
    """
    return {style["id"] for speaker in json.loads(speakers_json) for style in speaker["styles"]}


def run(
    base_url: str,
    models: list[str],
    speaker: int,
    attempts: int = DEFAULT_ATTEMPTS,
    delay: float = DEFAULT_DELAY,
    download_url: str = AIVISHUB_DOWNLOAD_URL,
) -> int:
    """声のモデルを入れて、読み込ませる。成功なら 0、失敗なら 1 を返す。

    引数:
        base_url: AivisSpeech Engine の URL（例: "http://tts:10101"）
        models: 入れておくモデルの UUID の一覧（AivisHub のモデルのページの URL にある ID）
        speaker: 植物が使う声のスタイル ID（例: 893625024 =「らせつん / 標準」）
        attempts: AivisSpeech Engine が応答するまで試す最大回数
        delay: 試す間隔（秒）
        download_url: モデルをダウンロードする URL の形（テストで差し替える）
    """
    # 1. AivisSpeech Engine が起動するまで待つ
    for attempt in range(1, attempts + 1):
        try:
            code, _ = _request(f"{base_url}/version")
        except OSError as e:
            print(f"tts-init: AivisSpeech Engine の起動を待っています（{attempt}/{attempts}）: {e}")
            time.sleep(delay)
            continue
        if code == 200:
            break
        print(f"tts-init: AivisSpeech Engine が準備中です（{code}、{attempt}/{attempts}）")
        time.sleep(delay)
    else:
        print(
            "tts-init: AivisSpeech Engine が応答しませんでした。docker compose logs tts を確認してください"
        )
        return 1

    # 2. モデルを 1 つずつ確かめ、入っていなければ AivisHub から入れる
    for model in models:
        code, _ = _request(f"{base_url}/aivm_models/{model}")
        if code == 200:
            print(f"tts-init: モデル {model} はもう入っています")
            continue
        print(
            f"tts-init: モデル {model} を AivisHub からダウンロードして入れます（数分かかります）"
        )
        body, content_type = _multipart({"url": download_url.format(uuid=model)})
        code, text = _request(
            f"{base_url}/aivm_models/install",
            method="POST",
            body=body,
            content_type=content_type,
            timeout=INSTALL_TIMEOUT,
        )
        if not 200 <= code < 300:
            print(
                f"tts-init: モデル {model} を入れられませんでした（{code}）: {text[:300]!r}\n"
                "  .env の AIVIS_MODELS の UUID と、この PC からインターネットにつながるかを確認してください"
            )
            return 1
        print(f"tts-init: モデル {model} を入れました")

    # 3. 植物が使う声（スタイル ID）が、入っている声の中にあるか確かめる
    code, text = _request(f"{base_url}/speakers")
    if code != 200:
        print(f"tts-init: 声の一覧を取れませんでした（{code}）")
        return 1
    available = style_ids(text)
    if speaker not in available:
        print(
            f"tts-init: 声のスタイル ID {speaker} が見つかりません。入っている ID: {sorted(available)}\n"
            "  .env の AIVIS_SPEAKER と AIVIS_MODELS を確認してください"
        )
        return 1

    # 4. 短い文を一度声にさせて、モデルを読み込ませる（最初の返事が遅れないように）
    params = urllib.parse.urlencode({"speaker": speaker, "text": WARM_UP_TEXT})
    code, query = _request(
        f"{base_url}/audio_query?{params}", method="POST", timeout=WARM_UP_TIMEOUT
    )
    if code == 200:
        code, _ = _request(
            f"{base_url}/synthesis?speaker={speaker}",
            method="POST",
            body=query,
            content_type="application/json",
            timeout=WARM_UP_TIMEOUT,
        )
    if code != 200:
        print(
            f"tts-init: 声 {speaker} で読み上げられませんでした（{code}）。docker compose logs tts を確認してください"
        )
        return 1
    print(f"tts-init: 声 {speaker} を読み込みました。準備完了")
    return 0


def main(env: dict[str, str]) -> int:
    """環境変数を読んで run() を呼ぶ。TTS_PROVIDER が aivis でなければ、何もせずに成功で終わる。"""
    if env.get("TTS_PROVIDER", "").strip().lower() != "aivis":
        print("tts-init: TTS_PROVIDER が aivis ではないので、AivisSpeech の準備はしません")
        return 0
    return run(
        env.get("AIVIS_URL", "http://tts:10101"),
        parse_models(env.get("AIVIS_MODELS", "")),
        int(env["AIVIS_SPEAKER"]),
    )


if __name__ == "__main__":
    sys.exit(main(dict(os.environ)))
