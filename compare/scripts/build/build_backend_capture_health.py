#!/usr/bin/env python3
"""Build backend capture-health evidence from completed run directories."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT = PROJECT_ROOT / "compare" / "results" / "backend_health" / "capture_health.json"
DEFAULT_COLLECTOR_BINARY = PROJECT_ROOT / "collector" / "target" / "release" / "mtagentlens"
REQUIRED_DB_TABLES = ("runs", "process_events", "tls_sessions", "actions")
OPTIONAL_DERIVED_DB_TABLES = ("message_bindings",)


def build_backend_capture_health(
    *,
    run_dirs: list[Path],
    output_path: Path = DEFAULT_OUTPUT,
    collector_binary: Path = DEFAULT_COLLECTOR_BINARY,
    runner: str = "compare/scripts/build_backend_capture_health.py",
) -> dict[str, Any]:
    runs = [_inspect_run_dir(path) for path in run_dirs]
    counters = _merge_counters(runs)
    checks = _build_checks(runs, counters)
    status = "completed" if runs and all(item["status"] == "pass" for item in checks.values()) else "blocked"

    artifact = {
        "schema": "mtagentlens.backend_capture_health.v1",
        "status": status,
        "provenance": {
            "git_commit": _git_commit(),
            "kernel": platform.platform(),
            "collector_binary_hash": _file_sha256(collector_binary),
            "collector_binary": _display_path(collector_binary),
            "runner": runner,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
        "checks": checks,
        "counters": counters,
        "runs": runs,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return artifact


def _inspect_run_dir(run_dir: Path) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    summary = _read_json(run_dir / "summary.json")
    collector_dir = run_dir / "collector"
    db_path = _resolve_db_path(run_dir, summary)
    trace_log = collector_dir / "trace.log"
    trace_err = collector_dir / "trace.err.log"
    pcap = collector_dir / "trace.pcap"
    counters = _parse_trace_err_counters(trace_err)
    db = _inspect_db(db_path)
    collector_health = summary.get("collector_health", {}) if isinstance(summary, dict) else {}
    problems: list[str] = []

    if not isinstance(summary, dict):
        problems.append("summary.json missing or invalid")
    elif summary.get("success") is not True:
        problems.append("summary success must be true")

    if not isinstance(collector_health, dict) or collector_health.get("status") != "completed":
        problems.append("collector_health.status must be completed")
    if not isinstance(collector_health, dict) or collector_health.get("stop_status") != "stopped":
        problems.append("collector stop_status must be stopped")
    if not db["exists"]:
        problems.append("responsibility DB missing")
    elif db["schema_version"] < 2:
        problems.append("responsibility DB schema_version must be >= 2")
    for table in REQUIRED_DB_TABLES:
        if db["table_counts"].get(table, 0) <= 0:
            problems.append(f"required DB table {table} must be non-empty")
    if not trace_err.exists():
        problems.append("trace.err.log missing")

    run_id = str(summary.get("run_id") if isinstance(summary, dict) else run_dir.name)
    scenario_id = _scenario_id(run_dir, summary)
    return {
        "run_id": run_id,
        "scenario_id": scenario_id,
        "status": "completed" if not problems else "blocked",
        "problems": problems,
        "artifacts": {
            "run_dir": _display_path(run_dir),
            "db_path": _display_path(db_path),
            "trace_log": _display_path(trace_log),
            "trace_err_log": _display_path(trace_err),
            "pcap": _display_path(pcap),
        },
        "collector": {
            "pid": collector_health.get("pid") if isinstance(collector_health, dict) else None,
            "exit_status": "checked" if isinstance(collector_health, dict) and collector_health.get("stop_status") == "stopped" else "missing",
            "health": collector_health if isinstance(collector_health, dict) else {},
        },
        "db": db,
        "counters": counters,
    }


def _build_checks(runs: list[dict[str, Any]], counters: dict[str, int | None]) -> dict[str, dict[str, Any]]:
    any_runs = bool(runs)
    collector_started = any_runs and all(
        run["collector"]["health"].get("enabled") is True
        and run["collector"]["health"].get("status") == "completed"
        for run in runs
    )
    collector_exit_checked = any_runs and all(run["collector"]["exit_status"] == "checked" for run in runs)
    db_schema_current = any_runs and all(run["db"]["schema_version"] >= 2 for run in runs)
    db_nonempty = any_runs and all(
        all(run["db"]["table_counts"].get(table, 0) > 0 for table in REQUIRED_DB_TABLES)
        for run in runs
    )
    return {
        "collector_started": _check(collector_started),
        "collector_exit_status_checked": _check(collector_exit_checked),
        "persistence_lost_count_zero": _check(counters.get("persistence_lost_count") == 0),
        "tls_drop_count_checked": _check(counters.get("tls_drop_count") == 0),
        "process_drop_count_checked": _check(counters.get("process_drop_count") == 0),
        "db_schema_current": _check(db_schema_current),
        "db_nonempty_required_tables": _check(db_nonempty),
    }


def _check(passed: bool) -> dict[str, Any]:
    return {"status": "pass" if passed else "fail"}


def _merge_counters(runs: list[dict[str, Any]]) -> dict[str, int | None]:
    merged: dict[str, int | None] = {
        "persistence_lost_count": 0,
        "tls_drop_count": 0,
        "process_drop_count": 0,
    }
    for key in list(merged):
        values = [run["counters"].get(key) for run in runs]
        if not values or any(value is None for value in values):
            merged[key] = None
        else:
            merged[key] = int(sum(values))
    return merged


def _parse_trace_err_counters(path: Path) -> dict[str, int | None]:
    counters: dict[str, int | None] = {
        "persistence_lost_count": None,
        "tls_drop_count": None,
        "process_drop_count": None,
    }
    if not path.exists():
        return counters
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        payload = _extract_json_object(line)
        if payload is None:
            continue
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        name = str(event.get("event", ""))
        if name == "PERSISTENCE_STATS" and "lost_persist_count" in event:
            counters["persistence_lost_count"] = int(event["lost_persist_count"])
        elif name in {"TLS_RINGBUF_STATS", "RINGBUF_STATS"} and "dropped_events" in event:
            counters["tls_drop_count"] = int(event["dropped_events"])
        elif name == "PROCESS_RINGBUF_STATS" and "dropped_events" in event:
            counters["process_drop_count"] = int(event["dropped_events"])
    return counters


def _extract_json_object(line: str) -> str | None:
    start = line.find("{")
    end = line.rfind("}")
    if start < 0 or end < start:
        return None
    return line[start : end + 1]


def _inspect_db(db_path: Path) -> dict[str, Any]:
    result = {
        "exists": db_path.exists(),
        "schema_version": 0,
        "table_counts": {table: 0 for table in REQUIRED_DB_TABLES + OPTIONAL_DERIVED_DB_TABLES},
    }
    if not db_path.exists():
        return result
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        result["schema_version"] = _query_int(
            conn,
            "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1",
        )
        for table in REQUIRED_DB_TABLES + OPTIONAL_DERIVED_DB_TABLES:
            result["table_counts"][table] = _query_int(conn, f"SELECT count(*) FROM {table}")
    except sqlite3.Error as exc:
        result["error"] = str(exc)
    finally:
        conn.close()
    return result


def _query_int(conn: sqlite3.Connection, sql: str) -> int:
    row = conn.execute(sql).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def _resolve_db_path(run_dir: Path, summary: dict[str, Any] | None) -> Path:
    if isinstance(summary, dict) and summary.get("database_path"):
        candidate = Path(str(summary["database_path"]))
        if candidate.exists():
            return candidate.resolve()
    collector_dir = run_dir / "collector"
    for candidate in (collector_dir / "trace.responsibility.db", run_dir / "trace.log.responsibility.db"):
        if candidate.exists():
            return candidate.resolve()
    matches = sorted(run_dir.rglob("*.responsibility.db"))
    return matches[0].resolve() if matches else (collector_dir / "trace.responsibility.db").resolve()


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _scenario_id(run_dir: Path, summary: dict[str, Any] | None) -> str:
    if isinstance(summary, dict) and summary.get("scenario_id"):
        return str(summary["scenario_id"])
    scenario_file = run_dir / ".scenario_id"
    try:
        return scenario_file.read_text(encoding="utf-8").strip()
    except OSError:
        return run_dir.name


def _file_sha256(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return "missing"
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _git_commit() -> str:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return proc.stdout.strip()


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", action="append", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--collector-binary", type=Path, default=DEFAULT_COLLECTOR_BINARY)
    parser.add_argument("--runner", default="compare/scripts/build_backend_capture_health.py")
    args = parser.parse_args()
    artifact = build_backend_capture_health(
        run_dirs=args.run_dir,
        output_path=args.output,
        collector_binary=args.collector_binary,
        runner=args.runner,
    )
    print(f"Wrote {args.output} ({artifact['status']})")


if __name__ == "__main__":
    main()
