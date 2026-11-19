"""中継の WebSocket サーバー（agent/relay/server.py、仕様 §3.2.1）のテスト。

LiveKit には入らず、tests/fakes.py の FakeBridge（ルームに入ったふり）を使う。
"""

import asyncio
import json

from aiohttp import WSMsgType
from aiohttp.test_utils import TestClient, TestServer

from agent.relay.server import create_app
from tests.fakes import FakeBridgeFactory

KEY = "test-device-key-0123456789"
AUTH = {"Authorization": f"Bearer {KEY}"}
START = json.dumps({"type": "start", "version": 1, "plant_id": "a1"})


async def make_client(factory: FakeBridgeFactory) -> TestClient:
    client = TestClient(TestServer(create_app(factory, KEY)))
    await client.start_server()
    return client


async def wait_until(condition, timeout: float = 2.0) -> None:
    """condition() が True になるまで少しずつ待つ（別のタスクの処理を待つため）。"""
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not condition():
        assert loop.time() < end, "timed out"
        await asyncio.sleep(0.01)


async def test_wrong_key_is_rejected_before_websocket():
    client = await make_client(FakeBridgeFactory())
    try:
        resp = await client.get("/relay", headers={"Authorization": "Bearer wrong"})
        assert resp.status == 401
    finally:
        await client.close()


async def test_start_then_ready_and_audio_flows_both_ways():
    factory = FakeBridgeFactory()
    client = await make_client(factory)
    try:
        ws = await client.ws_connect("/relay", headers=AUTH)
        await ws.send_str(START)
        ready = await ws.receive_json(timeout=2)
        assert ready == {
            "type": "ready",
            "sample_rate": 16000,
            "codec": "pcm16",
            "frame_bytes": 640,
        }
        bridge = factory.bridges[0]
        assert bridge.plant_id == "a1"

        # 鉢 → LiveKit: 1000 バイト送ると、640 バイトが 1 フレーム流れる（残りは次とつなぐ）
        await ws.send_bytes(b"\x01\x00" * 500)
        await wait_until(lambda: len(bridge.mic) == 1)
        assert bridge.mic[0] == (b"\x01\x00" * 500)[:640]

        # LiveKit → 鉢: 無音は送らず、声のフレーム（振幅 1000）だけが届く
        bridge.say(bytes(640))
        bridge.say(b"\xe8\x03" * 320)
        msg = await ws.receive(timeout=2)
        assert msg.type == WSMsgType.BINARY and msg.data == b"\xe8\x03" * 320

        await ws.close()
        await wait_until(lambda: bridge.closed)  # 鉢が切れたらルームから出る
    finally:
        await client.close()


async def test_mulaw_audio_is_expanded_for_livekit_and_compressed_for_the_pot():
    from agent.relay import mulaw

    factory = FakeBridgeFactory()
    client = await make_client(factory)
    try:
        ws = await client.ws_connect("/relay", headers=AUTH)
        await ws.send_str(
            json.dumps({"type": "start", "version": 1, "plant_id": "a1", "codec": "mulaw"})
        )
        ready = await ws.receive_json(timeout=2)
        assert ready["codec"] == "mulaw" and ready["frame_bytes"] == 320
        bridge = factory.bridges[0]

        # 鉢 → LiveKit: μ-law の 320 バイト（20ms）が、16 ビットの 640 バイトに戻って流れる
        voice = b"\xe8\x03" * 320  # 振幅 1000 の 20ms
        await ws.send_bytes(mulaw.encode(voice))
        await wait_until(lambda: len(bridge.mic) == 1)
        assert bridge.mic[0] == mulaw.decode(mulaw.encode(voice))

        # LiveKit → 鉢: 640 バイトの声が、μ-law の 320 バイトになって届く
        bridge.say(voice)
        msg = await ws.receive(timeout=2)
        assert msg.type == WSMsgType.BINARY and msg.data == mulaw.encode(voice)
        await ws.close()
    finally:
        await client.close()


async def test_bad_start_gets_error_and_policy_close():
    client = await make_client(FakeBridgeFactory())
    try:
        ws = await client.ws_connect("/relay", headers=AUTH)
        await ws.send_str('{"type":"start","version":1,"plant_id":"BAD!"}')
        err = await ws.receive_json(timeout=2)
        assert err["type"] == "error" and err["code"] == "invalid_start"
        msg = await ws.receive(timeout=2)
        assert msg.type == WSMsgType.CLOSE and msg.data == 1008
    finally:
        await client.close()


async def test_odd_length_audio_closes_the_connection():
    factory = FakeBridgeFactory()
    client = await make_client(factory)
    try:
        ws = await client.ws_connect("/relay", headers=AUTH)
        await ws.send_str(START)
        await ws.receive_json(timeout=2)
        await ws.send_bytes(b"\x00" * 641)
        err = await ws.receive_json(timeout=2)
        assert err["code"] == "invalid_audio"
        await wait_until(lambda: factory.bridges[0].closed)
    finally:
        await client.close()


async def test_livekit_down_tells_device_to_try_again_later():
    client = await make_client(FakeBridgeFactory(fail=True))
    try:
        ws = await client.ws_connect("/relay", headers=AUTH)
        await ws.send_str(START)
        err = await ws.receive_json(timeout=2)
        assert err["code"] == "livekit_unavailable"
        msg = await ws.receive(timeout=2)
        assert msg.type == WSMsgType.CLOSE and msg.data == 1013
    finally:
        await client.close()


async def test_new_connection_for_same_plant_replaces_the_old_one():
    factory = FakeBridgeFactory()
    client = await make_client(factory)
    try:
        old = await client.ws_connect("/relay", headers=AUTH)
        await old.send_str(START)
        await old.receive_json(timeout=2)

        new = await client.ws_connect("/relay", headers=AUTH)
        await new.send_str(START)

        msg = await old.receive(timeout=2)
        assert msg.type == WSMsgType.CLOSE and msg.data == 1000
        assert (await new.receive_json(timeout=2))["type"] == "ready"
        await wait_until(lambda: factory.bridges[0].closed)
        assert not factory.bridges[1].closed
        await new.close()
    finally:
        await client.close()


async def test_health_check():
    client = await make_client(FakeBridgeFactory())
    try:
        resp = await client.get("/healthz")
        assert resp.status == 200 and await resp.text() == "ok"
    finally:
        await client.close()
