#!/usr/bin/env python3
"""Deterministic measurements over the INC-2026-0914-03 replay logs.

The tool is intentionally measurement-only: it reports counts, rates, maxima
and release events. It does not decide a root cause, so analysts can cite its
output without inheriting a conclusion.

Usage:
    python3 tools/triage.py --logs logs --out build/triage.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from collections import Counter
from pathlib import Path

INCIDENT = "INC-2026-0914-03"

ACCESS_RE = re.compile(
    r'^(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\s+'
    r'(?P<host>\S+)\s+(?P<ip>\S+)\s+'
    r'"(?P<method>[A-Z]+)\s+(?P<path>\S+)\s+HTTP/1\.1"\s+'
    r'(?P<status>\d{3})\s+(?P<duration>\d+\.\d+)\s+'
    r'upstream=(?P<upstream>\S+)\s+service=(?P<service>\S+)$'
)

DB_RE = re.compile(
    r'^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) UTC '
    r'\[(?P<pid>\d+)\] (?P<level>[A-Z]+):\s+(?P<msg>.*)$'
)


def _read_lines(path: Path) -> list[str]:
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def parse_access(path: Path) -> list[dict]:
    rows: list[dict] = []
    for line in _read_lines(path):
        match = ACCESS_RE.match(line)
        if match is None:
            raise ValueError(f"unparsed access line in {path}: {line[:100]}")
        row = match.groupdict()
        row["status"] = int(row["status"])
        row["duration_s"] = float(row["duration"])
        row["minute"] = row["ts"][:16]
        rows.append(row)
    return rows


def parse_app(path: Path) -> list[dict]:
    events: list[dict] = []
    for line in _read_lines(path):
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid app log line in {path}: {line[:100]}") from exc
        for key in ("ts", "level", "host", "msg"):
            if key not in event:
                raise ValueError(f"app log line missing {key!r} in {path}")
        events.append(event)
    return events


def parse_db(path: Path) -> list[dict]:
    rows: list[dict] = []
    for line in _read_lines(path):
        match = DB_RE.match(line)
        if match is None:
            raise ValueError(f"unparsed database log line in {path}: {line[:100]}")
        rows.append(match.groupdict())
    return rows


def summarise_edge(rows: list[dict]) -> dict:
    status_counts = Counter(str(row["status"]) for row in rows)
    server_errors = [row for row in rows if row["status"] >= 500]
    per_minute = Counter(row["minute"] for row in server_errors)
    peak_minute = None
    if per_minute:
        # Highest count wins; an exact tie is broken by the earliest minute.
        peak_count = max(per_minute.values())
        peak_minute = min(
            minute for minute, count in per_minute.items() if count == peak_count
        )
    login = [row for row in rows if row["path"] == "/api/login"]
    return {
        "host": rows[0]["host"] if rows else "",
        "requests": len(rows),
        "status_counts": dict(sorted(status_counts.items())),
        "server_errors": len(server_errors),
        "server_error_rate": round(len(server_errors) / len(rows), 4) if rows else 0.0,
        "peak_error_minute": (
            {"minute": peak_minute, "server_errors": peak_count} if peak_minute else None
        ),
        "login_attempts": len(login),
        "login_failures": sum(1 for row in login if row["status"] == 401),
        "login_successes": sum(1 for row in login if 200 <= row["status"] < 300),
        "login_sources": sorted({row["ip"] for row in login}),
    }


def summarise_app(events: list[dict]) -> dict:
    errors = [event for event in events if event["level"] == "ERROR"]
    warnings = [event for event in events if event["level"] == "WARN"]
    pool_values = sorted(
        {event["pool_max"] for event in events if isinstance(event.get("pool_max"), int)}
    )
    releases = [
        {
            "ts": event["ts"],
            "msg": event["msg"],
            "version": event.get("version", ""),
        }
        for event in events
        if event["msg"] in ("release activated", "release rollback")
    ]
    return {
        "host": events[0]["host"] if events else "",
        "events": len(events),
        "errors": len(errors),
        "error_messages": dict(sorted(Counter(event["msg"] for event in errors).items())),
        "warnings": len(warnings),
        "warning_messages": dict(sorted(Counter(event["msg"] for event in warnings).items())),
        "first_error_ts": errors[0]["ts"] if errors else None,
        "last_error_ts": errors[-1]["ts"] if errors else None,
        "pool_max_values": pool_values,
        "release_events": releases,
    }


def summarise_db(rows: list[dict]) -> dict:
    authorized = 0
    active: set[str] = set()
    max_concurrent = 0
    connection_resets = 0
    slow_tx = 0
    fatal = 0
    warnings = 0
    for row in sorted(rows, key=lambda item: item["ts"]):
        msg = row["msg"]
        if row["level"] == "FATAL":
            fatal += 1
        if row["level"] == "WARNING":
            warnings += 1
            if msg.startswith("longest transaction exceeded"):
                slow_tx += 1
        if msg.startswith("connection authorized"):
            authorized += 1
            active.add(row["pid"])
            max_concurrent = max(max_concurrent, len(active))
        elif msg.startswith("disconnection"):
            active.discard(row["pid"])
        elif msg.startswith("could not receive data from client"):
            connection_resets += 1
    return {
        "host": "db-03",
        "lines": len(rows),
        "fatal_count": fatal,
        "warning_count": warnings,
        "authorized_sessions": authorized,
        "max_concurrent_sessions": max_concurrent,
        "connection_reset_count": connection_resets,
        "slow_transaction_warnings": slow_tx,
    }


def collect(logs_dir: Path) -> dict:
    edge_rows = parse_access(logs_dir / "edge-01" / "access.log")
    app_events = parse_app(logs_dir / "api-02" / "checkout.log")
    db_rows = parse_db(logs_dir / "db-03" / "postgres.log")
    if not edge_rows:
        raise ValueError("edge access log is empty")
    return {
        "incident": INCIDENT,
        "window": {
            "start": min(row["ts"] for row in edge_rows),
            "end": max(row["ts"] for row in edge_rows),
        },
        "edge": summarise_edge(edge_rows),
        "api": summarise_app(app_events),
        "db": summarise_db(db_rows),
    }


def write_json_atomic(path: Path, payload: dict) -> None:
    """Write via a sibling temp file so parallel readers never see half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(temp_name, path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


def _clock(timestamp: str) -> str:
    return timestamp[11:16]


def print_summary(payload: dict) -> None:
    edge = payload["edge"]
    api = payload["api"]
    db = payload["db"]
    rates = ", ".join(f"{status}={count}" for status, count in edge["status_counts"].items())
    peak = edge["peak_error_minute"]
    print(
        f"{edge['host']}: {edge['requests']} requests ({rates}); "
        f"5xx {edge['server_errors']} ({edge['server_error_rate'] * 100:.1f}%); "
        f"peak {_clock(peak['minute'])} with {peak['server_errors']}"
    )
    print(
        f"login: {edge['login_attempts']} attempts, {edge['login_failures']} failures, "
        f"{edge['login_successes']} successes from {len(edge['login_sources'])} sources"
    )
    error_text = ", ".join(f"{msg}={count}" for msg, count in api["error_messages"].items())
    print(
        f"{api['host']}: {api['errors']} ERROR ({error_text}); "
        f"first {_clock(api['first_error_ts'])} last {_clock(api['last_error_ts'])}; "
        f"pool_max={api['pool_max_values']}"
    )
    print(
        f"db-03: {db['fatal_count']} FATAL; {db['authorized_sessions']} authorized sessions; "
        f"max concurrent {db['max_concurrent_sessions']}; {db['connection_reset_count']} resets"
    )
    for release in api["release_events"]:
        print(f"release: {release['version']} {release['msg']} @ {_clock(release['ts'])}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--logs", type=Path, default=Path("logs"))
    parser.add_argument("--out", type=Path, default=Path("build/triage.json"))
    args = parser.parse_args(argv)

    try:
        payload = collect(args.logs)
    except (OSError, ValueError) as exc:
        print(f"triage failed: {exc}", file=sys.stderr)
        return 1

    write_json_atomic(args.out, payload)
    print_summary(payload)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
