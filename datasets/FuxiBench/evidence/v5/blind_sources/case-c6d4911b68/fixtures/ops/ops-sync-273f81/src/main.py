"""Event normalisation helpers for the ops-sync release gate."""


def normalise(event: dict) -> dict:
    """Drop unset fields while keeping zero and False values."""
    return {k: v for k, v in event.items() if v is not None}
