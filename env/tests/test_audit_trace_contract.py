"""Audit contract: one canonical capture parser, fail-closed admission."""
import json
import sqlite3
import subprocess
from pathlib import Path

from mtagentlens_benchmark.engine.collector_manager import (
    _parse_runtime_metrics_from_logs,
)

SCRIPT = Path(__file__).parents[1] / "scripts/audit_trace.sh"

TABLES = {
    "responsibility_alerts": "CREATE TABLE responsibility_alerts (decision TEXT, severity TEXT, confidence REAL)",
    "responsibility_claims": "CREATE TABLE responsibility_claims (claim_type TEXT)",
    "subagent_boundaries": "CREATE TABLE subagent_boundaries (reason TEXT)",
    "agent_identities": "CREATE TABLE agent_identities (id INTEGER)",
    "tls_sessions": "CREATE TABLE tls_sessions (id INTEGER)",
    "process_events": "CREATE TABLE process_events (id INTEGER)",
    "responsibility_tracebacks": "CREATE TABLE responsibility_tracebacks (id INTEGER)",
    "graph_nodes": "CREATE TABLE graph_nodes (id INTEGER)",
    "graph_edges": "CREATE TABLE graph_edges (id INTEGER)",
}


def _run(run_dir: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(SCRIPT), str(run_dir), "w2-contract-test", *extra],
        capture_output=True,
        text=True,
        timeout=60,
    )


def _make_run(
    run_dir: Path,
    *,
    health: dict | None = None,
    log_rows: list[dict] | None = None,
    trace_text: str = "",
    drop_table: str | None = None,
    alerts: int = 0,
    compact: bool = True,
) -> None:
    collector = run_dir / "collector"
    collector.mkdir(parents=True)
    db = collector / "collector.responsibility.db"
    with sqlite3.connect(db) as conn:
        for name, sql in TABLES.items():
            if name != drop_table:
                conn.execute(sql)
        conn.execute(
            "INSERT INTO tls_sessions VALUES (1)" if drop_table != "tls_sessions" else
            "SELECT 1"
        )
        conn.execute(
            "INSERT INTO process_events VALUES (1)" if drop_table != "process_events" else
            "SELECT 1"
        )
        for _ in range(alerts):
            conn.execute("INSERT INTO responsibility_alerts VALUES ('deny', 'high', 0.9)")
    if health is not None:
        (collector / "collector_health.json").write_text(
            json.dumps(health, indent=2), encoding="utf-8"
        )
    rows = log_rows if log_rows is not None else [
        {"event": "TLS_RINGBUF_STATS", "dropped_events": 0},
        {"event": "PROCESS_RINGBUF_STATS", "dropped_events": 0},
    ]
    separators = (",", ":") if compact else None
    (collector / "collector.log").write_text(
        "\n".join(json.dumps(row, separators=separators) for row in rows) + "\n",
        encoding="utf-8",
    )
    (collector / "collector").write_text(trace_text, encoding="utf-8")


def test_missing_run_dir_is_an_input_error(tmp_path):
    result = _run(tmp_path / "does-not-exist")
    assert result.returncode == 3, result.stdout + result.stderr
    assert "BLOCKED" not in result.stdout


def test_empty_run_is_blocked(tmp_path):
    run_dir = tmp_path / "empty-run"
    run_dir.mkdir()
    result = _run(run_dir)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "BLOCKED" in result.stdout


def test_benign_zero_alert_run_with_configured_sources_passes(tmp_path):
    run_dir = tmp_path / "benign"
    _make_run(
        run_dir,
        health={"configured_capture_sources": ["process"]},
        log_rows=[{"event": "PROCESS_RINGBUF_STATS", "dropped_events": 0}],
        alerts=0,
    )
    result = _run(run_dir)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ADMIT" in result.stdout
    assert "BLOCKED" not in result.stdout


def test_zero_observations_do_not_establish_capture_coverage(tmp_path):
    run_dir = tmp_path / "silent-source"
    _make_run(run_dir, health={"configured_capture_sources": ["process"]},
              log_rows=[{"event": "PROCESS_RINGBUF_STATS", "dropped_events": 0}])
    with sqlite3.connect(run_dir / "collector" / "collector.responsibility.db") as conn:
        conn.execute("DELETE FROM process_events")
    result = _run(run_dir)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "coverage" in result.stdout.lower()


def test_stdio_stats_are_not_payload_observations(tmp_path):
    run_dir = tmp_path / "stdio"
    _make_run(run_dir, health={"configured_capture_sources": ["stdio"]},
              log_rows=[], trace_text=json.dumps({"source": "stdio", "data": {
                  "direction": "STATS", "dropped_events": 0,
              }}) + "\n")
    assert _run(run_dir).returncode == 2
    trace = run_dir / "collector" / "collector"
    with trace.open("a") as output:
        output.write(json.dumps({"source": "stdio", "data": {
            "direction": "WRITE", "data": "synthetic output", "copy_failed": False,
        }}) + "\n")
    result = _run(run_dir)
    assert result.returncode == 0, result.stdout + result.stderr


def test_absent_enabled_source_final_is_blocked(tmp_path):
    run_dir = tmp_path / "missing-source"
    _make_run(
        run_dir,
        health={"configured_capture_sources": ["tls", "process"]},
        log_rows=[{"event": "TLS_RINGBUF_STATS", "dropped_events": 0}],
    )
    result = _run(run_dir)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "BLOCKED" in result.stdout


def test_periodic_only_stats_are_not_final_evidence(tmp_path):
    run_dir = tmp_path / "periodic"
    _make_run(
        run_dir,
        health={"configured_capture_sources": ["tls"]},
        log_rows=[{"event": "RINGBUF_STATS", "dropped_events": 0}],
    )
    result = _run(run_dir)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "BLOCKED" in result.stdout


def test_missing_or_bad_counter_is_blocked(tmp_path):
    for name, row in [
        ("missing", {"event": "TLS_RINGBUF_STATS"}),
        ("bad", {"event": "TLS_RINGBUF_STATS", "dropped_events": -1}),
    ]:
        run_dir = tmp_path / name
        _make_run(
            run_dir,
            health={"configured_capture_sources": ["tls"]},
            log_rows=[row],
        )
        result = _run(run_dir)
        assert result.returncode == 2, (name, result.stdout + result.stderr)
        assert "BLOCKED" in result.stdout, name


def test_compact_and_spaced_json_are_equivalent(tmp_path):
    for name, compact in [("compact", True), ("spaced", False)]:
        run_dir = tmp_path / name
        _make_run(
            run_dir,
            health={"configured_capture_sources": ["tls", "process"]},
            compact=compact,
        )
        result = _run(run_dir)
        assert result.returncode == 0, (name, result.stdout + result.stderr)


def test_readonly_missing_table_is_blocked_not_zero(tmp_path):
    run_dir = tmp_path / "missing-table"
    _make_run(
        run_dir,
        health={"configured_capture_sources": ["process"]},
        log_rows=[{"event": "PROCESS_RINGBUF_STATS", "dropped_events": 0}],
        drop_table="graph_edges",
    )
    result = _run(run_dir)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "BLOCKED" in result.stdout


def test_audit_does_not_write_the_database(tmp_path):
    run_dir = tmp_path / "readonly"
    _make_run(run_dir, health={"configured_capture_sources": ["process"]},
              log_rows=[{"event": "PROCESS_RINGBUF_STATS", "dropped_events": 0}])
    db = run_dir / "collector" / "collector.responsibility.db"
    before = db.read_bytes()
    result = _run(run_dir)
    assert result.returncode == 0, result.stdout + result.stderr
    assert db.read_bytes() == before


def test_missing_config_requires_explicit_sources(tmp_path):
    run_dir = tmp_path / "no-config"
    _make_run(run_dir)
    blocked = _run(run_dir)
    assert blocked.returncode == 2, blocked.stdout + blocked.stderr
    assert "--sources" in blocked.stdout + blocked.stderr

    admitted = _run(run_dir, "--sources", "tls,process")
    assert admitted.returncode == 0, admitted.stdout + admitted.stderr


def test_explicit_sources_cannot_hide_an_enabled_persisted_source(tmp_path):
    run_dir = tmp_path / "override"
    _make_run(run_dir, health={"configured_capture_sources": ["tls", "process"]},
              log_rows=[{"event": "PROCESS_RINGBUF_STATS", "dropped_events": 0}])
    result = _run(run_dir, "--sources", "process")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "conflict" in result.stdout.lower()


def test_malformed_health_config_fails_safely(tmp_path):
    for name, health in [
        ("string", {"configured_capture_sources": "tls"}),
        ("unknown", {"configured_capture_sources": ["tls", "bogus"]}),
        ("trace-args", {"trace_args": "--ssl=true"}),
    ]:
        run_dir = tmp_path / name
        _make_run(run_dir, health=health)
        result = _run(run_dir)
        assert result.returncode == 2, (name, result.stdout + result.stderr)
        assert "BLOCKED" in result.stdout, name


def test_shell_verdict_agrees_with_canonical_parser(tmp_path):
    cases = [
        ("complete", ["tls", "process"], [
            {"event": "TLS_RINGBUF_STATS", "dropped_events": 0},
            {"event": "PROCESS_RINGBUF_STATS", "dropped_events": 0},
        ], True),
        ("incomplete", ["tls", "process"], [
            {"event": "PROCESS_RINGBUF_STATS", "dropped_events": 0},
        ], False),
    ]
    for name, sources, rows, expected_complete in cases:
        run_dir = tmp_path / name
        _make_run(
            run_dir,
            health={"configured_capture_sources": sources},
            log_rows=rows,
        )
        metrics = _parse_runtime_metrics_from_logs(
            (run_dir / "collector" / "collector.log").read_text(encoding="utf-8"),
            run_dir / "collector" / "collector",
            set(sources),
        )
        assert metrics["capture_stats_complete"] is expected_complete, name
        result = _run(run_dir)
        assert (result.returncode == 0) is expected_complete, (
            name, result.stdout + result.stderr,
        )
