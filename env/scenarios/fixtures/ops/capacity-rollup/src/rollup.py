"""Aggregation helpers for the capacity-rollup pipeline."""

from __future__ import annotations


def fold_samples(samples: list[tuple[int, float]]) -> list[tuple[int, float]]:
    """Average non-negative samples into one-hour buckets.

    ``samples`` holds ``(offset_seconds, value)`` pairs; the return value keeps
    the bucket order stable so the rollup output is reproducible.
    """
    buckets: dict[int, list[float]] = {}
    for offset, value in samples:
        if value < 0:
            continue
        bucket = (int(offset) // 3600) * 3600
        buckets.setdefault(bucket, []).append(float(value))
    return [(bucket, sum(values) / len(values)) for bucket, values in sorted(buckets.items())]
