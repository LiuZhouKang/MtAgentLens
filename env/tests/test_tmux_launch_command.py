import shlex
from pathlib import Path
from types import SimpleNamespace

from mtagentlens_benchmark.engine import tmux_engine
from mtagentlens_benchmark.engine.tmux_engine import task_launch_command


def test_task_launch_command_materializes_a_multiline_task(tmp_path: Path) -> None:
    log = tmp_path / "agent.log"
    status = tmp_path / "agent.json"
    task = "line one\nline two with 'quotes' and $HOME"
    command = task_launch_command(
        pane_id="mtltest:0.0",
        task=task,
        log_file=log,
        status_file=status,
        env={},
        timeout_seconds=5,
        env_file=None,
        claude_bin="/bin/echo",
    )

    assert command[:3] == ["tmux", "send-keys", "-t"]
    assert command[3] == "mtltest:0.0"
    assert command[4].startswith("bash ")
    script = Path(shlex.split(command[4])[1])
    text = script.read_text(encoding="utf-8")
    assert text.startswith("#!/usr/bin/env bash")
    exec_argv = shlex.split(text[text.index("exec ") + len("exec "):])
    assert task in exec_argv
    assert script.stat().st_mode & 0o100


def test_task_launch_command_exports_the_env_file_path(tmp_path: Path) -> None:
    env_file = tmp_path / "run.env"
    env_file.write_text("ANTHROPIC_MODEL=deepseek-flash\n", encoding="utf-8")
    command = task_launch_command(
        pane_id="mtltest:0.0",
        task="hello",
        log_file=tmp_path / "agent.log",
        status_file=tmp_path / "agent.json",
        env={"SHOULD_NOT_APPEAR": "x"},
        timeout_seconds=5,
        env_file=env_file,
        claude_bin="/bin/echo",
    )
    script = Path(shlex.split(command[4])[1]).read_text(encoding="utf-8")
    assert f"MTAGENTLENS_ENV_FILE={env_file}" in script
    assert "SHOULD_NOT_APPEAR" not in script


def test_task_launch_command_uses_the_staged_wrapper_when_present(
    tmp_path: Path, monkeypatch,
) -> None:
    runtime = tmp_path / "runtime"
    wrapper = runtime / "scripts" / "task_wrapper.sh"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text("#!/bin/bash\n", encoding="utf-8")
    project = tmp_path / "project"
    monkeypatch.setattr(
        tmux_engine.BenchmarkWorkspace,
        "discover",
        classmethod(lambda cls: SimpleNamespace(root=runtime, project_root=project)),
    )
    command = task_launch_command(
        pane_id="mtltest:0.0", task="hello", log_file=tmp_path / "a.log",
        status_file=tmp_path / "a.json", env={}, timeout_seconds=5,
        env_file=None, claude_bin="/bin/echo",
    )
    script = Path(shlex.split(command[4])[1]).read_text(encoding="utf-8")
    assert str(wrapper) in script


def test_task_launch_command_falls_back_to_the_repo_wrapper(
    tmp_path: Path, monkeypatch,
) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    project = Path(__file__).resolve().parents[2]
    monkeypatch.setattr(
        tmux_engine.BenchmarkWorkspace,
        "discover",
        classmethod(lambda cls: SimpleNamespace(root=runtime, project_root=project)),
    )
    command = task_launch_command(
        pane_id="mtltest:0.0", task="hello", log_file=tmp_path / "a.log",
        status_file=tmp_path / "a.json", env={}, timeout_seconds=5,
        env_file=None, claude_bin="/bin/echo",
    )
    script = Path(shlex.split(command[4])[1]).read_text(encoding="utf-8")
    assert f"{project}/env/scripts/task_wrapper.sh" in script
