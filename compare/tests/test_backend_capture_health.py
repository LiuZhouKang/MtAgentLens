from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from compare.scripts.audit.audit_paper_experiment_readiness import _validate_backend_health_artifact
from compare.scripts.build.build_backend_capture_health import build_backend_capture_health


def test_backend_health_builder_blocks_without_machine_readable_counters(tmp_path: Path) -> None:
    run_dir = _make_run_dir(tmp_path, trace_err="Persistence: snapshots w=1 s=0 | actions w=1 f=0\n")
    collector_binary = tmp_path / "collector-bin"
    collector_binary.write_bytes(b"collector")
    output = tmp_path / "capture_health.json"

    artifact = build_backend_capture_health(
        run_dirs=[run_dir],
        output_path=output,
        collector_binary=collector_binary,
        runner="unit-test",
    )

    assert artifact["status"] == "blocked"
    assert artifact["checks"]["persistence_lost_count_zero"]["status"] == "fail"
    assert artifact["checks"]["tls_drop_count_checked"]["status"] == "fail"
    assert artifact["checks"]["process_drop_count_checked"]["status"] == "fail"
    assert _validate_backend_health_artifact(output)["valid"] is False


def test_backend_health_builder_completes_with_full_real_run_contract(tmp_path: Path) -> None:
    trace_err = "\n".join(
        [
            '{"event":"PERSISTENCE_STATS","lost_persist_count":0}',
            '{"event":"TLS_RINGBUF_STATS","dropped_events":0}',
            '{"event":"PROCESS_RINGBUF_STATS","dropped_events":0}',
        ]
    )
    run_dir = _make_run_dir(tmp_path, trace_err=trace_err)
    collector_binary = tmp_path / "collector-bin"
    collector_binary.write_bytes(b"collector")
    output = tmp_path / "capture_health.json"

    artifact = build_backend_capture_health(
        run_dirs=[run_dir],
        output_path=output,
        collector_binary=collector_binary,
        runner="unit-test",
    )

    assert artifact["status"] == "completed"
    assert artifact["checks"]["collector_started"]["status"] == "pass"
    assert artifact["checks"]["db_nonempty_required_tables"]["status"] == "pass"
    assert "message_bindings" in artifact["runs"][0]["db"]["table_counts"]
    assert artifact["counters"]["persistence_lost_count"] == 0
    assert artifact["counters"]["tls_drop_count"] == 0
    assert artifact["counters"]["process_drop_count"] == 0
    assert _validate_backend_health_artifact(output)["valid"] is True


def test_backend_health_builder_parses_json_counters_inside_collector_log_prefix(tmp_path: Path) -> None:
    trace_err = "\n".join(
        [
            '[INFO] mtagentlens::accountability {"event":"PERSISTENCE_STATS","lost_persist_count":0}',
            '[INFO] mtagentlens::framework [SSL] STDERR: {"event":"RINGBUF_STATS","dropped_events":0}',
            '[INFO] mtagentlens::framework [PROCESS] STDERR: {"event":"PROCESS_RINGBUF_STATS","dropped_events":0}',
        ]
    )
    run_dir = _make_run_dir(tmp_path, trace_err=trace_err)
    collector_binary = tmp_path / "collector-bin"
    collector_binary.write_bytes(b"collector")

    artifact = build_backend_capture_health(
        run_dirs=[run_dir],
        output_path=tmp_path / "capture_health.json",
        collector_binary=collector_binary,
        runner="unit-test",
    )

    assert artifact["status"] == "completed"
    assert artifact["counters"]["persistence_lost_count"] == 0
    assert artifact["counters"]["tls_drop_count"] == 0
    assert artifact["counters"]["process_drop_count"] == 0


def _make_run_dir(tmp_path: Path, *, trace_err: str) -> Path:
    run_dir = tmp_path / "run-1"
    collector_dir = run_dir / "collector"
    collector_dir.mkdir(parents=True)
    db_path = collector_dir / "collector.responsibility.db"
    _write_minimal_responsibility_db(db_path)
    (collector_dir / "collector.log").write_text(trace_err, encoding="utf-8")
    (collector_dir / "trace.pcap").write_bytes(b"pcap")
    (run_dir / ".scenario_id").write_text("demo", encoding="utf-8")
    (run_dir / "summary.json").write_text(
        json.dumps(
            {
                "run_id": "run-1",
                "scenario_id": "demo",
                "success": True,
                "database_path": str(db_path),
                "collector_health": {
                    "enabled": True,
                    "status": "completed",
                    "stage": "stop",
                    "stop_status": "stopped",
                    "is_running_after_stop": False,
                    "pid": 1234,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return run_dir


def _write_minimal_responsibility_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        for table in ("runs", "process_events", "tls_sessions", "actions", "message_bindings"):
            conn.execute(f"CREATE TABLE {table} (id TEXT)")
            if table != "message_bindings":
                conn.execute(f"INSERT INTO {table} VALUES ('x')")
        conn.execute("CREATE TABLE schema_version (version INTEGER)")
        conn.execute("INSERT INTO schema_version VALUES (2)")
        conn.commit()
    finally:
        conn.close()
