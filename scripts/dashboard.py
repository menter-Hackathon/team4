"""性格・成長・記憶を見せるダッシュボード（仕様 §6.6、#59）。

uv run python -m scripts.dashboard --db data/futamaru.db --plants a1,b1
→ ブラウザで http://127.0.0.1:8765 を開く

しくみ:
    1. ブラウザが / を開くと、HTML 1 枚（scripts/dashboard.html）を返す
    2. HTML の中の JavaScript が、数秒ごとに /api/state を読みに来る
    3. /api/state は、そのたびに DB を「読むだけ」で開き、株ごとの性格・成長・記憶を JSON にして返す
    4. グラフは HTML 側で Chart.js が描く
    5. 性格の 16 タイプ（「〇〇ふたまる」、#64）のイラストは /types/<英語名>.png で返す

DB には書き込まない。エージェントが動いている最中に開いても大丈夫なようにするため。
会話の記憶が見えるので、この PC からだけ開けるよう 127.0.0.1 で待ち受ける（0.0.0.0 にはしない）。
Docker で動かすときだけ、コンテナの中では --host 0.0.0.0 で待ち受け、PC 側の公開を 127.0.0.1 に絞る
（compose.yaml の dashboard。コンテナの中の 127.0.0.1 には、PC のブラウザから届かないため）。
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from agent.plant.dialect import resolve_dialect
from agent.plant.personality import Traits
from agent.plant.plant_type import ALL_TYPES, PlantType, plant_type
from agent.plant.prompt import PROMPT_MEMORY_LIMIT, rank_memories, recency_factor
from agent.plant.store import Person, PlantStore
from agent.plant.timeutil import iso, jst_now

# 待ち受けるアドレス。この PC の中からしか開けない（記憶をほかの人に見せないため、#59）
HOST = "127.0.0.1"
DEFAULT_PORT = 8765
# ブラウザに返す HTML。このファイルと同じフォルダに置いてある
HTML_PATH = Path(__file__).with_name("dashboard.html")
# 16 タイプのイラストを置くフォルダ（ファイル名は <英語名>.png、#64）。無いタイプは絵文字で出す
TYPE_IMAGE_DIR = Path(__file__).with_name("dashboard_assets") / "types"
# イラストとして返してよいファイル名の一覧（17 タイプの英語名だけ）。
# ブラウザから届いた名前をそのままファイルのパスに使わず、この一覧と照らし合わせることで、
# 「../」などでフォルダの外のファイル（DB など）を読まれないようにする
TYPE_IMAGE_NAMES = {f"{t.key}.png" for t in ALL_TYPES}


def _traits_json(t: Traits) -> dict[str, float]:
    """性格を JSON にする。表示用なので小数 3 桁に丸める。例: {"talkativeness": 0.55, ...}"""
    return {k: round(v, 3) for k, v in t.as_dict().items()}


def _type_json(t: PlantType) -> dict:
    """タイプを JSON にする。イラストのファイルがあれば、その URL も入れる（無ければ None）。

    例: {"key": "mint", "name": "ミントふたまる", "emoji": "🌿", "description": "…",
         "image": "/types/mint.png"}
    """
    has_image = (TYPE_IMAGE_DIR / f"{t.key}.png").is_file()
    return {
        "key": t.key,
        "name": t.name,
        "emoji": t.emoji,
        "description": t.description,
        "image": f"/types/{t.key}.png" if has_image else None,
    }


def view_state(
    store: PlantStore, plant_id: str, person: Person | None, traits: Traits, now: datetime
) -> dict:
    """1 人ぶん（person が None ならお客さん）の表示内容を作る。

    プロンプトを作るとき（仕様 §6.4, §6.5）と同じ材料を集める:
        traits:       今の性格（4 軸）
        history:      性格の移り変わり（成長曲線）。古い順
        memories:     覚えていること。プロンプトと同じ「重要度 × 新しさ」の上位 10 件
        last_summary: 前回の会話の要約（無ければ None）
        dialect:      その人に使う方言（お客さんは標準語）
        type:         今の性格の 16 タイプ（「〇〇ふたまる」、#64）。人ごとに違う
    """
    person_id = person.id if person else None
    # 1. 成長曲線。株を作った時点（または人を登録した時点）の初期値から、振り返りのたびに 1 行ずつある
    history = [
        {"recorded_at": h.recorded_at, "session_id": h.session_id, "traits": _traits_json(h.traits)}
        for h in store.trait_history(plant_id, person_id)
    ]
    # 2. 覚えていること。並べ方は prompt.rank_memories と同じにする（プロンプトに入るものと同じものが見える）。
    #    get_memories はその人の記憶だけを返す（お客さんならお客さんの記憶だけ、#70）
    top = rank_memories(store.get_memories(plant_id, person_id), now, PROMPT_MEMORY_LIMIT)
    memories = [
        {
            "content": m.content,
            "kind": m.kind,
            "importance": round(m.importance, 2),
            "score": round(m.importance * recency_factor(m.updated_at, now), 3),
        }
        for m in top
    ]
    # 3. 方言。人なら手で固定した方言か、直近の判定の多数決。お客さんは標準語（仕様 §6.5）
    if person is None:
        dialect = "standard"
    else:
        dialect = resolve_dialect(person.dialect_override, store.recent_dialects(person.id))
    return {
        "key": "guest" if person is None else f"person-{person.id}",
        "label": "お客さん（株の性格）" if person is None else person.name,
        "person_id": person_id,
        "traits": _traits_json(traits),
        "history": history,
        "memories": memories,
        "last_summary": store.latest_summary(plant_id, person_id),
        "dialect": dialect,
        "type": _type_json(plant_type(traits)),
    }


def plant_state(store: PlantStore, plant_id: str, now: datetime) -> dict:
    """1 株ぶんの表示内容を作る。

    戻り値の例:
        {"plant_id": "a1", "found": True, "name": "ふたまる",
         "views": [{"key": "guest", ...}, {"key": "person-1", "label": "たろう", ...}]}
    views の先頭はお客さん（株の性格）、続いて知っている人を登録順に並べる。
    株がまだ DB に無ければ {"plant_id": "a1", "found": False}。
    """
    try:
        plant = store.get_plant(plant_id)
    except KeyError:
        return {"plant_id": plant_id, "found": False}
    views = [view_state(store, plant_id, None, plant.traits, now)]
    for person in store.get_people(plant_id):
        views.append(view_state(store, plant_id, person, person.traits, now))
    return {"plant_id": plant_id, "found": True, "name": plant.name, "views": views}


def dashboard_state(db_path: str, plant_ids: list[str], now: datetime) -> dict:
    """ページ全体の表示内容を作る。リクエストのたびに DB を読むだけで開き、読み終えたら閉じる。

    毎回開き直すのは、エージェントが書き込んだばかりの内容を確実に読むため（開きっぱなしにしない）。
    DB が無い・開けないときは {"error": "..."} を返す（ページに理由を出すため）。
    """
    try:
        store = PlantStore(db_path, readonly=True)
    except sqlite3.OperationalError as e:
        return {"generated_at": iso(now), "error": f"DB を開けません: {db_path}（{e}）"}
    try:
        plants = [plant_state(store, pid, now) for pid in plant_ids]
    except sqlite3.Error as e:
        # 古い DB（people テーブルが無いなど）。エージェントを一度起動すれば列が足される（仕様 §6.5）
        return {"generated_at": iso(now), "error": f"DB を読めません: {e}"}
    finally:
        store.close()
    return {"generated_at": iso(now), "plants": plants}


def make_handler(db_path: str, plant_ids: list[str]) -> type[BaseHTTPRequestHandler]:
    """DB のパスと株を覚えた、リクエストの受け付け役（ハンドラ）のクラスを作る。

    http.server はクラスを渡す決まりなので、引数を閉じ込めたクラスをその場で作って返す。
    """

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # 名前は http.server の決まり（GET を受けると呼ばれる）
            """GET を受け付ける。/ → HTML、/api/state → JSON、/types/<名前>.png → イラスト、それ以外 → 404。"""
            path = self.path.split("?", 1)[0]
            if path.startswith("/types/"):
                self._send_type_image(path.removeprefix("/types/"))
            elif path == "/":
                self._send(200, "text/html; charset=utf-8", HTML_PATH.read_bytes())
            elif path == "/api/state":
                state = dashboard_state(db_path, plant_ids, jst_now())
                body = json.dumps(state, ensure_ascii=False).encode("utf-8")
                self._send(200, "application/json; charset=utf-8", body)
            else:
                self._send(404, "text/plain; charset=utf-8", b"not found")

        def _send_type_image(self, name: str) -> None:
            """タイプのイラストを返す。17 タイプの英語名以外・まだ描かれていないものは 404。"""
            # 一覧と完全に同じ名前だけを受け付ける（"../" や "%2e%2e" を含む名前は一覧に無いので 404）
            file = TYPE_IMAGE_DIR / name
            if name not in TYPE_IMAGE_NAMES or not file.is_file():
                self._send(404, "text/plain; charset=utf-8", b"not found")
                return
            self._send(200, "image/png", file.read_bytes())

        def _send(self, status: int, content_type: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")  # 自動更新で古い内容を見せない
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            # 数秒ごとのアクセスが標準出力を埋めないよう、ログは DEBUG に回す
            logging.getLogger(__name__).debug(format, *args)

    return Handler


def make_server(
    db_path: str, plant_ids: list[str], port: int, host: str = HOST
) -> ThreadingHTTPServer:
    """host の port で待ち受けるサーバーを作る（port=0 なら空いている番号。テスト用）。

    host は既定の 127.0.0.1（この PC からだけ開ける）のままにする。
    0.0.0.0 にするのは Docker のコンテナの中だけ（PC 側の公開は compose.yaml で 127.0.0.1 に絞る）。
    """
    return ThreadingHTTPServer((host, port), make_handler(db_path, plant_ids))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="ふたまるの性格・成長・記憶を見せるダッシュボード")
    parser.add_argument(
        "--db", default="data/futamaru.db", help="読む DB（既定: data/futamaru.db）"
    )
    parser.add_argument(
        "--plants",
        required=True,
        help="並べる株の plant_id をカンマ区切りで（例: a1,b1。simulate.py なら sample-a,sample-b）",
    )
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help=f"待ち受けるポート（既定: {DEFAULT_PORT}）"
    )
    parser.add_argument(
        "--host",
        default=HOST,
        help=f"待ち受けるアドレス（既定: {HOST}。Docker のコンテナの中でだけ 0.0.0.0 にする）",
    )
    args = parser.parse_args(argv)
    plant_ids = [p.strip() for p in args.plants.split(",") if p.strip()]
    # DB がまだ無くても起動はする（エージェントが後から作ることがあるため）。ただし理由がわかるように知らせる
    if not Path(args.db).exists():
        print(f"注意: DB がまだありません: {args.db}")
        print("  早送りデモなら先に scripts.simulate を実行してください（LLM が必要）。")
        print("  本番の DB なら、エージェントを起動して一度起こすと作られます。")
    server = make_server(args.db, plant_ids, args.port, args.host)
    # ブラウザで開く URL は、どちらの待ち受けでも 127.0.0.1（Docker でも PC 側は 127.0.0.1 に公開する）
    print(f"ダッシュボード: http://{HOST}:{server.server_address[1]}  （Ctrl+C で終了）")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
