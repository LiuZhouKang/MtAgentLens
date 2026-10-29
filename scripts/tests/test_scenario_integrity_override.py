# SPDX-License-Identifier: Apache-2.0 OR MIT
"""A documented v5 type override must not hide an unexplained scenario/GT mismatch."""
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
    gt["attack_type"] = "C"  # scenario JSON declares B
    assert _problems(gt), "an unexplained type mismatch must stay a problem"


def test_adjudicated_v5_type_override_is_accepted():
    gt = copy.deepcopy(json.loads(GT.read_text()))
    gt["attack_type"] = "C"
    gt["label_annotation"] = {
        "annotation_version": "v5-merge-1",
        "method": "ai_consensus_3",
        "v5_attack_type": "C",
        "v5_disagreement": {
            "fields": {"attack_type": {"decision": "v5", "rationale": "adjudicated"}}
        },
    }
    assert not _problems(gt), "a fully documented and adjudicated override is allowed"
