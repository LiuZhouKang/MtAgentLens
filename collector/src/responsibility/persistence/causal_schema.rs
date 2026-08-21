// SPDX-License-Identifier: MIT
// Clean causal schema: only the 9 tables written by CausalTraceAnalyzer/SqliteWriter.
// No responsibility tables. No legacy dropped tables.
//
// Used by: trace.log.causal.db

use rusqlite::{Connection, Result};

const CAUSAL_SCHEMA: &str = "
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS agent_instances (
    agent_id            TEXT PRIMARY KEY,
    first_seen_ns       INTEGER NOT NULL,
    last_seen_ns        INTEGER NOT NULL,
    root_processes_json TEXT NOT NULL DEFAULT '[]',
    tls_sessions_json   TEXT NOT NULL DEFAULT '[]',
    conversation_id     TEXT,
    confidence          TEXT NOT NULL DEFAULT 'authoritative'
);

CREATE TABLE IF NOT EXISTS agent_processes (
    agent_id      TEXT NOT NULL REFERENCES agent_instances(agent_id),
    pid           INTEGER NOT NULL,
    start_time_ns INTEGER NOT NULL,
    PRIMARY KEY (pid, start_time_ns)
);

CREATE TABLE IF NOT EXISTS unresolved_processes (
    pid           INTEGER NOT NULL,
    start_time_ns INTEGER NOT NULL,
    reason        TEXT NOT NULL,
    PRIMARY KEY (pid, start_time_ns)
);

CREATE TABLE IF NOT EXISTS intent_events (
    intent_id               TEXT PRIMARY KEY,
    agent_id                TEXT NOT NULL,
    timestamp_ns            INTEGER NOT NULL,
    tool_name               TEXT NOT NULL,
    tool_args_json          TEXT NOT NULL,
    intent_class            TEXT NOT NULL DEFAULT 'side_effect',
    parse_state             TEXT NOT NULL DEFAULT 'complete',
    primary_matching_eligible INTEGER NOT NULL DEFAULT 0,
    conversation_id         TEXT,
    message_id              TEXT,
    source_event_ref        TEXT,
    authority_source        TEXT NOT NULL DEFAULT 'http_response_tool_use',
    FOREIGN KEY(agent_id) REFERENCES agent_instances(agent_id)
);
CREATE INDEX IF NOT EXISTS idx_intent_agent_ts ON intent_events(agent_id, timestamp_ns);

CREATE TABLE IF NOT EXISTS action_events (
    action_id               TEXT PRIMARY KEY,
    agent_id                TEXT,
    timestamp_ns            INTEGER NOT NULL,
    pid                     INTEGER NOT NULL,
    process_start_time_ns   INTEGER NOT NULL,
    parent_pid              INTEGER,
    parent_start_time_ns    INTEGER,
    syscall                 TEXT NOT NULL,
    args_json               TEXT NOT NULL,
    comm                    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_action_agent_ts ON action_events(agent_id, timestamp_ns);
CREATE INDEX IF NOT EXISTS idx_action_process ON action_events(pid, process_start_time_ns);

CREATE TABLE IF NOT EXISTS action_candidates (
    candidate_id             TEXT PRIMARY KEY,
    schema_version           INTEGER NOT NULL,
    agent_id                 TEXT,
    timestamp_ns             INTEGER NOT NULL,
    candidate_type           TEXT NOT NULL,
    pid                      INTEGER NOT NULL,
    process_start_time_ns    INTEGER NOT NULL,
    normalized_args_json     TEXT NOT NULL,
    raw_event_refs_json      TEXT NOT NULL,
    derivation_mode          TEXT NOT NULL,
    lossiness                TEXT NOT NULL,
    aggregate_window_ns      INTEGER,
    aggregate_rule_name      TEXT,
    signal_class             TEXT,
    debug_notes              TEXT
);
CREATE INDEX IF NOT EXISTS idx_candidate_agent_ts ON action_candidates(agent_id, timestamp_ns);
CREATE INDEX IF NOT EXISTS idx_candidate_process ON action_candidates(pid, process_start_time_ns);

CREATE TABLE IF NOT EXISTS correlation_evidence (
    schema_version                  INTEGER NOT NULL,
    evidence_id                     TEXT PRIMARY KEY,
    agent_id                        TEXT NOT NULL,
    intent_id                       TEXT NOT NULL,
    candidate_id                    TEXT NOT NULL,
    timestamp_ns                    INTEGER NOT NULL,
    confidence                      REAL NOT NULL,
    match_level                     TEXT NOT NULL,
    matched_fields_json             TEXT,
    supporting_raw_event_refs_json  TEXT,
    conversation_id                 TEXT,
    debug_notes                     TEXT,
    authority_source                TEXT,
    message_id                      TEXT,
    source_event_ref                TEXT,
    derivation_mode                 TEXT,
    FOREIGN KEY(intent_id) REFERENCES intent_events(intent_id),
    FOREIGN KEY(candidate_id) REFERENCES action_candidates(candidate_id)
);
CREATE INDEX IF NOT EXISTS idx_correlation_evidence_agent_ts ON correlation_evidence(agent_id, timestamp_ns);
CREATE INDEX IF NOT EXISTS idx_correlation_evidence_intent ON correlation_evidence(intent_id);
CREATE INDEX IF NOT EXISTS idx_correlation_evidence_candidate ON correlation_evidence(candidate_id);

CREATE TABLE IF NOT EXISTS non_link_diagnostics (
    schema_version           INTEGER NOT NULL,
    diagnostic_id            TEXT PRIMARY KEY,
    timestamp_ns             INTEGER NOT NULL,
    agent_id                 TEXT,
    intent_id                TEXT,
    candidate_id             TEXT,
    reason                   TEXT NOT NULL,
    stage                    TEXT NOT NULL,
    intent_class             TEXT,
    parse_state              TEXT,
    match_level_considered   TEXT,
    confidence_observed      REAL,
    details_json             TEXT
);
CREATE INDEX IF NOT EXISTS idx_non_link_diag_agent_ts ON non_link_diagnostics(agent_id, timestamp_ns);
CREATE INDEX IF NOT EXISTS idx_non_link_diag_intent ON non_link_diagnostics(intent_id);

CREATE TABLE IF NOT EXISTS violation_pattern_matches (
    match_id TEXT PRIMARY KEY,
    template_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    matched_evidence_ids_json TEXT NOT NULL,
    matched_policies_json TEXT NOT NULL,
    matched_boundaries_json TEXT NOT NULL,
    matched_targets_json TEXT NOT NULL,
    start_ns INTEGER NOT NULL,
    end_ns INTEGER NOT NULL,
    severity TEXT NOT NULL,
    description TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_violation_pattern_agent_ts ON violation_pattern_matches(agent_id, end_ns);

CREATE TABLE IF NOT EXISTS evidence_records (
    evidence_id         TEXT PRIMARY KEY,
    source_event_id     TEXT NOT NULL,
    directness          TEXT NOT NULL,
    rule_or_inference_id TEXT NOT NULL,
    resource_id         TEXT,
    boundary_id         TEXT,
    confidence          REAL NOT NULL,
    redaction_status    TEXT NOT NULL,
    payload_ref         TEXT
);

CREATE TABLE IF NOT EXISTS responsibility_evidence_records (
    evidence_id             TEXT PRIMARY KEY,
    evidence_type           TEXT NOT NULL,
    directness              TEXT NOT NULL,
    observation_refs_json   TEXT,
    subject_hint            TEXT,
    resource_ref_json       TEXT,
    rule_id                 TEXT,
    matched_fields_json     TEXT,
    confidence              REAL NOT NULL,
    diagnostics_json        TEXT
);

CREATE TABLE IF NOT EXISTS responsibility_graph_snapshots (
    snapshot_id         TEXT PRIMARY KEY,
    timestamp_ns        INTEGER NOT NULL,
    agent_id            TEXT NOT NULL,
    nodes_json          TEXT NOT NULL,
    edges_json          TEXT NOT NULL,
    diagnostics_json    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS delegation_records (
    delegation_id       TEXT PRIMARY KEY,
    from_agent_id       TEXT NOT NULL,
    to_agent_id         TEXT NOT NULL,
    parent_pid          INTEGER NOT NULL,
    child_pid           INTEGER NOT NULL,
    timestamp_ns        INTEGER NOT NULL,
    child_comm          TEXT NOT NULL,
    confidence          REAL NOT NULL DEFAULT 0.85
);
CREATE INDEX IF NOT EXISTS idx_delegation_from_agent ON delegation_records(from_agent_id);
CREATE INDEX IF NOT EXISTS idx_delegation_to_agent ON delegation_records(to_agent_id);

CREATE TABLE IF NOT EXISTS context_pollution_events (
    event_id            TEXT PRIMARY KEY,
    policy_id           TEXT NOT NULL,
    boundary_id         TEXT NOT NULL,
    claim_state         TEXT NOT NULL,
    writer_agent_id     TEXT NOT NULL,
    reader_agent_id     TEXT NOT NULL,
    shared_path         TEXT NOT NULL,
    write_ts_ns         INTEGER NOT NULL,
    read_ts_ns          INTEGER NOT NULL,
    dt_ns               INTEGER NOT NULL,
    evidence_json       TEXT NOT NULL,
    reason              TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pollution_writer_ts ON context_pollution_events(writer_agent_id, write_ts_ns);
CREATE INDEX IF NOT EXISTS idx_pollution_reader_ts ON context_pollution_events(reader_agent_id, read_ts_ns);

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);
INSERT OR IGNORE INTO schema_version(version) VALUES (1);
";

pub fn open_causal_db(path: &str) -> Result<Connection> {
    let conn = Connection::open(path)?;
    conn.execute_batch(CAUSAL_SCHEMA)?;
    Ok(conn)
}
