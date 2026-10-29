// SPDX-License-Identifier: Apache-2.0 OR MIT
//
// executor.rs — Reanalysis of the complete versioned evidence checkpoint.
// Legacy rows remain viewable but cannot manufacture missing analysis inputs.
//
use crate::responsibility::claims::AlertBuilder;
use crate::responsibility::graph::snapshot::FrozenGraph;
#[cfg(test)]
use crate::responsibility::identity::{
    IdentityProvider, union_find::AuthoritativeIdentityProvider,
};
use crate::responsibility::persistence::PersistenceReader;
#[cfg(test)]
use crate::responsibility::types::{AgentId, BehaviorTimeline, ProcessId};
use crate::responsibility::types::{
    AlertId, AuthoritativeIdentity, ClaimId, ClaimRefinement, ClaimRefinementType,
    ClassificationRefinement, ClassificationRefinementType, CorrelationEvidence,
    EvidenceRefinement, GraphRefinement, IdentityRefinement, ProvisionalIdentity,
    ResponsibilityAlert, ResponsibilityClaim, ResponsibilityTraceback,
};
use anyhow::{Context, Result};
use std::collections::HashMap;

// ═══════════════════════════════════════════════════════════════════════════
// ReplayResult
// ═══════════════════════════════════════════════════════════════════════════

/// The result of a full authoritative replay.
#[derive(Debug, Clone)]
pub struct ReplayResult {
    pub run_id: String,
    pub final_verdict: crate::responsibility::classification::final_decision::FinalVerdict,
    pub classification: crate::responsibility::classification::ClassificationResult,
    /// Versioned score/coverage/execution snapshot from the same evaluation
    /// kernel as live finalization.
    pub run_metrics: super::run_metrics::RunMetrics,
    /// Authoritative identities produced by Phase 2 resolution.
    pub identities: Vec<AuthoritativeIdentity>,
    /// Correlation evidence produced by batch correlation.
    pub evidence: Vec<CorrelationEvidence>,
    /// The frozen responsibility graph.
    pub frozen_graph: FrozenGraph,
    /// Alerts generated from the authoritative graph.
    pub alerts: Vec<ResponsibilityAlert>,
    /// Claims generated from PolicyBoundaryCrossing edges.
    pub claims: Vec<ResponsibilityClaim>,
    /// Tracebacks built from claims.
    pub tracebacks: Vec<ResponsibilityTraceback>,
    /// Bounded occurrence attribution produced by the same decision kernel as
    /// live finalization; replay does not rebuild a second set.
    pub attributions: crate::responsibility::claims::attribution::AttributionResults,
    /// All collected refinements (identity, evidence, graph, classification, claim).
    pub refinements: RefinementBundle,
    /// Computed evaluation metrics.
    pub evaluation: EvaluationMetrics,
    /// Taint-gate counters produced while rebuilding claims. They explain a
    /// missing `TaintedContextUse` claim instead of leaving the absence
    /// unexplained.
    pub claim_diagnostics: crate::responsibility::claims::claim_builder::ClaimBuildDiagnostics,
    /// Delegation records read from the persisted table. A smaller-than-live
    /// count means the authorization input was lossy.
    pub delegations_loaded: usize,
    /// Records whose provisional agent IDs were rewritten to the authoritative
    /// IDs the replay graph uses. Records not fully mapped cannot authorize a
    /// share in replay.
    pub delegations_mapped: usize,
}

/// All refinement arrays collected during the replay.
#[derive(Debug, Clone, Default)]
pub struct RefinementBundle {
    pub identity: Vec<IdentityRefinement>,
    pub evidence: Vec<EvidenceRefinement>,
    pub graph: Vec<GraphRefinement>,
    pub classification: Vec<ClassificationRefinement>,
    pub claim: Vec<ClaimRefinement>,
}

/// Evaluation metrics computed from the replay.
#[derive(Debug, Clone, Default)]
pub struct EvaluationMetrics {
    pub phase1_alert_count: usize,
    pub phase2_alert_count: usize,
    pub confirmed_count: usize,
    pub changed_count: usize,
    pub dismissed_count: usize,
    pub discovered_count: usize,
    /// Ground-truth detection metrics are unavailable without independent labels.
    pub precision: Option<f32>,
    pub recall: Option<f32>,
    pub f1: Option<f32>,
    pub evidence_count: usize,
    pub non_link_count: usize,
    pub identity_count: usize,
    pub node_count: usize,
    pub edge_count: usize,
    pub claim_count: usize,
    pub traceback_count: usize,
}

// ═══════════════════════════════════════════════════════════════════════════
// ReplayExecutor
// ═══════════════════════════════════════════════════════════════════════════

/// Drives the full Phase 2 authoritative replay pipeline.
pub struct ReplayExecutor;

impl ReplayExecutor {
    /// Run the full authoritative replay for a given run.
    ///
    /// Uses the same frozen decision kernel as live finalization. Identity,
    /// correlation, provenance and model outputs are retained observations,
    /// not reconstructed with defaults from lossy legacy tables.
    pub fn reauthoritative(reader: &PersistenceReader, run_id: &str) -> Result<ReplayResult> {
        reader
            .get_run(run_id)
            .context("query run")?
            .ok_or_else(|| anyhow::anyhow!("run not found: {run_id}"))?;
        let (version, payload) = reader.get_analysis_checkpoint(run_id, super::checkpoint::CHECKPOINT_KIND)?
            .ok_or_else(|| anyhow::anyhow!("run {run_id} lacks a complete versioned analysis checkpoint; only recorded-result replay is available"))?;
        anyhow::ensure!(
            version == super::checkpoint::CHECKPOINT_VERSION,
            "unsupported analysis checkpoint version: {version}"
        );
        let checkpoint: super::checkpoint::AnalysisCheckpoint =
            serde_json::from_value(payload).context("decode complete analysis checkpoint")?;
        checkpoint.validate(run_id).map_err(anyhow::Error::msg)?;
        let analysis = checkpoint.evaluate();
        let final_verdict = analysis.verdict;
        let classification_result = analysis.classification;
        let run_metrics = analysis.run_metrics;
        let claims = analysis.claims;
        let tracebacks = analysis.tracebacks;
        let claim_diagnostics = analysis.diagnostics;
        let attributions = analysis.attributions;
        let graph = analysis.graph;
        let frozen_graph = FrozenGraph::new(
            checkpoint.graph.clone(),
            checkpoint.graph_refinements.clone(),
            Some(run_id.into()),
        );
        let phase1_alerts = reader
            .get_provisional_alerts(run_id)
            .context("load phase-1 alerts")?;
        let (provisional_identities, _) =
            reader.get_identities(run_id).context("load identities")?;
        let fallback = checkpoint.fallback_agent_id.as_ref();
        let has_graph_agent = graph.all_nodes().any(|node| {
            node.agent_id.as_ref().is_some_and(|agent| {
                agent.as_str() != crate::responsibility::graph::UNATTRIBUTED_AGENT_ID
            })
        });
        let alerts = if fallback.is_some() || has_graph_agent || !claims.is_empty() {
            let timestamp_ns = checkpoint.graph.timestamp_ns;
            let mut alert = AlertBuilder::compose_with_context(
                &claims,
                &classification_result,
                None,
                &graph,
                &checkpoint.timeline,
                fallback,
                crate::responsibility::claims::alert_builder::ComposeContext {
                    timestamp_ns,
                    phase: crate::responsibility::types::PhaseIndicator::Authoritative {
                        timestamp_ns,
                        overrides: None,
                    },
                    final_verdict: Some(&final_verdict),
                },
            );
            alert.provisional_ref = phase1_alerts.last().map(|alert| alert.alert_id.clone());
            vec![alert]
        } else {
            Vec::new()
        };
        let delegations_loaded = checkpoint.delegations.len();
        let delegations_mapped = checkpoint
            .delegations
            .iter()
            .filter(|delegation| {
                checkpoint
                    .identities
                    .iter()
                    .any(|identity| identity.agent_id.as_str() == delegation.from_agent_id)
                    && checkpoint
                        .identities
                        .iter()
                        .any(|identity| identity.agent_id.as_str() == delegation.to_agent_id)
            })
            .count();

        // ── Step 7: Collect refinements ───────────────────────────────
        let mut refinements = RefinementBundle::default();

        // Identity refinements
        refinements.identity = checkpoint.identity_refinements.clone();

        // Evidence refinements
        refinements.evidence = checkpoint.evidence_refinements.clone();

        // Graph refinements
        refinements.graph = frozen_graph.refinements.clone();

        // Classification refinements — compare phase1 vs phase2 alerts
        refinements.classification =
            Self::build_classification_refinements(&phase1_alerts, &alerts);

        // Claim refinements — match claims by subject agent
        refinements.claim = Self::build_claim_refinements(&provisional_identities, &claims);

        // ── Step 8: Compute evaluation metrics ────────────────────────
        let evaluation = EvaluationMetrics {
            phase1_alert_count: phase1_alerts.len(),
            phase2_alert_count: alerts.len(),
            confirmed_count: refinements
                .classification
                .iter()
                .filter(|r| matches!(r.refinement_type, ClassificationRefinementType::Confirmed))
                .count(),
            changed_count: refinements
                .classification
                .iter()
                .filter(|r| matches!(r.refinement_type, ClassificationRefinementType::Changed))
                .count(),
            dismissed_count: refinements
                .classification
                .iter()
                .filter(|r| matches!(r.refinement_type, ClassificationRefinementType::Dismissed))
                .count(),
            discovered_count: refinements
                .classification
                .iter()
                .filter(|r| matches!(r.refinement_type, ClassificationRefinementType::Discovered))
                .count(),
            evidence_count: checkpoint.evidence.len(),
            non_link_count: checkpoint.non_link_count,
            identity_count: checkpoint.identities.len(),
            node_count: frozen_graph.node_count(),
            edge_count: frozen_graph.edge_count(),
            claim_count: claims.len(),
            traceback_count: tracebacks.len(),
            precision: None,
            recall: None,
            f1: None,
        };

        Ok(ReplayResult {
            run_id: run_id.to_string(),
            final_verdict,
            classification: classification_result,
            run_metrics,
            identities: checkpoint.identities,
            evidence: checkpoint.evidence,
            frozen_graph,
            alerts,
            claims,
            tracebacks,
            attributions,
            refinements,
            evaluation,
            claim_diagnostics,
            delegations_loaded,
            delegations_mapped,
        })
    }

    // ── Internal helpers ────────────────────────────────────────────────

    /// Build classification refinements by comparing Phase 1 and Phase 2 alerts.
    fn build_classification_refinements(
        phase1: &[ResponsibilityAlert],
        phase2: &[ResponsibilityAlert],
    ) -> Vec<ClassificationRefinement> {
        let mut refinements = Vec::new();

        // Phase 1 → Phase 2 mapping by provisional_ref
        let p2_by_p1: HashMap<AlertId, &ResponsibilityAlert> = phase2
            .iter()
            .filter_map(|a| a.provisional_ref.as_ref().map(|r| (r.clone(), a)))
            .collect();

        for p1 in phase1 {
            if let Some(p2) = p2_by_p1.get(&p1.alert_id) {
                // P1 alert has a matching P2 alert
                let same_severity = matches!(
                    (&p1.severity, &p2.severity),
                    (
                        crate::responsibility::types::AlertSeverity::Info,
                        crate::responsibility::types::AlertSeverity::Info
                    ) | (
                        crate::responsibility::types::AlertSeverity::Warning,
                        crate::responsibility::types::AlertSeverity::Warning
                    ) | (
                        crate::responsibility::types::AlertSeverity::Critical,
                        crate::responsibility::types::AlertSeverity::Critical
                    )
                );
                let same_decision = matches!(
                    (&p1.decision, &p2.decision),
                    (
                        crate::responsibility::types::AlertDecision::Safe,
                        crate::responsibility::types::AlertDecision::Safe
                    ) | (
                        crate::responsibility::types::AlertDecision::Unsafe,
                        crate::responsibility::types::AlertDecision::Unsafe
                    ) | (
                        crate::responsibility::types::AlertDecision::Uncertain,
                        crate::responsibility::types::AlertDecision::Uncertain
                    )
                );
                let rtype = if same_severity && same_decision {
                    ClassificationRefinementType::Confirmed
                } else {
                    ClassificationRefinementType::Changed
                };
                refinements.push(ClassificationRefinement {
                    phase1_alert_id: p1.alert_id.clone(),
                    phase2_alert_id: p2.alert_id.clone(),
                    refinement_type: rtype,
                    reason: format!(
                        "severity={:?}→{:?}, decision={:?}→{:?}",
                        p1.severity, p2.severity, p1.decision, p2.decision
                    ),
                    score_delta: p2.confidence - p1.confidence,
                });
            } else {
                // P1 alert has no matching P2 alert → dismissed
                refinements.push(ClassificationRefinement {
                    phase1_alert_id: p1.alert_id.clone(),
                    phase2_alert_id: AlertId::from("none"),
                    refinement_type: ClassificationRefinementType::Dismissed,
                    reason: "Phase 1 alert not confirmed in Phase 2".to_string(),
                    score_delta: -p1.confidence,
                });
            }
        }

        // Phase 2 alerts with no Phase 1 match → discovered
        let p1_ids: std::collections::HashSet<_> =
            p2_by_p1.keys().map(|a| a.as_str().to_string()).collect();
        for p2 in phase2 {
            let has_p1 = p2
                .provisional_ref
                .as_ref()
                .is_some_and(|r| p1_ids.contains(r.as_str()));
            if !has_p1 {
                refinements.push(ClassificationRefinement {
                    phase1_alert_id: AlertId::from("none"),
                    phase2_alert_id: p2.alert_id.clone(),
                    refinement_type: ClassificationRefinementType::Discovered,
                    reason: "New alert discovered in Phase 2".to_string(),
                    score_delta: p2.confidence,
                });
            }
        }

        refinements
    }

    /// Build claim refinements from provisional identities and authoritative claims.
    fn build_claim_refinements(
        _provisional_identities: &[ProvisionalIdentity],
        claims: &[ResponsibilityClaim],
    ) -> Vec<ClaimRefinement> {
        // For now, mark all claims as Discovered since we are replaying
        // the authoritative path. In the full implementation, this would
        // compare against persisted Phase 1 claims.
        claims
            .iter()
            .map(|c| ClaimRefinement {
                phase1_claim_id: ClaimId::from("pending"),
                phase2_claim_id: c.claim_id.clone(),
                refinement_type: ClaimRefinementType::Discovered,
                reason: "Authoritative claim generated during replay".to_string(),
            })
            .collect()
    }
}

// ═══════════════════════════════════════════════════════════════════════════
// Tests
// ═══════════════════════════════════════════════════════════════════════════

#[cfg(test)]
mod tests {
    use super::*;
    use crate::responsibility::types::{AlertSeverity, PhaseIndicator};

    #[test]
    fn test_authoritative_identity_provider_lookup() {
        let pid1 = ProcessId::new(100, 1);
        let pid2 = ProcessId::new(200, 2);
        let agent_id = AgentId::from("agent-test");

        let resolution = crate::responsibility::identity::union_find::AuthoritativeResolution {
            agents: vec![AuthoritativeIdentity {
                agent_id: agent_id.clone(),
                process_ids: vec![pid1, pid2],
                message_ids: Vec::new(),
                tls_sessions: Vec::new(),
                confidence: crate::responsibility::types::AgentConfidence::Authoritative,
                provisional_refs: Vec::new(),
            }],
            refinements: Vec::new(),
            subagent_boundaries: Vec::new(),
            unresolved: Vec::new(),
            conversation_agents: Vec::new(),
            ambiguous_messages: Vec::new(),
        };

        let provider = AuthoritativeIdentityProvider::new(&resolution);
        assert_eq!(provider.agent_for_process(pid1), Some(agent_id.clone()));
        assert_eq!(provider.agent_for_process(pid2), Some(agent_id));
        assert_eq!(provider.agent_for_process(ProcessId::new(999, 1)), None);

        let all = provider.all_assignments();
        assert_eq!(all.len(), 2);
    }

    #[test]
    fn test_build_classification_refinements_confirmed() {
        let p1 = make_alert("p1", AlertSeverity::Warning, 0.7, None);
        let p2 = make_alert("p2", AlertSeverity::Warning, 0.7, Some(AlertId::from("p1")));

        let refs = ReplayExecutor::build_classification_refinements(&[p1], &[p2]);
        assert_eq!(refs.len(), 1);
        assert_eq!(
            refs[0].refinement_type,
            ClassificationRefinementType::Confirmed
        );
    }

    #[test]
    fn test_build_classification_refinements_changed() {
        let p1 = make_alert("p1", AlertSeverity::Info, 0.3, None);
        let p2 = make_alert(
            "p2",
            AlertSeverity::Critical,
            0.9,
            Some(AlertId::from("p1")),
        );

        let refs = ReplayExecutor::build_classification_refinements(&[p1], &[p2]);
        assert_eq!(refs.len(), 1);
        assert_eq!(
            refs[0].refinement_type,
            ClassificationRefinementType::Changed
        );
    }

    #[test]
    fn test_build_classification_refinements_dismissed() {
        let p1 = make_alert("p1", AlertSeverity::Warning, 0.6, None);

        let refs = ReplayExecutor::build_classification_refinements(&[p1], &[]);
        assert_eq!(refs.len(), 1);
        assert_eq!(
            refs[0].refinement_type,
            ClassificationRefinementType::Dismissed
        );
    }

    #[test]
    fn test_build_classification_refinements_discovered() {
        let p2 = make_alert("p2", AlertSeverity::Warning, 0.7, None);

        let refs = ReplayExecutor::build_classification_refinements(&[], &[p2]);
        assert_eq!(refs.len(), 1);
        assert_eq!(
            refs[0].refinement_type,
            ClassificationRefinementType::Discovered
        );
    }

    // ── test helpers ────────────────────────────────────────────────────

    fn make_alert(
        id: &str,
        severity: AlertSeverity,
        confidence: f32,
        provisional_ref: Option<AlertId>,
    ) -> ResponsibilityAlert {
        ResponsibilityAlert {
            alert_id: AlertId::from(id),
            timestamp_ns: 1000,
            display_profile: crate::responsibility::types::DisplayProfile::SafeBaseline,
            decision: crate::responsibility::types::AlertDecision::Safe,
            actionable: false,
            severity,
            confidence,
            authority: crate::responsibility::types::AlertAuthority::Supporting,
            reasoning: String::new(),
            title: String::new(),
            summary: String::new(),
            action_hint: None,
            primary_agent: crate::responsibility::types::AgentRef {
                agent_id: AgentId::from("agent-1"),
                claimed_role: None,
            },
            involved_agents: Vec::new(),
            engine: crate::responsibility::types::EngineSection {
                claim_ids: Vec::new(),
                rule_verdicts: Vec::new(),
                pattern_verdicts: Vec::new(),
                classification_score: 0.0,
                classification_route: String::new(),
                score_breakdown: crate::responsibility::types::ScoreBreakdown {
                    rule_score: 0.0,
                    pattern_score: 0.0,
                    boundary_score: 0.0,
                    evidence_score: 0.0,
                    role_score: 0.0,
                    repetition_score: 0.0,
                    behavioral_score: 0.0,
                    total: 0.0,
                    raw_score: 0.0,
                    calibrated_score: 0.0,
                    gate_factor: 1.0,
                    gate_version: "test".into(),
                    platt_a: 0.0,
                    platt_b: 0.0,
                    observer_adjusted: false,
                },
                cusum_triggered: false,
                cusum_dimensions: vec![],
                progressive_fused: 0.0,
                observer_trigger_reason: String::new(),
                score_model_version: "test".into(),
            },
            timeline: BehaviorTimeline {
                steps: Vec::new(),
                total_duration_ms: 0,
            },
            graph_snapshot: crate::responsibility::types::GraphSnapshot::default(),
            phase: PhaseIndicator::Provisional { timestamp_ns: 1000 },
            provisional_ref,
        }
    }
}

#[cfg(test)]
mod delegation_mapping_tests {
    use super::*;
    use crate::responsibility::judgment::delegation::DelegationObservation;
    use crate::responsibility::types::identity_types::AgentConfidence;

    fn identity(auth: &str, provisional: &[&str]) -> AuthoritativeIdentity {
        AuthoritativeIdentity {
            agent_id: AgentId::from(auth),
            process_ids: vec![],
            message_ids: vec![],
            tls_sessions: vec![],
            confidence: AgentConfidence::Authoritative,
            provisional_refs: provisional.iter().map(|p| AgentId::from(*p)).collect(),
        }
    }

    fn delegation(from: &str, to: &str) -> DelegationObservation {
        DelegationObservation {
            delegation_id: format!("d:{from}->{to}"),
            from_agent_id: from.to_string(),
            to_agent_id: to.to_string(),
            parent_pid: 1,
            parent_start_time_ns: 1,
            child_pid: 2,
            child_start_time_ns: 2,
            timestamp_ns: 3,
            child_comm: String::new(),
            confidence: 0.85,
        }
    }

    #[test]
    fn maps_provisional_delegation_ids_to_authoritative_ids() {
        let identities = vec![
            identity("agent-a", &["prov-a"]),
            identity("agent-b", &["prov-b"]),
        ];
        let mut delegations = vec![delegation("prov-a", "prov-b")];
        assert_eq!(
            map_delegations_to_authoritative(&mut delegations, &identities),
            1
        );
        assert_eq!(delegations[0].from_agent_id, "agent-a");
        assert_eq!(delegations[0].to_agent_id, "agent-b");
    }

    #[test]
    fn unmapped_delegations_are_left_unchanged_and_not_counted() {
        let identities = vec![identity("agent-a", &["prov-a"])];
        let mut delegations = vec![delegation("prov-a", "prov-unknown")];
        assert_eq!(
            map_delegations_to_authoritative(&mut delegations, &identities),
            0
        );
        assert_eq!(delegations[0].from_agent_id, "agent-a");
        assert_eq!(delegations[0].to_agent_id, "prov-unknown");
    }
}

/// Rewrite persisted (Phase-1 provisional) delegation agent IDs to the
/// authoritative IDs the replay graph uses.
///
/// The live run records delegations with provisional UUIDs; replay rebuilds
/// the graph from authoritative `agent-<sha>` identities. Comparing the two
/// ID spaces directly would authorize nothing and mint false
/// `TaintedContextUse` claims. Returns the number of records whose *both*
/// endpoints were mapped.
#[cfg(test)]
fn map_delegations_to_authoritative(
    delegations: &mut [crate::responsibility::judgment::delegation::DelegationObservation],
    authoritative: &[AuthoritativeIdentity],
) -> usize {
    use std::collections::HashMap;

    let mut mapping: HashMap<&str, &str> = HashMap::new();
    for identity in authoritative {
        for provisional in &identity.provisional_refs {
            mapping.insert(provisional.as_str(), identity.agent_id.as_str());
        }
    }

    let mut fully_mapped = 0;
    for delegation in delegations.iter_mut() {
        let from = mapping
            .get(delegation.from_agent_id.as_str())
            .map(|id| (*id).to_string());
        let to = mapping
            .get(delegation.to_agent_id.as_str())
            .map(|id| (*id).to_string());
        let both_mapped = from.is_some() && to.is_some();
        if let Some(id) = from {
            delegation.from_agent_id = id;
        }
        if let Some(id) = to {
            delegation.to_agent_id = id;
        }
        if both_mapped {
            fully_mapped += 1;
        }
    }
    fully_mapped
}
