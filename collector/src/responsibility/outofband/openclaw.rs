// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 MtAgentLens Project

//! Reader for OpenClaw's trajectory records.
//!
//! Opens the agent database **read-only** — this is not instrumentation of the
//! agent, only reading what the agent wrote down itself.
//!
//! Verified schema (2026-10-12):
//! `trajectory_runtime_events(created_at, event_json, run_id, seq, session_id)`

use super::profile::{OutOfBandKind, OutOfBandProfile};
use super::types::{OutOfBandIdentity, OutOfBandToolCall};
use rusqlite::{Connection, OpenFlags};
use std::path::{Path, PathBuf};

#[derive(Debug)]
pub enum ReadError {
    Io(std::io::Error),
    Sqlite(rusqlite::Error),
    /// `traceSchema` / `schemaVersion` did not match the profile.
    Schema {
        found: Option<u32>,
        expected: u32,
    },
}

impl std::fmt::Display for ReadError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Io(e) => write!(f, "out-of-band io error: {e}"),
            Self::Sqlite(e) => write!(f, "out-of-band sqlite error: {e}"),
            Self::Schema { found, expected } => write!(
                f,
                "out-of-band schema mismatch: found {found:?}, expected {expected}"
            ),
        }
    }
}

impl std::error::Error for ReadError {}

#[derive(Clone)]
pub struct OpenClawReader {
    state_dir: PathBuf,
    expected_schema: u32,
}

/// One persisted tool-call row plus the ordering key used by incremental reads.
#[derive(Debug, Clone, PartialEq)]
pub struct OutOfBandRow {
    pub created_at: i64,
    pub seq: i64,
    pub call: OutOfBandToolCall,
}

impl OpenClawReader {
    pub fn open(profile: &OutOfBandProfile) -> Result<Self, ReadError> {
        if profile.kind != OutOfBandKind::OpenClawTrajectory {
            return Err(ReadError::Schema {
                found: None,
                expected: profile.schema_version,
            });
        }
        Ok(Self {
            state_dir: profile.state_dir.clone(),
            expected_schema: profile.schema_version,
        })
    }

    /// `<state_dir>/agents/<agent_id>/agent/openclaw-agent.sqlite`
    pub fn agent_db_path(&self, agent_id: &str) -> PathBuf {
        self.state_dir
            .join("agents")
            .join(agent_id)
            .join("agent")
            .join("openclaw-agent.sqlite")
    }

    fn connect(&self, agent_id: &str) -> Result<Connection, ReadError> {
        Connection::open_with_flags(
            self.agent_db_path(agent_id),
            OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
        )
        .map_err(ReadError::Sqlite)
    }

    /// True when the newest event's `schemaVersion` and `traceSchema` match.
    ///
    /// Guards against silently misparsing a future format.
    pub fn schema_version_ok(&self, agent_id: &str) -> bool {
        let Ok(conn) = self.connect(agent_id) else {
            return false;
        };
        let Ok(json) = conn.query_row(
            "SELECT event_json FROM trajectory_runtime_events \
             WHERE event_json IS NOT NULL ORDER BY created_at DESC LIMIT 1",
            [],
            |r| r.get::<_, String>(0),
        ) else {
            return false;
        };
        let Ok(v) = serde_json::from_str::<serde_json::Value>(&json) else {
            return false;
        };
        v.get("schemaVersion").and_then(|x| x.as_u64()) == Some(self.expected_schema as u64)
            && v.get("traceSchema").and_then(|x| x.as_str()) == Some("openclaw-trajectory")
    }

    /// Latest `(created_at, seq)` for an agent, or `(0, 0)` when empty.
    ///
    /// Used to start a live reader at "now" instead of replaying the whole
    /// trajectory history into a new run.
    pub fn latest_cursor(&self, agent_id: &str) -> Result<(i64, i64), ReadError> {
        let conn = self.connect(agent_id)?;
        match conn.query_row(
            "SELECT created_at, seq FROM trajectory_runtime_events \
             ORDER BY created_at DESC, seq DESC LIMIT 1",
            [],
            |r| Ok((r.get::<_, i64>(0)?, r.get::<_, i64>(1)?)),
        ) {
            Ok(cursor) => Ok(cursor),
            Err(rusqlite::Error::QueryReturnedNoRows) => Ok((0, 0)),
            Err(e) => Err(ReadError::Sqlite(e)),
        }
    }

    /// Tool calls strictly after `(after_created_at, after_seq)`, oldest first.
    ///
    /// `seq` is per-session, so the tuple is a composite ordering key; two
    /// rows with the same timestamp and sequence are rare (millisecond clock)
    /// and the runtime's content dedup absorbs a duplicate.
    pub fn read_tool_calls_since(
        &self,
        agent_id: &str,
        after_created_at: i64,
        after_seq: i64,
    ) -> Result<Vec<OutOfBandRow>, ReadError> {
        let conn = self.connect(agent_id)?;
        let mut stmt = conn
            .prepare(
                "SELECT created_at, seq, event_json FROM trajectory_runtime_events \
                 WHERE event_json LIKE '%\"tool.call\"%' \
                   AND (created_at > ?1 OR (created_at = ?1 AND seq > ?2)) \
                 ORDER BY created_at ASC, seq ASC",
            )
            .map_err(ReadError::Sqlite)?;
        let rows = stmt
            .query_map(rusqlite::params![after_created_at, after_seq], |r| {
                Ok((
                    r.get::<_, i64>(0)?,
                    r.get::<_, i64>(1)?,
                    r.get::<_, String>(2)?,
                ))
            })
            .map_err(ReadError::Sqlite)?;
        let mut out = Vec::new();
        for row in rows {
            let (created_at, seq, json) = row.map_err(ReadError::Sqlite)?;
            if let Some(call) = parse_tool_call(&json) {
                out.push(OutOfBandRow {
                    created_at,
                    seq,
                    call,
                });
            }
        }
        Ok(out)
    }

    /// Read every recorded tool call, oldest first.
    ///
    /// Ordering is by `created_at` — NOT `seq`, which is per-session and would
    /// silently return an older session's rows.
    pub fn read_tool_calls(&self, agent_id: &str) -> Result<Vec<OutOfBandToolCall>, ReadError> {
        let conn = self.connect(agent_id)?;
        let mut stmt = conn
            .prepare(
                "SELECT event_json FROM trajectory_runtime_events \
                 WHERE event_json LIKE '%\"tool.call\"%' ORDER BY created_at ASC",
            )
            .map_err(ReadError::Sqlite)?;

        let rows = stmt
            .query_map([], |r| r.get::<_, String>(0))
            .map_err(ReadError::Sqlite)?;

        let mut out = Vec::new();
        for row in rows {
            let json = row.map_err(ReadError::Sqlite)?;
            if let Some(call) = parse_tool_call(&json) {
                out.push(call);
            }
        }
        Ok(out)
    }
}

/// Parse one `tool.call` record.
///
/// Returns `None` for other event types and for records missing required
/// fields — a malformed record is skipped, not fatal.
fn parse_tool_call(json: &str) -> Option<OutOfBandToolCall> {
    let v: serde_json::Value = serde_json::from_str(json).ok()?;
    if v.get("type").and_then(|x| x.as_str()) != Some("tool.call") {
        return None;
    }
    let data = v.get("data")?;
    let tool_name = data.get("name").and_then(|x| x.as_str())?.to_string();
    let args = data.get("args").cloned().unwrap_or(serde_json::Value::Null);
    let agent_key = v.get("sessionKey").and_then(|x| x.as_str())?.to_string();
    let run_id = v.get("runId").and_then(|x| x.as_str()).map(str::to_string);
    let ts_ns = v
        .get("ts")
        .and_then(|x| x.as_str())
        .and_then(iso8601_to_epoch_ns)
        .unwrap_or(0);

    Some(OutOfBandToolCall {
        tool_name,
        args,
        agent_key,
        run_id,
        ts_ns,
    })
}

/// Convert an ISO-8601 UTC timestamp (`2026-10-12T06:09:58.375Z`) to epoch ns.
///
/// Hand-rolled to avoid pulling a date crate for one fixed format.
fn iso8601_to_epoch_ns(ts: &str) -> Option<u64> {
    let (date, rest) = ts.split_once('T')?;
    let rest = rest.trim_end_matches('Z');
    let (time, frac) = match rest.split_once('.') {
        Some((t, f)) => (t, f),
        None => (rest, ""),
    };

    let mut d = date.split('-');
    let year: i64 = d.next()?.parse().ok()?;
    let month: i64 = d.next()?.parse().ok()?;
    let day: i64 = d.next()?.parse().ok()?;

    let mut t = time.split(':');
    let hour: i64 = t.next()?.parse().ok()?;
    let minute: i64 = t.next()?.parse().ok()?;
    let second: i64 = t.next()?.parse().ok()?;

    if !(1..=12).contains(&month) || !(1..=31).contains(&day) {
        return None;
    }

    // Days since the Unix epoch (Howard Hinnant's civil-from-days, inverted).
    let y = if month <= 2 { year - 1 } else { year };
    let era = if y >= 0 { y } else { y - 399 } / 400;
    let yoe = y - era * 400;
    let mp = (month + 9) % 12;
    let doy = (153 * mp + 2) / 5 + day - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    let days = era * 146097 + doe - 719468;

    let secs = days * 86400 + hour * 3600 + minute * 60 + second;
    if secs < 0 {
        return None;
    }

    let frac_ns: u64 = if frac.is_empty() {
        0
    } else {
        let mut f = frac.to_string();
        f.truncate(9);
        while f.len() < 9 {
            f.push('0');
        }
        f.parse().ok()?
    };

    Some(secs as u64 * 1_000_000_000 + frac_ns)
}

/// Identity asserted by the agent, derived from a tool call record.
pub fn identity_from_call(
    call: &OutOfBandToolCall,
    workspace_dir: Option<String>,
) -> OutOfBandIdentity {
    OutOfBandIdentity {
        agent_key: call.agent_key.clone(),
        workspace_dir,
    }
}

/// Build a profile pointing at `state_dir` for the given schema version.
pub fn profile_for(state_dir: &Path, schema_version: u32) -> OutOfBandProfile {
    OutOfBandProfile {
        agent: "openclaw".to_string(),
        kind: OutOfBandKind::OpenClawTrajectory,
        state_dir: state_dir.to_path_buf(),
        schema_version,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const CALL_JSON: &str = r#"{"traceSchema":"openclaw-trajectory","schemaVersion":1,"type":"tool.call","ts":"2026-10-12T06:09:58.375Z","sessionKey":"agent:main:main","runId":"run-1","data":{"toolCallId":"x","name":"ls","args":{"path":"/tmp/x"}}}"#;

    fn make_db(path: &Path) {
        let conn = Connection::open(path).unwrap();
        conn.execute_batch(
            "CREATE TABLE trajectory_runtime_events (
                 session_id TEXT, seq INTEGER, run_id TEXT,
                 event_json TEXT, created_at INTEGER);",
        )
        .unwrap();
        conn.execute(
            "INSERT INTO trajectory_runtime_events VALUES (?1,?2,?3,?4,?5)",
            rusqlite::params!["s1", 1, "run-1", CALL_JSON, 100i64],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO trajectory_runtime_events VALUES (?1,?2,?3,?4,?5)",
            rusqlite::params![
                "s1",
                2,
                "run-1",
                r#"{"traceSchema":"openclaw-trajectory","type":"session.started"}"#,
                200i64
            ],
        )
        .unwrap();
    }

    fn reader_for(dir: &Path) -> OpenClawReader {
        OpenClawReader {
            state_dir: dir.to_path_buf(),
            expected_schema: 1,
        }
    }

    #[test]
    fn parses_iso8601_to_epoch_ns() {
        let got = iso8601_to_epoch_ns("2026-10-12T06:09:58.375Z").unwrap();
        assert_eq!(
            got % 1_000_000_000,
            375_000_000,
            "fractional part preserved"
        );
        assert_eq!(got / 1_000_000_000, 1_791_785_398, "whole seconds match");
    }

    #[test]
    fn parses_tool_call_fields() {
        let c = parse_tool_call(CALL_JSON).expect("should parse");
        assert_eq!(c.tool_name, "ls");
        assert_eq!(c.args["path"], "/tmp/x");
        assert_eq!(c.agent_key, "agent:main:main");
        assert_eq!(c.run_id.as_deref(), Some("run-1"));
        assert!(c.ts_ns > 0);
    }

    #[test]
    fn ignores_non_tool_call_events() {
        let other = r#"{"traceSchema":"openclaw-trajectory","type":"session.started"}"#;
        assert!(parse_tool_call(other).is_none());
    }

    #[test]
    fn incremental_read_uses_composite_cursor() {
        let dir = tempfile::tempdir().unwrap();
        let db_dir = dir.path().join("agents").join("main").join("agent");
        std::fs::create_dir_all(&db_dir).unwrap();
        make_db(&db_dir.join("openclaw-agent.sqlite"));

        let r = reader_for(dir.path());
        assert_eq!(r.latest_cursor("main").unwrap(), (200, 2));
        assert!(
            r.read_tool_calls_since("main", 100, 1).unwrap().is_empty(),
            "the row at (100, 1) is not strictly after the cursor"
        );
        let rows = r.read_tool_calls_since("main", 0, 0).unwrap();
        assert_eq!(rows.len(), 1);
        assert_eq!((rows[0].created_at, rows[0].seq), (100, 1));
        assert_eq!(rows[0].call.tool_name, "ls");
    }

    #[test]
    fn reads_tool_calls_from_db() {
        let dir = tempfile::tempdir().unwrap();
        let db_dir = dir.path().join("agents").join("main").join("agent");
        std::fs::create_dir_all(&db_dir).unwrap();
        make_db(&db_dir.join("openclaw-agent.sqlite"));

        let r = reader_for(dir.path());
        let calls = r.read_tool_calls("main").unwrap();
        assert_eq!(calls.len(), 1);
        assert_eq!(calls[0].tool_name, "ls");
    }

    #[test]
    fn missing_db_is_an_error_not_a_panic() {
        let dir = tempfile::tempdir().unwrap();
        let r = reader_for(dir.path());
        assert!(r.read_tool_calls("nope").is_err());
        assert!(!r.schema_version_ok("nope"));
    }

    #[test]
    #[ignore = "requires a live OpenClaw installation"]
    fn reads_real_openclaw_state() {
        let home = std::env::var("HOME").unwrap();
        let profile = profile_for(&PathBuf::from(format!("{home}/.openclaw")), 1);
        let r = OpenClawReader::open(&profile).unwrap();
        assert!(
            r.schema_version_ok("main"),
            "schema should match the profile"
        );
        let calls = r.read_tool_calls("main").unwrap();
        assert!(
            !calls.is_empty(),
            "expected at least one recorded tool call"
        );
        assert!(
            calls.iter().all(|c| c.agent_key.starts_with("agent:")),
            "every record should carry an agent key"
        );
    }
}
