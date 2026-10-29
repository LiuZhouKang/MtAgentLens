# SPDX-License-Identifier: Apache-2.0 OR MIT
import json
from pathlib import Path

import pytest

from scripts.evaluation import overhead_paper_assets as assets

METRICS = (
    "condition_elapsed_s", "agent_duration_s", "collector_cpu_seconds",
    "collector_cpu_mean_percent", "collector_rss_peak_mib", "db_total_peak_mib",
    "trace_final_mib", "graph_nodes_final", "graph_edges_final",
    "graph_update_count", "graph_update_total_ns", "observer_attempts",
    "observer_input_tokens", "observer_output_tokens",
    "observer_cost_upper_bound_usd", "total_cost_upper_bound_usd",
    "alert_total_operations", "alert_linked_operations", "alert_coverage",
    "alert_p50_ms", "alert_p95_ms", "observer_p50_ms", "raw_missing_fraction",
    "persisted_operation_missing_fraction",
)


def _stats(value):
    return {"mean": value, "min": value * 0.9, "max": value * 1.1, "n": 3}


def _fixture(tmp_path):
    report = tmp_path / "study" / "report"
    report.mkdir(parents=True)
    formal = report.parent / "formal"
    formal.mkdir()
    cells = {}
    runs = []
    for profile in assets.PROFILES:
        for level in assets.LEVELS:
            key = f"{profile}/{level}"
            cells[key] = {
                "profile": profile, "concurrency": level, "n_runs": 3,
                "cell_status": "complete", "planned": True,
                "alert_total_operations": _stats(level * 16),
                "alert_linked_operations": _stats(1),
                "alert_coverage": _stats(1 / (level * 16)),
                "graph_update_total_ns": _stats(1e6),
                "observer_attempts": _stats(1 if profile == "C3" else 0),
                "observer_cost_upper_bound_usd": _stats(.001 if profile == "C3" else 0),
            }
            for metric in METRICS:
                cells[key].setdefault(metric, _stats(10 if profile == "C3" else 1))
            for repeat in (1, 2, 3):
                runs.append({
                    "condition_id": f"c{level}-r{repeat}-{profile}",
                    "block_id": f"c{level}-r{repeat}", "profile": profile,
                    "concurrency": level, "repeat": repeat, "included": True,
                    "result_status": "completed",
                    "condition_elapsed_s": 10,
                    "agent_duration_s": 10,
                    "collector_cpu_seconds": 1,
                    "collector_cpu_mean_percent": 10,
                    "collector_rss_peak_mib": 100,
                    "db_total_peak_mib": 1,
                    "trace_final_mib": 1,
                    "graph_nodes_final": 10,
                    "graph_edges_final": 10,
                    "graph_update_count": 10,
                    "graph_update_total_ns": 1e6,
                    "observer_attempts": 1 if profile == "C3" else 0,
                    "observer_input_tokens": 1,
                    "observer_output_tokens": 1,
                    "observer_cost_upper_bound_usd": .001,
                    "total_cost_upper_bound_usd": .001,
                    "alert_total_operations": level * 16,
                    "alert_linked_operations": 1,
                    "alert_coverage": 1 / (level * 16),
                    "alert_p50_ms": 10,
                    "alert_p95_ms": 10,
                    "observer_p50_ms": 10,
                    "raw_missing_fraction": 0.0,
                    "persisted_operation_missing_fraction": 0.0,
                    "layer_counters": {"sources": {
                        "Process:1": {"lost": 100 if level == 32 else 0},
                        "SSL:2": {"lost": 0}}},
                })
    summary = {"schema": "mtagentlens.overhead-summary/1", "cells": cells}
    raw = {"runs": runs, "paired_overheads": [
        {"concurrency": 1, "block_id": "c1-r1",
         "agent_duration_increase_percent": 10.0,
         "agent_wall_span_increase_percent": 10.0}]}
    (report / "summary.json").write_text(json.dumps(summary))
    (report / "raw-per-run.json").write_text(json.dumps(raw))
    (formal / "execution-report.json").write_text(json.dumps({
        "conditions_completed": 36, "complete": True, "plan_sha256": "a" * 64,
        "known_cost_upper_bound_usd": .6, "prior_cost_upper_bound_usd": .1,
        "recovery": {"archived_attempts": [{"condition_id": "c32-r3-C3",
                                            "path": "/tmp/archived"}]}}))
    (report.parent / "formal-recovery-amendment.json").write_text(json.dumps({
        "frozen_shutdown_timeout_s": 300, "recovery_shutdown_timeout_s": 600,
        "recovered_status": "completed", "recovered_condition_elapsed_s": 40,
        "cost_upper_bound_usd": .03}))
    tls = {"schema": "mtagentlens.probe-overhead-measurement/1", "status": "ok",
           "repeats": [{"programs": [
               {"id": 1, "name": "probe_SSL_rw_enter", "covered": True,
                "ns_per_call": 100, "run_cnt_delta": 10}]}],
           "summary": {"ns_per_successful_ssl_operation": {
               "mean": 100, "min": 90, "max": 110, "n": 3}}}
    diagnostics = [{"schema": "mtagentlens.probe-overhead-diagnostic/1", "status": "ok",
                    "programs": [
                        {"id": 2, "name": "enter_syscall", "covered": True,
                         "ns_per_call": 60, "run_cnt_delta": 10},
                        {"id": 3, "name": "probe_SSL_read_exit", "covered": True,
                         "ns_per_call": 999, "run_cnt_delta": 10}]}]
    return report, tls, diagnostics


def test_generate_writes_individual_figures_and_tables(tmp_path):
    report, tls, diagnostics = _fixture(tmp_path)
    out = tmp_path / "assets"
    result = assets.generate(report, out, probes=(tls, diagnostics))
    figures = sorted((out / "figures").glob("fig*.png"))
    assert len(figures) == 14
    assert all(path.stat().st_size > 1000 for path in figures)
    assert (out / "figures" / "fig01_component_walltime.pdf").is_file()
    assert (out / "tables" / "table1_component_accounting.md").is_file()
    assert (out / "tables" / "table5_probes.md").is_file()
    assert "T1." in (out / "tables" / "table1_component_accounting.md").read_text()
    probe_table = (out / "tables" / "table5_probes.md").read_text()
    assert "TLS microbenchmark" in probe_table
    assert "C1 diagnostic" in probe_table
    assert result["figures"]


def test_unknown_summary_schema_is_rejected(tmp_path):
    report, _, _ = _fixture(tmp_path)
    (report / "summary.json").write_text(json.dumps({"schema": "wrong", "cells": {}}))
    with pytest.raises(assets.AssetError):
        assets.generate(report, tmp_path / "assets")
