from __future__ import annotations

import sqlite3
from pathlib import Path

from compare.scripts.audit.audit_trace_redaction import audit_trace_redaction


def test_trace_redaction_audit_blocks_public_release_for_raw_local_paths(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset"
    trace_dir = dataset / "traces" / "benign" / "sample"
    trace_dir.mkdir(parents=True)
    db_path = trace_dir / "responsibility.db"
    _write_db(db_path, text="/home/lzk/MtAgentLens/env/scenarios/fixtures/benign/sample/docs.md")

    artifact = audit_trace_redaction(dataset_root=dataset)

    assert artifact["schema"] == "mtagentlens.trace_redaction_audit.v1"
    assert artifact["status"] == "blocked_for_public_release"
    assert artifact["summary"]["database_count"] == 1
    assert artifact["summary"]["integrity_failures"] == 0
    assert artifact["summary"]["local_path_findings"] == 1
    assert artifact["summary"]["secret_like_findings"] == 0
    assert artifact["summary"]["public_release_ready"] is False
    assert artifact["manual_qa_required"] is True

    finding = artifact["findings"][0]
    assert finding["kind"] == "local_path"
    assert finding["severity"] == "public_release_blocker"
    assert finding["table"] == "actions"
    assert finding["column"] == "normalized_args_json"
    assert "PROJECT_ROOT" in finding["redaction_hint"]


def test_trace_redaction_audit_allows_sanitized_minimal_db(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset"
    trace_dir = dataset / "traces" / "benign" / "sample"
    trace_dir.mkdir(parents=True)
    db_path = trace_dir / "responsibility.db"
    _write_db(db_path, text='[{"Path":"${PROJECT_ROOT}/env/scenarios/fixtures/sample/docs.md"}]')

    artifact = audit_trace_redaction(dataset_root=dataset)

    assert artifact["status"] == "passed_machine_scan"
    assert artifact["summary"]["public_release_ready"] is True
    assert artifact["findings"] == []


def _write_db(path: Path, *, text: str) -> None:
    con = sqlite3.connect(path)
    try:
        con.execute("CREATE TABLE schema_version (version INTEGER)")
        con.execute("INSERT INTO schema_version VALUES (2)")
        con.execute("CREATE TABLE runs (run_id TEXT PRIMARY KEY)")
        con.execute("INSERT INTO runs VALUES ('run-1')")
        con.execute("CREATE TABLE process_events (id TEXT)")
        con.execute("INSERT INTO process_events VALUES ('p')")
        con.execute("CREATE TABLE actions (normalized_args_json TEXT)")
        con.execute("INSERT INTO actions VALUES (?)", (text,))
        con.execute("CREATE TABLE responsibility_claims (claim_id TEXT)")
        con.commit()
    finally:
        con.close()
