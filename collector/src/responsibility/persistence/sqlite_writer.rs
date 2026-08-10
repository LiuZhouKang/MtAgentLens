// SPDX-License-Identifier: MIT

use crate::responsibility::evidence::action_candidate::ActionCandidate;
use crate::responsibility::evidence::correlator_legacy::CorrelationEvidence;
use crate::responsibility::evidence::non_link_diagnostic::NonLinkDiagnostic;
use crate::responsibility::graph::graph_store::GraphStore;
use crate::responsibility::judgment::delegation::DelegationRecord;
use crate::responsibility::judgment::policy::ContextPollutionResult;
use crate::responsibility::types::support_types::{
    ActionEvent, AgentInstance, IntentEvent, ResponsibilityClaimState, ViolationPatternMatchRecord,
};
use rusqlite::{Connection, params};
use std::sync::{Arc, Mutex};

fn claim_state_to_str(value: &ResponsibilityClaimState) -> &'static str {
    match value {
        ResponsibilityClaimState::Observed => "observed",
        ResponsibilityClaimState::Inferred => "inferred",
        ResponsibilityClaimState::Ambiguous => "ambiguous",
        ResponsibilityClaimState::Unproven => "unproven",
        ResponsibilityClaimState::Confirmed => "confirmed",
        ResponsibilityClaimState::BlockedAttempt => "blocked_attempt",
    }
}

fn serialize_json<T: serde::Serialize>(
    value: &T,
    field_name: &'static str,
) -> rusqlite::Result<String> {
    serde_json::to_string(value).map_err(|err| {
        rusqlite::Error::ToSqlConversionFailure(
            format!("failed to serialize {field_name}: {err}").into(),
        )
    })
}

#[derive(Clone)]
pub struct SqliteWriter {
    conn: Arc<Mutex<Connection>>,
}

impl SqliteWriter {
    pub fn new(conn: Arc<Mutex<Connection>>) -> Self {
        Self { conn }
    }

    /// Create a GraphStore backed by the same shared connection.
    /// The GraphStore provides time-indexed queries, adjacency walks,
    /// graph simplification, statistics, and cross-agent connectedness.
    pub fn graph_store(&self) -> GraphStore {
        GraphStore::new(Arc::clone(&self.conn))
    }

    pub fn write_agent(&self, agent: &AgentInstance) -> rusqlite::Result<()> {
        let conn = self.conn.lock().unwrap();
        let root_processes = serialize_json(&agent.root_processes, "root_processes")?;
        let tls_sessions = serialize_json(&agent.tls_sessions, "tls_sessions")?;
        conn.execute(
            "INSERT OR REPLACE INTO agent_instances
             (agent_id, first_seen_ns, last_seen_ns, root_processes_json, tls_sessions_json, conversation_id)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
            params![
                agent.agent_id,
                agent.first_seen_ns as i64,
                agent.last_seen_ns as i64,
                root_processes,
                tls_sessions,
                agent.conversation_id,
            ],
        )?;
        Ok(())
    }

    pub fn write_agent_v2(
        &self,
        agent_id: &str,
        conversation_id: &Option<String>,
        confidence: &str,
    ) -> rusqlite::Result<()> {
        let conn = self.conn.lock().unwrap();
        conn.execute(
            "INSERT OR REPLACE INTO agent_instances (agent_id, first_seen_ns, last_seen_ns, conversation_id, confidence) VALUES (?1, ?2, ?3, ?4, ?5)",
            params![agent_id, 0i64, 0i64, conversation_id.as_deref().unwrap_or(""), confidence],
        )?;
        Ok(())
    }

    pub fn write_agent_process(
        &self,
        agent_id: &str,
        pid: u32,
        start_time_ns: u64,
    ) -> rusqlite::Result<()> {
        let conn = self.conn.lock().unwrap();
        conn.execute(
            "INSERT OR IGNORE INTO agent_processes (agent_id, pid, start_time_ns) VALUES (?1, ?2, ?3)",
            params![agent_id, pid as i64, start_time_ns as i64],
        )?;
        Ok(())
    }

    pub fn write_unresolved_process(
        &self,
        pid: u32,
        start_time_ns: u64,
        reason: &str,
    ) -> rusqlite::Result<()> {
        let conn = self.conn.lock().unwrap();
        conn.execute(
            "INSERT OR IGNORE INTO unresolved_processes (pid, start_time_ns, reason) VALUES (?1, ?2, ?3)",
            params![pid as i64, start_time_ns as i64, reason],
        )?;
        Ok(())
    }

    pub fn write_intent(&self, intent: &IntentEvent) -> rusqlite::Result<()> {
        let conn = self.conn.lock().unwrap();
        let args = serialize_json(&intent.tool_args, "tool_args")?;
        conn.execute(
            "INSERT INTO intent_events
             (intent_id, agent_id, timestamp_ns, tool_name, tool_args_json, intent_class, parse_state, primary_matching_eligible, conversation_id, message_id, source_event_ref, authority_source)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12)
             ON CONFLICT(intent_id) DO UPDATE SET
               agent_id=excluded.agent_id,
               timestamp_ns=MIN(intent_events.timestamp_ns, excluded.timestamp_ns),
               tool_name=excluded.tool_name,
               tool_args_json=excluded.tool_args_json,
               intent_class=excluded.intent_class,
               parse_state=excluded.parse_state,
               primary_matching_eligible=excluded.primary_matching_eligible,
               conversation_id=excluded.conversation_id,
               message_id=excluded.message_id,
               source_event_ref=excluded.source_event_ref,
               authority_source=excluded.authority_source",
            params![
                intent.intent_id,
                intent.agent_id,
                intent.timestamp_ns as i64,
                intent.tool_name,
                args,
                intent.intent_class.to_string(),
                intent.parse_state.to_string(),
                if intent.primary_matching_eligible { 1i64 } else { 0i64 },
                intent.conversation_id,
                intent.message_id,
                intent.source_event_ref,
                intent.authority_source.to_string(),
            ],
        )?;
        Ok(())
    }

    pub fn write_action(&self, action: &ActionEvent) -> rusqlite::Result<()> {
        let conn = self.conn.lock().unwrap();
        let args = serialize_json(&action.args, "action_args")?;
        let (parent_pid, parent_start) = action
            .parent_process_id
            .map(|p| (Some(p.pid as i64), Some(p.start_time_ns as i64)))
            .unwrap_or((None, None));
        conn.execute(
            "INSERT OR IGNORE INTO action_events
             (action_id, agent_id, timestamp_ns, pid, process_start_time_ns,
              parent_pid, parent_start_time_ns, syscall, args_json, comm)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)",
            params![
                action.action_id,
                action.agent_id,
                action.timestamp_ns as i64,
                action.process_id.pid as i64,
                action.process_id.start_time_ns as i64,
                parent_pid,
                parent_start,
                action.syscall,
                args,
                action.comm,
            ],
        )?;
        Ok(())
    }

    pub fn write_candidate(&self, candidate: &ActionCandidate) -> rusqlite::Result<()> {
        let conn = self.conn.lock().unwrap();
        let normalized_args = serialize_json(&candidate.normalized_args, "normalized_args")?;
        let raw_event_refs = serialize_json(&candidate.raw_event_refs, "raw_event_refs")?;
        conn.execute(
            "INSERT OR IGNORE INTO action_candidates
             (candidate_id, schema_version, agent_id, timestamp_ns, candidate_type, pid, process_start_time_ns,
              normalized_args_json, raw_event_refs_json, derivation_mode, lossiness, aggregate_window_ns,
              aggregate_rule_name, signal_class, debug_notes)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15)",
            params![
                candidate.candidate_id,
                candidate.schema_version as i64,
                candidate.agent_id,
                candidate.timestamp_ns as i64,
                candidate.candidate_type.to_string(),
                candidate.process_id.pid as i64,
                candidate.process_id.start_time_ns as i64,
                normalized_args,
                raw_event_refs,
                candidate.derivation_mode.to_string(),
                candidate.lossiness.to_string(),
                candidate.aggregate_window_ns.map(|v| v as i64),
                candidate.aggregate_rule_name,
                candidate.signal_class.map(|v| v.to_string()),
                candidate.debug_notes,
            ],
        )?;
        Ok(())
    }

    pub fn write_correlation_evidence(
        &self,
        evidence: &CorrelationEvidence,
    ) -> rusqlite::Result<()> {
        let conn = self.conn.lock().unwrap();
        let matched_fields = serialize_json(&evidence.matched_fields, "matched_fields")?;
        let supporting_raw_event_refs = serialize_json(
            &evidence.supporting_raw_event_refs,
            "supporting_raw_event_refs",
        )?;
        conn.execute(
            "INSERT OR IGNORE INTO correlation_evidence
             (schema_version, evidence_id, agent_id, intent_id, candidate_id, timestamp_ns, confidence,
              match_level, matched_fields_json, supporting_raw_event_refs_json, conversation_id,
              authority_source, message_id, source_event_ref, debug_notes, derivation_mode)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15, ?16)",
            params![
                evidence.schema_version as i64,
                evidence.evidence_id,
                evidence.agent_id,
                evidence.intent_id,
                evidence.candidate_id,
                evidence.timestamp_ns as i64,
                evidence.confidence,
                evidence.match_level.to_string(),
                matched_fields,
                supporting_raw_event_refs,
                evidence.conversation_id,
                evidence.authority_source,
                evidence.message_id,
                evidence.source_event_ref,
                evidence.debug_notes,
                evidence.derivation_mode,
            ],
        )?;
        Ok(())
    }

    pub fn write_non_link_diagnostic(
        &self,
        diagnostic: &NonLinkDiagnostic,
    ) -> rusqlite::Result<()> {
        let conn = self.conn.lock().unwrap();
        let details = diagnostic
            .details
            .as_ref()
            .map(|value| serialize_json(value, "details"))
            .transpose()?;
        conn.execute(
            "INSERT OR IGNORE INTO non_link_diagnostics
             (schema_version, diagnostic_id, timestamp_ns, agent_id, intent_id, candidate_id, reason, stage,
              intent_class, parse_state, match_level_considered, confidence_observed, details_json)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13)",
            params![
                diagnostic.schema_version as i64,
                diagnostic.diagnostic_id.clone(),
                diagnostic.timestamp_ns as i64,
                diagnostic.agent_id.clone(),
                diagnostic.intent_id.clone(),
                diagnostic.candidate_id.clone(),
                diagnostic.reason.to_string(),
                diagnostic.stage.clone(),
                diagnostic.intent_class.clone(),
                diagnostic.parse_state.clone(),
                diagnostic.match_level_considered.clone(),
                diagnostic.confidence_observed,
                details,
            ],
        )?;
        Ok(())
    }

    pub fn delete_non_link_diagnostics_for_intent(&self, intent_id: &str) -> rusqlite::Result<()> {
        let conn = self.conn.lock().unwrap();
        conn.execute(
            "DELETE FROM non_link_diagnostics WHERE intent_id = ?1",
            [intent_id],
        )?;
        Ok(())
    }

    pub fn write_violation_pattern_match(
        &self,
        record: &ViolationPatternMatchRecord,
    ) -> rusqlite::Result<()> {
        let conn = self.conn.lock().unwrap();
        let matched_evidence_ids_json =
            serialize_json(&record.matched_link_ids, "matched_evidence_ids")?;
        let matched_policies_json = serialize_json(&record.matched_policies, "matched_policies")?;
        let matched_boundaries_json =
            serialize_json(&record.matched_boundaries, "matched_boundaries")?;
        let matched_targets_json = serialize_json(&record.matched_targets, "matched_targets")?;
        conn.execute(
            "INSERT OR REPLACE INTO violation_pattern_matches
             (match_id, template_id, agent_id, matched_evidence_ids_json, matched_policies_json, matched_boundaries_json, matched_targets_json, start_ns, end_ns, severity, description)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11)",
            params![
                record.match_id,
                record.template_id,
                record.agent_id,
                matched_evidence_ids_json,
                matched_policies_json,
                matched_boundaries_json,
                matched_targets_json,
                record.start_ns as i64,
                record.end_ns as i64,
                record.severity,
                record.description,
            ],
        )?;
        Ok(())
    }

    /// B2: persist cross-agent delegation records.
    pub fn write_delegation(&self, record: &DelegationRecord) -> rusqlite::Result<()> {
        let conn = self.conn.lock().unwrap();
        conn.execute(
            "INSERT OR IGNORE INTO delegation_records
             (delegation_id, from_agent_id, to_agent_id, parent_pid, child_pid, timestamp_ns, child_comm, confidence)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
            params![
                record.delegation_id,
                record.from_agent_id,
                record.to_agent_id,
                record.parent_pid as i64,
                record.child_pid as i64,
                record.timestamp_ns as i64,
                record.child_comm,
                record.confidence,
            ],
        )?;
        Ok(())
    }

    /// Persist a context pollution event.
    pub fn write_context_pollution(&self, event: &ContextPollutionResult) -> rusqlite::Result<()> {
        let conn = self.conn.lock().unwrap();
        let evidence = serialize_json(&event.evidence, "evidence")?;
        let event_id = format!(
            "ctx-pollution-{}-{}-{}",
            event.writer_agent_id,
            event.reader_agent_id,
            event.shared_path.replace('/', "-")
        );
        conn.execute(
            "INSERT OR IGNORE INTO context_pollution_events
             (event_id, policy_id, boundary_id, claim_state, writer_agent_id, reader_agent_id,
              shared_path, write_ts_ns, read_ts_ns, dt_ns, evidence_json, reason)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12)",
            params![
                event_id,
                event.policy_id.0,
                event.boundary_id.0,
                claim_state_to_str(&event.claim_state),
                event.writer_agent_id,
                event.reader_agent_id,
                event.shared_path,
                event.write_ts_ns as i64,
                event.read_ts_ns as i64,
                event.dt_ns as i64,
                evidence,
                event.reason,
            ],
        )?;
        Ok(())
    }
}
