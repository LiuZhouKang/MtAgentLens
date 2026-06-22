#!/usr/bin/env python3
"""Write a compact acceptance summary for frozen dataset quality evidence."""
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=Path("compare/Dataset/FuxiBench"))
    args = parser.parse_args()
    dataset = args.dataset.resolve()
    summary = build_summary(dataset)
    out = dataset / "quality" / "acceptance_summary.json"
    write_json(out, summary)
    print(f"Wrote {out}")
    return 0


def build_summary(dataset: Path) -> dict[str, Any]:
    manifest = read_json(dataset / "quality" / "manifest.json")
    promotion = read_json(dataset / "quality" / "gt_v2_promotion_report.json")
    candidate_summary = read_json(dataset / "quality" / "gt_v2_candidate_summary.json")
    backfill = read_json(dataset / "quality" / "type_c_boundary_backfill_report.json")
    evaluation = evaluate_json(dataset)
    type_c = type_c_boundary_summary(dataset, manifest)
    noise = expected_claim_noise_count(dataset)
    return {
        "schema": "mtagentprism.dataset_acceptance_summary.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": dataset.as_posix(),
        "quality_manifest": manifest.get("summary", {}),
        "ground_truth": {
            "promoted": promotion.get("summary", {}).get("promoted"),
            "candidates": candidate_summary.get("total_candidates"),
            "candidates_with_expected_claims": candidate_summary.get("candidates_with_expected_claims"),
            "attack_candidates_without_expected_claims": candidate_summary.get(
                "attack_candidates_without_expected_claims"
            ),
            "bad_expected_claims": noise,
        },
        "type_c_topology": {
            "samples": type_c,
            "backfill_summary": backfill.get("summary", {}),
        },
        "evaluation": {
            "paper_metrics_available": evaluation["quality_gate"]["paper_metrics_available"],
            "reason": evaluation["quality_gate"]["reason"],
            "total_cases": evaluation["total_cases"],
            "attack_cases": evaluation["attack_cases"],
            "benign_cases": evaluation["benign_cases"],
            "scenario_recall": evaluation["metrics"]["scenario_recall"],
            "precision": evaluation["metrics"]["precision"],
            "f1": evaluation["metrics"]["f1"],
            "total_expected_claims": evaluation["metrics"]["total_expected_claims"],
            "matched_claims": evaluation["metrics"]["matched_claims"],
            "claim_coverage": evaluation["metrics"]["claim_coverage"],
            "missing_responsibility_db": evaluation["quality"]["missing_responsibility_db"],
        },
        "validation_commands": [
            "python3 -m pytest compare/tests/test_dataset_adapter.py compare/tests/test_evaluate_frozen.py compare/tests/test_dataset_quality_audit.py compare/tests/test_gt_v2_candidate_generation.py compare/tests/test_gt_v2_promotion.py compare/tests/test_type_c_boundary_backfill.py -q",
            "cd collector && cargo test --lib accountability::identity::union_find::tests --quiet",
            "cd collector && cargo build --release --quiet",
            "python3 env/scripts/evaluate_frozen.py --dataset compare/Dataset/FuxiBench",
        ],
    }


def evaluate_json(dataset: Path) -> dict[str, Any]:
    proc = subprocess.run(
        [
            "python3",
            "env/scripts/evaluate_frozen.py",
            "--dataset",
            dataset.as_posix(),
            "--json",
        ],
        cwd=Path(__file__).resolve().parents[3],
        check=True,
        text=True,
        capture_output=True,
    )
    return json.loads(proc.stdout)


def type_c_boundary_summary(dataset: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for sample in manifest.get("samples", []):
        gt = sample.get("ground_truth_summary", {})
        if not isinstance(gt, dict) or gt.get("attack_type") != "C":
            continue
        db_rel = sample.get("paths", {}).get("responsibility_db")
        if not isinstance(db_rel, str):
            continue
        db_path = dataset / db_rel
        con = sqlite3.connect(db_path)
        try:
            boundary_count = con.execute("select count(*) from subagent_boundaries").fetchone()[0]
            agent_count = con.execute("select count(*) from agent_identities").fetchone()[0]
        finally:
            con.close()
        rows.append(
            {
                "scenario_id": sample.get("scenario_id"),
                "category": sample.get("category"),
                "agent_identities": agent_count,
                "subagent_boundaries": boundary_count,
            }
        )
    return rows


def expected_claim_noise_count(dataset: Path) -> int:
    markers = (
        "/.kiro/",
        "/handoffs/",
        "/.venv",
        "site-packages",
        "node_modules",
        "/env/runs/",
        "package-lock",
        "cargo.lock",
        "boundary.resource./bin/",
        "boundary.resource./usr/bin/",
    )
    count = 0
    for path in (dataset / "ground_truth").rglob("*.json"):
        if "_external_archive" in path.parts:
            continue
        data = read_json(path)
        for claim in data.get("expected_behavior", {}).get("expected_claims", []):
            lower = str(claim).lower()
            if any(marker in lower for marker in markers):
                count += 1
    return count


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
