import base64
import json
import time

from livekit import api

from scripts.issue_token import build_token

SECRET = "secret" * 6


def jwt_payload(token):
    part = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))


def test_token_claims():
    tok = build_token("a1", "key", SECRET)
    claims = api.TokenVerifier("key", SECRET).verify(tok)
    assert claims.identity == "device-a1"
    assert claims.video.room == "plant-a1" and claims.video.room_join
    assert claims.video.can_publish and claims.video.can_subscribe
    exp = jwt_payload(tok)["exp"]
    assert 29.9 * 86400 < exp - time.time() <= 30 * 86400 + 5
