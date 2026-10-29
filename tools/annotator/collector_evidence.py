# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Freeze a deterministic, read-only SQLite evidence export for annotation.

Actions, claims, alerts, intents and identity rows are annotation evidence;
the exporter never derives labels. Contents are copied into a bundle source so
the annotator can inspect and cite them after the original database moves.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

SCHEMA = "mtagentlens.collector-evidence/v1"
SOURCE_KIND = "raw_capture"
PRODUCER = "collector-sqlite-export/1"

# Short row attribute that makes action/decision kinds filterable in the UI
# without reading every preview.
_DETAIL_KEYS = {
    "responsibility_alerts": "decision",
    "responsibility_claims": "claim_type",
    "policy_evaluations": "boundary",
    "intents": "tool_name",
    "process_events": "comm",
    "delegation_records": "reason",
    "subagent_boundaries": "reason",
    "actions": "action_type",
}

# Ordered so exports are byte-for-byte reproducible for the same database.
_TABLES = (
    ("responsibility_alerts", "alert_id", "timestamp_ns"),
    ("responsibility_claims", "claim_id", None),
    ("responsibility_tracebacks", "traceback_id", None),
    ("policy_evaluations", "policy_id", None),
    ("intents", "intent_id", "timestamp_ns"),
    ("delegation_records", "delegation_id", "timestamp_ns"),
    ("subagent_boundaries", "boundary_id", None),
    ("agent_identities", "agent_id", None),
    ("provisional_identities", "agent_id", None),
    ("identity_process_beliefs", "belief_id", "observed_at_ns"),
    ("message_bindings", "binding_id", "timestamp_ns"),
    ("tls_sessions", "tls_session_id", "timestamp_ns"),
    ("process_events", "process_event_id", "timestamp_ns"),
    ("actions", "action_id", "timestamp_ns"),
)


def _connect_readonly(path: Path) -> sqlite3.Connection:
    if path.is_symlink() or not path.is_file():
        raise ValueError("collector database must be a regular non-symlink file")
    wal = path.with_name(path.name + "-wal")
    if wal.exists() and wal.stat().st_size:
        raise ValueError("collector database has an unbound WAL; finalize it first")
    return sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')]


def _timestamp_column(columns: list[str], table: str, preferred: str | None) -> str | None:
    candidates = [preferred] if preferred else []
    candidates += ["timestamp_ns", "observed_at_ns", "created_at"]
    return next((name for name in candidates if name and name in columns), None)


def _ordered_rows(conn: sqlite3.Connection, table: str, primary: str,
                  timestamp: str | None) -> list[dict]:
    columns = _columns(conn, table)
    if primary not in columns:
        raise ValueError(f"collector table {table!r} lacks its primary identifier")
    order = [primary]
    if timestamp and timestamp != primary:
        order.append(timestamp)
    query = f'SELECT * FROM "{table}" ORDER BY ' + ", ".join(f'"{name}"' for name in order)
    return [dict(row) for row in conn.execute(query)]


def export_collector_evidence(db_path: Path, out_path: Path, *,
                              source_id: str = "collector") -> tuple[Path, list[dict], list[str]]:
    """Write deterministic JSONL rows; return anchors and declared actor ids."""
    db_path, out_path = Path(db_path), Path(out_path)
    with closing(_connect_readonly(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "runs" not in tables:
            raise ValueError("collector database has no runs table")
        runs = [dict(row) for row in conn.execute("SELECT * FROM runs")]
        if len(runs) != 1:
            raise ValueError("collector database must contain exactly one run")
        run = runs[0]
        run_id = run.get("run_id")
        if not isinstance(run_id, str) or not run_id:
            raise ValueError("collector run has no run id")
        lines: list[dict] = [{"kind": "run_summary", "table": "runs",
                              "primary_id": run_id, "row": run}]
        declared_actors: set[str] = set()
        for table, primary, preferred_timestamp in _TABLES:
            if table not in tables:
                continue
            columns = _columns(conn, table)
            if "run_id" in columns:
                count = conn.execute(f'SELECT COUNT(*) FROM "{table}" WHERE run_id IS NOT ?', (run_id,)).fetchone()[0]
                if count:
                    raise ValueError(f"collector table {table!r} mixes run ids")
            timestamp = _timestamp_column(columns, table, preferred_timestamp)
            for row in _ordered_rows(conn, table, primary, timestamp):
                lines.append({"kind": "row", "table": table,
                              "primary_id": row[primary], "row": row})
                for key in ("agent_id", "from_agent_id", "to_agent_id",
                            "parent_agent_id", "child_agent_id"):
                    value = row.get(key)
                    if isinstance(value, str) and value:
                        declared_actors.add(value)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    events, offset = [], 0
    with out_path.open("wb") as stream:
        for line in lines:
            encoded = (json.dumps(line, sort_keys=True, ensure_ascii=False, allow_nan=False,
                                  separators=(",", ":")) + "\n").encode("utf-8")
            stream.write(encoded)
            row = line["row"]
            primary = line["primary_id"]
            event_id = f"{line['table']}:{primary}"
            timestamp = next((row[key] for key in ("timestamp_ns", "observed_at_ns", "started_at_ns",
                                                    "created_at") if isinstance(row.get(key), int)), None)
            events.append({
                "event_id": event_id,
                "source_id": source_id,
                "record_id": str(primary),
                "timestamp": str(timestamp if timestamp is not None else run.get("started_at_ns", 0)),
                "clock_domain": "collector-unix-ns",
                "derivation": ("collector sqlite " + line["table"] +
                               (" " + str(row[_DETAIL_KEYS[line["table"]]])
                                if line["table"] in _DETAIL_KEYS and row.get(_DETAIL_KEYS[line["table"]])
                                else "") + " row"),
                "byte_offset": offset,
                "byte_length": len(encoded),
                "record_sha256": hashlib.sha256(encoded).hexdigest(),
            })
            offset += len(encoded)
    if len({event["event_id"] for event in events}) != len(events):
        out_path.unlink(missing_ok=True)
        raise ValueError("collector evidence produced duplicate event ids")
    return out_path, events, sorted(declared_actors)
