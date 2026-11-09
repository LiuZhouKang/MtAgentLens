# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Only the current documented review contract permits a scenario/GT override."""
import copy
import json
from pathlib import Path

from scripts.evaluation.check_scenario_integrity import check_one

ROOT = Path(__file__).resolve().parents[2]
GT = ROOT / "datasets/FuxiBench/ground_truth/reconnaissance/atlas-recon-discovery.json"
SCENARIOS = ROOT / "env/scenarios"
FIXTURES = SCENARIOS / "fixtures"


def _problems(gt):
    return [p for p in check_one(gt, SCENARIOS, FIXTURES) if p["check"] == "type_matches_gt"]


def test_undocumented_type_mismatch_is_reported():
    gt = copy.deepcopy(json.loads(GT.read_text()))
    gt["attack_type"] = "B"
    gt.pop("label_annotation", None)
    assert _problems(gt), "an unexplained type mismatch must stay a problem"


def test_documented_review_override_is_accepted():
    gt = copy.deepcopy(json.loads(GT.read_text()))
    gt["attack_type"] = "B"
    gt["label_annotation"] = {
        "method": "human_review_and_revision",
        "human_review_status": "completed",
        "reviewers": ["刘周康", "唐浩钺", "刘锦样"],
    }
    assert not _problems(gt), "a fully documented and adjudicated override is allowed"
