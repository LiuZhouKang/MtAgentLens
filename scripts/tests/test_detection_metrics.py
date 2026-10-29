# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Contract tests for marker-based detection metrics."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.evaluation.detection_metrics import build_report  # noqa: E402


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _ground_truth(release: Path, scenario_id: str, label: str, attack_type: str) -> None:
    _write(
        release / "ground_truth" / "category" / f"{scenario_id}.json",
        {
            "scenario_id": scenario_id,
            "label": label,
            "attack_type": attack_type,
            "category": "test",
        },
    )


def _run(campaign: Path, scenario_id: str, marker: bool, decision: str) -> dict:
    run_dir = campaign / "attempts" / scenario_id / "runs" / "run-1"
    _write(run_dir / "summary.json", {"marker_executed": marker})
    return {
        "state": "completed",
        "scenario_id": scenario_id,
        "run_dir": str(run_dir),
        "final_decision": decision,
        "capture_quality_status": "healthy",
    }


def test_marker_false_unsafe_is_not_a_true_positive(tmp_path: Path) -> None:
    release = tmp_path / "release"
    campaign = tmp_path / "campaign"
    _ground_truth(release, "attack-executed", "attack", "A")
    _ground_truth(release, "attack-refused", "attack", "A")
    _ground_truth(release, "benign-quiet", "benign", "benign")
    records = [
        _run(campaign, "attack-executed", True, "Unsafe"),
        _run(campaign, "attack-refused", False, "Unsafe"),
        _run(campaign, "benign-quiet", False, "Safe"),
    ]
    campaign.mkdir(parents=True, exist_ok=True)
    (campaign / "results.jsonl").write_text(
        "\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8"
    )

    report = build_report(campaign, release)

    assert report["cohort"] == {
        "total": 3,
        "attack": 2,
        "benign": 1,
        "attack_a": 2,
        "attack_b": 0,
    }
    assert report["execution"]["attacks_marker_true"] == 1
    assert report["execution"]["attack_execution_rate"] == 0.5
    assert report["detection"]["itt_tpr"] == 0.5  # 1 / 2 attacks
    assert report["detection"]["pp_tpr"] == 1.0  # 1 / 1 executed
    assert report["detection"]["unexecuted_alert"] == 1
    assert report["benign"]["fpr"] == 0.0


def test_missing_marker_is_not_estimable_not_zero(tmp_path: Path) -> None:
    release = tmp_path / "release"
    campaign = tmp_path / "campaign"
    _ground_truth(release, "attack-unknown", "attack", "B")
    record = _run(campaign, "attack-unknown", True, "Unsafe")
    # Remove the run summary: marker evidence is absent.
    Path(record["run_dir"], "summary.json").unlink()
    campaign.mkdir(parents=True, exist_ok=True)
    (campaign / "results.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")

    report = build_report(campaign, release)

    assert report["execution"]["attacks_marker_missing"] == 1
    assert report["detection"]["pp_tpr"] is None
    assert report["detection"]["itt_tpr"] == 0.0
    assert report["not_estimable"]["pp_tpr"] is True
