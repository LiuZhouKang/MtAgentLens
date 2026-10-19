// SPDX-License-Identifier: Apache-2.0 OR MIT
//
// config.rs — GraphConfig for the ResponsibilityGraph module.
//
// Controls node/edge budgets, eviction behaviour, walk depth limits,
// taint propagation depth, snapshot interval, and SQLite persistence.

use serde::{Deserialize, Serialize};

/// Configuration for the ResponsibilityGraph and its builder.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GraphConfig {
    /// Soft limit on node count; when exceeded the graph starts logging warnings.
    pub soft_max_nodes: usize,
    /// Hard limit on node count; when exceeded the graph evicts oldest nodes.
    pub hard_max_nodes: usize,
    /// Soft limit on edge count.
    pub soft_max_edges: usize,
    /// Hard limit on edge count.
    pub hard_max_edges: usize,
    /// Number of nodes/edges to evict in one batch when a hard limit is hit.
    pub eviction_batch_size: usize,
    /// Maximum depth for BFS / backward-walk traversals.
    pub max_walk_depth: usize,
    /// Maximum depth for taint propagation chains.
    pub taint_propagation_depth: usize,
    /// Whether to persist the graph to SQLite (via GraphStore).
    pub enable_sqlite_persistence: bool,
    /// Interval between automatic snapshots (nanoseconds). 0 = disabled.
    pub snapshot_interval_ns: u64,
    /// Minimum confidence for edges to be retained after simplification.
    pub min_edge_confidence: f32,
}

impl Default for GraphConfig {
    fn default() -> Self {
        Self {
            soft_max_nodes: 10_000,
            hard_max_nodes: 50_000,
            soft_max_edges: 50_000,
            hard_max_edges: 200_000,
            eviction_batch_size: 100,
            max_walk_depth: 10,
            taint_propagation_depth: 5,
            enable_sqlite_persistence: true,
            snapshot_interval_ns: 60_000_000_000, // 60 s
            min_edge_confidence: 0.30,
        }
    }
}

impl GraphConfig {
    /// Strict bounds suitable for memory-constrained environments.
    pub fn tight() -> Self {
        Self {
            soft_max_nodes: 1_000,
            hard_max_nodes: 5_000,
            soft_max_edges: 5_000,
            hard_max_edges: 20_000,
            eviction_batch_size: 50,
            max_walk_depth: 5,
            taint_propagation_depth: 3,
            ..Default::default()
        }
    }

    /// Relaxed bounds for long-running collection sessions.
    pub fn relaxed() -> Self {
        Self {
            soft_max_nodes: 50_000,
            hard_max_nodes: 200_000,
            soft_max_edges: 200_000,
            hard_max_edges: 1_000_000,
            eviction_batch_size: 500,
            max_walk_depth: 20,
            taint_propagation_depth: 10,
            ..Default::default()
        }
    }
}
