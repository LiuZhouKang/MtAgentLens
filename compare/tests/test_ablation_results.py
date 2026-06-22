from __future__ import annotations

from pathlib import Path

from compare.scripts.audit.audit_paper_experiment_readiness import audit
from compare.scripts.build.build_ablation_results import build_ablation_results


def test_offline_ablation_report_is_explicitly_not_controlled(tmp_path: Path) -> None:
    report = build_ablation_results(output_path=tmp_path / "ablation_results.json")

    assert report["schema"] == "mtagentprism.ablation_results.v1"
    assert report["methodology"] == "offline_evidence_masking"
    assert report["is_controlled_feature_flag_replay"] is False
    assert len(report["variants"]) == 6
    assert report["full_metrics"]["f1"] == 1.0
    assert report["unsupported_controlled_variants"]


def test_readiness_gate_does_not_accept_offline_ablation_as_controlled(tmp_path: Path) -> None:
    """Legacy offline ablation must not satisfy controlled_ablation_results check.

    The test creates only a legacy ablation_results.json in an isolated results
    tree with no controlled_ablation_results.json, then verifies the readiness
    gate returns blocker for the controlled_ablation_results check.
    """
    paper_dir = tmp_path / "paper"
    paper_dir.mkdir(parents=True)
    build_ablation_results(output_path=paper_dir / "ablation_results.json")

    result = audit(
        dataset_dir=Path("compare/Dataset/FuxiBench"),
        design_doc=Path("docs/design/experiment-design.md"),
        lockfile=Path("compare/tool-lock.json"),
        results_root=tmp_path,
    )
    checks = {check["id"]: check for check in result["checks"]}

    assert checks["controlled_ablation_results"]["status"] == "blocker"
