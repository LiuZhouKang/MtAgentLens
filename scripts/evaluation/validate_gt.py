#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Validate FuxiBench ground truth v4.

Checks every frozen scenario (and the difficult-benign additions) has a GT
file with the required fields, concrete behavior patterns, the three named
annotators, and no claim that collector output was consulted.

Usage:
    python3 scripts/evaluation/validate_gt.py \
        --dataset datasets/FuxiBench
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ANNOTATORS = ["刘周康", "唐浩钺", "刘锦样"]
EXTRA_BENIGN = [
    "benign-credential-rotation-plan",
    "benign-env-example-review",
    "benign-etc-group-audit",
    "benign-heavy-build",
    "benign-known-hosts-audit",
    "benign-passwd-audit",
    "benign-ssh-config-audit",
]
REQUIRED = [
    "schema",
    "scenario_id",
    "category",
    "label",
    "attack_type",
    "task_summary",
    "payload",
    "min_decision",
    "should_alert",
    "execution_check",
    "annotation",
    "quality",
]
BEHAVIOR_REQUIRED = [
    "behavior_id",
    "description",
    "attack_pattern",
    "boundary_type",
    "severity",
    "should_alert",
    "expected_commands",
    "expected_resources",
    "evidence_requirements",
    "rationale",
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    args = parser.parse_args()
    dataset = args.dataset
    manifest = json.loads((dataset / "prompt_inputs" / "manifest.json").read_text())
    wanted = list(dict.fromkeys([e["scenario_id"] for e in manifest["entries"]] + EXTRA_BENIGN))
    gt_root = dataset / "ground_truth"

    index: dict[str, Path] = {}
    problems: list[str] = []
    for path in gt_root.rglob("*.json"):
        if any(part.startswith("_") for part in path.relative_to(gt_root).parts):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            problems.append(f"{path}: malformed JSON: {exc}")
            continue
        if not isinstance(data, dict):
            problems.append(f"{path}: GT must be an object")
            continue
        sid = data.get("scenario_id")
        if not isinstance(sid, str) or not sid.strip():
            problems.append(f"{path}: missing scenario identity")
        elif sid in index:
            problems.append(f"{sid}: duplicate GT identity: {index[sid]} and {path}")
        else:
            index[sid] = path
    summary = {"total": len(wanted), "present": 0, "attacks": 0, "benign": 0, "review": 0, "human_review_pending": 0}
    for sid in wanted:
        path = index.get(sid)
        if path is None:
            problems.append(f"{sid}: missing GT file")
            continue
        summary["present"] += 1
        data = json.loads(path.read_text(encoding="utf-8"))
        for key in REQUIRED:
            if key not in data:
                problems.append(f"{sid}: missing field {key}")
        if data.get("scenario_id") != sid:
            problems.append(f"{sid}: scenario_id mismatch")
        if data.get("schema") != "mtagentlens.ground_truth.v4":
            problems.append(f"{sid}: unsupported GT schema")
        label = data.get("label")
        attack_type = data.get("attack_type")
        if attack_type not in {"A", "B", "C", "benign"} or ((label == "benign") != (attack_type == "benign")):
            problems.append(f"{sid}: attack_type conflicts with label")
        if label not in ("attack", "benign"):
            problems.append(f"{sid}: invalid label {label!r}")
        if label == "attack":
            summary["attacks"] += 1
            behaviors = data.get("expected_behaviors") or []
            if not behaviors:
                problems.append(f"{sid}: attack with no expected_behaviors")
            for behavior in behaviors:
                for key in BEHAVIOR_REQUIRED:
                    if key not in behavior:
                        problems.append(f"{sid}: behavior missing {key}")
                if not behavior.get("expected_commands") and not behavior.get(
                    "expected_resources"
                ):
                    problems.append(
                        f"{sid}: behavior {behavior.get('behavior_id')} has no concrete pattern"
                    )
                for entry in (behavior.get("expected_commands") or []) + (
                    behavior.get("expected_resources") or []
                ):
                    try:
                        re.compile(entry.get("pattern", ""))
                    except re.error as exc:
                        problems.append(
                            f"{sid}: invalid regex {entry.get('pattern')!r}: {exc}"
                        )
        elif label == "benign":
            summary["benign"] += 1
            if data.get("expected_behaviors"):
                problems.append(f"{sid}: benign must not declare attack behaviors")
            if not (data.get("benign_activity") or {}).get("summary"):
                problems.append(f"{sid}: benign missing benign_activity.summary")
        annotation = data.get("annotation") or {}
        if annotation.get("human_annotators") != ANNOTATORS:
            problems.append(f"{sid}: human annotators must be {ANNOTATORS}")
        if annotation.get("human_review_status") != "completed":
            problems.append(f"{sid}: human review is not complete")
        if annotation.get("collector_output_consulted") is not False:
            problems.append(f"{sid}: must declare collector_output_consulted=false")
        quality = data.get("quality") or {}
        if quality.get("review_status") != "completed":
            problems.append(f"{sid}: quality review status is not completed")

    print(json.dumps({"schema": "mtagentlens.gt-validation/1", **summary, "problems": problems}, ensure_ascii=False, indent=2))
    return 1 if problems or summary["review"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
