// Canonical re-exports from types/graph_types.rs.
// Old ResponsibilityNode struct with legacy field names (subject, claimed_role,
// resource_ref, observation_refs, timestamp_range) has been removed.
// Use types::graph_types::ResponsibilityNode with agent_id/resource/timestamp_ns/phase instead.
pub use crate::responsibility::types::graph_types::ResponsibilityNodeType;
