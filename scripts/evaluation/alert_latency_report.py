# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Alert latency from recorded runs (review comment 5, partial).

Definition used here, and nothing stronger:

* **decision latency** = final alert timestamp − the latest correlated evidence
  timestamp at or before that alert. It answers "how long after the last piece of
  evidence did the run get its authoritative verdict".
* **finalization latency** = run ended_at − final alert timestamp.

This is **not** event-level alignment (ADR-0016 forbids claiming that): the
ledger stores run-level evidence, not a per-event decision trace. Runs without a
verdict or without evidence are excluded and counted, never averaged in as zero.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from pathlib import Path

REPORT_SCHEMA = "mtagentlens.alert-latency-report/1"


def percentile(values: list[float], fraction: float) -> float | None:
    """Nearest-rank percentile."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(fraction * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def load_ledger(path: Path) -> list[dict]:
    rows = []
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"{path}:{number}: {error}") from error
        rows.append(payload)
    if not rows:
        raise ValueError(f"{path}: no rows")
    return rows


def measure_run(db_path: Path) -> dict | None:
    """Return latencies for one finalized DB, or a reason it cannot be measured."""
    if not Path(db_path).is_file():
        return {"excluded": "missing_db"}
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        run = connection.execute(
            "SELECT started_at_ns, ended_at_ns FROM runs LIMIT 1").fetchone()
        if run is None:
            return {"excluded": "missing_run_record"}
        alerts = [row["timestamp_ns"] for row in connection.execute(
            "SELECT timestamp_ns FROM responsibility_alerts ORDER BY timestamp_ns")]
        if not alerts:
            return {"excluded": "no_alerts"}
        evidence = [row[0] for row in connection.execute(
            "SELECT timestamp_ns FROM correlation_evidence"
            " UNION ALL SELECT timestamp_ns FROM actions")]
        evidence = [value for value in evidence if value]
        if not evidence:
            return {"excluded": "no_evidence"}
        final_alert = alerts[-1]
        prior_evidence = [value for value in evidence if value <= final_alert]
        if not prior_evidence:
            return {"excluded": "no_evidence_before_alert"}
        decision_latency = (final_alert - max(prior_evidence)) / 1e9
        ended_at = run["ended_at_ns"]
        finalization = ((ended_at - final_alert) / 1e9
                        if ended_at and ended_at >= final_alert else None)
        return {
            "decision_latency_s": decision_latency,
            "finalization_latency_s": finalization,
            "alerts": len(alerts),
        }
    finally:
        connection.close()


def summarize(rows: list[dict], db_key: str = "db_path") -> dict:
    measured = []
    excluded: dict[str, int] = {}
    for row in rows:
        if row.get("final_decision") is None or row.get("state") == "failed":
            excluded["no_verdict"] = excluded.get("no_verdict", 0) + 1
            continue
        db_path = row.get(db_key)
        if not db_path:
            excluded["missing_db_path"] = excluded.get("missing_db_path", 0) + 1
            continue
        outcome = measure_run(Path(db_path))
        if outcome is None or "excluded" in outcome:
            reason = (outcome or {}).get("excluded", "unknown")
            excluded[reason] = excluded.get(reason, 0) + 1
            continue
        measured.append(outcome)
    decisions = [item["decision_latency_s"] for item in measured]
    finalizations = [item["finalization_latency_s"] for item in measured
                     if item["finalization_latency_s"] is not None]
    return {
        "measured_runs": len(measured),
        "excluded": dict(sorted(excluded.items())),
        "decision_latency_s": {
            "p50": percentile(decisions, 0.50),
            "p95": percentile(decisions, 0.95),
            "max": max(decisions) if decisions else None,
            "mean": (sum(decisions) / len(decisions)) if decisions else None,
        },
        "finalization_latency_s": {
            "p50": percentile(finalizations, 0.50),
            "p95": percentile(finalizations, 0.95),
            "max": max(finalizations) if finalizations else None,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", action="append", default=[], metavar="ARM=PATH")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    arms: dict[str, dict] = {}
    for item in args.results:
        arm, _, path = item.partition("=")
        if not arm or not path:
            print(f"error: expected ARM=PATH, got {item!r}", file=sys.stderr)
            return 2
        try:
            arms[arm] = summarize(load_ledger(Path(path)))
        except ValueError as error:
            print(f"error: {error}", file=sys.stderr)
            return 1
    if not arms:
        print("error: at least one --results is required", file=sys.stderr)
        return 2
    report = {
        "schema": REPORT_SCHEMA,
        "arms": arms,
        "definition": {
            "decision_latency": "final alert timestamp - latest correlated evidence at or before it",
            "finalization_latency": "run ended_at - final alert timestamp",
            "not_event_alignment": "ADR-0016: this is run-level evidence, not per-event alignment",
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    for arm, summary in arms.items():
        decision = summary["decision_latency_s"]
        finalization = summary["finalization_latency_s"]
        print(f"{arm}: measured={summary['measured_runs']} excluded={summary['excluded']} "
              f"decision p50={decision['p50']} p95={decision['p95']} "
              f"finalization p50={finalization['p50']} p95={finalization['p95']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
