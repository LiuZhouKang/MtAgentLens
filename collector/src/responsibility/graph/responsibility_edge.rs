// Canonical re-exports from types/graph_types.rs.
// Old ResponsibilityEdge struct with legacy field names (from_node, to_node,
// inference_rule_id, ambiguity) has been removed.
// Use types::graph_types::ResponsibilityEdge with source/target/phase/signal instead.
pub use crate::responsibility::types::graph_types::ResponsibilityEdgeType;
