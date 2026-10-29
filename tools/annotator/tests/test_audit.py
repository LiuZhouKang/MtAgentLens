# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Readiness audit reports evidence sufficiency, never labels or κ."""

import hashlib
import json

from tools.annotator.audit import audit_store
from tools.annotator.evidence import build_bundle
from tools.annotator.records import RecordStore
from tools.annotator.tests.test_evidence import fixture_inputs


def build_store(tmp_path, *, sources):
    inputs = fixture_inputs(tmp_path)
    bundle_dir = tmp_path / "bundle"
    payloads = {
        "runner": b'{"native_id":"r1"}\n',
        "task_brief": b'{"kind":"task_brief","text":"Read docs."}\n',
        "collector": b'{"kind":"row","table":"actions","row":{"action_type":"FileOpen"}}\n',
        "trajectory": b'{"kind":"assistant_message","text":"reading"}\n',
    }
    build_sources, events = {}, []
    for index, source_id in enumerate(sources):
        path = tmp_path / f"{source_id}.jsonl"
        path.write_bytes(payloads[source_id])
        build_sources[source_id] = {"path": path, "kind": "raw_capture",
                                    "producer": f"fixture-{source_id}"}
        events.append({
            "event_id": f"{source_id}-event",
            "source_id": source_id,
            "record_id": "r1",
            "timestamp": "1",
            "clock_domain": "test",
            "derivation": ("collector sqlite actions FileOpen row"
                           if source_id == "collector" else f"{source_id} fixture"),
            "byte_offset": 0,
            "byte_length": len(payloads[source_id]),
            "record_sha256": hashlib.sha256(payloads[source_id]).hexdigest(),
        })
    build_bundle(bundle_dir, release_root=inputs["release_root"], binding=inputs["binding"],
                 sources=build_sources, actors=["actor-a"], events=events)
    store = RecordStore(tmp_path / "records.sqlite")
    task = store.register_bundle(bundle_dir)
    return store, task


def test_full_evidence_is_ready_for_independent_annotation(tmp_path):
    store, task = build_store(tmp_path, sources=("runner", "task_brief", "trajectory", "collector"))
    report = audit_store(store.path, codebook_version="pilot/1")
    assert report["schema"] == "mtagentlens.annotation-readiness/v1"
    assert report["paper_metrics_available"] is False
    assert report["summary"]["tasks"] == 1
    assert report["summary"]["ready_tasks"] == 1
    assert report["summary"]["human_annotations"] == 0
    assert report["tasks"][0]["checks"] == {
        "runner": True, "task_brief": True, "trajectory": True,
        "collector_actions": True, "codebook_matches": True}
    assert report["tasks"][0]["blockers"] == []


def test_missing_evidence_sources_are_blockers(tmp_path):
    store, task = build_store(tmp_path, sources=("runner",))
    report = audit_store(store.path)
    assert report["summary"]["ready_tasks"] == 0
    blockers = report["tasks"][0]["blockers"]
    assert any("task brief" in item for item in blockers)
    assert any("trajectory" in item for item in blockers)
    assert any("collector" in item for item in blockers)


def test_audit_cli_writes_the_report(tmp_path, monkeypatch, capsys):
    store, _ = build_store(tmp_path, sources=("runner", "task_brief", "trajectory", "collector"))
    out = tmp_path / "audit.json"
    from tools.annotator.__main__ import main
    monkeypatch.setattr("sys.argv", ["annotator", "audit", "--store", str(store.path),
                                     "--codebook-version", "pilot/1", "--out", str(out)])
    assert main() == 0
    capsys.readouterr()
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["summary"]["ready_tasks"] == 1
    assert report["paper_metrics_available"] is False
