# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Merge `mtagentlens replay-sensitivity` reports into one E3 table.

The raw reports come from `mtagentlens replay-sensitivity` over the frozen
checkpoints of a campaign (one report per arm). This script never recomputes
routes: it only checks the reports are consistent and renders the paper table,
so a number in the paper can always be traced back to a replay report.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCHEMA = "mtagentlens.threshold-sensitivity/1"
SUMMARY_SCHEMA = "mtagentlens.e3-threshold-sensitivity/1"


def load_report(path: Path) -> dict:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("schema") != SCHEMA:
        raise ValueError(f"{path}: unexpected schema {payload.get('schema')!r}")
    if not payload.get("points"):
        raise ValueError(f"{path}: no grid points")
    for point in payload["points"]:
        if not point["threshold_safe"] < point["threshold_unsafe"]:
            raise ValueError(f"{path}: inverted thresholds {point}")
    return payload


def merge(reports: dict[str, dict]) -> dict:
    arms = []
    for arm, report in sorted(reports.items()):
        baseline = {route: count for route, count in report["baseline_route"]}
        runs = report["runs"]
        invariances = sorted({
            point["verdict_flips_vs_baseline"] for point in report["points"]
        })
        arms.append({
            "arm": arm,
            "runs": runs,
            "runs_without_checkpoint": report.get("runs_without_checkpoint", 0),
            "baseline_route": baseline,
            "grid_points": len(report["points"]),
            "max_route_flips": max(point["route_flips_vs_baseline"] for point in report["points"]),
            "min_route_flips": min(point["route_flips_vs_baseline"] for point in report["points"]),
            "verdict_flip_counts": invariances,
            "verdict_invariant_across_grid": invariances == [0],
            "observer_load_range": [
                min(point["to_observer"] for point in report["points"]),
                max(point["to_observer"] for point in report["points"]),
            ],
            "baseline_observer_load": baseline.get("ToObserver", 0),
            "relative_weights": {
                "points": len(report["relative_weight_points"]),
                "dimensions": sorted({point["dimension"] for point in report["relative_weight_points"]}),
                "verdict_flip_counts": sorted({point["verdict_flips_vs_baseline"] for point in report["relative_weight_points"]}),
                "max_route_flips": max(point["route_flips_vs_baseline"] for point in report["relative_weight_points"]),
            } if report.get("relative_weight_points") else None,
            "scoring_points": report.get("scoring_points", []),
            "scoring_verdict_flip_counts": sorted({
                point["verdict_flips_vs_baseline"]
                for point in report.get("scoring_points", [])
            }),
            "scoring_verdict_invariant": all(
                point["verdict_flips_vs_baseline"] == 0
                for point in report.get("scoring_points", [])
            ),
            "ablation_points": report.get("ablation_points", []),
            "ablation_dimensions": sorted({point["dimension"] for point in report.get("ablation_points", [])}),
            "ablation_verdict_flip_counts": sorted({
                point["verdict_flips_vs_baseline"]
                for point in report.get("ablation_points", [])
            }),
            "ablation_verdict_invariant": all(
                point["verdict_flips_vs_baseline"] == 0
                for point in report.get("ablation_points", [])
            ),
        })
    return {
        "schema": SUMMARY_SCHEMA,
        "arms": arms,
        "note": ("Analysis-kernel replay with recorded Observer observations held fixed; "
                 "route changes do not measure new Observer calls or end-to-end detection robustness. "
                 "Uniform weight scaling is a normalization control. Relative weights vary one dimension at a time."),
    }


def render_table(summary: dict) -> str:
    lines = ["| arm | runs | baseline route (DS/OB/DU) | threshold route flips (min–max) "
             "| Observer-route candidates (min–max) | threshold verdict flips | scoring verdict flips |",
             "|---|---|---|---|---|---|---|"]
    for arm in summary["arms"]:
        route = arm["baseline_route"]
        lines.append(
            f"| {arm['arm']} | {arm['runs']} | {route.get('DirectSafe', 0)}/"
            f"{route.get('ToObserver', 0)}/{route.get('DirectUnsafe', 0)} "
            f"| {arm['min_route_flips']}–{arm['max_route_flips']} "
            f"| {arm['observer_load_range'][0]}–{arm['observer_load_range'][1]} "
            f"| {arm['verdict_flip_counts']} | {arm['scoring_verdict_flip_counts']} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", action="append", default=[],
                        metavar="ARM=PATH", help="arm=path to a replay-sensitivity report")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    reports = {}
    for item in args.report:
        arm, _, path = item.partition("=")
        if not arm or not path:
            print(f"error: expected ARM=PATH, got {item!r}", file=sys.stderr)
            return 2
        reports[arm] = load_report(Path(path))
    if not reports:
        print("error: at least one --report is required", file=sys.stderr)
        return 2
    summary = merge(reports)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(render_table(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
