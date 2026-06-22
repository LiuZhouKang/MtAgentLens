from pathlib import Path
import json
import pytest

from mtagentprism_benchmark.engine.collector_manager import CollectorManager
from mtagentprism_benchmark.engine.executor import execute_scenario
from mtagentprism_benchmark.engine.runner import _save_summary
from mtagentprism_benchmark.types import AgentSpec, ScenarioConfig, ScenarioMode
from mtagentprism_benchmark.types import RunResult
from mtagentprism_benchmark.workspace import BenchmarkWorkspace


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

    monkeypatch.setattr(
        "mtagentprism_benchmark.engine.collector_manager.subprocess.Popen",
        fake_popen,
    )
    monkeypatch.setattr(
        "mtagentprism_benchmark.engine.collector_manager._find_claude_binary",
        lambda: None,
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
