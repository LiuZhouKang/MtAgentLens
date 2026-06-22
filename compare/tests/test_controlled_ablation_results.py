from __future__ import annotations

import json
from pathlib import Path

from compare.scripts.audit.audit_paper_experiment_readiness import _validate_controlled_ablation_artifact
from compare.scripts.build.build_controlled_ablation_results import build_controlled_ablation_results


def test_controlled_ablation_builder_defaults_to_blocked(tmp_path: Path) -> None:
    output = tmp_path / "controlled_ablation_results.json"
    artifact = build_controlled_ablation_results(output_path=output)

    assert artifact["schema"] == "mtagentprism.controlled_ablation_results.v1"
    assert artifact["status"] == "blocked"
    assert artifact["is_controlled_feature_flag_replay"] is True
    assert artifact["variants"] == []

    validation = _validate_controlled_ablation_artifact(output)
    assert validation["valid"] is False


def test_controlled_ablation_builder_accepts_completed_variant_input(tmp_path: Path) -> None:
    variants_input = tmp_path / "variant_runs.json"
    output = tmp_path / "controlled_ablation_results.json"
    outcomes = [
        {
            "scenario_id": f"scenario-{index:02d}",
            "status": "completed",
            "detected": index % 2 == 0,
            "matched_claims": 1,
            "expected_claims": 1,
        }
        for index in range(67)
    ]
    variants_input.write_text(
        json.dumps(
            {
                "baseline": {
                    "variant_id": "full_replay",
                    "status": "completed",
                    "runs_completed": 67,
                    "metrics": {"scenario_recall": 1.0, "claim_coverage": 1.0},
                    "parity": {"status": "pass"},
                },
                "variants": [
                    {
                        "variant_id": f"variant-{index}",
                        "status": "completed",
                        "disabled_features": [f"feature-{index}"],
                        "runs_completed": 67,
                        "metrics": {"scenario_recall": 0.9, "claim_coverage": 0.8},
                        "delta_vs_full": {"scenario_recall": -0.1},
                        "per_scenario_outcomes": outcomes,
                    }
                    for index in range(6)
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    artifact = build_controlled_ablation_results(output_path=output, variants_input=variants_input)

    assert artifact["status"] == "completed"
    validation = _validate_controlled_ablation_artifact(output)
    assert validation["valid"] is True
