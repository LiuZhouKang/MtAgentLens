// SPDX-License-Identifier: MIT
//
// observer/types.rs — CANONICAL Observer type system (§16).
//
// This is the SINGLE SOURCE OF TRUTH for all observer-related types.
// classification/types.rs re-exports from here for backward compatibility.
// Do NOT duplicate observer types in other modules.

use serde::{Deserialize, Serialize};
use crate::responsibility::claims::ClaimSeverity;
use crate::responsibility::types::alert_types::AlertSeverity;
pub use crate::responsibility::types::refinement::ScoreBreakdown;

// ── Observer Decision ───────────────────────────────────────────────────

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ObserverDecision {
    Safe,
    Unsafe,
    Uncertain,
}

impl ObserverDecision {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Safe => "safe",
            Self::Unsafe => "unsafe",
            Self::Uncertain => "uncertain",
        }
    }
}

// ── Cache TTL ───────────────────────────────────────────────────────────

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CacheTTL {
    Once,
    Task,
    All,
}

// ── Alert Authority ─────────────────────────────────────────────────────

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
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

// ── Observer Error ──────────────────────────────────────────────────────

#[derive(Debug)]
pub enum ObserverError {
    HttpError(String),
    Timeout,
    InvalidResponse(String),
    RateLimited,
    InjectionDetected,
    SelfReportedAttack,
    // ── Value-domain validation errors ──
    InvalidDecision(String),
    InvalidSeverity(String),
    InvalidConfidence(f32),
    InvalidCacheTTL(String),
    ReasoningTooLong(usize),
}

// ── Classification Result ───────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Default)]
pub struct ClassificationResult {
    pub score: f32,
    pub route: String,
    pub score_breakdown: ScoreBreakdown,
    /// CUSUM progressive scoring — persisted for ablation.
    #[serde(default)]
    pub cusum_triggered: bool,
    #[serde(default)]
    pub cusum_dimensions: Vec<String>,
    #[serde(default)]
    pub progressive_fused: f64,
    #[serde(default)]
    pub observer_trigger_reason: String,
    /// DeBERTa ONNX prompt injection score (D2Content dimension EWMA).
    /// Persisted for ablation measurement of AI model (Layer 3) contribution.
    /// 0.0 when DeBERTa model is not loaded or no content was analyzed.
    #[serde(default)]
    pub deberta_score: f64,
}

// ── Route ────────────────────────────────────────────────────────────────

pub enum Route {
    DirectSafe(ObserverOutput),
    DirectUnsafe(ObserverOutput),
    ToObserver(ObserverSignature),
}

// ── Classification Config ────────────────────────────────────────────────

#[derive(Debug, Clone)]
pub struct ClassificationConfig {
    pub threshold_unsafe: f32,
    pub threshold_safe: f32,
}

impl Default for ClassificationConfig {
    fn default() -> Self {
        Self {
            threshold_unsafe: 0.70,
            threshold_safe: 0.15,
        }
    }
}

// ── Observer Signature Sub-types ────────────────────────────────────────

#[derive(Debug, Clone, Serialize)]
pub struct EvidenceSignature {
    pub total: usize,
    pub direct: usize,
    pub inferred: usize,
    pub avg_confidence: f32,
    pub max_confidence: f32,
    pub match_levels: Vec<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct GraphSignature {
    pub node_count: usize,
    pub edge_count: usize,
    pub edge_types: Vec<String>,
    pub node_types: Vec<String>,
    pub boundary_crossings: usize,
    pub delegation_depth: usize,
    pub cross_agent_edges: usize,
    pub agent_count: usize,
    pub temporal_span_ms: u64,
}

#[derive(Debug, Clone, Serialize)]
pub struct PatternSignal {
    pub template_id: String,
    pub match_confidence: f32,
    pub severity: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct RoleSignal {
    pub claimed_role: Option<String>,
    pub role_mismatches: Vec<String>,
    pub subject: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct ResourceSignal {
    pub resource_type: String,
    pub path: String,
    pub operation: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct CorrelationSignal {
    pub has_delegation_chain: bool,
    pub has_tainted_context: bool,
    pub has_credential_pattern: bool,
    pub has_concurrent_access: bool,
}

#[derive(Debug, Clone, Serialize)]
pub struct RuleHitSignal {
    pub rule_id: String,
    pub matched_resource: String,
}

#[derive(Debug, Clone, Serialize, Default)]
pub struct ContentSignals {
    pub resource_paths_touched: Vec<String>,
    pub credential_patterns_detected: usize,
    pub unexpected_file_sources: Vec<String>,
    pub handoff_depth: usize,
    pub constraint_count_diff: i32,
}

// ── Observer Signature ──────────────────────────────────────────────────

/// Structured summary of the graph/evidence/classification state
/// passed to the LLM for arbitration.
#[derive(Debug, Clone, Serialize)]
pub struct ObserverSignature {
    pub classification: ClassificationResult,
    pub rule_hits: Vec<RuleHitSignal>,
    pub evidence: EvidenceSignature,
    pub graph: GraphSignature,
    pub patterns: Vec<PatternSignal>,
    pub roles: RoleSignal,
    pub resources_touched: Vec<ResourceSignal>,
    pub correlation_evidence: CorrelationSignal,
}

// ── Cache Prior ─────────────────────────────────────────────────────────

/// Historical anomaly context carried into the observer prompt.
#[derive(Debug, Clone, Serialize)]
pub struct CachePrior {
    pub l2_hit: bool,
    pub l2_prior_anomaly_rate: Option<f32>,
    pub l3_hit: bool,
    pub l3_fleet_anomaly_rate: Option<f32>,
}

// ── Observer Input ──────────────────────────────────────────────────────

/// Complete input to the Observer LLM for arbitration.
#[derive(Debug, Clone, Serialize)]
pub struct ObserverInput {
    pub signature: ObserverSignature,
    pub content_signals: Option<ContentSignals>,
    pub prior: CachePrior,
}

// ── Rule / Pattern Verdicts ─────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RuleVerdict {
    pub rule_id: String,
    pub description: String,
    pub matched_resource: String,
    pub llm_agreed: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub llm_comment: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PatternVerdict {
    pub template_id: String,
    pub match_confidence: f32,
    pub llm_agreed: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub llm_comment: Option<String>,
}

// ── Observer Output ─────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ObserverOutput {
    pub decision: ObserverDecision,
    pub severity: ClaimSeverity,
    pub confidence: f32,
    pub cache_ttl: CacheTTL,
    pub authority: AlertAuthority,
    pub reasoning: String,
    pub attacked_flag: bool,
    pub rule_verdicts: Vec<RuleVerdict>,
    pub pattern_verdicts: Vec<PatternVerdict>,
}

// ── Display Profile ─────────────────────────────────────────────────────

/// Controls which layers the frontend renders for an alert.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
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

// ── Cache Key ───────────────────────────────────────────────────────────

/// L1 cache key: u32 hash of edge_types + node_types + subject + resource_path.
#[derive(Debug, Clone, Copy, Hash, PartialEq, Eq)]
pub struct CacheKey(pub u32);

impl CacheKey {
    pub fn compute(
        edge_types: &[String],
        node_types: &[String],
        subject: &str,
        resource_path: &str,
    ) -> Self {
        use std::hash::{Hash, Hasher};
        let mut hasher = std::collections::hash_map::DefaultHasher::new();
        edge_types.hash(&mut hasher);
        node_types.hash(&mut hasher);
        subject.hash(&mut hasher);
        resource_path.hash(&mut hasher);
        Self(hasher.finish() as u32)
    }
}
