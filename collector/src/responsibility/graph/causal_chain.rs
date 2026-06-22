// SPDX-License-Identifier: MIT
//
// causal_chain.rs — CausalChainTracer: backward traversal through the
// responsibility graph to identify originating agents and events.
//
// Follows IntentCausesAction, AgentDelegatesTo, ContextFlowsFrom, and
// TaintPropagatesTo edges to trace causal chains from any target node
// back to the root cause.

use crate::responsibility::graph::graph::ResponsibilityGraph;
use crate::responsibility::types::{EdgeId, NodeId, ResponsibilityEdgeType, AgentId};
use serde::{Deserialize, Serialize};
use std::collections::HashSet;

// ─── CausalChain ─────────────────────────────────────────────────────────

/// A traced causal chain through the responsibility graph.
/// Nodes are ordered from root → target.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CausalChain {
    pub root_node_id: NodeId,
    pub chain_nodes: Vec<NodeId>,
    pub chain_edges: Vec<EdgeId>,
    pub confidence: f32,
    pub description: String,
    pub involved_agents: Vec<AgentId>,
    pub depth: usize,
}

// ─── CausalChainTracer ───────────────────────────────────────────────────

/// Traces causal chains backward through the responsibility graph.
///
/// Starting from any node, follows incoming causal edges to find the
/// originating agent or event. Used by TracebackBuilder for claim-level
/// attribution and by frontend tooling for visual causal exploration.
pub struct CausalChainTracer {
    max_depth: usize,
}

impl CausalChainTracer {
    pub fn new() -> Self {
        Self { max_depth: 20 }
    }

    pub fn with_max_depth(max_depth: usize) -> Self {
        Self { max_depth }
    }

    /// Trace the causal chain backward from `target`.
    ///
    /// At each step, chooses the highest-confidence incoming causal edge
    /// (boosted for IntentCausesAction +0.20, AgentDelegatesTo +0.15).
    /// Stops on cycle, max depth, or no more causal edges.
    pub fn trace(
        &self,
        graph: &ResponsibilityGraph,
        target: NodeId,
    ) -> Option<CausalChain> {
        if !graph.has_node(&target) {
            return None;
        }

        let mut chain_nodes: Vec<NodeId> = Vec::new();
        let mut chain_edges: Vec<EdgeId> = Vec::new();
        let mut visited: HashSet<NodeId> = HashSet::new();
        let mut involved_agents: HashSet<AgentId> = HashSet::new();
        let mut current = target.clone();

        if let Some(node) = graph.get_node(&current) {
            if let Some(ref agent) = node.agent_id {
                involved_agents.insert(agent.clone());
            }
        }
        visited.insert(current.clone());
        chain_nodes.push(current.clone());

        for _ in 0..self.max_depth {
            let incoming = graph.incoming_to(&current);
            let best = incoming
                .iter()
                .filter_map(|eid| {
                    graph.get_edge(eid).map(|e| {
                        let boost = match e.edge_type {
                            ResponsibilityEdgeType::IntentCausesAction => 0.20,
                            ResponsibilityEdgeType::AgentDelegatesTo => 0.15,
                            ResponsibilityEdgeType::ContextFlowsFrom => 0.10,
                            ResponsibilityEdgeType::TaintPropagatesTo => 0.10,
                            _ => 0.0,
                        };
                        (eid.clone(), e.confidence + boost)
                    })
                })
                .max_by(|(_, a), (_, b)| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));

            match best {
                Some((eid, _)) => {
                    let edge = match graph.get_edge(&eid) {
                        Some(e) => e.clone(),
                        None => break,
                    };
                    let prev = edge.source.clone();
                    if visited.contains(&prev) { break; }
                    if let Some(node) = graph.get_node(&prev) {
                        if let Some(ref agent) = node.agent_id {
                            involved_agents.insert(agent.clone());
                        }
                    }
                    visited.insert(prev.clone());
                    chain_nodes.push(prev.clone());
                    chain_edges.push(eid);
                    current = prev;
                }
                None => break,
            }
        }

        chain_nodes.reverse();
        chain_edges.reverse();

        let root_node_id = chain_nodes.first().cloned().unwrap_or(target);
        let depth = chain_edges.len();
        let confidence = if chain_edges.is_empty() {
            1.0
        } else {
            chain_edges.iter()
                .filter_map(|eid| graph.get_edge(eid))
                .map(|e| e.confidence)
                .sum::<f32>() / chain_edges.len() as f32
        };

        let description = if depth == 0 {
            format!("Node {} is a root cause (no incoming causal edges).", root_node_id)
        } else {
            let mut parts: Vec<String> = Vec::new();
            for i in 0..chain_nodes.len() {
                parts.push(format_node(graph, &chain_nodes[i]));
                if i < chain_edges.len() {
                    if let Some(edge) = graph.get_edge(&chain_edges[i]) {
                        parts.push(format!(" --[{:?}]→ ", edge.edge_type));
                    }
                }
            }
            parts.concat()
        };

        Some(CausalChain {
            root_node_id, chain_nodes, chain_edges,
            confidence, description,
            involved_agents: involved_agents.into_iter().collect(),
            depth,
        })
    }

    /// Trace chains for all nodes belonging to `agent`.
    pub fn trace_agent(&self, graph: &ResponsibilityGraph, agent: &AgentId) -> Vec<CausalChain> {
        graph.nodes_for_agent(agent).iter()
            .filter_map(|nid| self.trace(graph, nid.clone()))
            .collect()
    }

    /// Find the deepest chain in the graph.
    pub fn deepest_chain(&self, graph: &ResponsibilityGraph) -> Option<CausalChain> {
        graph.all_nodes()
            .filter_map(|n| self.trace(graph, n.node_id.clone()))
            .max_by_key(|c| c.depth)
    }

    /// Find all chains with depth >= min_depth.
    pub fn deep_chains(&self, graph: &ResponsibilityGraph, min_depth: usize) -> Vec<CausalChain> {
        graph.all_nodes()
            .filter_map(|n| self.trace(graph, n.node_id.clone()))
            .filter(|c| c.depth >= min_depth)
            .collect()
    }
}

impl Default for CausalChainTracer {
    fn default() -> Self { Self::new() }
}

fn format_node(graph: &ResponsibilityGraph, node_id: &NodeId) -> String {
    let node = match graph.get_node(node_id) {
        Some(n) => n,
        None => return format!("<missing>[{}]", node_id),
    };
    if let Some(ref agent) = node.agent_id {
        format!("{:?}(agent={})", node.node_type, agent)
    } else if let Some(ref res) = node.resource {
        format!("{:?}({:?})", node.node_type, res)
    } else {
        format!("{:?}", node.node_type)
    }
}

// ─── Tests ─────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use crate::responsibility::graph::config::GraphConfig;
    use crate::responsibility::types::{
        EdgeId, NodeId, PhaseIndicator, ResponsibilityEdge, ResponsibilityEdgeType,
        ResponsibilityNode, ResponsibilityNodeType,
    };

    fn n(id: &str, nt: ResponsibilityNodeType, agent: Option<&str>) -> ResponsibilityNode {
        ResponsibilityNode {
            node_id: NodeId::from(id), node_type: nt,
            agent_id: agent.map(|a| AgentId::from(a)),
            resource: None, evidence_refs: Vec::new(), timestamp_ns: 1000,
            phase: PhaseIndicator::Provisional { timestamp_ns: 1000 },
        }
    }
    fn e(id: &str, et: ResponsibilityEdgeType, src: &str, tgt: &str, conf: f32) -> ResponsibilityEdge {
        ResponsibilityEdge {
            edge_id: EdgeId::from(id), edge_type: et,
            source: NodeId::from(src), target: NodeId::from(tgt),
            evidence_refs: Vec::new(), confidence: conf,
            phase: PhaseIndicator::Provisional { timestamp_ns: 1000 },
        signal: None,
        }
    }

    #[test]
    fn test_trace_simple_chain() {
        let mut g = ResponsibilityGraph::new(GraphConfig::default());
        g.add_node(n("root", ResponsibilityNodeType::Agent, Some("a1")));
        g.add_node(n("mid", ResponsibilityNodeType::Resource, None));
        g.add_node(n("leaf", ResponsibilityNodeType::Resource, None));
        g.add_edge(e("e1", ResponsibilityEdgeType::IntentCausesAction, "root", "mid", 0.9));
        g.add_edge(e("e2", ResponsibilityEdgeType::IntentCausesAction, "mid", "leaf", 0.8));
        let chain = CausalChainTracer::new().trace(&g, NodeId::from("leaf")).unwrap();
        assert_eq!(chain.root_node_id, NodeId::from("root"));
        assert_eq!(chain.depth, 2);
        assert_eq!(chain.chain_nodes.len(), 3);
        assert!(chain.confidence > 0.7);
    }

    #[test]
    fn test_cycle_stops() {
        let mut g = ResponsibilityGraph::new(GraphConfig::default());
        g.add_node(n("a", ResponsibilityNodeType::Agent, Some("a1")));
        g.add_node(n("b", ResponsibilityNodeType::Resource, None));
        g.add_edge(e("e1", ResponsibilityEdgeType::IntentCausesAction, "a", "b", 0.9));
        g.add_edge(e("e2", ResponsibilityEdgeType::IntentCausesAction, "b", "a", 0.7));
        let chain = CausalChainTracer::new().trace(&g, NodeId::from("b")).unwrap();
        assert_eq!(chain.depth, 1);
    }

    #[test]
    fn test_missing_node() {
        let g = ResponsibilityGraph::new(GraphConfig::default());
        assert!(CausalChainTracer::new().trace(&g, NodeId::from("nonexistent")).is_none());
    }

    #[test]
    fn test_deepest_chain() {
        let mut g = ResponsibilityGraph::new(GraphConfig::default());
        g.add_node(n("n1", ResponsibilityNodeType::Agent, Some("a1")));
        g.add_node(n("n2", ResponsibilityNodeType::Resource, None));
        g.add_node(n("n3", ResponsibilityNodeType::Resource, None));
        g.add_edge(e("e1", ResponsibilityEdgeType::IntentCausesAction, "n1", "n2", 0.9));
        g.add_edge(e("e2", ResponsibilityEdgeType::IntentCausesAction, "n2", "n3", 0.8));
        let deepest = CausalChainTracer::new().deepest_chain(&g).unwrap();
        assert_eq!(deepest.depth, 2);
    }

    #[test]
    fn test_involved_agents() {
        let mut g = ResponsibilityGraph::new(GraphConfig::default());
        g.add_node(n("a1", ResponsibilityNodeType::Agent, Some("agent-1")));
        g.add_node(n("a2", ResponsibilityNodeType::Agent, Some("agent-2")));
        g.add_node(n("r1", ResponsibilityNodeType::Resource, None));
        g.add_edge(e("e1", ResponsibilityEdgeType::AgentDelegatesTo, "a1", "a2", 0.85));
        g.add_edge(e("e2", ResponsibilityEdgeType::IntentCausesAction, "a2", "r1", 0.9));
        let chain = CausalChainTracer::new().trace(&g, NodeId::from("r1")).unwrap();
        assert!(chain.involved_agents.contains(&AgentId::from("agent-1")));
        assert!(chain.involved_agents.contains(&AgentId::from("agent-2")));
    }
}
