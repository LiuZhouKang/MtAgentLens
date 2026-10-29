#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Merge AI-consensus GT v5 into the legacy v4-compatible detection contract.

Contract
--------
``mtagentlens.ground_truth.v5`` is authoritative for the scenario design layer:
``label``, ``attack_type``, the four evidence fields, design roles, causal
edges, blind references, votes and resolution. The merged per-scenario file
keeps ``schema = mtagentlens.ground_truth.v4`` so ``compare/tools/datasets.py``,
``scripts/evaluation/validate_gt.py`` and the dataset release builder keep
working, and carries that v5 layer as a top-level ``label_annotation`` block.

The legacy v4 detection contract (``task_summary``, ``payload``,
``benign_activity``, ``expected_behaviors``, ``min_decision``, ``should_alert``,
``execution_check`` and the historical ``annotation`` block) is copied
verbatim. No v4/v5 label or attack_type difference is applied silently: every
difference needs an adjudicated decision from ``flip-decisions.json`` (task-15
fourth/fifth fresh session):

* ``decision=v5`` for ``attack_type``: apply the v5 type; a bare
  ``payload.payload_channel_classification`` that duplicated the old type is
  updated and one disclosure line is appended to ``quality.notes``.
* ``decision=v5`` for ``label``: do NOT rewrite the detection contract. Keep the
  v4 label/attack_type and all expected behaviors as historical benchmark
  provenance, set ``quality.detection_excluded=true`` with
  ``quality.exclusion_reason``, and record ``label_annotation.v5_label`` /
  ``v5_disagreement``. Detection metrics must filter these scenarios out.
* ``decision=v4``: keep the v4 value and record the disagreement; no exclusion.
* ``decision=unknown``: keep the v4 detection contract but exclude the scenario.
* legacy ``quality.needs_human_review`` is cleared (its value is preserved in
  ``quality.v5_merge.legacy_needs_human_review``); no human review is claimed.

Every merged record records the v5 artifact path/sha256 and the canonical v5
record sha256, so the label source is tracked rather than implicit.

Usage
-----
    env/.venv/bin/python scripts/evaluation/gt_merge.py merge \
        --v5 local/workpackages/2026-10-26-final-dataset/gt/runs/full-93/gt-v5.json \
        --v4-root datasets/FuxiBench/ground_truth \
        --manifest datasets/FuxiBench/prompt_inputs/manifest.json \
        --flip-decisions local/workpackages/2026-10-26-final-dataset/gt/runs/full-93/flip-decisions.json \
        --out local/workpackages/2026-10-26-final-dataset/integration/full-93
    env/.venv/bin/python scripts/evaluation/gt_merge.py verify \
        --v5 <same> --v4-root <same> --staging <out> --manifest <same> \
        --flip-decisions <same>

Staging output is deterministic for identical inputs and is written outside the
live ground-truth tree; an existing directory is only replaced when it carries
this tool's staging marker and ``--overwrite`` is given.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_V4 = "mtagentlens.ground_truth.v4"
SCHEMA_V5 = "mtagentlens.ground_truth.v5"
METHOD = "ai_consensus_3"
QUALITY_TIER = "ai_consensus"
MERGE_VERSION = "v5-merge-1"
FLIP_SCHEMA = "mtagentlens.flip-decisions/1"
REPORT_SCHEMA = "mtagentlens.gt-merge-report/1"
MARKER_SCHEMA = "mtagentlens.gt-merge-staging/1"
MARKER = ".gt-merge-staging.json"
DECISIONS = {"v4", "v5", "unknown"}
DECISION_FIELDS = ("label", "attack_type")
FIELDS = ("authorization_boundary", "entry_point", "root_source", "instruction_introducer")
STATES = {"known", "unknown", "not_applicable"}
TYPES = {"A", "B", "C", "benign"}
PRESERVED_FIELDS = (
    "task_summary", "benign_activity", "expected_behaviors", "min_decision",
    "should_alert", "execution_check", "annotation", "category",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_json(value: Any) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def read_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def repo_rel(path: Path) -> str:
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(ROOT).as_posix()
    except ValueError:
        return resolved.as_posix()


def load_v5(path: Path) -> dict:
    """Load and validate the AI-consensus artifact boundary; never human gold."""
    artifact = read_json(path)
    if not isinstance(artifact, dict):
        raise ValueError("v5 artifact must be a JSON object")
    if artifact.get("schema") != SCHEMA_V5:
        raise ValueError(f"unsupported v5 artifact schema: {artifact.get('schema')!r}")
    if (artifact.get("method") != METHOD or artifact.get("quality_tier") != QUALITY_TIER
            or artifact.get("scope") != "scenario_design"
            or artifact.get("eligible_for_runtime_metrics") is not False):
        raise ValueError("v5 artifact is not AI-consensus scenario-design data")
    if artifact.get("human_review") is not False or artifact.get("human_annotations") != 0:
        raise ValueError("v5 artifact claims human review or human annotations")
    if artifact.get("annotator_count") != 3:
        raise ValueError("v5 artifact must record exactly three annotators")
    records = artifact.get("records")
    if not isinstance(records, list) or not records:
        raise ValueError("v5 artifact has no records")
    seen = set()
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("v5 record must be an object")
        scenario_id = record.get("scenario_id")
        if not isinstance(scenario_id, str) or not scenario_id or scenario_id in seen:
            raise ValueError(f"invalid or duplicate v5 scenario_id: {scenario_id!r}")
        seen.add(scenario_id)
        validate_v5_record(record)
    return artifact


def validate_v5_record(record: dict) -> None:
    scenario_id = record["scenario_id"]
    label, attack_type = record.get("label"), record.get("attack_type")
    if label == "unknown":
        raise ValueError(f"{scenario_id}: v5 abstained (label=unknown); an abstention cannot "
                         "be written as a v4 label and is never a negative")
    if label not in {"attack", "benign"} or attack_type not in TYPES:
        raise ValueError(f"{scenario_id}: invalid v5 label/attack_type {label!r}/{attack_type!r}")
    if (label == "benign") != (attack_type == "benign"):
        raise ValueError(f"{scenario_id}: v5 label conflicts with attack_type")
    if record.get("method") != METHOD:
        raise ValueError(f"{scenario_id}: v5 record method is not {METHOD}")
    if record.get("human_review") is not False or record.get("human_annotations") != 0:
        raise ValueError(f"{scenario_id}: v5 record claims human review or human annotations")
    references = record.get("references")
    if not isinstance(references, list):
        raise ValueError(f"{scenario_id}: v5 record has no references list")
    reference_ids = {reference.get("id") for reference in references if isinstance(reference, dict)}
    if len(reference_ids) != len(references):
        raise ValueError(f"{scenario_id}: duplicate or invalid v5 reference ids")
    for field in FIELDS:
        item = record.get(field)
        if not isinstance(item, dict) or item.get("status") not in STATES:
            raise ValueError(f"{scenario_id}: v5 field {field} has an invalid status")
        if item.get("status") != "not_applicable" and not item.get("value"):
            raise ValueError(f"{scenario_id}: v5 field {field} has no value")
        if not set(item.get("evidence_refs") or []) <= reference_ids:
            raise ValueError(f"{scenario_id}: v5 field {field} cites undeclared evidence")
    for collection in ("execution_roles", "causal_edges"):
        for item in record.get(collection) or []:
            if not set(item.get("evidence_refs") or []) <= reference_ids:
                raise ValueError(f"{scenario_id}: v5 {collection} cites undeclared evidence")
    cited = set(record.get("label_evidence_refs") or []) | set(record.get("attack_type_evidence_refs") or [])
    if not cited <= reference_ids:
        raise ValueError(f"{scenario_id}: v5 label evidence cites undeclared references")


def v4_index(v4_root: Path) -> dict[str, Path]:
    """Map scenario ids to their v4 files, skipping ``_reviewers``/``_causal`` trees."""
    root = Path(v4_root)
    if not root.is_dir():
        raise ValueError(f"missing v4 ground-truth root: {root}")
    index: dict[str, Path] = {}
    for path in sorted(root.rglob("*.json")):
        if any(part.startswith("_") for part in path.relative_to(root).parts):
            continue
        data = read_json(path)
        if not isinstance(data, dict) or data.get("schema") != SCHEMA_V4:
            raise ValueError(f"unexpected v4 ground truth schema in {path}")
        scenario_id = data.get("scenario_id")
        if not isinstance(scenario_id, str) or not scenario_id:
            raise ValueError(f"v4 ground truth without scenario_id: {path}")
        if scenario_id in index:
            raise ValueError(f"duplicate v4 ground-truth identity: {scenario_id}")
        index[scenario_id] = path
    return index


def manifest_ids(path: Path) -> set[str]:
    manifest = read_json(path)
    entries = manifest.get("entries") if isinstance(manifest, dict) else None
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"prompt-input manifest has no entries: {path}")
    ids = [entry.get("scenario_id") for entry in entries if isinstance(entry, dict)]
    if len(ids) != len(entries) or len(set(ids)) != len(ids):
        raise ValueError(f"prompt-input manifest has invalid scenario ids: {path}")
    return set(ids)


def _decision_session(item: dict, entry: dict) -> str | None:
    adjudication = entry.get("adjudication") if isinstance(entry.get("adjudication"), dict) else {}
    candidates = [item.get("session_id"), item.get("adjudication_session_id"),
                  item.get("adjudicator_session"), item.get("session"),
                  adjudication.get("session_id"), entry.get("adjudication_session_id"),
                  entry.get("session_id"), entry.get("adjudicator_session_id")]
    for value in (adjudication.get("sessions"), entry.get("adjudication_sessions")):
        if isinstance(value, list) and value and isinstance(value[0], str):
            candidates.append(value[0])
    for value in candidates:
        if isinstance(value, str) and value:
            return value
    return None


def load_flip_decisions(path: Path) -> tuple[dict[str, dict], dict]:
    """Normalize the adjudicated v4/v5 flip decisions and their provenance.

    Accepted shapes: ``{"scenarios": [...]}`` / ``{"decisions": [...]}`` with
    per-scenario ``fields`` (dict or list of ``{field, decision, ...}``), or flat
    ``label_decision``/``attack_type_decision`` keys. Every decision needs a
    ``v4|v5|unknown`` value, a rationale and an adjudication session id.
    """
    payload = read_json(path)
    if not isinstance(payload, dict):
        raise ValueError("flip decisions must be a JSON object")
    if payload.get("schema") not in (None, FLIP_SCHEMA):
        raise ValueError(f"unsupported flip-decisions schema: {payload.get('schema')!r}")
    if payload.get("human_review") is True or (payload.get("human_annotations") or 0) != 0:
        raise ValueError("flip decisions claim human review or human annotations")
    entries = payload.get("scenarios") or payload.get("decisions") or payload.get("flips")
    if isinstance(entries, dict):
        entries = [dict(value, scenario_id=value.get("scenario_id") or key)
                   for key, value in entries.items() if isinstance(value, dict)]
    if not isinstance(entries, list) or not entries:
        raise ValueError("flip decisions contain no scenario decisions")
    if all(isinstance(entry, dict) and entry.get("field") in DECISION_FIELDS
           and entry.get("decision") is not None for entry in entries):
        grouped: dict[str, dict] = {}
        for entry in entries:
            scenario_id = entry.get("scenario_id") or entry.get("id")
            grouped.setdefault(scenario_id, {"scenario_id": scenario_id, "fields": {}})
            grouped[scenario_id]["fields"][entry["field"]] = entry
        entries = list(grouped.values())
    normalized: dict[str, dict] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("flip decision entry must be an object")
        scenario_id = entry.get("scenario_id") or entry.get("id")
        if not isinstance(scenario_id, str) or not scenario_id or scenario_id in normalized:
            raise ValueError(f"invalid or duplicate flip-decisions scenario id: {scenario_id!r}")
        raw = entry.get("fields") or entry.get("field_decisions") or entry.get("decisions")
        fields: dict[str, dict] = {}
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict) and item.get("field") in DECISION_FIELDS:
                    fields[item["field"]] = item
        elif isinstance(raw, dict):
            for field in DECISION_FIELDS:
                if isinstance(raw.get(field), dict):
                    fields[field] = raw[field]
        for field in DECISION_FIELDS:
            nested = entry.get(field)
            if field not in fields and isinstance(nested, dict) and nested.get("decision") is not None:
                fields[field] = nested
            if field not in fields and entry.get(f"{field}_decision") is not None:
                fields[field] = {
                    "decision": entry[f"{field}_decision"],
                    "rationale": entry.get(f"{field}_rationale") or entry.get("rationale"),
                    "evidence_refs": entry.get(f"{field}_evidence_refs"),
                }
        clean: dict[str, dict] = {}
        for field, item in fields.items():
            decision = item.get("decision")
            if decision not in DECISIONS:
                raise ValueError(f"{scenario_id}: {field} flips decision must be one of "
                                 f"{sorted(DECISIONS)}, got {decision!r}")
            rationale = (item.get("rationale") or item.get("reason")
                         or entry.get("rationale") or entry.get("reason"))
            if not isinstance(rationale, str) or not rationale.strip():
                raise ValueError(f"{scenario_id}: {field} flip decision has no rationale")
            session = _decision_session(item, entry)
            if session is None:
                raise ValueError(f"{scenario_id}: {field} flip decision has no adjudication session")
            clean[field] = dict(item, decision=decision, rationale=rationale, session_id=session)
        normalized[scenario_id] = {
            "scenario_id": scenario_id,
            "fields": clean,
            "votes": entry.get("votes"),
            "adjudication": entry.get("adjudication"),
            "status": entry.get("status"),
            "source_sha256": sha256_json(entry),
        }
    provenance = {"path": repo_rel(path), "sha256": sha256_file(path),
                  "scenarios": len(normalized)}
    return normalized, provenance


def _expected_ids(records: dict[str, dict], expected_ids) -> tuple[set[str], str]:
    v5_ids = set(records)
    if expected_ids is None:
        return v5_ids, "v5"
    expected = set(expected_ids)
    if expected != v5_ids:
        raise ValueError(f"v5 coverage mismatch: missing v5 records for {sorted(expected - v5_ids)}; "
                         f"unexpected v5 records {sorted(v5_ids - expected)}")
    return expected, "argument"


def _diff(before: Any, after: Any, path: str = "") -> list[dict]:
    changes: list[dict] = []
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(set(before) | set(after)):
            child = f"{path}/{key}"
            if key not in before:
                changes.append({"path": child, "operation": "added", "before": None, "after": after[key]})
            elif key not in after:
                changes.append({"path": child, "operation": "removed", "before": before[key], "after": None})
            else:
                changes.extend(_diff(before[key], after[key], child))
    elif isinstance(before, list) and isinstance(after, list):
        if before != after:
            if after[:len(before)] == before:
                changes.append({"path": path, "operation": "append", "added": after[len(before):]})
            elif before[:len(after)] == after:
                changes.append({"path": path, "operation": "truncate", "removed": before[len(after):]})
            else:
                changes.append({"path": path, "operation": "replace", "before": before, "after": after})
    elif before != after:
        changes.append({"path": path, "operation": "replace", "before": before, "after": after})
    return changes


def _label_annotation(record: dict, *, artifact: dict, v5_path: Path, v5_sha: str,
                      v4_rel: str, v4_sha: str, updates: list[dict],
                      decisions: dict[str, dict] | None = None,
                      flip: dict | None = None,
                      flip_provenance: dict | None = None,
                      excluded: bool = False) -> dict:
    annotation = {
        "method": METHOD,
        "annotation_version": MERGE_VERSION,
        "quality_tier": QUALITY_TIER,
        "scope": "scenario_design",
        "eligible_for_runtime_metrics": False,
        "human_review": False,
        "human_annotations": 0,
        "annotator_count": 3,
        "protocol_version": artifact.get("protocol_version"),
        "input_view": artifact.get("input_view"),
        "reviewer_independence": artifact.get("reviewer_independence"),
        "source": {
            "path": repo_rel(v5_path),
            "sha256": v5_sha,
            "record_sha256": sha256_json(record),
        },
        "resolution": record.get("resolution"),
        "votes": record.get("votes"),
        "evidence": {
            "field_status": {field: record[field]["status"] for field in FIELDS},
            "fields": {field: {key: record[field].get(key)
                               for key in ("status", "value", "evidence_refs")} for field in FIELDS},
            "label_rationale": record.get("label_rationale"),
            "attack_type_rationale": record.get("attack_type_rationale"),
            "execution_roles": record.get("execution_roles"),
            "causal_edges": record.get("causal_edges"),
            "references": record.get("references"),
            "label_evidence_refs": record.get("label_evidence_refs"),
            "attack_type_evidence_refs": record.get("attack_type_evidence_refs"),
            "limitations": record.get("limitations"),
            "causal_chain_status": record.get("causal_chain_status"),
        },
        "legacy_disclosure": record.get("legacy_disclosure"),
        "v4_detection_contract": {
            "path": v4_rel,
            "sha256": v4_sha,
            "preserved_fields": list(PRESERVED_FIELDS),
            "attack_type_dependent_updates": updates,
        },
    }
    if decisions:
        annotation["v5_label"] = record["label"]
        annotation["v5_attack_type"] = record["attack_type"]
        annotation["v5_disagreement"] = {
            "fields": {
                field: {key: item.get(key) for key in
                        ("v4_value", "v5_value", "decision", "rationale", "session_id", "evidence_refs")}
                for field, item in decisions.items()
            },
            "votes": (flip or {}).get("votes"),
            "adjudication": (flip or {}).get("adjudication"),
            "status": (flip or {}).get("status"),
            "source": {
                "path": (flip_provenance or {}).get("path"),
                "sha256": (flip_provenance or {}).get("sha256"),
                "scenario_sha256": (flip or {}).get("source_sha256"),
            },
            "note": "Adjudicated v4/v5 disagreement, applied per the no-silent-flip merge "
                    "policy. No human annotation or human review is claimed.",
        }
        if excluded:
            annotation["legacy_disclosure_note"] = (
                "v4 label/attack_type and detection contract retained as historical benchmark "
                "provenance; the independent audit disagreement is recorded above and this "
                "scenario is excluded from detection metrics. No human review is claimed.")
    return annotation


def merge_record(v4_doc: dict, record: dict, *, artifact: dict, v5_path: Path, v5_sha: str,
                 v4_rel: str, v4_sha: str, flip: dict | None = None,
                 flip_provenance: dict | None = None) -> tuple[dict, list[dict], list[dict]]:
    """Return the merged v4-compatible document, its precise diff and conflict notes.

    v5 may disagree with v4 on label/attack_type. No disagreement is applied
    silently: every differing field needs an adjudicated decision. A ``v5``
    label decision never rewrites the detection contract (the v4 label and
    detection fields stay as benchmark provenance and the scenario is marked
    ``quality.detection_excluded``). An ``unknown`` decision also excludes the
    scenario. A ``v5`` attack_type decision is applied with disclosure; ``v4``
    decisions keep the v4 value and only record the disagreement.
    """
    scenario_id = v4_doc.get("scenario_id")
    if scenario_id != record.get("scenario_id"):
        raise ValueError(f"scenario identity mismatch: {scenario_id!r} vs {record.get('scenario_id')!r}")
    old_label, old_type = v4_doc.get("label"), v4_doc.get("attack_type")
    new_label, new_type = record["label"], record["attack_type"]
    differences = [field for field, old, new in
                   (("label", old_label, new_label), ("attack_type", old_type, new_type))
                   if old != new]
    decisions: dict[str, dict] = {}
    if differences:
        if flip is None:
            raise ValueError(f"{scenario_id}: v5 differs from v4 on {differences}; an adjudicated "
                             "flip decision is required (pass --flip-decisions)")
        for field in differences:
            item = (flip.get("fields") or {}).get(field)
            if not isinstance(item, dict) or item.get("decision") not in DECISIONS:
                raise ValueError(f"{scenario_id}: missing or invalid {field} flip decision for "
                                 f"v4 {old_label if field == 'label' else old_type!r} vs v5 "
                                 f"{new_label if field == 'label' else new_type!r}")
            decisions[field] = dict(
                item,
                v4_value=old_label if field == "label" else old_type,
                v5_value=new_label if field == "label" else new_type)
    label_decision = (decisions.get("label") or {}).get("decision")
    type_decision = (decisions.get("attack_type") or {}).get("decision")
    excluded = label_decision == "v5" or any(
        item["decision"] == "unknown" for item in decisions.values())
    merged = deepcopy(v4_doc)
    conflicts: list[dict] = []
    updates: list[dict] = []
    quality = merged.setdefault("quality", {})
    notes = quality.get("notes")
    if not isinstance(notes, list):
        notes = []
        quality["notes"] = notes
    applied_type = old_type
    if type_decision == "v5" and label_decision != "v5":
        applied_type = new_type
        merged["attack_type"] = new_type
        payload = merged.get("payload")
        channel = payload.get("payload_channel_classification") if isinstance(payload, dict) else None
        if isinstance(channel, str) and channel in {"A", "B", "C"} and channel == old_type:
            payload["payload_channel_classification"] = new_type
            updates.append({"path": "/payload/payload_channel_classification",
                            "before": channel, "after": new_type})
        elif channel is not None:
            conflicts.append({"scenario_id": scenario_id, "issue": "attack_type_dependent_text",
                              "detail": "payload.payload_channel_classification is not a bare type "
                                        f"code equal to {old_type!r}; left unchanged as "
                                        f"{channel!r}"})
        notes.append(
            f"{MERGE_VERSION}: attack_type {old_type}->{new_type} applied after an adjudicated "
            "v4/v5 flip decision (decision=v5); v4 detection-contract fields are preserved "
            "unchanged. No human review was performed or is claimed.")
    legacy_pending = quality.get("needs_human_review")
    quality["needs_human_review"] = False
    quality["v5_merge"] = {
        "method": METHOD,
        "annotation_version": MERGE_VERSION,
        "human_review": False,
        "human_annotations": 0,
        "legacy_needs_human_review": legacy_pending if isinstance(legacy_pending, bool) else None,
        "note": "Legacy human-review request superseded by the AI consensus-3 protocol; "
                "no human annotation or human review is claimed.",
    }
    if decisions:
        summary = "; ".join(f"{field}: v4 {item['v4_value']} vs v5 {item['v5_value']} -> "
                            f"{item['decision']}" for field, item in decisions.items())
        notes.append(
            f"{MERGE_VERSION}: adjudicated v4/v5 disagreement ({summary}); decision source "
            f"{(flip_provenance or {}).get('path')} sha256 {(flip_provenance or {}).get('sha256')}; "
            "no human review claimed.")
        if excluded:
            quality["detection_excluded"] = True
            quality["exclusion_reason"] = (
                f"independent 3-annotator + adjudication audit found {new_label}/{new_type}; "
                "v4 design/detection contract retained as historical benchmark provenance only; "
                "exclude this scenario from detection denominators")
        if label_decision == "v4":
            notes.append(f"{MERGE_VERSION}: label/attack_type kept from v4 after adjudication "
                         "(decision=v4); the v5 audit result is recorded in "
                         "label_annotation.v5_disagreement.")
    merged["label"] = old_label
    merged["attack_type"] = applied_type
    merged["label_annotation"] = _label_annotation(
        record, artifact=artifact, v5_path=v5_path, v5_sha=v5_sha,
        v4_rel=v4_rel, v4_sha=v4_sha, updates=updates,
        decisions=decisions or None, flip=flip, flip_provenance=flip_provenance,
        excluded=excluded)
    return merged, _diff(v4_doc, merged), conflicts


def _decisions_for(merged: dict) -> dict[str, dict]:
    annotation = merged.get("label_annotation") or {}
    fields = (annotation.get("v5_disagreement") or {}).get("fields") or {}
    return {field: item for field, item in fields.items() if isinstance(item, dict)}


def _row(scenario_id: str, v4_rel: str, v4_doc: dict, merged: dict, record: dict,
         changes: list[dict], decisions: dict[str, dict], excluded: bool) -> dict:
    return {
        "scenario_id": scenario_id,
        "v4_path": v4_rel,
        "v5_case_id": record.get("anon_case_id"),
        "label_before": v4_doc.get("label"),
        "label_after": merged.get("label"),
        "v5_label": record["label"],
        "label_changed": v4_doc.get("label") != merged.get("label"),
        "label_flip": v4_doc.get("label") != record["label"],
        "attack_type_before": v4_doc.get("attack_type"),
        "attack_type_after": merged.get("attack_type"),
        "v5_attack_type": record["attack_type"],
        "attack_type_changed": v4_doc.get("attack_type") != merged.get("attack_type"),
        "attack_type_flip": v4_doc.get("attack_type") != record["attack_type"],
        "flip_decisions": {field: item["decision"] for field, item in decisions.items()},
        "detection_excluded": excluded,
        "category_v4": v4_doc.get("category"),
        "category_v5": record.get("category"),
        "category_mismatch": bool(record.get("category")
                                  and v4_doc.get("category") != record.get("category")),
        "resolution": record.get("resolution"),
        "quality_status": (record.get("quality") or {}).get("status"),
        "field_status": {field: record[field]["status"] for field in FIELDS},
        "legacy_needs_human_review": (v4_doc.get("quality") or {}).get("needs_human_review"),
        "changes": changes,
    }


def render_markdown(report: dict) -> str:
    summary = report["summary"]
    inputs = report["inputs"]
    lines = [
        "# GT v5 merge report",
        "",
        f"- tool version: `{report['tool_version']}`",
        f"- v5 artifact: `{inputs['v5']['path']}` sha256 `{inputs['v5']['sha256']}`",
        f"- v4 root: `{inputs['v4_root']}`",
    ]
    if inputs.get("flip_decisions"):
        lines.append(f"- flip decisions: `{inputs['flip_decisions']['path']}` sha256 "
                     f"`{inputs['flip_decisions']['sha256']}`")
    lines += [
        f"- expected ids: {summary['scenarios']} merged (`{inputs['expected_ids_source']}`), "
        f"{summary['v4_not_merged']} v4 scenarios not merged",
        "",
        "Policy: v5 is authoritative for the design evidence layer; v4 keeps the detection "
        "contract. No v4/v5 label or attack_type difference is applied silently: each needs an "
        "adjudicated decision. A confirmed v5 label flip retains the v4 label/attack_type and "
        "marks the scenario `quality.detection_excluded` for detection denominators; a confirmed "
        "v5 attack_type flip is applied with disclosure; a `v4` decision keeps v4; `unknown` "
        "excludes the scenario. No human review is claimed.",
        "",
        "## Summary",
        "",
        "| metric | value |",
        "|---|---|",
    ]
    for key in ("label_changes", "attack_type_changes", "label_flips", "attack_type_flips",
                "excluded_scenarios", "category_mismatches", "changes_total"):
        lines.append(f"| {key} | {summary[key]} |")
    lines += ["", "| decision | count |", "|---|---|"]
    for key, value in summary["flip_decisions"].items():
        lines.append(f"| {key} | {value} |")
    lines += ["", "## Scenario diff", "",
              "| scenario | v4 label/type | v5 label/type | merged label/type | decision | excluded | changed paths |",
              "|---|---|---|---|---|---|---|"]
    for row in report["scenarios"]:
        paths = ", ".join(change["path"] for change in row["changes"])
        decision = ", ".join(f"{field}={value}" for field, value in row["flip_decisions"].items()) or "-"
        lines.append(f"| {row['scenario_id']} | {row['label_before']}/{row['attack_type_before']} "
                     f"| {row['v5_label']}/{row['v5_attack_type']} "
                     f"| {row['label_after']}/{row['attack_type_after']} | {decision} "
                     f"| {row['detection_excluded']} | {paths} |")
    excluded = [row for row in report["scenarios"] if row["detection_excluded"]]
    lines += ["", "## Detection-excluded scenarios", ""]
    lines += [f"- `{row['scenario_id']}`: v4 {row['label_before']}/{row['attack_type_before']} "
              f"retained; v5 audit {row['v5_label']}/{row['v5_attack_type']}; decision "
              f"{row['flip_decisions']}" for row in excluded] or ["- none"]
    conflicts = report["conflicts"]
    lines += ["", "## Conflicts / unresolved", ""]
    lines += [f"- `{item['scenario_id']}` {item['issue']}: {item['detail']}" for item in conflicts] or ["- none"]
    lines += ["", "## Next steps (W6 freeze, staging only)", "",
              "- Filter `quality.detection_excluded is True` scenarios out of detection "
              "denominators in evaluation consumers (see W6-FREEZE-COMMANDS.md).",
              "- Review this report, then archive the live `ground_truth/` to `_legacy_v4/` "
              "and copy the staged `gt/` tree over it; never delete historical evidence.",
              "- Rebuild `ground_truth/_causal/` from the merged GT and re-run the dataset "
              "release builder so the frozen release binds the merged files.",
              "- Update metadata/quality/card/protocol docs and run `scripts/verify.sh`.",
              "- Full ordered commands, gates and risks: "
              "`local/workpackages/2026-10-26-final-dataset/integration/W6-FREEZE-COMMANDS.md`.",
              ""]
    return "\n".join(lines)


def _check_staging_path(out_dir: Path, v4_root: Path) -> None:
    resolved = out_dir.resolve()
    live = v4_root.resolve()
    if resolved == live or resolved.is_relative_to(live):
        raise ValueError("staging destination must be outside the live v4 ground-truth tree")
    if live.is_relative_to(resolved) and resolved != live:
        raise ValueError("staging destination must not contain the live v4 ground-truth tree")


def merge_to_staging(v5_path: Path, v4_root: Path, out_dir: Path, *,
                     expected_ids=None, overwrite: bool = False,
                     flip_decisions: Path | None = None) -> dict:
    """Merge v5 records into a deterministic staging tree and write MERGE-REPORT.*."""
    v5_path, v4_root, out_dir = Path(v5_path), Path(v4_root), Path(out_dir)
    artifact = load_v5(v5_path)
    v5_sha = sha256_file(v5_path)
    records = {record["scenario_id"]: record for record in artifact["records"]}
    expected, expected_source = _expected_ids(records, expected_ids)
    flip_entries: dict[str, dict] = {}
    flip_provenance: dict | None = None
    if flip_decisions is not None:
        flip_entries, flip_provenance = load_flip_decisions(flip_decisions)
    index = v4_index(v4_root)
    missing = sorted(expected - set(index))
    if missing:
        raise ValueError(f"missing v4 ground truth for scenarios: {missing}")
    _check_staging_path(out_dir, v4_root)
    if out_dir.exists():
        previous = read_json(out_dir / MARKER) if (out_dir / MARKER).is_file() else None
        if not overwrite:
            raise ValueError("staging destination already exists; pass --overwrite")
        if not isinstance(previous, dict) or previous.get("schema") != MARKER_SCHEMA:
            raise ValueError("refusing to overwrite a directory that is not a gt-merge staging output")
        shutil.rmtree(out_dir)

    rows, conflicts, merged_by_id, marker_files = [], [], {}, {}
    for scenario_id in sorted(expected):
        v4_path = index[scenario_id]
        v4_doc = read_json(v4_path)
        v4_rel = v4_path.relative_to(v4_root).as_posix()
        v4_sha = sha256_file(v4_path)
        flip = flip_entries.get(scenario_id)
        merged, changes, record_conflicts = merge_record(
            v4_doc, records[scenario_id], artifact=artifact, v5_path=v5_path, v5_sha=v5_sha,
            v4_rel=v4_rel, v4_sha=v4_sha, flip=flip, flip_provenance=flip_provenance)
        decisions = {field: item for field, item in _decisions_for(merged).items()}
        merged_by_id[scenario_id] = merged
        rows.append(_row(scenario_id, v4_rel, v4_doc, merged, records[scenario_id], changes,
                         decisions, (merged.get("quality") or {}).get("detection_excluded") is True))
        conflicts.extend(record_conflicts)
        v5_category = records[scenario_id].get("category")
        if v5_category and v4_doc.get("category") != v5_category:
            conflicts.append({
                "scenario_id": scenario_id, "issue": "category_mismatch",
                "detail": f"v4 category {v4_doc.get('category')!r} != v5 category "
                          f"{v5_category!r}; v4 category and path are kept, review before "
                          "publication",
            })
    report = {
        "schema": REPORT_SCHEMA,
        "tool_version": MERGE_VERSION,
        "inputs": {
            "v5": {"path": repo_rel(v5_path), "sha256": v5_sha},
            "v4_root": repo_rel(v4_root),
            "expected_ids": sorted(expected),
            "expected_ids_source": expected_source,
            "flip_decisions": flip_provenance,
        },
        "summary": {
            "scenarios": len(rows),
            "label_changes": sum(row["label_changed"] for row in rows),
            "attack_type_changes": sum(row["attack_type_changed"] for row in rows),
            "label_flips": sum(row["label_flip"] for row in rows),
            "attack_type_flips": sum(row["attack_type_flip"] for row in rows),
            "excluded_scenarios": sum(row["detection_excluded"] for row in rows),
            "flip_decisions": {
                decision: sum(1 for row in rows for value in row["flip_decisions"].values()
                              if value == decision)
                for decision in sorted(DECISIONS)},
            "category_mismatches": sum(row["category_mismatch"] for row in rows),
            "v4_scenarios_total": len(index),
            "v4_not_merged": len(set(index) - expected),
            "changes_total": sum(len(row["changes"]) for row in rows),
            "field_status_counts": {
                field: {status: sum(1 for row in rows if row["field_status"][field] == status)
                        for status in sorted(STATES)} for field in FIELDS},
            "resolution_counts": {
                resolution: sum(1 for row in rows if row["resolution"] == resolution)
                for resolution in sorted({row["resolution"] for row in rows if row["resolution"]})},
        },
        "conflicts": conflicts,
        "scenarios": rows,
        "staging": {"gt_files": len(rows), "report": "MERGE-REPORT.json"},
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    for scenario_id, merged in merged_by_id.items():
        rel = index[scenario_id].relative_to(v4_root).as_posix()
        write_json(out_dir / "gt" / rel, merged)
        marker_files[rel] = sha256_file(out_dir / "gt" / rel)
    write_json(out_dir / "MERGE-REPORT.json", report)
    (out_dir / "MERGE-REPORT.md").write_text(render_markdown(report), encoding="utf-8")
    write_json(out_dir / MARKER, {
        "schema": MARKER_SCHEMA,
        "tool_version": MERGE_VERSION,
        "v5_sha256": v5_sha,
        "flip_decisions": flip_provenance,
        "expected_ids": sorted(expected),
        "gt_files": marker_files,
    })
    return report


def _reference_text(reference: dict) -> tuple[Path, Any]:
    path = Path(reference.get("path", ""))
    if not path.is_absolute():
        path = ROOT / path
    path = path.resolve()
    if "json_pointer" in reference:
        value = read_json(path)
        for token in str(reference["json_pointer"]).split("/")[1:]:
            key = token.replace("~1", "/").replace("~0", "~")
            value = value[int(key)] if isinstance(value, list) else value[key]
        return path, value
    lines = path.read_text(errors="backslashreplace").splitlines()
    start, end = reference.get("line_start"), reference.get("line_end")
    if not isinstance(start, int) or not isinstance(end, int) or not (1 <= start <= end <= len(lines)):
        raise ValueError(f"invalid reference line range for {reference.get('id')!r}")
    return path, "\n".join(lines[start - 1:end])


def _evidence_problems(scenario_id: str, references: list) -> list[str]:
    problems: list[str] = []
    for reference in references:
        reference_id = reference.get("id") if isinstance(reference, dict) else None
        try:
            path, actual = _reference_text(reference)
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            problems.append(f"evidence unreadable for {scenario_id}:{reference_id}: {exc}")
            continue
        if sha256_file(path) != reference.get("sha256"):
            problems.append(f"evidence hash mismatch for {scenario_id}:{reference_id}: {reference.get('path')}")
        if actual != reference.get("quote"):
            problems.append(f"evidence quote mismatch for {scenario_id}:{reference_id}")
    return problems


def _iter_evidence_refs(annotation: dict):
    evidence = annotation.get("evidence") or {}
    for field in FIELDS:
        yield from ((evidence.get("fields") or {}).get(field) or {}).get("evidence_refs") or []
    for collection in ("execution_roles", "causal_edges"):
        for item in evidence.get(collection) or []:
            yield from item.get("evidence_refs") or []
    for key in ("label_evidence_refs", "attack_type_evidence_refs"):
        yield from evidence.get(key) or []


def _normalized_report_bytes(rel: str, raw: bytes, *, v5_path: Path, v4_root: Path,
                             flip_decisions: Path | None) -> bytes:
    """Ignore relocated input paths; hashes and content stay fully compared."""
    if rel == "MERGE-REPORT.json":
        report = json.loads(raw)
        inputs = report.get("inputs") or {}
        for key in ("v5", "flip_decisions"):
            if isinstance(inputs.get(key), dict):
                inputs[key].pop("path", None)
        inputs["v4_root"] = None
        return json.dumps(report, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False).encode("utf-8")
    if rel == "MERGE-REPORT.md":
        text = raw.decode("utf-8")
        for path in (v5_path, v4_root, flip_decisions):
            if path is not None:
                text = text.replace(repo_rel(path), "<relocated-input>")
        text = re.sub(
            r"(?:(?:local/workpackages|compare/Dataset|env|scripts)/|/(?:tmp|home|root|Users|var)/)[^\s`]+",
            "<relocated-input>", text)
        return text.encode("utf-8")
    return raw


def verify_staging(v5_path: Path, v4_root: Path, staging: Path, *, expected_ids=None,
                   flip_decisions: Path | None = None) -> dict:
    """Re-run the merge and reject tampering, coverage gaps, evidence drift and human claims."""
    v5_path, v4_root, staging = Path(v5_path), Path(v4_root), Path(staging)
    problems: list[str] = []
    marker_path = staging / MARKER
    if not marker_path.is_file():
        return {"valid": False, "scenarios": 0,
                "problems": [f"coverage: missing staging marker {marker_path}"]}
    artifact = load_v5(v5_path)
    records = {record["scenario_id"]: record for record in artifact["records"]}
    expected, _ = _expected_ids(records, expected_ids)
    flip_provenance = None
    if flip_decisions is not None:
        flip_provenance = load_flip_decisions(flip_decisions)[1]
    with tempfile.TemporaryDirectory() as temp:
        replayed = Path(temp) / "expected"
        try:
            merge_to_staging(v5_path, v4_root, replayed,
                             expected_ids=None if expected_ids is None else expected,
                             flip_decisions=flip_decisions)
        except ValueError as exc:
            return {"valid": False, "scenarios": 0, "problems": [f"merge replay failed: {exc}"]}
        expected_tree = {path.relative_to(replayed).as_posix(): path.read_bytes()
                         for path in sorted(replayed.rglob("*")) if path.is_file()}
    actual_tree = {path.relative_to(staging).as_posix(): path.read_bytes()
                   for path in sorted(staging.rglob("*")) if path.is_file()}
    for rel in sorted(set(expected_tree) | set(actual_tree)):
        if rel not in actual_tree:
            problems.append(f"coverage: missing staged file {rel}")
        elif rel not in expected_tree:
            problems.append(f"deterministic mismatch: unexpected staged file {rel}")
        else:
            actual_bytes = _normalized_report_bytes(rel, actual_tree[rel], v5_path=v5_path,
                                                     v4_root=v4_root,
                                                     flip_decisions=flip_decisions)
            expected_bytes = _normalized_report_bytes(rel, expected_tree[rel], v5_path=v5_path,
                                                      v4_root=v4_root,
                                                      flip_decisions=flip_decisions)
            if actual_bytes != expected_bytes:
                problems.append(f"deterministic mismatch: {rel} differs from the merge input replay")
    report = read_json(staging / "MERGE-REPORT.json") if (staging / "MERGE-REPORT.json").is_file() else {}
    if not report.get("scenarios"):
        problems.append("report: missing per-scenario diff data")
    v5_sha = sha256_file(v5_path)
    index = v4_index(v4_root)
    warnings: list[str] = []
    for scenario_id in sorted(expected):
        v4_path = index.get(scenario_id)
        if v4_path is None:
            problems.append(f"coverage: missing v4 ground truth for {scenario_id}")
            continue
        merged_path = staging / "gt" / v4_path.relative_to(Path(v4_root)).as_posix()
        if not merged_path.is_file():
            problems.append(f"coverage: missing merged file for {scenario_id}")
            continue
        merged = read_json(merged_path)
        record = records[scenario_id]
        if merged.get("schema") != SCHEMA_V4:
            problems.append(f"{scenario_id}: merged schema is not {SCHEMA_V4}")
        annotation = merged.get("label_annotation")
        if not isinstance(annotation, dict):
            problems.append(f"{scenario_id}: missing label_annotation provenance block")
            continue
        if merged.get("label") != read_json(v4_path).get("label"):
            problems.append(f"{scenario_id}: policy violation: merged label must keep the v4 label")
        if (annotation.get("method") != METHOD or annotation.get("annotation_version") != MERGE_VERSION):
            problems.append(f"{scenario_id}: label source mismatch in label_annotation method/version")
        source = annotation.get("source") or {}
        if source.get("sha256") != v5_sha or source.get("record_sha256") != sha256_json(record):
            problems.append(f"{scenario_id}: label source mismatch (untracked v5 artifact/record hash)")
        contract = annotation.get("v4_detection_contract") or {}
        if contract.get("sha256") != sha256_file(v4_path):
            problems.append(f"{scenario_id}: label source mismatch (v4 detection contract changed)")
        if annotation.get("human_review") is not False or annotation.get("human_annotations") != 0:
            problems.append(f"{scenario_id}: human review claim in label_annotation")
        quality = merged.get("quality") or {}
        if quality.get("needs_human_review") is not False:
            problems.append(f"{scenario_id}: legacy human-review request not disclosed/cleared")
        if not isinstance(quality.get("v5_merge"), dict):
            problems.append(f"{scenario_id}: missing quality.v5_merge disclosure")
        v4_doc = read_json(v4_path)
        differences = [field for field in DECISION_FIELDS
                       if v4_doc.get(field) != record.get(field)]
        decisions = _decisions_for(merged)
        actual_excluded = quality.get("detection_excluded") is True
        if differences:
            if set(decisions) != set(differences):
                problems.append(f"{scenario_id}: flip decision coverage mismatch for "
                                f"{sorted(differences)}")
            for field in differences:
                if (decisions.get(field) or {}).get("decision") not in DECISIONS:
                    problems.append(f"{scenario_id}: invalid {field} flip decision in staging")
            expected_excluded = (
                (decisions.get("label") or {}).get("decision") == "v5"
                or any(item.get("decision") == "unknown" for item in decisions.values()))
            if expected_excluded != actual_excluded:
                problems.append(f"{scenario_id}: exclusion flag inconsistent with flip decision")
            if actual_excluded:
                if not quality.get("exclusion_reason"):
                    problems.append(f"{scenario_id}: excluded scenario has no exclusion_reason")
                if merged.get("attack_type") != v4_doc.get("attack_type"):
                    problems.append(f"{scenario_id}: excluded scenario must retain v4 attack_type")
                if not annotation.get("v5_label") or not annotation.get("legacy_disclosure_note"):
                    problems.append(f"{scenario_id}: excluded scenario missing v5_label/legacy note")
            type_decision = (decisions.get("attack_type") or {}).get("decision")
            expected_type = (record["attack_type"]
                             if type_decision == "v5" and not actual_excluded
                             else v4_doc.get("attack_type"))
            if merged.get("attack_type") != expected_type:
                problems.append(f"{scenario_id}: flip policy violation for attack_type")
            if not isinstance(annotation.get("v5_disagreement"), dict):
                problems.append(f"{scenario_id}: missing label_annotation.v5_disagreement")
        else:
            if decisions:
                problems.append(f"{scenario_id}: staged flip decision without a v4/v5 difference")
            if actual_excluded:
                problems.append(f"{scenario_id}: detection_excluded without an adjudicated difference")
        evidence = annotation.get("evidence") or {}
        references = evidence.get("references") or []
        declared = {reference.get("id") for reference in references if isinstance(reference, dict)}
        cited = set(_iter_evidence_refs(annotation))
        if not cited <= declared:
            problems.append(f"{scenario_id}: evidence reference not declared: {sorted(cited - declared)}")
        problems.extend(_evidence_problems(scenario_id, references))
        if (merged.get("category") != (record.get("category") or merged.get("category"))):
            warnings.append(f"{scenario_id}: v5 category {record.get('category')!r} differs from "
                            f"v4 category {merged.get('category')!r}; v4 category/path retained")
    return {
        "valid": not problems,
        "scenarios": len(expected),
        "inputs": {"v5": repo_rel(v5_path), "v4_root": repo_rel(v4_root),
                   "staging": repo_rel(staging), "flip_decisions": flip_provenance},
        "problems": problems,
        "warnings": warnings,
    }


def _cli_expected_ids(args) -> set[str] | None:
    if args.manifest and args.expected_ids:
        raise ValueError("pass either --manifest or --expected-ids, not both")
    if args.manifest:
        return manifest_ids(args.manifest)
    if args.expected_ids:
        ids = {value.strip() for value in args.expected_ids.split(",") if value.strip()}
        if not ids:
            raise ValueError("--expected-ids is empty")
        return ids
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (("merge", "merge v5 into a staging v4-compatible tree"),
                            ("verify", "verify a staging tree against its inputs")):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--v5", type=Path, required=True, help="gt-v5.json artifact")
        command.add_argument("--v4-root", type=Path, required=True, help="live v4 ground-truth tree")
        command.add_argument("--manifest", type=Path, help="prompt-input manifest (expected ids)")
        command.add_argument("--expected-ids", help="comma-separated expected scenario ids")
        command.add_argument("--flip-decisions", type=Path,
                             help="adjudicated v4/v5 flip decisions (required for any difference)")
        if name == "merge":
            command.add_argument("--out", type=Path, required=True, help="new staging directory")
            command.add_argument("--overwrite", action="store_true",
                                 help="replace a previous gt-merge staging directory")
        else:
            command.add_argument("--staging", type=Path, required=True, help="staging directory")
    args = parser.parse_args(argv)
    try:
        expected = _cli_expected_ids(args)
        if args.command == "merge":
            report = merge_to_staging(args.v5, args.v4_root, args.out,
                                      expected_ids=expected, overwrite=args.overwrite,
                                      flip_decisions=args.flip_decisions)
            print(json.dumps({"staging": repo_rel(args.out), "inputs": report["inputs"],
                              "summary": report["summary"]}, ensure_ascii=False))
            return 0
        result = verify_staging(args.v5, args.v4_root, args.staging, expected_ids=expected,
                                flip_decisions=args.flip_decisions)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["valid"] else 1
    except ValueError as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
