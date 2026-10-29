# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Observer call counts and run duration per arm (review comment 5, partial).

The reviewer asks for the full cost of the system, including Observer calls.
This script reports what the campaign ledger actually records:

* Observer calls per run (total, mean, p50, p95, max), by final decision;
* run duration (mean, p50, p95).

Token counts and money are **not recorded per call**, so this report does not
claim a cost: it only says how often the Observer was invoked and how long a run
took. Any cost statement needs a usage ledger that does not exist yet.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

REPORT_SCHEMA = "mtagentlens.observer-cost-report/1"


def percentile(values: list[float], fraction: float) -> float | None:
    """Nearest-rank percentile: the smallest value covering `fraction` of the data."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(fraction * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def load_rows(path: Path) -> tuple[list[dict], int]:
    rows = []
    excluded = 0
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"{path}:{number}: {error}") from error
        decision = payload.get("final_decision")
        state = payload.get("state")
        if decision is None or state == "failed":
            # A run without a verdict must not enter any denominator.
            excluded += 1
            continue
        calls = payload.get("observer_calls")
        duration = payload.get("duration_seconds")
        if not isinstance(calls, int) or calls < 0:
            raise ValueError(f"{path}:{number}: observer_calls must be a non-negative int")
        if not isinstance(duration, (int, float)) or duration < 0:
            raise ValueError(f"{path}:{number}: duration_seconds must be non-negative")
        rows.append({"observer_calls": calls, "duration_seconds": float(duration),
                     "final_decision": str(decision)})
    if not rows:
        raise ValueError(f"{path}: no rows with a verdict")
    return rows, excluded


def summarize(rows: list[dict]) -> dict:
    calls = [float(row["observer_calls"]) for row in rows]
    durations = [row["duration_seconds"] for row in rows]
    by_decision: dict[str, dict] = {}
    for row in rows:
        entry = by_decision.setdefault(row["final_decision"],
                                       {"runs": 0, "observer_calls": 0})
        entry["runs"] += 1
        entry["observer_calls"] += row["observer_calls"]
    return {
        "runs": len(rows),
        "observer_calls": {
            "total": int(sum(calls)),
            "mean": sum(calls) / len(calls),
            "p50": percentile(calls, 0.50),
            "p95": percentile(calls, 0.95),
            "max": int(max(calls)),
            "runs_without_calls": sum(1 for value in calls if value == 0),
        },
        "duration_seconds": {
            "mean": sum(durations) / len(durations),
            "p50": percentile(durations, 0.50),
            "p95": percentile(durations, 0.95),
            "max": max(durations),
        },
        "by_final_decision": dict(sorted(by_decision.items())),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", action="append", default=[], metavar="ARM=PATH",
                        help="arm=results.jsonl")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    arms: dict[str, dict] = {}
    for item in args.results:
        arm, _, path = item.partition("=")
        if not arm or not path:
            print(f"error: expected ARM=PATH, got {item!r}", file=sys.stderr)
            return 2
        try:
            rows, excluded = load_rows(Path(path))
            summary = summarize(rows)
            summary["excluded_without_verdict"] = excluded
            arms[arm] = summary
        except ValueError as error:
            print(f"error: {error}", file=sys.stderr)
            return 1
    if not arms:
        print("error: at least one --results is required", file=sys.stderr)
        return 2
    report = {
        "schema": REPORT_SCHEMA,
        "arms": arms,
        "note": ("Observer calls and durations as recorded by the campaign ledger. "
                 "No per-call token or money ledger exists, so this report makes no cost "
                 "claim; only the invocation count and run wall time are measured."),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    for arm, summary in arms.items():
        calls = summary["observer_calls"]
        duration = summary["duration_seconds"]
        print(f"{arm}: runs={summary['runs']} observer_calls total={calls['total']} "
              f"mean={calls['mean']:.2f} p50={calls['p50']} p95={calls['p95']} "
              f"max={calls['max']} zero_runs={calls['runs_without_calls']}")
        print(f"        duration_s mean={duration['mean']:.1f} p50={duration['p50']} "
              f"p95={duration['p95']} max={duration['max']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
