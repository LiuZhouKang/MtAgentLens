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
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCENARIO_DIR = ROOT / "env" / "scenarios" / "benign"
RUNS_DIR = ROOT / "env" / "runs"


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
    password = os.environ.get("MTAGENTLENS_SUDO_PASSWORD", "1")
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


def alert_stats(db_path: Path) -> dict:
    stats: dict = {
        "alerts_total": 0,
        "alerts_warning_plus": 0,
        "alerts_by_severity": {},
        "alerts_by_decision": {},
        "alerts_by_display_profile": {},
        "claims": 0,
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
    conn = sqlite3.connect(f"file:{readable}?mode=ro", uri=True)
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir")
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    results_path = out_dir / "results.jsonl"

    done: set[str] = set()
    if results_path.exists():
        for line in results_path.read_text().splitlines():
            try:
                done.add(json.loads(line)["scenario_id"])
            except (ValueError, KeyError):
                pass

    scenarios = sorted(SCENARIO_DIR.glob("*.json"))
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
            cmd = [
                "mtagentlens-benchmark", "run",
                "--scenario", scenario_id,
                "--mode", mode,
                "--collector",
                "--clean-profile",
            ]
            if agent.binary:
                cmd.extend(["--agent-binary", agent.binary])
                cmd.extend(["--agent-comm", agent.comm])
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
            if run_dir is not None:
                dbs = sorted(
                    glob.glob(str(run_dir / "collector" / "*.responsibility.db"))
                )
                if dbs:
                    db_path = Path(dbs[0])

            record = {
                "scenario_id": scenario_id,
                "mode": mode,
                "returncode": rc,
                "duration_seconds": round(duration, 1),
                "run_dir": str(run_dir) if run_dir else None,
                "db_path": str(db_path) if db_path else None,
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
                "error": "no responsibility db produced",
            })
            with results_path.open("a") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            print(json.dumps(record, ensure_ascii=False), flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
