# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Finalized collector rows become anchored annotation evidence; labels stay absent."""

import hashlib
import json
import sqlite3

import pytest

from tools.annotator.collector_evidence import export_collector_evidence
from tools.annotator.evidence import load_bundle
from tools.annotator.runner_import import import_runner_bundle
from tools.annotator.tests.test_runner_import import inputs, write_rows  # noqa: F401


def make_db(path, *, run_id="collector-run", mixed_run=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE runs (run_id TEXT, started_at_ns INTEGER, final_decision TEXT,
                           final_severity TEXT, decision_provenance_json TEXT);
        CREATE TABLE responsibility_alerts (alert_id TEXT, timestamp_ns INTEGER,
            decision TEXT, severity TEXT, title TEXT, run_id TEXT);
        CREATE TABLE actions (action_id TEXT, action_type TEXT, normalized_args_json TEXT,
            timestamp_ns INTEGER, outcome TEXT, run_id TEXT);
        CREATE TABLE intents (intent_id TEXT, timestamp_ns INTEGER, tool_name TEXT, run_id TEXT);
        CREATE TABLE agent_identities (agent_id TEXT, run_id TEXT);
        CREATE TABLE process_events (process_event_id TEXT, timestamp_ns INTEGER, run_id TEXT);
        CREATE TABLE unrelated_noise (thing TEXT);
    """)
    conn.execute("INSERT INTO runs VALUES (?,?,?,?,?)",
                 (run_id, 1000, "Safe", "Warning", "[]"))
    conn.execute("INSERT INTO responsibility_alerts VALUES (?,?,?,?,?,?)",
                 ("alert-1", 1100, "Uncertain", "Warning", "boundary", run_id))
    other = "other-run" if mixed_run else run_id
    conn.execute("INSERT INTO actions VALUES (?,?,?,?,?,?)",
                 ("action-1", "FileOpen", '[{"Path":"/etc/passwd"}]', 1200, "succeeded", run_id))
    conn.execute("INSERT INTO actions VALUES (?,?,?,?,?,?)",
                 ("action-2", "FileOpen", '[{"Path":"/etc/passwd"}]', 1300, "succeeded", other))
    conn.execute("INSERT INTO intents VALUES (?,?,?,?)", ("intent-1", 1250, "Read", run_id))
    conn.execute("INSERT INTO agent_identities VALUES (?,?)", ("agent-a", run_id))
    conn.execute("INSERT INTO agent_identities VALUES (?,?)", ("agent-b", run_id))
    conn.execute("INSERT INTO process_events VALUES (?,?,?)", ("process-1", 1400, run_id))
    conn.execute("INSERT INTO unrelated_noise VALUES ('ignored')")
    conn.commit()
    conn.close()
    return path


def test_export_is_deterministic_and_anchored(tmp_path):
    db = make_db(tmp_path / "collector" / "finalized.db")
    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"
    _, events, actors = export_collector_evidence(db, first)
    export_collector_evidence(db, second)
    assert first.read_bytes() == second.read_bytes()
    assert actors == ["agent-a", "agent-b"]
    assert len(events) == 8  # run, alert, two actions, intent, two agents, one process row
    raw = first.read_bytes()
    ids = set()
    for event in events:
        ids.add(event["event_id"])
        segment = raw[event["byte_offset"]:event["byte_offset"] + event["byte_length"]]
        assert hashlib.sha256(segment).hexdigest() == event["record_sha256"]
        line = json.loads(segment)
        assert line["table"] in {"runs", "responsibility_alerts", "actions", "intents",
                                 "agent_identities", "process_events"}
    assert len(ids) == len(events)
    assert all(event["source_id"] == "collector" for event in events)
    assert all(event["clock_domain"] == "collector-unix-ns" for event in events)
    unknown = [event for event in events if event["timestamp"] == "unknown"]
    assert unknown and all(event["event_id"].startswith("agent_identities:")
                           for event in unknown)
    assert "unrelated_noise" not in first.read_text()


def test_mixed_runs_and_unfinalized_wal_are_refused(tmp_path):
    db = make_db(tmp_path / "collector" / "finalized.db", mixed_run=True)
    with pytest.raises(ValueError, match="mixes run ids"):
        export_collector_evidence(db, tmp_path / "out.jsonl")
    wal = db.with_name(db.name + "-wal")
    wal.write_bytes(b"not empty")
    with pytest.raises(ValueError, match="WAL"):
        export_collector_evidence(db, tmp_path / "out2.jsonl")


def test_import_auto_discovers_the_sibling_collector_snapshot(tmp_path, inputs):
    spec, ledger, rows = inputs
    make_db(ledger.parent / "collector" / "finalized.db")
    bundle = import_runner_bundle(tmp_path / "bundle", release_root=spec["release_root"],
                                  ledger=ledger, codebook_version=spec["binding"]["codebook_version"])
    assert bundle["binding"] == spec["binding"]
    assert set(bundle["sources"]) == {"runner", "collector"}
    assert bundle["sources"]["collector"]["kind"] == "raw_capture"
    assert len(bundle["events"]) > len(rows)
    assert {"agent-a", "agent-b"} <= set(bundle["actors"])
    assert all(event["source_id"] in {"runner", "collector"} for event in bundle["events"])
    assert load_bundle(tmp_path / "bundle") == bundle


def test_invalid_collector_database_fails_without_partial_bundle(tmp_path, inputs):
    spec, ledger, _ = inputs
    db = ledger.parent / "collector" / "finalized.db"
    db.parent.mkdir(parents=True)
    db.write_bytes(b"not sqlite")
    with pytest.raises((ValueError, sqlite3.Error)):
        import_runner_bundle(tmp_path / "bundle", release_root=spec["release_root"],
                             ledger=ledger, codebook_version=spec["binding"]["codebook_version"])
    assert not (tmp_path / "bundle").exists()
