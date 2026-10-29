#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Write a rule-based adjudication dossier back into FuxiBench ground truth.

The dossier is produced by the protocol in
``datasets/FuxiBench/ADJUDICATION-PROTOCOL.md``. This tool applies it
mechanically and never touches the three named annotators or their raw reviewer
records. An automated resolution gains a `quality.review` block and never
asserts human sign-off or removes an outstanding human-review requirement.

Usage:
    python3 scripts/evaluation/apply_gt_adjudication.py \
        --dossier /tmp/gt-adjudication.json \
        --dataset datasets/FuxiBench \
        --scenarios env/scenarios
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from datetime import date
from typing import Any

RESOLVED_VERDICTS = {"close", "close_with_documented_limitations"}
TRUE_VERDICTS = RESOLVED_VERDICTS | {"blocked"}
PROTOCOL = "rule-based-adjudication-v1"
RULE = "protocol §2.2"



def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _dump(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _ground_truth_path(dataset_dir: Path, scenario_id: str) -> Path:
    matches = [
        path
        for path in sorted((dataset_dir / "ground_truth").rglob(f"{scenario_id}.json"))
        if not any(part.startswith("_") for part in path.relative_to(dataset_dir).parts)
    ]
    if len(matches) != 1:
        raise FileNotFoundError(f"expected one GT file for {scenario_id}, got {len(matches)}")
    return matches[0]


def _scenario_path(scenarios_root: Path, scenario_id: str) -> Path | None:
    matches = [
        path
        for path in sorted(scenarios_root.rglob(f"{scenario_id}.json"))
        if not ({"fixtures", "archive", "bench", "roles"} & set(path.relative_to(scenarios_root).parts))
        and not any(part.startswith("_") for part in path.relative_to(scenarios_root).parts)
    ]
    if len(matches) > 1:
        raise ValueError(f"ambiguous scenario for {scenario_id}: {matches}")
    return matches[0] if matches else None


def apply_dossier(
    entries: list[dict[str, Any]],
    *,
    dataset_dir: Path,
    scenarios_root: Path,
    dry_run: bool = False,
) -> dict[str, int]:
    summary = {"resolved": 0, "blocked": 0, "type_changed": 0, "skipped": 0}
    seen: set[str] = set()
    pending: list[tuple[Path, dict[str, Any]]] = []

    for entry in entries:
        scenario_id = str(entry.get("scenario_id") or "")
        if not scenario_id or not all(c.isalnum() or c in "-_" for c in scenario_id):
            raise ValueError("dossier entry has no scenario_id")
        if scenario_id in seen:
            raise ValueError(f"duplicate dossier entry: {scenario_id}")
        seen.add(scenario_id)

        verdict = str(entry.get("verdict") or "")
        if verdict not in TRUE_VERDICTS:
            raise ValueError(f"{scenario_id}: unknown verdict {verdict!r}")
        evidence = [str(item) for item in entry.get("evidence") or [] if str(item).strip()]
        if verdict in RESOLVED_VERDICTS and not evidence:
            raise ValueError(f"{scenario_id}: resolved entries need evidence")

        gt_path = _ground_truth_path(dataset_dir, scenario_id)
        gt = _load(gt_path)
        quality = gt.setdefault("quality", {})
        recorded_type = str(gt.get("attack_type") or "")
        proposed_type = str(entry.get("proposed_type") or recorded_type)
        if proposed_type not in {"A", "B", "C", "benign"}:
            raise ValueError(f"{scenario_id}: invalid proposed_type")
        if (gt.get("label") == "benign") != (proposed_type == "benign"):
            raise ValueError(f"{scenario_id}: proposed type conflicts with label")
        type_changed = proposed_type != recorded_type
        prior_review = quality.get("review")
        prior_change = (
            prior_review.get("type_change") if isinstance(prior_review, dict) else None
        )

        if verdict in RESOLVED_VERDICTS and entry.get("verified") is not True:
            raise ValueError(
                f"{scenario_id}: refusing to close before the item is verified "
                "by the adversarial pass"
            )
        if (
            verdict in RESOLVED_VERDICTS
            and type_changed
            and entry.get("verified", False) is not True
        ):
            raise ValueError(f"{scenario_id}: a type change must be verified")

        review: dict[str, Any] = {
            "status": "resolved" if verdict in RESOLVED_VERDICTS else "blocked",
            "method": PROTOCOL,
            "rule": RULE,
            "resolved_at": date.today().isoformat(),
            "human_review": "not_performed",
            "reviewer_kind": "automated_adjudication",
            "notes_disclosure": (
                "quality.notes is the original 2026-10-20 adjudication record; "
                "statements about the pre-rebuild prompt bundle are superseded by "
                "this review block."
            ),
            "evidence": evidence,
            "limitations": [str(item) for item in entry.get("limitations") or []],
            "residual_risks": [str(item) for item in entry.get("residual_risks") or []],
        }
        if type_changed:
            review["type_change"] = {"from": recorded_type, "to": proposed_type, "rule": RULE}
        elif isinstance(prior_change, dict):
            # Idempotent re-application: keep the recorded history instead of
            # forgetting that this item was reclassified.
            review["type_change"] = prior_change

        if verdict in RESOLVED_VERDICTS:
            if "needs_human_review" in quality:
                quality["legacy_needs_human_review"] = quality["needs_human_review"]
            quality["review"] = review
            if type_changed:
                gt["attack_type"] = proposed_type
                payload = gt.get("payload")
                if isinstance(payload, dict) and "payload_channel_classification" in payload:
                    current = payload.get("payload_channel_classification")
                    # Some files carried a full rationale in this field. Do not
                    # destroy it: keep it as notes, then store the canonical type.
                    if isinstance(current, str) and current not in {
                        "A",
                        "B",
                        "C",
                        "benign",
                    }:
                        previous = payload.get("payload_channel_notes")
                        addition = f"legacy_payload_channel_classification: {current}"
                        payload["payload_channel_notes"] = (
                            f"{previous}\n{addition}" if isinstance(previous, str) and previous else addition
                        )
                    payload["payload_channel_classification"] = proposed_type
                scenario_path = _scenario_path(scenarios_root, scenario_id)
                if scenario_path is not None:
                    scenario = _load(scenario_path)
                    if scenario.get("attack_type") != proposed_type:
                        scenario["attack_type"] = proposed_type
                        pending.append((scenario_path, scenario))
                summary["type_changed"] += 1
            summary["resolved"] += 1
        else:
            quality["review"] = review
            summary["blocked"] += 1

        pending.append((gt_path, gt))

    # Validate the complete dossier before changing any source file.
    if not dry_run:
        for path, value in pending:
            _dump(path, value)
    return summary


def normalize_false_flags(*, dataset_dir: Path, dry_run: bool = False) -> int:
    """Remove legacy `needs_human_review: false` so review.status is the only truth.

    A `true` value is never removed here: without a resolved review it would
    hide an open item.
    """
    removed = 0
    ground_truth = dataset_dir / "ground_truth"
    for path in sorted(ground_truth.rglob("*.json")):
        if any(part.startswith("_") for part in path.relative_to(ground_truth).parts):
            continue
        gt = _load(path)
        quality = gt.get("quality")
        if not isinstance(quality, dict) or "needs_human_review" not in quality:
            continue
        if quality.get("needs_human_review") is not False:
            continue
        quality.pop("needs_human_review")
        if not dry_run:
            _dump(path, gt)
        removed += 1
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dossier", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, default=Path("datasets/FuxiBench"))
    parser.add_argument("--scenarios", type=Path, default=Path("env/scenarios"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--normalize-all",
        action="store_true",
        help="also drop legacy needs_human_review=false flags from every GT",
    )
    args = parser.parse_args()

    entries = json.loads(args.dossier.read_text(encoding="utf-8"))
    if not isinstance(entries, list):
        parser.error("dossier must be a JSON list")
    summary = apply_dossier(
        entries,
        dataset_dir=args.dataset,
        scenarios_root=args.scenarios,
        dry_run=args.dry_run,
    )
    if args.normalize_all:
        summary["normalized_flag_removed"] = normalize_false_flags(
            dataset_dir=args.dataset, dry_run=args.dry_run
        )
    print(json.dumps({"schema": "mtagentlens.gt-adjudication-apply/1", **summary}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
