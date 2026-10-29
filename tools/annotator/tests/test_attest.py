# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Human review statements are separate from independent annotations."""

import json

import pytest

from tools.annotator.evidence import build_bundle
from tools.annotator.records import RecordStore
from tools.annotator.tests.test_evidence import fixture_inputs


def setup(tmp_path):
    inputs = fixture_inputs(tmp_path)
    build_bundle(tmp_path / "bundle", **inputs)
    store = RecordStore(tmp_path / "records.sqlite")
    task = store.register_bundle(tmp_path / "bundle")
    annotation = {
        "attempted": {"value": "unknown", "evidence_refs": []},
        "executed": {"value": "unknown", "evidence_refs": []},
        "succeeded": {"value": "unknown", "evidence_refs": []},
        "identities": {"event-1": None},
        "roots": {"actors": None, "evidence_refs": []},
        "notes": "模型草稿。",
    }
    proposal = store.save(task, "model-proposal:test", annotation,
                          method="model_proposal", expected_revision=None)
    return store, task, proposal


def test_attestation_is_separate_and_records_typed_statement(tmp_path):
    store, task, proposal = setup(tmp_path)
    record = store.save_attestation(
        task, "刘周康", proposal["revision_id"],
        statement="我已逐条查看证据与模型草稿，接受其结论。")
    assert record["schema"] == "mtagentlens.review-attestation/v1"
    assert record["reviewer"] == "刘周康"
    assert record["proposal_revision_id"] == proposal["revision_id"]
    assert record["reviewer_type"] == "human_statement"
    assert record["signature"] == {"method": "typed_statement", "cryptographic": False}
    assert record["paper_metrics_available"] is False
    listed = store.list_attestations()
    assert [item["attestation_id"] for item in listed] == [record["attestation_id"]]
    # Attestations are not annotations and do not enter the v2 export.
    export = store.export()
    assert export["tasks"][0]["revisions"] == [proposal]
    assert "attestations" not in export


def test_duplicate_stale_or_self_review_is_refused(tmp_path):
    store, task, proposal = setup(tmp_path)
    store.save_attestation(task, "刘周康", proposal["revision_id"], statement="确认。")
    with pytest.raises(ValueError, match="already"):
        store.save_attestation(task, "刘周康", proposal["revision_id"], statement="再次确认。")
    with pytest.raises(ValueError, match="annotator"):
        store.save_attestation(task, "model-proposal:test", proposal["revision_id"],
                               statement="自称确认。")
    with pytest.raises(ValueError, match="revision"):
        store.save_attestation(task, "唐浩钺", "not-a-revision", statement="确认。")
    with pytest.raises(ValueError, match="statement"):
        store.save_attestation(task, "唐浩钺", proposal["revision_id"], statement=" ")


def test_cli_records_and_lists_attestations(tmp_path, monkeypatch, capsys):
    store, task, proposal = setup(tmp_path)
    from tools.annotator.__main__ import main
    monkeypatch.setattr("sys.argv", [
        "annotator", "attest", "--store", str(store.path), "--task", task,
        "--reviewer", "刘锦样", "--proposal-revision", proposal["revision_id"],
        "--statement", "已核对证据与草稿。"])
    assert main() == 0
    capsys.readouterr()
    monkeypatch.setattr("sys.argv", ["annotator", "attestations", "--store", str(store.path)])
    assert main() == 0
    output = json.loads(capsys.readouterr().out)
    assert output["schema"] == "mtagentlens.review-attestations/v1"
    assert output["attestations"][0]["reviewer"] == "刘锦样"
    assert output["paper_metrics_available"] is False


def test_each_annotators_latest_proposal_can_be_reviewed_but_older_revisions_are_stale(tmp_path):
    store, task, first = setup(tmp_path)
    annotation = {
        "attempted": {"value": "unknown", "evidence_refs": []},
        "executed": {"value": "unknown", "evidence_refs": []},
        "succeeded": {"value": "unknown", "evidence_refs": []},
        "identities": {"event-1": None},
        "roots": {"actors": None, "evidence_refs": []},
        "notes": "第二个 agent 草稿。",
    }
    other = store.save(task, "model-proposal:other", annotation,
                       method="model_proposal", expected_revision=None)
    # A reviewer may review each annotator's latest proposal, even when another
    # annotator's proposal is globally newer.
    store.save_attestation(task, "刘周康", first["revision_id"], statement="复核 agent A。")
    store.save_attestation(task, "唐浩钺", other["revision_id"], statement="复核 agent B。")
    # A newer revision from the same annotator makes the old one stale.
    newer = store.save(task, "model-proposal:test", annotation | {"notes": "更新。"},
                       method="model_proposal", expected_revision=first["revision_id"])
    with pytest.raises(ValueError, match="stale"):
        store.save_attestation(task, "刘锦样", first["revision_id"], statement="过期。")
    store.save_attestation(task, "刘锦样", newer["revision_id"], statement="复核更新稿。")


def test_review_statements_never_become_human_labels_or_agreement_data(tmp_path):
    """A typed statement is a review record, not an independent annotation."""
    from scripts.evaluation.annotation_agreement import agreement_from_export

    store, task, proposal = setup(tmp_path)
    reviewers = ("刘周康", "唐浩钺", "刘锦样")
    for reviewer in reviewers:
        store.save_attestation(task, reviewer, proposal["revision_id"],
                               statement="已逐条查看证据与模型草稿。")
    export = store.export()
    assert export["paper_metrics_available"] is False
    assert "attestations" not in export
    assert len(store.list_attestations()) == 3
    report = agreement_from_export(export, reviewers)
    assert report["coverage"]["tasks_with_all_raters"] == 0
    assert report["coverage"]["per_rater"] == {name: 0 for name in reviewers}
    assert report["fields"]["attempted"]["items"] == 0
    assert report["gate"]["status"] == "not_evaluable"
    assert report["paper_metrics_available"] is False
