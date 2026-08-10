use super::ids::*;
use serde::{Deserialize, Serialize};

// ═══ Phase 1: Provisional Identity ═══

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProvisionalIdentity {
    pub agent_id: AgentId,
    pub process_ids: Vec<ProcessId>,
    pub primary_signal: IdentitySignal,
    pub confidence: f32,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum IdentitySignal {
    TlsSessionShared,
    TlsSessionNew,
    ProcessTreeInherited,
    Unknown,
}

// ═══ Phase 2: Authoritative Identity ═══

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AuthoritativeIdentity {
    pub agent_id: AgentId,
    pub process_ids: Vec<ProcessId>,
    pub message_ids: Vec<MessageId>,
    pub tls_sessions: Vec<TlsSessionId>,
    pub confidence: AgentConfidence,
    pub provisional_refs: Vec<AgentId>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum AgentConfidence {
    Authoritative,
    Inferred,
}

// ═══ Subagent Boundary ═══

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SubagentBoundary {
    pub parent_agent: AgentId,
    pub child_agent: AgentId,
    pub parent_process: ProcessId,
    pub child_process: ProcessId,
    pub reason: SubagentBoundaryReason,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum SubagentBoundaryReason {
    IndependentTls,
    ClaudeChildOfClaude,
    InfrastructureSpawn,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum SubagentSuspicionReason {
    ClaudeChildOfClaude,
    HandoffFileRead,
}

// ═══ Diagnostics ═══

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct UnresolvedDiagnostic {
    pub process_id: ProcessId,
    pub reason: UnresolvedReason,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum UnresolvedReason {
    NoPositiveSignal,
    NoProcessLineage,
}
