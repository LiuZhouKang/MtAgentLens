"""RC run selection: first valid attempt wins, invalid attempts stay visible."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.evaluation.select_valid_runs import is_valid, select  # noqa: E402


def _row(scenario, *, state="completed", capture="healthy", decision="Safe", db="/db"):
    return {
        "scenario_id": scenario, "state": state, "capture_quality_status": capture,
        "final_decision": decision, "db_path": db, "attempt_id": scenario + "-attempt",
    }


def test_first_valid_attempt_wins_and_void_attempts_are_recorded(tmp_path):
    first = tmp_path / "b1.jsonl"
    second = tmp_path / "b2.jsonl"
    first.write_text("\n".join(json.dumps(row) for row in [
        _row("case-a", capture="degraded"),
        _row("case-b"),
    ]) + "\n", encoding="utf-8")
    second.write_text("\n".join(json.dumps(row) for row in [
        _row("case-a"),
        _row("case-b", decision="Unsafe"),
    ]) + "\n", encoding="utf-8")
    selected, void = select([first, second])
    assert [row["scenario_id"] for row in selected] == ["case-a", "case-b"]
    assert selected[0]["decision" if False else "final_decision"] == "Safe"
    assert [row["scenario_id"] for row in void] == ["case-a"]
    assert void[0]["capture_quality_status"] == "degraded"


def test_missing_verdict_or_database_is_not_valid():
    assert not is_valid(_row("x", decision=None))
    assert not is_valid(_row("x", db=None))
    assert not is_valid(_row("x", state="failed"))
