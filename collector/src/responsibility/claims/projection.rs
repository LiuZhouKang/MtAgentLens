// SPDX-License-Identifier: MIT
//
// projection.rs — ClaimProjector: projects a ResponsibilityAlert into a
// ProjectedAlert suitable for API responses (lightweight, de-normalized).

use serde::{Deserialize, Serialize};

use crate::responsibility::types::{AlertAuthority, AlertDecision, AlertSeverity};

// ═════════════════════════════════════════════════════════════════════════════
// ProjectedAlert
// ═════════════════════════════════════════════════════════════════════════════

/// A flattened, API-friendly representation of a responsibility alert.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProjectedAlert {
    pub alert_id: String,
    pub agent_id: String,
    pub timestamp_ns: u64,
    pub decision: String,
    pub severity: String,
    pub confidence: f32,
    pub authority: String,
    pub title: String,
    pub summary: String,
    pub action_hint: Option<String>,
    pub engine_score: f32,
    pub engine_route: String,
    pub timeline_step_count: usize,
    pub graph_node_count: usize,
}

// ═════════════════════════════════════════════════════════════════════════════
// ClaimProjector
// ═════════════════════════════════════════════════════════════════════════════

/// Projects `ResponsibilityAlert` instances into `ProjectedAlert` for API use.
pub struct ClaimProjector;

impl ClaimProjector {
    /// Project a single alert.
    pub fn project(
        alert: &crate::responsibility::types::ResponsibilityAlert,
    ) -> ProjectedAlert {
        ProjectedAlert {
            alert_id: alert.alert_id.to_string(),
            agent_id: alert.primary_agent.agent_id.to_string(),
            timestamp_ns: alert.timestamp_ns,
            decision: alert_decision_label(&alert.decision).to_string(),
            severity: alert_severity_label(&alert.severity).to_string(),
            confidence: alert.confidence,
            authority: alert_authority_label(&alert.authority).to_string(),
            title: alert.title.clone(),
            summary: alert.summary.clone(),
            action_hint: alert.action_hint.clone(),
            engine_score: alert.engine.classification_score,
            engine_route: alert.engine.classification_route.clone(),
            timeline_step_count: alert.timeline.steps.len(),
            graph_node_count: alert.graph_snapshot.metrics.node_counts.values().sum(),
        }
    }

    /// Project a batch of alerts.
    pub fn project_batch(
        alerts: &[crate::responsibility::types::ResponsibilityAlert],
    ) -> Vec<ProjectedAlert> {
        alerts.iter().map(Self::project).collect()
    }
}

// ═════════════════════════════════════════════════════════════════════════════
// Label helpers
// ═════════════════════════════════════════════════════════════════════════════

fn alert_decision_label(dec: &AlertDecision) -> &'static str {
    match dec {
        AlertDecision::Safe => "safe",
        AlertDecision::Unsafe => "unsafe",
        AlertDecision::Uncertain => "uncertain",
    }
}

fn alert_severity_label(sev: &AlertSeverity) -> &'static str {
    match sev {
        AlertSeverity::Info => "info",
        AlertSeverity::Warning => "warning",
        AlertSeverity::Critical => "critical",
    }
}

fn alert_authority_label(auth: &AlertAuthority) -> &'static str {
    match auth {
        AlertAuthority::Authoritative => "authoritative",
        AlertAuthority::Supporting => "supporting",
        AlertAuthority::Unavailable => "unavailable",
    }
}

// ═════════════════════════════════════════════════════════════════════════════
// Tests
// ═════════════════════════════════════════════════════════════════════════════

#[cfg(test)]
mod tests {
    use super::*;
    use crate::responsibility::types::{
        AgentId, AgentRef, AlertId, BehaviorTimeline, BehaviorTimelineEntry,
        DisplayProfile, EngineSection, GraphMetrics, GraphSnapshot, PhaseIndicator,
        ResponsibilityAlert, RuleVerdict, PatternVerdict, ScoreBreakdown,
    };

    fn make_alert() -> ResponsibilityAlert {
        ResponsibilityAlert {
            alert_id: AlertId::from("alert-001"),
            timestamp_ns: 5000,
            display_profile: DisplayProfile::ObserverJudged,
            decision: AlertDecision::Unsafe,
            severity: AlertSeverity::Critical,
            confidence: 0.92,
            authority: AlertAuthority::Supporting,
            reasoning: "Observer LLM found concerning patterns".to_string(),
            title: "Observer reviewed: agent agent-1 — Policy Violation".to_string(),
            summary: "Analysis of responsibility graph: 1 agent(s)...".to_string(),
            action_hint: Some("Immediate review required".to_string()),
            primary_agent: AgentRef {
                agent_id: AgentId::from("agent-1"),
                claimed_role: Some("developer".to_string()),
            },
            involved_agents: vec![],
            engine: EngineSection {
                rule_verdicts: vec![RuleVerdict {
                    rule_id: "rule.shadow".into(),
                    description: "Access to /etc/shadow".into(),
                    matched_resource: "/etc/shadow".into(),
                    llm_agreed: Some(true),
                    llm_comment: Some("Confirmed by observer".into()),
                }],
                pattern_verdicts: vec![PatternVerdict {
                    template_id: "tpl.credential_theft".into(),
                    match_confidence: 0.85,
                    llm_agreed: Some(true),
                    llm_comment: None,
                }],
                classification_score: 0.75,
                classification_route: "observer".to_string(),
                score_breakdown: ScoreBreakdown {
                    rule_score: 0.30,
                    pattern_score: 0.20,
                    boundary_score: 0.15,
                    evidence_score: 0.10,
                    role_score: 0.0,
                    repetition_score: 0.0,
            behavioral_score: 0.0,
                    total: 0.75,
                },
                cusum_triggered: false,
                cusum_dimensions: vec![],
                progressive_fused: 0.0,
                observer_trigger_reason: String::new(),
                deberta_score: 0.0,
            },
            timeline: BehaviorTimeline {
                steps: vec![
                    BehaviorTimelineEntry {
                        timestamp_ns: 1000,
                        event_type: "intent".into(),
                        description: "Read /etc/shadow".into(),
                        resource: Some("/etc/shadow".into()),
                        evidence_ref: None,
                        ..Default::default()
                    },
                    BehaviorTimelineEntry {
                        timestamp_ns: 2000,
                        event_type: "action".into(),
                        description: "openat /etc/shadow".into(),
                        resource: Some("/etc/shadow".into()),
                        evidence_ref: None,
                        ..Default::default()
                    },
                ],
                total_duration_ms: 1,
            },
            graph_snapshot: {
                let mut node_counts = std::collections::HashMap::new();
                node_counts.insert("Agent".to_string(), 1);
                node_counts.insert("Resource".to_string(), 2);
                node_counts.insert("PolicyBoundary".to_string(), 1);
                GraphSnapshot {
                    snapshot_id: "test-snap".into(),
                    timestamp_ns: 0,
                    nodes: vec![],
                    edges: vec![],
                    metrics: GraphMetrics {
                        node_counts,
                        edge_counts: std::collections::HashMap::new(),
                        agent_count: 1,
                        cross_agent_edges: 0,
                        delegation_depth: 0,
                        taint_chains: 0,
                        avg_confidence: 0.85,
                    },
                }
            },
            phase: PhaseIndicator::Provisional { timestamp_ns: 5000 },
            provisional_ref: None,
        }
    }

    #[test]
    fn test_project_single_alert() {
        let alert = make_alert();
        let projected = ClaimProjector::project(&alert);

        assert_eq!(projected.alert_id, "alert-001");
        assert_eq!(projected.agent_id, "agent-1");
        assert_eq!(projected.timestamp_ns, 5000);
        assert_eq!(projected.decision, "unsafe");
        assert_eq!(projected.severity, "critical");
        assert_eq!(projected.confidence, 0.92);
        assert_eq!(projected.authority, "supporting");
        assert_eq!(projected.engine_score, 0.75);
        assert_eq!(projected.engine_route, "observer");
        assert_eq!(projected.timeline_step_count, 2);
        assert_eq!(projected.graph_node_count, 4); // 1 + 2 + 1
        assert_eq!(projected.action_hint, Some("Immediate review required".to_string()));
    }

    #[test]
    fn test_project_batch() {
        let alert = make_alert();
        let alerts = vec![alert.clone(), alert];
        let projected = ClaimProjector::project_batch(&alerts);
        assert_eq!(projected.len(), 2);
        assert_eq!(projected[0].alert_id, "alert-001");
        assert_eq!(projected[1].alert_id, "alert-001");
    }

    #[test]
    fn test_project_empty_action_hint() {
        let mut alert = make_alert();
        alert.action_hint = None;
        let projected = ClaimProjector::project(&alert);
        assert_eq!(projected.action_hint, None);
    }

    #[test]
    fn test_label_helpers() {
        assert_eq!(alert_decision_label(&AlertDecision::Safe), "safe");
        assert_eq!(alert_decision_label(&AlertDecision::Unsafe), "unsafe");
        assert_eq!(alert_decision_label(&AlertDecision::Uncertain), "uncertain");

        assert_eq!(alert_severity_label(&AlertSeverity::Info), "info");
        assert_eq!(alert_severity_label(&AlertSeverity::Warning), "warning");
        assert_eq!(alert_severity_label(&AlertSeverity::Critical), "critical");

        assert_eq!(alert_authority_label(&AlertAuthority::Authoritative), "authoritative");
        assert_eq!(alert_authority_label(&AlertAuthority::Supporting), "supporting");
        assert_eq!(alert_authority_label(&AlertAuthority::Unavailable), "unavailable");
    }
}
