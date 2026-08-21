// SPDX-License-Identifier: MIT
//
// v8_types.rs — v8 scoring type definitions (§3.6)
//
// ScoreBreakdownV8 replaces the v7 ScoreBreakdown with per-dimension
// contribution decomposition and progressive scoring state.

use std::collections::HashMap;

use serde::Serialize;

use crate::responsibility::scoring::progressive::ProgressiveResult;

/// Progressive scoring state snapshot embedded in the breakdown.
#[derive(Debug, Clone, Serialize)]
pub struct ProgressiveBreakdown {
    /// Per-agent progressive threat scores.
    pub pts: Vec<ProgressiveResult>,
    /// Global EWMA mean across all agents.
    pub ewma: f64,
    /// Global CUSUM statistic.
    pub cusum: f64,
    /// Whether CUSUM alarm is active.
    pub cusum_alarm: bool,
    /// Total events processed.
    pub event_count: u64,
    /// Current decay weight factor.
    pub decay_weight: f64,
}

/// v8 score breakdown with per-dimension contributions.
///
/// Each dimension's contribution is stored as a fraction of the total,
/// enabling SOC analysts to understand which signal sources drove the score.
#[derive(Debug, Clone, Serialize)]
pub struct ScoreBreakdownV8 {
    /// Contribution fraction per dimension name → value.
    pub dim_contributions: HashMap<String, f64>,
    /// Progressive scoring state.
    pub progressive: ProgressiveBreakdown,
    /// Classification route (direct_unsafe, fail_closed_unsafe, etc.).
    pub route: String,
    /// Final aggregated score [0, 1].
    pub total_score: f64,
}
