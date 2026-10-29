from __future__ import annotations

import json
from pathlib import Path

import pytest

from compare.scripts.vis.paper_report import (
    PaperReportError,
    dataset_counts,
    load_completed_artifact,
    load_paper_report_v1,
    prompt_mcnemar_rows,
)


def test_load_paper_report_v1_normalizes_metrics_by_tool(synthetic_paper_report: Path) -> None:
    report = load_paper_report_v1(synthetic_paper_report)

    assert report["schema"] == "mtagentlens.paper_evaluation_report.v1"
    assert set(report["metrics_by_tool"]) >= {"mtagentlens", "llm-guard", "meta-prompt-guard", "vigil"}
    assert report["metrics_by_tool"]["mtagentlens"]["f1"] == 0.75
    assert dataset_counts(report) == (8, 5, 3)

    rows = {row["tool_id"]: row for row in prompt_mcnemar_rows(report)}
    assert rows["llm-guard"]["b"] == 1
    assert rows["llm-guard"]["c"] == 1
    assert rows["meta-prompt-guard"]["b"] == 2
    assert rows["vigil"]["b"] == 3


def test_load_paper_report_v1_rejects_old_dict_schema(tmp_path: Path) -> None:
    path = tmp_path / "old_report.json"
    path.write_text(
        json.dumps(
            {
                "schema": "old",
                "main_metrics": {"mtagentlens": {"f1": 1.0}},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(PaperReportError):
        load_paper_report_v1(path)


def test_load_completed_artifact_rejects_planned_artifact(tmp_path: Path) -> None:
    path = tmp_path / "performance_results.json"
    path.write_text(
        json.dumps(
            {
                "schema": "mtagentlens.performance_results.v1",
                "status": "planned_missing_artifact",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(PaperReportError, match="not completed"):
        load_completed_artifact(path, expected_schema="mtagentlens.performance_results.v1")


def test_loader_rejects_diagnostic_report_without_completed_evidence(tmp_path: Path) -> None:
    path = tmp_path / "diagnostic.json"
    path.write_text(json.dumps({"schema": "mtagentlens.paper_evaluation_report.v1", "status": "blocked",
        "main_metrics": [{"tool_id": "mtagentlens", "f1": 1.0}]}))
    with pytest.raises(PaperReportError, match="not completed"):
        load_paper_report_v1(path)
