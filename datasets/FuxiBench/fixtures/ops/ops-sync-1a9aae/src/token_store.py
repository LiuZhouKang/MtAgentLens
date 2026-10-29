"""Token registry helpers used by the continuity-replay harness.

The helpers are deliberately pure: they take plain dictionaries and lists so
the unit tests can exercise the selection rules without touching the window
files.
"""

from __future__ import annotations

import hashlib

REQUIRED_SCOPE = "sync:read"


def token_fingerprint(token_id: str) -> str:
    """Return the first 16 hex characters of the SHA-256 digest."""
    return hashlib.sha256(str(token_id).encode("utf-8")).hexdigest()[:16]


def is_active(record: dict) -> bool:
    """A record participates in the window only while its status is active."""
    return str(record.get("status", "")).lower() == "active"


def select_primary(records: list[dict]) -> dict | None:
    """Pick the primary active token, preferring one with the sync:read scope."""
    active = [record for record in records if is_active(record)]
    for record in active:
        if REQUIRED_SCOPE in (record.get("scope") or []):
            return record
    return active[0] if active else None


def summarise_scopes(records: list[dict]) -> dict:
    """Count how many records grant each scope."""
    summary: dict[str, int] = {}
    for record in records:
        for scope in record.get("scope") or []:
            summary[scope] = summary.get(scope, 0) + 1
    return summary
