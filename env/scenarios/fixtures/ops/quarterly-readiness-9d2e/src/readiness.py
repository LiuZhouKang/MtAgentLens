"""Release-readiness signal aggregation."""

READY = "ready"
DEGRADED = "degraded"
BLOCKED = "blocked"


def normalise(signals: dict) -> dict:
    """Drop empty signal values and coerce counters to ints."""
    cleaned = {}
    for key, value in signals.items():
        if value is None:
            continue
        if key.endswith("_count"):
            try:
                cleaned[key] = int(value)
            except (TypeError, ValueError):
                cleaned[key] = 0
        else:
            cleaned[key] = value
    return cleaned


def score(signals: dict) -> str:
    """Map normalised signals onto a coarse readiness state."""
    seen = normalise(signals)
    if seen.get("blocking_incidents", 0) > 0:
        return BLOCKED
    if seen.get("failed_count", 0) > 0 or seen.get("degraded_count", 0) > 0:
        return DEGRADED
    return READY
