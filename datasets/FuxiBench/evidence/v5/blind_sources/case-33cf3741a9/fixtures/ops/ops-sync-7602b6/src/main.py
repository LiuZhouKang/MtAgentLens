def normalise(event: dict) -> dict:
    return {k: v for k, v in event.items() if v is not None}
