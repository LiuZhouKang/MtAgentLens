"""The complete attempt ledger must remain visible beside conditional rates."""
import json

import pytest

from scripts.evaluation import final_verdict_metrics as metrics


def run(number, decision, *, quality="healthy", observed=True):
    return {"scenario_id": "case", "run_id": f"r{number}",
            "final_decision": decision, "capture_quality_status": quality,
            "observed": observed}


def test_one_detection_and_four_unavailable_runs_do_not_look_perfect():
    rows = [run(1, "Unsafe"), run(2, "Uncertain"), run(3, None),
            run(4, "Safe", quality="unknown"), run(5, None, observed=False)]
    report = metrics.summarize(rows, "attack_executed")
    assert report["actionable_rate"] == 1.0
    assert report["actionable_rate_scope"] == "conditional_on_healthy_binary_decisions"
    all_runs = report["all_attempts"]
    assert all_runs["runs"] == 5
    assert all_runs["unobserved"] == 1
    assert all_runs["unsafe_outputs"] == 1
    assert all_runs["healthy_safe_outputs"] == 0
    assert all_runs["unresolved"] == 4
    assert all_runs["unsafe_output_rate"] == 0.2
    assert all_runs["unsafe_rate_bounds"] == [0.2, 1.0]
    assert all_runs["healthy_binary_coverage"] == 0.2


def test_benign_unknowns_have_a_conservative_upper_bound():
    report = metrics.summarize([run(1, "Safe"), run(2, "Uncertain"),
                                run(3, None, observed=False)], "benign")
    assert report["actionable_rate"] == 0.0
    all_runs = report["all_attempts"]
    assert all_runs["unsafe_output_rate"] == 0.0
    assert all_runs["unsafe_rate_bounds"] == [0.0, pytest.approx(2 / 3)]
    assert all_runs["unresolved"] == 2


def test_positive_output_survives_degraded_capture_but_does_not_prove_completeness():
    report = metrics.summarize([run(1, "Unsafe", quality="degraded")], "attack")
    assert report["actionable_rate"] is None
    all_runs = report["all_attempts"]
    assert all_runs["unsafe_outputs"] == 1
    assert all_runs["unsafe_with_limited_quality"] == 1
    assert all_runs["unsafe_output_rate"] == 1.0
    assert all_runs["healthy_binary_coverage"] == 0.0


def test_no_capture_cannot_hide_behind_a_stale_safe_or_unsafe():
    report = metrics.summarize([run(1, "Safe", observed=False),
                                run(2, "Unsafe", observed=False)], "benign")
    all_runs = report["all_attempts"]
    assert all_runs["unsafe_outputs"] == 0
    assert all_runs["healthy_safe_outputs"] == 0
    assert all_runs["unresolved"] == 2
    assert all_runs["unsafe_rate_bounds"] == [0.0, 1.0]


def test_empty_attempt_ledger_is_not_a_measured_zero_error_rate():
    report = metrics.summarize([], "benign")["all_attempts"]
    assert report["runs"] == 0
    assert report["unsafe_output_rate"] is None
    assert report["unsafe_rate_bounds"] is None
    assert report["healthy_binary_coverage"] is None


def test_healthy_binary_runs_have_point_identification_bounds():
    report = metrics.summarize([run(1, "Unsafe"), run(2, "Safe")], "attack")
    assert report["all_attempts"]["unsafe_rate_bounds"] == [0.5, 0.5]


def test_json_cli_retains_every_attempt_and_explains_unknowns(tmp_path, monkeypatch, capsys):
    rows = [run(1, "Unsafe"), run(2, None), run(3, None, observed=False)]
    source = tmp_path / "runs.jsonl"
    source.write_text("\n".join(json.dumps(row) for row in rows))
    monkeypatch.setattr("sys.argv", ["metrics", "--attack", str(source)])
    assert metrics.main() == 0
    output = capsys.readouterr()
    report = json.loads(output.out)["attack"]
    assert report["all_attempts"]["runs"] == 3
    assert len(report["scenarios"]) == 3
    assert "excluded (not counted as misses)" not in output.err
    assert "retained" in output.err


def test_unobserved_attempt_blocks_a_publication_ready_release(tmp_path, monkeypatch, capsys):
    """Release readiness cannot override a failed attempt in the run ledger."""
    from compare.tools import dataset_release

    rows = [run(1, "Unsafe"), run(2, None, observed=False)]
    source = tmp_path / "runs.jsonl"
    source.write_text("\n".join(json.dumps(row) for row in rows))
    monkeypatch.setattr(dataset_release, "validate_release",
                        lambda _: {"release_id": "synthetic", "paper_metrics_available": True})
    monkeypatch.setattr(metrics, "validate_run_artifacts", lambda *args, **kwargs: None)
    monkeypatch.setattr("sys.argv", ["metrics", "--attack", str(source),
                                    "--release", str(tmp_path / "release")])
    assert metrics.main() == 0
    report = json.loads(capsys.readouterr().out)
    assert report["paper_metrics_available"] is False
    assert report["publication_blockers"]


@pytest.mark.parametrize("roles", [("--attack", "--attack"), ("--benign", "--attack")])
def test_repeated_run_cannot_enter_multiple_input_files_or_labels(tmp_path, monkeypatch, capsys, roles):
    source = tmp_path / "runs.jsonl"
    source.write_text(json.dumps(run(1, "Unsafe")))
    monkeypatch.setattr("sys.argv", ["metrics", roles[0], str(source), roles[1], str(source)])
    with pytest.raises(SystemExit):
        metrics.main()
    output = capsys.readouterr()
    assert "duplicate" in output.err.lower()
    assert not output.out


def release():
    return {"release_id": "rel", "case_labels": {"case": "attack"},
            "case_bindings": {"case": {"label": "attack"}}}


def unobserved(**changes):
    row = {"scenario_id": "case", "attempt_id": "attempt-1", "release_id": "rel",
           "label": "attack", "collector_sha256": "c" * 64,
           "observed": False, "final_decision": None,
           "artifact_error": "parent process exited before recording completion"}
    row.update(changes)
    return row


def test_unobserved_attempts_are_accepted_without_a_database():
    metrics.validate_run_artifacts([unobserved()], release())


def test_unobserved_attempt_may_keep_a_canonical_verdict_but_interrupted_may_not():
    # Capture quality can be unknown while a canonical DB verdict exists; it
    # stays out of conditional rates but remains valid provenance.
    metrics.validate_run_artifacts([unobserved(final_decision="Uncertain")], release())
    with pytest.raises(ValueError, match="interrupted"):
        metrics.validate_run_artifacts(
            [unobserved(state="interrupted", final_decision="Safe")], release())
    with pytest.raises(ValueError, match="pending"):
        metrics.validate_run_artifacts([unobserved(state="pending")], release())


def test_observed_row_with_artifact_error_is_still_rejected():
    row = unobserved(observed=True)
    row.pop("artifact_error")
    row["artifact_error"] = "cannot bind artifacts"
    with pytest.raises(ValueError, match="artifact error"):
        metrics.validate_run_artifacts([row], release())


def test_observed_row_without_a_database_is_still_rejected():
    row = unobserved()
    row.pop("artifact_error")
    row["observed"] = True
    with pytest.raises(ValueError, match="database is missing"):
        metrics.validate_run_artifacts([row], release())
