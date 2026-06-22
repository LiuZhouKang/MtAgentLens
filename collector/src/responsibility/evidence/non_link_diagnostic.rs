// SPDX-License-Identifier: MIT
//
// non_link_diagnostic.rs — Convenience constructors for NonLinkDiagnostic instances.
//
// Produces NonLinkDiagnostic records when an intent cannot be matched to
// any action (or vice versa) in the evidence pipeline. These diagnostics
// feed into the Phase 1 streaming path and Phase 2 batch refinement.
//
// The canonical NonLinkDiagnostic struct and NonLinkReason enum are defined
// in crate::responsibility::types::evidence_types and re-exported here.

use crate::responsibility::types::{IntentId, MatchLevel};

// Re-export the canonical types for backward compat.
pub use crate::responsibility::types::{
    NonLinkDiagnostic, NonLinkReason, NON_LINK_DIAGNOSTIC_SCHEMA_VERSION,
};

// ─── Convenience constructors ─────────────────────────────────────────

/// Build a NonLinkDiagnostic for an intent that found no argument match.
pub fn no_argument_match(
    intent_id: IntentId,
    timestamp_ns: u64,
) -> NonLinkDiagnostic {
    NonLinkDiagnostic {
        schema_version: NON_LINK_DIAGNOSTIC_SCHEMA_VERSION,
        diagnostic_id: uuid::Uuid::new_v4().to_string(),
        timestamp_ns,
        agent_id: None,
        intent_id: Some(intent_id.to_string()),
        candidate_id: None,
        reason: NonLinkReason::NoArgumentMatch,
        stage: "evidence_streaming".to_string(),
        intent_class: None,
        parse_state: None,
        match_level_considered: None,
        confidence_observed: None,
        details: None,
    }
}

/// Build a NonLinkDiagnostic for an intent that has no eligible action candidate.
pub fn candidate_not_available(
    intent_id: IntentId,
    timestamp_ns: u64,
) -> NonLinkDiagnostic {
    NonLinkDiagnostic {
        schema_version: NON_LINK_DIAGNOSTIC_SCHEMA_VERSION,
        diagnostic_id: uuid::Uuid::new_v4().to_string(),
        timestamp_ns,
        agent_id: None,
        intent_id: Some(intent_id.to_string()),
        candidate_id: None,
        reason: NonLinkReason::CandidateNotAvailable,
        stage: "evidence_streaming".to_string(),
        intent_class: None,
        parse_state: None,
        match_level_considered: None,
        confidence_observed: None,
        details: None,
    }
}

/// Build a NonLinkDiagnostic when confidence fell below the cutoff.
pub fn below_confidence_cutoff(
    intent_id: IntentId,
    action_id: crate::responsibility::types::ActionId,
    observed_confidence: f32,
    match_level: MatchLevel,
    timestamp_ns: u64,
) -> NonLinkDiagnostic {
    NonLinkDiagnostic {
        schema_version: NON_LINK_DIAGNOSTIC_SCHEMA_VERSION,
        diagnostic_id: uuid::Uuid::new_v4().to_string(),
        timestamp_ns,
        agent_id: None,
        intent_id: Some(intent_id.to_string()),
        candidate_id: Some(action_id.to_string()),
        reason: NonLinkReason::BelowConfidenceCutoff,
        stage: "evidence_streaming".to_string(),
        intent_class: None,
        parse_state: None,
        match_level_considered: Some(format!("{:?}", match_level)),
        confidence_observed: Some(observed_confidence),
        details: None,
    }
}

/// Build a NonLinkDiagnostic when action is outside time window.
pub fn outside_time_window(
    intent_id: IntentId,
    action_id: crate::responsibility::types::ActionId,
    timestamp_ns: u64,
) -> NonLinkDiagnostic {
    NonLinkDiagnostic {
        schema_version: NON_LINK_DIAGNOSTIC_SCHEMA_VERSION,
        diagnostic_id: uuid::Uuid::new_v4().to_string(),
        timestamp_ns,
        agent_id: None,
        intent_id: Some(intent_id.to_string()),
        candidate_id: Some(action_id.to_string()),
        reason: NonLinkReason::OutsideTimeWindow,
        stage: "evidence_streaming".to_string(),
        intent_class: None,
        parse_state: None,
        match_level_considered: None,
        confidence_observed: None,
        details: None,
    }
}

/// Build a NonLinkDiagnostic for ambiguous competition.
pub fn ambiguous_competition(
    intent_id: IntentId,
    timestamp_ns: u64,
) -> NonLinkDiagnostic {
    NonLinkDiagnostic {
        schema_version: NON_LINK_DIAGNOSTIC_SCHEMA_VERSION,
        diagnostic_id: uuid::Uuid::new_v4().to_string(),
        timestamp_ns,
        agent_id: None,
        intent_id: Some(intent_id.to_string()),
        candidate_id: None,
        reason: NonLinkReason::AmbiguousCompetition,
        stage: "evidence_streaming".to_string(),
        intent_class: None,
        parse_state: None,
        match_level_considered: None,
        confidence_observed: None,
        details: None,
    }
}

/// Build a NonLinkDiagnostic for a non-primary-eligible intent.
pub fn intent_not_primary_eligible(
    intent_id: IntentId,
    timestamp_ns: u64,
) -> NonLinkDiagnostic {
    NonLinkDiagnostic {
        schema_version: NON_LINK_DIAGNOSTIC_SCHEMA_VERSION,
        diagnostic_id: uuid::Uuid::new_v4().to_string(),
        timestamp_ns,
        agent_id: None,
        intent_id: Some(intent_id.to_string()),
        candidate_id: None,
        reason: NonLinkReason::IntentNotPrimaryEligible,
        stage: "evidence_streaming".to_string(),
        intent_class: None,
        parse_state: None,
        match_level_considered: None,
        confidence_observed: None,
        details: None,
    }
}

/// Build a NonLinkDiagnostic when Observer LLM arbitration is pending.
pub fn observer_pending(
    intent_id: IntentId,
    action_id: crate::responsibility::types::ActionId,
    timestamp_ns: u64,
) -> NonLinkDiagnostic {
    NonLinkDiagnostic {
        schema_version: NON_LINK_DIAGNOSTIC_SCHEMA_VERSION,
        diagnostic_id: uuid::Uuid::new_v4().to_string(),
        timestamp_ns,
        agent_id: None,
        intent_id: Some(intent_id.to_string()),
        candidate_id: Some(action_id.to_string()),
        reason: NonLinkReason::ObserverPending,
        stage: "evidence_streaming".to_string(),
        intent_class: None,
        parse_state: None,
        match_level_considered: None,
        confidence_observed: None,
        details: None,
    }
}

// ─── Tests ───────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    fn iid() -> IntentId {
        IntentId::from("intent-001")
    }

    fn aid() -> crate::responsibility::types::ActionId {
        crate::responsibility::types::ActionId::from("action-001")
    }

    #[test]
    fn test_legacy_constructor() {
        let diag = NonLinkDiagnostic::new(
            1000,
            Some("agent-1".to_string()),
            Some("intent-1".to_string()),
            Some("action-1".to_string()),
            NonLinkReason::NoArgumentMatch,
            "test_stage".to_string(),
        );

        assert_eq!(diag.timestamp_ns, 1000);
        assert_eq!(diag.agent_id.unwrap(), "agent-1");
        assert_eq!(diag.schema_version, NON_LINK_DIAGNOSTIC_SCHEMA_VERSION);
    }

    #[test]
    fn test_legacy_builder_methods() {
        let diag = NonLinkDiagnostic::new(
            1000,
            None,
            None,
            None,
            NonLinkReason::NoArgumentMatch,
            "test".to_string(),
        )
        .with_intent_class(crate::responsibility::types::IntentClass::SideEffect)
        .with_parse_state(crate::responsibility::types::IntentParseState::Complete);

        assert_eq!(diag.intent_class.as_deref(), Some("side_effect"));
        assert_eq!(diag.parse_state.as_deref(), Some("complete"));
    }

    #[test]
    fn test_no_argument_match() {
        let diag = no_argument_match(iid(), 1000);
        assert_eq!(diag.reason, NonLinkReason::NoArgumentMatch);
        assert!(diag.intent_id.is_some());
        assert!(diag.candidate_id.is_none());
        assert_eq!(diag.timestamp_ns, 1000);
        assert!(!diag.diagnostic_id.is_empty());
    }

    #[test]
    fn test_candidate_not_available() {
        let diag = candidate_not_available(iid(), 2000);
        assert_eq!(diag.reason, NonLinkReason::CandidateNotAvailable);
    }

    #[test]
    fn test_below_confidence_cutoff() {
        let diag = below_confidence_cutoff(iid(), aid(), 0.45, MatchLevel::L5Substring, 3000);
        assert_eq!(diag.reason, NonLinkReason::BelowConfidenceCutoff);
        assert!(diag.candidate_id.is_some());
        assert!(diag.confidence_observed.is_some());
    }

    #[test]
    fn test_outside_time_window() {
        let diag = outside_time_window(iid(), aid(), 4000);
        assert_eq!(diag.reason, NonLinkReason::OutsideTimeWindow);
    }

    #[test]
    fn test_ambiguous_competition() {
        let diag = ambiguous_competition(iid(), 5000);
        assert_eq!(diag.reason, NonLinkReason::AmbiguousCompetition);
    }

    #[test]
    fn test_intent_not_primary_eligible() {
        let diag = intent_not_primary_eligible(iid(), 6000);
        assert_eq!(diag.reason, NonLinkReason::IntentNotPrimaryEligible);
    }

    #[test]
    fn test_observer_pending() {
        let diag = observer_pending(iid(), aid(), 7000);
        assert_eq!(diag.reason, NonLinkReason::ObserverPending);
    }

    #[test]
    fn test_diagnostic_ids_are_unique() {
        let d1 = no_argument_match(iid(), 1000);
        let d2 = no_argument_match(iid(), 1000);
        assert_ne!(d1.diagnostic_id, d2.diagnostic_id);
    }
}
