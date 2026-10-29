from pathlib import Path
import json
import sqlite3
import pytest

from mtagentlens_benchmark.engine.collector_manager import CollectorManager
from mtagentlens_benchmark.engine.collector_manager import _count_observer_calls
from mtagentlens_benchmark.engine.collector_manager import _parse_runtime_metrics_from_logs
from mtagentlens_benchmark.engine.collector_manager import _read_responsibility_db_metrics
from mtagentlens_benchmark.engine.collector_manager import _read_stable_responsibility_db_metrics
from mtagentlens_benchmark.engine.executor import execute_scenario
from mtagentlens_benchmark.engine.runner import _save_summary
from mtagentlens_benchmark.types import AgentSpec, ScenarioConfig, ScenarioMode
from mtagentlens_benchmark.types import RunResult
from mtagentlens_benchmark.workspace import BenchmarkWorkspace


class FailingCollector:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    def start(self, output_dir: Path) -> None:
        self.started = True
        raise RuntimeError("collector boot failed")

    def stop(self, wait_seconds: int = 5) -> None:
        self.stopped = True

    @property
    def is_running(self) -> bool:
        return False

    @property
    def output_paths(self) -> list[Path]:
        return []

    @property
    def database_path(self) -> Path | None:
        return None


def test_execute_scenario_fails_fast_when_collector_start_fails(tmp_path: Path) -> None:
    workspace = BenchmarkWorkspace.discover(Path("env"))
    run_dir = tmp_path / "run"
    scenario = ScenarioConfig(
        id="collector-start-failure",
        mode=ScenarioMode.SINGLE_AGENT,
        description="collector failure should fail the run before agents execute",
        agents=[AgentSpec(role_file="agent.md", task="this should not run")],
        fixtures_dir=tmp_path / "fixtures",
        timeout_seconds=1,
    )
    collector = FailingCollector()

    result = execute_scenario(
        scenario,
        run_dir,
        workspace,
        execution_mode="process",
        collector=collector,
        claude_bin="/definitely/not/used",
    )

    assert result.success is False
    assert "collector start failed" in (result.error or "")
    assert result.summary["collector_health"]["status"] == "failed"
    assert result.summary["collector_health"]["stage"] == "start"
    assert result.summary["collector_health"]["error_type"] == "RuntimeError"
    assert collector.started is True
    assert collector.stopped is False
    assert not (run_dir / "prompts" / "agent_0.txt").exists()


def test_save_summary_preserves_collector_health(tmp_path: Path) -> None:
    result = RunResult(
        run_id="run-1",
        scenario_id="scenario-1",
        success=True,
        duration_seconds=1.2,
        summary={
            "agents_succeeded": 1,
            "agents_total": 1,
            "collector_health": {
                "enabled": True,
                "status": "completed",
                "stop_status": "stopped",
            },
        },
    )

    _save_summary(tmp_path, result)

    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["collector_health"]["status"] == "completed"
    assert summary["agents_succeeded"] == 1


def test_collector_manager_start_raises_when_collector_exits_immediately(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = BenchmarkWorkspace.discover(Path("env"))
    processes: list[FakeProcess] = []

    class FakeProcess:
        def __init__(self, poll_result: int | None) -> None:
            self.poll_result = poll_result
            self.pid = 12345 + len(processes)
            self.stdin = None
            self.terminated = False
            self.killed = False

        def poll(self) -> int | None:
            return self.poll_result

        def terminate(self) -> None:
            self.terminated = True
            self.poll_result = 0

        def wait(self, timeout: int | None = None) -> int:
            return self.poll_result or 0

        def kill(self) -> None:
            self.killed = True
            self.poll_result = -9

        def send_signal(self, signum: int) -> None:
            self.terminated = True
            self.poll_result = 0

    def fake_popen(cmd, *args, **kwargs):
        poll_result = None if "tcpdump" in cmd else 1
        proc = FakeProcess(poll_result)
        processes.append(proc)
        return proc

    def fake_killpg(pid: int, signum: int) -> None:
        for proc in processes:
            if proc.pid == pid:
                proc.send_signal(signum)
                return

    monkeypatch.setattr(
        "mtagentlens_benchmark.engine.collector_manager.subprocess.Popen",
        fake_popen,
    )
    monkeypatch.setattr(
        "mtagentlens_benchmark.engine.collector_manager._find_claude_binary",
        lambda: None,
    )
    monkeypatch.setattr(
        "mtagentlens_benchmark.engine.collector_manager.os.killpg",
        fake_killpg,
    )

    manager = CollectorManager(
        workspace,
        binary="missing-collector",
        sudo=False,
        startup_check_seconds=0.0,
    )

    with pytest.raises(RuntimeError, match="collector exited during startup"):
        manager.start(tmp_path)

    assert len(processes) == 2
    assert processes[0].terminated is True
    assert manager.is_running is False


def test_collector_processes_start_in_own_sessions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = BenchmarkWorkspace.discover(Path("env"))
    popen_kwargs: list[dict] = []

    class FakeProcess:
        pid = 12345

        def __init__(self) -> None:
            self.stdin = None

        def poll(self) -> int | None:
            return None

        def terminate(self) -> None:
            pass

        def wait(self, timeout: int | None = None) -> int:
            return 0

        def kill(self) -> None:
            pass

        def send_signal(self, signum: int) -> None:
            pass

    def fake_popen(cmd, *args, **kwargs):
        popen_kwargs.append(dict(kwargs))
        return FakeProcess()

    monkeypatch.setattr(
        "mtagentlens_benchmark.engine.collector_manager.subprocess.Popen",
        fake_popen,
    )
    monkeypatch.setattr(
        "mtagentlens_benchmark.engine.collector_manager._find_claude_binary_with_diagnostics",
        lambda: (None, {"found": False}),
    )

    manager = CollectorManager(
        workspace,
        binary="collector/build/release/mtagentlens",
        sudo=False,
        startup_check_seconds=0.0,
        verify_uprobes=False,
        canary_test=False,
    )

    manager.start(tmp_path)

    assert len(popen_kwargs) == 2
    assert all(kwargs.get("start_new_session") is True for kwargs in popen_kwargs)


def test_collector_manager_passes_disable_responsibility_observer_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = BenchmarkWorkspace.discover(Path("env"))
    commands: list[list[str]] = []

    class FakeProcess:
        pid = 12345
        stdin = None

        def poll(self) -> int | None:
            return None

        def terminate(self) -> None:
            pass

        def wait(self, timeout: int | None = None) -> int:
            return 0

        def kill(self) -> None:
            pass

        def send_signal(self, signum: int) -> None:
            pass

    def fake_popen(cmd, *args, **kwargs):
        commands.append(list(cmd))
        return FakeProcess()

    monkeypatch.setattr(
        "mtagentlens_benchmark.engine.collector_manager.subprocess.Popen",
        fake_popen,
    )
    monkeypatch.setattr(
        "mtagentlens_benchmark.engine.collector_manager._find_claude_binary_with_diagnostics",
        lambda: (None, {"found": False}),
    )

    manager = CollectorManager(
        workspace,
        binary="collector/build/release/mtagentlens",
        sudo=False,
        startup_check_seconds=0.0,
        verify_uprobes=False,
        canary_test=False,
        disable_responsibility_observer=True,
    )

    manager.start(tmp_path)

    collector_cmd = commands[1]
    assert "--disable-responsibility-observer" in collector_cmd


def test_collector_manager_does_not_preserve_observer_env_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = BenchmarkWorkspace.discover(Path("env"))
    commands: list[list[str]] = []

    class FakeProcess:
        pid = 12345

        def __init__(self) -> None:
            self.stdin = None

        def poll(self) -> int | None:
            return None

        def terminate(self) -> None:
            pass

        def wait(self, timeout: int | None = None) -> int:
            return 0

        def kill(self) -> None:
            pass

        def send_signal(self, signum: int) -> None:
            pass

    def fake_popen(cmd, *args, **kwargs):
        commands.append(list(cmd))
        return FakeProcess()

    monkeypatch.delenv("MTAGENTLENS_PRESERVE_OBSERVER_ENV", raising=False)
    monkeypatch.setenv("MTAGENTLENS_SUDO_PASSWORD", "test-password")
    monkeypatch.setattr(
        "mtagentlens_benchmark.engine.collector_manager.subprocess.Popen",
        fake_popen,
    )
    monkeypatch.setattr(
        "mtagentlens_benchmark.engine.collector_manager._find_claude_binary_with_diagnostics",
        lambda: (None, {"found": False}),
    )

    manager = CollectorManager(
        workspace,
        binary="collector/build/release/mtagentlens",
        sudo=True,
        startup_check_seconds=0.0,
        verify_uprobes=False,
        canary_test=False,
    )

    manager.start(tmp_path)

    collector_cmd = commands[1]
    preserved = next(arg for arg in collector_cmd if arg.startswith("--preserve-env=")).split("=", 1)[1].split(",")
    assert not {"ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "MODEL"}.intersection(preserved)
    assert "MTAGENTLENS_HARD_SENSITIVE_EGRESS" in preserved


def test_collector_manager_preserves_observer_env_when_explicit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = BenchmarkWorkspace.discover(Path("env"))
    commands: list[list[str]] = []

    class FakeProcess:
        pid = 12345

        def __init__(self) -> None:
            self.stdin = None

        def poll(self) -> int | None:
            return None

        def terminate(self) -> None:
            pass

        def wait(self, timeout: int | None = None) -> int:
            return 0

        def kill(self) -> None:
            pass

        def send_signal(self, signum: int) -> None:
            pass

    def fake_popen(cmd, *args, **kwargs):
        commands.append(list(cmd))
        return FakeProcess()

    monkeypatch.setenv("MTAGENTLENS_PRESERVE_OBSERVER_ENV", "1")
    monkeypatch.setenv("MTAGENTLENS_SUDO_PASSWORD", "test-password")
    monkeypatch.setattr(
        "mtagentlens_benchmark.engine.collector_manager.subprocess.Popen",
        fake_popen,
    )
    monkeypatch.setattr(
        "mtagentlens_benchmark.engine.collector_manager._find_claude_binary_with_diagnostics",
        lambda: (None, {"found": False}),
    )

    manager = CollectorManager(
        workspace,
        binary="collector/build/release/mtagentlens",
        sudo=True,
        startup_check_seconds=0.0,
        verify_uprobes=False,
        canary_test=False,
    )

    manager.start(tmp_path)

    collector_cmd = commands[1]
    preserved = next(arg for arg in collector_cmd if arg.startswith("--preserve-env=")).split("=", 1)[1].split(",")
    assert {"ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "MODEL"}.issubset(preserved)


def test_runtime_health_uses_responsibility_db_component_evidence(tmp_path: Path) -> None:
    db = tmp_path / "collector.responsibility.db"
    with sqlite3.connect(db) as conn:
        conn.executescript(
            """
            CREATE TABLE tls_sessions(id TEXT);
            INSERT INTO tls_sessions VALUES ('tls-1');
            CREATE TABLE process_events(id TEXT);
            INSERT INTO process_events VALUES ('proc-1');
            CREATE TABLE graph_nodes(node_id TEXT);
            INSERT INTO graph_nodes VALUES ('node-1');
            CREATE TABLE graph_edges(edge_type TEXT);
            INSERT INTO graph_edges VALUES ('PolicyBoundaryCrossing');
            CREATE TABLE correlation_evidence(evidence_id TEXT);
            INSERT INTO correlation_evidence VALUES ('ev-1');
            CREATE TABLE responsibility_claims(claim_id TEXT);
            CREATE TABLE responsibility_alerts(alert_id TEXT);
            CREATE TABLE policy_evaluations(policy_id TEXT);
            INSERT INTO policy_evaluations VALUES ('policy-1');
            """
        )

    trace = tmp_path / "trace"
    trace.write_text("", encoding="utf-8")
    log_text = "\n".join([
        "BoringSSL detected! Attaching by offset...",
        "✓ Responsibility Observer LLM enabled",
        "Observer calls: 3 | Route: direct_safe | Final score: 0.11",
    ])

    metrics = _parse_runtime_metrics_from_logs(log_text, trace)
    metrics.update(_read_responsibility_db_metrics(db))
    metrics["tls_capture_active"] = bool(
        metrics.get("tls_sessions", 0) > 0
        or "BoringSSL detected! Attaching by offset" in log_text
        or metrics.get("ssl_events", 0) > 0
    )
    metrics["process_monitor_active"] = metrics.get("process_events", 0) > 0
    metrics["responsibility_runtime_active"] = bool(
        metrics.get("graph_edges", 0) > 0
        or metrics.get("correlation_evidence", 0) > 0
        or metrics.get("policy_evaluations", 0) > 0
    )
    metrics["observer_active"] = "Responsibility Observer LLM enabled" in log_text

    assert metrics["tls_sessions"] == 1
    assert metrics["process_events"] == 1
    assert metrics["pbc_edges"] == 1
    assert metrics["correlation_evidence"] == 1
    assert metrics["tls_capture_active"] is True
    assert metrics["process_monitor_active"] is True
    assert metrics["responsibility_runtime_active"] is True
    assert metrics["observer_active"] is True


def test_stable_db_metrics_keep_final_counts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshots = [
        {
            "responsibility_db_exists": True,
            "tls_sessions": 10,
            "process_events": 2,
            "graph_nodes": 3,
            "graph_edges": 4,
            "pbc_edges": 1,
            "correlation_evidence": 5,
            "responsibility_claims": 1,
            "responsibility_alerts": 1,
            "policy_evaluations": 1,
        },
        {
            "responsibility_db_exists": True,
            "tls_sessions": 11,
            "process_events": 2,
            "graph_nodes": 3,
            "graph_edges": 5,
            "pbc_edges": 1,
            "correlation_evidence": 6,
            "responsibility_claims": 1,
            "responsibility_alerts": 2,
            "policy_evaluations": 1,
        },
    ]

    def fake_read(_db_path: Path) -> dict:
        return snapshots.pop(0) if snapshots else {
            "responsibility_db_exists": True,
            "tls_sessions": 11,
            "process_events": 2,
            "graph_nodes": 3,
            "graph_edges": 5,
            "pbc_edges": 1,
            "correlation_evidence": 6,
            "responsibility_claims": 1,
            "responsibility_alerts": 2,
            "policy_evaluations": 1,
        }

    monkeypatch.setattr(
        "mtagentlens_benchmark.engine.collector_manager._read_responsibility_db_metrics",
        fake_read,
    )

    metrics = _read_stable_responsibility_db_metrics(
        tmp_path / "collector.responsibility.db",
        attempts=3,
        delay_seconds=0.0,
    )

    assert metrics["tls_sessions"] == 11
    assert metrics["graph_edges"] == 5
    assert metrics["correlation_evidence"] == 6
    assert metrics["responsibility_alerts"] == 2


def test_observer_call_count_can_use_classify_lines_when_summary_missing(
    tmp_path: Path,
) -> None:
    trace = tmp_path / "trace"
    trace.write_text("", encoding="utf-8")
    log_text = "\n".join([
        "✓ Responsibility Observer LLM enabled",
        '[classify] Observer LLM: safe confidence=0.92 — "ok"',
        '[classify] Observer LLM: unsafe confidence=0.68 — "review"',
    ])

    metrics = _parse_runtime_metrics_from_logs(log_text, trace)

    assert metrics["has_trace_output"] is False
    assert _count_observer_calls(log_text) == 2


def test_observer_call_count_prefers_summary_line() -> None:
    log_text = "\n".join([
        '[classify] Observer LLM: safe confidence=0.92 — "ok"',
        "Observer calls: 7 | Route: observer_safe | Final score: 0.08",
    ])

    assert _count_observer_calls(log_text) == 7


def test_runtime_metrics_read_the_trace_file_and_diagnostics_once(tmp_path, monkeypatch):
    workspace = BenchmarkWorkspace.discover(Path("env"))
    manager = CollectorManager(workspace, sudo=False, log_file="capture")
    manager._output_dir = tmp_path
    (tmp_path / "capture").write_text(
        '{"source":"ssl"}\n{"source":"http_parser"}\n{"source":"sse_processor"}\n'
    )
    # The manager is configured with --ssl=true and --process=true, so both
    # helpers must report their final counters for the run to be complete.
    (tmp_path / "capture.log").write_text(
        '{"event":"TLS_RINGBUF_STATS","dropped_events":0}\n'
        '{"event":"PROCESS_RINGBUF_STATS","dropped_events":0}\n'
    )
    monkeypatch.setattr(
        "mtagentlens_benchmark.engine.collector_manager._read_stable_responsibility_db_metrics",
        lambda _path: {"process_events": 1, "graph_edges": 1},
    )
    manager._collect_runtime_metrics()
    metrics = manager.health_snapshot["runtime_metrics"]
    assert metrics["ssl_events"] == 1
    assert metrics["http_events"] == 1
    assert metrics["overall_healthy"] is True
    assert metrics["configured_capture_sources"] == ["process", "tls"]


def test_collector_health_snapshot_persists_capture_source_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = BenchmarkWorkspace.discover(Path("env"))

    class FakeProcess:
        pid = 12345
        stdin = None

        def poll(self) -> int | None:
            return None

        def terminate(self) -> None:
            pass

        def wait(self, timeout: int | None = None) -> int:
            return 0

        def kill(self) -> None:
            pass

        def send_signal(self, signum: int) -> None:
            pass

    monkeypatch.setattr(
        "mtagentlens_benchmark.engine.collector_manager.subprocess.Popen",
        lambda *args, **kwargs: FakeProcess(),
    )
    monkeypatch.setattr(
        "mtagentlens_benchmark.engine.collector_manager._find_claude_binary_with_diagnostics",
        lambda: (None, {"found": False}),
    )

    manager = CollectorManager(
        workspace,
        binary="collector/build/release/mtagentlens",
        sudo=False,
        startup_check_seconds=0.0,
        verify_uprobes=False,
        canary_test=False,
        trace_args=["--ssl=false", "--process=true", "--stdio"],
    )
    manager.start(tmp_path)

    health = json.loads(
        (tmp_path / "collector_health.json").read_text(encoding="utf-8")
    )
    assert health["trace_args"] == ["--ssl=false", "--process=true", "--stdio"]
    assert set(health["configured_capture_sources"]) == {"process", "stdio"}


def test_collector_workspace_root_is_the_run_directory(tmp_path):
    """The harness starts the collector outside the agent's workspace.

    Reporting the collector's own cwd as the workspace made every in-workspace
    write look like a capability expansion (RC 2026-10-25: benign runs could not
    be cleared). The run directory is the agent's working directory.
    """
    from mtagentlens_benchmark.engine.collector_manager import collector_workspace_root

    run_dir = tmp_path / "20261025-000000-run"
    assert collector_workspace_root(run_dir / "collector") == run_dir.resolve()
