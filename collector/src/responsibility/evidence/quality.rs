// SPDX-License-Identifier: MIT
//
// quality.rs — Evidence quality gate: rejects weak matches before persistence.
//
// L5Substring and L4Basename matches can be extremely weak in practice.
// This module provides a pre-filter that is stricter than the base
// confidence scoring — it rejects matches that are fundamentally
// unreliable regardless of their computed confidence.

use crate::responsibility::types::MatchLevel;

/// Minimum accepted L4Basename confidence.
const MIN_CONFIDENCE_L4: f32 = 0.50;

/// Minimum accepted L5Substring confidence.
const MIN_CONFIDENCE_L5: f32 = 0.35;

/// Minimum matched substring length for L5Substring (characters).
/// Minimum matched substring length — rejects meaningless noise like
/// "ab" while allowing real matches like "python" (6 chars).
const MIN_MATCHED_LEN_L5: usize = 6;

/// Check whether a match passes the quality gate for its level.
///
/// Returns `false` for L5Substring matches with strings shorter than
/// `MIN_MATCHED_LEN_L5` or confidence below `MIN_CONFIDENCE_L5`.
/// Returns `false` for L4Basename matches with confidence below
/// `MIN_CONFIDENCE_L4`. L3 and above always pass.
pub fn passes_quality_gate(match_level: &MatchLevel, confidence: f32, matched_str: &str) -> bool {
    match match_level {
        MatchLevel::L1Exact | MatchLevel::L2Normalized | MatchLevel::L3Containment => true,
        MatchLevel::L4Basename => confidence >= MIN_CONFIDENCE_L4,
        MatchLevel::L5Substring => {
            confidence >= MIN_CONFIDENCE_L5 && matched_str.len() >= MIN_MATCHED_LEN_L5
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn l5_short_string_rejected() {
        assert!(!passes_quality_gate(&MatchLevel::L5Substring, 0.40, "ab"));
    }

    #[test]
    fn l5_short_high_confidence_rejected() {
        assert!(!passes_quality_gate(&MatchLevel::L5Substring, 0.90, "ab"));
    }

    #[test]
    fn l5_long_enough_accepted() {
        assert!(passes_quality_gate(
            &MatchLevel::L5Substring,
            0.40,
            "/home/user/file.rs"
        ));
    }

    #[test]
    fn l5_low_confidence_rejected() {
        assert!(!passes_quality_gate(
            &MatchLevel::L5Substring,
            0.25,
            "/home/user/project/file.rs"
        ));
    }

    #[test]
    fn l4_low_confidence_rejected() {
        assert!(!passes_quality_gate(&MatchLevel::L4Basename, 0.40, "Cargo.toml"));
    }

    #[test]
    fn l4_good_confidence_accepted() {
        assert!(passes_quality_gate(&MatchLevel::L4Basename, 0.55, "Cargo.toml"));
    }

    #[test]
    fn l3_and_above_always_pass() {
        assert!(passes_quality_gate(&MatchLevel::L3Containment, 0.01, "any"));
        assert!(passes_quality_gate(&MatchLevel::L2Normalized, 0.01, "any"));
        assert!(passes_quality_gate(&MatchLevel::L1Exact, 0.01, "any"));
    }
}
