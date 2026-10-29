"""Pure helpers for evaluating build-host requirement rows.

A requirement file is a sequence of `key = value` lines; empty values mean
"present but unset". The helpers here are deliberately side-effect free so the
release gate can unit-test its parsing rules without touching a host.
"""

from __future__ import annotations

REQUIRED_GROUPS = ("build", "docker")


def parse_requirement(line: str) -> tuple[str, str | None] | None:
    """Parse one `key = value` row, or return None when the row is not a row."""
    key, sep, value = line.partition("=")
    if not sep or not key.strip():
        return None
    return key.strip(), value.strip() or None


def parse_requirements(text: str) -> list[tuple[str, str | None]]:
    """Parse a requirement file, skipping comments and blank lines."""
    rows: list[tuple[str, str | None]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parsed = parse_requirement(line)
        if parsed is not None:
            rows.append(parsed)
    return rows


def missing_groups(
    *, present: list[str] | tuple[str, ...], required: tuple[str, ...] = REQUIRED_GROUPS
) -> list[str]:
    """Return the required groups the account does not belong to, sorted."""
    return sorted(set(required) - set(present))


def summarize(rows: list[tuple[str, str | None]]) -> dict[str, object]:
    """Return a compact summary of parsed requirement rows."""
    return {"total": len(rows), "keys": sorted({key for key, _ in rows})}
