"""Unsupported or absent truth must never become a perfect evaluation."""
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from mtagentlens_benchmark.annotator.ground_truth import (
    load_ground_truth,
    save_ground_truth,
)
from mtagentlens_benchmark.cli import main
from mtagentlens_benchmark.evaluator.metrics import (
    compute_benchmark_score,
    compute_causal_chain_metrics,
    compute_detection_metrics,
    compute_false_positive_metrics,
    compute_identity_metrics,
    compute_robustness_metrics,
)
from mtagentlens_benchmark.types import GroundTruth


def alert_truth():
    return {
        "scenario_id": "case",
        "annotator": "reviewer",
        "annotation_date": "2026-10-23",
        "alerts": [{
            "alert_id": "alert-1",
            "severity": "Warning",
            "category": "test",
            "causal_chain": [],
            "agent_id": "actor-1",
            "confidence": "certain",
            "evidence_refs": [1],
        }],
    }


def write_truth(tmp_path, data):
    path = tmp_path / "case.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.mark.parametrize("data", [
    {"schema": "mtagentlens.ground_truth.v4", "scenario_id": "case",
     "expected_behaviors": [{"pattern": "independent scenario expectation"}]},
    {"schema": "unknown.v99", **alert_truth()},
    {"schema": None, **alert_truth()},
    {"scenario_id": "case"},
    {"scenario_id": "case", "expected_behaviors": []},
    {"scenario_id": "case", "alerts": None},
    {"scenario_id": "case", "alerts": {}},
    [],
])
def test_loader_rejects_unsupported_or_malformed_truth(tmp_path, data):
    with pytest.raises(ValueError):
        load_ground_truth(write_truth(tmp_path, data))


def test_real_v4_scenario_is_not_silently_loaded_as_runtime_truth():
    source = (Path(__file__).parents[2] / "datasets/FuxiBench/ground_truth"
              / "prompt_injection/prompt-injection-classical.json")
    assert json.loads(source.read_text())["expected_behaviors"]
    with pytest.raises(ValueError, match="v4"):
        load_ground_truth(source)


@pytest.mark.parametrize("mutate", [
    lambda d: d["alerts"].append(dict(d["alerts"][0])),
    lambda d: d["alerts"][0].update(alert_id=""),
    lambda d: d["alerts"][0].update(alert_id="  "),
    lambda d: d["alerts"][0].update(agent_id=None),
    lambda d: d["alerts"][0].update(agent_id="  "),
    lambda d: d["alerts"][0].update(causal_chain="not a chain"),
    lambda d: d["alerts"][0].update(evidence_refs="not references"),
    lambda d: d["alerts"][0].update(evidence_refs=[True]),
    lambda d: d["alerts"][0].pop("confidence"),
    lambda d: d["alerts"].append("not an annotation"),
    lambda d: d.update(scenario_id=""),
])
def test_invalid_annotations_are_not_completed_with_invented_defaults(tmp_path, mutate):
    data = alert_truth()
    mutate(data)
    with pytest.raises(ValueError):
        load_ground_truth(write_truth(tmp_path, data))


def test_explicit_legacy_annotations_round_trip(tmp_path):
    gt = load_ground_truth(write_truth(tmp_path, alert_truth()))
    target = tmp_path / "saved.json"
    save_ground_truth(gt, target)
    assert load_ground_truth(target) == gt


def test_explicit_no_alert_annotation_is_readable_but_not_a_chain_score(tmp_path):
    data = alert_truth()
    data["alerts"] = []
    gt = load_ground_truth(write_truth(tmp_path, data))
    assert gt.alerts == []
    db = tmp_path / "must-not-be-created.db"
    with pytest.raises(ValueError, match="(?i)(truth|annotat)"):
        compute_causal_chain_metrics(db, gt)
    assert not db.exists()


@pytest.mark.parametrize("has_result", [False, True])
def test_score_rejects_empty_or_missing_inputs_instead_of_skipping(tmp_path, has_result):
    db = tmp_path / "missing.db"
    gt = load_ground_truth(write_truth(tmp_path, alert_truth()))
    with pytest.raises(ValueError):
        compute_benchmark_score([(db, gt)] if has_result else [], [])
    assert not db.exists()


def test_score_rejects_empty_truth_even_when_database_exists(tmp_path):
    db = tmp_path / "existing.db"
    db.touch()
    gt = GroundTruth("case", "reviewer", "2026-10-23", [])
    with pytest.raises(ValueError, match="(?i)(truth|annotat)"):
        compute_benchmark_score([(db, gt)], [])


@pytest.mark.parametrize("metric", [
    compute_detection_metrics, compute_causal_chain_metrics, compute_robustness_metrics,
])
def test_metrics_do_not_create_missing_databases(tmp_path, metric):
    gt = load_ground_truth(write_truth(tmp_path, alert_truth()))
    db = tmp_path / "missing.db"
    with pytest.raises(ValueError, match="(?i)database"):
        metric(db, gt)
    assert not db.exists()


def test_identity_metric_does_not_create_missing_database(tmp_path):
    db = tmp_path / "missing.db"
    with pytest.raises(ValueError, match="(?i)database"):
        compute_identity_metrics(db)
    assert not db.exists()


def test_missing_baseline_is_not_silently_assessed_as_zero_fp(tmp_path):
    db = tmp_path / "missing.db"
    with pytest.raises(ValueError, match="(?i)database"):
        compute_false_positive_metrics([(db, "case")])
    assert not db.exists()


@pytest.mark.parametrize("data", [
    {"schema": "mtagentlens.ground_truth.v4", "scenario_id": "case",
     "expected_behaviors": [{"pattern": "expectation"}]},
    {**alert_truth(), "alerts": []},
])
def test_cli_invalid_truth_fails_without_writing_a_report(tmp_path, data):
    gt_dir = tmp_path / "dataset/ground_truth/category"
    gt_dir.mkdir(parents=True)
    (gt_dir / "case.json").write_text(json.dumps(data))
    run = tmp_path / "dataset/traces/category/run"
    run.mkdir(parents=True)
    (run / ".scenario_id").write_text("case")
    (run / "responsibility.db").touch()
    output = tmp_path / "report"
    result = CliRunner().invoke(main, ["evaluate", "-d", str(tmp_path / "dataset"),
                                       "-o", str(output)])
    assert result.exit_code != 0
    assert "Error:" in result.output
    assert "truth" in result.output.lower()
    assert "Overall Score" not in result.output
    assert not output.exists()


@pytest.mark.parametrize("problem", ["missing_id", "unknown_case", "missing_db",
                                      "ambiguous_db", "wrong_truth_id", "duplicate_truth"])
def test_cli_never_silently_skips_an_invalid_run_in_a_mixed_cohort(tmp_path, problem, monkeypatch):
    """A good run must not conceal a second run that cannot be evaluated."""
    from mtagentlens_benchmark.evaluator import metrics
    from mtagentlens_benchmark.types import BenchmarkScore

    calls = []
    monkeypatch.setattr(metrics, "compute_benchmark_score",
                        lambda *args: calls.append(args) or BenchmarkScore())
    dataset = tmp_path / "dataset"
    gt_dir = dataset / "ground_truth/category"
    gt_dir.mkdir(parents=True)
    (gt_dir / "case.json").write_text(json.dumps(alert_truth()))
    for name in ("good", "bad"):
        run = dataset / f"traces/category/{name}"
        run.mkdir(parents=True)
        (run / ".scenario_id").write_text("case")
        (run / "responsibility.db").touch()
    bad = dataset / "traces/category/bad"
    if problem == "missing_id":
        (bad / ".scenario_id").unlink()
    elif problem == "unknown_case":
        (bad / ".scenario_id").write_text("unknown-case")
    elif problem == "missing_db":
        (bad / "responsibility.db").unlink()
    elif problem == "ambiguous_db":
        (bad / "other.db").touch()
    elif problem == "wrong_truth_id":
        (gt_dir / "case.json").write_text(json.dumps({**alert_truth(), "scenario_id": "different"}))
    elif problem == "duplicate_truth":
        second = dataset / "ground_truth/another_category"
        second.mkdir()
        (second / "case.json").write_text(json.dumps(alert_truth()))
    output = tmp_path / "report"
    result = CliRunner().invoke(main, ["evaluate", "-d", str(dataset), "-o", str(output)])
    assert result.exit_code != 0, result.output
    assert "Error:" in result.output
    assert "Overall Score" not in result.output
    assert not calls
    assert not output.exists()


def test_cli_empty_database_reports_a_data_error_without_a_report(tmp_path):
    gt_dir = tmp_path / "dataset/ground_truth/category"
    gt_dir.mkdir(parents=True)
    (gt_dir / "case.json").write_text(json.dumps(alert_truth()))
    run = tmp_path / "dataset/traces/category/run"
    run.mkdir(parents=True)
    (run / ".scenario_id").write_text("case")
    (run / "responsibility.db").touch()
    output = tmp_path / "report"
    result = CliRunner().invoke(main, ["evaluate", "-d", str(tmp_path / "dataset"),
                                      "-o", str(output)])
    assert result.exit_code != 0
    assert "Error:" in result.output
    assert "database" in result.output
    assert not output.exists()
