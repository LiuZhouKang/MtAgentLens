from __future__ import annotations

import json
from pathlib import Path


def test_canonical_figure_manifest_binds_completed_source_artifacts() -> None:
    manifest_path = Path("compare/results/paper/figures/figure_manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["schema"] == "mtagentprism.paper_figure_manifest.v1"
    assert manifest["status"] == "completed"
    assert set(manifest["source_artifacts"]) == {
        "evaluation_report",
        "controlled_ablation",
        "backend_capture_health",
        "performance",
    }
    for source in manifest["source_artifacts"].values():
        assert Path(source["path"]).exists()
        assert len(source["sha256"]) == 64

    figures = {figure["id"]: figure for figure in manifest["figures"]}
    assert set(figures) == {
        "fig1_main_metrics",
        "fig2_controlled_ablation",
        "fig3_backend_health",
        "fig4_performance",
    }
    for figure in figures.values():
        outputs = figure["outputs"]
        for key in ("svg", "pdf", "png", "source_data"):
            path = Path(outputs[key])
            assert path.exists(), f"missing {key}: {path}"
            assert path.stat().st_size > 0
        assert "claim" in figure and figure["claim"]


def test_performance_figure_keeps_latency_scope_narrow() -> None:
    source = json.loads(
        Path("compare/results/paper/source_data/fig4_performance_source_data.json").read_text(
            encoding="utf-8"
        )
    )

    assert "first-observation latency for low-level process-runner JSON events" in source["claim_boundary"]["supported"]
    assert "production LLM-agent end-to-end latency" in source["claim_boundary"]["not_supported"]
    assert "trace-mode ResponsibilityRuntime end-to-end latency" in source["claim_boundary"]["not_supported"]
