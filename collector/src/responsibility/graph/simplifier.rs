// SPDX-License-Identifier: MIT
//
// simplifier.rs — GraphSimplifier: simplifies/filters/windows the graph.
//
// Operations:
//   - Simplify: remove low-confidence edges, merge duplicate resource nodes.
//   - Filter: retain only nodes/edges matching a predicate.
//   - Window: retain only nodes/edges within a time window.

use crate::responsibility::graph::config::GraphConfig;
use crate::responsibility::graph::delta::GraphDelta;
use crate::responsibility::graph::graph::ResponsibilityGraph;
use crate::responsibility::types::{EdgeId, NodeId, ResponsibilityNodeType};

// ─── GraphSimplifier ───────────────────────────────────────────────────────

/// Simplifies the responsibility graph by removing noise and merging
/// redundant structures.
pub struct GraphSimplifier {
    config: GraphConfig,
}

impl GraphSimplifier {
    /// Create a new simplifier with the given config.
    pub fn new(config: GraphConfig) -> Self {
        Self { config }
    }

    /// Create with default config.
    pub fn with_default_config() -> Self {
        Self::new(GraphConfig::default())
    }

    // ── Simplify ──────────────────────────────────────────────────────

    /// Simplify the graph: remove low-confidence edges and merge duplicate
    /// resource nodes that point to the same canonical resource.
    ///
    /// Returns a GraphDelta describing what was removed and merged.
    pub fn simplify(&self, graph: &mut ResponsibilityGraph) -> GraphDelta {
        let mut delta = GraphDelta::empty();

        // ── 1. Remove edges below confidence threshold ────────────────
        let low_conf_edges: Vec<EdgeId> = graph
            .all_edges()
            .filter(|e| e.confidence < self.config.min_edge_confidence)
            .map(|e| e.edge_id.clone())
            .collect();

        for edge_id in &low_conf_edges {
            if let Some(removed) = graph.remove_edge(edge_id) {
                delta.removed_edges.push(edge_id.clone());
                delta.diagnostics.push(format!(
                    "simplifier: removed low-confidence edge {} (confidence={})",
                    removed.edge_id, removed.confidence
                ));
            }
        }

        // ── 2. Merge duplicate resource nodes ─────────────────────────
        self.merge_duplicate_resources(graph, &mut delta);

        // ── 3. Remove orphaned resource nodes ─────────────────────────
        let orphan_resources: Vec<NodeId> = graph
            .nodes_of_type(&ResponsibilityNodeType::Resource)
            .iter()
            .filter(|nid| {
                graph.outgoing_from(nid).is_empty() && graph.incoming_to(nid).is_empty()
            })
            .cloned()
            .collect();

        for nid in &orphan_resources {
            let removed = graph.remove_node(nid.clone());
            delta.removed_nodes.push(nid.clone());
            delta.removed_edges.extend(removed.removed_edges);
        }

        delta
    }

    // ── Filter ────────────────────────────────────────────────────────

    /// Filter the graph, removing nodes that do not satisfy the predicate.
    /// Edges incident on removed nodes are also removed.
    pub fn filter<F>(&self, graph: &mut ResponsibilityGraph, predicate: F) -> GraphDelta
    where
        F: Fn(&NodeId) -> bool,
    {
        let mut delta = GraphDelta::empty();

        let to_remove: Vec<NodeId> = graph
            .all_nodes()
            .filter(|n| !predicate(&n.node_id))
            .map(|n| n.node_id.clone())
            .collect();

        for nid in to_remove {
            let removed = graph.remove_node(nid.clone());
            delta.removed_nodes.push(nid);
            delta.removed_edges.extend(removed.removed_edges);
        }

        delta
    }

    /// Filter edges, removing those that do not satisfy the predicate.
    pub fn filter_edges<F>(&self, graph: &mut ResponsibilityGraph, predicate: F) -> GraphDelta
    where
        F: Fn(&EdgeId) -> bool,
    {
        let mut delta = GraphDelta::empty();

        let to_remove: Vec<EdgeId> = graph
            .all_edges()
            .filter(|e| !predicate(&e.edge_id))
            .map(|e| e.edge_id.clone())
            .collect();

        for eid in to_remove {
            if graph.remove_edge(&eid).is_some() {
                delta.removed_edges.push(eid);
            }
        }

        delta
    }

    // ── Window ────────────────────────────────────────────────────────

    /// Retain only nodes and edges within the given time window [start_ns, end_ns].
    pub fn window(
        &self,
        graph: &mut ResponsibilityGraph,
        start_ns: u64,
        end_ns: u64,
    ) -> GraphDelta {
        let mut delta = GraphDelta::empty();

        // Remove edges outside the window first.
        let out_of_window_edges: Vec<EdgeId> = graph
            .all_edges()
            .filter(|_e| {
                // Edges don't have timestamps; use evidence_refs to look up nodes.
                // For simplicity, keep all edges and rely on node windowing.
                false // Keep edges (remove none).
            })
            .map(|e| e.edge_id.clone())
            .collect();

        for eid in &out_of_window_edges {
            if graph.remove_edge(eid).is_some() {
                delta.removed_edges.push(eid.clone());
            }
        }

        // Remove nodes outside the window.
        let out_of_window_nodes: Vec<NodeId> = graph
            .all_nodes()
            .filter(|n| n.timestamp_ns < start_ns || n.timestamp_ns > end_ns)
            .map(|n| n.node_id.clone())
            .collect();

        for nid in out_of_window_nodes {
            let removed = graph.remove_node(nid.clone());
            delta.removed_nodes.push(nid);
            delta.removed_edges.extend(removed.removed_edges);
        }

        // Remove orphaned edges (edges whose source or target node was removed).
        let orphan_edges: Vec<EdgeId> = graph
            .all_edges()
            .filter(|e| !graph.has_node(&e.source) || !graph.has_node(&e.target))
            .map(|e| e.edge_id.clone())
            .collect();

        for eid in orphan_edges {
            if graph.remove_edge(&eid).is_some() {
                delta.removed_edges.push(eid);
            }
        }

        delta
    }

    // ── Internal ───────────────────────────────────────────────────────

    /// Merge duplicate resource nodes (same resource representation).
    fn merge_duplicate_resources(
        &self,
        graph: &mut ResponsibilityGraph,
        delta: &mut GraphDelta,
    ) {
        use std::collections::HashMap;

        let resource_nodes: Vec<_> = graph
            .all_nodes()
            .filter(|n| n.node_type == ResponsibilityNodeType::Resource && n.resource.is_some())
            .map(|n| (n.node_id.clone(), n.resource.clone().unwrap()))
            .collect();

        // Group by resource representation.
        let mut groups: HashMap<String, Vec<NodeId>> = HashMap::new();
        for (nid, res) in &resource_nodes {
            let key = format!("{:?}", res);
            groups.entry(key).or_default().push(nid.clone());
        }

        // Merge groups with >1 node.
        for (_key, node_ids) in groups {
            if node_ids.len() <= 1 {
                continue;
            }

            let keep = node_ids[0].clone();
            for remove_id in &node_ids[1..] {
                graph.merge_nodes(keep.clone(), remove_id.clone());
            }

            delta.diagnostics.push(format!(
                "simplifier: merged {} duplicate resource nodes into {}",
                node_ids.len() - 1,
                keep
            ));
        }
    }
}

impl Default for GraphSimplifier {
    fn default() -> Self {
        Self::with_default_config()
    }
}

// ─── Tests ─────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use crate::responsibility::types::{
        EdgeId, NodeId, PhaseIndicator, ResponsibilityEdge, ResponsibilityEdgeType,
        ResponsibilityNode, ResponsibilityNodeType, NormalizedResource,
    };

    fn make_node(id: &str, node_type: ResponsibilityNodeType) -> ResponsibilityNode {
        ResponsibilityNode {
            node_id: NodeId::from(id),
            node_type,
            agent_id: None,
            resource: None,
            evidence_refs: Vec::new(),
            timestamp_ns: 1000,
            phase: PhaseIndicator::Provisional { timestamp_ns: 1000 },
        }
    }

    fn make_edge(
        id: &str,
        edge_type: ResponsibilityEdgeType,
        source: &str,
        target: &str,
        confidence: f32,
    ) -> ResponsibilityEdge {
        ResponsibilityEdge {
            edge_id: EdgeId::from(id),
            edge_type,
            source: NodeId::from(source),
            target: NodeId::from(target),
            evidence_refs: Vec::new(),
            confidence,
            phase: PhaseIndicator::Provisional { timestamp_ns: 1000 },
        signal: None,
        }
    }

    #[test]
    fn test_simplify_removes_low_confidence_edges() {
        let config = GraphConfig {
            min_edge_confidence: 0.5,
            ..GraphConfig::default()
        };
        let mut graph = ResponsibilityGraph::new(config.clone());
        graph.add_node(make_node("n1", ResponsibilityNodeType::Agent));
        graph.add_node(make_node("n2", ResponsibilityNodeType::Resource));
        graph.add_edge(make_edge("e1", ResponsibilityEdgeType::IntentCausesAction, "n1", "n2", 0.9));
        graph.add_edge(make_edge("e2", ResponsibilityEdgeType::IntentCausesAction, "n1", "n2", 0.2));

        let simplifier = GraphSimplifier::new(config);
        let delta = simplifier.simplify(&mut graph);

        // e2 should be removed (confidence 0.2 < 0.5).
        assert!(delta.removed_edges.contains(&EdgeId::from("e2")));
        // e1 should remain.
        assert!(graph.get_edge(&EdgeId::from("e1")).is_some());
    }

    #[test]
    fn test_window_filters_by_time() {
        let config = GraphConfig::default();
        let mut graph = ResponsibilityGraph::new(config.clone());

        let mut old_node = make_node("old", ResponsibilityNodeType::Agent);
        old_node.timestamp_ns = 100;
        let mut new_node = make_node("new", ResponsibilityNodeType::Agent);
        new_node.timestamp_ns = 5000;

        graph.add_node(old_node);
        graph.add_node(new_node);

        let simplifier = GraphSimplifier::new(config);
        let delta = simplifier.window(&mut graph, 1000, 10000);

        // "old" should be removed (timestamp 100 < 1000).
        assert!(delta.removed_nodes.contains(&NodeId::from("old")));
        // "new" should remain.
        assert!(graph.has_node(&NodeId::from("new")));
    }

    #[test]
    fn test_filter_keeps_only_matching() {
        let config = GraphConfig::default();
        let mut graph = ResponsibilityGraph::new(config.clone());

        graph.add_node(make_node("keep", ResponsibilityNodeType::Agent));
        graph.add_node(make_node("drop", ResponsibilityNodeType::Resource));

        let simplifier = GraphSimplifier::new(config);
        let delta = simplifier.filter(&mut graph, |nid| {
            nid.as_str() == "keep"
        });

        assert!(delta.removed_nodes.contains(&NodeId::from("drop")));
        assert!(graph.has_node(&NodeId::from("keep")));
    }

    #[test]
    fn test_merge_duplicate_resources() {
        let mut graph = ResponsibilityGraph::new(GraphConfig::default());
        let res = NormalizedResource::File {
            canonical_path: "/tmp/test.txt".to_string(),
        };

        let mut n1 = make_node("r1", ResponsibilityNodeType::Resource);
        n1.resource = Some(res.clone());
        let mut n2 = make_node("r2", ResponsibilityNodeType::Resource);
        n2.resource = Some(res.clone());

        graph.add_node(n1);
        graph.add_node(n2);

        let simplifier = GraphSimplifier::with_default_config();
        let delta = simplifier.simplify(&mut graph);

        // After merge + orphan removal: duplicate merged, then the keep
        // node is removed as orphan (no incident edges). So 0 remain.
        let remaining = graph.nodes_of_type(&ResponsibilityNodeType::Resource);
        assert_eq!(remaining.len(), 0, "Duplicate merged then orphaned node removed");
    }
}
