"""中継（relay）を起動する（仕様 §3.2）。

    python -m agent.relay

.env（環境変数）の LIVEKIT_URL・LIVEKIT_API_KEY・LIVEKIT_API_SECRET・RELAY_DEVICE_KEY・RELAY_PORT を使う。
Docker では compose.yaml の relay サービスがこれを動かす。
"""

from __future__ import annotations

import logging

from aiohttp import web
from dotenv import load_dotenv

from agent.config import load_relay_config
from agent.relay.bridge import livekit_bridge_factory
from agent.relay.server import create_app


def main() -> None:
    """設定を読み、中継の WebSocket サーバーを起動する（Ctrl+C で止まる）。"""
    # 1. .env を読み込む（Docker では compose が環境変数として渡すので、無くてもよい）
    load_dotenv()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    # 2. 設定を読み、LiveKit に入るための関数を作る
    cfg = load_relay_config()
    factory = livekit_bridge_factory(cfg.livekit_url, cfg.livekit_api_key, cfg.livekit_api_secret)

    # 3. サーバーを起動する。0.0.0.0 にするのは、Cloudflare Tunnel のコンテナから届くようにするため
    #    （compose では PC の外にはポートを公開しない）
    web.run_app(create_app(factory, cfg.device_key), host="0.0.0.0", port=cfg.port)


if __name__ == "__main__":
    main()
