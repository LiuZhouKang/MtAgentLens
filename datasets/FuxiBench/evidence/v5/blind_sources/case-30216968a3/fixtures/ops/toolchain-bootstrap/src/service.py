"""Collector service entrypoint used by the build verification step."""

from __future__ import annotations


def describe() -> str:
    """Return the service identifier recorded in the build report."""
    return "collector-service"


def main() -> int:
    print(describe())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
