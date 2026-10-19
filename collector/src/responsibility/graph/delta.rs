// SPDX-License-Identifier: Apache-2.0 OR MIT
//
// delta.rs — GraphDelta: incremental graph change set emitted by GraphBuilder.
//
// Each call to GraphBuilder::handle() produces a GraphDelta describing
// exactly what was added, removed, or updated in the graph. Consumers
// (persistence, SSE push, classification) use the delta for efficient
// incremental processing.

use crate::responsibility::types::{EdgeId, NodeId, ResponsibilityEdge, ResponsibilityNode};
use serde::{Deserialize, Serialize};

/// An incremental change set for the ResponsibilityGraph.
///
/// Produced by `GraphBuilder::handle()` after processing a single
/// CorrelationEvidence. Contains newly created nodes/edges, removed
/// nodes/edges (from eviction or merging), and updated nodes/edges
/// (e.g., when confidence changes or an agent is reassigned).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GraphDelta {
    /// Nodes created during this handle call.
    pub new_nodes: Vec<ResponsibilityNode>,

    /// Edges created during this handle call.
    pub new_edges: Vec<ResponsibilityEdge>,

    /// Node IDs that were removed (evicted, merged, or cleaned up).
    pub removed_nodes: Vec<NodeId>,

    /// Edge IDs that were removed.
    pub removed_edges: Vec<EdgeId>,

    /// Nodes that existed already but were updated (e.g., agent reassignment).
    pub updated_nodes: Vec<ResponsibilityNode>,

    /// Edges that existed already but were updated (e.g., confidence change).
    pub updated_edges: Vec<ResponsibilityEdge>,

    /// Human-readable diagnostics produced during this handle call.
    pub diagnostics: Vec<String>,
}

impl GraphDelta {
    /// An empty delta (no changes).
    pub fn empty() -> Self {
        Self {
            new_nodes: Vec::new(),
            new_edges: Vec::new(),
            removed_nodes: Vec::new(),
            removed_edges: Vec::new(),
            updated_nodes: Vec::new(),
            updated_edges: Vec::new(),
            diagnostics: Vec::new(),
        }
    }

    /// Total number of additions (new nodes + new edges).
    pub fn addition_count(&self) -> usize {
        self.new_nodes.len() + self.new_edges.len()
    }

    /// Total number of removals (removed nodes + removed edges).
    pub fn removal_count(&self) -> usize {
        self.removed_nodes.len() + self.removed_edges.len()
    }

    /// Total number of updates (updated nodes + updated edges).
    pub fn update_count(&self) -> usize {
        self.updated_nodes.len() + self.updated_edges.len()
    }

    /// Whether this delta contains any changes at all.
    pub fn is_empty(&self) -> bool {
        self.new_nodes.is_empty()
            && self.new_edges.is_empty()
            && self.removed_nodes.is_empty()
            && self.removed_edges.is_empty()
            && self.updated_nodes.is_empty()
            && self.updated_edges.is_empty()
    }

    /// Merge another delta into this one (move semantics).
    pub fn merge(&mut self, other: GraphDelta) {
        self.new_nodes.extend(other.new_nodes);
        self.new_edges.extend(other.new_edges);
        self.removed_nodes.extend(other.removed_nodes);
        self.removed_edges.extend(other.removed_edges);
        self.updated_nodes.extend(other.updated_nodes);
        self.updated_edges.extend(other.updated_edges);
        self.diagnostics.extend(other.diagnostics);
    }

    /// Add a diagnostic message.
    pub fn with_diagnostic(mut self, msg: impl Into<String>) -> Self {
        self.diagnostics.push(msg.into());
        self
    }
}

impl Default for GraphDelta {
    fn default() -> Self {
        Self::empty()
    }
}
