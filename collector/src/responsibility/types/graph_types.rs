use super::alert_types::AlertSeverity;
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
    pub signal: Option<super::signal_types::DetectedSignal>,
}

// ═══ Graph snapshot and metrics ═══

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
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

// ═══ Pattern matching ═══

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GraphPatternTemplate {
    pub template_id: String,
    pub description: String,
    pub severity: AlertSeverity,
    pub required_node_types: Vec<ResponsibilityNodeType>,
    pub required_edge_types: Vec<ResponsibilityEdgeType>,
    pub min_matches: usize,
    pub min_distinct_agents: usize,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GraphPatternMatch {
    pub template_id: String,
    pub confidence: f32,
    pub severity: AlertSeverity,
    pub description: String,
    pub matched_node_ids: Vec<NodeId>,
    pub matched_edge_ids: Vec<EdgeId>,
}
