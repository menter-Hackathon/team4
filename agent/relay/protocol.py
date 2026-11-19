"""中継（relay）のプロトコルの決まりごと（仕様 §3.2.1）。

鉢（ESP32）と中継は、1 本の WebSocket で次のものをやりとりする。
  - テキスト: JSON の合図（start・ready・error）
  - バイナリ: 16kHz・モノラルの音（20ms ずつ）。start の codec で形を選ぶ
      pcm16: 16 ビットの PCM（20ms = 640 バイト）
      mulaw: μ-law で圧縮（20ms = 320 バイト。送る量が半分。2026-11-19 に追加）

このファイルは LiveKit にも WebSocket のライブラリにも依存しない。
決まりごとだけを関数にして、テストで確かめやすくしている。
"""

from __future__ import annotations

import array
import hmac
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass

# 音の形式（鉢・中継・LiveKit で同じにする）
SAMPLE_RATE = 16000
NUM_CHANNELS = 1
BYTES_PER_SAMPLE = 2  # 16 ビット
FRAME_MS = 20
SAMPLES_PER_FRAME = SAMPLE_RATE * FRAME_MS // 1000  # 320 サンプル
FRAME_BYTES = SAMPLES_PER_FRAME * BYTES_PER_SAMPLE * NUM_CHANNELS  # 640 バイト

PROTOCOL_VERSION = 1

# 音の送り方（start の codec）。鉢と中継の間だけの話で、LiveKit には 16 ビットの PCM で流す
CODEC_PCM16 = "pcm16"  # 16 ビットの PCM のまま（1 サンプル 2 バイト）
CODEC_MULAW = "mulaw"  # μ-law で圧縮（1 サンプル 1 バイト。送る量が半分。agent/relay/mulaw.py）
# codec ごとの、20ms 分のバイト数（ready で鉢に伝える）
CODEC_FRAME_BYTES = {CODEC_PCM16: FRAME_BYTES, CODEC_MULAW: SAMPLES_PER_FRAME}

# これ以下の振幅しかないフレームは無音として鉢へ送らない（16 ビットの最大は 32767。8 は聞こえない大きさ）
SILENCE_PEAK = 8

# plant_id に使える文字（英小文字・数字・_・-、1〜32 文字）。ルーム名 plant-<id> に入るので絞る
PLANT_ID_PATTERN = re.compile(r"[a-z0-9_-]{1,32}")

# WebSocket を閉じるときのコード（RFC 6455）
CLOSE_NORMAL = 1000  # ふつうに終わった・新しい接続に置き換えた
CLOSE_POLICY = 1008  # 決まりに合わない（start が無い・音の長さがおかしい）
CLOSE_TRY_AGAIN_LATER = 1013  # 今は無理なので、あとでつなぎ直してほしい（LiveKit に入れない）

# エラーのコード（{"type":"error","code":...} の code）
ERROR_INVALID_START = "invalid_start"
ERROR_INVALID_AUDIO = "invalid_audio"
ERROR_LIVEKIT_UNAVAILABLE = "livekit_unavailable"


class ProtocolError(Exception):
    """鉢から届いたものが決まりに合わない。code はエラーのコード、close_code は閉じるコード。"""

    def __init__(self, code: str, message: str, close_code: int = CLOSE_POLICY):
        super().__init__(message)
        self.code = code
        self.message = message
        self.close_code = close_code


def is_authorized(header: str | None, device_key: str) -> bool:
    """Authorization ヘッダーが「Bearer <鍵>」で、鍵が正しいかを返す。

    例: is_authorized("Bearer abc", "abc") → True、is_authorized(None, "abc") → False
    鍵の比べ方は hmac.compare_digest を使う（かかる時間から鍵を当てられないようにするため）。
    """
    if not header or not header.startswith("Bearer "):
        return False
    given = header[len("Bearer ") :].strip()
    return hmac.compare_digest(given.encode(), device_key.encode())


@dataclass(frozen=True)
class StartRequest:
    """鉢の start に書かれていたこと。"""

    plant_id: str  # どの株か（ルーム plant-<plant_id> に入る）
    codec: str  # 音の送り方（CODEC_PCM16 か CODEC_MULAW）


def parse_start(text: str) -> StartRequest:
    """鉢の最初のメッセージ（start）を読む。決まりに合わなければ ProtocolError。

    例: parse_start('{"type":"start","version":1,"plant_id":"a1"}')
          → StartRequest(plant_id="a1", codec="pcm16")
        parse_start('{"type":"start","version":1,"plant_id":"a1","codec":"mulaw"}')
          → StartRequest(plant_id="a1", codec="mulaw")
    """
    try:
        msg = json.loads(text)
    except json.JSONDecodeError as e:
        raise ProtocolError(ERROR_INVALID_START, "start が JSON ではありません") from e
    if not isinstance(msg, dict) or msg.get("type") != "start":
        raise ProtocolError(ERROR_INVALID_START, '最初に {"type":"start"} を送ってください')
    if msg.get("version") != PROTOCOL_VERSION:
        raise ProtocolError(
            ERROR_INVALID_START,
            f"version は {PROTOCOL_VERSION} にしてください: {msg.get('version')}",
        )
    plant_id = msg.get("plant_id")
    if not isinstance(plant_id, str) or not PLANT_ID_PATTERN.fullmatch(plant_id):
        raise ProtocolError(
            ERROR_INVALID_START, "plant_id は英小文字・数字・_・- の 1〜32 文字にしてください"
        )
    # codec は書かなくてもよい（最初の版のファームは書かない）。書かなければ圧縮しない PCM
    codec = msg.get("codec", CODEC_PCM16)
    if codec not in CODEC_FRAME_BYTES:
        raise ProtocolError(
            ERROR_INVALID_START,
            f"codec は {' か '.join(CODEC_FRAME_BYTES)} にしてください: {codec}",
        )
    return StartRequest(plant_id=plant_id, codec=codec)


def ready_message(codec: str = CODEC_PCM16) -> str:
    """LiveKit のルームに入れたときに鉢へ送る合図（ready）。使う codec と 20ms の大きさを伝える。

    例: ready_message("mulaw") → {"type":"ready","sample_rate":16000,"codec":"mulaw","frame_bytes":320}
    """
    return json.dumps(
        {
            "type": "ready",
            "sample_rate": SAMPLE_RATE,
            "codec": codec,
            "frame_bytes": CODEC_FRAME_BYTES[codec],
        },
        separators=(",", ":"),
    )


def error_message(code: str, message: str) -> str:
    """閉じる前に鉢へ送るエラー。例: error_message("invalid_audio", "...")"""
    return json.dumps(
        {"type": "error", "code": code, "message": message},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def is_silent(frame: bytes) -> bool:
    """フレームが無音か（どのサンプルも振幅が SILENCE_PEAK 以下か）を返す。無音は鉢へ送らない。

    エージェントが黙っている間も、LiveKit からは振幅 1〜2 のごく小さな音が 20ms ごとに届く
    （2026-11-19 の ohsawa の通し試験）。これを全部送ると、無言の間も 32KB/秒 を流し続けるので、
    聞こえないほど小さい音は無音として扱う。例: 全部 0 → True、1 つでも 1000 → False
    """
    samples = array.array(
        "h", frame
    )  # 16 ビットの符号付き整数として読む（リトルエンディアンの PC 前提）
    return max(samples, default=0) <= SILENCE_PEAK and min(samples, default=0) >= -SILENCE_PEAK


class FrameChunker:
    """届いた PCM を、ちょうど 640 バイト（20ms）ずつに区切り直す。

    鉢から届くバイナリの長さはまちまちでよい（仕様 §3.2.1）。余りは次に届いた分とつなげる。
    例: 1000 バイト届く → 640 バイトを 1 つ返し、360 バイトを残す。
        続けて 300 バイト届く → 660 バイトになるので 640 バイトを 1 つ返し、20 バイトを残す。
    """

    def __init__(self, frame_bytes: int = FRAME_BYTES):
        self._frame_bytes = frame_bytes
        self._buf = bytearray()

    def push(self, data: bytes) -> Iterator[bytes]:
        """data を足し、区切れたフレームを順に返す。長さが奇数なら ProtocolError。"""
        if len(data) % BYTES_PER_SAMPLE != 0:
            # 16 ビットの音なので、2 バイトの倍数でないと 1 サンプルが途中で切れている
            raise ProtocolError(
                ERROR_INVALID_AUDIO, "音のデータの長さが 2 バイトの倍数ではありません"
            )
        self._buf.extend(data)
        while len(self._buf) >= self._frame_bytes:
            frame = bytes(self._buf[: self._frame_bytes])
            del self._buf[: self._frame_bytes]
            yield frame
