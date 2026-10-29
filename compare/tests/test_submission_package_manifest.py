"""Manifest generation cannot turn missing experiment evidence into a release."""
import hashlib
import json
from pathlib import Path

import pytest

import compare.scripts.build.build_submission_package_manifest as builder
from compare.scripts.vis.paper_report import PaperReportError


def test_submission_manifest_rejects_missing_current_report():
    with pytest.raises(PaperReportError, match="cannot read"):
        builder.build_manifest()


def test_manifest_inventory_hashes_inputs_and_excludes_private_traces(tmp_path, monkeypatch):
    dataset = tmp_path / "dataset"
    for relative in ("ground_truth/example.json", "ground_truth/_external_archive/old.json",
                     "traces/run/responsibility.db", "prompt_inputs/cases/example/prompt.txt"):
        path = dataset / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("synthetic\n")
    artifact = tmp_path / "report.json"
    artifact.write_text("synthetic report\n")
    monkeypatch.setattr(builder, "DATASET_ROOT", dataset)
    monkeypatch.setattr(builder, "CANONICAL_ARTIFACTS", {"evaluation_report": artifact})
    monkeypatch.setattr(builder, "RELEASE_ARTIFACTS", {})
    monkeypatch.setattr(builder, "ANNOTATION_PACKAGE_FILES", {})
    monkeypatch.setattr(builder, "CODE_ARTIFACT_ROOTS", [])
    monkeypatch.setattr(builder, "CODE_ARTIFACT_FILES", [])
    monkeypatch.setattr(builder, "PROJECT_ROOT", tmp_path)
    entries = builder._file_inventory({"figures": []})
    assert len(entries) == 3
    for entry in entries:
        path = tmp_path / entry["path"]
        assert entry["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
        assert entry["bytes"] == path.stat().st_size
    assert not any("traces" in entry["path"] or "_external_archive" in entry["path"] for entry in entries)


def test_annotation_availability_requires_evidence_before_reliability_claim(tmp_path, monkeypatch):
    monkeypatch.setattr(builder, "RESULTS_ROOT", tmp_path)
    annotation = builder._build_annotation_availability()
    assert annotation["claim_boundary"] == "blind_annotation_quality_not_claimed"
    assert annotation["required_author_actions"]
