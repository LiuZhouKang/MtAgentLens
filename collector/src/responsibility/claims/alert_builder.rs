// SPDX-License-Identifier: MIT
//
// alert_builder.rs — AlertBuilder: composes a ResponsibilityAlert from
// claims, classification results, observer output, and graph context.

use std::collections::HashSet;

use crate::responsibility::classification::types::{
    AlertAuthority as ClassAlertAuthority, ClassificationResult, ObserverDecision, ObserverOutput,
};
use crate::responsibility::graph::ResponsibilityGraph;
use crate::responsibility::types::{
    AgentId, AgentRef, AlertAuthority, AlertDecision, AlertId, AlertSeverity,
    BehaviorTimeline, DisplayProfile, EngineSection, GraphSnapshot, PatternVerdict,
    PhaseIndicator, ResponsibilityAlert, ResponsibilityClaim, RuleVerdict, ScoreBreakdown,
    ResponsibilityNodeType,
};

use crate::responsibility::scoring::risk_factors::{RiskFactorConfig, compute_risk_score};
use super::text::AlertBuilderText;

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

impl AlertBuilder {
    pub fn compose(
        claims: &[ResponsibilityClaim],
        classification_result: &ClassificationResult,
        observer_output: Option<&ObserverOutput>,
        graph: &ResponsibilityGraph,
        timeline: &BehaviorTimeline,
        fallback_agent_id: Option<&AgentId>,
    ) -> ResponsibilityAlert {
        let primary_claim = claims.first();

        // ── Timestamp ───────────────────────────────────────────────────
        let timestamp_ns = primary_claim
            .map(|c| match &c.phase {
                PhaseIndicator::Provisional { timestamp_ns } => *timestamp_ns,
                PhaseIndicator::Authoritative { timestamp_ns, .. } => *timestamp_ns,
            })
            .unwrap_or(0);

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
        ) = resolve_alert_judgment(observer_output, classification_result, has_claims);

        // ── Engine section ──────────────────────────────────────────────
        let engine = build_engine_section(
            classification_result,
            &display_profile,
            &rule_verdicts,
            &pattern_verdicts,
            classification_result.cusum_triggered,
            classification_result.cusum_dimensions.clone(),
            classification_result.progressive_fused,
            classification_result.observer_trigger_reason.clone(),
            classification_result.deberta_score,
        );

        // ── Agent references ────────────────────────────────────────────
        let primary_agent = AgentRef {
            agent_id: subject.clone(),
            claimed_role: None,
        };

        let involved_agents = collect_involved_agents(graph, &subject);

        // ── Text generation ─────────────────────────────────────────────
        let title = AlertBuilderText::generate_title(primary_claim, &display_profile);
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

        // ── Phase ───────────────────────────────────────────────────────
        let phase = primary_claim
            .map(|c| c.phase.clone())
            .unwrap_or(PhaseIndicator::Provisional { timestamp_ns: 0 });

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
    match observer_output {
        Some(out) => {
            let dp = match out.decision {
                ObserverDecision::Safe => DisplayProfile::SafeBaseline,
                ObserverDecision::Unsafe | ObserverDecision::Uncertain => {
                    DisplayProfile::ObserverJudged
                }
            };
            let dec = convert_observer_decision(&out.decision);
            let auth = convert_classification_authority(&out.authority);

            // Map classification RuleVerdict → types RuleVerdict.
            // Fields are structurally identical but types differ.
            let rvs: Vec<RuleVerdict> = out
                .rule_verdicts
                .iter()
                .map(|rv| RuleVerdict {
                    rule_id: rv.rule_id.clone(),
                    description: rv.description.clone(),
                    matched_resource: rv.matched_resource.clone(),
                    llm_agreed: rv.llm_agreed,
                    llm_comment: rv.llm_comment.clone(),
                })
                .collect();

            let pvs: Vec<PatternVerdict> = out
                .pattern_verdicts
                .iter()
                .map(|pv| PatternVerdict {
                    template_id: pv.template_id.clone(),
                    match_confidence: pv.match_confidence,
                    llm_agreed: pv.llm_agreed,
                    llm_comment: pv.llm_comment.clone(),
                })
                .collect();

            (
                dp,
                dec,
                match out.severity {
                    crate::responsibility::claims::ClaimSeverity::Info => AlertSeverity::Info,
                    crate::responsibility::claims::ClaimSeverity::Warning => AlertSeverity::Warning,
                    crate::responsibility::claims::ClaimSeverity::Critical => AlertSeverity::Critical,
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
                false,       // is_admin_process (unknown at this level)
                false,       // is_repeated
                false,       // is_critical_target
                false,       // is_multi_agent
                false,       // is_privileged_context
            );
            let risk_score = compute_risk_score(classification_result.score as f64, &risk_factors);

            let (dec, sev, reason) = if !has_claims {
                (
                    AlertDecision::Safe,
                    AlertSeverity::Info,
                    format!(
                        "No responsibility claims — classification score {:.2}, risk_score={:.1}",
                        classification_result.score, risk_score,
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

            (
                DisplayProfile::UnsafeByEngine,
                dec,
                sev,
                classification_result.score,
                AlertAuthority::Authoritative,
                reason,
                Vec::new(),
                Vec::new(),
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
    deberta_score: f64,
) -> EngineSection {
    let classification_route = match display_profile {
        DisplayProfile::SafeBaseline => classification_result.route.clone(),
        DisplayProfile::UnsafeByEngine => classification_result.route.clone(),
        DisplayProfile::ObserverJudged => classification_result.route.clone(),
    };

    let score_breakdown = ScoreBreakdown {
        rule_score: classification_result.score_breakdown.rule_score,
        pattern_score: classification_result.score_breakdown.pattern_score,
        boundary_score: classification_result.score_breakdown.boundary_score,
        evidence_score: classification_result.score_breakdown.evidence_score,
        role_score: classification_result.score_breakdown.role_score,
        repetition_score: classification_result.score_breakdown.repetition_score,
        behavioral_score: classification_result.score_breakdown.behavioral_score,
        total: classification_result.score,
    };

    EngineSection {
        rule_verdicts: rule_verdicts.to_vec(),
        pattern_verdicts: pattern_verdicts.to_vec(),
        classification_score: classification_result.score,
        classification_route,
        score_breakdown,
        cusum_triggered,
        cusum_dimensions,
        progressive_fused,
        observer_trigger_reason,
        deberta_score,
    }
}

// ═════════════════════════════════════════════════════════════════════════════
// Agent collection
// ═════════════════════════════════════════════════════════════════════════════

fn collect_involved_agents(
    graph: &ResponsibilityGraph,
    primary: &AgentId,
) -> Vec<AgentRef> {
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

fn convert_classification_authority(aa: &ClassAlertAuthority) -> AlertAuthority {
    match aa {
        ClassAlertAuthority::Authoritative => AlertAuthority::Authoritative,
        ClassAlertAuthority::Supporting => AlertAuthority::Supporting,
        ClassAlertAuthority::Unavailable => AlertAuthority::Unavailable,
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
        RuleVerdict as ClassRuleVerdict, PatternVerdict as ClassPatternVerdict,
        ScoreBreakdown as ClassScoreBreakdown,
    };
    use crate::responsibility::types::{
        AgentId, AlertSeverity, BoundaryId, ClaimId, PhaseIndicator, PolicyId,
        ResponsibilityClaim, ResponsibilityClaimState, ResponsibilityClaimType,
        BehaviorTimelineEntry,
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
            },
            cusum_triggered: false,
            cusum_dimensions: vec![],
            progressive_fused: 0.0,
            observer_trigger_reason: String::new(),
            deberta_score: 0.0,
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
        let observer = make_observer_output(
            ObserverDecision::Unsafe,
            ClaimSeverity::Critical,
            0.90,
        );
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
    fn test_compose_engine_only_safe() {
        let g = ResponsibilityGraph::default();
        let claims = vec![make_claim(
            "agent-s",
            ResponsibilityClaimType::ResponsibilityAnomaly,
            0.10,
        )];
        let class_result = make_classification_result(0.05, "safe");
        let timeline = make_timeline();

        let alert = AlertBuilder::compose(&claims, &class_result, None, &g, &timeline, None);

        assert_eq!(alert.display_profile, DisplayProfile::UnsafeByEngine);
        assert_eq!(alert.decision, AlertDecision::Safe);
        assert_eq!(alert.severity, AlertSeverity::Info);
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
}
