// SPDX-License-Identifier: MIT
//
// confidence.rs — Centralized confidence scoring for CorrelationEvidence.
//
// Combines multiple signals into a single confidence score in [0, 1]:
//   - Match level base score (from argument_matcher)
//   - Time delta penalty (larger gaps reduce confidence)
//   - Tree distance penalty (farther process-tree hops reduce confidence)
//   - Signal class boost (high-signal actions get a small boost)
//   - Competing matches penalty (ambiguity reduces confidence)
//
// This is a pure function with no side effects.

use crate::responsibility::types::{MatchLevel, SignalClass};

/// Base score per match level (from argument_matcher).
fn base_score(level: &MatchLevel) -> f32 {
    match level {
        MatchLevel::L1Exact => 0.90,
        MatchLevel::L2Normalized => 0.75,
        MatchLevel::L3Containment => 0.65,
        MatchLevel::L4Basename => 0.50,
        MatchLevel::L5Substring => 0.35,
    }
}

// ─── Penalty/Bonus constants ──────────────────────────────────────────

/// Maximum time delta that still counts as "immediate" (500ms).
const IMMEDIATE_NS: u64 = 500_000_000;

/// Time delta at which the penalty saturates (30 seconds).
const SATURATE_TIME_NS: u64 = 30_000_000_000;

/// Maximum time penalty (subtracted from base score).
const MAX_TIME_PENALTY: f32 = 0.05;

/// Penalty per additional tree hop beyond the first.
const TREE_HOP_PENALTY: f32 = 0.03;

/// Maximum tree distance penalty.
const MAX_TREE_PENALTY: f32 = 0.15;

/// Bonus for high-signal actions.
const HIGH_SIGNAL_BONUS: f32 = 0.03;

/// Penalty per competing match (beyond the first).
const COMPETING_MATCH_PENALTY: f32 = 0.04;

/// Maximum competing matches penalty.
const MAX_COMPETING_PENALTY: f32 = 0.20;

/// Absolute minimum confidence (never return below this for a valid match).
const MIN_CONFIDENCE: f32 = 0.10;

/// Absolute maximum confidence.
const MAX_CONFIDENCE: f32 = 0.98;

// ─── Main scoring function ────────────────────────────────────────────

/// Compute a confidence score in [0, 1] for a correlation match.
///
/// # Arguments
///
/// * `match_level` - The L1-L5 match level from argument matching.
/// * `time_delta_ns` - Absolute time difference between intent and action timestamps.
/// * `tree_distance` - Number of hops in the process tree between intent and action
///   (0 = same process, 1 = parent/child, etc.).
/// * `signal_class` - The signal class of the action (HighSignal gets a bonus).
/// * `competing_matches` - Number of other candidate matches for the same intent/action.
///
/// # Returns
///
/// A confidence score clamped to [MIN_CONFIDENCE, MAX_CONFIDENCE].
pub fn compute_confidence(
    match_level: &MatchLevel,
    time_delta_ns: u64,
    tree_distance: usize,
    signal_class: &SignalClass,
    competing_matches: usize,
) -> f32 {
    let mut score = base_score(match_level);

    // ── Time delta penalty ──────────────────────────────────────────
    let time_penalty = compute_time_penalty(time_delta_ns);
    score -= time_penalty;

    // ── Tree distance penalty ───────────────────────────────────────
    let tree_penalty = compute_tree_penalty(tree_distance);
    score -= tree_penalty;

    // ── Signal class bonus ──────────────────────────────────────────
    if matches!(signal_class, SignalClass::HighSignal) {
        score += HIGH_SIGNAL_BONUS;
    }

    // ── Competing matches penalty ───────────────────────────────────
    let competing_penalty = compute_competing_penalty(competing_matches);
    score -= competing_penalty;

    // Clamp to valid range
    score.clamp(MIN_CONFIDENCE, MAX_CONFIDENCE)
}

/// Compute the time-delta penalty.
///
/// Within the immediate window (500ms): no penalty.
/// Between 500ms and 30s: linear penalty up to MAX_TIME_PENALTY.
/// Beyond 30s: saturates at MAX_TIME_PENALTY.
fn compute_time_penalty(delta_ns: u64) -> f32 {
    if delta_ns <= IMMEDIATE_NS {
        return 0.0;
    }

    if delta_ns >= SATURATE_TIME_NS {
        return MAX_TIME_PENALTY;
    }

    // Linear interpolation between IMMEDIATE_NS and SATURATE_TIME_NS
    let fraction = (delta_ns - IMMEDIATE_NS) as f32 / (SATURATE_TIME_NS - IMMEDIATE_NS) as f32;
    fraction * MAX_TIME_PENALTY
}

/// Compute the tree distance penalty.
///
/// Distance 0 (same process): no penalty.
/// Distance 1 (parent/child): TREE_HOP_PENALTY * 1.
/// Each additional hop adds TREE_HOP_PENALTY, up to MAX_TREE_PENALTY.
fn compute_tree_penalty(tree_distance: usize) -> f32 {
    if tree_distance == 0 {
        return 0.0;
    }

    let penalty = tree_distance as f32 * TREE_HOP_PENALTY;
    penalty.min(MAX_TREE_PENALTY)
}

/// Compute the competing matches penalty.
///
/// 0 competing matches → no penalty.
/// Each competing match adds COMPETING_MATCH_PENALTY, up to MAX_COMPETING_PENALTY.
fn compute_competing_penalty(competing_matches: usize) -> f32 {
    if competing_matches == 0 {
        return 0.0;
    }

    let penalty = competing_matches as f32 * COMPETING_MATCH_PENALTY;
    penalty.min(MAX_COMPETING_PENALTY)
}

// ─── Tests ───────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_l1_immediate_same_process_no_competition() {
        let confidence = compute_confidence(
            &MatchLevel::L1Exact,
            0,              // immediate
            0,              // same process
            &SignalClass::HighSignal,
            0,              // no competition
        );
        // 0.90 + 0.03 (high signal) = 0.93
        assert!((confidence - 0.93).abs() < 0.01);
    }

    #[test]
    fn test_l1_with_time_penalty() {
        let confidence = compute_confidence(
            &MatchLevel::L1Exact,
            15_000_000_000, // 15 seconds
            0,              // same process
            &SignalClass::HighSignal,
            0,
        );
        // 0.90 - (0.05 * (15-0.5)/(30-0.5)) + 0.03 ≈ 0.90 - 0.025 + 0.03 = 0.905
        assert!(confidence > 0.90);
    }

    #[test]
    fn test_l5_max_penalties() {
        let confidence = compute_confidence(
            &MatchLevel::L5Substring,
            60_000_000_000, // 60 seconds (saturated)
            5,              // deep tree distance
            &SignalClass::LowSignal,
            3,              // 3 competing matches
        );
        // 0.35 - 0.25 - 0.15 - (3*0.04=0.12) = -0.17 → clamped to MIN_CONFIDENCE
        assert!((confidence - MIN_CONFIDENCE).abs() < 0.01);
    }

    #[test]
    fn test_time_penalty_zero_within_immediate() {
        let penalty = compute_time_penalty(500_000_000); // exactly 500ms
        assert!((penalty - 0.0).abs() < 0.001);

        let penalty = compute_time_penalty(0);
        assert!((penalty - 0.0).abs() < 0.001);
    }

    #[test]
    fn test_time_penalty_max_beyond_saturate() {
        let penalty = compute_time_penalty(30_000_000_000); // exactly 30s
        assert!((penalty - MAX_TIME_PENALTY).abs() < 0.01);

        let penalty = compute_time_penalty(120_000_000_000); // 120s
        assert!((penalty - MAX_TIME_PENALTY).abs() < 0.01);
    }

    #[test]
    fn test_time_penalty_midpoint() {
        // At 15s (midpoint between 500ms and 30s), should be 0.50 * MAX_TIME_PENALTY
        let penalty = compute_time_penalty(15_000_000_000);
        let expected = 0.5 * MAX_TIME_PENALTY;
        assert!((penalty - expected).abs() < 0.05);
    }

    #[test]
    fn test_tree_penalty_zero() {
        assert!((compute_tree_penalty(0) - 0.0).abs() < 0.001);
    }

    #[test]
    fn test_tree_penalty_max() {
        // 6 hops * 0.03 = 0.18, clamped to 0.15
        let penalty = compute_tree_penalty(6);
        assert!((penalty - MAX_TREE_PENALTY).abs() < 0.01);
    }

    #[test]
    fn test_tree_penalty_single_hop() {
        let penalty = compute_tree_penalty(1);
        assert!((penalty - TREE_HOP_PENALTY).abs() < 0.01);
    }

    #[test]
    fn test_competing_penalty_zero() {
        assert!((compute_competing_penalty(0) - 0.0).abs() < 0.001);
    }

    #[test]
    fn test_competing_penalty_max() {
        // 6 * 0.04 = 0.24, clamped to 0.20
        let penalty = compute_competing_penalty(6);
        assert!((penalty - MAX_COMPETING_PENALTY).abs() < 0.01);
    }

    #[test]
    fn test_competing_penalty_single() {
        let penalty = compute_competing_penalty(1);
        assert!((penalty - COMPETING_MATCH_PENALTY).abs() < 0.01);
    }

    #[test]
    fn test_signal_class_bonus() {
        let conf_high = compute_confidence(
            &MatchLevel::L5Substring,
            0,
            0,
            &SignalClass::HighSignal,
            0,
        );
        let conf_low = compute_confidence(
            &MatchLevel::L5Substring,
            0,
            0,
            &SignalClass::LowSignal,
            0,
        );
        assert!(conf_high > conf_low);
    }

    #[test]
    fn test_clamp_to_min() {
        let confidence = compute_confidence(
            &MatchLevel::L5Substring,
            120_000_000_000,
            10,
            &SignalClass::LowSignal,
            10,
        );
        assert!(confidence >= MIN_CONFIDENCE);
    }

    #[test]
    fn test_clamp_to_max() {
        let confidence = compute_confidence(
            &MatchLevel::L1Exact,
            0,
            0,
            &SignalClass::HighSignal,
            0,
        );
        assert!(confidence <= MAX_CONFIDENCE);
    }

    #[test]
    fn test_internal_housekeeping_signal() {
        let confidence = compute_confidence(
            &MatchLevel::L3Containment,
            1_000_000_000,
            1,
            &SignalClass::InternalHousekeeping,
            0,
        );
        // InternalHousekeeping gets no HIGH_SIGNAL_BONUS
        let with_high = compute_confidence(
            &MatchLevel::L3Containment,
            1_000_000_000,
            1,
            &SignalClass::HighSignal,
            0,
        );
        assert!(confidence < with_high);
    }
}
