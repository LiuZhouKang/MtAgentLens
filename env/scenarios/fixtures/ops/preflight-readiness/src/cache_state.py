"""Helpers for inspecting the local build cache."""


def cache_usage(sizes):
    """Aggregate cache entry sizes into a usage summary."""
    return {"entries": len(sizes), "bytes": sum(sizes)}
