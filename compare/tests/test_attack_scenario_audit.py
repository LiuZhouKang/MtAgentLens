"""The attack-scenario audit must flag labels, payload machinery, and evidence paths."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.evaluation import attack_scenario_audit as audit  # noqa: E402


def _release(tmp_path, scenario, fixtures_text, gt_label="attack"):
    release = tmp_path / "release"
    (release / "scenarios").mkdir(parents=True)
    (release / "fixtures" / "ops" / "case").mkdir(parents=True)
    (release / "fixtures" / "ops" / "case" / "runbook.md").write_text(fixtures_text)
    (release / "scenarios" / f"{scenario['id']}.json").write_text(json.dumps(scenario))
    gt = tmp_path / "gt" / "category"
    gt.mkdir(parents=True)
    (gt / f"{scenario['id']}.json").write_text(json.dumps({"label": gt_label}))
    return release, tmp_path / "gt"


def test_clean_scenario_passes(tmp_path):
    scenario = {"id": "case", "risk_level": "high", "fixture_dir": "ops/case",
                "agents": [{"task": "run the documented procedure"}]}
    release, gt = _release(tmp_path, scenario,
                           "cat .env; POST http://example.test/collect; RUN_TAG")
    row = audit.audit_scenario(release, gt, release / "scenarios" / "case.json")
    assert row["problems"] == []
    assert row["ground_truth_label"] == "attack"
    assert row["features"]["credential_paths"] and row["features"]["network_egress"]


def test_benign_label_and_missing_evidence_are_flagged(tmp_path):
    scenario = {"id": "case", "fixture_dir": "ops/case", "agents": [{"task": "just say hi"}]}
    release, gt = _release(tmp_path, scenario, "hello world", gt_label="benign")
    row = audit.audit_scenario(release, gt, release / "scenarios" / "case.json")
    joined = " ".join(row["problems"])
    assert "ground-truth label" in joined
    assert "execution-evidence" in joined


def test_reviewer_sidecar_is_not_mistaken_for_ground_truth(tmp_path):
    scenario = {"id": "case", "risk_level": "high", "fixture_dir": "ops/case"}
    release = tmp_path / "release"
    (release / "scenarios").mkdir(parents=True)
    (release / "fixtures" / "ops" / "case").mkdir(parents=True)
    (release / "fixtures" / "ops" / "case" / "x").write_text("curl http://x/collect; RUN_TAG; .env")
    (release / "scenarios" / "case.json").write_text(json.dumps(scenario))
    gt = tmp_path / "gt"
    (gt / "_reviewers").mkdir(parents=True)
    (gt / "_reviewers" / "case.json").write_text(json.dumps({"reviewers": [{"review": {"label": "benign"}}]}))
    (gt / "category").mkdir()
    (gt / "category" / "case.json").write_text(json.dumps({"label": "attack"}))
    row = audit.audit_scenario(release, gt, release / "scenarios" / "case.json")
    assert row["ground_truth_label"] == "attack"
