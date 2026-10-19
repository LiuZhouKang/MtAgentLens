"""Single-scenario executor — process and tmux modes with collector support.

Phase 1 hardened: collector output verification, health warnings surfaced
in RunResult summary.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import time
import uuid
from pathlib import Path

from mtagentlens_benchmark.engine import clean_profile
from mtagentlens_benchmark.engine.collector_manager import _find_claude_binary
from mtagentlens_benchmark.engine.collector_manager import _find_claude_binary_with_diagnostics
from mtagentlens_benchmark.types import CollectorInterface, RunResult, ScenarioConfig
from mtagentlens_benchmark.workspace import BenchmarkWorkspace

STATUS_POLL_INTERVAL = 1.0  # seconds between status file polls


def _copy_claude_hooks_for_visualizer(settings: dict) -> None:
    """Copy Claude hooks only when explicitly requested for UI recording."""
    if os.environ.get("MTAGENTLENS_ENABLE_CLAUDE_HOOKS") != "1":
        return

    source = Path.home() / ".claude" / "settings.json"
    if not source.exists():
        return

    try:
        source_settings = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return

    hooks = source_settings.get("hooks")
    if isinstance(hooks, dict):
        settings["hooks"] = hooks


def _kill_leftover_claude() -> None:
    """Clean up benchmark-owned tmux sessions and stale Claude processes.

    Ghost Claude processes can persist across runs if the parent shell exits
    before children. Because TLS capture hooks the Claude Code binary, any
    unrelated live Claude session can pollute intent extraction. For benchmark
    recording, start from a clean Claude/tmux surface.
    """
    try:
        sessions = subprocess.run(
            ["tmux", "ls"],
            timeout=5, capture_output=True, text=True,
        )
        if sessions.returncode == 0:
            for line in sessions.stdout.splitlines():
                name = line.split(":", 1)[0].strip()
                if name.startswith("agtlab-"):
                    subprocess.run(
                        ["tmux", "kill-session", "-t", name],
                        timeout=5, capture_output=True,
                    )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    # NOTE: pkill removed — it kills the orchestrating Claude Code session.
    # Only tmux session cleanup (above) is safe for in-session benchmarks.


def execute_scenario(
    config: ScenarioConfig,
    run_dir: Path,
    workspace: BenchmarkWorkspace,
    execution_mode: str = "process",
    collector: CollectorInterface | None = None,
    claude_bin: str = "claude",
) -> RunResult:
    """Execute a single scenario and return the result.

    Completion: each agent writes a status JSON. The executor polls
    these files (1s interval) instead of blindly waiting for timeout.
    Claude uses `-p` (one-shot prompt mode) — prints response & exits.

    Collector: if provided, the collector starts BEFORE agents run and
    stops AFTER all agents complete. This ensures full eBPF coverage.
    """
    # Clean up leftover Claude processes from previous runs
    _kill_leftover_claude()

    run_id = f"{config.id}-{uuid.uuid4().hex[:8]}"
    t0 = time.time()

    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / ".scenario_id").write_text(config.id)
    logs_dir = run_dir / "logs"
    status_dir = run_dir / "status"
    collector_dir = run_dir / "collector"
    for d in [logs_dir, status_dir]:
        d.mkdir(exist_ok=True)

    # ── Start collector (before agents) ──
    ATTACH_WAIT_SECONDS = 3

    collector_started = False
    collector_health: dict = {
        "enabled": collector is not None,
        "status": "not_requested" if collector is None else "pending",
        "stage": None,
    }
    if collector is not None:
        try:
            collector.start(collector_dir)
            collector_started = True
            collector_health.update({
                "status": "started",
                "stage": "start",
                "output_dir": str(collector_dir),
                "pid": getattr(collector, "pid", None),
            })
            time.sleep(ATTACH_WAIT_SECONDS)
            if not collector.is_running:
                collector_health.update({
                    "status": "failed",
                    "stage": "post_start_health_check",
                    "is_running": False,
                })
                return RunResult(
                    run_id=run_id, scenario_id=config.id, success=False,
                    duration_seconds=time.time() - t0,
                    summary={"collector_health": collector_health},
                    error="collector start failed: collector is not running after start",
                )
            collector_health["is_running"] = True
        except Exception as e:
            collector_health.update({
                "status": "failed",
                "stage": "start",
                "error_type": type(e).__name__,
                "error": str(e),
            })
            return RunResult(
                run_id=run_id, scenario_id=config.id, success=False,
                duration_seconds=time.time() - t0,
                summary={"collector_health": collector_health},
                error=f"collector start failed: {e}",
            )

        # ── Collect early health diagnostics ──
        collector_hs = getattr(collector, "health_snapshot", {})
        if collector_hs:
            collector_health["binary_detection"] = collector_hs.get("binary_detection", {})
            collector_health["uprobe_verification"] = collector_hs.get("uprobe_verification", {})
            collector_health["canary_test"] = collector_hs.get("canary_test", {})

            uprobe = collector_hs.get("uprobe_verification", {})
            if uprobe and not uprobe.get("attached") and uprobe.get("errors"):
                collector_health["uprobe_startup_note"] = (
                    "Startup check did not observe an active TLS uprobe yet; "
                    "final status is determined from runtime TLS sessions and responsibility DB metrics. "
                    f"Details: {uprobe['errors']}"
                )

            binary_diag = collector_hs.get("binary_detection", {})
            if binary_diag and not binary_diag.get("final_path"):
                collector_health["binary_warning"] = (
                    f"Claude Code ELF binary not found. "
                    f"Rejected: {binary_diag.get('rejected_reasons', [])}"
                )

    # Resolve actual Claude binary path for both modes.
    # Default "claude" only works in non-sudo contexts.
    binary_path, _ = _find_claude_binary_with_diagnostics()
    if binary_path:
        claude_bin = binary_path

    try:
        if execution_mode in ("process", "batch"):
            success, summary = _run_process_mode(config, run_dir, status_dir, logs_dir)
        elif execution_mode in ("tmux-live", "tmux-task"):
            success, summary = _run_tmux_mode(
                config, run_dir, execution_mode, status_dir, logs_dir, claude_bin,
            )
        else:
            raise ValueError(f"Unknown execution mode: {execution_mode}")
    except Exception as e:
        stop_health = _stop_collector(collector, collector_started)
        collector_health.update(stop_health)
        return RunResult(
            run_id=run_id, scenario_id=config.id, success=False,
            duration_seconds=time.time() - t0,
            summary={"collector_health": collector_health},
            error=str(e),
        )

    # ── Stop collector (after agents) ──
    stop_health = _stop_collector(collector, collector_started)
    collector_health.update(stop_health)
    if stop_health.get("stop_status") == "failed":
        return RunResult(
            run_id=run_id, scenario_id=config.id, success=False,
            duration_seconds=time.time() - t0,
            summary={**summary, "collector_health": collector_health},
            error=f"collector stop failed: {stop_health.get('error', 'unknown error')}",
        )

    # ── Verify collector output integrity ──
    if collector_started:
        collector_health["output_verification"] = _verify_collector_output(
            collector_dir, collector,
        )

        # Update health with runtime metrics from collector
        if hasattr(collector, "health_snapshot"):
            runtime = collector.health_snapshot.get("runtime_metrics", {})
            if runtime:
                collector_health["runtime_metrics"] = runtime
                collector_health["overall_healthy"] = runtime.get("overall_healthy", False)

                if (
                    not runtime.get("has_trace_output")
                    and not runtime.get("responsibility_db_exists")
                ):
                    collector_health["trace_output_warning"] = (
                        "Collector trace output file is EMPTY (0 bytes). "
                        "No events were logged. Check collector stderr for errors."
                    )
                if not runtime.get("pbc_detection_healthy"):
                    collector_health["pbc_warning"] = (
                        "No PolicyBoundaryCrossing edges detected. "
                        "Without PBC edges, no detection is possible. "
                        "Check TLS capture, process events, and responsibility graph construction."
                    )
                if not runtime.get("ssl_uprobe_healthy"):
                    collector_health["ssl_warning"] = (
                        "TLS capture appears unhealthy — no TLS sessions or SSL events were observed."
                    )
                if not runtime.get("scoring_healthy"):
                    collector_health["scoring_warning"] = (
                        "Responsibility scoring produced no graph or evidence signals."
                    )

    collector_out = collector_dir / "collector.log" if collector_started else None
    db_path = collector.database_path if collector and collector_started else None

    # ── Clean up empty directories left by Claude's internals ──
    _cleanup_empty_dirs(run_dir)

    return RunResult(
        run_id=run_id, scenario_id=config.id, success=success,
        duration_seconds=time.time() - t0,
        collector_output=collector_out, database_path=db_path,
        task_logs={
            a.role_file: logs_dir / f"agent_{i}.log"
            for i, a in enumerate(config.agents)
        },
        summary={**summary, "collector_health": collector_health},
    )


# ── Process mode ────────────────────────────────────────────────────


def _run_process_mode(
    config: ScenarioConfig, run_dir: Path, status_dir: Path, logs_dir: Path,
) -> tuple[bool, dict]:
    """Run with `claude -p` (one-shot prompt mode) — Claude exits immediately."""
    env = os.environ.copy()

    _apply_benchmark_env(env, run_dir)
    _inject_mode_env(env, config)
    clean_artifacts = (
        clean_profile.create_artifacts(run_dir) if clean_profile.enabled() else None
    )
    if clean_artifacts:
        env = clean_profile.sanitize_env(env)

    tls_keylog = Path(f"/tmp/mtagentlens-tls-{uuid.uuid4().hex[:8]}.keys")
    tls_keylog_opt = f"--tls-keylog={tls_keylog}"

    original_user = os.environ.get("SUDO_USER", "")
    is_root = os.getuid() == 0
    if is_root and original_user:
        user_home = os.path.expanduser(f"~{original_user}")
        saved_home = os.environ.get("HOME", "")
        os.environ["HOME"] = user_home
        binary_path, _ = _find_claude_binary_with_diagnostics()
        claude_bin = binary_path or os.path.join(user_home, ".nvm/versions/node/*/bin/claude")
        os.environ["HOME"] = saved_home
    else:
        binary_path, _ = _find_claude_binary_with_diagnostics()
        claude_bin = binary_path or "claude"

    if is_root and original_user:
        user_home = os.path.expanduser(f"~{original_user}")
        agent_prefix = ["sudo", "-u", original_user]
        env["HOME"] = user_home
        env["USER"] = original_user
    else:
        agent_prefix = []
        existing = env.get("NODE_OPTIONS", "")
        env["NODE_OPTIONS"] = f"{existing} {tls_keylog_opt}".strip()

    prompts_dir = run_dir / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    agent_statuses = []

    for i, agent in enumerate(config.agents):
        status_file = status_dir / f"agent_{i}.json"
        log_file = logs_dir / f"agent_{i}.log"
        prompt_file = prompts_dir / f"agent_{i}.txt"

        prompt_file.write_text(agent.task, encoding="utf-8")
        _write_status(status_file, {
            "state": "running", "agent_index": i, "scenario": config.id,
        })

        claude_args = [
            "-p", str(agent.task),
            "--model", config.model,
            "--effort", str(getattr(config, "effort", "low")),
            "--output-format", "text",
        ]
        if config.permission_mode != "default":
            claude_args.extend(["--permission-mode", config.permission_mode])
        if clean_artifacts:
            claude_args = clean_profile.augment_claude_args(
                claude_args, clean_artifacts
            )

        if is_root and original_user:
            nvm_bin = os.path.join(user_home, ".nvm/versions/node")
            nvm_node_dirs = []
            if os.path.isdir(nvm_bin):
                for v in sorted(os.listdir(nvm_bin), reverse=True):
                    nvm_node_dirs.append(os.path.join(nvm_bin, v, "bin"))
            npm_bin = os.path.join(user_home, ".npm/_npx")
            extra_path = ":".join(
                nvm_node_dirs[:1] + [npm_bin] if nvm_node_dirs else []
            )
            full_path = (
                f"{extra_path}:{os.environ.get('PATH', '')}" if extra_path
                else os.environ.get("PATH", "")
            )
            inner_cmd = (
                f'export HOME={shlex.quote(user_home)} PATH={shlex.quote(full_path)} '
                f'NODE_OPTIONS={shlex.quote(tls_keylog_opt)}; '
                f'exec {shlex.join([claude_bin] + claude_args)}'
            )
            cmd = agent_prefix + ["bash", "-c", inner_cmd]
        else:
            cmd = agent_prefix + [claude_bin] + claude_args

        agent_start = time.time()
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, env=env, cwd=str(run_dir),
            )
            (Path("/tmp/mtagentlens-agent-pid")).write_text(str(proc.pid))
            try:
                stdout, stderr = proc.communicate(timeout=config.timeout_seconds)
            except subprocess.TimeoutExpired:
                proc.kill()
                stdout, stderr = proc.communicate()
                raise
            elapsed = time.time() - agent_start
            log_file.write_text(
                stdout + "\n\n--- stderr ---\n" + stderr, encoding="utf-8",
            )
            ok = proc.returncode == 0
            _write_status(status_file, {
                "state": "done", "agent_index": i,
                "exit_code": proc.returncode,
                "elapsed_seconds": round(elapsed, 1), "success": ok,
            })
            agent_statuses.append(ok)
        except subprocess.TimeoutExpired:
            log_file.write_text("TIMEOUT", encoding="utf-8")
            _write_status(status_file, {
                "state": "timeout", "agent_index": i,
                "elapsed_seconds": config.timeout_seconds, "success": False,
            })
            agent_statuses.append(False)
        except FileNotFoundError:
            log_file.write_text(f"CLAUDE_NOT_FOUND: {claude_bin}", encoding="utf-8")
            _write_status(status_file, {
                "state": "error", "agent_index": i,
                "error": f"claude binary not found: {claude_bin}", "success": False,
            })
            agent_statuses.append(False)

    if tls_keylog.exists():
        dest = run_dir / "tls.keys"
        shutil.copy2(str(tls_keylog), str(dest))
        try:
            shutil.chown(str(dest), user=original_user)
        except Exception:
            pass
        tls_keylog.unlink()

    _capture_agent_artifacts(run_dir, logs_dir, config)

    summary = {
        "agents_succeeded": sum(agent_statuses),
        "agents_total": len(config.agents),
    }
    if clean_artifacts:
        summary["clean_profile"] = clean_artifacts
    return all(agent_statuses), summary


# ── Tmux mode ────────────────────────────────────────────────────────


def _run_tmux_mode(
    config: ScenarioConfig, run_dir: Path, mode: str,
    status_dir: Path, logs_dir: Path, claude_bin: str,
) -> tuple[bool, dict]:
    """Run with tmux panes + task_wrapper.sh status file detection."""
    from mtagentlens_benchmark.engine.tmux_engine import (
        create_tmux_session, launch_agent_in_pane, kill_session,
    )

    session_name = f"agtlab-{uuid.uuid4().hex[:8]}"
    env = os.environ.copy()
    _apply_benchmark_env(env, run_dir)
    _inject_mode_env(env, config)
    clean_artifacts = (
        clean_profile.create_artifacts(run_dir) if clean_profile.enabled() else None
    )
    if clean_artifacts:
        env = clean_profile.sanitize_env(env)

    # ── Root → user de-escalation (same as process mode) ──
    # Claude Code refuses --permission-mode bypassPermissions as root.
    original_user = os.environ.get("SUDO_USER", "")
    is_root = os.getuid() == 0
    if is_root and original_user:
        user_home = os.path.expanduser(f"~{original_user}")
        env["HOME"] = user_home
        env["USER"] = original_user
        base_path = os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")
        # Ensure /home/<user>/.local/bin is in PATH so tmux can find claude
        local_bin = os.path.join(user_home, ".local", "bin")
        paths = [local_bin] + base_path.split(":")
        env["PATH"] = ":".join(paths)
        env["MTAGENTLENS_SUDO_USER"] = original_user

    # ── Write essential env vars to a temp file ──
    # Avoids tmux send-keys truncation when env_str has 100+ variables.
    env_file = run_dir / ".agent_env"
    env_lines = []
    essential_keys = {
        "ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_MODEL",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL",
        "ANTHROPIC_DEFAULT_OPUS_MODEL", "ANTHROPIC_REASONING_MODEL",
        "CLAUDE_CONFIG_DIR", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC",
        "CLAUDE_CODE_SIMPLE",
        "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS", "CLAUDE_SUBAGENTS_MODE",
        "HOME", "USER", "PATH", "API_TIMEOUT_MS", "LANGUAGE",
        "ENABLE_TOOL_SEARCH", "MODEL", "MTAGENTLENS_SUDO_USER",
    }
    for k, v in sorted(env.items()):
        if k in essential_keys or k.startswith("ANTHROPIC_") or k.startswith("CLAUDE_"):
            env_lines.append(f'{k}={shlex.quote(v)}')
    env_file.write_text("\n".join(env_lines), encoding="utf-8")

    # De-escalate from root → original user for Claude Code in tmux.
    # Claude refuses --dangerously-skip-permissions when running as root.
    agent_prefix = ""
    if is_root and original_user:
        agent_prefix = f"sudo -u {original_user} "

    create_tmux_session(session_name, config)

    # Build extra claude args from config
    extra_args = [
        "--output-format", "text",
        "--permission-mode", getattr(config, "permission_mode", "bypassPermissions"),
    ]
    model = getattr(config, "model", "haiku")
    if model:
        extra_args.extend(["--model", model])
    effort = getattr(config, "effort", None)
    if effort:
        extra_args.extend(["--effort", effort])
    if clean_artifacts:
        extra_args = clean_profile.augment_claude_args(extra_args, clean_artifacts)

    for i, agent in enumerate(config.agents):
        status_file = status_dir / f"agent_{i}.json"
        log_file = logs_dir / f"agent_{i}.log"
        _write_status(status_file, {"state": "pending", "agent_index": i})
        launch_agent_in_pane(
            session_name=session_name, pane_index=i,
            agent_task=agent.task, role_file=agent.role_file,
            mode_env={}, launch_env={"MTAGENTLENS_SUDO_USER": original_user} if is_root and original_user else {},
            log_file=log_file, status_file=status_file,
            pane_mode="task", claude_bin=claude_bin,
            timeout_seconds=config.timeout_seconds,
            env_file=env_file,
            extra_claude_args=extra_args,
        )

    # ── Poll for completion (deadline starts AFTER agent launch) ──
    agent_count = len(config.agents)
    deadline = time.time() + config.timeout_seconds
    agent_statuses = [False] * agent_count

    while time.time() < deadline:
        all_done = True
        for i in range(agent_count):
            status = _read_status(status_dir / f"agent_{i}.json")
            if status is None or status.get("state") in ("pending", "running"):
                all_done = False
            elif status.get("state") in ("done", "timeout", "error"):
                # task_wrapper.sh writes exit_code, not success
                agent_statuses[i] = status.get("success", status.get("exit_code") == 0)
        if all_done:
            break
        time.sleep(STATUS_POLL_INTERVAL)

    kill_session(session_name)

    # ── Capture agent artifacts (parity with process mode) ──
    _capture_agent_artifacts(run_dir, logs_dir, config)

    summary = {
        "agents_succeeded": sum(agent_statuses),
        "agents_total": agent_count,
    }
    if clean_artifacts:
        summary["clean_profile"] = clean_artifacts
    return all(agent_statuses), summary


# ── Helpers ──────────────────────────────────────────────────────────


def _apply_benchmark_env(env: dict, run_dir: Path) -> None:
    """Apply the benchmark's isolated AWS environment."""
    workspace = BenchmarkWorkspace.discover()
    config_env = workspace.config_dir / "claude.env"
    if not config_env.exists():
        return

    with open(config_env) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                env[key.strip()] = value.strip()

    isolated_config = run_dir / ".claude_benchmark"
    isolated_config.mkdir(parents=True, exist_ok=True)
    settings = {
        "enabledPlugins": {},
        "extraKnownMarketplaces": {},
        "includeCoAuthoredBy": False,
        "skipDangerousModePermissionPrompt": True,
    }
    _copy_claude_hooks_for_visualizer(settings)
    (isolated_config / "settings.json").write_text(
        json.dumps(settings, indent=2), encoding="utf-8",
    )
    env["CLAUDE_CONFIG_DIR"] = str(isolated_config)
    env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"


def _inject_mode_env(env: dict, config: ScenarioConfig) -> None:
    mode_env = {
        "single_agent": {
            "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "0",
            "CLAUDE_SUBAGENTS_MODE": "0",
        },
        "agent_teams": {
            "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1",
            "CLAUDE_SUBAGENTS_MODE": "0",
        },
        "subagents": {
            "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "0",
            "CLAUDE_SUBAGENTS_MODE": "1",
        },
    }
    for k, v in mode_env.get(config.mode.value, {}).items():
        env[k] = v


def _stop_collector(
    collector: CollectorInterface | None, was_started: bool,
) -> dict:
    if collector is None:
        return {"stop_status": "not_requested"}
    if not was_started:
        return {"stop_status": "not_started"}
    try:
        collector.stop()
    except Exception as e:
        return {
            "status": "failed",
            "stage": "stop",
            "stop_status": "failed",
            "error_type": type(e).__name__,
            "error": str(e),
        }
    return {
        "status": "completed",
        "stage": "stop",
        "stop_status": "stopped",
        "is_running_after_stop": collector.is_running,
    }


def _verify_collector_output(
    collector_dir: Path,
    collector: CollectorInterface,
) -> dict:
    """Verify collector output integrity after stop."""
    diag: dict = {
        "trace_exists": False,
        "trace_size_bytes": 0,
        "trace_non_empty": False,
        "err_log_exists": False,
        "responsibility_db_exists": False,
        "responsibility_db_tables": 0,
        "pcap_exists": False,
        "pcap_size_bytes": 0,
        "warnings": [],
    }

    trace_path = collector_dir / "collector.log"
    if trace_path.exists():
        diag["trace_exists"] = True
        diag["trace_size_bytes"] = trace_path.stat().st_size
        diag["trace_non_empty"] = trace_path.stat().st_size > 0
        if not diag["trace_non_empty"]:
            diag["trace_note"] = (
                "collector log is empty; collector may not have produced output"
            )
    else:
        diag["warnings"].append(
            "collector.log does not exist — collector may have crashed before writing output"
        )

    err_log = collector_dir / "collector.log"
    if err_log.exists():
        diag["err_log_exists"] = True
    else:
        diag["warnings"].append("collector.log missing — collector may not have started")

    db_path = getattr(collector, "database_path", None)
    if db_path and db_path.exists():
        diag["responsibility_db_exists"] = True
        try:
            con = sqlite3.connect(str(db_path))
            tables = con.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
            ).fetchone()[0]
            con.close()
            diag["responsibility_db_tables"] = tables
            if tables == 0:
                diag["warnings"].append("responsibility.db has zero tables")
        except Exception:
            diag["warnings"].append("responsibility.db exists but could not be queried")
    else:
        diag["warnings"].append(
            "responsibility.db does not exist — no persistence output from collector"
        )

    pcap_path = collector_dir / "trace.pcap"
    if pcap_path.exists():
        diag["pcap_exists"] = True
        diag["pcap_size_bytes"] = pcap_path.stat().st_size
        if pcap_path.stat().st_size == 0:
            diag["warnings"].append("trace.pcap is empty — tcpdump captured no packets")

    diag["all_healthy"] = (
        diag["responsibility_db_exists"]
        and diag.get("responsibility_db_tables", 0) > 0
        and not diag["warnings"]
    )

    return diag


def _capture_agent_artifacts(run_dir: Path, logs_dir: Path, config) -> None:
    """Extract Claude's behavior evidence: files written, tools called, outcomes."""
    import re

    artifacts_dir = run_dir / "artifacts"
    artifacts_dir.mkdir(exist_ok=True)

    behavior: dict = {
        "scenario_id": config.id,
        "agent_count": len(config.agents),
        "agents": [],
    }

    for i in range(len(config.agents)):
        log_file = logs_dir / f"agent_{i}.log"
        agent_info: dict = {"agent_index": i, "files_created": [], "tools_called": []}

        if log_file.exists():
            stdout = log_file.read_text(encoding="utf-8")

            path_pattern = re.compile(
                r'(?:written|wrote|created|saved|写入|创建|生成)\s+'
                r'(?:to|file|文件|到)?\s*`?([/\w.-]+(?:/\w.-]+)*\.\w+)`?',
                re.IGNORECASE,
            )
            for m in path_pattern.finditer(stdout):
                fpath = Path(m.group(1))
                agent_info["files_created"].append(str(fpath))
                if fpath.exists() and fpath.is_file():
                    try:
                        dest = artifacts_dir / fpath.name
                        shutil.copy2(str(fpath), str(dest))
                    except (OSError, PermissionError):
                        pass

            tmp_pattern = re.compile(r'/tmp/[\w./-]+')
            for m in tmp_pattern.finditer(stdout):
                fpath = Path(m.group(0))
                if (
                    fpath.exists() and fpath.is_file()
                    and str(fpath) not in agent_info["files_created"]
                ):
                    agent_info["files_created"].append(str(fpath))
                    try:
                        dest = artifacts_dir / fpath.name
                        shutil.copy2(str(fpath), str(dest))
                    except (OSError, PermissionError):
                        pass

            tool_pattern = re.compile(r'"tool"(?:\s*:\s*|":)\s*"(\w+)"')
            for m in tool_pattern.finditer(stdout):
                agent_info["tools_called"].append(m.group(1))

            agent_info["file_count"] = len(agent_info["files_created"])
            agent_info["tool_count"] = len(agent_info["tools_called"])

        behavior["agents"].append(agent_info)

    (run_dir / "behavior.json").write_text(
        json.dumps(behavior, indent=2, ensure_ascii=False), encoding="utf-8",
    )


def _cleanup_empty_dirs(run_dir: Path) -> None:
    """Remove empty directories left by Claude's internal machinery.

    Claude Code creates session-env, sessions, shell-snapshots, and tasks
    directories under .claude_benchmark/. Many of these are empty after the
    run completes. We remove only truly empty directories — any directory
    containing files is left intact.
    """
    claude_dir = run_dir / ".claude_benchmark"
    if not claude_dir.is_dir():
        return
    # Walk bottom-up so we can remove nested empty dirs
    for root, dirs, files in os.walk(str(claude_dir), topdown=False):
        for d in dirs:
            dpath = Path(root) / d
            try:
                if not any(dpath.iterdir()):
                    dpath.rmdir()
            except OSError:
                pass  # permission error or not empty — skip
    # Remove .claude_benchmark itself if entirely empty
    try:
        if not any(claude_dir.iterdir()):
            claude_dir.rmdir()
    except OSError:
        pass


def _write_status(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.rename(path)


def _read_status(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
