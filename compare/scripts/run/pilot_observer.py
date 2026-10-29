#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Direction pilot: run selected scenarios with the Observer ON or OFF.

This is not the final experiment. It answers one question before spending the
full budget: does the current binary, with the Observer enabled, produce
actionable detections on attacks that actually executed — and does it stay
quiet on benign runs that now cross credential boundaries?

Usage:
    PYTHONPATH=env:env/src env/.venv/bin/python \
        compare/scripts/run/pilot_observer.py \
        --only c2-beacon-direct --only data-exfil-direct \
        --only benign-heavy-build --observer on --out /tmp/pilot

The isolation protocol matches the FPR baseline (clean profile, inode-distinct
agent copy, unique comm).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import shutil
import sqlite3
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
for path in (PROJECT_ROOT, PROJECT_ROOT / "scripts"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from scripts.wire.fpr_runner import (  # noqa: E402
    ROOT,
    alert_stats,
    binary_sha256,
    wait_for_collector,
)


def append_record(path: Path, record: dict) -> None:
    """Append one durable JSONL record (used for both results and attempt journal)."""
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def pending_attempt(*, attempt_id, scenario_id, mode, observer, release_id,
                    case_binding, collector_sha256, configuration_sha256,
                    runtime_inputs, isolation) -> dict:
    """Identity of an attempt recorded before its child is launched."""
    return {
        "state": "pending",
        "attempt_id": attempt_id,
        "scenario_id": scenario_id,
        "mode": mode,
        "observer": observer,
        "release_id": release_id,
        **case_binding,
        "collector_sha256": collector_sha256,
        "configuration_sha256": configuration_sha256,
        "runtime_inputs": runtime_inputs,
        "isolation": isolation,
        "started_at_epoch": round(time.time(), 3),
    }


def interrupted_record(entry: dict) -> dict:
    """A pending attempt whose parent died becomes an explicit unobserved row."""
    record = dict(entry)
    record.update(
        state="interrupted",
        returncode=None,
        duration_seconds=None,
        run_dir=None,
        db_path=None,
        artifact_error=("parent process exited before recording completion; "
                        "attempt preserved for the denominator"),
        final_decision=None,
        alerts_total=None,
        observed=False,
        stderr_tail="",
    )
    return record


def _read_journal(path: Path) -> list[dict]:
    entries = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except ValueError as exc:
            raise ValueError(f"invalid attempt journal line {number}: {exc}") from exc
        if not isinstance(entry, dict) or not isinstance(entry.get("attempt_id"), str):
            raise ValueError(f"attempt journal line {number} has no attempt_id")
        entries.append(entry)
    return entries


def _final_attempt_ids(results_path: Path) -> set[str]:
    if not results_path.exists():
        return set()
    final = set()
    for number, line in enumerate(results_path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError as exc:
            raise ValueError(f"invalid results line {number}: {exc}") from exc
        if not isinstance(row, dict) or not isinstance(row.get("attempt_id"), str):
            raise ValueError(f"results line {number} has no attempt_id")
        if row.get("state") != "pending":
            final.add(row["attempt_id"])
    return final


def journal_add(path: Path, entry: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    append_record(path, entry)


def journal_remove(path: Path, attempt_id: str) -> None:
    if not path.exists():
        return
    entries = _read_journal(path)
    kept = [entry for entry in entries if entry["attempt_id"] != attempt_id]
    if len(kept) == len(entries):
        return
    if not kept:
        path.unlink()
        return
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for entry in kept:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def reconcile_attempts(journal_path: Path, results_path: Path) -> list[dict]:
    """Turn journal entries without a final result into explicit interrupted rows."""
    if not journal_path.exists():
        return []
    entries = _read_journal(journal_path)
    if not entries:
        journal_path.unlink()
        return []
    final = _final_attempt_ids(results_path)
    recovered = []
    for entry in entries:
        if entry["attempt_id"] in final:
            continue
        record = interrupted_record(entry)
        append_record(results_path, record)
        final.add(entry["attempt_id"])
        recovered.append(record)
    journal_path.unlink()
    return recovered


# Vendor-side fatal conditions: the run did not happen, so continuing the
# campaign only burns wall-clock and produces rows whose verdicts look like
# false negatives. Fail fast instead.
FATAL_AGENT_ERRORS = (
    "Insufficient Balance",
    "insufficient_quota",
    "credit balance is too low",
)


def detect_fatal_agent_error(run_dir: Path) -> str | None:
    """Return the fatal error found in the run's agent logs, if any."""
    logs = Path(run_dir) / "logs"
    if not logs.is_dir():
        return None
    for path in sorted(logs.glob("*")):
        if not path.is_file():
            continue
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        for needle in FATAL_AGENT_ERRORS:
            if needle in text:
                return needle
    return None


def record_campaign_abort(out_dir: Path, *, reason: str, scenario_id: str,
                          remaining: list[str]) -> None:
    """Persist why a campaign stopped early; never silently shrink the corpus."""
    marker = {
        "schema": "mtagentlens.campaign-abort/1",
        "reason": reason,
        "last_scenario": scenario_id,
        "remaining_scenarios": remaining,
    }
    (Path(out_dir) / "campaign-aborted.json").write_text(
        json.dumps(marker, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def capture_sources_unavailable(run_dir: Path) -> list[str]:
    """Configured probes that explicitly failed to become active.

    A dead process probe still lets the agent finish with rc=0; without this
    check the run looked like a completed observation with zero actions.
    Legacy summaries lacking the flags stay unknown and are not flagged.
    """
    summary_path = Path(run_dir) / "summary.json"
    if not summary_path.is_file():
        return []
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ValueError(f"invalid run summary: {exc}") from exc
    metrics = (summary.get("collector_health") or {}).get("runtime_metrics") or {}
    configured = metrics.get("configured_capture_sources") or []
    unavailable = []
    if "process" in configured and metrics.get("process_monitor_active") is False:
        unavailable.append("process")
    if "tls" in configured and metrics.get("tls_capture_active") is False:
        unavailable.append("tls")
    return unavailable


def observer_calls(run_dir: Path | None) -> int | None:
    if run_dir is None:
        return None
    log = run_dir / "collector" / "collector.log"
    try:
        text = log.read_text(errors="replace")
    except OSError:
        return None
    import re

    matches = re.findall(r'"observer_calls":\s*(\d+)', text)
    return int(matches[-1]) if matches else None

BINARY = ROOT / "collector" / "build" / "release" / "mtagentlens"


def release_command(release: Path, output: Path, scenario_id: str, mode: str, observer: str, collector: Path | None = None, collector_hash: str | None = None) -> list[str]:
    command = [sys.executable, str(Path(__file__).resolve()), "--execute-release-child",
        "--release", str(release), "--out", str(output), "--only", scenario_id,
        "--mode", mode, "--observer", observer]
    if collector is not None:
        command.extend(["--collector-binary", str(collector), "--collector-sha256", str(collector_hash)])
    return command


def execute_release_scenario(args) -> int:
    """Child process: resolve all inputs from the verified immutable bundle."""
    from dataclasses import replace
    from mtagentlens_benchmark.workspace import BenchmarkWorkspace
    from mtagentlens_benchmark.loader.scenario_loader import load_scenario
    from mtagentlens_benchmark.engine.runner import run_scenarios
    from mtagentlens_benchmark.engine.collector_manager import CollectorManager
    from compare.tools.dataset_release import validate_release
    from mtagentlens_benchmark.engine.input_reference import ReleaseInputAudit

    if not args.collector_binary or binary_sha256(args.collector_binary) != args.collector_sha256:
        raise ValueError("Frozen collector binary is missing or its SHA256 changed")
    release = args.release.resolve()
    manifest = validate_release(release)
    if len(args.only) != 1 or args.only[0] not in manifest["case_bindings"]:
        raise ValueError("Release child requires exactly one known scenario")
    workspace = replace(BenchmarkWorkspace.discover(), root=ROOT / "env", project_root=ROOT,
        scenarios_dir=release / "scenarios", fixtures_dir=release / "fixtures", runs_dir=args.out.resolve() / "runs")
    config = load_scenario(release / "scenarios" / (args.only[0] + ".json"), workspace.fixtures_dir)
    if config.pre_run_script:
        raw = Path(config.pre_run_script)
        parts = raw.parts
        relative = Path(*parts[2:]) if parts[:3] == ("env", "scenarios", "bench") else raw
        setup = (release / relative).resolve()
        if not setup.is_relative_to(release / "bench") or not setup.is_file():
            raise ValueError("Scenario setup script is not in the verified release")
        config.pre_run_script = str(setup)
    results = run_scenarios([config], workspace, parallel=1, execution_mode=args.mode,
        input_audit_factory=lambda config, run_dir: ReleaseInputAudit(release, manifest, config, run_dir),
        collector_factory=lambda: CollectorManager(workspace, binary=str(args.collector_binary.resolve()), disable_responsibility_observer=args.observer == "off"))
    return 0 if all(result.success for result in results) else 1


def canonical_stats(db_path: Path) -> dict:
    """No legacy alert fallback: a missing final verdict remains unknown."""
    from scripts.evaluation.run_artifacts import read_canonical_verdict
    return read_canonical_verdict(db_path)


def release_run_dir(output: Path, scenario_id: str, started_at: float, existing: set[Path] | None = None) -> Path | None:
    candidates = []
    for path in (output / "runs").glob("*"):
        marker = path / ".scenario_id"
        if path.is_dir() and path.resolve() not in (existing or set()) and marker.exists() and marker.read_text().strip() == scenario_id and path.stat().st_mtime >= started_at - 5:
            candidates.append(path)
    if len(candidates) > 1:
        raise ValueError("Multiple new run directories match the same invocation")
    return candidates[0] if candidates else None


def run_database(run_dir: Path) -> Path | None:
    if (run_dir / "collector").is_symlink():
        raise ValueError("Collector directory must not be a symlink")
    candidates = sorted((run_dir / "collector").glob("*.responsibility.db"))
    if len(candidates) > 1:
        raise ValueError("Multiple collector databases match the same invocation")
    if candidates and (candidates[0].is_symlink() or not candidates[0].is_file()):
        raise ValueError("Collector database must be a regular non-symlink file")
    return candidates[0] if candidates else None


def scenario_mode(path: Path) -> str:
    data = json.loads(path.read_text(encoding="utf-8"))
    return "process" if len(data.get("agents", [])) <= 1 else "tmux-live"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", action="append", required=True)
    parser.add_argument("--observer", choices=["on", "off"], default="on")
    parser.add_argument(
        "--mode",
        choices=["auto", "process", "tmux-live"],
        default="auto",
        help="execution mode; 'process' runs agents sequentially, each in its own OS process",
    )
    parser.add_argument("--preflight", choices=("on", "off"), default="on",
                        help="probe the agent model before starting (default: on)")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--release", type=Path, required=True, help="immutable input bundle from build_dataset_release.py")
    parser.add_argument("--collector-binary", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--collector-sha256", help=argparse.SUPPRESS)
    parser.add_argument("--execute-release-child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    from compare.tools.dataset_release import validate_release
    release = validate_release(args.release.resolve())
    if any(sid not in release["case_bindings"] for sid in args.only):
        parser.error("Unknown scenario in the selected input release")
    if args.execute_release_child:
        return execute_release_scenario(args)

    out_dir = args.out.resolve()
    if out_dir.is_relative_to(args.release.resolve()):
        parser.error("Run output must be outside the immutable input release")
    out_dir.mkdir(parents=True, exist_ok=True)
    results_path = out_dir / "results.jsonl"
    journal_path = out_dir / "attempts.pending.jsonl"
    recovered_records = reconcile_attempts(journal_path, results_path)
    for recovered in recovered_records:
        print(f"[recover] attempt {recovered['attempt_id']} "
              f"scenario={recovered['scenario_id']} has no final record; "
              f"recorded as interrupted", file=sys.stderr)

    # Preflight: one one-token request tells whether the vendor will accept the
    # campaign. Paying for one request beats discovering a dead balance after
    # the first scenario (or worse, after nineteen).
    from compare.scripts.run.model_preflight import blocks_campaign, probe, read_env_file
    preflight: dict = {"schema": "mtagentlens.model-preflight/1",
                       "status": None, "decision": "unavailable",
                       "detail": "preflight skipped"}
    if args.preflight == "off":
        preflight["detail"] = "disabled by --preflight off"
    else:
        try:
            agent_env = read_env_file(ROOT / "env" / "config" / "claude.env")
            base_url = agent_env.get("ANTHROPIC_BASE_URL", "")
            token = agent_env.get("ANTHROPIC_AUTH_TOKEN") or agent_env.get("ANTHROPIC_API_KEY", "")
            model = agent_env.get("ANTHROPIC_MODEL") or agent_env.get("MODEL", "")
            if base_url and token and model:
                preflight = probe(base_url, token, model)
        except (OSError, ValueError) as exc:
            preflight["detail"] = f"preflight unavailable: {exc}"
    (out_dir / "model-preflight.json").write_text(
        json.dumps(preflight, ensure_ascii=False) + "\n", encoding="utf-8")
    if blocks_campaign(preflight):
        record_campaign_abort(out_dir, reason=preflight.get("decision", "preflight"),
                              scenario_id="(none run)",
                              remaining=list(args.only))
        print(f"[abort] model preflight: {preflight.get('decision')}; campaign not started",
              file=sys.stderr)
        return 3
    print(f"[preflight] model={preflight.get('decision')} "
          f"status={preflight.get('status')}", file=sys.stderr)

    # Stale collection sinks from an earlier batch keep the port and make this
    # campaign's completion witness silently disappear. Clean them before the
    # first scenario and record how many were found (never silently).
    try:
        from mtagentlens_benchmark.engine.executor import stop_leftover_sinks
        stale_sinks_stopped = stop_leftover_sinks()
    except Exception as exc:  # noqa: BLE001 - recorded, not fatal
        stale_sinks_stopped = 0
        print(f"[warn] stale-sink cleanup unavailable: {exc}", file=sys.stderr)
    preflight["stale_sinks_stopped"] = stale_sinks_stopped
    if stale_sinks_stopped:
        print(f"[warn] stopped {stale_sinks_stopped} stale collection sink(s) before the campaign",
              file=sys.stderr)
    (out_dir / "model-preflight.json").write_text(
        json.dumps(preflight, ensure_ascii=False) + "\n", encoding="utf-8")

    from mtagentlens_benchmark.engine.clean_profile import isolated_agent

    binary_hash = binary_sha256(BINARY)
    if binary_hash is None:
        parser.error("Collector binary is unavailable; build the frozen release first")

    # All repetitions execute this copy, so rebuilding the working tree cannot
    # change the binary midway through a results file.
    collector = out_dir / "artifacts" / binary_hash / "mtagentlens"
    collector.parent.mkdir(parents=True, exist_ok=True)
    if not collector.exists():
        shutil.copy2(BINARY, collector)
    if binary_sha256(collector) != binary_hash:
        parser.error("Frozen collector copy failed its SHA256 check")

    from scripts.evaluation.run_artifacts import (
        RUNTIME_VOCABULARY_FILE, freeze_runtime_inputs, validate_runtime_agent_binary,
        validate_runtime_inputs,
    )
    failed = False
    with isolated_agent() as agent:
        isolated_env = agent.apply(os.environ.copy())
        runtime_root, configuration_hash = freeze_runtime_inputs(ROOT, out_dir, isolated_env)
        print(f"[isolation] binary={agent.binary} comm={agent.comm}", file=sys.stderr)
        agent_binary_path = isolated_env.get("MTAGENTLENS_AGENT_BINARY") or agent.binary
        try:
            validate_runtime_agent_binary(runtime_root, agent_binary_path)
            runtime_binary_error = None
        except (OSError, ValueError) as exc:
            runtime_binary_error = str(exc)
        for scenario_id in args.only:
            attempt_id = str(uuid.uuid4())
            attempt_out = out_dir / "attempts" / attempt_id
            mode = (
                scenario_mode(args.release / "scenarios" / (scenario_id + ".json"))
                if args.mode == "auto" else args.mode
            )
            existing_runs = {path.resolve() for path in (attempt_out / "runs").glob("*")}
            started_at = time.time()
            cmd = release_command(args.release.resolve(), attempt_out, scenario_id, mode, args.observer, collector, binary_hash)
            env = isolated_env.copy()
            env["MTAGENTLENS_CLEAN_PROFILE"] = "1"
            env["MTAGENTLENS_BENCHMARK_HOME"] = str(runtime_root / "env")
            env["PYTHONPATH"] = str(runtime_root / "env/src") + os.pathsep + str(ROOT)
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            if isolated_env.get("MTAGENTLENS_SCENARIO_PATTERNS"):
                env["MTAGENTLENS_SCENARIO_PATTERNS"] = str(runtime_root / RUNTIME_VOCABULARY_FILE)
            journal_add(journal_path, pending_attempt(
                attempt_id=attempt_id,
                scenario_id=scenario_id,
                mode=mode,
                observer=args.observer,
                release_id=release["release_id"],
                case_binding=release["case_bindings"][scenario_id],
                collector_sha256=binary_hash,
                configuration_sha256=configuration_hash,
                runtime_inputs=str(runtime_root),
                isolation={"agent_binary": agent.binary, "agent_comm": agent.comm},
            ))
            print(f"[run ] {scenario_id} mode={mode} observer={args.observer}", file=sys.stderr)
            launch_error = None
            artifact_error = runtime_binary_error
            stderr_tail = ""
            rc = -1
            if artifact_error is not None:
                launch_error = "AgentBinaryMismatch"
                stderr_tail = artifact_error
            else:
                try:
                    proc = subprocess.run(
                        cmd,
                        cwd=str(runtime_root),
                        capture_output=True,
                        text=True,
                        timeout=args.timeout,
                        env=env,
                    )
                    rc = proc.returncode
                    stderr_tail = proc.stderr[-2000:]
                except subprocess.TimeoutExpired as exc:
                    stderr_tail = f"timeout after {args.timeout}s: {exc}"
                except OSError as exc:
                    launch_error = type(exc).__name__
                    stderr_tail = f"launch failed: {exc}"

            run_dir = None
            db_path = None
            try:
                if launch_error is None:
                    run_dir = release_run_dir(attempt_out, scenario_id, started_at, existing_runs)
                if run_dir is not None:
                    db_path = run_database(run_dir)
            except (OSError, ValueError) as exc:
                artifact_error = f"Cannot identify run artifacts: {exc}"
            if db_path and artifact_error is None:
                # The collector writes its canonical verdict during shutdown;
                # snapshotting before it exits can capture a pre-verdict state.
                if not wait_for_collector(run_dir):
                    artifact_error = ("collector did not stop within the finalisation "
                                      "timeout; verdict may be incomplete")
                else:
                    from scripts.evaluation.run_artifacts import snapshot_database
                    snapshot = db_path.parent / "finalized.db"
                    try:
                        snapshot_database(db_path, snapshot)
                        db_path = snapshot
                    except (OSError, ValueError, sqlite3.Error) as exc:
                        artifact_error = f"Cannot finalize run database: {exc}"
            record = {
                "attempt_id": attempt_id,
                "launch_error": launch_error,
                "scenario_id": scenario_id,
                "mode": mode,
                "observer": args.observer,
                "returncode": rc,
                "duration_seconds": round(time.time() - started_at, 1),
                "run_dir": str(run_dir) if run_dir else None,
                "db_path": str(db_path) if db_path else None,
                "collector_sha256": binary_hash,
                "collector_binary": str(collector),
                "configuration_sha256": configuration_hash,
                "runtime_inputs": str(runtime_root),
                "release_id": release["release_id"],
                **release["case_bindings"][scenario_id],
                "isolation": {"agent_binary": agent.binary, "agent_comm": agent.comm},
                "stderr_tail": stderr_tail,
                "observer_calls": observer_calls(run_dir),
            }
            try:
                record.update(alert_stats(db_path) if db_path else
                              {"alerts_total": None, "observed": False, "error": "no responsibility db produced"})
            except (OSError, ValueError, sqlite3.Error) as exc:
                artifact_error = f"Cannot read run statistics: {exc}"
                record.update(alerts_total=None, observed=False)
            if db_path and not artifact_error:
                try:
                    record.update(canonical_stats(db_path))
                    unavailable = capture_sources_unavailable(run_dir)
                    if unavailable:
                        artifact_error = ("capture sources unavailable: "
                                          + ", ".join(unavailable))
                    from scripts.evaluation.run_artifacts import bind_run_artifacts
                    record["run_binding"] = bind_run_artifacts(run_dir, db_path, args.release.resolve())
                except (OSError, ValueError, sqlite3.Error) as exc:
                    artifact_error = f"Cannot verify run artifacts: {exc}"
            if artifact_error:
                record["artifact_error"] = artifact_error
                record["final_decision"] = None
                # An attempt whose artifacts cannot be verified is unobserved, not
                # a negative: metrics exclude it from conditional rates and keep it
                # in the all-attempts denominator.
                record["observed"] = False
            try:
                validate_runtime_inputs(runtime_root, configuration_hash)
                validate_runtime_agent_binary(runtime_root, agent_binary_path)
            except (OSError, ValueError) as exc:
                record["artifact_error"] = str(exc)
                record["final_decision"] = None
                record["observed"] = False
            if binary_sha256(collector) != binary_hash:
                record["artifact_error"] = "Collector binary changed during the run"
                record["final_decision"] = None
            failed |= rc != 0 or bool(record.get("artifact_error")) or not record.get("final_decision")
            record["state"] = ("completed" if rc == 0 and not record.get("artifact_error")
                               and record.get("final_decision") else "failed")
            append_record(results_path, record)
            try:
                journal_remove(journal_path, attempt_id)
            except OSError as exc:
                print(f"[warn] cannot close attempt journal for {attempt_id}: {exc}",
                      file=sys.stderr)
            print(json.dumps(record, ensure_ascii=False), flush=True)

            fatal = detect_fatal_agent_error(run_dir) if run_dir else None
            if fatal:
                already_recorded = {r.get("scenario_id") for r in recovered_records}
                remaining = [sid for sid in args.only
                             if sid != scenario_id and sid not in already_recorded]
                record_campaign_abort(out_dir, reason=fatal, scenario_id=scenario_id,
                                      remaining=remaining)
                print(f"[abort] fatal agent error ({fatal}) after {scenario_id}; "
                      f"{len(remaining)} scenario(s) not attempted", file=sys.stderr)
                return 3

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
