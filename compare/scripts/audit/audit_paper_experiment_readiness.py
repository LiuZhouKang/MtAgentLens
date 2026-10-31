#!/usr/bin/env python3
"""Evidence-gated audit for paper experiment readiness.

This script does not decide whether the paper idea is good. It checks whether
the repository currently contains the concrete artifacts needed to support the
experiment claims in the design document.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from compare.tools.registry import load_tool_metadata  # noqa: E402


DEFAULT_DATASET = PROJECT_ROOT / "datasets" / "FuxiBench"
DEFAULT_DESIGN = PROJECT_ROOT / "docs" / "superpowers" / "specs" / "2026-06-04-mtagentlens-experiment-design.md"
DEFAULT_LOCKFILE = PROJECT_ROOT / "compare" / "tool-lock.json"
DEFAULT_PROMPT_LOCKFILE = PROJECT_ROOT / "compare" / "tool-lock.prompt.json"
EXPECTED_STATIC_TOOLS = {
    "fixture-scanner",
    "gitleaks",
    "trufflehog",
    "semgrep",
    "detect-secrets",
    "yara-x",
}
PROMPT_BASELINE_TOOLS = {"llm-guard", "meta-prompt-guard", "vigil", "nemo-guardrails", "nemo", "agent-sentinel"}
RESULTS_ROOT = PROJECT_ROOT / "compare" / "results"
EXTERNAL_DEFERRED_POLICY_MARKER = "EXTERNAL_BENCHMARK_POLICY: deferred_from_main_claims"
PROMPT_TEXT_FORBIDDEN_METADATA_LINE = re.compile(
    r"^(scenario\s+id|mode|category|attack\s+type|label|risk[_\s-]*level|is[_\s-]*baseline)\s*:",
    re.IGNORECASE,
)
REQUIRED_PERFORMANCE_EXPERIMENTS = {"probe_latency", "throughput", "memory", "time_to_detect"}
REQUIRED_PERFORMANCE_PROVENANCE = {"git_commit", "kernel", "collector_binary_hash", "commands"}
REQUIRED_BACKEND_HEALTH_PROVENANCE = {"git_commit", "kernel", "collector_binary_hash", "runner"}
REQUIRED_BACKEND_HEALTH_CHECKS = {
    "collector_started",
    "collector_exit_status_checked",
    "persistence_lost_count_zero",
    "tls_drop_count_checked",
    "process_drop_count_checked",
    "db_schema_current",
    "db_nonempty_required_tables",
}
REQUIRED_CONTROLLED_ABLATION_PROVENANCE = {"dataset_hash", "git_commit", "runner"}
REQUIRED_CONTROLLED_ABLATION_DATASET_KEYS = {"version", "scenario_count"}
MIN_CONTROLLED_ABLATION_VARIANTS = 6
EXPECTED_CONTROLLED_ABLATION_SCENARIOS = 67


@dataclass(frozen=True)
class AuditCheck:
    id: str
    status: str
    summary: str
    evidence: dict[str, Any]
    recommendation: str


def audit(
    *,
    dataset_dir: Path = DEFAULT_DATASET,
    design_doc: Path = DEFAULT_DESIGN,
    lockfile: Path = DEFAULT_LOCKFILE,
    prompt_lockfile: Path = DEFAULT_PROMPT_LOCKFILE,
    results_root: Path = RESULTS_ROOT,
) -> dict[str, Any]:
    dataset_dir = dataset_dir.resolve()
    design_doc = design_doc.resolve()
    lockfile = lockfile.resolve()
    prompt_lockfile = prompt_lockfile.resolve()
    results_root = results_root.resolve()
    checks = [
        _check_design_doc_guard(design_doc),
        _check_frozen_dataset(dataset_dir),
        _check_dataset_metadata_consistency(dataset_dir),
        _check_attack_type_consistency(dataset_dir),
        _check_tool_lock(lockfile),
        _check_prompt_adapter_api_contract(),
        _check_prompt_baselines(dataset_dir, prompt_lockfile),
        _check_backend_capture_health(results_root),
        _check_external_benchmarks(dataset_dir, design_doc),
        _check_controlled_ablation_results(results_root),
        _check_performance_results(results_root),
        _check_statistical_report(results_root),
    ]
    status_counts: dict[str, int] = {}
    for check in checks:
        status_counts[check.status] = status_counts.get(check.status, 0) + 1
    overall_status = "blocked" if status_counts.get("blocker", 0) else "ready"
    return {
        "schema": "mtagentlens.paper_experiment_readiness.v1",
        "overall_status": overall_status,
        "status_counts": status_counts,
        "checks": [asdict(check) for check in checks],
    }


def _check_design_doc_guard(design_doc: Path) -> AuditCheck:
    text = _read_text(design_doc)
    first_lines = "\n".join(text.splitlines()[:20])
    has_guard = "Evidence-gated status" in first_lines or "evidence-gated" in first_lines.lower()
    return AuditCheck(
        id="design_doc_guard",
        status="pass" if has_guard else "blocker",
        summary="Experiment design document has an evidence-gated warning banner."
        if has_guard
        else "Experiment design document still presents unsupported claims as final design.",
        evidence={
            "design_doc": _display_path(design_doc),
            "has_evidence_gate_banner": has_guard,
        },
        recommendation=(
            "Keep the evidence-gated banner and claim-boundary matrix in the design document so completed, "
            "deferred, and unsupported claims remain separated during paper writing."
        ),
    )


def _check_frozen_dataset(dataset_dir: Path) -> AuditCheck:
    evaluate_dataset = _load_evaluate_frozen()
    try:
        result = evaluate_dataset(dataset_dir.resolve())
    except (OSError, ValueError) as exc:
        return AuditCheck(
            id="frozen_dataset_quality", status="blocker",
            summary="Frozen dataset inputs or canonical run artifacts failed validation.",
            evidence={"dataset": _display_path(dataset_dir), "error": str(exc)},
            recommendation="Build a versioned input release and recollect matching finalized run artifacts.",
        )
    metrics = result.get("metrics", {})
    gate = result.get("quality_gate", {})
    paper_metrics_available = bool(gate.get("paper_metrics_available"))
    return AuditCheck(
        id="frozen_dataset_quality",
        status="pass" if paper_metrics_available else "blocker",
        summary="Frozen dataset has paper metrics available."
        if paper_metrics_available
        else "Frozen dataset cannot yet support paper metrics.",
        evidence={
            "dataset": _display_path(dataset_dir),
            "total_cases": result.get("total_cases"),
            "attack_cases": result.get("attack_cases"),
            "benign_cases": result.get("benign_cases"),
            "paper_metrics_available": paper_metrics_available,
            "scenario_recall": metrics.get("scenario_recall"),
            "precision": metrics.get("precision"),
            "f1": metrics.get("f1"),
            "claim_coverage": metrics.get("claim_coverage"),
            "quality_gate_reason": gate.get("reason"),
            "unknown_runs": len(result.get("unknown_runs", [])),
            "uncertain_runs": len(result.get("uncertain_runs", [])),
            "quality_limited_runs": len(result.get("quality_limited_runs", [])),
            "attribution_unavailable_scenarios": result.get("attribution_unavailable_scenarios", []),
        },
        recommendation="Keep this gate green before changing experiment runners or ground truth.",
    )


def _check_dataset_metadata_consistency(dataset_dir: Path) -> AuditCheck:
    metadata_path = dataset_dir / "metadata.json"
    manifest_path = dataset_dir / "quality" / "manifest.json"
    problems: list[str] = []
    evidence: dict[str, Any] = {
        "metadata": _display_path(metadata_path),
        "manifest": _display_path(manifest_path),
    }
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return AuditCheck(
            id="dataset_metadata_consistency",
            status="blocker",
            summary="Dataset metadata or manifest cannot be loaded.",
            evidence={**evidence, "error": f"{type(exc).__name__}: {exc}"},
            recommendation="Regenerate metadata and quality manifest from the accepted frozen dataset.",
        )

    samples = manifest.get("samples", [])
    summary = manifest.get("summary", {})
    trace_db_count = sum(
        1
        for sample in samples
        if isinstance(sample, dict)
        and isinstance(sample.get("paths"), dict)
        and isinstance(sample["paths"].get("responsibility_db"), str)
        and (dataset_dir / sample["paths"]["responsibility_db"]).is_file()
    )
    total_samples = len(samples)
    paper_ready = summary.get("paper_ready")
    evidence.update(
        {
            "metadata_scenarios_total": metadata.get("scenarios_total"),
            "metadata_traces_collected": metadata.get("traces_collected"),
            "metadata_traces_excluded": metadata.get("traces_excluded"),
            "manifest_total_samples": total_samples,
            "manifest_paper_ready": paper_ready,
            "responsibility_db_count": trace_db_count,
        }
    )
    if metadata.get("scenarios_total") != total_samples:
        problems.append("metadata.scenarios_total does not match manifest sample count")
    if metadata.get("traces_collected") != trace_db_count:
        problems.append("metadata.traces_collected does not match responsibility DB count")
    if paper_ready == total_samples and metadata.get("traces_excluded", 0) not in (0, None):
        problems.append("metadata.traces_excluded is nonzero while manifest marks all samples paper_ready")

    status = "pass" if not problems else "blocker"
    return AuditCheck(
        id="dataset_metadata_consistency",
        status=status,
        summary="Dataset metadata matches the accepted quality manifest."
        if status == "pass"
        else "Dataset metadata is inconsistent with the accepted quality manifest.",
        evidence={**evidence, "problems": problems},
        recommendation="Update metadata.json whenever the frozen manifest is promoted or traces are replaced.",
    )


def _check_attack_type_consistency(dataset_dir: Path) -> AuditCheck:
    metadata_path = dataset_dir / "metadata.json"
    manifest_path = dataset_dir / "quality" / "manifest.json"
    prompt_manifest_path = dataset_dir / "prompt_inputs" / "manifest.json"
    evidence: dict[str, Any] = {
        "metadata": _display_path(metadata_path),
        "manifest": _display_path(manifest_path),
        "prompt_manifest": _display_path(prompt_manifest_path),
        "gt_attack_type_counts": {},
        "metadata_attack_type_counts": {},
        "prompt_attack_type_counts": {},
        "gt_prompt_mismatches": [],
        "gt_manifest_mismatches": [],
    }
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        prompt_manifest = json.loads(prompt_manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return AuditCheck(
            id="attack_type_consistency",
            status="blocker",
            summary="Dataset, ground-truth, or prompt-input metadata cannot be loaded for attack-type consistency.",
            evidence={**evidence, "error": f"{type(exc).__name__}: {exc}"},
            recommendation="Regenerate the frozen metadata, quality manifest, and prompt input manifest from the same GT source.",
        )

    samples = manifest.get("samples", [])
    prompt_by_id = {
        str(entry.get("scenario_id")): entry
        for entry in prompt_manifest.get("entries", [])
        if isinstance(entry, dict)
    }
    gt_counts: dict[str, int] = {}
    prompt_counts: dict[str, int] = {}
    gt_prompt_mismatches: list[dict[str, str]] = []
    gt_manifest_mismatches: list[dict[str, str]] = []
    for sample in samples:
        if not isinstance(sample, dict):
            continue
        scenario_id = str(sample.get("scenario_id", ""))
        paths = sample.get("paths", {})
        gt_rel = paths.get("ground_truth") if isinstance(paths, dict) else None
        if not isinstance(gt_rel, str):
            continue
        gt_path = dataset_dir / gt_rel
        try:
            gt = json.loads(gt_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        gt_type = _normalized_attack_type(gt)
        gt_counts[gt_type] = gt_counts.get(gt_type, 0) + 1
        prompt_type = str(prompt_by_id.get(scenario_id, {}).get("attack_type") or "")
        if prompt_type:
            prompt_counts[prompt_type] = prompt_counts.get(prompt_type, 0) + 1
            if prompt_type != gt_type:
                gt_prompt_mismatches.append(
                    {"scenario_id": scenario_id, "ground_truth": gt_type, "prompt_manifest": prompt_type}
                )
        manifest_gt = sample.get("ground_truth_summary", {})
        manifest_type = str(manifest_gt.get("attack_type") or "") if isinstance(manifest_gt, dict) else ""
        if manifest_type and manifest_type != gt_type:
            gt_manifest_mismatches.append(
                {"scenario_id": scenario_id, "ground_truth": gt_type, "quality_manifest": manifest_type}
            )

    metadata_counts = {
        "A": int(metadata.get("attack_types", {}).get("type_a_direct", 0) or 0),
        "B": int(metadata.get("attack_types", {}).get("type_b_indirect", 0) or 0),
        "C": int(metadata.get("attack_types", {}).get("type_c_multi_agent", 0) or 0),
        "benign": int(metadata.get("scenarios_benign", 0) or 0),
    }
    evidence.update(
        {
            "gt_attack_type_counts": dict(sorted(gt_counts.items())),
            "metadata_attack_type_counts": metadata_counts,
            "prompt_attack_type_counts": dict(sorted(prompt_counts.items())),
            "gt_prompt_mismatches": gt_prompt_mismatches,
            "gt_manifest_mismatches": gt_manifest_mismatches,
        }
    )
    problems = []
    for key in ("A", "B", "C", "benign"):
        if metadata_counts.get(key, 0) != gt_counts.get(key, 0):
            problems.append(f"metadata {key} count does not match ground truth")
        if prompt_counts and prompt_counts.get(key, 0) != gt_counts.get(key, 0):
            problems.append(f"prompt manifest {key} count does not match ground truth")
    if gt_prompt_mismatches:
        problems.append("prompt manifest attack_type differs from ground truth for one or more scenarios")
    if gt_manifest_mismatches:
        problems.append("quality manifest ground_truth_summary.attack_type differs from ground truth")

    status = "pass" if not problems else "blocker"
    return AuditCheck(
        id="attack_type_consistency",
        status=status,
        summary="Ground truth, dataset metadata, and prompt manifest use the same attack-type partition."
        if status == "pass"
        else "Ground truth, dataset metadata, and prompt manifest have inconsistent attack-type partitions.",
        evidence={**evidence, "problems": problems},
        recommendation="Use GT as the source of truth, regenerate prompt inputs and metadata, and do not report per-type metrics until this passes.",
    )


def _check_tool_lock(lockfile: Path) -> AuditCheck:
    try:
        tools = load_tool_metadata(lockfile)
        tool_ids = {tool.id for tool in tools}
        missing = sorted(EXPECTED_STATIC_TOOLS - tool_ids)
        unexpected_prompt = sorted(tool_ids & PROMPT_BASELINE_TOOLS)
        status = "pass" if not missing and not unexpected_prompt else "blocker"
        summary = (
            "Round 1 static tool lock is reproducible and intentionally excludes prompt baselines."
            if status == "pass"
            else "Tool lock is missing required static tools or includes prompt baselines before inputs are ready."
        )
        evidence = {
            "lockfile": _display_path(lockfile),
            "tool_ids": sorted(tool_ids),
            "missing_static_tools": missing,
            "prompt_tools_in_lockfile": unexpected_prompt,
        }
    except Exception as exc:  # noqa: BLE001 - audit reports the failure as evidence.
        status = "blocker"
        summary = "Tool lock cannot be loaded."
        evidence = {"lockfile": _display_path(lockfile), "error": f"{type(exc).__name__}: {exc}"}

    return AuditCheck(
        id="tool_lock_reproducibility",
        status=status,
        summary=summary,
        evidence=evidence,
        recommendation="Use pinned local/static tools for Round 1; add prompt baselines only after raw prompt inputs exist.",
    )


def _check_prompt_baselines(dataset_dir: Path, prompt_lockfile: Path) -> AuditCheck:
    tool_ids = set()
    load_error = None
    try:
        prompt_tools = load_tool_metadata(prompt_lockfile)
        tool_ids = {tool.id for tool in prompt_tools}
    except Exception as exc:  # noqa: BLE001 - audit reports the failure as evidence.
        prompt_tools = []
        load_error = f"{type(exc).__name__}: {exc}"
    prompt_tools_present = sorted(tool_ids & PROMPT_BASELINE_TOOLS)
    prompt_inputs_dir = dataset_dir / "prompt_inputs"
    prompt_manifest_path = prompt_inputs_dir / "manifest.json"
    manifest_evidence = _prompt_manifest_evidence(prompt_inputs_dir, prompt_manifest_path)
    prompt_modes_by_tool = {
        tool.id: sorted(tool.input_modes)
        for tool in prompt_tools
        if tool.id in PROMPT_BASELINE_TOOLS
    }
    required_tools_ready = {"llm-guard", "meta-prompt-guard", "vigil"}.issubset(set(prompt_tools_present))
    all_prompt_tools_use_prompt_text = all(
        "prompt_text" in modes for modes in prompt_modes_by_tool.values()
    )
    dataset_error = None
    expected_cases = 0
    try:
        from compare.tools.datasets import FrozenDatasetAdapter
        expected_cases = len(FrozenDatasetAdapter(dataset_dir).load_cases())
    except (OSError, ValueError) as exc:
        dataset_error = str(exc)
    status = (
        "pass"
        if load_error is None and dataset_error is None and expected_cases > 0
        and required_tools_ready
        and all_prompt_tools_use_prompt_text
        and manifest_evidence["manifest_valid"]
        and manifest_evidence["prompt_manifest_total_cases"] == expected_cases
        and manifest_evidence["prompt_text_files"] == expected_cases
        and manifest_evidence["bundle_json_files"] == expected_cases
        and manifest_evidence["entries_without_no_gt_leak_guarantee"] == []
        and manifest_evidence["attack_entries_without_fixture_text"] == []
        and manifest_evidence["entries_with_attack_type_mismatch"] == []
        and manifest_evidence["prompt_text_label_leaks"] == []
        and manifest_evidence["prompt_text_hash_mismatches"] == []
        else "blocker"
    )
    return AuditCheck(
        id="prompt_baseline_input_contract",
        status=status,
        summary="Prompt-level baseline inputs and tool registrations are ready."
        if status == "pass"
        else "Prompt-level baselines are not yet fairly evaluable on the frozen dataset.",
        evidence={
            "prompt_lockfile": _display_path(prompt_lockfile),
            "prompt_lockfile_error": load_error,
            "dataset_error": dataset_error,
            "expected_cases": expected_cases,
            "prompt_tools_present": prompt_tools_present,
            "prompt_modes_by_tool": prompt_modes_by_tool,
            **manifest_evidence,
        },
        recommendation=(
            "Create a frozen prompt-input bundle with raw agent task text and fixture text, then add pinned "
            "prompt baseline adapters in compare/tool-lock.prompt.json. Until then, do not report prompt-scanner F1."
        ),
    )


def _prompt_manifest_evidence(prompt_inputs_dir: Path, prompt_manifest_path: Path) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "prompt_inputs_dir": _display_path(prompt_inputs_dir),
        "prompt_manifest": _display_path(prompt_manifest_path),
        "prompt_manifest_exists": prompt_manifest_path.exists(),
        "manifest_valid": False,
        "prompt_manifest_total_cases": 0,
        "prompt_text_files": 0,
        "bundle_json_files": 0,
        "entries_without_no_gt_leak_guarantee": [],
        "attack_entries_without_fixture_text": [],
        "entries_with_attack_type_mismatch": [],
        "prompt_text_label_leaks": [],
        "prompt_text_hash_mismatches": [],
    }
    if not prompt_manifest_path.exists():
        return evidence
    try:
        manifest = json.loads(prompt_manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        evidence["manifest_error"] = f"JSONDecodeError: {exc.msg}"
        return evidence

    entries = manifest.get("entries", [])
    if not isinstance(entries, list):
        evidence["manifest_error"] = "entries is not a list"
        return evidence
    evidence["manifest_valid"] = manifest.get("schema") == "mtagentlens.prompt_inputs.v1"
    evidence["prompt_manifest_total_cases"] = len(entries)
    missing_leak_guarantee: list[str] = []
    attack_entries_without_fixture_text: list[str] = []
    attack_type_mismatches: list[dict[str, str]] = []
    label_leaks: list[dict[str, Any]] = []
    hash_mismatches: list[str] = []
    prompt_text_files = 0
    bundle_json_files = 0
    for entry in entries:
        if not isinstance(entry, dict):
            evidence["manifest_valid"] = False
            continue
        scenario_id = str(entry.get("scenario_id", ""))
        if entry.get("sanitized_no_ground_truth_leak") is not True:
            missing_leak_guarantee.append(scenario_id)
        gt_type = _ground_truth_attack_type_for(dataset_dir=prompt_inputs_dir.parent, scenario_id=scenario_id)
        prompt_type = str(entry.get("attack_type") or "")
        if gt_type and prompt_type and gt_type != prompt_type:
            attack_type_mismatches.append(
                {"scenario_id": scenario_id, "ground_truth": gt_type, "prompt_manifest": prompt_type}
            )
        if gt_type in {"B", "C"} and int(entry.get("fixture_file_count") or 0) <= 0:
            attack_entries_without_fixture_text.append(scenario_id)
        prompt_path = entry.get("prompt_text_path")
        if isinstance(prompt_path, str) and (prompt_inputs_dir / prompt_path).is_file():
            prompt_text_files += 1
            prompt_file = prompt_inputs_dir / prompt_path
            try:
                prompt_text = prompt_file.read_text(encoding="utf-8")
            except OSError:
                prompt_text = ""
            label_leaks.extend(
                {"scenario_id": scenario_id, **leak}
                for leak in _prompt_text_label_leaks(prompt_text)
            )
            expected_hash = str(entry.get("prompt_text_sha256") or "")
            if expected_hash and _sha256_text(prompt_text) != expected_hash:
                hash_mismatches.append(scenario_id)
        bundle_path = entry.get("bundle_json_path")
        if isinstance(bundle_path, str) and (prompt_inputs_dir / bundle_path).is_file():
            bundle_json_files += 1
    evidence["prompt_text_files"] = prompt_text_files
    evidence["bundle_json_files"] = bundle_json_files
    evidence["entries_without_no_gt_leak_guarantee"] = missing_leak_guarantee
    evidence["attack_entries_without_fixture_text"] = attack_entries_without_fixture_text
    evidence["entries_with_attack_type_mismatch"] = attack_type_mismatches
    evidence["prompt_text_label_leaks"] = label_leaks
    evidence["prompt_text_hash_mismatches"] = hash_mismatches
    return evidence


def _prompt_text_label_leaks(prompt_text: str) -> list[dict[str, Any]]:
    """Forbidden evaluator metadata in the task section only.

    Fixture text is the scenario's own material and may legitimately contain
    `mode: str` or `category: ...` (dataclass fields, config keys). The
    contract is that the *task/prompt* does not leak evaluator metadata; the
    builder redacts those lines there, and this check must look in the same
    place, or it flags ordinary fixture code as a leak.
    """
    task_text = prompt_text.split("\n## Fixture Text", 1)[0]
    leaks: list[dict[str, Any]] = []
    for line_no, line in enumerate(task_text.splitlines(), start=1):
        match = PROMPT_TEXT_FORBIDDEN_METADATA_LINE.match(line)
        if match is None:
            continue
        leaks.append({"line": line_no, "field": match.group(1), "text": line.strip()[:120]})
    return leaks


def _ground_truth_attack_type_for(*, dataset_dir: Path, scenario_id: str) -> str:
    ground_truth_dir = dataset_dir / "ground_truth"
    direct = ground_truth_dir / f"{scenario_id}.json"
    candidates = [direct] if direct.exists() else sorted(ground_truth_dir.rglob(f"{scenario_id}.json"))
    for candidate in candidates:
        if "_external_archive" in candidate.parts:
            continue
        try:
            return _normalized_attack_type(json.loads(candidate.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return ""


def _normalized_attack_type(ground_truth: dict[str, Any]) -> str:
    attack_type = ground_truth.get("attack_type")
    label = ground_truth.get("label")
    category = ground_truth.get("category")
    if label == "benign" or category == "benign" or attack_type == "benign":
        return "benign"
    return str(attack_type or "")


def _check_prompt_adapter_api_contract() -> AuditCheck:
    adapter_dir = PROJECT_ROOT / "compare" / "tools" / "adapters"
    incompatible: dict[str, list[str]] = {}
    for adapter_name in ("llm_guard.py", "meta_prompt_guard.py", "vigil.py"):
        path = adapter_dir / adapter_name
        text = _read_text(path)
        problems: list[str] = []
        adapter_result_blocks = _constructor_blocks(text, "AdapterResult")
        if any("metadata=" in block for block in adapter_result_blocks):
            problems.append("uses legacy AdapterResult(metadata=...) instead of AdapterResult(tool=...)")
        if "RunStatus.SUCCEEDED" in text:
            problems.append("uses legacy RunStatus.SUCCEEDED; current enum uses SUCCESS/NO_FINDINGS/FAILED")
        if any("dataset_name=" in block for block in adapter_result_blocks):
            problems.append("passes legacy AdapterResult(dataset_name=...) field")
        if any(
            "raw_archive_path=" in block or "elapsed_sec=" in block or "error_message=" in block
            for block in adapter_result_blocks
        ):
            problems.append("passes legacy AdapterResult raw_archive_path/elapsed_sec/error_message fields")
        if "evidence={" in text:
            problems.append("passes dict evidence to FindingRecord; current contract expects string evidence")
        if 'boundary_type="ContentBoundary"' in text:
            problems.append("passes string boundary_type; current contract expects BoundaryType enum or None")
        if problems:
            incompatible[adapter_name] = problems

    status = "pass" if not incompatible else "blocker"
    return AuditCheck(
        id="prompt_adapter_api_contract",
        status=status,
        summary="Prompt baseline adapters match the current runner API."
        if status == "pass"
        else "Prompt baseline adapters are not compatible with the current runner API.",
        evidence={"incompatible_adapters": incompatible},
        recommendation=(
            "Port LLM Guard, Meta Prompt Guard, Vigil, and NeMo wrappers to compare.tools.base.AdapterResult/"
            "FindingRecord before adding them to the paper lockfile."
        ),
    )


def _check_external_benchmarks(dataset_dir: Path, design_doc: Path) -> AuditCheck:
    archive_dir = dataset_dir / "ground_truth" / "_external_archive"
    archive_count = len(list(archive_dir.glob("*.json"))) if archive_dir.is_dir() else 0
    design_text = _read_text(design_doc)
    claims_401_external = _design_claims_401_external(design_text)
    external_policy_deferred = EXTERNAL_DEFERRED_POLICY_MARKER in design_text
    passing = (claims_401_external and archive_count >= 401) or (not claims_401_external and external_policy_deferred)
    status = "pass" if passing else "blocker"
    if claims_401_external and archive_count >= 401:
        summary = "External benchmark inventory supports the 401 external scenario claim."
    elif not claims_401_external and external_policy_deferred:
        summary = "External benchmark claim is explicitly deferred from the main paper design."
    else:
        summary = "External benchmark inventory does not support the 401 external / 468 total claim."
    return AuditCheck(
        id="external_benchmark_evidence",
        status=status,
        summary=summary,
        evidence={
            "external_archive_json_count": archive_count,
            "design_claims_401_external": claims_401_external,
            "external_policy_deferred_from_main_claims": external_policy_deferred,
            "archive_dir": _display_path(archive_dir),
        },
        recommendation=(
            "Either import and label the 401 external cases with a reproducible manifest, or keep the 468-benchmark "
            "claim explicitly deferred from the main paper design."
        ),
    )


def _design_claims_401_external(design_text: str) -> bool:
    """Detect completed main-design claims, not limitations/future-work mentions."""
    external_patterns = (
        r"\b401\s+external\b",
        r"\b67\s*\+\s*401\b",
        r"\b468\s+(?:benchmarks?|total|scenarios?)\b",
    )
    for line in design_text.splitlines():
        normalized = line.strip()
        if not normalized:
            continue
        lower = normalized.lower()
        if "external_benchmark_policy" in lower:
            continue
        if any(
            marker in lower
            for marker in (
                "do not",
                "does not",
                "unsupported",
                "forbidden",
                "future work",
                "deferred",
                "pending",
                "blocked",
                "not claim",
                "cannot",
                "must not",
            )
        ):
            continue
        if any(re.search(pattern, normalized, re.IGNORECASE) for pattern in external_patterns):
            return True
    return False


def _check_backend_capture_health(results_root: Path = RESULTS_ROOT) -> AuditCheck:
    health_dir = results_root / "backend_health"
    canonical_path = health_dir / "capture_health.json"
    candidate_paths = [canonical_path] if canonical_path.exists() else []
    if health_dir.exists():
        for path in sorted(health_dir.rglob("*.json")):
            if path not in candidate_paths:
                candidate_paths.append(path)

    validations = [_validate_backend_health_artifact(path) for path in candidate_paths]
    passing = [item for item in validations if item.get("valid") is True]
    status = "pass" if passing else "blocker"
    return AuditCheck(
        id="backend_capture_health",
        status=status,
        summary="Completed backend capture-health artifact is present."
        if status == "pass"
        else "Backend capture-health evidence is missing or incomplete.",
        evidence={
            "expected_artifact": _display_path(canonical_path),
            "backend_health_artifacts": [_display_path(path) for path in candidate_paths],
            "validations": validations,
        },
        recommendation=(
            "Add a run-level health artifact that proves collector startup was fatal-on-failure, persistence lost_count "
            "was zero, TLS/process drop counters were checked, and frozen DB schema/table counts passed."
        ),
    )


def _check_controlled_ablation_results(results_root: Path = RESULTS_ROOT) -> AuditCheck:
    report_path = results_root / "paper" / "evaluation_report.json"
    controlled_path = results_root / "paper" / "controlled_ablation_results.json"
    legacy_ablation_path = results_root / "paper" / "ablation_results.json"
    evidence: dict[str, Any] = {
        "report_path": _display_path(report_path),
        "report_exists": report_path.exists(),
        "controlled_ablation_results_path": _display_path(controlled_path),
        "controlled_ablation_results_exists": controlled_path.exists(),
        "legacy_ablation_results_path": _display_path(legacy_ablation_path),
        "legacy_ablation_results_exists": legacy_ablation_path.exists(),
    }
    validation = _validate_controlled_ablation_artifact(controlled_path)
    evidence["validation"] = validation
    if legacy_ablation_path.exists():
        evidence["legacy_validation"] = _validate_controlled_ablation_artifact(legacy_ablation_path)
    status = "pass" if validation.get("valid") is True else "blocker"
    summary = (
        "Controlled feature-flag ablation results are present."
        if status == "pass"
        else "Controlled ablation results are missing or not completed controlled feature-flag replay artifacts."
    )
    if controlled_path.exists():
        evidence["ablation_schema"] = validation.get("schema")
        evidence["ablation_methodology"] = validation.get("methodology")
        evidence["is_controlled_feature_flag_replay"] = validation.get("is_controlled_feature_flag_replay")
        evidence["variant_count"] = validation.get("variant_count", 0)
    elif report_path.exists():
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            metadata = report.get("_metadata", {})
            controlled_status = str(metadata.get("controlled_ablation_status", ""))
            evidence["controlled_ablation_status"] = controlled_status
        except json.JSONDecodeError as exc:
            evidence["error"] = f"JSONDecodeError: {exc.msg}"
            summary = "Controlled ablation report exists but is not valid JSON."
    return AuditCheck(
        id="controlled_ablation_results",
        status=status,
        summary=summary,
        evidence=evidence,
        recommendation=(
            "Use compare/results/paper/controlled_ablation_results.json as the canonical completed artifact. "
            "Keep older offline evidence-masking outputs as diagnostic-only artifacts."
        ),
    )


def _validate_controlled_ablation_artifact(path: Path) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "path": _display_path(path),
        "exists": path.exists(),
        "valid": False,
        "problems": [],
    }
    if not path.exists():
        evidence["problems"].append("artifact does not exist")
        return evidence
    try:
        artifact = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        evidence["problems"].append(f"invalid JSON: {exc.msg}")
        return evidence
    except OSError as exc:
        evidence["problems"].append(f"cannot read artifact: {exc}")
        return evidence

    if not isinstance(artifact, dict):
        evidence["problems"].append("artifact root is not an object")
        return evidence

    evidence["schema"] = artifact.get("schema")
    evidence["status"] = artifact.get("status")
    evidence["methodology"] = artifact.get("methodology")
    evidence["is_controlled_feature_flag_replay"] = artifact.get("is_controlled_feature_flag_replay")
    if artifact.get("schema") != "mtagentlens.controlled_ablation_results.v1":
        evidence["problems"].append("schema must be mtagentlens.controlled_ablation_results.v1")
    if artifact.get("status") != "completed":
        evidence["problems"].append("status must be completed")
    if artifact.get("is_controlled_feature_flag_replay") is not True:
        evidence["problems"].append("is_controlled_feature_flag_replay must be true")
    if artifact.get("methodology") == "offline_evidence_masking":
        evidence["problems"].append("offline_evidence_masking is not controlled feature-flag replay")

    dataset = artifact.get("dataset")
    if not isinstance(dataset, dict):
        evidence["dataset_keys"] = []
        evidence["problems"].append("dataset must be an object")
    else:
        dataset_keys = set(dataset)
        evidence["dataset_keys"] = sorted(dataset_keys)
        missing = sorted(REQUIRED_CONTROLLED_ABLATION_DATASET_KEYS - dataset_keys)
        evidence["missing_dataset_keys"] = missing
        if missing:
            evidence["problems"].append(f"missing dataset keys: {', '.join(missing)}")
        scenario_count = dataset.get("scenario_count")
        evidence["dataset_scenario_count"] = scenario_count
        if scenario_count != EXPECTED_CONTROLLED_ABLATION_SCENARIOS:
            evidence["problems"].append(
                f"dataset.scenario_count must be {EXPECTED_CONTROLLED_ABLATION_SCENARIOS}"
            )

    provenance = artifact.get("provenance")
    if not isinstance(provenance, dict):
        evidence["provenance_keys"] = []
        evidence["problems"].append("provenance must be an object")
    else:
        provenance_keys = set(provenance)
        missing = sorted(REQUIRED_CONTROLLED_ABLATION_PROVENANCE - provenance_keys)
        evidence["provenance_keys"] = sorted(provenance_keys)
        evidence["missing_provenance"] = missing
        if missing:
            evidence["problems"].append(f"missing provenance keys: {', '.join(missing)}")

    baseline = artifact.get("baseline")
    if not isinstance(baseline, dict):
        evidence["problems"].append("baseline must be an object")
    else:
        evidence["baseline_variant_id"] = baseline.get("variant_id")
        parity = baseline.get("parity")
        if baseline.get("status") != "completed":
            evidence["problems"].append("baseline.status must be completed")
        if baseline.get("runs_completed") != EXPECTED_CONTROLLED_ABLATION_SCENARIOS:
            evidence["problems"].append(
                f"baseline.runs_completed must be {EXPECTED_CONTROLLED_ABLATION_SCENARIOS}"
            )
        if not isinstance(baseline.get("metrics"), dict):
            evidence["problems"].append("baseline.metrics must be an object")
        if not isinstance(parity, dict) or parity.get("status") != "pass":
            evidence["problems"].append("baseline.parity.status must be pass")

    variants = artifact.get("variants")
    if not isinstance(variants, list):
        evidence["variant_count"] = 0
        evidence["problems"].append("variants must be a list")
    else:
        evidence["variant_count"] = len(variants)
        if len(variants) < MIN_CONTROLLED_ABLATION_VARIANTS:
            evidence["problems"].append(
                f"variants must contain at least {MIN_CONTROLLED_ABLATION_VARIANTS} completed variants"
            )
        incomplete = []
        bad_runs = []
        missing_disabled_features = []
        missing_outcomes = []
        for index, item in enumerate(variants):
            variant_id = str(item.get("variant_id") if isinstance(item, dict) else index)
            if not isinstance(item, dict) or item.get("status") != "completed":
                incomplete.append(variant_id)
                continue
            if item.get("runs_completed") != EXPECTED_CONTROLLED_ABLATION_SCENARIOS:
                bad_runs.append(variant_id)
            disabled_features = item.get("disabled_features")
            if not isinstance(disabled_features, list) or not disabled_features:
                missing_disabled_features.append(variant_id)
            if not isinstance(item.get("metrics"), dict):
                evidence["problems"].append(f"variant {variant_id} metrics must be an object")
            if not isinstance(item.get("delta_vs_full"), dict):
                evidence["problems"].append(f"variant {variant_id} delta_vs_full must be an object")
            outcomes = item.get("per_scenario_outcomes")
            if not isinstance(outcomes, list) or len(outcomes) != EXPECTED_CONTROLLED_ABLATION_SCENARIOS:
                missing_outcomes.append(variant_id)
        evidence["incomplete_variants"] = incomplete
        evidence["variants_with_bad_run_counts"] = bad_runs
        evidence["variants_missing_disabled_features"] = missing_disabled_features
        evidence["variants_missing_per_scenario_outcomes"] = missing_outcomes
        if incomplete:
            evidence["problems"].append("all variants must have status completed")
        if bad_runs:
            evidence["problems"].append(
                f"all variants must run {EXPECTED_CONTROLLED_ABLATION_SCENARIOS} scenarios"
            )
        if missing_disabled_features:
            evidence["problems"].append("all variants must declare non-empty disabled_features")
        if missing_outcomes:
            evidence["problems"].append(
                f"all variants must include {EXPECTED_CONTROLLED_ABLATION_SCENARIOS} per-scenario outcomes"
            )

    claim_scope = artifact.get("claim_scope")
    if not isinstance(claim_scope, dict):
        evidence["problems"].append("claim_scope must be an object")
    else:
        supported = claim_scope.get("supported_claims")
        unsupported = claim_scope.get("unsupported_claims")
        if not isinstance(supported, list) or not supported:
            evidence["problems"].append("claim_scope.supported_claims must be a non-empty list")
        if not isinstance(unsupported, list):
            evidence["problems"].append("claim_scope.unsupported_claims must be a list")

    evidence["valid"] = not evidence["problems"]
    return evidence


def _check_performance_results(results_root: Path = RESULTS_ROOT) -> AuditCheck:
    perf_dir = results_root / "perf"
    preferred_paths = [perf_dir / "performance_results.json", perf_dir / "perf_report.json"]
    candidate_paths = [path for path in preferred_paths if path.exists()]
    if perf_dir.exists():
        for path in sorted(perf_dir.rglob("*.json")):
            if path not in candidate_paths:
                candidate_paths.append(path)

    validations = [_validate_performance_artifact(path) for path in candidate_paths]
    passing = [item for item in validations if item.get("valid") is True]
    status = "pass" if passing else "blocker"
    return AuditCheck(
        id="performance_results",
        status=status,
        summary="Completed performance result artifact is present."
        if status == "pass"
        else "Performance artifacts are missing or not completed result artifacts.",
        evidence={
            "perf_dir": _display_path(perf_dir),
            "expected_artifacts": [_display_path(path) for path in preferred_paths],
            "perf_artifacts": [_display_path(path) for path in candidate_paths],
            "validations": validations,
        },
        recommendation="Run probe latency, throughput, memory, storage, and time-to-detect benchmarks before reporting runtime cost.",
    )


def _validate_backend_health_artifact(path: Path) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "path": _display_path(path),
        "exists": path.exists(),
        "valid": False,
        "problems": [],
    }
    if not path.exists():
        evidence["problems"].append("artifact does not exist")
        return evidence
    try:
        artifact = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        evidence["problems"].append(f"invalid JSON: {exc.msg}")
        return evidence
    except OSError as exc:
        evidence["problems"].append(f"cannot read artifact: {exc}")
        return evidence

    if not isinstance(artifact, dict):
        evidence["problems"].append("artifact root is not an object")
        return evidence

    evidence["schema"] = artifact.get("schema")
    evidence["status"] = artifact.get("status")
    if artifact.get("schema") != "mtagentlens.backend_capture_health.v1":
        evidence["problems"].append("schema must be mtagentlens.backend_capture_health.v1")
    if artifact.get("status") != "completed":
        evidence["problems"].append("status must be completed")

    provenance = artifact.get("provenance")
    if not isinstance(provenance, dict):
        evidence["provenance_keys"] = []
        evidence["problems"].append("provenance must be an object")
    else:
        provenance_keys = set(provenance)
        missing = sorted(REQUIRED_BACKEND_HEALTH_PROVENANCE - provenance_keys)
        evidence["provenance_keys"] = sorted(provenance_keys)
        evidence["missing_provenance"] = missing
        if missing:
            evidence["problems"].append(f"missing provenance keys: {', '.join(missing)}")

    checks = artifact.get("checks")
    if not isinstance(checks, dict):
        evidence["check_keys"] = []
        evidence["problems"].append("checks must be an object")
    else:
        check_keys = set(checks)
        missing = sorted(REQUIRED_BACKEND_HEALTH_CHECKS - check_keys)
        evidence["check_keys"] = sorted(check_keys)
        evidence["missing_checks"] = missing
        if missing:
            evidence["problems"].append(f"missing backend health checks: {', '.join(missing)}")
        failing = []
        for check_name in sorted(REQUIRED_BACKEND_HEALTH_CHECKS & check_keys):
            check_value = checks.get(check_name)
            if not isinstance(check_value, dict) or check_value.get("status") != "pass":
                failing.append(check_name)
        evidence["failing_checks"] = failing
        if failing:
            evidence["problems"].append("all required backend health checks must have status pass")

    runs = artifact.get("runs")
    if not isinstance(runs, list):
        evidence["run_count"] = 0
        evidence["problems"].append("runs must be a list")
    else:
        evidence["run_count"] = len(runs)
        if not runs:
            evidence["problems"].append("runs must be non-empty")
        incomplete_runs = [
            str(item.get("run_id") or index)
            for index, item in enumerate(runs)
            if not isinstance(item, dict) or item.get("status") != "completed"
        ]
        evidence["incomplete_runs"] = incomplete_runs
        if incomplete_runs:
            evidence["problems"].append("all backend health runs must have status completed")

    counters = artifact.get("counters", {})
    if isinstance(counters, dict):
        nonzero_loss = {
            key: value
            for key, value in counters.items()
            if key.endswith(("lost_count", "drop_count", "dropped_events"))
            and isinstance(value, (int, float))
            and value != 0
        }
        evidence["nonzero_loss_counters"] = nonzero_loss
        if nonzero_loss:
            evidence["problems"].append("drop/loss counters must be zero for completed backend health")

    evidence["valid"] = not evidence["problems"]
    return evidence


def _validate_performance_artifact(path: Path) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "path": _display_path(path),
        "exists": path.exists(),
        "valid": False,
        "problems": [],
    }
    if not path.exists():
        evidence["problems"].append("artifact does not exist")
        return evidence
    try:
        artifact = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        evidence["problems"].append(f"invalid JSON: {exc.msg}")
        return evidence
    except OSError as exc:
        evidence["problems"].append(f"cannot read artifact: {exc}")
        return evidence

    if not isinstance(artifact, dict):
        evidence["problems"].append("artifact root is not an object")
        return evidence

    evidence["schema"] = artifact.get("schema")
    evidence["status"] = artifact.get("status")
    if artifact.get("schema") != "mtagentlens.performance_results.v1":
        evidence["problems"].append("schema must be mtagentlens.performance_results.v1")
    if artifact.get("status") != "completed":
        evidence["problems"].append("status must be completed")

    provenance = artifact.get("provenance")
    if not isinstance(provenance, dict):
        evidence["provenance_keys"] = []
        evidence["problems"].append("provenance must be an object")
    else:
        provenance_keys = set(provenance)
        missing = sorted(REQUIRED_PERFORMANCE_PROVENANCE - provenance_keys)
        evidence["provenance_keys"] = sorted(provenance_keys)
        evidence["missing_provenance"] = missing
        if missing:
            evidence["problems"].append(f"missing provenance keys: {', '.join(missing)}")
        commands = provenance.get("commands")
        if "commands" in provenance and (not isinstance(commands, list) or not commands):
            evidence["problems"].append("provenance.commands must be a non-empty list")

    experiments = artifact.get("experiments")
    if not isinstance(experiments, dict):
        evidence["experiment_keys"] = []
        evidence["problems"].append("experiments must be an object")
    else:
        experiment_keys = set(experiments)
        missing = sorted(REQUIRED_PERFORMANCE_EXPERIMENTS - experiment_keys)
        evidence["experiment_keys"] = sorted(experiment_keys)
        evidence["missing_experiments"] = missing
        if missing:
            evidence["problems"].append(f"missing performance experiments: {', '.join(missing)}")
        incomplete = []
        empty_runs = []
        for name in sorted(REQUIRED_PERFORMANCE_EXPERIMENTS & experiment_keys):
            experiment = experiments.get(name)
            if not isinstance(experiment, dict):
                incomplete.append(name)
                continue
            if experiment.get("status") != "completed":
                incomplete.append(name)
            runs = experiment.get("runs")
            if not isinstance(runs, list) or not runs:
                empty_runs.append(name)
        evidence["incomplete_experiments"] = incomplete
        evidence["experiments_without_runs"] = empty_runs
        if incomplete:
            evidence["problems"].append("all required performance experiments must have status completed")
        if empty_runs:
            evidence["problems"].append("all required performance experiments must include non-empty runs")

    evidence["valid"] = not evidence["problems"]
    return evidence


def _check_statistical_report(results_root: Path = RESULTS_ROOT) -> AuditCheck:
    report_path = results_root / "paper" / "evaluation_report.json"
    if not report_path.exists():
        return AuditCheck(
            id="statistical_report",
            status="blocker",
            summary="Paper statistical report is missing.",
            evidence={"report_path": _display_path(report_path), "exists": False},
            recommendation="Generate the paper report only after baseline and ablation protocols are fixed.",
        )
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return AuditCheck(
            id="statistical_report",
            status="blocker",
            summary="Paper statistical report is invalid JSON.",
            evidence={"report_path": _display_path(report_path), "error": exc.msg},
            recommendation="Regenerate the report from deterministic inputs.",
        )
    has_stats = all(key in report for key in ("main_metrics", "mcnemar", "pr_curves"))
    schema_ok = report.get("schema") == "mtagentlens.paper_evaluation_report.v1"
    return AuditCheck(
        id="statistical_report",
        status="pass" if has_stats and schema_ok else "blocker",
        summary="Paper statistical report has main metric, McNemar, and PR-curve sections."
        if has_stats and schema_ok
        else "Paper statistical report lacks required statistical sections.",
        evidence={
            "report_path": _display_path(report_path),
            "schema_ok": schema_ok,
            "required_sections_present": has_stats,
        },
        recommendation="Use paired tests and bootstrap CIs only after all compared tools have fair, metric-eligible runs.",
    )


def _load_evaluate_frozen():
    path = PROJECT_ROOT / "env" / "scripts" / "evaluate_frozen.py"
    spec = importlib.util.spec_from_file_location("mtagentlens_evaluate_frozen", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.evaluate_dataset


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _constructor_blocks(text: str, constructor: str) -> list[str]:
    starts = [match.start() for match in re.finditer(rf"\b{re.escape(constructor)}\(", text)]
    blocks: list[str] = []
    for start in starts:
        depth = 0
        for index in range(start, len(text)):
            char = text[index]
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    blocks.append(text[start : index + 1])
                    break
    return blocks


def _display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def render_markdown(result: dict[str, Any]) -> str:
    lines = [
        "# MtAgentLens Paper Experiment Readiness Audit",
        "",
        f"Overall status: `{result['overall_status']}`",
        "",
        "| Check | Status | Summary |",
        "|---|---:|---|",
    ]
    for check in result["checks"]:
        lines.append(f"| `{check['id']}` | `{check['status']}` | {check['summary']} |")
    lines.extend(["", "## Blocking Details", ""])
    for check in result["checks"]:
        if check["status"] != "blocker":
            continue
        lines.extend(
            [
                f"### {check['id']}",
                "",
                check["summary"],
                "",
                f"Recommendation: {check['recommendation']}",
                "",
                "Evidence:",
                "",
                "```json",
                json.dumps(check["evidence"], ensure_ascii=False, indent=2, sort_keys=True),
                "```",
                "",
            ]
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit paper experiment readiness.")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--design-doc", default=str(DEFAULT_DESIGN))
    parser.add_argument("--lockfile", default=str(DEFAULT_LOCKFILE))
    parser.add_argument("--prompt-lockfile", default=str(DEFAULT_PROMPT_LOCKFILE))
    parser.add_argument("--json", action="store_true", help="Print JSON instead of Markdown.")
    parser.add_argument("--markdown-output", default="", help="Write a Markdown report to this path.")
    parser.add_argument("--allow-blockers", action="store_true", help="Return success even when blockers are present.")
    args = parser.parse_args(argv)

    result = audit(
        dataset_dir=Path(args.dataset),
        design_doc=Path(args.design_doc),
        lockfile=Path(args.lockfile),
        prompt_lockfile=Path(args.prompt_lockfile),
    )
    if args.markdown_output:
        output_path = Path(args.markdown_output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(render_markdown(result) + "\n", encoding="utf-8")

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(render_markdown(result))

    if result["overall_status"] == "blocked" and not args.allow_blockers:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
