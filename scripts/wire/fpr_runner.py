#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Measure the false-positive rate of the responsibility layer on benign scenarios.

For each scenario under env/scenarios/benign/:
  1. run `mtagentlens-benchmark run --scenario <id> --mode <auto> --collector`
  2. locate the run directory created by that invocation
  3. read alert rows straight from SQLite (not through the Rust reader, which
     silently coerces unknown values)

Output: one JSON object per scenario on stdout (and in results.jsonl), so the
measurement is resumable and every number traces back to a run directory.

Usage: scripts/wire/run-benign-fpr.sh [output_dir]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sqlite3
import shutil
import subprocess
import sys
import hashlib
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCENARIO_DIR = ROOT / "env" / "scenarios" / "benign"
RUNS_DIR = ROOT / "env" / "runs"
COLLECTOR_BINARY = ROOT / "collector" / "build" / "release" / "mtagentlens"


def binary_sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def scenario_mode(path: Path) -> str:
    data = json.loads(path.read_text())
    agents = data.get("agents", [])
    if data.get("mode") == "single_agent" or len(agents) <= 1:
        return "process"
    return "tmux-live"


def newest_run_dir(scenario_id: str, started_at: float) -> Path | None:
    candidates: list[Path] = []
    for run_dir in RUNS_DIR.iterdir():
        if not run_dir.is_dir():
            continue
        marker = run_dir / ".scenario_id"
        if not marker.exists():
            continue
        try:
            if marker.read_text().strip() != scenario_id:
                continue
        except OSError:
            continue
        if run_dir.stat().st_mtime + 5 < started_at:
            continue
        candidates.append(run_dir)
    return max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None


def _sudo_readable_copy(db_path: Path) -> Path | None:
    """Copy a root-owned 0600 database so this process can read it.

    The collector intentionally creates its database with 0600 permissions.
    The measurement previously failed with "unable to open database file" for
    every run when invoked as the normal user; the copy is the read-only
    equivalent of `sudo sqlite3`.
    """
    import tempfile

    tmpdir = Path(tempfile.mkdtemp(prefix="mtl-fpr-db-"))
    tmp = tmpdir / db_path.name
    password = os.environ.get("MTAGENTLENS_SUDO_PASSWORD", "")
    proc = subprocess.run(
        ["sudo", "-S", "-p", "", "cp", str(db_path), str(tmp)],
        input=password + "\n",
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        return None
    subprocess.run(
        ["sudo", "-S", "-p", "", "chown", f"{os.getuid()}:{os.getgid()}", str(tmp)],
        input=password + "\n",
        text=True,
        capture_output=True,
    )
    return tmp


_SQLITE_BACKUP = (
    "import sqlite3,sys;"
    "source=sqlite3.connect(sys.argv[1]);"
    "target=sqlite3.connect(sys.argv[2]);"
    "source.backup(target);"
    "target.close();source.close()"
)


def collector_running(run_dir: Path) -> bool:
    """Is the collector for this run still writing?

    An unavailable `pgrep` means "cannot observe", which degrades to the
    previous behaviour (snapshot immediately) rather than blocking the campaign.
    """
    try:
        result = subprocess.run(["pgrep", "-f", f"-o {run_dir}/collector/collector"],
                                capture_output=True, text=True)
    except OSError:
        return False
    return bool((getattr(result, "stdout", "") or "").strip())


def wait_for_collector(run_dir: Path, *, timeout: float = 180.0) -> bool:
    """Wait for graceful finalisation; a timeout is reported, never assumed."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not collector_running(run_dir):
            return True
        time.sleep(1.0)
    return False


_VERDICT_QUERY = (
    "import sqlite3,sys;"
    "c=sqlite3.connect('file:'+sys.argv[1]+'?mode=ro',uri=True);"
    "row=c.execute('select final_decision from runs').fetchone();"
    "print('' if row is None or row[0] is None else row[0])"
)


def verdict_state(db_path: Path) -> str | None:
    """Poll the canonical verdict without copying the database.

    Waiting for the process to exit is not enough: the collector finishes its
    write just after shutdown (observed once), so the verdict itself is the
    condition to wait for. A readable database is queried directly; a
    root-owned one through sudo.
    """
    db_path = Path(db_path)
    if not db_path.is_file():
        return None
    try:
        if os.access(db_path, os.R_OK):
            import sqlite3
            connection = sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True)
            try:
                row = connection.execute("select final_decision from runs").fetchone()
            finally:
                connection.close()
            value = row[0] if row else None
        else:
            password = os.environ.get("MTAGENTLENS_SUDO_PASSWORD", "")
            probe = subprocess.run(
                ["sudo", "-S", "-p", "", sys.executable, "-c", _VERDICT_QUERY, str(db_path)],
                input=password + "\n", text=True, capture_output=True)
            if probe.returncode != 0:
                return None
            value = (probe.stdout or "").strip() or None
    except (OSError, sqlite3.Error, ValueError):
        return None
    return value if value in ("Safe", "Unsafe", "Uncertain") else None


def stop_collector(run_dir: Path, *, timeout: float = 90.0) -> bool:
    """Stop the collector this run owns, so finalisation can complete.

    `tmux-live` mode returned from the benchmark leaving the collector running:
    the run then has no canonical verdict until something else terminates it
    (observed on 4/4 multi-agent benign runs). Measurement must own the
    lifecycle instead of hoping the harness does.
    """
    pattern = f"-o {run_dir}/collector/collector"
    password = os.environ.get("MTAGENTLENS_SUDO_PASSWORD", "")
    subprocess.run(["sudo", "-S", "-p", "", "pkill", "-INT", "-f", pattern],
                   input=password + "\n", text=True, capture_output=True)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not collector_running(run_dir):
            return True
        time.sleep(0.5)
    return not collector_running(run_dir)


def wait_for_verdict(db_path: Path, *, timeout: float = 180.0) -> bool:
    """Wait until the canonical verdict exists; a timeout is reported, not assumed."""
    deadline = time.monotonic() + timeout
    delay = 0.2
    while time.monotonic() < deadline:
        if verdict_state(db_path) is not None:
            return True
        time.sleep(min(delay, 2.0))
        delay *= 1.6
    return verdict_state(db_path) is not None


def finalized_snapshot(db_path: Path) -> Path | None:
    """A WAL-consistent copy of a root-owned database.

    Copying the main file while the collector still holds unflushed WAL pages
    loses the final verdict (observed: canonical verdict missing in 6/18 rows).
    A SQLite online backup taken as root includes committed WAL pages.
    """
    db_path = Path(db_path)
    directory = Path(tempfile.mkdtemp(prefix="mtl-fpr-db-"))
    target = directory / db_path.name
    password = os.environ.get("MTAGENTLENS_SUDO_PASSWORD", "")
    backup = subprocess.run(
        ["sudo", "-S", "-p", "", sys.executable, "-c", _SQLITE_BACKUP, str(db_path), str(target)],
        input=password + "\n", text=True, capture_output=True)
    if backup.returncode != 0 or not target.is_file():
        return None
    subprocess.run(["sudo", "-S", "-p", "", "chown", f"{os.getuid()}:{os.getgid()}", str(target)],
                   input=password + "\n", text=True, capture_output=True)
    return target


def observation_state(db_path: Path | None) -> dict:
    """Canonical capture-quality and metric eligibility for one run.

    Read from the collector's frozen analysis checkpoint, never from our own
    counters: a run whose loss counters are missing is `unknown`, not healthy.
    """
    base = {"run_id": None, "capture_quality_status": "unknown",
            "evaluation_status": "unobserved"}
    if db_path is None:
        return base
    readable = Path(db_path)
    if not os.access(readable, os.R_OK):
        copy = _sudo_readable_copy(readable)
        if copy is None:
            return {**base, "artifact_error": "capture quality unreadable (sudo copy failed)"}
        readable = copy
    try:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from scripts.evaluation.run_artifacts import run_observation_state
        return run_observation_state(readable)
    except (ValueError, sqlite3.Error, KeyError) as exc:
        return {**base, "artifact_error": f"capture quality unavailable: {exc}"}


def alert_stats(db_path: Path) -> dict:
    stats: dict = {
        "alerts_total": 0,
        "alerts_warning_plus": 0,
        "alerts_by_severity": {},
        "alerts_by_decision": {},
        "alerts_by_display_profile": {},
        "claims": 0,
        # Per-type split so a gate can be judged (TaintedContextUse vs
        # PolicyViolation), not just the total claim count.
        "claims_by_type": {},
        # Canonical per-run verdict. `None` means the run has no recorded
        # final decision (pre-v3 DB or a run that never finalized) — not Safe.
        "final_decision": None,
        "final_severity": None,
        "decision_source": None,
        "intents": 0,
        "actions": 0,
        "evidence": 0,
        # A run with zero intents/actions/evidence is not a clean run; it is
        # an unobserved one. Counting it as "no alert" would dilute the FPR.
        "observed": False,
    }
    if not db_path.exists():
        stats["error"] = "responsibility db missing"
        return stats
    readable = db_path
    if not os.access(db_path, os.R_OK):
        readable = _sudo_readable_copy(db_path)
        if readable is None:
            stats["error"] = "responsibility db is not readable (sudo copy failed)"
            return stats
    conn = sqlite3.connect(readable.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        if "responsibility_alerts" in tables:
            for sev, decision, profile, count in conn.execute(
                "SELECT severity, decision, display_profile, COUNT(*) "
                "FROM responsibility_alerts GROUP BY 1, 2, 3"
            ):
                stats["alerts_total"] += count
                stats["alerts_by_severity"][str(sev)] = (
                    stats["alerts_by_severity"].get(str(sev), 0) + count
                )
                stats["alerts_by_decision"][str(decision)] = (
                    stats["alerts_by_decision"].get(str(decision), 0) + count
                )
                stats["alerts_by_display_profile"][str(profile)] = (
                    stats["alerts_by_display_profile"].get(str(profile), 0) + count
                )
                if str(sev) in ("Warning", "Critical"):
                    stats["alerts_warning_plus"] += count
        # The run's canonical verdict (schema v3). Falling back to "any alert
        # row with decision=Unsafe" made phase-1 provisional alerts count: a
        # run where the Observer said Unsafe early and the aggregated final
        # verdict was Safe (or vice versa) was scored by whichever row the
        # query happened to see.
        if "runs" in tables:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(runs)")}
            if "final_decision" in cols:
                row = conn.execute(
                    "SELECT final_decision, final_severity FROM runs "
                    "WHERE final_decision IS NOT NULL LIMIT 1"
                ).fetchone()
                if row:
                    stats["final_decision"] = row[0]
                    stats["final_severity"] = row[1]
                    stats["decision_source"] = "runs.final_decision"
        # `stats` is pre-seeded with final_decision=None, so a membership test
        # was always false and the legacy fallback never ran.
        if stats.get("final_decision") is None and "responsibility_alerts" in tables:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(responsibility_alerts)")}
            if "phase" in cols:
                row = conn.execute(
                    "SELECT decision FROM responsibility_alerts "
                    "WHERE phase LIKE 'Authoritative%' "
                    "ORDER BY timestamp_ns DESC LIMIT 1"
                ).fetchone()
                if row:
                    stats["final_decision"] = row[0]
                    stats["decision_source"] = "authoritative_alert"
        if "process_events" in tables:
            stats["process_comms"] = sorted(
                {str(r[0]) for r in conn.execute("SELECT DISTINCT comm FROM process_events")}
            )
        for name, key in (
            ("responsibility_claims", "claims"),
            ("intents", "intents"),
            ("actions", "actions"),
            ("correlation_evidence", "evidence"),
        ):
            if name in tables:
                stats[key] = conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
        if "responsibility_claims" in tables:
            for claim_type, count in conn.execute(
                "SELECT claim_type, COUNT(*) FROM responsibility_claims GROUP BY 1"
            ):
                stats["claims_by_type"][str(claim_type)] = count
    finally:
        conn.close()
    if readable != db_path:
        import shutil

        shutil.rmtree(readable.parent, ignore_errors=True)
    stats["observed"] = (
        stats.get("intents", 0) > 0
        or stats.get("actions", 0) > 0
        or stats.get("evidence", 0) > 0
    )
    return stats


def completed_scenarios(results_path: Path) -> set[str]:
    """Scenarios with a completed, observed run; corrupt lines abort the scan."""
    done: set[str] = set()
    if not results_path.exists():
        return done
    for number, line in enumerate(results_path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError as exc:
            raise ValueError(f"invalid results line {number}: {exc}") from exc
        if not isinstance(record, dict) or not isinstance(record.get("scenario_id"), str) \
                or not record["scenario_id"]:
            raise ValueError(f"results line {number} has no scenario id")
        if record.get("state") == "pending":
            continue
        # A row is only "done" when it carries a usable canonical verdict. A
        # database read mid-finalisation returns alerts but no verdict; retrying
        # that scenario is cheaper than silently losing it from every denominator.
        if (record.get("returncode") == 0 and record.get("alerts_total") is not None
                and record.get("final_decision") in ("Safe", "Unsafe", "Uncertain")):
            done.add(record["scenario_id"])
    return done


def scenario_command(scenario_id: str, mode: str, *, agent_binary: str | None,
                     agent_comm: str | None, disable_observer: bool = False) -> list[str]:
    """The exact benchmark invocation for one measured run.

    `disable_observer` is the deterministic-core arm: the semantic Observer can
    abstain (`Uncertain`) on benign traffic, so a review rate measured with it
    enabled is not the same quantity as the deterministic false-positive rate.
    """
    cmd = [
        "mtagentlens-benchmark", "run",
        "--scenario", scenario_id,
        "--mode", mode,
        "--collector",
        "--clean-profile",
    ]
    if disable_observer:
        cmd.append("--disable-responsibility-observer")
    if agent_binary:
        cmd.extend(["--agent-binary", agent_binary])
        cmd.extend(["--agent-comm", agent_comm])
    return cmd


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--only", action="append", default=[], help="scenario id (repeatable)")
    parser.add_argument("--limit", type=int, default=0, help="first N scenarios")
    parser.add_argument("--disable-observer", action="store_true",
                        help="deterministic-core arm: run without the semantic Observer")
    parser.add_argument("--pass-label", default="1",
                        help="repetition label recorded on every row (one output dir per pass)")
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    results_path = out_dir / "results.jsonl"

    try:
        # Only a completed, observed run counts as done. A failed or DB-less
        # attempt is retried; a corrupt line must abort instead of silently
        # re-running scenarios on top of unreadable evidence.
        done = completed_scenarios(results_path)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    scenarios = sorted(SCENARIO_DIR.glob("*.json"))
    if args.only:
        wanted = set(args.only)
        scenarios = [path for path in scenarios if path.stem in wanted]
    elif args.limit:
        scenarios = scenarios[: args.limit]
    if not scenarios:
        print(f"ERROR: no benign scenarios under {SCENARIO_DIR}", file=sys.stderr)
        return 2

    from mtagentlens_benchmark.engine.clean_profile import isolated_agent

    with isolated_agent() as agent:
        print(
            f"[isolation] binary={agent.binary} comm={agent.comm}",
            file=sys.stderr,
        )
        for scenario_path in scenarios:
            scenario_id = scenario_path.stem
            if scenario_id in done:
                print(f"[skip] {scenario_id} (already measured)", file=sys.stderr)
                continue

            mode = scenario_mode(scenario_path)
            started_at = time.time()
            cmd = scenario_command(scenario_id, mode, agent_binary=agent.binary,
                                   agent_comm=agent.comm,
                                   disable_observer=args.disable_observer)
            env = agent.apply(os.environ.copy())
            env["MTAGENTLENS_CLEAN_PROFILE"] = "1"
            print(f"[run ] {scenario_id} mode={mode}", file=sys.stderr)
            try:
                proc = subprocess.run(
                    cmd, cwd=str(ROOT), capture_output=True, text=True,
                    timeout=args.timeout, env=env,
                )
                rc = proc.returncode
                stdout_tail = proc.stdout[-4000:]
                stderr_tail = proc.stderr[-4000:]
            except subprocess.TimeoutExpired as exc:
                rc = -1
                stdout_tail = (
                    (exc.stdout or b"").decode(errors="replace")[-4000:]
                    if isinstance(exc.stdout, bytes)
                    else str(exc.stdout or "")[-4000:]
                )
                stderr_tail = f"timeout after {args.timeout}s"

            duration = time.time() - started_at
            run_dir = newest_run_dir(scenario_id, started_at)
            db_path = None
            source_db = None
            artifact_error = None
            if run_dir is not None:
                dbs = sorted(
                    glob.glob(str(run_dir / "collector" / "*.responsibility.db"))
                )
                if dbs:
                    source_db = Path(dbs[0])
            if source_db is not None:
                # A returned benchmark does not guarantee a stopped collector.
                if run_dir is not None and collector_running(run_dir):
                    stop_collector(run_dir)
                # The canonical verdict is written during shutdown, just after the
                # process exits; waiting on the verdict itself is the only robust
                # condition (a process-exit wait once still missed it).
                if not wait_for_verdict(source_db, timeout=240.0):
                    artifact_error = "no canonical verdict within the finalisation timeout"
                else:
                    db_path = finalized_snapshot(source_db)
                    if db_path is None:
                        artifact_error = "cannot snapshot the finalized database"

            record = {
                "scenario_id": scenario_id,
                "collector_binary": str(COLLECTOR_BINARY),
                "collector_sha256": binary_sha256(COLLECTOR_BINARY),
                "mode": mode,
                "observer": "off" if args.disable_observer else "on",
                "pass": args.pass_label,
                "returncode": rc,
                "duration_seconds": round(duration, 1),
                "run_dir": str(run_dir) if run_dir else None,
                "db_path": str(db_path) if db_path else None,
                "source_db": str(source_db) if source_db else None,
                "finalized_db_sha256": (hashlib.sha256(db_path.read_bytes()).hexdigest()
                                        if db_path is not None else None),
                "isolation": {
                    "agent_binary": agent.binary,
                    "agent_comm": agent.comm,
                    "clean_profile": True,
                },
                "stdout_tail": stdout_tail,
                "stderr_tail": stderr_tail,
            }
            record.update(alert_stats(db_path) if db_path else {
                "alerts_total": None,
                "error": artifact_error or "no responsibility db produced",
            })
            record.update(observation_state(db_path))
            if db_path is not None and db_path != source_db:
                # The snapshot exists only to read a WAL-consistent verdict; the
                # ledger points at the real evidence and its recorded hash.
                record["db_path"] = str(source_db)
                record["snapshot_used"] = True
                shutil.rmtree(db_path.parent, ignore_errors=True)
            if artifact_error:
                record["artifact_error"] = artifact_error
                record.setdefault("final_decision", None)
            with results_path.open("a") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            print(json.dumps(record, ensure_ascii=False), flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
