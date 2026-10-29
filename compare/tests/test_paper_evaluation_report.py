from __future__ import annotations

import hashlib
import json
from pathlib import Path

from compare.scripts.build.build_paper_evaluation_report import _validated_prompt_contract, build_report


def test_paper_report_accepts_the_bound_fuxibench_package() -> None:
    contract = _validated_prompt_contract(Path("datasets/FuxiBench"))
    assert contract["status"] == "label_leak_free"


def test_paper_report_preserves_synthetic_metrics_and_unknown_quality(tmp_path: Path, synthetic_dataset: Path, monkeypatch) -> None:
    from env.scripts.evaluate_frozen import evaluate_dataset
    from compare.scripts.build.build_prompt_input_bundle import build_prompt_input_bundle
    import compare.scripts.build.build_paper_evaluation_report as builder
    build_prompt_input_bundle(dataset_dir=synthetic_dataset, scenarios_root=synthetic_dataset / "scenarios")
    results = tmp_path / "baseline"
    results.mkdir()
    (results / "metrics.csv").write_text("tool_id,tool_name,f1,scenario_recall\nlocal-test,Local test,0.5,0.5\n")
    (results / "tool_status.csv").write_text("tool_id,status\nlocal-test,no_findings\n")
    (results / "findings.jsonl").write_text("")
    for attr in ("DEFAULT_CONTROLLED_ABLATION", "DEFAULT_PERFORMANCE", "DEFAULT_BACKEND_HEALTH"):
        monkeypatch.setattr(builder, attr, tmp_path / (attr + ".json"))
    output = tmp_path / "report.json"
    report = build_report(dataset_dir=synthetic_dataset, prompt_results_dir=results, output_path=output)
    assert report["dataset_summary"]["total_cases"] == 2
    assert report["dataset_summary"]["paper_metrics_available"] is False
    assert "2-scenario" in report["claim_boundaries"]["supported_claims"][0]
    assert report["_metadata"]["controlled_ablation_status"] == "planned_missing_artifact"
    assert report["main_metrics"][1]["f1"] == 0.5
    assert json.loads(output.read_text())["main_metrics"][0]["tp"] == 1


def test_paper_report_rejects_prompt_label_leak(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset"
    prompt_dir = dataset / "prompt_inputs"
    case_dir = prompt_dir / "cases" / "demo"
    case_dir.mkdir(parents=True)
    (case_dir / "prompt.txt").write_text("Attack type: B\n", encoding="utf-8")
    digest = hashlib.sha256(b"Attack type: B\n").hexdigest()
    (prompt_dir / "manifest.json").write_text(
        json.dumps(
            {
                "schema": "mtagentlens.prompt_inputs.v1",
                "entries": [
                    {
                        "scenario_id": "demo",
                        "attack_type": "B",
                        "fixture_file_count": 1,
                        "sanitized_no_ground_truth_leak": True,
                        "prompt_text_path": "cases/demo/prompt.txt",
                        "bundle_json_path": "cases/demo/bundle.json",
                        "prompt_text_sha256": digest,
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (case_dir / "bundle.json").write_text("{}\n", encoding="utf-8")

    try:
        _validated_prompt_contract(dataset)
    except ValueError as exc:
        assert "label leaks" in str(exc)
    else:
        raise AssertionError("prompt label leak should prevent paper report generation")
