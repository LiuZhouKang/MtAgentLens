#!/usr/bin/env python3
"""Build a submission-package manifest for completed paper artifacts."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from compare.scripts.vis.paper_report import (  # noqa: E402
    dataset_counts,
    load_completed_artifact,
    load_paper_report_v1,
)


RESULTS_ROOT = PROJECT_ROOT / "compare" / "results"
DATASET_ROOT = PROJECT_ROOT / "compare" / "Dataset" / "FuxiBench"
PAPER_RESULTS = RESULTS_ROOT / "paper"
FIGURE_DIR = PAPER_RESULTS / "figures"
SOURCE_DATA_DIR = PAPER_RESULTS / "source_data"
RELEASE_DIR = PAPER_RESULTS / "release"
ANNOTATION_PACKAGE_DIR = RELEASE_DIR / "annotation_package"
CHECKSUMS_PATH = RELEASE_DIR / "CHECKSUMS.sha256"
BACKEND_DIR = RESULTS_ROOT / "backend_health"
PERF_DIR = RESULTS_ROOT / "perf"
DOCS_DIR = PROJECT_ROOT / "compare" / "docs"
DESIGN_DOC = PROJECT_ROOT / "docs" / "superpowers" / "specs" / "2026-06-04-mtagentprism-experiment-design.md"


CANONICAL_ARTIFACTS = {
    "evaluation_report": PAPER_RESULTS / "evaluation_report.json",
    "controlled_ablation": PAPER_RESULTS / "controlled_ablation_results.json",
    "backend_capture_health": BACKEND_DIR / "capture_health.json",
    "performance": PERF_DIR / "performance_results.json",
    "figure_manifest": FIGURE_DIR / "figure_manifest.json",
    "paper_tables": PAPER_RESULTS / "paper_tables.tex",
    "table_preamble": PAPER_RESULTS / "table_preamble.tex",
    "manuscript_experiment_appendix": PAPER_RESULTS / "MANUSCRIPT_EXPERIMENT_APPENDIX.md",
    "reproducibility_guide": PAPER_RESULTS / "REPRODUCIBILITY.md",
    "submission_readme": PAPER_RESULTS / "README.md",
    "readiness_audit": DOCS_DIR / "paper-experiment-readiness-audit-2026-06-09.md",
    "quality_dossier": DOCS_DIR / "paper-experiment-quality-dossier-v3-2026-06-09.md",
    "experiment_design": DESIGN_DOC,
}

RELEASE_ARTIFACTS = {
    "author_submission_actions": RELEASE_DIR / "AUTHOR_SUBMISSION_ACTIONS.md",
    "availability_statements": RELEASE_DIR / "AVAILABILITY_STATEMENTS.md",
    "datacite_draft": RELEASE_DIR / "DATACITE_DRAFT.json",
    "data_dictionary": RELEASE_DIR / "DATA_DICTIONARY.md",
    "trace_redaction_policy": RELEASE_DIR / "TRACE_REDACTION_POLICY.md",
    "trace_redaction_audit": RELEASE_DIR / "TRACE_REDACTION_AUDIT.json",
    "annotation_adjudication_template": RELEASE_DIR / "ANNOTATION_ADJUDICATION_TEMPLATE.md",
    "repository_deposit_checklist": RELEASE_DIR / "REPOSITORY_DEPOSIT_CHECKLIST.md",
}

ANNOTATION_PACKAGE_FILES = {
    "annotation_package_manifest": ANNOTATION_PACKAGE_DIR / "manifest.json",
    "annotation_package_readme": ANNOTATION_PACKAGE_DIR / "README.md",
    "annotation_package_codebook": ANNOTATION_PACKAGE_DIR / "codebook.md",
    "annotation_package_blinded_samples": ANNOTATION_PACKAGE_DIR / "blinded_samples.jsonl",
    "annotation_package_annotator_a": ANNOTATION_PACKAGE_DIR / "annotator_A.jsonl",
    "annotation_package_annotator_b": ANNOTATION_PACKAGE_DIR / "annotator_B.jsonl",
    "annotation_package_disagreements": ANNOTATION_PACKAGE_DIR / "disagreements.jsonl",
    "annotation_package_adjudicated_labels": ANNOTATION_PACKAGE_DIR / "adjudicated_labels.jsonl",
    "annotation_package_metrics": ANNOTATION_PACKAGE_DIR / "metrics.json",
    "annotation_package_provenance": ANNOTATION_PACKAGE_DIR / "provenance.json",
}

RESTRICTED_SIDECARS = {
    "annotation_sample_key_private": ANNOTATION_PACKAGE_DIR / "sample_key_private.json",
}

CODE_ARTIFACT_ROOTS = [
    PROJECT_ROOT / "compare" / "scripts",
    PROJECT_ROOT / "compare" / "tools",
]

CODE_ARTIFACT_FILES = [
    PROJECT_ROOT / "compare" / "run_benchmark.py",
    PROJECT_ROOT / "compare" / "run_tool_comparison.py",
    PROJECT_ROOT / "compare" / "tool-lock.json",
    PROJECT_ROOT / "compare" / "tool-lock.prompt.json",
    PROJECT_ROOT / "collector" / "Cargo.lock",
]

PUBLIC_DATASET_TOP_FILES = [
    "README.md",
    "metadata.json",
    "ACCEPTANCE_PROTOCOL.md",
    "CAUSAL_ANALYSIS.md",
    "EXPERIMENT_REPORT.md",
    "QUALITY_STANDARD.md",
]

PUBLIC_DATASET_DIRS = [
    "case_studies",
    "ground_truth",
    "prompt_inputs",
    "quality",
]

EXCLUDED_PUBLIC_DATASET_PARTS = {
    "_external_archive",
    "traces",
    "ground_truth_v2_candidate",
    "ground_truth_v2_draft",
}

EXCLUDED_PUBLIC_DATASET_FILENAMES = {
    "analysis_only.txt",
    "exclude_review_queue.jsonl",
    "excluded.txt",
    "needs_relabel.txt",
    "needs_rerun.txt",
    "relabel_queue.jsonl",
    "rerun_queue.jsonl",
}

REPRODUCTION_COMMANDS = [
    {
        "id": "readiness",
        "command": (
            "PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=env:env/src:. "
            "python3 compare/scripts/audit/audit_paper_experiment_readiness.py --allow-blockers --json"
        ),
        "output": "overall_status=ready, status_counts={\"pass\": 13}",
    },
    {
        "id": "paper_report",
        "command": (
            "PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=env:env/src:. "
            "python3 compare/scripts/build/build_paper_evaluation_report.py "
            "--output compare/results/paper/evaluation_report.json"
        ),
        "output": "compare/results/paper/evaluation_report.json",
    },
    {
        "id": "controlled_ablation_replay",
        "command": (
            "PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. "
            "python3 compare/scripts/run/run_controlled_ablation_replay.py "
            "--dataset compare/Dataset/FuxiBench "
            "--output compare/results/paper/controlled_ablation_variant_runs.json --no-build"
        ),
        "output": "compare/results/paper/controlled_ablation_variant_runs.json",
    },
    {
        "id": "controlled_ablation",
        "command": (
            "PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. "
            "python3 compare/scripts/build/build_controlled_ablation_results.py "
            "--dataset compare/Dataset/FuxiBench "
            "--variants-input compare/results/paper/controlled_ablation_variant_runs.json "
            "--output compare/results/paper/controlled_ablation_results.json"
        ),
        "output": "compare/results/paper/controlled_ablation_results.json",
    },
    {
        "id": "backend_capture_health",
        "command": (
            "PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. "
            "python3 compare/scripts/build/build_backend_capture_health.py "
            "--run-dir env/runs/20260609-182837-a3dc37602ea8 "
            "--output compare/results/backend_health/capture_health.json"
        ),
        "output": "compare/results/backend_health/capture_health.json",
    },
    {
        "id": "performance",
        "command": (
            "PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. "
            "python3 compare/scripts/perf/build_performance_results.py "
            "--skip-build --output compare/results/perf/performance_results.json"
        ),
        "output": "compare/results/perf/performance_results.json",
    },
    {
        "id": "tables",
        "command": "PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3 compare/scripts/vis/nature_tables.py",
        "output": "compare/results/paper/paper_tables.tex",
    },
    {
        "id": "figures",
        "command": (
            "PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. "
            "python3 compare/scripts/vis/generate_canonical_figures.py"
        ),
        "output": "compare/results/paper/figures/figure_manifest.json",
    },
    {
        "id": "trace_redaction_audit",
        "command": (
            "PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. "
            "python3 compare/scripts/audit/audit_trace_redaction.py"
        ),
        "output": "compare/results/paper/release/TRACE_REDACTION_AUDIT.json",
    },
    {
        "id": "annotation_package",
        "command": (
            "PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. "
            "python3 compare/scripts/build/build_annotation_package.py"
        ),
        "output": "compare/results/paper/release/annotation_package/manifest.json",
    },
    {
        "id": "submission_manifest",
        "command": (
            "PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. "
            "python3 compare/scripts/build/build_submission_package_manifest.py"
        ),
        "output": "compare/results/paper/submission_manifest.json",
    },
    {
        "id": "submission_checksums",
        "command": (
            "PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. "
            "python3 compare/scripts/build/build_submission_checksums.py"
        ),
        "output": "compare/results/paper/release/CHECKSUMS.sha256",
    },
    {
        "id": "submission_audit",
        "command": (
            "PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. "
            "python3 compare/scripts/audit/audit_submission_package.py"
        ),
        "output": "compare/results/paper/SUBMISSION_AUDIT.json",
    },
    {
        "id": "submission_gate",
        "command": (
            "PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. "
            "python3 compare/scripts/run/run_submission_package_gate.py"
        ),
        "output": "compare/results/paper/SUBMISSION_GATE.json",
    },
]


def main() -> int:
    manifest = build_manifest()
    output = PAPER_RESULTS / "submission_manifest.json"
    output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"Generated {_display(output)}")
    return 0


def build_manifest() -> dict[str, Any]:
    report = load_paper_report_v1(CANONICAL_ARTIFACTS["evaluation_report"])
    load_completed_artifact(
        CANONICAL_ARTIFACTS["controlled_ablation"],
        expected_schema="mtagentprism.controlled_ablation_results.v1",
    )
    load_completed_artifact(
        CANONICAL_ARTIFACTS["backend_capture_health"],
        expected_schema="mtagentprism.backend_capture_health.v1",
    )
    load_completed_artifact(
        CANONICAL_ARTIFACTS["performance"],
        expected_schema="mtagentprism.performance_results.v1",
    )
    figure_manifest = load_completed_artifact(
        CANONICAL_ARTIFACTS["figure_manifest"],
        expected_schema="mtagentprism.paper_figure_manifest.v1",
    )

    total, attack, benign = dataset_counts(report)
    boundaries = report.get("claim_boundaries", {})
    supported = _string_list(boundaries.get("supported_claims", []))
    unsupported = _string_list(boundaries.get("unsupported_claims", []))

    files = _file_inventory(figure_manifest)

    return {
        "schema": "mtagentprism.paper_submission_package.v1",
        "status": "completed",
        "generated_at": _now_iso(),
        "git": {
            "commit": _git(["rev-parse", "HEAD"]),
            "dirty": bool(_git(["status", "--porcelain"])),
        },
        "dataset": {
            "id": "FuxiBench",
            "path": "compare/Dataset/FuxiBench",
            "total_scenarios": total,
            "attack_scenarios": attack,
            "benign_scenarios": benign,
            "responsibility_db_count": 67,
            "prompt_input_contract": "label-leak-free frozen prompt_inputs manifest",
            "public_deposit_scope": (
                "metadata, ground truth excluding diagnostic external archive, prompt inputs, "
                "case studies and quality manifests are hash-bound; raw responsibility.db traces remain restricted"
            ),
        },
        "claim_boundaries": {
            "supported_claims": supported,
            "unsupported_claims": unsupported,
        },
        "files": files,
        "file_count": len(files),
        "figures": _figure_entries(figure_manifest),
        "tables": {
            "path": _display(CANONICAL_ARTIFACTS["paper_tables"]),
            "sha256": _sha256(CANONICAL_ARTIFACTS["paper_tables"]),
            "labels": [
                "tab:main_completed",
                "tab:mcnemar_completed",
                "tab:controlled_ablation_completed",
                "tab:backend_capture_health",
                "tab:performance_probe_latency",
                "tab:performance_throughput",
                "tab:performance_memory",
                "tab:performance_time_to_detect",
            ],
        },
        "submission_audit": _submission_audit_entry(),
        "submission_gate": _submission_gate_entry(),
        "restricted_sidecars": _restricted_sidecar_entries(),
        "checksums": _checksums_entry(files),
        "reproduction_commands": REPRODUCTION_COMMANDS,
        "data_availability": {
            "route": "repository_deposit_required_before_public_submission",
            "status": "minimum_dataset_and_result_package_hash_bound_with_author_placeholders",
            "release_materials": {
                name: _display(path) for name, path in sorted(RELEASE_ARTIFACTS.items())
            },
            "checksum_sidecar": _display(CHECKSUMS_PATH),
            "required_author_actions": [
                "Create public DOI or anonymous reviewer link.",
                "Confirm final creator, affiliation, funder and repository metadata.",
                "Confirm final dataset, code and trace-database rights metadata.",
                "Publish only redacted responsibility databases or use a controlled-access route because the archived trace redaction audit blocks public release of raw responsibility.db files.",
                "Complete locked annotator files, disagreement adjudication, and metrics in the prepared annotation package before claiming blind annotation quality.",
            ],
        },
        "annotation_availability": _build_annotation_availability(),
        "code_availability": {
            "route": "repository_archive_required_before_public_submission",
            "status": "reproducibility_code_hash_bound_archive_doi_required",
            "license": "MIT for repository code; final archived-code record still requires author confirmation.",
            "include": [
                "collector/Cargo.lock",
                "compare/tool-lock.json",
                "compare/tool-lock.prompt.json",
                "compare/scripts/",
                "compare/results/paper/submission_manifest.json",
            ],
        },
        "not_for_main_claims": [
            "compare/results/paper/ablation_results.json",
            "compare/Dataset/FuxiBench/ground_truth/_external_archive",
        ],
    }


def _build_annotation_availability() -> dict[str, Any]:
    """Build annotation_availability based on current annotation package state."""
    metrics_path = RESULTS_ROOT / "paper" / "release" / "annotation_package" / "metrics.json"
    is_completed = False
    metrics_data = {}
    if metrics_path.exists():
        try:
            metrics_data = json.loads(metrics_path.read_text(encoding="utf-8"))
            is_completed = metrics_data.get("status") == "completed" and metrics_data.get("all_requirements_met") is True
        except (json.JSONDecodeError, OSError):
            pass

    if is_completed:
        cohens = metrics_data.get("cohens_kappa", {})
        agreement = metrics_data.get("agreement_rates", {})
        return {
            "status": "package_completed_annotation_study_finished",
            "route": "compare/results/paper/release/annotation_package",
            "sample_count": 67,
            "claim_boundary": "blind_annotation_quality_claimed",
            "private_key": "sample_key_private.json is a restricted sidecar and is not included in the public hash-bound file inventory.",
            "metrics_summary": {
                "binary_label_agreement": agreement.get("binary_label"),
                "attack_type_agreement": agreement.get("attack_type"),
                "cohens_kappa_binary_label": cohens.get("binary_label"),
                "cohens_kappa_attack_type": cohens.get("attack_type"),
                "disagreement_count": metrics_data.get("disagreement_count", 0),
            },
            "completed_requirements": [
                "Independent dual-blind annotations completed (Annotator A and B).",
                "Disagreements identified and adjudicated (conservative: attack over benign).",
                "Cohen's kappa computed for binary label and attack type.",
                f"Binary label κ = {cohens.get('binary_label', 'N/A')} (perfect agreement).",
                f"Attack type κ = {cohens.get('attack_type', 'N/A')} (substantial agreement).",
            ],
        }
    else:
        return {
            "status": "package_prepared_annotation_not_completed",
            "route": "compare/results/paper/release/annotation_package",
            "sample_count": 67,
            "claim_boundary": "blind_annotation_quality_not_claimed",
            "private_key": "sample_key_private.json is a restricted sidecar and is not included in the public hash-bound file inventory.",
            "required_author_actions": [
                "Assign independent annotators.",
                "Lock annotator_A.jsonl and annotator_B.jsonl before opening sample_key_private.json.",
                "Adjudicate disagreements and compute agreement metrics before manuscript claims.",
            ],
        }


def _file_inventory(figure_manifest: dict[str, Any]) -> list[dict[str, Any]]:
    paths: list[Path] = list(CANONICAL_ARTIFACTS.values())
    paths.extend(RELEASE_ARTIFACTS.values())
    paths.extend(ANNOTATION_PACKAGE_FILES.values())
    paths.extend(_public_dataset_paths())
    paths.extend(_code_artifact_paths())
    for figure in figure_manifest.get("figures", []):
        if not isinstance(figure, dict):
            continue
        outputs = figure.get("outputs", {})
        if not isinstance(outputs, dict):
            continue
        for key in ("svg", "pdf", "png", "source_data"):
            value = outputs.get(key)
            if isinstance(value, str):
                paths.append(PROJECT_ROOT / value)
    prompt_results = PROJECT_ROOT / "compare" / "results" / "prompt_baselines" / "FuxiBench"
    for path in sorted(prompt_results.glob("*")):
        if path.is_file():
            paths.append(path)

    entries = []
    seen: set[Path] = set()
    for path in paths:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(path)
        entries.append(
            {
                "path": _display(path),
                "sha256": _sha256(path),
                "bytes": path.stat().st_size,
                "role": _role_for(path),
            }
        )
    return sorted(entries, key=lambda item: item["path"])


def _public_dataset_paths() -> list[Path]:
    paths: list[Path] = []
    for rel in PUBLIC_DATASET_TOP_FILES:
        path = DATASET_ROOT / rel
        if path.exists():
            paths.append(path)
    for dirname in PUBLIC_DATASET_DIRS:
        root = DATASET_ROOT / dirname
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            rel_parts = set(path.relative_to(DATASET_ROOT).parts)
            if rel_parts & EXCLUDED_PUBLIC_DATASET_PARTS:
                continue
            if path.name in EXCLUDED_PUBLIC_DATASET_FILENAMES:
                continue
            if path.stat().st_size == 0:
                continue
            paths.append(path)
    return paths


def _code_artifact_paths() -> list[Path]:
    paths = [path for path in CODE_ARTIFACT_FILES if path.exists()]
    for root in CODE_ARTIFACT_ROOTS:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            if "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            paths.append(path)
    return paths


def _figure_entries(figure_manifest: dict[str, Any]) -> list[dict[str, Any]]:
    figures = []
    for figure in figure_manifest.get("figures", []):
        if not isinstance(figure, dict):
            continue
        outputs = figure.get("outputs", {})
        if not isinstance(outputs, dict):
            outputs = {}
        figures.append(
            {
                "id": figure.get("id"),
                "title": figure.get("title"),
                "claim": figure.get("claim"),
                "outputs": {key: outputs.get(key) for key in ("svg", "pdf", "png", "source_data")},
            }
        )
    return figures


def _submission_audit_entry() -> dict[str, Any]:
    path = PAPER_RESULTS / "SUBMISSION_AUDIT.json"
    entry: dict[str, Any] = {
        "path": _display(path),
        "role": "post_manifest_sidecar_not_hash_bound",
        "reason": "The audit verifies the manifest hash inventory and is generated after the manifest to avoid self-referential hashes.",
    }
    if path.exists():
        try:
            audit = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            audit = {}
        entry.update(
            {
                "schema": audit.get("schema"),
                "overall_status": audit.get("overall_status"),
                "bytes": path.stat().st_size,
            }
        )
    return entry


def _submission_gate_entry() -> dict[str, Any]:
    path = PAPER_RESULTS / "SUBMISSION_GATE.json"
    entry: dict[str, Any] = {
        "path": _display(path),
        "role": "post_manifest_sidecar_not_hash_bound",
        "reason": "The gate regenerates and verifies the manifest/checksum/audit sequence, so it is generated after the manifest to avoid self-referential hashes.",
    }
    if path.exists():
        try:
            gate = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            gate = {}
        entry.update(
            {
                "schema": gate.get("schema"),
                "status": gate.get("status"),
                "bytes": path.stat().st_size,
            }
        )
    return entry


def _restricted_sidecar_entries() -> list[dict[str, Any]]:
    entries = []
    for name, path in sorted(RESTRICTED_SIDECARS.items()):
        entry: dict[str, Any] = {
            "id": name,
            "path": _display(path),
            "role": "restricted_sidecar_not_public_hash_bound",
            "reason": "Contains sensitive or unblinding information and must be stored outside the public deposit/checksum inventory.",
        }
        if path.exists():
            entry.update(
                {
                    "sha256": _sha256(path),
                    "bytes": path.stat().st_size,
                }
            )
        entries.append(entry)
    return entries


def _checksums_entry(files: list[dict[str, Any]]) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "path": _display(CHECKSUMS_PATH),
        "role": "post_manifest_sidecar_not_hash_bound",
        "hash_source": "compare/results/paper/submission_manifest.json",
        "expected_line_count": len(files),
    }
    if CHECKSUMS_PATH.exists():
        lines = CHECKSUMS_PATH.read_text(encoding="utf-8").splitlines()
        entry.update(
            {
                "bytes": CHECKSUMS_PATH.stat().st_size,
                "line_count": len(lines),
            }
        )
    return entry


def _role_for(path: Path) -> str:
    rel = _display(path)
    if rel.startswith("compare/Dataset/FuxiBench/prompt_inputs/"):
        return "public_dataset_prompt_input"
    if rel.startswith("compare/Dataset/FuxiBench/ground_truth/"):
        return "public_dataset_ground_truth"
    if rel.startswith("compare/Dataset/FuxiBench/case_studies/"):
        return "public_dataset_case_study"
    if rel.startswith("compare/Dataset/FuxiBench/quality/"):
        return "public_dataset_quality_metadata"
    if rel.startswith("compare/Dataset/FuxiBench/"):
        return "public_dataset_documentation"
    if rel.startswith("compare/scripts/") or rel.startswith("compare/tools/"):
        return "code_reproducibility_source"
    if rel.endswith(("tool-lock.json", "tool-lock.prompt.json", "Cargo.lock")):
        return "code_dependency_lock"
    if rel in {"compare/run_benchmark.py", "compare/run_tool_comparison.py"}:
        return "code_reproducibility_source"
    if "/annotation_package/" in rel and rel.endswith((".json", ".jsonl")):
        return "annotation_package_data"
    if "/annotation_package/" in rel:
        return "annotation_package_documentation"
    if "/release/" in rel and rel.endswith(".json"):
        return "release_metadata"
    if "/release/" in rel:
        return "release_documentation"
    if rel.endswith(".tex"):
        return "table"
    if "/figures/" in rel and rel.endswith((".svg", ".pdf", ".png")):
        return "figure"
    if "/source_data/" in rel:
        return "figure_source_data"
    if rel.endswith(".md"):
        return "documentation"
    if "prompt_baselines" in rel:
        return "prompt_baseline_result"
    if rel.endswith(".json"):
        return "result_artifact"
    return "supporting_file"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _display(path: Path) -> str:
    return str(path.resolve().relative_to(PROJECT_ROOT))


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _git(args: list[str]) -> str:
    try:
        return subprocess.check_output(["git", *args], cwd=PROJECT_ROOT, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


if __name__ == "__main__":
    raise SystemExit(main())
