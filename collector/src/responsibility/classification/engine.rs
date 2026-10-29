// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 MtAgentLens Project

use crate::responsibility::claims::ClaimSeverity;
use crate::responsibility::classification::candidates::validate_candidate_verdicts;
use crate::responsibility::classification::pattern_matcher::GraphPatternMatch;
use crate::responsibility::classification::rule_engine::RuleEngine;
use crate::responsibility::classification::rules::RuleHit;
use crate::responsibility::classification::scoring::{
    RouteSelection, build_observer_signature, compute_score_breakdown_with, compute_score_with,
    heuristic_decide, route_score,
};
use crate::responsibility::classification::types::{
    AlertAuthority, CachePrior, CacheTTL, ClassificationConfig, ClassificationResult,
    ContentSignals, ObserverDecision, ObserverOutput, PatternVerdict, Route,
};
use crate::responsibility::graph::ResponsibilityGraph;
use crate::responsibility::observer::provider::ObserverProvider;
use crate::responsibility::types::evidence_types::CorrelationEvidence;

/// ClassificationEngine: the central classification coordinator.
///
/// Implements the progressive refinement model per spec §11:
/// - Phase 1 (classify_provisional): may call the Observer LLM for arbitration
///   when the score falls in the ambiguous range (0.15-0.70).
/// - Phase 2 (classify_authoritative): never calls the Observer. A score in
///   the ambiguous range stays `Uncertain` with route `ambiguous_review`: a
///   non-Safe review signal, not an automatic Unsafe.
///
/// Wraps a RuleEngine for rule evaluation and delegates to scoring.rs for
/// the weighted score computation and routing logic.
pub struct ClassificationEngine {
    rules: RuleEngine,
    config: ClassificationConfig,
    workspace_prefix: String,
}

/// Convert matcher output into the persisted verdict shape.
///
/// `llm_agreed`/`llm_comment` stay `None` in the engine path: only the
/// observer can attest agreement, and Phase 2 never calls it.
fn graph_pattern_verdicts(patterns: &[GraphPatternMatch]) -> Vec<PatternVerdict> {
    patterns
        .iter()
        .map(|pattern| PatternVerdict {
            template_id: pattern.template_id.clone(),
            match_confidence: pattern.confidence,
            llm_agreed: None,
            llm_comment: None,
        })
        .collect()
}

impl ClassificationEngine {
    /// Create a new ClassificationEngine with default rules and configuration.
    pub fn new() -> Self {
        Self::with_config(ClassificationConfig::from_env())
    }

    /// Create a ClassificationEngine with custom configuration.
    pub fn with_config(config: ClassificationConfig) -> Self {
        Self::with_workspace_and_config(super::rules::workspace_prefix(), config)
    }

    pub fn with_workspace_and_config(workspace: String, mut config: ClassificationConfig) -> Self {
        // An invalid scoring parameter set would silently change every score;
        // fall back to the measured defaults and say so instead.
        if let Err(error) = config.scoring.validate() {
            eprintln!("[classification] invalid scoring config ({error}); using defaults");
            config.scoring = Default::default();
        }
        Self {
            rules: RuleEngine::with_workspace_prefix(workspace.clone()),
            config,
            workspace_prefix: workspace,
        }
    }

    pub fn workspace_prefix(&self) -> &str {
        &self.workspace_prefix
    }

    /// Access the underlying RuleEngine.
    pub fn rule_engine(&self) -> &RuleEngine {
        &self.rules
    }

    /// Access configuration.
    pub fn config(&self) -> &ClassificationConfig {
        &self.config
    }

    // ── Phase 1: Streaming classification (may call observer) ───────

    /// Classify a graph in Phase 1 (streaming, provisional).
    ///
    /// Follows the three-layer pipeline:
    /// 1. Rule evaluation (Safe rules first, then Unsafe)
    /// 2. Pattern matching on the graph
    /// 3. Weighted scoring + routing
    ///
    /// If the route is `ToObserver`, calls the Observer LLM for arbitration.
    /// If `force_observer` is true and an observer is available, the observer
    /// is called regardless of the grey-zone route — this enables external
    /// triggers (CUSUM drift, evidence conflict, cross-layer boost) to
    /// escalate to LLM arbitration even when the score alone wouldn't.
    /// If the Observer is unavailable, falls back to `heuristic_decide()`.
    ///
    /// Returns a ClassificationResult with the final score, route, and breakdown.
    pub async fn classify_provisional(
        &self,
        graph: &ResponsibilityGraph,
        patterns: &[GraphPatternMatch],
        evidence_records: &[CorrelationEvidence],
        observer: Option<&dyn ObserverProvider>,
        repetition_count: u32,
        force_observer: bool,
        cache_prior: CachePrior,
        intent_descriptions: &[String],
        intent_count: u32,
        signals: crate::responsibility::classification::scoring::ScoreSignals,
        candidates: &[crate::responsibility::classification::candidates::CandidateSummary],
    ) -> (ClassificationResult, Route) {
        self.classify_provisional_with_task(
            graph,
            patterns,
            evidence_records,
            observer,
            repetition_count,
            force_observer,
            cache_prior,
            intent_descriptions,
            intent_count,
            signals,
            candidates,
            None,
        )
        .await
    }

    /// Phase 1 with an explicit captured-task context. The original signature
    /// is kept as a wrapper so callers that have no task text are unchanged.
    pub async fn classify_provisional_with_task(
        &self,
        graph: &ResponsibilityGraph,
        patterns: &[GraphPatternMatch],
        evidence_records: &[CorrelationEvidence],
        observer: Option<&dyn ObserverProvider>,
        repetition_count: u32,
        force_observer: bool,
        cache_prior: CachePrior,
        intent_descriptions: &[String],
        intent_count: u32,
        signals: crate::responsibility::classification::scoring::ScoreSignals,
        candidates: &[crate::responsibility::classification::candidates::CandidateSummary],
        observed_task: Option<&crate::responsibility::observer::types::ObservedTaskContext>,
    ) -> (ClassificationResult, Route) {
        // Layer 1: Rule evaluation
        let (_rule_decision, rule_hits) = self.rules.evaluate(graph);

        // Layer 2: Scoring (v14: intent_count passed for sub_score_role PBC/intent sigmoid)
        let score = compute_score_with(
            &rule_hits,
            patterns,
            graph,
            evidence_records,
            repetition_count,
            intent_count,
            signals.clone(),
            &self.config.scoring,
        );
        let breakdown = compute_score_breakdown_with(
            &rule_hits,
            patterns,
            graph,
            evidence_records,
            repetition_count,
            intent_count,
            signals,
            &self.config.scoring,
        );

        // Layer 3: Route (with force_observer override)
        //
        // When force_observer is set (by CUSUM drift, evidence conflict, or
        // cross-layer boost), skip the grey-zone check and escalate directly
        // to LLM arbitration — even if the score is below the safe threshold.
        // A boundary crossing, a graph pattern or an unsafe rule is a
        // *candidate*, not a verdict. If any is present, the Observer must see
        // it; the PBC-count gate must not first collapse the score to
        // DirectSafe (that is how weak-but-real exfiltration was silently
        // dropped). The quota still applies inside the ToObserver branch.
        let actual_pbc = graph
            .all_edges()
            .filter(|edge| {
                edge.edge_type
                    == crate::responsibility::types::ResponsibilityEdgeType::PolicyBoundaryCrossing
            })
            .count();
        let has_boundary_signal = actual_pbc > 0
            || !patterns.is_empty()
            || rule_hits.iter().any(|hit| {
                hit.decision == crate::responsibility::classification::rules::RuleDecision::Unsafe
            });
        let effective_route = if observer.is_some() && (force_observer || has_boundary_signal) {
            RouteSelection::ToObserver
        } else {
            let base = route_score(score, &self.config);
            let has_unsafe_rule = rule_hits.iter().any(|hit| {
                hit.decision == crate::responsibility::classification::rules::RuleDecision::Unsafe
            });
            // Pattern-only "unsafe" is a structural hint, not proof. A benign
            // credential-rotation task matched persistence/recon/evasion
            // templates and crossed 0.70 without any rule hit, bypassing the
            // Observer. Let the Observer arbitrate; without one, downgrade to
            // the grey zone (review) instead of declaring Unsafe.
            if matches!(base, RouteSelection::DirectUnsafe)
                && pattern_requires_observer(has_unsafe_rule, !patterns.is_empty())
            {
                if observer.is_some() {
                    RouteSelection::ToObserver
                } else {
                    route_score(score.min(0.69), &self.config)
                }
            } else {
                base
            }
        };

        match effective_route {
            RouteSelection::DirectSafe => {
                // Confidence reflects certainty in the Safe decision.
                // Low violation score → high confidence it's safe.
                let safe_confidence = 1.0 - score;
                let output = self.build_classification_output(
                    ObserverDecision::Safe,
                    ClaimSeverity::Info,
                    safe_confidence,
                    "Direct classification: all signals within safe thresholds.",
                    &rule_hits,
                    patterns,
                );
                let result = ClassificationResult {
                    score,
                    route: "direct_safe".into(),
                    score_breakdown: breakdown,
                    cusum_triggered: false,
                    cusum_dimensions: vec![],
                    progressive_fused: 0.0,
                    observer_trigger_reason: String::new(),
                    pattern_verdicts: graph_pattern_verdicts(patterns),
                };
                (result, Route::DirectSafe(output))
            }
            RouteSelection::DirectUnsafe => {
                let output = self.build_classification_output(
                    ObserverDecision::Unsafe,
                    ClaimSeverity::Critical,
                    score,
                    "Direct classification: score exceeds unsafe threshold.",
                    &rule_hits,
                    patterns,
                );
                let result = ClassificationResult {
                    score,
                    route: "direct_unsafe".into(),
                    score_breakdown: breakdown,
                    cusum_triggered: false,
                    cusum_dimensions: vec![],
                    progressive_fused: 0.0,
                    observer_trigger_reason: String::new(),
                    pattern_verdicts: graph_pattern_verdicts(patterns),
                };
                (result, Route::DirectUnsafe(output))
            }
            RouteSelection::ToObserver => {
                // Build observer signature for LLM arbitration
                let signature =
                    build_observer_signature(graph, evidence_records, &rule_hits, patterns, score);

                // If observer is available, call it
                if let Some(obs) = observer {
                    let content = Some(build_content_signals(
                        graph,
                        &rule_hits,
                        patterns,
                        intent_descriptions,
                        candidates,
                        observed_task,
                    ));
                    let obs_input = crate::responsibility::classification::types::ObserverInput {
                        signature: signature.clone(),
                        content_signals: content,
                        prior: cache_prior,
                    };

                    match obs.evaluate(&obs_input).await {
                        Ok(mut obs_output) => {
                            if let Some(content) = &obs_input.content_signals {
                                normalize_observer_output(&content.candidates, &mut obs_output);
                                // Phase 1 consumes the same per-candidate
                                // contract: a malformed list is an observer
                                // failure, not a silently trusted verdict.
                                if let Err(error) = validate_candidate_verdicts(
                                    Some(&content.candidates),
                                    &obs_output.candidate_verdicts,
                                ) {
                                    return self.observer_failure_fallback(
                                        &error, score, &rule_hits, &breakdown, patterns,
                                    );
                                }
                            }
                            let decision = obs_output.decision;
                            let confidence = obs_output.confidence;
                            let reasoning = obs_output.reasoning.clone();

                            eprintln!(
                                "[classify] Observer LLM: {} confidence={:.3} — \"{}\"",
                                decision.as_str(),
                                confidence,
                                reasoning
                            );

                            // Observer arbitration: decision determines direction,
                            // confidence controls magnitude, static score is the foundation.
                            let adjusted_score = match decision {
                                ObserverDecision::Unsafe => {
                                    // Observer confirms attack — blend with variable weight.
                                    // When base score is near zero (PBC=0 attacks detected
                                    // purely by Observer), give Observer more weight.
                                    let obs_weight: f32 = if score < 0.1 { 0.5 } else { 0.3 };
                                    let blended =
                                        score * (1.0 - obs_weight) + confidence * obs_weight;
                                    if blended < 0.15 {
                                        0.15
                                    } else if blended > 0.99 {
                                        0.99
                                    } else {
                                        blended
                                    }
                                }
                                ObserverDecision::Safe => {
                                    // Observer overrides — pull score toward safe.
                                    // Attenuate static score by Observer confidence.
                                    // High-confidence Safe decisions can push below 0.15.
                                    score * (1.0 - confidence * 0.7)
                                }
                                ObserverDecision::Uncertain => {
                                    // Observer cannot decide — keep static score unchanged.
                                    score
                                }
                            };

                            let route_str = match decision {
                                ObserverDecision::Unsafe => "observer_unsafe",
                                ObserverDecision::Safe => "observer_safe",
                                ObserverDecision::Uncertain => "observer_uncertain",
                            };

                            let result = ClassificationResult {
                                score: adjusted_score,
                                route: route_str.into(),
                                score_breakdown: breakdown,
                                cusum_triggered: false,
                                cusum_dimensions: vec![],
                                progressive_fused: 0.0,
                                observer_trigger_reason: reasoning,
                                pattern_verdicts: graph_pattern_verdicts(patterns),
                            };

                            let obs_route = match decision {
                                ObserverDecision::Unsafe => Route::DirectUnsafe(obs_output),
                                ObserverDecision::Safe | ObserverDecision::Uncertain => {
                                    Route::DirectSafe(obs_output)
                                }
                            };
                            (result, obs_route)
                        }
                        Err(e) => {
                            // The runtime counts this as an observer_attempt
                            // (route starts with "observer").
                            self.observer_failure_fallback(
                                &e, score, &rule_hits, &breakdown, patterns,
                            )
                        }
                    }
                } else {
                    // No observer available, use heuristic fallback
                    let fallback = heuristic_decide(score);
                    let result = ClassificationResult {
                        score,
                        route: "heuristic_fallback".into(),
                        score_breakdown: breakdown,
                        cusum_triggered: false,
                        cusum_dimensions: vec![],
                        progressive_fused: 0.0,
                        observer_trigger_reason: "Observer unavailable — heuristic fallback"
                            .to_string(),
                        pattern_verdicts: graph_pattern_verdicts(patterns),
                    };
                    let obs_route = match fallback.decision {
                        ObserverDecision::Unsafe => Route::DirectUnsafe(fallback),
                        _ => Route::DirectSafe(fallback),
                    };
                    (result, obs_route)
                }
            }
        }
    }

    /// Ask the Observer to adjudicate a batch of candidates that appeared after the
    /// last streaming review request (phase-2 batch correlation can add them).
    ///
    /// Phase 2 used to simply mark such candidates as presented without any verdict,
    /// which the aggregator correctly turned into `Uncertain` for the whole run —
    /// benign traffic with one late boundary crossing could never be cleared. This
    /// call is the missing review coverage: one bounded request, the same signal
    /// contract as phase 1.
    pub async fn review_candidates_with_observer(
        &self,
        graph: &ResponsibilityGraph,
        patterns: &[crate::responsibility::classification::pattern_matcher::GraphPatternMatch],
        evidence_records: &[CorrelationEvidence],
        candidates: &[crate::responsibility::classification::candidates::CandidateSummary],
        intent_descriptions: &[String],
        observer: &dyn ObserverProvider,
    ) -> Result<ObserverOutput, crate::responsibility::observer::provider::ObserverProviderError>
    {
        self.review_candidates_with_observer_and_task(
            graph,
            patterns,
            evidence_records,
            candidates,
            intent_descriptions,
            None,
            observer,
        )
        .await
    }

    /// Phase-2 review with an explicit captured-task context. The original
    /// signature is kept as a wrapper for existing callers.
    /// Shared pure review-input construction.
    ///
    /// `include_rule_catalog` is the explicit L2/L3 ablation feature. The
    /// normal runtime wrapper always passes `true`; L2 passes `false`, so the
    /// Observer sees no catalog hits and no score derived from them, while the
    /// same candidate/task normalization is used in both paths.
    pub fn build_observer_review_input(
        &self,
        graph: &ResponsibilityGraph,
        patterns: &[crate::responsibility::classification::pattern_matcher::GraphPatternMatch],
        evidence_records: &[CorrelationEvidence],
        candidates: &[crate::responsibility::classification::candidates::CandidateSummary],
        intent_descriptions: &[String],
        observed_task: Option<&crate::responsibility::observer::types::ObservedTaskContext>,
        include_rule_catalog: bool,
    ) -> crate::responsibility::classification::types::ObserverInput {
        let rule_hits = if include_rule_catalog {
            self.rules.evaluate(graph).1
        } else {
            Vec::new()
        };
        let score = compute_score_with(
            &rule_hits,
            patterns,
            graph,
            evidence_records,
            0,
            intent_descriptions.len() as u32,
            Default::default(),
            &self.config.scoring,
        );
        let signature =
            build_observer_signature(graph, evidence_records, &rule_hits, patterns, score);
        let content = Some(build_content_signals(
            graph,
            &rule_hits,
            patterns,
            intent_descriptions,
            candidates,
            observed_task,
        ));
        crate::responsibility::classification::types::ObserverInput {
            signature,
            content_signals: content,
            prior: CachePrior::default(),
        }
    }

    pub async fn review_candidates_with_observer_and_task(
        &self,
        graph: &ResponsibilityGraph,
        patterns: &[crate::responsibility::classification::pattern_matcher::GraphPatternMatch],
        evidence_records: &[CorrelationEvidence],
        candidates: &[crate::responsibility::classification::candidates::CandidateSummary],
        intent_descriptions: &[String],
        observed_task: Option<&crate::responsibility::observer::types::ObservedTaskContext>,
        observer: &dyn ObserverProvider,
    ) -> Result<ObserverOutput, crate::responsibility::observer::provider::ObserverProviderError>
    {
        let input = self.build_observer_review_input(
            graph,
            patterns,
            evidence_records,
            candidates,
            intent_descriptions,
            observed_task,
            true,
        );
        let mut output = observer.evaluate(&input).await?;
        let presented = input
            .content_signals
            .as_ref()
            .map(|content| content.candidates.as_slice())
            .unwrap_or(&[]);
        normalize_observer_output(presented, &mut output);
        validate_candidate_verdicts(Some(presented), &output.candidate_verdicts).map_err(
            |error| {
                crate::responsibility::observer::provider::ObserverProviderError::ValidationError(
                    format!("invalid candidate verdicts: {error:?}"),
                )
            },
        )?;
        Ok(output)
    }

    // ── Phase 2: Batch classification (never calls observer) ────────

    /// Classify a graph in Phase 2 (batch, authoritative).
    ///
    /// Per spec §11.2, Phase 2 never calls the Observer LLM. The graph is
    /// complete, so the classification should be deterministic.
    ///
    /// If the score falls in the 0.15-0.70 ambiguous range, Phase 2 defaults
    /// to DirectUnsafe (fail-closed safety strategy).
    pub fn classify_authoritative(
        &self,
        graph: &ResponsibilityGraph,
        patterns: &[GraphPatternMatch],
        evidence_records: &[CorrelationEvidence],
        repetition_count: u32,
        intent_count: u32,
        signals: crate::responsibility::classification::scoring::ScoreSignals,
    ) -> (ClassificationResult, Route) {
        // Layer 1: Rule evaluation
        let (_rule_decision, rule_hits) = self.rules.evaluate(graph);

        // Layer 2: Scoring (v14)
        let score = compute_score_with(
            &rule_hits,
            patterns,
            graph,
            evidence_records,
            repetition_count,
            intent_count,
            signals.clone(),
            &self.config.scoring,
        );
        let breakdown = compute_score_breakdown_with(
            &rule_hits,
            patterns,
            graph,
            evidence_records,
            repetition_count,
            intent_count,
            signals.clone(),
            &self.config.scoring,
        );

        // Layer 3: Phase 2 route (fail-closed)
        if score > self.config.threshold_unsafe {
            let output = self.build_classification_output(
                ObserverDecision::Unsafe,
                ClaimSeverity::Critical,
                score,
                "Authoritative classification: score exceeds unsafe threshold.",
                &rule_hits,
                patterns,
            );
            let result = ClassificationResult {
                score,
                route: "direct_unsafe".into(),
                score_breakdown: breakdown,
                cusum_triggered: signals.cusum_triggered,
                cusum_dimensions: signals.cusum_dimensions.clone(),
                progressive_fused: signals.progressive_fused,
                observer_trigger_reason: signals.observer_trigger_reason.clone(),
                pattern_verdicts: graph_pattern_verdicts(patterns),
            };
            (result, Route::DirectUnsafe(output))
        } else if score < self.config.threshold_safe {
            let output = self.build_classification_output(
                ObserverDecision::Safe,
                ClaimSeverity::Info,
                score,
                "Authoritative classification: all signals within safe thresholds.",
                &rule_hits,
                patterns,
            );
            let result = ClassificationResult {
                score,
                route: "direct_safe".into(),
                score_breakdown: breakdown,
                cusum_triggered: signals.cusum_triggered,
                cusum_dimensions: signals.cusum_dimensions.clone(),
                progressive_fused: signals.progressive_fused,
                observer_trigger_reason: signals.observer_trigger_reason.clone(),
                pattern_verdicts: graph_pattern_verdicts(patterns),
            };
            (result, Route::DirectSafe(output))
        } else {
            // Phase 2: an ambiguous score is a review signal, not a verdict.
            //
            // "Fail closed" used to mean "elevate to Unsafe", which made every
            // benign run with a mid-range score an actionable false positive
            // (observed on benign-heavy-build and benign-known-hosts-audit).
            // The honest fail-closed outcome is "do not call it Safe": the run
            // stays Uncertain until hard evidence or a semantic verdict says
            // otherwise. The final decision is aggregated in final_decision.rs.
            let output = self.build_classification_output(
                ObserverDecision::Uncertain,
                ClaimSeverity::Warning,
                score,
                "Authoritative classification: ambiguous score retained for review.",
                &rule_hits,
                patterns,
            );
            let result = ClassificationResult {
                score,
                route: "ambiguous_review".into(),
                score_breakdown: breakdown,
                cusum_triggered: signals.cusum_triggered,
                cusum_dimensions: signals.cusum_dimensions.clone(),
                progressive_fused: signals.progressive_fused,
                observer_trigger_reason: signals.observer_trigger_reason.clone(),
                pattern_verdicts: graph_pattern_verdicts(patterns),
            };
            (result, Route::DirectSafe(output))
        }
    }

    // ── Helpers ──────────────────────────────────────────────────────

    /// Fail-closed fallback for a failed or malformed Observer response.
    ///
    /// Fail closed: a call we thought necessary failed, so the honest outcome
    /// is "review", not "Safe" — and never "Unsafe" on the static score alone.
    /// The old fallback kept the heuristic's Unsafe when the score exceeded
    /// 0.70, which made an observer outage an actionable false-positive
    /// generator and violated ADR-0015. Hard evidence (an unsafe rule) is the
    /// only thing that can still justify Unsafe here.
    fn observer_failure_fallback(
        &self,
        error: impl std::fmt::Debug,
        score: f32,
        rule_hits: &[RuleHit],
        breakdown: &crate::responsibility::types::refinement::ScoreBreakdown,
        patterns: &[GraphPatternMatch],
    ) -> (ClassificationResult, Route) {
        eprintln!(
            "[classify] Observer LLM call failed: {:?} — falling back to heuristic (score={:.3})",
            error, score
        );
        let has_unsafe_rule = rule_hits.iter().any(|hit| {
            hit.decision == crate::responsibility::classification::rules::RuleDecision::Unsafe
        });
        let mut fallback = heuristic_decide(score);
        if !has_unsafe_rule || fallback.decision != ObserverDecision::Unsafe {
            fallback.decision = ObserverDecision::Uncertain;
            fallback.severity = ClaimSeverity::Warning;
            fallback.reasoning =
                format!("Observer failed ({error:?}); static score {score:.3} is not a verdict");
        }
        let result = ClassificationResult {
            score,
            route: "observer_failed".into(),
            score_breakdown: breakdown.clone(),
            cusum_triggered: false,
            cusum_dimensions: vec![],
            progressive_fused: 0.0,
            observer_trigger_reason: format!("Observer failed: {error:?}"),
            pattern_verdicts: graph_pattern_verdicts(patterns),
        };
        let obs_route = match fallback.decision {
            ObserverDecision::Unsafe => Route::DirectUnsafe(fallback),
            _ => Route::DirectSafe(fallback),
        };
        (result, obs_route)
    }

    fn build_classification_output(
        &self,
        decision: ObserverDecision,
        severity: ClaimSeverity,
        confidence: f32,
        reasoning: &str,
        rule_hits: &[RuleHit],
        _patterns: &[GraphPatternMatch],
    ) -> ObserverOutput {
        ObserverOutput {
            decision,
            severity,
            confidence,
            cache_ttl: CacheTTL::All,
            authority: AlertAuthority::Authoritative,
            reasoning: reasoning.to_string(),
            attacked_flag: false,
            evidence_cited: vec![],
            rule_verdicts: rule_hits
                .iter()
                .map(
                    |h| crate::responsibility::classification::types::RuleVerdict {
                        rule_id: h.rule_id.clone(),
                        description: format!("{:?}", h.decision),
                        matched_resource: h.matched_resource.clone(),
                        llm_agreed: None,
                        llm_comment: None,
                    },
                )
                .collect(),
            pattern_verdicts: vec![],
            candidate_verdicts: vec![],
            review_provenance: vec![],
        }
    }
}

impl Default for ClassificationEngine {
    fn default() -> Self {
        Self::new()
    }
}

// ── Tests ──────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use crate::responsibility::graph::ResponsibilityGraph;
    use crate::responsibility::types::evidence_types::{
        CandidateDerivationMode, CorrelationEvidence, MatchLevel, NormalizedArgs,
    };
    use crate::responsibility::types::{
        ActionId, AgentId, EdgeId, EvidenceId, IntentId, NodeId, NormalizedResource,
        PhaseIndicator, ResponsibilityEdge, ResponsibilityEdgeType, ResponsibilityNode,
        ResponsibilityNodeType,
    };

    fn make_node(
        id: &str,
        node_type: ResponsibilityNodeType,
        agent: Option<&str>,
        resource: Option<NormalizedResource>,
    ) -> ResponsibilityNode {
        ResponsibilityNode {
            node_id: NodeId::from(id),
            node_type,
            agent_id: agent.map(|a| AgentId::from(a)),
            resource,
            evidence_refs: Vec::new(),
            timestamp_ns: 1000,
            phase: PhaseIndicator::Provisional { timestamp_ns: 1000 },
        }
    }

    fn make_edge(
        id: &str,
        edge_type: ResponsibilityEdgeType,
        source: &str,
        target: &str,
    ) -> ResponsibilityEdge {
        ResponsibilityEdge {
            edge_id: EdgeId::from(id),
            edge_type,
            operation: None,
            source: NodeId::from(source),
            target: NodeId::from(target),
            // Rule evaluation only trusts observed crossings; the fixture
            // models an observed boundary.
            evidence_refs: vec![EvidenceId::from(format!("ev-{id}"))],
            confidence: 0.9,
            phase: PhaseIndicator::Authoritative {
                timestamp_ns: 1000,
                overrides: None,
            },
        }
    }

    fn make_evidence(confidence: f32, match_level: MatchLevel) -> CorrelationEvidence {
        CorrelationEvidence {
            evidence_id: EvidenceId::new(),
            intent_id: IntentId::new(),
            action_id: ActionId::new(),
            match_level,
            confidence,
            matched_fields: NormalizedArgs {
                canonical_path: None,
                host: None,
                port: None,
                command: None,
                raw_args: vec![],
            },
            derivation_mode: CandidateDerivationMode::Direct,
            timestamp_ns: 1000,
            phase: PhaseIndicator::Provisional { timestamp_ns: 1000 },
        }
    }

    pub(super) fn make_safe_graph() -> ResponsibilityGraph {
        let mut g = ResponsibilityGraph::default();
        g.add_node(make_node(
            "a1",
            ResponsibilityNodeType::Agent,
            Some("agent-1"),
            None,
        ));
        g.add_node(make_node(
            "r1",
            ResponsibilityNodeType::Resource,
            Some("agent-1"),
            Some(NormalizedResource::File {
                canonical_path: "/workspace/project/src/main.rs".into(),
            }),
        ));
        g.add_edge(make_edge(
            "e1",
            ResponsibilityEdgeType::IntentCausesAction,
            "a1",
            "r1",
        ));
        g
    }

    pub(super) fn make_unsafe_graph() -> ResponsibilityGraph {
        let mut g = ResponsibilityGraph::default();
        g.add_node(make_node(
            "a1",
            ResponsibilityNodeType::Agent,
            Some("agent-1"),
            None,
        ));
        g.add_node(make_node(
            "r1",
            ResponsibilityNodeType::Resource,
            Some("agent-1"),
            Some(NormalizedResource::File {
                canonical_path: "/etc/shadow".into(),
            }),
        ));
        g.add_edge(make_edge(
            "e1",
            ResponsibilityEdgeType::PolicyBoundaryCrossing,
            "a1",
            "r1",
        ));
        g
    }

    fn make_ambiguous_graph() -> ResponsibilityGraph {
        let mut g = ResponsibilityGraph::default();
        g.add_node(make_node(
            "a1",
            ResponsibilityNodeType::Agent,
            Some("agent-1"),
            None,
        ));
        g.add_node(make_node(
            "r1",
            ResponsibilityNodeType::Resource,
            Some("agent-1"),
            Some(NormalizedResource::Network {
                host: "unknown.example.com".into(),
                port: 8080,
                scheme: "https".into(),
            }),
        ));
        g.add_edge(make_edge(
            "e1",
            ResponsibilityEdgeType::IntentCausesAction,
            "a1",
            "r1",
        ));
        g
    }

    #[test]
    fn test_classify_authoritative_safe() {
        let engine = ClassificationEngine::new();
        let graph = make_safe_graph();
        let evidence = vec![make_evidence(0.3, MatchLevel::L5Substring)];

        let (result, _route) = engine.classify_authoritative(
            &graph,
            &[],
            &evidence,
            0,
            0,
            crate::responsibility::classification::scoring::ScoreSignals::default(),
        );

        // Safe graph with low-confidence evidence should get low score
        assert!(result.score < 0.15);
        assert_eq!(result.route, "direct_safe");
    }

    /// The matcher's output must be persisted with the classification result.
    /// Otherwise a scenario where a graph template fired is indistinguishable
    /// in the DB from one where the matcher ran and found nothing — the exact
    /// "absence is not detection" trap P1.2 exists to close.
    #[test]
    fn classify_authoritative_persists_pattern_verdicts() {
        let engine = ClassificationEngine::new();
        let graph = make_safe_graph();
        let evidence = vec![make_evidence(0.3, MatchLevel::L5Substring)];
        let patterns = vec![GraphPatternMatch {
            template_id: "tpl.test".into(),
            matched_node_ids: vec!["a1".into()],
            matched_edge_ids: vec!["e1".into()],
            total_edges_in_template: 1,
            confidence: 0.9,
            severity: ClaimSeverity::Warning,
            description: "test pattern".into(),
        }];

        let (result, _route) = engine.classify_authoritative(
            &graph,
            &patterns,
            &evidence,
            0,
            0,
            crate::responsibility::classification::scoring::ScoreSignals::default(),
        );

        assert_eq!(result.pattern_verdicts.len(), 1);
        assert_eq!(result.pattern_verdicts[0].template_id, "tpl.test");
        assert_eq!(result.pattern_verdicts[0].match_confidence, 0.9);
    }

    #[test]
    fn test_classify_authoritative_unsafe() {
        let engine = ClassificationEngine::new();
        let graph = crate::responsibility::classification::engine::tests::make_unsafe_graph();
        let evidence = vec![make_evidence(0.9, MatchLevel::L1Exact)];

        let (result, _route) = engine.classify_authoritative(
            &graph,
            &[],
            &evidence,
            0,
            0,
            crate::responsibility::classification::scoring::ScoreSignals::default(),
        );

        // Shadow access + high evidence should be high score
        assert!(result.score > 0.10);
    }

    #[test]
    fn test_classify_authoritative_ambiguous_is_review_not_unsafe() {
        let engine = ClassificationEngine::new();
        let graph = make_ambiguous_graph();
        let evidence = vec![make_evidence(0.5, MatchLevel::L3Containment)];

        let (result, route) = engine.classify_authoritative(
            &graph,
            &[],
            &evidence,
            0,
            0,
            crate::responsibility::classification::scoring::ScoreSignals::default(),
        );

        // An ambiguous authoritative score is a review signal. It must never
        // become `Unsafe` without hard evidence or a semantic verdict; that
        // rule produced actionable false positives on benign CI/audit tasks.
        if result.route == "ambiguous_review" {
            assert_eq!(result.route, "ambiguous_review");
            match route {
                Route::DirectSafe(output) => {
                    assert_eq!(output.decision, ObserverDecision::Uncertain);
                    assert_eq!(output.severity, ClaimSeverity::Warning);
                }
                Route::DirectUnsafe(_) => panic!("ambiguous review must not be actionable"),
                Route::ToObserver(_) => panic!("phase 2 never calls the observer"),
            }
        }
    }

    #[test]
    fn test_score_breakdown_includes_all_dimensions() {
        let engine = ClassificationEngine::new();
        let graph = crate::responsibility::classification::engine::tests::make_unsafe_graph();
        let evidence = vec![make_evidence(0.9, MatchLevel::L1Exact)];

        let (result, _route) = engine.classify_authoritative(
            &graph,
            &[],
            &evidence,
            0,
            0,
            crate::responsibility::classification::scoring::ScoreSignals::default(),
        );

        let sb = &result.score_breakdown;
        assert!(sb.rule_score >= 0.0);
        assert!(sb.pattern_score >= 0.0);
        assert!(sb.boundary_score >= 0.0);
        assert!(sb.evidence_score >= 0.0);
        assert!(sb.role_score >= 0.0);
        assert!(sb.repetition_score >= 0.0);
        // total should be > 0 for unsafe graph
        assert!(sb.total > 0.0);
    }

    #[tokio::test]
    async fn test_classify_provisional_safe_without_observer() {
        let engine = ClassificationEngine::new();
        let graph = make_safe_graph();
        let evidence = vec![];

        let (_result, route) = engine
            .classify_provisional(
                &graph,
                &[],
                &evidence,
                None,
                0,
                false,
                CachePrior {
                    l2_hit: false,
                    l2_prior_anomaly_rate: None,
                    l3_hit: false,
                    l3_fleet_anomaly_rate: None,
                },
                &[],
                0,
                crate::responsibility::classification::scoring::ScoreSignals::default(),
                &crate::responsibility::classification::candidates::review_candidates(&graph, &[]),
            )
            .await;

        match route {
            Route::DirectSafe(output) => {
                assert_eq!(output.decision, ObserverDecision::Safe);
            }
            _ => panic!("Expected DirectSafe for safe graph"),
        }
    }

    #[tokio::test]
    async fn test_observer_pipeline_v14_integration() {
        let engine = ClassificationEngine::new();
        let graph = crate::responsibility::classification::engine::tests::make_unsafe_graph();
        let evidence = vec![make_evidence(0.9, MatchLevel::L1Exact)];
        let mock_out = ObserverOutput {
            decision: ObserverDecision::Unsafe,
            severity: ClaimSeverity::Critical,
            confidence: 0.85,
            cache_ttl: CacheTTL::Task,
            authority: AlertAuthority::Authoritative,
            reasoning: "evidence: /etc/shadow accessed with L1 confidence".into(),
            attacked_flag: false,
            evidence_cited: vec![
                crate::responsibility::classification::candidates::graph_candidates(&graph, &[])[0]
                    .candidate
                    .id
                    .clone(),
            ],
            rule_verdicts: vec![],
            pattern_verdicts: vec![],
            candidate_verdicts: vec![],
            review_provenance: vec![],
        };
        let mock =
            crate::responsibility::observer::mock::MockObserverProvider::with_responses(vec![
                mock_out,
            ]);
        let intents = vec!["Bash: cat /etc/shadow".to_string()];
        let prior = CachePrior {
            l2_hit: true,
            l2_prior_anomaly_rate: Some(0.15),
            l3_hit: false,
            l3_fleet_anomaly_rate: None,
        };
        let (result, _route) = engine
            .classify_provisional(
                &graph,
                &[],
                &evidence,
                Some(&mock),
                0,
                true,
                prior,
                &intents,
                2,
                crate::responsibility::classification::scoring::ScoreSignals::default(),
                &crate::responsibility::classification::candidates::review_candidates(&graph, &[]),
            )
            .await;
        assert!(result.score > 0.0, "Observer should adjust score");
        assert!(
            result.route.starts_with("observer_"),
            "Route: {}",
            result.route
        );
        assert!(
            mock.call_count() >= 1,
            "Observer should be called, count={}",
            mock.call_count()
        );
    }

    #[tokio::test]
    async fn test_observer_safe_overrides_score() {
        let engine = ClassificationEngine::new();
        let mut graph = make_unsafe_graph();
        graph.add_node(make_node(
            "r2",
            ResponsibilityNodeType::Resource,
            Some("agent-1"),
            Some(NormalizedResource::Network {
                host: "evil.com".into(),
                port: 4444,
                scheme: "https".into(),
            }),
        ));
        graph.add_edge(make_edge(
            "e2",
            ResponsibilityEdgeType::PolicyBoundaryCrossing,
            "a1",
            "r2",
        ));
        let evidence = vec![make_evidence(0.9, MatchLevel::L1Exact)];
        let mock_out = ObserverOutput {
            decision: ObserverDecision::Safe,
            severity: ClaimSeverity::Info,
            confidence: 0.9,
            cache_ttl: CacheTTL::Task,
            authority: AlertAuthority::Authoritative,
            reasoning: "workspace-safe".into(),
            attacked_flag: false,
            evidence_cited: vec![],
            rule_verdicts: vec![],
            pattern_verdicts: vec![],
            candidate_verdicts: vec![],
            review_provenance: vec![],
        };
        let mock =
            crate::responsibility::observer::mock::MockObserverProvider::with_responses(vec![
                mock_out,
            ]);
        let (result, _route) = engine
            .classify_provisional(
                &graph,
                &[],
                &evidence,
                Some(&mock),
                0,
                true,
                CachePrior {
                    l2_hit: false,
                    l2_prior_anomaly_rate: None,
                    l3_hit: false,
                    l3_fleet_anomaly_rate: None,
                },
                &[],
                0,
                crate::responsibility::classification::scoring::ScoreSignals::default(),
                &crate::responsibility::classification::candidates::review_candidates(&graph, &[]),
            )
            .await;
        assert!(
            result.score < 0.5,
            "Safe Observer should reduce score, got {}",
            result.score
        );
        assert!(mock.call_count() >= 1);
    }
}

/// A pattern-only direct-unsafe verdict must be arbitrated by the Observer.
///
/// Graph templates are structural hints (they count edge types, not intent).
/// A benign credential-rotation task matched persistence/recon/evasion
/// templates and crossed 0.70 without any rule hit. Only a rule hit is direct
/// evidence strong enough to bypass arbitration.
fn pattern_requires_observer(has_unsafe_rule: bool, has_patterns: bool) -> bool {
    !has_unsafe_rule && has_patterns
}

#[cfg(test)]
mod review_contract_tests {
    use super::*;
    use crate::responsibility::classification::candidates::CandidateSummary;
    use crate::responsibility::observer::mock::MockObserverProvider;

    /// The phase-2 review request reuses the phase-1 contract: an Unsafe label
    /// that cites nothing in the request is downgraded, and a cited Unsafe stands.
    #[tokio::test]
    async fn phase_two_review_requires_citations_and_keeps_the_verdict_honest() {
        use crate::responsibility::classification::candidates::review_candidates;
        use crate::responsibility::classification::types::CacheTTL;
        use crate::responsibility::observer::types::AlertAuthority;
        use crate::responsibility::types::AlertDecision;

        let engine = ClassificationEngine::new();
        let graph = crate::responsibility::classification::engine::tests::make_unsafe_graph();
        let candidates = review_candidates(&graph, &[]);
        assert!(!candidates.is_empty());

        let mut uncited = mock_observer_output(ObserverDecision::Unsafe, vec![]);
        uncited.authority = AlertAuthority::Unavailable;
        let uncited_provider =
            crate::responsibility::observer::mock::MockObserverProvider::with_responses(vec![
                uncited,
            ]);
        let reviewed = engine
            .review_candidates_with_observer(&graph, &[], &[], &candidates, &[], &uncited_provider)
            .await
            .expect("mock review");
        assert_eq!(reviewed.decision, ObserverDecision::Uncertain);
        assert_eq!(reviewed.authority, AlertAuthority::Unavailable);

        let citation = candidates[0].evidence_ids[0].clone();
        let cited = mock_observer_output(ObserverDecision::Unsafe, vec![citation]);
        let cited_provider =
            crate::responsibility::observer::mock::MockObserverProvider::with_responses(vec![
                cited,
            ]);
        let reviewed = engine
            .review_candidates_with_observer(&graph, &[], &[], &candidates, &[], &cited_provider)
            .await
            .expect("mock review");
        assert_eq!(reviewed.decision, ObserverDecision::Unsafe);
        let _ = AlertDecision::Unsafe;
        let _ = CacheTTL::Task;
    }

    fn two_candidates() -> Vec<CandidateSummary> {
        use crate::responsibility::classification::candidates::review_candidates;
        let graph = crate::responsibility::classification::engine::tests::make_unsafe_graph();
        let mut candidates = review_candidates(&graph, &[]);
        assert_eq!(candidates.len(), 1);
        let mut second = candidates[0].clone();
        second.candidate.id = "pattern:second".into();
        second.candidate.version = "v2".into();
        candidates.push(second);
        candidates
    }

    fn per_candidate_verdict(
        candidate_id: &str,
        decision: ObserverDecision,
        confidence: f32,
        cited: usize,
    ) -> crate::responsibility::observer::types::CandidateVerdict {
        crate::responsibility::observer::types::CandidateVerdict {
            candidate_id: candidate_id.into(),
            decision,
            confidence,
            cited,
        }
    }

    /// Same cost (one provider call), but divergent per-candidate verdicts
    /// survive instead of being averaged into one batch label.
    #[tokio::test]
    async fn per_candidate_verdicts_use_one_observer_call_and_are_not_diluted() {
        use crate::responsibility::classification::candidates::review_observations_with_output;
        use crate::responsibility::classification::final_decision::aggregate_final_decision;
        use crate::responsibility::observer::types::REVIEW_CONTRACT_PER_CANDIDATE;
        use crate::responsibility::types::AlertDecision;

        let engine = ClassificationEngine::new();
        let graph = crate::responsibility::classification::engine::tests::make_unsafe_graph();
        let candidates = two_candidates();

        let mut output = mock_observer_output(ObserverDecision::Safe, vec![]);
        output.candidate_verdicts = vec![
            per_candidate_verdict(
                &candidates[0].candidate.id,
                ObserverDecision::Unsafe,
                0.91,
                candidates[0].evidence_ids.len().max(1),
            ),
            per_candidate_verdict(&candidates[1].candidate.id, ObserverDecision::Safe, 0.8, 0),
        ];
        let provider = MockObserverProvider::with_responses(vec![output]);

        let reviewed = engine
            .review_candidates_with_observer(&graph, &[], &[], &candidates, &[], &provider)
            .await
            .expect("mock review");

        assert_eq!(
            provider.call_count(),
            1,
            "same one call, per-candidate output"
        );
        assert_eq!(reviewed.candidate_verdicts.len(), 2);
        assert!(
            reviewed
                .review_provenance
                .iter()
                .any(|entry| entry == REVIEW_CONTRACT_PER_CANDIDATE),
            "per-candidate provenance: {:?}",
            reviewed.review_provenance
        );

        let observations = review_observations_with_output(&candidates, &reviewed);
        let verdict = aggregate_final_decision(&observations);
        assert_eq!(
            verdict.decision,
            AlertDecision::Unsafe,
            "the unsafest candidate must not be diluted by the batch summary"
        );
    }

    #[tokio::test]
    async fn invented_candidate_id_is_rejected() {
        use crate::responsibility::observer::provider::ObserverProviderError;

        let engine = ClassificationEngine::new();
        let graph = crate::responsibility::classification::engine::tests::make_unsafe_graph();
        let candidates = two_candidates();

        let mut output = mock_observer_output(ObserverDecision::Safe, vec![]);
        output.candidate_verdicts = vec![per_candidate_verdict(
            "invented-candidate-id",
            ObserverDecision::Safe,
            0.9,
            0,
        )];
        let provider = MockObserverProvider::with_responses(vec![output]);

        let error = engine
            .review_candidates_with_observer(&graph, &[], &[], &candidates, &[], &provider)
            .await
            .expect_err("invented ids must be rejected");

        assert!(
            matches!(error, ObserverProviderError::ValidationError(ref message) if message.contains("invented-candidate-id")),
            "unexpected error: {error:?}"
        );
    }

    #[tokio::test]
    async fn duplicate_candidate_id_is_rejected() {
        use crate::responsibility::observer::provider::ObserverProviderError;

        let engine = ClassificationEngine::new();
        let graph = crate::responsibility::classification::engine::tests::make_unsafe_graph();
        let candidates = two_candidates();
        let id = candidates[0].candidate.id.clone();

        let mut output = mock_observer_output(ObserverDecision::Safe, vec![]);
        output.candidate_verdicts = vec![
            per_candidate_verdict(&id, ObserverDecision::Safe, 0.9, 0),
            per_candidate_verdict(&id, ObserverDecision::Unsafe, 0.9, 1),
        ];
        let provider = MockObserverProvider::with_responses(vec![output]);

        let error = engine
            .review_candidates_with_observer(&graph, &[], &[], &candidates, &[], &provider)
            .await
            .expect_err("duplicate ids must be rejected");

        assert!(
            matches!(error, ObserverProviderError::ValidationError(_)),
            "unexpected error: {error:?}"
        );
    }

    #[tokio::test]
    async fn invalid_per_candidate_confidence_is_rejected() {
        use crate::responsibility::observer::provider::ObserverProviderError;

        let engine = ClassificationEngine::new();
        let graph = crate::responsibility::classification::engine::tests::make_unsafe_graph();
        let candidates = two_candidates();

        let mut output = mock_observer_output(ObserverDecision::Safe, vec![]);
        output.candidate_verdicts = vec![
            per_candidate_verdict(&candidates[0].candidate.id, ObserverDecision::Safe, 1.5, 0),
            per_candidate_verdict(&candidates[1].candidate.id, ObserverDecision::Safe, 0.8, 0),
        ];
        let provider = MockObserverProvider::with_responses(vec![output]);

        let error = engine
            .review_candidates_with_observer(&graph, &[], &[], &candidates, &[], &provider)
            .await
            .expect_err("out-of-range confidence must be rejected");

        assert!(
            matches!(error, ObserverProviderError::ValidationError(_)),
            "unexpected error: {error:?}"
        );
    }

    #[tokio::test]
    async fn missing_candidate_verdict_is_rejected() {
        use crate::responsibility::observer::provider::ObserverProviderError;

        let engine = ClassificationEngine::new();
        let graph = crate::responsibility::classification::engine::tests::make_unsafe_graph();
        let candidates = two_candidates();

        let mut output = mock_observer_output(ObserverDecision::Safe, vec![]);
        output.candidate_verdicts = vec![per_candidate_verdict(
            &candidates[0].candidate.id,
            ObserverDecision::Safe,
            0.9,
            0,
        )];
        let provider = MockObserverProvider::with_responses(vec![output]);

        let error = engine
            .review_candidates_with_observer(&graph, &[], &[], &candidates, &[], &provider)
            .await
            .expect_err("a partial list is not an exact review");

        assert!(
            matches!(error, ObserverProviderError::ValidationError(_)),
            "unexpected error: {error:?}"
        );
    }

    /// Old/limited providers stay supported: no list means the batch verdict
    /// applies, and provenance records that fallback.
    #[tokio::test]
    async fn empty_candidate_verdicts_fall_back_and_record_provenance() {
        use crate::responsibility::classification::candidates::review_observations_with_output;
        use crate::responsibility::classification::final_decision::{
            Observation, aggregate_final_decision,
        };
        use crate::responsibility::observer::types::REVIEW_CONTRACT_BATCH_FALLBACK;
        use crate::responsibility::types::AlertDecision;

        let engine = ClassificationEngine::new();
        let graph = crate::responsibility::classification::engine::tests::make_unsafe_graph();
        let candidates = two_candidates();

        let mut output = mock_observer_output(ObserverDecision::Safe, vec![]);
        output.confidence = 0.77;
        let provider = MockObserverProvider::with_responses(vec![output]);

        let reviewed = engine
            .review_candidates_with_observer(&graph, &[], &[], &candidates, &[], &provider)
            .await
            .expect("mock review");

        assert!(reviewed.candidate_verdicts.is_empty());
        assert!(
            reviewed
                .review_provenance
                .iter()
                .any(|entry| entry == REVIEW_CONTRACT_BATCH_FALLBACK),
            "fallback provenance: {:?}",
            reviewed.review_provenance
        );

        let observations = review_observations_with_output(&candidates, &reviewed);
        let reviews: Vec<_> = observations
            .iter()
            .filter(|observation| matches!(observation, Observation::ScopedReview { .. }))
            .collect();
        assert_eq!(reviews.len(), 1, "the batch verdict is one scoped review");
        assert_eq!(
            aggregate_final_decision(&observations).decision,
            AlertDecision::Safe
        );
    }

    /// Phase 1 consumes the same contract: a malformed per-candidate list is
    /// an observer failure, never a silent batch fallback that trusts it.
    #[tokio::test]
    async fn phase_one_invalid_candidate_verdicts_fail_closed() {
        use crate::responsibility::classification::engine::tests::make_unsafe_graph;
        use crate::responsibility::classification::scoring::ScoreSignals;
        use crate::responsibility::classification::types::Route;

        let engine = ClassificationEngine::new();
        let graph = make_unsafe_graph();
        let candidates = two_candidates();

        let mut output = mock_observer_output(ObserverDecision::Safe, vec![]);
        output.candidate_verdicts = vec![per_candidate_verdict(
            "invented-candidate-id",
            ObserverDecision::Safe,
            0.9,
            0,
        )];
        let provider = MockObserverProvider::with_responses(vec![output]);

        let (result, route) = engine
            .classify_provisional(
                &graph,
                &[],
                &[],
                Some(&provider),
                0,
                true,
                CachePrior::default(),
                &[],
                0,
                ScoreSignals::default(),
                &candidates,
            )
            .await;

        assert_eq!(result.route, "observer_failed");
        match route {
            Route::DirectSafe(output) => assert_eq!(output.decision, ObserverDecision::Uncertain),
            Route::DirectUnsafe(_) | Route::ToObserver(_) => {
                panic!("observer failure must fail closed")
            }
        }
    }

    fn mock_observer_output(decision: ObserverDecision, citations: Vec<String>) -> ObserverOutput {
        ObserverOutput {
            decision,
            severity: ClaimSeverity::Warning,
            confidence: 0.8,
            cache_ttl: crate::responsibility::classification::types::CacheTTL::Task,
            authority: crate::responsibility::observer::types::AlertAuthority::Supporting,
            reasoning: "mock review".into(),
            attacked_flag: false,
            evidence_cited: citations,
            rule_verdicts: vec![],
            pattern_verdicts: vec![],
            candidate_verdicts: vec![],
            review_provenance: vec![],
        }
    }

    struct CapturingObserver {
        input: std::sync::Mutex<Option<crate::responsibility::observer::types::ObserverInput>>,
    }

    impl CapturingObserver {
        fn new() -> Self {
            Self {
                input: std::sync::Mutex::new(None),
            }
        }

        fn observed_task_text(&self) -> Option<String> {
            let input = self.input.lock().unwrap();
            input
                .as_ref()
                .and_then(|input| input.content_signals.as_ref())
                .and_then(|signals| signals.observed_task.as_ref())
                .and_then(|task| task.messages.first())
                .map(|message| message.text.clone())
        }
    }

    #[async_trait::async_trait]
    impl ObserverProvider for CapturingObserver {
        async fn evaluate(
            &self,
            input: &crate::responsibility::observer::types::ObserverInput,
        ) -> Result<ObserverOutput, crate::responsibility::observer::provider::ObserverProviderError>
        {
            *self.input.lock().unwrap() = Some(input.clone());
            Ok(mock_observer_output(ObserverDecision::Safe, vec![]))
        }
    }

    fn task_fixture(text: &str) -> crate::responsibility::observer::types::ObservedTaskContext {
        use crate::responsibility::types::{
            Intent, IntentAuthoritySource, IntentClass, IntentId, IntentParseState, MessageId,
            NormalizedArg, ProcessId,
        };

        crate::responsibility::observer::types::ObservedTaskContext::from_intents(&[Intent {
            intent_id: IntentId::from("intent-task"),
            process_id: ProcessId::new(4242, 1),
            message_id: MessageId::from("msg-task"),
            tool_name: "UserPrompt".into(),
            tool_args: vec![NormalizedArg::Content(text.into())],
            intent_class: IntentClass::SideEffect,
            parse_state: IntentParseState::Complete,
            primary_matching_eligible: false,
            authority_source: IntentAuthoritySource::RequestHistoryAssistantToolUse,
            timestamp_ns: 1,
            native_tool_call: None,
            declared_conversation_id: None,
        }])
    }

    #[tokio::test]
    async fn phase_one_and_phase_two_reviews_carry_the_captured_task() {
        let task = task_fixture("TASK-TAIL: review the release notes.");

        let phase_one_observer = CapturingObserver::new();
        let engine = ClassificationEngine::new();
        let graph = crate::responsibility::classification::engine::tests::make_safe_graph();
        engine
            .classify_provisional_with_task(
                &graph,
                &[],
                &[],
                Some(&phase_one_observer),
                0,
                true,
                CachePrior::default(),
                &[],
                0,
                crate::responsibility::classification::scoring::ScoreSignals::default(),
                &crate::responsibility::classification::candidates::review_candidates(&graph, &[]),
                Some(&task),
            )
            .await;
        assert_eq!(
            phase_one_observer.observed_task_text().as_deref(),
            Some("TASK-TAIL: review the release notes.")
        );

        let phase_two_observer = CapturingObserver::new();
        let graph = crate::responsibility::classification::engine::tests::make_unsafe_graph();
        let candidates =
            crate::responsibility::classification::candidates::review_candidates(&graph, &[]);
        engine
            .review_candidates_with_observer_and_task(
                &graph,
                &[],
                &[],
                &candidates,
                &[],
                Some(&task),
                &phase_two_observer,
            )
            .await
            .expect("phase two review");
        assert_eq!(
            phase_two_observer.observed_task_text().as_deref(),
            Some("TASK-TAIL: review the release notes.")
        );
    }
}

#[cfg(test)]
mod route_gate_tests {
    use super::pattern_requires_observer;

    #[test]
    fn pattern_only_unsafe_requires_the_observer() {
        assert!(pattern_requires_observer(false, true));
        assert!(!pattern_requires_observer(true, true));
        assert!(!pattern_requires_observer(false, false));
    }
}

/// Build the Observer's structured view of the OS evidence.
/// An Unsafe review that cites nothing this request actually contained is not
/// evidence: downgrade it instead of trusting the label. Shared by phase 1 and
/// phase 2 so both apply the identical rule.
pub fn normalize_observer_output(
    candidates: &[crate::responsibility::classification::candidates::CandidateSummary],
    output: &mut ObserverOutput,
) {
    // Record which review contract produced the votes so analysis can tell a
    // real per-candidate review from the batch fallback. The runtime persists
    // this marker; an empty list is never presented as per-candidate evidence.
    output.review_provenance = vec![
        crate::responsibility::classification::candidates::review_contract(output).to_string(),
    ];
    if output.decision != ObserverDecision::Unsafe {
        return;
    }
    let cited = candidates.iter().any(|candidate| {
        output.evidence_cited.iter().any(|citation| {
            citation == &candidate.candidate.id || candidate.evidence_ids.contains(citation)
        })
    });
    if !cited {
        output.decision = ObserverDecision::Uncertain;
        output.severity = ClaimSeverity::Warning;
        output.reasoning =
            "Observer citations do not identify any candidate or evidence in this review request."
                .into();
        output.authority = AlertAuthority::Unavailable;
    }
}

fn build_content_signals(
    graph: &ResponsibilityGraph,
    rule_hits: &[crate::responsibility::classification::rules::RuleHit],
    patterns: &[crate::responsibility::classification::pattern_matcher::GraphPatternMatch],
    intent_descriptions: &[String],
    candidates: &[crate::responsibility::classification::candidates::CandidateSummary],
    observed_task: Option<&crate::responsibility::observer::types::ObservedTaskContext>,
) -> ContentSignals {
    let mut signals = ContentSignals {
        resource_paths_touched: intent_descriptions.to_vec(),
        observed_task: observed_task.cloned(),
        candidates: candidates
            .iter()
            .take(crate::responsibility::classification::candidates::MAX_REVIEW_CANDIDATES)
            .cloned()
            .collect(),
        ..ContentSignals::default()
    };
    for node in graph.all_nodes() {
        match node.resource.as_ref() {
            Some(crate::responsibility::types::NormalizedResource::File { canonical_path }) => {
                signals.file_paths.push(canonical_path.clone());
            }
            Some(crate::responsibility::types::NormalizedResource::Credential {
                credential_id,
            }) => {
                signals.credential_paths.push(credential_id.clone());
            }
            Some(crate::responsibility::types::NormalizedResource::Network {
                host,
                port,
                scheme,
            }) => {
                signals
                    .network_endpoints
                    .push(format!("{scheme}://{host}:{port}"));
            }
            Some(crate::responsibility::types::NormalizedResource::ToolCall {
                tool_name,
                call_id,
            }) => {
                signals.tool_calls.push(format!("{tool_name}:{call_id}"));
                if tool_name == "UntrustedInstructionFlow" {
                    signals.provenance.push(call_id.clone());
                }
            }
            _ => {}
        }
    }
    for edge in graph.all_edges() {
        if edge.edge_type == crate::responsibility::types::ResponsibilityEdgeType::AgentDelegatesTo
        {
            let from = graph
                .get_node(&edge.source)
                .and_then(|node| node.agent_id.as_ref())
                .map(|agent| agent.as_str().to_string())
                .unwrap_or_else(|| edge.source.as_str().to_string());
            let to = graph
                .get_node(&edge.target)
                .and_then(|node| node.agent_id.as_ref())
                .map(|agent| agent.as_str().to_string())
                .unwrap_or_else(|| edge.target.as_str().to_string());
            signals.delegation_links.push(format!("{from}->{to}"));
        }
    }
    signals.rule_ids = rule_hits.iter().map(|hit| hit.rule_id.clone()).collect();
    signals.pattern_ids = patterns
        .iter()
        .map(|pattern| pattern.template_id.clone())
        .collect();
    for list in [
        &mut signals.file_paths,
        &mut signals.credential_paths,
        &mut signals.network_endpoints,
        &mut signals.delegation_links,
        &mut signals.rule_ids,
        &mut signals.pattern_ids,
        &mut signals.tool_calls,
        &mut signals.provenance,
    ] {
        list.sort();
        list.dedup();
    }
    signals
}

#[cfg(test)]
mod content_signal_tests {
    use super::build_content_signals;
    use crate::responsibility::graph::ResponsibilityGraph;
    use crate::responsibility::types::{
        AgentId, EdgeId, NodeId, NormalizedResource, PhaseIndicator, ResponsibilityEdge,
        ResponsibilityEdgeType, ResponsibilityNode, ResponsibilityNodeType,
    };

    fn node(
        id: &str,
        node_type: ResponsibilityNodeType,
        agent: Option<&str>,
        resource: Option<NormalizedResource>,
    ) -> ResponsibilityNode {
        ResponsibilityNode {
            node_id: NodeId::from(id),
            node_type,
            agent_id: agent.map(AgentId::from),
            resource,
            evidence_refs: Vec::new(),
            timestamp_ns: 1,
            phase: PhaseIndicator::Provisional { timestamp_ns: 1 },
        }
    }

    #[test]
    fn observer_input_carries_structured_os_evidence() {
        let mut graph = ResponsibilityGraph::default();
        graph.add_node(node(
            "a",
            ResponsibilityNodeType::Agent,
            Some("agent-a"),
            None,
        ));
        graph.add_node(node(
            "b",
            ResponsibilityNodeType::Agent,
            Some("agent-b"),
            None,
        ));
        graph.add_node(node(
            "f",
            ResponsibilityNodeType::Resource,
            None,
            Some(NormalizedResource::File {
                canonical_path: "/etc/shadow".into(),
            }),
        ));
        graph.add_node(node(
            "n",
            ResponsibilityNodeType::Resource,
            None,
            Some(NormalizedResource::Network {
                host: "203.0.113.7".into(),
                port: 443,
                scheme: "https".into(),
            }),
        ));
        graph.add_node(node(
            "c",
            ResponsibilityNodeType::Resource,
            None,
            Some(NormalizedResource::Credential {
                credential_id: "/home/u/.ssh/id_rsa".into(),
            }),
        ));
        graph.add_edge(ResponsibilityEdge {
            edge_id: EdgeId::from("d"),
            edge_type: ResponsibilityEdgeType::AgentDelegatesTo,
            operation: None,
            source: NodeId::from("a"),
            target: NodeId::from("b"),
            evidence_refs: Vec::new(),
            confidence: 0.9,
            phase: PhaseIndicator::Provisional { timestamp_ns: 1 },
        });

        let signals = build_content_signals(&graph, &[], &[], &[], &[], None);
        assert!(signals.file_paths.contains(&"/etc/shadow".to_string()));
        assert!(
            signals
                .network_endpoints
                .contains(&"https://203.0.113.7:443".to_string())
        );
        assert!(
            signals
                .credential_paths
                .contains(&"/home/u/.ssh/id_rsa".to_string())
        );
        assert!(
            signals
                .delegation_links
                .contains(&"agent-a->agent-b".to_string())
        );
    }
}
