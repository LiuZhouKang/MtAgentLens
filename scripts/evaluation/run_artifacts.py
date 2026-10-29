# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Preserve and verify the SQLite snapshot underlying a run's verdict."""
from __future__ import annotations

import hashlib
from contextlib import closing
import sqlite3
from pathlib import Path

RUNTIME_VOCABULARY_FILE = "collector/patterns/runtime-scenario-patterns.toml"


def _connect(path: Path) -> sqlite3.Connection:
    # as_uri escapes URI metacharacters in user-selected filesystem paths.
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot_database(source: Path, destination: Path) -> None:
    """SQLite backup includes committed WAL pages; copying the main file does not."""
    import os
    import tempfile

    if destination.exists():
        raise ValueError("Finalized database destination already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix=".snapshot-", dir=destination.parent, delete=False) as handle:
        temporary = Path(handle.name)
    try:
        with closing(_connect(source)) as src, closing(sqlite3.connect(temporary)) as dst:
            src.backup(dst)
            dst.commit()
            dst.execute("PRAGMA journal_mode=DELETE")
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        os.link(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def read_canonical_verdict(path: Path, *, expected_implementation: str | None = None) -> dict:
    """Exactly one run in a self-contained, content-addressable snapshot."""
    wal = path.with_name(path.name + "-wal")
    if wal.exists() and wal.stat().st_size:
        raise ValueError("Database has an unbound WAL; create a finalized SQLite snapshot first")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with closing(_connect(path)) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
        if not {"run_id", "final_decision", "final_severity"} <= columns:
            raise ValueError("Database has no canonical run verdict; recollect the run")
        rows = list(conn.execute("SELECT run_id, final_decision, final_severity FROM runs"))
    if len(rows) != 1:
        raise ValueError("Expected exactly one run in the finalized database")
    run_id, decision, severity = rows[0]
    if decision not in (None, "Safe", "Unsafe", "Uncertain"):
        raise ValueError("Unknown canonical verdict")
    return {**run_observation_state(path, expected_implementation=expected_implementation),
        "run_id": run_id, "final_decision": decision, "final_severity": severity,
        "decision_source": "runs.final_decision" if decision else None, "db_sha256": digest}


def freeze_runtime_inputs(project: Path, output: Path, environ: dict[str, str]) -> tuple[Path, str]:
    """Private executable inputs; credentials stay outside public dataset releases.

    Only the hash is written into run records. Runtime files retain restricted
    permissions because observer/agent configuration can contain credentials.
    """
    import json
    import shutil
    import tempfile
    from compare.tools.dataset_release import _inventory

    vocabulary = None
    if environ.get("MTAGENTLENS_SCENARIO_PATTERNS"):
        vocabulary = Path(environ["MTAGENTLENS_SCENARIO_PATTERNS"])
        if not vocabulary.is_absolute():
            vocabulary = project / vocabulary
        if vocabulary.is_symlink() or not vocabulary.is_file():
            raise ValueError("Explicit scenario vocabulary must be an existing regular file")

    agent_binary = None
    agent_binary_sha256 = None
    if environ.get("MTAGENTLENS_AGENT_BINARY"):
        agent_binary = Path(environ["MTAGENTLENS_AGENT_BINARY"])
        if not agent_binary.is_absolute():
            agent_binary = project / agent_binary
        if agent_binary.is_symlink() or not agent_binary.is_file():
            raise ValueError("Explicit agent binary must be an existing regular file")
        try:
            agent_binary_sha256 = _sha256_file(agent_binary)
        except OSError as exc:
            raise ValueError("Explicit agent binary is not readable") from exc

    output.mkdir(parents=True, exist_ok=True)
    runtime = Path(tempfile.mkdtemp(prefix=".runtime-", dir=output))
    runtime.chmod(0o700)
    if vocabulary is not None:
        target = runtime / RUNTIME_VOCABULARY_FILE
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(vocabulary, target)
        target.chmod(0o600)
    files = ("env/config/observer.toml", "observer_llm.toml", "env/config/claude.env")
    for relative in files:
        source = project / relative
        if source.is_file():
            target = runtime / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            target.chmod(0o600)
    # The benchmark engine is an executable research input, just as the
    # collector binary and the fixture hooks are. The product has no model or
    # server input, so no model tree is frozen into the runtime.
    sources = [(project / "env/src/mtagentlens_benchmark", runtime / "env/src/mtagentlens_benchmark")]
    for source, destination in sources:
        if source.is_dir():
            if any(path.is_symlink() for path in source.rglob("*")):
                raise ValueError(f"Runtime input tree contains symlinks: {source}")
            shutil.copytree(source, destination, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    settings = {key: value for key, value in environ.items() if key.startswith(("MTAGENTLENS_DISABLE", "MTAGENTLENS_PROVENANCE", "MTAGENTLENS_OPENCLAW"))
        or key in ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_MODEL", "MODEL",
                   "MTAGENTLENS_OBSERVER_MODEL", "MTAGENTLENS_PRESERVE_OBSERVER_ENV",
                   "MTAGENTLENS_AGENT_COMMS", "MTAGENTLENS_AGENT_COMM",
                   "MTAGENTLENS_AGENT_BINARY", "MTAGENTLENS_AGENT_CGROUP",
                   "MTAGENTLENS_AGENT_EXE", "MTAGENTLENS_SCENARIO_PATTERNS")}
    manifest = {"schema": "mtagentlens.runtime-inputs/1", "files": _inventory(runtime),
        "settings_sha256": {key: hashlib.sha256(value.encode()).hexdigest() for key, value in settings.items()}}
    if agent_binary_sha256 is not None:
        manifest["agent_binary_sha256"] = agent_binary_sha256
    digest = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    (runtime / "runtime.json").write_text(json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8")
    return runtime, digest


def validate_runtime_inputs(root: Path, expected: str) -> None:
    import json
    from compare.tools.dataset_release import _inventory
    manifest = json.loads((root / "runtime.json").read_text(encoding="utf-8"))
    if manifest.get("schema") != "mtagentlens.runtime-inputs/1":
        raise ValueError("Unknown runtime input manifest")
    actual = _inventory(root)
    actual.pop("runtime.json", None)
    if actual != manifest.get("files") or hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest() != expected:
        raise ValueError("Runtime configuration/runner input hash mismatch")


def validate_runtime_agent_binary(root: Path, binary_path: str | Path | None) -> str | None:
    """Prove the current agent executable still has the frozen recorded bytes.

    A manifest without a declared agent binary carries no proof, so callers get
    ``None`` instead of a fabricated digest. When a digest is recorded, the
    current source must exist as a regular non-symlink file with those bytes.
    """
    import json
    manifest = json.loads((root / "runtime.json").read_text(encoding="utf-8"))
    recorded = manifest.get("agent_binary_sha256")
    if not recorded:
        return None
    if not binary_path:
        raise ValueError("Runtime agent binary is unavailable")
    path = Path(binary_path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("Runtime agent binary is missing or not a regular file")
    try:
        digest = _sha256_file(path)
    except OSError as exc:
        raise ValueError("Runtime agent binary is unreadable") from exc
    if digest != recorded:
        raise ValueError("Runtime agent binary changed during the run")
    return digest


def capture_quality_status(quality: object) -> str:
    """Unknown counters never establish zero loss or a complete observation."""
    if not isinstance(quality, dict):
        return "unknown"
    if quality.get("status") == "degraded":
        return "degraded"
    sources = quality.get("sources")
    if not isinstance(sources, dict) or not sources:
        return "unknown"
    complete = quality.get("status") == "healthy"
    for source in sources.values():
        if not isinstance(source, dict):
            return "unknown"
        numeric_bad = any(isinstance(source.get(key), int) and not isinstance(source.get(key), bool) and source[key] > 0
            for key in ("lost", "rate_limited", "parse_errors", "stdout_skipped"))
        if source.get("initial_snapshot_complete") is False or numeric_bad or any(source.get(key) is True for key in ("forced_kill", "stdout_error", "stderr_error")) \
            or source.get("attach_error") is not None or source.get("attached") == 0 \
            or source.get("core_read") is False or source.get("core_write") is False \
            or (source.get("exit_status") is not None and source["exit_status"] != 0):
            return "degraded"
        complete &= source.get("final_stats_seen") is True and source.get("drain_complete") is True
        complete &= all(type(source.get(key)) is int and source[key] == 0 for key in ("lost", "rate_limited", "exit_status"))
    analysis = quality.get("analysis")
    if not isinstance(analysis, dict):
        return "unknown"
    if type(analysis.get("initial_snapshot_dropped")) is int and analysis["initial_snapshot_dropped"] > 0:
        return "degraded"
    for key in ("persist_dropped", "persist_closed", "persist_failures", "raw_log_failures"):
        value = analysis.get(key)
        if type(value) is int and value > 0:
            return "degraded"
        complete &= type(value) is int and value == 0
    # `partial` flags mark buffered fragments whose complete message is also
    # emitted; every streaming run has many. Final decode failures are the
    # evidence-quality signal that actually means bytes could not be used.
    decode = analysis.get("decode_status")
    if isinstance(decode, dict) and any(
        type(decode.get(key)) is int and decode[key] > 0
        for key in ("malformed", "transport_error")
    ):
        return "degraded"
    if analysis.get("persist_flush_failed") is True:
        return "degraded"
    complete &= analysis.get("persist_flush_failed") is False
    # Coverage, not only loss: a process helper that attached and lost nothing
    # but observed no event did not see the run (comm filter matched nothing,
    # or the probe missed the agent). TLS stays ungated: a run may make no call.
    if any(str(key).startswith("Process:") for key in sources):
        counts = analysis.get("event_counts")
        observed = counts.get("process") if isinstance(counts, dict) else None
        complete &= type(observed) is int and observed > 0
    return "healthy" if complete else "unknown"


def run_observation_state(path: Path, *, expected_implementation: str | None = None) -> dict:
    """Read real canonical provenance/checkpoint diagnostics for metric eligibility.

    ``expected_implementation`` pins the analysis kernel that must have written the
    checkpoint. Without it a frozen database analysed by an older kernel is
    indistinguishable from a current one, and its verdict silently enters the
    metrics. With it, a stale or fingerprint-less checkpoint is rejected instead
    of being downgraded to "unknown".
    """
    import json
    with closing(_connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        columns = {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
        if not {"run_id", "final_decision"} <= columns:
            raise ValueError("Missing canonical run verdict")
        rows = list(conn.execute("SELECT * FROM runs"))
        if len(rows) != 1:
            raise ValueError("Expected exactly one run for observation quality")
        run = dict(rows[0])
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        quality = None
        implementation = None
        if "analysis_checkpoints" in tables:
            checkpoint = conn.execute("SELECT version, payload_json FROM analysis_checkpoints WHERE run_id=? AND checkpoint_kind='frozen-analysis'", (run["run_id"],)).fetchall()
            if len(checkpoint) == 1 and checkpoint[0][0] == 1:
                from tools.annotator.payload_codec import decode_checkpoint_payload
                payload = json.loads(decode_checkpoint_payload(checkpoint[0][1]))
                if payload.get("run_id") == run["run_id"] and payload.get("contract") == "frozen-evidence-reanalysis-v1":
                    recorded = payload.get("implementation")
                    implementation = recorded if isinstance(recorded, str) and recorded else None
                    if expected_implementation is not None and implementation is not None \
                            and implementation != expected_implementation:
                        raise ValueError(
                            "Stale analysis checkpoint: recorded implementation "
                            f"{implementation or 'none'} does not match the expected analysis "
                            "kernel; recapture the run with the current build")
                    quality = payload.get("capture_quality")
    if expected_implementation is not None and implementation is None:
        raise ValueError(
            "Analysis checkpoint carries no implementation fingerprint; it cannot be "
            "attributed to the expected analysis kernel, so the run is stale")
    status = capture_quality_status(quality)
    provenance = json.loads(run.get("decision_provenance_json") or "[]")
    if not isinstance(provenance, list):
        raise ValueError("Invalid canonical decision provenance")
    if "capture_quality:degraded" in provenance:
        status = "degraded"
    elif "capture_quality:unknown" in provenance and status != "degraded":
        status = "unknown"
    decision = run.get("final_decision")
    eligibility = ("unknown_verdict" if decision not in ("Safe", "Unsafe", "Uncertain")
        else "uncertain" if decision == "Uncertain" else "quality_limited" if status != "healthy" else "eligible")
    return {"run_id": run["run_id"], "final_decision": decision, "capture_quality_status": status,
        "evaluation_status": eligibility, "decision_provenance": provenance,
        "checkpoint_implementation": implementation}


def _binding_regular_file(path: Path, root: Path, label: str) -> Path:
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Run binding requires a regular non-symlink {label}")
    try:
        path.resolve().relative_to(Path(root).resolve())
    except ValueError as exc:
        raise ValueError(f"Run binding {label} is outside the expected directory") from exc
    return path


def _tmux_transport(rows: list[dict]) -> dict | None:
    """Optional typed tmux metadata; pane pids never stand in for agent pids."""
    attempts = [row for row in rows
                if row["event_type"] == "launch_attempt"
                and isinstance(row["details"].get("tmux"), dict)]
    if not attempts:
        return None
    panes = []
    for row in attempts:
        tmux = row["details"]["tmux"]
        panes.append({
            "agent_index": row["details"]["agent_index"],
            "session": tmux["session"],
            "pane": tmux["pane"],
            "pane_id": tmux["pane_id"],
            "pane_pid": tmux["pane_pid"],
            "role_file": tmux["role_file"],
        })
    return {
        "transport": "tmux",
        "session": panes[0]["session"],
        "agent_pid_observed": False,
        "pane_pid_is_not_agent_pid": True,
        "panes_expected": len(panes),
        "panes_observed": sum(pane["pane_id"] is not None and pane["pane_pid"] is not None
                              for pane in panes),
        "pane_observation_complete": all(
            pane["pane_id"] is not None and pane["pane_pid"] is not None for pane in panes),
        "panes": panes,
    }


def _binding_run(path: Path, label: str) -> dict:
    with closing(_connect(Path(path))) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
        required = {"run_id", "scenario_id", "mode", "ended_at_ns", "final_decision", "final_severity"}
        if not required <= columns:
            raise ValueError(f"{label} database has no canonical run record")
        rows = list(conn.execute(
            "SELECT run_id, scenario_id, mode, ended_at_ns, final_decision, final_severity FROM runs"))
    if len(rows) != 1:
        raise ValueError(f"Expected exactly one closed run in the {label} database")
    run = dict(zip(("run_id", "scenario_id", "mode", "ended_at_ns", "final_decision", "final_severity"),
                   rows[0]))
    if not isinstance(run["run_id"], str) or not run["run_id"].strip():
        raise ValueError(f"The {label} database has no collector run identity")
    if type(run["ended_at_ns"]) is not int or run["ended_at_ns"] <= 0:
        raise ValueError(f"The {label} database does not contain a closed run")
    return run


def bind_run_artifacts(run_dir: Path, database: Path, release_root: Path) -> dict:
    """Associate a run's harness ledger and summary with its collector databases."""
    from tools.annotator.evidence import _loads_strict
    from tools.annotator.runner_import import read_runner_evidence

    run_dir = Path(run_dir)
    if not run_dir.is_dir():
        raise ValueError("Run directory is missing")
    collector_dir = run_dir / "collector"
    if collector_dir.is_symlink() or not collector_dir.is_dir():
        raise ValueError("Run collector directory is missing")

    summary_path = _binding_regular_file(run_dir / "summary.json", run_dir, "summary")
    ledger_path = _binding_regular_file(run_dir / "runner-events.jsonl", run_dir, "runner ledger")
    frozen_path = _binding_regular_file(database, collector_dir, "frozen database")

    summary_bytes = summary_path.read_bytes()
    summary_sha256 = hashlib.sha256(summary_bytes).hexdigest()
    summary = _loads_strict(summary_bytes.decode("utf-8"))
    if not isinstance(summary, dict):
        raise ValueError("Run summary must be a JSON object")
    ledger_sha256 = hashlib.sha256(ledger_path.read_bytes()).hexdigest()
    frozen_sha256 = hashlib.sha256(frozen_path.read_bytes()).hexdigest()

    evidence = read_runner_evidence(ledger_path, release_root=Path(release_root))
    if hashlib.sha256(evidence["raw"]).hexdigest() != ledger_sha256:
        raise ValueError("Runner ledger changed while verifying")
    tmux = _tmux_transport(evidence["rows"])
    first = evidence["rows"][0]
    reference = first["details"].get("input_reference")
    if not isinstance(reference, dict):
        raise ValueError("Runner run_started record lacks a verified input reference")
    historical_run = Path(reference["materialized_fixture_dir"]).parent

    source_value = summary.get("database_path")
    if not isinstance(source_value, str) or not source_value:
        raise ValueError("Run summary has no source database path")
    declared_source = Path(source_value)
    if declared_source.parent != historical_run / "collector":
        raise ValueError("Run summary source database is outside its historical collector directory")
    source_path = _binding_regular_file(collector_dir / declared_source.name, collector_dir, "source database")
    if not source_path.name.endswith(".responsibility.db"):
        raise ValueError("Run source database is not a responsibility database")
    candidates = [path for path in collector_dir.iterdir()
                  if path.name.endswith(".responsibility.db")]
    if len(candidates) != 1 or candidates[0].resolve() != source_path.resolve():
        raise ValueError("Collector must contain exactly one matching responsibility database")
    if (source_path.resolve() == frozen_path.resolve() or
            (source_path.stat().st_dev, source_path.stat().st_ino) ==
            (frozen_path.stat().st_dev, frozen_path.stat().st_ino)):
        raise ValueError("Source and frozen databases must be distinct files")

    health = summary.get("collector_health")
    if not isinstance(health, dict) or health.get("stop_status") != "stopped" \
            or health.get("is_running_after_stop") is not False:
        raise ValueError("Collector did not report a verified stop")

    binding = evidence["binding"]
    for key in ("run_id", "scenario_id", "source_run_uuid"):
        if summary.get(key) != binding[key]:
            raise ValueError(f"Run summary {key} does not match native runner evidence")
    declared_ledger = summary.get("runner_events_path")
    if not isinstance(declared_ledger, str) or \
            Path(declared_ledger) != historical_run / "runner-events.jsonl":
        raise ValueError("Run summary ledger path does not match the native ledger")

    scenario_id = binding["scenario_id"]
    if not scenario_id or scenario_id in (".", "..") or "/" in scenario_id or "\\" in scenario_id:
        raise ValueError("Native scenario id is not a release-relative name")
    scenario_relative = "scenarios/" + scenario_id + ".json"
    scenario_bytes = (Path(release_root) / scenario_relative).read_bytes()
    if hashlib.sha256(scenario_bytes).hexdigest() != reference["source_files"].get(scenario_relative):
        raise ValueError("Release scenario changed while binding run artifacts")
    scenario = _loads_strict(scenario_bytes.decode("utf-8"))
    if not isinstance(scenario, dict) or scenario.get("id") != scenario_id:
        raise ValueError("Release scenario does not match the native run")
    mode = scenario.get("mode")
    if not isinstance(mode, str) or not mode:
        raise ValueError("Release scenario has no mode")

    source_run = _binding_run(source_path, "source")
    frozen_run = _binding_run(frozen_path, "frozen")
    canonical = read_canonical_verdict(frozen_path)
    if canonical["db_sha256"] != frozen_sha256:
        raise ValueError("Frozen database changed while verifying")
    if (frozen_run["run_id"], frozen_run["final_decision"], frozen_run["final_severity"]) != \
            (canonical["run_id"], canonical["final_decision"], canonical["final_severity"]):
        raise ValueError("Frozen database verdict changed while verifying")
    fields = ("run_id", "scenario_id", "mode", "ended_at_ns", "final_decision", "final_severity")
    if any(source_run[key] != frozen_run[key] for key in fields):
        raise ValueError("Source and frozen databases do not describe the same run")
    if source_run["scenario_id"] != scenario_id or frozen_run["scenario_id"] != scenario_id:
        raise ValueError("Collector run scenario does not match native evidence")
    if source_run["mode"] != mode or frozen_run["mode"] != mode:
        raise ValueError("Collector run mode does not match the release scenario")

    for path, root, label, expected in (
            (summary_path, run_dir, "summary", summary_sha256),
            (ledger_path, run_dir, "runner ledger", ledger_sha256),
            (frozen_path, collector_dir, "frozen database", frozen_sha256)):
        current = _binding_regular_file(path, root, label)
        if hashlib.sha256(current.read_bytes()).hexdigest() != expected:
            raise ValueError(f"{label} changed while verifying run artifacts")

    from scripts.evaluation.collector_context import read_collector_context, compare_process_context
    with closing(_connect(source_path)) as connection:
        source_context = read_collector_context(connection, source_run["run_id"])
    with closing(_connect(frozen_path)) as connection:
        collector_context = read_collector_context(connection, frozen_run["run_id"])
    if source_context != collector_context:
        raise ValueError("Source and frozen collector contexts do not match")
    from scripts.evaluation.kernel_process_alignment import read_kernel_mappings, align_kernel_processes
    with closing(_connect(source_path)) as connection:
        source_mappings = read_kernel_mappings(connection, source_run["run_id"])
    with closing(_connect(frozen_path)) as connection:
        kernel_mappings = read_kernel_mappings(connection, frozen_run["run_id"])
    if source_mappings != kernel_mappings:
        raise ValueError("Source and frozen kernel process mappings do not match")
    kernel_fields = {}
    if kernel_mappings is not None:
        import json
        kernel_fields = {
            "kernel_mappings_sha256": hashlib.sha256(json.dumps(
                kernel_mappings, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            "kernel_process_alignment": align_kernel_processes(evidence["rows"], collector_context, kernel_mappings),
        }
    from scripts.evaluation.kernel_process_alignment import read_initial_snapshot
    with closing(_connect(source_path)) as connection:
        source_snapshot = read_initial_snapshot(connection, source_run["run_id"])
    with closing(_connect(frozen_path)) as connection:
        initial_snapshot = read_initial_snapshot(connection, frozen_run["run_id"])
    if source_snapshot != initial_snapshot:
        raise ValueError("Source and frozen initial snapshots do not match")
    snapshot_fields = {}
    if initial_snapshot is not None:
        import json
        snapshot_fields = {"initial_snapshot_sha256": hashlib.sha256(json.dumps(
            initial_snapshot, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}
    context_fields = {}
    if collector_context is not None:
        import json
        context_fields = {
            "collector_context_sha256": hashlib.sha256(json.dumps(
                collector_context, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            "process_context_check": compare_process_context(evidence["rows"], collector_context),
        }
    root = run_dir.resolve()
    return {
        "schema": ("mtagentlens.run-binding/v4" if snapshot_fields else
                   "mtagentlens.run-binding/v3" if kernel_fields else
                   "mtagentlens.run-binding/v2" if context_fields else "mtagentlens.run-binding/v1"),
        **context_fields,
        **kernel_fields,
        **snapshot_fields,
        **({"tmux": tmux} if tmux is not None else {}),
        "scenario_id": scenario_id,
        "release_id": binding["data_release_id"],
        "harness_run_id": binding["run_id"],
        "source_run_uuid": binding["source_run_uuid"],
        "collector_run_id": frozen_run["run_id"],
        "summary_sha256": summary_sha256,
        "runner_events_sha256": ledger_sha256,
        "database_sha256": frozen_sha256,
        "source_database": source_path.resolve().relative_to(root).as_posix(),
        "database": frozen_path.resolve().relative_to(root).as_posix(),
        "event_alignment_available": False,
    }


def validate_run_binding(row: dict, release_root: Path) -> None:
    """Recompute a row's binding instead of trusting its recorded artifact paths."""
    if not isinstance(row, dict):
        raise ValueError("Run record is not an object")
    binding = row.get("run_binding")
    if not isinstance(binding, dict):
        raise ValueError("Run binding is missing")
    paths = {}
    for key in ("run_dir", "db_path"):
        value = row.get(key)
        if not isinstance(value, str) or not value or "\x00" in value:
            raise ValueError(f"Run binding has a malformed {key}")
        path = Path(value)
        if not path.is_absolute():
            raise ValueError(f"Run binding has a malformed {key}")
        paths[key] = path
    rebuilt = bind_run_artifacts(paths["run_dir"], paths["db_path"], Path(release_root))
    if binding != rebuilt:
        raise ValueError("Run binding does not match recomputed run artifacts")
    if row.get("run_id") != rebuilt["collector_run_id"]:
        raise ValueError("Run id does not match the collector run")
    if row.get("scenario_id") != rebuilt["scenario_id"]:
        raise ValueError("Run scenario does not match the run binding")
