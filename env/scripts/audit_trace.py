#!/usr/bin/env python3
"""Audit one benchmark run with the canonical capture parser.

Exit codes: 0 = admissible, 2 = blocked, 3 = invalid invocation/input.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "env" / "src"))

from mtagentlens_benchmark.engine.collector_manager import (  # noqa: E402
    _parse_runtime_metrics_from_logs,
    _recover_configured_capture_sources,
)

REQUIRED_TABLES = (
    "responsibility_alerts",
    "responsibility_claims",
    "subagent_boundaries",
    "agent_identities",
    "tls_sessions",
    "process_events",
    "responsibility_tracebacks",
    "graph_nodes",
    "graph_edges",
)


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # exit 3 for invalid input, not 2
        self.print_usage(sys.stderr)
        print(f"INPUT ERROR: {message}", file=sys.stderr)
        raise SystemExit(3)


def _source_list(value: str) -> set[str]:
    names = {part.strip() for part in value.split(",") if part.strip()}
    if not names:
        raise argparse.ArgumentTypeError("--sources must not be empty")
    invalid = names - {"tls", "process", "stdio"}
    if invalid:
        raise argparse.ArgumentTypeError(
            f"unknown capture source(s): {', '.join(sorted(invalid))}"
        )
    return names


def _load_health(run_dir: Path) -> dict:
    path = run_dir / "collector" / "collector_health.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"cannot read collector_health.json: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("collector_health.json must contain a JSON object")
    return data


def _read_db(db_path: Path) -> dict[str, int]:
    if not db_path.is_file() or db_path.stat().st_size <= 0:
        raise ValueError(f"responsibility database missing or empty: {db_path}")
    conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        integrity = conn.execute("PRAGMA quick_check").fetchone()
        if integrity is None or str(integrity[0]).lower() != "ok":
            raise ValueError(f"responsibility database failed integrity check: {integrity}")
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        missing = [name for name in REQUIRED_TABLES if name not in tables]
        if missing:
            raise ValueError(
                "responsibility database is missing table(s): " + ", ".join(missing)
            )
        return {
            name: int(conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0])
            for name in REQUIRED_TABLES
        }
    finally:
        conn.close()


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc


def main() -> int:
    parser = _Parser(description="Audit a run with the canonical capture parser.")
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("scenario_id")
    parser.add_argument(
        "--sources",
        type=_source_list,
        default=None,
        help="Expected capture sources for ad hoc runs, e.g. tls,process,stdio",
    )
    args = parser.parse_args()

    run_dir: Path = args.run_dir
    if not run_dir.is_dir():
        print(f"INPUT ERROR: run directory not found: {run_dir}", file=sys.stderr)
        return 3

    collector_dir = run_dir / "collector"
    db_path = collector_dir / "collector.responsibility.db"
    log_path = collector_dir / "collector.log"
    trace_path = collector_dir / "collector"

    print("═" * 59)
    print(f"  TRACE AUDIT: {args.scenario_id}")
    print(f"  DB: {db_path}")
    print("═" * 59)

    failures: list[str] = []

    print("\n── 数据库完整性（只读） ──")
    counts: dict[str, int] = {}
    try:
        counts = _read_db(db_path)
    except (sqlite3.Error, OSError, ValueError) as exc:
        print(f"  ❌ {exc}")
        failures.append(str(exc))
    else:
        for name in REQUIRED_TABLES:
            print(f"    {name}: {counts[name]}")

    print("\n── 采集源配置 ──")
    expected: set[str] | None = None
    try:
        persisted = _recover_configured_capture_sources(_load_health(run_dir))
        if args.sources is not None and persisted is not None and args.sources != persisted:
            raise ValueError("--sources conflicts with persisted capture configuration")
        expected = persisted if persisted is not None else args.sources
        if expected is None:
            failures.append("expected capture sources unknown; pass --sources tls,process,stdio")
            print("    expected sources unknown (no persisted config)")
        else:
            origin = "collector_health.json" if persisted is not None else "--sources"
            print(f"    expected ({origin}): {', '.join(sorted(expected))}")
    except (OSError, ValueError) as exc:
        print(f"    malformed health config: {exc}")
        failures.append(f"malformed health config: {exc}")

    log_text = ""
    metrics: dict | None = None
    if expected is not None and not failures:
        print("\n── 采集计数（canonical parser） ──")
        try:
            log_text = _read_text(log_path)
        except ValueError as exc:
            print(f"  ❌ {exc}")
            failures.append(str(exc))
        else:
            metrics = _parse_runtime_metrics_from_logs(log_text, trace_path, expected)
            drops = metrics.get("ring_buffer_drops")
            print(f"    ring_buffer_drops: {drops}")
            print(f"    capture_stats_complete: {metrics.get('capture_stats_complete')}")
            print(f"    capture_loss_free: {metrics.get('capture_loss_free')}")
            for source in sorted(expected):
                if source == "tls":
                    observed = max(counts.get("tls_sessions", 0),
                                   metrics.get("ssl_events", 0))
                elif source == "process":
                    observed = counts.get("process_events", 0)
                else:
                    observed = metrics.get("stdio_events", 0)
                print(f"    {source}: observed={observed}")
                if not observed:
                    failures.append(f"capture coverage unverified for {source}: no observations")
            if not expected:
                failures.append("capture coverage unverified: no capture sources configured")
            if not metrics.get("capture_stats_complete"):
                failures.append(
                    "capture counters incomplete or malformed; a configured source "
                    "has no valid final stats"
                )
            elif not metrics.get("capture_loss_free"):
                failures.append(f"capture lost events: dropped_events={drops}")

    print("\n── 运行信息 ──")
    phase2 = "PHASE2" in log_text if log_text else False
    print(f"    PHASE2 finalization: {'seen' if phase2 else 'not seen'}")
    if log_text:
        errors = sum(
            marker in log_text for marker in ("ERROR", "FATAL", "panic")
        )
        print(f"    collector ERROR/FATAL/panic markers present: {errors}")

    print("\n" + "═" * 59)
    if failures:
        print(f"  VERDICT: BLOCKED ({len(failures)} issue(s))")
        for issue in failures:
            print(f"    - {issue}")
        print("═" * 59)
        return 2
    print("  VERDICT: ADMIT")
    print("  Capture-quality checks passed; execution and dataset eligibility require separate evidence.")
    print("═" * 59)
    return 0


if __name__ == "__main__":
    sys.exit(main())
