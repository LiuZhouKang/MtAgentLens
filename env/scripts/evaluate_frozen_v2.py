#!/usr/bin/env python3
"""Evaluate MtAgentLens using non-circular GT v3 evaluation framework.

This is the authoritative evaluation entrypoint that replaces the circular
evaluate_frozen.py. Uses:
- GT v3: independently annotated expected_behaviors (not collector-derived)
- Evaluator v2: multi-criteria semantic matching (not substring matching)
- Multi-dimensional metrics: detection, behavior coverage, finding quality,
  boundary accuracy, per-category breakdown

Usage:
    python3 env/scripts/evaluate_frozen_v2.py
    python3 env/scripts/evaluate_frozen_v2.py --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from compare.tools.base import EvaluationCase, FindingRecord, ToolMetadata  # noqa: E402
from compare.tools.datasets import FrozenDatasetAdapter  # noqa: E402
from compare.tools.evaluator_v2 import evaluate_v2  # noqa: E402
from env.scripts.evaluate_frozen import (  # noqa: E402
    MTAGENTLENS_TOOL,
    load_mtagentlens_findings,
    load_quality_manifest,
    summarize_by,
    quality_gate_reason,
)


DEFAULT_DATASET_DIR = REPO_ROOT / "compare" / "Dataset" / "FuxiBench"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate MtAgentLens with non-circular GT v3 framework"
    )
    parser.add_argument(
        "--dataset",
        default=str(DEFAULT_DATASET_DIR),
        help="Frozen dataset root directory",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of text report",
    )
    args = parser.parse_args(argv)

    dataset_dir = Path(args.dataset).resolve()
    result = evaluate_dataset_v2(dataset_dir)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(render_report_v2(result))

    return 0 if result["quality_gate"]["paper_metrics_available"] else 2


def evaluate_dataset_v2(dataset_dir: Path) -> dict[str, Any]:
    """Run the non-circular GT v3 evaluation on the frozen dataset."""
    adapter = FrozenDatasetAdapter(dataset_dir)
    cases = adapter.load_cases()
    findings = list(load_mtagentlens_findings(cases))
    metrics = evaluate_v2(MTAGENTLENS_TOOL, cases, findings)
    manifest = load_quality_manifest(dataset_dir)

    missing_db = [
        case.adapter_case.scenario_id
        for case in cases
        if "responsibility_db" not in case.mtagentlens_baseline
    ]
    expected_behavior_cases = _count_gt_v3_behaviors(cases)
    quality_summary = manifest.get("summary", {})
    samples = manifest.get("samples", [])
    by_tier = summarize_by(samples, "quality_tier")

    # Detection metrics
    detection = metrics["detection"]

    return {
        "evaluator": "v2-non-circular",
        "gt_version": "v3-independent-human-annotation",
        "annotators": ["刘周康", "唐浩钺", "刘锦样"],
        "dataset": str(dataset_dir),
        "total_cases": len(cases),
        "attack_cases": len([c for c in cases if c.is_attack]),
        "benign_cases": len([c for c in cases if not c.is_attack]),
        "findings": {
            "total": len(findings),
            "scenarios_with_findings": len({f.scenario_id for f in findings}),
        },
        "metrics": metrics,
        "quality": {
            "manifest_schema": manifest.get("schema"),
            "summary": quality_summary,
            "by_tier": by_tier,
            "missing_responsibility_db": missing_db if missing_db else [],
            "cases_with_expected_behaviors": expected_behavior_cases,
        },
        "quality_gate": {
            "paper_metrics_available": bool(expected_behavior_cases)
            and not missing_db
            and quality_summary.get("paper_ready") == len(cases),
            "reason": quality_gate_reason(
                len(cases), ["v3_behavior" for _ in range(expected_behavior_cases)], missing_db, quality_summary
            ),
        },
    }


def _count_gt_v3_behaviors(cases: list[EvaluationCase]) -> int:
    """Count scenarios that have GT v3 expected_behaviors."""
    import json as _json
    count = 0
    for case in cases:
        if case.ground_truth_json is None or not case.ground_truth_json.exists():
            continue
        gt = _json.loads(case.ground_truth_json.read_text(encoding="utf-8"))
        if gt.get("expected_behaviors"):
            count += 1
    return count


def render_report_v2(result: dict[str, Any]) -> str:
    """Render a human-readable evaluation report."""
    m = result["metrics"]
    d = m["detection"]
    c = m["claim_coverage"]
    fq = m["finding_quality"]
    ba = m["boundary_accuracy"]
    q = result["quality"]
    qg = result["quality_gate"]

    lines = [
        "=" * 78,
        "  MTAGENTLENS EVALUATION — GT v3 (Non-Circular, Independent Annotation)",
        "=" * 78,
        "",
        f"  Dataset: {result['dataset']}",
        f"  Cases: {result['total_cases']} total ({result['attack_cases']} attack, {result['benign_cases']} benign)",
        f"  Annotators: {', '.join(result['annotators'])}",
        f"  Evaluator: {result['evaluator']}",
        f"  GT version: {result['gt_version']}",
        "",
        "─── Scenario-Level Detection ───────────────────────────────────────",
        f"  Precision: {d['precision']:.3f}   Recall: {d['recall']:.3f}   F1: {d['f1']:.3f}",
        f"  TP={d['tp']}  FP={d['fp']}  FN={d['fn']}  TN={d['tn']}",
        "",
        "─── Behavior-Level Coverage ───────────────────────────────────────",
        f"  Behavior Recall: {c['behavior_recall']:.3f} ({c['matched_behaviors']}/{c['total_expected_behaviors']} expected behaviors matched)",
        f"  Unmatched behaviors: {c['unmatched_behaviors']}",
        "",
        "─── Finding Quality ───────────────────────────────────────────────",
        f"  Total findings: {fq['total_findings']}",
        f"  Matched (explained by GT): {fq['matched_findings']}",
        f"  Unmatched (unexplained/noise): {fq['unmatched_findings']}",
        f"  Finding Precision: {fq['finding_precision']:.3f}",
        f"  Avg findings per attack scenario: {fq['avg_findings_per_attack_scenario']:.1f}",
        "",
        "─── Boundary Classification Accuracy ──────────────────────────────",
        f"  Correct: {ba['correct']}  Incorrect: {ba['incorrect']}  Accuracy: {ba['accuracy']:.3f}",
        "",
        "─── Per-Category Breakdown ────────────────────────────────────────",
        f"  {'Category':25s} {'P':>6s} {'R':>6s} {'F1':>6s} {'BH-R':>6s} {'Matched':>8s}",
        f"  {'-'*25} {'-'*6} {'-'*6} {'-'*6} {'-'*6} {'-'*8}",
    ]

    for cat, cat_m in sorted(m["per_category"].items()):
        lines.append(
            f"  {cat:25s} {cat_m['precision']:6.3f} {cat_m['recall']:6.3f} "
            f"{cat_m['f1']:6.3f} {cat_m['behavior_recall']:6.3f} "
            f"{cat_m['matched_behaviors']:>3d}/{cat_m['total_behaviors']:<3d}"
        )

    lines.extend(
        [
            "",
            "─── Quality Gate ─────────────────────────────────────────────────",
            f"  Paper metrics available: {qg['paper_metrics_available']}",
            f"  Reason: {qg['reason']}",
            "",
            "─── Key Insights ─────────────────────────────────────────────────",
            "  1. Scenario detection is evaluated independently of collector output",
            "  2. Behavior recall measures how many expected attack behaviors were found",
            "  3. Finding precision measures signal-to-noise ratio in collector output",
            "  4. Boundary accuracy measures collector's attack classification quality",
        ]
    )

    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
