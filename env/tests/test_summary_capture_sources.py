"""The summary must reuse recovered source config, or mark completeness unknown."""
import importlib.util
import json
from pathlib import Path

SUMMARY = Path(__file__).parents[1] / "scripts/summarize_responsibility_run.py"
spec = importlib.util.spec_from_file_location("summarize_responsibility_run", SUMMARY)
summary = importlib.util.module_from_spec(spec)
spec.loader.exec_module(summary)


def _run_dir(tmp_path, health=None, log=None):
    collector = tmp_path / "run" / "collector"
    collector.mkdir(parents=True)
    (collector / "collector.log").write_text(json.dumps(log), encoding="utf-8")
    (collector / "collector").write_text("", encoding="utf-8")
    if health is not None:
        (collector / "collector_health.json").write_text(
            json.dumps(health), encoding="utf-8"
        )
    return tmp_path / "run"


def test_summary_marks_completeness_unknown_without_source_config(tmp_path):
    run = _run_dir(tmp_path, log={"event": "PROCESS_RINGBUF_STATS", "dropped_events": 0})
    diagnostics = summary.runtime_diagnostics(run)
    assert diagnostics["ringbuf_drops"] == 0
    assert diagnostics["capture_stats_complete"] is None


def test_summary_uses_persisted_source_config(tmp_path):
    run = _run_dir(
        tmp_path,
        health={"configured_capture_sources": ["tls", "process"]},
        log={"event": "PROCESS_RINGBUF_STATS", "dropped_events": 0},
    )
    diagnostics = summary.runtime_diagnostics(run)
    assert diagnostics["capture_stats_complete"] is False


def test_health_lines_do_not_report_the_removed_onnx_classifier(tmp_path):
    run = _run_dir(
        tmp_path,
        log={"event": "removed classifier banner", "text": "DeBERTa classifier"},
    )
    assert summary.health_lines(run) == []
