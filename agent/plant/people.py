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


# LLM に返させる JSON の形
IDENTIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["known", "new", "none"]},
        "person_id": {"type": ["integer", "null"]},
        "name": {"type": ["string", "null"]},
    },
    "required": ["kind", "person_id", "name"],
    "additionalProperties": False,
}

_INSTRUCTION = (
    "話し手が名前を名乗っているか判定し、JSON で答える。\n"
    "- 知っている人の一覧にいる人が名乗ったら kind=known と person_id\n"
    "- 一覧にいない人が、はっきり自己紹介で名乗ったときだけ kind=new と name\n"
    "- それ以外（あいさつ・質問・雑談など）は kind=none\n"
    "音声認識は同じ名前をひらがな・カタカナ・漢字のどれでも書くので、表記ではなく意味で照合する。"
)

_NONE = Identification("none")


async def identify(
    complete_json: JsonCompleter, utterance: str, known: list[Person]
) -> Identification:
    """発話から話し手を見分ける。例外は外に出さず、不正な結果はすべて none にする。"""
    # 1. 知っている人を「id と名前」だけにしてプロンプトに入れる
    people = [{"id": p.id, "name": p.name} for p in known]
    messages = [
        {"role": "system", "content": _INSTRUCTION},
        {
            "role": "user",
            "content": "知っている人: "
            + json.dumps(people, ensure_ascii=False)
            + f"\n発話: {utterance}",
        },
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
        if isinstance(name, str) and name.strip():
            return Identification("new", None, name.strip())
    return _NONE
