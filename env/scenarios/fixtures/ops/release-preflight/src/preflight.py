def parse_kv(text: str) -> dict[str, str]:
    """Parse simple KEY=VALUE configuration text."""
    values: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def missing_keys(values: dict[str, str], required: list[str]) -> list[str]:
    """Return required keys that are absent or empty."""
    return [key for key in required if not values.get(key)]
