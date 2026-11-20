"""AivisSpeech の声を選ぶためのコマンド（仕様 §8.3、#56）。

AivisSpeech は、入っている音声モデルとスタイルで声が決まる。どの声にするかは聞いて決めるので、
一覧を出すのと、サンプルの WAV を作るのをこのコマンドで行う。

    # 1. 入っている声の一覧（スタイル ID）を出す
    uv run python -m scripts.aivis_voices --list
    # 2. 気になる声のサンプルを作って聞く（data/voice-samples/<ID>.wav ができる）
    uv run python -m scripts.aivis_voices --speaker 888753760 888753761
    afplay data/voice-samples/888753760.wav
    # 3. 決めた ID を .env の AIVIS_SPEAKER に書く（TTS_PROVIDER=aivis も）

AivisSpeech Engine の URL は .env の AIVIS_BASE_URL（空なら http://127.0.0.1:10101）。
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import aiohttp
from dotenv import load_dotenv

from agent.config import aivis_base_url_from_env
from agent.plugins.aivis_tts import AivisOptions, synthesize_wav

# サンプルの文。Issue #56 で OpenAI の声を聞き比べたときと同じ文にする
SAMPLE_TEXT = "あ、おかえり！きのう言ってたテスト、どうだった？ずっと気になってたんだ！"
# サンプルの WAV を置く場所。data/ は Git に入らない
DEFAULT_OUT = "data/voice-samples"


async def list_voices(session: aiohttp.ClientSession, base_url: str) -> list[tuple[int, str, str]]:
    """入っている声を (スタイル ID, モデルの名前, スタイルの名前) の一覧で返す。

    GET /speakers は「モデルごとに、スタイル（ノーマル・喜び など）の一覧」を返すので、
    スタイル 1 つを 1 行に並べ直す。
    例: [(888753760, "Anneli", "ノーマル"), (888753761, "Anneli", "通常"), ...]
    """
    async with session.get(f"{base_url.rstrip('/')}/speakers") as res:
        res.raise_for_status()
        speakers = await res.json()
    return [
        (style["id"], speaker["name"], style["name"])
        for speaker in speakers
        for style in speaker.get("styles", [])
    ]


async def make_samples(
    session: aiohttp.ClientSession,
    base_url: str,
    speakers: list[int],
    text: str,
    out_dir: Path,
    *,
    speed: float = 1.0,
    pitch: float = 0.0,
) -> list[Path]:
    """声ごとにサンプルの WAV を作り、作ったファイルのパスを返す。ファイル名は「スタイル ID.wav」。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for speaker in speakers:
        opts = AivisOptions(
            base_url=base_url, speaker=speaker, speed=speed, pitch=pitch, timeout=60
        )
        wav = await synthesize_wav(session, opts, text, connect_timeout=5)
        path = out_dir / f"{speaker}.wav"
        path.write_bytes(wav)
        paths.append(path)
    return paths


async def run(args: argparse.Namespace, base_url: str) -> int:
    """引数に合わせて一覧を出す・サンプルを作る。戻り値は終了コード（0 が成功）。"""
    async with aiohttp.ClientSession() as session:
        try:
            if args.list:
                print("スタイルID  モデル / スタイル")
                for style_id, name, style in await list_voices(session, base_url):
                    print(f"{style_id:>10}  {name} / {style}")
            if args.speaker:
                paths = await make_samples(
                    session,
                    base_url,
                    args.speaker,
                    args.text,
                    Path(args.out),
                    speed=args.speed,
                    pitch=args.pitch,
                )
                for path in paths:
                    print(f"作りました: {path}  （聞く: afplay {path}）")
        except (aiohttp.ClientError, TimeoutError) as e:
            print(f"AivisSpeech Engine（{base_url}）につながりません: {e}")
            return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AivisSpeech の声の一覧を出し、サンプルを作る")
    parser.add_argument("--list", action="store_true", help="入っている声とスタイル ID を出す")
    parser.add_argument(
        "--speaker", type=int, nargs="*", default=[], help="サンプルを作る声のスタイル ID"
    )
    parser.add_argument("--text", default=SAMPLE_TEXT, help="サンプルで読ませる文")
    parser.add_argument("--speed", type=float, default=1.0, help="話す速さ（1.0 がふつう）")
    parser.add_argument("--pitch", type=float, default=0.0, help="声の高さ（0.0 がふつう）")
    parser.add_argument(
        "--out", default=DEFAULT_OUT, help=f"WAV を置くフォルダ（既定: {DEFAULT_OUT}）"
    )
    args = parser.parse_args(argv)
    if not args.list and not args.speaker:
        parser.error("--list か --speaker のどちらかを指定してください")
    load_dotenv()
    return asyncio.run(run(args, aivis_base_url_from_env()))


if __name__ == "__main__":
    raise SystemExit(main())
