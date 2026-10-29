# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Build the self-contained FuxiBench 2.0 dataset package.

Stages:
  gt        migrate the current merged GT to the two-type taxonomy with portable paths
  package   build datasets/FuxiBench from scenarios/fixtures/bench/prompts/GT/evidence
  validate  run the portability/completeness validator on a package

The migration is deliberately deterministic: no model calls, no timestamps in record
fields, and every legacy value is preserved under an explicit legacy key.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIVE_GT = ROOT / "datasets/FuxiBench/ground_truth"
WORK = ROOT / "local/workpackages/2026-10-26-final-dataset"
PACKAGE = ROOT / "datasets/FuxiBench"
OLD_TO_NEW = {"B": "A", "C": "B"}
TAXONOMY = "fuxibench-2type/1"
REPLACEMENTS = [
    ("/home/lzk/MtAgentLens/", ""),
    ("/home/lzk/MtAgentLens", ""),
    ("local/workpackages/2026-10-26-final-dataset/gt/runs/full-93/", "evidence/v5/"),
    ("local/workpackages/2026-10-26-final-dataset/integration/full-93/", "evidence/v5/"),
    ("local/workpackages/2026-10-26-final-dataset/release/FuxiBench-v5/", "evidence/legacy/release-v5/"),
    ("local/workpackages/2026-10-26-final-dataset/", "evidence/v5/"),
    ("local/workpackages/2026-10-25-rc-fix4/", "evidence/legacy/rc-fix4/"),
    ("local/workpackages/2026-10-25-ai-dataset/", "evidence/legacy/ai-silver-v1/"),
    ("local/workpackages/2026-10-25-sink-release3/", "evidence/legacy/release3/"),
    ("local/workpackages/", "evidence/legacy/"),
    ("datasets/FuxiBench/", ""),
    ("compare/Dataset/", "evidence/legacy/datasets/"),
    ("compare/", "evidence/legacy/repo/"),
    ("env/scenarios/fixtures/", "fixtures/"),
    ("env/scenarios/", "scenarios/"),
    ("env/scenarios", "scenarios"),
    ("/home/lzk", "<repo>"),
    ("compare/Dataset", "dataset"),
]
FORBIDDEN = ("/home/lzk", "local/workpackages", "local/e8", "env/scenarios", "compare/Dataset")
# Bare "/home/" is allowed only as attack-detection semantics (regex patterns / <fixture>/home).


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def portable(value):
    """Recursively rewrite dataset-external paths; /tmp remains scenario semantics."""
    if isinstance(value, dict):
        return {k: portable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [portable(v) for v in value]
    if not isinstance(value, str):
        return value
    text = value
    for old, new in REPLACEMENTS:
        text = text.replace(old, new)
    return text


def legacy_label_fields(record: dict) -> tuple[str, str]:
    return record["label"], record["attack_type"]


def _benign_activity(record: dict) -> dict:
    activity = copy.deepcopy(record.get("benign_activity") or {})
    if not activity.get("summary"):
        activity["summary"] = (
            "Independent 3-annotator + adjudication audit found this scenario benign; "
            "it is retained as an audited benign record. The historical v4 attack contract "
            "is preserved under legacy_v4_detection_contract for provenance only."
        )
    return activity


def _new_annotation(old: dict) -> dict:
    return {
        "method": "ai_consensus_3",
        "annotation_version": "fuxibench-2.0",
        "reviewer_agent_labels": old.get("reviewer_agent_labels") or old.get("annotators") or [],
        "collector_output_consulted": False,
        "human_annotation": False,
        "human_review_status": "not_performed",
        "legacy_v4": {
            "method": old.get("method"),
            "agreement": old.get("agreement"),
            "inputs": old.get("inputs"),
            "reviewer_records": old.get("reviewer_records"),
            "agreement_report": old.get("agreement_report"),
        },
    }


def migrate_record(record: dict) -> dict:
    out = copy.deepcopy(record)
    old_label, old_type = legacy_label_fields(out)
    excluded = bool((out.get("quality") or {}).get("detection_excluded"))
    out["taxonomy_version"] = TAXONOMY
    out["legacy_label"] = old_label
    out["legacy_attack_type"] = old_type
    if excluded:
        # Audited truth: the independent adjudication found the scenario benign. The
        # benchmark's historical attack contract is preserved but never scored.
        contract = {}
        for key in ("expected_behaviors", "min_decision", "should_alert", "execution_check", "payload"):
            if key in out:
                contract[key] = out.pop(key)
        out["legacy_v4_detection_contract"] = contract
        out["label"] = "benign"
        out["attack_type"] = "benign"
        out["expected_behaviors"] = []
        out["min_decision"] = "Safe"
        out["should_alert"] = False
        out["payload"] = {"carrier": "none", "carrier_type": "none",
                          "description": "Audited benign; historical payload is in legacy_v4_detection_contract.",
                          "payload_channel_classification": "benign"}
        out["execution_check"] = {}
        out["benign_activity"] = _benign_activity(out)
    elif old_label == "attack":
        out["attack_type"] = OLD_TO_NEW.get(old_type, old_type)
        out["benign_activity"] = _benign_activity(out) if out.get("benign_activity") else out.get("benign_activity")

    # Keep the old v4 annotation as provenance, expose one current annotation block that
    # validate_gt can consume without pretending the old reviewers are v5 label authors.
    old_annotation = out.pop("annotation", {}) or {}
    out["legacy_annotation"] = old_annotation
    out["annotation"] = _new_annotation(old_annotation)

    la = out.setdefault("label_annotation", {})
    la["taxonomy_version"] = TAXONOMY
    la["legacy_v5_label"] = la.get("v5_label", old_label)
    la["legacy_v5_attack_type"] = la.get("v5_attack_type", "benign")
    la["v5_label"] = out["label"]
    la["v5_attack_type"] = out["attack_type"]
    if la.get("v5_disagreement"):
        la["v5_disagreement"]["legacy_mapping"] = {"old_attack_type": old_type, "new_attack_type": out["attack_type"]}

    payload = out.get("payload")
    if isinstance(payload, dict) and isinstance(payload.get("payload_channel_classification"), str):
        channel = payload["payload_channel_classification"]
        if channel in OLD_TO_NEW:
            payload["legacy_payload_channel_classification"] = channel
            payload["payload_channel_classification"] = OLD_TO_NEW[channel]
        elif channel == "benign" and out["attack_type"] == "benign":
            pass

    # Old quality review paths/names are historical; keep the block but make the current
    # human/status flags explicit and portable.
    quality = out.setdefault("quality", {})
    quality["legacy_needs_human_review"] = bool(quality.get("needs_human_review"))
    quality["needs_human_review"] = False
    quality["human_review_status"] = "not_performed"
    quality["taxonomy_version"] = TAXONOMY
    if excluded:
        quality["detection_excluded"] = True
        quality["exclusion_reason"] = quality.get("exclusion_reason") or (
            "Independent 3-annotator + adjudication audit found this scenario benign; "
            "the historical v4 attack contract is preserved for provenance only."
        )
    return portable(out)


def migrate_tree(src: Path, dst: Path) -> dict:
    dst.mkdir(parents=True, exist_ok=True)
    records = []
    for path in sorted(src.rglob("*.json")):
        rel = path.relative_to(src)
        if any(part.startswith("_") for part in rel.parts):
            continue
        record = migrate_record(json.loads(path.read_text(encoding="utf-8")))
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        records.append(record)
    attack = [r for r in records if r["label"] == "attack"]
    benign = [r for r in records if r["label"] == "benign"]
    excluded = [r for r in records if (r.get("quality") or {}).get("detection_excluded")]
    included = [r for r in records if not (r.get("quality") or {}).get("detection_excluded")]
    summary = {
        "records": len(records),
        "attack": len(attack),
        "benign": len(benign),
        "excluded": len(excluded),
        "included_attack": sum(1 for r in included if r["label"] == "attack"),
        "included_benign": sum(1 for r in included if r["label"] == "benign"),
        "attack_types": {t: sum(1 for r in attack if r["attack_type"] == t) for t in ("A", "B")},
        "included_attack_types": {t: sum(1 for r in included if r["attack_type"] == t) for t in ("A", "B")},
    }
    (dst.parent / "GT-MIGRATION-SUMMARY.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def scan_forbidden(root: Path) -> list[dict]:
    problems = []
    for path in sorted(root.rglob("*.json")):
        text = path.read_text(encoding="utf-8", errors="replace")
        for needle in FORBIDDEN:
            if needle in text:
                problems.append({"path": str(path.relative_to(root)), "contains": needle})
                break
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["gt", "validate"])
    parser.add_argument("--src", type=Path, default=LIVE_GT)
    parser.add_argument("--out", type=Path, default=PACKAGE / "ground_truth")
    args = parser.parse_args()
    if args.stage == "gt":
        summary = migrate_tree(args.src.resolve(), args.out.resolve())
        problems = scan_forbidden(args.out.resolve())
        print(json.dumps({"summary": summary, "forbidden": problems[:5], "forbidden_count": len(problems)}, ensure_ascii=False, indent=2))
        return 1 if problems else 0
    problems = scan_forbidden(args.out.resolve())
    print(json.dumps({"root": str(args.out), "forbidden": problems[:20], "count": len(problems)}, ensure_ascii=False, indent=2))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
