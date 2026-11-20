"""STT: kotoba-whisper on Speaches（OpenAI 互換、仕様 §8.2）。

STT（Speech To Text）は、持ち主の声を文字にする部分。
LiveKit が「話し終わった」と判定した音声のかたまりを、1 回ずつ送って文字にしてもらう。
"""

from __future__ import annotations

from livekit.plugins import openai
from openai import AsyncOpenAI

from agent.config import Config

# ウェイクワードの認識補助（仕様 §5.3）。Whisper 系は prompt に書いた言葉を聞き取りやすくなるので、
# 「ふたまる」を「二丸」「下丸」などと書き間違えにくくする
STT_PROMPT = "ふたまる"


def create_stt(cfg: Config) -> openai.STT:
    """声を文字にする STT（LiveKit のプラグインの形）を作る。"""
    client = AsyncOpenAI(
        base_url=cfg.stt_base_url, api_key=cfg.stt_api_key, timeout=cfg.stt_timeout, max_retries=0
    )
    # language="ja": 日本語と決めておくと、言語の自動判定の誤りが無くなる
    # use_realtime=False: 音声のかたまりを 1 回ずつ /audio/transcriptions に送る方式（仕様 §8.2 の API）
    return openai.STT(
        model=cfg.stt_model, language="ja", prompt=STT_PROMPT, use_realtime=False, client=client
    )
