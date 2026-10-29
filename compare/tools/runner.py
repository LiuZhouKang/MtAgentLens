from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from typing import Iterable

from compare.tools.base import AdapterResult, EvaluationCase, FindingRecord, RunStatus, ToolMetadata
from compare.tools.datasets import load_dataset
from compare.tools.evaluator import evaluate_tool_results
from compare.tools.registry import instantiate_adapter, load_tool_metadata


_STATUS_FIELDS = (
    "tool_id",
    "scenario_id",
    "status",
    "exit_code",
    "duration_seconds",
    "raw_output_path",
    "stdout_path",
    "stderr_path",
    "failure_reason",
)


_SUMMARY_GUARDRAILS = """# MtAgentLens Local Tool Comparison Summary

## Round 1 guardrails

- No cloud APIs were used.
- No LLM Judge was used.
- No runtime replay was performed.
- Comparator inputs were sanitized scenario artifacts and fixture files, not MtAgentLens-derived claims or alerts.
- Frozen precision and F1 are not reported without a verified benign set.
- Unsupported cases are excluded from metric denominators and reported separately as unsupported_scenarios.
"""


def run_comparison(
    dataset_name: str,
    tools: list[str],
    dataset_root: Path,
    lockfile: Path,
    output_dir: Path,
    experimental: bool,
) -> int:
    del experimental

    try:
        tool_metadata = load_tool_metadata(lockfile)
    except (OSError, ValueError) as exc:
        print(f"failed to load tool lockfile {lockfile}: {_exception_summary(exc)}", file=sys.stderr)
        return 2

    output_dir.mkdir(parents=True, exist_ok=True)
    selected_tools = _select_tools(tool_metadata, tools)
    if selected_tools is None:
        known_tools = ", ".join(sorted(tool.id for tool in tool_metadata))
        requested_tools = ", ".join(tools)
        print(f"unknown tool id in requested tools: {requested_tools}; known tools: {known_tools}", file=sys.stderr)
        return 2

    try:
        dataset = load_dataset(dataset_name, dataset_root)
        cases = dataset.load_cases()
    except (OSError, ValueError) as exc:
        print(f"invalid evaluation dataset: {_exception_summary(exc)}", file=sys.stderr)
        return 2
    release_path = dataset.dataset_dir / "release.json"
    release = json.loads(release_path.read_text()) if release_path.exists() else {}

    all_findings: list[FindingRecord] = []
    all_results: list[AdapterResult] = []
    metrics: list[dict[str, object]] = []

    for tool in selected_tools:
        tool_results: list[AdapterResult] = []
        eligible_cases: list[EvaluationCase] = []
        supported_scenarios = 0

        try:
            adapter = instantiate_adapter(tool)
        except Exception as exc:  # noqa: BLE001 - runner converts adapter failures into status rows.
            for case in cases:
                tool_results.append(_failed_result(tool, case.adapter_case.scenario_id, _exception_summary(exc)))
        else:
            for case in cases:
                result, is_supported = _run_case(adapter, tool, case, output_dir)
                if is_supported:
                    supported_scenarios += 1

                tool_results.append(result)
                all_findings.extend(result.findings)
                if result.is_metric_eligible():
                    eligible_cases.append(case)

        unsupported_scenarios = sum(result.status == RunStatus.UNSUPPORTED_DATASET for result in tool_results)
        metric_findings = [finding for result in tool_results if result.is_metric_eligible() for finding in result.findings]
        all_results.extend(tool_results)
        metrics.append(
            _with_support_counts(
                evaluate_tool_results(tool, eligible_cases, metric_findings),
                dataset_total_scenarios=len(cases),
                supported_scenarios=supported_scenarios,
                unsupported_scenarios=unsupported_scenarios,
            )
        )

    _write_findings(output_dir / "findings.jsonl", all_findings)
    for row in metrics:
        row["release_id"] = release.get("release_id")
        row["paper_metrics_available"] = release.get("paper_metrics_available", False)
    (output_dir / "input_release.json").write_text(json.dumps({
        "release_id": release.get("release_id"),
        "paper_metrics_available": release.get("paper_metrics_available", False),
        "publication_blockers": release.get("publication_blockers", ["Unversioned historical input"]),
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_status(output_dir / "tool_status.csv", all_results)
    _write_metrics(output_dir / "metrics.csv", metrics)
    _write_summary(output_dir / "summary.md")
    return 0


def _exception_summary(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"


def _run_case(adapter, tool: ToolMetadata, case: EvaluationCase, output_dir: Path) -> tuple[AdapterResult, bool]:
    adapter_case = case.adapter_case
    case_output_dir = output_dir / "raw" / tool.id / adapter_case.scenario_id
    is_supported = False

    try:
        is_supported = adapter.supports(adapter_case)
        if not is_supported:
            return _unsupported_result(tool, adapter_case.scenario_id), False

        return adapter.run(adapter_case, case_output_dir), True
    except Exception as exc:  # noqa: BLE001 - runner converts adapter failures into status rows.
        return (
            _failed_result(
                tool,
                adapter_case.scenario_id,
                _exception_summary(exc),
                _existing_artifact(case_output_dir, adapter_case.scenario_id, ("*.json", "*.jsonl")),
                _existing_artifact(case_output_dir, adapter_case.scenario_id, ("*.stdout.txt", "*.jsonl")),
                _existing_artifact(case_output_dir, adapter_case.scenario_id, ("*.stderr.txt",)),
            ),
            is_supported,
        )


def _unsupported_result(tool: ToolMetadata, scenario_id: str) -> AdapterResult:
    return AdapterResult(
        tool=tool,
        scenario_id=scenario_id,
        status=RunStatus.UNSUPPORTED_DATASET,
        findings=(),
        raw_output_path=None,
        stdout_path=None,
        stderr_path=None,
        exit_code=None,
        duration_seconds=0.0,
        failure_reason="Adapter does not support case artifacts.",
    )


def _with_support_counts(
    metrics: dict[str, object],
    *,
    dataset_total_scenarios: int,
    supported_scenarios: int,
    unsupported_scenarios: int,
) -> dict[str, object]:
    return {
        **metrics,
        "dataset_total_scenarios": dataset_total_scenarios,
        "supported_scenarios": supported_scenarios,
        "unsupported_scenarios": unsupported_scenarios,
    }


def _failed_result(
    tool: ToolMetadata,
    scenario_id: str,
    failure_reason: str,
    raw_output_path: Path | None = None,
    stdout_path: Path | None = None,
    stderr_path: Path | None = None,
) -> AdapterResult:
    return AdapterResult(
        tool=tool,
        scenario_id=scenario_id,
        status=RunStatus.FAILED,
        findings=(),
        raw_output_path=raw_output_path,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        exit_code=None,
        duration_seconds=0.0,
        failure_reason=failure_reason,
    )


def _existing_artifact(output_dir: Path, scenario_id: str, patterns: tuple[str, ...]) -> Path | None:
    for pattern in patterns:
        for path in sorted(output_dir.glob(f"{scenario_id}.{pattern}")):
            if path.exists():
                return path
    return None


def _select_tools(metadata: list[ToolMetadata], requested_tools: list[str]) -> list[ToolMetadata] | None:
    if requested_tools == ["all"]:
        return metadata

    by_id = {tool.id: tool for tool in metadata}
    selected: list[ToolMetadata] = []
    for tool_id in requested_tools:
        tool = by_id.get(tool_id)
        if tool is None:
            return None
        selected.append(tool)
    return selected


def _write_findings(path: Path, findings: Iterable[FindingRecord]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for finding in findings:
            handle.write(json.dumps(finding.to_json_dict(), sort_keys=True) + "\n")


def _write_status(path: Path, results: Iterable[AdapterResult]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=_STATUS_FIELDS)
        writer.writeheader()
        for result in results:
            writer.writerow(
                {
                    "tool_id": result.tool.id,
                    "scenario_id": result.scenario_id,
                    "status": result.status.value,
                    "exit_code": "" if result.exit_code is None else result.exit_code,
                    "duration_seconds": result.duration_seconds,
                    "raw_output_path": "" if result.raw_output_path is None else str(result.raw_output_path),
                    "stdout_path": "" if result.stdout_path is None else str(result.stdout_path),
                    "stderr_path": "" if result.stderr_path is None else str(result.stderr_path),
                    "failure_reason": "" if result.failure_reason is None else result.failure_reason,
                }
            )


def _write_metrics(path: Path, metrics: list[dict[str, object]]) -> None:
    fieldnames = _metric_fieldnames(metrics)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in metrics:
            writer.writerow({field: _csv_value(row.get(field, "")) for field in fieldnames})


def _metric_fieldnames(metrics: list[dict[str, object]]) -> list[str]:
    preferred = [
        "tool_id",
        "tool_name",
        "dataset_total_scenarios",
        "supported_scenarios",
        "unsupported_scenarios",
        "total_scenarios",
        "attack_scenarios",
        "benign_scenarios",
        "dataset_has_benign_set",
        "detected_attack_scenarios",
        "missed_attack_scenarios",
        "scenario_recall",
        "recall",
        "precision",
        "f1",
        "tp",
        "fp",
        "fn",
        "tn",
        "total_expected_claims",
        "matched_claims",
        "claim_coverage",
        "claim_coverage_by_boundary",
        "claim_coverage_by_category",
    ]
    seen = set(preferred)
    extras: list[str] = []
    for row in metrics:
        for key in row:
            if key not in seen:
                seen.add(key)
                extras.append(key)
    return preferred + extras


def _csv_value(value: object) -> object:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, sort_keys=True)
    return value


def _write_summary(path: Path) -> None:
    path.write_text(_SUMMARY_GUARDRAILS, encoding="utf-8")
