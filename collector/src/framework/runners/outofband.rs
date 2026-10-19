// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 MtAgentLens Project

//! Polling runner for an agent's own persisted tool calls.
//!
//! OpenClaw writes a `trajectory_runtime_events` row for every tool call into
//! `~/.openclaw/agents/<agent>/agent/openclaw-agent.sqlite`. The eBPF path
//! observes the syscalls those calls produce, but not the structured
//! arguments the model chose. Reading the agent's own record closes that gap
//! without instrumenting the agent: the database is opened read-only and the
//! embedded facts are labelled `OutOfBandAsserted`.
//!
//! This runner emits one [`Event`] per new tool call, with
//! `source = "openclaw"` and `event = "TOOL_CALL"`. Routing turns those into
//! intents (see `runtime::routing::outofband_intent`). It starts at the
//! database's current cursor, so a new run never replays history.

use super::{EventStream, Runner, RunnerError};
use crate::framework::analyzers::Analyzer;
use crate::framework::core::Event;
use crate::responsibility::outofband::openclaw::{OpenClawReader, profile_for};
use async_trait::async_trait;
use futures::StreamExt;
use std::collections::HashMap;
use std::path::PathBuf;
use std::time::Duration;

/// Default poll interval. The agent's own writes are not latency-critical.
const DEFAULT_POLL_MS: u64 = 1_000;

/// Polls one or more OpenClaw agent databases for new tool calls.
pub struct OpenClawRunner {
    reader: OpenClawReader,
    agents: Vec<String>,
    poll: Duration,
    analyzers: Vec<Box<dyn Analyzer>>,
    /// `(created_at, seq)` per agent; used by the unit-test driver.
    #[cfg(test)]
    cursors: HashMap<String, (i64, i64)>,
    #[cfg(test)]
    initialized: bool,
    /// Gateway PID used as the intent's process identity. 0 when unknown.
    gateway_pid: u32,
}

impl OpenClawRunner {
    pub fn new(state_dir: PathBuf, agents: Vec<String>) -> Self {
        let reader = OpenClawReader::open(&profile_for(&state_dir, 1))
            .expect("OpenClawReader::open only rejects a mismatched profile kind");
        let gateway_pid = find_gateway_pid();
        Self {
            reader,
            agents,
            poll: Duration::from_millis(DEFAULT_POLL_MS),
            analyzers: Vec::new(),
            #[cfg(test)]
            cursors: HashMap::new(),
            #[cfg(test)]
            initialized: false,
            gateway_pid,
        }
    }

    pub fn with_poll_interval_ms(mut self, millis: u64) -> Self {
        self.poll = Duration::from_millis(millis.max(50));
        self
    }

    /// One poll. Returns events for tool calls strictly after the cursor.
    #[cfg(test)]
    fn poll_once(&mut self) -> Vec<Event> {
        if !self.initialized {
            for agent in &self.agents {
                if let Ok(cursor) = self.reader.latest_cursor(agent) {
                    self.cursors.insert(agent.clone(), cursor);
                }
            }
            self.initialized = true;
            return Vec::new();
        }

        let mut events = Vec::new();
        for agent in &self.agents {
            let (mut created_at, mut seq) = self.cursors.get(agent).copied().unwrap_or((0, 0));
            match self.reader.read_tool_calls_since(agent, created_at, seq) {
                Ok(rows) => {
                    for row in rows {
                        created_at = row.created_at;
                        seq = row.seq;
                        // Event timestamps use the same CLOCK_MONOTONIC as
                        // bpf_ktime_get_ns; the record's own timestamp is
                        // wall-clock and would never fall inside an action's
                        // correlation window.
                        events.push(Event::new_with_timestamp(
                            monotonic_now_ns(),
                            "openclaw".to_string(),
                            self.gateway_pid,
                            "openclaw".to_string(),
                            serde_json::json!({
                                "event": "TOOL_CALL",
                                "tool_name": row.call.tool_name,
                                "args": row.call.args,
                                "agent_key": row.call.agent_key,
                                "run_id": row.call.run_id,
                                "created_at": row.created_at,
                                "seq": row.seq,
                            }),
                        ));
                    }
                }
                Err(err) => {
                    // A missing/locked database must not kill the run; the
                    // eBPF path is independent and remains authoritative.
                    log::warn!("openclaw out-of-band read failed for {agent}: {err}");
                }
            }
            self.cursors.insert(agent.clone(), (created_at, seq));
        }
        events
    }
}

#[async_trait]
impl Runner for OpenClawRunner {
    async fn run(&mut self) -> Result<EventStream, RunnerError> {
        let reader = self.reader.clone();
        let agents = self.agents.clone();
        let poll = self.poll;
        let gateway_pid = self.gateway_pid;
        let mut cursors: HashMap<String, (i64, i64)> = HashMap::new();
        for agent in &agents {
            if let Ok(cursor) = reader.latest_cursor(agent) {
                cursors.insert(agent.clone(), cursor);
            }
        }

        let state = (reader, agents, poll, gateway_pid, cursors);
        let stream = futures::stream::unfold(state, |state| async move {
            let (reader, agents, poll, gateway_pid, mut cursors) = state;
            tokio::time::sleep(poll).await;
            let mut events = Vec::new();
            for agent in &agents {
                let (mut created_at, mut seq) = cursors.get(agent).copied().unwrap_or((0, 0));
                if let Ok(rows) = reader.read_tool_calls_since(agent, created_at, seq) {
                    for row in rows {
                        created_at = row.created_at;
                        seq = row.seq;
                        // Event timestamps use the same CLOCK_MONOTONIC as
                        // bpf_ktime_get_ns; the record's own timestamp is
                        // wall-clock and would never fall inside an action's
                        // correlation window.
                        events.push(Event::new_with_timestamp(
                            monotonic_now_ns(),
                            "openclaw".to_string(),
                            gateway_pid,
                            "openclaw".to_string(),
                            serde_json::json!({
                                "event": "TOOL_CALL",
                                "tool_name": row.call.tool_name,
                                "args": row.call.args,
                                "agent_key": row.call.agent_key,
                                "run_id": row.call.run_id,
                                "created_at": row.created_at,
                                "seq": row.seq,
                            }),
                        ));
                    }
                }
                cursors.insert(agent.clone(), (created_at, seq));
            }
            Some((
                futures::stream::iter(events),
                (reader, agents, poll, gateway_pid, cursors),
            ))
        })
        .flatten();

        let mut analyzers = std::mem::take(&mut self.analyzers);
        super::common::AnalyzerProcessor::process_through_analyzers(
            Box::pin(stream),
            &mut analyzers,
        )
        .await
    }

    fn add_analyzer(mut self, analyzer: Box<dyn Analyzer>) -> Self {
        self.analyzers.push(analyzer);
        self
    }

    fn name(&self) -> &str {
        "openclaw"
    }

    fn id(&self) -> String {
        "openclaw".to_string()
    }
}

/// Current CLOCK_MONOTONIC in nanoseconds, matching `bpf_ktime_get_ns()`.
///
/// Rust's `Instant` is monotonic but opaque; `/proc/uptime` exposes the same
/// clock as a decimal number and is cheap to read once per poll.
fn monotonic_now_ns() -> u64 {
    std::fs::read_to_string("/proc/uptime")
        .ok()
        .and_then(|s| s.split_whitespace().next()?.parse::<f64>().ok())
        .map(|secs| (secs * 1_000_000_000.0) as u64)
        .unwrap_or(0)
}

/// Find the long-lived OpenClaw gateway process.
///
/// The cgroup unit is the most precise signal (the gateway runs as
/// `openclaw-gateway.service`); the command line is the fallback. Returns 0
/// when the gateway is not running — the intent then correlates by content
/// only.
fn find_gateway_pid() -> u32 {
    let Ok(entries) = std::fs::read_dir("/proc") else {
        return 0;
    };
    let mut cmdline_match = 0;
    for entry in entries.flatten() {
        let name = entry.file_name();
        let Some(pid) = name.to_str().and_then(|s| s.parse::<u32>().ok()) else {
            continue;
        };
        if let Ok(cgroup) = std::fs::read_to_string(format!("/proc/{pid}/cgroup"))
            && cgroup.contains("openclaw-gateway.service")
        {
            return pid;
        }
        if cmdline_match == 0
            && let Ok(cmdline) = std::fs::read(format!("/proc/{pid}/cmdline"))
        {
            let text = String::from_utf8_lossy(&cmdline);
            if text.contains("openclaw") && text.contains("gateway") {
                cmdline_match = pid;
            }
        }
    }
    cmdline_match
}

#[cfg(test)]
mod tests {
    use super::*;
    use rusqlite::Connection;
    use std::path::Path;

    fn make_db(path: &Path) {
        let conn = Connection::open(path).unwrap();
        conn.execute_batch(
            "CREATE TABLE trajectory_runtime_events (
                 session_id TEXT, seq INTEGER, run_id TEXT,
                 event_json TEXT, created_at INTEGER);",
        )
        .unwrap();
        let call = r#"{"traceSchema":"openclaw-trajectory","schemaVersion":1,"type":"tool.call","ts":"2026-10-12T06:09:58.375Z","sessionKey":"agent:main:main","runId":"r1","data":{"name":"ls","args":{"path":"/tmp"}}}"#;
        conn.execute(
            "INSERT INTO trajectory_runtime_events VALUES (?1,?2,?3,?4,?5)",
            rusqlite::params!["s1", 1, "r1", call, 100i64],
        )
        .unwrap();
    }

    fn runner_for(dir: &Path) -> OpenClawRunner {
        let db_dir = dir.join("agents").join("main").join("agent");
        std::fs::create_dir_all(&db_dir).unwrap();
        make_db(&db_dir.join("openclaw-agent.sqlite"));
        OpenClawRunner::new(dir.to_path_buf(), vec!["main".into()])
    }

    #[test]
    fn first_poll_starts_at_the_cursor_and_emits_nothing() {
        let dir = tempfile::tempdir().unwrap();
        let mut runner = runner_for(dir.path());
        assert!(runner.poll_once().is_empty());
    }

    #[test]
    fn a_new_row_is_emitted_once() {
        let dir = tempfile::tempdir().unwrap();
        let mut runner = runner_for(dir.path());
        assert!(runner.poll_once().is_empty());

        let db = dir.path().join("agents/main/agent/openclaw-agent.sqlite");
        let conn = Connection::open(&db).unwrap();
        let call = r#"{"traceSchema":"openclaw-trajectory","schemaVersion":1,"type":"tool.call","ts":"2026-10-12T06:10:00.000Z","sessionKey":"agent:main:main","runId":"r1","data":{"name":"exec","args":{"command":"ls"}}}"#;
        conn.execute(
            "INSERT INTO trajectory_runtime_events VALUES (?1,?2,?3,?4,?5)",
            rusqlite::params!["s1", 2, "r1", call, 200i64],
        )
        .unwrap();
        drop(conn);

        let events = runner.poll_once();
        assert_eq!(events.len(), 1, "one new tool call");
        assert_eq!(events[0].source, "openclaw");
        assert_eq!(events[0].data["tool_name"], "exec");
        assert_eq!(events[0].data["agent_key"], "agent:main:main");
        assert!(runner.poll_once().is_empty(), "cursor advanced");
    }
}
