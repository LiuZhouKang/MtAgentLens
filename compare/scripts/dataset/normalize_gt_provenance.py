#!/usr/bin/env python3
"""Disclose historical AI reviewer provenance without changing semantic labels."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path

DISCLOSURE = {
    "status": "historical_unrevalidated",
    "note": "Historical reviewer-agent notes are preserved verbatim; prompt/fixture freshness assertions have not been revalidated. Consult current byte checks. No human sign-off.",
}


def normalize_annotation(annotation: dict) -> dict:
    if annotation.get("method") == "human" or annotation.get("human_annotation") is True:
        raise ValueError("Cannot reclassify a human annotation with the historical AI migration")
    result = deepcopy(annotation)
    if "annotators" in result:
        result.setdefault("reviewer_agent_labels", result.pop("annotators"))
    if result.get("method") != "model_proposal":
        result.setdefault("legacy_method", result.get("method"))
    result.update(method="model_proposal", reviewer_kind="ai_agent", human_annotation=False,
                  human_review_status="not_performed", independence_verified=False,
                  provenance_status="partial_original_records_unavailable")
    return result


def normalize_gt(data: dict) -> dict:
    result = deepcopy(data)
    result["annotation"] = normalize_annotation(result.get("annotation") or {})
    quality = result.setdefault("quality", {})
    if quality.get("legacy_needs_human_review"):
        quality["needs_human_review"] = True
    quality["human_review_status"] = "not_performed"
    quality["notes_disclosure"] = DISCLOSURE.copy()
    if isinstance(quality.get("review"), dict):
        quality["review"]["human_review_status"] = "not_performed"
        quality["review"]["notes_disclosure"] = DISCLOSURE.copy()
    return result


def normalize_metadata(data: dict) -> dict:
    result = deepcopy(data)
    result["annotation"] = normalize_annotation(result.get("annotation") or {})
    result["annotation"]["review"] = "74 historical review requests received rule-based AI adjudication; human review was not performed. See per-scenario quality.review and preserved legacy records."
    result["quality"] = "ai_reviewed_codebook_v4_human_unverified"
    result["human_annotation"] = False
    result["paper_metrics_available"] = False
    result["publication_blockers"] = [
        "Scenario labels are AI-reviewed design expectations, not human-verified run outcomes.",
        "Independent blind review cannot be established from retained reviewer JSON alone.",
        "Runtime human annotation and causal ground truth require separate evidence-bound review.",
    ]
    result.setdefault("adjudication", {})["human_review_status"] = "not_performed"
    result["working_tree_status"] = "candidate_not_frozen"
    if result.get("version") == "1.4.0":
        result["version"] = "1.4.1-candidate"
    result["dataset_updated"] = "2026-10-25"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = args.dataset / "ground_truth"
    paths = [p for p in sorted(root.rglob("*.json"))
             if not any(part.startswith("_") for part in p.relative_to(root).parts)]
    updates = []
    for path in paths + [args.dataset / "metadata.json"]:
        data = json.loads(path.read_text(encoding="utf-8"))
        if path.name != "metadata.json" and data.get("schema") != "mtagentlens.ground_truth.v4":
            raise ValueError(f"Unexpected GT schema: {path}")
        normalized = normalize_metadata(data) if path.name == "metadata.json" else normalize_gt(data)
        if normalized != data:
            updates.append((path, normalized))
    # Validate every input before applying the provenance-only migration.
    if args.write:
        for path, data in updates:
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"scenarios": len(paths), "provenance_changes": len(updates), "written": args.write}))
    return int(bool(updates) and not args.write)


if __name__ == "__main__":
    raise SystemExit(main())
