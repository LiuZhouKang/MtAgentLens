# SPDX-License-Identifier: Apache-2.0 OR MIT
"""E5 evaluator: ground truth comes from the spec, unattributed is never a merge."""

import json
from pathlib import Path

import pytest

from scripts.evaluation.e5_identity_microbenchmark import (
    SUMMARY_SCHEMA,
    evaluate_trace,
    load_traces,
    summarize,
)


def trace(topology: str, events: list[tuple[str, str, str | None]]):
    return {"schema": "mtagentlens.e5-identity-trace/1", "trace_id": f"{topology}#0",
            "topology": topology, "unresolved_processes": 0, "subagent_boundaries": 0,
            "events": [{"event_id": eid, "truth_actor": truth, "predicted_actor": predicted}
                       for eid, truth, predicted in events]}


def test_a_correct_merge_scores_one():
    row = evaluate_trace(trace("T1_single_agent", [
        ("a", "agent-A", "x"), ("b", "agent-A", "x"), ("c", "agent-A", "x")]))
    assert row["metrics"]["pairwise"]["f1"] == 1.0
    assert row["metrics"]["merged_predicted_clusters"] == 0


def test_a_silent_merge_of_two_actors_is_visible():
    row = evaluate_trace(trace("T4_connection_reuse", [
        ("a", "agent-A", "x"), ("b", "agent-B", "x")]))
    assert row["metrics"]["pairwise"]["precision"] == 0.0
    assert row["metrics"]["pairwise"]["recall"] is None
    assert row["metrics"]["merged_predicted_clusters"] == 1


def test_unattributed_events_are_singletons_not_merges():
    row = evaluate_trace(trace("T2_same_process_sequential", [
        ("a", "agent-A", None), ("b", "agent-B", None)]))
    assert row["unattributed_events"] == 2
    assert row["metrics"]["merged_predicted_clusters"] == 0
    assert row["metrics"]["split_truth_clusters"] == 0


def test_a_split_of_one_actor_is_visible():
    row = evaluate_trace(trace("T5_subagent_fork", [
        ("a", "agent-A", "x"), ("b", "agent-A", "y")]))
    assert row["metrics"]["split_truth_clusters"] == 1


def test_summary_averages_per_topology():
    rows = [evaluate_trace(trace("T1_single_agent", [("a", "A", "x"), ("b", "A", "x")])),
            evaluate_trace(trace("T1_single_agent", [("a", "A", "x"), ("b", "A", "y")]))]
    summary = summarize(rows)
    assert summary["schema"] == SUMMARY_SCHEMA
    entry = summary["topologies"][0]
    assert entry["topology"] == "T1_single_agent"
    assert entry["pairwise_f1"] == pytest.approx(0.5)
    assert entry["traces"] == 2


def test_loader_rejects_foreign_schema(tmp_path: Path):
    path = tmp_path / "traces.jsonl"
    path.write_text(json.dumps({"schema": "other"}) + "\n")
    with pytest.raises(ValueError, match="unexpected schema"):
        load_traces(path)


def test_loader_requires_at_least_one_trace(tmp_path: Path):
    path = tmp_path / "empty.jsonl"
    path.write_text("\n")
    with pytest.raises(ValueError, match="no traces"):
        load_traces(path)
