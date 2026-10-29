# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Derived capture quality never rewrites evidence and never assumes health."""

import json
import sqlite3

import pytest

from scripts.evaluation.enrich_campaign_results import enrich, write_ledger


def test_enrichment_keeps_measured_fields_and_marks_unknown(tmp_path):
    db = tmp_path / "run.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE unrelated(x)")
    conn.commit()
    conn.close()
    rows = [{"scenario_id": "benign-a", "db_path": str(db), "final_decision": "Safe",
             "observed": True, "capture_quality_status": "healthy"},
            {"scenario_id": "benign-b", "db_path": None, "final_decision": None,
             "observed": False}]
    enriched = enrich(rows)
    assert [row["scenario_id"] for row in enriched] == ["benign-a", "benign-b"]
    assert enriched[0]["final_decision"] == "Safe"
    assert enriched[0]["observed"] is True
    # A checkpoint that cannot be read is unknown, not healthy.
    assert enriched[0]["capture_quality_status"] == "unknown"
    assert "quality_error" in enriched[0]
    assert enriched[1]["capture_quality_status"] == "unknown"
    assert enriched[1]["evaluation_status"] == "unobserved"


def test_ledger_is_written_once(tmp_path):
    target = tmp_path / "derived.jsonl"
    write_ledger(target, [{"scenario_id": "s", "capture_quality_status": "unknown"}])
    assert json.loads(target.read_text().strip())["scenario_id"] == "s"
    with pytest.raises(ValueError, match="exists"):
        write_ledger(target, [])
