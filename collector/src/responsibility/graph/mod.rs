// SPDX-License-Identifier: MIT
//
// graph/ — ResponsibilityGraph core module.
//
// This is the central graph module. It is the ONLY module authorized to
// construct and mutate the ResponsibilityGraph. All other modules consume
// the graph read-only or through the GraphBuilder interface.

// ── Legacy / utility modules ────────────────────────────────────────────
pub mod graph_simplifier;
pub mod graph_store;
pub mod pattern_matcher;
pub mod responsibility_edge;
pub mod responsibility_node;

// ── Canonical modules ────────────────────────────────────────────────────
pub mod causal_chain;
pub mod config;
pub mod delta;
pub mod graph;
pub mod graph_builder;
pub mod repository;
pub mod simplifier;
pub mod snapshot;
pub mod taint;

// ── Canonical exports ────────────────────────────────────────────────────
pub use causal_chain::{CausalChain, CausalChainTracer};
pub use config::GraphConfig;
pub use delta::GraphDelta;
pub use graph::ResponsibilityGraph;
pub use graph_builder::GraphBuilder;
pub use repository::GraphRepository;
pub use simplifier::GraphSimplifier;
pub use snapshot::{FrozenGraph, create_snapshot, diff_snapshot};
pub use taint::{TaintAccess, TaintRecord, TaintTracker};

// ── Type re-exports (canonical definitions in types/) ────────────────────
pub use crate::responsibility::types::graph_types::{
    GraphDiff, ResponsibilityEdge as NewResponsibilityEdge,
    ResponsibilityEdgeType as NewResponsibilityEdgeType,
    ResponsibilityNode as NewResponsibilityNode,
    ResponsibilityNodeType as NewResponsibilityNodeType,
};

// ── Module re-exports ────────────────────────────────────────────────────
pub use graph_store::GraphStore;
pub use pattern_matcher::{
    GraphPatternMatch, GraphPatternMatcher, GraphPatternTemplate, default_templates,
};
pub use responsibility_edge::ResponsibilityEdgeType;
pub use responsibility_node::ResponsibilityNodeType;
pub mod graph_pattern_matcher;
