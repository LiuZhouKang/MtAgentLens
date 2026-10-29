# SPDX-License-Identifier: Apache-2.0 OR MIT
"""E2: the activity baseline must be honest about what volume alone buys."""

import json
from pathlib import Path

import pytest

from scripts.evaluation.e2_pbc_activity import (
    FACTS_SCHEMA,
    activity_baseline,
    load_arm,
    rank_auroc,
    summarize,
)


def facts(pbc: int, verdict: str) -> dict:
    return {"schema": FACTS_SCHEMA, "run_id": "r", "pbc_edges": pbc, "verdict": verdict}


def test_activity_baseline_counts_hits_and_false_alarms():
    attack = [facts(12, "Unsafe"), facts(0, "Safe")]
    benign = [facts(3, "Safe"), facts(15, "Safe")]
    baseline = activity_baseline(attack, benign, threshold=10)
    assert baseline["tpr"] == 1.0
    assert baseline["fpr"] == pytest.approx(0.5)
    assert baseline["alarms"] == {"attack": 1, "benign": 1}


def test_rank_auroc_handles_ties_as_half():
    attack = [facts(5, "Safe")]
    benign = [facts(5, "Safe")]
    assert rank_auroc(attack, benign) == 0.5
    assert rank_auroc([facts(6, "Safe")], [facts(1, "Safe")]) == 1.0


def test_summary_strata_partition_every_run():
    attack = [facts(0, "Safe"), facts(2, "Unsafe"), facts(15, "Uncertain"), facts(40, "Safe")]
    benign = [facts(0, "Safe"), facts(0, "Safe")]
    summary = summarize(attack, benign)
    assert summary["attack"]["runs"] == 4
    assert sum(s["runs"] for s in summary["attack"]["distribution"]["strata"]) == 4
    assert sum(s["runs"] for s in summary["benign"]["distribution"]["strata"]) == 2
    assert summary["attack"]["distribution"]["min"] == 0
    assert summary["attack"]["distribution"]["max"] == 40


def test_loader_rejects_foreign_schema_or_verdict(tmp_path: Path):
    path = tmp_path / "facts.jsonl"
    path.write_text(json.dumps({"schema": "x"}) + "\n")
    with pytest.raises(ValueError, match="unexpected schema"):
        load_arm(path)
    path.write_text(json.dumps({"schema": FACTS_SCHEMA, "verdict": "Maybe"}) + "\n")
    with pytest.raises(ValueError, match="unexpected verdict"):
        load_arm(path)
