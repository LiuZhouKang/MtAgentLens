// SPDX-License-Identifier: MIT
//
// text.rs — AlertBuilderText: generates human-readable title, summary, and
// action_hint strings from ResponsibilityClaims and display profiles.

use crate::responsibility::graph::ResponsibilityGraph;
use crate::responsibility::types::{
    AgentId, DisplayProfile, ResponsibilityClaim, ResponsibilityClaimState, ResponsibilityClaimType,
};

// ═════════════════════════════════════════════════════════════════════════════
// AlertBuilderText
// ═════════════════════════════════════════════════════════════════════════════

/// Stateless text generator for responsibility alert display strings.
pub struct AlertBuilderText;

impl AlertBuilderText {
    /// Generate a concise title for a claim under a given display profile.
    pub fn generate_title(claim: Option<&ResponsibilityClaim>, profile: &DisplayProfile) -> String {
        let claim = match claim {
            Some(c) => c,
            None => {
                return match profile {
                    DisplayProfile::SafeBaseline | DisplayProfile::UnsafeByEngine => {
                        "Agent activity observed — no policy violations detected".to_string()
                    }
                    DisplayProfile::ObserverJudged => {
                        "Observer review complete — no claims generated".to_string()
                    }
                };
            }
        };

        let agent_label = &claim.subject;
        let type_label = claim_type_label(&claim.claim_type);

        match profile {
            DisplayProfile::SafeBaseline => {
                format!(
                    "Baseline safe: agent {} — {} ({}% confidence)",
                    truncate_agent(agent_label),
                    type_label,
                    (claim.confidence * 100.0) as i32,
                )
            }
            DisplayProfile::UnsafeByEngine => {
                format!(
                    "Engine alert: agent {} — {} (severity: {:?})",
                    truncate_agent(agent_label),
                    type_label,
                    claim.severity,
                )
            }
            DisplayProfile::ObserverJudged => {
                format!(
                    "Observer reviewed: agent {} — {} ({:?})",
                    truncate_agent(agent_label),
                    type_label,
                    claim.claim_state,
                )
            }
        }
    }

    /// Generate a multi-sentence summary covering all claims and graph context.
    pub fn generate_summary(claims: &[ResponsibilityClaim], graph: &ResponsibilityGraph) -> String {
        if claims.is_empty() {
            return "No responsibility claims detected — graph appears safe.".to_string();
        }

        let agent_count = graph.metrics().agent_count;
        let edge_count = graph.edge_count();
        let node_count = graph.node_count();

        let mut parts: Vec<String> = Vec::new();

        // Opening context.
        parts.push(format!(
            "Analysis of responsibility graph: {} agent(s), {} node(s), {} edge(s).",
            agent_count, node_count, edge_count,
        ));

        // Summarize claims by type and state.
        let violation_count = claims
            .iter()
            .filter(|c| matches!(c.claim_type, ResponsibilityClaimType::PolicyViolation))
            .count();
        let taint_count = claims
            .iter()
            .filter(|c| matches!(c.claim_type, ResponsibilityClaimType::TaintedContextUse))
            .count();
        let delegation_count = claims
            .iter()
            .filter(|c| matches!(c.claim_type, ResponsibilityClaimType::SuspiciousDelegation))
            .count();
        let blocked_count = claims
            .iter()
            .filter(|c| matches!(c.claim_type, ResponsibilityClaimType::BlockedAttempt))
            .count();
        let capability_count = claims
            .iter()
            .filter(|c| {
                matches!(
                    c.claim_type,
                    ResponsibilityClaimType::UnauthorizedCapabilityUse
                )
            })
            .count();

        let mut detail = String::new();
        if violation_count > 0 {
            detail.push_str(&format!("{} policy violation(s)", violation_count));
        }
        if taint_count > 0 {
            if !detail.is_empty() {
                detail.push_str(", ");
            }
            detail.push_str(&format!("{} tainted context use(s)", taint_count));
        }
        if delegation_count > 0 {
            if !detail.is_empty() {
                detail.push_str(", ");
            }
            detail.push_str(&format!("{} suspicious delegation(s)", delegation_count));
        }
        if capability_count > 0 {
            if !detail.is_empty() {
                detail.push_str(", ");
            }
            detail.push_str(&format!(
                "{} unauthorized capability use(s)",
                capability_count
            ));
        }
        if blocked_count > 0 {
            if !detail.is_empty() {
                detail.push_str(", ");
            }
            detail.push_str(&format!("{} blocked attempt(s)", blocked_count));
        }

        if detail.is_empty() {
            parts.push("No policy violations detected.".to_string());
        } else {
            parts.push(format!("Detected: {}.", detail));
        }

        // Add per-claim detail.
        for (i, claim) in claims.iter().enumerate().take(5) {
            let state_label = claim_state_label(&claim.claim_state);
            parts.push(format!(
                "  Claim #{}: {} by agent {} — {} (confidence {:.0}%)",
                i + 1,
                claim_type_label(&claim.claim_type),
                truncate_agent(&claim.subject),
                state_label,
                claim.confidence * 100.0,
            ));
        }
        if claims.len() > 5 {
            parts.push(format!("  ... and {} more claim(s).", claims.len() - 5));
        }

        // Add competing explanation context.
        let has_competing = claims.iter().any(|c| !c.competing_explanations.is_empty());
        if has_competing {
            parts.push(
                "Competing explanations exist for some claims — review is recommended.".to_string(),
            );
        }

        // Confidence assessment.
        let avg_confidence: f32 = if claims.is_empty() {
            0.0
        } else {
            claims.iter().map(|c| c.confidence).sum::<f32>() / claims.len() as f32
        };
        if avg_confidence > 0.80 {
            parts.push("Overall high confidence in claim assessments.".to_string());
        } else if avg_confidence > 0.50 {
            parts.push("Moderate confidence — further investigation may be warranted.".to_string());
        } else {
            parts.push(
                "Low confidence — these claims are tentative and require verification.".to_string(),
            );
        }

        parts.join(" ")
    }

    /// Generate an action hint for a claim, or None if no specific action is needed.
    pub fn generate_action_hint(claim: Option<&ResponsibilityClaim>) -> Option<String> {
        let claim = claim?;

        let hint = match (&claim.claim_type, &claim.claim_state) {
            (ResponsibilityClaimType::PolicyViolation, ResponsibilityClaimState::Confirmed) => {
                format!(
                    "Immediate review required: investigate agent {} for policy violation",
                    truncate_agent(&claim.subject),
                )
            }
            (ResponsibilityClaimType::PolicyViolation, ResponsibilityClaimState::Observed) => {
                format!(
                    "Review agent {} activity — policy violation patterns detected",
                    truncate_agent(&claim.subject),
                )
            }
            (ResponsibilityClaimType::PolicyViolation, _) => {
                format!(
                    "Monitor agent {} — possible policy issues (low confidence)",
                    truncate_agent(&claim.subject),
                )
            }
            (ResponsibilityClaimType::TaintedContextUse, _) => {
                format!(
                    "Investigate agent {} for use of contaminated context artifacts",
                    truncate_agent(&claim.subject),
                )
            }
            (ResponsibilityClaimType::SuspiciousDelegation, _) => {
                format!(
                    "Audit delegation chain involving agent {}",
                    truncate_agent(&claim.subject),
                )
            }
            (ResponsibilityClaimType::UnauthorizedCapabilityUse, _) => {
                format!(
                    "Revoke or review unauthorized capability access for agent {}",
                    truncate_agent(&claim.subject),
                )
            }
            (ResponsibilityClaimType::BlockedAttempt, _) => {
                format!(
                    "No immediate action required — blocked attempt by agent {}",
                    truncate_agent(&claim.subject),
                )
            }
            (ResponsibilityClaimType::ResponsibilityAnomaly, _) => {
                format!(
                    "Investigate responsibility anomaly for agent {}",
                    truncate_agent(&claim.subject),
                )
            }
        };

        Some(hint)
    }
}

// ═════════════════════════════════════════════════════════════════════════════
// Helpers
// ═════════════════════════════════════════════════════════════════════════════

fn claim_type_label(ct: &ResponsibilityClaimType) -> &'static str {
    match ct {
        ResponsibilityClaimType::PolicyViolation => "Policy Violation",
        ResponsibilityClaimType::ResponsibilityAnomaly => "Responsibility Anomaly",
        ResponsibilityClaimType::SuspiciousDelegation => "Suspicious Delegation",
        ResponsibilityClaimType::TaintedContextUse => "Tainted Context Use",
        ResponsibilityClaimType::UnauthorizedCapabilityUse => "Unauthorized Capability Use",
        ResponsibilityClaimType::BlockedAttempt => "Blocked Attempt",
    }
}

fn claim_state_label(cs: &ResponsibilityClaimState) -> &'static str {
    match cs {
        ResponsibilityClaimState::Observed => "observed",
        ResponsibilityClaimState::Inferred => "inferred",
        ResponsibilityClaimState::Ambiguous => "ambiguous",
        ResponsibilityClaimState::Unproven => "unproven",
        ResponsibilityClaimState::Confirmed => "confirmed",
        ResponsibilityClaimState::BlockedAttempt => "blocked",
    }
}

fn truncate_agent(agent_id: &AgentId) -> &str {
    let s: &str = agent_id.as_str();
    // Show at most the first 12 characters for display.
    if s.len() > 12 { &s[..12] } else { s }
}

// ═════════════════════════════════════════════════════════════════════════════
// Tests
// ═════════════════════════════════════════════════════════════════════════════

#[cfg(test)]
mod tests {
    use super::*;
    use crate::responsibility::types::{
        AgentId, AlertSeverity, BoundaryId, ClaimId, CompetingExplanation, PhaseIndicator,
        PolicyId, ResponsibilityClaim, ResponsibilityClaimState, ResponsibilityClaimType,
    };

    fn make_claim(
        claim_type: ResponsibilityClaimType,
        state: ResponsibilityClaimState,
        confidence: f32,
        subject: &str,
    ) -> ResponsibilityClaim {
        ResponsibilityClaim {
            claim_id: ClaimId::new(),
            claim_type,
            subject: AgentId::from(subject),
            policy_id: PolicyId::from("policy.test"),
            boundary_id: BoundaryId::from("boundary.test"),
            claim_state: state,
            severity: AlertSeverity::Warning,
            confidence,
            evidence_refs: vec![],
            competing_explanations: vec![],
            traceback_ref: None,
            phase: PhaseIndicator::Provisional { timestamp_ns: 0 },
        }
    }

    #[test]
    fn test_generate_title_safe_baseline() {
        let claim = make_claim(
            ResponsibilityClaimType::PolicyViolation,
            ResponsibilityClaimState::Observed,
            0.75,
            "agent-abc123",
        );
        let title = AlertBuilderText::generate_title(Some(&claim), &DisplayProfile::SafeBaseline);
        assert!(title.contains("Baseline safe"));
        assert!(title.contains("agent-abc123"));
        assert!(title.contains("75%"));
    }

    #[test]
    fn test_generate_title_unsafe_by_engine() {
        let claim = make_claim(
            ResponsibilityClaimType::TaintedContextUse,
            ResponsibilityClaimState::Confirmed,
            0.90,
            "agent-xyz",
        );
        let title = AlertBuilderText::generate_title(Some(&claim), &DisplayProfile::UnsafeByEngine);
        assert!(title.contains("Engine alert"));
        assert!(title.contains("agent-xyz"));
        assert!(title.contains("Tainted Context Use"));
    }

    #[test]
    fn test_generate_title_observer_judged() {
        let claim = make_claim(
            ResponsibilityClaimType::SuspiciousDelegation,
            ResponsibilityClaimState::Inferred,
            0.60,
            "agent-delegate",
        );
        let title = AlertBuilderText::generate_title(Some(&claim), &DisplayProfile::ObserverJudged);
        assert!(title.contains("Observer reviewed"));
        assert!(title.contains("Suspicious Delegation"));
    }

    #[test]
    fn test_generate_title_none_claim() {
        let title = AlertBuilderText::generate_title(None, &DisplayProfile::SafeBaseline);
        assert_eq!(
            title,
            "Agent activity observed — no policy violations detected"
        );
    }

    #[test]
    fn test_generate_summary_empty() {
        let g = crate::responsibility::graph::ResponsibilityGraph::default();
        let summary = AlertBuilderText::generate_summary(&[], &g);
        assert!(summary.contains("No responsibility claims"));
    }

    #[test]
    fn test_generate_summary_with_claims() {
        let mut g = crate::responsibility::graph::ResponsibilityGraph::default();
        // Add a minimal agent node so metrics reflect it.
        let node = crate::responsibility::types::ResponsibilityNode {
            node_id: crate::responsibility::types::NodeId::from("n1"),
            node_type: crate::responsibility::types::ResponsibilityNodeType::Agent,
            agent_id: Some(AgentId::from("agent-1")),
            resource: None,
            evidence_refs: vec![],
            timestamp_ns: 1000,
            phase: PhaseIndicator::Provisional { timestamp_ns: 1000 },
        };
        g.add_node(node);

        let claims = vec![
            make_claim(
                ResponsibilityClaimType::PolicyViolation,
                ResponsibilityClaimState::Observed,
                0.80,
                "agent-1",
            ),
            make_claim(
                ResponsibilityClaimType::TaintedContextUse,
                ResponsibilityClaimState::Confirmed,
                0.90,
                "agent-1",
            ),
        ];

        let summary = AlertBuilderText::generate_summary(&claims, &g);
        assert!(summary.contains("1 policy violation"));
        assert!(summary.contains("1 tainted context use"));
        assert!(summary.contains("Claim #1"));
        assert!(summary.contains("Claim #2"));
    }

    #[test]
    fn test_generate_action_hint_policy_violation_confirmed() {
        let claim = make_claim(
            ResponsibilityClaimType::PolicyViolation,
            ResponsibilityClaimState::Confirmed,
            0.95,
            "agent-critical",
        );
        let hint = AlertBuilderText::generate_action_hint(Some(&claim));
        assert!(hint.is_some());
        assert!(hint.unwrap().contains("Immediate review required"));
    }

    #[test]
    fn test_generate_action_hint_blocked_attempt() {
        let claim = make_claim(
            ResponsibilityClaimType::BlockedAttempt,
            ResponsibilityClaimState::BlockedAttempt,
            0.33,
            "agent-blocked",
        );
        let hint = AlertBuilderText::generate_action_hint(Some(&claim));
        assert!(hint.is_some());
        assert!(hint.unwrap().contains("No immediate action required"));
    }

    #[test]
    fn test_generate_action_hint_none() {
        let hint = AlertBuilderText::generate_action_hint(None);
        assert!(hint.is_none());
    }
}
