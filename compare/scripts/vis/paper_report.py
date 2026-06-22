"""Shared loader for the canonical paper evaluation report."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_REPORT = PROJECT_ROOT / "compare" / "results" / "paper" / "evaluation_report.json"
EXPECTED_SCHEMA = "mtagentprism.paper_evaluation_report.v1"


class PaperReportError(ValueError):
    """Raised when a paper-facing report/artifact is missing or invalid."""


def load_paper_report_v1(path: Path = DEFAULT_REPORT) -> dict[str, Any]:
    """Load and normalize the canonical paper report.

    The report builder stores ``main_metrics`` as an ordered list. Downstream
    table and figure scripts should use the normalized ``metrics_by_tool`` map
    instead of assuming an older dict-shaped schema.
    """
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise PaperReportError(f"cannot read paper report: {path}") from exc
    except json.JSONDecodeError as exc:
        raise PaperReportError(f"invalid paper report JSON: {path}: {exc.msg}") from exc

    if not isinstance(report, dict):
        raise PaperReportError("paper report root must be an object")
    if report.get("schema") != EXPECTED_SCHEMA:
        raise PaperReportError(f"paper report schema must be {EXPECTED_SCHEMA}")

    main_metrics = report.get("main_metrics")
    if not isinstance(main_metrics, list):
        raise PaperReportError("paper report main_metrics must be a list")
    metrics_by_tool: dict[str, dict[str, Any]] = {}
    for row in main_metrics:
        if not isinstance(row, dict) or not row.get("tool_id"):
            raise PaperReportError("every main_metrics row must contain tool_id")
        metrics_by_tool[str(row["tool_id"])] = row
    if "mtagentprism" not in metrics_by_tool:
        raise PaperReportError("main_metrics must include mtagentprism")

    normalized = dict(report)
    normalized["metrics_by_tool"] = metrics_by_tool
    return normalized


def dataset_counts(report: dict[str, Any]) -> tuple[int, int, int]:
    summary = report.get("dataset_summary", {})
    if not isinstance(summary, dict):
        return 0, 0, 0
    total = int(summary.get("total_cases", 0))
    attack = int(summary.get("attack_cases", 0))
    benign = int(summary.get("benign_cases", 0))
    return total, attack, benign


def prompt_mcnemar_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    mcnemar = report.get("mcnemar", {})
    if not isinstance(mcnemar, dict):
        return rows
    for tool_id in sorted(mcnemar):
        item = mcnemar[tool_id]
        if not isinstance(item, dict):
            continue
        if "b_mtap_correct_prompt_wrong" not in item:
            continue
        rows.append(
            {
                "tool_id": tool_id,
                "comparison": f"MtAgentPrism vs {tool_id}",
                "b": int(item.get("b_mtap_correct_prompt_wrong", 0)),
                "c": int(item.get("c_prompt_correct_mtap_wrong", 0)),
                "statistic": float(item.get("statistic_yates", 0.0)),
                "p_value": float(item.get("p_value_chi_square_1df", 1.0)),
                "paired_unit": item.get("paired_unit", "scenario"),
            }
        )
    return rows


def unsupported_claims(report: dict[str, Any]) -> set[str]:
    boundaries = report.get("claim_boundaries", {})
    if not isinstance(boundaries, dict):
        return set()
    claims = boundaries.get("unsupported_claims", [])
    if not isinstance(claims, list):
        return set()
    return {str(item) for item in claims}


def load_completed_artifact(path: Path, *, expected_schema: str) -> dict[str, Any]:
    """Load a paper-facing artifact only when it is completed."""
    try:
        artifact = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise PaperReportError(f"missing required artifact: {path}") from exc
    except json.JSONDecodeError as exc:
        raise PaperReportError(f"invalid artifact JSON: {path}: {exc.msg}") from exc
    if not isinstance(artifact, dict):
        raise PaperReportError(f"artifact root must be an object: {path}")
    if artifact.get("schema") != expected_schema:
        raise PaperReportError(f"artifact schema must be {expected_schema}: {path}")
    if artifact.get("status") != "completed":
        raise PaperReportError(f"artifact is not completed: {path}")
    return artifact
