"""株が知っている人の一覧を見る・方言を固定する／外す（デモ用）。

uv run python -m scripts.people --plant-id a1
uv run python -m scripts.people --plant-id a1 --person たろう --dialect kansai
uv run python -m scripts.people --plant-id a1 --person タロウ --auto
"""

from __future__ import annotations

import argparse
import os
from datetime import datetime

from dotenv import load_dotenv

from agent.plant.dialect import DIALECTS, resolve_dialect
from agent.plant.store import Person, PlantStore
from agent.plant.wake import normalize


def _find_by_name(store: PlantStore, plant_id: str, name: str) -> Person | None:
    """名前で人を探す。ひらがな・カタカナや空白の違いは無視して比べる。"""
    target = normalize(name)
    for person in store.get_people(plant_id):
        if normalize(person.name) == target:
            return person
    return None


def _print_people(store: PlantStore, plant_id: str) -> None:
    """株が知っている人を 1 人 1 行で表示する。"""
    people = store.get_people(plant_id)
    if not people:
        print("まだ誰も知りません")
        return
    for p in people:
        t = p.traits
        recent = store.recent_dialects(p.id)
        current = resolve_dialect(p.dialect_override, recent)
        mode = "固定" if p.dialect_override else "自動"
        print(
            f"[{p.id}] {p.name}  "
            f"talkativeness={t.talkativeness:.2f} affection={t.affection:.2f} "
            f"energy={t.energy:.2f} curiosity={t.curiosity:.2f}  "
            f"方言={current}（{mode}）  直近の判定={recent}"
        )


def main(argv: list[str], store: PlantStore, now: datetime) -> int:
    """コマンドの本体。成功なら 0、エラーなら 1 を返す（テストから直接呼べる）。"""
    parser = argparse.ArgumentParser(description="株が知っている人の一覧表示と方言の固定")
    parser.add_argument("--plant-id", required=True)
    who = parser.add_mutually_exclusive_group()
    who.add_argument("--person", help="名前（ひらがな・カタカナの違いは無視する）")
    who.add_argument("--person-id", type=int)
    change = parser.add_mutually_exclusive_group()
    change.add_argument("--dialect", choices=DIALECTS, help="この方言に固定する")
    change.add_argument("--auto", action="store_true", help="固定を外して自動判定に戻す")
    parser.add_argument("--db", help="DB のパス（省略時は FUTAMARU_DB_PATH）")
    args = parser.parse_args(argv)

    # 人を指定しなければ一覧だけ表示する
    if args.person is None and args.person_id is None:
        store.get_or_create_plant(args.plant_id, now)
        _print_people(store, args.plant_id)
        return 0

    if args.dialect is None and not args.auto:
        print("--dialect か --auto を指定してください")
        return 1

    store.get_or_create_plant(args.plant_id, now)
    if args.person_id is not None:
        try:
            person = store.get_person(args.person_id)
        except KeyError:
            person = None
        if person is None or person.plant_id != args.plant_id:
            print(f"person-id {args.person_id} は株 {args.plant_id} にいません")
            return 1
    else:
        person = _find_by_name(store, args.plant_id, args.person)
        if person is None:
            if args.auto:
                print(f"「{args.person}」はまだ登録されていません")
                return 1
            person = store.add_person(args.plant_id, args.person, now)

    store.set_dialect_override(person.id, args.dialect)  # --auto のときは None
    print(
        f"{person.name}: " + (f"{args.dialect} に固定" if args.dialect else "自動判定に戻しました")
    )
    return 0


if __name__ == "__main__":
    load_dotenv()
    # --db だけ先に読み取って DB を開く（残りの引数は main が解釈する）
    import sys

    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--db")
    known, _ = pre.parse_known_args()
    db_path = known.db or os.environ.get("FUTAMARU_DB_PATH", "./data/futamaru.db")
    raise SystemExit(main(sys.argv[1:], PlantStore(db_path), datetime.now().astimezone()))
