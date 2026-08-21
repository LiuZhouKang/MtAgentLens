// SPDX-License-Identifier: MIT
//
// buffer.rs — Thin GraphDeltaBuffer (Vec accumulator).
//
// Per spec §15, the runtime's buffer is a minimal accumulator that collects
// nodes, edges, evidence, and timeline entries for batch processing during
// trigger(). Unlike the legacy buffer.rs, this is intentionally simple:
// no compression, no incremental snapshots, no behavior timeline generation.
//
// The classification pipeline reads from this buffer during each trigger()
// invocation, then clears it.

use crate::responsibility::types::alert_types::BehaviorTimelineEntry;
use crate::responsibility::types::evidence_types::{Action, CorrelationEvidence, Intent};
use crate::responsibility::types::graph_types::{ResponsibilityEdge, ResponsibilityNode};
use crate::responsibility::types::ids::{ActionId, IntentId};

// ─── GraphDeltaBuffer ──────────────────────────────────────────────────

/// Thin accumulator for graph deltas, evidence, and timeline entries.
///
/// Accumulates data between trigger() invocations. Each trigger() drains
/// the buffer and processes accumulated items through the full pipeline.
#[derive(Debug, Clone, Default)]
pub struct GraphDeltaBuffer {
    /// Accumulated graph nodes.
    pub nodes: Vec<ResponsibilityNode>,
    /// Accumulated graph edges.
    pub edges: Vec<ResponsibilityEdge>,
    /// Accumulated intents (for graph builder lookups).
    pub intents: Vec<Intent>,
    /// Accumulated actions (for graph builder lookups).
    pub actions: Vec<Action>,
    /// Accumulated correlation evidence.
    pub evidence: Vec<CorrelationEvidence>,
    /// Behavior timeline entries (ordered by time).
    pub timeline: Vec<BehaviorTimelineEntry>,
}

impl GraphDeltaBuffer {
    /// Create an empty buffer.
    pub fn new() -> Self {
        Self::default()
    }

    /// Push a graph node into the buffer.
    pub fn push_node(&mut self, node: ResponsibilityNode) {
        self.nodes.push(node);
    }

    /// Push a graph edge into the buffer.
    pub fn push_edge(&mut self, edge: ResponsibilityEdge) {
        self.edges.push(edge);
    }

    /// Push an intent into the buffer.
    pub fn push_intent(&mut self, intent: Intent) {
        self.intents.push(intent);
    }

    /// Push an action into the buffer.
    pub fn push_action(&mut self, action: Action) {
        self.actions.push(action);
    }

    /// Push correlation evidence into the buffer.
    pub fn push_evidence(&mut self, ev: CorrelationEvidence) {
        self.evidence.push(ev);
    }

    /// Push a timeline entry.
    pub fn push_timeline(&mut self, entry: BehaviorTimelineEntry) {
        self.timeline.push(entry);
    }

    /// Total node count.
    pub fn node_count(&self) -> usize {
        self.nodes.len()
    }

    /// Total edge count.
    pub fn edge_count(&self) -> usize {
        self.edges.len()
    }

    /// Total evidence count.
    pub fn evidence_count(&self) -> usize {
        self.evidence.len()
    }

    /// Whether the buffer has any accumulated items.
    pub fn has_pending(&self) -> bool {
        !self.nodes.is_empty()
            || !self.edges.is_empty()
            || !self.evidence.is_empty()
            || !self.timeline.is_empty()
    }

    /// Look up an intent by its ID.
    pub fn find_intent_by_id(&self, intent_id: &IntentId) -> Option<&Intent> {
        self.intents.iter().find(|i| i.intent_id == *intent_id)
    }

    /// Look up an action by its ID.
    pub fn find_action_by_id(&self, action_id: &ActionId) -> Option<&Action> {
        self.actions.iter().find(|a| a.action_id == *action_id)
    }

    /// Clear all accumulated data. Called after each trigger().
    pub fn clear(&mut self) {
        self.nodes.clear();
        self.edges.clear();
        self.intents.clear();
        self.actions.clear();
        self.evidence.clear();
        self.timeline.clear();
    }
}
