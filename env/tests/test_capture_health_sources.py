"""Runtime health follows configured sources and observed capture evidence."""
import json
from types import SimpleNamespace

from mtagentlens_benchmark.engine import collector_manager as capture
from mtagentlens_benchmark.engine.executor import _verify_collector_output


def _runtime(tmp_path, monkeypatch, trace_args, startup=None):
    manager = capture.CollectorManager(SimpleNamespace(root=tmp_path), trace_args=trace_args)
    manager._output_dir = tmp_path
    manager._health_snapshot.update(startup or {})
    sources = capture._configured_capture_sources(trace_args)
    rows = [{"event": {"tls": "TLS_RINGBUF_STATS", "process": "PROCESS_RINGBUF_STATS"}[source],
             "dropped_events": 0} for source in sorted(sources)]
    (tmp_path / "collector.log").write_text("\n".join(json.dumps(row) for row in rows))
    (tmp_path / "collector").write_text('{"source":"ssl"}\n' if "tls" in sources else '')
    monkeypatch.setattr(capture, "_read_stable_responsibility_db_metrics",
                        lambda _: {"process_events": 1 if "process" in sources else 0,
                                   "tls_sessions": 1 if "tls" in sources else 0,
                                   "graph_edges": 1})
    manager._collect_runtime_metrics()
    return manager.health_snapshot["runtime_metrics"]


def test_process_only_capture_does_not_require_disabled_tls(tmp_path, monkeypatch):
    metrics = _runtime(tmp_path, monkeypatch, ["--ssl=false", "--process=true"])
    assert metrics["overall_healthy"] is True


def test_empty_tracefs_note_is_not_an_explicit_probe_failure(tmp_path, monkeypatch):
    metrics = _runtime(tmp_path, monkeypatch, ["--ssl=true", "--process=true"], {
        "uprobe_verification": {"attached": False, "errors": [], "note": "perf_event attachment"},
    })
    assert metrics["overall_healthy"] is True
    assert metrics["startup_capture_verified"] is False


def test_explicit_probe_failure_still_prevents_healthy_capture(tmp_path, monkeypatch):
    metrics = _runtime(tmp_path, monkeypatch, ["--ssl=true", "--process=true"], {
        "uprobe_verification": {"attached": False, "errors": ["attach failed"]},
    })
    assert metrics["overall_healthy"] is False


def test_canary_uses_tls_events_in_the_trace_not_process_command_text(tmp_path):
    trace = tmp_path / "capture"
    trace.write_text(json.dumps({"source": "ssl", "data": {"buf": "mtagentlens-canary"}}))
    assert capture._canary_captured(tmp_path, "capture") is True
    trace.write_text(json.dumps({"source": "process", "data": {"command": "mtagentlens-canary"}}))
    assert capture._canary_captured(tmp_path, "capture") is False


def test_stderr_does_not_masquerade_as_trace_output(tmp_path):
    (tmp_path / "capture.log").write_text("diagnostic output only")
    collector = SimpleNamespace(database_path=None, log_file="capture")
    result = _verify_collector_output(tmp_path, collector)
    assert result["trace_non_empty"] is False
    (tmp_path / "capture").write_text('{"source":"process"}\n')
    result = _verify_collector_output(tmp_path, collector)
    assert result["trace_non_empty"] is True


def test_split_false_tls_flag_skips_tls_startup_probes(tmp_path, monkeypatch):
    calls = []

    class Process:
        pid = 123
        stdin = None

        def poll(self):
            return None

    monkeypatch.setattr(capture.subprocess, "Popen", lambda *args, **kwargs: Process())
    monkeypatch.setattr(capture, "_find_claude_binary_with_diagnostics", lambda: ("/tmp/synthetic-agent", {}))
    monkeypatch.setattr(capture.CollectorManager, "_check_collector_startup", lambda *args: None)
    monkeypatch.setattr(capture, "_verify_uprobes_attached", lambda *args: calls.append("uprobe") or {})
    monkeypatch.setattr(capture, "_tls_canary_test", lambda *args, **kwargs: calls.append("canary") or {})
    workspace = SimpleNamespace(root=tmp_path, project_root=tmp_path)
    manager = capture.CollectorManager(workspace, sudo=False,
        trace_args=["--ssl", "false", "--process=true"])
    manager.start(tmp_path)
    assert calls == []
