"""考え中のつなぎ（「んー…」）を流す（#39、メンター FB 3）。

なぜつなぎを流すのか:
    持ち主が話し終えてから植物が喋り出すまで、今は 3〜5 秒かかることがある。
    メンターの FB で「4〜5 秒は許容範囲。ディスプレイがあれば考え中の表示ができるといい」と言われた。
    ふたまるにはディスプレイが無いので、代わりに声で「考え中」を伝える。
    返事が遅いときだけ、植物が先に「んー…」「えっとね…」と言う。

全体の流れ:
    1. エージェントが起動したら、FillerAudio.prepare() でつなぎの言葉を 1 回だけ声にして覚えておく
       （main.py が裏で走らせる。返事のたびに TTS を待たなくて済むように、前もって作る）
    2. 植物が返事をするたびに、main.py の tts_node が with_filler() を通して音声を流す
    3. with_filler() は、本物の返事の最初の音が delay 秒たっても来なければ、
       覚えておいたつなぎの声を先に流し、そのあとに本物の返事を流す
    4. 返事が速ければ（delay 秒以内に音が来れば）、つなぎは流さない

つなぎは「音声」として足すだけで、返事の文字には入らない。
そのため、会話ログ（utterances）にも LLM の会話履歴にも「んー…」は残らない。
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import AsyncIterator, Callable, Sequence
from typing import Any

logger = logging.getLogger(__name__)

# つなぎの言葉。毎回同じだと機械っぽいので、この中から選ぶ。
# 短く、どんな返事の前に付いても不自然にならないものにしている
FILLER_PHRASES = ("んー…", "えっとね…", "そうだなぁ…")

# これより小さい音（16bit の振れ幅）は無音とみなす。最大は 32767 なので、500 はかなり小さい音
SILENCE_LEVEL = 500


def trim_trailing_silence(frames: list[Any]) -> list[Any]:
    """声が終わったあとの、無音だけのかたまり（フレーム）を後ろから取り除く。

    TTS で作ったつなぎには、声のあとに無音が 0.2〜1.2 秒ついていた（OpenAI TTS で確認）。
    つなぎは最後まで流してから本物の返事に移るので、無音が長いとその分だけ返事が遅れる。
    フレームは 0.1 秒くらいずつなので、無音だけのフレームを捨てれば十分短くなる。

    例: [声, 声, 無音, 無音] → [声, 声]
    """
    end = len(frames)
    # frame.data は 16bit の音の大きさの並び（memoryview）。一番大きい音が SILENCE_LEVEL 以下なら無音
    while end > 0 and max((abs(v) for v in frames[end - 1].data), default=0) <= SILENCE_LEVEL:
        end -= 1
    return frames[:end]


class FillerAudio:
    """つなぎの言葉を前もって声にして、覚えておく。

    引数:
        phrases: つなぎの言葉の一覧（既定は FILLER_PHRASES）
        choose: 一覧から 1 つ選ぶ関数（既定は random.choice。テストでは決まったものを選ぶ関数を渡す）
        trim: 声にしたあとに、後ろの無音を切る関数（既定は trim_trailing_silence）。None なら切らない
    """

    def __init__(
        self,
        phrases: Sequence[str] = FILLER_PHRASES,
        *,
        choose: Callable[[list], Any] = random.choice,
        trim: Callable[[list[Any]], list[Any]] | None = trim_trailing_silence,
    ) -> None:
        self._phrases = tuple(phrases)
        self._choose = choose
        self._trim = trim
        # 声にできたつなぎ。1 つのつなぎ = 音声のかたまり（フレーム）のリスト
        self._clips: list[list[Any]] = []

    async def prepare(self, tts: Any) -> None:
        """つなぎの言葉を 1 つずつ TTS で声にして覚える。

        引数:
            tts: 植物の声の TTS（OpenAI TTS か AivisSpeech）。返事と同じ声にするため、同じものを使う
        失敗しても例外は出さない（つなぎが無くても会話はできるため）。ログに警告を残すだけにする。
        """
        for phrase in self._phrases:
            try:
                # tts.synthesize(文字) は、文字を一度に声にする。
                # 返ってくる音声のかたまり（ev.frame）を、順番にリストに集める
                async with tts.synthesize(phrase) as stream:
                    frames = [ev.frame async for ev in stream]
                # 後ろの無音を切る。無音のぶん返事が遅れないように
                if self._trim is not None:
                    frames = self._trim(frames)
            except Exception as e:
                logger.warning("could not prepare filler %r: %r", phrase, e)
                continue
            if frames:
                self._clips.append(frames)
        logger.info("prepared %d filler clips", len(self._clips))

    def pick(self) -> list[Any] | None:
        """覚えているつなぎから 1 つ選んで返す。まだ 1 つも用意できていなければ None。"""
        if not self._clips:
            return None
        return self._choose(self._clips)


async def with_filler[T](
    audio: AsyncIterator[T],
    pick: Callable[[], list[T] | None],
    *,
    delay: float,
) -> AsyncIterator[T]:
    """本物の返事の音声（audio）を流す。最初の音が delay 秒たっても来なければ、先につなぎを流す。

    関数名の後ろの [T] は「音声の 1 かたまりの型」を表す名前（Python 3.12 の書き方）。
    本番では LiveKit の rtc.AudioFrame、テストでは文字列で代用できるよう、型を決めずに T と書いている

    引数:
        audio: 本物の返事の音声（LiveKit の既定の tts_node が作るもの）
        pick: つなぎを 1 つ選んで返す関数（FillerAudio.pick）。None を返したらつなぎは流さない
        delay: 何秒待ってもまだ音が来なければ、つなぎを流すか。0 以下ならつなぎを使わない
    例（delay=1.5 のとき）:
        返事の最初の音が 0.8 秒で来た → 返事だけを流す
        返事の最初の音が 2.5 秒で来た → 1.5 秒の時点で「んー…」を流し、そのあと返事を流す

    しくみ:
        本物の音声を、裏のタスク（pump）で受け取ってキュー（順番待ちの列）に入れる。
        表では、キューから最初の 1 つを delay 秒だけ待つ。来なければつなぎを流し、
        そのあとはキューに入った本物の音声を順に流す。つなぎを流している間に届いた本物の音声は、
        キューにたまっているので、抜けることはない。
    """
    # つなぎを使わない設定なら、本物の音声をそのまま流すだけ
    if delay <= 0:
        async for frame in audio:
            yield frame
        return

    # キューに入れる印。_END は「本物の音声が最後まで終わった」、例外は「TTS が失敗した」
    queue: asyncio.Queue = asyncio.Queue()
    _END = object()

    async def pump() -> None:
        """本物の音声を受け取り、順にキューへ入れる。"""
        try:
            async for frame in audio:
                await queue.put(frame)
        except Exception as e:  # TTS の失敗などは、表の側でそのまま投げ直す
            await queue.put(e)
        finally:
            await queue.put(_END)

    task = asyncio.create_task(pump())
    try:
        # 1. 最初の音を delay 秒だけ待つ。来なければ、つなぎを流す
        try:
            first = await asyncio.wait_for(queue.get(), delay)
        except TimeoutError:
            clip = pick()
            if clip is not None:
                for frame in clip:
                    yield frame
            first = await queue.get()
        # 2. あとは届いた順に流す。終わりの印が来たら終わる。失敗の印なら例外を投げ直す
        item = first
        while item is not _END:
            if isinstance(item, Exception):
                raise item
            yield item
            item = await queue.get()
    finally:
        # 持ち主が割り込んだときなど、LiveKit がこの音声を途中で止めることがある。
        # そのときは裏のタスクも止めて、本物の TTS の通信を残さない
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        aclose = getattr(audio, "aclose", None)
        if aclose is not None:
            await aclose()
