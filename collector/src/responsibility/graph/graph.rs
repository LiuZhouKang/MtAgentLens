// SPDX-License-Identifier: Apache-2.0 OR MIT
//
// graph.rs — ResponsibilityGraph: the central in-memory graph data structure.
//
// Maintains nodes and edges with six indexes for efficient lookup by:
//   1. Agent → nodes
//   2. Agent → edges
//   3. Source node → outgoing edges
//   4. Target node → incoming edges
//   5. Node type → nodes
//   6. Edge type → edges
//
// Supports traversal (bfs_from, walk_backward, connected_agents), node/edge
// lifecycle (merge, remove, evict), and metrics reporting.

use crate::responsibility::graph::config::GraphConfig;
use crate::responsibility::types::{
    AgentId, EdgeId, GraphMetrics, NodeId, ResponsibilityEdge, ResponsibilityEdgeType,
    ResponsibilityNode, ResponsibilityNodeType,
};
use std::collections::{HashMap, HashSet, VecDeque};

// ─── ResponsibilityGraph ───────────────────────────────────────────────────

/// The central in-memory responsibility graph.
///
/// Nodes represent agents, resources, and policy boundaries.
/// Edges represent causal relationships: intent→action, delegation,
/// context flow, taint propagation, and policy-boundary crossings.
#[derive(Debug, Clone)]
pub struct ResponsibilityGraph {
    // ── Primary storage ───────────────────────────────────────────────
    nodes: HashMap<NodeId, ResponsibilityNode>,
    edges: HashMap<EdgeId, ResponsibilityEdge>,

    // ── Six indexes ───────────────────────────────────────────────────
    /// Index 1: AgentId → nodes owned by that agent.
    agent_nodes: HashMap<AgentId, Vec<NodeId>>,
    /// Index 2: AgentId → edges incident on that agent's nodes.
    agent_edges: HashMap<AgentId, Vec<EdgeId>>,
    /// Index 3: Source NodeId → outgoing edges.
    outgoing_edges: HashMap<NodeId, Vec<EdgeId>>,
    /// Index 4: Target NodeId → incoming edges.
    incoming_edges: HashMap<NodeId, Vec<EdgeId>>,
    /// Index 5: Node type → nodes of that type.
    nodes_by_type: HashMap<ResponsibilityNodeType, Vec<NodeId>>,
    /// Index 6: Edge type → edges of that type.
    edges_by_type: HashMap<ResponsibilityEdgeType, Vec<EdgeId>>,

    // ── Insertion order (for FIFO eviction) ───────────────────────────
    node_order: Vec<NodeId>,
    edge_order: Vec<EdgeId>,

    // ── Configuration ─────────────────────────────────────────────────
    config: GraphConfig,
}

impl ResponsibilityGraph {
    /// Create a new, empty graph with the given configuration.
    pub fn new(config: GraphConfig) -> Self {
        Self {
            nodes: HashMap::new(),
            edges: HashMap::new(),
            agent_nodes: HashMap::new(),
            agent_edges: HashMap::new(),
            outgoing_edges: HashMap::new(),
            incoming_edges: HashMap::new(),
            nodes_by_type: HashMap::new(),
            edges_by_type: HashMap::new(),
            node_order: Vec::new(),
            edge_order: Vec::new(),
            config,
        }
    }

    /// Create a new graph with default configuration.
    pub fn with_default_config() -> Self {
        Self::new(GraphConfig::default())
    }

    // ── Node operations ───────────────────────────────────────────────

    /// Add a node to the graph. Returns the NodeId.
    pub fn add_node(&mut self, node: ResponsibilityNode) -> NodeId {
        let node_id = node.node_id.clone();
        let agent_id = node.agent_id.clone();
        let node_type = node.node_type.clone();

        // Update primary storage.
        self.nodes.insert(node_id.clone(), node);

        // Update indexes.
        if let Some(ref agent) = agent_id {
            self.agent_nodes
                .entry(agent.clone())
                .or_default()
                .push(node_id.clone());
        }
        self.nodes_by_type
            .entry(node_type)
            .or_default()
            .push(node_id.clone());

        // Track insertion order.
        self.node_order.push(node_id.clone());

        // Enforce hard limit.
        self.enforce_node_limit();

        node_id
    }

    /// Get a node by ID.
    pub fn get_node(&self, node_id: &NodeId) -> Option<&ResponsibilityNode> {
        self.nodes.get(node_id)
    }

    /// Check if a node exists.
    pub fn has_node(&self, node_id: &NodeId) -> bool {
        self.nodes.contains_key(node_id)
    }

    /// Update an existing node (replaces in-place).
    /// Returns the old node if it existed, None otherwise.
    pub fn update_node(&mut self, node: ResponsibilityNode) -> Option<ResponsibilityNode> {
        let node_id = node.node_id.clone();

        self.nodes.insert(node_id, node)
    }

    /// Remove a node and all its incident edges.
    /// Returns the removed node and all removed edge IDs.
    pub fn remove_node(&mut self, node_id: NodeId) -> RemovedNode {
        let removed_node = self.nodes.remove(&node_id);

        // Collect all incident edge IDs.
        let outgoing = self.outgoing_edges.remove(&node_id).unwrap_or_default();
        let incoming = self.incoming_edges.remove(&node_id).unwrap_or_default();
        let incident_edges: Vec<EdgeId> = outgoing.iter().chain(incoming.iter()).cloned().collect();

        // Remove incident edges.
        let mut removed_edge_ids = Vec::new();
        for edge_id in &incident_edges {
            if let Some(edge) = self.edges.remove(edge_id) {
                removed_edge_ids.push(edge.edge_id.clone());

                // Clean up agent_edges index.
                if let Some(node) = removed_node.as_ref()
                    && let Some(ref agent) = node.agent_id
                    && let Some(agent_edge_list) = self.agent_edges.get_mut(agent)
                {
                    agent_edge_list.retain(|e| e != edge_id);
                }

                // Clean up edges_by_type index.
                if let Some(type_list) = self.edges_by_type.get_mut(&edge.edge_type) {
                    type_list.retain(|e| e != edge_id);
                }
            }
        }

        // Clean up agent_nodes index.
        if let Some(ref node) = removed_node {
            if let Some(ref agent) = node.agent_id
                && let Some(agent_node_list) = self.agent_nodes.get_mut(agent)
            {
                agent_node_list.retain(|n| n != &node_id);
            }
            // Clean up nodes_by_type index.
            if let Some(type_list) = self.nodes_by_type.get_mut(&node.node_type) {
                type_list.retain(|n| n != &node_id);
            }
        }

        // Clean up insertion order.
        self.node_order.retain(|n| n != &node_id);
        self.edge_order.retain(|e| !removed_edge_ids.contains(e));

        RemovedNode {
            node: removed_node,
            removed_edges: removed_edge_ids,
        }
    }

    /// Evict the oldest nodes until the node count reaches `target_count`.
    ///
    /// Nodes are evicted in FIFO order (oldest first). Each evicted node
    /// also removes its incident edges.
    pub fn evict_oldest(&mut self, target_count: usize) -> Vec<RemovedNode> {
        let current = self.nodes.len();
        if current <= target_count {
            return Vec::new();
        }

        let to_remove = (current - target_count).min(self.config.eviction_batch_size);
        let mut removed = Vec::with_capacity(to_remove);

        for _ in 0..to_remove {
            if let Some(oldest) = self.node_order.first().cloned() {
                removed.push(self.remove_node(oldest));
            } else {
                break;
            }
        }

        removed
    }

    /// Merge two nodes: reassign all edges from `remove` to `keep`,
    /// then remove `remove`.
    pub fn merge_nodes(&mut self, keep: NodeId, remove: NodeId) {
        // Reassign outgoing edges.
        if let Some(out_edges) = self.outgoing_edges.remove(&remove) {
            for edge_id in &out_edges {
                if let Some(edge) = self.edges.get_mut(edge_id) {
                    edge.source = keep.clone();
                }
            }
            self.outgoing_edges
                .entry(keep.clone())
                .or_default()
                .extend(out_edges);
        }

        // Reassign incoming edges.
        if let Some(in_edges) = self.incoming_edges.remove(&remove) {
            for edge_id in &in_edges {
                if let Some(edge) = self.edges.get_mut(edge_id) {
                    edge.target = keep.clone();
                }
            }
            self.incoming_edges
                .entry(keep.clone())
                .or_default()
                .extend(in_edges);
        }

        // Remove the merged node.
        self.remove_node(remove);
    }

    // ── Edge operations ───────────────────────────────────────────────

    /// Add an edge to the graph. Returns the EdgeId.
    pub fn add_edge(&mut self, edge: ResponsibilityEdge) -> EdgeId {
        let edge_id = edge.edge_id.clone();
        let source = edge.source.clone();
        let target = edge.target.clone();
        let edge_type = edge.edge_type.clone();

        // Update primary storage.
        self.edges.insert(edge_id.clone(), edge);

        // Update indexes (clone before move into entry).
        self.outgoing_edges
            .entry(source.clone())
            .or_default()
            .push(edge_id.clone());
        self.incoming_edges
            .entry(target.clone())
            .or_default()
            .push(edge_id.clone());
        self.edges_by_type
            .entry(edge_type)
            .or_default()
            .push(edge_id.clone());

        // Update agent_edges: find the agent for source and target nodes.
        let source_agent = self.get_node(&source).and_then(|n| n.agent_id.clone());
        let target_agent = self.get_node(&target).and_then(|n| n.agent_id.clone());

        if let Some(agent) = source_agent {
            self.agent_edges
                .entry(agent)
                .or_default()
                .push(edge_id.clone());
        }
        if let Some(agent) = target_agent {
            // Only add if different from source agent to avoid double-counting.
            if source_agent_incoming_different(&self.agent_edges, &agent, &edge_id) {
                self.agent_edges
                    .entry(agent)
                    .or_default()
                    .push(edge_id.clone());
            }
        }

        // Track insertion order.
        self.edge_order.push(edge_id.clone());

        // Enforce hard limit.
        self.enforce_edge_limit();

        edge_id
    }

    /// Get an edge by ID.
    pub fn get_edge(&self, edge_id: &EdgeId) -> Option<&ResponsibilityEdge> {
        self.edges.get(edge_id)
    }

    /// Get a mutable reference to an edge by ID.
    pub fn get_edge_mut(&mut self, edge_id: &EdgeId) -> Option<&mut ResponsibilityEdge> {
        self.edges.get_mut(edge_id)
    }

    /// Remove an edge by ID.
    pub fn remove_edge(&mut self, edge_id: &EdgeId) -> Option<ResponsibilityEdge> {
        let removed = self.edges.remove(edge_id);

        if let Some(ref edge) = removed {
            // Clean up outgoing_edges.
            if let Some(out_list) = self.outgoing_edges.get_mut(&edge.source) {
                out_list.retain(|e| e != edge_id);
            }
            // Clean up incoming_edges.
            if let Some(in_list) = self.incoming_edges.get_mut(&edge.target) {
                in_list.retain(|e| e != edge_id);
            }
            // Clean up edges_by_type.
            if let Some(type_list) = self.edges_by_type.get_mut(&edge.edge_type) {
                type_list.retain(|e| e != edge_id);
            }
            // Clean up agent_edges.
            for agent_list in self.agent_edges.values_mut() {
                agent_list.retain(|e| e != edge_id);
            }
            // Clean up insertion order.
            self.edge_order.retain(|e| e != edge_id);
        }

        removed
    }

    // ── Traversal (per spec §10.6) ────────────────────────────────────

    /// Breadth-first traversal from a start node.
    ///
    /// Returns layers: each layer is a vector of (node_id, edge_id_that_led_here).
    /// The first layer contains only the start node with no incoming edge.
    pub fn bfs_from(&self, start: NodeId, max_depth: usize) -> Vec<Vec<(NodeId, Option<EdgeId>)>> {
        let mut layers: Vec<Vec<(NodeId, Option<EdgeId>)>> = Vec::new();
        let mut visited: HashSet<NodeId> = HashSet::new();
        let mut queue: VecDeque<(NodeId, Option<EdgeId>, usize)> = VecDeque::new();

        visited.insert(start.clone());
        queue.push_back((start.clone(), None, 0));

        let mut current_layer: Vec<(NodeId, Option<EdgeId>)> = Vec::new();
        let mut current_depth = 0;

        while let Some((node_id, edge_id, depth)) = queue.pop_front() {
            if depth > max_depth {
                break;
            }

            if depth != current_depth {
                layers.push(std::mem::take(&mut current_layer));
                current_depth = depth;
            }

            current_layer.push((node_id.clone(), edge_id));

            // Enqueue neighbors via outgoing edges.
            if let Some(out_edges) = self.outgoing_edges.get(&node_id) {
                for out_edge_id in out_edges {
                    if let Some(edge) = self.edges.get(out_edge_id)
                        && !visited.contains(&edge.target)
                    {
                        visited.insert(edge.target.clone());
                        queue.push_back((
                            edge.target.clone(),
                            Some(out_edge_id.clone()),
                            depth + 1,
                        ));
                    }
                }
            }
        }

        if !current_layer.is_empty() {
            layers.push(current_layer);
        }

        layers
    }

    /// Walk backward from a node, following incoming edges.
    ///
    /// Returns a list of (node_id, edge_id) pairs in reverse causal order
    /// (most recent first, root cause last).
    pub fn walk_backward(&self, start: NodeId, max_depth: usize) -> Vec<(NodeId, EdgeId)> {
        let mut path: Vec<(NodeId, EdgeId)> = Vec::new();
        let mut visited: HashSet<NodeId> = HashSet::new();
        let mut current = start;

        visited.insert(current.clone());

        for _ in 0..max_depth {
            // Find the first incoming edge from an unvisited node.
            let incoming = self.incoming_edges.get(&current);
            let next = incoming.and_then(|edges| {
                edges.iter().find_map(|edge_id| {
                    self.edges.get(edge_id).and_then(|edge| {
                        if !visited.contains(&edge.source) {
                            Some((edge.source.clone(), edge_id.clone()))
                        } else {
                            None
                        }
                    })
                })
            });

            match next {
                Some((prev_node, edge_id)) => {
                    path.push((current.clone(), edge_id.clone()));
                    visited.insert(prev_node.clone());
                    current = prev_node;
                }
                None => break,
            }
        }

        // Add the final node (root) without an edge.
        if !path.is_empty() || visited.len() == 1 {
            // The last current node is the root.
        }

        path
    }

    /// Find all agents connected to the given agent via any edge path.
    ///
    /// Performs a BFS following both outgoing and incoming edges to discover
    /// all agents reachable from the given agent's nodes.
    pub fn connected_agents(&self, agent: &AgentId) -> Vec<AgentId> {
        let mut connected: Vec<AgentId> = Vec::new();
        let mut visited_agents: HashSet<AgentId> = HashSet::new();
        let mut visited_nodes: HashSet<NodeId> = HashSet::new();

        // Start from all nodes owned by this agent.
        let start_nodes = self.agent_nodes.get(agent).cloned().unwrap_or_default();

        for start_node in &start_nodes {
            if !visited_nodes.insert(start_node.clone()) {
                continue;
            }

            let mut queue: VecDeque<NodeId> = VecDeque::new();
            queue.push_back(start_node.clone());

            while let Some(node_id) = queue.pop_front() {
                // Check agent for this node.
                if let Some(node) = self.nodes.get(&node_id)
                    && let Some(ref node_agent) = node.agent_id
                    && node_agent != agent
                    && visited_agents.insert(node_agent.clone())
                {
                    connected.push(node_agent.clone());
                }

                // Follow outgoing edges.
                if let Some(out_edges) = self.outgoing_edges.get(&node_id) {
                    for edge_id in out_edges {
                        if let Some(edge) = self.edges.get(edge_id)
                            && visited_nodes.insert(edge.target.clone())
                        {
                            queue.push_back(edge.target.clone());
                        }
                    }
                }

                // Follow incoming edges (reverse direction for broader connectivity).
                if let Some(in_edges) = self.incoming_edges.get(&node_id) {
                    for edge_id in in_edges {
                        if let Some(edge) = self.edges.get(edge_id)
                            && visited_nodes.insert(edge.source.clone())
                        {
                            queue.push_back(edge.source.clone());
                        }
                    }
                }
            }
        }

        connected
    }

    // ── Query helpers ─────────────────────────────────────────────────

    /// Get all nodes for a given agent.
    pub fn nodes_for_agent(&self, agent: &AgentId) -> &[NodeId] {
        self.agent_nodes
            .get(agent)
            .map(|v| v.as_slice())
            .unwrap_or(&[])
    }

    /// Get all edges incident on a given agent's nodes.
    pub fn edges_for_agent(&self, agent: &AgentId) -> &[EdgeId] {
        self.agent_edges
            .get(agent)
            .map(|v| v.as_slice())
            .unwrap_or(&[])
    }

    /// Get outgoing edges from a node.
    pub fn outgoing_from(&self, node_id: &NodeId) -> &[EdgeId] {
        self.outgoing_edges
            .get(node_id)
            .map(|v| v.as_slice())
            .unwrap_or(&[])
    }

    /// Get incoming edges to a node.
    pub fn incoming_to(&self, node_id: &NodeId) -> &[EdgeId] {
        self.incoming_edges
            .get(node_id)
            .map(|v| v.as_slice())
            .unwrap_or(&[])
    }

    /// Get all nodes of a specific type.
    pub fn nodes_of_type(&self, node_type: &ResponsibilityNodeType) -> &[NodeId] {
        self.nodes_by_type
            .get(node_type)
            .map(|v| v.as_slice())
            .unwrap_or(&[])
    }

    /// Get all edges of a specific type.
    pub fn edges_of_type(&self, edge_type: &ResponsibilityEdgeType) -> &[EdgeId] {
        self.edges_by_type
            .get(edge_type)
            .map(|v| v.as_slice())
            .unwrap_or(&[])
    }

    /// Find a node by agent ID. Returns the first matching Agent node.
    pub fn find_agent_node(&self, agent: &AgentId) -> Option<&ResponsibilityNode> {
        self.agent_nodes
            .get(agent)
            .and_then(|node_ids| node_ids.first())
            .and_then(|nid| self.nodes.get(nid))
    }

    /// Find or return None for a Resource node by its resource.
    pub fn find_resource_node(&self, resource_repr: &str) -> Option<&ResponsibilityNode> {
        self.nodes_by_type
            .get(&ResponsibilityNodeType::Resource)
            .and_then(|node_ids| {
                node_ids.iter().find_map(|nid| {
                    self.nodes.get(nid).and_then(|node| {
                        node.resource.as_ref().and_then(|r| {
                            if format!("{:?}", r) == resource_repr {
                                Some(node)
                            } else {
                                None
                            }
                        })
                    })
                })
            })
    }

    // ── Metrics ───────────────────────────────────────────────────────

    /// Compute graph metrics.
    pub fn metrics(&self) -> GraphMetrics {
        let mut node_counts: HashMap<String, usize> = HashMap::new();
        let mut edge_counts: HashMap<String, usize> = HashMap::new();

        for (nt, nodes) in &self.nodes_by_type {
            node_counts.insert(format!("{:?}", nt), nodes.len());
        }
        for (et, edges) in &self.edges_by_type {
            edge_counts.insert(format!("{:?}", et), edges.len());
        }

        let agent_count = self.agent_nodes.len();

        let cross_agent_edges = self
            .edges
            .values()
            .filter(|e| {
                let src_agent = self.nodes.get(&e.source).and_then(|n| n.agent_id.as_ref());
                let tgt_agent = self.nodes.get(&e.target).and_then(|n| n.agent_id.as_ref());
                src_agent.is_some() && tgt_agent.is_some() && src_agent != tgt_agent
            })
            .count();

        let delegation_depth = self.compute_max_delegation_depth();
        let taint_chains = self
            .edges_by_type
            .get(&ResponsibilityEdgeType::TaintPropagatesTo)
            .map(|e| e.len())
            .unwrap_or(0);

        let avg_confidence = if self.edges.is_empty() {
            0.0
        } else {
            let total: f32 = self.edges.values().map(|e| e.confidence).sum();
            total / self.edges.len() as f32
        };

        GraphMetrics {
            node_counts,
            edge_counts,
            agent_count,
            cross_agent_edges,
            delegation_depth,
            taint_chains,
            avg_confidence,
        }
    }

    // ── Accessors ─────────────────────────────────────────────────────

    /// Total number of nodes.
    pub fn node_count(&self) -> usize {
        self.nodes.len()
    }

    /// Total number of edges.
    pub fn edge_count(&self) -> usize {
        self.edges.len()
    }

    /// All nodes (read-only iterator).
    pub fn all_nodes(&self) -> impl Iterator<Item = &ResponsibilityNode> {
        self.nodes.values()
    }

    /// All edges (read-only iterator).
    pub fn all_edges(&self) -> impl Iterator<Item = &ResponsibilityEdge> {
        self.edges.values()
    }

    /// Snapshot: collect all nodes and edges.
    pub fn collect_nodes(&self) -> Vec<ResponsibilityNode> {
        self.nodes.values().cloned().collect()
    }

    /// Snapshot: collect all edges.
    pub fn collect_edges(&self) -> Vec<ResponsibilityEdge> {
        self.edges.values().cloned().collect()
    }

    /// Get the configuration.
    pub fn config(&self) -> &GraphConfig {
        &self.config
    }

    // ── Internal ───────────────────────────────────────────────────────

    /// Enforce the hard node limit by evicting oldest nodes.
    fn enforce_node_limit(&mut self) {
        while self.nodes.len() > self.config.hard_max_nodes {
            if self.node_order.is_empty() {
                break;
            }
            let oldest = self.node_order[0].clone();
            self.remove_node(oldest);
        }
    }

    /// Enforce the hard edge limit by evicting oldest edges.
    fn enforce_edge_limit(&mut self) {
        while self.edges.len() > self.config.hard_max_edges {
            if self.edge_order.is_empty() {
                break;
            }
            let oldest = self.edge_order[0].clone();
            self.remove_edge(&oldest);
        }
    }

    /// Compute the maximum delegation depth (longest chain of AgentDelegatesTo edges).
    fn compute_max_delegation_depth(&self) -> usize {
        let delegation_edges = self
            .edges_by_type
            .get(&ResponsibilityEdgeType::AgentDelegatesTo);

        let edge_set: HashSet<&EdgeId> = match delegation_edges {
            Some(edges) => edges.iter().collect(),
            None => return 0,
        };

        // Build adjacency for delegation edges only.
        let mut adj: HashMap<NodeId, Vec<NodeId>> = HashMap::new();
        for edge_id in &edge_set {
            if let Some(edge) = self.edges.get(edge_id) {
                adj.entry(edge.source.clone())
                    .or_default()
                    .push(edge.target.clone());
            }
        }

        // Find longest path in the delegation DAG.
        let mut memo: HashMap<NodeId, usize> = HashMap::new();
        let mut max_depth = 0;

        for node_id in adj.keys() {
            let depth = self.delegation_dfs(node_id, &adj, &mut memo, &mut HashSet::new());
            max_depth = max_depth.max(depth);
        }

        max_depth
    }

    fn delegation_dfs(
        &self,
        node: &NodeId,
        adj: &HashMap<NodeId, Vec<NodeId>>,
        memo: &mut HashMap<NodeId, usize>,
        visiting: &mut HashSet<NodeId>,
    ) -> usize {
        if let Some(&cached) = memo.get(node) {
            return cached;
        }
        if !visiting.insert(node.clone()) {
            return 0; // Cycle detected.
        }

        let mut max_child = 0;
        if let Some(children) = adj.get(node) {
            for child in children {
                let child_depth = self.delegation_dfs(child, adj, memo, visiting);
                max_child = max_child.max(child_depth);
            }
        }

        visiting.remove(node);
        let depth = if adj.contains_key(node) {
            1 + max_child
        } else {
            0
        };
        memo.insert(node.clone(), depth);
        depth
    }
}

impl Default for ResponsibilityGraph {
    fn default() -> Self {
        Self::with_default_config()
    }
}

// ─── RemovedNode ───────────────────────────────────────────────────────────

/// Result of removing a node from the graph.
#[derive(Debug, Clone)]
pub struct RemovedNode {
    /// The removed node (None if it didn't exist).
    pub node: Option<ResponsibilityNode>,
    /// Edge IDs that were removed because they were incident on this node.
    pub removed_edges: Vec<EdgeId>,
}

// ─── Helper ────────────────────────────────────────────────────────────────

/// Check whether we should add an agent-edge entry for the target agent.
/// Returns true if the edge_id isn't already in the target agent's list
/// (avoiding double-counting when source == target agent).
fn source_agent_incoming_different(
    agent_edges: &HashMap<AgentId, Vec<EdgeId>>,
    agent: &AgentId,
    edge_id: &EdgeId,
) -> bool {
    agent_edges
        .get(agent)
        .map(|edges| !edges.contains(edge_id))
        .unwrap_or(true)
}

// ─── Tests ─────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use crate::responsibility::types::{PhaseIndicator, ResponsibilityNodeType};

    fn make_node(
        id: &str,
        node_type: ResponsibilityNodeType,
        agent: Option<&str>,
    ) -> ResponsibilityNode {
        ResponsibilityNode {
            node_id: NodeId::from(id),
            node_type,
            agent_id: agent.map(|a| AgentId::from(a)),
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
    ) -> ResponsibilityEdge {
        ResponsibilityEdge {
            edge_id: EdgeId::from(id),
            edge_type,
            source: NodeId::from(source),
            target: NodeId::from(target),
            evidence_refs: Vec::new(),
            confidence: 0.8,
            phase: PhaseIndicator::Provisional { timestamp_ns: 1000 },
        }
    }

    #[test]
    fn test_add_and_get_node() {
        let mut g = ResponsibilityGraph::with_default_config();
        let node = make_node("n1", ResponsibilityNodeType::Agent, Some("agent-1"));
        let nid = g.add_node(node);
        assert_eq!(nid, NodeId::from("n1"));
        assert!(g.get_node(&NodeId::from("n1")).is_some());
    }

    #[test]
    fn test_node_indexes() {
        let mut g = ResponsibilityGraph::with_default_config();
        g.add_node(make_node("n1", ResponsibilityNodeType::Agent, Some("a1")));
        g.add_node(make_node(
            "n2",
            ResponsibilityNodeType::Resource,
            Some("a1"),
        ));

        assert_eq!(g.nodes_for_agent(&AgentId::from("a1")).len(), 2);
        assert_eq!(g.nodes_of_type(&ResponsibilityNodeType::Agent).len(), 1);
        assert_eq!(g.nodes_of_type(&ResponsibilityNodeType::Resource).len(), 1);
    }

    #[test]
    fn test_add_and_get_edge() {
        let mut g = ResponsibilityGraph::with_default_config();
        g.add_node(make_node("n1", ResponsibilityNodeType::Agent, Some("a1")));
        g.add_node(make_node(
            "n2",
            ResponsibilityNodeType::Resource,
            Some("a1"),
        ));

        let edge = make_edge("e1", ResponsibilityEdgeType::IntentCausesAction, "n1", "n2");
        g.add_edge(edge);

        assert_eq!(g.outgoing_from(&NodeId::from("n1")).len(), 1);
        assert_eq!(g.incoming_to(&NodeId::from("n2")).len(), 1);
        assert_eq!(
            g.edges_of_type(&ResponsibilityEdgeType::IntentCausesAction)
                .len(),
            1
        );
    }

    #[test]
    fn test_bfs_from() {
        let mut g = ResponsibilityGraph::with_default_config();
        g.add_node(make_node("a", ResponsibilityNodeType::Agent, Some("a1")));
        g.add_node(make_node(
            "r1",
            ResponsibilityNodeType::Resource,
            Some("a1"),
        ));
        g.add_node(make_node(
            "r2",
            ResponsibilityNodeType::Resource,
            Some("a1"),
        ));

        g.add_edge(make_edge(
            "e1",
            ResponsibilityEdgeType::IntentCausesAction,
            "a",
            "r1",
        ));
        g.add_edge(make_edge(
            "e2",
            ResponsibilityEdgeType::IntentCausesAction,
            "a",
            "r2",
        ));

        let layers = g.bfs_from(NodeId::from("a"), 2);
        assert_eq!(layers.len(), 2); // layer 0: a, layer 1: r1, r2
        assert_eq!(layers[0].len(), 1); // just the start node
        assert_eq!(layers[1].len(), 2); // two resource nodes
    }

    #[test]
    fn test_walk_backward() {
        let mut g = ResponsibilityGraph::with_default_config();
        g.add_node(make_node("a", ResponsibilityNodeType::Agent, Some("a1")));
        g.add_node(make_node("r", ResponsibilityNodeType::Resource, Some("a1")));
        g.add_edge(make_edge(
            "e1",
            ResponsibilityEdgeType::IntentCausesAction,
            "a",
            "r",
        ));

        let path = g.walk_backward(NodeId::from("r"), 5);
        assert!(!path.is_empty());
        // First entry should be (r, e1) — walking from r back to a via e1.
        assert_eq!(path[0].0, NodeId::from("r"));
        assert_eq!(path[0].1, EdgeId::from("e1"));
    }

    #[test]
    fn test_connected_agents() {
        let mut g = ResponsibilityGraph::with_default_config();
        g.add_node(make_node(
            "a1_node",
            ResponsibilityNodeType::Agent,
            Some("agent-1"),
        ));
        g.add_node(make_node(
            "a2_node",
            ResponsibilityNodeType::Agent,
            Some("agent-2"),
        ));
        g.add_node(make_node(
            "r",
            ResponsibilityNodeType::Resource,
            Some("agent-1"),
        ));

        g.add_edge(make_edge(
            "e1",
            ResponsibilityEdgeType::AgentDelegatesTo,
            "a1_node",
            "a2_node",
        ));

        let connected = g.connected_agents(&AgentId::from("agent-1"));
        assert!(connected.contains(&AgentId::from("agent-2")));
    }

    #[test]
    fn test_merge_nodes() {
        let mut g = ResponsibilityGraph::with_default_config();
        g.add_node(make_node("keep", ResponsibilityNodeType::Agent, Some("a1")));
        g.add_node(make_node(
            "remove",
            ResponsibilityNodeType::Resource,
            Some("a1"),
        ));
        g.add_node(make_node("target", ResponsibilityNodeType::Resource, None));

        g.add_edge(make_edge(
            "e1",
            ResponsibilityEdgeType::IntentCausesAction,
            "remove",
            "target",
        ));
        g.add_edge(make_edge(
            "e2",
            ResponsibilityEdgeType::IntentCausesAction,
            "keep",
            "remove",
        ));

        g.merge_nodes(NodeId::from("keep"), NodeId::from("remove"));

        // "remove" should be gone.
        assert!(!g.has_node(&NodeId::from("remove")));
        // Edges should be reassigned to "keep".
        let outgoing = g.outgoing_from(&NodeId::from("keep"));
        assert!(!outgoing.is_empty());
    }

    #[test]
    fn test_metrics() {
        let mut g = ResponsibilityGraph::with_default_config();
        g.add_node(make_node("n1", ResponsibilityNodeType::Agent, Some("a1")));
        g.add_node(make_node("n2", ResponsibilityNodeType::Agent, Some("a2")));
        g.add_edge(make_edge(
            "e1",
            ResponsibilityEdgeType::AgentDelegatesTo,
            "n1",
            "n2",
        ));

        let metrics = g.metrics();
        assert_eq!(metrics.agent_count, 2);
        assert!(metrics.cross_agent_edges >= 1);
    }

    #[test]
    fn test_evict_oldest() {
        let mut g = ResponsibilityGraph::new(GraphConfig {
            hard_max_nodes: 10,
            ..GraphConfig::default()
        });

        for i in 0..5 {
            g.add_node(make_node(
                &format!("n{}", i),
                ResponsibilityNodeType::Resource,
                None,
            ));
        }

        assert_eq!(g.node_count(), 5);
        g.evict_oldest(2);
        assert_eq!(g.node_count(), 2);
    }

    #[test]
    fn test_remove_node_cleans_edges() {
        let mut g = ResponsibilityGraph::with_default_config();
        g.add_node(make_node("n1", ResponsibilityNodeType::Agent, Some("a1")));
        g.add_node(make_node(
            "n2",
            ResponsibilityNodeType::Resource,
            Some("a1"),
        ));
        g.add_edge(make_edge(
            "e1",
            ResponsibilityEdgeType::IntentCausesAction,
            "n1",
            "n2",
        ));

        let removed = g.remove_node(NodeId::from("n1"));
        assert!(removed.node.is_some());
        assert!(!removed.removed_edges.is_empty());
        // n2 should still exist.
        assert!(g.has_node(&NodeId::from("n2")));
        // But e1 should be gone.
        assert!(g.get_edge(&EdgeId::from("e1")).is_none());
    }
}
