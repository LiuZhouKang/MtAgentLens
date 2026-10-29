// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 MtAgentLens Project

// ── Legacy modules (backward compat) ───────────────────────────────────────
// The legacy correlator, its duplicate MatchLevel / CorrelationEvidence /
// ActionCandidateType definitions, the unused EvidenceRecordNew type and the
// dead Innovation-7/17 graph modules were deleted on 2026-10-18. The audit and
// the semantic mapping table are in docs/adr/0011-one-match-level.md.

// ── Authoritative modules ──────────────────────────────────────────────────
pub mod action_classifier;
pub mod args;
pub mod argument_matcher;
pub mod buffer;
pub mod confidence;
pub mod correlator;
pub mod intent_extractor;
pub mod non_link_diagnostic;
pub mod quality;
pub mod retention;

// ── Authoritative re-exports ───────────────────────────────────────────────
pub use action_classifier::ActionClassifier;
pub use args::{extract_action_args, extract_intent_args};
pub use argument_matcher::{ArgMatch, match_args, match_args_detailed, match_level_base_score};
pub use buffer::{ActionBuffer, IntentBuffer};
pub use confidence::compute_confidence;
pub use correlator::{CorrelationResult, EvidenceCorrelator};
pub use intent_extractor::IntentExtractor;
pub use non_link_diagnostic::{
    ambiguous_competition, below_confidence_cutoff, candidate_not_available,
    intent_not_primary_eligible, no_argument_match, observer_pending, outside_time_window,
};
pub use quality::passes_quality_gate;
// Re-export types that moved to types/ for backward compat
pub use crate::responsibility::types::{NonLinkDiagnostic, NonLinkReason};
