"""Pure helpers that normalize report records."""

from __future__ import annotations

from typing import Iterable


def normalize_record(record: dict) -> dict:
    """Return a record with stable keys and trimmed string values."""
    normalized = {}
    for key, value in record.items():
        if isinstance(value, str):
            normalized[key] = value.strip()
        else:
            normalized[key] = value
    return normalized


def filter_ok(records: Iterable[dict]) -> list[dict]:
    """Keep records whose status is ``ok`` after normalization."""
    kept = []
    for record in records:
        normalized = normalize_record(record)
        if normalized.get("status") == "ok":
            kept.append(normalized)
    return kept
