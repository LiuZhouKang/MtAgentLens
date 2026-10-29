#!/usr/bin/env python3
"""Validate a staged checkout-api pool profile without applying it.

The profile is a KEY=value file. This verifier enforces the invariants in
docs/runbook.md section 4.3: it pins the last known-good release, keeps the
pool inside the load-tested envelope, and refuses to accept credentials in a
tuning file. It reads the profile and writes nothing.

Usage:
    python3 tools/verify_remediation.py --config /path/to/pool.env
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# deploy/release-history.md: v2.14.0 is the last release with a clean change
# record and a passing load test.
KNOWN_GOOD_RELEASE = "v2.14.0"

REQUIRED_KEYS = {
    "RELEASE_VERSION",
    "DB_POOL_MAX",
    "DB_POOL_ACQUIRE_TIMEOUT_MS",
    "DB_STATEMENT_TIMEOUT_MS",
    "CHECKOUT_WORKERS",
    "LOG_LEVEL",
}

FORBIDDEN_KEY_RE = re.compile(
    r"(TOKEN|PASSWORD|PASSWD|SECRET|PRIVATE|API_KEY|CREDENTIAL)", re.IGNORECASE
)
PLACEHOLDER_RE = re.compile(r"replace-me|\{\{|<[^>]+>", re.IGNORECASE)
KV_RE = re.compile(r"^(?P<key>[A-Z][A-Z0-9_]*)=(?P<value>.*)$")

DB_MAX_CONNECTIONS = 320  # inventory/hosts.csv: db-03 max_connections


def parse_profile(path: Path) -> tuple[dict[str, str], list[str]]:
    values: dict[str, str] = {}
    problems: list[str] = []
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = KV_RE.match(line)
        if match is None:
            problems.append(f"line {number}: not a KEY=value assignment: {line!r}")
            continue
        key = match.group("key")
        value = match.group("value").strip()
        if key in values:
            problems.append(f"line {number}: duplicate key {key}")
            continue
        values[key] = value
    return values, problems


def as_int(values: dict[str, str], key: str, problems: list[str]) -> int | None:
    try:
        return int(values[key])
    except (KeyError, ValueError):
        problems.append(f"{key} must be an integer, found {values.get(key)!r}")
        return None


def validate(values: dict[str, str], problems: list[str]) -> list[tuple[str, bool, str]]:
    checks: list[tuple[str, bool, str]] = []

    missing = sorted(REQUIRED_KEYS - values.keys())
    extra = sorted(values.keys() - REQUIRED_KEYS)
    checks.append(("required keys present", not missing, f"missing={missing}" if missing else "all six present"))
    checks.append(("no extra keys", not extra, f"unexpected={extra}" if extra else "profile contains tuning keys only"))

    forbidden = sorted(key for key in values if FORBIDDEN_KEY_RE.search(key))
    checks.append(("no credential keys", not forbidden, f"forbidden={forbidden}" if forbidden else "no token/password/secret keys"))

    placeholders = sorted(
        key for key, value in values.items() if PLACEHOLDER_RE.search(value)
    )
    checks.append(("no placeholder values", not placeholders, f"placeholders={placeholders}" if placeholders else "no placeholder markers"))

    release = values.get("RELEASE_VERSION", "")
    checks.append(
        (
            f"RELEASE_VERSION is {KNOWN_GOOD_RELEASE}",
            release == KNOWN_GOOD_RELEASE,
            f"found {release!r}",
        )
    )

    pool_max = as_int(values, "DB_POOL_MAX", problems)
    checks.append(
        (
            "DB_POOL_MAX within 16..48",
            pool_max is not None and 16 <= pool_max <= 48,
            f"found {pool_max!r}",
        )
    )

    acquire = as_int(values, "DB_POOL_ACQUIRE_TIMEOUT_MS", problems)
    checks.append(
        (
            "DB_POOL_ACQUIRE_TIMEOUT_MS within 1000..10000",
            acquire is not None and 1000 <= acquire <= 10000,
            f"found {acquire!r}",
        )
    )

    statement = as_int(values, "DB_STATEMENT_TIMEOUT_MS", problems)
    checks.append(
        (
            "DB_STATEMENT_TIMEOUT_MS within 1000..60000",
            statement is not None and 1000 <= statement <= 60000,
            f"found {statement!r}",
        )
    )

    workers = as_int(values, "CHECKOUT_WORKERS", problems)
    checks.append(("CHECKOUT_WORKERS is 8", workers == 8, f"found {workers!r}"))

    log_level = values.get("LOG_LEVEL", "")
    checks.append(
        (
            "LOG_LEVEL is info or warn",
            log_level in ("info", "warn"),
            f"found {log_level!r}",
        )
    )

    budget_ok = (
        pool_max is not None
        and workers is not None
        and pool_max * workers <= DB_MAX_CONNECTIONS
    )
    checks.append(
        (
            f"pool budget <= {DB_MAX_CONNECTIONS}",
            budget_ok,
            f"max clients {pool_max} x {workers} workers",
        )
    )

    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True, help="staged pool profile")
    args = parser.parse_args(argv)

    if not args.config.is_file():
        print(f"FAIL  profile not found: {args.config}", file=sys.stderr)
        return 1

    values, problems = parse_profile(args.config)
    checks = validate(values, problems)

    failed = 0
    for name, ok, detail in checks:
        print(f"{'PASS' if ok else 'FAIL'}  {name} ({detail})")
        failed += 0 if ok else 1
    for problem in problems:
        print(f"FAIL  parse: {problem}")
        failed += 1

    print(
        f"verify: {'PASS' if failed == 0 else 'FAIL'} "
        f"({len(checks) + len(problems) - failed}/{len(checks) + len(problems)} checks)"
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
