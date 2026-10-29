"""Tmux pane orchestration engine — preserves run_lab.py logic."""
from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

from mtagentlens_benchmark.types import ScenarioConfig
from mtagentlens_benchmark.workspace import BenchmarkWorkspace


def create_tmux_session(
    session_name: str, scenario: ScenarioConfig, *, cwd: Path | None = None,
) -> str:
    """Create a tmux session with per-agent panes.

    ``cwd`` is passed to both new-session and every split-window so the pane
    shell starts where the launch audit says it starts.
    """
    _kill_session_if_exists(session_name)

    num_agents = len(scenario.agents)
    layout = scenario.tmux_layout

    cmd = ["tmux", "new-session", "-d", "-s", session_name, "-x", "240", "-y", "80"]
    if cwd is not None:
        cmd.extend(["-c", str(cwd)])
    subprocess.run(cmd, check=True)

    for i in range(1, num_agents):
        split_argv = ["tmux"] + _split_command(layout, i, num_agents) + [
            "-t", f"{session_name}:0",
        ]
        if cwd is not None:
            split_argv.extend(["-c", str(cwd)])
        subprocess.run(split_argv, check=True)

    subprocess.run(
        ["tmux", "select-layout", "-t", f"{session_name}:0", _layout_name(layout)],
        check=True,
    )

    return session_name


def launch_agent_in_pane(
    session_name: str,
    pane_index: int,
    agent_task: str,
    launch_env: dict[str, str],
    log_file: Path,
    status_file: Path,
    pane_mode: str = "cli",
    claude_bin: str = "claude",
    timeout_seconds: int = 600,
    env_file: Path | None = None,
    extra_claude_args: list[str] | None = None,
    launch_command: list[str] | None = None,
) -> None:
    """Launch a Claude agent in a specific tmux pane."""
    pane_id = f"{session_name}:0.{pane_index}"

    if pane_mode == "task":
        _launch_task_mode(
            pane_id, agent_task, log_file, status_file, launch_env,
            timeout_seconds=timeout_seconds,
            env_file=env_file,
            claude_bin=claude_bin,
            extra_claude_args=extra_claude_args,
            launch_command=launch_command,
        )
    elif pane_mode == "cli-auto":
        _launch_cli_auto_mode(pane_id, agent_task, log_file, launch_env, claude_bin)
    else:
        _launch_cli_mode(pane_id, agent_task, launch_env, claude_bin)


def kill_session(session_name: str) -> None:
    _kill_session_if_exists(session_name)


def observe_session_panes(session_name: str) -> list[dict]:
    """Observe real pane ids/pids; a pane pid is the shell, not the agent."""
    try:
        result = subprocess.run(
            ["tmux", "list-panes", "-t", f"{session_name}:0",
             "-F", "#{pane_index}\t#{pane_id}\t#{pane_pid}"],
            capture_output=True, text=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []
    if result.returncode != 0:
        return []
    panes = []
    for line in result.stdout.splitlines():
        fields = line.split("\t")
        if len(fields) != 3:
            continue
        try:
            pane_index, pane_pid = int(fields[0]), int(fields[2])
        except ValueError:
            continue
        if pane_index < 0 or pane_pid <= 0 or not fields[1]:
            continue
        panes.append({
            "pane_index": pane_index,
            "pane_id": fields[1],
            "pane_pid": pane_pid,
        })
    return sorted(panes, key=lambda pane: pane["pane_index"])


def _kill_session_if_exists(name: str) -> None:
    try:
        subprocess.run(["tmux", "kill-session", "-t", name], capture_output=True)
    except FileNotFoundError:
        pass


def _split_command(layout: str, index: int, total: int) -> list[str]:
    if layout == "tiled" or layout.startswith("focus"):
        return ["split-window", "-h"] if index % 2 == 1 else ["split-window", "-v"]
    return ["split-window", "-h"]


def _layout_name(layout: str) -> str:
    if layout == "focus-top":
        return "main-horizontal"
    return "tiled" if layout == "tiled" else "even-horizontal"


def _task_wrapper_path(ws) -> Path:
    """Resolve task_wrapper.sh in the staged env, falling back to the repo.

    The campaign runtime copy intentionally contains only config/src; the tmux
    launcher used to point at <runtime>/env/scripts/task_wrapper.sh, which does
    not exist there, so every pane stayed pending with no log. Walk both this
    module and the cwd upwards to find the repository copy.
    """
    candidates = [
        ws.root / "scripts" / "task_wrapper.sh",
        ws.project_root / "env" / "scripts" / "task_wrapper.sh",
    ]
    for start in (Path(__file__).resolve().parent, Path.cwd()):
        for parent in (start, *start.parents):
            candidates.append(parent / "env" / "scripts" / "task_wrapper.sh")
            candidates.append(parent / "scripts" / "task_wrapper.sh")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        "task_wrapper.sh not found in the staged env or the repository"
    )


def task_launch_command(
    *,
    pane_id: str,
    task: str,
    log_file: Path,
    status_file: Path,
    env: dict[str, str],
    timeout_seconds: int,
    env_file: Path | None,
    claude_bin: str,
    extra_claude_args: list[str] | None = None,
) -> list[str]:
    """Exact tmux send-keys argv shared by execution and the launch audit.

    Audited runs supply an env file, so their command carries its path rather
    than token values. The legacy no-file fallback embeds the provided env.
    The task_wrapper arguments are the ones actually invoked.
    """
    ws = BenchmarkWorkspace.discover()
    wrapper = _task_wrapper_path(ws)

    # tmux send-keys treats a newline inside its argument as Enter. Multi-line
    # task prompts therefore split the launch command and left every pane at an
    # unterminated shell quote (agents stayed pending). Materialize the exact
    # argv into a script and send only "bash <script>".
    argv = [
        "bash", str(wrapper), task, str(status_file), str(log_file),
        str(timeout_seconds), claude_bin, *(extra_claude_args or []),
    ]
    script_dir = Path(log_file).parent
    script_dir.mkdir(parents=True, exist_ok=True)
    safe_pane = (
        pane_id.replace(":", "_").replace(".", "_").replace("%", "p")
    )
    script = script_dir / f"launch-{safe_pane}.sh"
    lines = ["#!/usr/bin/env bash", "set -euo pipefail"]
    if env_file and env_file.exists():
        lines.append(f"export MTAGENTLENS_ENV_FILE={shlex.quote(str(env_file))}")
    else:
        for key, value in env.items():
            lines.append(f"export {key}={shlex.quote(value)}")
    lines.append("exec " + shlex.join(argv))
    script.write_text("\n".join(lines) + "\n", encoding="utf-8")
    script.chmod(0o700)

    return ["tmux", "send-keys", "-t", pane_id, f"bash {shlex.quote(str(script))}", "Enter"]


def _launch_task_mode(
    pane_id: str, task: str, log_file: Path, status_file: Path, env: dict,
    timeout_seconds: int = 600,
    env_file: Path | None = None,
    claude_bin: str = "claude",
    extra_claude_args: list[str] | None = None,
    launch_command: list[str] | None = None,
) -> None:
    """Launch task via task_wrapper.sh (one-shot claude -p with watchdog).

    A caller that needs the exact argv for its immutable launch audit passes
    ``launch_command`` from ``task_launch_command``; otherwise this builds the
    same argv through that single helper.
    """
    if launch_command is None:
        launch_command = task_launch_command(
            pane_id=pane_id, task=task, log_file=log_file,
            status_file=status_file, env=env,
            timeout_seconds=timeout_seconds, env_file=env_file,
            claude_bin=claude_bin, extra_claude_args=extra_claude_args,
        )
    subprocess.run(launch_command, check=True)


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
