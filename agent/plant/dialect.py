"""方言の決め方と、方言ごとの指示文（設計: 話し相手の見分けと方言）。

ふたまるは、話し相手の方言をまねて話す。方言は次の 2 つで決まる:
    1. 手で固定した方言（people.dialect_override）があれば、それを使う
    2. 無ければ、その人との直近 5 回の判定の多数決（同数なら新しいほう）。判定が無ければ標準語

このファイルは DB にも LiveKit にも依存しない。
"""

from __future__ import annotations

from collections import Counter

# 使える方言。DB（sessions.owner_dialect / people.dialect_override）にはこの文字列で保存する
DIALECTS = ("standard", "kansai", "hakata", "hiroshima")
# 「判断できなかった」を表す値。発話が短い・少ないセッションに付く。多数決には数えない
UNKNOWN = "unknown"
# 多数決で見る判定の件数
RECENT_LIMIT = 5

# システムプロンプトに足す 1 段落。標準語は足さないので、ここには入れない
_PROMPTS = {
    "kansai": "関西弁で話す（例: 〜やで、〜やん、ほんまに、おおきに）。",
    "hakata": "博多弁で話す（例: 〜ばい、〜たい、〜と？、よかよ）。",
    "hiroshima": "広島弁で話す（例: 〜じゃけぇ、〜じゃん、〜よの、ええよ）。",
}

# TTS（声の出し方）の指示に足す文。標準語は足さない
_TTS = {
    "kansai": "関西弁のイントネーションで話す。",
    "hakata": "博多弁のイントネーションで話す。",
    "hiroshima": "広島弁のイントネーションで話す。",
}


def resolve_dialect(override: str | None, recent: list[str]) -> str:
    """その人に使う方言を決める。

    Args:
        override: 手で固定した方言。無ければ None
        recent: その人の直近の判定（unknown は除いてある）。**新しい順**

    Returns:
        DIALECTS のどれか。

    例:
        resolve_dialect("hakata", ["kansai"] * 5)  -> "hakata"（固定が最優先）
        resolve_dialect(None, ["hakata", "kansai"])  -> "hakata"（同数なら新しいほう）
        resolve_dialect(None, [])  -> "standard"
    """
    # 1. 固定があればそれを使う
    if override:
        return override
    # 2. 新しい順の先頭 5 件だけを数える（6 件目以降は古いので無視する）
    votes = recent[:RECENT_LIMIT]
    if not votes:
        return "standard"  # 3. 判定が 1 つも無ければ標準語
    counts = Counter(votes)
    top = max(counts.values())
    # 4. 最多が複数なら、votes の中で先に出てくる（= 新しい）ものを選ぶ
    for d in votes:
        if counts[d] == top:
            return d
    return "standard"  # ここには来ない（votes が空でないので必ず上で返る）


def dialect_prompt(dialect: str) -> str | None:
    """システムプロンプトに足す方言の指示を返す。標準語（や未知の値）なら None。"""
    return _PROMPTS.get(dialect)


def dialect_tts(dialect: str) -> str | None:
    """TTS の指示に足す方言の指示を返す。標準語（や未知の値）なら None。"""
    return _TTS.get(dialect)
