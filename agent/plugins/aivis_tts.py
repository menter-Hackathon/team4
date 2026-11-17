"""TTS: ローカルの AivisSpeech Engine（仕様 §8.3、#56）。

AivisSpeech Engine は、GPU の PC で動かす日本語の TTS（Style-Bert-VITS2 ベース）。
API は VOICEVOX と同じ形で、1 つの文を声にするのに 2 回呼ぶ:
    1. POST /audio_query?text=…&speaker=…  → 読み方・抑揚などの「合成の設計図」（JSON）が返る
    2. POST /synthesis?speaker=…（本文は 1 の JSON）  → WAV が返る
1 と 2 の間で、話す速さ・声の高さ・出力の形式（24kHz・モノラル）を書き換える。

LiveKit には AivisSpeech 用のプラグインが無いので、LiveKit の TTS の決まった形
（tts.TTS と tts.ChunkedStream）に合わせて、ここで小さく作る。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import aiohttp
from livekit.agents import (
    DEFAULT_API_CONNECT_OPTIONS,
    APIConnectionError,
    APIConnectOptions,
    APIStatusError,
    APITimeoutError,
    tts,
)

from agent.config import Config

# 返してもらう音声の形式。OpenAI TTS（§8.3）と同じ 24kHz・モノラルにそろえる
SAMPLE_RATE = 24000
NUM_CHANNELS = 1


@dataclass(frozen=True)
class AivisOptions:
    """AivisSpeech に渡す設定。

    base_url: AivisSpeech Engine の URL（例: http://127.0.0.1:10101）
    speaker: 声のスタイル ID（scripts/aivis_voices.py --list で調べる）
    speed: 話す速さ（1.0 がふつう。大きいほど速い）
    pitch: 声の高さ（0.0 がふつう。少し上げると幼く聞こえる。例: 0.05）
    timeout: 1 回の呼び出しを待つ秒数
    """

    base_url: str
    speaker: int
    speed: float = 1.0
    pitch: float = 0.0
    timeout: float = 10.0


class AivisTTS(tts.TTS):
    """AivisSpeech Engine で声を作る TTS。文を 1 つずつまとめて声にする（ストリーミングはしない）。

    LiveKit は、LLM の返事を文に区切ってから synthesize() を呼ぶので、
    ストリーミングしなくても、1 文目ができた時点で話し始められる。
    """

    def __init__(self, opts: AivisOptions) -> None:
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=SAMPLE_RATE,
            num_channels=NUM_CHANNELS,
        )
        self._opts = opts
        # HTTP の接続は使い回す（毎回つなぎ直すと遅いため）。最初に使うときに作る
        self._session: aiohttp.ClientSession | None = None

    @property
    def opts(self) -> AivisOptions:
        return self._opts

    @property
    def model(self) -> str:
        return "aivisspeech"

    @property
    def provider(self) -> str:
        return "AivisSpeech"

    def session(self) -> aiohttp.ClientSession:
        """HTTP の接続（aiohttp のセッション）を返す。まだ無い・閉じていれば作る。"""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    def synthesize(
        self, text: str, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> tts.ChunkedStream:
        """text を声にする。LiveKit から 1 文ごとに呼ばれる。"""
        return _AivisChunkedStream(tts=self, input_text=text, conn_options=conn_options)

    async def aclose(self) -> None:
        """終わるときに HTTP の接続を閉じる。"""
        if self._session is not None and not self._session.closed:
            await self._session.close()


async def synthesize_wav(
    session: aiohttp.ClientSession, opts: AivisOptions, text: str, connect_timeout: float
) -> bytes:
    """AivisSpeech で text を声にして、WAV のバイト列を返す。

    TTS からも、声を選ぶスクリプト（scripts/aivis_voices.py）からも使う。
    失敗したら aiohttp の例外（ClientResponseError・TimeoutError など）をそのまま投げる。
    """
    base = opts.base_url.rstrip("/")
    timeout = aiohttp.ClientTimeout(total=opts.timeout, sock_connect=connect_timeout)
    params = {"speaker": str(opts.speaker)}
    # 1. 読み方・抑揚の設計図を作ってもらう
    async with session.post(
        f"{base}/audio_query", params={**params, "text": text}, timeout=timeout
    ) as res:
        res.raise_for_status()
        query = await res.json()
    # 2. 速さ・高さ・出力の形式を書き換える（AivisSpeech の既定の出力は 44.1kHz なので 24kHz にする）
    query["speedScale"] = opts.speed
    query["pitchScale"] = opts.pitch
    query["outputSamplingRate"] = SAMPLE_RATE
    query["outputStereo"] = False
    # 3. 設計図を渡して、声（WAV）を作ってもらう
    async with session.post(f"{base}/synthesis", params=params, json=query, timeout=timeout) as res:
        res.raise_for_status()
        return await res.read()


class _AivisChunkedStream(tts.ChunkedStream):
    """1 文ぶんの声を作る処理。LiveKit が _run() を呼び、出来た音声を output_emitter に渡す。"""

    def __init__(self, *, tts: AivisTTS, input_text: str, conn_options: APIConnectOptions) -> None:
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
        self._aivis = tts

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        """AivisSpeech を呼んで、WAV を LiveKit に渡す。

        失敗は LiveKit の例外に直して投げる。LiveKit はそれを見て、その応答をスキップする（仕様 §10）。
        """
        try:
            wav = await synthesize_wav(
                self._aivis.session(),
                self._aivis.opts,
                self.input_text,
                self._conn_options.timeout,
            )
        except TimeoutError as e:
            raise APITimeoutError("AivisSpeech が時間内に返事をしませんでした") from e
        except aiohttp.ClientResponseError as e:
            raise APIStatusError(
                f"AivisSpeech がエラーを返しました: {e.message}", status_code=e.status
            ) from e
        except aiohttp.ClientError as e:
            raise APIConnectionError(f"AivisSpeech につながりません: {e}") from e
        # WAV のまま渡す（mime_type で WAV と伝えると、LiveKit が中身を読んで音声にする）
        output_emitter.initialize(
            request_id=uuid.uuid4().hex,
            sample_rate=SAMPLE_RATE,
            num_channels=NUM_CHANNELS,
            mime_type="audio/wav",
        )
        output_emitter.push(wav)
        output_emitter.flush()


def create_aivis_tts(cfg: Config) -> AivisTTS:
    """設定（.env の AIVIS_*）から AivisSpeech の TTS を作る。"""
    if cfg.aivis_speaker is None:
        # load_config が先に確かめているので、ここには来ないはず
        raise ValueError("AIVIS_SPEAKER が設定されていません")
    return AivisTTS(
        AivisOptions(
            base_url=cfg.aivis_base_url,
            speaker=cfg.aivis_speaker,
            speed=cfg.aivis_speed,
            pitch=cfg.aivis_pitch,
            timeout=cfg.tts_timeout,
        )
    )
