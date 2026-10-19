// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 MtAgentLens Project

//! Core data types for Causal Trace (Subproject 1)
//!
//! This module defines the fundamental data structures for tracking
//! agent instances, process identities, and action events with pid reuse prevention.

use serde::{Deserialize, Serialize};
use std::collections::HashSet;

// Re-exports from new types/ module (authoritative definitions).
pub use crate::responsibility::types::evidence_types::IntentAuthoritySource;
pub use crate::responsibility::types::evidence_types::IntentClass;
pub use crate::responsibility::types::evidence_types::IntentParseState;
pub use crate::responsibility::types::ids::ProcessId;

pub use crate::responsibility::types::ids::AgentId;
pub use crate::responsibility::types::ids::AlertId;
pub use crate::responsibility::types::ids::BoundaryId;
pub use crate::responsibility::types::ids::EdgeId;
pub use crate::responsibility::types::ids::EventId;
pub use crate::responsibility::types::ids::EvidenceId;
pub use crate::responsibility::types::ids::NodeId;
pub use crate::responsibility::types::ids::PolicyId;
pub use crate::responsibility::types::ids::TracebackId;

pub use crate::responsibility::types::policy_types::PolicyEvaluationResult;
pub use crate::responsibility::types::policy_types::ResponsibilityClaimState;

// Backward-compatible aliases for bin/replay.rs and bin/covert-channel.rs
pub type ClaimState = crate::responsibility::types::policy_types::ResponsibilityClaimState;
pub type SnapshotData = AccountabilitySnapshot;
pub type TracebackData = AccountabilityTraceback;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub enum ResourceId {
    File {
        canonical_path: String,
    },
    Network {
        host: String,
        port: u16,
        scheme: Option<String>,
    },
    Process {
        process_id: ProcessId,
    },
    LlmConversation {
        provider: String,
        model: String,
        conversation_id: String,
    },
    ToolCall {
        tool_name: String,
        call_id: Option<String>,
    },
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub enum EvidenceDirectness {
    Direct,
    Inferred,
}

impl std::fmt::Display for EvidenceDirectness {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let value = match self {
            Self::Direct => "direct",
            Self::Inferred => "inferred",
        };
        write!(f, "{}", value)
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct EvidenceRecord {
    pub evidence_id: EvidenceId,
    pub source_event_id: EventId,
    pub directness: EvidenceDirectness,
    pub rule_or_inference_id: String,
    pub resource_id: Option<ResourceId>,
    pub boundary_id: Option<BoundaryId>,
    pub confidence: f32,
    pub redaction_status: String,
    pub payload_ref: Option<String>,
}

pub use crate::responsibility::types::policy_types::EvidenceRef;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum CausalNodeType {
    FileRead,
    FileWrite,
    ProcessExec,
    NetworkConnect,
    ToolCall,
    Unknown,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct CausalNode {
    pub node_id: NodeId,
    pub node_type: CausalNodeType,
    pub timestamp_ns: u64,
    pub agent_id: AgentId,
    pub process_id: ProcessId,
    pub operation: String,
    pub target: Option<String>,
    pub resource_id: Option<ResourceId>,
    pub event_id: EventId,
    pub metadata: serde_json::Value,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum CausalityType {
    Direct,
    Indirect,
    Delegation,
    AuthorityTransfer,
    ContextInheritance,
    ContaminationPropagation,
    CapabilityUse,
    PolicyViolationProvenance,
    Uncertainty,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct CausalEdge {
    pub edge_id: EdgeId,
    pub from_node_id: NodeId,
    pub to_node_id: NodeId,
    pub causality_type: CausalityType,
    pub confidence: f32,
    pub evidence: Vec<EvidenceRef>,
}

/// An edge must carry non-temporal evidence: temporal proximity alone is not
/// a causal claim. This predicate lives on the type it describes (it used to
/// sit in a legacy correlator module that no production code called).
pub fn edge_has_non_temporal_support(edge: &CausalEdge) -> anyhow::Result<()> {
    if edge.evidence.is_empty() {
        anyhow::bail!("responsibility edge missing evidence");
    }
    Ok(())
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct AccountabilityTraceback {
    pub traceback_id: TracebackId,
    pub alert_id: AlertId,
    pub chain_node_ids: Vec<NodeId>,
    pub root_cause_node_id: NodeId,
    pub explanation_summary: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct AccountabilityTracebackReplay {
    pub traceback_id: TracebackId,
    pub alert_id: AlertId,
    pub chain_node_ids: Vec<NodeId>,
    pub root_cause_node_id: NodeId,
    pub supporting_evidence_ids: Vec<EvidenceId>,
    pub explanation_summary: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct AccountabilityAuditSummary {
    pub run_id: String,
    pub alert_count: usize,
    pub traceback_count: usize,
    pub summary_text: String,
}

/// Record of a detected handoff injection — a shared artifact with an
/// injected priority addendum that a downstream subagent accepted and executed.
///
/// Captures the full injection chain: which shared file was injected, which
/// probe confirmed propagation, which acceptance marker was observed, and
/// which agents were affected.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct HandoffInjectionRecord {
    /// Stable identifier for this injection event.
    pub record_id: String,

    /// Scenario identifier (e.g. "subagents-handoff-injection").
    pub scenario_id: String,

    /// Path suffix of the shared handoff artifact that was injected.
    pub shared_artifact_suffix: String,

    /// Path suffix of the probe file confirming injection propagation.
    pub probe_suffix: String,

    /// The acceptance marker text detected in the subagent's output.
    pub acceptance_marker: String,

    /// Root PID of the agent that accepted the injected content.
    pub consumer_agent_root_pid: u32,

    /// Root PIDs of all agents that read the shared artifact (propagation chain).
    pub propagation_chain_pids: Vec<u32>,

    /// Agent count that read the shared file (derived from propagation_chain).
    pub agent_count: u32,

    /// Source field where the acceptance marker was found (e.g. "http_body", "sse_text").
    pub evidence_source: String,

    /// Timestamp of detection in nanoseconds.
    pub timestamp_ns: u64,

    /// Excerpt of the evidence content (truncated).
    pub evidence_excerpt: String,
}

impl HandoffInjectionRecord {
    pub fn new(
        record_id: String,
        scenario_id: String,
        shared_artifact_suffix: String,
        probe_suffix: String,
        acceptance_marker: String,
        consumer_agent_root_pid: u32,
        propagation_chain_pids: Vec<u32>,
        evidence_source: String,
        timestamp_ns: u64,
        evidence_excerpt: String,
    ) -> Self {
        let agent_count = propagation_chain_pids.len() as u32;
        Self {
            record_id,
            scenario_id,
            shared_artifact_suffix,
            probe_suffix,
            acceptance_marker,
            consumer_agent_root_pid,
            propagation_chain_pids,
            agent_count,
            evidence_source,
            timestamp_ns,
            evidence_excerpt,
        }
    }

    /// Whether this represents team-scale propagation (3+ agents affected).
    pub fn is_team_scale(&self) -> bool {
        self.agent_count >= 3
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ViolationPatternMatchRecord {
    pub match_id: String,
    pub template_id: String,
    pub agent_id: String,
    pub matched_link_ids: Vec<String>,
    pub matched_policies: Vec<String>,
    pub matched_boundaries: Vec<String>,
    pub matched_targets: Vec<String>,
    pub start_ns: u64,
    pub end_ns: u64,
    pub severity: String,
    pub description: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct AccountabilitySnapshot {
    pub snapshot_id: String,
    pub timestamp_ns: u64,
    pub agent_id: String,
    pub link_ids: Vec<String>,
    pub evidence_ids: Vec<String>,
    pub policy_ids: Vec<String>,
    pub boundary_ids: Vec<String>,
    pub claim_states: Vec<String>,
    pub traceback_ids: Vec<String>,
    pub violation_match_ids: Vec<String>,
    pub node_count: usize,
    pub edge_count: usize,
    pub snapshot_kind: String,
}

/// Agent instance representing a single agent or agent team member
///
/// An AgentInstance is identified by:
/// - root_processes: Set of ProcessId that belong to this agent
/// - conversation_id: Optional LLM conversation identifier
/// - tls_sessions: Set of TLS session IDs for network tracking
///
/// The union-find algorithm will merge AgentInstances based on:
/// 1. Process lineage (parent-child relationships)
/// 2. TLS session sharing (network connections)
/// 3. Conversation ID matching (LLM API calls)
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AgentInstance {
    /// Unique identifier for this agent instance
    pub agent_id: String,

    /// Root processes belonging to this agent (using ProcessId to prevent pid reuse)
    pub root_processes: HashSet<ProcessId>,

    /// Optional conversation ID from LLM API calls
    pub conversation_id: Option<String>,

    /// TLS session IDs associated with this agent
    pub tls_sessions: HashSet<String>,

    /// Timestamp when this agent instance was first observed (nanoseconds)
    pub first_seen_ns: u64,

    /// Timestamp when this agent instance was last active (nanoseconds)
    pub last_seen_ns: u64,
}

impl AgentInstance {
    pub fn new(agent_id: String, root_process: ProcessId, timestamp_ns: u64) -> Self {
        let mut root_processes = HashSet::new();
        root_processes.insert(root_process);

        Self {
            agent_id,
            root_processes,
            conversation_id: None,
            tls_sessions: HashSet::new(),
            first_seen_ns: timestamp_ns,
            last_seen_ns: timestamp_ns,
        }
    }

    /// Update last_seen timestamp
    pub fn touch(&mut self, timestamp_ns: u64) {
        if timestamp_ns > self.last_seen_ns {
            self.last_seen_ns = timestamp_ns;
        }
    }

    /// Add a process to this agent instance
    pub fn add_process(&mut self, process_id: ProcessId) {
        self.root_processes.insert(process_id);
    }

    /// Add a TLS session to this agent instance
    pub fn add_tls_session(&mut self, session_id: String) {
        self.tls_sessions.insert(session_id);
    }

    /// Set the conversation ID for this agent instance
    pub fn set_conversation_id(&mut self, conversation_id: String) {
        self.conversation_id = Some(conversation_id);
    }
}

/// Action event representing a system call or operation performed by an agent
///
/// ActionEvent captures the "action" side of the intent→action causal edge.
/// It includes the ProcessId (with start_time_ns) to prevent misattribution
/// due to pid reuse.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ActionEvent {
    /// Unique identifier for this action
    pub action_id: String,

    /// Stable typed event identifier for downstream accountability contracts.
    pub event_id: EventId,

    /// Process that performed this action (with start_time to prevent pid reuse)
    pub process_id: ProcessId,

    /// Parent process ID (with start_time)
    pub parent_process_id: Option<ProcessId>,

    /// Agent instance ID (assigned by union-find)
    pub agent_id: Option<String>,

    /// Confidence level of the agent assignment (Phase 2).
    /// None during Phase 1 (streaming), "authoritative" or "inferred" after Phase 2.
    pub agent_confidence: Option<String>,

    /// Timestamp when this action occurred (nanoseconds)
    pub timestamp_ns: u64,

    /// System call name (e.g., "openat", "connect", "execve")
    pub syscall: String,

    /// System call arguments (JSON-encoded for flexibility)
    pub args: serde_json::Value,

    /// Return value or result of the syscall
    pub result: Option<i64>,

    /// Process command name
    pub comm: String,
}

impl ActionEvent {
    pub fn new(
        action_id: String,
        process_id: ProcessId,
        parent_process_id: Option<ProcessId>,
        timestamp_ns: u64,
        syscall: String,
        args: serde_json::Value,
        comm: String,
    ) -> Self {
        Self {
            event_id: EventId(action_id.clone()),
            action_id,
            process_id,
            parent_process_id,
            agent_id: None,
            agent_confidence: None,
            timestamp_ns,
            syscall,
            args,
            result: None,
            comm,
        }
    }

    /// Assign this action to an agent instance
    pub fn assign_agent(&mut self, agent_id: String) {
        self.agent_id = Some(agent_id);
    }
}

/// Intent event representing an LLM tool call or decision
///
/// IntentEvent captures the "intent" side of the intent→action causal edge.
/// It is extracted from LLM API responses (tool_use blocks in Claude API,
/// function_call in OpenAI API, etc.)
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct IntentEvent {
    /// Unique identifier for this intent
    pub intent_id: String,

    /// Stable typed event identifier for downstream accountability contracts.
    pub event_id: EventId,

    /// Agent instance ID that generated this intent
    pub agent_id: String,

    /// Confidence level of the agent assignment (Phase 2).
    /// None during Phase 1 (streaming), "authoritative" or "inferred" after Phase 2.
    pub agent_confidence: Option<String>,

    /// Process identity for dynamic agent resolution at correlator match-time.
    pub process_id: ProcessId,

    /// Timestamp when this intent was generated (nanoseconds)
    pub timestamp_ns: u64,

    /// Tool name (e.g., "Read", "Write", "Bash")
    pub tool_name: String,

    /// Tool arguments (JSON-encoded)
    pub tool_args: serde_json::Value,

    /// Intent taxonomy class for downstream use.
    pub intent_class: IntentClass,

    /// Parse quality state for this extracted intent.
    pub parse_state: IntentParseState,

    /// Whether this intent is eligible for primary side-effect matching.
    pub primary_matching_eligible: bool,

    /// Conversation ID from LLM API
    pub conversation_id: Option<String>,

    /// Message ID from the structured transport payload when available.
    pub message_id: Option<String>,

    /// Source event reference describing where this intent was extracted from.
    pub source_event_ref: Option<String>,

    /// Authority source for this extracted intent.
    pub authority_source: IntentAuthoritySource,
}

impl IntentEvent {
    pub fn new(
        intent_id: String,
        agent_id: String,
        timestamp_ns: u64,
        tool_name: String,
        tool_args: serde_json::Value,
    ) -> Self {
        Self {
            event_id: EventId(intent_id.clone()),
            intent_id,
            agent_id,
            agent_confidence: None,
            timestamp_ns,
            tool_name: tool_name.clone(),
            tool_args,
            intent_class: classify_intent_class(&tool_name),
            parse_state: IntentParseState::Complete,
            primary_matching_eligible: false,
            process_id: ProcessId::new(0, 0),
            conversation_id: None,
            message_id: None,
            source_event_ref: None,
            authority_source: IntentAuthoritySource::HttpResponseToolUse,
        }
        .refresh_matching_eligibility()
    }

    pub fn with_parse_state(mut self, parse_state: IntentParseState) -> Self {
        self.parse_state = parse_state;
        self.refresh_matching_eligibility()
    }

    pub fn with_intent_class(mut self, intent_class: IntentClass) -> Self {
        self.intent_class = intent_class;
        self.refresh_matching_eligibility()
    }

    pub fn with_authority_source(mut self, authority_source: IntentAuthoritySource) -> Self {
        self.authority_source = authority_source;
        self
    }

    fn refresh_matching_eligibility(mut self) -> Self {
        self.primary_matching_eligible = is_primary_matching_tool(&self.tool_name)
            && matches!(self.intent_class, IntentClass::SideEffect)
            && matches!(self.parse_state, IntentParseState::Complete);
        self
    }

    /// Set the conversation ID for this intent
    pub fn set_conversation_id(&mut self, conversation_id: String) {
        self.conversation_id = Some(conversation_id);
    }

    pub fn set_message_id(&mut self, message_id: String) {
        self.message_id = Some(message_id);
    }

    pub fn set_source_event_ref(&mut self, source_event_ref: String) {
        self.source_event_ref = Some(source_event_ref);
    }
}

/// Classify a tool name into an `IntentClass`.
///
/// Delegates to the single canonical table in
/// [`tool_vocabulary`](crate::responsibility::types::tool_vocabulary). This
/// function used to carry its own table, which disagreed with the live
/// extractor's on `Bash` (and several other names) — so the same tool had
/// different `primary_matching_eligible` depending on which path built it.
pub fn classify_intent_class(tool_name: &str) -> IntentClass {
    crate::responsibility::types::tool_vocabulary::classify_intent_class(tool_name)
}

pub fn is_primary_matching_tool(tool_name: &str) -> bool {
    !matches!(tool_name, "WebSearch" | "Glob" | "text_response")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_process_id_equality() {
        let p1 = ProcessId::new(1234, 1000000);
        let p2 = ProcessId::new(1234, 1000000);
        let p3 = ProcessId::new(1234, 2000000); // same pid, different start_time

        assert_eq!(p1, p2);
        assert_ne!(p1, p3);
    }

    #[test]
    fn test_process_id_hash() {
        use std::collections::HashSet;

        let mut set = HashSet::new();
        set.insert(ProcessId::new(1234, 1000000));
        set.insert(ProcessId::new(1234, 1000000)); // duplicate
        set.insert(ProcessId::new(1234, 2000000)); // different start_time

        assert_eq!(set.len(), 2); // only 2 unique ProcessIds
    }

    #[test]
    fn test_agent_instance_creation() {
        let process_id = ProcessId::new(1234, 1000000);
        let agent = AgentInstance::new("agent-1".to_string(), process_id, 5000000);

        assert_eq!(agent.agent_id, "agent-1");
        assert_eq!(agent.root_processes.len(), 1);
        assert!(agent.root_processes.contains(&process_id));
        assert_eq!(agent.first_seen_ns, 5000000);
        assert_eq!(agent.last_seen_ns, 5000000);
    }

    #[test]
    fn test_agent_instance_add_process() {
        let p1 = ProcessId::new(1234, 1000000);
        let p2 = ProcessId::new(5678, 2000000);

        let mut agent = AgentInstance::new("agent-1".to_string(), p1, 5000000);
        agent.add_process(p2);

        assert_eq!(agent.root_processes.len(), 2);
        assert!(agent.root_processes.contains(&p1));
        assert!(agent.root_processes.contains(&p2));
    }

    #[test]
    fn test_action_event_creation() {
        let process_id = ProcessId::new(1234, 1000000);
        let parent_id = ProcessId::new(1000, 500000);
        let args = serde_json::json!({"path": "/etc/shadow", "flags": "O_RDONLY"});

        let action = ActionEvent::new(
            "action-1".to_string(),
            process_id,
            Some(parent_id),
            6000000,
            "openat".to_string(),
            args.clone(),
            "bash".to_string(),
        );

        assert_eq!(action.action_id, "action-1");
        assert_eq!(action.process_id, process_id);
        assert_eq!(action.parent_process_id, Some(parent_id));
        assert_eq!(action.timestamp_ns, 6000000);
        assert_eq!(action.syscall, "openat");
        assert_eq!(action.args, args);
        assert_eq!(action.comm, "bash");
        assert!(action.agent_id.is_none());
    }

    #[test]
    fn test_intent_event_creation() {
        let args = serde_json::json!({"file_path": "/etc/shadow"});

        let intent = IntentEvent::new(
            "intent-1".to_string(),
            "agent-1".to_string(),
            5500000,
            "Read".to_string(),
            args.clone(),
        );

        assert_eq!(intent.intent_id, "intent-1");
        assert_eq!(intent.agent_id, "agent-1");
        assert_eq!(intent.timestamp_ns, 5500000);
        assert_eq!(intent.tool_name, "Read");
        assert_eq!(intent.tool_args, args);
        assert_eq!(intent.intent_class, IntentClass::SideEffect);
        assert_eq!(intent.parse_state, IntentParseState::Complete);
        assert!(intent.primary_matching_eligible);
        assert!(intent.conversation_id.is_none());
        assert!(intent.message_id.is_none());
        assert!(intent.source_event_ref.is_none());
        assert_eq!(
            intent.authority_source,
            IntentAuthoritySource::HttpResponseToolUse
        );
    }

    #[test]
    fn test_orchestration_intent_is_not_primary_matching_eligible() {
        let intent = IntentEvent::new(
            "intent-2".to_string(),
            "agent-1".to_string(),
            5500000,
            "TaskCreate".to_string(),
            serde_json::json!({"description": "delegate work"}),
        );

        assert_eq!(intent.intent_class, IntentClass::Orchestration);
        assert_eq!(intent.parse_state, IntentParseState::Complete);
        assert!(!intent.primary_matching_eligible);
    }

    #[test]
    fn test_incomplete_side_effect_intent_is_not_primary_matching_eligible() {
        let intent = IntentEvent::new(
            "intent-3".to_string(),
            "agent-1".to_string(),
            5500000,
            "Read".to_string(),
            serde_json::json!({}),
        )
        .with_parse_state(IntentParseState::Malformed);

        assert_eq!(intent.intent_class, IntentClass::SideEffect);
        assert_eq!(intent.parse_state, IntentParseState::Malformed);
        assert!(!intent.primary_matching_eligible);
    }

    #[test]
    fn test_unknown_tool_classification_is_not_side_effect() {
        let intent = IntentEvent::new(
            "intent-4".to_string(),
            "agent-1".to_string(),
            5500000,
            "CustomTool".to_string(),
            serde_json::json!({"foo": "bar"}),
        )
        .with_parse_state(IntentParseState::Unsupported);

        assert_eq!(intent.intent_class, IntentClass::Unknown);
        assert_eq!(intent.parse_state, IntentParseState::Unsupported);
        assert!(!intent.primary_matching_eligible);
    }

    #[test]
    fn test_with_authority_source_overrides_default() {
        let intent = IntentEvent::new(
            "intent-auth".to_string(),
            "agent-1".to_string(),
            5500000,
            "Bash".to_string(),
            serde_json::json!({"command": "ls"}),
        )
        .with_authority_source(IntentAuthoritySource::RequestHistoryAssistantToolUse);

        assert_eq!(
            intent.authority_source,
            IntentAuthoritySource::RequestHistoryAssistantToolUse
        );
    }

    #[test]
    fn test_websearch_complete_side_effect_is_not_primary_matching_eligible() {
        let intent = IntentEvent::new(
            "intent-5".to_string(),
            "agent-1".to_string(),
            5500000,
            "WebSearch".to_string(),
            serde_json::json!({"query": "latest mtagentlens docs"}),
        );

        assert_eq!(intent.intent_class, IntentClass::SideEffect);
        assert_eq!(intent.parse_state, IntentParseState::Complete);
        assert!(!intent.primary_matching_eligible);
    }

    #[test]
    fn test_glob_complete_side_effect_is_not_primary_matching_eligible() {
        let intent = IntentEvent::new(
            "intent-6".to_string(),
            "agent-1".to_string(),
            5500000,
            "Glob".to_string(),
            serde_json::json!({"pattern": "src/**/*.rs"}),
        );

        assert_eq!(intent.intent_class, IntentClass::SideEffect);
        assert_eq!(intent.parse_state, IntentParseState::Complete);
        assert!(!intent.primary_matching_eligible);
    }

    #[test]
    fn test_text_response_complete_side_effect_is_not_primary_matching_eligible() {
        let intent = IntentEvent::new(
            "intent-7".to_string(),
            "agent-1".to_string(),
            5500000,
            "text_response".to_string(),
            serde_json::json!({"extracted_args": [{"type": "path", "value": "/tmp/x"}]}),
        );

        assert_eq!(intent.intent_class, IntentClass::SideEffect);
        assert_eq!(intent.parse_state, IntentParseState::Complete);
        assert!(!intent.primary_matching_eligible);
    }
}
