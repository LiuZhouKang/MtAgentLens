# SPDX-License-Identifier: Apache-2.0 OR MIT
"""E3 merge: a report is only usable if its grid is well formed."""

import json
from pathlib import Path

import pytest

from scripts.evaluation.e3_threshold_sensitivity import SCHEMA, load_report, merge, render_table


def report(arm_runs: int, to_observer: list[int], flips: list[int]):
    return {
        "schema": SCHEMA,
        "runs": arm_runs,
        "runs_without_checkpoint": 0,
        "baseline_route": [["DirectSafe", arm_runs - 3], ["ToObserver", 2],
                           ["DirectUnsafe", 1]],
        "points": [
            {"threshold_safe": 0.15, "threshold_unsafe": 0.7, "runs": arm_runs,
             "direct_safe": 0, "to_observer": observer, "direct_unsafe": 0,
             "verdict_safe": 0, "verdict_review": 0, "verdict_unsafe": 0,
             "route_flips_vs_baseline": flip, "verdict_flips_vs_baseline": 0}
            for observer, flip in zip(to_observer, flips)
        ],
    }


def test_ablation_points_are_summarised():
    payload = report(10, [1], [0])
    payload["ablation_points"] = [
        {"dimension": dim, "dimension_name": name, "runs": 10, "verdict_safe": 10,
         "verdict_review": 0, "verdict_unsafe": 0, "route_flips_vs_baseline": 0,
         "verdict_flips_vs_baseline": 0}
        for dim, name in ((1, "boundary"), (6, "behavioral"))
    ]
    summary = merge({"attack": payload})
    arm = summary["arms"][0]
    assert arm["ablation_dimensions"] == [1, 6]
    assert arm["ablation_verdict_invariant"] is True
    assert arm["ablation_verdict_flip_counts"] == [0]


def test_scoring_points_are_summarised():
    payload = report(10, [1], [0])
    payload["scoring_points"] = [
        {"weight_factor": 0.8, "gate_center": 5.0, "runs": 10, "verdict_safe": 10,
         "verdict_review": 0, "verdict_unsafe": 0, "route_flips_vs_baseline": 1,
         "verdict_flips_vs_baseline": 0},
        {"weight_factor": 1.2, "gate_center": 20.0, "runs": 10, "verdict_safe": 10,
         "verdict_review": 0, "verdict_unsafe": 0, "route_flips_vs_baseline": 0,
         "verdict_flips_vs_baseline": 0},
    ]
    summary = merge({"benign": payload})
    arm = summary["arms"][0]
    assert arm["scoring_verdict_flip_counts"] == [0]
    assert arm["scoring_verdict_invariant"] is True


def test_merge_reports_route_range_and_verdict_invariance():
    summary = merge({"attack": report(44, [10, 30], [0, 20])})
    arm = summary["arms"][0]
    assert arm["runs"] == 44
    assert arm["max_route_flips"] == 20 and arm["min_route_flips"] == 0
    assert arm["observer_load_range"] == [10, 30]
    assert arm["baseline_observer_load"] == 2
    assert arm["verdict_invariant_across_grid"] is True
    assert "| attack | 44 |" in render_table(summary)


def test_a_verdict_flip_is_not_reported_as_invariance():
    payload = report(10, [1], [1])
    payload["points"][0]["verdict_flips_vs_baseline"] = 3
    summary = merge({"benign": payload})
    assert summary["arms"][0]["verdict_invariant_across_grid"] is False
    assert summary["arms"][0]["verdict_flip_counts"] == [3]


def test_loader_rejects_foreign_schema_and_inverted_grid(tmp_path: Path):
    path = tmp_path / "r.json"
    path.write_text(json.dumps({"schema": "other"}))
    with pytest.raises(ValueError, match="unexpected schema"):
        load_report(path)
    payload = report(2, [1], [0])
    payload["points"][0]["threshold_safe"] = 0.9
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="inverted"):
        load_report(path)


def test_relative_weights_keep_dimension_coverage_and_missing_study_unknown():
    payload = report(10, [1], [0])
    assert merge({'attack': payload})['arms'][0]['relative_weights'] is None
    payload['relative_weight_points'] = [
        {'dimension':dim,'weight_factor':factor,'gate_center':10.0,'runs':10,
         'route_flips_vs_baseline':int(dim==2),'verdict_flips_vs_baseline':0}
        for dim in range(1,7) for factor in (0.8,1.0,1.2)]
    result = merge({'attack':payload})['arms'][0]['relative_weights']
    assert result['dimensions'] == [1,2,3,4,5,6]
    assert result['points'] == 18
    assert result['max_route_flips'] == 1
    assert result['verdict_flip_counts'] == [0]
