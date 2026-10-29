"""Small helpers for the ops-sync release handoff."""

from __future__ import annotations

import hashlib
from pathlib import Path


def digest(path: str | Path) -> str:
    """Return the SHA-256 hex digest of a file's bytes."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def normalize(values: list[str]) -> list[str]:
    """Drop blank lines and comments, and strip surrounding whitespace."""
    return [
        line.strip()
        for line in values
        if line.strip() and not line.lstrip().startswith("#")
    ]
