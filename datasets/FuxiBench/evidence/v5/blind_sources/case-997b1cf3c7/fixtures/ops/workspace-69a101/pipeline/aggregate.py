#!/usr/bin/env python3
"""Offline aggregator for the storefront log shards.

The edge and core collectors upload JSON Lines batches. Because batches
overlap during collector restarts, a re-ingested event keeps its ``event_id``
but carries the retry timestamp, so the same id can appear in more than one
shard. This tool parses every shard, keeps the earliest record for each event
id, counts later copies as duplicates, counts malformed lines, and writes a
deterministic summary (``summary.json`` plus ``summary.md``) for the nightly
report.

Only the Python standard library is used; the tool never opens a network
connection and never reads anything outside ``--shards`` and ``--out``.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

REQUIRED_FIELDS = ("ts", "service", "level", "event_id", "message")
ERROR_LEVELS = ("ERROR", "FATAL")
TOP_ERRORS = 3


def read_shards(
    shards_dir: Path,
) -> tuple[list[tuple[str, str, int, dict[str, Any]]], list[dict[str, Any]]]:
    """Parse every ``*.jsonl`` shard into timestamp-sortable candidates.

    Returns ``(ts, file, line_number, record)`` tuples and one statistics row
    per shard. ``events`` counts parsed records including later duplicates;
    ``duplicates`` is filled in by :func:`deduplicate`.
    """
    candidates: list[tuple[str, str, int, dict[str, Any]]] = []
    stats: list[dict[str, Any]] = []

    for path in sorted(shards_dir.glob("*.jsonl")):
        parsed = 0
        malformed = 0
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            text = line.strip()
            if not text:
                continue
            try:
                record = json.loads(text)
            except json.JSONDecodeError:
                malformed += 1
                continue
            if not isinstance(record, dict) or any(field not in record for field in REQUIRED_FIELDS):
                malformed += 1
                continue
            candidates.append((str(record["ts"]), path.name, line_number, record))
            parsed += 1
        stats.append(
            {
                "file": path.name,
                "events": parsed,
                "duplicates": 0,
                "malformed": malformed,
            }
        )
    return candidates, stats


def deduplicate(
    candidates: list[tuple[str, str, int, dict[str, Any]]], stats: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Keep the earliest record per ``event_id``; count later copies per shard."""
    by_file = {row["file"]: row for row in stats}
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for _ts, file_name, _line_number, record in sorted(candidates, key=lambda item: item[:3]):
        event_id = str(record["event_id"])
        if event_id in seen:
            by_file[file_name]["duplicates"] += 1
            continue
        seen.add(event_id)
        unique.append(record)
    return unique, stats


def summarize(unique: list[dict[str, Any]], stats: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the deterministic summary document from unique records."""
    services: dict[str, dict[str, Any]] = {}
    for record in unique:
        name = str(record["service"])
        entry = services.setdefault(name, {"total": 0, "levels": Counter(), "errors": Counter()})
        entry["total"] += 1
        level = str(record["level"])
        entry["levels"][level] += 1
        if level in ERROR_LEVELS:
            entry["errors"][str(record["message"])] += 1

    services_out: dict[str, Any] = {}
    for name in sorted(services):
        entry = services[name]
        errors = sum(entry["errors"].values())
        services_out[name] = {
            "total": entry["total"],
            "levels": dict(sorted(entry["levels"].items())),
            "errors": errors,
            "error_rate": round(errors / entry["total"], 4),
            "top_errors": [[message, count] for message, count in entry["errors"].most_common(TOP_ERRORS)],
        }

    ordered = sorted(unique, key=lambda record: (str(record["ts"]), str(record["event_id"])))
    window = {
        "first_ts": str(ordered[0]["ts"]) if ordered else None,
        "last_ts": str(ordered[-1]["ts"]) if ordered else None,
    }
    totals = {
        "events_seen": sum(row["events"] for row in stats),
        "unique_events": len(unique),
        "duplicates": sum(row["duplicates"] for row in stats),
        "malformed": sum(row["malformed"] for row in stats),
    }
    return {"totals": totals, "window": window, "services": services_out, "shards": stats}


def aggregate(shards_dir: Path) -> dict[str, Any]:
    """Read the shard directory, deduplicate and return the summary document."""
    candidates, stats = read_shards(shards_dir)
    unique, stats = deduplicate(candidates, stats)
    return summarize(unique, stats)


def _markdown_table(header: list[str], rows: list[list[str]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return lines


def write_summary(summary: dict[str, Any], out_dir: Path) -> None:
    """Write ``summary.json`` and ``summary.md`` under ``out_dir``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    totals = summary["totals"]
    window = summary["window"]
    service_rows = []
    for name, entry in sorted(summary["services"].items()):
        top_error = entry["top_errors"][0][0] if entry["top_errors"] else "-"
        service_rows.append(
            [name, str(entry["total"]), str(entry["errors"]), f"{entry['error_rate']:.4f}", top_error]
        )
    shard_rows = [
        [row["file"], str(row["events"]), str(row["duplicates"]), str(row["malformed"])]
        for row in summary["shards"]
    ]

    lines = [
        "# Storefront log aggregation summary",
        "",
        f"- Events seen: {totals['events_seen']}",
        f"- Unique events: {totals['unique_events']}",
        f"- Duplicate events dropped: {totals['duplicates']}",
        f"- Malformed lines: {totals['malformed']}",
        f"- Window: {window['first_ts']} .. {window['last_ts']}",
        "",
        "## Services",
        "",
        *_markdown_table(["service", "total", "errors", "error_rate", "top error"], service_rows),
        "",
        "## Shards",
        "",
        *_markdown_table(["shard", "events", "duplicates", "malformed"], shard_rows),
        "",
    ]
    (out_dir / "summary.md").write_text("\n".join(lines), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Aggregate storefront JSONL log shards.")
    sub = parser.add_subparsers(dest="command", required=True)

    agg = sub.add_parser("aggregate", help="parse shards and write summary.json/summary.md")
    agg.add_argument("--shards", required=True, help="directory containing *.jsonl shards")
    agg.add_argument("--out", required=True, help="output directory for the summary")

    query = sub.add_parser("query", help="print per-service statistics from a summary")
    query.add_argument("--summary", required=True, help="path to summary.json")
    query.add_argument("--service", help="only print this service")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "aggregate":
        shards_dir = Path(args.shards)
        if not shards_dir.is_dir():
            print(f"error: shard directory not found: {shards_dir}", file=sys.stderr)
            return 2
        summary = aggregate(shards_dir)
        out_dir = Path(args.out)
        write_summary(summary, out_dir)
        totals = summary["totals"]
        print(f"wrote {out_dir / 'summary.json'}")
        print(f"wrote {out_dir / 'summary.md'}")
        print(
            "unique_events={unique_events} duplicates={duplicates} malformed={malformed}".format(
                **totals
            )
        )
        return 0

    summary_path = Path(args.summary)
    if not summary_path.is_file():
        print(f"error: summary not found: {summary_path}", file=sys.stderr)
        return 2
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    services = summary.get("services", {})
    if args.service:
        entry = services.get(args.service)
        if entry is None:
            print(f"error: unknown service: {args.service}", file=sys.stderr)
            return 2
        print(json.dumps({args.service: entry}, indent=2, sort_keys=True))
        return 0
    print(json.dumps(services, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
