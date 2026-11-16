"""STT（kotoba-whisper など）の確認をまとめて行い、#2 に貼れる表を出す（仕様 §5.3・§8.2）。

#2 の「完了の確認方法」の 1〜4 を、コマンド 1 つで確かめるためのスクリプト。

    uv run python -m scripts.check_stt                      # 無音・雑音だけ確かめる
    uv run python -m scripts.check_stt futamaru1.wav ...    # 録音したファイルも確かめる

STT の場所は .env の STT_BASE_URL・STT_MODEL・STT_API_KEY を使う（--base-url などで上書きできる）。
エージェントと同じ条件（language=ja、prompt=「ふたまる」）で文字起こしするので、本番と同じ結果になる。

確かめること:
    1. 録音したファイルが文字起こしできるか
    2. 「ふたまる」が、ウェイクワード（.env の WAKE_WORDS）として見つかるか
    3. 無音・雑音だけの音声で、「ふたまる」や「ご視聴ありがとうございました」などが出ないか
       （Whisper は無音から、動画の締めの言葉をでっちあげることがある）
    4. 1 回の文字起こしにかかった秒数

録音のしかた（Mac）: QuickTime Player の［新規オーディオ収録］で「ふたまる」「ふたまるくん」
「ふたまる、今日ね…」などを 1 つずつ録り、書き出したファイルを渡す。WAV でも m4a でもよい。
"""

from __future__ import annotations

import argparse
import io
import os
import random
import sys
import time
import wave
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from agent.config import DEFAULT_WAKE_WORDS
from agent.plant.wake import detect_wake, normalize
from agent.plugins.whisper_stt import STT_PROMPT

# 無音・雑音から Whisper がでっちあげやすい言葉（YouTube の字幕で学習しているため）。
# 正規化してから「含むか」で調べる
HALLUCINATIONS = (
    "ご視聴ありがとうございました",
    "チャンネル登録",
    "おやすみなさい",
    "ありがとうございました",
)


@dataclass
class CheckResult:
    """1 つの音声の確認結果。"""

    name: str  # 音声の名前（ファイル名、または「無音 3 秒」など）
    text: str  # 文字起こしの結果。失敗したら空
    seconds: float  # 文字起こしにかかった秒数
    wake: bool  # ウェイクワードが見つかったか
    hallucination: str | None  # でっちあげの言葉が出たら、その言葉
    error: str | None = None  # 失敗したときのエラー


def make_wav(samples: list[int], rate: int = 16000) -> bytes:
    """16bit・モノラルの WAV を作る。samples は -32768〜32767 の整数の列。"""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)  # 16bit = 2 バイト
        w.setframerate(rate)
        w.writeframes(b"".join(s.to_bytes(2, "little", signed=True) for s in samples))
    return buf.getvalue()


def silence_wav(seconds: float = 3.0, rate: int = 16000) -> bytes:
    """無音の WAV を作る。"""
    return make_wav([0] * int(seconds * rate), rate)


def noise_wav(seconds: float = 3.0, rate: int = 16000, level: int = 2000, seed: int = 0) -> bytes:
    """小さめのホワイトノイズの WAV を作る。会場のざわざわの代わり。

    seed を決めておくと、毎回同じ雑音になる（結果を比べやすい）。
    """
    rng = random.Random(seed)
    return make_wav([rng.randint(-level, level) for _ in range(int(seconds * rate))], rate)


def judge(name: str, text: str, seconds: float, wake_words: tuple[str, ...]) -> CheckResult:
    """文字起こしの結果から、ウェイクワードとでっちあげの言葉を調べる。

    例: judge("a.wav", "下丸くん元気?", 0.8, ("下丸",)) → wake=True、hallucination=None
    """
    norm = normalize(text)
    found = next((h for h in HALLUCINATIONS if normalize(h) in norm), None)
    return CheckResult(
        name=name,
        text=text,
        seconds=seconds,
        wake=detect_wake(text, wake_words) is not None,
        hallucination=found,
    )


def transcribe(client: OpenAI, model: str, name: str, audio: bytes) -> tuple[str, float]:
    """エージェントと同じ条件で 1 回文字起こしし、(文字, かかった秒数) を返す。"""
    start = time.perf_counter()
    res = client.audio.transcriptions.create(
        model=model, file=(name, audio), language="ja", prompt=STT_PROMPT
    )
    return res.text, time.perf_counter() - start


def run_checks(
    client: OpenAI, model: str, files: list[Path], wake_words: tuple[str, ...]
) -> list[CheckResult]:
    """無音・雑音と、渡されたファイルを順に文字起こしして、結果の一覧を返す。

    1 つが失敗しても、残りは続ける（失敗は error に入れる）。
    """
    # 1. 確かめる音声を並べる。無音と雑音はここで作る
    inputs: list[tuple[str, str, bytes]] = [
        ("無音 3 秒", "silence.wav", silence_wav()),
        ("雑音 3 秒", "noise.wav", noise_wav()),
    ]
    for path in files:
        inputs.append((path.name, path.name, path.read_bytes()))

    # 2. 1 つずつ文字起こしして、結果を調べる
    results = []
    for label, file_name, audio in inputs:
        try:
            text, seconds = transcribe(client, model, file_name, audio)
            results.append(judge(label, text, seconds, wake_words))
        except Exception as e:  # 1 つの失敗で全体を止めない
            results.append(CheckResult(label, "", 0.0, False, None, error=str(e)))
    return results


def to_markdown(results: list[CheckResult], model: str) -> str:
    """#2 にそのまま貼れる Markdown の表にする。"""
    lines = [
        f"STT のモデル: `{model}`",
        "",
        "| 音声 | 文字起こし | ウェイクワード | でっちあげ | 秒 |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        text = f"エラー: {r.error}" if r.error else (r.text or "（空）")
        lines.append(
            f"| {r.name} | {text} | {'あり' if r.wake else 'なし'} | "
            f"{r.hallucination or '-'} | {r.seconds:.2f} |"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """コマンドの入り口。問題（エラー、無音・雑音で起きる、でっちあげ）があれば 1 を返す。"""
    load_dotenv()
    parser = argparse.ArgumentParser(description="STT の確認をまとめて行う（#2）")
    parser.add_argument("files", nargs="*", type=Path, help="録音した音声ファイル")
    parser.add_argument("--base-url", default=os.environ.get("STT_BASE_URL"))
    parser.add_argument(
        "--model", default=os.environ.get("STT_MODEL", "kotoba-tech/kotoba-whisper-v2.0-faster")
    )
    parser.add_argument("--api-key", default=os.environ.get("STT_API_KEY", "EMPTY"))
    args = parser.parse_args(argv)
    if not args.base_url:
        print("STT_BASE_URL を .env に書くか、--base-url で渡してください", file=sys.stderr)
        return 2

    # ウェイクワードは .env の WAKE_WORDS（無ければエージェントの既定）。エージェントと同じ読み方をする
    raw = os.environ.get("WAKE_WORDS", "")
    wake_words = tuple(w.strip() for w in raw.split(",") if w.strip()) or DEFAULT_WAKE_WORDS

    client = OpenAI(base_url=args.base_url, api_key=args.api_key, timeout=60, max_retries=0)
    results = run_checks(client, args.model, args.files, wake_words)
    print(to_markdown(results, args.model))

    # 無音・雑音（最初の 2 つ）で起きたり、でっちあげが出たりしたら問題
    bad = [r for r in results if r.error or r.hallucination]
    bad += [r for r in results[:2] if r.wake]
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
