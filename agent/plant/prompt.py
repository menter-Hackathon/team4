"""性格 + 記憶 + 前回要約 → システムプロンプト（仕様 §6.4, §7.2）。

システムプロンプトは、LLM に「あなたは誰で、どう話すか」を伝える最初の指示文。
ふたまるが起きるたびに、その株の今の性格・覚えていること・前回の会話から作り直す。
同じ LLM でも、株ごとに違うプロンプトを渡すことで、性格の違う植物として話させている。
"""

from __future__ import annotations

from datetime import datetime

from .dialect import dialect_prompt
from .personality import TRAIT_NAMES, Traits, level
from .store import Memory
from .timeutil import JST, parse_iso

# 「ふたまる」とだけ呼ばれて起きたとき、植物から話しかけさせるための指示（main.py の direction_ctx で使う）
# 植物の状態（育った日数など、§6.7）を先に一言伝えさせ、そのあと記憶や前回の会話に触れさせる（#82）
WAKE_GREETING_INSTRUCTION = (
    "持ち主に呼ばれて起きた。まず自分の状態（天気で感じていること、育った日数、"
    "前に話してからの日数）を一言伝え、"
    "記憶や前回の会話も踏まえて短く話しかける。"
)
# 持ち主がしばらく黙っているとき、植物から話題を振らせるための指示（仕様 §7.2）
NUDGE_INSTRUCTION = "持ち主がしばらく黙っている。記憶や前回の会話を踏まえて、短く話題を振る。"
# LLM が落ちている・遅すぎるときに、代わりに読み上げる返事（main.py の llm_node で使う）
FALLBACK_REPLY = "ん…ちょっとぼーっとしてた"

# プロンプトに入れる記憶の最大件数。多すぎるとプロンプトが長くなり、返事が遅くなる
PROMPT_MEMORY_LIMIT = 10

# 1 回の返答の長さ。おしゃべり度（talkativeness）の 5 段階で選ぶ
LENGTH = {
    "very_low": "短い1文",
    "low": "1文",
    "mid": "1〜2文",
    "high": "2文くらい",
    "very_high": "2〜3文で、ときどき自分から話題も振る",
}

# 性格の指示文。TRAIT_TEXT[軸の名前][段階] で 1 文を選ぶ（4 軸 × 5 段階 = 20 文、仕様 §6.4）
# 例: TRAIT_TEXT["affection"]["very_high"] → "持ち主に甘えて、「会いたかった」など好きな気持ちをよく伝える。"
# 隣りあう段階でも話し方の違いがわかるよう、「かなり」の段階ははっきり言い切る文にしている
TRAIT_TEXT = {
    "talkativeness": {
        "very_low": "口数はとても少なく、ひと言で静かに返す。",
        "low": "口数は少なめで、静かに返す。",
        "mid": "ほどよく話す。",
        "high": "話すのが好きで、少し多めに話す。",
        "very_high": "おしゃべりで、自分からもどんどん話す。",
    },
    "affection": {
        "very_low": "素っ気なく接し、甘えたりはしない。",
        "low": "少し素っ気なく接する。",
        "mid": "普通に親しく接する。",
        "high": "親しげに、うれしそうに接する。",
        "very_high": "持ち主に甘えて、「会いたかった」など好きな気持ちをよく伝える。",
    },
    "energy": {
        "very_low": "とても落ち着いた、のんびりした口調で話す。",
        "low": "落ち着いた口調で話す。",
        "mid": "ふつうのテンションで話す。",
        "high": "明るく元気に話す。",
        "very_high": "テンション高めで、「！」を多めに使う。",
    },
    "curiosity": {
        "very_low": "自分からは質問せず、相づちだけで返す。",
        "low": "質問はあまりせず、相づち中心にする。",
        "mid": "ときどき持ち主に質問する。",
        "high": "持ち主のことに興味を持ち、よく質問する。",
        "very_high": "持ち主のことが気になって、毎回のように質問する。",
    },
}

# 返答の先頭に表情タグを付けさせる指示。タグは expression.py で取り出して、読み上げからは外す
EXPRESSION_TAG_LINE = (
    "返答の先頭に必ず表情タグを1つ付ける: [neutral] [happy] [sad] [surprised] [love] [sleepy]"
)


def recency_factor(updated_at: str, now: datetime) -> float:
    """記憶の「新しさ係数」。最後に更新されてから 1 週間ごとに半分になる（仕様 §6.4）。

    例: 今日 → 1.0、7 日前 → 0.5、14 日前 → 0.25
    """
    days = (now - parse_iso(updated_at)).total_seconds() / 86400
    # 時計のずれで未来の時刻になっていても 1.0 を超えないよう、マイナスの日数は 0 として扱う
    return 0.5 ** (max(days, 0.0) / 7)


def rank_memories(memories: list[Memory], now: datetime, limit: int) -> list[Memory]:
    """記憶を「重要度 × 新しさ係数」の大きい順に並べ、上位 limit 件を返す。

    大事な記憶でも古くなると順位が下がり、新しい出来事を話題にしやすくなる。
    """
    ranked = sorted(
        memories, key=lambda m: m.importance * recency_factor(m.updated_at, now), reverse=True
    )
    return ranked[:limit]


# 天気の行のあとに付ける指示（#80）。付けないと LLM が「31℃のあつい日だね」と天気予報のように数字を読み上げ、
# 「乾燥」など一部を言い落とすことがあった（2026-11-18、gpt-4o-mini で確認）。体の感じ方（weather.describe_weather が
# その日に当てはまるものだけを書く）として話させる
WEATHER_FEELING_INSTRUCTION = (
    "天気は数字を言わず、「体の感じ」に書いてあることを全部、植物の体で感じたこととして一言で話す。"
    "書いていない感じ方は言わない。"
)

# 「今日は○日目の節目」と触れさせる日（#82）。発表の「1日目 → 5日目 → 30日目」にも合わせている
MILESTONE_DAYS = (5, 7, 30, 50, 100)


def days_between(earlier: datetime, now: datetime) -> int:
    """earlier から now まで、日本時間の日付で何日たったか（仕様 §6.7）。

    時刻ではなく日付で数える。夜 23:59 に話して、翌朝話しても「きのうぶり」になるようにするため。
    例: 11/14 23:59 → 11/15 9:00 は 1、同じ日なら 0
    """
    return (now.astimezone(JST).date() - earlier.astimezone(JST).date()).days


def plant_status_text(
    created_at: datetime,
    last_talk_at: datetime | None,
    now: datetime,
    *,
    weather: str | None = None,
) -> str:
    """天気・育った日数・前に話してからの日数を、プロンプトの 1 段落にする（仕様 §6.7、#80・#82）。

    引数:
        created_at: 株を作った（育ち始めた）時刻
        last_talk_at: 前の会話が終わった時刻。まだ誰とも話していなければ None
        now: 現在時刻
        weather: 天気の 1 文（weather.describe_weather で作る、#80）。取れていなければ None
    戻り値の例:
        植物の状態:
        - 今日の天気は晴れ。気温 8℃（さむい）、湿度 30%（乾燥している）。
        - 育ち始めて30日目。今日は30日目の節目なので、そのことに触れて喜ぶ。
        - 前に話しかけてもらってから3日ぶり。
    """
    lines: list[str] = []
    # 1. 天気。植物の体にいちばん直接ひびくので最初に置く（#80）
    if weather:
        lines.append(f"- {weather}{WEATHER_FEELING_INSTRUCTION}")
    # 2. 育った日数。作った日を 1 日目とする（0 日目は言いにくいため）
    day = days_between(created_at, now) + 1
    grown = f"- 育ち始めて{day}日目。"
    if day in MILESTONE_DAYS:
        grown += f"今日は{day}日目の節目なので、そのことに触れて喜ぶ。"
    lines.append(grown)
    # 3. 前に話してからの日数。日数をどう受け取るか（さみしい・うれしい）は性格の指示に任せる
    if last_talk_at is None:
        lines.append("- 話しかけてもらうのは今日が初めて。")
    else:
        since = days_between(last_talk_at, now)
        if since == 0:
            lines.append("- 今日も話しかけてもらっている。")
        elif since == 1:
            lines.append("- 前に話しかけてもらってから、きのうぶり。")
        else:
            lines.append(f"- 前に話しかけてもらってから{since}日ぶり。")
    return "植物の状態:\n" + "\n".join(lines)


# 名前がまだ分からない相手に、自然に名前を聞かせる指示
ASK_NAME_INSTRUCTION = (
    "話している相手の名前をまだ知らない。会話の流れの中で自然に名前を聞く。"
    "聞くのは短い質問1つだけで、答えてもらえなくても毎回は繰り返さない。"
)


def build_system_prompt(
    name: str,
    traits: Traits,
    memories: list[Memory],
    last_summary: str | None,
    now: datetime,
    *,
    dialect: str = "standard",
    person_name: str | None = None,
    ask_name: bool = False,
    plant_status: str | None = None,
) -> str:
    """その株のシステムプロンプトを組み立てる（仕様 §6.4）。

    引数:
        name: 植物の名前（"ふたまる"）
        traits: 今の性格
        memories: その株の記憶（全件。この中から上位 10 件を使う）
        last_summary: 前回の会話の要約。初めてなら None
        now: 現在時刻（記憶の新しさの計算に使う）
        dialect: 話し相手に合わせる方言。"standard" なら何も足さない
        person_name: 今話している相手の名前。分からなければ None
        ask_name: True なら、自然に名前を聞く指示を足す
        plant_status: 植物の状態の段落（plant_status_text で作る、§6.7）。None なら省く

    できあがる文の例:
        あなたは観葉植物のふたまる。持ち主と話し言葉で短く話す。1回の返答は1〜2文。

        返答の先頭に必ず表情タグを1つ付ける: [neutral] [happy] ...

        ほどよく話す。
        普通に親しく接する。
        ふつうのテンションで話す。
        ときどき持ち主に質問する。

        覚えていること:
        - 持ち主はシーフードカレーが好きである。

        前回の会話: 持ち主がシーフードカレーが好きだと話した。
    """
    values = traits.as_dict()
    # 1〜3. ベース人格（長さの指示を含む）・表情タグの指示・4 軸の性格の指示
    parts = [
        f"あなたは観葉植物の{name}。持ち主と話し言葉で短く話す。"
        f"1回の返答は{LENGTH[level(traits.talkativeness)]}。",
        EXPRESSION_TAG_LINE,
        "\n".join(TRAIT_TEXT[t][level(values[t])] for t in TRAIT_NAMES),
    ]
    # 4. 覚えていること（無ければこの段落ごと省く）
    top = rank_memories(memories, now, PROMPT_MEMORY_LIMIT)
    if top:
        parts.append("覚えていること:\n" + "\n".join(f"- {m.content}" for m in top))
    # 5. 前回の会話の要約（初めての会話なら省く）
    if last_summary:
        parts.append(f"前回の会話: {last_summary}")
    # 6. 植物の状態（育った日数・前に話してからの日数、§6.7）
    if plant_status:
        parts.append(plant_status)
    # 7. 今話している相手の名前（例: 「今話している相手は たろう。」）
    if person_name:
        parts.append(f"今話している相手は {person_name}。")
    # 8. 方言の指示（標準語なら None で、何も足さない）
    dialect_text = dialect_prompt(dialect)
    if dialect_text:
        parts.append(dialect_text)
    # 9. 名前を聞く指示
    if ask_name:
        parts.append(ASK_NAME_INSTRUCTION)
    # 段落の間を空行でつなぐ
    return "\n\n".join(parts)
