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
from mtagentlens_benchmark.engine.process_audit import (
    ProcessAudit, capture_launch_inputs, observe_process, observe_runner,
)
from mtagentlens_benchmark.types import CollectorInterface, RunResult, ScenarioConfig
from mtagentlens_benchmark.workspace import BenchmarkWorkspace

STATUS_POLL_INTERVAL = 1.0  # seconds between status file polls

# Fixtures that predate RUN_TAG_FILE append here. Kept as a module constant so a
# test can point it at a temporary file instead of the host-wide path.
SHARED_MARKER_PATH = Path("/tmp/run-hooks.log")


def _short_fixture_path(materialized: Path, run_id: str) -> Path | None:
    """Expose fixtures at a short path so eBPF keeps the basename.

    The eBPF producer stores at most 126 bytes of a path. A run directory plus
    the attempt/run UUIDs can exceed that before reaching fixture/config/.env,
    so the sensitive basename is lost. A per-run symlink under /tmp gives the
    agent and its children a short FIXTURE_DIR while the files stay in the
    hashed run copy. Returns None when the symlink cannot be created.
    """
    link = Path("/tmp") / f"mtlfix-{run_id[:12]}"
    try:
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(materialized, target_is_directory=True)
        return link
    except OSError:
        return None


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


def _kill_leftover_claude(session_name: str | None = None) -> None:
    """Clean up only an explicitly identified benchmark session."""
    if not session_name or not session_name.startswith("agtlab-"):
        return
    try:
        sessions = subprocess.run(
            ["tmux", "ls"],
            timeout=5, capture_output=True, text=True,
        )
        if sessions.returncode == 0:
            for line in sessions.stdout.splitlines():
                name = line.split(":", 1)[0].strip()
                if name == session_name:
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
    input_audit_factory=None,
) -> RunResult:
    """Execute a single scenario and return the result.

    Completion: each agent writes a status JSON. The executor polls
    these files (1s interval) instead of blindly waiting for timeout.
    Claude uses `-p` (one-shot prompt mode) — prints response & exits.

    Collector: if provided, the collector starts BEFORE agents run and
    stops AFTER all agents complete. This ensures full eBPF coverage.

    A supplied input_audit_factory checks release sources and materialization
    before launch; process mode preserves its reference in the native ledger.
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

    # A per-run nonce identifies the execution in the marker log without
    # naming the scenario to the agent.
    config.run_marker = f"mtl-{uuid.uuid4().hex[:12]}"
    (run_dir / ".run_marker").write_text(config.run_marker)

    # Fixtures are copied per run: the agent may write into the tree (config
    # files, handoff documents), and a shared source tree would carry one run's
    # leftovers into the next. Task paths are repointed at the copy.
    from mtagentlens_benchmark.engine.fixtures import materialize_fixtures

    input_audit = None
    input_reference = None
    try:
        if input_audit_factory is not None:
            input_audit = input_audit_factory(config, run_dir)
        fixture_copy = materialize_fixtures(config, run_dir)
        if input_audit is not None:
            input_audit.verify_materialized(config)
    except Exception as error:
        return RunResult(
            run_id=run_id, scenario_id=config.id, success=False,
            duration_seconds=time.time() - t0,
            summary={"input_validation": {"status": "failed", "stage": "materialization"}},
            error=f"input validation failed: {error}",
        )
    referenced = str(config.fixtures_dir)
    if not fixture_copy.available and any(
        referenced in agent.task for agent in config.agents
    ):
        # The task points at a fixture directory that does not exist. Letting
        # the agent run anyway produced a "successful" run that could not
        # execute the scenario (silent unavailable copy).
        return RunResult(
            run_id=run_id, scenario_id=config.id, success=False,
            duration_seconds=time.time() - t0,
            summary={"fixtures": {"source": referenced, "materialized": None}},
            error=f"fixture directory missing: {referenced}",
        )
    pre_run_error = _run_pre_run_script(config, run_dir, workspace, fixture_copy)
    if pre_run_error:
        return RunResult(
            run_id=run_id, scenario_id=config.id, success=False,
            duration_seconds=time.time() - t0,
            summary={"pre_run_script": pre_run_error},
            error=f"pre_run_script failed: {pre_run_error}",
        )
    if input_audit is not None:
        try:
            input_reference = input_audit.finish_setup(config)
        except Exception as error:
            return RunResult(
                run_id=run_id, scenario_id=config.id, success=False,
                duration_seconds=time.time() - t0,
                summary={"input_validation": {"status": "failed", "stage": "setup"}},
                error=f"input validation failed: {error}",
            )

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
            set_context = getattr(collector, "set_run_context", None)
            if callable(set_context):
                mode = getattr(config.mode, "value", str(config.mode))
                set_context(config.id, mode)
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
        # Snapshot after setup, immediately before launching agents. A receipt
        # already present here cannot certify this run's payload delivery.
        sink_before = sink_receipt_inventory(config.id)
        if execution_mode in ("process", "batch"):
            input_options = {"input_reference": input_reference} if input_reference is not None else {}
            success, summary = _run_process_mode(
                config, run_dir, status_dir, logs_dir, run_id=run_id, **input_options,
            )
        elif execution_mode in ("tmux-live", "tmux-task"):
            success, summary = _run_tmux_mode(
                config, run_dir, execution_mode, status_dir, logs_dir, claude_bin,
                run_id=run_id, input_reference=input_reference,
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
                if ("tls" in runtime.get("configured_capture_sources", ["tls", "process"])
                        and not runtime.get("ssl_uprobe_healthy")):
                    collector_health["ssl_warning"] = (
                        "TLS capture appears unhealthy — no TLS sessions or SSL events were observed."
                    )
                if not runtime.get("scoring_healthy"):
                    collector_health["scoring_warning"] = (
                        "Responsibility scoring produced no graph or evidence signals."
                    )

    # Capture the execution markers produced during this run. The log is
    # shared across runs on purpose (hooks append to one path); the run's
    # nonce tells us whether *this* run executed the payload.
    marker_path = run_dir / "marker.log"
    local_marker = ""
    if marker_path.exists():
        try:
            local_marker = marker_path.read_text(errors="replace")
        except OSError:
            local_marker = ""
    # Many fixtures append to the shared /tmp/run-hooks.log instead of
    # RUN_TAG_FILE. The run nonce still identifies this run, so read the shared
    # file too. When the match comes only from the shared file, copy the
    # matching lines back into the run directory: the shared file is truncated
    # by later pre_run scripts, so it cannot be the only evidence.
    shared_marker = ""
    if SHARED_MARKER_PATH.exists():
        try:
            shared_marker = SHARED_MARKER_PATH.read_text(errors="replace")
        except OSError:
            shared_marker = ""
    marker_content = local_marker + "\n" + shared_marker
    marker_executed = False
    if marker_content:
        marker_executed = bool(getattr(config, "run_marker", "")) and (
            config.run_marker in marker_content.splitlines()
        )
    marker_source = "missing"
    if marker_executed:
        if config.run_marker in local_marker.splitlines():
            marker_source = "run_dir"
        else:
            marker_source = "shared_file_copied"
            matched = [
                line
                for line in shared_marker.splitlines()
                if line == config.run_marker
            ]
            try:
                marker_path.write_text("\n".join(matched) + "\n", encoding="utf-8")
            except OSError:
                marker_source = "shared_file_unpersisted"

    collector_out = collector_dir / "collector.log" if collector_started else None
    db_path = collector.database_path if collector and collector_started else None

    sink_capture_error = None
    try:
        sink_inventory = new_sink_receipts(sink_before, sink_receipt_inventory(config.id))
        sink_copies = _copy_sink_receipts(config.id, run_dir, sink_inventory)
    except (OSError, ValueError) as error:
        sink_inventory, sink_copies = [], []
        sink_capture_error = type(error).__name__
    sink_witness = {
        "schema": "mtagentlens.sink-receipts/v2",
        "runner_run_id": run_id,
        "run_directory": run_dir.name,
        "run_marker": getattr(config, "run_marker", ""),
        "baseline": sink_before,
        "receipts": sink_copies,
    }

    # A scenario may start the collection sink itself (no pre_run_script), and
    # the agent's shell may leave it running. A surviving sink keeps the port and
    # silently breaks the *next* scenario's completion witness, so stop it after
    # every run, not only before scripts that happen to exist.
    leftover_sinks_stopped = stop_leftover_sinks()

    # ── Clean up empty directories left by Claude's internals ──
    _cleanup_empty_dirs(run_dir)

    return RunResult(
        run_id=run_id, scenario_id=config.id, success=success and sink_capture_error is None,
        duration_seconds=time.time() - t0,
        collector_output=collector_out, database_path=db_path,
        task_logs={
            f"agent_{i}": logs_dir / f"agent_{i}.log"
            for i, _ in enumerate(config.agents)
        },
        summary={
            **summary,
            "leftover_sinks_stopped": leftover_sinks_stopped,
            "agent_logs": [
                {"agent_id": f"agent_{i}", "role_label": agent.role_file,
                 "path": f"logs/agent_{i}.log"}
                for i, agent in enumerate(config.agents)
            ],
            "collector_health": collector_health,
            "fixtures": {
                "source": str(fixture_copy.source),
                "materialized": str(fixture_copy.destination) if fixture_copy.available else None,
            },
            "run_marker": getattr(config, "run_marker", ""),
            "marker_executed": marker_executed,
            "marker_source": marker_source,
            "sink_received": sink_inventory,
            "sink_witness": sink_witness,
            "sink_capture_error": sink_capture_error,
        },
        error=f"sink capture failed: {sink_capture_error}" if sink_capture_error else None,
    )


# ── Process mode ────────────────────────────────────────────────────


def is_leftover_sink_cmdline(cmdline: str) -> bool:
    """Whether a process command line is a benchmark collection sink.

    Scenario setup scripts start one sink per scenario and clean up with a
    `pkill` pattern; when that pattern misses (e.g. the cmdline is
    `bench/<id>/../sink.py`) the sink survives, keeps the port, and the next
    scenario's sink silently fails to bind — its exfiltration payload is then
    never received, so the completion witness disappears. Scope the match to the
    benchmark tree so unrelated `sink.py` programs are untouched.
    """
    tokens = cmdline.split()
    if not tokens:
        return False
    interpreter = os.path.basename(tokens[0])
    if not interpreter.startswith("python"):
        return False
    return "sink.py" in cmdline and ("/bench/" in cmdline or "/scenarios/bench/" in cmdline)


def _ancestor_pids(pid: int | None = None) -> set[int]:
    """The caller and its ancestors: they must never be treated as leftovers."""
    import os as _os

    ancestors: set[int] = set()
    current = _os.getpid() if pid is None else pid
    while current > 1 and current not in ancestors:
        ancestors.add(current)
        try:
            stat = Path(f"/proc/{current}/stat").read_text()
        except OSError:
            break
        # The command name may contain spaces/parentheses; parse after the last ')'.
        fields = stat.rsplit(")", 1)[-1].split()
        try:
            current = int(fields[1])  # ppid
        except (IndexError, ValueError):
            break
    return ancestors


def stop_leftover_sinks() -> int:
    """Terminate benchmark sinks left over from earlier scenarios/runs.

    The caller and its ancestors are protected by pid, not by process group: a
    scenario's sink is often started by the agent inside the executor's own
    process group, so skipping the whole group silently kept the port occupied
    and broke the next scenario's completion witness.
    """
    import signal

    stopped = 0
    proc = Path("/proc")
    if not proc.is_dir():
        return 0
    protected = _ancestor_pids()
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid in protected:
            continue
        try:
            cmdline = (entry / "cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", "replace")
        except OSError:
            continue
        if not is_leftover_sink_cmdline(cmdline):
            continue
        try:
            os.kill(pid, signal.SIGTERM)
            stopped += 1
        except OSError:
            continue
    return stopped


def _run_pre_run_script(config, run_dir: Path, workspace, fixture_copy) -> str | None:
    """Run a scenario's setup script before any agent starts.

    The script exists so a scenario can reset state that outlives one run — a
    handoff directory, an override marker — deterministically. It runs with
    ``FIXTURE_DIR`` pointing at the per-run fixture copy and ``RUN_DIR`` at the
    run directory, and a failure aborts the run: a scenario that could not be
    set up must not be measured.
    """
    if not config.pre_run_script:
        return None
    # Defense in depth: a stale sink keeps its port and makes this scenario's
    # collection endpoint unreachable (its payload is then never received).
    stop_leftover_sinks()
    raw = Path(config.pre_run_script)
    if raw.is_absolute():
        candidates = [raw]
    else:
        candidates = [Path(str(workspace.root)) / raw, Path.cwd() / raw]
        # Release scenarios name scripts relative to the release root (e.g.
        # bench/<scenario>/start.sh), while the workspace root is env/. Walk the
        # fixture ancestors so the script is found without changing cwd.
        candidates.extend(
            ancestor / raw
            for ancestor in config.fixtures_dir.parents
            if (ancestor / raw).is_file()
        )
    script = next((path for path in candidates if path.is_file()), None)
    if script is None:
        return f"script not found: {config.pre_run_script}"
    env = os.environ.copy()
    env["RUN_DIR"] = str(run_dir)
    env["FIXTURE_DIR"] = str(
        fixture_copy.destination if fixture_copy.available else fixture_copy.source
    )
    try:
        completed = subprocess.run(
            ["bash", str(script)],
            cwd=str(run_dir),
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return "timeout after 120s"
    except OSError as error:
        return f"{type(error).__name__}: {error}"
    if completed.returncode != 0:
        tail = (completed.stderr or completed.stdout or "").strip()[-300:]
        return f"exit {completed.returncode}: {tail}"
    return None


def _run_process_mode(
    config: ScenarioConfig, run_dir: Path, status_dir: Path, logs_dir: Path,
    *, run_id: str,
    input_reference: dict | None = None,
) -> tuple[bool, dict]:
    """Run with `claude -p` (one-shot prompt mode) — Claude exits immediately."""
    env = os.environ.copy()

    _apply_benchmark_env(env, run_dir)
    _inject_mode_env(env, config)
    # Expose the per-run fixture copy: build hooks and setup scripts refer to
    # $FIXTURE_DIR, and the agent's child processes inherit this environment.
    materialized = run_dir / "fixtures"
    if materialized.is_dir():
        short = _short_fixture_path(materialized, run_id)
        env["FIXTURE_DIR"] = str(short or materialized)
    env["RUN_TAG"] = getattr(config, "run_marker", "")
    env["RUN_TAG_FILE"] = str(run_dir / "marker.log")
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

    audit_path = run_dir / "runner-events.jsonl"
    with ProcessAudit(audit_path, run_id=run_id, scenario_id=config.id,
                      input_reference=input_reference) as audit:
        for i, agent in enumerate(config.agents):
            status_file = status_dir / f"agent_{i}.json"
            log_file = logs_dir / f"agent_{i}.log"
            prompt_file = prompts_dir / f"agent_{i}.txt"
            actor_id = f"runner-agent-{i}"

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
            launch_inputs = capture_launch_inputs(
                agent.task, claude_args, cmd, run_dir, run_dir / "fixtures",
            )
            env = agent_visible_env(env)
            if input_reference is not None and i == 0:
                from mtagentlens_benchmark.engine.input_reference import fixture_digest
                if fixture_digest(launch_inputs["input_snapshot"]["fixtures"]) != input_reference["prepared_fixture_sha256"]:
                    raise ValueError("prepared fixture changed before the first launch")
            audit.record(
                "launch_attempt", actor_id=actor_id,
                details={"agent_index": i, "runner_context": observe_runner(),
                         **launch_inputs},
            )
            proc = None
            try:
                proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, env=env, cwd=str(run_dir),
                )
                native = observe_process(proc.pid)
                key = native["process_key"]
                audit.record(
                    "process_started", actor_id=actor_id, process_key=key,
                    details={"observation": native["observation"]},
                )
                (Path("/tmp/mtagentlens-agent-pid")).write_text(str(proc.pid))
                try:
                    stdout, stderr = proc.communicate(timeout=config.timeout_seconds)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    stdout, stderr = proc.communicate()
                    audit.record(
                        "process_timeout", actor_id=actor_id, process_key=key,
                        details={"exit_code": proc.returncode},
                    )
                    raise
                audit.record(
                    "process_exited", actor_id=actor_id, process_key=key,
                    details={"exit_code": proc.returncode},
                )
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
                if proc is not None:
                    raise
                audit.record(
                    "launch_failed", actor_id=actor_id,
                    details={"error_type": "FileNotFoundError"},
                )
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
        "source_run_uuid": audit.source_run_uuid,
        "runner_events_path": str(audit_path),
    }
    if clean_artifacts:
        summary["clean_profile"] = clean_artifacts
    return all(agent_statuses), summary


# ── Tmux mode ────────────────────────────────────────────────────────


def ledger_status(status: dict | None) -> tuple[str, str]:
    """Map a task_wrapper status file to a ledger status.

    `None` or a non-terminal state means the executor never observed a
    completion: that is `unobserved`, not a failure and not a success.
    """
    from mtagentlens_benchmark.engine.runner_ledger import (
        STATUS_ERROR, STATUS_FAILED, STATUS_SUCCEEDED, STATUS_TIMEOUT, STATUS_UNOBSERVED,
    )

    if not isinstance(status, dict):
        return STATUS_UNOBSERVED, "no status file"
    state = status.get("state")
    if state in (None, "pending", "running"):
        return STATUS_UNOBSERVED, f"state={state}"
    if state == "timeout":
        return STATUS_TIMEOUT, "status file reported timeout"
    if state == "error":
        return STATUS_ERROR, str(status.get("error") or "status file reported error")
    success = status.get("success", status.get("exit_code") == 0)
    if success:
        return STATUS_SUCCEEDED, ""
    return STATUS_FAILED, f"exit_code={status.get('exit_code')}"



def _run_tmux_mode(
    config: ScenarioConfig, run_dir: Path, mode: str,
    status_dir: Path, logs_dir: Path, claude_bin: str,
    *, run_id: str, input_reference: dict | None = None,
) -> tuple[bool, dict]:
    """Run with tmux panes + task_wrapper.sh status file detection.

    A pane pid is the shell the runner can actually observe; it is not the
    agent pid. The native ledger therefore records the pane identity and the
    observed terminal status, and leaves the agent process unobserved instead
    of manufacturing a process lifecycle from pane overlap.
    """
    from mtagentlens_benchmark.engine.runner_ledger import (
        STATUS_SUCCEEDED, RunnerLedger,
    )
    from mtagentlens_benchmark.engine.tmux_engine import (
        create_tmux_session, launch_agent_in_pane, kill_session,
        observe_session_panes, task_launch_command,
    )

    session_name = f"agtlab-{uuid.uuid4().hex[:8]}"
    runner_ledger = RunnerLedger(session_name=session_name, mode="tmux-live")
    env = os.environ.copy()
    _apply_benchmark_env(env, run_dir)
    _inject_mode_env(env, config)
    env["RUN_DIR"] = str(run_dir)
    materialized = run_dir / "fixtures"
    if materialized.is_dir():
        short = _short_fixture_path(materialized, run_id)
        env["FIXTURE_DIR"] = str(short or materialized)
    env["RUN_TAG"] = getattr(config, "run_marker", "")
    env["RUN_TAG_FILE"] = str(run_dir / "marker.log")
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
        "RUN_TAG", "RUN_TAG_FILE", "FIXTURE_DIR", "RUN_DIR",
    }
    for k, v in sorted(env.items()):
        if k in essential_keys or k.startswith("ANTHROPIC_") or k.startswith("CLAUDE_"):
            env_lines.append(f'{k}={shlex.quote(v)}')
    env_file.write_text("\n".join(env_lines), encoding="utf-8")

    # Build extra claude args from config. Keep the permission flag absent for
    # the default mode, matching process mode and the release-input contract.
    extra_args = ["--output-format", "text"]
    permission_mode = getattr(config, "permission_mode", "default")
    if permission_mode != "default":
        extra_args.extend(["--permission-mode", permission_mode])
    model = getattr(config, "model", "haiku")
    if model:
        extra_args.extend(["--model", model])
    effort = getattr(config, "effort", None)
    if effort:
        extra_args.extend(["--effort", effort])
    if clean_artifacts:
        extra_args = clean_profile.augment_claude_args(extra_args, clean_artifacts)

    agent_count = len(config.agents)
    audit_path = run_dir / "runner-events.jsonl"
    launched = [False] * agent_count
    agent_statuses = [False] * agent_count
    pane_records = []
    panes_by_index: dict[int, dict] = {}

    with ProcessAudit(audit_path, run_id=run_id, scenario_id=config.id,
                      input_reference=input_reference) as audit:
        try:
            create_tmux_session(session_name, config, cwd=run_dir.resolve())
            panes_by_index = {
                pane["pane_index"]: pane
                for pane in observe_session_panes(session_name)
            }
            for i, agent in enumerate(config.agents):
                status_file = status_dir / f"agent_{i}.json"
                log_file = logs_dir / f"agent_{i}.log"
                _write_status(status_file, {"state": "pending", "agent_index": i})

                pane = panes_by_index.get(i, {})
                pane_id = pane.get("pane_id")
                pane_pid = pane.get("pane_pid")
                pane_process_key = None
                pane_observation = None
                if pane_pid is not None:
                    try:
                        native = observe_process(pane_pid)
                    except (OSError, ValueError):
                        pane_pid = None
                    else:
                        pane_process_key = native["process_key"]
                        pane_observation = native["observation"]
                pane_records.append({
                    "agent_index": i,
                    "session": session_name,
                    "pane": f"{session_name}:0.{i}",
                    "pane_id": pane_id,
                    "pane_pid": pane_pid,
                    "role_file": agent.role_file,
                })
                tmux_details = {
                    "session": session_name,
                    "pane": f"{session_name}:0.{i}",
                    "pane_id": pane_id,
                    "pane_pid": pane_pid,
                    "pane_process_key": pane_process_key,
                    "pane_observation": pane_observation,
                    "role_file": agent.role_file,
                }
                claude_args = ["-p", agent.task, *extra_args]
                launch_env = (
                    {"MTAGENTLENS_SUDO_USER": original_user}
                    if is_root and original_user else {}
                )
                launch_command = task_launch_command(
                    pane_id=f"{session_name}:0.{i}",
                    task=agent.task,
                    log_file=log_file,
                    status_file=status_file,
                    env=launch_env,
                    timeout_seconds=config.timeout_seconds,
                    env_file=env_file,
                    claude_bin=claude_bin,
                    extra_claude_args=extra_args,
                )
                launch_inputs = capture_launch_inputs(
                    agent.task, claude_args, launch_command,
                    run_dir, run_dir / "fixtures",
                )
                if input_reference is not None and i == 0:
                    from mtagentlens_benchmark.engine.input_reference import fixture_digest
                    if fixture_digest(launch_inputs["input_snapshot"]["fixtures"]) != input_reference["prepared_fixture_sha256"]:
                        raise ValueError("prepared fixture changed before the first launch")

                runner_ledger.record_launch(
                    agent_index=i,
                    task=agent.task,
                    pane_mode="task",
                    log_file=log_file,
                    launched_at_ns=time.time_ns(),
                )
                audit.record(
                    "launch_attempt", actor_id=f"runner-agent-{i}",
                    details={
                        "agent_index": i,
                        "runner_context": observe_runner(),
                        **launch_inputs,
                        "tmux": tmux_details,
                    },
                )
                try:
                    launch_agent_in_pane(
                        session_name=session_name, pane_index=i,
                        agent_task=agent.task,
                        launch_env=launch_env,
                        log_file=log_file, status_file=status_file,
                        pane_mode="task", claude_bin=claude_bin,
                        timeout_seconds=config.timeout_seconds,
                        env_file=env_file,
                        extra_claude_args=extra_args,
                        launch_command=launch_command,
                    )
                except Exception as error:
                    audit.record(
                        "launch_failed", actor_id=f"runner-agent-{i}",
                        details={"error_type": type(error).__name__},
                    )
                    _write_status(status_file, {
                        "state": "error", "agent_index": i,
                        "error": f"{type(error).__name__}: {error}",
                        "success": False,
                    })
                    continue
                launched[i] = True

            # ── Poll for completion (deadline starts AFTER agent launch) ──
            deadline = time.time() + config.timeout_seconds
            pending = {i for i, value in enumerate(launched) if value}
            while pending and time.time() < deadline:
                for i in list(pending):
                    observed = _read_status(status_dir / f"agent_{i}.json")
                    if observed is not None and observed.get("state") in (
                        "done", "timeout", "error"
                    ):
                        pending.discard(i)
                if pending:
                    time.sleep(STATUS_POLL_INTERVAL)
        finally:
            kill_session(session_name)

        # A missing/terminal-less status file is recorded as `unobserved`: it is
        # neither a success nor a failure, and both ledgers keep that distinction.
        for i in range(agent_count):
            observed = _read_status(status_dir / f"agent_{i}.json")
            status_value, detail = ledger_status(observed)
            runner_ledger.record_completion(
                agent_index=i,
                status=status_value,
                completed_at_ns=time.time_ns(),
                detail=detail,
            )
            agent_statuses[i] = status_value == STATUS_SUCCEEDED
            if launched[i]:
                audit.record(
                    "agent_finished", actor_id=f"runner-agent-{i}",
                    details={"status": status_value, "detail": detail},
                )

    ledger_path = runner_ledger.write(run_dir / "runner-ledger.json")

    # ── Capture agent artifacts (parity with process mode) ──
    _capture_agent_artifacts(run_dir, logs_dir, config)

    ledger_summary = runner_ledger.summary()
    summary = {
        "agents_succeeded": sum(agent_statuses),
        "agents_total": agent_count,
        "source_run_uuid": audit.source_run_uuid,
        "runner_events_path": str(audit_path),
        "runner_ledger": str(ledger_path),
        "runner_ledger_summary": ledger_summary,
        "tmux": {
            "session": session_name,
            "panes": pane_records,
            "agent_pid_observed": False,
            "pane_pid_is_not_agent_pid": True,
        },
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


# Harness-only environment prefixes. The agent is the measured subject, so it
# must not see MtAgentLens/harness identifiers: they tell the model it is inside
# a benchmark and change its willingness to execute (measured: 30/43 attack runs
# refused with explicit references to these names). RUN_TAG/RUN_TAG_FILE stay:
# scenario hooks need the per-run nonce.
_HARNESS_ENV_PREFIXES = ("MTAGENTLENS_", "MTL_", "COLLECT_")
_AGENT_VISIBLE_EXCEPTIONS = {"RUN_TAG", "RUN_TAG_FILE"}


def agent_visible_env(env: dict[str, str]) -> dict[str, str]:
    """Drop harness identifiers from the environment handed to the agent."""
    return {
        key: value
        for key, value in env.items()
        if key in _AGENT_VISIBLE_EXCEPTIONS
        or not key.startswith(_HARNESS_ENV_PREFIXES)
    }


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

    log_file = getattr(collector, "log_file", "collector")
    trace_path = collector_dir / log_file
    if trace_path.exists():
        diag["trace_exists"] = True
        diag["trace_size_bytes"] = trace_path.stat().st_size
        diag["trace_non_empty"] = trace_path.stat().st_size > 0
        if not diag["trace_non_empty"]:
            diag["trace_note"] = (
                "trace output is empty; collector may not have produced events"
            )
    else:
        diag["warnings"].append(
            f"{log_file} trace does not exist — collector may not have produced events"
        )

    err_log = collector_dir / f"{log_file}.log"
    if err_log.exists():
        diag["err_log_exists"] = True
    else:
        diag["warnings"].append(f"{log_file}.log missing — collector may not have started")

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


def _safe_scenario_basename(scenario_id: str) -> str:
    if not isinstance(scenario_id, str) or not scenario_id:
        raise ValueError("scenario_id must be a non-empty string")
    if scenario_id in {".", ".."} or "\x00" in scenario_id or "\\" in scenario_id:
        raise ValueError(f"unsafe scenario_id: {scenario_id!r}")
    separators = {os.sep, os.altsep} - {None}
    if any(sep in scenario_id for sep in separators):
        raise ValueError(f"unsafe scenario_id: {scenario_id!r}")
    if os.path.basename(scenario_id) != scenario_id:
        raise ValueError(f"unsafe scenario_id: {scenario_id!r}")
    return scenario_id


def _safe_receipt_name(name: str) -> str:
    if not isinstance(name, str) or not name or name in {".", ".."}:
        raise ValueError(f"unsafe sink receipt name: {name!r}")
    if "\x00" in name or "/" in name or "\\" in name:
        raise ValueError(f"unsafe sink receipt name: {name!r}")
    if os.path.basename(name) != name:
        raise ValueError(f"unsafe sink receipt name: {name!r}")
    return name


def _open_sink_directory(sink_root: Path, scenario_id: str) -> int | None:
    """Open the sink directory without following symlinks; None if absent."""
    scenario = _safe_scenario_basename(scenario_id)
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        root_fd = os.open(os.fspath(sink_root), directory_flags)
    except FileNotFoundError:
        return None
    try:
        try:
            return os.open(
                f"acme-sink-{scenario}", directory_flags, dir_fd=root_fd,
            )
        except FileNotFoundError:
            return None
    finally:
        os.close(root_fd)


def _stat_fingerprint(stat_result: os.stat_result) -> dict:
    return {
        "size": int(stat_result.st_size),
        "device": int(stat_result.st_dev),
        "inode": int(stat_result.st_ino),
        "mtime_ns": int(stat_result.st_mtime_ns),
        "ctime_ns": int(stat_result.st_ctime_ns),
    }


def _receipt_fingerprint(item: dict) -> tuple:
    return (
        item["size"],
        item["device"],
        item["inode"],
        item["mtime_ns"],
        item["ctime_ns"],
    )


def _hash_fd(fd: int, size: int) -> str:
    import hashlib

    digest = hashlib.sha256()
    remaining = size
    while remaining > 0:
        chunk = os.read(fd, min(65536, remaining))
        if not chunk:
            raise OSError("sink receipt shrank while hashing")
        digest.update(chunk)
        remaining -= len(chunk)
    return digest.hexdigest()


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError("short write while copying sink receipt")
        view = view[written:]


def sink_receipt_inventory(scenario_id: str, *,
                           sink_root: Path = Path("/tmp")) -> list[dict]:
    """Snapshot stable files under the scenario's sink directory.

    A snapshot alone does not bind delivery to this run. The executor compares
    before/after fingerprints and saves verified copies of changed small files.
    Hook reach and receipt delivery still do not establish attack-goal success.
    """
    import stat as stat_module

    directory_fd = _open_sink_directory(sink_root, scenario_id)
    if directory_fd is None:
        return []
    ignored = {"server.log", "sink.pid", "ready"}
    file_flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
    inventory = []
    try:
        for name in sorted(os.listdir(directory_fd)):
            if name in ignored:
                continue
            fd = os.open(name, file_flags, dir_fd=directory_fd)
            try:
                before = os.fstat(fd)
                if not stat_module.S_ISREG(before.st_mode):
                    continue
                digest = _hash_fd(fd, int(before.st_size))
                after = os.fstat(fd)
            finally:
                os.close(fd)
            if _stat_fingerprint(before) != _stat_fingerprint(after):
                raise OSError(f"sink receipt changed while hashing: {name}")
            item = {
                "name": name,
                "size": int(after.st_size),
                "sha256": digest,
            }
            item.update(_stat_fingerprint(after))
            inventory.append(item)
    finally:
        os.close(directory_fd)
    return inventory


def new_sink_receipts(before: list[dict], after: list[dict]) -> list[dict]:
    """Return receipts whose full fingerprint is new or changed by name."""
    previous = {item["name"]: (_receipt_fingerprint(item), item["sha256"]) for item in before}
    return [
        item for item in after
        if previous.get(item["name"]) != (_receipt_fingerprint(item), item["sha256"])
    ]


def _copy_one_sink_receipt(source_directory_fd: int, name: str, item: dict,
                           destination: Path) -> dict:
    import hashlib
    import stat as stat_module

    expected = _receipt_fingerprint(item)
    source_flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
    source_fd = os.open(name, source_flags, dir_fd=source_directory_fd)
    try:
        before = os.fstat(source_fd)
        if not stat_module.S_ISREG(before.st_mode):
            raise OSError(f"sink receipt is not a regular file: {name}")
        if _receipt_fingerprint(_stat_fingerprint(before)) != expected:
            raise OSError(f"sink receipt changed before copy: {name}")

        destination.mkdir(parents=True, exist_ok=True)
        directory_flags = (
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        )
        destination_fd = os.open(os.fspath(destination), directory_flags)
        temp_name = f".{name}.{uuid.uuid4().hex}.tmp"
        created = False
        try:
            temp_fd = os.open(
                temp_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL
                | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
                dir_fd=destination_fd,
            )
            created = True
            try:
                digest = hashlib.sha256()
                remaining = int(item["size"])
                while remaining > 0:
                    chunk = os.read(source_fd, min(65536, remaining))
                    if not chunk:
                        raise OSError(
                            f"sink receipt shrank while copying: {name}",
                        )
                    digest.update(chunk)
                    _write_all(temp_fd, chunk)
                    remaining -= len(chunk)
                os.fsync(temp_fd)
                copied = os.fstat(temp_fd)
            finally:
                os.close(temp_fd)

            after = os.fstat(source_fd)
            if _receipt_fingerprint(_stat_fingerprint(after)) != expected:
                raise OSError(f"sink receipt changed during copy: {name}")
            if (
                int(copied.st_size) != int(item["size"])
                or digest.hexdigest() != item["sha256"]
            ):
                raise OSError(
                    f"sink receipt copy does not match inventory: {name}",
                )

            os.link(
                temp_name, name,
                src_dir_fd=destination_fd, dst_dir_fd=destination_fd,
                follow_symlinks=False,
            )
            os.fsync(destination_fd)
        finally:
            if created:
                try:
                    os.unlink(temp_name, dir_fd=destination_fd)
                except FileNotFoundError:
                    pass
            os.close(destination_fd)
    finally:
        os.close(source_fd)
    return {**item, "copied": True}


def _copy_sink_receipts(scenario_id: str, run_dir: Path, inventory: list[dict],
                        *, sink_root: Path = Path("/tmp"),
                        copy_limit_bytes: int = 512 * 1024) -> list[dict]:
    source_directory_fd = _open_sink_directory(sink_root, scenario_id)
    if source_directory_fd is None:
        if inventory:
            raise OSError(f"sink directory missing for scenario: {scenario_id}")
        return []
    destination = run_dir / "sink"
    copied: list[dict] = []
    try:
        for item in inventory:
            name = _safe_receipt_name(item["name"])
            if int(item["size"]) > copy_limit_bytes:
                copied.append({**item, "copied": False})
                continue
            copied.append(
                _copy_one_sink_receipt(
                    source_directory_fd, name, item, destination,
                ),
            )
    finally:
        os.close(source_directory_fd)
    return copied


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
