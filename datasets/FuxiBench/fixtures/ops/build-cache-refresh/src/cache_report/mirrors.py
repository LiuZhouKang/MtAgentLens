"""Parse mirror configuration files and pick a regional endpoint."""

from pathlib import Path


def parse_env_file(path):
    """Return the KEY=value pairs from a simple env file."""
    values = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def select_mirror(mirrors, region):
    """Return the URL for a region, falling back to the upstream entry."""
    for entry in mirrors:
        if entry["region"] == region:
            return entry["url"]
    for entry in mirrors:
        if entry["region"] == "upstream":
            return entry["url"]
    return None
