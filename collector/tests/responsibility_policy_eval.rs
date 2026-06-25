use mtagentlens::responsibility::judgment::policy::evaluate_boundary_policy;
use mtagentlens::responsibility::types::support_types::{
    BoundaryId, PolicyId, ResponsibilityClaimState,
};

// ── Legacy-wrapper tests (now ResourceId-dispatched) ─────────────────

#[test]
fn file_read_direct_evaluates_to_observed_policy_match() {
    let result = evaluate_boundary_policy(
        "agent-1",
        "link-1",
        Some("file_read"),
        Some("direct"),
        Some("/tmp/demo.txt"),
    );

    assert!(result.matched);
    // ResourceId dispatch: file → file_read boundary
    assert_eq!(
        result.policy_id,
        Some(PolicyId("policy.file.read".to_string()))
    );
    assert_eq!(
        result.boundary_id,
        Some(BoundaryId("boundary.file_read".to_string()))
    );
    // derivation_mode="direct" promotes to Observed
    assert_eq!(result.claim_state, ResponsibilityClaimState::Observed);
}

#[test]
fn file_read_with_sensitive_path_becomes_observed() {
    let result = evaluate_boundary_policy(
        "agent-1",
        "link-1",
        Some("file_read"),
        Some("direct"),
        Some("/etc/shadow"),
    );

    assert!(result.matched);
    assert_eq!(
        result.policy_id,
        Some(PolicyId("policy.file.read".to_string()))
    );
    assert_eq!(result.claim_state, ResponsibilityClaimState::Observed);
    assert!(result.reason.contains("sensitive") || result.reason.contains("capability"));
}

#[test]
fn process_exec_indirect_evaluates_to_inferred_policy_match() {
    let result = evaluate_boundary_policy(
        "agent-1",
        "link-2",
        Some("process_exec"),
        Some("aggregated"),
        Some("/usr/bin/python3"),
    );

    assert!(result.matched);
    // ResourceId dispatch: process → process_exec boundary
    assert_eq!(
        result.policy_id,
        Some(PolicyId("policy.process.exec".to_string()))
    );
    assert_eq!(
        result.boundary_id,
        Some(BoundaryId("boundary.process_exec".to_string()))
    );
    assert_eq!(result.claim_state, ResponsibilityClaimState::Inferred);
}

#[test]
fn network_connect_with_suspicious_target_becomes_observed() {
    let result = evaluate_boundary_policy(
        "agent-1",
        "link-3",
        Some("network_connect"),
        Some("direct"),
        Some("malicious.example.com:4444"),
    );

    assert!(result.matched);
    assert_eq!(
        result.policy_id,
        Some(PolicyId("policy.network.connect".to_string()))
    );
    assert_eq!(result.claim_state, ResponsibilityClaimState::Observed);
}

#[test]
fn unknown_candidate_type_treated_as_tool_call_and_matches() {
    // "Bash" is not a known candidate_type, so it's treated as a
    // ResourceId::ToolCall. Tool calls match the capability boundary.
    let result = evaluate_boundary_policy("agent-1", "link-4", Some("Bash"), Some("direct"), None);

    assert!(result.matched);
    assert_eq!(
        result.policy_id,
        Some(PolicyId("policy.capability.tool".to_string()))
    );
    // derivation_mode="direct" promotes to Observed
    assert_eq!(result.claim_state, ResponsibilityClaimState::Observed);
}

#[test]
fn none_candidate_type_fails_closed_as_unproven() {
    let result = evaluate_boundary_policy("agent-1", "link-5", None, None, None);

    assert!(!result.matched);
    assert_eq!(result.policy_id, None);
    assert_eq!(result.boundary_id, None);
    assert_eq!(result.claim_state, ResponsibilityClaimState::Unproven);
}

#[test]
fn orchestration_tool_triggers_observed() {
    let result = evaluate_boundary_policy(
        "agent-1",
        "link-6",
        Some("TaskCreate"),
        Some("direct"),
        None,
    );

    assert!(result.matched);
    assert_eq!(
        result.policy_id,
        Some(PolicyId("policy.capability.tool".to_string()))
    );
    assert_eq!(result.claim_state, ResponsibilityClaimState::Observed);
    assert!(result.reason.contains("orchestration"));
}
