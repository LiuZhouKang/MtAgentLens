# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Corrupt FPR ledgers must fail loudly instead of silently re-running scenarios."""

import json

import pytest

from scripts.wire.fpr_runner import completed_scenarios


def write(path, records):
    path.write_text("".join(json.dumps(record) + "\n" for record in records))


def test_only_completed_runs_with_a_canonical_verdict_count_as_done(tmp_path):
    path = tmp_path / "results.jsonl"
    write(path, [
        {"scenario_id": "done", "returncode": 0, "alerts_total": 1, "final_decision": "Safe"},
        {"scenario_id": "done-review", "returncode": 0, "alerts_total": 2,
         "final_decision": "Uncertain"},
        {"scenario_id": "no-verdict", "returncode": 0, "alerts_total": 1},
        {"scenario_id": "failed", "returncode": 1, "alerts_total": None},
        {"scenario_id": "no-db", "returncode": 0, "alerts_total": None,
         "final_decision": None},
        {"scenario_id": "interrupted", "state": "interrupted", "observed": False},
    ])
    # A row without a canonical verdict was read mid-finalisation: retry it.
    assert completed_scenarios(path) == {"done", "done-review"}


def test_missing_results_file_means_nothing_is_done(tmp_path):
    assert completed_scenarios(tmp_path / "absent.jsonl") == set()


def test_malformed_line_fails_instead_of_being_skipped(tmp_path):
    path = tmp_path / "results.jsonl"
    path.write_text('{"scenario_id":"done","returncode":0,"alerts_total":0}\n{not json}\n')
    with pytest.raises(ValueError, match="line 2"):
        completed_scenarios(path)


def test_line_without_scenario_identity_fails(tmp_path):
    path = tmp_path / "results.jsonl"
    path.write_text('{"returncode":0,"alerts_total":0}\n')
    with pytest.raises(ValueError, match="scenario id"):
        completed_scenarios(path)


def test_scenario_command_separates_the_two_observation_arms():
    """A review rate measured with the Observer enabled is not the deterministic FPR."""
    from scripts.wire.fpr_runner import scenario_command

    base = scenario_command("benign-api-doc", "process", agent_binary=None, agent_comm=None)
    assert base[:7] == ["mtagentlens-benchmark", "run", "--scenario", "benign-api-doc",
                        "--mode", "process", "--collector"]
    assert "--clean-profile" in base
    assert "--disable-responsibility-observer" not in base
    assert "--agent-binary" not in base

    core = scenario_command("benign-api-doc", "process", agent_binary="mtl-1",
                            agent_comm="mtl-1", disable_observer=True)
    assert "--disable-responsibility-observer" in core
    assert core[core.index("--agent-binary") + 1] == "mtl-1"
    assert core[core.index("--agent-comm") + 1] == "mtl-1"


def test_observation_state_reports_unknown_instead_of_assuming_healthy(tmp_path):
    """Metric eligibility must come from the frozen checkpoint, never from optimism."""
    import sqlite3

    from scripts.wire.fpr_runner import observation_state

    assert observation_state(None) == {"run_id": None, "capture_quality_status": "unknown",
                                       "evaluation_status": "unobserved"}
    unreadable = sqlite3.connect(tmp_path / "runs.db")
    unreadable.execute("CREATE TABLE unrelated(x)")
    unreadable.commit()
    unreadable.close()
    state = observation_state(tmp_path / "runs.db")
    assert state["capture_quality_status"] == "unknown"
    assert "artifact_error" in state


def test_verdict_state_only_accepts_canonical_verdicts(tmp_path):
    """Polling helper: NULL, missing table and junk values are not verdicts."""
    import sqlite3

    from scripts.wire.fpr_runner import verdict_state, wait_for_verdict

    db = tmp_path / "runs.db"
    connection = sqlite3.connect(db)
    connection.execute("CREATE TABLE runs(run_id TEXT, final_decision TEXT)")
    connection.execute("INSERT INTO runs VALUES ('r1', NULL)")
    connection.commit()
    assert verdict_state(db) is None
    assert wait_for_verdict(db, timeout=0.3) is False
    connection.execute("UPDATE runs SET final_decision='Uncertain'")
    connection.commit()
    connection.close()
    assert verdict_state(db) == "Uncertain"
    assert wait_for_verdict(db, timeout=1.0) is True
    assert verdict_state(tmp_path / "absent.db") is None
