"""Render isolated synthetic inputs; never assert old experimental numbers."""
import json

import compare.scripts.vis.generate_canonical_figures as figures


def test_main_figure_uses_actual_sample_count_and_metrics(synthetic_paper_report):
    from compare.scripts.vis.paper_report import load_paper_report_v1
    figure = figures._fig_main_metrics(load_paper_report_v1(synthetic_paper_report))
    assert "8" in figure["title"]
    assert figure["source_data"]["rows"][0]["f1"] == 0.75
    assert "full" not in figure["claim"].lower()
    assert "67" not in figure["svg"]


def test_ablation_figure_does_not_reuse_previous_experiment_claims():
    artifact = {"baseline": {"metrics": {"scenario_recall": 0.6}}, "variants": [
        {"variant_id": "disable-file-read", "status": "completed", "metrics": {"scenario_recall": 0.8}}]}
    figure = figures._fig_ablation(artifact)
    assert figure["source_data"]["rows"][0]["scenario_recall"] == 0.6
    assert figure["source_data"]["rows"][1]["scenario_recall"] == 0.8
    assert "0.537" not in figure["claim"]
    assert "reduces" not in figure["claim"]


def test_performance_figure_preserves_supplied_claim_boundary():
    boundary = {"supported": ["first-observation latency for low-level process-runner JSON events"],
        "not_supported": ["production LLM-agent end-to-end latency", "trace-mode ResponsibilityRuntime end-to-end latency"]}
    artifact = {"claim_boundary": boundary, "experiments": {
        "probe_latency": {"summary": {}}, "throughput": {"summary": {}},
        "memory": {"summary": {"peak_rss_mb": 12}},
        "time_to_detect": {"summary": {"latency_ms": {"mean": 8, "p95": 11}}}}}
    figure = figures._fig_performance(artifact)
    assert figure["source_data"]["claim_boundary"] == boundary
    assert figure["source_data"]["metric_rows"][1]["value"] == 8
    assert "<svg" in figure["svg"]
