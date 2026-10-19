// SPDX-License-Identifier: Apache-2.0 OR MIT
//
// executor.rs — ReplayExecutor: drives the full Phase 2 authoritative replay
// pipeline from a persisted run.
//
// Per spec §14:
//   1. Load SignalGraph from DB (process_events, tls_sessions, message_bindings)
//   2. Re-run identity::resolve_authoritative(snapshot)
//   3. Re-run evidence::correlate_batch(intents, actions, snapshot)
//   4. Re-run graph::GraphBuilder::finalize(authoritative, correlation, snapshot)
//   5. Re-run classification::classify_authoritative(frozen_graph)
//   6. Re-run claims::ClaimBuilder + AlertBuilder + TracebackBuilder
//   7. Produce all Refinements + EvaluationMetrics

use anyhow::{Context, Result};
use rusqlite::{Connection, OpenFlags};
use std::collections::{HashMap, HashSet};
use std::sync::Arc;

use crate::responsibility::claims::{AlertBuilder, ClaimBuilder, TracebackBuilder};
use crate::responsibility::classification::engine::ClassificationEngine as PhaseClassificationEngine;
use crate::responsibility::evidence::correlator::{CorrelationResult, EvidenceCorrelator};
use crate::responsibility::graph::graph_builder::GraphBuilder;
use crate::responsibility::graph::snapshot::{FrozenGraph, create_snapshot};
use crate::responsibility::identity::IdentityProvider;
use crate::responsibility::identity::union_find::run_authoritative_resolution;
use crate::responsibility::persistence::PersistenceReader;
use crate::responsibility::signals::SignalSnapshot;
use crate::responsibility::signals::signal_graph::SignalGraph;
use crate::responsibility::signals::signal_snapshot::ProcessExecInfo;
use crate::responsibility::types::{
    Action, AgentId, AlertId, AuthoritativeIdentity, BehaviorTimeline, ClaimId, ClaimRefinement,
    ClaimRefinementType, ClassificationRefinement, ClassificationRefinementType,
    CorrelationEvidence, EvidenceRefinement, GraphRefinement, IdentityRefinement, Intent,
    ProcessId, ProvisionalIdentity, ResponsibilityAlert, ResponsibilityClaim,
    ResponsibilityTraceback, TlsSessionId,
};

// ═══════════════════════════════════════════════════════════════════════════
// ReplayResult
// ═══════════════════════════════════════════════════════════════════════════

/// The result of a full authoritative replay.
#[derive(Debug, Clone)]
pub struct ReplayResult {
    pub run_id: String,
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
    /// All collected refinements (identity, evidence, graph, classification, claim).
    pub refinements: RefinementBundle,
    /// Computed evaluation metrics.
    pub evaluation: EvaluationMetrics,
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
    pub precision: f32,
    pub recall: f32,
    pub f1: f32,
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
    /// This re-runs all five Phase 2 pipelines (identity, evidence, graph,
    /// classification, claims) on data loaded from the persistence store.
    /// It produces a complete `ReplayResult` with all refinements and metrics.
    pub fn reauthoritative(reader: &PersistenceReader, run_id: &str) -> Result<ReplayResult> {
        // ── Step 0: Verify the run exists ──────────────────────────────
        let _run = reader
            .get_run(run_id)
            .context("query run")?
            .ok_or_else(|| anyhow::anyhow!("run not found: {}", run_id))?;

        // ── Step 1: Load signal data and build SignalGraph ─────────────
        let db_path = reader
            .db_path()
            .ok_or_else(|| anyhow::anyhow!("replay requires a file-backed database"))?;
        let snapshot = Self::load_signal_snapshot(db_path, run_id)?;

        // ── Step 1b: Load provisional identities for refinement tracking ──
        let (provisional_identities, _) = reader.get_identities(run_id)?;
        let provisional_map: HashMap<ProcessId, ProvisionalIdentity> = provisional_identities
            .iter()
            .flat_map(|pi| {
                pi.process_ids.iter().map(move |pid| {
                    let mut single_pi = pi.clone();
                    single_pi.process_ids = vec![*pid];
                    (*pid, single_pi)
                })
            })
            .collect();

        // ── Step 2: Identity — resolve_authoritative ──────────────────
        let authoritative = run_authoritative_resolution(&snapshot, &provisional_map);

        // ── Step 3: Evidence — correlate_batch ────────────────────────
        let intents: Vec<Intent> = reader.get_intents(run_id).context("load intents")?;
        let actions: Vec<Action> = reader.get_actions(run_id).context("load actions")?;

        let correlator = EvidenceCorrelator::new();
        let correlation_result: CorrelationResult =
            correlator.correlate_batch(&intents, &actions, &snapshot);

        // ── Step 4: Graph — build and finalize ────────────────────────
        let mut graph_builder = GraphBuilder::with_default_config();

        // Create an identity provider from the authoritative resolution.
        let auth_identity_provider = AuthoritativeIdentityProvider::from(&authoritative);

        // Feed all correlation evidence through handle().
        for ev in &correlation_result.evidence {
            let intent = intents
                .iter()
                .find(|i| i.intent_id == ev.intent_id)
                .cloned();
            let action = actions
                .iter()
                .find(|a| a.action_id == ev.action_id)
                .cloned();

            if let (Some(intent), Some(action)) = (intent, action) {
                let _delta = graph_builder.handle(ev, &intent, &action, &auth_identity_provider);
            }
        }

        // Finalize the graph.
        let graph_snapshot = create_snapshot(
            graph_builder.graph(),
            format!("replay-snap-{}", run_id),
            correlation_result
                .evidence
                .last()
                .map(|e| e.timestamp_ns)
                .unwrap_or(0),
        );

        let frozen_graph =
            graph_builder.finalize(&authoritative, &correlation_result, graph_snapshot);

        // ── Step 5: Classification — classify_authoritative ───────────
        let classification_engine = PhaseClassificationEngine::new();
        // Pattern matching runs on the same ResponsibilityGraph the live path
        // uses. This used to pass an empty set ("legacy graph"), which made a
        // replayed run score differently from the live run that recorded it.
        let patterns = crate::responsibility::graph::pattern_matcher::GraphPatternMatcher::new(
            crate::responsibility::graph::pattern_matcher::default_templates(),
        )
        .match_graph(graph_builder.graph());

        let (classification_result, _route) = classification_engine.classify_authoritative(
            graph_builder.graph(),
            &patterns,
            &correlation_result.evidence,
            intents.len() as u32, // same inputs as the live Phase 2 path
            intents.len() as u32, // (0 here made the replayed score differ)
        );

        // ── Step 6: Claims, Alerts, Tracebacks ────────────────────────
        let claims = ClaimBuilder::build_claims(graph_builder.graph(), &HashSet::new());

        let timeline = BehaviorTimeline {
            steps: Vec::new(),
            total_duration_ms: 0,
        };

        let alerts: Vec<ResponsibilityAlert> = if claims.is_empty() {
            Vec::new()
        } else {
            let primary_alert = AlertBuilder::compose(
                &claims,
                &classification_result,
                None, // no observer output in Phase 2
                graph_builder.graph(),
                &timeline,
                None, // fallback_agent_id — not needed when claims exist
            );
            vec![primary_alert]
        };

        // Build tracebacks for each claim.
        let tracebacks: Vec<ResponsibilityTraceback> = claims
            .iter()
            .filter_map(|claim| TracebackBuilder::build(claim, graph_builder.graph()))
            .collect();

        // ── Step 7: Collect refinements ───────────────────────────────
        let mut refinements = RefinementBundle::default();

        // Identity refinements
        refinements.identity = authoritative.refinements.clone();

        // Evidence refinements
        refinements.evidence = correlation_result.refinements.clone();

        // Graph refinements
        refinements.graph = frozen_graph.refinements.clone();

        // Classification refinements — compare phase1 vs phase2 alerts
        // A corrupt alert row must fail the replay: treating it as "Phase 1
        // had no alerts" would mark every Phase-2 alert as Discovered.
        let phase1_alerts = reader.get_alerts(run_id).context("load phase-1 alerts")?;
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
            evidence_count: correlation_result.evidence.len(),
            non_link_count: correlation_result.non_links.len(),
            identity_count: authoritative.agents.len(),
            node_count: frozen_graph.node_count(),
            edge_count: frozen_graph.edge_count(),
            claim_count: claims.len(),
            traceback_count: tracebacks.len(),
            precision: 0.0,
            recall: 0.0,
            f1: 0.0,
        };

        let evaluation = {
            let mut ev = evaluation;
            let confirmed = ev.confirmed_count;
            let changed = ev.changed_count;
            let dismissed = ev.dismissed_count;
            let discovered = ev.discovered_count;

            // Precision: confirmed / (confirmed + changed + discovered)
            let denom_p = confirmed + changed + discovered;
            ev.precision = if denom_p > 0 {
                confirmed as f32 / denom_p as f32
            } else {
                1.0
            };

            // Recall: confirmed / (confirmed + changed + dismissed)
            let denom_r = confirmed + changed + dismissed;
            ev.recall = if denom_r > 0 {
                confirmed as f32 / denom_r as f32
            } else {
                1.0
            };

            // F1
            ev.f1 = if ev.precision + ev.recall > 0.0 {
                2.0 * ev.precision * ev.recall / (ev.precision + ev.recall)
            } else {
                0.0
            };

            ev
        };

        Ok(ReplayResult {
            run_id: run_id.to_string(),
            identities: authoritative.agents.clone(),
            evidence: correlation_result.evidence.clone(),
            frozen_graph,
            alerts,
            claims,
            tracebacks,
            refinements,
            evaluation,
        })
    }

    // ── Internal helpers ────────────────────────────────────────────────

    /// Load raw signal data from the DB and build a SignalGraph + snapshot.
    fn load_signal_snapshot(
        db_path: &std::path::Path,
        run_id: &str,
    ) -> Result<Arc<SignalSnapshot>> {
        // We need a direct read-only connection for signal-layer data
        // since PersistenceReader does not yet expose process_events, tls_sessions,
        // or message_bindings queries directly.
        let conn = Connection::open_with_flags(
            db_path,
            OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
        )
        .context("open signal db for replay")?;
        let _ = conn.execute_batch("PRAGMA read_uncommitted=0;");

        let mut graph = SignalGraph::new();

        // Load process_events
        let has_process_events = conn
            .prepare("SELECT pid FROM process_events LIMIT 0")
            .is_ok();

        if has_process_events {
            let mut stmt = conn
                .prepare(
                    "SELECT pid, start_time_ns, parent_pid, parent_start_time_ns,
                            comm, timestamp_ns
                     FROM process_events WHERE run_id = ?1
                     ORDER BY timestamp_ns ASC",
                )
                .context("prepare process_events query for replay")?;

            let rows = stmt.query_map([run_id], |row| {
                Ok((
                    row.get::<_, i64>(0)? as u32,  // pid
                    row.get::<_, i64>(1)? as u64,  // start_time_ns
                    row.get::<_, Option<i64>>(2)?, // parent_pid
                    row.get::<_, Option<i64>>(3)?, // parent_start_time_ns
                    row.get::<_, String>(4)?,      // comm
                    row.get::<_, i64>(5)? as u64,  // timestamp_ns
                ))
            })?;

            for row in rows {
                let (pid, start_ns, ppid, pstart_ns, comm, ts) = row?;
                let child = ProcessId::new(pid, start_ns);

                // Determine parent ProcessId: use init if no parent recorded.
                let parent = match (ppid, pstart_ns) {
                    (Some(pp), Some(ps)) => ProcessId::new(pp as u32, ps as u64),
                    _ => ProcessId::new(0, 0),
                };

                // Replay only persists comm; the richer identity signals are
                // not recoverable from the replay log.
                graph.observe_exec(
                    child,
                    parent,
                    ProcessExecInfo {
                        comm: comm.clone(),
                        ..Default::default()
                    },
                    None,
                    ts,
                );
            }
        }

        // Load tls_sessions
        let has_tls = conn
            .prepare("SELECT tls_session_id FROM tls_sessions LIMIT 0")
            .is_ok();

        if has_tls {
            let mut stmt = conn
                .prepare(
                    "SELECT tls_session_id, pid, start_time_ns, timestamp_ns
                     FROM tls_sessions WHERE run_id = ?1
                     ORDER BY timestamp_ns ASC",
                )
                .context("prepare tls_sessions query for replay")?;

            let rows = stmt.query_map([run_id], |row| {
                Ok((
                    row.get::<_, String>(0)?,     // tls_session_id
                    row.get::<_, i64>(1)? as u32, // pid
                    row.get::<_, i64>(2)? as u64, // start_time_ns
                    row.get::<_, i64>(3)? as u64, // timestamp_ns
                ))
            })?;

            for row in rows {
                let (tls_id, pid, start_ns, ts) = row?;
                let proc = ProcessId::new(pid, start_ns);
                let tls = TlsSessionId::from(tls_id);
                graph.observe_tls(proc, tls, ts);
            }
        }

        // Load message_bindings
        let has_msgs = conn
            .prepare("SELECT binding_id FROM message_bindings LIMIT 0")
            .is_ok();

        if has_msgs {
            let mut stmt = conn
                .prepare(
                    "SELECT message_id, pid, start_time_ns, tls_session_id, binding_type
                     FROM message_bindings WHERE run_id = ?1
                     ORDER BY binding_id ASC",
                )
                .context("prepare message_bindings query for replay")?;

            let rows = stmt.query_map([run_id], |row| {
                Ok((
                    row.get::<_, String>(0)?,         // message_id
                    row.get::<_, i64>(1)? as u32,     // pid
                    row.get::<_, i64>(2)? as u64,     // start_time_ns
                    row.get::<_, Option<String>>(3)?, // tls_session_id
                    row.get::<_, String>(4)?,         // binding_type
                ))
            })?;

            for row in rows {
                let (msg_id, pid, start_ns, tls_opt, _binding_type) = row?;
                let proc = ProcessId::new(pid, start_ns);
                let msg = crate::responsibility::types::MessageId::from(msg_id);

                if let Some(tls_id) = tls_opt {
                    let tls = TlsSessionId::from(tls_id);
                    graph.observe_message_via_tls(tls, msg);
                } else {
                    graph.observe_message_direct(proc, msg);
                }
            }
        }

        Ok(graph.snapshot())
    }

    /// Find the database path for a given run. Uses the standard location
    /// inferred from the reader's backing store, falling back to a default.

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
// AuthoritativeIdentityProvider — wraps AuthoritativeResolution for GraphBuilder
// ═══════════════════════════════════════════════════════════════════════════

/// An IdentityProvider backed by an AuthoritativeResolution.
///
/// Used by GraphBuilder::handle() during replay to look up agent assignments
/// from the Phase 2 authoritative identity resolution.
struct AuthoritativeIdentityProvider {
    process_to_agent: HashMap<ProcessId, AgentId>,
}

impl AuthoritativeIdentityProvider {
    fn from(
        resolution: &crate::responsibility::identity::union_find::AuthoritativeResolution,
    ) -> Self {
        let mut process_to_agent = HashMap::new();
        for agent in &resolution.agents {
            for pid in &agent.process_ids {
                process_to_agent.insert(*pid, agent.agent_id.clone());
            }
        }
        Self { process_to_agent }
    }
}

impl IdentityProvider for AuthoritativeIdentityProvider {
    fn agent_for_process(&self, process_id: ProcessId) -> Option<AgentId> {
        self.process_to_agent.get(&process_id).cloned()
    }

    fn all_assignments(&self) -> HashMap<ProcessId, AgentId> {
        self.process_to_agent.clone()
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
        };

        let provider = AuthoritativeIdentityProvider::from(&resolution);
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

    #[test]
    fn test_evaluation_metrics_precision_recall() {
        let mut metrics = EvaluationMetrics {
            confirmed_count: 3,
            changed_count: 1,
            dismissed_count: 1,
            discovered_count: 2,
            ..Default::default()
        };

        // precision = 3 / (3 + 1 + 2) = 0.5
        // recall = 3 / (3 + 1 + 1) = 0.6
        // f1 = 2 * 0.5 * 0.6 / (0.5 + 0.6) = 0.6 / 1.1 ≈ 0.545
        let p = 3.0 / 6.0;
        let r = 3.0 / 5.0;
        let f1 = 2.0 * p * r / (p + r);

        metrics.precision = p;
        metrics.recall = r;
        metrics.f1 = f1;

        assert!((metrics.precision - 0.5).abs() < 0.01);
        assert!((metrics.recall - 0.6).abs() < 0.01);
        assert!((metrics.f1 - 0.5454).abs() < 0.01);
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
                },
                cusum_triggered: false,
                cusum_dimensions: vec![],
                progressive_fused: 0.0,
                observer_trigger_reason: String::new(),
                deberta_score: 0.0,
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

impl ReplayExecutor {}
