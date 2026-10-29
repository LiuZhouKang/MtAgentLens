# SPDX-License-Identifier: Apache-2.0 OR MIT
"""GT v5 merge tests: v5 design authority, v4 detection contract, adjudicated flips.

These tests use tiny synthetic v4/v5 artifacts so they never read or write the
live dataset. The merged output must stay ``mtagentlens.ground_truth.v4`` for
``compare/tools/datasets.py`` while carrying the AI-consensus provenance block.
No v4/v5 label or attack_type difference is ever applied silently.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from scripts.evaluation.gt_merge import (
    MERGE_VERSION,
    merge_to_staging,
    verify_staging,
)

FIELDS = ("authorization_boundary", "entry_point", "root_source", "instruction_introducer")
PRESERVED = ("task_summary", "benign_activity", "expected_behaviors",
             "min_decision", "should_alert", "execution_check", "annotation", "category")


# --- GT v5 -> v4 merge contract ---------------------------------------------


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, value) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def v4_record(sid="attack-one", *, label="attack", attack_type="B",
              category="reconnaissance", channel="B"):
    behavior = {
        "behavior_id": "test-hook-reads-config",
        "description": "fixture test hook reads the deployment config",
        "attack_pattern": "CredentialAccess",
        "boundary_type": "ResourceBoundary",
        "severity": "Critical",
        "should_alert": True,
        "min_agents_involved": 1,
        "expected_commands": [{"type": "shell_command", "pattern": "make test", "why": "trigger"}],
        "expected_resources": [{"type": "file", "pattern": "config/service\\.env", "why": "secret"}],
        "evidence_requirements": "openat config/service.env",
        "rationale": "the task does not authorize reading deployment config",
    }
    return {
        "schema": "mtagentlens.ground_truth.v4",
        "scenario_id": sid,
        "category": category,
        "label": label,
        "attack_type": attack_type,
        "task_summary": f"task summary for {sid}",
        "payload": None if label == "benign" else {
            "carrier": "tests/__init__.py hook",
            "carrier_type": "fixture_test_hook",
            "payload_channel_classification": channel,
        },
        "benign_activity": {"summary": f"benign work for {sid}"} if label == "benign" else None,
        "expected_behaviors": [] if label == "benign" else [behavior],
        "min_decision": "Safe" if label == "benign" else "Unsafe",
        "should_alert": label == "attack",
        "execution_check": {"summary": f"check {sid}", "checks": [{"id": "c1"}]},
        "annotation": {
            "method": "model_proposal",
            "reviewer_agent_labels": ["刘周康", "唐浩钺", "刘锦样"],
            "collector_output_consulted": False,
            "agreement": {"label": {"values": ["attack"] * 3}},
        },
        "quality": {
            "notes": [f"legacy note for {sid}"],
            "needs_human_review": True,
            "legacy_needs_human_review": True,
            "human_review_status": "not_performed",
            "review": {"status": "resolved", "method": "rule-based-adjudication-v1",
                       "human_review": "waived_by_author", "evidence": ["legacy evidence"]},
        },
    }


def v5_record(sid="attack-one", *, label="attack", attack_type="B", references=None):
    good = {"status": "known", "value": f"{sid} field value", "evidence_refs": ["r1"]}
    record = {field: dict(good) for field in FIELDS}
    record.update({
        "scenario_id": sid,
        "anon_case_id": "case-0123456789",
        "category": "reconnaissance",
        "label": label,
        "attack_type": attack_type,
        "label_rationale": f"{sid} label rationale",
        "attack_type_rationale": f"{sid} type rationale",
        "execution_roles": [{"id": "design_role_0", "value": "runner", "evidence_refs": ["r1"]}],
        "causal_edges": [{"source": "design_role_0", "target": "runner.publish",
                          "relation": "runs", "evidence_refs": ["r1"]}],
        "references": references or [],
        "label_evidence_refs": ["r1"],
        "attack_type_evidence_refs": ["r1"],
        "limitations": ["no runtime validation"],
        "causal_chain_status": "not_applicable" if label == "benign" else "known",
        "quality_tier": "ai_consensus",
        "method": "ai_consensus_3",
        "human_review": False,
        "human_annotations": 0,
        "resolution": "consensus",
        "votes": {
            "label": {label: 3},
            "attack_type": {attack_type: 3},
            "label_votes": [label] * 3,
            "attack_type_votes": [attack_type] * 3,
            "fields": {field: {"status_votes": ["known"] * 3, "value_votes_distinct": 3,
                               "status_agreement": "unanimous", "selected_annotator": 1,
                               "value_divergence": True} for field in FIELDS},
        },
        "quality": {"status": "consensus", "needs_human_review": False,
                    "human_review_performed": False,
                    "note": "AI consensus label; no human annotation or human review is claimed."},
        "legacy_disclosure": {
            "legacy_v4_label": label,
            "legacy_v4_attack_type": "B",
            "legacy_v4_category": "reconnaissance",
            "legacy_v4_type_mismatch": True,
        },
    })
    return record


def make_workspace(tmp_path, *, scenarios=None, v5_overrides=None):
    """Build a synthetic v4 tree + v5 artifact with evidence-backed references."""
    source = tmp_path / "evidence" / "fixture.txt"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("line one\nline two\n", encoding="utf-8")
    reference = {"id": "r1", "path": str(source), "sha256": sha256_bytes(source.read_bytes()),
                 "line_start": 1, "line_end": 1, "quote": "line one"}
    scenarios = scenarios or ["attack-one", "benign-one"]
    v4_root = tmp_path / "v4"
    for sid in scenarios:
        record = v4_record(sid, label="benign", attack_type="benign",
                           category="benign") if sid.startswith("benign") else v4_record(sid)
        write_json(v4_root / record["category"] / f"{sid}.json", record)
    write_json(v4_root / "_reviewers" / "attack-one.json", {"not": "ground truth"})
    records = []
    for sid in scenarios:
        override = (v5_overrides or {}).get(sid, {})
        if sid.startswith("benign"):
            record = v5_record(sid, label="benign", attack_type="benign",
                              references=[reference])
            record["legacy_disclosure"]["legacy_v4_attack_type"] = "benign"
        else:
            record = v5_record(sid, references=[reference])
        record.update(override)
        records.append(record)
    v5 = {
        "schema": "mtagentlens.ground_truth.v5",
        "protocol_version": "ai-consensus-3+adjudication/2",
        "input_view": "human-design-audit-v2",
        "quality_tier": "ai_consensus",
        "method": "ai_consensus_3",
        "scope": "scenario_design",
        "eligible_for_runtime_metrics": False,
        "human_review": False,
        "human_annotations": 0,
        "annotator_count": 3,
        "reviewer_independence": "same model fresh sessions; not human experts",
        "records": records,
    }
    return write_json(tmp_path / "gt-v5.json", v5), v4_root


def flip_decision(decision, *, v4_value, v5_value, sid="attack-one"):
    return {
        "decision": decision,
        "rationale": f"adjudicated {sid}: v4 {v4_value} vs v5 {v5_value} -> {decision}",
        "session_id": f"session-flip-{sid}",
        "v4_value": v4_value,
        "v5_value": v5_value,
    }


def write_flip_decisions(base, scenarios):
    entries = []
    for sid, fields in scenarios.items():
        entries.append({
            "scenario_id": sid,
            "votes": {"label_votes": ["attack", "attack", "attack"],
                      "attack_type_votes": ["B", "B", "B"]},
            "adjudication": {"session_id": f"session-flip-{sid}"},
            "fields": fields,
        })
    return write_json(base / "flip-decisions.json", {
        "schema": "mtagentlens.flip-decisions/1",
        "human_review": False,
        "human_annotations": 0,
        "scenarios": entries,
    })


def staged_tree(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes()
            for path in sorted(root.rglob("*")) if path.is_file()}


def test_attack_type_decision_v5_applies_with_disclosure(tmp_path):
    v5_path, v4_root = make_workspace(tmp_path, v5_overrides={"attack-one": {"attack_type": "C"}})
    ids = {"attack-one", "benign-one"}
    decisions = write_flip_decisions(tmp_path, {"attack-one": {
        "attack_type": flip_decision("v5", v4_value="B", v5_value="C")}})
    report = merge_to_staging(v5_path, v4_root, tmp_path / "staging", expected_ids=ids,
                              flip_decisions=decisions)
    merged = json.loads((tmp_path / "staging/gt/reconnaissance/attack-one.json").read_text())
    assert (merged["schema"], merged["label"], merged["attack_type"]) == (
        "mtagentlens.ground_truth.v4", "attack", "C")
    assert merged["payload"]["payload_channel_classification"] == "C"
    assert merged["quality"]["needs_human_review"] is False
    assert merged["quality"]["v5_merge"]["legacy_needs_human_review"] is True
    assert merged["quality"].get("detection_excluded") is None
    assert any("B->C" in note and MERGE_VERSION in note for note in merged["quality"]["notes"])
    annotation = merged["label_annotation"]
    assert annotation["method"] == "ai_consensus_3"
    assert annotation["annotation_version"] == MERGE_VERSION
    assert annotation["human_review"] is False and annotation["human_annotations"] == 0
    assert annotation["evidence"]["fields"]["entry_point"]["value"] == "attack-one field value"
    assert annotation["v5_label"] == "attack" and annotation["v5_attack_type"] == "C"
    disagreement = annotation["v5_disagreement"]
    assert disagreement["fields"]["attack_type"]["decision"] == "v5"
    assert disagreement["fields"]["attack_type"]["session_id"] == "session-flip-attack-one"
    assert disagreement["fields"]["attack_type"]["v4_value"] == "B"
    assert disagreement["votes"]["attack_type_votes"] == ["B", "B", "B"]
    assert annotation["v4_detection_contract"]["attack_type_dependent_updates"] == [
        {"path": "/payload/payload_channel_classification", "before": "B", "after": "C"}]
    assert report["summary"]["attack_type_changes"] == 1
    assert report["summary"]["label_changes"] == 0
    assert report["summary"]["excluded_scenarios"] == 0
    assert report["summary"]["flip_decisions"] == {"unknown": 0, "v4": 0, "v5": 1}
    verify = verify_staging(v5_path, v4_root, tmp_path / "staging", expected_ids=ids,
                            flip_decisions=decisions)
    assert verify["valid"] is True, verify["problems"]


def test_unchanged_v4_contract_fields_are_preserved_byte_identically(tmp_path):
    v5_path, v4_root = make_workspace(tmp_path)
    ids = {"attack-one", "benign-one"}
    merge_to_staging(v5_path, v4_root, tmp_path / "staging", expected_ids=ids)
    for sid, rel in (("attack-one", "reconnaissance/attack-one.json"),
                     ("benign-one", "benign/benign-one.json")):
        original = json.loads((v4_root / rel).read_text())
        merged = json.loads((tmp_path / "staging/gt" / rel).read_text())
        for field in PRESERVED:
            assert merged[field] == original[field], (sid, field)
            assert json.dumps(merged[field], sort_keys=True) == json.dumps(original[field], sort_keys=True)
        assert merged["scenario_id"] == original["scenario_id"]
        assert merged["label"] == original["label"]
        assert merged["attack_type"] == original["attack_type"]
        assert "v5_disagreement" not in merged["label_annotation"]
        assert merged["quality"].get("detection_excluded") is None


def test_changed_type_scenario_gets_disclosure_and_report(tmp_path):
    v5_path, v4_root = make_workspace(tmp_path, v5_overrides={"attack-one": {"attack_type": "C"}})
    ids = {"attack-one", "benign-one"}
    decisions = write_flip_decisions(tmp_path, {"attack-one": {
        "attack_type": flip_decision("v5", v4_value="B", v5_value="C")}})
    report = merge_to_staging(v5_path, v4_root, tmp_path / "staging", expected_ids=ids,
                              flip_decisions=decisions)
    merged = json.loads((tmp_path / "staging/gt/reconnaissance/attack-one.json").read_text())
    assert merged["label_annotation"]["v4_detection_contract"]["sha256"] == sha256_bytes(
        (v4_root / "reconnaissance/attack-one.json").read_bytes())
    disclosure = merged["quality"]["v5_merge"]
    assert disclosure["annotation_version"] == MERGE_VERSION
    assert disclosure["human_review"] is False
    row = next(row for row in report["scenarios"] if row["scenario_id"] == "attack-one")
    assert "/quality/notes" in {change["path"] for change in row["changes"]}
    assert row["flip_decisions"] == {"attack_type": "v5"}
    assert report["summary"]["v4_scenarios_total"] == 2
    assert report["summary"]["v4_not_merged"] == 0
    assert report["inputs"]["flip_decisions"]["sha256"] == sha256_bytes(decisions.read_bytes())
    report_md = (tmp_path / "staging/MERGE-REPORT.md").read_text()
    assert "attack-one" in report_md and "flip decisions" in report_md


def test_missing_and_extra_ids_are_rejected(tmp_path):
    v5_path, v4_root = make_workspace(tmp_path)
    with pytest.raises(ValueError, match="coverage"):
        merge_to_staging(v5_path, v4_root, tmp_path / "a",
                         expected_ids={"attack-one", "benign-one", "absent-one"})
    with pytest.raises(ValueError, match="coverage"):
        merge_to_staging(v5_path, v4_root, tmp_path / "b", expected_ids={"attack-one"})
    v5 = json.loads(v5_path.read_text())
    orphan = json.loads(json.dumps(v5["records"][0]))
    orphan["scenario_id"] = "orphan-one"
    v5["records"].append(orphan)
    v5_path.write_text(json.dumps(v5))
    with pytest.raises(ValueError, match="missing v4"):
        merge_to_staging(v5_path, v4_root, tmp_path / "c",
                         expected_ids={"attack-one", "benign-one", "orphan-one"})


def test_human_review_claims_are_never_fabricated(tmp_path):
    v5_path, v4_root = make_workspace(tmp_path)
    v5 = json.loads(v5_path.read_text())
    v5["human_review"] = True
    v5_path.write_text(json.dumps(v5))
    with pytest.raises(ValueError, match="human"):
        merge_to_staging(v5_path, v4_root, tmp_path / "staging",
                         expected_ids={"attack-one", "benign-one"})
    v5["human_review"] = False
    v5["records"][0]["human_annotations"] = 1
    v5_path.write_text(json.dumps(v5))
    with pytest.raises(ValueError, match="human"):
        merge_to_staging(v5_path, v4_root, tmp_path / "staging",
                         expected_ids={"attack-one", "benign-one"})


def test_missing_flip_decision_is_rejected(tmp_path):
    v5_path, v4_root = make_workspace(tmp_path, v5_overrides={"attack-one": {"attack_type": "C"}})
    ids = {"attack-one", "benign-one"}
    with pytest.raises(ValueError, match="flip decision"):
        merge_to_staging(v5_path, v4_root, tmp_path / "a", expected_ids=ids)
    incomplete = write_flip_decisions(tmp_path / "incomplete", {"attack-one": {
        "label": flip_decision("v4", v4_value="attack", v5_value="attack")}})
    with pytest.raises(ValueError, match="attack_type"):
        merge_to_staging(v5_path, v4_root, tmp_path / "b", expected_ids=ids,
                         flip_decisions=incomplete)
    invalid = write_flip_decisions(tmp_path / "invalid", {"attack-one": {
        "attack_type": flip_decision("v6", v4_value="B", v5_value="C")}})
    with pytest.raises(ValueError, match="decision"):
        merge_to_staging(v5_path, v4_root, tmp_path / "c", expected_ids=ids,
                         flip_decisions=invalid)


def test_label_v5_flip_keeps_v4_detection_contract_and_excludes(tmp_path):
    v5_path, v4_root = make_workspace(tmp_path, v5_overrides={
        "attack-one": {"label": "benign", "attack_type": "benign"}})
    ids = {"attack-one", "benign-one"}
    decisions = write_flip_decisions(tmp_path, {"attack-one": {
        "label": flip_decision("v5", v4_value="attack", v5_value="benign"),
        "attack_type": flip_decision("v5", v4_value="B", v5_value="benign")}})
    report = merge_to_staging(v5_path, v4_root, tmp_path / "staging", expected_ids=ids,
                              flip_decisions=decisions)
    merged = json.loads((tmp_path / "staging/gt/reconnaissance/attack-one.json").read_text())
    original = json.loads((v4_root / "reconnaissance/attack-one.json").read_text())
    assert merged["label"] == "attack" and merged["attack_type"] == "B"
    assert merged["expected_behaviors"] == original["expected_behaviors"]
    assert merged["quality"]["detection_excluded"] is True
    assert "independent 3-annotator" in merged["quality"]["exclusion_reason"]
    assert any("adjudicated v4/v5 disagreement" in note for note in merged["quality"]["notes"])
    annotation = merged["label_annotation"]
    assert annotation["v5_label"] == "benign" and annotation["v5_attack_type"] == "benign"
    assert annotation["v5_disagreement"]["fields"]["label"]["decision"] == "v5"
    assert annotation["legacy_disclosure_note"].startswith("v4 label/attack_type")
    from compare.tools.datasets import _expected_claims
    assert _expected_claims(merged), "excluded record must still satisfy the v4 detection contract"
    assert report["summary"]["label_flips"] == 1
    assert report["summary"]["excluded_scenarios"] == 1
    assert report["summary"]["flip_decisions"] == {"unknown": 0, "v4": 0, "v5": 2}
    verify = verify_staging(v5_path, v4_root, tmp_path / "staging", expected_ids=ids,
                            flip_decisions=decisions)
    assert verify["valid"] is True, verify["problems"]


def test_label_v4_decision_retains_and_records_disagreement(tmp_path):
    v5_path, v4_root = make_workspace(tmp_path, v5_overrides={
        "attack-one": {"label": "benign", "attack_type": "benign"}})
    ids = {"attack-one", "benign-one"}
    decisions = write_flip_decisions(tmp_path, {"attack-one": {
        "label": flip_decision("v4", v4_value="attack", v5_value="benign"),
        "attack_type": flip_decision("v4", v4_value="B", v5_value="benign")}})
    report = merge_to_staging(v5_path, v4_root, tmp_path / "staging", expected_ids=ids,
                              flip_decisions=decisions)
    merged = json.loads((tmp_path / "staging/gt/reconnaissance/attack-one.json").read_text())
    assert merged["label"] == "attack" and merged["attack_type"] == "B"
    assert merged["quality"].get("detection_excluded") is None
    assert merged["label_annotation"]["v5_disagreement"]["fields"]["label"]["decision"] == "v4"
    assert merged["label_annotation"]["v5_label"] == "benign"
    assert report["summary"]["excluded_scenarios"] == 0
    assert report["summary"]["flip_decisions"] == {"unknown": 0, "v4": 2, "v5": 0}
    verify = verify_staging(v5_path, v4_root, tmp_path / "staging", expected_ids=ids,
                            flip_decisions=decisions)
    assert verify["valid"] is True, verify["problems"]


def test_unknown_decision_excludes_scenario(tmp_path):
    v5_path, v4_root = make_workspace(tmp_path, v5_overrides={"attack-one": {"attack_type": "C"}})
    ids = {"attack-one", "benign-one"}
    decisions = write_flip_decisions(tmp_path, {"attack-one": {
        "attack_type": flip_decision("unknown", v4_value="B", v5_value="C")}})
    report = merge_to_staging(v5_path, v4_root, tmp_path / "staging", expected_ids=ids,
                              flip_decisions=decisions)
    merged = json.loads((tmp_path / "staging/gt/reconnaissance/attack-one.json").read_text())
    assert merged["attack_type"] == "B"
    assert merged["quality"]["detection_excluded"] is True
    assert "adjudication audit found" in merged["quality"]["exclusion_reason"]
    assert merged["label_annotation"]["v5_disagreement"]["fields"]["attack_type"]["decision"] == "unknown"
    assert report["summary"]["excluded_scenarios"] == 1
    verify = verify_staging(v5_path, v4_root, tmp_path / "staging", expected_ids=ids,
                            flip_decisions=decisions)
    assert verify["valid"] is True, verify["problems"]


def test_merge_is_deterministic_and_verifies(tmp_path):
    v5_path, v4_root = make_workspace(tmp_path, v5_overrides={"attack-one": {"attack_type": "C"}})
    ids = {"attack-one", "benign-one"}
    decisions = write_flip_decisions(tmp_path, {"attack-one": {
        "attack_type": flip_decision("v5", v4_value="B", v5_value="C")}})
    merge_to_staging(v5_path, v4_root, tmp_path / "one", expected_ids=ids,
                     flip_decisions=decisions)
    merge_to_staging(v5_path, v4_root, tmp_path / "two", expected_ids=ids,
                     flip_decisions=decisions)
    assert staged_tree(tmp_path / "one") == staged_tree(tmp_path / "two")
    verification = verify_staging(v5_path, v4_root, tmp_path / "one", expected_ids=ids,
                                  flip_decisions=decisions)
    assert verification["valid"] is True, verification["problems"]
    assert verification["scenarios"] == 2


def test_staging_path_safety(tmp_path):
    v5_path, v4_root = make_workspace(tmp_path)
    with pytest.raises(ValueError, match="v4"):
        merge_to_staging(v5_path, v4_root, v4_root / "staging",
                         expected_ids={"attack-one", "benign-one"})
    unknown = tmp_path / "unknown"
    unknown.mkdir()
    (unknown / "keep.txt").write_text("do not delete")
    with pytest.raises(ValueError, match="staging"):
        merge_to_staging(v5_path, v4_root, unknown, overwrite=True,
                         expected_ids={"attack-one", "benign-one"})
    merge_to_staging(v5_path, v4_root, tmp_path / "staging",
                     expected_ids={"attack-one", "benign-one"})
    merge_to_staging(v5_path, v4_root, tmp_path / "staging", overwrite=True,
                     expected_ids={"attack-one", "benign-one"})
    assert (tmp_path / "staging/.gt-merge-staging.json").is_file()


def test_verify_rejects_evidence_hash_and_untracked_label(tmp_path):
    v5_path, v4_root = make_workspace(tmp_path, v5_overrides={"attack-one": {"attack_type": "C"}})
    ids = {"attack-one", "benign-one"}
    decisions = write_flip_decisions(tmp_path, {"attack-one": {
        "attack_type": flip_decision("v5", v4_value="B", v5_value="C")}})
    staging = tmp_path / "staging"
    merge_to_staging(v5_path, v4_root, staging, expected_ids=ids, flip_decisions=decisions)
    merged_path = staging / "gt/reconnaissance/attack-one.json"
    merged = json.loads(merged_path.read_text())
    merged["label_annotation"]["v5_label"] = "benign"
    write_json(merged_path, merged)
    problems = verify_staging(v5_path, v4_root, staging, expected_ids=ids,
                              flip_decisions=decisions)["problems"]
    assert any("deterministic mismatch" in problem or "flip" in problem for problem in problems), problems

    merge_to_staging(v5_path, v4_root, tmp_path / "staging2", expected_ids=ids,
                     flip_decisions=decisions)
    source = tmp_path / "evidence/fixture.txt"
    source.write_text("tampered line two\n", encoding="utf-8")
    problems = verify_staging(v5_path, v4_root, tmp_path / "staging2", expected_ids=ids,
                              flip_decisions=decisions)["problems"]
    assert any("evidence" in problem for problem in problems), problems


def test_verify_accepts_identical_relocated_v4_tree(tmp_path):
    """After the W6 GT swap the merge base is archived at a new path."""
    v5_path, v4_root = make_workspace(tmp_path)
    ids = {"attack-one", "benign-one"}
    merge_to_staging(v5_path, v4_root, tmp_path / "staging", expected_ids=ids)
    relocated = tmp_path / "relocated-v4"
    shutil.copytree(v4_root, relocated)
    verification = verify_staging(v5_path, relocated, tmp_path / "staging", expected_ids=ids)
    assert verification["valid"] is True, verification["problems"]


def test_verify_rejects_coverage_gap(tmp_path):
    v5_path, v4_root = make_workspace(tmp_path)
    ids = {"attack-one", "benign-one"}
    staging = tmp_path / "staging"
    merge_to_staging(v5_path, v4_root, staging, expected_ids=ids)
    (staging / "gt/benign/benign-one.json").unlink()
    problems = verify_staging(v5_path, v4_root, staging, expected_ids=ids)["problems"]
    assert any("coverage" in problem for problem in problems), problems


def test_verify_requires_identical_flip_decisions(tmp_path):
    v5_path, v4_root = make_workspace(tmp_path, v5_overrides={"attack-one": {"attack_type": "C"}})
    ids = {"attack-one", "benign-one"}
    decisions = write_flip_decisions(tmp_path, {"attack-one": {
        "attack_type": flip_decision("v5", v4_value="B", v5_value="C")}})
    merge_to_staging(v5_path, v4_root, tmp_path / "staging", expected_ids=ids,
                     flip_decisions=decisions)
    without = verify_staging(v5_path, v4_root, tmp_path / "staging", expected_ids=ids)
    assert without["valid"] is False and any("flip decision" in problem
                                            for problem in without["problems"])
    changed = write_flip_decisions(tmp_path / "changed", {"attack-one": {
        "attack_type": flip_decision("v4", v4_value="B", v5_value="C")}})
    problems = verify_staging(v5_path, v4_root, tmp_path / "staging", expected_ids=ids,
                              flip_decisions=changed)["problems"]
    assert problems and any("deterministic mismatch" in problem or "flip policy" in problem
                            for problem in problems), problems


def test_excluded_attack_still_satisfies_v4_loader_contract(tmp_path):
    v5_path, v4_root = make_workspace(tmp_path, v5_overrides={
        "attack-one": {"label": "benign", "attack_type": "benign"}})
    decisions = write_flip_decisions(tmp_path, {"attack-one": {
        "label": flip_decision("v5", v4_value="attack", v5_value="benign"),
        "attack_type": flip_decision("v5", v4_value="B", v5_value="benign")}})
    merge_to_staging(v5_path, v4_root, tmp_path / "staging",
                     expected_ids={"attack-one", "benign-one"}, flip_decisions=decisions)
    merged = json.loads((tmp_path / "staging/gt/reconnaissance/attack-one.json").read_text())
    from compare.tools.datasets import _expected_claims
    assert merged["label"] == "attack" and merged["quality"]["detection_excluded"] is True
    assert len(_expected_claims(merged, None)) == 1
