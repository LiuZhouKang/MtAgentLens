"""Audit helper used by the toolchain-refresh check target."""


def normalise_event(name: str) -> str:
    """Return a stable event key for the audit log."""
    return name.strip().lower().replace(" ", "-")


def summarise(events: list[str]) -> dict[str, int]:
    """Count events by their normalised name."""
    counts: dict[str, int] = {}
    for event in events:
        key = normalise_event(event)
        counts[key] = counts.get(key, 0) + 1
    return counts
