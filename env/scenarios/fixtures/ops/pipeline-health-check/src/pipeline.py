"""Stage-timing helpers for the ingestion pipeline self-check."""

from __future__ import annotations


def parse_stage_line(line: str) -> tuple[str, float] | None:
    """Parse one ``stage=milliseconds`` diagnostic line.

    Returns ``None`` for blank lines, comments, and malformed values so the
    sampler can skip them without aborting the window.
    """
    text = line.strip()
    if not text or text.startswith("#") or "=" not in text:
        return None
    name, _, value = text.partition("=")
    name = name.strip()
    if not name:
        return None
    try:
        return name, float(value.strip())
    except ValueError:
        return None


def ewma(values: list[float], alpha: float) -> float:
    """Exponentially weighted moving average, seeded with the first sample."""
    if not values:
        return 0.0
    result = float(values[0])
    for value in values[1:]:
        result = alpha * float(value) + (1.0 - alpha) * result
    return result


def throughput_ok(samples: list[float], floor: float) -> bool:
    """True when every sample stays at or above the configured floor."""
    return bool(samples) and min(samples) >= floor
