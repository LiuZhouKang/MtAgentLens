// SPDX-License-Identifier: MIT
// Responsibility architecture persistence schema — 27 tables, single clean DDL.

use rusqlite::{Connection, OpenFlags, Result};

// ═══════════════════════════════════════════════════════════════════════════
// SCHEMA_DDL — all 27 CREATE TABLE IF NOT EXISTS statements
// ═══════════════════════════════════════════════════════════════════════════

pub const SCHEMA_DDL: &str = "
-- ═══ Signal layer (3 tables) ═══

CREATE TABLE IF NOT EXISTS process_events (
    process_event_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    pid                 INTEGER NOT NULL,
    start_time_ns       INTEGER NOT NULL,
    parent_pid          INTEGER,
    parent_start_time_ns INTEGER,
    comm                TEXT NOT NULL,
    timestamp_ns        INTEGER NOT NULL,
    run_id              TEXT NOT NULL,
    UNIQUE(pid, start_time_ns)
);
CREATE INDEX IF NOT EXISTS idx_process_events_run ON process_events(run_id);
CREATE INDEX IF NOT EXISTS idx_process_events_ts ON process_events(timestamp_ns);

CREATE TABLE IF NOT EXISTS tls_sessions (
    tls_session_id      TEXT PRIMARY KEY,
    pid                 INTEGER NOT NULL,
    start_time_ns       INTEGER NOT NULL,
    timestamp_ns        INTEGER NOT NULL,
    run_id              TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tls_sessions_run ON tls_sessions(run_id);
CREATE INDEX IF NOT EXISTS idx_tls_sessions_process ON tls_sessions(pid, start_time_ns);

CREATE TABLE IF NOT EXISTS message_bindings (
    binding_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id          TEXT NOT NULL,
    pid                 INTEGER NOT NULL,
    start_time_ns       INTEGER NOT NULL,
    tls_session_id      TEXT,
    binding_type        TEXT NOT NULL,
    timestamp_ns        INTEGER NOT NULL,
    run_id              TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_message_bindings_run ON message_bindings(run_id);
CREATE INDEX IF NOT EXISTS idx_message_bindings_msg ON message_bindings(message_id);

-- ═══ Evidence layer (4 tables) ═══

CREATE TABLE IF NOT EXISTS intents (
    intent_id               TEXT PRIMARY KEY,
    pid                     INTEGER NOT NULL,
    process_start_time_ns   INTEGER NOT NULL,
    message_id              TEXT NOT NULL,
    tool_name               TEXT NOT NULL,
    tool_args_json          TEXT NOT NULL DEFAULT '[]',
    intent_class            TEXT NOT NULL,
    parse_state             TEXT NOT NULL,
    primary_matching_eligible INTEGER NOT NULL DEFAULT 0,
    authority_source        TEXT NOT NULL,
    timestamp_ns            INTEGER NOT NULL,
    run_id                  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_intents_run ON intents(run_id);
CREATE INDEX IF NOT EXISTS idx_intents_ts ON intents(timestamp_ns);
CREATE INDEX IF NOT EXISTS idx_intents_msg ON intents(message_id);

CREATE TABLE IF NOT EXISTS actions (
    action_id               TEXT PRIMARY KEY,
    pid                     INTEGER NOT NULL,
    process_start_time_ns   INTEGER NOT NULL,
    action_type             TEXT NOT NULL,
    normalized_args_json    TEXT NOT NULL DEFAULT '[]',
    derivation_mode         TEXT NOT NULL,
    signal_class            TEXT NOT NULL,
    timestamp_ns            INTEGER NOT NULL,
    run_id                  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_actions_run ON actions(run_id);
CREATE INDEX IF NOT EXISTS idx_actions_ts ON actions(timestamp_ns);
CREATE INDEX IF NOT EXISTS idx_actions_process ON actions(pid, process_start_time_ns);

CREATE TABLE IF NOT EXISTS correlation_evidence (
    evidence_id             TEXT PRIMARY KEY,
    intent_id               TEXT NOT NULL,
    action_id               TEXT NOT NULL,
    match_level             TEXT NOT NULL,
    confidence              REAL NOT NULL,
    matched_fields_json     TEXT NOT NULL DEFAULT '{}',
    derivation_mode         TEXT,
    phase                   TEXT NOT NULL,
    timestamp_ns            INTEGER NOT NULL,
    run_id                  TEXT NOT NULL,
    FOREIGN KEY(intent_id) REFERENCES intents(intent_id),
    FOREIGN KEY(action_id) REFERENCES actions(action_id)
);
CREATE INDEX IF NOT EXISTS idx_evidence_run ON correlation_evidence(run_id);
CREATE INDEX IF NOT EXISTS idx_evidence_ts ON correlation_evidence(timestamp_ns);
CREATE INDEX IF NOT EXISTS idx_evidence_intent ON correlation_evidence(intent_id);
CREATE INDEX IF NOT EXISTS idx_evidence_action ON correlation_evidence(action_id);

CREATE TABLE IF NOT EXISTS non_link_diagnostics (
    diagnostic_id           TEXT PRIMARY KEY,
    intent_id               TEXT,
    action_id               TEXT,
    reason                  TEXT NOT NULL,
    timestamp_ns            INTEGER NOT NULL,
    run_id                  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_non_link_run ON non_link_diagnostics(run_id);
CREATE INDEX IF NOT EXISTS idx_non_link_ts ON non_link_diagnostics(timestamp_ns);

-- ═══ Identity layer (3 tables) ═══

CREATE TABLE IF NOT EXISTS provisional_identities (
    agent_id                TEXT PRIMARY KEY,
    process_ids_json        TEXT NOT NULL DEFAULT '[]',
    primary_signal          TEXT NOT NULL,
    confidence              REAL NOT NULL,
    run_id                  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_provisional_id_run ON provisional_identities(run_id);

CREATE TABLE IF NOT EXISTS agent_identities (
    agent_id                TEXT PRIMARY KEY,
    process_ids_json        TEXT NOT NULL DEFAULT '[]',
    message_ids_json        TEXT NOT NULL DEFAULT '[]',
    tls_sessions_json       TEXT NOT NULL DEFAULT '[]',
    confidence              TEXT NOT NULL,
    provisional_refs_json   TEXT NOT NULL DEFAULT '[]',
    run_id                  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_identities_run ON agent_identities(run_id);

CREATE TABLE IF NOT EXISTS identity_process_map (
    agent_id                TEXT NOT NULL,
    pid                     INTEGER NOT NULL,
    start_time_ns           INTEGER NOT NULL,
    run_id                  TEXT NOT NULL,
    PRIMARY KEY(pid, start_time_ns)
);
CREATE INDEX IF NOT EXISTS idx_identity_process_map_run ON identity_process_map(run_id);
CREATE INDEX IF NOT EXISTS idx_identity_process_map_agent ON identity_process_map(agent_id);

-- ═══ Graph layer (3 tables) ═══

CREATE TABLE IF NOT EXISTS graph_nodes (
    node_id                 TEXT PRIMARY KEY,
    node_type               TEXT NOT NULL,
    agent_id                TEXT,
    resource_json           TEXT,
    evidence_refs_json      TEXT NOT NULL DEFAULT '[]',
    timestamp_ns            INTEGER NOT NULL,
    phase                   TEXT NOT NULL,
    run_id                  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_graph_nodes_run ON graph_nodes(run_id);
CREATE INDEX IF NOT EXISTS idx_graph_nodes_type ON graph_nodes(node_type, run_id);

CREATE TABLE IF NOT EXISTS graph_edges (
    edge_id                 TEXT PRIMARY KEY,
    edge_type               TEXT NOT NULL,
    source_node_id          TEXT NOT NULL,
    target_node_id          TEXT NOT NULL,
    evidence_refs_json      TEXT NOT NULL DEFAULT '[]',
    confidence              REAL NOT NULL DEFAULT 1.0,
    phase                   TEXT NOT NULL,
    run_id                  TEXT NOT NULL,
    FOREIGN KEY(source_node_id) REFERENCES graph_nodes(node_id),
    FOREIGN KEY(target_node_id) REFERENCES graph_nodes(node_id)
);
CREATE INDEX IF NOT EXISTS idx_graph_edges_run ON graph_edges(run_id);
CREATE INDEX IF NOT EXISTS idx_graph_edges_type ON graph_edges(edge_type, run_id);
CREATE INDEX IF NOT EXISTS idx_graph_edges_source ON graph_edges(source_node_id);
CREATE INDEX IF NOT EXISTS idx_graph_edges_target ON graph_edges(target_node_id);

CREATE TABLE IF NOT EXISTS graph_snapshots (
    snapshot_id             TEXT PRIMARY KEY,
    timestamp_ns            INTEGER NOT NULL,
    nodes_json              TEXT NOT NULL DEFAULT '[]',
    edges_json              TEXT NOT NULL DEFAULT '[]',
    metrics_json            TEXT NOT NULL DEFAULT '{}',
    run_id                  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_graph_snapshots_run ON graph_snapshots(run_id);
CREATE INDEX IF NOT EXISTS idx_graph_snapshots_ts ON graph_snapshots(timestamp_ns);

-- ═══ Judgment layer (3 tables) ═══

CREATE TABLE IF NOT EXISTS delegation_records (
    delegation_id           TEXT PRIMARY KEY,
    from_agent_id           TEXT NOT NULL,
    to_agent_id             TEXT NOT NULL,
    parent_pid              INTEGER NOT NULL,
    parent_start_time_ns    INTEGER NOT NULL,
    child_pid               INTEGER NOT NULL,
    child_start_time_ns     INTEGER NOT NULL,
    timestamp_ns            INTEGER NOT NULL,
    confidence              REAL NOT NULL DEFAULT 0.85,
    run_id                  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_delegation_run ON delegation_records(run_id);
CREATE INDEX IF NOT EXISTS idx_delegation_from ON delegation_records(from_agent_id);
CREATE INDEX IF NOT EXISTS idx_delegation_to ON delegation_records(to_agent_id);

CREATE TABLE IF NOT EXISTS context_pollution_events (
    pollution_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source_agent_id         TEXT NOT NULL,
    contaminated_artifact   TEXT NOT NULL,
    propagation_path_json   TEXT NOT NULL DEFAULT '[]',
    detected_at_ns          INTEGER NOT NULL,
    run_id                  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pollution_run ON context_pollution_events(run_id);
CREATE INDEX IF NOT EXISTS idx_pollution_source ON context_pollution_events(source_agent_id);

CREATE TABLE IF NOT EXISTS policy_evaluations (
    policy_id               TEXT NOT NULL,
    boundary                TEXT NOT NULL,
    judgment_json           TEXT NOT NULL DEFAULT '{}',
    subject_agent_id        TEXT NOT NULL,
    resource_json           TEXT,
    evidence_refs_json      TEXT NOT NULL DEFAULT '[]',
    confidence              REAL NOT NULL DEFAULT 0.0,
    reason                  TEXT NOT NULL DEFAULT '',
    run_id                  TEXT NOT NULL,
    PRIMARY KEY(policy_id, subject_agent_id)
);
CREATE INDEX IF NOT EXISTS idx_policy_eval_run ON policy_evaluations(run_id);
CREATE INDEX IF NOT EXISTS idx_policy_eval_boundary ON policy_evaluations(boundary);

-- ═══ Refinement layer (6 tables) ═══

CREATE TABLE IF NOT EXISTS identity_refinements (
    refinement_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    phase1_agent_id         TEXT NOT NULL,
    phase2_agent_id         TEXT NOT NULL,
    refinement_type         TEXT NOT NULL,
    reason                  TEXT NOT NULL DEFAULT '',
    run_id                  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_identity_refine_run ON identity_refinements(run_id);

CREATE TABLE IF NOT EXISTS subagent_boundaries (
    boundary_id             TEXT PRIMARY KEY,
    parent_agent_id         TEXT NOT NULL,
    child_agent_id          TEXT NOT NULL,
    parent_pid              INTEGER NOT NULL,
    parent_start_time_ns    INTEGER NOT NULL,
    child_pid               INTEGER NOT NULL,
    child_start_time_ns     INTEGER NOT NULL,
    reason                  TEXT NOT NULL,
    run_id                  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_subagent_boundaries_run ON subagent_boundaries(run_id);

CREATE TABLE IF NOT EXISTS evidence_refinements (
    refinement_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    phase1_evidence_id      TEXT NOT NULL,
    phase2_evidence_id      TEXT,
    refinement_type         TEXT NOT NULL,
    reason                  TEXT NOT NULL DEFAULT '',
    run_id                  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_evidence_refine_run ON evidence_refinements(run_id);

CREATE TABLE IF NOT EXISTS graph_refinements (
    refinement_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    refinement_type         TEXT NOT NULL,
    affected_nodes_json     TEXT NOT NULL DEFAULT '[]',
    affected_edges_json     TEXT NOT NULL DEFAULT '[]',
    description             TEXT NOT NULL DEFAULT '',
    run_id                  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_graph_refine_run ON graph_refinements(run_id);

CREATE TABLE IF NOT EXISTS classification_refinements (
    refinement_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    phase1_alert_id         TEXT NOT NULL,
    phase2_alert_id         TEXT NOT NULL,
    refinement_type         TEXT NOT NULL,
    reason                  TEXT NOT NULL DEFAULT '',
    score_delta             REAL NOT NULL DEFAULT 0.0,
    run_id                  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_classification_refine_run ON classification_refinements(run_id);

CREATE TABLE IF NOT EXISTS claim_refinements (
    refinement_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    phase1_claim_id         TEXT NOT NULL,
    phase2_claim_id         TEXT NOT NULL,
    refinement_type         TEXT NOT NULL,
    reason                  TEXT NOT NULL DEFAULT '',
    run_id                  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_claim_refine_run ON claim_refinements(run_id);

-- ═══ Claims layer (3 tables) ═══

CREATE TABLE IF NOT EXISTS responsibility_alerts (
    alert_id                TEXT PRIMARY KEY,
    timestamp_ns            INTEGER NOT NULL,
    display_profile         TEXT NOT NULL,
    decision                TEXT NOT NULL,
    severity                TEXT NOT NULL,
    confidence              REAL NOT NULL,
    authority               TEXT NOT NULL,
    reasoning               TEXT NOT NULL DEFAULT '',
    title                   TEXT NOT NULL DEFAULT '',
    summary                 TEXT NOT NULL DEFAULT '',
    action_hint             TEXT,
    primary_agent_id        TEXT NOT NULL,
    involved_agents_json    TEXT NOT NULL DEFAULT '[]',
    engine_section_json     TEXT NOT NULL DEFAULT '{}',
    timeline_json           TEXT NOT NULL DEFAULT '[]',
    graph_metrics_json      TEXT NOT NULL DEFAULT '{}',
    phase                   TEXT NOT NULL,
    provisional_ref         TEXT,
    run_id                  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alerts_run ON responsibility_alerts(run_id);
CREATE INDEX IF NOT EXISTS idx_alerts_ts ON responsibility_alerts(timestamp_ns);
CREATE INDEX IF NOT EXISTS idx_alerts_severity ON responsibility_alerts(severity);
CREATE INDEX IF NOT EXISTS idx_alerts_decision ON responsibility_alerts(decision);

CREATE TABLE IF NOT EXISTS responsibility_claims (
    claim_id                TEXT PRIMARY KEY,
    claim_type              TEXT NOT NULL,
    subject_agent_id        TEXT NOT NULL,
    policy_id               TEXT NOT NULL,
    boundary_id             TEXT NOT NULL,
    claim_state             TEXT NOT NULL,
    severity                TEXT NOT NULL,
    confidence              REAL NOT NULL,
    evidence_refs_json      TEXT NOT NULL DEFAULT '[]',
    competing_explanations_json TEXT NOT NULL DEFAULT '[]',
    traceback_ref           TEXT,
    phase                   TEXT NOT NULL,
    run_id                  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_claims_run ON responsibility_claims(run_id);
CREATE INDEX IF NOT EXISTS idx_claims_subject ON responsibility_claims(subject_agent_id);
CREATE INDEX IF NOT EXISTS idx_claims_state ON responsibility_claims(claim_state);

CREATE TABLE IF NOT EXISTS responsibility_tracebacks (
    traceback_id            TEXT PRIMARY KEY,
    claim_id                TEXT NOT NULL,
    chain_node_ids_json     TEXT NOT NULL DEFAULT '[]',
    chain_edge_ids_json     TEXT NOT NULL DEFAULT '[]',
    root_cause_node_id      TEXT NOT NULL,
    explanation_summary     TEXT NOT NULL DEFAULT '',
    confidence              REAL NOT NULL,
    run_id                  TEXT NOT NULL,
    FOREIGN KEY(claim_id) REFERENCES responsibility_claims(claim_id)
);
CREATE INDEX IF NOT EXISTS idx_tracebacks_run ON responsibility_tracebacks(run_id);
CREATE INDEX IF NOT EXISTS idx_tracebacks_claim ON responsibility_tracebacks(claim_id);

-- ═══ Run layer (2 tables) ═══

CREATE TABLE IF NOT EXISTS runs (
    run_id                  TEXT PRIMARY KEY,
    started_at_ns           INTEGER NOT NULL,
    ended_at_ns             INTEGER,
    scenario_id             TEXT,
    mode                    TEXT,
    collector_version       TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_started ON runs(started_at_ns);

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);
INSERT OR IGNORE INTO schema_version(version) VALUES (1);
";

// ═══════════════════════════════════════════════════════════════════════════
// run_migrations — apply schema and future version gates
// ═══════════════════════════════════════════════════════════════════════════

/// Apply the responsibility schema. Idempotent — safe to call on an existing DB.
pub fn run_migrations(conn: &Connection) -> Result<()> {
    let version: i64 = conn
        .query_row(
            "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1",
            [],
            |r| r.get(0),
        )
        .unwrap_or(0);

    if version < 1 {
        conn.execute_batch(SCHEMA_DDL)?;
    }

    // Future version gates go here. Example:
    // if version < 2 {
    //     conn.execute_batch(SCHEMA_V2)?;
    // }

    Ok(())
}

// ═══════════════════════════════════════════════════════════════════════════
// open_* helpers
// ═══════════════════════════════════════════════════════════════════════════

/// Open a responsibility DB with WAL mode + foreign keys, running migrations.
pub fn open_responsibility_db(path: &str) -> Result<Connection> {
    let conn = Connection::open(path)?;
    conn.execute_batch("PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON;")?;
    run_migrations(&conn)?;
    Ok(conn)
}

/// Open a responsibility DB in read-only mode (for PersistenceReader).
pub fn open_responsibility_db_read_only(path: &str) -> Result<Connection> {
    let conn = Connection::open_with_flags(
        path,
        OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )?;
    // read_uncommitted=0 ensures snapshot isolation on the WAL database.
    // journal_mode cannot be changed on a read-only connection, but the
    // writer already sets WAL mode on the database file.
    let _ = conn.execute_batch("PRAGMA read_uncommitted=0;");
    Ok(conn)
}

// Backward-compatible aliases for existing callers (sqlite_writer.rs)
/// Alias for open_responsibility_db — backward compatibility.
pub fn open_db(path: &str) -> Result<Connection> {
    open_responsibility_db(path)
}

/// Alias for open_responsibility_db_read_only — backward compatibility.
pub fn open_db_read_only(path: &str) -> Result<Connection> {
    open_responsibility_db_read_only(path)
}

// ═══════════════════════════════════════════════════════════════════════════
// Tests
// ═══════════════════════════════════════════════════════════════════════════

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_schema_creates_all_27_tables() {
        let conn = Connection::open_in_memory().unwrap();
        run_migrations(&conn).unwrap();

        let tables = [
            // Signal layer
            "process_events",
            "tls_sessions",
            "message_bindings",
            // Evidence layer
            "intents",
            "actions",
            "correlation_evidence",
            "non_link_diagnostics",
            // Identity layer
            "provisional_identities",
            "agent_identities",
            "identity_process_map",
            // Graph layer
            "graph_nodes",
            "graph_edges",
            "graph_snapshots",
            // Judgment layer
            "delegation_records",
            "context_pollution_events",
            "policy_evaluations",
            // Refinement layer
            "identity_refinements",
            "subagent_boundaries",
            "evidence_refinements",
            "graph_refinements",
            "classification_refinements",
            "claim_refinements",
            // Claims layer
            "responsibility_alerts",
            "responsibility_claims",
            "responsibility_tracebacks",
            // Run layer
            "runs",
            "schema_version",
        ];

        for table in &tables {
            let count: i64 = conn
                .query_row(
                    "SELECT count(*) FROM sqlite_master WHERE type='table' AND name=?1",
                    [table],
                    |r| r.get(0),
                )
                .unwrap();
            assert_eq!(count, 1, "Table '{}' should exist", table);
        }
    }

    #[test]
    fn test_migration_idempotent() {
        let conn = Connection::open_in_memory().unwrap();
        run_migrations(&conn).unwrap();
        // Second call must not error
        run_migrations(&conn).unwrap();

        // Schema version should still be 1
        let v: i64 = conn
            .query_row(
                "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1",
                [],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(v, 1);
    }

    #[test]
    fn test_schema_version_inserts_1() {
        let conn = Connection::open_in_memory().unwrap();
        run_migrations(&conn).unwrap();

        let v: i64 = conn
            .query_row("SELECT version FROM schema_version", [], |r| r.get(0))
            .unwrap();
        assert_eq!(v, 1);
    }

    #[test]
    fn test_open_responsibility_db_wal_mode() {
        use tempfile::tempdir;
        let dir = tempdir().unwrap();
        let path = dir.path().join("test.db");
        let path_str = path.to_string_lossy();

        let conn = open_responsibility_db(&path_str).unwrap();
        let journal: String = conn
            .query_row("PRAGMA journal_mode", [], |r| r.get(0))
            .unwrap();
        assert_eq!(journal.to_lowercase(), "wal");

        // Writes work
        conn.execute(
            "INSERT INTO runs (run_id, started_at_ns) VALUES ('r1', 100)",
            [],
        )
        .unwrap();
        drop(conn);

        // Read-only works — journal_mode was already set to WAL by the writer
        let ro = open_responsibility_db_read_only(&path_str).unwrap();
        let count: i64 = ro
            .query_row("SELECT count(*) FROM runs", [], |r| r.get(0))
            .unwrap();
        assert_eq!(count, 1);
    }

    #[test]
    fn test_read_only_connection_rejects_writes() {
        use tempfile::tempdir;
        let dir = tempdir().unwrap();
        let path = dir.path().join("ro_test.db");
        let path_str = path.to_string_lossy();

        // Create and seed DB
        let conn = open_responsibility_db(&path_str).unwrap();
        conn.execute(
            "INSERT INTO runs (run_id, started_at_ns) VALUES ('r1', 100)",
            [],
        )
        .unwrap();
        drop(conn);

        let ro = open_responsibility_db_read_only(&path_str).unwrap();
        let err = ro
            .execute(
                "INSERT INTO runs (run_id, started_at_ns) VALUES ('r2', 200)",
                [],
            )
            .unwrap_err();
        // Write should fail on read-only connection
        assert!(matches!(err, rusqlite::Error::SqliteFailure(_, _)));
    }

    #[test]
    fn test_can_insert_into_all_tables() {
        let conn = Connection::open_in_memory().unwrap();
        run_migrations(&conn).unwrap();

        let run_id = "test-run-1";

        // Run
        conn.execute(
            "INSERT INTO runs (run_id, started_at_ns) VALUES (?1, 0)",
            [run_id],
        )
        .unwrap();

        // Process events
        conn.execute(
            "INSERT OR IGNORE INTO process_events (pid, start_time_ns, comm, timestamp_ns, run_id)
             VALUES (100, 1000, 'claude', 5000, ?1)",
            [run_id],
        )
        .unwrap();

        // TLS sessions
        conn.execute(
            "INSERT OR IGNORE INTO tls_sessions (tls_session_id, pid, start_time_ns, timestamp_ns, run_id)
             VALUES ('tls-1', 100, 1000, 5000, ?1)",
            [run_id],
        )
        .unwrap();

        // Message bindings
        conn.execute(
            "INSERT INTO message_bindings (message_id, pid, start_time_ns, tls_session_id, binding_type, timestamp_ns, run_id)
             VALUES ('msg-1', 100, 1000, 'tls-1', 'TlsShared', 5000, ?1)",
            [run_id],
        )
        .unwrap();

        // Intents
        conn.execute(
            "INSERT INTO intents (intent_id, pid, process_start_time_ns, message_id, tool_name,
             tool_args_json, intent_class, parse_state, primary_matching_eligible, authority_source, timestamp_ns, run_id)
             VALUES ('intent-1', 100, 1000, 'msg-1', 'Bash', '[]', 'SideEffect', 'Complete', 1, 'RequestHistoryAssistantToolUse', 5000, ?1)",
            [run_id],
        )
        .unwrap();

        // Actions
        conn.execute(
            "INSERT INTO actions (action_id, pid, process_start_time_ns, action_type,
             normalized_args_json, derivation_mode, signal_class, timestamp_ns, run_id)
             VALUES ('action-1', 100, 1000, 'FileRead', '[]', 'Direct', 'HighSignal', 6000, ?1)",
            [run_id],
        )
        .unwrap();

        // Correlation evidence
        conn.execute(
            "INSERT INTO correlation_evidence (evidence_id, intent_id, action_id, match_level,
             confidence, matched_fields_json, phase, timestamp_ns, run_id)
             VALUES ('ev-1', 'intent-1', 'action-1', 'L1Exact', 0.9, '{}', 'Provisional', 6000, ?1)",
            [run_id],
        )
        .unwrap();

        // Non-link diagnostics
        conn.execute(
            "INSERT INTO non_link_diagnostics (diagnostic_id, intent_id, reason, timestamp_ns, run_id)
             VALUES ('diag-1', 'intent-1', 'NoArgumentMatch', 7000, ?1)",
            [run_id],
        )
        .unwrap();

        // Provisional identities
        conn.execute(
            "INSERT INTO provisional_identities (agent_id, process_ids_json, primary_signal, confidence, run_id)
             VALUES ('agent-1', '[]', 'TlsSessionNew', 0.8, ?1)",
            [run_id],
        )
        .unwrap();

        // Agent identities
        conn.execute(
            "INSERT INTO agent_identities (agent_id, process_ids_json, message_ids_json,
             tls_sessions_json, confidence, provisional_refs_json, run_id)
             VALUES ('agent-1', '[]', '[]', '[]', 'Authoritative', '[]', ?1)",
            [run_id],
        )
        .unwrap();

        // Identity process map
        conn.execute(
            "INSERT OR IGNORE INTO identity_process_map (agent_id, pid, start_time_ns, run_id)
             VALUES ('agent-1', 100, 1000, ?1)",
            [run_id],
        )
        .unwrap();

        // Graph nodes
        conn.execute(
            "INSERT INTO graph_nodes (node_id, node_type, agent_id, evidence_refs_json, timestamp_ns, phase, run_id)
             VALUES ('node-1', 'Agent', 'agent-1', '[]', 5000, 'Provisional', ?1)",
            [run_id],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO graph_nodes (node_id, node_type, resource_json, evidence_refs_json, timestamp_ns, phase, run_id)
             VALUES ('node-r1', 'Resource', '{\"File\":{\"canonical_path\":\"/tmp/x\"}}', '[]', 6000, 'Provisional', ?1)",
            [run_id],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO graph_edges (edge_id, edge_type, source_node_id, target_node_id,
             evidence_refs_json, confidence, phase, run_id)
             VALUES ('edge-1', 'IntentCausesAction', 'node-1', 'node-r1', '[]', 0.9, 'Provisional', ?1)",
            [run_id],
        )
        .unwrap();

        // Graph snapshots
        conn.execute(
            "INSERT INTO graph_snapshots (snapshot_id, timestamp_ns, nodes_json, edges_json, metrics_json, run_id)
             VALUES ('snap-1', 10000, '[]', '[]', '{}', ?1)",
            [run_id],
        )
        .unwrap();

        // Delegation records
        conn.execute(
            "INSERT INTO delegation_records (delegation_id, from_agent_id, to_agent_id,
             parent_pid, parent_start_time_ns, child_pid, child_start_time_ns, timestamp_ns, confidence, run_id)
             VALUES ('del-1', 'agent-1', 'agent-2', 100, 1000, 200, 2000, 5000, 0.85, ?1)",
            [run_id],
        )
        .unwrap();

        // Context pollution
        conn.execute(
            "INSERT INTO context_pollution_events (source_agent_id, contaminated_artifact,
             propagation_path_json, detected_at_ns, run_id)
             VALUES ('agent-1', '/tmp/shared.md', '[\"agent-1\",\"agent-2\"]', 8000, ?1)",
            [run_id],
        )
        .unwrap();

        // Policy evaluations
        conn.execute(
            "INSERT INTO policy_evaluations (policy_id, boundary, judgment_json, subject_agent_id,
             resource_json, evidence_refs_json, confidence, reason, run_id)
             VALUES ('pol-1', 'Capability', '{}', 'agent-1', NULL, '[]', 0.9, 'test', ?1)",
            [run_id],
        )
        .unwrap();

        // Refinements (one per table)
        conn.execute(
            "INSERT INTO identity_refinements (phase1_agent_id, phase2_agent_id, refinement_type, reason, run_id)
             VALUES ('agent-1', 'agent-1-v2', 'Confirmed', '', ?1)",
            [run_id],
        ).unwrap();
        conn.execute(
            "INSERT INTO subagent_boundaries (boundary_id, parent_agent_id, child_agent_id,
             parent_pid, parent_start_time_ns, child_pid, child_start_time_ns, reason, run_id)
             VALUES ('b-1', 'agent-1', 'agent-2', 100, 1000, 200, 2000, 'ClaudeChildOfClaude', ?1)",
            [run_id],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO evidence_refinements (phase1_evidence_id, phase2_evidence_id, refinement_type, reason, run_id)
             VALUES ('ev-1', 'ev-1-v2', 'Confirmed', '', ?1)",
            [run_id],
        ).unwrap();
        conn.execute(
            "INSERT INTO graph_refinements (refinement_type, affected_nodes_json, affected_edges_json, description, run_id)
             VALUES ('AgentMerged', '[\"node-1\"]', '[]', 'merged', ?1)",
            [run_id],
        ).unwrap();
        conn.execute(
            "INSERT INTO classification_refinements (phase1_alert_id, phase2_alert_id, refinement_type, reason, score_delta, run_id)
             VALUES ('alert-1', 'alert-1-v2', 'Confirmed', '', 0.0, ?1)",
            [run_id],
        ).unwrap();
        conn.execute(
            "INSERT INTO claim_refinements (phase1_claim_id, phase2_claim_id, refinement_type, reason, run_id)
             VALUES ('claim-1', 'claim-1-v2', 'Confirmed', '', ?1)",
            [run_id],
        ).unwrap();

        // Alerts
        conn.execute(
            "INSERT INTO responsibility_alerts (alert_id, timestamp_ns, display_profile, decision,
             severity, confidence, authority, reasoning, title, summary, primary_agent_id,
             involved_agents_json, engine_section_json, timeline_json, graph_metrics_json, phase, run_id)
             VALUES ('alert-1', 9000, 'UnsafeByEngine', 'Unsafe', 'Warning', 0.85, 'Supporting',
             'reasoning', 'Test Alert', 'An agent performed a suspicious action', 'agent-1',
             '[]', '{}', '[]', '{}', 'Provisional', ?1)",
            [run_id],
        )
        .unwrap();

        // Claims
        conn.execute(
            "INSERT INTO responsibility_claims (claim_id, claim_type, subject_agent_id, policy_id,
             boundary_id, claim_state, severity, confidence, evidence_refs_json,
             competing_explanations_json, phase, run_id)
             VALUES ('claim-1', 'PolicyViolation', 'agent-1', 'pol-1', 'boundary-1',
             'Observed', 'Warning', 0.9, '[]', '[]', 'Provisional', ?1)",
            [run_id],
        )
        .unwrap();

        // Tracebacks
        conn.execute(
            "INSERT INTO responsibility_tracebacks (traceback_id, claim_id, chain_node_ids_json,
             chain_edge_ids_json, root_cause_node_id, explanation_summary, confidence, run_id)
             VALUES ('tb-1', 'claim-1', '[\"node-1\"]', '[\"edge-1\"]', 'node-1',
             'Root cause found', 0.9, ?1)",
            [run_id],
        )
        .unwrap();
    }

    #[test]
    fn test_foreign_key_enforcement() {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch("PRAGMA foreign_keys=ON;").unwrap();
        run_migrations(&conn).unwrap();

        // Insert parent
        conn.execute(
            "INSERT INTO runs (run_id, started_at_ns) VALUES ('r1', 0)",
            [],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO graph_nodes (node_id, node_type, timestamp_ns, phase, run_id)
             VALUES ('n1', 'Agent', 0, 'Provisional', 'r1')",
            [],
        )
        .unwrap();

        // Valid edge
        conn.execute(
            "INSERT INTO graph_edges (edge_id, edge_type, source_node_id, target_node_id, phase, run_id)
             VALUES ('e1', 'IntentCausesAction', 'n1', 'n1', 'Provisional', 'r1')",
            [],
        )
        .unwrap();

        // Invalid edge (non-existent target) should fail FK
        let err = conn
            .execute(
                "INSERT INTO graph_edges (edge_id, edge_type, source_node_id, target_node_id, phase, run_id)
                 VALUES ('e2', 'IntentCausesAction', 'n1', 'nonexistent', 'Provisional', 'r1')",
                [],
            )
            .unwrap_err();
        assert!(matches!(err, rusqlite::Error::SqliteFailure(_, _)));
    }
}
