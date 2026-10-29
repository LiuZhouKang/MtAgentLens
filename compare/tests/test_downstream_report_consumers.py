from __future__ import annotations

import json
from pathlib import Path

from compare.scripts.vis.paper_report import load_paper_report_v1


def test_downstream_report_consumers_preserve_synthetic_counts(synthetic_paper_report: Path) -> None:
    report = load_paper_report_v1(synthetic_paper_report)
    assert report["dataset_summary"] == {"total_cases": 8, "attack_cases": 5, "benign_cases": 3}
    assert report["metrics_by_tool"]["mtagentlens"]["f1"] == 0.75
    assert report["metrics_by_tool"]["llm-guard"]["f1"] == 0.65
