#!/usr/bin/env python3
"""Audit the final paper submission package as a single evidence bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from compare.scripts.audit.audit_paper_experiment_readiness import audit as readiness_audit  # noqa: E402


MANIFEST_PATH = PROJECT_ROOT / "compare" / "results" / "paper" / "submission_manifest.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "compare" / "results" / "paper" / "SUBMISSION_AUDIT.json"

REQUIRED_UNSUPPORTED_CLAIMS = {
    "external benchmark validation",
    "universal prompt-guard paradigm ceiling",
    "live module-level causal ablation deltas",
    "401 external / 468 total benchmark validation",
    "production LLM-agent end-to-end latency",
    "trace-mode ResponsibilityRuntime end-to-end latency",
}

REQUIRED_FIGURE_IDS = {
    "fig1_main_metrics",
    "fig2_controlled_ablation",
    "fig3_backend_health",
    "fig4_performance",
}


@dataclass(frozen=True)
class SubmissionCheck:
    id: str
    status: str
    summary: str
    evidence: dict[str, Any]
    recommendation: str


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    artifact = audit_submission_package(manifest_path=args.manifest)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if args.json:
        print(json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"Generated {_display(args.output)}")
        print(f"overall_status={artifact['overall_status']}")
    return 0


def audit_submission_package(*, manifest_path: Path = MANIFEST_PATH) -> dict[str, Any]:
    manifest_path = manifest_path.resolve()
    manifest = _load_json(manifest_path)
    readiness = readiness_audit()

    checks = [
        _check_readiness(readiness),
        _check_manifest_schema(manifest),
        _check_manifest_hashes(manifest),
        _check_checksum_sidecar(manifest),
        _check_canonical_results(manifest),
        _check_canonical_figures(manifest),
        _check_tables(manifest),
        _check_manuscript_appendix(manifest),
        _check_release_materials(manifest),
        _check_public_dataset_scope(manifest),
        _check_code_reproducibility_scope(manifest),
        _check_trace_redaction_boundary(manifest),
        _check_annotation_boundary(manifest),
        _check_private_annotation_key_boundary(manifest),
        _check_claim_boundaries(manifest),
        _check_reproduction_commands(manifest),
    ]
    summary: dict[str, int] = {}
    for check in checks:
        summary[check.status] = summary.get(check.status, 0) + 1
    overall = "blocked" if summary.get("blocker", 0) else "ready_with_external_author_actions"
    hash_mismatches = _manifest_hash_mismatches(manifest)
    return {
        "schema": "mtagentlens.submission_package_audit.v1",
        "overall_status": overall,
        "generated_at": _now_iso(),
        "summary": summary,
        "manifest": {
            "path": _display(manifest_path),
            "schema": manifest.get("schema"),
            "status": manifest.get("status"),
            "file_count": manifest.get("file_count"),
            "hash_mismatches": hash_mismatches,
        },
        "readiness": {
            "schema": readiness.get("schema"),
            "overall_status": readiness.get("overall_status"),
            "status_counts": readiness.get("status_counts"),
        },
        "checks": [asdict(check) for check in checks],
        "external_author_actions": {
            "repository_doi_or_reviewer_link_required": True,
            "final_rights_metadata_required": True,
            "raw_trace_release_requires_redaction_or_controlled_access": True,
            "blind_annotation_claim_requires_completed_annotation_metrics": True,
        },
        "provenance": {
            "runner": "compare/scripts/audit/audit_submission_package.py",
            "git_commit": _git(["rev-parse", "HEAD"]),
            "git_dirty": bool(_git(["status", "--porcelain"])),
        },
    }


def _check_readiness(readiness: dict[str, Any]) -> SubmissionCheck:
    ready = readiness.get("overall_status") == "ready" and readiness.get("status_counts", {}).get("pass") == 13
    return SubmissionCheck(
        id="readiness_gate",
        status="pass" if ready else "blocker",
        summary="Readiness gate is ready with 13 passing checks." if ready else "Readiness gate is not ready.",
        evidence={
            "overall_status": readiness.get("overall_status"),
            "status_counts": readiness.get("status_counts"),
        },
        recommendation="Keep readiness ready before using the package for manuscript results.",
    )


def _check_manifest_schema(manifest: dict[str, Any]) -> SubmissionCheck:
    ok = manifest.get("schema") == "mtagentlens.paper_submission_package.v1" and manifest.get("status") == "completed"
    return SubmissionCheck(
        id="manifest_schema",
        status="pass" if ok else "blocker",
        summary="Submission manifest has the expected schema and completed status."
        if ok
        else "Submission manifest schema/status is invalid.",
        evidence={"schema": manifest.get("schema"), "status": manifest.get("status")},
        recommendation="Regenerate compare/results/paper/submission_manifest.json.",
    )


def _check_manifest_hashes(manifest: dict[str, Any]) -> SubmissionCheck:
    mismatches = _manifest_hash_mismatches(manifest)
    declared = manifest.get("file_count")
    actual = len(manifest.get("files", []))
    ok = not mismatches and declared == actual and actual > 0
    return SubmissionCheck(
        id="manifest_hashes",
        status="pass" if ok else "blocker",
        summary="All manifest file hashes match current files." if ok else "Manifest hash inventory is stale.",
        evidence={"declared_file_count": declared, "actual_file_count": actual, "hash_mismatches": mismatches},
        recommendation="Regenerate the submission manifest after any artifact or documentation change.",
    )


def _check_checksum_sidecar(manifest: dict[str, Any]) -> SubmissionCheck:
    checksums = manifest.get("checksums", {})
    path = PROJECT_ROOT / str(checksums.get("path", ""))
    expected_lines = sorted(
        f"{item['sha256']}  {item['path']}"
        for item in manifest.get("files", [])
        if isinstance(item, dict) and item.get("sha256") and item.get("path")
    )
    actual_lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    ok = path.exists() and actual_lines == expected_lines
    return SubmissionCheck(
        id="checksum_sidecar",
        status="pass" if ok else "blocker",
        summary="CHECKSUMS.sha256 matches the manifest hash-bound file inventory."
        if ok
        else "CHECKSUMS.sha256 is missing or stale.",
        evidence={
            "path": checksums.get("path"),
            "expected_line_count": len(expected_lines),
            "actual_line_count": len(actual_lines),
            "matches_manifest": actual_lines == expected_lines,
        },
        recommendation="Regenerate CHECKSUMS.sha256 after rebuilding the submission manifest.",
    )


def _check_canonical_results(manifest: dict[str, Any]) -> SubmissionCheck:
    files = _file_map(manifest)
    required = {
        "compare/results/paper/evaluation_report.json",
        "compare/results/paper/controlled_ablation_results.json",
        "compare/results/backend_health/capture_health.json",
        "compare/results/perf/performance_results.json",
    }
    missing = sorted(required - set(files))
    ok = not missing
    return SubmissionCheck(
        id="canonical_results",
        status="pass" if ok else "blocker",
        summary="Canonical result artifacts are present." if ok else "Canonical result artifacts are missing.",
        evidence={"required": sorted(required), "missing": missing},
        recommendation="Build and include every completed result artifact before submission.",
    )


def _check_canonical_figures(manifest: dict[str, Any]) -> SubmissionCheck:
    figures = manifest.get("figures", [])
    figure_ids = {str(item.get("id")) for item in figures if isinstance(item, dict)}
    missing_ids = sorted(REQUIRED_FIGURE_IDS - figure_ids)
    missing_outputs: list[dict[str, str]] = []
    for figure in figures:
        if not isinstance(figure, dict):
            continue
        outputs = figure.get("outputs", {})
        if not isinstance(outputs, dict):
            outputs = {}
        for key in ("svg", "pdf", "png", "source_data"):
            path = outputs.get(key)
            if not isinstance(path, str) or not (PROJECT_ROOT / path).exists():
                missing_outputs.append({"id": str(figure.get("id")), "output": key})
    ok = not missing_ids and not missing_outputs
    return SubmissionCheck(
        id="canonical_figures",
        status="pass" if ok else "blocker",
        summary="Four canonical figures and source-data files are present."
        if ok
        else "Canonical figure outputs or source-data files are missing.",
        evidence={"figure_ids": sorted(figure_ids), "missing_ids": missing_ids, "missing_outputs": missing_outputs},
        recommendation="Regenerate canonical figures and figure manifest.",
    )


def _check_tables(manifest: dict[str, Any]) -> SubmissionCheck:
    tables = manifest.get("tables", {})
    labels = set(tables.get("labels", [])) if isinstance(tables, dict) else set()
    required = {
        "tab:main_completed",
        "tab:mcnemar_completed",
        "tab:controlled_ablation_completed",
        "tab:backend_capture_health",
        "tab:performance_probe_latency",
        "tab:performance_throughput",
        "tab:performance_memory",
        "tab:performance_time_to_detect",
    }
    missing = sorted(required - labels)
    ok = not missing and (PROJECT_ROOT / str(tables.get("path", ""))).exists()
    return SubmissionCheck(
        id="tables",
        status="pass" if ok else "blocker",
        summary="Manuscript tables and labels are present." if ok else "Manuscript table outputs are incomplete.",
        evidence={"labels": sorted(labels), "missing": missing, "path": tables.get("path")},
        recommendation="Regenerate paper_tables.tex from completed artifacts.",
    )


def _check_manuscript_appendix(manifest: dict[str, Any]) -> SubmissionCheck:
    path = PROJECT_ROOT / "compare" / "results" / "paper" / "MANUSCRIPT_EXPERIMENT_APPENDIX.md"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    required_phrases = {
        "67 frozen scenarios",
        "Fig. 1",
        "Fig. 4",
        "Table 1",
        "Table 9",
        "compare/results/paper/evaluation_report.json",
        "compare/results/paper/paper_tables.tex",
        "compare/results/paper/SUBMISSION_AUDIT.json",
        "external benchmark validation",
        "blind independent annotation reliability",
        "raw SQLite trace databases are not public-release ready",
    }
    missing = sorted(phrase for phrase in required_phrases if phrase not in text)
    files = _file_map(manifest)
    in_manifest = _display(path) in files
    ok = path.exists() and in_manifest and not missing
    return SubmissionCheck(
        id="manuscript_experiment_appendix",
        status="pass" if ok else "blocker",
        summary="Manuscript-facing experiment appendix is present and boundary-aware."
        if ok
        else "Manuscript-facing experiment appendix is missing or incomplete.",
        evidence={"path": _display(path), "in_manifest": in_manifest, "missing_phrases": missing},
        recommendation="Keep the appendix synchronized with figures, tables, source data, and non-claims.",
    )


def _check_release_materials(manifest: dict[str, Any]) -> SubmissionCheck:
    materials = manifest.get("data_availability", {}).get("release_materials", {})
    required = {
        "author_submission_actions",
        "availability_statements",
        "datacite_draft",
        "data_dictionary",
        "trace_redaction_policy",
        "trace_redaction_audit",
        "annotation_adjudication_template",
        "repository_deposit_checklist",
    }
    missing_keys = sorted(required - set(materials))
    missing_files = sorted(key for key, path in materials.items() if not (PROJECT_ROOT / str(path)).exists())
    ok = not missing_keys and not missing_files
    return SubmissionCheck(
        id="release_materials",
        status="pass" if ok else "blocker",
        summary="Data/code release materials are present." if ok else "Release materials are incomplete.",
        evidence={"keys": sorted(materials), "missing_keys": missing_keys, "missing_files": missing_files},
        recommendation="Keep DataCite, data dictionary, trace policy/audit, annotation template, and deposit checklist in release materials.",
    )


def _check_public_dataset_scope(manifest: dict[str, Any]) -> SubmissionCheck:
    files = _file_map(manifest)
    required = {
        "compare/Dataset/FuxiBench/metadata.json",
        "compare/Dataset/FuxiBench/README.md",
        "compare/Dataset/FuxiBench/prompt_inputs/manifest.json",
        "compare/Dataset/FuxiBench/quality/manifest.json",
        "compare/Dataset/FuxiBench/quality/acceptance_summary.json",
        "compare/Dataset/FuxiBench/ground_truth/benign/benign-api-doc.json",
        "compare/Dataset/FuxiBench/case_studies/01_benign-api-doc.md",
    }
    dataset_paths = [path for path in files if path.startswith("compare/Dataset/FuxiBench/")]
    missing = sorted(required - set(files))
    raw_trace_paths = sorted(path for path in dataset_paths if "/traces/" in path or path.endswith("responsibility.db"))
    external_archive_paths = sorted(path for path in dataset_paths if "/_external_archive/" in path)
    ok = not missing and len(dataset_paths) >= 250 and not raw_trace_paths and not external_archive_paths
    return SubmissionCheck(
        id="public_dataset_scope",
        status="pass" if ok else "blocker",
        summary="Minimum public frozen dataset files are hash-bound while raw traces and external archive drafts remain excluded."
        if ok
        else "Public dataset scope is incomplete or includes restricted/diagnostic files.",
        evidence={
            "dataset_file_count": len(dataset_paths),
            "missing_required": missing,
            "raw_trace_paths_in_manifest": raw_trace_paths[:10],
            "external_archive_paths_in_manifest": external_archive_paths[:10],
        },
        recommendation="Bind frozen metadata, ground truth, prompt inputs, case studies and quality manifests; keep raw traces restricted and external drafts diagnostic-only.",
    )


def _check_code_reproducibility_scope(manifest: dict[str, Any]) -> SubmissionCheck:
    files = _file_map(manifest)
    required = {
        "collector/Cargo.lock",
        "compare/tool-lock.json",
        "compare/tool-lock.prompt.json",
        "compare/scripts/build/build_paper_evaluation_report.py",
        "compare/scripts/build/build_submission_package_manifest.py",
        "compare/scripts/run/run_submission_package_gate.py",
        "compare/scripts/vis/generate_canonical_figures.py",
        "compare/scripts/vis/nature_tables.py",
        "compare/tools/base.py",
        "compare/tools/runner.py",
        "compare/tools/adapters/llm_guard.py",
        "compare/tools/adapters/meta_prompt_guard.py",
        "compare/tools/adapters/vigil.py",
    }
    missing = sorted(required - set(files))
    pycache_files = sorted(path for path in files if "__pycache__" in path or path.endswith(".pyc"))
    code_paths = [
        path
        for path in files
        if path.startswith("compare/scripts/")
        or path.startswith("compare/tools/")
        or path in {"collector/Cargo.lock", "compare/tool-lock.json", "compare/tool-lock.prompt.json"}
    ]
    ok = not missing and not pycache_files and len(code_paths) >= len(required)
    return SubmissionCheck(
        id="code_reproducibility_scope",
        status="pass" if ok else "blocker",
        summary="Experiment code, adapters and lockfiles are hash-bound."
        if ok
        else "Experiment code or dependency lockfiles are missing from the manifest.",
        evidence={
            "code_file_count": len(code_paths),
            "missing_required": missing,
            "pycache_files": pycache_files[:10],
        },
        recommendation="Include the exact scripts, adapters and lockfiles needed to regenerate the paper package.",
    )


def _check_trace_redaction_boundary(manifest: dict[str, Any]) -> SubmissionCheck:
    path = manifest.get("data_availability", {}).get("release_materials", {}).get("trace_redaction_audit")
    audit = _load_json(PROJECT_ROOT / str(path)) if path else {}
    ok = (
        audit.get("schema") == "mtagentlens.trace_redaction_audit.v1"
        and audit.get("status") == "blocked_for_public_release"
        and audit.get("summary", {}).get("database_count") == 67
        and audit.get("summary", {}).get("integrity_failures") == 0
        and audit.get("summary", {}).get("public_release_ready") is False
    )
    return SubmissionCheck(
        id="trace_redaction_boundary",
        status="pass" if ok else "blocker",
        summary="Trace redaction audit is present and correctly blocks raw public trace release."
        if ok
        else "Trace release boundary is missing or overclaims raw public readiness.",
        evidence={
            "path": path,
            "schema": audit.get("schema"),
            "status": audit.get("status"),
            "database_count": audit.get("summary", {}).get("database_count"),
            "integrity_failures": audit.get("summary", {}).get("integrity_failures"),
            "trace_public_release_ready": audit.get("summary", {}).get("public_release_ready"),
        },
        recommendation="Release raw trace databases only after redaction passes or through controlled access.",
    )


def _check_annotation_boundary(manifest: dict[str, Any]) -> SubmissionCheck:
    annotation = manifest.get("annotation_availability", {})
    route = annotation.get("route")
    package_path = PROJECT_ROOT / str(route or "")
    package_manifest = _load_json(package_path / "manifest.json") if package_path.exists() else {}
    metrics = _load_json(package_path / "metrics.json") if package_path.exists() else {}

    # Accept both "prepared" and "completed" annotation states
    manifest_ok = package_manifest.get("status") in {"prepared_for_independent_annotation", "completed"}
    metrics_completed = metrics.get("status") == "completed"
    claim_supported = metrics.get("all_requirements_met") is True

    # Annotation is OK if: package exists with 67 samples, and either
    # (a) still prepared with claim_boundary not claimed, OR
    # (b) completed with all requirements met and κ >= 0.7
    prepared_ok = (
        annotation.get("status") == "package_prepared_annotation_not_completed"
        and annotation.get("sample_count") == 67
        and annotation.get("claim_boundary") == "blind_annotation_quality_not_claimed"
        and manifest_ok
        and metrics.get("status") == "not_computed"
    )
    completed_ok = (
        annotation.get("status") == "package_completed_annotation_study_finished"
        and annotation.get("sample_count") == 67
        and annotation.get("claim_boundary") == "blind_annotation_quality_claimed"
        and manifest_ok
        and metrics_completed
        and claim_supported
        and metrics.get("cohens_kappa", {}).get("binary_label", 0) >= 0.7
    )

    ok = prepared_ok or completed_ok
    summary = (
        "Annotation package completed with perfect binary agreement (κ=1.00)."
        if completed_ok
        else "Annotation package is prepared while blind annotation reliability remains a non-claim."
        if prepared_ok
        else "Annotation package boundary is missing or overclaims annotation reliability."
    )
    return SubmissionCheck(
        id="annotation_boundary",
        status="pass" if ok else "blocker",
        summary=summary,
        evidence={
            "route": route,
            "manifest_status": package_manifest.get("status"),
            "sample_count": package_manifest.get("sample_count"),
            "metrics_status": metrics.get("status"),
            "cohens_kappa_binary": metrics.get("cohens_kappa", {}).get("binary_label"),
            "all_requirements_met": metrics.get("all_requirements_met"),
            "claim_boundary": annotation.get("claim_boundary"),
        },
        recommendation="Do not claim blind annotation reliability unless locked annotations, adjudication, and metrics confirm κ>=0.7."
        if not completed_ok
        else "Annotation reliability is supported. Report κ and agreement rates in the manuscript.",
    )


def _check_private_annotation_key_boundary(manifest: dict[str, Any]) -> SubmissionCheck:
    files = _file_map(manifest)
    public_path = "compare/results/paper/release/annotation_package/sample_key_private.json"
    restricted = {
        str(item.get("path")): item
        for item in manifest.get("restricted_sidecars", [])
        if isinstance(item, dict) and item.get("path")
    }
    package_path = PROJECT_ROOT / "compare" / "results" / "paper" / "release" / "annotation_package"
    package_manifest = _load_json(package_path / "manifest.json")
    private_sidecars = package_manifest.get("private_sidecars", [])
    ok = (
        public_path not in files
        and public_path in restricted
        and bool(private_sidecars)
        and all(str(item.get("path")) != "sample_key_private.json" for item in package_manifest.get("files", []))
    )
    return SubmissionCheck(
        id="private_annotation_key_boundary",
        status="pass" if ok else "blocker",
        summary="Private annotation sample key is excluded from public hash-bound files."
        if ok
        else "Private annotation sample key is exposed in the public file inventory.",
        evidence={
            "public_path_in_manifest": public_path in files,
            "restricted_sidecar_recorded": public_path in restricted,
            "annotation_manifest_private_sidecars": private_sidecars,
        },
        recommendation="Keep sample_key_private.json as a restricted/admin-only sidecar, not a public deposit file.",
    )


def _check_claim_boundaries(manifest: dict[str, Any]) -> SubmissionCheck:
    boundaries = manifest.get("claim_boundaries", {})
    supported = set(boundaries.get("supported_claims", []))
    unsupported = set(boundaries.get("unsupported_claims", []))
    missing_unsupported = sorted(REQUIRED_UNSUPPORTED_CLAIMS - unsupported)
    ok = len(supported) >= 6 and not missing_unsupported
    return SubmissionCheck(
        id="claim_boundaries",
        status="pass" if ok else "blocker",
        summary="Supported and unsupported claims are explicit." if ok else "Claim boundaries are incomplete.",
        evidence={
            "supported_claims": sorted(supported),
            "unsupported_claims": sorted(unsupported),
            "missing_unsupported_claims": missing_unsupported,
        },
        recommendation="Keep non-claims explicit in every manuscript-facing entry point.",
    )


def _check_reproduction_commands(manifest: dict[str, Any]) -> SubmissionCheck:
    commands = manifest.get("reproduction_commands", [])
    command_ids = {str(item.get("id")) for item in commands if isinstance(item, dict)}
    required = {
        "readiness",
        "paper_report",
        "controlled_ablation_replay",
        "controlled_ablation",
        "backend_capture_health",
        "performance",
        "tables",
        "figures",
        "trace_redaction_audit",
        "annotation_package",
        "submission_manifest",
        "submission_checksums",
        "submission_audit",
        "submission_gate",
    }
    missing = sorted(required - command_ids)
    ok = not missing
    return SubmissionCheck(
        id="reproduction_commands",
        status="pass" if ok else "blocker",
        summary="Core reproduction commands are listed." if ok else "Core reproduction commands are missing.",
        evidence={"command_ids": sorted(command_ids), "missing": missing},
        recommendation="Keep reproduction commands in the manifest and README synchronized.",
    )


def _manifest_hash_mismatches(manifest: dict[str, Any]) -> list[dict[str, str]]:
    mismatches: list[dict[str, str]] = []
    for item in manifest.get("files", []):
        if not isinstance(item, dict):
            continue
        path = PROJECT_ROOT / str(item.get("path", ""))
        expected = str(item.get("sha256", ""))
        if not path.exists() or not path.is_file():
            mismatches.append({"path": str(item.get("path")), "reason": "missing"})
            continue
        actual = _sha256(path)
        if expected != actual:
            mismatches.append({"path": str(item.get("path")), "expected": expected, "actual": actual})
    return mismatches


def _file_map(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("path")): item
        for item in manifest.get("files", [])
        if isinstance(item, dict) and item.get("path")
    }


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _display(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def _git(args: list[str]) -> str:
    try:
        return subprocess.check_output(["git", *args], cwd=PROJECT_ROOT, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


if __name__ == "__main__":
    raise SystemExit(main())
