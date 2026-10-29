// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 MtAgentLens Project

//! Persistence-only retention for correlation evidence.
//!
//! Live detection keeps every correlation in memory. Persisting all of them is
//! what turns a long run into hundreds of thousands of rows: one action can
//! inherit every ancestor intent (ProcessLineage) and match every containment
//! string. This policy selects a bounded, deterministic set for SQLite while
//! never changing the graph/claims inputs.

use std::collections::{HashMap, HashSet};

use crate::responsibility::classification::candidates::is_sensitive_path;
use crate::responsibility::types::{
    Action, ActionCandidateType, CorrelationEvidence, MatchLevel, NormalizedArg,
    ResponsibilityClaim,
};

/// Security-relevant actions get a larger evidence budget because hard rules
/// and claims cite them directly.
const SECURITY_ACTIONS: &[ActionCandidateType] = &[
    ActionCandidateType::FileDelete,
    ActionCandidateType::FileRename,
    ActionCandidateType::FileWrite,
    ActionCandidateType::FileOverwrite,
    ActionCandidateType::NetworkConnect,
    ActionCandidateType::NetworkSend,
    ActionCandidateType::ProcessExec,
    ActionCandidateType::ProcessSpawn,
];

const SECURITY_BUDGET: usize = 8;
const ORDINARY_BUDGET: usize = 1;

fn rank(level: &MatchLevel) -> u8 {
    // ProcessLineage is stronger than containment for causal attribution, but
    // weaker than exact/normalized argument matches.
    match level {
        MatchLevel::L1Exact => 6,
        MatchLevel::L2Normalized => 5,
        MatchLevel::ProcessLineage => 4,
        MatchLevel::L3Containment => 3,
        MatchLevel::L4Basename => 2,
        MatchLevel::L5Substring => 1,
    }
}

fn is_security_action(action: &Action) -> bool {
    if SECURITY_ACTIONS.contains(&action.action_type) {
        return true;
    }
    matches!(
        action.action_type,
        ActionCandidateType::FileOpen | ActionCandidateType::FileRead
    ) && action.normalized_args.iter().any(|arg| match arg {
        NormalizedArg::Path(path) => is_sensitive_path(path),
        NormalizedArg::PathCaptureIncomplete {
            normalized_path, ..
        } => is_sensitive_path(normalized_path),
        _ => false,
    })
}

fn order_key(evidence: &CorrelationEvidence) -> (u8, u32, u64, &str) {
    (
        rank(&evidence.match_level),
        (evidence.confidence * 1_000_000.0) as u32,
        evidence.timestamp_ns,
        evidence.evidence_id.as_str(),
    )
}

/// Select the evidence rows that should be persisted.
///
/// - every evidence id cited by a claim is retained;
/// - security actions retain at most eight highest-quality rows;
/// - ordinary actions retain one highest-quality row.
///
/// The returned vector is sorted by evidence id for deterministic writes.
pub fn select_for_persistence(
    evidence: &[CorrelationEvidence],
    actions: &[Action],
    claims: &[ResponsibilityClaim],
) -> Vec<CorrelationEvidence> {
    let action_index: HashMap<&str, &Action> = actions
        .iter()
        .map(|action| (action.action_id.as_str(), action))
        .collect();
    let cited: HashSet<&str> = claims
        .iter()
        .flat_map(|claim| claim.evidence_refs.iter())
        .map(|id| id.as_str())
        .collect();
    let mut groups: HashMap<&str, Vec<&CorrelationEvidence>> = HashMap::new();
    for item in evidence {
        groups
            .entry(item.action_id.as_str())
            .or_default()
            .push(item);
    }

    let mut selected: HashMap<&str, CorrelationEvidence> = HashMap::new();
    for (action_id, mut group) in groups {
        group.sort_by(|left, right| order_key(right).cmp(&order_key(left)));
        let budget = action_index
            .get(action_id)
            .copied()
            .is_some_and(is_security_action)
            .then_some(SECURITY_BUDGET)
            .unwrap_or(ORDINARY_BUDGET);
        for item in group.into_iter().take(budget) {
            selected.insert(item.evidence_id.as_str(), item.clone());
        }
    }
    // Claims are the hard coverage invariant: never persist a claim whose
    // evidence link is absent from SQLite.
    for item in evidence {
        if cited.contains(item.evidence_id.as_str()) {
            selected.insert(item.evidence_id.as_str(), item.clone());
        }
    }

    let mut out: Vec<_> = selected.into_values().collect();
    out.sort_by(|left, right| left.evidence_id.as_str().cmp(right.evidence_id.as_str()));
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::responsibility::types::{
        ActionId, ActionOutcome, AgentId, AlertSeverity, BoundaryId, CandidateDerivationMode,
        ClaimId, EvidenceId, IntentId, PhaseIndicator, PolicyId, ProcessId, ResponsibilityClaim,
        ResponsibilityClaimState, ResponsibilityClaimType, SignalClass,
    };

    fn action(id: &str, action_type: ActionCandidateType, path: Option<&str>) -> Action {
        Action {
            outcome: ActionOutcome::Succeeded,
            syscall_ret: Some(0),
            syscall_name: None,
            action_id: ActionId::from(id),
            process_id: ProcessId::new(1, 1),
            action_type,
            normalized_args: path
                .map(|path| vec![NormalizedArg::Path(path.into())])
                .unwrap_or_default(),
            derivation_mode: CandidateDerivationMode::Direct,
            signal_class: SignalClass::HighSignal,
            timestamp_ns: 1,
        }
    }

    fn evidence(id: &str, action: &str, level: MatchLevel, confidence: f32) -> CorrelationEvidence {
        CorrelationEvidence {
            evidence_id: EvidenceId::from(id),
            intent_id: IntentId::from("intent-1"),
            action_id: ActionId::from(action),
            match_level: level,
            confidence,
            matched_fields: Default::default(),
            derivation_mode: CandidateDerivationMode::Direct,
            phase: PhaseIndicator::Provisional { timestamp_ns: 1 },
            timestamp_ns: 1,
        }
    }

    fn claim(evidence_id: &str) -> ResponsibilityClaim {
        ResponsibilityClaim {
            claim_id: ClaimId::from("claim-1"),
            claim_type: ResponsibilityClaimType::PolicyViolation,
            subject: AgentId::from("agent-1"),
            policy_id: PolicyId::from("policy-1"),
            boundary_id: BoundaryId::from("boundary-1"),
            claim_state: ResponsibilityClaimState::Observed,
            severity: AlertSeverity::Warning,
            confidence: 0.5,
            evidence_refs: vec![EvidenceId::from(evidence_id)],
            competing_explanations: Vec::new(),
            traceback_ref: None,
            phase: PhaseIndicator::Authoritative {
                timestamp_ns: 1,
                overrides: None,
            },
        }
    }

    #[test]
    fn ordinary_action_keeps_only_the_best_row() {
        let actions = vec![action(
            "a1",
            ActionCandidateType::FileOpen,
            Some("/tmp/plain"),
        )];
        let evidence = vec![
            evidence("e1", "a1", MatchLevel::L3Containment, 0.65),
            evidence("e2", "a1", MatchLevel::L2Normalized, 0.75),
            evidence("e3", "a1", MatchLevel::L5Substring, 0.35),
        ];
        let selected = select_for_persistence(&evidence, &actions, &[]);
        assert_eq!(selected.len(), 1);
        assert_eq!(selected[0].evidence_id.as_str(), "e2");
    }

    #[test]
    fn security_action_retains_the_bounded_budget() {
        let actions = vec![action("a1", ActionCandidateType::NetworkConnect, None)];
        let evidence: Vec<_> = (0..12)
            .map(|i| evidence(&format!("e{i}"), "a1", MatchLevel::L3Containment, 0.65))
            .collect();
        let selected = select_for_persistence(&evidence, &actions, &[]);
        assert_eq!(selected.len(), SECURITY_BUDGET);
    }

    #[test]
    fn claim_reference_forces_a_row_that_ranked_below_the_budget() {
        let actions = vec![action(
            "a1",
            ActionCandidateType::FileOpen,
            Some("/tmp/plain"),
        )];
        let evidence = vec![
            evidence("best", "a1", MatchLevel::L1Exact, 0.9),
            evidence("weak", "a1", MatchLevel::L5Substring, 0.35),
        ];
        let selected = select_for_persistence(&evidence, &actions, &[claim("weak")]);
        assert_eq!(selected.len(), 2);
        assert!(
            selected
                .iter()
                .any(|item| item.evidence_id.as_str() == "weak")
        );
    }

    #[test]
    fn different_actions_keep_their_own_best_row() {
        let actions = vec![
            action("a1", ActionCandidateType::FileOpen, Some("/tmp/plain")),
            action("a2", ActionCandidateType::FileOpen, Some("/tmp/other")),
        ];
        let evidence = vec![
            evidence("e1", "a1", MatchLevel::L1Exact, 0.9),
            evidence("e2", "a1", MatchLevel::L5Substring, 0.35),
            evidence("e3", "a2", MatchLevel::L1Exact, 0.9),
        ];
        let selected = select_for_persistence(&evidence, &actions, &[]);
        assert_eq!(selected.len(), 2);
    }
}
