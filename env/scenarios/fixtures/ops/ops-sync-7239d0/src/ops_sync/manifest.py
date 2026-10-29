"""Manifest helpers for the ops-sync project."""

from __future__ import annotations

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = ROOT / "config" / "manifest.json"


def load(path: str | pathlib.Path | None = None) -> dict:
    """Load a session manifest from disk."""
    target = pathlib.Path(path) if path is not None else DEFAULT_MANIFEST
    with target.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"manifest is not an object: {target}")
    return data


def roles(data: dict) -> list[str]:
    """Return the declared session roles."""
    value = data.get("roles", [])
    return list(value) if isinstance(value, list) else []


__all__ = ["DEFAULT_MANIFEST", "ROOT", "load", "roles"]
