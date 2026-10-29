"""Version handling and summary formatting."""

from __future__ import annotations

import re

_VERSION_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


def parse_version(text: str) -> tuple[int, int, int]:
    """Parse ``MAJOR.MINOR.PATCH`` into a tuple."""
    match = _VERSION_RE.match(text.strip())
    if match is None:
        raise ValueError(f"not a semantic version: {text!r}")
    major, minor, patch = (int(group) for group in match.groups())
    return major, minor, patch


def compare_versions(left: str, right: str) -> int:
    """Return -1, 0 or 1 like a comparison function."""
    left_version = parse_version(left)
    right_version = parse_version(right)
    return (left_version > right_version) - (left_version < right_version)


def release_name(version: str, build_date: str) -> str:
    """Stable artifact name for a version and ISO date."""
    parse_version(version)
    return f"releasekit-{version}-{build_date}"


def format_summary_lines(rows: list[str], footer: str = "Build successful!") -> list[str]:
    """Assemble the build summary body."""
    header = "=== BUILD SUMMARY ==="
    return [header, *rows, footer]
