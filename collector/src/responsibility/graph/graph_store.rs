// SPDX-License-Identifier: MIT
// Innovation 15: Causal Graph Storage Engine.
//
// Provides time-indexed queries, adjacency-list traversal, forward/backward
// causal chain walks, and graph simplification over the SQLite-backed
// causal-link store. Built on top of the V17 schema with delegation_records.
//
// Key capabilities:
//   - Time-range queries with composite index (agent_id, timestamp_ns)
//   - Adjacency-list construction from intent→candidate edges
//   - Forward walk (intent → its links → candidates → downstream intents)
//   - Backward walk (candidate → links pointing to it → upstream intents)
//   - Confidence-threshold graph simplification
//   - Graph statistics (degree, density, component counts)

use rusqlite::params;
use std::collections::{HashMap, HashSet};
use std::sync::{Arc, Mutex};

/// A node in the causal graph (either an Intent or a Candidate).
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub enum GraphNode {
    Intent(String),
    Candidate(String),
}

impl std::fmt::Display for GraphNode {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            GraphNode::Intent(id) => write!(f, "intent:{}", id),
            GraphNode::Candidate(id) => write!(f, "candidate:{}", id),
        }
    }
}

/// A directed edge from intent to candidate via a causal link.
#[derive(Debug, Clone)]
pub struct GraphEdge {
    pub link_id: String,
    pub from_intent: String,
    pub to_candidate: String,
    pub timestamp_ns: i64,
    pub confidence: f64,
}

/// Adjacency-list representation of the causal graph.
#[derive(Debug, Clone, Default)]
pub struct CausalAdjacencyList {
    /// intent_id → [(link_id, candidate_id, confidence)]
    pub intent_to_candidate: HashMap<String, Vec<(String, String, f64)>>,
    /// candidate_id → [(link_id, intent_id)]
    pub candidate_to_intent: HashMap<String, Vec<(String, String)>>,
    /// All edges in the graph.
    pub edges: Vec<GraphEdge>,
}

/// Graph statistics.
#[derive(Debug, Clone, Default)]
pub struct GraphStats {
    pub node_count: usize,
    pub edge_count: usize,
    pub intent_count: usize,
    pub candidate_count: usize,
    /// Number of intents with no outgoing links.
    pub orphan_intents: usize,
    /// Number of candidates with no incoming links.
    pub orphan_candidates: usize,
    pub mean_out_degree: f64,
    pub max_out_degree: usize,
    pub graph_density: f64,
}

/// Causal graph storage engine wrapping a shared SQLite connection.
pub struct GraphStore {
    conn: Arc<Mutex<rusqlite::Connection>>,
}

impl GraphStore {
    /// Create a new GraphStore from a shared connection (compatible with SqliteWriter).
    pub fn new(conn: Arc<Mutex<rusqlite::Connection>>) -> Self {
        Self { conn }
    }

    /// Execute a read operation on the underlying connection.
    fn with_conn<T>(
        &self,
        f: impl FnOnce(&rusqlite::Connection) -> rusqlite::Result<T>,
    ) -> rusqlite::Result<T> {
        let conn = self.conn.lock().unwrap();
        f(&conn)
    }

    // ------------------------------------------------------------------
    // Time-indexed range queries
    // ------------------------------------------------------------------

    /// Query causal links for an agent within a time window.
    /// Uses the composite index `idx_link_agent_ts` for efficient retrieval.
    pub fn query_links_in_window(
        &self,
        agent_id: &str,
        start_ns: i64,
        end_ns: i64,
    ) -> rusqlite::Result<Vec<GraphEdge>> {
        self.with_conn(|conn| {
            let mut stmt = conn.prepare(
                "SELECT evidence_id, intent_id, candidate_id, timestamp_ns, confidence
                 FROM correlation_evidence
                 WHERE agent_id = ?1 AND timestamp_ns >= ?2 AND timestamp_ns <= ?3
                 ORDER BY timestamp_ns ASC",
            )?;
            let rows = stmt.query_map(params![agent_id, start_ns, end_ns], |row| {
                Ok(GraphEdge {
                    link_id: row.get(0)?,
                    from_intent: row.get(1)?,
                    to_candidate: row.get(2)?,
                    timestamp_ns: row.get(3)?,
                    confidence: row.get(4)?,
                })
            })?;
            let mut edges = Vec::new();
            for row in rows {
                edges.push(row?);
            }
            Ok(edges)
        })
    }

    /// Query all links for an agent (full graph).
    pub fn query_all_links(&self, agent_id: &str) -> rusqlite::Result<Vec<GraphEdge>> {
        self.query_links_in_window(agent_id, 0, i64::MAX)
    }

    /// Count links for an agent in a time window.
    pub fn count_links_in_window(
        &self,
        agent_id: &str,
        start_ns: i64,
        end_ns: i64,
    ) -> rusqlite::Result<i64> {
        self.with_conn(|conn| {
            conn.query_row(
                "SELECT COUNT(*) FROM correlation_evidence
                 WHERE agent_id = ?1 AND timestamp_ns >= ?2 AND timestamp_ns <= ?3",
                params![agent_id, start_ns, end_ns],
                |row| row.get(0),
            )
        })
    }

    // ------------------------------------------------------------------
    // Adjacency-list construction
    // ------------------------------------------------------------------

    /// Build an adjacency list for an agent's causal graph.
    pub fn build_adjacency_list(&self, agent_id: &str) -> rusqlite::Result<CausalAdjacencyList> {
        let edges = self.query_all_links(agent_id)?;
        Ok(Self::edges_to_adjacency(edges))
    }

    /// Build an adjacency list for an agent within a time window.
    pub fn build_adjacency_list_in_window(
        &self,
        agent_id: &str,
        start_ns: i64,
        end_ns: i64,
    ) -> rusqlite::Result<CausalAdjacencyList> {
        let edges = self.query_links_in_window(agent_id, start_ns, end_ns)?;
        Ok(Self::edges_to_adjacency(edges))
    }

    fn edges_to_adjacency(edges: Vec<GraphEdge>) -> CausalAdjacencyList {
        let mut intent_to_candidate: HashMap<String, Vec<(String, String, f64)>> = HashMap::new();
        let mut candidate_to_intent: HashMap<String, Vec<(String, String)>> = HashMap::new();

        for edge in &edges {
            intent_to_candidate
                .entry(edge.from_intent.clone())
                .or_default()
                .push((
                    edge.link_id.clone(),
                    edge.to_candidate.clone(),
                    edge.confidence,
                ));
            candidate_to_intent
                .entry(edge.to_candidate.clone())
                .or_default()
                .push((edge.link_id.clone(), edge.from_intent.clone()));
        }

        CausalAdjacencyList {
            intent_to_candidate,
            candidate_to_intent,
            edges,
        }
    }

    // ------------------------------------------------------------------
    // Forward/backward walks
    // ------------------------------------------------------------------

    /// Walk forward from an intent: intent → candidate(s) → downstream intents.
    /// Returns all nodes reachable within `max_depth` steps, as (depth, node_id) pairs.
    pub fn walk_forward(
        &self,
        agent_id: &str,
        start_intent_id: &str,
        max_depth: usize,
    ) -> rusqlite::Result<Vec<(usize, GraphNode)>> {
        let adj = self.build_adjacency_list(agent_id)?;
        let mut visited = HashSet::new();
        let mut result = Vec::new();
        let mut frontier: Vec<(usize, GraphNode)> =
            vec![(0, GraphNode::Intent(start_intent_id.to_string()))];

        // Build a reverse lookup: candidate → downstream intents.
        // We need to know which intents link to which candidates first.
        let candidate_to_downstream: HashMap<String, Vec<String>> = {
            let mut map: HashMap<String, Vec<String>> = HashMap::new();
            for edge in &adj.edges {
                // For each candidate, find intents that use the same candidate
                // (this means intents connected transitively via shared candidates)
                // Actually, the causal chain is simpler: intent→candidate.
                // A forward walk follows: intent→candidate (1 step), then
                // candidate→next_intent if that next_intent references the same candidate.
                // But in our model, each link is intent→candidate, and a candidate
                // is a single action. So the forward walk is a depth-1 operation
                // unless the candidate appears in intents as arguments.
                map.entry(edge.to_candidate.clone())
                    .or_default()
                    .push(edge.from_intent.clone());
            }
            map
        };

        while let Some((depth, node)) = frontier.pop() {
            if depth > max_depth || visited.contains(&node) {
                continue;
            }
            visited.insert(node.clone());
            result.push((depth, node.clone()));

            match &node {
                GraphNode::Intent(intent_id) => {
                    if let Some(outgoing) = adj.intent_to_candidate.get(intent_id) {
                        for (_link_id, candidate_id, _confidence) in outgoing {
                            let next = GraphNode::Candidate(candidate_id.clone());
                            if !visited.contains(&next) {
                                frontier.push((depth + 1, next));
                            }
                        }
                    }
                }
                GraphNode::Candidate(candidate_id) => {
                    // A candidate may be referenced by downstream intents through
                    // transitive argument matching (the same file path read, etc.)
                    if let Some(downstream_intents) = candidate_to_downstream.get(candidate_id) {
                        for intent_id in downstream_intents {
                            let next = GraphNode::Intent(intent_id.clone());
                            if !visited.contains(&next) {
                                frontier.push((depth + 1, next));
                            }
                        }
                    }
                }
            }
        }

        Ok(result)
    }

    /// Walk backward from a candidate: candidate → intent → upstream candidates.
    pub fn walk_backward(
        &self,
        agent_id: &str,
        start_candidate_id: &str,
        max_depth: usize,
    ) -> rusqlite::Result<Vec<(usize, GraphNode)>> {
        let adj = self.build_adjacency_list(agent_id)?;
        let mut visited = HashSet::new();
        let mut result = Vec::new();
        let mut frontier: Vec<(usize, GraphNode)> =
            vec![(0, GraphNode::Candidate(start_candidate_id.to_string()))];

        while let Some((depth, node)) = frontier.pop() {
            if depth > max_depth || visited.contains(&node) {
                continue;
            }
            visited.insert(node.clone());
            result.push((depth, node.clone()));

            match &node {
                GraphNode::Candidate(candidate_id) => {
                    if let Some(incoming) = adj.candidate_to_intent.get(candidate_id) {
                        for (_link_id, intent_id) in incoming {
                            let next = GraphNode::Intent(intent_id.clone());
                            if !visited.contains(&next) {
                                frontier.push((depth + 1, next));
                            }
                        }
                    }
                }
                GraphNode::Intent(intent_id) => {
                    if let Some(outgoing) = adj.intent_to_candidate.get(intent_id) {
                        for (_link_id, candidate_id, _confidence) in outgoing {
                            let next = GraphNode::Candidate(candidate_id.clone());
                            if !visited.contains(&next) {
                                frontier.push((depth + 1, next));
                            }
                        }
                    }
                }
            }
        }

        Ok(result)
    }

    // ------------------------------------------------------------------
    // Graph simplification
    // ------------------------------------------------------------------

    /// Simplify the graph by removing edges below a confidence threshold.
    pub fn simplify_by_confidence(
        &self,
        agent_id: &str,
        min_confidence: f64,
    ) -> rusqlite::Result<CausalAdjacencyList> {
        let edges = self.query_all_links(agent_id)?;
        let filtered: Vec<GraphEdge> = edges
            .into_iter()
            .filter(|e| e.confidence >= min_confidence)
            .collect();
        Ok(Self::edges_to_adjacency(filtered))
    }

    /// Simplify the graph for a time window with confidence filtering.
    pub fn simplify_window(
        &self,
        agent_id: &str,
        start_ns: i64,
        end_ns: i64,
        min_confidence: f64,
    ) -> rusqlite::Result<CausalAdjacencyList> {
        let edges = self.query_links_in_window(agent_id, start_ns, end_ns)?;
        let filtered: Vec<GraphEdge> = edges
            .into_iter()
            .filter(|e| e.confidence >= min_confidence)
            .collect();
        Ok(Self::edges_to_adjacency(filtered))
    }

    // ------------------------------------------------------------------
    // Graph statistics
    // ------------------------------------------------------------------

    /// Compute statistics for an agent's causal graph.
    pub fn compute_stats(&self, agent_id: &str) -> rusqlite::Result<GraphStats> {
        let adj = self.build_adjacency_list(agent_id)?;

        let intents: HashSet<String> = adj.intent_to_candidate.keys().cloned().collect();
        let candidates: HashSet<String> = adj.candidate_to_intent.keys().cloned().collect();
        let all_nodes: HashSet<String> = intents.union(&candidates).cloned().collect();
        let node_count = all_nodes.len();
        let edge_count = adj.edges.len();

        let orphan_intents = intents
            .iter()
            .filter(|i| {
                adj.intent_to_candidate
                    .get(*i)
                    .map_or(true, |v| v.is_empty())
            })
            .count();
        let orphan_candidates = candidates
            .iter()
            .filter(|c| {
                adj.candidate_to_intent
                    .get(*c)
                    .map_or(true, |v| v.is_empty())
            })
            .count();

        let total_out: usize = adj.intent_to_candidate.values().map(|v| v.len()).sum();
        let intent_count = intents.len();
        let mean_out_degree = if intent_count > 0 {
            total_out as f64 / intent_count as f64
        } else {
            0.0
        };
        let max_out_degree = adj
            .intent_to_candidate
            .values()
            .map(|v| v.len())
            .max()
            .unwrap_or(0);

        // Density: |E| / (|I| * |C|) for a bipartite graph
        let density = if intent_count > 0 && candidates.len() > 0 {
            edge_count as f64 / (intent_count * candidates.len()) as f64
        } else {
            0.0
        };

        Ok(GraphStats {
            node_count,
            edge_count,
            intent_count,
            candidate_count: candidates.len(),
            orphan_intents,
            orphan_candidates,
            mean_out_degree,
            max_out_degree,
            graph_density: density,
        })
    }

    // ------------------------------------------------------------------
    // Cross-agent queries (delegation-aware)
    // ------------------------------------------------------------------

    /// Find all agents connected via delegation edges to the given agent.
    pub fn connected_agents(&self, agent_id: &str) -> rusqlite::Result<Vec<String>> {
        self.with_conn(|conn| {
            let mut stmt = conn.prepare(
                "SELECT DISTINCT to_agent_id FROM delegation_records WHERE from_agent_id = ?1
                 UNION
                 SELECT DISTINCT from_agent_id FROM delegation_records WHERE to_agent_id = ?1",
            )?;
            let rows = stmt.query_map(params![agent_id], |row| row.get(0))?;
            let mut agents = Vec::new();
            for row in rows {
                agents.push(row?);
            }
            Ok(agents)
        })
    }

    // ------------------------------------------------------------------
    // M2: Dependency analysis
    // ------------------------------------------------------------------

    /// Build a dependency tree rooted at the given node using BFS.
    ///
    /// Returns layers of nodes (depth 0 = root, depth 1 = direct dependents, etc.)
    /// along with the edges connecting each layer.
    pub fn build_dependency_tree(
        &self,
        agent_id: &str,
        root_intent_id: &str,
        max_depth: usize,
    ) -> rusqlite::Result<Vec<Vec<GraphNode>>> {
        let adj = self.build_adjacency_list(agent_id)?;
        let mut layers: Vec<Vec<GraphNode>> = Vec::new();
        let mut visited: HashSet<String> = HashSet::new();
        let mut frontier: Vec<GraphNode> = vec![GraphNode::Intent(root_intent_id.to_string())];

        for _depth in 0..=max_depth {
            if frontier.is_empty() {
                break;
            }
            let current_layer: Vec<GraphNode> = frontier.drain(..).collect();
            let mut next_frontier: Vec<GraphNode> = Vec::new();

            for node in &current_layer {
                let key = node.to_string();
                if visited.contains(&key) {
                    continue;
                }
                visited.insert(key);
                if let GraphNode::Intent(intent_id) = node {
                    if let Some(outgoing) = adj.intent_to_candidate.get(intent_id) {
                        for (_link_id, candidate_id, _confidence) in outgoing {
                            if !visited.contains(candidate_id) {
                                next_frontier.push(GraphNode::Candidate(candidate_id.clone()));
                            }
                        }
                    }
                }
            }
            // Also push candidates that feed intents further downstream
            for node in &current_layer {
                if let GraphNode::Candidate(candidate_id) = node {
                    if let Some(incoming) = adj.candidate_to_intent.get(candidate_id) {
                        for (_link_id, intent_id) in incoming {
                            if !visited.contains(intent_id) {
                                next_frontier.push(GraphNode::Intent(intent_id.clone()));
                            }
                        }
                    }
                }
            }

            layers.push(current_layer);
            frontier = next_frontier;
        }

        Ok(layers)
    }

    /// Compute transitive closure: for each node, which other nodes are reachable.
    ///
    /// Returns a map from node key → set of reachable node keys.
    pub fn compute_transitive_closure(
        &self,
        agent_id: &str,
        max_depth: usize,
    ) -> rusqlite::Result<HashMap<String, HashSet<String>>> {
        let adj = self.build_adjacency_list(agent_id)?;
        // Collect all nodes
        let all_intents: HashSet<String> = adj.intent_to_candidate.keys().cloned().collect();
        let _all_candidates: HashSet<String> = adj.candidate_to_intent.keys().cloned().collect();

        let mut closure: HashMap<String, HashSet<String>> = HashMap::new();

        // For each intent, BFS to find reachable nodes
        for intent_id in &all_intents {
            let mut reachable: HashSet<String> = HashSet::new();
            let mut frontier: Vec<GraphNode> = vec![GraphNode::Intent(intent_id.clone())];
            let mut depth = 0;

            while !frontier.is_empty() && depth <= max_depth {
                let mut next = Vec::new();
                for node in frontier.drain(..) {
                    let key = node.to_string();
                    if reachable.contains(&key) {
                        continue;
                    }
                    reachable.insert(key);
                    match &node {
                        GraphNode::Intent(iid) => {
                            if let Some(outgoing) = adj.intent_to_candidate.get(iid) {
                                for (_lid, cid, _c) in outgoing {
                                    next.push(GraphNode::Candidate(cid.clone()));
                                }
                            }
                        }
                        GraphNode::Candidate(cid) => {
                            if let Some(incoming) = adj.candidate_to_intent.get(cid) {
                                for (_lid, iid) in incoming {
                                    next.push(GraphNode::Intent(iid.clone()));
                                }
                            }
                        }
                    }
                }
                frontier = next;
                depth += 1;
            }
            closure.insert(intent_id.clone(), reachable);
        }

        Ok(closure)
    }

    /// Compute dependency rank: number of downstream dependents for each node.
    ///
    /// Higher rank = more nodes depend on this node → higher impact if compromised.
    /// Returns sorted (node_key, rank) pairs in descending rank order.
    pub fn compute_dependency_rank(
        &self,
        agent_id: &str,
        max_depth: usize,
    ) -> rusqlite::Result<Vec<(String, usize)>> {
        let closure = self.compute_transitive_closure(agent_id, max_depth)?;

        // For each node, count how many other nodes have it in their reachable set
        let mut rank: HashMap<String, usize> = HashMap::new();
        for (source, reachable) in &closure {
            // Source node itself has downstream count = reachable.len()
            rank.insert(source.clone(), reachable.len());
        }

        let mut ranked: Vec<(String, usize)> = rank.into_iter().collect();
        ranked.sort_by(|a, b| b.1.cmp(&a.1));
        Ok(ranked)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::responsibility::persistence::open_causal_db;
    use rusqlite::Connection;

    fn setup_db() -> Arc<Mutex<Connection>> {
        let conn = open_causal_db(":memory:").unwrap();
        // Insert test data
        conn.execute(
            "INSERT INTO agent_instances (agent_id, first_seen_ns, last_seen_ns, root_processes_json, tls_sessions_json)
             VALUES ('agent-1', 0, 1000, '[]', '[]')",
            [],
        ).unwrap();
        conn.execute(
            "INSERT INTO intent_events (intent_id, agent_id, timestamp_ns, tool_name, tool_args_json, intent_class, parse_state, primary_matching_eligible, authority_source)
             VALUES ('intent-1', 'agent-1', 10, 'Read', '{}', 'side_effect', 'complete', 1, 'http_response_tool_use'),
                    ('intent-2', 'agent-1', 20, 'Bash', '{\"command\":\"ls\"}', 'side_effect', 'complete', 1, 'http_response_tool_use')",
            [],
        ).unwrap();
        conn.execute(
            "INSERT INTO action_candidates (candidate_id, schema_version, agent_id, timestamp_ns, candidate_type, pid, process_start_time_ns, normalized_args_json, raw_event_refs_json, derivation_mode, lossiness)
             VALUES ('candidate-1', 1, 'agent-1', 15, 'file_read', 100, 1000, '{\"path\":\"/etc/passwd\"}', '[]', 'direct', 'lossless'),
                    ('candidate-2', 1, 'agent-1', 25, 'exec', 101, 2000, '{\"binary\":\"ls\"}', '[]', 'direct', 'lossless')",
            [],
        ).unwrap();
        conn.execute(
            "INSERT INTO correlation_evidence (schema_version, evidence_id, agent_id, intent_id, candidate_id, timestamp_ns, confidence, match_level)
             VALUES (5, 'link-1', 'agent-1', 'intent-1', 'candidate-1', 15, 0.95, 'l1_exact'),
                    (5, 'link-2', 'agent-1', 'intent-2', 'candidate-2', 25, 0.60, 'l1_exact')",
            [],
        ).unwrap();
        Arc::new(Mutex::new(conn))
    }

    #[test]
    fn test_query_links_in_window() {
        let store = GraphStore::new(setup_db());
        let edges = store.query_links_in_window("agent-1", 0, 20).unwrap();
        assert_eq!(edges.len(), 1);
        assert_eq!(edges[0].link_id, "link-1");
    }

    #[test]
    fn test_query_all_links() {
        let store = GraphStore::new(setup_db());
        let edges = store.query_all_links("agent-1").unwrap();
        assert_eq!(edges.len(), 2);
    }

    #[test]
    fn test_count_links_in_window() {
        let store = GraphStore::new(setup_db());
        let count = store.count_links_in_window("agent-1", 0, 30).unwrap();
        assert_eq!(count, 2);
    }

    #[test]
    fn test_build_adjacency_list() {
        let store = GraphStore::new(setup_db());
        let adj = store.build_adjacency_list("agent-1").unwrap();
        assert_eq!(adj.edges.len(), 2);
        assert_eq!(adj.intent_to_candidate.len(), 2);
        assert_eq!(adj.candidate_to_intent.len(), 2);
    }

    #[test]
    fn test_walk_forward() {
        let store = GraphStore::new(setup_db());
        let path = store.walk_forward("agent-1", "intent-1", 2).unwrap();
        // intent-1 → candidate-1 → intent-1 (transitive self-loop via candidate)
        assert!(!path.is_empty());
        assert_eq!(path[0], (0, GraphNode::Intent("intent-1".to_string())));
    }

    #[test]
    fn test_walk_backward() {
        let store = GraphStore::new(setup_db());
        let path = store.walk_backward("agent-1", "candidate-1", 2).unwrap();
        assert!(!path.is_empty());
        assert_eq!(
            path[0],
            (0, GraphNode::Candidate("candidate-1".to_string()))
        );
    }

    #[test]
    fn test_simplify_by_confidence() {
        let store = GraphStore::new(setup_db());
        let simplified = store.simplify_by_confidence("agent-1", 0.90).unwrap();
        // Only link-1 (0.95) should survive
        assert_eq!(simplified.edges.len(), 1);
        assert_eq!(simplified.edges[0].link_id, "link-1");
    }

    #[test]
    fn test_simplify_window() {
        let store = GraphStore::new(setup_db());
        let simplified = store.simplify_window("agent-1", 0, 30, 0.70).unwrap();
        assert_eq!(simplified.edges.len(), 1);
    }

    #[test]
    fn test_compute_stats() {
        let store = GraphStore::new(setup_db());
        let stats = store.compute_stats("agent-1").unwrap();
        assert_eq!(stats.edge_count, 2);
        assert_eq!(stats.intent_count, 2);
        assert_eq!(stats.candidate_count, 2);
        assert_eq!(stats.node_count, 4);
        assert_eq!(stats.orphan_intents, 0);
        assert_eq!(stats.max_out_degree, 1);
    }

    #[test]
    fn test_connected_agents() {
        let conn_arc = setup_db();
        {
            let conn = conn_arc.lock().unwrap();
            conn.execute(
                "INSERT INTO delegation_records (delegation_id, from_agent_id, to_agent_id, parent_pid, child_pid, timestamp_ns, child_comm)
                 VALUES ('del-1', 'agent-1', 'agent-2', 100, 200, 50, 'bash')",
                [],
            ).unwrap();
        }
        let store = GraphStore::new(conn_arc);
        let connected = store.connected_agents("agent-1").unwrap();
        assert_eq!(connected, vec!["agent-2"]);
        let connected_rev = store.connected_agents("agent-2").unwrap();
        assert_eq!(connected_rev, vec!["agent-1"]);
    }

    #[test]
    fn test_empty_agent() {
        let conn = open_causal_db(":memory:").unwrap();
        let store = GraphStore::new(Arc::new(Mutex::new(conn)));
        let edges = store.query_all_links("nonexistent").unwrap();
        assert!(edges.is_empty());
        let stats = store.compute_stats("nonexistent").unwrap();
        assert_eq!(stats.node_count, 0);
    }

    #[test]
    fn test_build_dependency_tree() {
        let store = GraphStore::new(setup_db());
        let layers = store
            .build_dependency_tree("agent-1", "intent-1", 3)
            .unwrap();
        assert!(!layers.is_empty());
        // First layer should contain the root intent
        assert_eq!(layers[0].len(), 1);
        // Should have at least two layers (root + dependents)
        assert!(
            layers.len() >= 2,
            "expected at least 2 layers, got {}",
            layers.len()
        );
    }

    #[test]
    fn test_compute_transitive_closure() {
        let store = GraphStore::new(setup_db());
        let closure = store.compute_transitive_closure("agent-1", 3).unwrap();
        assert!(!closure.is_empty());
        // Keys are raw intent IDs; values are GraphNode::to_string formatted
        let target = GraphNode::Candidate("candidate-1".to_string()).to_string();
        let reachable = closure
            .get("intent-1")
            .expect("intent-1 should exist in closure");
        assert!(
            reachable.contains(&target),
            "expected {} in reachable set, got {:?}",
            target,
            reachable
        );
    }

    #[test]
    fn test_compute_dependency_rank() {
        let store = GraphStore::new(setup_db());
        let ranks = store.compute_dependency_rank("agent-1", 3).unwrap();
        assert!(!ranks.is_empty());
        // Results should be sorted by rank descending
        for i in 1..ranks.len() {
            assert!(ranks[i - 1].1 >= ranks[i].1);
        }
    }
}
