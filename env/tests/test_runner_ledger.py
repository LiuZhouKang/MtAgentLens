"""Runner ledger: unobserved is a fact, never an assumed success."""

import json
from pathlib import Path

import pytest

from mtagentlens_benchmark.engine.runner_ledger import (
    LEDGER_SCHEMA,
    STATUS_FAILED,
    STATUS_SUCCEEDED,
    STATUS_UNOBSERVED,
    RunnerLedger,
)


def ledger() -> RunnerLedger:
    return RunnerLedger(session_name="agtlab-test", mode="tmux-live")


def test_launch_and_completion_are_recorded():
    subject = ledger()
    subject.record_launch(agent_index=0, task="scan docs", pane_mode="task",
                          log_file=Path("/run/agent_0.log"), launched_at_ns=100)
    subject.record_completion(agent_index=0, status=STATUS_SUCCEEDED, completed_at_ns=200)
    summary = subject.summary()
    assert summary["agents_total"] == 1
    assert summary["agents_succeeded"] == 1
    assert summary["all_observed"] is True


def test_missing_completion_stays_unobserved():
    subject = ledger()
    subject.record_launch(agent_index=0, task="scan docs", pane_mode="task",
                          log_file=Path("/run/agent_0.log"), launched_at_ns=100)
    summary = subject.summary()
    assert summary["agents_unobserved"] == 1
    assert summary["statuses"][STATUS_UNOBSERVED] == 1
    assert summary["all_observed"] is False


def test_duplicate_launch_and_completion_are_rejected():
    subject = ledger()
    subject.record_launch(agent_index=0, task="t", pane_mode="task",
                          log_file=Path("a.log"), launched_at_ns=100)
    with pytest.raises(ValueError, match="launched twice"):
        subject.record_launch(agent_index=0, task="t", pane_mode="task",
                              log_file=Path("a.log"), launched_at_ns=101)
    subject.record_completion(agent_index=0, status=STATUS_FAILED, completed_at_ns=200)
    with pytest.raises(ValueError, match="completed twice"):
        subject.record_completion(agent_index=0, status=STATUS_FAILED, completed_at_ns=201)


def test_completion_without_launch_or_time_travel_is_rejected():
    subject = ledger()
    with pytest.raises(ValueError, match="without a launch"):
        subject.record_completion(agent_index=3, status=STATUS_SUCCEEDED, completed_at_ns=1)
    subject.record_launch(agent_index=0, task="t", pane_mode="task",
                          log_file=Path("a.log"), launched_at_ns=100)
    with pytest.raises(ValueError, match="before it launched"):
        subject.record_completion(agent_index=0, status=STATUS_SUCCEEDED, completed_at_ns=99)
    with pytest.raises(ValueError, match="unknown completion status"):
        subject.record_completion(agent_index=0, status="maybe", completed_at_ns=101)


def test_write_is_atomic_and_schema_is_stable(tmp_path: Path):
    subject = ledger()
    subject.record_launch(agent_index=1, task="second", pane_mode="task",
                          log_file=Path("b.log"), launched_at_ns=100)
    subject.record_launch(agent_index=0, task="first", pane_mode="task",
                          log_file=Path("a.log"), launched_at_ns=90)
    path = subject.write(tmp_path / "runner-ledger.json")
    payload = json.loads(path.read_text())
    assert payload["schema"] == LEDGER_SCHEMA
    assert [agent["agent_index"] for agent in payload["agents"]] == [0, 1]
    assert not list(tmp_path.glob("*.tmp"))


def test_status_file_mapping_keeps_unobserved_distinct():
    from mtagentlens_benchmark.engine.executor import ledger_status
    from mtagentlens_benchmark.engine.runner_ledger import (
        STATUS_ERROR,
        STATUS_TIMEOUT,
    )

    assert ledger_status(None) == (STATUS_UNOBSERVED, "no status file")
    assert ledger_status({"state": "pending"})[0] == STATUS_UNOBSERVED
    assert ledger_status({"state": "running"})[0] == STATUS_UNOBSERVED
    assert ledger_status({"state": "done", "success": True})[0] == STATUS_SUCCEEDED
    assert ledger_status({"state": "done", "exit_code": 1})[0] == STATUS_FAILED
    assert ledger_status({"state": "timeout"})[0] == STATUS_TIMEOUT
    assert ledger_status({"state": "error", "error": "boom"}) == (STATUS_ERROR, "boom")
