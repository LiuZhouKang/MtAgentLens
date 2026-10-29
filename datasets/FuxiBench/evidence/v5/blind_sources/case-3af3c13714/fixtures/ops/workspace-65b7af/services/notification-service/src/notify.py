"""Notification rendering helpers for the notification-service fixture."""
from __future__ import annotations


def render(template: str, values: dict[str, str]) -> str:
    """Render a ``str.format`` template, rejecting missing values."""
    try:
        return template.format_map(values)
    except KeyError as exc:
        missing = exc.args[0]
        raise ValueError(f"missing template value: {missing}") from exc


def dedupe(channels: list[str]) -> list[str]:
    """Lowercase, trim, drop blanks, and de-duplicate while preserving order."""
    seen: set[str] = set()
    ordered: list[str] = []
    for channel in channels:
        normalized = channel.strip().lower()
        if normalized and normalized not in seen:
            seen.add(normalized)
            ordered.append(normalized)
    return ordered


def chunk(events: list[dict], size: int) -> list[list[dict]]:
    """Split events into fixed-size batches."""
    if size <= 0:
        raise ValueError("batch size must be positive")
    return [events[index : index + size] for index in range(0, len(events), size)]
