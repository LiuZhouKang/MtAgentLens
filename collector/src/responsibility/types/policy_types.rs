use super::ids::*;
use serde::{Deserialize, Serialize};

// ═══ Accountability Claim State (shared by judgment + graph) ═══

/// Claim state used by the four-boundary policy evaluation engine.
/// Mirrors the states in PolicyJudgmentResult but specialized for the
/// accountability claim lifecycle.
// v8.1: Re-export from alert_types (single source of truth)
pub use super::alert_types::ResponsibilityClaimState;

/// Result of a single boundary policy evaluation.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct PolicyEvaluationResult {
    pub policy_id: Option<PolicyId>,
    pub boundary_id: Option<BoundaryId>,
    pub claim_state: ResponsibilityClaimState,
    pub matched: bool,
    pub reason: String,
}

/// Reference to a piece of evidence by ID.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq, Hash)]
pub struct EvidenceRef {
    pub evidence_id: EvidenceId,
}

// ═══ Policy Evaluation ═══

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PolicyEvaluation {
    pub policy_id: PolicyId,
    pub boundary: PolicyBoundary,
    pub judgment: PolicyJudgment,
    pub subject: AgentId,
    pub resource: Option<super::resource::NormalizedResource>,
    pub evidence: Vec<EvidenceId>,
    pub confidence: f32,
    pub reason: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub enum PolicyBoundary {
    Delegation,
    Capability,
    ContextTrust,
    Taint,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PolicyJudgment {
    pub judgment_id: String,
    pub policy_id: PolicyId,
    pub boundary_id: BoundaryId,
    pub subject: AgentId,
    pub graph_ref: String,
    pub evidence_refs: Vec<EvidenceId>,
    pub result: PolicyJudgmentResult,
    pub reason: String,
    pub confidence: f32,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum PolicyJudgmentResult {
    Allowed,
    Violation,
    Ambiguous,
    Unproven,
    BlockedAttempt,
}

// ═══ Policy Rule ═══

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PolicyRule {
    pub rule_id: String,
    pub priority: u32,
    pub boundary: PolicyBoundary,
    pub resource: ResourceSelector,
    pub action: ActionSelector,
    pub judgment: PolicyJudgmentResult,
    pub description: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum ResourceSelector {
    Any,
    Exact(String),
    Prefix(String),
    Glob(String),
    Host(String),
    Port(u16),
    PortRange(u16, u16),
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum ActionSelector {
    Any,
    Read,
    Write,
    Execute,
    Delete,
    Operation(OperationCategory),
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq, Hash)]
pub enum OperationCategory {
    FileRead,
    FileWrite,
    FileExec,
    NetworkConnect,
    NetworkSend,
    ProcessEnum,
    CredentialAccess,
    ResourceHog,
}

// ═══ Delegation ═══

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DelegationRecord {
    pub delegation_id: String,
    pub from_agent: AgentId,
    pub to_agent: AgentId,
    pub parent_process: ProcessId,
    pub child_process: ProcessId,
    pub timestamp_ns: u64,
    pub confidence: f32,
}

// ═══ Context Pollution ═══

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ContextPollution {
    pub source_agent: AgentId,
    pub contaminated_artifact: String,
    pub propagation_path: Vec<AgentId>,
    pub detected_at_ns: u64,
}
