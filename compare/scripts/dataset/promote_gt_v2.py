#!/usr/bin/env python3
"""Promote reviewed GT v2 candidates into the frozen dataset ground_truth tree.

The promotion step is intentionally strict. Candidate files are generated from
observed DB evidence, but this script is the auditable gate that decides whether
they are clean enough to become the evaluator-facing ground truth contract.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROMOTED_SCHEMA = "mtagentprism.ground_truth.v2"
PROMOTED_STATUS = "auto_adjudicated_with_db_evidence"
NOISE_EXPECTED_CLAIM_MARKERS = (
    "/.kiro/",
    "/handoffs/",
    "/.venv",
    "/venv/",
    "/site-packages/",
    "/node_modules/",
    "/collector/build/",
    "/build/release/",
    "/env/runs/",
    "package-lock.json",
    "cargo.lock",
)
COMMAND_RESOURCE_RE = re.compile(
    r"^boundary\.resource\./(?:usr/)?bin/(?:cat|ls|grep|find|curl|bash|sh|python3?|head|tail|dd)\b"
)


class PromotionError(RuntimeError):
    pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=Path("compare/Dataset/FuxiBench"))
    parser.add_argument("--no-backup", action="store_true", help="Do not copy legacy GT files before writing.")
    args = parser.parse_args()

    try:
        report = promote_dataset(args.dataset.resolve(), backup=not args.no_backup)
    except PromotionError as exc:
        print(f"Promotion failed: {exc}")
        return 2

    print(f"Promoted {report['summary']['promoted']} GT files")
    print(f"Wrote {args.dataset / 'quality' / 'gt_v2_promotion_report.json'}")
    return 0


def promote_dataset(dataset: Path, *, backup: bool = False) -> dict[str, Any]:
    manifest = read_json(dataset / "quality" / "manifest.json")
    samples = manifest.get("samples", [])
    if not isinstance(samples, list) or not samples:
        raise PromotionError("quality/manifest.json must contain non-empty samples")

    candidate_dir = dataset / "quality" / "ground_truth_v2_candidate"
    if not candidate_dir.is_dir():
        raise PromotionError(f"missing candidate directory: {candidate_dir}")

    sample_by_id = {}
    for sample in samples:
        if not isinstance(sample, dict) or not sample.get("scenario_id"):
            raise PromotionError("manifest samples must be objects with scenario_id")
        sid = str(sample["scenario_id"])
        if sid in sample_by_id:
            raise PromotionError(f"duplicate manifest scenario_id: {sid}")
        sample_by_id[sid] = sample

    candidates = [read_json(candidate_dir / f"{sid}.json") for sid in sorted(sample_by_id)]
    validate_candidate_set(dataset, sample_by_id, candidates)

    backup_dir = None
    if backup:
        backup_dir = backup_ground_truth(dataset)

    promoted_records = []
    for candidate in candidates:
        sid = candidate["scenario_id"]
        sample = sample_by_id[sid]
        output_rel = str(sample.get("paths", {}).get("ground_truth") or "")
        if not output_rel:
            raise PromotionError(f"{sid}: manifest path ground_truth missing")
        output_path = dataset_path(dataset, output_rel)
        promoted = build_promoted_gt(dataset, candidate, sample, candidate_dir / f"{sid}.json")
        write_json(output_path, promoted)
        promoted_records.append(
            {
                "scenario_id": sid,
                "category": promoted["category"],
                "label": promoted["label"],
                "ground_truth": rel(dataset, output_path),
                "expected_claims": len(promoted["expected_behavior"].get("expected_claims", [])),
                "source_candidate": rel(dataset, candidate_dir / f"{sid}.json"),
            }
        )

    report = {
        "schema": "mtagentprism.gt_v2_promotion_report.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": dataset.as_posix(),
        "summary": {
            "manifest_samples": len(sample_by_id),
            "candidates": len(candidates),
            "promoted": len(promoted_records),
            "backup_dir": rel(dataset, backup_dir) if backup_dir else None,
        },
        "promoted": promoted_records,
        "quality_gate": {
            "attack_candidates_require_expected_claims": True,
            "benign_candidates_require_should_alert_false": True,
            "responsibility_db_evidence_checked": True,
            "no_noisy_expected_claims": True,
        },
    }
    write_json(dataset / "quality" / "gt_v2_promotion_report.json", report)
    return report


def validate_candidate_set(
    dataset: Path,
    sample_by_id: dict[str, dict[str, Any]],
    candidates: list[dict[str, Any]],
) -> None:
    candidate_ids = [candidate.get("scenario_id") for candidate in candidates]
    if set(candidate_ids) != set(sample_by_id):
        missing = sorted(set(sample_by_id) - set(candidate_ids))
        extra = sorted(set(candidate_ids) - set(sample_by_id))
        raise PromotionError(f"candidate/manifest mismatch missing={missing} extra={extra}")

    for candidate in candidates:
        validate_candidate(dataset, sample_by_id[str(candidate["scenario_id"])], candidate)


def validate_candidate(dataset: Path, sample: dict[str, Any], candidate: dict[str, Any]) -> None:
    sid = str(sample["scenario_id"])
    if candidate.get("schema") != "mtagentprism.ground_truth.v2.candidate":
        raise PromotionError(f"{sid}: unsupported candidate schema")
    if candidate.get("annotation_status") != "machine_generated_needs_review":
        raise PromotionError(f"{sid}: unexpected candidate annotation_status")
    if candidate.get("scenario_id") != sid:
        raise PromotionError(f"{sid}: scenario_id mismatch")
    if candidate.get("category") != sample.get("category"):
        raise PromotionError(f"{sid}: category mismatch")

    paths = sample.get("paths", {})
    if not isinstance(paths, dict):
        raise PromotionError(f"{sid}: manifest paths missing")
    for key in ("ground_truth", "trace", "responsibility_db"):
        if not isinstance(paths.get(key), str) or not paths[key]:
            raise PromotionError(f"{sid}: manifest path {key} missing")

    provenance = candidate.get("provenance", {})
    if provenance.get("legacy_ground_truth") != paths.get("ground_truth"):
        raise PromotionError(f"{sid}: legacy GT provenance mismatch")
    if provenance.get("responsibility_db") != paths.get("responsibility_db"):
        raise PromotionError(f"{sid}: responsibility DB provenance mismatch")
    if provenance.get("trace") != paths.get("trace"):
        raise PromotionError(f"{sid}: trace provenance mismatch")

    label = candidate.get("label")
    attack_type = candidate.get("attack_type")
    if label not in {"attack", "benign"}:
        raise PromotionError(f"{sid}: label must be attack or benign")
    if label == "benign" and attack_type != "benign":
        raise PromotionError(f"{sid}: benign candidate must use attack_type=benign")
    if label == "attack" and attack_type not in {"A", "B", "C"}:
        raise PromotionError(f"{sid}: attack candidate must use attack_type A/B/C")

    behavior = candidate.get("expected_behavior")
    if not isinstance(behavior, dict):
        raise PromotionError(f"{sid}: expected_behavior missing")
    expected_claims = behavior.get("expected_claims", [])
    if not isinstance(expected_claims, list):
        raise PromotionError(f"{sid}: expected_claims must be a list")
    if label == "attack" and candidate.get("execution_status") in {"executed", "partial", "undetectable"}:
        if not expected_claims:
            raise PromotionError(f"{sid}: executed attack candidate lacks expected_claims")
        if behavior.get("should_alert") is not True:
            raise PromotionError(f"{sid}: executed attack should_alert must be true")
    if label == "benign":
        if expected_claims:
            raise PromotionError(f"{sid}: benign candidate must not have expected_claims")
        if behavior.get("should_alert") is not False:
            raise PromotionError(f"{sid}: benign should_alert must be false")

    noisy = [claim for claim in expected_claims if is_noisy_expected_claim(claim)]
    if noisy:
        raise PromotionError(f"{sid}: noisy expected_claim values: {noisy[:3]}")

    contract = candidate.get("evidence_contract")
    if not isinstance(contract, dict) or contract.get("db_required") is not True:
        raise PromotionError(f"{sid}: evidence_contract.db_required must be true")
    db_path = dataset_path(dataset, paths["responsibility_db"])
    validate_db_evidence(sid, label, db_path)


def validate_db_evidence(sid: str, label: str, db_path: Path) -> None:
    if not db_path.exists() or db_path.stat().st_size == 0:
        raise PromotionError(f"{sid}: missing responsibility DB")
    con = sqlite3.connect(db_path)
    try:
        tables = {
            row[0]
            for row in con.execute("select name from sqlite_master where type='table'")
            if not row[0].startswith("sqlite_")
        }
        required = {"responsibility_claims", "responsibility_alerts", "agent_identities", "correlation_evidence"}
        missing = sorted(required - tables)
        if missing:
            raise PromotionError(f"{sid}: responsibility DB missing tables {missing}")
        agent_identities = count_rows(con, "agent_identities")
        correlation_evidence = count_rows(con, "correlation_evidence")
        if agent_identities < 1:
            raise PromotionError(f"{sid}: responsibility DB has no agent identities")
        if correlation_evidence < 1:
            raise PromotionError(f"{sid}: responsibility DB has no correlation evidence")
        if label == "attack":
            claims = count_rows(con, "responsibility_claims")
            detecting = count_detecting_alerts(con)
            if claims < 1:
                raise PromotionError(f"{sid}: attack DB has no responsibility claims")
            if detecting < 1:
                raise PromotionError(f"{sid}: attack DB has no detecting alerts")
    finally:
        con.close()


def build_promoted_gt(
    dataset: Path,
    candidate: dict[str, Any],
    sample: dict[str, Any],
    candidate_path: Path,
) -> dict[str, Any]:
    promoted = {
        key: value
        for key, value in candidate.items()
        if key not in {"case_study_summary", "observed_behavior"}
    }
    promoted["schema"] = PROMOTED_SCHEMA
    promoted["annotation_status"] = PROMOTED_STATUS
    promoted["promoted_at"] = datetime.now(timezone.utc).isoformat()
    promoted["observed_behavior_summary"] = compact_observed_behavior(candidate.get("observed_behavior", {}))
    promoted["provenance"] = dict(candidate.get("provenance", {}))
    promoted["provenance"]["candidate_ground_truth"] = rel(dataset, candidate_path)
    promoted["quality"] = {
        "paper_ready": True,
        "source_candidate_review_findings": candidate.get("quality", {}).get("review_findings", []),
        "promotion_checks": [
            "manifest_paths_match",
            "db_evidence_present",
            "expected_claims_clean",
            "label_contract_valid",
        ],
        "source_quality_tier": sample.get("quality_tier"),
    }
    promoted["annotation"] = dict(candidate.get("annotation", {}))
    promoted["annotation"]["adjudication"] = "accepted_by_scripted_evidence_gate"
    promoted["annotation"]["promotion"] = {
        "method": "auto_adjudicated_with_db_evidence",
        "reviewer": "codex",
        "notes": (
            "Promoted by compare/scripts/promote_gt_v2.py after strict DB, "
            "manifest, label, and expected-claim noise checks."
        ),
    }
    return promoted


def compact_observed_behavior(observed: Any) -> dict[str, Any]:
    if not isinstance(observed, dict):
        return {}
    table_counts = observed.get("table_counts", {})
    return {
        "claim_count": observed.get("claim_count"),
        "detecting_alert_count": observed.get("detecting_alert_count"),
        "safe_alert_count": observed.get("safe_alert_count"),
        "agent_identity_count": observed.get("agent_identity_count"),
        "subagent_boundary_count": observed.get("subagent_boundary_count"),
        "correlation_evidence": table_counts.get("correlation_evidence") if isinstance(table_counts, dict) else None,
    }


def is_noisy_expected_claim(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return True
    lower = value.lower()
    if any(marker in lower for marker in NOISE_EXPECTED_CLAIM_MARKERS):
        return True
    if COMMAND_RESOURCE_RE.search(value):
        return True
    return False


def backup_ground_truth(dataset: Path) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = dataset / "quality" / f"ground_truth_legacy_backup_{timestamp}"
    backup_dir.mkdir(parents=True, exist_ok=False)
    for path in sorted((dataset / "ground_truth").rglob("*.json")):
        if "_external_archive" in path.parts:
            continue
        target = backup_dir / path.relative_to(dataset / "ground_truth")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    return backup_dir


def count_rows(con: sqlite3.Connection, table: str) -> int:
    return int(con.execute(f"select count(*) from {table}").fetchone()[0])


def count_detecting_alerts(con: sqlite3.Connection) -> int:
    columns = {row[1] for row in con.execute("pragma table_info(responsibility_alerts)")}
    if "decision" not in columns:
        return 0
    return int(
        con.execute(
            "select count(*) from responsibility_alerts where decision in ('Unsafe','Uncertain')"
        ).fetchone()[0]
    )


def dataset_path(dataset: Path, relpath: str) -> Path:
    path = (dataset / relpath).resolve()
    try:
        path.relative_to(dataset.resolve())
    except ValueError as exc:
        raise PromotionError(f"path escapes dataset root: {relpath}") from exc
    return path


def rel(dataset: Path, path: Path | None) -> str | None:
    if path is None:
        return None
    return path.resolve().relative_to(dataset.resolve()).as_posix()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
