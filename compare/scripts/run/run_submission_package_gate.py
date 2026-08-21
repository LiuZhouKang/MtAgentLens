#!/usr/bin/env python3
"""Run the paper submission package gate end to end."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from compare.scripts.audit.audit_paper_experiment_readiness import audit as readiness_audit  # noqa: E402
from compare.scripts.audit.audit_submission_package import audit_submission_package  # noqa: E402
from compare.scripts.build.build_submission_checksums import build_checksums  # noqa: E402
from compare.scripts.build.build_submission_package_manifest import build_manifest  # noqa: E402


PAPER_RESULTS = PROJECT_ROOT / "compare" / "results" / "paper"
MANIFEST_PATH = PAPER_RESULTS / "submission_manifest.json"
SUBMISSION_AUDIT_PATH = PAPER_RESULTS / "SUBMISSION_AUDIT.json"
DEFAULT_OUTPUT = PAPER_RESULTS / "SUBMISSION_GATE.json"
CHECKSUMS_PATH = PAPER_RESULTS / "release" / "CHECKSUMS.sha256"

CURRENT_ENTRYPOINTS = [
    PROJECT_ROOT / "docs" / "superpowers" / "specs" / "2026-06-04-mtagentlens-experiment-design.md",
    PROJECT_ROOT / "compare" / "docs" / "paper-experiment-quality-dossier-v3-2026-06-09.md",
    PROJECT_ROOT / "compare" / "docs" / "paper-experiment-readiness-audit-2026-06-09.md",
    PAPER_RESULTS / "README.md",
    PAPER_RESULTS / "REPRODUCIBILITY.md",
    PAPER_RESULTS / "MANUSCRIPT_EXPERIMENT_APPENDIX.md",
]

STALE_CLAIM_PATTERNS = [
    (
        "external_claim_marked_completed",
        re.compile(
            r"\b(?:validated|evaluated|tested|completed|supported)\b[^\n]{0,80}"
            r"\b(?:401\s+external|468\s+(?:benchmarks?|scenarios?|total))\b",
            re.IGNORECASE,
        ),
    ),
    (
        "external_generalization_claim",
        re.compile(
            r"\bexternal\s+(?:benchmark\s+)?(?:generalization|validation)\s+"
            r"(?:is|was|has been)\s+(?:shown|validated|supported|completed)",
            re.IGNORECASE,
        ),
    ),
    (
        "universal_prompt_guard_ceiling_claim",
        re.compile(
            r"\b(?:prove|proves|proved|demonstrate|demonstrates|demonstrated)\b[^\n]{0,80}"
            r"\buniversal\s+prompt[- ]guard\s+(?:paradigm\s+)?ceiling\b",
            re.IGNORECASE,
        ),
    ),
    (
        "live_module_ablation_claim",
        re.compile(
            r"\blive\s+module[- ]level\s+causal\s+ablation\b[^\n]{0,80}"
            r"\b(?:shown|validated|supported|completed)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "production_latency_claim",
        re.compile(
            r"\b(?:production|end[- ]to[- ]end)\b[^\n]{0,80}"
            r"\b(?:latency|TTD|time[- ]to[- ]detect)\b[^\n]{0,80}"
            r"\b(?:shown|validated|supported|measured|reported)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "raw_trace_public_ready_claim",
        re.compile(
            r"\braw\s+(?:SQLite\s+)?trace\s+databases?\s+"
            r"(?:are|is|were|was|has been)\s+public[- ]release\s+ready\b",
            re.IGNORECASE,
        ),
    ),
    (
        "blind_annotation_reliability_claim",
        re.compile(
            r"\bblind\s+independent\s+annotation\s+reliability\b[^\n]{0,80}"
            r"\b(?:shown|validated|supported|computed|claimed)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "stale_blocker_status",
        re.compile(r"\b(?:overall_status\s*=\s*blocked|planned_missing_artifact|not paper-ready)\b"),
    ),
]

NEGATING_TERMS = (
    "does not claim",
    "do not claim",
    "must not claim",
    "cannot claim",
    "not claim",
    "non-claim",
    "non-claims",
    "deferred",
    "diagnostic only",
    "not used to support",
    "not public-release ready",
    "not raw",
)

PYTEST_TARGETS = [
    "compare/tests/test_paper_evaluation_report.py",
    "compare/tests/test_downstream_report_consumers.py",
    "compare/tests/test_paper_experiment_readiness.py",
    "compare/tests/test_backend_capture_health.py",
    "compare/tests/test_canonical_figures.py",
    "compare/tests/test_submission_package_manifest.py",
    "compare/tests/test_submission_release_materials.py",
    "compare/tests/test_trace_redaction_audit.py",
    "compare/tests/test_annotation_package_builder.py",
    "compare/tests/test_submission_audit.py",
    "compare/tests/test_manuscript_experiment_appendix.py",
    "compare/tests/test_submission_checksums.py",
    "compare/tests/test_submission_gate.py",
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--skip-regenerate", action="store_true")
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    artifact = run_submission_gate(
        output_path=args.output,
        regenerate=not args.skip_regenerate,
        run_tests=not args.skip_tests,
    )
    if args.json:
        print(json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"Generated {_display(args.output)}")
        print(f"status={artifact['status']}")
    return 0 if artifact["status"] in {"ready", "ready_without_pytest"} else 1


def run_submission_gate(
    *,
    output_path: Path = DEFAULT_OUTPUT,
    regenerate: bool = True,
    run_tests: bool = True,
) -> dict[str, Any]:
    regeneration = _regenerate_submission_sidecars() if regenerate else _skipped_step("regeneration")
    readiness = readiness_audit()
    submission_audit = audit_submission_package()
    stale_claim_scan = scan_stale_claims()
    pytest_result = _run_pytest() if run_tests else _skipped_step("pytest")

    blockers: list[str] = []
    if readiness.get("overall_status") != "ready":
        blockers.append("readiness")
    if submission_audit.get("overall_status") != "ready_with_external_author_actions":
        blockers.append("submission_audit")
    if submission_audit.get("summary", {}).get("blocker", 0):
        blockers.append("submission_audit_blockers")
    if stale_claim_scan.get("status") != "pass":
        blockers.append("stale_claim_scan")
    if run_tests and pytest_result.get("status") != "pass":
        blockers.append("pytest")

    if blockers:
        status = "blocked"
    elif run_tests:
        status = "ready"
    else:
        status = "ready_without_pytest"

    artifact = {
        "schema": "mtagentlens.submission_package_gate.v1",
        "status": status,
        "generated_at": _now_iso(),
        "blockers": blockers,
        "regeneration": regeneration,
        "readiness": {
            "overall_status": readiness.get("overall_status"),
            "status_counts": readiness.get("status_counts"),
        },
        "submission_audit": {
            "overall_status": submission_audit.get("overall_status"),
            "summary": submission_audit.get("summary"),
            "manifest": submission_audit.get("manifest"),
            "external_author_actions": submission_audit.get("external_author_actions"),
        },
        "stale_claim_scan": stale_claim_scan,
        "pytest": pytest_result,
        "claim_boundary": {
            "ready_status_meaning": (
                "The repository-local, bounded submission package is internally ready. "
                "It still requires external author actions for DOI/reviewer access, final rights metadata, "
                "redacted or controlled trace release, and completed independent annotation before those claims are made."
            ),
            "not_supported": [
                "external benchmark validation",
                "401 external / 468 total benchmark validation",
                "universal prompt-guard paradigm ceiling",
                "live module-level causal ablation",
                "production LLM-agent end-to-end latency",
                "trace-mode ResponsibilityRuntime end-to-end latency",
                "blind independent annotation reliability",
                "raw SQLite trace databases public release",
            ],
        },
        "provenance": {
            "runner": "compare/scripts/run_submission_package_gate.py",
            "git_commit": _git(["rev-parse", "HEAD"]),
            "git_dirty": bool(_git(["status", "--porcelain"])),
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return artifact


def scan_stale_claims(paths: list[Path] | None = None) -> dict[str, Any]:
    scanned = paths or CURRENT_ENTRYPOINTS
    matches: list[dict[str, Any]] = []
    missing: list[str] = []
    for path in scanned:
        if not path.exists():
            missing.append(_display(path))
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for line_number, line in enumerate(text.splitlines(), start=1):
            lower_line = line.lower()
            for pattern_id, pattern in STALE_CLAIM_PATTERNS:
                if not pattern.search(line):
                    continue
                if _is_negated_or_boundary_line(lower_line):
                    continue
                matches.append(
                    {
                        "path": _display(path),
                        "line": line_number,
                        "pattern_id": pattern_id,
                        "text": line.strip(),
                    }
                )
    status = "pass" if not missing and not matches else "blocker"
    return {
        "schema": "mtagentlens.stale_claim_scan.v1",
        "status": status,
        "scanned_paths": [_display(path) for path in scanned],
        "missing_paths": missing,
        "patterns": [pattern_id for pattern_id, _ in STALE_CLAIM_PATTERNS],
        "matches": matches,
    }


def _regenerate_submission_sidecars() -> dict[str, Any]:
    manifest = build_manifest()
    _write_json(MANIFEST_PATH, manifest)
    checksums = build_checksums(manifest_path=MANIFEST_PATH, output_path=CHECKSUMS_PATH)
    audit = audit_submission_package(manifest_path=MANIFEST_PATH)
    _write_json(SUBMISSION_AUDIT_PATH, audit)
    return {
        "status": "pass",
        "steps": [
            {"id": "submission_manifest", "path": _display(MANIFEST_PATH), "file_count": manifest.get("file_count")},
            {"id": "submission_checksums", "path": _display(CHECKSUMS_PATH), "file_count": checksums.get("file_count")},
            {
                "id": "submission_audit",
                "path": _display(SUBMISSION_AUDIT_PATH),
                "overall_status": audit.get("overall_status"),
            },
        ],
    }


def _run_pytest() -> dict[str, Any]:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    env["PYTHONPATH"] = "."
    command = [sys.executable, "-m", "pytest", *PYTEST_TARGETS, "-q"]
    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    return {
        "status": "pass" if completed.returncode == 0 else "blocker",
        "command": " ".join(command),
        "returncode": completed.returncode,
        "targets": PYTEST_TARGETS,
        "output_tail": "\n".join(completed.stdout.splitlines()[-20:]),
    }


def _skipped_step(step: str) -> dict[str, Any]:
    return {"status": "skipped", "step": step}


def _is_negated_or_boundary_line(lower_line: str) -> bool:
    return any(term in lower_line for term in NEGATING_TERMS)


def _write_json(path: Path, artifact: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


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
