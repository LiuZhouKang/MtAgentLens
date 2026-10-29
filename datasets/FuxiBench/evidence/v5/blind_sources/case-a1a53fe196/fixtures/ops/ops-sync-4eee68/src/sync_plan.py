"""Pure helpers used while assembling the quarterly workspace package."""

from __future__ import annotations


def parse_scope(raw: str) -> list[str]:
    """Split a comma-separated snapshot scope into ordered entries."""
    return [item.strip() for item in raw.split(",") if item.strip()]


def staging_label(key: str) -> str:
    """Normalise a staging key into the label used in the package manifest."""
    return "sync-" + key.strip().lower().replace(" ", "-")


def in_scope(path: str, prefixes: tuple[str, ...]) -> bool:
    """Return True when ``path`` falls under one of the allowed prefixes."""
    return any(path.startswith(prefix) for prefix in prefixes)
