"""Pure helpers used by the build-metrics relay."""

from typing import Any


def normalise(event: dict[str, Any]) -> dict[str, Any]:
    """Drop null fields so downstream consumers see a dense record."""
    return {key: value for key, value in event.items() if value is not None}


def bucket_duration(milliseconds: int, width: int = 1000) -> int:
    """Round a duration down to the start of its bucket."""
    if width <= 0:
        raise ValueError("width must be positive")
    return (milliseconds // width) * width
