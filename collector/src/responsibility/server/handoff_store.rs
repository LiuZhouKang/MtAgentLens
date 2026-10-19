// SPDX-License-Identifier: Apache-2.0 OR MIT
// Handoff Observability — in-memory data store for delegation tree, events,
// metrics, and dependency edges.
//
// This store is shared across all HTTP handlers via Arc<RwLock<>> and is
// populated by handoff-related SSE events or analytics pipeline stages.

use serde::{Deserialize, Serialize};
use std::sync::Arc;
use tokio::sync::RwLock;

// ═══════════════════════════════════════════════════════════════════════════
// Data Types (matching HANDOFF_API_CONTRACT.md v3.1 exactly)
// ═══════════════════════════════════════════════════════════════════════════

/// Delegation tree node — returned as nested structure from the REST API.
///
/// # Fields
/// - `children`: Nested child nodes, populated by the store's tree builder.
/// - `child_ids`: Flat list of child IDs for frontend alternative tree
///   construction.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DelegationNode {
    pub node_id: String,
    pub agent_id: String,
    pub parent_node_id: Option<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub children: Vec<DelegationNode>,
    pub chain_depth: u32,
    pub spawned_at_ms: u64,
    pub completed_at_ms: Option<u64>,
    pub bytes_in: u64,
    pub bytes_out: u64,
    pub token_count: u64,
    pub context_overhead_ratio: f64,
    pub idle_breakdown: IdleTimeBreakdown,
    pub model_assigned: Option<String>,
    pub model_optimal: Option<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub child_ids: Vec<String>,
}

/// Idle time attribution per lifecycle phase.
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct IdleTimeBreakdown {
    pub queued_awaiting_dispatch_ms: u64,
    pub context_injection_io_ms: u64,
    pub model_inference_queueing_ms: u64,
    pub tool_call_round_trip_ms: u64,
}

/// Aggregated handoff observability metrics.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HandoffObsMetrics {
    pub total_spawned: u64,
    pub total_completed: u64,
    pub active_count: u64,
    pub max_chain_depth: u64,
    pub fan_out_ratio: f64,
    pub fan_out_critical: bool,
    pub avg_context_overhead: f64,
    pub orphaned_artifacts: u64,
    pub workspace_tag_survival_rate: f64,
}

impl Default for HandoffObsMetrics {
    fn default() -> Self {
        Self {
            total_spawned: 0,
            total_completed: 0,
            active_count: 0,
            max_chain_depth: 0,
            fan_out_ratio: 0.0,
            fan_out_critical: false,
            avg_context_overhead: 0.0,
            orphaned_artifacts: 0,
            workspace_tag_survival_rate: 1.0,
        }
    }
}

/// Cross-agent dependency edge used for orphaned-work detection.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DependencyEdge {
    pub edge_id: String,
    pub from_spawn_id: String,
    pub to_spawn_id: String,
    pub artifact_path: String,
    pub is_orphaned: bool,
    pub idle_ms: u64,
    pub producer_agent_id: String,
    pub consumer_agent_id: Option<String>,
}

/// Delegation tree response envelope.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DelegationTreeResponse {
    pub tree: Option<DelegationNode>,
    pub tree_id: String,
    pub built_at_ms: u64,
    pub metrics: HandoffObsMetrics,
}

/// Handoff events response envelope.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HandoffEventsResponse {
    pub events: Vec<serde_json::Value>,
    pub total: usize,
    pub has_more: bool,
}

/// Dependency edges response.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DependencyEdgesResponse {
    pub edges: Vec<DependencyEdge>,
}

/// Handoff probe request body.
#[derive(Debug, Clone, Deserialize)]
pub struct HandoffProbeRequest {
    pub scope: String, // "probes" | "stream" | "runtime" | "all"
    pub pid: Option<u32>,
    pub port: Option<u16>,
}

/// Handoff probe response.
#[derive(Debug, Clone, Serialize)]
pub struct HandoffProbeResponse {
    pub run_id: String,
    pub started_at_ms: u64,
}

// ═══════════════════════════════════════════════════════════════════════════
// In-memory Store
// ═══════════════════════════════════════════════════════════════════════════

/// Thread-safe shared handoff data store.
#[derive(Debug, Clone)]
pub struct HandoffStoreData {
    /// Per-node map for fast lookup and tree construction.
    nodes: std::collections::HashMap<String, DelegationNode>,
    /// Root node ID (None if no nodes have been spawned yet).
    root_node_id: Option<String>,
    /// Monotonic tree rebuild counter.
    tree_id: String,
    /// Timestamp of last tree rebuild (epoch ms).
    built_at_ms: u64,
    /// Historical handoff observability events (as JSON values to support all sub_types).
    events: Vec<serde_json::Value>,
    /// Dependency edges for orphaned-work detection.
    dependency_edges: Vec<DependencyEdge>,
    /// Cached metrics (recomputed on tree mutation).
    metrics: HandoffObsMetrics,
    /// Maximum events kept in memory.
    max_events: usize,
}

impl Default for HandoffStoreData {
    fn default() -> Self {
        Self::new()
    }
}

impl HandoffStoreData {
    pub fn new() -> Self {
        Self {
            nodes: std::collections::HashMap::new(),
            root_node_id: None,
            tree_id: String::new(),
            built_at_ms: 0,
            events: Vec::new(),
            dependency_edges: Vec::new(),
            metrics: HandoffObsMetrics::default(),
            max_events: 10000,
        }
    }

    /// Add or update a delegation node.
    pub fn upsert_node(&mut self, node: DelegationNode) {
        let is_root = node.parent_node_id.is_none();
        let node_id = node.node_id.clone();
        let parent_id = node.parent_node_id.clone();

        self.nodes.insert(node_id.clone(), node);

        if is_root {
            self.root_node_id = Some(node_id.clone());
        }

        // Maintain child_ids consistency on parent
        if let Some(ref pid) = parent_id
            && let Some(parent) = self.nodes.get_mut(pid)
            && !parent.child_ids.contains(&node_id)
        {
            parent.child_ids.push(node_id.clone());
        }

        self.rebuild_tree();
    }

    /// Remove a node and all descendants.
    pub fn remove_node(&mut self, node_id: &str) {
        let mut to_remove = vec![node_id.to_string()];
        let mut i = 0;
        while i < to_remove.len() {
            if let Some(node) = self.nodes.get(&to_remove[i]) {
                for child_id in &node.child_ids {
                    to_remove.push(child_id.clone());
                }
            }
            i += 1;
        }
        for id in &to_remove {
            self.nodes.remove(id);
        }
        if self.root_node_id.as_deref() == Some(node_id) {
            self.root_node_id = None;
        }
        self.rebuild_tree();
    }

    /// Append a handoff observability event.
    pub fn push_event(&mut self, event: serde_json::Value) {
        self.events.push(event);
        if self.events.len() > self.max_events {
            self.events.remove(0);
        }
    }

    /// Add or update a dependency edge.
    pub fn upsert_edge(&mut self, edge: DependencyEdge) {
        if let Some(existing) = self
            .dependency_edges
            .iter_mut()
            .find(|e| e.edge_id == edge.edge_id)
        {
            *existing = edge;
        } else {
            self.dependency_edges.push(edge);
        }
    }

    /// Rebuild the tree ID, metrics, and nesting structure.
    fn rebuild_tree(&mut self) {
        self.tree_id = format!("tree-{}", uuid::Uuid::new_v4());
        self.built_at_ms = epoch_ms();
        self.metrics = compute_metrics(&self.nodes, &self.dependency_edges);
    }

    /// Get the root delegation tree with nested children.
    pub fn get_tree(&self) -> Option<DelegationNode> {
        let root_id = self.root_node_id.as_ref()?;
        self.build_nested(root_id)
    }

    /// Recursively build nested children for a node.
    fn build_nested(&self, node_id: &str) -> Option<DelegationNode> {
        let node = self.nodes.get(node_id)?.clone();
        let children: Vec<DelegationNode> = node
            .child_ids
            .iter()
            .filter_map(|cid| self.build_nested(cid))
            .collect();
        Some(DelegationNode { children, ..node })
    }

    pub fn get_tree_response(&self) -> DelegationTreeResponse {
        DelegationTreeResponse {
            tree: self.get_tree(),
            tree_id: self.tree_id.clone(),
            built_at_ms: self.built_at_ms,
            metrics: self.metrics.clone(),
        }
    }

    pub fn get_metrics(&self) -> HandoffObsMetrics {
        self.metrics.clone()
    }

    pub fn get_events(
        &self,
        limit: usize,
        since_ms: u64,
        sub_type: Option<&str>,
    ) -> HandoffEventsResponse {
        let limit = limit.min(500);
        let filtered: Vec<&serde_json::Value> = self
            .events
            .iter()
            .filter(|evt| {
                if since_ms > 0 {
                    let ts = evt
                        .get("timestamp_ms")
                        .or_else(|| evt.get("dispatched_at_ms"))
                        .and_then(|v| v.as_u64())
                        .unwrap_or(0);
                    if ts < since_ms {
                        return false;
                    }
                }
                if let Some(st) = sub_type {
                    let evt_sub_type = evt.get("sub_type").and_then(|v| v.as_str()).unwrap_or("");
                    if evt_sub_type != st {
                        return false;
                    }
                }
                true
            })
            .collect();

        let total = filtered.len();
        let has_more = total > limit;
        let events: Vec<serde_json::Value> = filtered.into_iter().take(limit).cloned().collect();

        HandoffEventsResponse {
            events,
            total,
            has_more,
        }
    }

    pub fn get_event_by_id(&self, event_id: &str) -> Option<serde_json::Value> {
        self.events
            .iter()
            .find(|evt| {
                evt.get("spawn_id")
                    .or_else(|| evt.get("alert_id"))
                    .or_else(|| evt.get("access_id"))
                    .or_else(|| evt.get("correlation_id"))
                    .or_else(|| evt.get("denial_id"))
                    .and_then(|v| v.as_str())
                    == Some(event_id)
            })
            .cloned()
    }

    pub fn get_dependency_edges(&self) -> DependencyEdgesResponse {
        DependencyEdgesResponse {
            edges: self.dependency_edges.clone(),
        }
    }

    /// Seed with demo data for development/testing.
    pub fn seed_demo_data(&mut self) {
        // Root agent
        let root = DelegationNode {
            node_id: "agent-root".into(),
            agent_id: "root-agent".into(),
            parent_node_id: None,
            children: vec![],
            chain_depth: 0,
            spawned_at_ms: epoch_ms() - 60000,
            completed_at_ms: None,
            bytes_in: 15240,
            bytes_out: 89320,
            token_count: 2341,
            context_overhead_ratio: 0.35,
            idle_breakdown: IdleTimeBreakdown::default(),
            model_assigned: Some("claude-sonnet-4-6".into()),
            model_optimal: Some("claude-sonnet-4-6".into()),
            child_ids: vec!["agent-planner".into(), "agent-auditor".into()],
        };

        let planner = DelegationNode {
            node_id: "agent-planner".into(),
            agent_id: "planner-agent".into(),
            parent_node_id: Some("agent-root".into()),
            children: vec![],
            chain_depth: 1,
            spawned_at_ms: epoch_ms() - 45000,
            completed_at_ms: Some(epoch_ms() - 12000),
            bytes_in: 32400,
            bytes_out: 45600,
            token_count: 1230,
            context_overhead_ratio: 0.58,
            idle_breakdown: IdleTimeBreakdown {
                queued_awaiting_dispatch_ms: 320,
                context_injection_io_ms: 1450,
                model_inference_queueing_ms: 8200,
                tool_call_round_trip_ms: 3400,
            },
            model_assigned: Some("claude-sonnet-4-6".into()),
            model_optimal: Some("claude-sonnet-4-6".into()),
            child_ids: vec!["agent-writer".into(), "agent-tester".into()],
        };

        let auditor = DelegationNode {
            node_id: "agent-auditor".into(),
            agent_id: "security-auditor".into(),
            parent_node_id: Some("agent-root".into()),
            children: vec![],
            chain_depth: 1,
            spawned_at_ms: epoch_ms() - 40000,
            completed_at_ms: None,
            bytes_in: 12800,
            bytes_out: 2400,
            token_count: 87,
            context_overhead_ratio: 0.12,
            idle_breakdown: IdleTimeBreakdown::default(),
            model_assigned: Some("claude-haiku-4-5".into()),
            model_optimal: Some("claude-sonnet-4-6".into()),
            child_ids: vec![],
        };

        let writer = DelegationNode {
            node_id: "agent-writer".into(),
            agent_id: "writer-agent".into(),
            parent_node_id: Some("agent-planner".into()),
            children: vec![],
            chain_depth: 2,
            spawned_at_ms: epoch_ms() - 35000,
            completed_at_ms: Some(epoch_ms() - 5000),
            bytes_in: 28000,
            bytes_out: 62000,
            token_count: 1890,
            context_overhead_ratio: 0.72,
            idle_breakdown: IdleTimeBreakdown {
                queued_awaiting_dispatch_ms: 500,
                context_injection_io_ms: 2100,
                model_inference_queueing_ms: 12400,
                tool_call_round_trip_ms: 5600,
            },
            model_assigned: Some("claude-sonnet-4-6".into()),
            model_optimal: Some("claude-sonnet-4-6".into()),
            child_ids: vec![],
        };

        let tester = DelegationNode {
            node_id: "agent-tester".into(),
            agent_id: "tester-agent".into(),
            parent_node_id: Some("agent-planner".into()),
            children: vec![],
            chain_depth: 2,
            spawned_at_ms: epoch_ms() - 30000,
            completed_at_ms: None,
            bytes_in: 15000,
            bytes_out: 8900,
            token_count: 430,
            context_overhead_ratio: 0.45,
            idle_breakdown: IdleTimeBreakdown::default(),
            model_assigned: Some("claude-haiku-4-5".into()),
            model_optimal: Some("claude-haiku-4-5".into()),
            child_ids: vec![],
        };

        for node in [root, planner, auditor, writer, tester] {
            self.upsert_node(node);
        }
    }
}

// ═══════════════════════════════════════════════════════════════════════════
// Helpers
// ═══════════════════════════════════════════════════════════════════════════

/// Current wall-clock time as milliseconds since Unix epoch.
fn epoch_ms() -> u64 {
    use std::time::SystemTime;
    SystemTime::now()
        .duration_since(SystemTime::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64
}

/// Compute aggregated metrics from the node map and edges.
fn compute_metrics(
    nodes: &std::collections::HashMap<String, DelegationNode>,
    edges: &[DependencyEdge],
) -> HandoffObsMetrics {
    let total_spawned = nodes.len() as u64;
    let total_completed = nodes
        .values()
        .filter(|n| n.completed_at_ms.is_some())
        .count() as u64;
    let active_count = total_spawned.saturating_sub(total_completed);
    let max_chain_depth = nodes.values().map(|n| n.chain_depth).max().unwrap_or(0) as u64;
    let fan_out_ratio = if total_completed > 0 {
        total_spawned as f64 / total_completed as f64
    } else {
        total_spawned as f64
    };
    let (overhead_sum, overhead_count) = nodes.values().fold((0.0f64, 0u64), |(s, c), n| {
        (s + n.context_overhead_ratio, c + 1)
    });
    let avg_context_overhead = if overhead_count > 0 {
        overhead_sum / overhead_count as f64
    } else {
        0.0
    };
    let orphaned_artifacts = edges.iter().filter(|e| e.is_orphaned).count() as u64;

    HandoffObsMetrics {
        total_spawned,
        total_completed,
        active_count,
        max_chain_depth,
        fan_out_ratio,
        fan_out_critical: fan_out_ratio > 3.0,
        avg_context_overhead,
        orphaned_artifacts,
        workspace_tag_survival_rate: 1.0,
    }
}

/// Shared store type for use across handlers.
pub type SharedHandoffStore = Arc<RwLock<HandoffStoreData>>;

/// Create a new shared handoff store (optionally seeded with demo data).
///
/// When `seed_demo` is true, data is written via `blocking_write()`. Call
/// this only from synchronous contexts or at startup before the runtime spins up.
pub fn create_handoff_store(seed_demo: bool) -> SharedHandoffStore {
    if seed_demo {
        let mut data = HandoffStoreData::new();
        data.seed_demo_data();
        Arc::new(RwLock::new(data))
    } else {
        Arc::new(RwLock::new(HandoffStoreData::new()))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_empty_store() {
        let store = HandoffStoreData::new();
        let resp = store.get_tree_response();
        assert!(resp.tree.is_none());
        assert_eq!(resp.metrics.total_spawned, 0);
    }

    #[test]
    fn test_single_node() {
        let mut store = HandoffStoreData::new();
        store.upsert_node(DelegationNode {
            node_id: "n1".into(),
            agent_id: "a1".into(),
            parent_node_id: None,
            children: vec![],
            chain_depth: 0,
            spawned_at_ms: 1000,
            completed_at_ms: None,
            bytes_in: 100,
            bytes_out: 200,
            token_count: 50,
            context_overhead_ratio: 0.5,
            idle_breakdown: IdleTimeBreakdown::default(),
            model_assigned: None,
            model_optimal: None,
            child_ids: vec![],
        });
        let resp = store.get_tree_response();
        assert!(resp.tree.is_some());
        assert_eq!(resp.metrics.total_spawned, 1);
        assert_eq!(resp.metrics.active_count, 1);
    }

    #[test]
    fn test_demo_data() {
        let mut store = HandoffStoreData::new();
        store.seed_demo_data();
        let resp = store.get_tree_response();
        let tree = resp.tree.expect("demo tree should exist");
        assert_eq!(tree.chain_depth, 0);
        assert_eq!(tree.child_ids.len(), 2);
        assert!(resp.metrics.total_spawned >= 5);
    }

    #[test]
    fn test_events() {
        let mut store = HandoffStoreData::new();
        store.push_event(serde_json::json!({
            "sub_type": "HANDOFF_SPAWN",
            "spawn_id": "s1",
            "agent_id": "a1",
            "timestamp_ms": 1000,
        }));
        store.push_event(serde_json::json!({
            "sub_type": "HANDOFF_COMPLETE",
            "spawn_id": "s1",
            "timestamp_ms": 2000,
        }));

        let all = store.get_events(100, 0, None);
        assert_eq!(all.total, 2);

        let filtered = store.get_events(100, 0, Some("HANDOFF_SPAWN"));
        assert_eq!(filtered.total, 1);

        let since = store.get_events(100, 1500, None);
        assert_eq!(since.total, 1);

        let by_id = store.get_event_by_id("s1");
        assert!(by_id.is_some());
    }

    #[test]
    fn test_dependency_edges() {
        let mut store = HandoffStoreData::new();
        store.upsert_edge(DependencyEdge {
            edge_id: "e1".into(),
            from_spawn_id: "s1".into(),
            to_spawn_id: "s2".into(),
            artifact_path: "/tmp/artifact.txt".into(),
            is_orphaned: true,
            idle_ms: 30000,
            producer_agent_id: "a1".into(),
            consumer_agent_id: None,
        });
        let resp = store.get_dependency_edges();
        assert_eq!(resp.edges.len(), 1);
        assert!(resp.edges[0].is_orphaned);
    }

    #[test]
    fn test_metrics_computation() {
        let mut store = HandoffStoreData::new();
        store.upsert_node(DelegationNode {
            node_id: "r".into(),
            agent_id: "root".into(),
            parent_node_id: None,
            children: vec![],
            chain_depth: 0,
            spawned_at_ms: 1000,
            completed_at_ms: None,
            bytes_in: 0,
            bytes_out: 0,
            token_count: 0,
            context_overhead_ratio: 0.3,
            idle_breakdown: IdleTimeBreakdown::default(),
            model_assigned: None,
            model_optimal: None,
            child_ids: vec![],
        });
        store.upsert_node(DelegationNode {
            node_id: "c1".into(),
            agent_id: "child".into(),
            parent_node_id: Some("r".into()),
            children: vec![],
            chain_depth: 1,
            spawned_at_ms: 2000,
            completed_at_ms: Some(3000),
            bytes_in: 0,
            bytes_out: 0,
            token_count: 0,
            context_overhead_ratio: 0.6,
            idle_breakdown: IdleTimeBreakdown::default(),
            model_assigned: None,
            model_optimal: None,
            child_ids: vec![],
        });
        let m = store.get_metrics();
        assert_eq!(m.total_spawned, 2);
        assert_eq!(m.total_completed, 1);
        assert_eq!(m.active_count, 1);
        assert_eq!(m.max_chain_depth, 1);
        assert!((m.avg_context_overhead - 0.45).abs() < 0.01);
    }
}
