#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Summarize FuxiBench ground truth v4 and compare with the frozen metadata.

Reports label/type distribution, behavior counts, scenarios needing human
review, and any attack_type disagreement with the frozen manifest (a
disagreement is data, not an error: the reviewers saw the scenario text).

Usage:
    python3 scripts/evaluation/gt_summary.py --dataset datasets/FuxiBench
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    args = parser.parse_args()
    dataset = args.dataset
    gt_root = dataset / "ground_truth"
    manifest = json.loads((dataset / "prompt_inputs" / "manifest.json").read_text())
    frozen = {e["scenario_id"]: e for e in manifest["entries"]}

    rows = []
    for path in gt_root.rglob("*.json"):
        if any(part.startswith("_") for part in path.relative_to(gt_root).parts):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if data.get("schema") != "mtagentlens.ground_truth.v4":
            continue
        rows.append(data)

    labels = Counter(r["label"] for r in rows)
    types = Counter(r["attack_type"] for r in rows)
    behaviors = [len(r.get("expected_behaviors") or []) for r in rows]
    def unresolved(row: dict) -> bool:
        quality = row.get("quality") or {}
        # AI adjudication and historical waivers do not constitute human review.
        return bool(quality.get("needs_human_review") or quality.get("legacy_needs_human_review"))

    review = [r["scenario_id"] for r in rows if unresolved(r)]
    ai_resolved = [
        r["scenario_id"]
        for r in rows
        if ((r.get("quality") or {}).get("review") or {}).get("status") == "resolved"
    ]
    mismatches = []
    for row in rows:
        entry = frozen.get(row["scenario_id"])
        if entry and entry["attack_type"] != row["attack_type"]:
            mismatches.append(
                {
                    "scenario_id": row["scenario_id"],
                    "frozen": entry["attack_type"],
                    "gt_v4": row["attack_type"],
                }
            )
    report = {
        "schema": "mtagentlens.gt-summary/1",
        "scenarios": len(rows),
        "labels": dict(labels),
        "attack_types": dict(types),
        "behaviors_total": sum(behaviors),
        "behaviors_median": statistics.median(behaviors) if behaviors else 0,
        "needs_human_review": sorted(review),
        "needs_human_review_count": len(review),
        "ai_adjudicated_count": len(ai_resolved),
        "human_review_performed": False,
        "human_review_evidence": "Not certified by scenario GT; use evidence-bound runtime annotations and attestations.",
        "legacy_needs_human_review_count": sum(bool((r.get("quality") or {}).get("legacy_needs_human_review")) for r in rows),
        "attack_type_mismatches": mismatches,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
