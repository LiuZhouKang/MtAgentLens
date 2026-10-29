# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Detector-blind reference bundle: no detector output and no fabricated root."""
import hashlib
import json
import stat
from pathlib import Path

import pytest

from scripts.evaluation import reference_bundle as rb

RELEASE = "9c7099c1d36d21af025f5050d462b273cc04aef0c69d3ff72e902779372c6ea3"


def record(case_id="case-0000", **overrides):
    value = {
        "case_id": case_id,
        "task_id": "a" * 64,
        "method": rb.METHOD,
        "human_review": False,
        "paper_metrics_available": False,
        "binding": {"data_release_id": RELEASE, "source_run_uuid": "run-1",
                    "run_id": "run-1", "scenario_id": "scenario-x"},
        "annotation": {
            "attempted": {"value": "yes", "evidence_refs": ["e1"]},
            "executed": {"value": "yes", "evidence_refs": ["e1"]},
            "succeeded": {"value": "unknown", "evidence_refs": ["e1"]},
            "roots": {"actors": [], "evidence_refs": ["e1"]},
        },
        "event_identity_policy": {"logical_identity": None, "identity_known": 0},
        "logical_root_actor": None,
        "entry": "e1",
        "chain": ["e1"],
        "entry_chain_status": "AI hypotheses from observed evidence",
    }
    value.update(overrides)
    return value


def runtime_root(tmp_path: Path, *, annotations=None, verification=None) -> Path:
    root = tmp_path / "runtime"
    root.mkdir(parents=True)
    payload = {
        "schema": "mtagentlens.ai-runtime-consensus/v1",
        "protocol_version": "ai-consensus-3/1",
        "reviewer_count": 3,
        "reviewer_ids": ["dsh-a", "dsh-b", "dsh-c"],
        "adjudicator_id": rb.ADJUDICATOR,
        "method": rb.METHOD,
        "human_review": False,
        "paper_metrics_available": False,
        "records": [record()],
    }
    if annotations:
        payload.update(annotations)
    (root / "annotations.json").write_text(json.dumps(payload))
    verified = {
        "schema": rb.VERIFICATION_SCHEMA,
        "valid": True,
        "completed": len(payload["records"]),
        "candidate_cases": len(payload["records"]),
        "all_candidates_reviewed": True,
    }
    if verification:
        verified.update(verification)
    (root / "verification.json").write_text(json.dumps(verified))
    return root


@pytest.fixture
def mocked_verify(monkeypatch):
    calls = []

    def fake_verify(root, reviewers=None, write_report=True):
        annotations = json.loads((Path(root) / "annotations.json").read_text())
        records = annotations.get("records") or []
        calls.append({"root": Path(root), "reviewers": reviewers,
                      "write_report": write_report})
        return {
            "schema": rb.VERIFICATION_SCHEMA,
            "valid": True,
            "completed": len(records),
            "candidate_cases": len(records),
            "reviewer_count": 3,
            "reviewer_ids": ["dsh-a", "dsh-b", "dsh-c"],
            "adjudicator_id": rb.ADJUDICATOR,
            "tool_events_checked": True,
            "all_candidates_reviewed": True,
            "errors": [],
        }

    monkeypatch.setattr(rb.ai_runtime, "verify_artifact", fake_verify)
    return calls


def _real_pipeline_root(tmp_path: Path) -> Path:
    from tools.annotator.ai_runtime import dump, finalize, verify_artifact
    from tools.annotator.tests.test_ai_runtime import _build_runtime_case, _write_review

    root = tmp_path / "runtime"
    root.mkdir()
    case = _build_runtime_case(root, 0)
    dump(root / "inventory.json", {"cases": [case]})
    review = {
        "case_id": case["case_id"], "attempted": "no", "executed": "no",
        "succeeded": "no", "evidence_refs": ["event-1"],
        "notes": "Synthetic detector-blind review; provenance regression only.",
        "entry": None, "chain": [], "root_actor": None,
    }
    for reviewer in ("dsh-a", "dsh-b", "dsh-c"):
        _write_review(root, reviewer, case["case_id"], review, f"session-{reviewer}")
    finalize(root, reviewers=3)
    assert verify_artifact(root, reviewers=3)["valid"] is True
    return root


def test_build_binds_reviewed_runtime_and_keeps_root_unknown(tmp_path, mocked_verify):
    root = runtime_root(tmp_path)
    out = tmp_path / "bundle.json"
    manifest = rb.build(root, out)
    assert mocked_verify == [{"root": root, "reviewers": 3, "write_report": False}]
    assert manifest["proof"]["reviewer_count"] == 3
    assert manifest["proof"]["tool_events_checked"] is True
    assert manifest["proof"]["stored_verification_sha256"] == manifest["source"]["verification_sha256"]
    assert manifest["proof"]["recomputed_verification_sha256"] != manifest["source"]["verification_sha256"]
    assert manifest["schema"] == rb.SCHEMA
    assert manifest["release_id"] == RELEASE
    assert manifest["protocol"]["reviewer_count"] == 3
    assert manifest["protocol"]["human_review"] is False
    assert manifest["protocol"]["paper_metrics_available"] is False
    assert manifest["reference"]["identity"]["status"] == "unknown"
    assert manifest["reference"]["attribution"]["root_status"] == "unknown"
    assert manifest["reference"]["attribution"]["root_known"] == 0
    assert manifest["reference"]["attribution"]["estimable"] is False
    assert manifest["coverage"]["entry_known"] == 1
    assert manifest["coverage"]["chain_known"] == 1
    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    assert json.loads(out.read_text()) == manifest

    result = rb.validate(out)
    assert result["valid"] is True, result
    assert result["release_id"] == RELEASE


def test_build_rejects_detector_output_in_review_artifact(tmp_path):
    root = runtime_root(tmp_path)
    annotations = json.loads((root / "annotations.json").read_text())
    annotations["records"][0]["annotation"]["final_decision"] = "Unsafe"
    (root / "annotations.json").write_text(json.dumps(annotations))
    with pytest.raises(rb.BundleError, match="detector/ground-truth residue"):
        rb.build(root, tmp_path / "bundle.json")


def test_build_rejects_fabricated_root_and_human_claims(tmp_path):
    root = runtime_root(tmp_path)
    annotations = json.loads((root / "annotations.json").read_text())
    annotations["records"][0]["logical_root_actor"] = "agent-a"
    (root / "annotations.json").write_text(json.dumps(annotations))
    with pytest.raises(rb.BundleError, match="logical root"):
        rb.build(root, tmp_path / "bundle.json")

    root = runtime_root(tmp_path / "second")
    annotations = json.loads((root / "annotations.json").read_text())
    annotations["records"][0]["annotation"]["roots"]["actors"] = ["agent-a"]
    (root / "annotations.json").write_text(json.dumps(annotations))
    with pytest.raises(rb.BundleError, match="root set"):
        rb.build(root, tmp_path / "bundle.json")

    root = runtime_root(tmp_path / "third")
    annotations = json.loads((root / "annotations.json").read_text())
    annotations["human_review"] = True
    (root / "annotations.json").write_text(json.dumps(annotations))
    with pytest.raises(rb.BundleError, match="human review"):
        rb.build(root, tmp_path / "bundle.json")


def test_build_requires_three_reviewers_and_verified_pipeline(tmp_path):
    root = runtime_root(tmp_path, annotations={"reviewer_count": 2})
    with pytest.raises(rb.BundleError, match="exactly three"):
        rb.build(root, tmp_path / "bundle.json")

    root = runtime_root(tmp_path / "second", annotations={"paper_metrics_available": True})
    with pytest.raises(rb.BundleError, match="paper_metrics_available"):
        rb.build(root, tmp_path / "bundle.json")

    root = runtime_root(tmp_path / "third", verification={"valid": False})
    with pytest.raises(rb.BundleError, match="verification is not valid"):
        rb.build(root, tmp_path / "bundle.json")


def test_validate_detects_changed_evidence_and_promoted_metrics(tmp_path, mocked_verify):
    root = runtime_root(tmp_path)
    out = tmp_path / "bundle.json"
    rb.build(root, out)
    (root / "annotations.json").write_text((root / "annotations.json").read_text() + "\n")
    result = rb.validate(out)
    assert result["valid"] is False
    assert any("annotations sha256 mismatch" in problem for problem in result["problems"])

    root = runtime_root(tmp_path / "second")
    out = tmp_path / "second.json"
    rb.build(root, out)
    manifest = json.loads(out.read_text())
    manifest["reference"]["attribution"]["root_known"] = 1
    manifest["reference"]["attribution"]["estimable"] = True
    out.write_text(json.dumps(manifest))
    result = rb.validate(out)
    assert result["valid"] is False
    assert any("root must remain unknown" in problem for problem in result["problems"])


def test_cli_build_and_validate_exit_codes(tmp_path, capsys, mocked_verify):
    root = runtime_root(tmp_path)
    out = tmp_path / "bundle.json"
    assert rb.main(["build", "--runtime-root", str(root), "--out", str(out)]) == 0
    assert rb.main(["validate", "--bundle", str(out)]) == 0
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["valid"] is True

    (root / "annotations.json").write_text("{}")
    assert rb.main(["validate", "--bundle", str(out)]) == 1
    assert json.loads(capsys.readouterr().out)["valid"] is False

    missing = tmp_path / "missing"
    assert rb.main(["build", "--runtime-root", str(missing),
                    "--out", str(tmp_path / "x.json")]) == 2
    assert "cannot read JSON" in capsys.readouterr().err


def test_build_rejects_fabricated_two_file_runtime(tmp_path):
    root = runtime_root(tmp_path)
    out = tmp_path / "bundle.json"
    with pytest.raises(rb.BundleError, match="re-verify.*inventory"):
        rb.build(root, out)
    assert not out.exists()


def test_real_pipeline_is_read_only_and_rejects_nonfresh_sessions(tmp_path):
    root = _real_pipeline_root(tmp_path)
    verification_path = root / "verification.json"
    stored = json.loads(verification_path.read_text())
    stored["sentinel"] = "must survive read-only re-verification"
    verification_path.write_text(json.dumps(stored))
    before = {str(path.relative_to(root)): path.read_bytes()
              for path in root.rglob("*") if path.is_file()}

    out = tmp_path / "bundle.json"
    manifest = rb.build(root, out)
    after = {str(path.relative_to(root)): path.read_bytes()
             for path in root.rglob("*") if path.is_file()}
    assert before == after
    assert manifest["proof"]["recomputed_verification_sha256"] != manifest["source"]["verification_sha256"]
    assert rb.validate(out)["valid"] is True, rb.validate(out)

    metadata_path = root / "reviews/dsh-a-case-0000.metadata.json"
    metadata = json.loads(metadata_path.read_text())
    metadata["fresh_session"] = False
    metadata_path.write_text(json.dumps(metadata))
    with pytest.raises(rb.BundleError, match="fresh"):
        rb.build(root, tmp_path / "bad.json")


def test_validate_rejects_legacy_bundle_without_recomputed_proof(tmp_path, mocked_verify):
    root = runtime_root(tmp_path)
    out = tmp_path / "bundle.json"
    rb.build(root, out)
    manifest = json.loads(out.read_text())
    del manifest["proof"]
    out.write_text(json.dumps(manifest))
    result = rb.validate(out)
    assert result["valid"] is False
    assert any("recomputed verification proof" in problem for problem in result["problems"])
