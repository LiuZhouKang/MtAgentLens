from __future__ import annotations

import hashlib
import json
from pathlib import Path

from compare.scripts.build.build_paper_evaluation_report import _validated_prompt_contract, build_report


def test_build_paper_evaluation_report_from_completed_artifacts(tmp_path: Path) -> None:
    output = tmp_path / "evaluation_report.json"

    report = build_report(output_path=output)

    assert output.exists()
    assert report["schema"] == "mtagentlens.paper_evaluation_report.v1"
    metrics = {row["tool_id"]: row for row in report["main_metrics"]}
    assert set(metrics) >= {"mtagentlens", "llm-guard", "meta-prompt-guard", "vigil"}
    assert metrics["mtagentlens"]["f1"] == 1.0
    assert metrics["mtagentlens"]["claim_coverage"] == 1.0
    assert metrics["llm-guard"]["supported_scenarios"] == 67
    assert metrics["meta-prompt-guard"]["supported_scenarios"] == 67
    assert metrics["vigil"]["supported_scenarios"] == 67
    assert report["_metadata"]["controlled_ablation_status"] == "completed"
    assert report["_metadata"]["controlled_ablation_artifact"] == "compare/results/paper/controlled_ablation_results.json"
    assert report["_metadata"]["performance_status"] == "completed"
    assert report["_metadata"]["backend_capture_health_status"] == "completed"
    assert report["_metadata"]["external_benchmark_status"] == "deferred_from_main_claims"
    assert report["_metadata"]["statistics_scope"] == (
        "completed_artifacts_only:"
        "frozen_main_prompt_baseline,"
        "controlled_ablation,"
        "backend_capture_health_canary,"
        "local_performance_microbenchmarks"
    )
    assert "Local process/file performance microbenchmarks" in " ".join(report["claim_boundaries"]["supported_claims"])
    assert "external benchmark validation" in report["claim_boundaries"]["unsupported_claims"]
    assert "runtime overhead and time-to-detect" not in report["claim_boundaries"]["unsupported_claims"]
    assert "468-benchmark external validation" not in report["claim_boundaries"]["unsupported_claims"]
    assert "mcnemar" in report
    assert "pr_curves" in report
    assert report["mcnemar"]["llm-guard"]["paired_unit"] == "scenario"
    assert report["mcnemar"]["llm-guard"]["b_mtap_correct_prompt_wrong"] == metrics["llm-guard"]["fn"]
    assert report["mcnemar"]["llm-guard"]["c_prompt_correct_mtap_wrong"] == 0

    loaded = json.loads(output.read_text(encoding="utf-8"))
    assert loaded["main_metrics"][0]["tool_id"] == "mtagentlens"


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
