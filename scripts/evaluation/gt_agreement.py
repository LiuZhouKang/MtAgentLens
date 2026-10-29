#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Inter-annotator agreement for FuxiBench ground truth v4.

Reads the reviewer sidecars written by the annotation workflow
(`ground_truth/_reviewers/<scenario>.json`), each holding the three reviewers'
independent labels and attack types, and computes:

  * Fleiss' kappa for `label` (attack/benign)
  * Fleiss' kappa for `attack_type` (A/B/C/benign)
  * percentage agreement for both
  * behavior-set Jaccard median when the sidecar records it

This is the only source of the kappa reported for the dataset. There is no
hardcoded value anywhere else.

Usage:
    python3 scripts/evaluation/gt_agreement.py \
        --reviewers datasets/FuxiBench/ground_truth/_reviewers \
        --out datasets/FuxiBench/ground_truth/_agreement.json
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path


def fleiss_kappa(rows: list[list[str]]) -> float | None:
    """Fleiss' kappa for a fixed number of raters per item."""
    rows = [r for r in rows if r]
    if not rows:
        return None
    n_raters = len(rows[0])
    if n_raters < 2 or any(len(r) != n_raters for r in rows):
        raise SystemExit("all items must have the same number of ratings")
    categories = sorted({c for r in rows for c in r})
    # P_i: agreement within item.
    p_items = []
    for r in rows:
        counts = {c: r.count(c) for c in categories}
        total = sum(counts.values())
        p = (sum(v * v for v in counts.values()) - total) / (total * (total - 1))
        p_items.append(p)
    p_bar = sum(p_items) / len(p_items)
    # P_e: chance agreement from marginal proportions.
    total_ratings = len(rows) * n_raters
    p_e = sum(
        (sum(1 for r in rows for c in r if c == cat) / total_ratings) ** 2
        for cat in categories
    )
    if p_e >= 1.0:
        return None
    return (p_bar - p_e) / (1.0 - p_e)


def percent_agreement(rows: list[list[str]]) -> float | None:
    rows = [r for r in rows if r]
    if not rows:
        return None
    agree = sum(1 for r in rows if len(set(r)) == 1)
    return agree / len(rows)


ANNOTATORS = ["刘周康", "唐浩钺", "刘锦样"]


def validate_reviewers(sidecar: dict, scenario_id: str) -> list[dict]:
    if sidecar.get("scenario_id") != scenario_id:
        raise ValueError(f"{scenario_id}: reviewer scenario identity mismatch")
    reviewers = sidecar.get("reviewers") or []
    if [r.get("name") for r in reviewers] != ANNOTATORS:
        raise ValueError(f"{scenario_id}: missing or duplicate named reviewers")
    for r in reviewers:
        review = r.get("review") or {}
        if review.get("label") not in ("attack", "benign"):
            raise ValueError(f"{scenario_id}: incomplete raw review for {r['name']}: missing label")
        if review.get("attack_type") not in ("A", "B", "C", "benign"):
            raise ValueError(f"{scenario_id}: invalid or missing attack_type for {r['name']}")
        if (review["label"] == "benign") != (review["attack_type"] == "benign"):
            raise ValueError(f"{scenario_id}: inconsistent reviewer label/type for {r['name']}")
        if not isinstance(review.get("expected_behaviors"), list):
            raise ValueError(f"{scenario_id}: incomplete behavior list for {r['name']}")
    return reviewers


def agreement_report(root: Path) -> dict:
    labels, types, jaccards, per_scenario = [], [], [], []
    for path in sorted(root.glob("*.json")):
        sidecar = json.loads(path.read_text(encoding="utf-8"))
        sid = sidecar.get("scenario_id", path.stem)
        reviewers = validate_reviewers(sidecar, sid)
        row_labels = [r["review"]["label"] for r in reviewers]
        row_types = [r["review"]["attack_type"] for r in reviewers]
        labels.append(row_labels)
        types.append(row_types)
        sets = [{b["attack_pattern"] for b in r["review"]["expected_behaviors"]} for r in reviewers]
        pairs = [len(a & b) / len(a | b) for i, a in enumerate(sets) for b in sets[i + 1:] if a | b]
        # Empty benign behavior sets are not evidence of semantic agreement.
        if pairs:
            jaccards.append(statistics.median(pairs))
        per_scenario.append({"scenario_id": sid, "labels": row_labels, "attack_types": row_types})
    return {"schema": "mtagentlens.gt-agreement/2", "reviewers_dir": str(root),
        "scenarios": len(labels), "complete": True,
        "label": {"fleiss_kappa": fleiss_kappa(labels), "percent_agreement": percent_agreement(labels)},
        "attack_type": {"fleiss_kappa": fleiss_kappa(types), "percent_agreement": percent_agreement(types)},
        "pattern_category_jaccard_median": statistics.median(jaccards) if jaccards else None,
        "pattern_category_items": len(jaccards),
        "note": "AI reviewer agents; not human annotation. Jaccard compares attack-pattern category sets, not behavior semantics. Undefined kappa is null.",
        "per_scenario": per_scenario}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reviewers", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    try:
        report = agreement_report(args.reviewers)
    except (ValueError, KeyError) as exc:
        print(json.dumps({"complete": False, "problems": [str(exc)]}, ensure_ascii=False))
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.out:
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
