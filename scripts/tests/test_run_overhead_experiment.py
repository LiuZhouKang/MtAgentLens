# SPDX-License-Identifier: Apache-2.0 OR MIT
import json
import pytest
from scripts.evaluation import run_overhead_experiment as experiment


def test_forced_kill_reserves_one_censored_observer_request(tmp_path):
    directory=tmp_path/'study-old'/'formal'/'c16-r1-C3'
    directory.mkdir(parents=True)
    (directory/'measurement-evidence.json').write_text(json.dumps({
        'cost_upper_bound_usd':.03,'observer':{'attempts':2}}))
    (directory/'result.json').write_text(json.dumps({
        'profile':'C3','collector':{'forced_kill':True}}))
    rows=experiment.prior_cost_rows(tmp_path)
    assert rows[0]['cost_upper_bound_usd']==pytest.approx(.34)
    assert rows[0]['censored_request_reserve_usd']==.31


def test_budget_refuses_unknown_attempt_cost():
    assert experiment.check_budget([{'cost_upper_bound_usd': .1}], .2) == pytest.approx(.1)
    with pytest.raises(ValueError, match='unknown'):
        experiment.check_budget([{'cost_upper_bound_usd': None}], 5)
    with pytest.raises(ValueError, match='budget'):
        experiment.check_budget([{'cost_upper_bound_usd': .3}], .2)


def test_environment_parser_does_not_execute_shell_or_export_password(tmp_path):
    p = tmp_path/'config.env'
    p.write_text("ANTHROPIC_MODEL='deepseek-flash'\nAGENTSIGHT_SUDO_PASSWORD=private\nOTHER=$(echo no)\n")
    result = experiment.load_vendor_env(p, {})
    assert result['ANTHROPIC_MODEL'] == 'deepseek-flash'
    assert 'AGENTSIGHT_SUDO_PASSWORD' not in result
    assert 'OTHER' not in result


def test_full_profile_requires_real_observer_and_linked_alert():
    result={'profile':'C3','status':'completed','controlled_capture':{'estimable':True}}
    supplement={'observer':{'attempt_records':[]},'alert_latency':{'linked_operations':0}}
    with pytest.raises(ValueError,match='Observer'):
        experiment.validate_measurement(result,supplement)
    supplement['observer']['attempt_records']=[{'success':True,'usage':{'input_tokens':1}}]
    with pytest.raises(ValueError,match='linked'):
        experiment.validate_measurement(result,supplement)
    supplement['alert_latency']['linked_operations']=1
    experiment.validate_measurement(result,supplement)


def test_recovery_plan_changes_only_timeout_and_rehashes(tmp_path):
    from scripts.evaluation import overhead_driver as driver
    frozen = {"timeout_s": 300.0, "out": str(tmp_path / "formal"),
              "condition_runs": [{"condition_id": "c32-r3-C3", "profile": "C3"}]}
    frozen["plan_sha256"] = driver.sha256_bytes(driver.canonical(frozen).encode())
    original = json.loads(json.dumps(frozen))
    plan = experiment.build_recovery_plan(frozen, 600)
    assert plan["timeout_s"] == 600.0
    assert plan["condition_runs"] == original["condition_runs"]
    assert plan["plan_sha256"] != original["plan_sha256"]
    assert frozen == original
    stored = plan.pop("plan_sha256")
    assert driver.sha256_bytes(driver.canonical(plan).encode()) == stored
    with pytest.raises(ValueError):
        experiment.build_recovery_plan(frozen, 0)


def test_recovery_targets_cover_failed_rows_in_plan_order():
    plan = {"condition_runs": [{"condition_id": "a"}, {"condition_id": "b"},
                               {"condition_id": "c"}]}
    execution = {"results": [{"condition_id": "a", "status": "completed"},
                             {"condition_id": "c", "status": "failed"}]}
    assert experiment.recovery_targets(execution, plan) == ["b", "c"]


def test_archive_condition_moves_failed_attempt_out_of_formal(tmp_path):
    study = tmp_path / "study"
    (study / "formal" / "c32-r3-C3").mkdir(parents=True)
    (study / "formal" / "c32-r3-C3" / "result.json").write_text("{}")
    moved = experiment.archive_condition(study, "c32-r3-C3", "20261028T180000Z")
    assert moved == study / "formal-attempts" / "c32-r3-C3-20261028T180000Z"
    assert (moved / "result.json").is_file()
    assert not (study / "formal" / "c32-r3-C3").exists()
    assert experiment.archive_condition(study, "c1-r1-C0", "x") is None


def test_execution_report_merge_replaces_attempt_and_preserves_cost():
    execution = {
        "conditions_expected": 2, "conditions_completed": 2, "complete": False,
        "failure": "forced kill", "known_cost_upper_bound_usd": 1.5,
        "results": [{"condition_id": "a", "status": "completed"},
                    {"condition_id": "b", "status": "failed", "error": "forced kill"}]}
    recovered = [{"condition_id": "b", "status": "completed", "error": None}]
    meta = {"recovery_plan_sha256": "new", "archived_attempts": [{"condition_id": "b"}]}
    report = experiment.update_execution_report(execution, recovered, meta, 0.25)
    assert report["complete"] is True
    assert report["failure"] is None
    assert report["conditions_completed"] == 2
    assert report["results"][1]["status"] == "completed"
    assert report["known_cost_upper_bound_usd"] == pytest.approx(1.75)
    assert report["recovery"] == meta
    assert execution["results"][1]["status"] == "failed"
