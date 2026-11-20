"""性格の 16 タイプ（「〇〇ふたまる」）を決める（仕様 §6.6、#64）。

性格の 4 軸（おしゃべり度・甘え度・元気度・好奇心）を、それぞれ「高い／低い」に分けると、
組み合わせは 2 × 2 × 2 × 2 = 16 通りになる。その 1 つ 1 つに植物の名前を付けて、
「話しかけ方によって、ふたまるにどんな花が咲いたか」として見せる。
まだ性格がほとんど動いていない間は、どのタイプでもない「ふたば」にする。

使うのはダッシュボードだけ（会話のプロンプトには入れない、#64）。
名前と説明はオリジナル（ラブタイプ16診断・MBTI などの名前は使わない）。
イラストはちゃむさんが描き、key（英語名）をファイル名にする（例: sunflower.png）。
"""

from __future__ import annotations

from dataclasses import dataclass

from .personality import TRAIT_NAMES, Traits, level


@dataclass(frozen=True)
class PlantType:
    """タイプ 1 つぶんの情報。

    key: 英語名。イラストのファイル名に使う（例: "sunflower" → sunflower.png）
    name: 表示名（例: "ひまわりふたまる"）
    emoji: イラストが届くまでの代わりに出す絵文字
    description: ひとこと説明（ダッシュボードにそのまま出す）
    """

    key: str
    name: str
    emoji: str
    description: str


# まだタイプが決まっていない状態。最初はみんなここから始まる
SPROUT = PlantType("sprout", "ふたば", "🌱", "これからどんな子に育つかな")

# 16 タイプ。キーは (おしゃべり, 甘え, 元気, 好奇心) がそれぞれ高いか（True）低いか（False）。
# 並びは TRAIT_NAMES と同じ。一覧は #64 のコメント（2026-11-17、仮）と同じ
TYPES: dict[tuple[bool, bool, bool, bool], PlantType] = {
    # おしゃべり × 甘えん坊
    (True, True, True, True): PlantType(
        "sunflower", "ひまわりふたまる", "🌻", "誰にでも全力で話しかける、太陽みたいな子"
    ),
    (True, True, True, False): PlantType(
        "tulip",
        "チューリップふたまる",
        "🌷",
        "自分の話を聞いてほしくてたまらない、にぎやかな甘えんぼ",
    ),
    (True, True, False, True): PlantType(
        "chamomile",
        "カモミールふたまる",
        "🍵",
        "のんびりした口調で、あなたのことをたくさん聞く聞き上手",
    ),
    (True, True, False, False): PlantType(
        "wisteria", "藤ふたまる", "🪻", "寄りかかるように、ゆっくり自分の話をする甘えんぼ"
    ),
    # おしゃべり × クール
    (True, False, True, True): PlantType(
        "mint", "ミントふたまる", "🌿", "さわやかに次々と質問する、知りたがり"
    ),
    (True, False, True, False): PlantType(
        "chili", "トウガラシふたまる", "🌶️", "ピリッと元気にまくしたてる、マイペースなおしゃべり"
    ),
    (True, False, False, True): PlantType(
        "rosemary", "ローズマリーふたまる", "🍃", "落ち着いて理由を聞きたがる、ちょっと物知り"
    ),
    (True, False, False, False): PlantType(
        "bamboo", "竹ふたまる", "🎋", "淡々と、自分のペースで語る"
    ),
    # 口数少なめ × 甘えん坊
    (False, True, True, True): PlantType(
        "clover",
        "クローバーふたまる",
        "🍀",
        "ひとことずつだけど、うれしそうに聞いてくる人なつっこい子",
    ),
    (False, True, True, False): PlantType(
        "daisy", "デイジーふたまる", "🌼", "言葉は少ないけど、ニコニコそばにいる元気な子"
    ),
    (False, True, False, True): PlantType(
        "violet", "すみれふたまる", "💜", "そっと寄り添って、ときどき小さく質問する"
    ),
    (False, True, False, False): PlantType(
        "primrose", "月見草ふたまる", "🌙", "静かにそばにいたい、ひかえめな甘えんぼ"
    ),
    # 口数少なめ × クール
    (False, False, True, True): PlantType(
        "mimosa", "オジギソウふたまる", "🙇", "なんにでもピクッと反応する、好奇心のかたまり"
    ),
    (False, False, True, False): PlantType(
        "thistle", "アザミふたまる", "💥", "トゲはあるけど元気いっぱい、ぶっきらぼうな子"
    ),
    (False, False, False, True): PlantType(
        "cactus", "サボテンふたまる", "🌵", "そっけないけど、実はあなたに興味しんしん"
    ),
    (False, False, False, False): PlantType(
        "moss", "コケふたまる", "🪨", "静かにそこにいるだけで落ち着く、マイペースな子"
    ),
}

# ふたば＋16 タイプの全部（17 個）。ダッシュボードが返してよいイラストの名前の一覧にも使う
ALL_TYPES: tuple[PlantType, ...] = (SPROUT, *TYPES.values())


def _is_high(value: float) -> bool:
    """軸の値が「高い」側か。0.5 ちょうどは高い側に入れる。

    小数の誤差（0.5 から 0.05 を 2 回引くと 0.39999999999999997 になる）で
    結果が変わらないよう、level() と同じく小数 6 桁に丸めてから比べる。
    """
    return round(value, 6) >= 0.5


def plant_type(traits: Traits) -> PlantType:
    """今の性格から、タイプを 1 つ決める（仕様 §6.6、#64 の B 案）。

    1. 4 軸とも 5 段階の「ふつう」（0.4 より上・0.6 未満）なら、まだ「ふたば」
       （1 回の会話で動くのは最大 ±0.05 なので、最短 2 回の会話でタイプが決まる。
        少しの変化でタイプが入れ替わらないよう、すぐには決めない）
    2. 1 つでも「ふつう」を出たら、4 軸それぞれを 0.5 以上（高い）か未満（低い）かで分け、
       その組み合わせのタイプにする

    例: Traits() → ふたば、Traits(0.9, 0.9, 0.9, 0.9) → ひまわりふたまる、
        Traits(0.6, 0.45, 0.5, 0.5) → ミントふたまる（おしゃべり高・甘え低・元気高・好奇心高）
    """
    values = [traits.as_dict()[name] for name in TRAIT_NAMES]
    # 1. まだ性格がほとんど動いていない
    if all(level(v) == "mid" for v in values):
        return SPROUT
    # 2. 高い／低いの組み合わせでタイプを引く
    highs = (_is_high(values[0]), _is_high(values[1]), _is_high(values[2]), _is_high(values[3]))
    return TYPES[highs]
