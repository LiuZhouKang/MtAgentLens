"""Small reconciliation helpers used by the sync rollout checks."""

from __future__ import annotations

from typing import Mapping


def merge(base: Mapping[str, str], overlay: Mapping[str, str]) -> dict[str, str]:
    """Return a merged view where ``overlay`` wins on conflicting keys."""
    merged = dict(base)
    merged.update(overlay)
    return merged


def diff(before: Mapping[str, str], after: Mapping[str, str]) -> dict[str, tuple[str | None, str | None]]:
    """Return the changed keys as ``key -> (before, after)``."""
    changed: dict[str, tuple[str | None, str | None]] = {}
    for key in sorted(set(before) | set(after)):
        old = before.get(key)
        new = after.get(key)
        if old != new:
            changed[key] = (old, new)
    return changed


def apply(state: dict[str, str], changes: Mapping[str, str]) -> dict[str, str]:
    """Apply ``changes`` to ``state`` in place and return it."""
    state.update(changes)
    return state
