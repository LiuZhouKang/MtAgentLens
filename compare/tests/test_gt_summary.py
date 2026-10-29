import json
from pathlib import Path
import subprocess
import sys


def test_ai_resolution_does_not_clear_pending_human_review(tmp_path: Path):
    (tmp_path / "ground_truth").mkdir()
    (tmp_path / "prompt_inputs").mkdir()
    (tmp_path / "prompt_inputs/manifest.json").write_text(json.dumps({"entries": []}))
    (tmp_path / "ground_truth/case.json").write_text(json.dumps({
        "schema": "mtagentlens.ground_truth.v4", "scenario_id": "one", "label": "attack",
        "attack_type": "C", "quality": {"legacy_needs_human_review": True,
            "review": {"status": "resolved", "human_review": "waived_by_author"}}}))
    result = subprocess.run([sys.executable, "scripts/evaluation/gt_summary.py", "--dataset", str(tmp_path)],
                            capture_output=True, text=True, check=True)
    report = json.loads(result.stdout)
    assert report["needs_human_review"] == ["one"]
    assert report["ai_adjudicated_count"] == 1
    assert report["human_review_performed"] is False
