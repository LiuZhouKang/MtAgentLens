"""Session audit helpers used by the replay harness."""


def normalise_event(event: dict) -> dict:
    """Drop keys whose value is None so records compare cleanly."""
    return {key: value for key, value in event.items() if value is not None}


def summarise_events(events: list[dict]) -> dict:
    """Count events by event_type."""
    summary: dict[str, int] = {}
    for event in events:
        kind = event.get("event_type") or "unknown"
        summary[kind] = summary.get(kind, 0) + 1
    return summary


def identity_has_session(sessions: list[dict], pid: int, start_time_ns: int) -> bool:
    """Whether any TLS session row belongs to the exact (pid, start) identity."""
    return any(
        row.get("pid") == pid and row.get("start_time_ns") == start_time_ns
        for row in sessions
    )
