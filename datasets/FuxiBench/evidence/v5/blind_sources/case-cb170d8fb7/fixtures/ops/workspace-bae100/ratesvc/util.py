"""Interval and rate parsing helpers for the rate-pricing pipeline."""

from __future__ import annotations


def split_interval(start: float, end: float, size: float) -> list[tuple[float, float]]:
    """Split ``[start, end)`` into consecutive chunks of width ``size``."""
    count = int((end - start) // size)
    return [(start + index * size, start + (index + 1) * size) for index in range(count)]


def parse_rate(text: str) -> float:
    """Parse a decimal rate from ``text``."""
    return float(text)
