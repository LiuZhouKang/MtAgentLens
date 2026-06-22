#!/usr/bin/env python3
"""Build the machine-readable paper evaluation report from completed artifacts."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from env.scripts.evaluate_frozen import evaluate_dataset, load_mtagentprism_findings  # noqa: E402
from compare.scripts.audit.audit_paper_experiment_readiness import _prompt_manifest_evidence  # noqa: E402
from compare.tools.datasets import FrozenDatasetAdapter  # noqa: E402


DEFAULT_DATASET = PROJECT_ROOT / "compare" / "Dataset" / "FuxiBench"
DEFAULT_PROMPT_RESULTS = PROJECT_ROOT / "compare" / "results" / "prompt_baselines" / "FuxiBench"
DEFAULT_OUTPUT = PROJECT_ROOT / "compare" / "results" / "paper" / "evaluation_report.json"
DEFAULT_STATIC_LOCKFILE = PROJECT_ROOT / "compare" / "tool-lock.json"
DEFAULT_PROMPT_LOCKFILE = PROJECT_ROOT / "compare" / "tool-lock.prompt.json"
DEFAULT_CONTROLLED_ABLATION = PROJECT_ROOT / "compare" / "results" / "paper" / "controlled_ablation_results.json"
DEFAULT_PERFORMANCE = PROJECT_ROOT / "compare" / "results" / "perf" / "performance_results.json"
DEFAULT_BACKEND_HEALTH = PROJECT_ROOT / "compare" / "results" / "backend_health" / "capture_health.json"


def build_report(
    *,
    dataset_dir: Path = DEFAULT_DATASET,
    prompt_results_dir: Path = DEFAULT_PROMPT_RESULTS,
    output_path: Path = DEFAULT_OUTPUT,
    static_lockfile: Path = DEFAULT_STATIC_LOCKFILE,
    prompt_lockfile: Path = DEFAULT_PROMPT_LOCKFILE,
) -> dict[str, Any]:
    dataset_dir = dataset_dir.resolve()
    prompt_results_dir = prompt_results_dir.resolve()
    output_path = output_path.resolve()
    static_lockfile = static_lockfile.resolve()
    prompt_lockfile = prompt_lockfile.resolve()

    frozen_eval = evaluate_dataset(dataset_dir)
    prompt_contract = _validated_prompt_contract(dataset_dir)
    prompt_metrics = _read_metrics(prompt_results_dir / "metrics.csv")
    prompt_status = _read_status_summary(prompt_results_dir / "tool_status.csv")
    mtap_metrics = _mtagentprism_metric_row(frozen_eval)
    main_metrics = [mtap_metrics] + prompt_metrics

    # Add per_scenario data for baselines using same criterion as evaluator
    scenario_labels = _scenario_labels(dataset_dir)
    detected_by_tool = _detected_scenarios_by_tool(prompt_results_dir / "findings.jsonl")
    for m in main_metrics:
        tid = m.get("tool_id", "")
        if tid == "mtagentprism":
            continue
        detected_set = detected_by_tool.get(tid, set())
        m["per_scenario"] = [
            {"scenario_id": sid, "is_attack": lbl == "attack", "detected": sid in detected_set}
            for sid, lbl in scenario_labels.items()
        ]

    mcnemar = _mcnemar_against_mtagentprism(dataset_dir, prompt_results_dir / "findings.jsonl")
    pr_curves = _pr_curves(prompt_results_dir / "findings.jsonl", dataset_dir, frozen_eval)
    controlled_ablation = _controlled_ablation_metadata(DEFAULT_CONTROLLED_ABLATION)
    performance = _completed_artifact_metadata(
        DEFAULT_PERFORMANCE,
        expected_schema="mtagentprism.performance_results.v1",
        prefix="performance",
    )
    backend_health = _completed_artifact_metadata(
        DEFAULT_BACKEND_HEALTH,
        expected_schema="mtagentprism.backend_capture_health.v1",
        prefix="backend_capture_health",
    )
    completed_scopes = [
        "frozen_main_prompt_baseline",
        "controlled_ablation" if controlled_ablation["controlled_ablation_status"] == "completed" else "",
        "backend_capture_health_canary" if backend_health["backend_capture_health_status"] == "completed" else "",
        "local_performance_microbenchmarks" if performance["performance_status"] == "completed" else "",
    ]
    completed_scopes = [scope for scope in completed_scopes if scope]
    supported_claims = [
        "67-scenario frozen MtAgentPrism offline evaluation",
        "Prompt baseline results over frozen prompt_inputs manifest",
    ]
    unsupported_claims = [
        "external benchmark validation",
        "universal prompt-guard paradigm ceiling",
        "live module-level causal ablation deltas",
    ]
    if controlled_ablation["controlled_ablation_status"] == "completed":
        supported_claims.append("Controlled SQLite persisted-graph action-family feature-flag ablation")
    else:
        unsupported_claims.append("controlled ablation deltas")
    if backend_health["backend_capture_health_status"] == "completed":
        supported_claims.append("Backend capture-health canary for collector startup, shutdown, counters, and core SQLite persistence")
    else:
        unsupported_claims.append("backend capture health")
    if performance["performance_status"] == "completed":
        supported_claims.append("Local process/file performance microbenchmarks and low-level process-runner first-observation latency")
    else:
        unsupported_claims.append("runtime overhead and time-to-detect")

    report = {
        "schema": "mtagentprism.paper_evaluation_report.v1",
        "_metadata": {
            "dataset": _display_path(dataset_dir),
            "dataset_hash": _tree_sha256(dataset_dir, include_suffixes=(".json", ".md", ".txt", ".csv")),
            "prompt_inputs_manifest": _display_path(dataset_dir / "prompt_inputs" / "manifest.json"),
            "prompt_inputs_manifest_hash": _file_sha256(dataset_dir / "prompt_inputs" / "manifest.json"),
            "prompt_input_contract_status": prompt_contract["status"],
            "prompt_text_label_leak_cases": prompt_contract["label_leak_cases"],
            "prompt_text_hash_mismatches": prompt_contract["hash_mismatches"],
            "prompt_text_excluded_metadata": prompt_contract["excluded_metadata"],
            "static_tool_lock": _display_path(static_lockfile),
            "static_tool_lock_hash": _file_sha256(static_lockfile),
            "prompt_tool_lock": _display_path(prompt_lockfile),
            "prompt_tool_lock_hash": _file_sha256(prompt_lockfile),
            "prompt_results_dir": _display_path(prompt_results_dir),
            "prompt_results_hash": _tree_sha256(prompt_results_dir, include_suffixes=(".csv", ".jsonl", ".md")),
            **controlled_ablation,
            **performance,
            **backend_health,
            "external_benchmark_status": "deferred_from_main_claims",
            "statistics_scope": "completed_artifacts_only:" + ",".join(completed_scopes),
        },
        "dataset_summary": {
            "total_cases": frozen_eval["total_cases"],
            "attack_cases": frozen_eval["attack_cases"],
            "benign_cases": frozen_eval["benign_cases"],
            "paper_metrics_available": frozen_eval["quality_gate"]["paper_metrics_available"],
            "quality_gate_reason": frozen_eval["quality_gate"]["reason"],
        },
        "main_metrics": main_metrics,
        "tool_status_summary": prompt_status,
        "mcnemar": mcnemar,
        "pr_curves": pr_curves,
        "claim_boundaries": {
            "supported_claims": supported_claims,
            "unsupported_claims": [
                *unsupported_claims,
                "401 external / 468 total benchmark validation",
                "production LLM-agent end-to-end latency",
                "trace-mode ResponsibilityRuntime end-to-end latency",
                "live eBPF/TLS/process collector robustness",
                "raw trace or SSE parser robustness under input event fragmentation",
            ],
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def _validated_prompt_contract(dataset_dir: Path) -> dict[str, Any]:
    prompt_inputs_dir = dataset_dir / "prompt_inputs"
    prompt_manifest_path = prompt_inputs_dir / "manifest.json"
    evidence = _prompt_manifest_evidence(prompt_inputs_dir, prompt_manifest_path)
    label_leaks = evidence.get("prompt_text_label_leaks", [])
    hash_mismatches = evidence.get("prompt_text_hash_mismatches", [])
    if label_leaks:
        raise ValueError(f"Prompt inputs contain label leaks in {len(label_leaks)} lines")
    if hash_mismatches:
        raise ValueError(f"Prompt input hashes do not match manifest for {len(hash_mismatches)} cases")
    if evidence.get("manifest_valid") is not True:
        raise ValueError("Prompt input manifest is missing or invalid")
    if evidence.get("prompt_text_files") != evidence.get("prompt_manifest_total_cases"):
        raise ValueError("Prompt input manifest does not match prompt text file count")

    manifest = json.loads(prompt_manifest_path.read_text(encoding="utf-8"))
    sanitization = manifest.get("sanitization", {})
    excluded_metadata = []
    if isinstance(sanitization, dict) and isinstance(sanitization.get("prompt_text_excluded_metadata"), list):
        excluded_metadata = sorted(str(item) for item in sanitization["prompt_text_excluded_metadata"])
    return {
        "status": "label_leak_free",
        "label_leak_cases": 0,
        "hash_mismatches": [],
        "excluded_metadata": excluded_metadata,
    }


def _controlled_ablation_metadata(path: Path) -> dict[str, Any]:
    base = {
        "controlled_ablation_artifact": _display_path(path),
        "controlled_ablation_status": "planned_missing_artifact",
        "controlled_ablation_methodology": "",
    }
    if not path.exists():
        return base
    try:
        artifact = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {**base, "controlled_ablation_status": "invalid_artifact"}
    if (
        artifact.get("schema") == "mtagentprism.controlled_ablation_results.v1"
        and artifact.get("status") == "completed"
        and artifact.get("is_controlled_feature_flag_replay") is True
    ):
        return {
            **base,
            "controlled_ablation_status": "completed",
            "controlled_ablation_methodology": str(artifact.get("methodology") or ""),
            "controlled_ablation_hash": _file_sha256(path),
        }
    return {
        **base,
        "controlled_ablation_status": str(artifact.get("status") or "blocked_or_unsupported"),
        "controlled_ablation_methodology": str(artifact.get("methodology") or ""),
    }


def _completed_artifact_metadata(path: Path, *, expected_schema: str, prefix: str) -> dict[str, Any]:
    base = {
        f"{prefix}_artifact": _display_path(path),
        f"{prefix}_status": "planned_missing_artifact",
    }
    if not path.exists():
        return base
    try:
        artifact = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {**base, f"{prefix}_status": "invalid_artifact"}
    if artifact.get("schema") == expected_schema and artifact.get("status") == "completed":
        return {
            **base,
            f"{prefix}_status": "completed",
            f"{prefix}_schema": expected_schema,
            f"{prefix}_hash": _file_sha256(path),
        }
    return {
        **base,
        f"{prefix}_status": str(artifact.get("status") or "blocked_or_unsupported"),
        f"{prefix}_schema": str(artifact.get("schema") or ""),
    }


def _mtagentprism_metric_row(frozen_eval: dict[str, Any]) -> dict[str, Any]:
    metrics = frozen_eval["metrics"]
    return {
        "tool_id": "mtagentprism",
        "tool_name": "MtAgentPrism",
        "tool_family": "runtime-causal-observability",
        "input_contract": "frozen responsibility.db artifacts",
        "dataset_total_scenarios": frozen_eval["total_cases"],
        "supported_scenarios": frozen_eval["total_cases"],
        "unsupported_scenarios": 0,
        "total_scenarios": metrics["total_scenarios"],
        "attack_scenarios": metrics["attack_scenarios"],
        "benign_scenarios": metrics["benign_scenarios"],
        "detected_attack_scenarios": metrics["detected_attack_scenarios"],
        "missed_attack_scenarios": metrics["missed_attack_scenarios"],
        "tp": metrics["tp"],
        "fp": metrics["fp"],
        "fn": metrics["fn"],
        "tn": metrics["tn"],
        "precision": metrics["precision"],
        "recall": metrics["recall"],
        "scenario_recall": metrics["scenario_recall"],
        "f1": metrics["f1"],
    }


def _read_metrics(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            normalized = {
                "tool_id": row["tool_id"],
                "tool_name": row["tool_name"],
                "tool_family": "prompt-baseline",
                "input_contract": "frozen prompt_inputs manifest",
            }
            for key, value in row.items():
                if key in normalized:
                    continue
                normalized[key] = _parse_csv_value(value)
            rows.append(normalized)
    return rows


def _read_status_summary(path: Path) -> dict[str, dict[str, int]]:
    summary: dict[str, dict[str, int]] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            tool_id = row["tool_id"]
            status = row["status"]
            summary.setdefault(tool_id, {})
            summary[tool_id][status] = summary[tool_id].get(status, 0) + 1
    return summary


def _mcnemar_against_mtagentprism(dataset_dir: Path, findings_path: Path) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    labels = _scenario_labels(dataset_dir)
    attack_scenarios = {scenario_id for scenario_id, label in labels.items() if label == "attack"}
    mtap_detected = _mtagentprism_detected_scenarios(dataset_dir)
    prompt_detected_by_tool = _detected_scenarios_by_tool(findings_path)
    for tool_id, prompt_detected in sorted(prompt_detected_by_tool.items()):
        if tool_id == "mtagentprism":
            continue
        b = sum(
            1
            for scenario_id in attack_scenarios
            if scenario_id in mtap_detected and scenario_id not in prompt_detected
        )
        c = sum(
            1
            for scenario_id in attack_scenarios
            if scenario_id not in mtap_detected and scenario_id in prompt_detected
        )
        statistic, p_value = _mcnemar_yates(b, c)
        results[tool_id] = {
            "comparison": "mtagentprism_vs_prompt_baseline_on_attack_detection",
            "paired_unit": "scenario",
            "attack_scenarios": len(attack_scenarios),
            "b_mtap_correct_prompt_wrong": b,
            "c_prompt_correct_mtap_wrong": c,
            "statistic_yates": statistic,
            "p_value_chi_square_1df": p_value,
            "interpretation": "paired attack-detection test; benign false positives are reported separately",
        }
    return results


def _mcnemar_yates(b: int, c: int) -> tuple[float, float]:
    if b + c == 0:
        return 0.0, 1.0
    statistic = (abs(b - c) - 1) ** 2 / (b + c)
    p_value = math.erfc(math.sqrt(statistic / 2))
    return statistic, p_value


def _pr_curves(findings_path: Path, dataset_dir: Path, frozen_eval: dict[str, Any]) -> dict[str, list[dict[str, float]]]:
    attack_cases = int(frozen_eval["attack_cases"])
    labels = _scenario_labels(dataset_dir)
    findings_by_tool = _findings_by_tool(findings_path)
    curves: dict[str, list[dict[str, float]]] = {}
    for tool_id, findings in findings_by_tool.items():
        ranked = sorted(findings, key=lambda item: item["confidence"], reverse=True)
        detected_attack: set[str] = set()
        detected_benign: set[str] = set()
        points = [{"threshold": 1.000001, "precision": 1.0, "recall": 0.0, "tp": 0.0, "fp": 0.0}]
        for finding in ranked:
            scenario_id = finding["scenario_id"]
            if labels.get(scenario_id) == "attack":
                detected_attack.add(scenario_id)
            elif labels.get(scenario_id) == "benign":
                detected_benign.add(scenario_id)
            tp = len(detected_attack)
            fp = len(detected_benign)
            precision = tp / (tp + fp) if tp + fp else 1.0
            recall = tp / attack_cases if attack_cases else 0.0
            points.append(
                {
                    "threshold": float(finding["confidence"]),
                    "precision": precision,
                    "recall": recall,
                    "tp": float(tp),
                    "fp": float(fp),
                }
            )
        curves[tool_id] = points
    if "mtagentprism" not in curves:
        curves["mtagentprism"] = [
            {"threshold": 1.0, "precision": 1.0, "recall": 0.0, "tp": 0.0, "fp": 0.0},
            {
                "threshold": 0.0,
                "precision": 1.0,
                "recall": 1.0,
                "tp": float(attack_cases),
                "fp": 0.0,
            },
        ]
    return curves


def _scenario_labels(dataset_dir: Path) -> dict[str, str]:
    labels = {}
    for case in FrozenDatasetAdapter(dataset_dir).load_cases():
        labels[case.adapter_case.scenario_id] = "attack" if case.is_attack else "benign"
    return labels


def _mtagentprism_detected_scenarios(dataset_dir: Path) -> set[str]:
    cases = FrozenDatasetAdapter(dataset_dir).load_cases()
    return {
        finding.scenario_id
        for finding in load_mtagentprism_findings(cases)
    }


def _detected_scenarios_by_tool(path: Path) -> dict[str, set[str]]:
    detected_by_tool: dict[str, set[str]] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            item = json.loads(line)
            tool_id = str(item.get("tool_id") or "")
            scenario_id = str(item.get("scenario_id") or "")
            if tool_id and scenario_id:
                detected_by_tool.setdefault(tool_id, set()).add(scenario_id)
    return detected_by_tool


def _findings_by_tool(path: Path) -> dict[str, list[dict[str, Any]]]:
    findings_by_tool: dict[str, list[dict[str, Any]]] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            item = json.loads(line)
            item["confidence"] = float(item.get("confidence") or 0.0)
            findings_by_tool.setdefault(item["tool_id"], []).append(item)
    return findings_by_tool


def _parse_csv_value(value: str) -> Any:
    if value in {"True", "False"}:
        return value == "True"
    if value == "":
        return value
    if value.startswith("{") or value.startswith("["):
        return json.loads(value)
    try:
        if any(char in value for char in (".", "e", "E")):
            return float(value)
        return int(value)
    except ValueError:
        return value


def _tree_sha256(root: Path, *, include_suffixes: tuple[str, ...]) -> str:
    digest = hashlib.sha256()
    if not root.exists():
        return ""
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in include_suffixes:
            continue
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""


def _display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(resolved)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build paper evaluation report from completed experiment artifacts.")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--prompt-results", default=str(DEFAULT_PROMPT_RESULTS))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--static-lockfile", default=str(DEFAULT_STATIC_LOCKFILE))
    parser.add_argument("--prompt-lockfile", default=str(DEFAULT_PROMPT_LOCKFILE))
    args = parser.parse_args(argv)
    report = build_report(
        dataset_dir=Path(args.dataset),
        prompt_results_dir=Path(args.prompt_results),
        output_path=Path(args.output),
        static_lockfile=Path(args.static_lockfile),
        prompt_lockfile=Path(args.prompt_lockfile),
    )
    print(json.dumps({"output": args.output, "tools": [row["tool_id"] for row in report["main_metrics"]]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
