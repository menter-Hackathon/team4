"""ESP32 用の LiveKit 長期トークンを発行する（仕様 §3）。

uv run python -m scripts.issue_token --plant-id a1
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import timedelta

from dotenv import load_dotenv
from livekit import api


def build_token(plant_id: str, api_key: str, api_secret: str, ttl_days: int = 30) -> str:
    return (
        api.AccessToken(api_key, api_secret)
        .with_identity(f"device-{plant_id}")
        .with_grants(
            api.VideoGrants(
                room_join=True, room=f"plant-{plant_id}", can_publish=True, can_subscribe=True
            )
        )
        .with_ttl(timedelta(days=ttl_days))
        .to_jwt()
    )


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="ESP32 用の LiveKit トークンを発行する")
    parser.add_argument("--plant-id", required=True)
    parser.add_argument("--ttl-days", type=int, default=30)
    args = parser.parse_args()
    key, secret = os.environ.get("LIVEKIT_API_KEY"), os.environ.get("LIVEKIT_API_SECRET")
    if not key or not secret:
        sys.exit("LIVEKIT_API_KEY と LIVEKIT_API_SECRET を設定してください")
    print(build_token(args.plant_id, key, secret, args.ttl_days))


if __name__ == "__main__":
    main()
