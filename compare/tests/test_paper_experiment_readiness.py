from __future__ import annotations

import json
from pathlib import Path

import compare.scripts.audit.audit_paper_experiment_readiness as readiness
from compare.scripts.audit.audit_paper_experiment_readiness import audit
from compare.scripts.audit.audit_paper_experiment_readiness import (
    _validate_controlled_ablation_artifact,
    _check_external_benchmarks,
    _design_claims_401_external,
    _prompt_text_label_leaks,
    _validate_backend_health_artifact,
    _validate_performance_artifact,
)


def test_readiness_audit_blocks_current_unfrozen_inputs() -> None:
    result = audit(
        dataset_dir=Path("datasets/FuxiBench"),
        design_doc=Path("docs/design/experiment-design.md"),
        lockfile=Path("compare/tool-lock.json"),
    )

    checks = {check["id"]: check for check in result["checks"]}

    assert result["overall_status"] == "blocked"
    assert checks["frozen_dataset_quality"]["status"] == "blocker"
    # Dataset work adds observed runs incrementally; the audit must stay a
    # blocker until the corpus is complete, and unknown_runs shrinks as runs
    # are filed rather than being a fixed 93 forever.
    unknown = checks["frozen_dataset_quality"]["evidence"]["unknown_runs"]
    assert isinstance(unknown, int) and 0 <= unknown <= 93
    assert checks["tool_lock_reproducibility"]["status"] == "pass"
    assert checks["controlled_ablation_results"]["status"] == "blocker"
    assert checks["performance_results"]["status"] == "blocker"


def test_external_gate_accepts_deferred_policy_without_completed_external_claim(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset"
    archive = dataset / "ground_truth" / "_external_archive"
    archive.mkdir(parents=True)
    for index in range(23):
        (archive / f"case-{index}.json").write_text("{}\n", encoding="utf-8")
    design = tmp_path / "design.md"
    design.write_text(
        "\n".join(
            [
                "EXTERNAL_BENCHMARK_POLICY: deferred_from_main_claims",
                "Future work may import 401 external cases.",
                "The main paper must not claim 468 benchmarks.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    check = _check_external_benchmarks(dataset, design)

    assert check.status == "pass"
    assert check.evidence["external_archive_json_count"] == 23
    assert check.evidence["design_claims_401_external"] is False


def test_external_claim_detector_flags_completed_main_claim_but_not_limitations() -> None:
    assert _design_claims_401_external("We validate the system on 468 benchmarks.") is True
    assert _design_claims_401_external("Experiment 3 uses 67 + 401 external scenarios.") is True
    assert _design_claims_401_external("Future work may import 401 external cases.") is False
    assert _design_claims_401_external("We do not claim 468 benchmarks.") is False


def test_prompt_baseline_gate_uses_independent_prompt_lockfile() -> None:
    result = audit(
        dataset_dir=Path("datasets/FuxiBench"),
        design_doc=Path("docs/design/experiment-design.md"),
        lockfile=Path("compare/tool-lock.json"),
        prompt_lockfile=Path("compare/tool-lock.prompt.json"),
    )

    checks = {check["id"]: check for check in result["checks"]}
    evidence = checks["prompt_baseline_input_contract"]["evidence"]

    assert checks["tool_lock_reproducibility"]["status"] == "pass"
    assert checks["prompt_baseline_input_contract"]["status"] == "pass"
    assert evidence["expected_cases"] == 93
    assert evidence["prompt_lockfile"] == "compare/tool-lock.prompt.json"
    assert set(evidence["prompt_tools_present"]) >= {"llm-guard", "meta-prompt-guard", "vigil"}
    assert evidence["prompt_manifest_total_cases"] > 0
    assert evidence["prompt_text_files"] == evidence["prompt_manifest_total_cases"]
    assert evidence["attack_entries_without_fixture_text"] == []
    assert evidence["entries_with_attack_type_mismatch"] == []
    assert evidence["prompt_text_label_leaks"] == []


def test_prompt_label_leak_detector_rejects_structural_metadata_lines() -> None:
    leaks = _prompt_text_label_leaks(
        "\n".join(
            [
                "# Prompt",
                "Attack type: B",
                "Category: benign",
                "Scenario ID: prompt-injection",
            ]
        )
    )

    assert [leak["field"].lower() for leak in leaks] == ["attack type", "category", "scenario id"]


def test_readiness_audit_json_serializable() -> None:
    result = audit(
        dataset_dir=Path("datasets/FuxiBench"),
        design_doc=Path("docs/design/experiment-design.md"),
        lockfile=Path("compare/tool-lock.json"),
    )

    json.dumps(result, ensure_ascii=False, sort_keys=True)

def test_backend_health_gate_rejects_empty_or_planned_artifacts(tmp_path: Path) -> None:
    empty = tmp_path / "capture_health.json"
    empty.write_text(json.dumps({"schema": "mtagentlens.backend_capture_health.v1"}) + "\n", encoding="utf-8")
    evidence = _validate_backend_health_artifact(empty)
    assert evidence["valid"] is False

    planned = tmp_path / "planned_capture_health.json"
    planned.write_text(
        json.dumps(
            {
                "schema": "mtagentlens.backend_capture_health.v1",
                "status": "planned_missing_artifact",
                "provenance": {},
                "checks": {},
                "runs": [],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    evidence = _validate_backend_health_artifact(planned)
    assert evidence["valid"] is False

    blocked = tmp_path / "blocked_capture_health.json"
    blocked.write_text(
        json.dumps(
            {
                "schema": "mtagentlens.backend_capture_health.v1",
                "status": "blocked",
                "provenance": {
                    "git_commit": "abc",
                    "kernel": "Linux",
                    "collector_binary_hash": "def",
                    "runner": "unit-test",
                },
                "checks": {
                    "collector_started": {"status": "pass"},
                    "collector_exit_status_checked": {"status": "pass"},
                    "persistence_lost_count_zero": {"status": "fail"},
                    "tls_drop_count_checked": {"status": "fail"},
                    "process_drop_count_checked": {"status": "fail"},
                    "db_schema_current": {"status": "pass"},
                    "db_nonempty_required_tables": {"status": "pass"},
                },
                "runs": [{"run_id": "run-1", "scenario_id": "demo", "status": "blocked"}],
                "counters": {
                    "persistence_lost_count": None,
                    "tls_drop_count": None,
                    "process_drop_count": None,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    evidence = _validate_backend_health_artifact(blocked)
    assert evidence["valid"] is False


def test_controlled_ablation_gate_rejects_offline_or_shallow_artifacts(tmp_path: Path) -> None:
    offline = tmp_path / "ablation_results.json"
    offline.write_text(
        json.dumps(
            {
                "schema": "mtagentlens.ablation_results.v1",
                "methodology": "offline_evidence_masking",
                "is_controlled_feature_flag_replay": False,
                "variants": [{"variant_id": "mask_claims", "status": "completed"}],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    evidence = _validate_controlled_ablation_artifact(offline)
    assert evidence["valid"] is False
    assert any("schema" in problem for problem in evidence["problems"])

    shallow = tmp_path / "shallow_controlled.json"
    shallow.write_text(
        json.dumps(
            {
                "schema": "mtagentlens.controlled_ablation_results.v1",
                "status": "completed",
                "methodology": "sqlite_persisted_graph_action_family_feature_flags",
                "is_controlled_feature_flag_replay": True,
                "variants": [{"variant_id": "disable_x", "status": "completed"} for _ in range(6)],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    evidence = _validate_controlled_ablation_artifact(shallow)
    assert evidence["valid"] is False
    assert any("baseline" in problem for problem in evidence["problems"])
    assert any("per-scenario outcomes" in problem for problem in evidence["problems"])


def test_controlled_ablation_gate_accepts_completed_schema(tmp_path: Path) -> None:
    path = tmp_path / "controlled_ablation_results.json"
    outcomes = [
        {
            "scenario_id": f"scenario-{index:02d}",
            "status": "completed",
            "detected": index % 2 == 0,
            "matched_claims": 1,
            "expected_claims": 1,
        }
        for index in range(67)
    ]
    variants = [
        {
            "variant_id": f"variant-{index}",
            "status": "completed",
            "disabled_features": [f"feature-{index}"],
            "runs_completed": 67,
            "metrics": {"scenario_recall": 0.9, "claim_coverage": 0.8},
            "delta_vs_full": {"scenario_recall": -0.1},
            "per_scenario_outcomes": outcomes,
        }
        for index in range(6)
    ]
    path.write_text(
        json.dumps(
            {
                "schema": "mtagentlens.controlled_ablation_results.v1",
                "status": "completed",
                "methodology": "sqlite_persisted_graph_action_family_feature_flags",
                "is_controlled_feature_flag_replay": True,
                "dataset": {"version": "FuxiBench", "scenario_count": 67},
                "provenance": {
                    "dataset_hash": "dataset-hash",
                    "git_commit": "git-sha",
                    "runner": "unit-test",
                },
                "baseline": {
                    "variant_id": "full_replay",
                    "status": "completed",
                    "runs_completed": 67,
                    "metrics": {"scenario_recall": 1.0, "claim_coverage": 1.0},
                    "parity": {"status": "pass", "missing_scenarios": []},
                },
                "variants": variants,
                "claim_scope": {
                    "supported_claims": ["sqlite replay feature-flag deltas"],
                    "unsupported_claims": ["live eBPF capture ablation"],
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    evidence = _validate_controlled_ablation_artifact(path)
    assert evidence["valid"] is True


def test_backend_health_gate_accepts_completed_schema(tmp_path: Path) -> None:
    path = tmp_path / "capture_health.json"
    checks = {
        "collector_started": {"status": "pass"},
        "collector_exit_status_checked": {"status": "pass"},
        "persistence_lost_count_zero": {"status": "pass"},
        "tls_drop_count_checked": {"status": "pass"},
        "process_drop_count_checked": {"status": "pass"},
        "db_schema_current": {"status": "pass"},
        "db_nonempty_required_tables": {"status": "pass"},
    }
    path.write_text(
        json.dumps(
            {
                "schema": "mtagentlens.backend_capture_health.v1",
                "status": "completed",
                "provenance": {
                    "git_commit": "abc",
                    "kernel": "Linux",
                    "collector_binary_hash": "def",
                    "runner": "unit-test",
                },
                "checks": checks,
                "runs": [{"run_id": "run-1", "scenario_id": "demo", "status": "completed"}],
                "counters": {
                    "persistence_lost_count": 0,
                    "tls_drop_count": 0,
                    "process_drop_count": 0,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    evidence = _validate_backend_health_artifact(path)
    assert evidence["valid"] is True

def test_performance_gate_rejects_empty_or_partial_artifacts(tmp_path: Path) -> None:
    path = tmp_path / "perf_report.json"
    path.write_text(json.dumps({"schema": "mtagentlens.performance_results.v1"}) + "\n", encoding="utf-8")
    evidence = _validate_performance_artifact(path)
    assert evidence["valid"] is False

    partial = tmp_path / "partial_perf_report.json"
    partial.write_text(
        json.dumps(
            {
                "schema": "mtagentlens.performance_results.v1",
                "status": "completed",
                "provenance": {"git_commit": "abc"},
                "experiments": {
                    "throughput": {"status": "completed", "runs": [{"events_per_sec": 1000}]}
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    evidence = _validate_performance_artifact(partial)
    assert evidence["valid"] is False


def test_performance_gate_accepts_completed_schema(tmp_path: Path) -> None:
    path = tmp_path / "perf_report.json"
    path.write_text(
        json.dumps(
            {
                "schema": "mtagentlens.performance_results.v1",
                "status": "completed",
                "provenance": {
                    "git_commit": "abc",
                    "kernel": "Linux",
                    "collector_binary_hash": "def",
                    "commands": ["cmd"],
                },
                "experiments": {
                    "probe_latency": {"status": "completed", "runs": [{"p99_us": 1.0}]},
                    "throughput": {"status": "completed", "runs": [{"events_per_sec": 1000}]},
                    "memory": {"status": "completed", "runs": [{"peak_rss_mb": 10}]},
                    "time_to_detect": {"status": "completed", "runs": [{"median_s": 1}]},
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    evidence = _validate_performance_artifact(path)
    assert evidence["valid"] is True
