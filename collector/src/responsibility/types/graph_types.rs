// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 MtAgentLens Project

use super::ids::*;
use super::phase::PhaseIndicator;
use super::resource::NormalizedResource;
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq, Hash)]
pub enum ResponsibilityNodeType {
    Agent,
    Resource,
    PolicyBoundary,
    Process,
    ToolCall,
    Credential,
    Context,
}

/// The observable kind of a node's resource.
///
/// The graph stores almost everything as a `Resource` node and puts the real
/// distinction in `node.resource`. Templates that only compare node types
/// cannot tell a file write from a network send; this is the discriminator
/// they need.
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq, Hash)]
#[serde(rename_all = "snake_case")]
pub enum ResourceKind {
    File,
    Network,
    Process,
    Conversation,
    ToolCall,
    Credential,
    Context,
}

impl ResourceKind {
    pub fn matches(self, resource: &NormalizedResource) -> bool {
        matches!(
            (self, resource),
            (Self::File, NormalizedResource::File { .. })
                | (Self::Network, NormalizedResource::Network { .. })
                | (Self::Process, NormalizedResource::Process { .. })
                | (
                    Self::Conversation,
                    NormalizedResource::LlmConversation { .. }
                )
                | (Self::ToolCall, NormalizedResource::ToolCall { .. })
                | (Self::Credential, NormalizedResource::Credential { .. })
                | (Self::Context, NormalizedResource::Context { .. })
        )
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ResponsibilityNode {
    pub node_id: NodeId,
    pub node_type: ResponsibilityNodeType,
    pub agent_id: Option<AgentId>,
    pub resource: Option<NormalizedResource>,
    pub evidence_refs: Vec<EvidenceId>,
    pub timestamp_ns: u64,
    pub phase: PhaseIndicator,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq, Hash)]
pub enum ResponsibilityEdgeType {
    IntentCausesAction,
    AgentDelegatesTo,
    ContextFlowsFrom,
    TaintPropagatesTo,
    PolicyBoundaryCrossing,
    CompetingExplanation,
    Delegation,
    AuthorityTransfer,
    CapabilityUse,
    ContextInheritance,
    TaintPropagation,
    ViolationProvenance,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ResponsibilityEdge {
    pub edge_id: EdgeId,
    pub edge_type: ResponsibilityEdgeType,
    pub source: NodeId,
    pub target: NodeId,
    pub evidence_refs: Vec<EvidenceId>,
    pub confidence: f32,
    pub phase: PhaseIndicator,
}

// ═══ Graph snapshot and metrics ═══

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(default)]
pub struct GraphSnapshot {
    pub snapshot_id: String,
    pub timestamp_ns: u64,
    pub nodes: Vec<ResponsibilityNode>,
    pub edges: Vec<ResponsibilityEdge>,
    pub metrics: GraphMetrics,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct GraphMetrics {
    pub node_counts: std::collections::HashMap<String, usize>,
    pub edge_counts: std::collections::HashMap<String, usize>,
    pub agent_count: usize,
    pub cross_agent_edges: usize,
    pub delegation_depth: usize,
    pub taint_chains: usize,
    pub avg_confidence: f32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GraphDiff {
    pub added_nodes: Vec<NodeId>,
    pub removed_nodes: Vec<NodeId>,
    pub added_edges: Vec<EdgeId>,
    pub removed_edges: Vec<EdgeId>,
    pub node_count_change: i32,
    pub edge_count_change: i32,
}
