# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Model proposals are labelled, blinded, validated and never human labels."""

import hashlib
import json

import pytest

from tools.annotator.evidence import build_bundle
from tools.annotator.propose import export_task, save_proposal
from tools.annotator.records import RecordStore
from tools.annotator.tests.test_evidence import fixture_inputs


def prepared(tmp_path):
    spec = fixture_inputs(tmp_path)
    raw = spec["sources"]["runner"]["path"]
    raw.write_text(json.dumps({
        "native_id": "r1", "scenario_id": "scenario", "run_id": "run-1",
        "text": ("scenario run-1 executed by actor-a; "
                 "source_run_uuid=efc35a21-b940-4916-a5c8-9996638b60dd"),
    }) + "\n")
    spec["events"][0].update(byte_length=raw.stat().st_size,
                            record_sha256=hashlib.sha256(raw.read_bytes()).hexdigest())
    build_bundle(tmp_path / "bundle", **spec)
    store = tmp_path / "records.sqlite"
    RecordStore(store).register_bundle(tmp_path / "bundle")
    task = RecordStore(store).export()["tasks"][0]
    return store, task["task_id"]


def proposal(**changes):
    annotation = {
        "attempted": {"value": "unknown", "evidence_refs": []},
        "executed": {"value": "unknown", "evidence_refs": []},
        "succeeded": {"value": "unknown", "evidence_refs": []},
        "identities": {"event-1": None},
        "roots": {"actors": None, "evidence_refs": []},
        "notes": "证据不足，保留未知。",
    }
    annotation.update(changes)
    return annotation


def test_export_task_blinds_identifiers_and_has_no_labels(tmp_path):
    store, task_id = prepared(tmp_path)
    exported = export_task(RecordStore(store), task_id)
    assert exported["task_id"] == task_id
    assert exported["codebook_version"] == "pilot/1"
    text = exported["events"][0]["text"]
    assert text.count("blinded-") >= 2
    assert '\"scenario\"' not in text
    assert "run-1" not in text
    assert "efc35a21-b940-4916-a5c8-9996638b60dd" not in text
    assert exported["events"][0]["redactions"] >= 3
    assert "revisions" not in exported


def test_save_proposal_is_labelled_model_proposal(tmp_path):
    store, task_id = prepared(tmp_path)
    record = save_proposal(RecordStore(store), task_id, proposal(),
                           annotator="model-proposal:test", codebook_version="pilot/1")
    assert record["method"] == "model_proposal"
    assert record["annotator"] == "model-proposal:test"
    assert record["annotation"]["attempted"]["value"] == "unknown"
    exported = RecordStore(store).export()
    revision = exported["tasks"][0]["revisions"][0]
    assert revision["method"] == "model_proposal"
    assert exported["paper_metrics_available"] is False


def test_wrong_codebook_or_annotator_is_refused(tmp_path):
    store, task_id = prepared(tmp_path)
    with pytest.raises(ValueError, match="codebook"):
        save_proposal(RecordStore(store), task_id, proposal(),
                      annotator="model-proposal:test", codebook_version="pilot/2")
    with pytest.raises(ValueError, match="annotator"):
        save_proposal(RecordStore(store), task_id, proposal(),
                      annotator="  ", codebook_version="pilot/1")


def test_invalid_proposal_keeps_the_store_unchanged(tmp_path):
    store, task_id = prepared(tmp_path)
    with pytest.raises(ValueError, match="evidence"):
        save_proposal(RecordStore(store), task_id, proposal(
            attempted={"value": "yes", "evidence_refs": []}),
            annotator="model-proposal:test", codebook_version="pilot/1")
    assert RecordStore(store).export()["tasks"][0]["revisions"] == []


def test_cli_exports_evidence_and_saves_a_proposal(tmp_path, monkeypatch, capsys):
    store, task_id = prepared(tmp_path)
    evidence_path = tmp_path / "task.json"
    monkeypatch.setattr("sys.argv", ["annotator", "task-evidence", "--store", str(store),
                                     "--task", task_id, "--out", str(evidence_path)])
    from tools.annotator.__main__ import main
    assert main() == 0
    capsys.readouterr()
    exported = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert exported["schema"] == "mtagentlens.proposal-task-export/v1"
    annotation_path = tmp_path / "proposal.json"
    annotation_path.write_text(json.dumps(proposal(), ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["annotator", "propose", "--store", str(store),
                                     "--task", task_id, "--annotator", "model-proposal:test",
                                     "--annotation", str(annotation_path),
                                     "--codebook-version", "pilot/1"])
    assert main() == 0
    record = json.loads(capsys.readouterr().out)
    assert record["method"] == "model_proposal"
