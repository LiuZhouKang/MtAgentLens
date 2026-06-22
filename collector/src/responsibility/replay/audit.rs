// SPDX-License-Identifier: MIT
//
// audit.rs — AuditSummary generation from replay results.
//
// Provides a concise, human-readable summary of an authoritative replay,
// including agent counts, alert statistics, evidence metrics, and refinement
// breakdowns. Intended for consumption by the web server, evaluation scripts,
// and run tear-down reporting.

use crate::responsibility::replay::executor::ReplayResult;
use crate::responsibility::types::{AlertSeverity, IdentityRefinementType};
use serde::{Deserialize, Serialize};

// ═══════════════════════════════════════════════════════════════════════════
// AuditSummary
// ═══════════════════════════════════════════════════════════════════════════

/// A concise summary of an authoritative replay for audit consumption.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AuditSummary {
    /// Run identifier.
    pub run_id: String,

    /// Agent summary.
    pub agent_count: usize,
    pub subagent_boundary_count: usize,

    /// Alert statistics.
    pub total_alerts: usize,
    pub critical_alerts: usize,
    pub warning_alerts: usize,
    pub info_alerts: usize,

    /// Evidence statistics.
    pub evidence_count: usize,
    pub non_link_count: usize,
    pub avg_evidence_confidence: f32,
    pub max_evidence_confidence: f32,

    /// Graph dimensions.
    pub node_count: usize,
    pub edge_count: usize,

    /// Refinement summary.
    pub total_refinements: usize,
    pub identity_refinements: usize,
    pub evidence_refinements: usize,
    pub graph_refinements: usize,
    pub classification_refinements: usize,
    pub claim_refinements: usize,

    /// Identity resolution summary.
    pub agents_confirmed: usize,
    pub agents_merged: usize,
    pub agents_split: usize,
    pub agents_dismissed: usize,
    pub unresolved_processes: usize,

    /// Evaluation metrics snapshot.
    pub precision: f32,
    pub recall: f32,
    pub f1: f32,

    /// Human-readable summary text.
    pub summary_text: String,
}

// ═══════════════════════════════════════════════════════════════════════════
// AuditSummary generation
// ═══════════════════════════════════════════════════════════════════════════

impl AuditSummary {
    /// Generate an audit summary from a replay result.
    pub fn from_replay(result: &ReplayResult) -> Self {
        let eval = &result.evaluation;

        // Agent statistics from authoritative identities
        let agent_count = result.identities.len();
        let subagent_boundary_count = result
            .refinements
            .identity
            .iter()
            .filter(|r| matches!(r.refinement_type, IdentityRefinementType::Split))
            .count();

        // Alert severity breakdown
        let critical_alerts = result
            .alerts
            .iter()
            .filter(|a| matches!(a.severity, AlertSeverity::Critical))
            .count();
        let warning_alerts = result
            .alerts
            .iter()
            .filter(|a| matches!(a.severity, AlertSeverity::Warning))
            .count();
        let info_alerts = result
            .alerts
            .iter()
            .filter(|a| matches!(a.severity, AlertSeverity::Info))
            .count();

        // Evidence confidence
        let avg_confidence = if eval.evidence_count > 0 {
            result
                .evidence
                .iter()
                .map(|e| e.confidence)
                .sum::<f32>()
                / eval.evidence_count as f32
        } else {
            0.0
        };
        let max_confidence = result
            .evidence
            .iter()
            .map(|e| e.confidence)
            .fold(0.0f32, f32::max);

        // Identity resolution summary
        let agents_confirmed = result
            .refinements
            .identity
            .iter()
            .filter(|r| matches!(r.refinement_type, IdentityRefinementType::Confirmed))
            .count();
        let agents_merged = result
            .refinements
            .identity
            .iter()
            .filter(|r| matches!(r.refinement_type, IdentityRefinementType::Merged))
            .count();
        let agents_split = subagent_boundary_count;
        let agents_dismissed = result
            .refinements
            .identity
            .iter()
            .filter(|r| matches!(r.refinement_type, IdentityRefinementType::Dismissed))
            .count();
        let unresolved_processes = result
            .refinements
            .identity
            .iter()
            .filter(|r| matches!(r.refinement_type, IdentityRefinementType::Reassigned))
            .count();

        let total_refinements = result.refinements.identity.len()
            + result.refinements.evidence.len()
            + result.refinements.graph.len()
            + result.refinements.classification.len()
            + result.refinements.claim.len();

        // Build human-readable summary
        let summary_text = format!(
            "Run {}: {} agent(s), {} alert(s) ({} critical, {} warning, {} info). \
             {} evidence links (avg confidence {:.2}, max {:.2}). \
             {} non-links. {} nodes, {} edges. \
             Precision={:.2}, Recall={:.2}, F1={:.2}. \
             {} refinements total ({} identity, {} evidence, {} graph, {} classification, {} claim).",
            result.run_id,
            agent_count,
            result.alerts.len(),
            critical_alerts,
            warning_alerts,
            info_alerts,
            eval.evidence_count,
            avg_confidence,
            max_confidence,
            eval.non_link_count,
            eval.node_count,
            eval.edge_count,
            eval.precision,
            eval.recall,
            eval.f1,
            total_refinements,
            result.refinements.identity.len(),
            result.refinements.evidence.len(),
            result.refinements.graph.len(),
            result.refinements.classification.len(),
            result.refinements.claim.len(),
        );

        Self {
            run_id: result.run_id.clone(),
            agent_count,
            subagent_boundary_count,
            total_alerts: result.alerts.len(),
            critical_alerts,
            warning_alerts,
            info_alerts,
            evidence_count: eval.evidence_count,
            non_link_count: eval.non_link_count,
            avg_evidence_confidence: avg_confidence,
            max_evidence_confidence: max_confidence,
            node_count: eval.node_count,
            edge_count: eval.edge_count,
            total_refinements,
            identity_refinements: result.refinements.identity.len(),
            evidence_refinements: result.refinements.evidence.len(),
            graph_refinements: result.refinements.graph.len(),
            classification_refinements: result.refinements.classification.len(),
            claim_refinements: result.refinements.claim.len(),
            agents_confirmed,
            agents_merged,
            agents_split,
            agents_dismissed,
            unresolved_processes,
            precision: eval.precision,
            recall: eval.recall,
            f1: eval.f1,
            summary_text,
        }
    }
}

// ═══════════════════════════════════════════════════════════════════════════
// Tests
// ═══════════════════════════════════════════════════════════════════════════

#[cfg(test)]
mod tests {
    use super::*;
    use crate::responsibility::replay::executor::{
        EvaluationMetrics, RefinementBundle, ReplayResult,
    };
    use crate::responsibility::graph::snapshot::FrozenGraph;
    use crate::responsibility::types::{
        AlertDecision, AlertId, AlertSeverity, DisplayProfile, EngineSection,
        GraphMetrics, GraphRefinementType, GraphSnapshot, IdentityRefinement,
        IdentityRefinementType, PhaseIndicator, ResponsibilityAlert, ScoreBreakdown,
    };

    fn make_minimal_replay_result() -> ReplayResult {
        let alert = ResponsibilityAlert {
            alert_id: AlertId::from("a1"),
            timestamp_ns: 1000,
            display_profile: DisplayProfile::UnsafeByEngine,
            decision: AlertDecision::Unsafe,
            severity: AlertSeverity::Critical,
            confidence: 0.95,
            authority: crate::responsibility::types::AlertAuthority::Authoritative,
            reasoning: "test".into(),
            title: "Test Alert".into(),
            summary: "Test summary".into(),
            action_hint: None,
            primary_agent: crate::responsibility::types::AgentRef {
                agent_id: crate::responsibility::types::AgentId::from("agent-1"),
                claimed_role: None,
            },
            involved_agents: Vec::new(),
            engine: EngineSection {
                rule_verdicts: Vec::new(),
                pattern_verdicts: Vec::new(),
                classification_score: 0.95,
                classification_route: "direct_unsafe".into(),
                score_breakdown: ScoreBreakdown::default(),
                cusum_triggered: false,
                cusum_dimensions: vec![],
                progressive_fused: 0.0,
                observer_trigger_reason: String::new(),
                deberta_score: 0.0,
            },
            timeline: crate::responsibility::types::BehaviorTimeline {
                steps: Vec::new(),
                total_duration_ms: 0,
            },
            graph_snapshot: GraphSnapshot::default(),
            phase: PhaseIndicator::Authoritative {
                timestamp_ns: 1000,
                overrides: None,
            },
            provisional_ref: Some(AlertId::from("p1")),
        };

        let refinements = RefinementBundle {
            identity: vec![
                IdentityRefinement {
                    phase1_agent_id: crate::responsibility::types::AgentId::from("a1"),
                    phase2_agent_id: crate::responsibility::types::AgentId::from("a1-auth"),
                    refinement_type: IdentityRefinementType::Confirmed,
                    reason: "test".into(),
                },
            ],
            ..Default::default()
        };

        let snapshot = GraphSnapshot {
            snapshot_id: "snap-1".into(),
            timestamp_ns: 1000,
            nodes: Vec::new(),
            edges: Vec::new(),
            metrics: GraphMetrics::default(),
        };

        ReplayResult {
            run_id: "run-test".into(),
            identities: Vec::new(),
            evidence: Vec::new(),
            frozen_graph: FrozenGraph::new(snapshot, Vec::new(), None),
            alerts: vec![alert],
            claims: Vec::new(),
            tracebacks: Vec::new(),
            refinements,
            evaluation: EvaluationMetrics {
                phase1_alert_count: 1,
                phase2_alert_count: 1,
                confirmed_count: 1,
                evidence_count: 5,
                non_link_count: 2,
                node_count: 10,
                edge_count: 15,
                claim_count: 3,
                traceback_count: 2,
                precision: 1.0,
                recall: 1.0,
                f1: 1.0,
                ..Default::default()
            },
        }
    }

    #[test]
    fn test_audit_summary_generation() {
        let result = make_minimal_replay_result();
        let summary = AuditSummary::from_replay(&result);

        assert_eq!(summary.run_id, "run-test");
        assert_eq!(summary.total_alerts, 1);
        assert_eq!(summary.critical_alerts, 1);
        assert_eq!(summary.warning_alerts, 0);
        assert_eq!(summary.info_alerts, 0);
        assert_eq!(summary.evidence_count, 5);
        assert_eq!(summary.node_count, 10);
        assert_eq!(summary.edge_count, 15);
        assert_eq!(summary.precision, 1.0);
        assert_eq!(summary.recall, 1.0);
        assert_eq!(summary.f1, 1.0);
        assert!(summary.summary_text.contains("run-test"));
        assert!(summary.summary_text.contains("Precision=1.00"));
    }

    #[test]
    fn test_audit_summary_serialization() {
        let result = make_minimal_replay_result();
        let summary = AuditSummary::from_replay(&result);

        let json = serde_json::to_string(&summary).unwrap();
        let parsed: AuditSummary = serde_json::from_str(&json).unwrap();

        assert_eq!(parsed.run_id, "run-test");
        assert_eq!(parsed.total_alerts, 1);
        assert_eq!(parsed.f1, 1.0);
    }

    #[test]
    fn test_empty_replay_produces_zero_summary() {
        let snapshot = GraphSnapshot {
            snapshot_id: "empty".into(),
            timestamp_ns: 0,
            nodes: Vec::new(),
            edges: Vec::new(),
            metrics: GraphMetrics::default(),
        };

        let result = ReplayResult {
            run_id: "empty-run".into(),
            identities: Vec::new(),
            evidence: Vec::new(),
            frozen_graph: FrozenGraph::new(snapshot, Vec::new(), None),
            alerts: Vec::new(),
            claims: Vec::new(),
            tracebacks: Vec::new(),
            refinements: RefinementBundle::default(),
            evaluation: EvaluationMetrics::default(),
        };

        let summary = AuditSummary::from_replay(&result);
        assert_eq!(summary.total_alerts, 0);
        assert_eq!(summary.critical_alerts, 0);
        assert_eq!(summary.evidence_count, 0);
        assert_eq!(summary.avg_evidence_confidence, 0.0);
        assert_eq!(summary.max_evidence_confidence, 0.0);
        assert!(summary.summary_text.contains("empty-run"));
    }
}
