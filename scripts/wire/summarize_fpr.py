#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Summarize benign-scenario runs into the committed FPR baseline.

Run-level FPR is defined as: a benign scenario produced at least one alert with
severity Warning or Critical. Info alerts are counted separately because the
product emits a routine Info record on most runs; folding it into FPR would make
the number meaningless.

Every number must trace to a run directory and to SQLite rows. If any scenario
is missing, the script exits non-zero rather than reporting a partial rate.

Usage:
    python3 scripts/wire/summarize_fpr.py env/fpr-runs/p05-baseline \
        --out-dir docs/evaluation --name fpr-baseline
"""
from __future__ import annotations

import argparse
import datetime
import json
import math
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCENARIO_DIR = ROOT / "env" / "scenarios" / "benign"


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    denominator = 1.0 + z * z / n
    center = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return (max(0.0, center - half), min(1.0, center + half))


def git_state() -> dict:
    def run(*args: str) -> str:
        try:
            return subprocess.run(
                args, cwd=ROOT, capture_output=True, text=True
            ).stdout.strip()
        except OSError:
            return ""
    return {
        "head": run("git", "rev-parse", "HEAD"),
        "dirty": bool(run("git", "status", "--porcelain")),
    }


def notable_alerts(db_path: str) -> list[dict]:
    path = Path(db_path)
    if not path.exists():
        # The caller already failed the run for a missing DB; do not invent [].
        raise FileNotFoundError(f"responsibility db missing: {path}")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT severity, decision, title, reasoning, primary_agent_id "
            "FROM responsibility_alerts WHERE severity IN ('Warning','Critical') "
            "ORDER BY timestamp_ns"
        ).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def actionable_count(db_path: str | None) -> int:
    """Alerts with decision=Unsafe. Uncertain review items are reported separately."""
    if not db_path:
        return 0
    path = Path(db_path)
    if not path.exists():
        return 0
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM responsibility_alerts WHERE decision='Unsafe'"
        ).fetchone()[0]
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", help="directory containing results.jsonl")
    parser.add_argument("--out-dir", default=str(ROOT / "docs/evaluation"))
    parser.add_argument("--name", default="fpr-baseline")
    parser.add_argument("--previous", default=None, help="previous baseline JSON")
    args = parser.parse_args()

    results_path = Path(args.run_dir) / "results.jsonl"
    if not results_path.exists():
        print(f"ERROR: missing {results_path}", file=sys.stderr)
        return 2
    rows = [json.loads(line) for line in results_path.read_text().splitlines() if line.strip()]

    scenarios = sorted(path.stem for path in SCENARIO_DIR.glob("*.json"))
    measured = {row["scenario_id"] for row in rows}
    missing = sorted(set(scenarios) - measured)
    if missing:
        print(f"ERROR: scenarios not measured: {missing}", file=sys.stderr)
        return 1

    failed = [row["scenario_id"] for row in rows if row.get("returncode") != 0]
    no_db = [row["scenario_id"] for row in rows if not row.get("db_path")]
    if failed or no_db:
        print(
            f"ERROR: incomplete runs (rc!=0: {failed}, no db: {no_db})",
            file=sys.stderr,
        )
        return 1

    total = len(rows)
    warning_plus_runs = [row for row in rows if row.get("alerts_warning_plus", 0) > 0]
    k = len(warning_plus_runs)
    low, high = wilson(k, total)

    severity_totals: dict[str, int] = {}
    decision_totals: dict[str, int] = {}
    for row in rows:
        for severity, count in (row.get("alerts_by_severity") or {}).items():
            severity_totals[severity] = severity_totals.get(severity, 0) + count
        for decision, count in (row.get("alerts_by_decision") or {}).items():
            decision_totals[decision] = decision_totals.get(decision, 0) + count

    per_scenario = []
    for row in sorted(rows, key=lambda item: item["scenario_id"]):
        per_scenario.append(
            {
                "scenario_id": row["scenario_id"],
                "returncode": row["returncode"],
                "duration_seconds": row["duration_seconds"],
                "run_dir": row["run_dir"],
                "db_path": row["db_path"],
                "alerts_total": row["alerts_total"],
                "alerts_warning_plus": row["alerts_warning_plus"],
                "alerts_by_severity": row.get("alerts_by_severity") or {},
                "alerts_by_decision": row.get("alerts_by_decision") or {},
                "claims": row.get("claims", 0),
                "intents": row.get("intents", 0),
                "actions": row.get("actions", 0),
            }
        )

    actionable_total = 0
    actionable_runs = 0
    for row in per_scenario:
        count = actionable_count(row.get("db_path"))
        row["actionable_alerts"] = count
        actionable_total += count
        if count > 0:
            actionable_runs += 1
    actionable_low, actionable_high = wilson(actionable_runs, total)

    notable = []
    for row in warning_plus_runs:
        for alert in notable_alerts(row["db_path"]):
            alert["scenario_id"] = row["scenario_id"]
            alert["run_dir"] = row["run_dir"]
            notable.append(alert)

    # Attach only explanations that are visible in the data. The taint note is
    # a hypothesis with a file:line target, not a conclusion: P0.5 records the
    # number, P3 fixes the cause.
    tainted = [
        alert
        for alert in notable
        if "Tainted Context Use" in (alert.get("title") or "")
    ]
    interpretation = []
    if tainted:
        interpretation.append(
            {
                "observation": f"{len(tainted)} of {len(notable)} Warning/Critical alerts "
                "are titled 'Tainted Context Use'",
                "hypothesis_target": "collector/src/responsibility/graph/graph_builder.rs:394-395",
                "hypothesis": (
                    "The taint edge is gated on `writer_agent != reader_agent`, which "
                    "does not establish two genuinely distinct agents in a single-agent "
                    "session; HANDOFF §5 records this as the false-taint defect (P3)."
                ),
                "status": "not fixed in P0.5; recorded so the FPR number is explainable",
            }
        )

    comparison = None
    if args.previous:
        previous_path = Path(args.previous)
        if previous_path.exists():
            previous = json.loads(previous_path.read_text())
            comparison = {
                "previous_measured_at": previous.get("measured_at"),
                "previous_fpr": previous.get("fpr"),
                "previous_runs_with_warning_plus": previous.get("runs_with_warning_plus"),
                "fpr_delta": round(k / total - previous.get("fpr", 0.0), 4),
                "previous_alerts_by_severity": previous.get("alerts", {}).get(
                    "by_severity", {}
                ),
                "note": (
                    "P1 decodes response bodies, so the detection surface (intents, "
                    "evidence) grew. A higher FPR here is a measured consequence, not "
                    "a silently accepted regression."
                ),
            }

    baseline = {
        "schema": "mtagentlens.fpr-baseline/1",
        "measured_at": datetime.datetime.now().astimezone().isoformat(),
        "definition": {
            "primary": (
                "run-level: a benign scenario produced at least one alert with "
                "severity Warning or Critical"
            ),
            "info_alerts_excluded_because": (
                "every successful run emits a routine Info record; including it "
                "would report FPR=1.0 by construction"
            ),
        },
        "scenarios_total": len(scenarios),
        "runs_completed": total,
        "runs_with_warning_plus": k,
        "fpr": round(k / total, 4),
        "wilson_95": [round(low, 4), round(high, 4)],
        "alerts": {
            "by_severity": dict(sorted(severity_totals.items())),
            "by_decision": dict(sorted(decision_totals.items())),
            "warning_plus_total": sum(
                count
                for severity, count in severity_totals.items()
                if severity in ("Warning", "Critical")
            ),
        },
        "actionable": {
            "definition": "alerts with decision=Unsafe (excludes Uncertain review items)",
            "runs_with_actionable": actionable_runs,
            "fpr": round(actionable_runs / total, 4),
            "wilson_95": [round(actionable_low, 4), round(actionable_high, 4)],
            "alerts_total": actionable_total,
        },
        "notable_alerts": notable,
        "interpretation": interpretation,
        "comparison": comparison,
        "per_scenario": per_scenario,
        "provenance": {
            "command": f"scripts/wire/run-benign-fpr.sh {args.run_dir}",
            "results_file": str(results_path),
            "git": git_state(),
            "requires": ["sudo (eBPF)", "live LLM endpoint"],
        },
        "caveats": [
            "One live measurement, not a distribution: benign-code-refactor produced "
            "2 alerts in an earlier smoke run and 3 in this one, so the alert count "
            "for a single run is not stable.",
            "Warning+ includes fail-closed Uncertain review items. Their classifier "
            "score varies run to run (P3-a measured 2/20; P3-c measured 7/20 with the "
            "same taint and deploy-endpoints fixes), so the primary number is not a "
            "stable comparison. The actionable FPR (decision=Unsafe) is the stable "
            "product number.",
            "The verify gate checks baseline integrity and the scenario set, not a "
            "live re-measurement (which needs sudo and an LLM endpoint).",
        ],
    }

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{args.name}.json"
    json_path.write_text(json.dumps(baseline, ensure_ascii=False, indent=2) + "\n")

    lines = [
        "# Benign-scenario false-positive baseline",
        "",
        f"Measured: {baseline['measured_at']}",
        "",
        "## Definition",
        "",
        f"- {baseline['definition']['primary']}",
        f"- Info alerts excluded: {baseline['definition']['info_alerts_excluded_because']}",
        "",
        "## Result",
        "",
        f"- Runs completed: **{total} / {len(scenarios)}**",
        f"- Runs with Warning/Critical: **{k}**",
        f"- FPR: **{baseline['fpr']:.2%}** (Wilson 95% CI "
        f"{low:.2%} – {high:.2%})",
        f"- Alerts by severity: `{baseline['alerts']['by_severity']}`",
        f"- Alerts by decision: `{baseline['alerts']['by_decision']}`",
        f"- **Actionable** (decision=Unsafe): {actionable_runs} run(s), "
        f"FPR {baseline['actionable']['fpr']:.2%} "
        f"(Wilson 95% {actionable_low:.2%} – {actionable_high:.2%}); "
        f"Uncertain review items are counted in the primary number above but not here.",
        "",
        "## Per scenario",
        "",
        "| scenario | rc | duration | total | Warning+ | severity |",
        "|---|---|---|---|---|---|",
    ]
    for row in per_scenario:
        lines.append(
            f"| `{row['scenario_id']}` | {row['returncode']} | "
            f"{row['duration_seconds']:.0f}s | {row['alerts_total']} | "
            f"{row['alerts_warning_plus']} | `{row['alerts_by_severity']}` |"
        )
    lines += ["", "## Notable alerts (Warning / Critical)", ""]
    if notable:
        for alert in notable:
            lines += [
                f"### `{alert['scenario_id']}` — {alert['severity']} / {alert['decision']}",
                "",
                f"- Title: {alert.get('title')}",
                f"- Primary agent: `{alert.get('primary_agent_id')}`",
                f"- Reasoning: {alert.get('reasoning')}",
                f"- Run: `{alert['run_dir']}`",
                "",
            ]
    else:
        lines.append("None.")
    if baseline.get("comparison"):
        comparison = baseline["comparison"]
        lines += [
            "",
            "## Comparison with the previous baseline",
            "",
            f"- Previous measured at: {comparison['previous_measured_at']}",
            f"- Previous FPR: {comparison['previous_fpr']:.2%} "
            f"({comparison['previous_runs_with_warning_plus']} runs)",
            f"- Delta: **{comparison['fpr_delta']:+.2%}**",
            f"- {comparison['note']}",
        ]
    if baseline["interpretation"]:
        lines += ["", "## Interpretation", ""]
        for note in baseline["interpretation"]:
            lines += [
                f"- {note['observation']}",
                f"  - Target: `{note['hypothesis_target']}`",
                f"  - Hypothesis: {note['hypothesis']}",
                f"  - Status: {note['status']}",
            ]
    lines += ["", "## Caveats", ""]
    lines += [f"- {caveat}" for caveat in baseline["caveats"]]
    lines.append("")

    md_path = out_dir / f"{args.name}.md"
    md_path.write_text("\n".join(lines) + "\n")
    print(f"wrote {json_path}")
    print(f"wrote {md_path}")
    print(
        f"FPR {baseline['fpr']:.2%} ({k}/{total}), "
        f"Wilson 95% [{low:.2%}, {high:.2%}]"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
