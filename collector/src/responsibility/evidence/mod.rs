// ── Legacy modules (backward compat) ───────────────────────────────────────
pub mod action_candidate;
pub mod correlator_legacy;
pub mod evidence_record;
pub mod root_cause;

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

// ── Legacy re-exports (backward compat — old consumers) ────────────────────
pub use action_candidate::{
    ActionCandidate, ActionCandidateType, CandidateDerivationMode, Lossiness, SignalClass,
};
// Legacy types from correlator_legacy — re-exported for backward compat.
pub use correlator_legacy::{
    CorrelationEvidence, EvidenceCorrelator, MatchLevel, PendingArbitration,
};
pub use evidence_record::{
    EvidenceDiagnostic, EvidenceDirectnessNew, EvidenceReconstruction, EvidenceRecordNew,
    EvidenceType, MatchedField,
};
pub use root_cause::rank_legacy_correlation_evidence;

// ── Authoritative re-exports ───────────────────────────────────────────────
pub use action_classifier::ActionClassifier;
pub use args::{extract_action_args, extract_intent_args};
pub use argument_matcher::{match_args, match_level_base_score};
pub use buffer::{ActionBuffer, IntentBuffer};
pub use confidence::compute_confidence;
pub use correlator::{CorrelationResult, EvidenceCorrelator as NewEvidenceCorrelator};
pub use intent_extractor::IntentExtractor;
pub use non_link_diagnostic::{
    ambiguous_competition, below_confidence_cutoff, candidate_not_available,
    intent_not_primary_eligible, no_argument_match, observer_pending, outside_time_window,
};
pub use quality::passes_quality_gate;
// Re-export types that moved to types/ for backward compat
pub use crate::responsibility::types::{NonLinkDiagnostic, NonLinkReason};
pub mod normalizer;
