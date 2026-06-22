// SPDX-License-Identifier: MIT
//
// evaluation.rs — EvaluationMetrics computation for Phase 1 → Phase 2
// progressive refinement assessment.
//
// Per spec §14, EvaluationMetrics captures:
//   - Phase 1 vs Phase 2 alert counts
//   - Refinement confirmation, change, dismissal, discovery counts
//   - Per-domain refinement breakdowns
//   - Precision, recall, F1 scores
//   - Evidence and non-link counts

use crate::responsibility::replay::executor::RefinementBundle;
use crate::responsibility::types::{
    ClassificationRefinementType, ClaimRefinementType, EvidenceRefinementType,
    GraphRefinementType, IdentityRefinementType,
};
use serde::{Deserialize, Serialize};

// ═══════════════════════════════════════════════════════════════════════════
// EvaluationMetrics
// ═══════════════════════════════════════════════════════════════════════════

/// Quantitative evaluation metrics for a run's Phase 1 → Phase 2 refinement.
///
/// Computed from refinement records and alert counts. Precision, recall, and
/// F1 are computed using the classification refinement counts:
///
///   precision = confirmed / (confirmed + changed + discovered)
///   recall    = confirmed / (confirmed + changed + dismissed)
///   f1        = 2 * precision * recall / (precision + recall)
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EvaluationMetrics {
    /// Number of Phase 1 (provisional) alerts.
    pub phase1_alert_count: usize,
    /// Number of Phase 2 (authoritative) alerts.
    pub phase2_alert_count: usize,

    /// Classification refinement counts.
    pub confirmed_count: usize,
    pub changed_count: usize,
    pub dismissed_count: usize,
    pub discovered_count: usize,

    /// Precision = confirmed / (confirmed + changed + discovered).
    pub precision: f32,
    /// Recall = confirmed / (confirmed + changed + dismissed).
    pub recall: f32,
    /// F1 = 2 * precision * recall / (precision + recall).
    pub f1: f32,

    /// Total correlation evidence count.
    pub evidence_count: usize,
    /// Total non-link diagnostic count.
    pub non_link_count: usize,
    /// Total authoritative identity count.
    pub identity_count: usize,
    /// Total frozen graph node count.
    pub node_count: usize,
    /// Total frozen graph edge count.
    pub edge_count: usize,
    /// Total authoritative claim count.
    pub claim_count: usize,
    /// Total traceback count.
    pub traceback_count: usize,

    /// Per-domain refinement breakdowns.
    pub identity_refinement_breakdown: IdentityRefinementBreakdown,
    pub evidence_refinement_breakdown: EvidenceRefinementBreakdown,
    pub graph_refinement_breakdown: GraphRefinementBreakdown,
    pub classification_refinement_breakdown: ClassificationRefinementBreakdown,
    pub claim_refinement_breakdown: ClaimRefinementBreakdown,
}

/// Per-type counts of identity refinements.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct IdentityRefinementBreakdown {
    pub confirmed: usize,
    pub merged: usize,
    pub split: usize,
    pub reassigned: usize,
    pub dismissed: usize,
    pub total: usize,
}

/// Per-type counts of evidence refinements.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct EvidenceRefinementBreakdown {
    pub confirmed: usize,
    pub upgraded: usize,
    pub downgraded: usize,
    pub dismissed: usize,
    pub discovered: usize,
    pub total: usize,
}

/// Per-type counts of graph refinements.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct GraphRefinementBreakdown {
    pub agent_merged: usize,
    pub agent_split: usize,
    pub agent_reassigned: usize,
    pub edge_upgraded: usize,
    pub edge_downgraded: usize,
    pub edge_added: usize,
    pub edge_removed: usize,
    pub total: usize,
}

/// Per-type counts of classification refinements.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct ClassificationRefinementBreakdown {
    pub confirmed: usize,
    pub changed: usize,
    pub dismissed: usize,
    pub discovered: usize,
    pub total: usize,
}

/// Per-type counts of claim refinements.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct ClaimRefinementBreakdown {
    pub confirmed: usize,
    pub severity_changed: usize,
    pub dismissed: usize,
    pub discovered: usize,
    pub total: usize,
}

impl Default for EvaluationMetrics {
    fn default() -> Self {
        Self {
            phase1_alert_count: 0,
            phase2_alert_count: 0,
            confirmed_count: 0,
            changed_count: 0,
            dismissed_count: 0,
            discovered_count: 0,
            precision: 0.0,
            recall: 0.0,
            f1: 0.0,
            evidence_count: 0,
            non_link_count: 0,
            identity_count: 0,
            node_count: 0,
            edge_count: 0,
            claim_count: 0,
            traceback_count: 0,
            identity_refinement_breakdown: IdentityRefinementBreakdown::default(),
            evidence_refinement_breakdown: EvidenceRefinementBreakdown::default(),
            graph_refinement_breakdown: GraphRefinementBreakdown::default(),
            classification_refinement_breakdown: ClassificationRefinementBreakdown::default(),
            claim_refinement_breakdown: ClaimRefinementBreakdown::default(),
        }
    }
}

// ═══════════════════════════════════════════════════════════════════════════
// EvaluationMetrics computation
// ═══════════════════════════════════════════════════════════════════════════

impl EvaluationMetrics {
    /// Compute full evaluation metrics from refinement data and scene counts.
    pub fn compute(
        phase1_alert_count: usize,
        phase2_alert_count: usize,
        refinements: &RefinementBundle,
        evidence_count: usize,
        non_link_count: usize,
        identity_count: usize,
        node_count: usize,
        edge_count: usize,
        claim_count: usize,
        traceback_count: usize,
    ) -> Self {
        // ── Classification refinement counts ───────────────────────────
        let confirmed_count = refinements
            .classification
            .iter()
            .filter(|r| matches!(r.refinement_type, ClassificationRefinementType::Confirmed))
            .count();
        let changed_count = refinements
            .classification
            .iter()
            .filter(|r| matches!(r.refinement_type, ClassificationRefinementType::Changed))
            .count();
        let dismissed_count = refinements
            .classification
            .iter()
            .filter(|r| matches!(r.refinement_type, ClassificationRefinementType::Dismissed))
            .count();
        let discovered_count = refinements
            .classification
            .iter()
            .filter(|r| matches!(r.refinement_type, ClassificationRefinementType::Discovered))
            .count();

        // ── Per-domain breakdowns ──────────────────────────────────────
        let identity_breakdown = build_identity_breakdown(&refinements.identity);
        let evidence_breakdown = build_evidence_breakdown(&refinements.evidence);
        let graph_breakdown = build_graph_breakdown(&refinements.graph);
        let classification_breakdown =
            ClassificationRefinementBreakdown {
                confirmed: confirmed_count,
                changed: changed_count,
                dismissed: dismissed_count,
                discovered: discovered_count,
                total: refinements.classification.len(),
            };
        let claim_breakdown = build_claim_breakdown(&refinements.claim);

        // ── Precision / Recall / F1 ───────────────────────────────────
        let denom_p = confirmed_count + changed_count + discovered_count;
        let precision = if denom_p > 0 {
            confirmed_count as f32 / denom_p as f32
        } else {
            1.0
        };

        let denom_r = confirmed_count + changed_count + dismissed_count;
        let recall = if denom_r > 0 {
            confirmed_count as f32 / denom_r as f32
        } else {
            1.0
        };

        let f1 = if precision + recall > 0.0 {
            2.0 * precision * recall / (precision + recall)
        } else {
            0.0
        };

        Self {
            phase1_alert_count,
            phase2_alert_count,
            confirmed_count,
            changed_count,
            dismissed_count,
            discovered_count,
            precision,
            recall,
            f1,
            evidence_count,
            non_link_count,
            identity_count,
            node_count,
            edge_count,
            claim_count,
            traceback_count,
            identity_refinement_breakdown: identity_breakdown,
            evidence_refinement_breakdown: evidence_breakdown,
            graph_refinement_breakdown: graph_breakdown,
            classification_refinement_breakdown: classification_breakdown,
            claim_refinement_breakdown: claim_breakdown,
        }
    }
}

// ═══════════════════════════════════════════════════════════════════════════
// Breakdown builders
// ═══════════════════════════════════════════════════════════════════════════

fn build_identity_breakdown(
    refinements: &[crate::responsibility::types::IdentityRefinement],
) -> IdentityRefinementBreakdown {
    let mut b = IdentityRefinementBreakdown::default();
    for r in refinements {
        match r.refinement_type {
            IdentityRefinementType::Confirmed => b.confirmed += 1,
            IdentityRefinementType::Merged => b.merged += 1,
            IdentityRefinementType::Split => b.split += 1,
            IdentityRefinementType::Reassigned => b.reassigned += 1,
            IdentityRefinementType::Dismissed => b.dismissed += 1,
        }
    }
    b.total = refinements.len();
    b
}

fn build_evidence_breakdown(
    refinements: &[crate::responsibility::types::EvidenceRefinement],
) -> EvidenceRefinementBreakdown {
    let mut b = EvidenceRefinementBreakdown::default();
    for r in refinements {
        match r.refinement_type {
            EvidenceRefinementType::Confirmed => b.confirmed += 1,
            EvidenceRefinementType::Upgraded => b.upgraded += 1,
            EvidenceRefinementType::Downgraded => b.downgraded += 1,
            EvidenceRefinementType::Dismissed => b.dismissed += 1,
            EvidenceRefinementType::Discovered => b.discovered += 1,
        }
    }
    b.total = refinements.len();
    b
}

fn build_graph_breakdown(
    refinements: &[crate::responsibility::types::GraphRefinement],
) -> GraphRefinementBreakdown {
    let mut b = GraphRefinementBreakdown::default();
    for r in refinements {
        match r.refinement_type {
            GraphRefinementType::AgentMerged => b.agent_merged += 1,
            GraphRefinementType::AgentSplit => b.agent_split += 1,
            GraphRefinementType::AgentReassigned => b.agent_reassigned += 1,
            GraphRefinementType::EdgeUpgraded => b.edge_upgraded += 1,
            GraphRefinementType::EdgeDowngraded => b.edge_downgraded += 1,
            GraphRefinementType::EdgeAdded => b.edge_added += 1,
            GraphRefinementType::EdgeRemoved => b.edge_removed += 1,
        }
    }
    b.total = refinements.len();
    b
}

fn build_claim_breakdown(
    refinements: &[crate::responsibility::types::ClaimRefinement],
) -> ClaimRefinementBreakdown {
    let mut b = ClaimRefinementBreakdown::default();
    for r in refinements {
        match r.refinement_type {
            ClaimRefinementType::Confirmed => b.confirmed += 1,
            ClaimRefinementType::SeverityChanged => b.severity_changed += 1,
            ClaimRefinementType::Dismissed => b.dismissed += 1,
            ClaimRefinementType::Discovered => b.discovered += 1,
        }
    }
    b.total = refinements.len();
    b
}

// ═══════════════════════════════════════════════════════════════════════════
// Tests
// ═══════════════════════════════════════════════════════════════════════════

#[cfg(test)]
mod tests {
    use super::*;
    use crate::responsibility::types::{
        AlertId, ClaimId, ClaimRefinement, ClaimRefinementType, ClassificationRefinement,
        ClassificationRefinementType, EdgeId, EvidenceId, EvidenceRefinement,
        EvidenceRefinementType, GraphRefinement, GraphRefinementType, IdentityRefinement,
        IdentityRefinementType, NodeId,
    };

    fn make_refinements() -> RefinementBundle {
        RefinementBundle {
            identity: vec![
                IdentityRefinement {
                    phase1_agent_id: crate::responsibility::types::AgentId::from("a1"),
                    phase2_agent_id: crate::responsibility::types::AgentId::from("a1-auth"),
                    refinement_type: IdentityRefinementType::Confirmed,
                    reason: "test".into(),
                },
                IdentityRefinement {
                    phase1_agent_id: crate::responsibility::types::AgentId::from("a2"),
                    phase2_agent_id: crate::responsibility::types::AgentId::from("a2-auth"),
                    refinement_type: IdentityRefinementType::Merged,
                    reason: "test".into(),
                },
                IdentityRefinement {
                    phase1_agent_id: crate::responsibility::types::AgentId::from("a3"),
                    phase2_agent_id: crate::responsibility::types::AgentId::from("a3-auth"),
                    refinement_type: IdentityRefinementType::Dismissed,
                    reason: "test".into(),
                },
            ],
            evidence: vec![
                EvidenceRefinement {
                    phase1_evidence_id: EvidenceId::from("e1"),
                    phase2_evidence_id: Some(EvidenceId::from("e1-auth")),
                    refinement_type: EvidenceRefinementType::Confirmed,
                    reason: "test".into(),
                },
                EvidenceRefinement {
                    phase1_evidence_id: EvidenceId::from("e2"),
                    phase2_evidence_id: Some(EvidenceId::from("e2-auth")),
                    refinement_type: EvidenceRefinementType::Upgraded,
                    reason: "test".into(),
                },
                EvidenceRefinement {
                    phase1_evidence_id: EvidenceId::from("e3"),
                    phase2_evidence_id: None,
                    refinement_type: EvidenceRefinementType::Dismissed,
                    reason: "test".into(),
                },
                EvidenceRefinement {
                    phase1_evidence_id: EvidenceId::from("none"),
                    phase2_evidence_id: Some(EvidenceId::from("e4-auth")),
                    refinement_type: EvidenceRefinementType::Discovered,
                    reason: "test".into(),
                },
            ],
            graph: vec![
                GraphRefinement {
                    refinement_type: GraphRefinementType::AgentReassigned,
                    affected_nodes: vec![NodeId::from("n1")],
                    affected_edges: vec![],
                    description: "test".into(),
                },
                GraphRefinement {
                    refinement_type: GraphRefinementType::EdgeAdded,
                    affected_nodes: vec![],
                    affected_edges: vec![EdgeId::from("edge1")],
                    description: "test".into(),
                },
                GraphRefinement {
                    refinement_type: GraphRefinementType::EdgeRemoved,
                    affected_nodes: vec![],
                    affected_edges: vec![EdgeId::from("edge2")],
                    description: "test".into(),
                },
            ],
            classification: vec![
                ClassificationRefinement {
                    phase1_alert_id: AlertId::from("p1"),
                    phase2_alert_id: AlertId::from("p1-auth"),
                    refinement_type: ClassificationRefinementType::Confirmed,
                    reason: "test".into(),
                    score_delta: 0.0,
                },
                ClassificationRefinement {
                    phase1_alert_id: AlertId::from("p2"),
                    phase2_alert_id: AlertId::from("p2-auth"),
                    refinement_type: ClassificationRefinementType::Changed,
                    reason: "test".into(),
                    score_delta: 0.3,
                },
                ClassificationRefinement {
                    phase1_alert_id: AlertId::from("p3"),
                    phase2_alert_id: AlertId::from("none"),
                    refinement_type: ClassificationRefinementType::Dismissed,
                    reason: "test".into(),
                    score_delta: -0.5,
                },
                ClassificationRefinement {
                    phase1_alert_id: AlertId::from("none"),
                    phase2_alert_id: AlertId::from("p4-new"),
                    refinement_type: ClassificationRefinementType::Discovered,
                    reason: "test".into(),
                    score_delta: 0.8,
                },
            ],
            claim: vec![
                ClaimRefinement {
                    phase1_claim_id: ClaimId::from("pending"),
                    phase2_claim_id: ClaimId::from("c1"),
                    refinement_type: ClaimRefinementType::Discovered,
                    reason: "test".into(),
                },
            ],
        }
    }

    #[test]
    fn test_compute_evaluation_metrics() {
        let refinements = make_refinements();
        let metrics = EvaluationMetrics::compute(4, 4, &refinements, 10, 2, 1, 5, 8, 4, 3);

        assert_eq!(metrics.phase1_alert_count, 4);
        assert_eq!(metrics.phase2_alert_count, 4);
        assert_eq!(metrics.confirmed_count, 1);
        assert_eq!(metrics.changed_count, 1);
        assert_eq!(metrics.dismissed_count, 1);
        assert_eq!(metrics.discovered_count, 1);

        // precision = 1 / (1+1+1) = 1/3
        // recall = 1 / (1+1+1) = 1/3
        // f1 = 2 * (1/3 * 1/3) / (1/3 + 1/3) = 2/9 / 2/3 = 1/3
        assert!((metrics.precision - 1.0 / 3.0).abs() < 0.01);
        assert!((metrics.recall - 1.0 / 3.0).abs() < 0.01);
        assert!((metrics.f1 - 1.0 / 3.0).abs() < 0.01);

        assert_eq!(metrics.evidence_count, 10);
        assert_eq!(metrics.non_link_count, 2);
        assert_eq!(metrics.identity_count, 1);
        assert_eq!(metrics.node_count, 5);
        assert_eq!(metrics.edge_count, 8);
        assert_eq!(metrics.claim_count, 4);
        assert_eq!(metrics.traceback_count, 3);
    }

    #[test]
    fn test_perfect_precision_recall() {
        let refinements = RefinementBundle {
            classification: vec![
                ClassificationRefinement {
                    phase1_alert_id: AlertId::from("p1"),
                    phase2_alert_id: AlertId::from("p1-auth"),
                    refinement_type: ClassificationRefinementType::Confirmed,
                    reason: "test".into(),
                    score_delta: 0.0,
                },
                ClassificationRefinement {
                    phase1_alert_id: AlertId::from("p2"),
                    phase2_alert_id: AlertId::from("p2-auth"),
                    refinement_type: ClassificationRefinementType::Confirmed,
                    reason: "test".into(),
                    score_delta: 0.0,
                },
            ],
            ..Default::default()
        };

        let metrics = EvaluationMetrics::compute(2, 2, &refinements, 0, 0, 0, 0, 0, 0, 0);

        // precision = 2 / (2+0+0) = 1.0
        // recall = 2 / (2+0+0) = 1.0
        // f1 = 1.0
        assert!((metrics.precision - 1.0).abs() < 0.01);
        assert!((metrics.recall - 1.0).abs() < 0.01);
        assert!((metrics.f1 - 1.0).abs() < 0.01);
    }

    #[test]
    fn test_identity_breakdown_counts() {
        let refinements = make_refinements();
        let b = build_identity_breakdown(&refinements.identity);
        assert_eq!(b.confirmed, 1);
        assert_eq!(b.merged, 1);
        assert_eq!(b.dismissed, 1);
        assert_eq!(b.split, 0);
        assert_eq!(b.reassigned, 0);
        assert_eq!(b.total, 3);
    }

    #[test]
    fn test_evidence_breakdown_counts() {
        let refinements = make_refinements();
        let b = build_evidence_breakdown(&refinements.evidence);
        assert_eq!(b.confirmed, 1);
        assert_eq!(b.upgraded, 1);
        assert_eq!(b.dismissed, 1);
        assert_eq!(b.discovered, 1);
        assert_eq!(b.downgraded, 0);
        assert_eq!(b.total, 4);
    }

    #[test]
    fn test_graph_breakdown_counts() {
        let refinements = make_refinements();
        let b = build_graph_breakdown(&refinements.graph);
        assert_eq!(b.agent_reassigned, 1);
        assert_eq!(b.edge_added, 1);
        assert_eq!(b.edge_removed, 1);
        assert_eq!(b.agent_merged, 0);
        assert_eq!(b.total, 3);
    }

    #[test]
    fn test_claim_breakdown_counts() {
        let refinements = make_refinements();
        let b = build_claim_breakdown(&refinements.claim);
        assert_eq!(b.discovered, 1);
        assert_eq!(b.confirmed, 0);
        assert_eq!(b.total, 1);
    }
}
