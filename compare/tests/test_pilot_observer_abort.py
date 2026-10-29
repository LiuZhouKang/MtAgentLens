# SPDX-License-Identifier: Apache-2.0 OR MIT
"""A campaign must stop when the vendor rejects the agent, not run the rest blind."""

import json
from pathlib import Path

from compare.scripts.run.pilot_observer import (
    detect_fatal_agent_error,
    record_campaign_abort,
)


def run_dir_with_log(tmp_path: Path, text: str) -> Path:
    run_dir = tmp_path / "run"
    (run_dir / "logs").mkdir(parents=True)
    (run_dir / "logs" / "agent_0.log").write_text(text, encoding="utf-8")
    return run_dir


def test_insufficient_balance_is_fatal(tmp_path):
    run_dir = run_dir_with_log(tmp_path, "API Error: 402 Insufficient Balance\n")
    assert detect_fatal_agent_error(run_dir) == "Insufficient Balance"


def test_a_clean_log_is_not_fatal(tmp_path):
    run_dir = run_dir_with_log(tmp_path, "all good\n")
    assert detect_fatal_agent_error(run_dir) is None


def test_missing_logs_is_not_a_false_alarm(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    assert detect_fatal_agent_error(run_dir) is None


def test_abort_marker_records_what_was_not_attempted(tmp_path):
    record_campaign_abort(tmp_path, reason="Insufficient Balance",
                          scenario_id="c2-dns-dead-drop",
                          remaining=["recon-direct", "supplychain-direct"])
    marker = json.loads((tmp_path / "campaign-aborted.json").read_text())
    assert marker["reason"] == "Insufficient Balance"
    assert marker["last_scenario"] == "c2-dns-dead-drop"
    assert marker["remaining_scenarios"] == ["recon-direct", "supplychain-direct"]
