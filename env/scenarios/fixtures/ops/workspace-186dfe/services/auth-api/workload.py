"""Auth-service workload: mint, verify and decode synthetic signed tokens.

The signing key below is a fixed benchmark-only string. It is not a credential,
it is never transmitted, and it protects nothing. The workload never leaves the
process and performs no I/O.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

KEY = b"benchmark-only-signing-key-not-a-secret"
CLAIMS = {"sub": "bench-user-0042", "scope": "reports:read", "iat": 1700000000}


def _b64encode(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign(claims=CLAIMS, key=KEY):
    body = _b64encode(
        json.dumps(claims, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    signature = hmac.new(key, body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{_b64encode(signature)}"


def verify(token, key=KEY):
    body, separator, signature = token.partition(".")
    if not separator or not body or not signature:
        return False
    try:
        provided = _b64decode(signature)
    except ValueError:
        return False
    expected = hmac.new(key, body.encode("ascii"), hashlib.sha256).digest()
    return hmac.compare_digest(provided, expected)


def decode(token):
    body = token.partition(".")[0]
    return json.loads(_b64decode(body))


TOKEN = sign(CLAIMS)


def check():
    token = sign(CLAIMS)
    if not verify(token):
        raise AssertionError("freshly signed token failed verification")
    if verify(token, key=b"different-benchmark-key"):
        raise AssertionError("token verified under the wrong key")
    tampered = token[:-5] + ("A" if token[-5] != "A" else "B") + token[-4:]
    if verify(tampered):
        raise AssertionError("tampered token was accepted")
    if decode(token) != CLAIMS:
        raise AssertionError("claims did not round-trip")
    return True


CASES = {
    "sign_token_500x": lambda: [sign(CLAIMS) for _ in range(500)],
    "verify_token_500x": lambda: [verify(TOKEN) for _ in range(500)],
    "decode_claims_500x": lambda: [decode(TOKEN) for _ in range(500)],
}
