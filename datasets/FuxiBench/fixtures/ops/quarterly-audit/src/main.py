"""Helpers for the quarterly configuration-migration audit."""


def canonical_key(name: str) -> str:
    """Normalise a manifest key so legacy and target entries compare equal."""
    return name.strip().lower().replace("-", "_").replace(" ", "_")


def redact(value: str) -> str:
    """Mask the middle of a value while keeping it recognisable in reports."""
    if len(value) <= 4:
        return "*" * len(value)
    return value[:2] + "*" * (len(value) - 4) + value[-2:]
