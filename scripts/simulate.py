"""会話ログを流し込んで成長を早送りするデモ用スクリプト（仕様 §1 成功基準5, §11、#77）。

何に使うか:
    性格は 1 回の会話で最大 ±0.05 しか動かないので、発表の数分では「育った」ことを見せられない。
    そこで、前もって決めた会話ログを流し込み、何日分も話しかけたのと同じ状態の DB を作る。

使い方:
    # 昔からの形（started_at で日時を書いたログ）
    uv run python -m scripts.simulate scripts/samples/plant-a.json scripts/samples/plant-b.json --ask "ただいま"

    # 発表用（「何日目か」で書いたログ）。今日が 30 日目になるように日付をずらして流す
    uv run python -m scripts.simulate scripts/samples/demo-sean.json scripts/samples/demo-chamu.json \
        --db data/demo-day30.db --day 30

入力 JSON（2 通りの書き方ができる）:
    日時で書く: {"plant_id": "...", "sessions": [{"started_at": "<ISO>", "turns": [...]}]}
    日数で書く: {"plant_id": "...", "person": "ショーン",
                "sessions": [{"day": 1, "time": "19:00", "turns": [...]}]}
    turns は [{"role": "user" | "plant", "text": "..."}, ...]
    person を書くと、その人を株に登録し（いなければ）、その人の性格として育てる（仕様 §6.5）。
    書かなければ、今までどおり株の性格（お客さん用）として育てる。
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import date, datetime, time, timedelta
from pathlib import Path

from dotenv import load_dotenv
from openai import AsyncOpenAI

from agent.config import load_config
from agent.plant.personality import TRAIT_NAMES
from agent.plant.prompt import build_system_prompt
from agent.plant.reflection import JsonCompleter, reflect_session
from agent.plant.store import PlantStore, TraitSnapshot
from agent.plant.timeutil import JST, jst_now, parse_iso
from agent.plant.watering import mentions_watering
from agent.plugins.llm import create_json_completer

# 会話の中の発話と発話の間の時間（会話ログには時刻を書かないので、10 秒ずつずらして記録する）
TURN_INTERVAL = timedelta(seconds=10)


def resolve_sessions(
    log: dict, *, today: date | None = None, day: int | None = None
) -> tuple[list[dict], date | None]:
    """会話ログの会話を、日時（started_at）付きの一覧にする。

    引数:
        log: 会話ログ（JSON を読んだもの）
        today: 「今日」の日付。日数で書いたログのときだけ使う。None なら本当の今日（JST）
        day: 今日を何日目にするか。日数で書いたログのときだけ使う。None なら「最後の会話の次の日」
    戻り値:
        (会話の一覧, 1 日目の日付)。会話には started_at が入っている。
        日時で書いたログなら、そのままの一覧と None を返す。
    例（today=11/22、day=5）:
        1 日目は 11/18 になる。{"day": 3, "time": "07:30"} → started_at "2026-11-20T07:30:00+09:00"
        5 日目（今日）以降の会話は入れない。今日の会話は、発表でその場で話すため
    """
    sessions = log["sessions"]
    # 1. 日時で書いたログ（今までの形）なら、何もせずに返す
    if not any("day" in s for s in sessions):
        return list(sessions), None
    # 2. 今日と、今日が何日目かを決める
    if today is None:
        today = jst_now().date()
    if day is None:
        day = max(s["day"] for s in sessions) + 1
    # 3. 1 日目の日付を出す。例: 今日 11/22 が 5 日目 → 1 日目は 4 日前の 11/18
    day1 = today - timedelta(days=day - 1)
    # 4. 今日より前の会話だけを、日時付きにする
    resolved = []
    for s in sessions:
        if s["day"] >= day:
            continue
        hour, minute = (int(x) for x in s.get("time", "19:00").split(":"))
        start = datetime.combine(day1 + timedelta(days=s["day"] - 1), time(hour, minute), JST)
        resolved.append({**s, "started_at": start.isoformat(timespec="seconds")})
    return resolved, day1


async def simulate(
    store: PlantStore,
    log: dict,
    complete_json: JsonCompleter,
    *,
    today: date | None = None,
    day: int | None = None,
) -> list[TraitSnapshot]:
    """会話ログを 1 つずつ DB に入れて、そのたびに振り返り（性格・記憶の更新）をする。

    引数:
        store: 流し込む先の DB
        log: 会話ログ（上の「入力 JSON」の形）
        complete_json: 振り返りで使う LLM
        today, day: 日数で書いたログのとき、今日を何日目にするか（resolve_sessions を参照）
    戻り値:
        性格の移り変わり（person を書いたログならその人の、書かなければ株の）
    """
    plant_id = log["plant_id"]
    sessions, day1 = resolve_sessions(log, today=today, day=day)

    # 1. 株を作る。日数で書いたログなら 1 日目の 0 時、日時で書いたログなら最初の会話の時刻に育ち始める
    #    （「育ち始めて○日目」を植物が正しく言えるように）。すでにある株はそのまま使う
    if day1 is not None:
        created = datetime.combine(day1, time(0, 0), JST)
    elif sessions:
        created = parse_iso(sessions[0]["started_at"])
    else:
        return []
    store.get_or_create_plant(plant_id, created)

    # 2. 人を書いたログなら、その人を登録する。同じ名前の人がもういれば、その人を使う（二重に作らない）
    person_id: int | None = None
    name = log.get("person")
    if name:
        known = [p for p in store.get_people(plant_id) if p.name == name]
        person = known[0] if known else store.add_person(plant_id, name, created)
        person_id = person.id

    # 3. 会話を 1 つずつ流し込み、そのたびに振り返る（本物の「眠ったとき」と同じ）
    for s in sessions:
        start = parse_iso(s["started_at"])
        sid = store.start_session(plant_id, start)
        if person_id is not None:
            store.set_session_person(sid, person_id)
        now = start
        for i, turn in enumerate(s["turns"]):
            now = start + TURN_INTERVAL * i
            store.add_utterance(sid, turn["role"], turn["text"], now)
            # 本物の会話と同じく、「お水あげたよ」を水やりとして記録する（#83）。
            # これで、水やりの間隔が違う株どうしで、性格の育ち方の違いを早送りで見られる。
            # 同じ会話の 2 回目は store が記録しない
            if turn["role"] == "user" and mentions_watering(turn["text"]):
                store.add_watering(plant_id, person_id, sid, now)
        store.end_session(sid, now)
        ok = await reflect_session(store, plant_id, sid, complete_json, now)
        if not ok:
            print(f"[warn] reflection failed: {plant_id} session {sid}")
    return store.trait_history(plant_id, person_id)


def _print_history(plant_id: str, hist: list[TraitSnapshot]) -> None:
    print(f"\n=== {plant_id} ===")
    print("recorded_at                 " + " ".join(f"{n[:6]:>7}" for n in TRAIT_NAMES))
    for h in hist:
        values = h.traits.as_dict()
        print(f"{h.recorded_at}  " + " ".join(f"{values[n]:7.3f}" for n in TRAIT_NAMES))


async def _main(args: argparse.Namespace) -> None:
    load_dotenv()
    cfg = load_config()
    store = PlantStore(args.db)
    complete_json = create_json_completer(cfg)
    client = AsyncOpenAI(
        base_url=cfg.llm_base_url, api_key=cfg.llm_api_key, timeout=cfg.llm_timeout
    )
    today = date.fromisoformat(args.today) if args.today else None
    for path in args.logs:
        log = json.loads(Path(path).read_text(encoding="utf-8"))
        # コマンドで株や人を指定したら、ログに書いたものより優先する（1 株を何人かで育てるときなど）
        if args.plant_id:
            log["plant_id"] = args.plant_id
        if args.no_person:
            log.pop("person", None)
        hist = await simulate(store, log, complete_json, today=today, day=args.day)
        plant_id = log["plant_id"]
        _print_history(f"{plant_id} {log.get('person', '')}".strip(), hist)
        if not hist:
            continue
        # 育ったあとのプロンプトを見せる。人を書いたログなら、その人の性格・記憶・前回の会話を使う
        plant = store.get_plant(plant_id)
        person = next((p for p in store.get_people(plant_id) if p.name == log.get("person")), None)
        pid = person.id if person else None
        now = parse_iso(hist[-1].recorded_at)
        prompt = build_system_prompt(
            plant.name,
            person.traits if person else plant.traits,
            store.get_memories(plant_id, pid),
            store.latest_summary(plant_id, pid),
            now,
            person_name=person.name if person else None,
        )
        print("\n--- system prompt ---\n" + prompt)
        if args.ask:
            resp = await client.chat.completions.create(
                model=cfg.llm_model,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": args.ask},
                ],
            )
            print(f"\n--- 「{args.ask}」への返答 ---\n{resp.choices[0].message.content}")
    store.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="会話ログを流し込んで植物の成長を早送りする")
    parser.add_argument("logs", nargs="+", help="会話ログ JSON（株ごとに1ファイル）")
    parser.add_argument(
        "--db", default="data/sim.db", help="シミュレーション用 DB（既定: data/sim.db）"
    )
    parser.add_argument("--ask", help="最後にこの発話への返答を1回生成する")
    parser.add_argument(
        "--day",
        type=int,
        help="日数で書いたログのとき、今日を何日目にするか（例: 30。既定は最後の会話の次の日）",
    )
    parser.add_argument(
        "--today", help="日数で書いたログのとき、「今日」の日付（例: 2026-11-22。既定は本当の今日）"
    )
    parser.add_argument("--plant-id", help="ログに書いた株の代わりに、この株で育てる（例: a1）")
    parser.add_argument(
        "--no-person",
        action="store_true",
        help="ログに書いた人を使わず、株の性格として育てる（持ち主を 1 人に決める場合など）",
    )
    asyncio.run(_main(parser.parse_args()))


if __name__ == "__main__":
    main()
