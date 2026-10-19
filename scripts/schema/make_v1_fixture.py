#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Generate collector/tests/fixtures/schema/live-v1.db.

The upgrade contract needs a real v1 database in version control, not one that
is synthesized by the code under test (a bug in the code would synthesize a
matching fixture). This script extracts the frozen DDL from
collector/src/responsibility/persistence/schema.rs and inserts a small,
deterministic data set.

The fixture is stamped `user_version = 1` and `application_id = 0`, which is
exactly what the historical writer produced: the version table existed, the
file header did not. Regenerating it is only necessary if the v1 DDL is edited,
which the frozen-DDL rule forbids.

Usage: python3 scripts/schema/make_v1_fixture.py
"""

import pathlib
import re
import sqlite3

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCHEMA_RS = ROOT / "collector/src/responsibility/persistence/schema.rs"
OUT = ROOT / "collector/tests/fixtures/schema/live-v1.db"


def extract_v1_ddl() -> str:
    source = SCHEMA_RS.read_text(encoding="utf-8")
    marker = "pub const SCHEMA_V1_DDL: &str = \""
    start = source.index(marker) + len(marker)
    end = source.index('";', start)
    return source[start:end]


def main() -> None:
    ddl = extract_v1_ddl()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    if OUT.exists():
        OUT.unlink()

    conn = sqlite3.connect(OUT)
    conn.executescript(ddl)

    run_id = "fixture-run-1"
    conn.execute(
        "INSERT INTO runs (run_id, started_at_ns, ended_at_ns, scenario_id, mode, collector_version)"
        " VALUES (?, 1000, 2000, 'fixture-scenario', 'record', 'fixture')",
        (run_id,),
    )
    for pid, start, parent in ((100, 1000, 1), (200, 2000, 100)):
        conn.execute(
            "INSERT INTO process_events (pid, start_time_ns, parent_pid, parent_start_time_ns,"
            " comm, timestamp_ns, run_id) VALUES (?, ?, ?, ?, 'claude', ?, ?)",
            (pid, start, parent, 900, start + 10, run_id),
        )
        conn.execute(
            "INSERT INTO tls_sessions (tls_session_id, pid, start_time_ns, timestamp_ns, run_id)"
            " VALUES (?, ?, ?, ?, ?)",
            (f"tls-{pid}", pid, start, start + 20, run_id),
        )
    for index, pid in enumerate((100, 200)):
        conn.execute(
            "INSERT INTO intents (intent_id, pid, process_start_time_ns, message_id, tool_name,"
            " tool_args_json, intent_class, parse_state, primary_matching_eligible,"
            " authority_source, timestamp_ns, run_id)"
            " VALUES (?, ?, ?, ?, ?, '[]', 'SideEffect', 'Complete', 1,"
            " 'RequestHistoryAssistantToolUse', ?, ?)",
            (
                f"intent-{index}",
                pid,
                1000 if pid == 100 else 2000,
                f"msg-{index}",
                "Bash",
                1100 + index,
                run_id,
            ),
        )
        conn.execute(
            "INSERT INTO actions (action_id, pid, process_start_time_ns, action_type,"
            " normalized_args_json, derivation_mode, signal_class, timestamp_ns, run_id)"
            " VALUES (?, ?, ?, 'ProcessExec', '[]', 'Direct', 'HighSignal', ?, ?)",
            (
                f"action-{index}",
                pid,
                1000 if pid == 100 else 2000,
                1150 + index,
                run_id,
            ),
        )
    conn.execute(
        "INSERT INTO correlation_evidence (evidence_id, intent_id, action_id, match_level,"
        " confidence, matched_fields_json, phase, timestamp_ns, run_id)"
        " VALUES ('ev-0', 'intent-0', 'action-0', 'L1Exact', 0.9, '{}', 'Provisional', 1200, ?)",
        (run_id,),
    )
    conn.execute(
        "INSERT INTO provisional_identities (agent_id, process_ids_json, primary_signal,"
        " confidence, run_id) VALUES ('agent-provisional', '[100]', 'TlsSessionNew', 0.6, ?)",
        (run_id,),
    )
    conn.execute(
        "INSERT INTO agent_identities (agent_id, process_ids_json, message_ids_json,"
        " tls_sessions_json, confidence, provisional_refs_json, run_id)"
        " VALUES ('agent-authoritative', '[100,200]', '[]', '[]', 'Authoritative', '[]', ?)",
        (run_id,),
    )
    for pid, start in ((100, 1000), (200, 2000)):
        conn.execute(
            "INSERT INTO identity_process_map (agent_id, pid, start_time_ns, run_id)"
            " VALUES ('agent-authoritative', ?, ?, ?)",
            (pid, start, run_id),
        )
    conn.execute(
        "INSERT INTO graph_nodes (node_id, node_type, agent_id, evidence_refs_json,"
        " timestamp_ns, phase, run_id) VALUES ('node-agent', 'Agent', 'agent-authoritative',"
        " '[]', 1300, 'Provisional', ?)",
        (run_id,),
    )
    conn.execute(
        "INSERT INTO graph_snapshots (snapshot_id, timestamp_ns, nodes_json, edges_json,"
        " metrics_json, run_id) VALUES ('snap-1', 1400, '[]', '[]', '{}', ?)",
        (run_id,),
    )
    conn.execute(
        "INSERT INTO responsibility_alerts (alert_id, timestamp_ns, display_profile, decision,"
        " severity, confidence, authority, reasoning, title, summary, primary_agent_id,"
        " involved_agents_json, engine_section_json, timeline_json, graph_metrics_json,"
        " phase, run_id) VALUES ('alert-1', 1500, 'SafeBaseline', 'Safe', 'Info', 0.8,"
        " 'Supporting', 'fixture', 'fixture alert', 'fixture summary', 'agent-authoritative',"
        " '[]', '{}', '[]', '{}', 'Provisional', ?)",
        (run_id,),
    )

    conn.commit()
    # The historical writer never stamped the file header. Keep it that way so
    # the adoption path is exercised.
    conn.execute("PRAGMA user_version = 1")
    conn.execute("PRAGMA application_id = 0")
    conn.execute("VACUUM")
    conn.close()
    print(f"wrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
