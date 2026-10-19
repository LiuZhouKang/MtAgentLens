// SPDX-License-Identifier: Apache-2.0 OR MIT
//
// snapshot.rs — GraphSnapshot creation, FrozenGraph (Phase 2), and snapshot helpers.
//
// GraphSnapshot captures a point-in-time copy of the responsibility graph.
// FrozenGraph represents a Phase 2 finalized graph with authoritative
// identity, evidence, and graph refinements.

use crate::responsibility::graph::graph::ResponsibilityGraph;
use crate::responsibility::types::{
    GraphMetrics, GraphRefinement, GraphSnapshot, ResponsibilityEdge, ResponsibilityNode,
};
use serde::{Deserialize, Serialize};

// ─── FrozenGraph ───────────────────────────────────────────────────────────

/// A Phase 2 frozen graph: the authoritative responsibility graph with
/// all refinements applied.
///
/// Produced by `GraphBuilder::finalize()` at session end.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FrozenGraph {
    /// The frozen responsibility graph (nodes + edges).
    pub nodes: Vec<ResponsibilityNode>,
    /// The frozen edges.
    pub edges: Vec<ResponsibilityEdge>,
    /// Point-in-time snapshot metadata.
    pub snapshot: GraphSnapshot,
    /// Graph-level refinements from Phase 2 (agent merges, splits, reassignments).
    pub refinements: Vec<GraphRefinement>,
    /// Timestamp when this frozen graph was created.
    pub frozen_at_ns: u64,
    /// Session identifier.
    pub session_id: Option<String>,
}

impl FrozenGraph {
    /// Create a frozen graph from a snapshot and refinements.
    pub fn new(
        snapshot: GraphSnapshot,
        refinements: Vec<GraphRefinement>,
        session_id: Option<String>,
    ) -> Self {
        let frozen_at_ns = snapshot.timestamp_ns;
        let nodes = snapshot.nodes.clone();
        let edges = snapshot.edges.clone();

        Self {
            nodes,
            edges,
            snapshot,
            refinements,
            frozen_at_ns,
            session_id,
        }
    }

    /// Total node count.
    pub fn node_count(&self) -> usize {
        self.nodes.len()
    }

    /// Total edge count.
    pub fn edge_count(&self) -> usize {
        self.edges.len()
    }

    /// Number of graph refinements.
    pub fn refinement_count(&self) -> usize {
        self.refinements.len()
    }

    /// Get the metrics from the snapshot.
    pub fn metrics(&self) -> &GraphMetrics {
        &self.snapshot.metrics
    }
}

// ─── Snapshot helpers ──────────────────────────────────────────────────────

/// Create a snapshot from the current state of the graph.
pub fn create_snapshot(
    graph: &ResponsibilityGraph,
    snapshot_id: String,
    timestamp_ns: u64,
) -> GraphSnapshot {
    let nodes = graph.collect_nodes();
    let edges = graph.collect_edges();
    let metrics = graph.metrics();

    GraphSnapshot {
        snapshot_id,
        timestamp_ns,
        nodes,
        edges,
        metrics,
    }
}

/// Compute the difference between a snapshot and the current graph state.
pub fn diff_snapshot(
    graph: &ResponsibilityGraph,
    previous: &GraphSnapshot,
) -> crate::responsibility::types::GraphDiff {
    use std::collections::HashSet;

    let current_node_ids: HashSet<_> = graph.all_nodes().map(|n| n.node_id.clone()).collect();
    let prev_node_ids: HashSet<_> = previous.nodes.iter().map(|n| n.node_id.clone()).collect();

    let current_edge_ids: HashSet<_> = graph.all_edges().map(|e| e.edge_id.clone()).collect();
    let prev_edge_ids: HashSet<_> = previous.edges.iter().map(|e| e.edge_id.clone()).collect();

    let added_nodes: Vec<_> = current_node_ids
        .difference(&prev_node_ids)
        .cloned()
        .collect();
    let removed_nodes: Vec<_> = prev_node_ids
        .difference(&current_node_ids)
        .cloned()
        .collect();
    let added_edges: Vec<_> = current_edge_ids
        .difference(&prev_edge_ids)
        .cloned()
        .collect();
    let removed_edges: Vec<_> = prev_edge_ids
        .difference(&current_edge_ids)
        .cloned()
        .collect();

    let node_count_change = added_nodes.len() as i32 - removed_nodes.len() as i32;
    let edge_count_change = added_edges.len() as i32 - removed_edges.len() as i32;

    crate::responsibility::types::GraphDiff {
        added_nodes,
        removed_nodes,
        added_edges,
        removed_edges,
        node_count_change,
        edge_count_change,
    }
}

// ─── Tests ─────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use crate::responsibility::graph::config::GraphConfig;
    use crate::responsibility::types::{
        PhaseIndicator, ResponsibilityEdgeType, ResponsibilityNodeType,
    };

    fn make_node(id: &str, node_type: ResponsibilityNodeType) -> ResponsibilityNode {
        ResponsibilityNode {
            node_id: crate::responsibility::types::NodeId::from(id),
            node_type,
            agent_id: None,
            resource: None,
            evidence_refs: Vec::new(),
            timestamp_ns: 1000,
            phase: PhaseIndicator::Provisional { timestamp_ns: 1000 },
        }
    }

    #[test]
    fn test_create_snapshot() {
        let mut g = ResponsibilityGraph::new(GraphConfig::default());
        g.add_node(make_node("n1", ResponsibilityNodeType::Agent));

        let snap = create_snapshot(&g, "snap-1".into(), 2000);
        assert_eq!(snap.nodes.len(), 1);
        // Node has no agent_id, so it does not count toward agent_count
        assert_eq!(snap.metrics.agent_count, 0);
    }

    #[test]
    fn test_diff_snapshot() {
        let mut g = ResponsibilityGraph::new(GraphConfig::default());
        g.add_node(make_node("n1", ResponsibilityNodeType::Agent));

        let snap1 = create_snapshot(&g, "snap-1".into(), 1000);

        // Add another node.
        g.add_node(make_node("n2", ResponsibilityNodeType::Resource));

        let diff = diff_snapshot(&g, &snap1);
        assert_eq!(diff.added_nodes.len(), 1);
        assert_eq!(
            diff.added_nodes[0],
            crate::responsibility::types::NodeId::from("n2")
        );
        assert_eq!(diff.node_count_change, 1);
    }

    #[test]
    fn test_frozen_graph() {
        let mut g = ResponsibilityGraph::new(GraphConfig::default());
        g.add_node(make_node("n1", ResponsibilityNodeType::Agent));

        let snap = create_snapshot(&g, "final-snap".into(), 5000);
        let frozen = FrozenGraph::new(snap, Vec::new(), Some("session-1".into()));

        assert_eq!(frozen.node_count(), 1);
        assert_eq!(frozen.edge_count(), 0);
        assert_eq!(frozen.refinement_count(), 0);
        assert_eq!(frozen.session_id, Some("session-1".into()));
    }
}
