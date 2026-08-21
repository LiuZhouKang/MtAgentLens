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

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum DisplayProfile {
    SafeBaseline,
    UnsafeByEngine,
    ObserverJudged,
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

#[derive(Debug, Clone, Serialize, Deserialize)]
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
