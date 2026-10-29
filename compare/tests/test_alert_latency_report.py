# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Alert latency: defined from recorded evidence, never as per-event alignment."""

import json
import sqlite3
from pathlib import Path

import pytest

from scripts.evaluation.alert_latency_report import (
    REPORT_SCHEMA,
    load_ledger,
    measure_run,
    percentile,
    summarize,
)


def make_db(path: Path, *, evidence_ts=1_000_000_000_000, alert_ts=1_011_000_000_000,
            ended_ts=1_012_000_000_000) -> Path:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE runs (started_at_ns INTEGER, ended_at_ns INTEGER);
        CREATE TABLE responsibility_alerts (timestamp_ns INTEGER);
        CREATE TABLE correlation_evidence (timestamp_ns INTEGER);
        CREATE TABLE actions (timestamp_ns INTEGER);
        """)
    connection.execute("INSERT INTO runs VALUES (?, ?)", (900, ended_ts))
    connection.execute("INSERT INTO responsibility_alerts VALUES (?)", (alert_ts,))
    connection.execute("INSERT INTO correlation_evidence VALUES (?)", (evidence_ts,))
    connection.commit()
    connection.close()
    return path


def test_latency_is_alert_minus_last_evidence(tmp_path: Path):
    db = make_db(tmp_path / "run.db")
    outcome = measure_run(db)
    assert outcome["decision_latency_s"] == pytest.approx(11.0)
    assert outcome["finalization_latency_s"] == pytest.approx(1.0)
    assert outcome["alerts"] == 1


def test_evidence_after_the_alert_is_not_used(tmp_path: Path):
    db = make_db(tmp_path / "run.db", evidence_ts=2_000_000_000_000)
    assert measure_run(db) == {"excluded": "no_evidence_before_alert"}


def test_missing_or_empty_inputs_are_excluded_not_zeroed(tmp_path: Path):
    assert measure_run(tmp_path / "missing.db") == {"excluded": "missing_db"}
    db = make_db(tmp_path / "run.db")
    rows = [{"db_path": str(db), "final_decision": "Safe", "state": "ok"},
            {"db_path": str(db), "final_decision": None, "state": "failed"},
            {"db_path": str(tmp_path / "missing.db"), "final_decision": "Safe", "state": "ok"}]
    summary = summarize(rows)
    assert summary["measured_runs"] == 1
    assert summary["excluded"] == {"missing_db": 1, "no_verdict": 1}


def test_percentiles_are_nearest_rank():
    assert percentile([], 0.5) is None
    assert percentile([1.0, 2.0, 3.0, 4.0], 0.50) == 2.0
    assert percentile([1.0, 2.0, 3.0, 4.0], 0.95) == 4.0


def test_loader_and_schema(tmp_path: Path):
    ledger = tmp_path / "results.jsonl"
    ledger.write_text(json.dumps({"db_path": "/none", "final_decision": "Safe"}) + "\n")
    rows = load_ledger(ledger)
    assert len(rows) == 1
    empty = tmp_path / "empty.jsonl"
    empty.write_text("")
    with pytest.raises(ValueError, match="no rows"):
        load_ledger(empty)
    from scripts.evaluation.alert_latency_report import main
    out = tmp_path / "report.json"
    assert main(["--results", f"attack={ledger}", "--out", str(out)]) == 0
    payload = json.loads(out.read_text())
    assert payload["schema"] == REPORT_SCHEMA
    assert "not_event_alignment" in payload["definition"]
