"""Attack-only capacity experiment plan parsing and paid-execution guards."""

import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.evaluation import attack_capacity_experiment as capacity  # noqa: E402


def _triggers():
    return capacity.load_triggers(capacity.DEFAULT_TRIGGERS)


def _cohort():
    return capacity.canonical_cohort(capacity.DEFAULT_RELEASE)


def _ground_truth():
    from compare.tools.detection_scope import included_ground_truth

    return included_ground_truth(capacity.DEFAULT_RELEASE / "ground_truth")


def test_documented_blocks_extracts_bash_blocks_only(tmp_path):
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    (fixture / "docs.md").write_text(
        "# runbook\n```bash\necho one\n```\nprose\n```sh\necho two\n```\n```python\nprint('x')\n```\n",
        encoding="utf-8",
    )
    assert capacity.documented_blocks(fixture) == ["echo one\n", "echo two\n"]


def test_capture_status_reads_checkpoint_payload(tmp_path):
    database = tmp_path / "run.db"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE analysis_checkpoints (payload_json TEXT)")
    connection.execute(
        "INSERT INTO analysis_checkpoints VALUES (?)",
        (json.dumps({"capture_quality": {"status": "healthy"}}),),
    )
    connection.commit()
    connection.close()
    assert capacity.capture_status(database) == "healthy"
    assert capacity.capture_status(tmp_path / "missing.db") is None


def test_trigger_manifest_matches_canonical_single_agent_attack_cohort():
    triggers = _triggers()
    cohort = _cohort()
    assert len(cohort) == 36
    assert set(triggers) == set(cohort)
    assert all(gt.get("label") == "attack" for sid, gt in _ground_truth().items()
               if sid in cohort)
    for scenario, spec in triggers.items():
        assert spec.get("commands") or spec.get("runbook_blocks"), scenario


def test_plan_lists_correct_36_single_agent_attacks():
    plan = capacity.build_plan(capacity.DEFAULT_RELEASE, _triggers(), _cohort())
    assert plan["cohort_size"] == 36
    assert plan["requested"] == sorted(_cohort())
    assert plan["execute"] is False
    assert plan["uses_agent_model"] is False
    assert plan["observer_may_be_paid"] is True
    assert plan["scope"] == "direct_payload_control_not_main_93_llm_agent_campaign"


def test_default_cli_prints_plan_and_never_executes_even_when_paid(tmp_path, monkeypatch, capsys):
    out = tmp_path / "capacity"
    monkeypatch.setenv(capacity.PAID_ENV, "1")
    monkeypatch.setattr(capacity, "run_capacity", lambda *a, **k: pytest.fail("executed"))
    code = capacity.main(["--release", str(capacity.DEFAULT_RELEASE), "--out", str(out)])
    assert code == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["execute"] is False
    assert plan["cohort_size"] == 36
    assert not out.exists()


def test_explicit_execute_denied_without_paid_env(tmp_path, monkeypatch, capsys):
    out = tmp_path / "capacity"
    monkeypatch.delenv(capacity.PAID_ENV, raising=False)
    monkeypatch.setattr(capacity, "run_capacity", lambda *a, **k: pytest.fail("executed"))
    scenario = sorted(_cohort())[0]
    code = capacity.main(["--release", str(capacity.DEFAULT_RELEASE), "--out", str(out),
                          "--execute", "--only", scenario])
    assert code == 2
    assert "MTL_ALLOW_PAID" in capsys.readouterr().err
    assert not out.exists()


def test_direct_run_capacity_requires_both_execute_and_paid_env(tmp_path, monkeypatch):
    out = tmp_path / "capacity"
    scenario = sorted(_cohort())[0]
    with pytest.raises(capacity.CapacityError):
        capacity.run_capacity(capacity.DEFAULT_RELEASE, out, _triggers(), [scenario])
    monkeypatch.delenv(capacity.PAID_ENV, raising=False)
    with pytest.raises(capacity.CapacityError):
        capacity.run_capacity(capacity.DEFAULT_RELEASE, out, _triggers(), [scenario], execute=True)
    assert not out.exists()


def test_benign_and_duplicate_requests_fail_before_side_effects(tmp_path, monkeypatch):
    out = tmp_path / "capacity"
    monkeypatch.setenv(capacity.PAID_ENV, "1")
    benign = sorted(sid for sid, gt in _ground_truth().items() if gt.get("label") == "benign")[0]
    with pytest.raises(capacity.CapacityError):
        capacity.run_capacity(capacity.DEFAULT_RELEASE, out, _triggers(), [benign], execute=True)
    scenario = sorted(_cohort())[0]
    with pytest.raises(capacity.CapacityError):
        capacity.run_capacity(capacity.DEFAULT_RELEASE, out, _triggers(), [scenario, scenario],
                              execute=True)
    assert not out.exists()


def test_multi_agent_attack_request_fails_before_side_effects(tmp_path, monkeypatch):
    out = tmp_path / "capacity"
    monkeypatch.setenv(capacity.PAID_ENV, "1")
    cohort = _cohort()
    multi = sorted(sid for sid, gt in _ground_truth().items()
                   if gt.get("label") == "attack" and sid not in cohort)[0]
    with pytest.raises(capacity.CapacityError):
        capacity.run_capacity(capacity.DEFAULT_RELEASE, out, _triggers(), [multi], execute=True)
    assert not out.exists()
