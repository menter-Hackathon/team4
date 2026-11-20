"""中継の WebSocket サーバー（仕様 §3.2.1）。

鉢（ESP32）は wss://<ホスト名>/relay につなぐ（Cloudflare Tunnel が、この中継の /relay に届ける）。
1 本の接続の流れ:
  1. 鍵を確かめる（Authorization: Bearer <RELAY_DEVICE_KEY>）。違えば HTTP 401
  2. WebSocket にして、鉢の start を待つ（5 秒）
  3. 同じ株の古い接続があれば閉じる（新しい接続に置き換える）
  4. 鉢の代わりに LiveKit のルームに入り、ready を送る
  5. 鉢のマイクの音 → LiveKit、エージェントの声 → 鉢 を、どちらかが終わるまで続ける
  6. 終わったらルームから出る（エージェントは「ESP32 が出た」として会話を終える）
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from aiohttp import WSMsgType, web

from agent.relay import mulaw
from agent.relay.bridge import BridgeFactory, DeviceBridge
from agent.relay.protocol import (
    CLOSE_NORMAL,
    CLOSE_TRY_AGAIN_LATER,
    CODEC_MULAW,
    ERROR_INVALID_START,
    ERROR_LIVEKIT_UNAVAILABLE,
    FrameChunker,
    ProtocolError,
    StartRequest,
    error_message,
    is_authorized,
    is_silent,
    parse_start,
    ready_message,
)

logger = logging.getLogger(__name__)

START_TIMEOUT = 5.0  # 接続してから start が届くまで待つ秒数
JOIN_TIMEOUT = 15.0  # LiveKit のルームに入るまで待つ秒数
HEARTBEAT = (
    15.0  # この間隔で ping を送る（Cloudflare は 100 秒ほど無通信だと切るため、それより短く）
)
RECEIVE_TIMEOUT = 30.0  # 鉢から何も届かないまま、この秒数がたったら閉じる

# aiohttp の Application に持たせる値のキー
FACTORY_KEY = web.AppKey("bridge_factory", BridgeFactory)
DEVICE_KEY_KEY = web.AppKey("device_key", str)
ACTIVE_KEY = web.AppKey("active", dict)  # plant_id → 今つながっている WebSocket


def create_app(bridge_factory: BridgeFactory, device_key: str) -> web.Application:
    """中継の aiohttp アプリを作る。

    引数:
      bridge_factory: plant_id を受け取り、LiveKit のルームに入った DeviceBridge を返す関数
                      （本物は bridge.livekit_bridge_factory、テストでは偽物）
      device_key: 鉢が送ってくる鍵と比べる値（.env の RELAY_DEVICE_KEY）
    """
    app = web.Application()
    app[FACTORY_KEY] = bridge_factory
    app[DEVICE_KEY_KEY] = device_key
    app[ACTIVE_KEY] = {}
    app.router.add_get("/relay", relay_handler)
    app.router.add_get("/healthz", health_handler)
    return app


async def health_handler(_request: web.Request) -> web.Response:
    """動いているかの確認用（compose・手動の確認で使う）。"""
    return web.Response(text="ok")


async def _close_with_error(ws: web.WebSocketResponse, err: ProtocolError) -> None:
    """エラーを鉢に伝えてから閉じる。鉢がもういなくても気にしない。"""
    with contextlib.suppress(Exception):
        await ws.send_str(error_message(err.code, err.message))
    await ws.close(code=err.close_code, message=err.code.encode())


async def relay_handler(request: web.Request) -> web.StreamResponse:
    """/relay への接続 1 本を最後まで扱う（流れはモジュールの説明を参照）。"""
    app = request.app

    # --- 1. 鍵を確かめる（WebSocket にする前に断る） ---
    if not is_authorized(request.headers.get("Authorization"), app[DEVICE_KEY_KEY]):
        logger.warning("relay: rejected a connection with a wrong key from %s", request.remote)
        raise web.HTTPUnauthorized(text="wrong device key")

    ws = web.WebSocketResponse(heartbeat=HEARTBEAT, receive_timeout=RECEIVE_TIMEOUT)
    await ws.prepare(request)

    # --- 2. start を待つ ---
    try:
        start = await _receive_start(ws)
    except ProtocolError as err:
        logger.warning("relay: bad start (%s)", err.message)
        await _close_with_error(ws, err)
        return ws

    plant_id, codec = start.plant_id, start.codec

    # --- 3. 同じ株の古い接続を閉じる ---
    active: dict[str, web.WebSocketResponse] = app[ACTIVE_KEY]
    old = active.get(plant_id)
    if old is not None and not old.closed:
        logger.info("relay: plant %s reconnected; closing the old connection", plant_id)
        await old.close(code=CLOSE_NORMAL, message=b"replaced")
    active[plant_id] = ws

    try:
        # --- 4. LiveKit のルームに入る ---
        try:
            bridge = await asyncio.wait_for(app[FACTORY_KEY](plant_id), JOIN_TIMEOUT)
        except Exception as e:  # LiveKit が落ちている・キーが違う など
            logger.exception("relay: plant %s could not join LiveKit", plant_id)
            await _close_with_error(
                ws,
                ProtocolError(
                    ERROR_LIVEKIT_UNAVAILABLE, f"LiveKit に入れません: {e}", CLOSE_TRY_AGAIN_LATER
                ),
            )
            return ws

        # --- 5. 音を受け渡す ---
        error: ProtocolError | None = None
        try:
            await ws.send_str(ready_message(codec))
            logger.info("relay: plant %s is ready (codec %s)", plant_id, codec)
            error = await _pump(ws, bridge, codec)
        finally:
            # --- 6. ルームから出る ---
            # 鉢へのエラーの通知より先に出る。WebSocket を閉じるときは鉢の返事を最大 10 秒待つので、
            # その間エージェントに「鉢がまだいる」と思わせないため
            await bridge.close()
        if error is not None:
            await _close_with_error(ws, error)
    finally:
        if active.get(plant_id) is ws:
            del active[plant_id]
        if not ws.closed:
            await ws.close()
        logger.info("relay: plant %s disconnected", plant_id)
    return ws


async def _receive_start(ws: web.WebSocketResponse) -> StartRequest:
    """最初のメッセージ（start）を START_TIMEOUT 秒まで待ち、その中身（株と codec）を返す。"""
    try:
        msg = await ws.receive(timeout=START_TIMEOUT)
    except TimeoutError as e:
        raise ProtocolError(ERROR_INVALID_START, "start が届きませんでした") from e
    if msg.type != WSMsgType.TEXT:
        raise ProtocolError(ERROR_INVALID_START, '最初に {"type":"start"} を送ってください')
    return parse_start(msg.data)


async def _pump(
    ws: web.WebSocketResponse, bridge: DeviceBridge, codec: str
) -> ProtocolError | None:
    """鉢 → LiveKit と LiveKit → 鉢 を同時に動かし、どちらかが終わったら両方止める。

    codec: 鉢との間の音の送り方（protocol.CODEC_PCM16 か CODEC_MULAW）

    戻り値: 鉢から決まりに合わないものが届いて止まったときは、その ProtocolError（鉢に伝える）。
            ふつうに終わったときは None。
    """
    to_room = asyncio.create_task(_device_to_room(ws, bridge, codec))
    to_device = asyncio.create_task(_room_to_device(ws, bridge, codec))
    done, pending = await asyncio.wait({to_room, to_device}, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    for task in pending:
        with contextlib.suppress(asyncio.CancelledError):
            await task
    # 終わった側の例外（音の長さがおかしい など）を確かめる
    for task in done:
        err = task.exception()
        if isinstance(err, ProtocolError):
            return err
        if err is not None:
            logger.error("relay: audio pump failed", exc_info=err)
    return None


async def _device_to_room(ws: web.WebSocketResponse, bridge: DeviceBridge, codec: str) -> None:
    """鉢から届いた音を 16 ビットの PCM に戻し、640 バイトずつに区切り直して LiveKit に流す。

    μ-law なら先に展開する（1 バイト → 2 バイト）。接続が閉じたら終わる。
    """
    chunker = FrameChunker()
    while True:
        try:
            msg = await ws.receive()
        except TimeoutError:
            logger.warning("relay: nothing from the device for %.0f s; closing", RECEIVE_TIMEOUT)
            return
        if msg.type == WSMsgType.BINARY:
            pcm = mulaw.decode(msg.data) if codec == CODEC_MULAW else msg.data
            for frame in chunker.push(pcm):
                await bridge.push_mic(frame)
        elif msg.type == WSMsgType.TEXT:
            # 今のプロトコルでは start 以降のテキストは使わない。将来の合図のために無視する
            logger.debug("relay: ignored text from device: %s", msg.data[:100])
        else:  # CLOSE・CLOSING・CLOSED・ERROR
            return


async def _room_to_device(ws: web.WebSocketResponse, bridge: DeviceBridge, codec: str) -> None:
    """エージェントの声を鉢へ送る。無音のフレームは送らない（仕様 §3.2.1）。

    μ-law なら 640 バイトの PCM を 320 バイトに圧縮してから送る。
    """
    async for frame in bridge.plant_audio():
        if ws.closed:
            return
        if is_silent(frame):
            continue
        await ws.send_bytes(mulaw.encode(frame) if codec == CODEC_MULAW else frame)
