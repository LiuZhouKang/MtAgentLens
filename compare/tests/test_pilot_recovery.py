# SPDX-License-Identifier: Apache-2.0 OR MIT
"""A killed pilot parent must not lose its unrecorded attempt from the denominator."""

import json

import pytest

from compare.scripts.run import pilot_observer as pilot


def pending(attempt_id="attempt-1", scenario_id="scenario"):
    return pilot.pending_attempt(
        attempt_id=attempt_id,
        scenario_id=scenario_id,
        mode="single_agent",
        observer="on",
        release_id="release",
        case_binding={"label": "attack", "case_id": "case-1"},
        collector_sha256="c" * 64,
        configuration_sha256="d" * 64,
        runtime_inputs="/tmp/runtime",
        isolation={"agent_binary": "/tmp/agent", "agent_comm": "agent"},
    )


def test_pending_attempt_without_final_record_becomes_interrupted(tmp_path):
    journal = tmp_path / "attempts.pending.jsonl"
    results = tmp_path / "results.jsonl"
    pilot.journal_add(journal, pending())
    recovered = pilot.reconcile_attempts(journal, results)
    assert len(recovered) == 1
    row = recovered[0]
    assert row["attempt_id"] == "attempt-1"
    assert row["state"] == "interrupted"
    assert row["scenario_id"] == "scenario"
    assert row["label"] == "attack"
    assert row["final_decision"] is None
    assert row["observed"] is False
    assert not journal.exists()
    assert len(json.loads(json.dumps(row))) == len(row)
    assert pilot.reconcile_attempts(journal, results) == []
    assert len(results.read_text().splitlines()) == 1


def test_final_record_closes_the_journal_entry_without_duplicating(tmp_path):
    journal = tmp_path / "attempts.pending.jsonl"
    results = tmp_path / "results.jsonl"
    pilot.journal_add(journal, pending())
    pilot.append_record(results, {"attempt_id": "attempt-1", "state": "completed",
                                  "final_decision": "Safe"})
    assert pilot.reconcile_attempts(journal, results) == []
    assert len(results.read_text().splitlines()) == 1
    assert not journal.exists()


def test_only_the_unrecorded_attempt_is_recovered(tmp_path):
    journal = tmp_path / "attempts.pending.jsonl"
    results = tmp_path / "results.jsonl"
    pilot.journal_add(journal, pending("a-1"))
    pilot.journal_add(journal, pending("a-2", "other"))
    pilot.append_record(results, {"attempt_id": "a-1", "state": "completed"})
    recovered = pilot.reconcile_attempts(journal, results)
    assert [row["attempt_id"] for row in recovered] == ["a-2"]
    assert json.loads(results.read_text().splitlines()[-1])["attempt_id"] == "a-2"


def test_historical_result_without_state_counts_as_final(tmp_path):
    journal = tmp_path / "attempts.pending.jsonl"
    results = tmp_path / "results.jsonl"
    pilot.append_record(results, {"attempt_id": "legacy", "final_decision": "Safe"})
    pilot.journal_add(journal, pending("legacy"))
    assert pilot.reconcile_attempts(journal, results) == []
    assert len(results.read_text().splitlines()) == 1


def test_malformed_journal_stops_recovery_and_keeps_the_file(tmp_path):
    journal = tmp_path / "attempts.pending.jsonl"
    journal.write_text("{not json}\n")
    with pytest.raises(ValueError, match="journal"):
        pilot.reconcile_attempts(journal, tmp_path / "results.jsonl")
    assert journal.exists()


def test_journal_remove_rewrites_atomically_and_drops_only_the_id(tmp_path):
    journal = tmp_path / "attempts.pending.jsonl"
    pilot.journal_add(journal, pending("a-1"))
    pilot.journal_add(journal, pending("a-2"))
    pilot.journal_remove(journal, "a-1")
    remaining = [json.loads(line)["attempt_id"] for line in journal.read_text().splitlines()]
    assert remaining == ["a-2"]
    pilot.journal_remove(journal, "missing")
    assert [json.loads(line)["attempt_id"] for line in journal.read_text().splitlines()] == ["a-2"]
    assert not (tmp_path / "attempts.pending.jsonl.tmp").exists()
