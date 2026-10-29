"""Reconcile the sync counts reported by each hop."""
from __future__ import annotations

from collections import Counter


def tally(states: list[str]) -> dict[str, int]:
    """Count the states reported in one hop's sample."""
    return dict(Counter(states))


def drift(declared: dict[str, int], observed: dict[str, int]) -> dict[str, int]:
    """Return observed minus declared for every state either side names."""
    keys = set(declared) | set(observed)
    return {key: observed.get(key, 0) - declared.get(key, 0) for key in sorted(keys)}


def consistent(declared: dict[str, int], observed: dict[str, int]) -> bool:
    """True when every state matches between the two samples."""
    return all(value == 0 for value in drift(declared, observed).values())
