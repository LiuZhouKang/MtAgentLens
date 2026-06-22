from __future__ import annotations

import json
from pathlib import Path

from compare.scripts.vis.paper_report import load_paper_report_v1


def test_downstream_report_consumers_load_completed_report_without_drift() -> None:
    report_path = Path("compare/results/paper/evaluation_report.json")
    report = load_paper_report_v1(report_path)

    assert report["schema"] == "mtagentprism.paper_evaluation_report.v1"
    ds = report.get("dataset_summary", report.get("dataset", {}))
    assert ds.get("attack_cases", 0) + ds.get("benign_cases", 0) == 67

    tool_results = report.get("tool_results", report.get("tool_status_summary", []))
    tools = {tool["tool_id"]: tool for tool in tool_results} if isinstance(tool_results, list) else {}
    if tools:
        assert "mtagentprism" in tools
        mtap = tools["mtagentprism"]
        if "metrics" in mtap:
            assert mtap["metrics"]["precision"] == 1.0
            assert mtap["metrics"]["recall"] == 1.0
            assert mtap["metrics"]["f1"] == 1.0
