// SPDX-License-Identifier: Apache-2.0 OR MIT
// Schema adoption probe: open a responsibility database the way the collector
// does and print its resulting structure as JSON.
//
// Usage: cargo run --example schema_adopt -- <db> [<db> ...]
//
// Used by scripts/schema/sweep_legacy_dbs.py to prove that real archived
// databases are adopted (not merely "openable") and to fingerprint their
// column sets before/after migration.

use mtagentlens::responsibility::persistence::schema::open_responsibility_db;
use serde_json::json;
use std::collections::BTreeMap;

fn structure(conn: &rusqlite::Connection) -> serde_json::Value {
    let mut stmt = conn
        .prepare(
            "SELECT type, name FROM sqlite_master
             WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%'
             ORDER BY type, name",
        )
        .unwrap();
    let objects: Vec<(String, String)> = stmt
        .query_map([], |row| Ok((row.get(0)?, row.get(1)?)))
        .unwrap()
        .map(|row| row.unwrap())
        .collect();

    let mut tables = BTreeMap::new();
    let mut views = BTreeMap::new();
    for (kind, name) in objects {
        let mut cols = conn.prepare(&format!("PRAGMA table_info({name})")).unwrap();
        let columns: Vec<String> = cols
            .query_map([], |row| row.get::<_, String>(1))
            .unwrap()
            .map(|row| row.unwrap())
            .collect();
        if kind == "view" {
            views.insert(name, columns);
        } else {
            tables.insert(name, columns);
        }
    }

    json!({
        "tables": tables,
        "views": views,
    })
}

fn main() {
    for path in std::env::args().skip(1) {
        let before = match rusqlite::Connection::open_with_flags(
            &path,
            rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY,
        ) {
            Ok(conn) => structure(&conn),
            Err(err) => {
                println!(
                    "{}",
                    json!({"path": path, "ok": false, "stage": "read-before", "error": err.to_string()})
                );
                continue;
            }
        };

        match open_responsibility_db(&path) {
            Ok(conn) => {
                let after = structure(&conn);
                let user_version: i64 = conn
                    .query_row("PRAGMA user_version", [], |row| row.get(0))
                    .unwrap();
                let application_id: i64 = conn
                    .query_row("PRAGMA application_id", [], |row| row.get(0))
                    .unwrap();
                let beliefs: i64 = conn
                    .query_row("SELECT COUNT(*) FROM identity_process_beliefs", [], |row| {
                        row.get(0)
                    })
                    .unwrap_or(-1);
                println!(
                    "{}",
                    json!({
                        "path": path,
                        "ok": true,
                        "user_version": user_version,
                        "application_id": application_id,
                        "beliefs": beliefs,
                        "before": before,
                        "after": after,
                    })
                );
            }
            Err(err) => {
                println!(
                    "{}",
                    json!({"path": path, "ok": false, "stage": "adopt", "error": err.to_string()})
                );
            }
        }
    }
}
