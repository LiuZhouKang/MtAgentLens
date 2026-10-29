// SPDX-License-Identifier: Apache-2.0 OR MIT
//
// alert_builder.rs — AlertBuilder: composes a ResponsibilityAlert from
// claims, classification results, observer output, and graph context.

use std::collections::HashSet;

use crate::responsibility::classification::final_decision::FinalVerdict;
use crate::responsibility::classification::types::{
    ClassificationResult, ObserverDecision, ObserverOutput,
};
use crate::responsibility::graph::ResponsibilityGraph;
use crate::responsibility::types::{
    AgentId, AgentRef, AlertAuthority, AlertDecision, AlertId, AlertSeverity, BehaviorTimeline,
    DisplayProfile, EngineSection, GraphSnapshot, PatternVerdict, PhaseIndicator,
    ResponsibilityAlert, ResponsibilityClaim, ResponsibilityNodeType, RuleVerdict, ScoreBreakdown,
};

use super::text::AlertBuilderText;
use crate::responsibility::scoring::risk_factors::{RiskFactorConfig, compute_risk_score};

// ═════════════════════════════════════════════════════════════════════════════
// AlertBuilder
// ═════════════════════════════════════════════════════════════════════════════

/// Composes a `ResponsibilityAlert` from four independent inputs:
///
/// 1. **Claims** — `PolicyBoundaryCrossing`-derived responsibility claims
/// 2. **ClassificationResult** — rule hits, pattern matches, score, route
/// 3. **ObserverOutput** (optional) — LLM decision, severity, confidence, verdicts
/// 4. **Graph** — structural context for involved agents and metrics
/// 5. **Timeline** — ordered behavior trace
pub struct AlertBuilder;

/// Phase context for an alert.
///
/// Grouped because the three values travel together: a provisional alert has
/// a live timestamp, an authoritative one carries the run's aggregated
/// verdict, and mixing them is what produced alerts with `timestamp_ns = 0`
/// and a final `Safe` next to a provisional `Unsafe`.
pub struct ComposeContext<'a> {
    pub timestamp_ns: u64,
    pub phase: PhaseIndicator,
    pub final_verdict: Option<&'a FinalVerdict>,
}

impl AlertBuilder {
    /// Compose an alert without an explicit phase context.
    ///
    /// Kept for callers that build an alert from a single claim (tests and
    /// replay of historical rows). The timestamp falls back to the claim's
    /// timestamp, then to the last timeline step, so an alert without claims
    /// no longer records `timestamp_ns = 0` — that made every observer-only
    /// alert unorderable and indistinguishable from an unset field.
    pub fn compose(
        claims: &[ResponsibilityClaim],
        classification_result: &ClassificationResult,
        observer_output: Option<&ObserverOutput>,
        graph: &ResponsibilityGraph,
        timeline: &BehaviorTimeline,
        fallback_agent_id: Option<&AgentId>,
    ) -> ResponsibilityAlert {
        let timestamp_ns = claims
            .first()
            .map(|c| match &c.phase {
                PhaseIndicator::Provisional { timestamp_ns } => *timestamp_ns,
                PhaseIndicator::Authoritative { timestamp_ns, .. } => *timestamp_ns,
            })
            .filter(|ts| *ts != 0)
            .or_else(|| timeline.steps.last().map(|step| step.timestamp_ns))
            .unwrap_or(0);
        let phase = claims
            .first()
            .map(|c| c.phase.clone())
            .unwrap_or(PhaseIndicator::Provisional { timestamp_ns });
        Self::compose_with_context(
            claims,
            classification_result,
            observer_output,
            graph,
            timeline,
            fallback_agent_id,
            ComposeContext {
                timestamp_ns,
                phase,
                final_verdict: None,
            },
        )
    }

    /// Compose an alert with an explicit timestamp, phase and (optionally) the
    /// run's aggregated final verdict.
    ///
    /// The phase-2 alert must be `Authoritative` and carry the run's final
    /// verdict; the phase-1 live alert must stay `Provisional`. Both used to be
    /// persisted as `Provisional { timestamp_ns: 0 }`, which is why a run could
    /// hold a phase-1 `Unsafe` and a phase-2 `Safe` with no way to tell which
    /// one was the answer.
    pub fn compose_with_context(
        claims: &[ResponsibilityClaim],
        classification_result: &ClassificationResult,
        observer_output: Option<&ObserverOutput>,
        graph: &ResponsibilityGraph,
        timeline: &BehaviorTimeline,
        fallback_agent_id: Option<&AgentId>,
        context: ComposeContext<'_>,
    ) -> ResponsibilityAlert {
        // Deterministic primary claim: highest confidence, then more evidence,
        // then the stable claim id. Taking claims.first() depended on graph/map
        // iteration order and made replay pick a different primary agent.
        let primary_claim = claims.iter().max_by(|left, right| {
            left.confidence
                .total_cmp(&right.confidence)
                .then_with(|| left.evidence_refs.len().cmp(&right.evidence_refs.len()))
                .then_with(|| right.claim_id.as_str().cmp(left.claim_id.as_str()))
        });
        let ComposeContext {
            timestamp_ns,
            phase,
            final_verdict,
        } = context;

        // ── Subject agent ───────────────────────────────────────────────
        // Prefer the claim's subject. When no claims exist (no PBC edges),
        // extract the agent from the graph's Agent nodes, then fall back
        // to the explicitly provided fallback_agent_id (from the identity
        // resolver). Only use "unknown" when nothing else is available.
        let subject = primary_claim
            .map(|c| c.subject.clone())
            .or_else(|| {
                graph
                    .all_nodes()
                    .filter(|n| n.node_type == ResponsibilityNodeType::Agent)
                    .filter_map(|n| n.agent_id.clone())
                    .next()
            })
            .or_else(|| fallback_agent_id.cloned())
            .unwrap_or_else(|| AgentId::from("unknown"));

        // ── Determine display profile, decision, severity, authority ────
        let has_claims = !claims.is_empty();
        let (
            display_profile,
            decision,
            severity,
            confidence,
            authority,
            reasoning,
            rule_verdicts,
            pattern_verdicts,
        ) = resolve_alert_judgment(
            observer_output,
            classification_result,
            has_claims,
            final_verdict,
        );

        // ── Engine section ──────────────────────────────────────────────
        let mut engine = build_engine_section(
            classification_result,
            &display_profile,
            &rule_verdicts,
            &pattern_verdicts,
            classification_result.cusum_triggered,
            classification_result.cusum_dimensions.clone(),
            classification_result.progressive_fused,
            classification_result.observer_trigger_reason.clone(),
        );
        engine.claim_ids = claims
            .iter()
            .map(|claim| claim.claim_id.to_string())
            .collect();

        // ── Agent references ────────────────────────────────────────────
        let primary_agent = AgentRef {
            agent_id: subject.clone(),
            claimed_role: None,
        };

        let involved_agents = collect_involved_agents(graph, &subject);

        // ── Text generation ─────────────────────────────────────────────
        let title = AlertBuilderText::generate_title(primary_claim, &display_profile, &severity);
        let summary = AlertBuilderText::generate_summary(claims, graph);
        let action_hint = AlertBuilderText::generate_action_hint(primary_claim);

        // ── Graph snapshot ───────────────────────────────────────────────
        let graph_snapshot = GraphSnapshot {
            snapshot_id: uuid::Uuid::new_v4().to_string(),
            timestamp_ns,
            nodes: graph.all_nodes().cloned().collect(),
            edges: graph.all_edges().cloned().collect(),
            metrics: graph.metrics(),
        };

        // Phase is supplied by the caller: phase 1 is Provisional, phase 2 is
        // Authoritative. Deriving it from the primary claim made phase-2
        // alerts with no claims look provisional.

        // ── Final confidence ────────────────────────────────────────────
        // Confidence always reflects certainty in the *decision*, not
        // certainty that a violation exists. When the score is near 0
        // (safe), confidence is high that it's safe. When the score is
        // near 1 (unsafe), confidence is high that it's unsafe.
        // Ambiguous scores (0.15–0.70) have lower confidence.
        let claim_confidence = primary_claim.map(|c| c.confidence).unwrap_or(0.5);
        let final_confidence = if observer_output.is_some() {
            confidence
        } else {
            // Engine-only: confidence = distance from ambiguity boundary.
            // Safe (score < 0.15): confidence = 1.0 - score  (e.g., 0.99 at score=0.01)
            // Unsafe (score > 0.70): confidence = score  (e.g., 0.95 at score=0.95)
            // Ambiguous: blended with claim_confidence
            let engine_confidence = if classification_result.score < 0.15 {
                1.0 - classification_result.score
            } else if classification_result.score > 0.70 {
                classification_result.score
            } else {
                // Fall through to blend
                classification_result.score * 0.6 + claim_confidence * 0.4
            };
            engine_confidence.clamp(0.0, 1.0)
        };

        ResponsibilityAlert {
            alert_id: AlertId::new(),
            timestamp_ns,
            display_profile,
            actionable: decision.is_actionable(),
            decision,
            severity,
            confidence: final_confidence,
            authority,
            reasoning,
            title,
            summary,
            action_hint,
            primary_agent,
            involved_agents,
            engine,
            timeline: timeline.clone(),
            graph_snapshot,
            phase,
            provisional_ref: None,
        }
    }
}

// ═════════════════════════════════════════════════════════════════════════════
// Judgment resolution
// ═════════════════════════════════════════════════════════════════════════════

/// Resolve the display profile, decision, severity, confidence, authority,
/// reasoning, and verdicts from the observer output (if present) or fall back
/// to engine-only classification.
fn resolve_alert_judgment(
    observer_output: Option<&ObserverOutput>,
    classification_result: &ClassificationResult,
    has_claims: bool,
    final_verdict: Option<&FinalVerdict>,
) -> (
    DisplayProfile,
    AlertDecision,
    AlertSeverity,
    f32,
    AlertAuthority,
    String,
    Vec<RuleVerdict>,
    Vec<PatternVerdict>,
) {
    if let Some(verdict) = final_verdict {
        let display_profile = match verdict.decision {
            AlertDecision::Safe => DisplayProfile::SafeBaseline,
            // A review/abstain verdict (including the degraded-capture gate)
            // must never be rendered as an engine-detected unsafe result.
            AlertDecision::Uncertain => DisplayProfile::ObserverJudged,
            AlertDecision::Unsafe => match verdict.authority {
                AlertAuthority::Supporting => DisplayProfile::ObserverJudged,
                _ => DisplayProfile::UnsafeByEngine,
            },
        };
        // Display confidence: certainty in the *aggregated* verdict, not a
        // calibrated probability. Hard evidence is authoritative; a semantic
        // verdict is supporting; review is by definition uncertain.
        let confidence = match (&verdict.decision, &verdict.authority) {
            (AlertDecision::Safe, _) => 1.0,
            (AlertDecision::Unsafe, AlertAuthority::Authoritative) => 0.95,
            (AlertDecision::Unsafe, _) => 0.80,
            (AlertDecision::Uncertain, _) => 0.50,
        };
        // The run-level verdict decides the alert, but the *evidence* that
        // produced it must stay attached. This branch used to return two empty
        // vectors, so every authoritative alert lost its rule hits and graph
        // template matches — the pattern ablation read zero for every run.
        let rule_verdicts = observer_output
            .map(|output| output.rule_verdicts.clone())
            .unwrap_or_default();
        return (
            display_profile,
            verdict.decision.clone(),
            verdict.severity.clone(),
            confidence,
            verdict.authority.clone(),
            verdict.reasoning.clone(),
            rule_verdicts,
            classification_result.pattern_verdicts.clone(),
        );
    }

    match observer_output {
        Some(out) => {
            let dp = match out.decision {
                ObserverDecision::Safe => DisplayProfile::SafeBaseline,
                ObserverDecision::Unsafe | ObserverDecision::Uncertain => {
                    DisplayProfile::ObserverJudged
                }
            };
            let dec = convert_observer_decision(&out.decision);
            let auth = out.authority.clone();
            let rvs = out.rule_verdicts.clone();
            let pvs = out.pattern_verdicts.clone();

            (
                dp,
                dec,
                match out.severity {
                    crate::responsibility::claims::ClaimSeverity::Info => AlertSeverity::Info,
                    crate::responsibility::claims::ClaimSeverity::Warning => AlertSeverity::Warning,
                    crate::responsibility::claims::ClaimSeverity::Critical => {
                        AlertSeverity::Critical
                    }
                },
                out.confidence,
                auth,
                out.reasoning.clone(),
                rvs,
                pvs,
            )
        }
        None => {
            // Engine-only fallback.
            // Without claims (no PBC edges crossed), the classification score
            // reflects observed agent activity but no actual policy violation.
            // In this case, cap severity at Info to avoid false Warning/Critical
            // alerts for benign activity (e.g., network testing scenarios).
            // ── Compute entity risk factors ─────────────────────────
            let risk_cfg = RiskFactorConfig::default();
            let risk_factors = risk_cfg.compute_factors(
                false, // is_admin_process (unknown at this level)
                false, // is_repeated
                false, // is_critical_target
                false, // is_multi_agent
                false, // is_privileged_context
            );
            let risk_score = compute_risk_score(classification_result.score as f64, &risk_factors);

            // `fail_closed_unsafe` is the historical route name still present in
            // older databases; `ambiguous_review` is the current one. Both are
            // review signals, never actionable on their own.
            let is_fail_closed = matches!(
                classification_result.route.as_str(),
                "fail_closed_unsafe" | "ambiguous_review"
            );
            let is_direct_unsafe = classification_result.route == "direct_unsafe"
                || classification_result.route == "unsafe_by_engine";

            let (dec, sev, reason) = if !has_claims && is_fail_closed {
                (
                    AlertDecision::Uncertain,
                    AlertSeverity::Warning,
                    format!(
                        "No responsibility claims, but authoritative classification used fail-closed routing for ambiguous score {:.2}, risk_score={:.1}",
                        classification_result.score, risk_score,
                    ),
                )
            } else if !has_claims && is_direct_unsafe {
                (
                    AlertDecision::Unsafe,
                    AlertSeverity::Critical,
                    format!(
                        "No responsibility claims, but engine route {} indicates unsafe behavior with score {:.2}, risk_score={:.1}",
                        classification_result.route, classification_result.score, risk_score,
                    ),
                )
            } else if !has_claims {
                (
                    AlertDecision::Safe,
                    AlertSeverity::Info,
                    format!(
                        "No responsibility claims — classification score {:.2}, risk_score={:.1}",
                        classification_result.score, risk_score,
                    ),
                )
            } else if has_claims && is_direct_unsafe {
                (
                    AlertDecision::Unsafe,
                    AlertSeverity::Critical,
                    format!(
                        "Responsibility claims observed and engine route {} indicates unsafe behavior with score {:.2}, risk_score={:.1}",
                        classification_result.route, classification_result.score, risk_score,
                    ),
                )
            } else if classification_result.score > 0.70 {
                (
                    AlertDecision::Unsafe,
                    AlertSeverity::Critical,
                    format!(
                        "Engine classification score {:.2} exceeds unsafe threshold 0.70, risk_score={:.1}",
                        classification_result.score, risk_score,
                    ),
                )
            } else if classification_result.score > 0.15 {
                (
                    AlertDecision::Uncertain,
                    AlertSeverity::Warning,
                    format!(
                        "Engine classification score {:.2} in ambiguous range (0.15–0.70], risk_score={:.1}",
                        classification_result.score, risk_score,
                    ),
                )
            } else if has_claims {
                // A claim is an observation, not a verdict. With the engine
                // score in the safe range the alert remains visible for
                // review (`Uncertain`) but is informational: calling it a
                // Warning made every benign claim a false alert (17/19 on the
                // 2026-07 corpus). The claim itself is still persisted.
                (
                    AlertDecision::Uncertain,
                    AlertSeverity::Info,
                    format!(
                        "Responsibility claims observed, but engine score {:.2} is in the safe range; retained as an informational review signal, risk_score={:.1}",
                        classification_result.score, risk_score,
                    ),
                )
            } else {
                (
                    AlertDecision::Safe,
                    AlertSeverity::Info,
                    format!(
                        "Engine classification score {:.2} below safe threshold 0.15, risk_score={:.1}",
                        classification_result.score, risk_score,
                    ),
                )
            };

            // The display profile describes the source of the judgment, not
            // its outcome: a Safe decision from the engine is a safe baseline,
            // not "judged unsafe by the engine" (which is what every Safe alert
            // used to claim).
            let display_profile = if dec == AlertDecision::Safe {
                DisplayProfile::SafeBaseline
            } else {
                DisplayProfile::UnsafeByEngine
            };
            (
                display_profile,
                dec,
                sev,
                classification_result.score,
                AlertAuthority::Authoritative,
                reason,
                Vec::new(),
                // The matcher ran on this graph; persist what it found. An
                // empty list must mean "no match", not "the result was
                // dropped" (the P1.2 absence trap).
                classification_result.pattern_verdicts.clone(),
            )
        }
    }
}

// ═════════════════════════════════════════════════════════════════════════════
// Engine section builder
// ═════════════════════════════════════════════════════════════════════════════

fn build_engine_section(
    classification_result: &ClassificationResult,
    display_profile: &DisplayProfile,
    rule_verdicts: &[RuleVerdict],
    pattern_verdicts: &[PatternVerdict],
    cusum_triggered: bool,
    cusum_dimensions: Vec<String>,
    progressive_fused: f64,
    observer_trigger_reason: String,
) -> EngineSection {
    let classification_route = match display_profile {
        DisplayProfile::SafeBaseline => classification_result.route.clone(),
        DisplayProfile::UnsafeByEngine => classification_result.route.clone(),
        DisplayProfile::ObserverJudged => classification_result.route.clone(),
    };

    // `score_breakdown.total` is the engine's own gated score, computed from
    // the six sub-scores. `classification_score` below is the *final* score,
    // which the Observer may have adjusted. They were previously conflated:
    // a persisted row could show six zero sub-scores with total 0.45 (the
    // Observer confidence), making the decomposition impossible to audit.
    let score_breakdown = ScoreBreakdown {
        rule_score: classification_result.score_breakdown.rule_score,
        pattern_score: classification_result.score_breakdown.pattern_score,
        boundary_score: classification_result.score_breakdown.boundary_score,
        evidence_score: classification_result.score_breakdown.evidence_score,
        role_score: classification_result.score_breakdown.role_score,
        repetition_score: classification_result.score_breakdown.repetition_score,
        behavioral_score: classification_result.score_breakdown.behavioral_score,
        total: classification_result.score_breakdown.total,
        raw_score: classification_result.score_breakdown.raw_score,
        calibrated_score: classification_result.score_breakdown.calibrated_score,
        gate_factor: classification_result.score_breakdown.gate_factor,
        gate_version: classification_result.score_breakdown.gate_version.clone(),
        platt_a: classification_result.score_breakdown.platt_a,
        platt_b: classification_result.score_breakdown.platt_b,
        observer_adjusted: matches!(display_profile, DisplayProfile::ObserverJudged),
    };

    EngineSection {
        claim_ids: Vec::new(),
        rule_verdicts: rule_verdicts.to_vec(),
        pattern_verdicts: pattern_verdicts.to_vec(),
        classification_score: classification_result.score,
        classification_route,
        score_breakdown,
        cusum_triggered,
        cusum_dimensions,
        progressive_fused,
        observer_trigger_reason,
        score_model_version: "six-dim/v14".to_string(),
    }
}

// ═════════════════════════════════════════════════════════════════════════════
// Agent collection
// ═════════════════════════════════════════════════════════════════════════════

fn collect_involved_agents(graph: &ResponsibilityGraph, primary: &AgentId) -> Vec<AgentRef> {
    let mut seen: HashSet<AgentId> = HashSet::new();

    graph
        .all_nodes()
        .filter_map(|n| n.agent_id.as_ref())
        .filter(|aid| {
            if *aid == primary {
                return false;
            }
            seen.insert((*aid).clone())
        })
        .map(|aid| AgentRef {
            agent_id: aid.clone(),
            claimed_role: None,
        })
        .collect()
}

// ═════════════════════════════════════════════════════════════════════════════
// Type conversions
// ═════════════════════════════════════════════════════════════════════════════

fn convert_observer_decision(dec: &ObserverDecision) -> AlertDecision {
    match dec {
        ObserverDecision::Safe => AlertDecision::Safe,
        ObserverDecision::Unsafe => AlertDecision::Unsafe,
        ObserverDecision::Uncertain => AlertDecision::Uncertain,
    }
}

// ═════════════════════════════════════════════════════════════════════════════
// Tests
// ═════════════════════════════════════════════════════════════════════════════

#[cfg(test)]
mod tests {
    use super::*;
    use crate::responsibility::claims::ClaimSeverity;
    use crate::responsibility::classification::types::{
        AlertAuthority as ClassAlertAuthority, CacheTTL, ObserverDecision, ObserverOutput,
        PatternVerdict as ClassPatternVerdict, RuleVerdict as ClassRuleVerdict,
        ScoreBreakdown as ClassScoreBreakdown,
    };
    use crate::responsibility::types::{
        AgentId, AlertSeverity, BehaviorTimelineEntry, BoundaryId, ClaimId, PhaseIndicator,
        PolicyId, ResponsibilityClaim, ResponsibilityClaimState, ResponsibilityClaimType,
    };

    fn make_classification_result(score: f32, route: &str) -> ClassificationResult {
        ClassificationResult {
            score,
            route: route.to_string(),
            score_breakdown: ClassScoreBreakdown {
                rule_score: score * 0.3,
                pattern_score: score * 0.2,
                boundary_score: score * 0.2,
                evidence_score: score * 0.15,
                role_score: score * 0.05,
                repetition_score: score * 0.1,
                behavioral_score: score * 0.1,
                total: score,
                raw_score: score,
                calibrated_score: score,
                gate_factor: 1.0,
                gate_version: "test".into(),
                platt_a: 7.0,
                platt_b: -3.0,
                observer_adjusted: false,
            },
            cusum_triggered: false,
            cusum_dimensions: vec![],
            progressive_fused: 0.0,
            observer_trigger_reason: String::new(),
            pattern_verdicts: vec![],
        }
    }

    fn make_observer_output(
        decision: ObserverDecision,
        severity: ClaimSeverity,
        confidence: f32,
    ) -> ObserverOutput {
        ObserverOutput {
            decision,
            severity,
            confidence,
            cache_ttl: CacheTTL::Once,
            authority: ClassAlertAuthority::Supporting,
            reasoning: "Observer LLM evaluation".to_string(),
            attacked_flag: false,
            evidence_cited: vec![],
            rule_verdicts: vec![ClassRuleVerdict {
                rule_id: "rule.test".into(),
                description: "Test rule".into(),
                matched_resource: "/test/path".into(),
                llm_agreed: Some(true),
                llm_comment: Some("Confirmed".into()),
            }],
            pattern_verdicts: vec![ClassPatternVerdict {
                template_id: "tpl.test".into(),
                match_confidence: 0.85,
                llm_agreed: Some(true),
                llm_comment: Some("Matched pattern".into()),
            }],
            candidate_verdicts: vec![],
            review_provenance: vec![],
        }
    }

    fn make_claim(
        subject: &str,
        claim_type: ResponsibilityClaimType,
        confidence: f32,
    ) -> ResponsibilityClaim {
        ResponsibilityClaim {
            claim_id: ClaimId::new(),
            claim_type,
            subject: AgentId::from(subject),
            policy_id: PolicyId::from("policy.test"),
            boundary_id: BoundaryId::from("boundary.test"),
            claim_state: ResponsibilityClaimState::Observed,
            severity: AlertSeverity::Warning,
            confidence,
            evidence_refs: vec![],
            competing_explanations: vec![],
            traceback_ref: None,
            phase: PhaseIndicator::Provisional { timestamp_ns: 1000 },
        }
    }

    fn make_timeline() -> BehaviorTimeline {
        BehaviorTimeline {
            steps: vec![BehaviorTimelineEntry {
                timestamp_ns: 1000,
                event_type: "test_event".to_string(),
                description: "Test event".to_string(),
                resource: None,
                evidence_ref: None,
                ..Default::default()
            }],
            total_duration_ms: 0,
        }
    }

    #[test]
    fn engine_breakdown_total_survives_observer_adjustment() {
        let mut g = ResponsibilityGraph::default();
        g.add_node(crate::responsibility::types::ResponsibilityNode {
            node_id: crate::responsibility::types::NodeId::from("n1"),
            node_type: crate::responsibility::types::ResponsibilityNodeType::Agent,
            agent_id: Some(AgentId::from("agent-1")),
            resource: None,
            evidence_refs: vec![],
            timestamp_ns: 1000,
            phase: PhaseIndicator::Provisional { timestamp_ns: 1000 },
        });
        let mut class_result = make_classification_result(0.20, "observer_unsafe");
        // The Observer-adjusted final score differs from the engine score.
        class_result.score = 0.45;
        let observer =
            make_observer_output(ObserverDecision::Unsafe, ClaimSeverity::Critical, 0.45);
        let alert = AlertBuilder::compose(
            &[],
            &class_result,
            Some(&observer),
            &g,
            &make_timeline(),
            None,
        );
        assert_eq!(alert.engine.score_breakdown.total, 0.20);
        assert_eq!(alert.engine.classification_score, 0.45);
    }

    #[test]
    fn test_compose_with_observer() {
        let mut g = ResponsibilityGraph::default();
        g.add_node(crate::responsibility::types::ResponsibilityNode {
            node_id: crate::responsibility::types::NodeId::from("n1"),
            node_type: crate::responsibility::types::ResponsibilityNodeType::Agent,
            agent_id: Some(AgentId::from("agent-1")),
            resource: None,
            evidence_refs: vec![],
            timestamp_ns: 1000,
            phase: PhaseIndicator::Provisional { timestamp_ns: 1000 },
        });

        let claims = vec![make_claim(
            "agent-1",
            ResponsibilityClaimType::PolicyViolation,
            0.80,
        )];
        let class_result = make_classification_result(0.75, "observer");
        let observer =
            make_observer_output(ObserverDecision::Unsafe, ClaimSeverity::Critical, 0.90);
        let timeline = make_timeline();

        let alert =
            AlertBuilder::compose(&claims, &class_result, Some(&observer), &g, &timeline, None);

        assert_eq!(alert.display_profile, DisplayProfile::ObserverJudged);
        assert_eq!(alert.decision, AlertDecision::Unsafe);
        assert_eq!(alert.severity, AlertSeverity::Critical);
        assert_eq!(alert.confidence, 0.90);
        assert_eq!(alert.authority, AlertAuthority::Supporting);
        assert!(alert.title.contains("Observer reviewed"));
        assert!(!alert.engine.rule_verdicts.is_empty());
    }

    #[test]
    fn test_compose_engine_only_unsafe() {
        let g = ResponsibilityGraph::default();
        let claims = vec![make_claim(
            "agent-e",
            ResponsibilityClaimType::PolicyViolation,
            0.85,
        )];
        let class_result = make_classification_result(0.85, "unsafe_by_engine");
        let timeline = make_timeline();

        let alert = AlertBuilder::compose(&claims, &class_result, None, &g, &timeline, None);

        assert_eq!(alert.display_profile, DisplayProfile::UnsafeByEngine);
        assert_eq!(alert.decision, AlertDecision::Unsafe);
        assert_eq!(alert.severity, AlertSeverity::Critical);
        assert_eq!(alert.authority, AlertAuthority::Authoritative);
    }

    #[test]
    fn capture_gate_uncertain_is_not_rendered_as_engine_unsafe() {
        let g = ResponsibilityGraph::default();
        let class_result = make_classification_result(0.0, "safe");
        let timeline = make_timeline();
        let verdict = FinalVerdict {
            decision: AlertDecision::Uncertain,
            severity: AlertSeverity::Warning,
            authority: AlertAuthority::Unavailable,
            reasoning: "Capture completeness is unproven or degraded.".into(),
            provenance: vec!["capture_quality:degraded".into()],
        };
        let alert = AlertBuilder::compose_with_context(
            &[],
            &class_result,
            None,
            &g,
            &timeline,
            None,
            ComposeContext {
                timestamp_ns: 7,
                phase: PhaseIndicator::Provisional { timestamp_ns: 7 },
                final_verdict: Some(&verdict),
            },
        );
        assert_eq!(alert.decision, AlertDecision::Uncertain);
        assert_eq!(alert.display_profile, DisplayProfile::ObserverJudged);
        assert!(!alert.title.contains("no policy violations"));
    }

    #[test]
    fn test_compose_engine_only_safe() {
        let g = ResponsibilityGraph::default();
        let class_result = make_classification_result(0.05, "safe");
        let timeline = make_timeline();

        let alert = AlertBuilder::compose(&[], &class_result, None, &g, &timeline, None);

        assert_eq!(alert.display_profile, DisplayProfile::SafeBaseline);
        assert_eq!(alert.decision, AlertDecision::Safe);
        assert_eq!(alert.severity, AlertSeverity::Info);
    }

    /// A claim is an observation, not a verdict. When the engine score is in
    /// the safe range, the alert stays visible (`Uncertain`, needs review) but
    /// is informational, not a Warning. This is what makes Warning+ usable:
    /// on the 2026-07 corpus it moves benign Warning+ from 17/19 to 0/19
    /// while keeping 63/67 attack scenarios.
    #[test]
    fn claim_with_a_safe_engine_score_is_informational() {
        let g = ResponsibilityGraph::default();
        let claims = vec![make_claim(
            "agent-s",
            ResponsibilityClaimType::PolicyViolation,
            0.80,
        )];
        let class_result = make_classification_result(0.05, "direct_safe");
        let timeline = make_timeline();

        let alert = AlertBuilder::compose(&claims, &class_result, None, &g, &timeline, None);

        assert_eq!(alert.decision, AlertDecision::Uncertain);
        assert_eq!(alert.severity, AlertSeverity::Info);
        assert!(!alert.actionable);
        assert!(alert.reasoning.contains("Responsibility claims observed"));
        assert!(alert.title.contains("Engine alert"));
        assert!(
            alert.title.contains("Info"),
            "the title must state the alert's severity, not the claim's: {}",
            alert.title
        );
    }

    #[test]
    fn test_compose_empty_claims() {
        let g = ResponsibilityGraph::default();
        let class_result = make_classification_result(0.10, "safe");
        let timeline = make_timeline();

        let alert = AlertBuilder::compose(&[], &class_result, None, &g, &timeline, None);

        assert_eq!(alert.primary_agent.agent_id, AgentId::from("unknown"));
        assert!(alert.summary.contains("No responsibility claims"));
    }

    #[test]
    fn test_compose_empty_claims_fail_closed_stays_warning() {
        let g = ResponsibilityGraph::default();
        let class_result = make_classification_result(0.16, "fail_closed_unsafe");
        let timeline = make_timeline();

        let alert = AlertBuilder::compose(&[], &class_result, None, &g, &timeline, None);

        assert_eq!(alert.decision, AlertDecision::Uncertain);
        assert_eq!(alert.severity, AlertSeverity::Warning);
        assert!(alert.reasoning.contains("fail-closed"));
    }

    #[test]
    fn test_compose_empty_claims_direct_unsafe_stays_critical() {
        let g = ResponsibilityGraph::default();
        let class_result = make_classification_result(0.85, "direct_unsafe");
        let timeline = make_timeline();

        let alert = AlertBuilder::compose(&[], &class_result, None, &g, &timeline, None);

        assert_eq!(alert.decision, AlertDecision::Unsafe);
        assert_eq!(alert.severity, AlertSeverity::Critical);
    }

    #[test]
    fn test_involved_agents_excludes_primary() {
        let mut g = ResponsibilityGraph::default();
        g.add_node(crate::responsibility::types::ResponsibilityNode {
            node_id: crate::responsibility::types::NodeId::from("n1"),
            node_type: crate::responsibility::types::ResponsibilityNodeType::Agent,
            agent_id: Some(AgentId::from("primary")),
            resource: None,
            evidence_refs: vec![],
            timestamp_ns: 1000,
            phase: PhaseIndicator::Provisional { timestamp_ns: 1000 },
        });
        g.add_node(crate::responsibility::types::ResponsibilityNode {
            node_id: crate::responsibility::types::NodeId::from("n2"),
            node_type: crate::responsibility::types::ResponsibilityNodeType::Agent,
            agent_id: Some(AgentId::from("other")),
            resource: None,
            evidence_refs: vec![],
            timestamp_ns: 2000,
            phase: PhaseIndicator::Provisional { timestamp_ns: 2000 },
        });

        let claims = vec![make_claim(
            "primary",
            ResponsibilityClaimType::PolicyViolation,
            0.80,
        )];
        let class_result = make_classification_result(0.15, "ambiguous");
        let timeline = make_timeline();

        let alert = AlertBuilder::compose(&claims, &class_result, None, &g, &timeline, None);

        assert_eq!(alert.primary_agent.agent_id, AgentId::from("primary"));
        assert_eq!(alert.involved_agents.len(), 1);
        assert_eq!(alert.involved_agents[0].agent_id, AgentId::from("other"));
    }

    /// The engine section must carry the matcher's verdicts. Dropping them
    /// made a fired template indistinguishable from no match in the DB.
    #[test]
    fn engine_only_alert_carries_pattern_verdicts() {
        let g = ResponsibilityGraph::default();
        let timeline = make_timeline();
        let mut class_result = make_classification_result(0.20, "ambiguous");
        class_result.pattern_verdicts = vec![PatternVerdict {
            template_id: "tpl.x".into(),
            match_confidence: 0.8,
            llm_agreed: None,
            llm_comment: None,
        }];

        let alert = AlertBuilder::compose(
            &[],
            &class_result,
            None,
            &g,
            &timeline,
            Some(&AgentId::from("agent-a")),
        );

        assert_eq!(alert.engine.pattern_verdicts.len(), 1);
        assert_eq!(alert.engine.pattern_verdicts[0].template_id, "tpl.x");
    }

    /// Product contract surfaced on the wire: `actionable` is true exactly
    /// when `decision == Unsafe`. An `Uncertain` alert stays visible as a
    /// signal but must not be presented as actionable.
    #[test]
    fn alert_links_only_the_exact_claims_it_composed() {
        let mut selected = make_claim("selected", ResponsibilityClaimType::TaintedContextUse, 0.9);
        selected.claim_id = ClaimId::from("claim-selected");
        let unrelated = make_claim("unrelated", ResponsibilityClaimType::PolicyViolation, 0.99);
        let graph = ResponsibilityGraph::with_default_config();
        let classification = make_classification_result(0.9, "direct_unsafe");
        let alert = AlertBuilder::compose(
            &[selected.clone()],
            &classification,
            None,
            &graph,
            &make_timeline(),
            None,
        );
        assert_eq!(alert.engine.claim_ids, vec!["claim-selected"]);
        assert!(
            !alert
                .engine
                .claim_ids
                .contains(&unrelated.claim_id.to_string())
        );
        let no_claims = AlertBuilder::compose(
            &[],
            &classification,
            None,
            &graph,
            &make_timeline(),
            Some(&AgentId::from("selected")),
        );
        assert!(
            no_claims.engine.claim_ids.is_empty(),
            "Unsafe without linked claims has no invented claim type"
        );
        let legacy: EngineSection = serde_json::from_str("{}").unwrap();
        assert!(legacy.claim_ids.is_empty());
    }

    #[test]
    fn compose_with_context_keeps_explicit_timestamp_and_phase() {
        use crate::responsibility::classification::final_decision::{
            Observation, aggregate_final_decision,
        };

        let g = ResponsibilityGraph::default();
        let timeline = make_timeline();
        let verdict = aggregate_final_decision(&[Observation::HardViolation {
            rule_ids: vec!["unsafe.shadow".into()],
        }]);
        let alert = AlertBuilder::compose_with_context(
            &[],
            &make_classification_result(0.9, "direct_unsafe"),
            None,
            &g,
            &timeline,
            Some(&AgentId::from("agent-a")),
            ComposeContext {
                timestamp_ns: 1_700_000_000_000_000_000,
                phase: PhaseIndicator::Authoritative {
                    timestamp_ns: 1_700_000_000_000_000_000,
                    overrides: None,
                },
                final_verdict: Some(&verdict),
            },
        );

        assert_eq!(alert.timestamp_ns, 1_700_000_000_000_000_000);
        assert!(matches!(alert.phase, PhaseIndicator::Authoritative { .. }));
        assert_eq!(alert.decision, AlertDecision::Unsafe);
        assert_eq!(alert.authority, AlertAuthority::Authoritative);
        assert!(alert.actionable);
    }

    #[test]
    fn final_verdict_overrides_a_contradictory_engine_route() {
        use crate::responsibility::classification::final_decision::{
            Observation, aggregate_final_decision,
        };

        let g = ResponsibilityGraph::default();
        let timeline = make_timeline();
        // The engine says Safe (low static score) but the observer consensus
        // is Unsafe with cited evidence. The aggregate must win.
        let verdict = aggregate_final_decision(&[Observation::ObserverUnsafe {
            confidence: 0.85,
            cited: 2,
        }]);
        let alert = AlertBuilder::compose_with_context(
            &[],
            &make_classification_result(0.02, "direct_safe"),
            None,
            &g,
            &timeline,
            Some(&AgentId::from("agent-a")),
            ComposeContext {
                timestamp_ns: 42,
                phase: PhaseIndicator::Provisional { timestamp_ns: 42 },
                final_verdict: Some(&verdict),
            },
        );
        assert_eq!(alert.decision, AlertDecision::Unsafe);
        assert_eq!(alert.display_profile, DisplayProfile::ObserverJudged);
    }

    #[test]
    fn compose_without_claims_gets_a_real_timestamp_from_the_timeline() {
        let g = ResponsibilityGraph::default();
        let mut timeline = make_timeline();
        timeline
            .steps
            .push(crate::responsibility::types::BehaviorTimelineEntry {
                timestamp_ns: 12345,
                event_type: "file_read".into(),
                description: "read a file".into(),
                resource: None,
                evidence_ref: None,
                agent_id: Some(AgentId::from("agent-a")),
                operation: Some("FileRead".into()),
                evidence_level: None,
                ..Default::default()
            });
        let alert = AlertBuilder::compose(
            &[],
            &make_classification_result(0.0, "direct_safe"),
            None,
            &g,
            &timeline,
            Some(&AgentId::from("agent-a")),
        );
        assert_eq!(alert.timestamp_ns, 12345);
    }

    #[test]
    fn composed_alert_actionable_equals_decision_unsafe() {
        let g = ResponsibilityGraph::default();
        let timeline = make_timeline();
        let fallback = AgentId::from("agent-a");

        let safe = AlertBuilder::compose(
            &[],
            &make_classification_result(0.0, "direct_safe"),
            None,
            &g,
            &timeline,
            Some(&fallback),
        );
        assert_eq!(safe.decision, AlertDecision::Safe);
        assert!(!safe.actionable);

        let claims = vec![make_claim(
            "agent-a",
            ResponsibilityClaimType::TaintedContextUse,
            0.60,
        )];
        let uncertain = AlertBuilder::compose(
            &claims,
            &make_classification_result(0.40, "ambiguous"),
            None,
            &g,
            &timeline,
            Some(&fallback),
        );
        assert_eq!(uncertain.decision, AlertDecision::Uncertain);
        assert!(!uncertain.actionable);

        let unsafe_alert = AlertBuilder::compose(
            &claims,
            &make_classification_result(0.90, "unsafe_by_engine"),
            None,
            &g,
            &timeline,
            Some(&fallback),
        );
        assert_eq!(unsafe_alert.decision, AlertDecision::Unsafe);
        assert!(unsafe_alert.actionable);
    }
}
