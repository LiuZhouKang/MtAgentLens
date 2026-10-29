// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 MtAgentLens Project

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

/// Message-level identity for a process that carried more than one conversation.
///
/// A shared process cannot be one logical agent when two sessions ran through
/// it: actions stay unattributed (fail-closed), while messages and tool calls
/// that carry a conversation id are attributed to the session's own agent.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ConversationIdentity {
    pub agent_id: AgentId,
    pub process_id: ProcessId,
    pub conversation_id: String,
    /// Messages whose own captured request declared this conversation and
    /// which were observed for this process. Empty for a candidate without
    /// message support; it is never inferred from the process or session.
    #[serde(default)]
    pub message_ids: Vec<MessageId>,
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

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum SubagentBoundaryReason {
    IndependentTls,
    ClaudeChildOfClaude,
    InfrastructureSpawn,
    /// A process-spawn delegation record explicitly separates the child from
    /// its parent (A delegated work to B), even when the child has no TLS of
    /// its own.
    DelegationRecord,
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
    /// One process carried more than one live conversation, so the resolver
    /// cannot decide which logical agent its events belong to. Assigning both
    /// sessions to one agent would be a false merge, so the component stays
    /// unassigned (E5 topology T2/T3).
    AmbiguousMultipleSessions,
}
