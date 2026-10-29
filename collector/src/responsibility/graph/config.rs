// SPDX-License-Identifier: Apache-2.0 OR MIT
//
// config.rs — GraphConfig for the ResponsibilityGraph module.
//
// Controls node/edge budgets and eviction behaviour. Fields that used to be
// declared here but never read (soft limits, walk depth, snapshot interval,
// SQLite flag) were removed rather than left as no-op knobs.

use serde::{Deserialize, Serialize};

/// Configuration for the ResponsibilityGraph and its builder.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GraphConfig {
    /// Hard limit on node count; when exceeded the graph evicts oldest nodes.
    pub hard_max_nodes: usize,
    /// Hard limit on edge count.
    pub hard_max_edges: usize,
    /// Number of nodes/edges to evict in one batch when a hard limit is hit.
    pub eviction_batch_size: usize,
}

impl Default for GraphConfig {
    fn default() -> Self {
        Self {
            hard_max_nodes: 50_000,
            hard_max_edges: 200_000,
            eviction_batch_size: 100,
        }
    }
}

impl GraphConfig {
    /// Strict bounds suitable for memory-constrained environments.
    pub fn tight() -> Self {
        Self {
            hard_max_nodes: 5_000,
            hard_max_edges: 20_000,
            eviction_batch_size: 50,
            ..Default::default()
        }
    }

    /// Relaxed bounds for long-running collection sessions.
    pub fn relaxed() -> Self {
        Self {
            hard_max_nodes: 200_000,
            hard_max_edges: 1_000_000,
            eviction_batch_size: 500,
            ..Default::default()
        }
    }
}
