from __future__ import annotations

import csv
import json
from pathlib import Path

from compare.tools.base import AdapterResult, BoundaryType, FindingRecord, RunStatus, ToolMetadata
from compare.tools.datasets import load_dataset
from compare.tools.runner import run_comparison


def test_runner_describes_unbound_dataset_without_claiming_historical_identity(tmp_path, synthetic_dataset):
    dataset = tmp_path / "datasets" / "FuxiBench"
    dataset.parent.mkdir()
    synthetic_dataset.rename(dataset)
    output = tmp_path / "results"
    assert run_comparison(dataset_name="FuxiBench", tools=["fixture-scanner"],
                          dataset_root=dataset.parent, lockfile=Path("compare/tool-lock.json"),
                          output_dir=output, experimental=False) == 0
    source = json.loads((output / "input_release.json").read_text())
    assert source["release_id"] is None
    assert source["paper_metrics_available"] is None
    assert source["publication_blockers"] == []
    assert source["source_status"] == "unbound"


def test_runner_does_not_claim_unverified_release_identity(tmp_path, synthetic_dataset):
    dataset = tmp_path / "datasets" / "FuxiBench"
    dataset.parent.mkdir()
    synthetic_dataset.rename(dataset)
    (dataset / "release.json").write_text(json.dumps({"release_id": "stale"}))
    output = tmp_path / "results"
    assert run_comparison(dataset_name="FuxiBench", tools=["fixture-scanner"],
                          dataset_root=dataset.parent, lockfile=Path("compare/tool-lock.json"),
                          output_dir=output, experimental=False) == 0
    assert json.loads((output / "input_release.json").read_text())["release_id"] is None


def _current_frozen_case_ids(current_release_dataset) -> list[str]:
    return [case.adapter_case.scenario_id for case in load_dataset("FuxiBench", current_release_dataset.parent).load_cases()]


def test_runner_writes_findings_metrics_status_and_summary(tmp_path: Path, current_release_dataset) -> None:
    output_dir = tmp_path / "results"

    exit_code = run_comparison(
        dataset_name="FuxiBench",
        tools=["fixture-scanner"],
        dataset_root=current_release_dataset.parent,
        lockfile=Path("compare/tool-lock.json"),
        output_dir=output_dir,
        experimental=False,
    )

    assert exit_code == 0
    assert (output_dir / "findings.jsonl").exists()
    assert (output_dir / "metrics.csv").exists()
    assert (output_dir / "tool_status.csv").exists()
    assert (output_dir / "summary.md").exists()

    with (output_dir / "metrics.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    with (output_dir / "tool_status.csv").open(newline="", encoding="utf-8") as handle:
        status_rows = list(csv.DictReader(handle))

    assert "stdout_path" in status_rows[0]
    assert "stderr_path" in status_rows[0]
    assert rows[0]["tool_id"] == "fixture-scanner"
    assert rows[0]["dataset_has_benign_set"] == "True"
    assert rows[0]["precision"] != "not_applicable_no_benign_set"
    assert rows[0]["f1"] != "not_applicable_no_benign_set"

    summary = (output_dir / "summary.md").read_text(encoding="utf-8")
    assert "No cloud APIs were used" in summary
    assert "No LLM Judge was used" in summary
    assert "No runtime replay was performed" in summary
    assert "not MtAgentLens-derived claims or alerts" in summary
    assert "Frozen precision and F1 are not reported" in summary
    assert "Unsupported cases are excluded from metric denominators" in summary


def test_runner_rejects_unknown_tool(tmp_path: Path, capsys, current_release_dataset) -> None:
    exit_code = run_comparison(
        dataset_name="FuxiBench",
        tools=["missing-tool"],
        dataset_root=current_release_dataset.parent,
        lockfile=Path("compare/tool-lock.json"),
        output_dir=tmp_path / "results",
        experimental=False,
    )

    assert exit_code == 2
    captured = capsys.readouterr()
    assert "unknown tool id" in captured.err
    assert "missing-tool" in captured.err


def test_runner_converts_support_errors_to_failed_status(tmp_path: Path, monkeypatch, current_release_dataset) -> None:
    class BrokenAdapter:
        def __init__(self, metadata: ToolMetadata) -> None:
            self.metadata = metadata

        def supports(self, case) -> bool:
            raise RuntimeError("support probe failed")

        def run(self, case, output_dir):
            raise AssertionError("run should not be called")

    def instantiate_broken_adapter(metadata: ToolMetadata) -> BrokenAdapter:
        return BrokenAdapter(metadata)

    monkeypatch.setattr("compare.tools.runner.instantiate_adapter", instantiate_broken_adapter)
    output_dir = tmp_path / "results"

    exit_code = run_comparison(
        dataset_name="FuxiBench",
        tools=["fixture-scanner"],
        dataset_root=current_release_dataset.parent,
        lockfile=Path("compare/tool-lock.json"),
        output_dir=output_dir,
        experimental=False,
    )

    assert exit_code == 0
    with (output_dir / "tool_status.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows
    assert {row["status"] for row in rows} == {"failed"}
    assert all(row["failure_reason"] == "RuntimeError: support probe failed" for row in rows)
    assert (output_dir / "metrics.csv").exists()
    assert (output_dir / "summary.md").exists()


def test_runner_converts_adapter_instantiation_errors_to_failed_status(tmp_path: Path, monkeypatch, current_release_dataset) -> None:
    def instantiate_broken_adapter(metadata: ToolMetadata):
        raise RuntimeError("adapter import failed")

    monkeypatch.setattr("compare.tools.runner.instantiate_adapter", instantiate_broken_adapter)
    output_dir = tmp_path / "results"

    exit_code = run_comparison(
        dataset_name="FuxiBench",
        tools=["fixture-scanner"],
        dataset_root=current_release_dataset.parent,
        lockfile=Path("compare/tool-lock.json"),
        output_dir=output_dir,
        experimental=False,
    )

    assert exit_code == 0
    with (output_dir / "tool_status.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows
    assert {row["status"] for row in rows} == {"failed"}
    assert all(row["failure_reason"] == "RuntimeError: adapter import failed" for row in rows)
    assert (output_dir / "metrics.csv").exists()
    assert (output_dir / "summary.md").exists()


def test_runner_preserves_artifact_paths_when_adapter_crashes_after_writing_files(tmp_path: Path, monkeypatch, current_release_dataset) -> None:
    class CrashingAdapter:
        def __init__(self, metadata: ToolMetadata) -> None:
            self.metadata = metadata

        def supports(self, case) -> bool:
            return True

        def run(self, case, output_dir):
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / f"{case.scenario_id}.tool.json").write_text("partial raw", encoding="utf-8")
            (output_dir / f"{case.scenario_id}.tool.stdout.txt").write_text("stdout", encoding="utf-8")
            (output_dir / f"{case.scenario_id}.tool.stderr.txt").write_text("stderr", encoding="utf-8")
            raise RuntimeError("parse crashed")

    def instantiate_crashing_adapter(metadata: ToolMetadata) -> CrashingAdapter:
        return CrashingAdapter(metadata)

    monkeypatch.setattr("compare.tools.runner.instantiate_adapter", instantiate_crashing_adapter)
    output_dir = tmp_path / "results"

    exit_code = run_comparison(
        dataset_name="FuxiBench",
        tools=["fixture-scanner"],
        dataset_root=current_release_dataset.parent,
        lockfile=Path("compare/tool-lock.json"),
        output_dir=output_dir,
        experimental=False,
    )

    assert exit_code == 0
    with (output_dir / "tool_status.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows
    assert {row["status"] for row in rows} == {"failed"}
    assert all(row["raw_output_path"] for row in rows)
    assert all(row["stdout_path"] for row in rows)
    assert all(row["stderr_path"] for row in rows)
    assert all(Path(row["raw_output_path"]).exists() for row in rows)
    assert all(row["failure_reason"] == "RuntimeError: parse crashed" for row in rows)


def test_runner_returns_error_for_missing_lockfile(tmp_path: Path, capsys, current_release_dataset) -> None:
    output_dir = tmp_path / "results"
    missing_lockfile = tmp_path / "missing-tool-lock.json"

    exit_code = run_comparison(
        dataset_name="FuxiBench",
        tools=["fixture-scanner"],
        dataset_root=current_release_dataset.parent,
        lockfile=missing_lockfile,
        output_dir=output_dir,
        experimental=False,
    )

    assert exit_code == 2
    assert not output_dir.exists()
    captured = capsys.readouterr()
    assert "failed to load tool lockfile" in captured.err
    assert str(missing_lockfile) in captured.err


def test_runner_reports_supported_and_unsupported_denominators(tmp_path: Path, monkeypatch, current_release_dataset) -> None:
    case_ids = _current_frozen_case_ids(current_release_dataset)
    unsupported_id = case_ids[0]

    class PartiallySupportedAdapter:
        def __init__(self, metadata: ToolMetadata) -> None:
            self.metadata = metadata

        def supports(self, case) -> bool:
            return case.scenario_id != unsupported_id

        def run(self, case, output_dir) -> AdapterResult:
            return AdapterResult(
                tool=self.metadata,
                scenario_id=case.scenario_id,
                status=RunStatus.NO_FINDINGS,
                findings=(),
                raw_output_path=None,
                stdout_path=None,
                stderr_path=None,
                exit_code=0,
                duration_seconds=0.0,
                failure_reason=None,
            )

    def instantiate_partially_supported_adapter(metadata: ToolMetadata) -> PartiallySupportedAdapter:
        return PartiallySupportedAdapter(metadata)

    monkeypatch.setattr("compare.tools.runner.instantiate_adapter", instantiate_partially_supported_adapter)
    output_dir = tmp_path / "results"

    exit_code = run_comparison(
        dataset_name="FuxiBench",
        tools=["fixture-scanner"],
        dataset_root=current_release_dataset.parent,
        lockfile=Path("compare/tool-lock.json"),
        output_dir=output_dir,
        experimental=False,
    )

    assert exit_code == 0
    with (output_dir / "metrics.csv").open(newline="", encoding="utf-8") as handle:
        row = next(csv.DictReader(handle))

    assert row["dataset_total_scenarios"] == str(len(case_ids))
    assert row["supported_scenarios"] == str(len(case_ids) - 1)
    assert row["unsupported_scenarios"] == "1"
    assert row["total_scenarios"] == str(len(case_ids) - 1)


def test_runner_counts_supported_cases_even_when_supported_runs_fail(tmp_path: Path, monkeypatch, current_release_dataset) -> None:
    case_ids = _current_frozen_case_ids(current_release_dataset)
    unsupported_id = case_ids[0]

    class FailingSupportedAdapter:
        def __init__(self, metadata: ToolMetadata) -> None:
            self.metadata = metadata

        def supports(self, case) -> bool:
            return case.scenario_id != unsupported_id

        def run(self, case, output_dir) -> AdapterResult:
            return AdapterResult(
                tool=self.metadata,
                scenario_id=case.scenario_id,
                status=RunStatus.FAILED,
                findings=(),
                raw_output_path=None,
                stdout_path=None,
                stderr_path=None,
                exit_code=2,
                duration_seconds=0.0,
                failure_reason="supported run failed",
            )

    def instantiate_failing_supported_adapter(metadata: ToolMetadata) -> FailingSupportedAdapter:
        return FailingSupportedAdapter(metadata)

    monkeypatch.setattr("compare.tools.runner.instantiate_adapter", instantiate_failing_supported_adapter)
    output_dir = tmp_path / "results"

    exit_code = run_comparison(
        dataset_name="FuxiBench",
        tools=["fixture-scanner"],
        dataset_root=current_release_dataset.parent,
        lockfile=Path("compare/tool-lock.json"),
        output_dir=output_dir,
        experimental=False,
    )

    assert exit_code == 0
    with (output_dir / "metrics.csv").open(newline="", encoding="utf-8") as handle:
        row = next(csv.DictReader(handle))

    assert row["dataset_total_scenarios"] == str(len(case_ids))
    assert row["supported_scenarios"] == str(len(case_ids) - 1)
    assert row["unsupported_scenarios"] == "1"
    assert row["total_scenarios"] == "0"


def test_runner_excludes_failed_result_findings_from_metrics(tmp_path: Path, monkeypatch, current_release_dataset) -> None:
    class BadFindingAdapter:
        def __init__(self, metadata: ToolMetadata) -> None:
            self.metadata = metadata

        def supports(self, case) -> bool:
            return True

        def run(self, case, output_dir) -> AdapterResult:
            return AdapterResult(
                tool=self.metadata,
                scenario_id=case.scenario_id,
                status=RunStatus.FAILED,
                findings=(
                    FindingRecord(
                        tool_id=self.metadata.id,
                        dataset_name=case.dataset_name,
                        scenario_id=case.scenario_id,
                        finding_id=f"bad:{case.scenario_id}",
                        severity="Critical",
                        category="secret_or_sensitive_resource",
                        boundary_type=BoundaryType.RESOURCE,
                        resource="endpoints.txt",
                        command=None,
                        text_span="endpoints.txt",
                        evidence="This finding belongs to a failed run.",
                        confidence=1.0,
                        raw_output_path=None,
                    ),
                ),
                raw_output_path=None,
                stdout_path=None,
                stderr_path=None,
                exit_code=1,
                duration_seconds=0.0,
                failure_reason="failed after partial output",
            )

    def instantiate_bad_finding_adapter(metadata: ToolMetadata) -> BadFindingAdapter:
        return BadFindingAdapter(metadata)

    captured_finding_counts: list[int] = []

    def fake_evaluate_tool_results(tool, cases, findings):
        findings_list = list(findings)
        captured_finding_counts.append(len(findings_list))
        return {
            "tool_id": tool.id,
            "tool_name": tool.name,
            "total_scenarios": 0,
            "attack_scenarios": 0,
            "benign_scenarios": 0,
            "dataset_has_benign_set": False,
            "detected_attack_scenarios": 0,
            "missed_attack_scenarios": 0,
            "scenario_recall": 0.0,
            "recall": 0.0,
            "precision": "not_applicable_no_benign_set",
            "f1": "not_applicable_no_benign_set",
            "tp": 0,
            "fp": "not_applicable_no_benign_set",
            "fn": 0,
            "tn": "not_applicable_no_benign_set",
            "total_expected_claims": 0,
            "matched_claims": 0,
            "claim_coverage": 0.0,
            "claim_coverage_by_boundary": {},
            "claim_coverage_by_category": {},
        }

    monkeypatch.setattr("compare.tools.runner.instantiate_adapter", instantiate_bad_finding_adapter)
    monkeypatch.setattr("compare.tools.runner.evaluate_tool_results", fake_evaluate_tool_results)
    output_dir = tmp_path / "results"

    exit_code = run_comparison(
        dataset_name="FuxiBench",
        tools=["fixture-scanner"],
        dataset_root=current_release_dataset.parent,
        lockfile=Path("compare/tool-lock.json"),
        output_dir=output_dir,
        experimental=False,
    )

    assert exit_code == 0
    with (output_dir / "metrics.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["total_scenarios"] == "0"
    assert rows[0]["matched_claims"] == "0"
    assert captured_finding_counts == [0]
