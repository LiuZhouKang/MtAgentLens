"""Telemetry batch normalisation and retry backoff."""

MAX_BATCH = 500
BASE_DELAY = 0.25
MAX_DELAY = 4.0


def normalise_batch(events: list) -> list:
    """Drop events without a service name and stamp a monotonic sequence."""
    cleaned = []
    for event in events:
        if not isinstance(event, dict):
            continue
        service = event.get("service")
        if not service or not isinstance(service, str):
            continue
        item = dict(event)
        item["service"] = service.strip().lower()
        item["seq"] = len(cleaned)
        cleaned.append(item)
    return cleaned[:MAX_BATCH]


def backoff_delay(attempt: int) -> float:
    """Exponential backoff with a hard ceiling and a floor of one attempt."""
    if attempt <= 0:
        return BASE_DELAY
    delay = BASE_DELAY * (2 ** min(attempt, 6))
    return min(delay, MAX_DELAY)


def chunked(events: list, size: int = MAX_BATCH) -> list:
    """Split events into transport-sized batches."""
    if size <= 0:
        raise ValueError("size must be positive")
    return [events[index:index + size] for index in range(0, len(events), size)]
