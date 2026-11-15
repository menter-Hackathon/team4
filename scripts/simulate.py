"""会話ログを流し込んで成長を早送りするデモ用スクリプト（仕様 §1 成功基準5, §11）。

uv run python -m scripts.simulate scripts/samples/plant-a.json scripts/samples/plant-b.json --ask "ただいま"

入力 JSON:
  {"plant_id": "...", "sessions": [{"started_at": "<ISO>", "turns": [{"role": "user"|"plant", "text": "..."}]}]}
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv
from openai import AsyncOpenAI

from agent.config import load_config
from agent.plant.personality import TRAIT_NAMES
from agent.plant.prompt import build_system_prompt
from agent.plant.reflection import JsonCompleter, reflect_session
from agent.plant.store import PlantStore, TraitSnapshot
from agent.plant.timeutil import parse_iso
from agent.plugins.llm import create_json_completer

TURN_INTERVAL = timedelta(seconds=10)


async def simulate(
    store: PlantStore, log: dict, complete_json: JsonCompleter
) -> list[TraitSnapshot]:
    plant_id = log["plant_id"]
    sessions = log["sessions"]
    if sessions:
        store.get_or_create_plant(plant_id, parse_iso(sessions[0]["started_at"]))
    for s in sessions:
        start = parse_iso(s["started_at"])
        sid = store.start_session(plant_id, start)
        now = start
        for i, turn in enumerate(s["turns"]):
            now = start + TURN_INTERVAL * i
            store.add_utterance(sid, turn["role"], turn["text"], now)
        store.end_session(sid, now)
        ok = await reflect_session(store, plant_id, sid, complete_json, now)
        if not ok:
            print(f"[warn] reflection failed: {plant_id} session {sid}")
    return store.trait_history(plant_id)


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
    for path in args.logs:
        log = json.loads(Path(path).read_text(encoding="utf-8"))
        hist = await simulate(store, log, complete_json)
        plant_id = log["plant_id"]
        _print_history(plant_id, hist)
        plant = store.get_plant(plant_id)
        now = parse_iso(hist[-1].recorded_at)
        prompt = build_system_prompt(
            plant.name,
            plant.traits,
            store.get_memories(plant_id),
            store.latest_summary(plant_id),
            now,
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
    asyncio.run(_main(parser.parse_args()))


if __name__ == "__main__":
    main()
