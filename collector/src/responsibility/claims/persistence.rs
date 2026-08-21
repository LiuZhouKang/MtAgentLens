use crate::responsibility::claims::{
    AccountabilityClaim, AccountabilityClaimNewState, AccountabilityClaimType, ClaimSeverity,
};
use crate::responsibility::evidence::EvidenceRecordNew;
use crate::responsibility::types::ResponsibilityTraceback;
use rusqlite::{Connection, params};
use std::sync::{Arc, Mutex};

/// Writes accountability artifacts to the responsibility DB:
///   - responsibility_claims
///   - responsibility_tracebacks
///   - responsibility_evidence_records
///
/// Complements AlertWriter (alerts) and GraphWriter (nodes/edges/snapshots).
pub struct ResponsibilityWriter {
    conn: Arc<Mutex<Connection>>,
}

impl ResponsibilityWriter {
    pub fn new(conn: Arc<Mutex<Connection>>) -> Self {
        Self { conn }
    }

    // ── Claims ────────────────────────────────────────────────────────

    pub fn write_claim_compact(
        &self,
        claim_id: &str,
        alert_id: &str,
        agent_id: &str,
        _decision: &str,
        severity: &str,
        confidence: f32,
        _reasoning: &str,
        traceback_id: &str,
    ) -> rusqlite::Result<()> {
        let conn = self.conn.lock().unwrap();
        conn.execute(
            "INSERT OR REPLACE INTO responsibility_claims
             (claim_id, claim_type, subject, policy_id, boundary_id, judgment_id,
              responsibility_chain_json, evidence_refs_json, claim_state, severity,
              confidence, competing_explanations_json, traceback_ref,
              involved_agent_ids_json)
             VALUES (?1, 'accountability_anomaly', ?2, '', '', ?3,
              '[]', '[]', 'observed', ?4, ?5, '[]', ?6, '[]')",
            params![
                claim_id,
                agent_id,
                alert_id,
                severity,
                confidence,
                traceback_id
            ],
        )?;
        Ok(())
    }

    /// Full claim write (used by replay and when full claim data is available).
    pub fn write_claim(&self, claim: &AccountabilityClaim) -> rusqlite::Result<()> {
        let conn = self.conn.lock().unwrap();
        let responsibility_chain_json = serde_json::to_string(&claim.responsibility_chain)
            .map_err(|e| rusqlite::Error::ToSqlConversionFailure(e.into()))?;
        let evidence_refs_json = serde_json::to_string(&claim.evidence_refs)
            .map_err(|e| rusqlite::Error::ToSqlConversionFailure(e.into()))?;
        let competing_explanations_json = serde_json::to_string(&claim.competing_explanations)
            .map_err(|e| rusqlite::Error::ToSqlConversionFailure(e.into()))?;
        let involved_agent_ids_json = serde_json::to_string(&claim.involved_agent_ids)
            .map_err(|e| rusqlite::Error::ToSqlConversionFailure(e.into()))?;
        conn.execute(
            "INSERT OR REPLACE INTO responsibility_claims
             (claim_id, claim_type, subject, policy_id, boundary_id, judgment_id,
              responsibility_chain_json, evidence_refs_json, claim_state, severity,
              confidence, competing_explanations_json, traceback_ref,
              involved_agent_ids_json)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14)",
            params![
                claim.claim_id,
                claim_type_to_str(&claim.claim_type),
                claim.subject,
                claim.policy_id,
                claim.boundary_id,
                claim.judgment_id,
                responsibility_chain_json,
                evidence_refs_json,
                claim_state_to_str(&claim.claim_state),
                severity_to_str(&claim.severity),
                claim.confidence,
                competing_explanations_json,
                claim.traceback_ref,
                involved_agent_ids_json,
            ],
        )?;
        Ok(())
    }

    // ── Tracebacks ────────────────────────────────────────────────────

    pub fn write_traceback_compact(
        &self,
        traceback_id: &str,
        claim_id: &str,
        node_ids: &[String],
        edge_ids: &[String],
        root_cause_id: &str,
        explanation: &str,
        confidence: f32,
    ) -> rusqlite::Result<()> {
        let conn = self.conn.lock().unwrap();
        let nodes_json = serde_json::to_string(node_ids).unwrap_or_else(|_| "[]".into());
        let edges_json = serde_json::to_string(edge_ids).unwrap_or_else(|_| "[]".into());
        conn.execute(
            "INSERT OR REPLACE INTO responsibility_tracebacks
             (traceback_id, claim_id, chain_node_ids_json, chain_edge_ids_json,
              root_cause_node_id, explanation_summary, confidence)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
            params![
                traceback_id,
                claim_id,
                nodes_json,
                edges_json,
                root_cause_id,
                explanation,
                confidence,
            ],
        )?;
        Ok(())
    }

    pub fn write_traceback(&self, traceback: &ResponsibilityTraceback) -> rusqlite::Result<()> {
        let conn = self.conn.lock().unwrap();
        let chain_node_ids_json = serde_json::to_string(&traceback.chain_node_ids)
            .map_err(|e| rusqlite::Error::ToSqlConversionFailure(e.into()))?;
        let chain_edge_ids_json = serde_json::to_string(&traceback.chain_edge_ids)
            .map_err(|e| rusqlite::Error::ToSqlConversionFailure(e.into()))?;
        conn.execute(
            "INSERT OR REPLACE INTO responsibility_tracebacks
             (traceback_id, claim_id, chain_node_ids_json, chain_edge_ids_json,
              root_cause_node_id, explanation_summary, confidence)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
            params![
                traceback.traceback_id.to_string(),
                traceback.claim_id.to_string(),
                chain_node_ids_json,
                chain_edge_ids_json,
                traceback.root_cause_node_id.to_string(),
                traceback.explanation_summary,
                traceback.confidence,
            ],
        )?;
        Ok(())
    }

    // ── Evidence records ──────────────────────────────────────────────

    pub fn write_evidence(&self, records: &[EvidenceRecordNew]) -> rusqlite::Result<()> {
        if records.is_empty() {
            return Ok(());
        }
        let conn = self.conn.lock().unwrap();
        for rec in records {
            let observation_refs_json =
                serde_json::to_string(&rec.observation_refs).unwrap_or_else(|_| "[]".into());
            let matched_fields_json =
                serde_json::to_string(&rec.matched_fields).unwrap_or_else(|_| "[]".into());
            let diagnostics_json =
                serde_json::to_string(&rec.diagnostics).unwrap_or_else(|_| "[]".into());
            let resource_ref_json = rec
                .resource_ref
                .as_ref()
                .map(|r| serde_json::to_string(r).unwrap_or_default())
                .unwrap_or_else(|| "null".to_string());
            conn.execute(
                "INSERT OR REPLACE INTO responsibility_evidence_records
                 (evidence_id, evidence_type, directness, observation_refs_json,
                  subject_hint, resource_ref_json, rule_id, matched_fields_json,
                  confidence, diagnostics_json)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)",
                params![
                    rec.evidence_id,
                    evidence_type_str(&rec.evidence_type),
                    directness_str(&rec.directness),
                    observation_refs_json,
                    rec.subject_hint,
                    resource_ref_json,
                    rec.rule_id,
                    matched_fields_json,
                    rec.confidence,
                    diagnostics_json,
                ],
            )?;
        }
        Ok(())
    }
}

// ── String converters ─────────────────────────────────────────────────

fn claim_type_to_str(value: &AccountabilityClaimType) -> &'static str {
    match value {
        AccountabilityClaimType::PolicyViolation => "policy_violation",
        AccountabilityClaimType::AccountabilityAnomaly => "accountability_anomaly",
        AccountabilityClaimType::SuspiciousDelegation => "suspicious_delegation",
        AccountabilityClaimType::TaintedContextUse => "tainted_context_use",
        AccountabilityClaimType::UnauthorizedCapabilityUse => "unauthorized_capability_use",
        AccountabilityClaimType::BlockedAttempt => "blocked_attempt",
    }
}

fn claim_state_to_str(value: &AccountabilityClaimNewState) -> &'static str {
    match value {
        AccountabilityClaimNewState::Observed => "observed",
        AccountabilityClaimNewState::Inferred => "inferred",
        AccountabilityClaimNewState::Ambiguous => "ambiguous",
        AccountabilityClaimNewState::Unproven => "unproven",
        AccountabilityClaimNewState::BlockedAttempt => "blocked_attempt",
    }
}

fn severity_to_str(value: &ClaimSeverity) -> &'static str {
    match value {
        ClaimSeverity::Info => "info",
        ClaimSeverity::Warning => "warning",
        ClaimSeverity::Critical => "critical",
    }
}

fn evidence_type_str(t: &crate::responsibility::evidence::EvidenceType) -> &'static str {
    match t {
        crate::responsibility::evidence::EvidenceType::CapabilityUse => "capability_use",
        _ => "other",
    }
}

fn directness_str(d: &crate::responsibility::evidence::EvidenceDirectnessNew) -> &'static str {
    match d {
        crate::responsibility::evidence::EvidenceDirectnessNew::Direct => "direct",
        crate::responsibility::evidence::EvidenceDirectnessNew::Inferred => "inferred",
    }
}
