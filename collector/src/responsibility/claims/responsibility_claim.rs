use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq, Hash)]
#[serde(rename_all = "snake_case")]
pub enum ResponsibilityClaimType {
    PolicyViolation,
    AccountabilityAnomaly,
    SuspiciousDelegation,
    TaintedContextUse,
    UnauthorizedCapabilityUse,
    BlockedAttempt,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ResponsibilityClaimNewState {
    Observed,
    Inferred,
    Ambiguous,
    Unproven,
    BlockedAttempt,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ClaimSeverity {
    Info,
    Warning,
    Critical,
}

impl ClaimSeverity {
    pub fn as_str(&self) -> &'static str {
        match self {
            ClaimSeverity::Info => "info",
            ClaimSeverity::Warning => "warning",
            ClaimSeverity::Critical => "critical",
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct CompetingExplanation {
    pub label: String,
    pub evidence_refs: Vec<String>,
    pub rule_id: String,
    pub rationale: String,
    pub aligned_pattern: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ResponsibilityClaim {
    pub claim_id: String,
    pub claim_type: crate::responsibility::claims::ResponsibilityClaimType,
    pub subject: Option<String>,
    pub policy_id: Option<String>,
    pub boundary_id: Option<String>,
    pub judgment_id: String,
    pub responsibility_chain: Vec<String>,
    pub evidence_refs: Vec<String>,
    pub claim_state: ResponsibilityClaimNewState,
    pub severity: ClaimSeverity,
    pub confidence: f32,
    pub competing_explanations: Vec<CompetingExplanation>,
    pub traceback_ref: Option<String>,
    /// All agent IDs involved in this claim (for multi-agent claims like
    /// SuspiciousDelegation and TaintedContextUse). The primary subject
    /// is still stored in `subject`.
    pub involved_agent_ids: Vec<String>,
}

impl ResponsibilityClaim {
    pub fn qualifies_for_strong_alert(&self) -> bool {
        matches!(
            self.claim_state,
            ResponsibilityClaimNewState::Observed | ResponsibilityClaimNewState::Inferred
        )
    }
}
