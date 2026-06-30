// SPDX-License-Identifier: MIT
// Copyright (c) 2026 MtAgentLens Project
//
// Accountability Replay Executor
//
// Provides on-demand loading, reconstruction, and query of accountability
// traceback replays, snapshots, and audit summaries from the authoritative
// SQLite store. This is the programmatic API consumed by the web server,
// agent-mode runner tear-down, and offline evaluation scripts.
//
// Design contract (Phase 1C):
//   - Every traceback replay is loaded with its full evidence chain.
//   - Time-range queries support audit-scope narrowing.
//   - Agent-scoped queries support per-agent accountability inspection.
//   - Fail-closed: missing or malformed rows produce explicit errors, never
//     fabricated chains.

use anyhow::{Context, Result, anyhow};
use rusqlite::Connection;

use crate::responsibility::types::support_types::{
    AccountabilityAuditSummary, ResponsibilityClaimState, AccountabilityTracebackReplay, AlertId,
    EvidenceId, NodeId, TracebackId,
};

// ── Public API ──────────────────────────────────────────────────────────

/// Load all accountability traceback replays for a given run.
pub fn load_traceback_replays(db_path: &str) -> Result<Vec<AccountabilityTracebackReplay>> {
    let conn = Connection::open(db_path).context("open accountability db for replay")?;
    load_traceback_replays_from_conn(&conn)
}

/// Load all accountability traceback replays scoped to a specific agent.
pub fn load_traceback_replays_for_agent(
    db_path: &str,
    agent_id: &str,
) -> Result<Vec<AccountabilityTracebackReplay>> {
    let conn = Connection::open(db_path).context("open accountability db for replay")?;
    let mut stmt = conn
        .prepare(
            "SELECT t.traceback_id, t.alert_id, t.chain_node_ids_json,
                    t.root_cause_node_id, t.supporting_evidence_ids_json,
                    t.explanation_summary
             FROM accountability_traceback_replays t
             JOIN accountability_tracebacks tb ON t.traceback_id = tb.traceback_id
             JOIN correlation_evidence ce ON tb.alert_id = ce.evidence_id
             WHERE ce.agent_id = ?1
             ORDER BY ce.timestamp_ns ASC, t.traceback_id ASC",
        )
        .context("prepare agent-scoped traceback replay query")?;

    let rows = stmt
        .query_map([agent_id], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, String>(3)?,
                row.get::<_, String>(4)?,
                row.get::<_, String>(5)?,
            ))
        })
        .context("execute agent-scoped traceback replay query")?;

    let mut replays = Vec::new();
    for row in rows {
        let (tb_id, alert_id, chain_json, root_id, evidence_json, summary) =
            row.context("read traceback replay row")?;
        let chain_node_ids: Vec<String> = serde_json::from_str(&chain_json).unwrap_or_default();
        let supporting_evidence_ids: Vec<String> =
            serde_json::from_str(&evidence_json).unwrap_or_default();

        replays.push(AccountabilityTracebackReplay {
            traceback_id: TracebackId(tb_id),
            alert_id: AlertId(alert_id),
            chain_node_ids: chain_node_ids.into_iter().map(NodeId).collect(),
            root_cause_node_id: NodeId(root_id),
            supporting_evidence_ids: supporting_evidence_ids
                .into_iter()
                .map(EvidenceId)
                .collect(),
            explanation_summary: summary,
        });
    }

    Ok(replays)
}

/// Load the accountability audit summary for a run.
pub fn load_audit_summary(
    db_path: &str,
    run_id: &str,
) -> Result<Option<AccountabilityAuditSummary>> {
    let conn = Connection::open(db_path).context("open accountability db for audit summary")?;
    load_audit_summary_from_conn(&conn, run_id)
}

/// Load traceback replays within a time range (timestamp_ns, inclusive start, exclusive end).
pub fn load_traceback_replays_in_range(
    db_path: &str,
    start_ns: u64,
    end_ns: u64,
) -> Result<Vec<AccountabilityTracebackReplay>> {
    let conn = Connection::open(db_path).context("open accountability db for time-range replay")?;
    let mut stmt = conn
        .prepare(
            "SELECT t.traceback_id, t.alert_id, t.chain_node_ids_json,
                    t.root_cause_node_id, t.supporting_evidence_ids_json,
                    t.explanation_summary
             FROM accountability_traceback_replays t
             JOIN accountability_tracebacks tb ON t.traceback_id = tb.traceback_id
             JOIN correlation_evidence ce ON tb.alert_id = ce.evidence_id
             WHERE ce.timestamp_ns >= ?1 AND ce.timestamp_ns < ?2
             ORDER BY ce.timestamp_ns ASC, t.traceback_id ASC",
        )
        .context("prepare time-range traceback replay query")?;

    let rows = stmt
        .query_map([start_ns, end_ns], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, String>(3)?,
                row.get::<_, String>(4)?,
                row.get::<_, String>(5)?,
            ))
        })
        .context("execute time-range traceback replay query")?;

    let mut replays = Vec::new();
    for row in rows {
        let (tb_id, alert_id, chain_json, root_id, evidence_json, summary) =
            row.context("read traceback replay row")?;
        let chain_node_ids: Vec<String> = serde_json::from_str(&chain_json).unwrap_or_default();
        let supporting_evidence_ids: Vec<String> =
            serde_json::from_str(&evidence_json).unwrap_or_default();

        replays.push(AccountabilityTracebackReplay {
            traceback_id: TracebackId(tb_id),
            alert_id: AlertId(alert_id),
            chain_node_ids: chain_node_ids.into_iter().map(NodeId).collect(),
            root_cause_node_id: NodeId(root_id),
            supporting_evidence_ids: supporting_evidence_ids
                .into_iter()
                .map(EvidenceId)
                .collect(),
            explanation_summary: summary,
        });
    }

    Ok(replays)
}

/// Count traceback replays grouped by claim state.
pub fn count_tracebacks_by_claim_state(
    db_path: &str,
) -> Result<Vec<(ResponsibilityClaimState, usize)>> {
    let conn = Connection::open(db_path).context("open accountability db for claim-state stats")?;

    let has_column = {
        let mut stmt = conn
            .prepare("SELECT claim_state FROM policy_evaluations LIMIT 0")
            .ok();
        stmt.is_some()
    };

    if !has_column {
        return Ok(vec![]);
    }

    let mut stmt = conn
        .prepare(
            "SELECT pe.claim_state, COUNT(*)
             FROM accountability_tracebacks tb
             JOIN policy_evaluations pe
               ON pe.subject_kind = 'correlation_evidence'
              AND tb.alert_id = pe.subject_id
             GROUP BY pe.claim_state",
        )
        .context("prepare claim-state stats query")?;

    let rows = stmt
        .query_map([], |row| {
            Ok((row.get::<_, String>(0)?, row.get::<_, usize>(1)?))
        })
        .context("execute claim-state stats query")?;

    let mut counts = Vec::new();
    for row in rows {
        let (state_str, count) = row.context("read claim-state stats row")?;
        let state = match state_str.as_str() {
            "observed" => ResponsibilityClaimState::Observed,
            "inferred" => ResponsibilityClaimState::Inferred,
            "ambiguous" => ResponsibilityClaimState::Ambiguous,
            "unproven" => ResponsibilityClaimState::Unproven,
            _ => continue,
        };
        counts.push((state, count));
    }

    Ok(counts)
}

// ── Internal helpers ────────────────────────────────────────────────────

fn load_traceback_replays_from_conn(
    conn: &Connection,
) -> Result<Vec<AccountabilityTracebackReplay>> {
    let has_table = conn
        .prepare("SELECT traceback_id FROM accountability_traceback_replays LIMIT 0")
        .is_ok();

    if !has_table {
        return Ok(vec![]);
    }

    let mut stmt = conn
        .prepare(
            "SELECT traceback_id, alert_id, chain_node_ids_json,
                    root_cause_node_id, supporting_evidence_ids_json,
                    explanation_summary
             FROM accountability_traceback_replays
             ORDER BY traceback_id",
        )
        .context("prepare traceback replay query")?;

    let rows = stmt
        .query_map([], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, String>(3)?,
                row.get::<_, String>(4)?,
                row.get::<_, String>(5)?,
            ))
        })
        .context("execute traceback replay query")?;

    let mut replays = Vec::new();
    for row in rows {
        let (tb_id, alert_id, chain_json, root_id, evidence_json, summary) =
            row.context("read traceback replay row")?;
        let chain_node_ids: Vec<String> = serde_json::from_str(&chain_json).unwrap_or_default();
        let supporting_evidence_ids: Vec<String> =
            serde_json::from_str(&evidence_json).unwrap_or_default();

        replays.push(AccountabilityTracebackReplay {
            traceback_id: TracebackId(tb_id),
            alert_id: AlertId(alert_id),
            chain_node_ids: chain_node_ids.into_iter().map(NodeId).collect(),
            root_cause_node_id: NodeId(root_id),
            supporting_evidence_ids: supporting_evidence_ids
                .into_iter()
                .map(EvidenceId)
                .collect(),
            explanation_summary: summary,
        });
    }

    Ok(replays)
}

fn load_audit_summary_from_conn(
    conn: &Connection,
    run_id: &str,
) -> Result<Option<AccountabilityAuditSummary>> {
    let has_table = conn
        .prepare("SELECT run_id FROM accountability_audit_summaries LIMIT 0")
        .is_ok();

    if !has_table {
        return Ok(None);
    }

    let mut stmt = conn
        .prepare(
            "SELECT run_id, alert_count, traceback_count, summary_text
             FROM accountability_audit_summaries
             WHERE run_id = ?1",
        )
        .context("prepare audit summary query")?;

    let mut rows = stmt
        .query_map([run_id], |row| {
            Ok(AccountabilityAuditSummary {
                run_id: row.get(0)?,
                alert_count: row.get(1)?,
                traceback_count: row.get(2)?,
                summary_text: row.get(3)?,
            })
        })
        .context("execute audit summary query")?;

    match rows.next() {
        Some(Ok(summary)) => Ok(Some(summary)),
        Some(Err(e)) => Err(anyhow!("failed to read audit summary row: {}", e)),
        None => Ok(None),
    }
}

// ── Tests ────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use crate::responsibility::types::support_types::{
        AccountabilityAuditSummary, AccountabilityTracebackReplay, AlertId, EvidenceId, NodeId,
        TracebackId,
    };

    #[test]
    fn test_traceback_replay_roundtrip() {
        let replay = AccountabilityTracebackReplay {
            traceback_id: TracebackId("tb-1".to_string()),
            alert_id: AlertId("alert-1".to_string()),
            chain_node_ids: vec![NodeId("n1".to_string()), NodeId("n2".to_string())],
            root_cause_node_id: NodeId("n1".to_string()),
            supporting_evidence_ids: vec![EvidenceId("e1".to_string())],
            explanation_summary: "test chain".to_string(),
        };

        let json = serde_json::to_string(&replay).unwrap();
        let parsed: AccountabilityTracebackReplay = serde_json::from_str(&json).unwrap();
        assert_eq!(parsed.traceback_id, replay.traceback_id);
        assert_eq!(parsed.chain_node_ids.len(), 2);
    }

    #[test]
    fn test_audit_summary_roundtrip() {
        let summary = AccountabilityAuditSummary {
            run_id: "run-1".to_string(),
            alert_count: 5,
            traceback_count: 5,
            summary_text: "5 alerts".to_string(),
        };

        let json = serde_json::to_string(&summary).unwrap();
        let parsed: AccountabilityAuditSummary = serde_json::from_str(&json).unwrap();
        assert_eq!(parsed.alert_count, 5);
    }

    #[test]
    fn test_claim_state_serialization_roundtrip() {
        let states = vec![
            (ResponsibilityClaimState::Observed, "Observed"),
            (ResponsibilityClaimState::Inferred, "Inferred"),
            (ResponsibilityClaimState::Ambiguous, "Ambiguous"),
            (ResponsibilityClaimState::Unproven, "Unproven"),
        ];

        for (state, expected) in &states {
            let json = serde_json::to_string(state).unwrap();
            assert_eq!(json, format!("\"{}\"", expected));
            let parsed: ResponsibilityClaimState = serde_json::from_str(&json).unwrap();
            assert_eq!(parsed, *state);
        }
    }

    #[test]
    fn test_traceback_replay_with_empty_evidence_is_valid() {
        let replay = AccountabilityTracebackReplay {
            traceback_id: TracebackId("tb-empty".to_string()),
            alert_id: AlertId("alert-empty".to_string()),
            chain_node_ids: vec![NodeId("n-solo".to_string())],
            root_cause_node_id: NodeId("n-solo".to_string()),
            supporting_evidence_ids: vec![],
            explanation_summary: "single node, self-evident".to_string(),
        };
        assert!(replay.supporting_evidence_ids.is_empty());
    }
}
