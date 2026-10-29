// SPDX-License-Identifier: Apache-2.0 OR MIT
// PersistenceReader — independent read-only connection for querying persisted data.

use crate::responsibility::persistence::schema::{SchemaError, open_responsibility_db_read_only};
use crate::responsibility::types::alert_types::{
    AgentRef, AlertAuthority, AlertDecision, AlertSeverity, BehaviorTimeline, DisplayProfile,
    ResponsibilityAlert,
};
use crate::responsibility::types::evidence_types::{
    Action, ActionCandidateType, CandidateDerivationMode, CorrelationEvidence, Intent,
    IntentAuthoritySource, IntentClass, IntentParseState, MatchLevel, NonLinkDiagnostic,
    NonLinkReason,
};
use crate::responsibility::types::graph_types::{
    ResponsibilityEdge, ResponsibilityEdgeType, ResponsibilityNode, ResponsibilityNodeType,
};
use crate::responsibility::types::identity_types::{
    AgentConfidence, AuthoritativeIdentity, IdentitySignal, ProvisionalIdentity,
};
use crate::responsibility::types::ids::*;
use crate::responsibility::types::phase::PhaseIndicator;
use crate::responsibility::types::policy_types::DelegationRecord;
use crate::responsibility::types::replay_types::RunRecord;

use rusqlite::{Connection, Result, params};

// ═══════════════════════════════════════════════════════════════════════════
// Helper deserialization
// ═══════════════════════════════════════════════════════════════════════════

fn deser_required<T: serde::de::DeserializeOwned>(val: &str, field: &'static str) -> Result<T> {
    serde_json::from_str(val).map_err(|e| {
        rusqlite::Error::FromSqlConversionFailure(
            0,
            rusqlite::types::Type::Text,
            Box::new(std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                format!("failed to deserialize {field}: {e}"),
            )),
        )
    })
}

fn deser_json<T: serde::de::DeserializeOwned + Default>(
    val: &str,
    field: &'static str,
) -> Result<T> {
    // An empty column means "not recorded" (legacy rows and SQL defaults).
    // Anything else must parse: a corrupt value is an error, never an empty
    // structure that reads as "no agents / no evidence".
    if val.trim().is_empty() {
        return Ok(T::default());
    }
    deser_required(val, field)
}

/// `timeline_json` has a schema default of `'[]'`, written by `CREATE TABLE`;
/// `[]` therefore means "no timeline recorded", not corruption. Any other
/// malformed value is surfaced as an error.
fn deser_timeline(raw: &str) -> Result<BehaviorTimeline> {
    if raw.trim() == "[]" {
        return Ok(BehaviorTimeline::default());
    }
    deser_json(raw, "timeline")
}

fn parse_phase(raw: &str) -> Result<PhaseIndicator> {
    let text = raw.trim();
    if text.starts_with('{') {
        return deser_required(text, "phase");
    }
    let timestamp_ns = match text.split_once("timestamp_ns:") {
        Some((_, value)) => value
            .trim_start()
            .split(|c: char| !c.is_ascii_digit())
            .next()
            .and_then(|digits| digits.parse().ok())
            .ok_or(rusqlite::Error::InvalidQuery)?,
        None => 0,
    };
    if text == "Provisional" || text.starts_with("Provisional {") {
        Ok(PhaseIndicator::Provisional { timestamp_ns })
    } else if text == "Authoritative" || text.starts_with("Authoritative {") {
        let overrides = match text.split_once("overrides: Some(") {
            Some((_, rest)) => {
                let end = rest.rfind(')').ok_or(rusqlite::Error::InvalidQuery)?;
                Some(Box::new(parse_phase(&rest[..end])?))
            }
            None => None,
        };
        Ok(PhaseIndicator::Authoritative {
            timestamp_ns,
            overrides,
        })
    } else {
        Err(rusqlite::Error::InvalidQuery)
    }
}

fn parse_severity(raw: &str) -> AlertSeverity {
    match raw {
        "Info" => AlertSeverity::Info,
        "Warning" => AlertSeverity::Warning,
        "Critical" => AlertSeverity::Critical,
        _ => AlertSeverity::Info,
    }
}

fn parse_decision(raw: &str) -> AlertDecision {
    match raw {
        "Safe" => AlertDecision::Safe,
        "Unsafe" => AlertDecision::Unsafe,
        "Uncertain" => AlertDecision::Uncertain,
        _ => AlertDecision::Uncertain,
    }
}

fn parse_authority(raw: &str) -> AlertAuthority {
    match raw {
        "Authoritative" => AlertAuthority::Authoritative,
        "Supporting" => AlertAuthority::Supporting,
        "Unavailable" => AlertAuthority::Unavailable,
        _ => AlertAuthority::Unavailable,
    }
}

fn parse_display_profile(raw: &str) -> DisplayProfile {
    match raw {
        "SafeBaseline" => DisplayProfile::SafeBaseline,
        "UnsafeByEngine" => DisplayProfile::UnsafeByEngine,
        "ObserverJudged" => DisplayProfile::ObserverJudged,
        _ => DisplayProfile::SafeBaseline,
    }
}

fn parse_match_level(raw: &str) -> MatchLevel {
    match raw {
        "L1Exact" => MatchLevel::L1Exact,
        "ProcessLineage" => MatchLevel::ProcessLineage,
        "L2Normalized" => MatchLevel::L2Normalized,
        "L3Containment" => MatchLevel::L3Containment,
        "L4Basename" => MatchLevel::L4Basename,
        "L5Substring" => MatchLevel::L5Substring,
        other => {
            // The weakest level is the safe fallback, but silence would hide a
            // writer/reader drift (a new variant persisted but not mapped).
            log::warn!("unknown persisted match_level {other:?}; reading as L5Substring");
            MatchLevel::L5Substring
        }
    }
}

fn parse_intent_class(raw: &str) -> IntentClass {
    match normalize_enum_token(raw).as_str() {
        "orchestration" => IntentClass::Orchestration,
        "sideeffect" => IntentClass::SideEffect,
        _ => IntentClass::Unknown,
    }
}

fn parse_parse_state(raw: &str) -> IntentParseState {
    match normalize_enum_token(raw).as_str() {
        "complete" => IntentParseState::Complete,
        "partial" => IntentParseState::Partial,
        "malformed" => IntentParseState::Malformed,
        "unsupported" => IntentParseState::Unsupported,
        _ => IntentParseState::Complete,
    }
}

/// Lower-case `raw` and drop non-alphanumerics, so `SideEffect`,
/// `side_effect` and `SIDE_EFFECT` all compare equal.
///
/// The `intents` columns do not share one casing convention: `intent_class` is
/// written through `Display` (lower-case, `side_effect`), while `parse_state`
/// and `authority_source` are written through serde (PascalCase). A parser that
/// accepted only PascalCase therefore turned **every** `intent_class` read back
/// from the database into `Unknown` — silently, on every run ever recorded —
/// which would also have hidden any improvement to intent classification.
///
/// The lower-case spelling is load-bearing and must not be "fixed" at the
/// writer: `compare/scripts/evaluate_mtagentlens.py`, `compare/scripts/run/
/// audit_scenario.py` and `compare/scripts/ablation/run_staircase.py` all score
/// with `WHERE intent_class='side_effect'`.
fn normalize_enum_token(raw: &str) -> String {
    raw.chars()
        .filter(|c| c.is_alphanumeric())
        .flat_map(|c| c.to_lowercase())
        .collect()
}

fn parse_authority_source(raw: &str) -> IntentAuthoritySource {
    match raw {
        "RequestHistoryAssistantToolUse" => IntentAuthoritySource::RequestHistoryAssistantToolUse,
        "SseResponseToolUse" => IntentAuthoritySource::SseResponseToolUse,
        "HttpResponseToolUse" => IntentAuthoritySource::HttpResponseToolUse,
        "TextFallback" => IntentAuthoritySource::TextFallback,
        "OutOfBandAsserted" => IntentAuthoritySource::OutOfBandAsserted,
        other => {
            log::warn!(
                "unknown persisted authority_source {other:?}; reading as HttpResponseToolUse"
            );
            IntentAuthoritySource::HttpResponseToolUse
        }
    }
}

fn parse_action_type(raw: &str) -> Result<ActionCandidateType> {
    serde_json::from_value(serde_json::Value::String(raw.to_owned())).map_err(|e| {
        rusqlite::Error::FromSqlConversionFailure(0, rusqlite::types::Type::Text, Box::new(e))
    })
}

fn parse_derivation_mode(raw: &str) -> CandidateDerivationMode {
    match raw {
        "Direct" => CandidateDerivationMode::Direct,
        "Aggregated" => CandidateDerivationMode::Aggregated,
        "MultiEmit" => CandidateDerivationMode::MultiEmit,
        _ => CandidateDerivationMode::Direct,
    }
}

fn parse_signal_class(raw: &str) -> crate::responsibility::types::evidence_types::SignalClass {
    use crate::responsibility::types::evidence_types::SignalClass;
    match raw {
        "HighSignal" => SignalClass::HighSignal,
        "LowSignal" => SignalClass::LowSignal,
        "InternalHousekeeping" => SignalClass::InternalHousekeeping,
        _ => SignalClass::HighSignal,
    }
}

fn parse_non_link_reason(raw: &str) -> NonLinkReason {
    match raw {
        "NoArgumentMatch" => NonLinkReason::NoArgumentMatch,
        "CandidateNotAvailable" => NonLinkReason::CandidateNotAvailable,
        "BelowConfidenceCutoff" => NonLinkReason::BelowConfidenceCutoff,
        "OutsideTimeWindow" => NonLinkReason::OutsideTimeWindow,
        "AmbiguousCompetition" => NonLinkReason::AmbiguousCompetition,
        "IntentNotPrimaryEligible" => NonLinkReason::IntentNotPrimaryEligible,
        "ObserverPending" => NonLinkReason::ObserverPending,
        _ => NonLinkReason::NoArgumentMatch,
    }
}

// ═══════════════════════════════════════════════════════════════════════════
// PersistenceReader
// ═══════════════════════════════════════════════════════════════════════════

/// Independent read-only connection for querying persisted responsibility data.
pub struct PersistenceReader {
    conn: Connection,
    /// Path of the database this reader opened. `None` for in-memory test
    /// connections, where a second connection to the same data is impossible.
    path: Option<std::path::PathBuf>,
}

impl PersistenceReader {
    /// Open a read-only connection to the responsibility database.
    /// The writer must have already set WAL mode on the database file.
    /// Uses read_uncommitted=0 for proper snapshot isolation.
    ///
    /// Validation is shared with every other opener: a legacy causal or
    /// unrecognised database is rejected instead of being served as a run.
    pub fn open(path: &str) -> std::result::Result<Self, SchemaError> {
        let conn = open_responsibility_db_read_only(path)?;
        Ok(Self {
            conn,
            path: Some(std::path::PathBuf::from(path)),
        })
    }

    /// The database file behind this reader, when it has one.
    ///
    /// Threading this path through is what makes replay open the *same* file
    /// it read from; the previous implementation guessed among hardcoded
    /// paths and could silently replay a different database.
    pub fn db_path(&self) -> Option<&std::path::Path> {
        self.path.as_deref()
    }

    /// Open from an existing in-memory or read-write connection (for testing).
    #[allow(dead_code)]
    pub(crate) fn from_connection(conn: Connection) -> Self {
        Self { conn, path: None }
    }
}

// ═══════════════════════════════════════════════════════════════════════════
// Run queries
// ═══════════════════════════════════════════════════════════════════════════

impl PersistenceReader {
    /// Whether this database carries the v3 run-verdict columns.
    fn has_final_verdict_columns(&self) -> Result<bool> {
        let mut stmt = self.conn.prepare("PRAGMA table_info(runs)")?;
        let mut rows = stmt.query([])?;
        while let Some(row) = rows.next()? {
            let name: String = row.get(1)?;
            if name == "final_decision" {
                return Ok(true);
            }
        }
        Ok(false)
    }

    fn run_record_columns(&self) -> Result<&'static str> {
        Ok(if self.has_final_verdict_columns()? {
            "run_id, started_at_ns, ended_at_ns, scenario_id, mode, collector_version, \
             final_decision, final_severity, final_authority, final_alert_id, \
             decision_provenance_json"
        } else {
            "run_id, started_at_ns, ended_at_ns, scenario_id, mode, collector_version, \
             NULL, NULL, NULL, NULL, NULL"
        })
    }

    fn map_run_record(row: &rusqlite::Row<'_>) -> rusqlite::Result<RunRecord> {
        let provenance: Option<String> = row.get(10)?;
        Ok(RunRecord {
            run_id: row.get(0)?,
            started_at_ns: row.get::<_, i64>(1)? as u64,
            ended_at_ns: row.get::<_, Option<i64>>(2)?.map(|v| v as u64),
            scenario_id: row.get(3)?,
            mode: row.get(4)?,
            collector_version: row.get(5)?,
            final_decision: row.get(6)?,
            final_severity: row.get(7)?,
            final_authority: row.get(8)?,
            final_alert_id: row.get(9)?,
            decision_provenance: provenance
                .and_then(|raw| serde_json::from_str::<Vec<String>>(&raw).ok())
                .unwrap_or_default(),
        })
    }

    /// Get a single run by ID.
    pub fn get_run(&self, run_id: &str) -> Result<Option<RunRecord>> {
        let sql = format!(
            "SELECT {} FROM runs WHERE run_id = ?1",
            self.run_record_columns()?
        );
        let mut stmt = self.conn.prepare(&sql)?;
        let mut rows = stmt.query_map(params![run_id], Self::map_run_record)?;
        match rows.next() {
            Some(Ok(run)) => Ok(Some(run)),
            Some(Err(e)) => Err(e),
            None => Ok(None),
        }
    }

    /// List all runs, ordered by start time descending.
    pub fn get_analysis_checkpoint(
        &self,
        run_id: &str,
        kind: &str,
    ) -> Result<Option<(u32, serde_json::Value)>> {
        use rusqlite::OptionalExtension;
        if !self
            .conn
            .prepare(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='analysis_checkpoints'",
            )?
            .exists([])?
        {
            return Ok(None);
        }
        self.conn.query_row(
            "SELECT version,payload_json FROM analysis_checkpoints WHERE run_id=?1 AND checkpoint_kind=?2",
            params![run_id, kind],
            |row| {
                let raw: String = row.get(1)?;
                let decoded = super::codec::decode_json(&raw).map_err(|error| {
                    rusqlite::Error::FromSqlConversionFailure(
                        1,
                        rusqlite::types::Type::Text,
                        Box::new(std::io::Error::new(
                            std::io::ErrorKind::InvalidData,
                            error,
                        )),
                    )
                })?;
                Ok((row.get(0)?, deser_required(&decoded, "analysis_checkpoint")?))
            },
        ).optional()
    }

    /// List all runs, ordered by start time descending.
    pub fn list_runs(&self) -> Result<Vec<RunRecord>> {
        let sql = format!(
            "SELECT {} FROM runs ORDER BY started_at_ns DESC",
            self.run_record_columns()?
        );
        let mut stmt = self.conn.prepare(&sql)?;
        let rows = stmt.query_map([], Self::map_run_record)?;
        rows.collect()
    }
}

// ═══════════════════════════════════════════════════════════════════════════
// Alert queries
// ═══════════════════════════════════════════════════════════════════════════

impl PersistenceReader {
    /// Get all alerts for a run.
    pub fn get_alerts(&self, run_id: &str) -> Result<Vec<ResponsibilityAlert>> {
        let mut stmt = self.conn.prepare(
            "SELECT alert_id, timestamp_ns, display_profile, decision, severity, confidence,
                    authority, reasoning, title, summary, action_hint, primary_agent_id,
                    involved_agents_json, engine_section_json, timeline_json,
                    graph_metrics_json, phase, provisional_ref
             FROM responsibility_alerts WHERE run_id = ?1 ORDER BY timestamp_ns ASC",
        )?;
        let rows = stmt.query_map(params![run_id], map_alert_row)?;
        rows.collect()
    }

    /// Get only the final authoritative alerts of a run.
    ///
    /// Provisional alerts are phase-1 history; replay emits the authoritative
    /// set, so strict parity must compare like with like.
    pub fn get_authoritative_alerts(&self, run_id: &str) -> Result<Vec<ResponsibilityAlert>> {
        let mut stmt = self.conn.prepare(
            "SELECT alert_id, timestamp_ns, display_profile, decision, severity, confidence,
                    authority, reasoning, title, summary, action_hint, primary_agent_id,
                    involved_agents_json, engine_section_json, timeline_json,
                    graph_metrics_json, phase, provisional_ref
             FROM responsibility_alerts
             WHERE run_id = ?1 AND phase LIKE 'Authoritative%'
             ORDER BY timestamp_ns ASC",
        )?;
        let rows = stmt.query_map(params![run_id], map_alert_row)?;
        rows.collect()
    }

    /// Get only the phase-1 (provisional) alerts of a run.
    ///
    /// Replay compared every persisted alert against its own phase-2 output,
    /// so a phase-2 alert counted as both "phase 1" and "phase 2": confirmed
    /// was always zero and discovered was inflated. `phase` is stored as the
    /// Debug form of `PhaseIndicator`, hence the prefix match.
    pub fn get_provisional_alerts(&self, run_id: &str) -> Result<Vec<ResponsibilityAlert>> {
        let mut stmt = self.conn.prepare(
            "SELECT alert_id, timestamp_ns, display_profile, decision, severity, confidence,
                    authority, reasoning, title, summary, action_hint, primary_agent_id,
                    involved_agents_json, engine_section_json, timeline_json,
                    graph_metrics_json, phase, provisional_ref
             FROM responsibility_alerts
             WHERE run_id = ?1 AND phase LIKE 'Provisional%'
             ORDER BY timestamp_ns ASC",
        )?;
        let rows = stmt.query_map(params![run_id], map_alert_row)?;
        rows.collect()
    }

    /// Get alerts for one run, newest first — the REST `/alerts` contract.
    ///
    /// Kept separate from [`Self::get_alerts`] (which is ASC for replay) so the
    /// ordering of equal timestamps matches the previous whole-DB query.
    pub fn get_alerts_desc(&self, run_id: &str) -> Result<Vec<ResponsibilityAlert>> {
        let mut stmt = self.conn.prepare(
            "SELECT alert_id, timestamp_ns, display_profile, decision, severity, confidence,
                    authority, reasoning, title, summary, action_hint, primary_agent_id,
                    involved_agents_json, engine_section_json, timeline_json,
                    graph_metrics_json, phase, provisional_ref
             FROM responsibility_alerts WHERE run_id = ?1 ORDER BY timestamp_ns DESC",
        )?;
        let rows = stmt.query_map(params![run_id], map_alert_row)?;
        rows.collect()
    }

    /// Distinct run ids present in the alerts table, newest run first.
    ///
    /// The REST route names a run *directory*, while alerts are keyed by the
    /// collector's run UUID. The server uses this list to resolve the two
    /// without guessing: one id is accepted as-is, several ids require an
    /// explicit match instead of silently mixing runs.
    pub fn list_alert_run_ids(&self) -> Result<Vec<String>> {
        let mut stmt = self.conn.prepare(
            "SELECT run_id, MAX(timestamp_ns) AS newest
             FROM responsibility_alerts GROUP BY run_id ORDER BY newest DESC",
        )?;
        let rows = stmt.query_map([], |row| row.get::<_, String>(0))?;
        rows.collect()
    }

    /// Get every alert in the DB, newest first.
    pub fn get_alerts_all(&self) -> Result<Vec<ResponsibilityAlert>> {
        let mut stmt = self.conn.prepare(
            "SELECT alert_id, timestamp_ns, display_profile, decision, severity, confidence,
                    authority, reasoning, title, summary, action_hint, primary_agent_id,
                    involved_agents_json, engine_section_json, timeline_json,
                    graph_metrics_json, phase, provisional_ref
             FROM responsibility_alerts ORDER BY timestamp_ns DESC",
        )?;
        let rows = stmt.query_map([], map_alert_row)?;
        rows.collect()
    }
}

impl PersistenceReader {
    /// Delegation records for a run, oldest first.
    ///
    /// The live runtime uses these as the taint authorization input; replay
    /// reads them from the same table so it reaches the same classification.
    pub fn get_delegations(&self, run_id: &str) -> Result<Vec<DelegationRecord>> {
        let mut stmt = self.conn.prepare(
            "SELECT delegation_id, from_agent_id, to_agent_id, parent_pid,
                    parent_start_time_ns, child_pid, child_start_time_ns,
                    timestamp_ns, confidence
             FROM delegation_records WHERE run_id = ?1 ORDER BY timestamp_ns ASC",
        )?;
        let rows = stmt.query_map(params![run_id], |row| {
            Ok(DelegationRecord {
                delegation_id: row.get(0)?,
                from_agent: AgentId::from(row.get::<_, String>(1)?),
                to_agent: AgentId::from(row.get::<_, String>(2)?),
                parent_process: ProcessId::new(
                    row.get::<_, i64>(3)? as u32,
                    row.get::<_, i64>(4)? as u64,
                ),
                child_process: ProcessId::new(
                    row.get::<_, i64>(5)? as u32,
                    row.get::<_, i64>(6)? as u64,
                ),
                timestamp_ns: row.get::<_, i64>(7)? as u64,
                confidence: row.get(8)?,
            })
        })?;
        rows.collect()
    }
}

fn map_alert_row(row: &rusqlite::Row<'_>) -> rusqlite::Result<ResponsibilityAlert> {
    let involved_raw: String = row.get(12)?;
    let engine_raw: String = row.get(13)?;
    let timeline_raw: String = row.get(14)?;
    let metrics_raw: String = row.get(15)?;
    let phase_raw: String = row.get(16)?;

    // Derived, not persisted: recompute from the stored decision so old rows
    // and new rows share the same product contract.
    let decision = parse_decision(&row.get::<_, String>(3)?);
    let actionable = decision.is_actionable();

    Ok(ResponsibilityAlert {
        alert_id: AlertId::from(row.get::<_, String>(0)?),
        timestamp_ns: row.get::<_, i64>(1)? as u64,
        display_profile: parse_display_profile(&row.get::<_, String>(2)?),
        decision,
        actionable,
        severity: parse_severity(&row.get::<_, String>(4)?),
        confidence: row.get(5)?,
        authority: parse_authority(&row.get::<_, String>(6)?),
        reasoning: row.get(7)?,
        title: row.get(8)?,
        summary: row.get(9)?,
        action_hint: row.get(10)?,
        primary_agent: AgentRef {
            agent_id: AgentId::from(row.get::<_, String>(11)?),
            claimed_role: None,
        },
        involved_agents: deser_json(&involved_raw, "involved_agents")?,
        engine: deser_json(&engine_raw, "engine_section")?,
        timeline: deser_timeline(&timeline_raw)?,
        graph_snapshot: deser_json(&metrics_raw, "graph_snapshot")?,
        phase: parse_phase(&phase_raw)?,
        provisional_ref: row.get::<_, Option<String>>(17)?.map(AlertId::from),
    })
}

// ═══════════════════════════════════════════════════════════════════════════
// Evidence queries
// ═══════════════════════════════════════════════════════════════════════════

impl PersistenceReader {
    /// Get all correlation evidence for a run, with related intents and actions.
    pub fn get_evidence(&self, run_id: &str) -> Result<Vec<CorrelationEvidence>> {
        let mut stmt = self.conn.prepare(
            "SELECT evidence_id, intent_id, action_id, match_level, confidence,
                    matched_fields_json, derivation_mode, phase, timestamp_ns
             FROM correlation_evidence WHERE run_id = ?1 ORDER BY timestamp_ns ASC",
        )?;
        let rows = stmt.query_map(params![run_id], |row| {
            let matched_raw: String = row.get(5)?;
            let phase_raw: String = row.get(7)?;
            Ok(CorrelationEvidence {
                evidence_id: EvidenceId::from(row.get::<_, String>(0)?),
                intent_id: IntentId::from(row.get::<_, String>(1)?),
                action_id: ActionId::from(row.get::<_, String>(2)?),
                match_level: parse_match_level(&row.get::<_, String>(3)?),
                confidence: row.get(4)?,
                matched_fields: deser_json(&matched_raw, "matched_fields")?,
                derivation_mode: parse_derivation_mode(
                    &row.get::<_, Option<String>>(6)?.unwrap_or_default(),
                ),
                phase: parse_phase(&phase_raw)?,
                timestamp_ns: row.get::<_, i64>(8)? as u64,
            })
        })?;
        rows.collect()
    }

    /// Get only the final authoritative evidence of a run.
    ///
    /// The live runtime persists every streaming (provisional) correlation and
    /// later supersedes it with the authoritative batch. Replay reconstructs
    /// only the final batch, so parity must be measured against the
    /// authoritative rows; provisional rows are historical phase-1 state.
    pub fn get_authoritative_evidence(&self, run_id: &str) -> Result<Vec<CorrelationEvidence>> {
        let mut stmt = self.conn.prepare(
            "SELECT evidence_id, intent_id, action_id, match_level, confidence,
                    matched_fields_json, derivation_mode, phase, timestamp_ns
             FROM correlation_evidence
             WHERE run_id = ?1 AND phase LIKE 'Authoritative%'
             ORDER BY timestamp_ns ASC",
        )?;
        let rows = stmt.query_map(params![run_id], |row| {
            let matched_raw: String = row.get(5)?;
            let phase_raw: String = row.get(7)?;
            Ok(CorrelationEvidence {
                evidence_id: EvidenceId::from(row.get::<_, String>(0)?),
                intent_id: IntentId::from(row.get::<_, String>(1)?),
                action_id: ActionId::from(row.get::<_, String>(2)?),
                match_level: parse_match_level(&row.get::<_, String>(3)?),
                confidence: row.get(4)?,
                matched_fields: deser_json(&matched_raw, "matched_fields")?,
                derivation_mode: parse_derivation_mode(
                    &row.get::<_, Option<String>>(6)?.unwrap_or_default(),
                ),
                phase: parse_phase(&phase_raw)?,
                timestamp_ns: row.get::<_, i64>(8)? as u64,
            })
        })?;
        rows.collect()
    }

    /// Get intents for a run.
    pub fn get_intents(&self, run_id: &str) -> Result<Vec<Intent>> {
        let native_columns =
            if super::schema::column_exists(&self.conn, "intents", "native_tool_call_json")? {
                "native_tool_call_json,declared_conversation_id"
            } else {
                "NULL,NULL"
            };
        let mut stmt = self.conn.prepare(&format!(
            "SELECT intent_id, pid, process_start_time_ns, message_id, tool_name,
                    tool_args_json, intent_class, parse_state, primary_matching_eligible,
                    authority_source, timestamp_ns,{native_columns}
             FROM intents WHERE run_id = ?1 ORDER BY timestamp_ns ASC",
        ))?;
        let rows = stmt.query_map(params![run_id], |row| {
            let args_raw: String = row.get(5)?;
            let native_json: Option<String> = row.get(11)?;
            let native_tool_call = match native_json {
                Some(raw) => Some(deser_required(&raw, "native_tool_call_json")?),
                None => None,
            };
            Ok(Intent {
                intent_id: IntentId::from(row.get::<_, String>(0)?),
                process_id: ProcessId::new(
                    row.get::<_, i64>(1)? as u32,
                    row.get::<_, i64>(2)? as u64,
                ),
                message_id: MessageId::from(row.get::<_, String>(3)?),
                tool_name: row.get(4)?,
                tool_args: deser_json(&args_raw, "tool_args")?,
                intent_class: parse_intent_class(&row.get::<_, String>(6)?),
                parse_state: parse_parse_state(&row.get::<_, String>(7)?),
                primary_matching_eligible: row.get::<_, i64>(8)? != 0,
                authority_source: parse_authority_source(&row.get::<_, String>(9)?),
                timestamp_ns: row.get::<_, i64>(10)? as u64,
                native_tool_call,
                declared_conversation_id: row.get(12)?,
            })
        })?;
        rows.collect()
    }

    /// Get actions for a run.
    pub fn get_actions(&self, run_id: &str) -> Result<Vec<Action>> {
        let outcome_columns = if super::schema::column_exists(&self.conn, "actions", "outcome")? {
            "outcome,syscall_ret,syscall_name"
        } else {
            "'unknown',NULL,NULL"
        };
        let mut stmt = self.conn.prepare(&format!(
            "SELECT action_id, pid, process_start_time_ns, action_type,
                    normalized_args_json, derivation_mode, signal_class, timestamp_ns,{outcome_columns}
             FROM actions WHERE run_id = ?1 ORDER BY timestamp_ns ASC",
        ))?;
        let rows = stmt.query_map(params![run_id], |row| {
            let args_raw: String = row.get(4)?;
            Ok(Action {
                action_id: ActionId::from(row.get::<_, String>(0)?),
                process_id: ProcessId::new(
                    row.get::<_, i64>(1)? as u32,
                    row.get::<_, i64>(2)? as u64,
                ),
                action_type: parse_action_type(&row.get::<_, String>(3)?)?,
                normalized_args: deser_json(&args_raw, "normalized_args")?,
                derivation_mode: parse_derivation_mode(&row.get::<_, String>(5)?),
                signal_class: parse_signal_class(&row.get::<_, String>(6)?),
                outcome: deser_required(
                    &serde_json::to_string(&row.get::<_, String>(8)?).unwrap(),
                    "action_outcome",
                )?,
                syscall_ret: row.get(9)?,
                syscall_name: row.get(10)?,
                timestamp_ns: row.get::<_, i64>(7)? as u64,
            })
        })?;
        rows.collect()
    }

    /// Get non-link diagnostics for a run.
    pub fn get_non_link_diagnostics(&self, run_id: &str) -> Result<Vec<NonLinkDiagnostic>> {
        let mut stmt = self.conn.prepare(
            "SELECT diagnostic_id, intent_id, action_id, reason, timestamp_ns
             FROM non_link_diagnostics WHERE run_id = ?1 ORDER BY timestamp_ns ASC",
        )?;
        let rows = stmt.query_map(params![run_id], |row| {
            Ok(NonLinkDiagnostic {
                schema_version: 1,
                diagnostic_id: row.get(0)?,
                intent_id: row.get::<_, Option<String>>(1)?,
                candidate_id: row.get::<_, Option<String>>(2)?,
                reason: parse_non_link_reason(&row.get::<_, String>(3)?),
                timestamp_ns: row.get::<_, i64>(4)? as u64,
                agent_id: None,
                stage: String::new(),
                intent_class: None,
                parse_state: None,
                match_level_considered: None,
                confidence_observed: None,
                details: None,
            })
        })?;
        rows.collect()
    }
}

// ═══════════════════════════════════════════════════════════════════════════
// Graph queries
// ═══════════════════════════════════════════════════════════════════════════

impl PersistenceReader {
    /// Get the full responsibility graph for a run (nodes + edges).
    ///
    /// The final `graph_snapshots` row is the canonical graph: it is the only
    /// representation finalization writes. The legacy `graph_nodes` /
    /// `graph_edges` tables are read only when no snapshot exists, so an
    /// archived database and a short run that never finalized stay readable.
    pub fn get_graph(
        &self,
        run_id: &str,
    ) -> Result<(Vec<ResponsibilityNode>, Vec<ResponsibilityEdge>)> {
        if let Some((nodes, edges)) = self.get_latest_graph_snapshot(run_id)? {
            return Ok((nodes, edges));
        }
        let nodes = self.get_graph_nodes(run_id)?;
        let edges = self.get_graph_edges(run_id)?;
        Ok((nodes, edges))
    }

    /// Read the newest snapshot for a run, if one exists.
    ///
    /// Ordering matches the graph HTTP API (`timestamp_ns`, then
    /// `snapshot_id`), so the two views can never select different snapshots.
    /// A corrupt snapshot is an error, not a reason to silently serve the
    /// legacy tables: a snapshot row exists, so it is the best available
    /// answer and its corruption must be visible.
    fn get_latest_graph_snapshot(
        &self,
        run_id: &str,
    ) -> Result<Option<(Vec<ResponsibilityNode>, Vec<ResponsibilityEdge>)>> {
        use rusqlite::OptionalExtension;
        self.conn
            .query_row(
                "SELECT nodes_json, edges_json FROM graph_snapshots
                 WHERE run_id = ?1
                 ORDER BY timestamp_ns DESC, snapshot_id DESC LIMIT 1",
                params![run_id],
                |row| {
                    let nodes = deser_required::<Vec<ResponsibilityNode>>(
                        &row.get::<_, String>(0)?,
                        "graph_snapshot.nodes",
                    )?;
                    let edges = deser_required::<Vec<ResponsibilityEdge>>(
                        &row.get::<_, String>(1)?,
                        "graph_snapshot.edges",
                    )?;
                    Ok((nodes, edges))
                },
            )
            .optional()
    }

    fn get_graph_nodes(&self, run_id: &str) -> Result<Vec<ResponsibilityNode>> {
        let mut stmt = self.conn.prepare(
            "SELECT node_id, node_type, agent_id, resource_json, evidence_refs_json,
                    timestamp_ns, phase
             FROM graph_nodes WHERE run_id = ?1 ORDER BY timestamp_ns ASC",
        )?;
        let rows = stmt.query_map(params![run_id], |row| {
            let resource_raw: Option<String> = row.get(3)?;
            let ev_refs_raw: String = row.get(4)?;
            let phase_raw: String = row.get(6)?;
            Ok(ResponsibilityNode {
                node_id: NodeId::from(row.get::<_, String>(0)?),
                node_type: parse_node_type(&row.get::<_, String>(1)?),
                agent_id: row.get::<_, Option<String>>(2)?.map(AgentId::from),
                resource: match resource_raw {
                    // "null" is the writer's representation of "no resource".
                    Some(raw) if raw.trim() == "null" => None,
                    Some(raw) => Some(deser_required(&raw, "graph_node.resource")?),
                    None => None,
                },
                evidence_refs: deser_json(&ev_refs_raw, "evidence_refs")?,
                timestamp_ns: row.get::<_, i64>(5)? as u64,
                phase: parse_phase(&phase_raw)?,
            })
        })?;
        rows.collect()
    }

    fn get_graph_edges(&self, run_id: &str) -> Result<Vec<ResponsibilityEdge>> {
        let operation =
            if super::schema::column_exists(&self.conn, "graph_edges", "operation_json")? {
                "operation_json"
            } else {
                "NULL"
            };
        let mut stmt = self.conn.prepare(&format!(
            "SELECT edge_id, edge_type, source_node_id, target_node_id,
                    evidence_refs_json, confidence, phase, {operation}
             FROM graph_edges WHERE run_id = ?1 ORDER BY rowid ASC",
        ))?;
        let rows = stmt.query_map(params![run_id], |row| {
            let ev_refs_raw: String = row.get(4)?;
            let phase_raw: String = row.get(6)?;
            let operation_raw: Option<String> = row.get(7)?;
            Ok(ResponsibilityEdge {
                operation: match operation_raw {
                    Some(raw) => deser_required(&raw, "graph_edge_operation")?,
                    None => None,
                },
                edge_id: EdgeId::from(row.get::<_, String>(0)?),
                edge_type: parse_edge_type(&row.get::<_, String>(1)?),
                source: NodeId::from(row.get::<_, String>(2)?),
                target: NodeId::from(row.get::<_, String>(3)?),
                evidence_refs: deser_json(&ev_refs_raw, "evidence_refs")?,
                confidence: row.get(5)?,
                phase: parse_phase(&phase_raw)?,
            })
        })?;
        rows.collect()
    }
}

// ═══════════════════════════════════════════════════════════════════════════
// Identity queries
// ═══════════════════════════════════════════════════════════════════════════

impl PersistenceReader {
    /// Get all identities for a run (both provisional and authoritative).
    pub fn get_identities(
        &self,
        run_id: &str,
    ) -> Result<(Vec<ProvisionalIdentity>, Vec<AuthoritativeIdentity>)> {
        let provisional = self.get_provisional_identities(run_id)?;
        let authoritative = self.get_authoritative_identities(run_id)?;
        Ok((provisional, authoritative))
    }

    fn get_provisional_identities(&self, run_id: &str) -> Result<Vec<ProvisionalIdentity>> {
        let mut stmt = self.conn.prepare(
            "SELECT agent_id, process_ids_json, primary_signal, confidence
             FROM provisional_identities WHERE run_id = ?1",
        )?;
        let rows = stmt.query_map(params![run_id], |row| {
            let pids_raw: String = row.get(1)?;
            Ok(ProvisionalIdentity {
                agent_id: AgentId::from(row.get::<_, String>(0)?),
                process_ids: deser_json(&pids_raw, "process_ids")?,
                primary_signal: parse_identity_signal(&row.get::<_, String>(2)?),
                confidence: row.get(3)?,
            })
        })?;
        rows.collect()
    }

    fn get_authoritative_identities(&self, run_id: &str) -> Result<Vec<AuthoritativeIdentity>> {
        let mut stmt = self.conn.prepare(
            "SELECT agent_id, process_ids_json, message_ids_json, tls_sessions_json,
                    confidence, provisional_refs_json
             FROM agent_identities WHERE run_id = ?1",
        )?;
        let rows = stmt.query_map(params![run_id], |row| {
            let pids_raw: String = row.get(1)?;
            let msgs_raw: String = row.get(2)?;
            let tlss_raw: String = row.get(3)?;
            let confidence_raw: String = row.get(4)?;
            let prov_raw: String = row.get(5)?;
            Ok(AuthoritativeIdentity {
                agent_id: AgentId::from(row.get::<_, String>(0)?),
                process_ids: deser_json(&pids_raw, "process_ids")?,
                message_ids: deser_json(&msgs_raw, "message_ids")?,
                tls_sessions: deser_json(&tlss_raw, "tls_sessions")?,
                confidence: parse_agent_confidence(&confidence_raw),
                provisional_refs: deser_json(&prov_raw, "provisional_refs")?,
            })
        })?;
        rows.collect()
    }
}

// ═══════════════════════════════════════════════════════════════════════════
// Evaluation data query
// ═══════════════════════════════════════════════════════════════════════════

/// Aggregated evaluation data for a run.
#[derive(Debug, Clone)]
pub struct EvaluationData {
    pub run_id: String,
    pub alert_count: usize,
    pub claim_count: usize,
    pub evidence_count: usize,
    pub node_count: usize,
    pub edge_count: usize,
    pub identity_count: usize,
    pub refinements: RefinementCounts,
}

#[derive(Debug, Clone, Default)]
pub struct RefinementCounts {
    pub identity: usize,
    pub evidence: usize,
    pub graph: usize,
    pub classification: usize,
    pub claim: usize,
}

impl PersistenceReader {
    /// Get aggregated evaluation data for a run.
    pub fn get_evaluation_data(&self, run_id: &str) -> Result<EvaluationData> {
        let alert_count = self.count("responsibility_alerts", run_id)?;
        let claim_count = self.count("responsibility_claims", run_id)?;
        let evidence_count = self.count("correlation_evidence", run_id)?;
        let node_count = self.count("graph_nodes", run_id)?;
        let edge_count = self.count("graph_edges", run_id)?;
        let identity_count = self.count("provisional_identities", run_id)?;

        let refinements = RefinementCounts {
            identity: self.count("identity_refinements", run_id)?,
            evidence: self.count("evidence_refinements", run_id)?,
            graph: self.count("graph_refinements", run_id)?,
            classification: self.count("classification_refinements", run_id)?,
            claim: self.count("claim_refinements", run_id)?,
        };

        Ok(EvaluationData {
            run_id: run_id.to_string(),
            alert_count,
            claim_count,
            evidence_count,
            node_count,
            edge_count,
            identity_count,
            refinements,
        })
    }

    fn count(&self, table: &str, run_id: &str) -> Result<usize> {
        self.conn.query_row(
            &format!("SELECT count(*) FROM {table} WHERE run_id = ?1"),
            params![run_id],
            |r| r.get::<_, i64>(0).map(|v| v as usize),
        )
    }
}

// ═══════════════════════════════════════════════════════════════════════════
// Parse helpers for Debug-format enums stored as TEXT
// ═══════════════════════════════════════════════════════════════════════════

fn parse_node_type(raw: &str) -> ResponsibilityNodeType {
    match raw {
        "Agent" => ResponsibilityNodeType::Agent,
        "Resource" => ResponsibilityNodeType::Resource,
        "PolicyBoundary" => ResponsibilityNodeType::PolicyBoundary,
        _ => ResponsibilityNodeType::Agent,
    }
}

fn parse_edge_type(raw: &str) -> ResponsibilityEdgeType {
    match raw {
        "IntentCausesAction" => ResponsibilityEdgeType::IntentCausesAction,
        "AgentDelegatesTo" => ResponsibilityEdgeType::AgentDelegatesTo,
        "ContextFlowsFrom" => ResponsibilityEdgeType::ContextFlowsFrom,
        "TaintPropagatesTo" => ResponsibilityEdgeType::TaintPropagatesTo,
        "PolicyBoundaryCrossing" => ResponsibilityEdgeType::PolicyBoundaryCrossing,
        "CompetingExplanation" => ResponsibilityEdgeType::CompetingExplanation,
        _ => ResponsibilityEdgeType::IntentCausesAction,
    }
}

fn parse_identity_signal(raw: &str) -> IdentitySignal {
    match raw {
        "TlsSessionShared" => IdentitySignal::TlsSessionShared,
        "TlsSessionNew" => IdentitySignal::TlsSessionNew,
        "ProcessTreeInherited" => IdentitySignal::ProcessTreeInherited,
        "Unknown" => IdentitySignal::Unknown,
        _ => IdentitySignal::Unknown,
    }
}

fn parse_agent_confidence(raw: &str) -> AgentConfidence {
    match raw {
        "Authoritative" => AgentConfidence::Authoritative,
        "Inferred" => AgentConfidence::Inferred,
        _ => AgentConfidence::Inferred,
    }
}

// ═══════════════════════════════════════════════════════════════════════════
// Tests
// ═══════════════════════════════════════════════════════════════════════════

#[cfg(test)]
mod tests {
    use super::*;
    use crate::responsibility::persistence::schema::open_responsibility_db;

    fn setup_db() -> (tempfile::TempDir, String) {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("reader_test.db");
        let path_str = path.to_string_lossy().into_owned();
        (dir, path_str)
    }

    /// The writer persists `format!("{:?}", action.action_type)`. Every
    /// variant must round-trip, or a row reads back as a different action —
    /// the unknown fallback is `FileRead`, which would silently turn a rename
    /// into a read.
    #[test]
    fn parse_action_type_covers_every_persisted_variant() {
        use crate::responsibility::types::ActionCandidateType;

        for (raw, expected) in [
            ("FileOpen", ActionCandidateType::FileOpen),
            ("FileRead", ActionCandidateType::FileRead),
            ("FileWrite", ActionCandidateType::FileWrite),
            ("FileOverwrite", ActionCandidateType::FileOverwrite),
            ("FileDelete", ActionCandidateType::FileDelete),
            ("FileRename", ActionCandidateType::FileRename),
            ("NetworkConnect", ActionCandidateType::NetworkConnect),
            ("NetworkSend", ActionCandidateType::NetworkSend),
            ("ProcessExec", ActionCandidateType::ProcessExec),
            ("ProcessSpawn", ActionCandidateType::ProcessSpawn),
        ] {
            assert_eq!(parse_action_type(raw).unwrap(), expected, "variant {raw}");
        }
        assert!(parse_action_type("FutureUnknownOperation").is_err());
    }

    /// The writer persists match_level with Debug spelling; a missing variant
    /// silently downgrades evidence to L5Substring and changes replay.
    #[test]
    fn parse_match_level_covers_every_persisted_variant() {
        use crate::responsibility::types::MatchLevel;
        for (raw, expected) in [
            ("L1Exact", MatchLevel::L1Exact),
            ("L2Normalized", MatchLevel::L2Normalized),
            ("L3Containment", MatchLevel::L3Containment),
            ("L4Basename", MatchLevel::L4Basename),
            ("L5Substring", MatchLevel::L5Substring),
            ("ProcessLineage", MatchLevel::ProcessLineage),
        ] {
            assert_eq!(parse_match_level(raw), expected, "variant {raw}");
        }
    }

    #[test]
    fn recorded_phase_preserves_time_and_rejects_unknown_authority() {
        assert_eq!(
            parse_phase("Provisional { timestamp_ns: 123 }").unwrap(),
            PhaseIndicator::Provisional { timestamp_ns: 123 }
        );
        assert!(parse_phase("corrupt phase").is_err());
    }

    /// Replay must see the same delegation records the live run used for the
    /// taint authorization gate; otherwise a replayed run classifies an
    /// authorized share as a violation.
    #[test]
    fn get_delegations_returns_the_persisted_records() {
        let (_dir, path) = setup_db();
        let conn = open_responsibility_db(&path).unwrap();
        conn.execute(
            "INSERT INTO delegation_records
             (delegation_id, from_agent_id, to_agent_id, parent_pid, parent_start_time_ns,
              child_pid, child_start_time_ns, timestamp_ns, confidence, run_id)
             VALUES ('d1', 'writer', 'reader', 10, 100, 11, 200, 300, 0.85, 'r1')",
            [],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO delegation_records
             (delegation_id, from_agent_id, to_agent_id, parent_pid, parent_start_time_ns,
              child_pid, child_start_time_ns, timestamp_ns, confidence, run_id)
             VALUES ('d2', 'other', 'writer', 12, 400, 13, 500, 600, 0.85, 'r2')",
            [],
        )
        .unwrap();
        drop(conn);

        let reader = PersistenceReader::open(&path).unwrap();
        let records = reader.get_delegations("r1").unwrap();
        assert_eq!(records.len(), 1);
        assert_eq!(records[0].from_agent.as_str(), "writer");
        assert_eq!(records[0].to_agent.as_str(), "reader");
        assert_eq!(records[0].parent_process.pid, 10);
        assert_eq!(records[0].child_process.pid, 11);
        assert_eq!(records[0].timestamp_ns, 300);
    }

    #[test]
    fn test_list_runs() {
        let (_dir, path) = setup_db();
        let conn = open_responsibility_db(&path).unwrap();
        conn.execute(
            "INSERT INTO runs (run_id, started_at_ns) VALUES ('r1', 100)",
            [],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO runs (run_id, started_at_ns) VALUES ('r2', 200)",
            [],
        )
        .unwrap();
        drop(conn);

        let reader = PersistenceReader::open(&path).unwrap();
        let runs = reader.list_runs().unwrap();
        assert_eq!(runs.len(), 2);
        // Ordered DESC by started_at_ns
        assert_eq!(runs[0].run_id, "r2");
        assert_eq!(runs[1].run_id, "r1");
    }

    #[test]
    fn test_get_run() {
        let (_dir, path) = setup_db();
        let conn = open_responsibility_db(&path).unwrap();
        conn.execute(
            "INSERT INTO runs (run_id, started_at_ns, ended_at_ns, scenario_id) VALUES ('r1', 100, 200, 'sc1')",
            [],
        )
        .unwrap();
        drop(conn);

        let reader = PersistenceReader::open(&path).unwrap();
        let run = reader.get_run("r1").unwrap().unwrap();
        assert_eq!(run.run_id, "r1");
        assert_eq!(run.started_at_ns, 100);
        assert_eq!(run.ended_at_ns, Some(200));
        assert_eq!(run.scenario_id.as_deref(), Some("sc1"));

        assert!(reader.get_run("nonexistent").unwrap().is_none());
    }

    /// A corrupt JSON column must surface as an error. Returning an empty
    /// structure here would present "no agents involved" for a row that
    /// actually recorded agents — a silent lie in the accountability chain.
    #[test]
    fn corrupt_json_columns_surface_as_errors() {
        let (_dir, path) = setup_db();
        let conn = open_responsibility_db(&path).unwrap();
        conn.execute(
            "INSERT INTO runs (run_id, started_at_ns) VALUES ('r1', 0)",
            [],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO responsibility_alerts (
                 alert_id, timestamp_ns, display_profile, decision, severity, confidence,
                 authority, reasoning, title, summary, action_hint, primary_agent_id,
                 involved_agents_json, engine_section_json, timeline_json, graph_metrics_json,
                 phase, provisional_ref, run_id)
             VALUES ('a1', 1, 'UnsafeByEngine', 'Unsafe', 'Critical', 0.9,
                 'Authoritative', '', '', '', NULL, 'agent-1',
                 '[{not json', '{}', '[]', '{}',
                 'Authoritative { timestamp_ns: 1 }', NULL, 'r1')",
            [],
        )
        .unwrap();
        drop(conn);

        let reader = PersistenceReader::open(&path).unwrap();
        let err = reader.get_alerts("r1").unwrap_err().to_string();
        assert!(err.contains("involved_agents"), "got: {err}");
    }

    /// `graph_nodes.resource_json` must not be coerced to `None` when the
    /// column is corrupt: a resource-less node is a different fact.
    #[test]
    fn corrupt_graph_node_resource_surfaces_as_error() {
        let (_dir, path) = setup_db();
        let conn = open_responsibility_db(&path).unwrap();
        conn.execute(
            "INSERT INTO graph_nodes
             (node_id, node_type, agent_id, resource_json, evidence_refs_json,
              timestamp_ns, phase, run_id)
             VALUES ('n1', 'Resource', NULL, '{not json', '[]', 1,
                     'Authoritative { timestamp_ns: 1, overrides: None }', 'r1')",
            [],
        )
        .unwrap();
        drop(conn);

        let reader = PersistenceReader::open(&path).unwrap();
        let err = reader.get_graph("r1").unwrap_err().to_string();
        assert!(err.contains("graph_node.resource"), "got: {err}");
    }

    /// The schema default for `timeline_json` is `'[]'`, which cannot
    /// deserialize into `BehaviorTimeline`; it must read as "no timeline",
    /// not as a corrupt row (otherwise every defaulted alert breaks the API).
    #[test]
    fn schema_default_timeline_reads_as_empty() {
        let (_dir, path) = setup_db();
        let conn = open_responsibility_db(&path).unwrap();
        conn.execute(
            "INSERT INTO runs (run_id, started_at_ns) VALUES ('r1', 0)",
            [],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO responsibility_alerts (
                 alert_id, timestamp_ns, display_profile, decision, severity, confidence,
                 authority, reasoning, title, summary, action_hint, primary_agent_id,
                 involved_agents_json, engine_section_json, timeline_json, graph_metrics_json,
                 phase, provisional_ref, run_id)
             VALUES ('a1', 1, 'UnsafeByEngine', 'Unsafe', 'Warning', 0.8,
                 'Authoritative', '', '', '', NULL, 'agent-1',
                 '[]', '{}', '[]', '{}',
                 'Authoritative { timestamp_ns: 1 }', NULL, 'r1')",
            [],
        )
        .unwrap();
        drop(conn);

        let reader = PersistenceReader::open(&path).unwrap();
        let alerts = reader.get_alerts("r1").unwrap();
        assert_eq!(alerts.len(), 1);
        assert!(alerts[0].timeline.steps.is_empty());
        assert!(alerts[0].engine.rule_verdicts.is_empty());
    }

    #[test]
    fn test_get_alerts_empty() {
        let (_dir, path) = setup_db();
        let conn = open_responsibility_db(&path).unwrap();
        conn.execute(
            "INSERT INTO runs (run_id, started_at_ns) VALUES ('r1', 0)",
            [],
        )
        .unwrap();
        drop(conn);

        let reader = PersistenceReader::open(&path).unwrap();
        let alerts = reader.get_alerts("r1").unwrap();
        assert!(alerts.is_empty());
    }

    #[test]
    fn test_get_evidence_empty() {
        let (_dir, path) = setup_db();
        let conn = open_responsibility_db(&path).unwrap();
        conn.execute(
            "INSERT INTO runs (run_id, started_at_ns) VALUES ('r1', 0)",
            [],
        )
        .unwrap();
        drop(conn);

        let reader = PersistenceReader::open(&path).unwrap();
        let evidence = reader.get_evidence("r1").unwrap();
        assert!(evidence.is_empty());
    }

    fn snapshot_node(
        node_id: &str,
        node_type: ResponsibilityNodeType,
        agent_id: Option<&str>,
        timestamp_ns: u64,
    ) -> ResponsibilityNode {
        ResponsibilityNode {
            node_id: NodeId::from(node_id),
            node_type,
            agent_id: agent_id.map(AgentId::from),
            resource: None,
            evidence_refs: Vec::new(),
            timestamp_ns,
            phase: PhaseIndicator::Authoritative {
                timestamp_ns,
                overrides: None,
            },
        }
    }

    fn snapshot_edge(
        edge_id: &str,
        source: &str,
        target: &str,
        timestamp_ns: u64,
    ) -> ResponsibilityEdge {
        ResponsibilityEdge {
            edge_id: EdgeId::from(edge_id),
            edge_type: ResponsibilityEdgeType::AgentDelegatesTo,
            operation: None,
            source: NodeId::from(source),
            target: NodeId::from(target),
            evidence_refs: Vec::new(),
            confidence: 0.9,
            phase: PhaseIndicator::Authoritative {
                timestamp_ns,
                overrides: None,
            },
        }
    }
    /// The canonical graph is the latest `graph_snapshots` row. The legacy
    /// node/edge tables may still hold phase-1 rows (or a partially complete
    /// graph), so reading them first silently served a stale graph whenever
    /// the two representations diverged.
    #[test]
    fn get_graph_prefers_latest_snapshot_over_divergent_tables() {
        let (_dir, path) = setup_db();
        let conn = open_responsibility_db(&path).unwrap();
        conn.execute(
            "INSERT INTO runs (run_id, started_at_ns) VALUES ('r1', 0)",
            [],
        )
        .unwrap();
        // Divergent legacy rows: a stale agent that no snapshot names.
        conn.execute(
            "INSERT INTO graph_nodes
             (node_id, node_type, agent_id, evidence_refs_json, timestamp_ns, phase, run_id)
             VALUES ('stale-node', 'Agent', 'stale-agent', '[]', 5,
                     'Provisional { timestamp_ns: 5 }', 'r1')",
            [],
        )
        .unwrap();

        let old_nodes = vec![snapshot_node(
            "old-node",
            ResponsibilityNodeType::Agent,
            Some("old-agent"),
            10,
        )];
        let canonical_nodes = vec![
            snapshot_node(
                "canonical-agent-node",
                ResponsibilityNodeType::Agent,
                Some("canonical-agent"),
                20,
            ),
            snapshot_node(
                "canonical-resource-node",
                ResponsibilityNodeType::Resource,
                None,
                21,
            ),
        ];
        let canonical_edges = vec![snapshot_edge(
            "canonical-edge",
            "canonical-agent-node",
            "canonical-resource-node",
            22,
        )];
        for (snapshot_id, timestamp_ns, nodes, edges) in [
            ("snap-old", 10, &old_nodes, &Vec::new()),
            ("snap-canonical", 20, &canonical_nodes, &canonical_edges),
        ] {
            conn.execute(
                "INSERT INTO graph_snapshots
                 (snapshot_id, timestamp_ns, nodes_json, edges_json, metrics_json, run_id)
                 VALUES (?1, ?2, ?3, ?4, '{}', 'r1')",
                params![
                    snapshot_id,
                    timestamp_ns,
                    serde_json::to_string(nodes).unwrap(),
                    serde_json::to_string(edges).unwrap(),
                ],
            )
            .unwrap();
        }
        drop(conn);

        let reader = PersistenceReader::open(&path).unwrap();
        let (nodes, edges) = reader.get_graph("r1").unwrap();
        assert_eq!(nodes, canonical_nodes);
        assert_eq!(edges, canonical_edges);
    }
    /// A legacy database predates the final snapshot (or its run was too short
    /// to finalize), so the node/edge tables remain the only source.
    #[test]
    fn get_graph_falls_back_to_legacy_tables_without_a_snapshot() {
        let (_dir, path) = setup_db();
        let conn = open_responsibility_db(&path).unwrap();
        conn.execute(
            "INSERT INTO runs (run_id, started_at_ns) VALUES ('r1', 0)",
            [],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO graph_nodes
             (node_id, node_type, agent_id, evidence_refs_json, timestamp_ns, phase, run_id)
             VALUES ('legacy-agent', 'Agent', 'agent-1', '[]', 5,
                     'Authoritative { timestamp_ns: 5, overrides: None }', 'r1')",
            [],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO graph_nodes
             (node_id, node_type, resource_json, evidence_refs_json, timestamp_ns, phase, run_id)
             VALUES ('legacy-resource', 'Resource',
                     '{\"File\":{\"canonical_path\":\"/tmp/x\"}}',
                     '[]', 6, 'Authoritative { timestamp_ns: 6, overrides: None }', 'r1')",
            [],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO graph_edges
             (edge_id, edge_type, source_node_id, target_node_id,
              evidence_refs_json, confidence, phase, run_id)
             VALUES ('legacy-edge', 'IntentCausesAction', 'legacy-agent', 'legacy-resource',
                     '[]', 0.75, 'Authoritative { timestamp_ns: 6, overrides: None }', 'r1')",
            [],
        )
        .unwrap();
        drop(conn);

        let reader = PersistenceReader::open(&path).unwrap();
        let (nodes, edges) = reader.get_graph("r1").unwrap();
        let node_ids: Vec<_> = nodes.iter().map(|node| node.node_id.as_str()).collect();
        assert_eq!(node_ids, vec!["legacy-agent", "legacy-resource"]);
        assert_eq!(edges.len(), 1);
        assert_eq!(edges[0].edge_id.as_str(), "legacy-edge");
        assert_eq!(edges[0].confidence, 0.75);
    }
    #[test]
    fn test_get_graph_empty() {
        let (_dir, path) = setup_db();
        let conn = open_responsibility_db(&path).unwrap();
        conn.execute(
            "INSERT INTO runs (run_id, started_at_ns) VALUES ('r1', 0)",
            [],
        )
        .unwrap();
        drop(conn);

        let reader = PersistenceReader::open(&path).unwrap();
        let (nodes, edges) = reader.get_graph("r1").unwrap();
        assert!(nodes.is_empty());
        assert!(edges.is_empty());
    }

    #[test]
    fn test_get_evaluation_data() {
        let (_dir, path) = setup_db();
        let conn = open_responsibility_db(&path).unwrap();
        conn.execute(
            "INSERT INTO runs (run_id, started_at_ns) VALUES ('r1', 0)",
            [],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO responsibility_alerts (alert_id, timestamp_ns, display_profile, decision, severity, confidence, authority, primary_agent_id, phase, run_id)
             VALUES ('a1', 100, 'UnsafeByEngine', 'Unsafe', 'Warning', 0.9, 'Supporting', 'ag1', 'Provisional', 'r1')",
            [],
        ).unwrap();
        drop(conn);

        let reader = PersistenceReader::open(&path).unwrap();
        let eval = reader.get_evaluation_data("r1").unwrap();
        assert_eq!(eval.alert_count, 1);
        assert_eq!(eval.claim_count, 0);
        assert_eq!(eval.evidence_count, 0);
    }

    /// The `intents` columns are written with two different casing
    /// conventions, and these are the values a real run puts in the database
    /// (verified against a live capture): `intent_class` lower-case through
    /// `Display`, `parse_state` / `authority_source` PascalCase through serde.
    ///
    /// Parsing only PascalCase silently turned every stored `intent_class` into
    /// `Unknown` on read-back.
    #[test]
    fn intent_class_round_trips_the_casing_a_real_run_writes() {
        assert_eq!(parse_intent_class("side_effect"), IntentClass::SideEffect);
        assert_eq!(
            parse_intent_class("orchestration"),
            IntentClass::Orchestration
        );
        assert_eq!(parse_intent_class("unknown"), IntentClass::Unknown);
        // PascalCase and separators keep working, so replay fixtures and any
        // future convention change stay readable.
        assert_eq!(parse_intent_class("SideEffect"), IntentClass::SideEffect);
        assert_eq!(parse_intent_class("SIDE_EFFECT"), IntentClass::SideEffect);
        assert_eq!(
            parse_intent_class("SideEffect"),
            parse_intent_class("side_effect")
        );

        assert_eq!(parse_parse_state("Complete"), IntentParseState::Complete);
        assert_eq!(parse_parse_state("complete"), IntentParseState::Complete);
        assert_eq!(
            parse_authority_source("SseResponseToolUse"),
            IntentAuthoritySource::SseResponseToolUse
        );
    }

    #[test]
    fn intent_reads_work_when_native_columns_are_absent() {
        use crate::responsibility::persistence::schema::SCHEMA_V1_DDL;
        let (_dir, path) = setup_db();
        let conn = Connection::open(&path).unwrap();
        conn.execute_batch(SCHEMA_V1_DDL).unwrap();
        conn.execute(
            "INSERT INTO runs (run_id, started_at_ns) VALUES ('r1', 0)",
            [],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO intents (intent_id, pid, process_start_time_ns, message_id, tool_name,
             tool_args_json, intent_class, parse_state, primary_matching_eligible,
             authority_source, timestamp_ns, run_id)
             VALUES ('old', 1, 1, 'msg', 'Read', '[]', 'SideEffect', 'Complete', 1,
                     'HttpResponseToolUse', 1, 'r1')",
            [],
        )
        .unwrap();
        drop(conn);

        let reader = PersistenceReader::open(&path).unwrap();
        let intents = reader.get_intents("r1").unwrap();
        assert_eq!(intents.len(), 1);
        assert!(intents[0].native_tool_call.is_none());
        assert!(intents[0].declared_conversation_id.is_none());
    }
}
