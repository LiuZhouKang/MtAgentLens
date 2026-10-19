// SPDX-License-Identifier: Apache-2.0 OR MIT
// Responsibility architecture persistence schema — one authoritative DDL,
// a versioned migration ladder, and a fail-closed opener.
//
// Every write-path opener must classify the file *before* touching it. The
// retired causal schema and any unrecognised database are rejected with zero
// writes: an incremental `CREATE TABLE IF NOT EXISTS` batch on a foreign
// database would silently graft 27 unrelated tables onto it.

use rusqlite::{Connection, OpenFlags};
use std::fmt;

// ═══════════════════════════════════════════════════════════════════════════
// Version spine
// ═══════════════════════════════════════════════════════════════════════════

/// Stamped into every database this collector owns (`PRAGMA application_id`).
/// ASCII "MTL1" — a positive identity that no foreign SQLite file carries.
pub const APPLICATION_ID: i64 = 0x4D54_4C31;

/// Highest schema version this binary can write. Bumped only together with the
/// matching entry in [`MIGRATIONS`].
pub const LATEST_SCHEMA_VERSION: i64 = 2;

/// Tables that only the retired causal schema has. Any hit means the file is a
/// legacy causal database and must never receive live DDL.
const CAUSAL_MARKERS: &[&str] = &[
    "agent_instances",
    "intent_events",
    "action_candidates",
    "agent_processes",
    "action_events",
    "causal_links",
    "violation_pattern_matches",
    "evidence_records",
    "responsibility_graph_snapshots",
    "causal_graph_snapshots",
];

/// The three tables every live collector database must have. Adoption (the
/// right to migrate or stamp a pre-existing file) requires *all* of them.
const LIVE_MARKERS: &[&str] = &["runs", "process_events", "intents"];

/// Tables whose *shape* distinguishes the causal schema from the live one.
/// A fixture that only materialises `correlation_evidence` still has to be
/// recognised as causal, so the table name alone is not enough.
const CAUSAL_COLUMN_MARKERS: &[(&str, &str)] = &[
    ("correlation_evidence", "candidate_id"),
    ("non_link_diagnostics", "candidate_id"),
    ("delegation_records", "child_comm"),
    ("context_pollution_events", "writer_agent_id"),
];

/// What kind of SQLite file the opener found.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SchemaKind {
    /// No user tables — a new file that this collector may own.
    Fresh,
    /// A live collector database (positive identification via [`LIVE_MARKERS`]).
    Live,
    /// The retired causal schema. Read-only compatibility is gone; reject.
    LegacyCausal,
    /// Anything else. Fail closed rather than graft tables onto it.
    Unknown,
}

/// Fail-closed schema errors. Every variant except `Sqlite` is raised before
/// the first write (classification and version gates are read-only).
#[derive(Debug)]
pub enum SchemaError {
    Sqlite(rusqlite::Error),
    LegacyCausalSchema,
    UnknownSchema,
    UnsupportedSchemaVersion {
        found: i64,
        latest: i64,
    },
    Migration {
        version: i64,
        source: rusqlite::Error,
    },
    BackupFailed {
        path: String,
        reason: String,
    },
}

impl fmt::Display for SchemaError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            SchemaError::Sqlite(e) => write!(f, "sqlite error: {e}"),
            SchemaError::LegacyCausalSchema => write!(
                f,
                "legacy causal schema detected — this database predates the unified \
                 collector schema and is refused without modification"
            ),
            SchemaError::UnknownSchema => write!(
                f,
                "unknown database schema — the file is neither a fresh database nor a \
                 live collector database; refusing to write"
            ),
            SchemaError::UnsupportedSchemaVersion { found, latest } => write!(
                f,
                "schema version {found} is newer than this binary supports (latest {latest})"
            ),
            SchemaError::Migration { version, source } => {
                write!(f, "migration to version {version} failed: {source}")
            }
            SchemaError::BackupFailed { path, reason } => {
                write!(f, "pre-migration backup of {path} failed: {reason}")
            }
        }
    }
}

impl std::error::Error for SchemaError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            SchemaError::Sqlite(e) => Some(e),
            SchemaError::Migration { source, .. } => Some(source),
            _ => None,
        }
    }
}

impl From<rusqlite::Error> for SchemaError {
    fn from(value: rusqlite::Error) -> Self {
        SchemaError::Sqlite(value)
    }
}

/// Adapt a [`SchemaError`] for the few legacy call sites whose signatures are
/// still `rusqlite::Result`. New code should return [`SchemaError`] directly.
pub fn schema_error_to_rusqlite(err: SchemaError) -> rusqlite::Error {
    match err {
        SchemaError::Sqlite(err) => err,
        other => rusqlite::Error::SqliteFailure(
            rusqlite::ffi::Error::new(rusqlite::ffi::SQLITE_ERROR),
            Some(other.to_string()),
        ),
    }
}

// ═══════════════════════════════════════════════════════════════════════════
// SCHEMA_V1_DDL — the 27-table baseline, frozen verbatim.
//
// This string is never edited again. Every future change is a numbered
// migration in MIGRATIONS; that is what makes the version spine meaningful.
// ═══════════════════════════════════════════════════════════════════════════

pub const SCHEMA_V1_DDL: &str = "
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

/// Backward-compatible alias for the frozen v1 DDL. New code must not depend
/// on this name; migrations are the only way the schema changes.
pub const SCHEMA_DDL: &str = SCHEMA_V1_DDL;

// ═══════════════════════════════════════════════════════════════════════════
// Migrations
// ═══════════════════════════════════════════════════════════════════════════

/// One rung of the ladder. `version` is the version the database is at *after*
/// `apply` succeeds.
pub struct Migration {
    pub version: i64,
    pub apply: fn(&Connection) -> rusqlite::Result<()>,
}

fn migrate_to_v1(conn: &Connection) -> rusqlite::Result<()> {
    conn.execute_batch(SCHEMA_V1_DDL)
}

// ═══════════════════════════════════════════════════════════════════════════
// v2 — identity beliefs
//
// `identity_process_map` conflated three different claims into one row: "this
// process belonged to this agent", "how strongly do we know that", and "since
// when". v2 makes the belief append-only and keeps the old name as a view, so
// every existing reader (frontend SQL included) keeps working unchanged.
// ═══════════════════════════════════════════════════════════════════════════

const SCHEMA_V2_BELIEFS_DDL: &str = "
CREATE TABLE IF NOT EXISTS identity_process_beliefs (
    belief_id               INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id                TEXT NOT NULL,
    pid                     INTEGER NOT NULL,
    start_time_ns           INTEGER NOT NULL,
    run_id                  TEXT NOT NULL,
    belief_level            TEXT NOT NULL CHECK (
        belief_level IN ('Unknown', 'Provisional', 'Inferred', 'Authoritative')
    ),
    observed_at_ns          INTEGER NOT NULL,
    source                  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_identity_beliefs_process
    ON identity_process_beliefs(pid, start_time_ns, run_id);
CREATE INDEX IF NOT EXISTS idx_identity_beliefs_agent
    ON identity_process_beliefs(agent_id);
CREATE INDEX IF NOT EXISTS idx_identity_beliefs_run
    ON identity_process_beliefs(run_id);
";

const SCHEMA_V2_VIEWS_DDL: &str = "
DROP VIEW IF EXISTS identity_process_map;
DROP VIEW IF EXISTS identity_process_map_by_run;
DROP VIEW IF EXISTS identity_process_belief_windows;

-- One row per process, keeping the historical primary-key semantics
-- (pid, start_time_ns). The strongest belief wins; recency breaks ties.
CREATE VIEW identity_process_map AS
SELECT agent_id, pid, start_time_ns, run_id
FROM identity_process_beliefs b
WHERE b.belief_id = (
    SELECT b2.belief_id
    FROM identity_process_beliefs b2
    WHERE b2.pid = b.pid AND b2.start_time_ns = b.start_time_ns
    ORDER BY
        CASE b2.belief_level
            WHEN 'Authoritative' THEN 3
            WHEN 'Inferred' THEN 2
            WHEN 'Provisional' THEN 1
            ELSE 0
        END DESC,
        b2.observed_at_ns DESC,
        b2.belief_id DESC
    LIMIT 1
);

-- One row per (run, process). This is the view a run-scoped consumer should
-- use; the historical map folded every run into one process row.
CREATE VIEW identity_process_map_by_run AS
SELECT agent_id, pid, start_time_ns, run_id
FROM identity_process_beliefs b
WHERE b.belief_id = (
    SELECT b2.belief_id
    FROM identity_process_beliefs b2
    WHERE b2.pid = b.pid AND b2.start_time_ns = b.start_time_ns
      AND b2.run_id = b.run_id
    ORDER BY
        CASE b2.belief_level
            WHEN 'Authoritative' THEN 3
            WHEN 'Inferred' THEN 2
            WHEN 'Provisional' THEN 1
            ELSE 0
        END DESC,
        b2.observed_at_ns DESC,
        b2.belief_id DESC
    LIMIT 1
);

-- Contiguous belief periods: each row is valid until the next observation.
CREATE VIEW identity_process_belief_windows AS
SELECT
    belief_id,
    agent_id,
    pid,
    start_time_ns,
    run_id,
    belief_level,
    source,
    observed_at_ns AS valid_from_ns,
    LEAD(observed_at_ns) OVER (
        PARTITION BY pid, start_time_ns, run_id
        ORDER BY observed_at_ns, belief_id
    ) AS valid_to_ns
FROM identity_process_beliefs;
";

/// v2 migration. Never invents a belief level:
/// - a process whose agent is in `agent_identities` inherits that row's
///   confidence (`Authoritative` / `Inferred`),
/// - a process whose agent is in `provisional_identities` becomes `Provisional`,
/// - anything else is migrated as `Unknown` with the source recording that the
///   provenance was lost.
fn migrate_to_v2(conn: &Connection) -> rusqlite::Result<()> {
    conn.execute_batch(SCHEMA_V2_BELIEFS_DDL)?;

    // A v1 database created before identity persistence existed has no map at
    // all; v2 must still succeed (D3).
    let has_map_table: bool = conn.query_row(
        "SELECT count(*) FROM sqlite_master WHERE type = 'table' AND name = 'identity_process_map'",
        [],
        |row| row.get::<_, i64>(0),
    )? > 0;

    if has_map_table {
        conn.execute_batch(
            "INSERT INTO identity_process_beliefs
                 (agent_id, pid, start_time_ns, run_id, belief_level, observed_at_ns, source)
             SELECT
                 m.agent_id,
                 m.pid,
                 m.start_time_ns,
                 m.run_id,
                 CASE
                     WHEN EXISTS (
                         SELECT 1 FROM agent_identities a WHERE a.agent_id = m.agent_id
                     ) THEN COALESCE((
                         SELECT CASE a.confidence
                                    WHEN 'Authoritative' THEN 'Authoritative'
                                    WHEN 'Inferred' THEN 'Inferred'
                                    ELSE 'Unknown'
                                END
                         FROM agent_identities a WHERE a.agent_id = m.agent_id
                         LIMIT 1
                     ), 'Unknown')
                     WHEN EXISTS (
                         SELECT 1 FROM provisional_identities p WHERE p.agent_id = m.agent_id
                     ) THEN 'Provisional'
                     ELSE 'Unknown'
                 END,
                 0,
                 'v1_identity_process_map'
             FROM identity_process_map m;",
        )?;
        conn.execute_batch("DROP TABLE identity_process_map;")?;
    }

    conn.execute_batch(SCHEMA_V2_VIEWS_DDL)
}

/// The ladder. Must be sorted ascending, versions unique, and the last entry
/// must equal [`LATEST_SCHEMA_VERSION`] — `run_migrations` asserts this.
pub const MIGRATIONS: &[Migration] = &[
    Migration {
        version: 1,
        apply: migrate_to_v1,
    },
    Migration {
        version: 2,
        apply: migrate_to_v2,
    },
];

fn pragma_i64(conn: &Connection, name: &str) -> rusqlite::Result<i64> {
    conn.query_row(&format!("PRAGMA {name}"), [], |row| row.get(0))
}

fn table_exists(conn: &Connection, name: &str) -> rusqlite::Result<bool> {
    let count: i64 = conn.query_row(
        "SELECT count(*) FROM sqlite_master WHERE type = 'table' AND name = ?1",
        [name],
        |row| row.get(0),
    )?;
    Ok(count > 0)
}

/// Does `table` exist and carry `column`? Used to tell the two historical
/// shapes apart when they share a table name.
fn table_has_column(conn: &Connection, table: &str, column: &str) -> rusqlite::Result<bool> {
    if !table_exists(conn, table)? {
        return Ok(false);
    }
    let mut stmt = conn.prepare(&format!("PRAGMA table_info({table})"))?;
    let mut rows = stmt.query([])?;
    while let Some(row) = rows.next()? {
        let name: String = row.get(1)?;
        if name == column {
            return Ok(true);
        }
    }
    Ok(false)
}

fn user_object_count(conn: &Connection) -> rusqlite::Result<i64> {
    conn.query_row(
        "SELECT count(*) FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'",
        [],
        |row| row.get(0),
    )
}

/// Classify a database by reading only. Never writes, never migrates.
///
/// A non-SQLite file surfaces as [`SchemaError::UnknownSchema`] rather than a
/// raw SQLite error so callers have one fail-closed story.
pub fn classify_schema(conn: &Connection) -> Result<SchemaKind, SchemaError> {
    let read_header = || -> Result<(i64, i64), SchemaError> {
        let application_id = pragma_i64(conn, "application_id")?;
        let user_version = pragma_i64(conn, "user_version")?;
        Ok((application_id, user_version))
    };
    let (application_id, user_version) = match read_header() {
        Ok(header) => header,
        Err(SchemaError::Sqlite(rusqlite::Error::SqliteFailure(err, _)))
            if matches!(err.code, rusqlite::ErrorCode::NotADatabase) =>
        {
            return Ok(SchemaKind::Unknown);
        }
        Err(err) => return Err(err),
    };
    if user_version > LATEST_SCHEMA_VERSION {
        return Err(SchemaError::UnsupportedSchemaVersion {
            found: user_version,
            latest: LATEST_SCHEMA_VERSION,
        });
    }

    let classify = || -> rusqlite::Result<SchemaKind> {
        let mut causal_hits = 0usize;
        for marker in CAUSAL_MARKERS {
            if table_exists(conn, marker)? {
                causal_hits += 1;
            }
        }
        for (table, column) in CAUSAL_COLUMN_MARKERS {
            if table_has_column(conn, table, column)? {
                causal_hits += 1;
            }
        }
        if causal_hits > 0 {
            return Ok(SchemaKind::LegacyCausal);
        }

        // A non-zero, foreign application id is a positive counter-signal:
        // another application owns this file even if table names collide.
        if application_id != 0 && application_id != APPLICATION_ID {
            return Ok(SchemaKind::Unknown);
        }

        let mut live_hits = 0usize;
        for marker in LIVE_MARKERS {
            if table_exists(conn, marker)? {
                live_hits += 1;
            }
        }
        if live_hits == LIVE_MARKERS.len() {
            return Ok(SchemaKind::Live);
        }

        if user_object_count(conn)? == 0 {
            return Ok(SchemaKind::Fresh);
        }
        Ok(SchemaKind::Unknown)
    };

    let kind = match classify() {
        Ok(kind) => kind,
        Err(rusqlite::Error::SqliteFailure(err, _))
            if matches!(err.code, rusqlite::ErrorCode::NotADatabase) =>
        {
            return Ok(SchemaKind::Unknown);
        }
        Err(err) => return Err(SchemaError::Sqlite(err)),
    };

    // The version can also live only in the `schema_version` table (that is
    // how the historical writer stamped it). Gate on the effective version
    // here so every caller rejects a future database before the first write.
    if matches!(kind, SchemaKind::Fresh | SchemaKind::Live) {
        let effective = effective_version(conn)?;
        if effective > LATEST_SCHEMA_VERSION {
            return Err(SchemaError::UnsupportedSchemaVersion {
                found: effective,
                latest: LATEST_SCHEMA_VERSION,
            });
        }
    }
    Ok(kind)
}

/// `max(user_version, MAX(schema_version))` — the number the *data* believes.
/// Both headers exist because two historical binaries each wrote only one.
fn effective_version(conn: &Connection) -> rusqlite::Result<i64> {
    let user_version = pragma_i64(conn, "user_version")?;
    let highest_table_version = if table_exists(conn, "schema_version")? {
        conn.query_row(
            "SELECT COALESCE(MAX(version), 0) FROM schema_version",
            [],
            |row| row.get(0),
        )?
    } else {
        0
    };
    Ok(user_version.max(highest_table_version))
}

fn apply_migration(conn: &Connection, migration: &Migration) -> Result<(), SchemaError> {
    // BEGIN IMMEDIATE: take the write lock before running DDL so two openers
    // cannot interleave halves of the same migration.
    conn.execute_batch("BEGIN IMMEDIATE")?;
    let result = (|| -> rusqlite::Result<()> {
        (migration.apply)(conn)?;
        conn.execute_batch(&format!(
            "PRAGMA application_id = {APPLICATION_ID};
             PRAGMA user_version = {};",
            migration.version
        ))?;
        conn.execute(
            "INSERT OR IGNORE INTO schema_version(version) VALUES (?1)",
            [migration.version],
        )?;
        Ok(())
    })();

    match result {
        Ok(()) => {
            conn.execute_batch("COMMIT")?;
            Ok(())
        }
        Err(source) => {
            // Roll the half-applied migration back. A failure here must not
            // leave a database that claims a version it does not have.
            let _ = conn.execute_batch("ROLLBACK");
            Err(SchemaError::Migration {
                version: migration.version,
                source,
            })
        }
    }
}

fn run_migration_ladder(
    conn: &Connection,
    migrations: &[Migration],
    start_version: i64,
) -> Result<(), SchemaError> {
    let mut previous = 0;
    for migration in migrations {
        assert!(
            migration.version > previous,
            "MIGRATIONS must be sorted ascending with unique versions"
        );
        previous = migration.version;
        if migration.version > start_version {
            apply_migration(conn, migration)?;
        }
    }
    Ok(())
}

/// Bring a writable connection up to [`LATEST_SCHEMA_VERSION`].
///
/// Callers must have classified the file first — this function assumes the
/// database is either fresh or a live collector database.
pub fn run_migrations(conn: &Connection) -> Result<(), SchemaError> {
    assert_eq!(
        MIGRATIONS.last().map(|m| m.version),
        Some(LATEST_SCHEMA_VERSION),
        "the migration ladder must end at LATEST_SCHEMA_VERSION"
    );

    let start = effective_version(conn)?;
    if start > LATEST_SCHEMA_VERSION {
        return Err(SchemaError::UnsupportedSchemaVersion {
            found: start,
            latest: LATEST_SCHEMA_VERSION,
        });
    }
    run_migration_ladder(conn, MIGRATIONS, start)
}

/// Make the file header agree with the migration ladder: `application_id`
/// identifies the owner, `user_version` names the actual version, and the
/// `schema_version` table keeps the version visible to SQL-only consumers.
fn ensure_header(conn: &Connection) -> Result<(), SchemaError> {
    let version = effective_version(conn)?;
    let application_id = pragma_i64(conn, "application_id")?;
    let user_version = pragma_i64(conn, "user_version")?;
    if application_id == APPLICATION_ID && user_version == version {
        return Ok(());
    }

    conn.execute_batch("BEGIN IMMEDIATE")?;
    let result = (|| -> rusqlite::Result<()> {
        conn.execute_batch(&format!(
            "PRAGMA application_id = {APPLICATION_ID};
             PRAGMA user_version = {version};"
        ))?;
        conn.execute(
            "INSERT OR IGNORE INTO schema_version(version) VALUES (?1)",
            [version],
        )?;
        Ok(())
    })();
    match result {
        Ok(()) => {
            conn.execute_batch("COMMIT")?;
            Ok(())
        }
        Err(source) => {
            let _ = conn.execute_batch("ROLLBACK");
            Err(SchemaError::Sqlite(source))
        }
    }
}

// ═══════════════════════════════════════════════════════════════════════════
// open_* helpers
// ═══════════════════════════════════════════════════════════════════════════

/// Copy a non-empty database before the v2 rewrite.
///
/// `VACUUM INTO` refuses an existing target, so the backup is written to a
/// process-unique temporary name and renamed into place. An existing
/// `<db>.pre-v2.bak` is left alone (D5): a failed earlier attempt must never
/// make the database permanently unopenable.
fn backup_before_v2(conn: &Connection) -> Result<(), SchemaError> {
    let Some(path) = conn.path() else {
        return Ok(());
    };
    if path.is_empty() || path == ":memory:" {
        return Ok(());
    }
    let backup = format!("{path}.pre-v2.bak");
    if std::path::Path::new(&backup).exists() {
        return Ok(());
    }
    let temp = format!("{backup}.tmp-{}", std::process::id());
    let _ = std::fs::remove_file(&temp);

    let vacuum = format!("VACUUM INTO '{}'", temp.replace('\'', "''"));
    conn.execute_batch(&vacuum)
        .map_err(|err| SchemaError::BackupFailed {
            path: backup.clone(),
            reason: err.to_string(),
        })?;
    std::fs::rename(&temp, &backup).map_err(|err| {
        let _ = std::fs::remove_file(&temp);
        SchemaError::BackupFailed {
            path: backup.clone(),
            reason: err.to_string(),
        }
    })?;
    Ok(())
}

/// Classify, then migrate. This is the single write-path opener: both
/// `open_responsibility_db` and `PersistenceWriter` go through it, so there is
/// exactly one place where a foreign database can be rejected.
fn prepare_writable(conn: &Connection) -> Result<(), SchemaError> {
    match classify_schema(conn)? {
        SchemaKind::Fresh | SchemaKind::Live => {}
        SchemaKind::LegacyCausal => return Err(SchemaError::LegacyCausalSchema),
        SchemaKind::Unknown => return Err(SchemaError::UnknownSchema),
    }

    // Backup before the first change, and before WAL touches the file. A
    // database with data but no version metadata also earns a backup: it may
    // hold rows that v1/v2 will rewrite.
    if user_object_count(conn)? > 0 && effective_version(conn)? < 2 {
        backup_before_v2(conn)?;
    }

    // Only after classification succeeds may the file be touched. Setting WAL
    // is a write; running it before the gate would contradict the zero-write
    // promise of the rejection path.
    conn.execute_batch(
        "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL; PRAGMA foreign_keys=ON;",
    )?;
    run_migrations(conn)?;
    ensure_header(conn)
}

/// Open a responsibility DB for writing: WAL mode, migrations, and a stamped
/// application id. Rejects legacy causal and unknown databases without
/// modifying them.
pub fn open_responsibility_db(path: &str) -> Result<Connection, SchemaError> {
    // Classify and version-gate over a read-only connection first. Opening a
    // WAL database read-write can recovery-checkpoint a hot `-wal` on close,
    // which would break the zero-write promise of the rejection path.
    if path != ":memory:" && std::path::Path::new(path).exists() {
        let probe = Connection::open_with_flags(
            path,
            OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
        )?;
        // Reject on the probe: a rejected database must never be opened
        // read-write, not even for an instant.
        match classify_schema(&probe)? {
            SchemaKind::Fresh | SchemaKind::Live => {}
            SchemaKind::LegacyCausal => return Err(SchemaError::LegacyCausalSchema),
            SchemaKind::Unknown => return Err(SchemaError::UnknownSchema),
        }
    }

    let conn = Connection::open(path)?;
    prepare_writable(&conn)?;
    // Run databases hold captured prompts, tool arguments and (until the
    // redactor runs) header values. SQLite creates files with the process
    // umask, which is commonly 0644 — readable by every local user. Restrict
    // the owner's databases; WAL/SHM files inherit this mode from SQLite.
    restrict_file_permissions(path);
    Ok(conn)
}

/// Best-effort `0600` on the database file. A failure is reported, not hidden:
/// silently leaving a world-readable capture database is exactly the kind of
/// "looks fine" state this project refuses.
fn restrict_file_permissions(path: &str) {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        match std::fs::metadata(path) {
            Ok(metadata) => {
                let mut permissions = metadata.permissions();
                if permissions.mode() & 0o077 != 0 {
                    permissions.set_mode(0o600);
                    if let Err(err) = std::fs::set_permissions(path, permissions) {
                        eprintln!("schema: cannot restrict permissions on {path}: {err}");
                    }
                }
            }
            Err(err) => {
                eprintln!("schema: cannot stat {path} to restrict permissions: {err}");
            }
        }
    }
    #[cfg(not(unix))]
    {
        let _ = path;
    }
}

/// Shared read-only validation.
///
/// Read-only opens never migrate, so they cannot damage a file. They reject
/// every database this binary cannot interpret: legacy causal, unknown shapes,
/// and future versions. With the causal-only routes retired there is no reader
/// left that should see a causal file, so accepting one would only hide a
/// mis-resolved artifact behind a "no such table" error later.
pub fn verify_readable_schema(conn: &Connection) -> Result<(), SchemaError> {
    match classify_schema(conn)? {
        SchemaKind::LegacyCausal => return Err(SchemaError::LegacyCausalSchema),
        SchemaKind::Unknown => return Err(SchemaError::UnknownSchema),
        SchemaKind::Fresh | SchemaKind::Live => {}
    }
    let version = effective_version(conn)?;
    if version > LATEST_SCHEMA_VERSION {
        return Err(SchemaError::UnsupportedSchemaVersion {
            found: version,
            latest: LATEST_SCHEMA_VERSION,
        });
    }
    Ok(())
}

/// Open a responsibility DB in read-only mode (for PersistenceReader).
pub fn open_responsibility_db_read_only(path: &str) -> Result<Connection, SchemaError> {
    let conn = Connection::open_with_flags(
        path,
        OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )?;
    verify_readable_schema(&conn)?;
    // read_uncommitted=0 ensures snapshot isolation on the WAL database.
    // journal_mode cannot be changed on a read-only connection, but the
    // writer already sets WAL mode on the database file.
    let _ = conn.execute_batch("PRAGMA read_uncommitted=0;");
    Ok(conn)
}

/// Alias for open_responsibility_db_read_only — backward compatibility.
pub fn open_db_read_only(path: &str) -> Result<Connection, SchemaError> {
    open_responsibility_db_read_only(path)
}

// ═══════════════════════════════════════════════════════════════════════════
// Tests
// ═══════════════════════════════════════════════════════════════════════════

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_schema_creates_all_tables_and_views() {
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
            "identity_process_beliefs",
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

        // v2 turns the identity map into compatibility views over the
        // append-only belief table.
        for view in [
            "identity_process_map",
            "identity_process_map_by_run",
            "identity_process_belief_windows",
        ] {
            let count: i64 = conn
                .query_row(
                    "SELECT count(*) FROM sqlite_master WHERE type='view' AND name=?1",
                    [view],
                    |r| r.get(0),
                )
                .unwrap();
            assert_eq!(count, 1, "View '{}' should exist", view);
        }
    }

    #[test]
    fn test_migration_idempotent() {
        let conn = Connection::open_in_memory().unwrap();
        run_migrations(&conn).unwrap();
        // Second call must not error
        run_migrations(&conn).unwrap();

        let v: i64 = conn
            .query_row(
                "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1",
                [],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(v, LATEST_SCHEMA_VERSION);
    }

    #[test]
    fn test_schema_version_inserts_latest() {
        let conn = Connection::open_in_memory().unwrap();
        run_migrations(&conn).unwrap();

        let v: i64 = conn
            .query_row("SELECT MAX(version) FROM schema_version", [], |r| r.get(0))
            .unwrap();
        assert_eq!(v, LATEST_SCHEMA_VERSION);
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

        // Identity beliefs (the v2 map is a view over this table)
        conn.execute(
            "INSERT INTO identity_process_beliefs
             (agent_id, pid, start_time_ns, run_id, belief_level, observed_at_ns, source)
             VALUES ('agent-1', 100, 1000, ?1, 'Authoritative', 5000, 'test')",
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

    // ── Version spine ─────────────────────────────────────────────────────

    fn temp_db_path(dir: &tempfile::TempDir, name: &str) -> String {
        dir.path().join(name).to_string_lossy().into_owned()
    }

    fn live_tables(conn: &Connection) {
        conn.execute_batch(SCHEMA_V1_DDL).unwrap();
        // Simulate the historical writer: it stamped the version table but
        // never the file header.
        conn.execute_batch("PRAGMA user_version = 0; PRAGMA application_id = 0;")
            .unwrap();
    }

    fn assert_header(conn: &Connection, expected_version: i64) {
        let application_id: i64 = conn
            .query_row("PRAGMA application_id", [], |row| row.get(0))
            .unwrap();
        assert_eq!(application_id, APPLICATION_ID);
        let user_version: i64 = conn
            .query_row("PRAGMA user_version", [], |row| row.get(0))
            .unwrap();
        assert_eq!(user_version, expected_version);
        let table_version: i64 = conn
            .query_row("SELECT MAX(version) FROM schema_version", [], |row| {
                row.get(0)
            })
            .unwrap();
        assert_eq!(table_version, expected_version);
    }

    #[cfg(unix)]
    #[test]
    fn writable_open_restricts_database_permissions_to_the_owner() {
        use std::os::unix::fs::PermissionsExt;
        let dir = tempfile::tempdir().unwrap();
        let path = temp_db_path(&dir, "private.db");
        {
            // Simulate SQLite's umask-derived 0644.
            std::fs::write(&path, b"").unwrap();
            let mut permissions = std::fs::metadata(&path).unwrap().permissions();
            permissions.set_mode(0o644);
            std::fs::set_permissions(&path, permissions).unwrap();
        }
        let _conn = open_responsibility_db(&path).unwrap();
        let mode = std::fs::metadata(&path).unwrap().permissions().mode() & 0o777;
        assert_eq!(
            mode, 0o600,
            "capture databases must not be group/world readable"
        );
    }

    #[test]
    fn test_fresh_db_is_stamped() {
        let dir = tempfile::tempdir().unwrap();
        let path = temp_db_path(&dir, "fresh.db");

        let conn = open_responsibility_db(&path).unwrap();
        assert_header(&conn, LATEST_SCHEMA_VERSION);
        let kind = classify_schema(&conn).unwrap();
        assert_eq!(kind, SchemaKind::Live);
    }

    #[test]
    fn test_v1_live_db_is_adopted_and_stamped() {
        let dir = tempfile::tempdir().unwrap();
        let path = temp_db_path(&dir, "v1.db");
        {
            let conn = Connection::open(&path).unwrap();
            live_tables(&conn);
            conn.execute(
                "INSERT INTO runs (run_id, started_at_ns) VALUES ('r1', 100)",
                [],
            )
            .unwrap();
        }

        let conn = open_responsibility_db(&path).unwrap();
        assert_header(&conn, LATEST_SCHEMA_VERSION);
        let runs: i64 = conn
            .query_row("SELECT count(*) FROM runs", [], |row| row.get(0))
            .unwrap();
        assert_eq!(runs, 1, "adoption must not disturb data");
    }

    #[test]
    fn test_live_shaped_db_without_version_metadata_is_migrated() {
        let dir = tempfile::tempdir().unwrap();
        let path = temp_db_path(&dir, "bare.db");
        {
            let conn = Connection::open(&path).unwrap();
            live_tables(&conn);
            conn.execute_batch("DELETE FROM schema_version;").unwrap();
        }

        let conn = open_responsibility_db(&path).unwrap();
        assert_header(&conn, LATEST_SCHEMA_VERSION);
    }

    fn assert_zero_write(mut open: impl FnMut() -> Result<Connection, SchemaError>, path: &str) {
        let before = std::fs::read(path).unwrap();
        let err = open().unwrap_err();
        let after = std::fs::read(path).unwrap();
        assert_eq!(before, after, "rejection must not modify the database file");
        // The error is the contract; print it so a wrong variant is diagnosable.
        assert!(
            matches!(
                err,
                SchemaError::LegacyCausalSchema
                    | SchemaError::UnknownSchema
                    | SchemaError::UnsupportedSchemaVersion { .. }
            ),
            "unexpected rejection: {err}"
        );
    }

    #[test]
    fn test_legacy_causal_db_is_rejected_without_writes() {
        let dir = tempfile::tempdir().unwrap();
        let path = temp_db_path(&dir, "causal.db");
        {
            let conn = Connection::open(&path).unwrap();
            conn.execute_batch(
                "CREATE TABLE agent_instances (agent_id TEXT PRIMARY KEY);
                 CREATE TABLE correlation_evidence (evidence_id TEXT PRIMARY KEY, candidate_id TEXT);",
            )
            .unwrap();
        }
        assert_zero_write(|| open_responsibility_db(&path), &path);
        let err = open_responsibility_db(&path).unwrap_err();
        assert!(matches!(err, SchemaError::LegacyCausalSchema), "{err}");
    }

    #[test]
    fn test_partial_causal_shape_is_recognised() {
        let dir = tempfile::tempdir().unwrap();
        let path = temp_db_path(&dir, "partial-causal.db");
        {
            let conn = Connection::open(&path).unwrap();
            // Only the legacy shape of a shared table — no table-name marker.
            conn.execute_batch(
                "CREATE TABLE correlation_evidence (
                    schema_version INTEGER NOT NULL,
                    evidence_id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    intent_id TEXT NOT NULL,
                    candidate_id TEXT NOT NULL
                );",
            )
            .unwrap();
        }
        assert_eq!(
            classify_schema(&Connection::open(&path).unwrap()).unwrap(),
            SchemaKind::LegacyCausal
        );
        assert_zero_write(|| open_responsibility_db(&path), &path);
    }

    #[test]
    fn test_unknown_schema_is_rejected_without_writes() {
        let dir = tempfile::tempdir().unwrap();
        let path = temp_db_path(&dir, "foreign.db");
        {
            let conn = Connection::open(&path).unwrap();
            conn.execute_batch("CREATE TABLE notes (body TEXT);")
                .unwrap();
        }
        assert_zero_write(|| open_responsibility_db(&path), &path);
        let err = open_responsibility_db(&path).unwrap_err();
        assert!(matches!(err, SchemaError::UnknownSchema), "{err}");
    }

    #[test]
    fn test_future_schema_version_is_rejected_without_writes() {
        let dir = tempfile::tempdir().unwrap();
        let path = temp_db_path(&dir, "future.db");
        {
            let conn = Connection::open(&path).unwrap();
            live_tables(&conn);
            conn.execute_batch("PRAGMA user_version = 99;").unwrap();
        }
        assert_zero_write(|| open_responsibility_db(&path), &path);
        let err = open_responsibility_db(&path).unwrap_err();
        assert!(
            matches!(err, SchemaError::UnsupportedSchemaVersion { found: 99, .. }),
            "{err}"
        );
    }

    fn failing_migration(conn: &Connection) -> rusqlite::Result<()> {
        conn.execute_batch("CREATE TABLE half_applied (x INTEGER);")?;
        Err(rusqlite::Error::SqliteFailure(
            rusqlite::ffi::Error::new(rusqlite::ffi::SQLITE_ERROR),
            Some("synthetic failure".to_string()),
        ))
    }

    #[test]
    fn test_failed_migration_rolls_back_and_reopen_recovers() {
        let dir = tempfile::tempdir().unwrap();
        let path = temp_db_path(&dir, "rollback.db");
        let conn = Connection::open(&path).unwrap();

        let ladder = [Migration {
            version: 1,
            apply: failing_migration,
        }];
        let err = run_migration_ladder(&conn, &ladder, 0).unwrap_err();
        assert!(
            matches!(err, SchemaError::Migration { version: 1, .. }),
            "{err}"
        );

        let half: i64 = conn
            .query_row(
                "SELECT count(*) FROM sqlite_master WHERE name = 'half_applied'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(half, 0, "a failed migration must leave no partial DDL");
        let user_version: i64 = conn
            .query_row("PRAGMA user_version", [], |row| row.get(0))
            .unwrap();
        assert_eq!(
            user_version, 0,
            "a failed migration must not bump the header"
        );

        // Reopening with the real ladder must succeed — the failure is not
        // permanent corruption.
        drop(conn);
        let conn = open_responsibility_db(&path).unwrap();
        assert_header(&conn, LATEST_SCHEMA_VERSION);
    }

    #[test]
    fn test_read_only_validation_rejects_causal_and_unknown() {
        let dir = tempfile::tempdir().unwrap();
        let unknown = temp_db_path(&dir, "foreign.db");
        {
            let conn = Connection::open(&unknown).unwrap();
            conn.execute_batch("CREATE TABLE notes (body TEXT);")
                .unwrap();
        }
        assert!(matches!(
            open_responsibility_db_read_only(&unknown),
            Err(SchemaError::UnknownSchema)
        ));

        // With the causal routes retired, no reader may open a causal file.
        let causal = temp_db_path(&dir, "causal.db");
        {
            let conn = Connection::open(&causal).unwrap();
            conn.execute_batch("CREATE TABLE agent_instances (agent_id TEXT PRIMARY KEY);")
                .unwrap();
        }
        assert!(matches!(
            open_responsibility_db_read_only(&causal),
            Err(SchemaError::LegacyCausalSchema)
        ));
    }

    #[test]
    fn test_persistence_reader_uses_shared_validation() {
        let dir = tempfile::tempdir().unwrap();
        let path = temp_db_path(&dir, "foreign.db");
        {
            let conn = Connection::open(&path).unwrap();
            conn.execute_batch("CREATE TABLE notes (body TEXT);")
                .unwrap();
        }
        let err = match crate::responsibility::persistence::PersistenceReader::open(&path) {
            Ok(_) => panic!("unknown schema must be rejected by PersistenceReader::open"),
            Err(err) => err,
        };
        assert!(matches!(err, SchemaError::UnknownSchema), "{err}");
    }

    #[test]
    fn test_future_version_hidden_in_schema_version_table_is_rejected_without_writes() {
        let dir = tempfile::tempdir().unwrap();
        let path = temp_db_path(&dir, "future-table.db");
        {
            let conn = Connection::open(&path).unwrap();
            live_tables(&conn);
            conn.execute_batch(
                "DELETE FROM schema_version;
                 INSERT INTO schema_version(version) VALUES (99);",
            )
            .unwrap();
        }

        let files_before = fs_listing(&dir);
        let before = std::fs::read(&path).unwrap();
        let err = open_responsibility_db(&path).unwrap_err();
        let after = std::fs::read(&path).unwrap();

        assert!(
            matches!(err, SchemaError::UnsupportedSchemaVersion { found: 99, .. }),
            "{err}"
        );
        assert_eq!(before, after, "the rejection path must not write the file");
        assert_eq!(
            fs_listing(&dir),
            files_before,
            "no backup or journal file may appear for a rejected database"
        );
    }

    fn fs_listing(dir: &tempfile::TempDir) -> Vec<String> {
        let mut names: Vec<String> = std::fs::read_dir(dir.path())
            .unwrap()
            .map(|entry| entry.unwrap().file_name().to_string_lossy().into_owned())
            .collect();
        names.sort();
        names
    }

    #[test]
    fn test_foreign_application_id_with_live_table_names_is_rejected() {
        let dir = tempfile::tempdir().unwrap();
        let path = temp_db_path(&dir, "foreign-app.db");
        {
            let conn = Connection::open(&path).unwrap();
            conn.execute_batch(
                "CREATE TABLE runs (id INTEGER PRIMARY KEY);
                 CREATE TABLE process_events (id INTEGER PRIMARY KEY);
                 CREATE TABLE intents (id INTEGER PRIMARY KEY);
                 PRAGMA application_id = 0x11111111;",
            )
            .unwrap();
        }
        assert_zero_write(|| open_responsibility_db(&path), &path);
        let err = open_responsibility_db(&path).unwrap_err();
        assert!(matches!(err, SchemaError::UnknownSchema), "{err}");
    }

    #[test]
    fn test_hot_wal_legacy_causal_is_rejected_without_checkpointing() {
        let dir = tempfile::tempdir().unwrap();
        let live = temp_db_path(&dir, "causal-live.db");
        let copy = temp_db_path(&dir, "causal-copy.db");
        {
            let conn = Connection::open(&live).unwrap();
            conn.execute_batch("PRAGMA journal_mode=WAL;").unwrap();
            conn.execute_batch(
                "CREATE TABLE agent_instances (agent_id TEXT PRIMARY KEY, payload TEXT);
                 INSERT INTO agent_instances VALUES ('a', 'kept-in-wal');",
            )
            .unwrap();
            // Copy the database together with its hot WAL: this is the on-disk
            // state a killed collector leaves behind.
            std::fs::copy(&live, &copy).unwrap();
            std::fs::copy(format!("{live}-wal"), format!("{copy}-wal")).unwrap();
        }

        let before = std::fs::read(&copy).unwrap();
        let wal_before = std::fs::read(format!("{copy}-wal")).unwrap();
        let err = open_responsibility_db(&copy).unwrap_err();
        let after = std::fs::read(&copy).unwrap();

        assert!(matches!(err, SchemaError::LegacyCausalSchema), "{err}");
        assert_eq!(before, after, "a hot WAL must not be checkpointed");
        assert_eq!(
            std::fs::read(format!("{copy}-wal")).unwrap(),
            wal_before,
            "the WAL must survive a rejected open unchanged"
        );
    }

    #[test]
    fn test_v2_creates_the_backup_once_and_never_overwrites_it() {
        let dir = tempfile::tempdir().unwrap();
        let path = temp_db_path(&dir, "backup.db");
        {
            let conn = Connection::open(&path).unwrap();
            live_tables(&conn);
            conn.execute_batch("INSERT OR REPLACE INTO schema_version(version) VALUES (1);")
                .unwrap();
        }

        let _ = open_responsibility_db(&path).unwrap();
        let backup = format!("{path}.pre-v2.bak");
        assert!(
            std::path::Path::new(&backup).is_file(),
            "v2 must leave a recoverable copy"
        );

        // D5: a leftover backup from an earlier attempt must not block a
        // reopen, and must not be overwritten.
        let sentinel = b"sentinel";
        std::fs::write(&backup, sentinel).unwrap();
        let conn = open_responsibility_db(&path).unwrap();
        assert_eq!(
            conn.query_row("PRAGMA user_version", [], |r| r.get::<_, i64>(0))
                .unwrap(),
            2
        );
        drop(conn);
        assert_eq!(std::fs::read(&backup).unwrap(), sentinel);
    }

    #[test]
    fn test_v2_succeeds_when_v1_never_had_an_identity_map() {
        let dir = tempfile::tempdir().unwrap();
        let path = temp_db_path(&dir, "no-identity.db");
        {
            let conn = Connection::open(&path).unwrap();
            live_tables(&conn);
            conn.execute_batch("DROP TABLE identity_process_map;")
                .unwrap();
        }

        let conn = open_responsibility_db(&path).unwrap();
        assert_header(&conn, LATEST_SCHEMA_VERSION);
        let views: i64 = conn
            .query_row(
                "SELECT count(*) FROM sqlite_master
                 WHERE type='view' AND name IN (
                     'identity_process_map',
                     'identity_process_map_by_run',
                     'identity_process_belief_windows')",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(views, 3, "views must exist even with no identity data");
    }

    #[test]
    fn test_identity_map_view_prefers_the_strongest_belief() {
        let conn = Connection::open_in_memory().unwrap();
        run_migrations(&conn).unwrap();
        conn.execute_batch(
            "INSERT INTO identity_process_beliefs
                 (agent_id, pid, start_time_ns, run_id, belief_level, observed_at_ns, source)
             VALUES ('provisional-agent', 7, 70, 'r', 'Provisional', 100, 'phase1'),
                    ('authoritative-agent', 7, 70, 'r', 'Authoritative', 50, 'phase2'),
                    ('late-weak-agent', 7, 70, 'r', 'Provisional', 900, 'late');",
        )
        .unwrap();

        let mapped: String = conn
            .query_row(
                "SELECT agent_id FROM identity_process_map WHERE pid = 7 AND start_time_ns = 70",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(
            mapped, "authoritative-agent",
            "a late weak observation must not override the authoritative belief"
        );

        let windows: i64 = conn
            .query_row(
                "SELECT count(*) FROM identity_process_belief_windows WHERE pid = 7",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(windows, 3);
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
