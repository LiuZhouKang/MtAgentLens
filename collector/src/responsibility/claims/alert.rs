// SPDX-License-Identifier: MIT

use crate::responsibility::claims::ClaimSeverity;
use crate::responsibility::classification::types::{
    AlertAuthority, DisplayProfile, ObserverDecision, ObserverOutput, PatternVerdict, RuleVerdict,
    ScoreBreakdown,
};
use serde::{Deserialize, Serialize};

// ── Redesigned Alert Data Types ──────────────────────────────────────

/// Lightweight agent identity reference.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AgentRef {
    pub agent_id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub claimed_role: Option<String>,
}

/// What the rule engine and pattern matcher produced before Observer review.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EngineSection {
    pub rule_verdicts: Vec<RuleVerdict>,
    pub pattern_verdicts: Vec<PatternVerdict>,
    pub classification_score: f32,
    pub classification_route: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub score_breakdown: Option<ScoreBreakdown>,
}

/// Single entry in the behavior timeline.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BehaviorTimelineEntry {
    pub timestamp_ns: u64,
    pub event_type: String,
    pub description: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub resource: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub evidence_ref: Option<String>,
}

/// Ordered sequence of observed actions with duration.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BehaviorTimeline {
    pub steps: Vec<BehaviorTimelineEntry>,
    pub total_duration_ms: u64,
}

/// Snapshot of the causal graph at the moment the alert was raised.
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct CausalGraphSnapshot {
    pub node_count: usize,
    pub edge_count: usize,
    pub boundary_crossings: usize,
    pub agent_count: usize,
    pub delegation_depth: usize,
    pub temporal_span_ms: u64,
}

/// The authoritative alert produced after engine + Observer review.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AccountabilityAlert {
    pub alert_id: String,
    pub timestamp_ns: u64,

    // ── Display control ──
    pub display_profile: DisplayProfile,

    // ── Judgment (top-level, always present) ──
    pub decision: ObserverDecision,
    pub severity: ClaimSeverity,
    pub confidence: f32,
    pub authority: AlertAuthority,
    pub reasoning: String,

    // ── Human-readable ──
    pub title: String,
    pub summary: String,
    pub action_hint: String,

    // ── Subjects ──
    pub primary_agent: AgentRef,
    pub involved_agents: Vec<AgentRef>,

    // ── Engine analysis ──
    pub engine: EngineSection,

    // ── Behavior trace ──
    pub timeline: BehaviorTimeline,

    // ── Causal graph ──
    pub causal_graph: CausalGraphSnapshot,

    // ── LLM output (only when Observer LLM invoked) ──
    #[serde(skip_serializing_if = "Option::is_none")]
    pub observer_output: Option<ObserverOutput>,
}
