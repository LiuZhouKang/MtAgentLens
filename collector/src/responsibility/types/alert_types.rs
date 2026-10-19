// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 MtAgentLens Project

use super::ids::*;
use super::phase::PhaseIndicator;
use serde::{Deserialize, Serialize};

// ═══ Alert severity and decision ═══

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum AlertSeverity {
    Info,
    Warning,
    Critical,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum AlertDecision {
    Safe,
    Unsafe,
    Uncertain,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum AlertAuthority {
    Authoritative,
    Supporting,
    Unavailable,
}

impl AlertAuthority {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Authoritative => "authoritative",
            Self::Supporting => "supporting",
            Self::Unavailable => "unavailable",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum DisplayProfile {
    SafeBaseline,
    UnsafeByEngine,
    ObserverJudged,
}

impl DisplayProfile {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::SafeBaseline => "safe_baseline",
            Self::UnsafeByEngine => "unsafe_by_engine",
            Self::ObserverJudged => "observer_judged",
        }
    }
}

// ═══ ResponsibilityAlert ═══

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ResponsibilityAlert {
    pub alert_id: AlertId,
    pub timestamp_ns: u64,
    pub display_profile: DisplayProfile,
    pub decision: AlertDecision,
    pub severity: AlertSeverity,
    pub confidence: f32,
    pub authority: AlertAuthority,
    pub reasoning: String,
    pub title: String,
    pub summary: String,
    pub action_hint: Option<String>,
    pub primary_agent: AgentRef,
    pub involved_agents: Vec<AgentRef>,
    pub engine: EngineSection,
    pub timeline: BehaviorTimeline,
    pub graph_snapshot: super::graph_types::GraphSnapshot,
    pub phase: PhaseIndicator,
    pub provisional_ref: Option<AlertId>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AgentRef {
    pub agent_id: AgentId,
    pub claimed_role: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(default)]
pub struct EngineSection {
    pub rule_verdicts: Vec<RuleVerdict>,
    pub pattern_verdicts: Vec<PatternVerdict>,
    pub classification_score: f32,
    pub classification_route: String,
    pub score_breakdown: super::refinement::ScoreBreakdown,
    /// CUSUM progressive scoring data — persisted for ablation measurement.
    #[serde(default)]
    pub cusum_triggered: bool,
    #[serde(default)]
    pub cusum_dimensions: Vec<String>,
    #[serde(default)]
    pub progressive_fused: f64,
    #[serde(default)]
    pub observer_trigger_reason: String,
    /// DeBERTa ONNX prompt injection score (D2Content dimension EWMA).
    /// Available when the ONNX model is loaded. 0.0 otherwise.
    #[serde(default)]
    pub deberta_score: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RuleVerdict {
    pub rule_id: String,
    pub description: String,
    pub matched_resource: String,
    pub llm_agreed: Option<bool>,
    pub llm_comment: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PatternVerdict {
    pub template_id: String,
    pub match_confidence: f32,
    pub llm_agreed: Option<bool>,
    pub llm_comment: Option<String>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct BehaviorTimeline {
    pub steps: Vec<BehaviorTimelineEntry>,
    pub total_duration_ms: u64,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct BehaviorTimelineEntry {
    pub timestamp_ns: u64,
    pub event_type: String,
    pub description: String,
    pub resource: Option<String>,
    pub evidence_ref: Option<EvidenceId>,
    // ── Enriched fields (populated when evidence/identity data is available) ──
    /// Agent that performed this action (from identity resolution).
    pub agent_id: Option<AgentId>,
    /// Semantic operation kind: "FileRead" / "FileWrite" / "NetworkConnect" / "ProcessExec" / "Delegation".
    pub operation: Option<String>,
    /// Evidence match level for this step: "L1Exact" / "L2Normalized" ... "L5Substring".
    pub evidence_level: Option<String>,
    /// Rule IDs that this behavior triggered.
    pub triggering_rule_ids: Vec<String>,
    /// Pattern template ID that this behavior matched.
    pub triggering_pattern_id: Option<String>,
    /// Original event reference for traceability.
    pub source_event_id: Option<String>,
}

// ═══ ResponsibilityClaim ═══

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ResponsibilityClaim {
    pub claim_id: ClaimId,
    pub claim_type: ResponsibilityClaimType,
    pub subject: AgentId,
    pub policy_id: PolicyId,
    pub boundary_id: BoundaryId,
    pub claim_state: ResponsibilityClaimState,
    pub severity: AlertSeverity,
    pub confidence: f32,
    pub evidence_refs: Vec<EvidenceId>,
    pub competing_explanations: Vec<CompetingExplanation>,
    pub traceback_ref: Option<TracebackId>,
    pub phase: PhaseIndicator,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq, Hash)]
pub enum ResponsibilityClaimType {
    PolicyViolation,
    ResponsibilityAnomaly,
    SuspiciousDelegation,
    TaintedContextUse,
    UnauthorizedCapabilityUse,
    BlockedAttempt,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum ResponsibilityClaimState {
    Observed,
    Inferred,
    Ambiguous,
    Unproven,
    Confirmed,
    BlockedAttempt,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CompetingExplanation {
    pub label: String,
    pub evidence_refs: Vec<EvidenceId>,
    pub rule_id: Option<String>,
    pub rationale: String,
}

// ═══ ResponsibilityTraceback ═══

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ResponsibilityTraceback {
    pub traceback_id: TracebackId,
    pub claim_id: ClaimId,
    pub chain_node_ids: Vec<NodeId>,
    pub chain_edge_ids: Vec<EdgeId>,
    pub root_cause_node_id: NodeId,
    pub explanation_summary: String,
    pub confidence: f32,
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Guard the wire name of `deberta_score`.
    ///
    /// Migrated from the never-compiled `types/alert_types_serde_test.rs`,
    /// which held the only assertion on this key. The frontend reads it
    /// (`frontend/lib/trace-workbench.ts`), so a rename or a dropped field
    /// would silently degrade that surface, and nothing else covers it.
    #[test]
    fn deberta_score_keeps_its_wire_name() {
        let es = EngineSection {
            rule_verdicts: vec![],
            pattern_verdicts: vec![],
            classification_score: 0.5,
            classification_route: "direct_safe".into(),
            score_breakdown: Default::default(),
            cusum_triggered: false,
            cusum_dimensions: vec![],
            progressive_fused: 0.0,
            observer_trigger_reason: String::new(),
            deberta_score: 0.85,
        };
        let json = serde_json::to_string(&es).expect("EngineSection must serialize");
        assert!(json.contains("deberta_score"), "wire name changed: {json}");
        assert!(json.contains("0.85"), "value missing: {json}");
    }

    /// Persisted/HTTP wire contract: these enums are written with
    /// `format!("{:?}")` and read by the frontend as PascalCase strings; the
    /// canonical types must not gain a `rename_all`.
    #[test]
    fn alert_authority_and_display_profile_stay_pascal_case() {
        assert_eq!(
            serde_json::to_string(&AlertAuthority::Authoritative).unwrap(),
            "\"Authoritative\""
        );
        assert_eq!(
            serde_json::to_string(&DisplayProfile::UnsafeByEngine).unwrap(),
            "\"UnsafeByEngine\""
        );
    }

    /// `engine_section_json` has always carried `llm_comment: null` when the
    /// observer did not comment; `skip_serializing_if` would silently change
    /// the persisted shape.
    #[test]
    fn rule_verdict_keeps_null_llm_comment() {
        let verdict = RuleVerdict {
            rule_id: "r".into(),
            description: "d".into(),
            matched_resource: "m".into(),
            llm_agreed: None,
            llm_comment: None,
        };
        let json = serde_json::to_string(&verdict).unwrap();
        assert!(json.contains("\"llm_comment\":null"), "{json}");
    }
}
