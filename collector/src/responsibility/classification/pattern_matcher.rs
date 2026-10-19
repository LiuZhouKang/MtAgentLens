// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 MtAgentLens Project

// Migrated from graph/pattern_matcher.rs.
// Re-exports from the original location to avoid code duplication
// while establishing this as the canonical import path going forward.
pub use crate::responsibility::graph::pattern_matcher::{
    GraphPatternMatch, GraphPatternMatcher, GraphPatternTemplate, default_templates,
};
