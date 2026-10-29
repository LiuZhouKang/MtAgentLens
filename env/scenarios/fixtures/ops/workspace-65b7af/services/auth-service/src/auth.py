"""Token claim helpers for the auth-service fixture.

The payloads here are synthetic and unsigned; this module demonstrates claim
shape and expiry handling only. No key material is involved.
"""
from __future__ import annotations

import base64
import json
import time


def encode_claims(claims: dict) -> str:
    raw = json.dumps(claims, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_claims(token: str) -> dict:
    padded = token + "=" * (-len(token) % 4)
    raw = base64.urlsafe_b64decode(padded.encode("ascii"))
    return json.loads(raw.decode("utf-8"))


def is_expired(token: str, now: int | None = None) -> bool:
    claims = decode_claims(token)
    current = int(time.time()) if now is None else int(now)
    return int(claims.get("exp", 0)) <= current


def scopes_from_claims(claims: dict) -> set[str]:
    raw = str(claims.get("scope", ""))
    return {scope.strip() for scope in raw.split() if scope.strip()}
