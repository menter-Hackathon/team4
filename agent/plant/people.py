"""発話から「今話しているのは誰か」を名前で見分ける（設計: 話し相手の見分けと方言）。

LLM に「知っている人の一覧」と発話を渡し、次のどれかを JSON で答えさせる:
    known: 一覧にいる人が名乗った   例: {"kind": "known", "person_id": 1}
    new:   一覧にいない人が名乗った 例: {"kind": "new", "name": "はなこ"}
    none:  名乗っていない（あいさつ・質問・雑談など）

LLM の出力は信用しすぎない。壊れた JSON や一覧に無い id は、すべて none にする。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

from .reflection import JsonCompleter, _strip_fence
from .store import Person


@dataclass
class Identification:
    """名前探しの結果。known なら person_id、new なら name が入る。"""

    kind: Literal["known", "new", "none"]
    person_id: int | None = None
    name: str | None = None


# LLM に返させる JSON の形。
# 必ず書かせる項目（required）は kind だけにする。person_id と name は、書いてあれば使う。
# なぜ 3 つとも必須にしないのか:
#   Ollama の Gemma 4 は、知っている人なら person_id だけ、新しい人なら name だけ書いて
#   JSON を閉じようとする。3 つとも必須にすると、Ollama が「残りも書け」と縛るため、
#   Gemma 4 は次の項目へ進めずに空白を出し続け、3 秒の時間切れになっていた
#   （名乗った回だけ毎回失敗していた。2026-11-18 の console 確認）。
#   kind だけ必須にすると、24 回試して 24 回とも 0.2〜0.3 秒で正しく返った。
#   読み取る側（identify）は data.get() で読むので、項目が無くても困らない
IDENTIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["known", "new", "none"]},
        "person_id": {"type": ["integer", "null"]},
        "name": {"type": ["string", "null"]},
    },
    "required": ["kind"],
    "additionalProperties": False,
}

_INSTRUCTION = (
    "話し手が名前を名乗っているか判定し、JSON で答える。\n"
    "- 知っている人の一覧にいる人が名乗ったら kind=known と person_id\n"
    "- 一覧にいない人が、はっきり自己紹介で名乗ったときだけ kind=new と name\n"
    "- 植物が名前を聞いた直後の、名前だけの短い答え（例:「はなこ」「はなこです」）も名乗りとみなす"
    "（一覧にいれば known、いなければ new）\n"
    "- それ以外（あいさつ・質問・雑談など）は kind=none\n"
    "- 「ふたまる」は植物の名前で、発話の先頭にあれば植物への呼びかけ。人の名前には含めない"
    "（例:「ふたまる太郎だよ」なら名前は「太郎」）\n"
    "音声認識は同じ名前をひらがな・カタカナ・漢字のどれでも書くので、表記ではなく意味で照合する。"
)

_NONE = Identification("none")

# 名前の最大の文字数。名前は DB にずっと残り、毎回プロンプトに入るので、長すぎるものは切る
MAX_NAME_CHARS = 20


def clean_name(name: str) -> str | None:
    """名前を DB・プロンプトに入れてよい形に整える。中身が無ければ None。

    1. 改行・タブ・続いた空白を 1 つの空白にし、前後の空白を消す。
       改行が残ると、プロンプトの中で「名前の行」の外に別の指示を書けてしまうため
    2. MAX_NAME_CHARS 文字までに切る

    例: "  はなこ\n" → "はなこ"、"山田\n太郎" → "山田 太郎"、" \n " → None
    """
    cleaned = " ".join(name.split())[:MAX_NAME_CHARS]
    return cleaned or None


async def identify(
    complete_json: JsonCompleter,
    utterance: str,
    known: list[Person],
    *,
    previous_plant: str | None = None,
) -> Identification:
    """発話から話し手を見分ける。例外は外に出さず、不正な結果はすべて none にする。

    引数:
        complete_json: LLM に JSON を作らせる関数
        utterance: ユーザーの発話（例: "はなこ"）
        known: この株が知っている人の一覧
        previous_plant: 植物が直前に言ったこと（例: "お名前は？"）。無ければ None。
            「お名前は？」→「はなこ」のように、名前だけの答えを名乗りと判断できるように渡す
            （最終レビュー I2）。キーワード指定・既定値 None なので、今までの呼び方もそのまま使える
    """
    # 1. 知っている人を「id と名前」だけにしてプロンプトに入れる。
    #    植物の直前の発言があれば、発話の前に並べる（会話の順番どおりに読めるように）
    people = [{"id": p.id, "name": p.name} for p in known]
    content = "知っている人: " + json.dumps(people, ensure_ascii=False)
    if previous_plant:
        content += f"\n直前の植物の発言: {previous_plant}"
    content += f"\n発話: {utterance}"
    messages = [
        {"role": "system", "content": _INSTRUCTION},
        {"role": "user", "content": content},
    ]
    # 2. LLM に聞く。失敗（通信エラー等）は none
    try:
        raw = await complete_json(messages, IDENTIFY_SCHEMA)
        data = json.loads(_strip_fence(raw))
    except Exception:
        return _NONE
    if not isinstance(data, dict):
        return _NONE
    # 3. kind ごとに中身を検証する
    kind = data.get("kind")
    if kind == "known":
        pid = data.get("person_id")
        # bool は int の仲間なので除く。一覧に無い id も none
        if isinstance(pid, int) and not isinstance(pid, bool) and pid in {p.id for p in known}:
            return Identification("known", pid)
    elif kind == "new":
        name = data.get("name")
        cleaned = clean_name(name) if isinstance(name, str) else None
        if cleaned:
            return Identification("new", None, cleaned)
    return _NONE
