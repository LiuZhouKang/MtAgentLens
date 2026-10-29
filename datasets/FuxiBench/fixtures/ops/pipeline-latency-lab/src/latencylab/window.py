"""Small windowing helpers used by the CI latency dashboard fixtures."""

from typing import Iterable


def clamp(value: float, low: float, high: float) -> float:
    """Constrain value to the inclusive [low, high] range."""
    if low > high:
        raise ValueError("low must not exceed high")
    return max(low, min(high, value))


def percentile(values: Iterable[float], fraction: float) -> float:
    """Return the nearest-rank percentile of values for 0 <= fraction <= 1."""
    data = sorted(values)
    if not data:
        raise ValueError("percentile needs at least one value")
    if not 0.0 <= fraction <= 1.0:
        raise ValueError("fraction must be between 0 and 1")
    rank = max(1, int(round(fraction * len(data) + 0.5)))
    index = min(rank, len(data)) - 1
    return data[index]


def window_bounds(start: int, width: int) -> tuple[int, int]:
    """Return the half-open [start, end) bounds of the window containing start."""
    if width <= 0:
        raise ValueError("width must be positive")
    anchor = (start // width) * width
    return anchor, anchor + width
