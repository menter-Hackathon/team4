"""中継の LiveKit 側。鉢の代わりに LiveKit のルームに入る（仕様 §3.2）。

鉢 1 台につき LiveKitBridge を 1 つ作る。
  - ルーム plant-<plant_id> に device-<plant_id> として入る（エージェントから見ると、今までの ESP32 と同じ）
  - 鉢のマイクの音を、自分のマイクのトラックとして publish する
  - エージェントの声を subscribe して、640 バイト（20ms）ずつ取り出せるようにする

server.py は「DeviceBridge の形をしたもの」だけを使う。テストでは偽物に差し替える。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import timedelta
from typing import Protocol

from livekit import api, rtc

from agent.relay.protocol import FRAME_MS, NUM_CHANNELS, SAMPLE_RATE, SAMPLES_PER_FRAME

logger = logging.getLogger(__name__)

# 鉢へ送る前にためておけるエージェントの声（フレーム数）。50 フレーム = 1 秒。
# 鉢への送信が詰まったときは古いものから捨てる（遅れた声を鳴らすより、今の声を鳴らすため）
PLANT_AUDIO_QUEUE_FRAMES = 50
# 中継が作るトークンの有効期限。接続のたびに作るので短くてよい
TOKEN_TTL = timedelta(hours=6)


class DeviceBridge(Protocol):
    """鉢 1 台ぶんの LiveKit とのやりとり（server.py が使う形）。"""

    async def push_mic(self, frame: bytes) -> None:
        """鉢のマイクの音（640 バイト = 20ms）をルームに流す。"""
        ...

    def plant_audio(self) -> AsyncIterator[bytes]:
        """エージェントの声を 640 バイトずつ返す。close() されたら終わる。"""
        ...

    async def close(self) -> None:
        """ルームから出る。何度呼んでもよい。"""
        ...


# plant_id を受け取り、ルームに入った DeviceBridge を返す関数。入れなければ例外を投げる
BridgeFactory = Callable[[str], Awaitable[DeviceBridge]]


def device_token(plant_id: str, api_key: str, api_secret: str) -> str:
    """鉢の代わりにルームへ入るためのトークンを作る（仕様 §3.1 と同じ名前・権限）。

    例: device_token("a1", ...) → identity "device-a1"、ルーム "plant-a1" に入れるトークン
    """
    return (
        api.AccessToken(api_key, api_secret)
        .with_identity(f"device-{plant_id}")
        .with_grants(
            api.VideoGrants(
                room_join=True, room=f"plant-{plant_id}", can_publish=True, can_subscribe=True
            )
        )
        .with_ttl(TOKEN_TTL)
        .to_jwt()
    )


class LiveKitBridge:
    """LiveKit のルームに入り、鉢の音とエージェントの声を受け渡す（DeviceBridge の本物）。"""

    def __init__(self, plant_id: str):
        self.plant_id = plant_id
        self._room = rtc.Room()
        self._source = rtc.AudioSource(SAMPLE_RATE, NUM_CHANNELS)
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=PLANT_AUDIO_QUEUE_FRAMES)
        self._readers: set[asyncio.Task[None]] = set()
        self._closed = False

    async def connect(self, url: str, token: str) -> None:
        """ルームに入り、マイクのトラックを publish する。

        手順:
          1. エージェントの声のトラックが届いたら読み始めるように、出来事を登録する
          2. ルームに入る（auto_subscribe なので、エージェントの声は自動で subscribe される）
          3. 鉢のマイクの音を流すトラックを publish する
        """

        # 1. 音声のトラックを subscribe したら、20ms ずつ読むタスクを始める
        @self._room.on("track_subscribed")
        def _on_track(
            track: rtc.Track, _pub: rtc.RemoteTrackPublication, who: rtc.RemoteParticipant
        ) -> None:
            if track.kind != rtc.TrackKind.KIND_AUDIO:
                return
            logger.info("plant %s: subscribed to audio from %s", self.plant_id, who.identity)
            task = asyncio.create_task(self._read_track(track))
            self._readers.add(task)
            task.add_done_callback(self._readers.discard)

        # 2. ルームに入る
        await self._room.connect(url, token)

        # 3. マイクのトラックを publish する（エージェントは SOURCE_MICROPHONE の音を聞く）
        track = rtc.LocalAudioTrack.create_audio_track("microphone", self._source)
        options = rtc.TrackPublishOptions()
        options.source = rtc.TrackSource.SOURCE_MICROPHONE
        await self._room.local_participant.publish_track(track, options)
        logger.info("plant %s: joined room %s", self.plant_id, self._room.name)

    async def _read_track(self, track: rtc.Track) -> None:
        """エージェントの声を 20ms（640 バイト）ずつ読み、キューに入れる。"""
        stream = rtc.AudioStream(
            track, sample_rate=SAMPLE_RATE, num_channels=NUM_CHANNELS, frame_size_ms=FRAME_MS
        )
        try:
            async for event in stream:
                frame = bytes(event.frame.data)
                if self._queue.full():
                    # 鉢への送信が追いついていない。いちばん古いフレームを捨てて場所を空ける
                    with contextlib.suppress(asyncio.QueueEmpty):
                        self._queue.get_nowait()
                self._queue.put_nowait(frame)
        finally:
            await stream.aclose()

    async def push_mic(self, frame: bytes) -> None:
        """鉢のマイクの音（640 バイト）を AudioSource に渡す。"""
        if self._closed:
            return
        audio = rtc.AudioFrame(frame, SAMPLE_RATE, NUM_CHANNELS, SAMPLES_PER_FRAME)
        await self._source.capture_frame(audio)

    async def plant_audio(self) -> AsyncIterator[bytes]:
        """エージェントの声を 640 バイトずつ返す。close() で None が入ったら終わる。"""
        while True:
            frame = await self._queue.get()
            if frame is None:
                return
            yield frame

    async def close(self) -> None:
        """読むタスクを止め、ルームから出る。plant_audio() にも終わりを知らせる。"""
        if self._closed:
            return
        self._closed = True
        for task in list(self._readers):
            task.cancel()
        with contextlib.suppress(Exception):
            await self._room.disconnect()
        # キューがいっぱいでも終わりの印（None）は必ず入れる
        while self._queue.full():
            self._queue.get_nowait()
        self._queue.put_nowait(None)
        logger.info("plant %s: left the room", self.plant_id)


def livekit_bridge_factory(url: str, api_key: str, api_secret: str) -> BridgeFactory:
    """設定を覚えた BridgeFactory を作る。server.py はこれを呼ぶだけでルームに入れる。"""

    async def connect(plant_id: str) -> DeviceBridge:
        bridge = LiveKitBridge(plant_id)
        try:
            await bridge.connect(url, device_token(plant_id, api_key, api_secret))
        except BaseException:
            # 入れなかったときも、作りかけの接続を片付けてから例外を伝える
            await bridge.close()
            raise
        return bridge

    return connect
