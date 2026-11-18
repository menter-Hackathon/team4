"""会話終了時の記憶抽出と性格変化量の判定（仕様 §6.3）。

植物が眠るたびに、その会話（セッション）を LLM に読ませて「振り返り（reflection）」をする。
LLM には次の 4 つを JSON で返させる。
    - summary:          会話の要約（次に起きたとき「前回の会話」としてプロンプトに入る）
    - new_memories:     新しく覚えること（例: 「持ち主はカレーが好き」）
    - updated_memories: 前から覚えていることの書き換え
    - trait_deltas:     性格 4 軸をどれだけ動かすか（-0.05〜+0.05）
結果は DB に保存され、次の会話から性格と記憶に反映される。これで植物が「育つ」。

LLM の出力は形が崩れていることがあるので、ここで念入りに検査してから使う。
"""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from .dialect import DIALECTS, UNKNOWN
from .personality import TRAIT_NAMES, apply_deltas, clamp
from .prompt import days_between, rank_memories
from .store import MemoryUpdate, NewMemory, PlantStore
from .timeutil import parse_iso

logger = logging.getLogger(__name__)

# 「メッセージ一覧と JSON スキーマを渡すと、LLM の返した JSON 文字列を返す」関数の型。
# 本番は plugins/llm.py の create_json_completer、テストは偽物（tests/fakes.py）を使う
JsonCompleter = Callable[[list[dict], dict], Awaitable[str]]

# 記憶の種類。preference: 好み、event: 出来事、habit: 習慣
MEMORY_KINDS = ("preference", "event", "habit")
# 振り返りで LLM に見せる既存の記憶の最大件数（重複して覚えないよう、今の記憶も見せる）
REFLECTION_MEMORY_LIMIT = 30
# LLM の出力が壊れていたときに試す最大回数（1 回目 + 再試行 1 回、仕様 §6.3 手順6）
MAX_ATTEMPTS = 2

# LLM に返させる JSON の形（JSON スキーマ）。vLLM などの「構造化出力」に渡すと、この形で返してくれる

REFLECTION_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "new_memories": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "content": {"type": "string"},
                    "kind": {"type": "string", "enum": list(MEMORY_KINDS)},
                    "importance": {"type": "number"},
                },
                "required": ["content", "kind", "importance"],
            },
        },
        "updated_memories": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "content": {"type": "string"},
                    "importance": {"type": "number"},
                },
                "required": ["id", "content", "importance"],
            },
        },
        "trait_deltas": {
            "type": "object",
            "properties": {name: {"type": "number"} for name in TRAIT_NAMES},
            "required": list(TRAIT_NAMES),
        },
        # 持ち主の方言の判定。判断できなければ unknown
        "owner_dialect": {"type": "string", "enum": [*DIALECTS, UNKNOWN]},
        # 持ち主が水をあげたと言ったか（#81）。会話中のキーワードで取りこぼした言い方を拾う
        "watered": {"type": "boolean"},
    },
    "required": [
        "summary",
        "new_memories",
        "updated_memories",
        "trait_deltas",
        "owner_dialect",
        "watered",
    ],
}

# 振り返りで LLM に渡す指示文。スキーマとは別に、ここにも出力の形を書く（Gemma 4 の公式の推奨）
SYSTEM_MESSAGE = """あなたは観葉植物「ふたまる」の記憶係。持ち主との会話ログを読み、次の JSON だけを出力する。
{
  "summary": "会話の要約（1〜2文）",
  "new_memories": [{"content": "持ち主について覚えておくこと", "kind": "preference | event | habit", "importance": 0.0〜1.0}],
  "updated_memories": [{"id": 既存記憶の id, "content": "更新後の内容", "importance": 0.0〜1.0}],
  "trait_deltas": {"talkativeness": -0.05〜0.05, "affection": -0.05〜0.05, "energy": -0.05〜0.05, "curiosity": -0.05〜0.05},
  "owner_dialect": "standard | kansai | hakata | hiroshima | unknown",
  "watered": true または false
}
- 既存の記憶と同じ内容は new_memories に入れない。内容が変わった・補強された場合は updated_memories でその id を更新する。
- trait_deltas は今回の会話で性格がどう変わるかの判定:
  talkativeness は話しかけ頻度・会話の長さ、affection は褒め言葉・優しい言葉の量、
  energy はユーザーの口調・話題の明るさ、curiosity はユーザーが自分の話をする量で判断する。
- 「## 世話の様子」も trait_deltas の判断に使う（植物は世話のしかたでも育ち方が変わる）:
  affection は、前にこの相手と話してから 3 日以上あいていれば少し下げ（さみしくてすねる）、
  毎日のように話しかけられていれば少し上げる。
  energy は、この会話で水をもらった・水をこまめにもらっていれば少し上げ、
  水をもらってから 3 日以上たっていれば少し下げる（のどが渇いて元気がない）。
- owner_dialect は「持ち主」の発言（ふたまるの発言は見ない）の方言を判定する。
  standard（標準語）/ kansai（関西弁）/ hakata（博多弁）/ hiroshima（広島弁）のどれか。
  発言が短い・少ない・方言の特徴が読み取れないなど判断できないときは unknown にする。
- watered は、持ち主がこの会話の中で「ふたまるに水をあげた」と言ったときだけ true にする。
  これからあげる・あげていない・ほかの植物の話なら false にする。"""


class ReflectionError(Exception):
    """LLM の出力が使えない形だったときのエラー。"""


@dataclass
class ReflectionResult:
    """検査済みの振り返り結果。"""

    summary: str
    new_memories: list[NewMemory]
    updated_memories: list[MemoryUpdate]
    trait_deltas: dict[str, object]
    # 持ち主の方言。DIALECTS のどれか、判断できなければ UNKNOWN
    owner_dialect: str = UNKNOWN
    # 持ち主が水をあげたと言ったか（#81）。true 以外（無い・文字列など）は False
    watered: bool = False


def _importance(value: object) -> float:
    """記憶の重要度を 0.0〜1.0 の数値に直す。数値でなければ真ん中の 0.5 にする。

    例: 0.8 → 0.8、2 → 1.0、"high" → 0.5、NaN → 0.5
    """
    # bool は Python では数値の仲間（True == 1）なので、先に弾く
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.5
    # NaN・Infinity も数値ではない扱いにする。clamp(NaN) は上限の 1.0 を返してしまうため（#7）
    if not math.isfinite(value):
        return 0.5
    return clamp(float(value), 0.0, 1.0)


def _kind(value: object) -> str | None:
    """記憶の種類を小文字にそろえる。文字列でなければ None。

    例: "Event" → "event"、" habit " → "habit"、3 → None
    """
    return value.strip().lower() if isinstance(value, str) else None


def _memory_id(value: object) -> int | None:
    """記憶の id を整数に直す。直せなければ None（その更新は捨てる）。

    例: 3 → 3、"3" → 3、" 12 " → 12、True → None、"abc" → None、3.0 → None
    """
    # bool は Python では int の仲間（True == 1）なので、先に弾く
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdecimal():
        return int(value.strip())
    return None


def _strip_fence(raw: str) -> str:
    """LLM が JSON を ```json ... ``` で囲んで返してきたときに、囲みを外す。

    例: "```json\n{\"a\": 1}\n```" → "{\"a\": 1}"
    """
    text = raw.strip()
    if text.startswith("```"):
        # 1 行目（```json）を捨てる
        text = text.split("\n", 1)[1] if "\n" in text else ""
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    return text.strip()


def parse_reflection(raw: str) -> ReflectionResult:
    """LLM が返した文字列を検査して ReflectionResult にする。

    全体が壊れている（JSON でない・必須のキーが無い・型が違う）ときは ReflectionError を投げ、
    呼び出し側（reflect_session）で再試行する。
    記憶 1 件ずつの中身がおかしいときは、その 1 件だけ捨てて残りは使う。
    """
    # 1. JSON として読めるか
    try:
        data = json.loads(_strip_fence(raw))
    except json.JSONDecodeError as e:
        raise ReflectionError(f"JSON として不正: {e}") from e
    # 2. 4 つの必須キーがそろっていて、型が正しいか
    if not isinstance(data, dict):
        raise ReflectionError("トップレベルがオブジェクトではない")
    for key in ("summary", "new_memories", "updated_memories", "trait_deltas"):
        if key not in data:
            raise ReflectionError(f"必須キー {key} が無い")
    if not isinstance(data["summary"], str):
        raise ReflectionError("summary が文字列ではない")
    if not isinstance(data["new_memories"], list) or not isinstance(data["updated_memories"], list):
        raise ReflectionError("new_memories / updated_memories が配列ではない")
    if not isinstance(data["trait_deltas"], dict):
        raise ReflectionError("trait_deltas がオブジェクトではない")

    # 3. 新しい記憶: 中身が空でなく、種類が 3 種類のどれかのものだけ使う。
    #    種類は "Event" のように大文字で来ることがあるので、小文字にそろえてから比べる（#8）
    new_memories = [
        NewMemory(m["content"], _kind(m.get("kind")), _importance(m.get("importance")))
        for m in data["new_memories"]
        if isinstance(m, dict)
        and isinstance(m.get("content"), str)
        and m["content"].strip()
        and _kind(m.get("kind")) in MEMORY_KINDS
    ]
    # 4. 記憶の更新: id が整数で、中身が空でないものだけ使う。
    #    id は "3" のように文字列で来ることがあるので、数字だけの文字列は整数に直す（#8）
    #    （存在しない id は、DB に書くとき store.apply_reflection が無視する）
    updated_memories = [
        MemoryUpdate(_memory_id(m.get("id")), m["content"], _importance(m.get("importance")))
        for m in data["updated_memories"]
        if isinstance(m, dict)
        and _memory_id(m.get("id")) is not None
        and isinstance(m.get("content"), str)
        and m["content"].strip()
    ]
    # 5. 方言: 無い・知らない値なら unknown にする（エラーにはしない。古い出力にも対応するため）
    dialect = data.get("owner_dialect")
    owner_dialect = dialect if isinstance(dialect, str) and dialect in DIALECTS else UNKNOWN
    return ReflectionResult(
        data["summary"],
        new_memories,
        updated_memories,
        data["trait_deltas"],
        owner_dialect,
        # 「is True」で比べるのは、本物の true だけを水やりと数えるため（#81）。
        # LLM が "yes" や 1 などを返しても、水やりとは数えない（うっかり記録しないほうが安全）
        data.get("watered") is True,
    )


def _build_messages(
    store: PlantStore, plant_id: str, session_id: int, person_id: int | None, now: datetime
) -> list[dict]:
    """振り返りで LLM に渡すメッセージを作る。

    ユーザー側のメッセージの例:
        ## 会話ログ
        持ち主: 今日カレー食べた
        ふたまる: いいね！何カレー？

        ## 既存の記憶
        [id=1] 持ち主はピザが好き (importance=0.6)
    """
    # 会話ログを「持ち主: …」「ふたまる: …」の行にする
    lines = [
        f"{'持ち主' if u.role == 'user' else 'ふたまる'}: {u.text}"
        for u in store.get_utterances(session_id)
    ]
    # 既存の記憶は id 付きで見せる。LLM が「この記憶を更新する」と id で指定できるように
    # 見せるのは、この人の記憶だけ（ほかの人の記憶も、お客さんとの記憶も混ぜない、#70）
    memories = rank_memories(store.get_memories(plant_id, person_id), now, REFLECTION_MEMORY_LIMIT)
    memory_lines = [f"[id={m.id}] {m.content} (importance={m.importance:g})" for m in memories]
    user = (
        "## 会話ログ\n"
        + "\n".join(lines)
        + "\n\n## 既存の記憶\n"
        + ("\n".join(memory_lines) or "（なし）")
        + "\n\n## 世話の様子\n"
        + "\n".join(_care_lines(store, plant_id, session_id, person_id))
    )
    return [{"role": "system", "content": SYSTEM_MESSAGE}, {"role": "user", "content": user}]


def _care_lines(
    store: PlantStore, plant_id: str, session_id: int, person_id: int | None
) -> list[str]:
    """振り返りに渡す「世話の様子」の行を作る（仕様 §6.7、#83）。

    植物の性格は、話し方だけでなく世話のしかたでも変わるようにする。
    放っておかれるとすねる（affection が下がる）、こまめに水をもらうと元気（energy が上がる）。
    LLM が判断できるよう、話しかけの間隔と水やりの間隔を、会話ログと一緒に渡す。

    日数は「この会話が始まった時刻」から数える。振り返りは会話が終わったあと（ときには
    エージェントの再起動後に何日も遅れて）行われるので、振り返った時刻から数えるとずれるため。

    戻り値の例:
        ["- 前にこの相手と話してから3日。",
         "- この会話で水をもらった。その前に水をもらってから4日。"]
    """
    session = store.get_session(session_id)
    started = parse_iso(session.started_at)

    # 1. 話しかけの間隔。この相手（お客さんなら相手を問わず）との、この会話より前の会話から数える
    previous_talk = store.last_talk_at(plant_id, person_id, before=started)
    if previous_talk is None:
        talk = "- この相手と話すのは初めて。"
    else:
        days = days_between(previous_talk, started)
        talk = (
            "- 前にこの相手と話したのは同じ日。"
            if days == 0
            else f"- 前にこの相手と話してから{days}日。"
        )

    # 2. 水やり。この会話で水をもらったかと、この会話より前の最後の水やりから何日か
    previous_water = store.last_watered_at(plant_id, before=started)
    if store.watered_in_session(session_id):
        water = "- この会話で水をもらった。" + (
            f"その前に水をもらってから{days_between(previous_water, started)}日。"
            if previous_water
            else "それまで一度も水をもらったことがなかった。"
        )
    else:
        water = "- この会話では水をもらっていない。" + (
            f"最後に水をもらってから{days_between(previous_water, started)}日。"
            if previous_water
            else "まだ一度も水をもらったことがない。"
        )
    return [talk, water]


async def reflect_session(
    store: PlantStore, plant_id: str, session_id: int, complete_json: JsonCompleter, now: datetime
) -> bool:
    """1セッションぶんの reflection。成功（または不要）なら True。

    失敗（2 回試しても LLM の出力が使えない）なら False を返し、DB 上は「未処理」のまま残す。
    次にエージェントが起動したとき、もう一度試される（conversation.py の start）。
    """
    # 1. ユーザーが一言も話していない（「ふたまる」だけで終わった）なら、LLM を呼ばず処理済みにする
    session = store.get_session(session_id)
    if session.user_turns == 0:
        store.mark_reflected(session_id)
        return True

    # 2. LLM に振り返らせる。出力が壊れていたら 1 回だけ再試行する
    messages = _build_messages(store, plant_id, session_id, session.person_id, now)
    result: ReflectionResult | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            result = parse_reflection(await complete_json(messages, REFLECTION_SCHEMA))
            break
        except Exception:
            logger.warning(
                "reflection failed (session=%s, attempt=%d)", session_id, attempt, exc_info=True
            )
    if result is None:
        return False

    # 3. 新しい性格を計算する。おしゃべり度には直近 7 日間に話しかけられた回数も使う
    #    話し相手が分かっていればその人の性格・発話数、いなければ（ゲスト）株の性格を使う
    person_id = session.person_id
    recent = store.count_user_utterances_since(
        plant_id, now - timedelta(days=7), person_id=person_id
    )
    base = store.get_person(person_id).traits if person_id else store.get_plant(plant_id).traits
    traits = apply_deltas(base, result.trait_deltas, recent)
    # 4. 会話中に見分けられなかった水やりを記録する（#81）。
    #    会話中は「水」＋「あげた」のような決まった言い方しか見分けられない（watering.py）。
    #    「ジョウロで湿らせといた」のような言い方は、ここで LLM が watered=true にして拾う。
    #    会話中にもう記録していれば、add_watering が二重に記録しない。
    #    時刻は振り返った時刻（now）ではなく、会話が終わった時刻（ended_at）にする。
    #    エージェントを再起動したときにまとめて振り返ると、now は何日も後の時刻になることがあるため
    if result.watered:
        watered_at = parse_iso(session.ended_at) if session.ended_at else now
        store.add_watering(plant_id, person_id, session_id, watered_at)
    # 5. 要約・記憶・性格を、まとめて 1 回で DB に保存する（途中で失敗しても半端に残らない）
    store.apply_reflection(
        plant_id,
        session_id,
        result.summary,
        result.new_memories,
        result.updated_memories,
        traits,
        now,
        person_id=person_id,
        owner_dialect=result.owner_dialect,
    )
    return True


async def recover(
    store: PlantStore, plant_id: str, complete_json: JsonCompleter, now: datetime
) -> None:
    """起動時の修復: 閉じていないセッションを閉じ、未処理の reflection を再実行する（§6.3 手順8）。"""
    repaired = store.repair_unfinished_sessions(plant_id)
    if repaired:
        logger.info("repaired %d unfinished sessions for %s", repaired, plant_id)
    for session_id in store.unreflected_sessions(plant_id):
        await reflect_session(store, plant_id, session_id, complete_json, now)
