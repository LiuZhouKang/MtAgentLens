# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Observer-cost report: only what the ledger records may be reported."""

import json
from pathlib import Path

import pytest

from scripts.evaluation.observer_cost_report import (
    REPORT_SCHEMA,
    load_rows,
    percentile,
    summarize,
)


def test_percentile_edges():
    assert percentile([], 0.5) is None
    assert percentile([1.0], 0.95) == 1.0
    assert percentile([1.0, 2.0, 3.0, 4.0], 0.50) == 2.0
    assert percentile([1.0, 2.0, 3.0, 4.0], 0.95) == 4.0


def test_summary_reports_counts_and_decisions():
    rows = [
        {"observer_calls": 5, "duration_seconds": 10.0, "final_decision": "Unsafe"},
        {"observer_calls": 0, "duration_seconds": 20.0, "final_decision": "Safe"},
        {"observer_calls": 1, "duration_seconds": 30.0, "final_decision": "Uncertain"},
    ]
    summary = summarize(rows)
    assert summary["runs"] == 3
    assert summary["observer_calls"]["total"] == 6
    assert summary["observer_calls"]["runs_without_calls"] == 1
    assert summary["duration_seconds"]["max"] == 30.0
    assert summary["by_final_decision"]["Unsafe"] == {"runs": 1, "observer_calls": 5}


def test_loader_excludes_runs_without_a_verdict(tmp_path: Path):
    path = tmp_path / "results.jsonl"
    path.write_text(json.dumps({"observer_calls": None, "duration_seconds": 5.0,
                                "final_decision": None, "state": "failed"}) + "\n"
                    + json.dumps({"observer_calls": 1, "duration_seconds": 6.0,
                                  "final_decision": "Safe"}) + "\n")
    rows, excluded = load_rows(path)
    assert excluded == 1
    assert len(rows) == 1


def test_loader_rejects_negative_or_missing_counts(tmp_path: Path):
    path = tmp_path / "results.jsonl"
    path.write_text(json.dumps({"observer_calls": -1, "duration_seconds": 1.0,
                                "final_decision": "Safe"}) + "\n")
    with pytest.raises(ValueError, match="non-negative"):
        load_rows(path)
    path.write_text(json.dumps({"duration_seconds": 1.0, "final_decision": "Safe"}) + "\n")
    with pytest.raises(ValueError, match="observer_calls"):
        load_rows(path)
    path.write_text("")
    with pytest.raises(ValueError, match="no rows with a verdict"):
        load_rows(path)


def test_cli_writes_schema(tmp_path: Path):
    results = tmp_path / "results.jsonl"
    results.write_text(json.dumps({"observer_calls": 2, "duration_seconds": 5.0,
                                   "final_decision": "Safe"}) + "\n")
    out = tmp_path / "out.json"
    import sys
    from scripts.evaluation.observer_cost_report import main
    argv = ["--results", f"attack={results}", "--out", str(out)]
    assert main(argv) == 0
    payload = json.loads(out.read_text())
    assert payload["schema"] == REPORT_SCHEMA
    assert payload["arms"]["attack"]["observer_calls"]["total"] == 2
