// SPDX-License-Identifier: Apache-2.0 OR MIT
//! Upgrade contract against a real, committed v1 database.
//!
//! The fixture (`tests/fixtures/schema/live-v1.db`) is produced by
//! `scripts/schema/make_v1_fixture.py` from the frozen v1 DDL and stamped the
//! way the historical writer stamped it: `user_version = 1`,
//! `application_id = 0`. These tests exist so a third party can falsify the
//! adoption claim without trusting the code that implements it.

use std::collections::BTreeSet;
use std::path::Path;

use mtagentlens::responsibility::persistence::schema::{
    APPLICATION_ID, LATEST_SCHEMA_VERSION, SCHEMA_V1_DDL, SchemaKind, classify_schema,
    open_responsibility_db, open_responsibility_db_read_only,
};
use rusqlite::Connection;

fn fixture_path() -> &'static Path {
    Path::new(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/tests/fixtures/schema/live-v1.db"
    ))
}

fn table_names(conn: &Connection) -> BTreeSet<String> {
    let mut stmt = conn
        .prepare("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")
        .unwrap();
    stmt.query_map([], |row| row.get::<_, String>(0))
        .unwrap()
        .map(|row| row.unwrap())
        .collect()
}

fn columns(conn: &Connection, table: &str) -> BTreeSet<(String, String, i64)> {
    let mut stmt = conn
        .prepare(&format!("PRAGMA table_info({table})"))
        .unwrap();
    stmt.query_map([], |row| {
        Ok((
            row.get::<_, String>(1)?,
            row.get::<_, String>(2)?,
            row.get::<_, i64>(3)?,
        ))
    })
    .unwrap()
    .map(|row| row.unwrap())
    .collect()
}

fn index_names(conn: &Connection) -> BTreeSet<String> {
    let mut stmt = conn
        .prepare("SELECT name FROM sqlite_master WHERE type = 'index' AND name NOT LIKE 'sqlite_%'")
        .unwrap();
    stmt.query_map([], |row| row.get::<_, String>(0))
        .unwrap()
        .map(|row| row.unwrap())
        .collect()
}

/// D4: totals are compared per table, but `schema_version` is excluded (v2
/// legitimately rewrites it) and identity is compared as a multiset rather
/// than a primary-key digest (v2 changes the identity primary key).
fn table_row_counts(conn: &Connection) -> Vec<(String, i64)> {
    let tables = table_names(conn);
    let mut counts = Vec::new();
    for table in tables {
        if table == "schema_version" || table.starts_with("sqlite_") {
            continue;
        }
        // v1 stores identity as a table, v2 as beliefs + compatibility view.
        // The mapping itself is compared as a multiset below.
        if table == "identity_process_map" || table == "identity_process_beliefs" {
            continue;
        }
        let count: i64 = conn
            .query_row(&format!("SELECT count(*) FROM \"{table}\""), [], |row| {
                row.get(0)
            })
            .unwrap();
        counts.push((table, count));
    }
    counts
}

fn identity_multiset(conn: &Connection) -> BTreeSet<(String, i64, i64, String)> {
    let mut stmt = conn
        .prepare(
            "SELECT agent_id, pid, start_time_ns, run_id FROM identity_process_map
             ORDER BY agent_id, pid, start_time_ns, run_id",
        )
        .unwrap();
    stmt.query_map([], |row| {
        Ok((
            row.get::<_, String>(0)?,
            row.get::<_, i64>(1)?,
            row.get::<_, i64>(2)?,
            row.get::<_, String>(3)?,
        ))
    })
    .unwrap()
    .map(|row| row.unwrap())
    .collect()
}

fn copy_fixture(dir: &tempfile::TempDir) -> String {
    let target = dir.path().join("live-v1.db");
    std::fs::copy(fixture_path(), &target).unwrap();
    target.to_string_lossy().into_owned()
}

#[test]
fn fixture_is_a_faithful_v1_database() {
    let fixture = fixture_path();
    assert!(fixture.is_file(), "fixture missing: {}", fixture.display());

    let conn = Connection::open(fixture).unwrap();
    let recorded: i64 = conn
        .query_row("PRAGMA user_version", [], |row| row.get(0))
        .unwrap();
    assert_eq!(recorded, 1, "the fixture must claim v1");
    let application_id: i64 = conn
        .query_row("PRAGMA application_id", [], |row| row.get(0))
        .unwrap();
    assert_eq!(
        application_id, 0,
        "historical writers never stamped the header"
    );
    assert_eq!(
        classify_schema(&conn).unwrap(),
        SchemaKind::Live,
        "the fixture must be positively identifiable as a live database"
    );
}

#[test]
fn fixture_columns_match_the_frozen_v1_ddl() {
    let ddl_conn = Connection::open_in_memory().unwrap();
    ddl_conn.execute_batch(SCHEMA_V1_DDL).unwrap();

    let fixture = Connection::open(fixture_path()).unwrap();
    assert_eq!(
        table_names(&fixture),
        table_names(&ddl_conn),
        "table set drifted from the frozen v1 DDL"
    );
    assert_eq!(
        index_names(&fixture),
        index_names(&ddl_conn),
        "index set drifted from the frozen v1 DDL"
    );
    for table in table_names(&fixture) {
        assert_eq!(
            columns(&fixture, &table),
            columns(&ddl_conn, &table),
            "columns of {table} drifted from the frozen v1 DDL"
        );
    }
}

#[test]
fn adopting_a_v1_database_stamps_the_header_and_preserves_data() {
    let dir = tempfile::tempdir().unwrap();
    let path = copy_fixture(&dir);

    let before = {
        let conn = Connection::open(&path).unwrap();
        (table_row_counts(&conn), identity_multiset(&conn))
    };

    let conn = open_responsibility_db(&path).unwrap();

    let user_version: i64 = conn
        .query_row("PRAGMA user_version", [], |row| row.get(0))
        .unwrap();
    assert_eq!(user_version, LATEST_SCHEMA_VERSION);
    let application_id: i64 = conn
        .query_row("PRAGMA application_id", [], |row| row.get(0))
        .unwrap();
    assert_eq!(application_id, APPLICATION_ID);

    let after = (table_row_counts(&conn), identity_multiset(&conn));
    let mut expected_counts = before.0;
    expected_counts.push(("analysis_checkpoints".into(), 0));
    expected_counts.sort();
    assert_eq!(
        expected_counts, after.0,
        "adoption must preserve every legacy row and add only the empty checkpoint table"
    );
    assert_eq!(before.1, after.1, "identity mappings must survive adoption");

    // v2 surface: the belief table is authoritative and the old name is a
    // view over it, so every existing reader keeps working.
    let beliefs: i64 = conn
        .query_row("SELECT count(*) FROM identity_process_beliefs", [], |row| {
            row.get(0)
        })
        .unwrap();
    assert_eq!(beliefs, 2);
    let levels: BTreeSet<String> = conn
        .prepare("SELECT belief_level FROM identity_process_beliefs")
        .unwrap()
        .query_map([], |row| row.get::<_, String>(0))
        .unwrap()
        .map(|row| row.unwrap())
        .collect();
    assert_eq!(
        levels,
        BTreeSet::from(["Authoritative".to_string()]),
        "a process whose agent is authoritative must not be migrated as unknown"
    );
    for view in [
        "identity_process_map",
        "identity_process_map_by_run",
        "identity_process_belief_windows",
    ] {
        let kind: String = conn
            .query_row(
                "SELECT type FROM sqlite_master WHERE name = ?1",
                [view],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(kind, "view", "{view} must be a compatibility view");
    }
    // The frontend contract: this exact projection is what db-import.ts runs.
    let mapped: Vec<(String, i64, i64)> = conn
        .prepare("SELECT agent_id, pid, start_time_ns FROM identity_process_map ORDER BY pid")
        .unwrap()
        .query_map([], |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)))
        .unwrap()
        .map(|row| row.unwrap())
        .collect();
    assert_eq!(
        mapped,
        vec![
            ("agent-authoritative".to_string(), 100, 1000),
            ("agent-authoritative".to_string(), 200, 2000),
        ]
    );
    let windows: i64 = conn
        .query_row(
            "SELECT count(*) FROM identity_process_belief_windows",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(windows, 2);
}

#[test]
fn read_only_open_does_not_modify_a_v1_database() {
    let dir = tempfile::tempdir().unwrap();
    let path = copy_fixture(&dir);
    let before = std::fs::read(&path).unwrap();

    let conn = open_responsibility_db_read_only(&path).unwrap();
    let runs: i64 = conn
        .query_row("SELECT count(*) FROM runs", [], |row| row.get(0))
        .unwrap();
    assert_eq!(runs, 1);
    drop(conn);

    let after = std::fs::read(&path).unwrap();
    assert_eq!(before, after, "read-only open must not write the file");
}

#[test]
fn migration_v5_adds_native_columns_and_preserves_old_intents() {
    let dir = tempfile::tempdir().unwrap();
    let path = copy_fixture(&dir);
    let before: Vec<(String, String, i64)> = {
        let conn = Connection::open(&path).unwrap();
        conn.prepare("SELECT intent_id, tool_name, timestamp_ns FROM intents ORDER BY intent_id")
            .unwrap()
            .query_map([], |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)))
            .unwrap()
            .map(|row| row.unwrap())
            .collect()
    };
    assert!(!before.is_empty(), "the v1 fixture must carry old intents");

    let conn = open_responsibility_db(&path).unwrap();
    let version: i64 = conn
        .query_row(
            "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(version, LATEST_SCHEMA_VERSION);

    let cols = columns(&conn, "intents");
    for expected in ["native_tool_call_json", "declared_conversation_id"] {
        assert!(
            cols.iter().any(|(name, _, _)| name == expected),
            "missing migrated column {expected}"
        );
    }
    let after: Vec<(String, String, i64)> = conn
        .prepare("SELECT intent_id, tool_name, timestamp_ns FROM intents ORDER BY intent_id")
        .unwrap()
        .query_map([], |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)))
        .unwrap()
        .map(|row| row.unwrap())
        .collect();
    assert_eq!(after, before, "old intent rows must survive the upgrade");

    let native_nulls: Vec<(Option<String>, Option<String>)> = conn
        .prepare(
            "SELECT native_tool_call_json, declared_conversation_id FROM intents ORDER BY intent_id",
        )
        .unwrap()
        .query_map([], |row| Ok((row.get(0)?, row.get(1)?)))
        .unwrap()
        .map(|row| row.unwrap())
        .collect();
    assert!(
        native_nulls
            .iter()
            .all(|(native, conversation)| { native.is_none() && conversation.is_none() }),
        "old rows must read as absent native references, never synthesised ids"
    );
}
