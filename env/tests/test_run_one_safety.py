"""Safety contract for env/run_one.py: run a candidate, never publish it."""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

RUN_ONE_PATH = Path(__file__).resolve().parents[1] / "run_one.py"
SCENARIO_ID = "safety-probe"
CATEGORY = "benign"
SENTINELS = {
    "responsibility.db": b"frozen-db-sentinel",
    "collector.log": b"frozen-log-sentinel",
    "responsibility.db-wal": b"frozen-wal-sentinel",
    "responsibility.db-shm": b"frozen-shm-sentinel",
}


def _load_run_one():
    spec = importlib.util.spec_from_file_location(
        "run_one_safety_under_test", RUN_ONE_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeWorkspace:
    def __init__(self, root: Path):
        self.root = root
        self.fixtures_dir = root / "scenarios" / "fixtures"

    def scenario_path(self, scenario_id: str) -> Path:
        return self.root / "scenarios" / f"{scenario_id}.json"


class _FakeCollectorManager:
    def __init__(self, workspace, sudo: bool = True):
        self.workspace = workspace
        self.sudo = sudo

    def set_run_context(self, *args) -> None:
        pass


def _make_candidate_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE graph_edges (edge_type TEXT)")
        conn.execute("INSERT INTO graph_edges VALUES ('PolicyBoundaryCrossing')")
        conn.execute("CREATE TABLE responsibility_alerts (id INTEGER)")
        conn.execute("INSERT INTO responsibility_alerts VALUES (1)")
        conn.execute("CREATE TABLE correlation_evidence (id INTEGER)")
        conn.execute("INSERT INTO correlation_evidence VALUES (1)")
        conn.commit()
    finally:
        conn.close()


def _prepare(tmp_path: Path, monkeypatch) -> tuple[object, Path]:
    run_one = _load_run_one()
    monkeypatch.setattr(run_one, "ROOT", tmp_path)
    monkeypatch.setattr(run_one, "AUDIT_SCRIPT", tmp_path / "env/scripts/audit_trace.py")
    env_root = tmp_path / "env"
    (env_root / "scenarios" / "fixtures").mkdir(parents=True)
    scenario = env_root / "scenarios" / f"{SCENARIO_ID}.json"
    scenario.write_text(
        json.dumps(
            {
                "mode": "single_agent",
                "category": CATEGORY,
                "agents": [{"role_file": "probe", "task": "noop"}],
                "fixture_dir": SCENARIO_ID,
                "timeout": 5,
            }
        ),
        encoding="utf-8",
    )
    workspace = _FakeWorkspace(env_root)

    class _WorkspaceFactory:
        @classmethod
        def discover(cls):
            return workspace

    monkeypatch.setattr(run_one, "BenchmarkWorkspace", _WorkspaceFactory)
    monkeypatch.setattr(run_one, "CollectorManager", _FakeCollectorManager)
    monkeypatch.setattr(run_one, "RUNS_DIR", tmp_path / "runs", raising=False)

    frozen_root = tmp_path / "datasets/FuxiBench/traces"
    frozen_dir = frozen_root / CATEGORY / SCENARIO_ID
    frozen_dir.mkdir(parents=True)
    for name, payload in SENTINELS.items():
        (frozen_dir / name).write_bytes(payload)
    monkeypatch.setattr(
        run_one, "TRACES_DIR", frozen_root, raising=False
    )
    return run_one, frozen_dir


def _invoke(
    run_one,
    monkeypatch,
    *,
    success: bool,
    audit_returncode: int,
) -> tuple[bool, list[tuple[list[str], dict]], list[Path]]:
    run_dirs: list[Path] = []

    def fake_execute(config, run_dir, workspace, execution_mode, collector):
        run_dir.mkdir(parents=True, exist_ok=True)
        collector_dir = run_dir / "collector"
        collector_dir.mkdir()
        db_path = collector_dir / "collector.responsibility.db"
        _make_candidate_db(db_path)
        (collector_dir / "collector.log").write_text(
            "candidate log\n", encoding="utf-8"
        )
        run_dirs.append(run_dir)
        return SimpleNamespace(
            success=success,
            database_path=db_path,
            error=None if success else "scenario failed",
        )

    audit_calls: list[tuple[list[str], dict]] = []

    def fake_run(args, **kwargs):
        audit_calls.append((list(args), dict(kwargs)))
        return subprocess.CompletedProcess(
            args, audit_returncode, stdout="", stderr=""
        )

    monkeypatch.setattr(run_one, "execute_scenario", fake_execute)
    monkeypatch.setattr(subprocess, "run", fake_run)
    return run_one.run_one(SCENARIO_ID), audit_calls, run_dirs


def _assert_frozen_untouched(frozen_dir: Path) -> None:
    for name, payload in SENTINELS.items():
        path = frozen_dir / name
        assert path.exists(), f"frozen {name} was deleted"
        assert path.read_bytes() == payload, f"frozen {name} was overwritten"


def _assert_canonical_audit(
    run_one, call: tuple[list[str], dict], run_dir: Path
) -> None:
    args, kwargs = call
    assert args[0] == sys.executable
    assert Path(args[1]).resolve() == (
        run_one.ROOT / "env" / "scripts" / "audit_trace.py"
    ).resolve()
    assert Path(args[2]) == run_dir
    assert args[3] == SCENARIO_ID
    assert kwargs.get("shell") is not True


def test_failed_execution_is_not_success_and_skips_admission(tmp_path, monkeypatch):
    run_one, frozen_dir = _prepare(tmp_path, monkeypatch)
    ok, audit_calls, _ = _invoke(
        run_one, monkeypatch, success=False, audit_returncode=0
    )

    assert ok is False
    assert audit_calls == []
    _assert_frozen_untouched(frozen_dir)


def test_success_with_nonzero_audit_is_not_admitted(tmp_path, monkeypatch):
    run_one, frozen_dir = _prepare(tmp_path, monkeypatch)
    ok, audit_calls, run_dirs = _invoke(
        run_one, monkeypatch, success=True, audit_returncode=2
    )

    assert ok is False
    assert len(audit_calls) == 1
    _assert_canonical_audit(run_one, audit_calls[0], run_dirs[0])
    assert (run_dirs[0] / "collector" / "collector.responsibility.db").exists()
    _assert_frozen_untouched(frozen_dir)


def test_success_with_passing_audit_is_admitted_without_publishing(
    tmp_path, monkeypatch
):
    run_one, frozen_dir = _prepare(tmp_path, monkeypatch)
    ok, audit_calls, run_dirs = _invoke(
        run_one, monkeypatch, success=True, audit_returncode=0
    )

    assert ok is True
    assert len(audit_calls) == 1
    _assert_canonical_audit(run_one, audit_calls[0], run_dirs[0])
    assert (run_dirs[0] / "collector" / "collector.responsibility.db").exists()
    _assert_frozen_untouched(frozen_dir)
