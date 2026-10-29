from pathlib import Path

from mtagentlens_benchmark.engine.collector_manager import CollectorManager
from mtagentlens_benchmark.workspace import BenchmarkWorkspace


def manager(comm: str = "claude") -> CollectorManager:
    return CollectorManager(BenchmarkWorkspace.discover(Path("env")), comm=comm)


def test_default_comm_includes_the_real_binary_basename() -> None:
    # The CLI is 'claude'; the kernel comm of the on-disk binary is 'claude.exe'.
    assert manager()._effective_comm("/opt/bin/claude.exe") == "claude,claude.exe"


def test_explicit_capture_surface_name_is_not_broadened() -> None:
    assert manager("claude-capture")._effective_comm("/opt/bin/claude.exe") == "claude-capture"


def test_environment_override_wins(monkeypatch) -> None:
    monkeypatch.setenv("MTAGENTLENS_AGENT_COMM", "claude.exe")
    assert manager()._effective_comm("/opt/bin/claude.exe") == "claude.exe"


def test_missing_binary_keeps_the_generic_default() -> None:
    assert manager()._effective_comm(None) == "claude"
