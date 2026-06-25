"""Tmux pane orchestration engine — preserves run_lab.py logic."""
from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

from mtagentlens_benchmark.types import ScenarioConfig
from mtagentlens_benchmark.workspace import BenchmarkWorkspace


def create_tmux_session(session_name: str, scenario: ScenarioConfig) -> str:
    """Create a tmux session with per-agent panes."""
    _kill_session_if_exists(session_name)

    num_agents = len(scenario.agents)
    layout = scenario.tmux_layout

    cmd = ["tmux", "new-session", "-d", "-s", session_name, "-x", "240", "-y", "80"]
    subprocess.run(cmd, check=True)

    for i in range(1, num_agents):
        split_cmd = _split_command(layout, i, num_agents)
        subprocess.run(
            ["tmux"] + split_cmd + ["-t", f"{session_name}:0"],
            check=True,
        )

    subprocess.run(
        ["tmux", "select-layout", "-t", f"{session_name}:0", _layout_name(layout)],
        check=True,
    )

    return session_name


def launch_agent_in_pane(
    session_name: str,
    pane_index: int,
    agent_task: str,
    role_file: str,
    mode_env: dict[str, str],
    launch_env: dict[str, str],
    log_file: Path,
    status_file: Path,
    pane_mode: str = "cli",
    claude_bin: str = "claude",
    timeout_seconds: int = 600,
    env_file: Path | None = None,
    extra_claude_args: list[str] | None = None,
) -> None:
    """Launch a Claude agent in a specific tmux pane."""
    pane_id = f"{session_name}:0.{pane_index}"

    if pane_mode == "task":
        _launch_task_mode(
            pane_id, agent_task, log_file, status_file, launch_env,
            timeout_seconds=timeout_seconds,
            env_file=env_file,
            extra_claude_args=extra_claude_args,
        )
    elif pane_mode == "cli-auto":
        _launch_cli_auto_mode(pane_id, agent_task, log_file, launch_env, claude_bin)
    else:
        _launch_cli_mode(pane_id, agent_task, launch_env, claude_bin)


def kill_session(session_name: str) -> None:
    _kill_session_if_exists(session_name)


def _kill_session_if_exists(name: str) -> None:
    subprocess.run(["tmux", "kill-session", "-t", name], capture_output=True)


def _split_command(layout: str, index: int, total: int) -> list[str]:
    if layout == "tiled" or layout.startswith("focus"):
        return ["split-window", "-h"] if index % 2 == 1 else ["split-window", "-v"]
    return ["split-window", "-h"]


def _layout_name(layout: str) -> str:
    if layout == "focus-top":
        return "main-horizontal"
    return "tiled" if layout == "tiled" else "even-horizontal"


def _launch_task_mode(
    pane_id: str, task: str, log_file: Path, status_file: Path, env: dict,
    timeout_seconds: int = 600,
    env_file: Path | None = None,
    extra_claude_args: list[str] | None = None,
) -> None:
    """Launch task via task_wrapper.sh (one-shot claude -p with watchdog).

    Env vars are passed via AGENTSIGHT_ENV_FILE (sourced by task_wrapper.sh)
    instead of inline, to avoid tmux send-keys truncation.
    Extra claude args (--model, --effort, etc.) are passed as positional
    args after the timeout, to be forwarded to claude -p by task_wrapper.sh.
    """
    ws = BenchmarkWorkspace.discover()
    wrapper = ws.root / "scripts" / "task_wrapper.sh"

    # Use env file if provided, otherwise inline env (fallback)
    if env_file and env_file.exists():
        env_prefix = f"AGENTSIGHT_ENV_FILE={shlex.quote(str(env_file))}"
    else:
        env_prefix = " ".join(f"{k}={shlex.quote(v)}" for k, v in env.items())

    # Build extra args for claude (passed after timeout to task_wrapper.sh)
    extra = " ".join(shlex.quote(a) for a in (extra_claude_args or [])) if extra_claude_args else ""

    cmd = (
        f"{env_prefix} bash {shlex.quote(str(wrapper))} "
        f"{shlex.quote(task)} {shlex.quote(str(status_file))} "
        f"{shlex.quote(str(log_file))} {shlex.quote(str(timeout_seconds))}"
    )
    if extra:
        cmd += f" {extra}"

    subprocess.run([
        "tmux", "send-keys", "-t", pane_id, cmd, "Enter",
    ], check=True)


def _launch_cli_auto_mode(pane_id: str, task: str, log_file: Path, env: dict, claude_bin: str) -> None:
    env_str = " ".join(f"{k}={shlex.quote(v)}" for k, v in env.items())
    subprocess.run([
        "tmux", "send-keys", "-t", pane_id,
        f"{env_str} {claude_bin} --model haiku 2>&1 | tee {shlex.quote(str(log_file))}",
        "Enter",
    ], check=True)


def _launch_cli_mode(pane_id: str, task: str, env: dict, claude_bin: str) -> None:
    env_str = " ".join(f"{k}={shlex.quote(v)}" for k, v in env.items())
    subprocess.run([
        "tmux", "send-keys", "-t", pane_id,
        f"{env_str} {claude_bin} --model haiku",
        "Enter",
    ], check=True)
