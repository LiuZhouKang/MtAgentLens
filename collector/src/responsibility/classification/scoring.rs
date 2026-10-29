// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 MtAgentLens Project

use crate::responsibility::claims::ClaimSeverity;
use crate::responsibility::classification::pattern_matcher::GraphPatternMatch;
use crate::responsibility::classification::rules::{RuleDecision, RuleHit};
use crate::responsibility::classification::types::{
    AlertAuthority, CacheTTL, ClassificationConfig, ClassificationResult, CorrelationSignal,
    EvidenceSignature, GraphSignature, ObserverDecision, ObserverOutput, ObserverSignature,
    PatternSignal, PatternVerdict, ResourceSignal, RoleSignal, RuleHitSignal,
};
use crate::responsibility::graph::ResponsibilityGraph;
use crate::responsibility::types::evidence_types::{CorrelationEvidence, MatchLevel};
use crate::responsibility::types::{
    NormalizedResource, ResponsibilityEdgeType, ResponsibilityNodeType, ScoreBreakdown,
};

// ═══════════════════════════════════════════════════════════════════════════
//  Standalone scoring functions per spec §11.1
// ═══════════════════════════════════════════════════════════════════════════

/// Optional OS signals supplied by the runtime.
///
/// D6 used to be a constant shaped by an unrelated proxy (confirmed-PBC
/// count). The runtime now supplies the agent's behavioral-baseline deviation;
/// `None` means "not available", not "zero".
#[derive(Debug, Clone, Default, serde::Serialize, serde::Deserialize)]
pub struct ScoreSignals {
    pub baseline_deviation: Option<f32>,
    /// Diagnostics carried through to the persisted classification record.
    /// They are not inputs to the score; the authoritative classification used
    /// to write constants here, so CUSUM/trigger ablation columns read zero for
    /// every phase-2 alert.
    pub cusum_triggered: bool,
    pub cusum_dimensions: Vec<String>,
    pub progressive_fused: f64,
    pub observer_trigger_reason: String,
}

/// Tunable constants of the score pipeline.
///
/// The defaults are the measured operating point; everything here can be
/// overridden so a sensitivity analysis can replay recorded checkpoints with
/// perturbed weights and the evidence-gate centre (review comment 6). Changing a
/// value is an analysis-protocol parameter, not a policy change: the final
/// verdict still aggregates typed evidence.
#[derive(Debug, Clone, Copy, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct ScoringConfig {
    /// D1..D6 fusion weights (boundary, pattern, role, repetition, evidence,
    /// behavioral). The fusion normalises by the weight sum, so only ratios
    /// matter.
    pub weights: [f64; 6],
    /// Evidence gate: PBC count at which the gate reaches 0.5.
    pub gate_center: f64,
    /// Evidence gate: logistic steepness.
    pub gate_slope: f64,
    /// Platt calibration `a` (slope).
    pub platt_a: f64,
    /// Platt calibration `b` (offset).
    pub platt_b: f64,
    /// D4 saturation denominator (repetition count that reaches 1.0).
    pub repetition_denominator: f64,
    /// D5 saturation denominator (combined confirmed signals that reach 1.0).
    pub evidence_denominator: f64,
}

impl Default for ScoringConfig {
    fn default() -> Self {
        Self {
            weights: [0.28, 0.25, 0.20, 0.10, 0.10, 0.07],
            gate_center: 10.0,
            gate_slope: 2.0,
            platt_a: 7.0,
            platt_b: -3.0,
            repetition_denominator: 50.0,
            evidence_denominator: 50.0,
        }
    }
}

impl ScoringConfig {
    /// Reject configurations that would silently change the score semantics.
    pub fn validate(&self) -> Result<(), String> {
        if self
            .weights
            .iter()
            .any(|weight| !weight.is_finite() || *weight < 0.0)
        {
            return Err("weights must be finite and non-negative".into());
        }
        if self.weights.iter().sum::<f64>() <= 0.0 {
            return Err("weights must not all be zero".into());
        }
        if !(self.gate_slope.is_finite() && self.gate_slope > 0.0) {
            return Err("gate_slope must be positive".into());
        }
        if !self.gate_center.is_finite() {
            return Err("gate_center must be finite".into());
        }
        for (name, value) in [
            ("repetition_denominator", self.repetition_denominator),
            ("evidence_denominator", self.evidence_denominator),
        ] {
            if !(value.is_finite() && value > 0.0) {
                return Err(format!("{name} must be positive"));
            }
        }
        Ok(())
    }
}

/// Compute the classification score using v9 six-dimension fusion.
///
/// The old 7-dim weighted sum is replaced by OWA(0.80) + GeoMean_active(0.20)
/// fusion followed by Platt sigmoid calibration.  This gives balanced
/// scoring that requires corroboration (GeoMean) while still detecting
/// single-dimension strong signals (OWA).
///
/// Each sub-score is normalized to [0, 1].
/// Scoring with an explicit parameter set (used by replay sensitivity).
pub fn compute_score_parts_with(
    rule_hits: &[RuleHit],
    patterns: &[GraphPatternMatch],
    graph: &ResponsibilityGraph,
    evidence_records: &[CorrelationEvidence],
    repetition_count: u32,
    intent_count: u32,
    signals: ScoreSignals,
    config: &ScoringConfig,
) -> ScoreParts {
    // Compute all 7 sub-scores (kept for ScoreBreakdown)
    let rule = sub_score_rule(rule_hits);
    // D2 is driven by graph-pattern matches only. The external content
    // classifier was removed, so no external signal can raise this dimension.
    let pattern = sub_score_pattern(patterns);
    let boundary = sub_score_boundary(graph);
    let evidence = sub_score_evidence(evidence_records);
    let role = sub_score_role(graph, evidence_records, intent_count);
    let repetition = sub_score_repetition(repetition_count, config.repetition_denominator);
    let behavioral = match signals.baseline_deviation {
        Some(deviation) => deviation.clamp(0.0, 1.0),
        None => sub_score_behavioral(graph, evidence_records, config.evidence_denominator),
    };

    // Map to 6-dimension array for fusion:
    // D1: max(boundary, rule) — boundary severity
    // D2: pattern — content/injection patterns
    // D3: role — agentic amplification
    // D4: repetition — temporal dynamics
    // D5: evidence — cross-context corroboration
    // D6: behavioral — baseline deviation
    let dims: [f64; 6] = [
        boundary.max(rule) as f64,
        pattern as f64,
        role as f64,
        repetition as f64,
        evidence as f64,
        behavioral as f64,
    ];

    // OWA+GeoMean fusion → Platt calibration
    let raw = crate::responsibility::scoring::fusion::fuse_dimensions(&dims, &config.weights);
    let calibrated = crate::responsibility::scoring::fusion::platt_sigmoid_params(
        raw,
        config.platt_a,
        config.platt_b,
    );

    let score = (calibrated as f32).min(1.0);

    // Minimum evidence gate: PBC < 10 with no unsafe rules → hard cap at 0.10.
    //
    // This is the optimal operating point (TPR=1.000, FPR=0.263 on 79 scenarios).
    // A composite gate (PBC + total_edges) was tested and rejected — total_edges
    // is contaminated by Claude infrastructure activity (config loading, git ops)
    // and does not discriminate attack from benign. The correct path to lower FPR
    // is Observer LLM semantic analysis, not additional statistical proxies.
    let has_unsafe_rules = rule_hits.iter().any(|h| h.decision == RuleDecision::Unsafe);
    let actual_pbc = graph
        .all_edges()
        .filter(|e| e.edge_type == ResponsibilityEdgeType::PolicyBoundaryCrossing)
        .count();
    let effective_gate = evidence_gate_with(actual_pbc, has_unsafe_rules, config);
    ScoreParts {
        raw: raw as f32,
        calibrated: score,
        gate: effective_gate,
        final_score: (score * effective_gate).min(1.0),
        platt_a: config.platt_a as f32,
        platt_b: config.platt_b as f32,
    }
}

/// Auditable intermediates of the score pipeline.
#[derive(Debug, Clone, Copy)]
pub struct ScoreParts {
    pub raw: f32,
    pub calibrated: f32,
    pub gate: f32,
    pub final_score: f32,
    pub platt_a: f32,
    pub platt_b: f32,
}

/// Backwards-compatible wrapper returning only the final score.
pub fn compute_score(
    rule_hits: &[RuleHit],
    patterns: &[GraphPatternMatch],
    graph: &ResponsibilityGraph,
    evidence_records: &[CorrelationEvidence],
    repetition_count: u32,
    intent_count: u32,
    signals: ScoreSignals,
) -> f32 {
    compute_score_with(
        rule_hits,
        patterns,
        graph,
        evidence_records,
        repetition_count,
        intent_count,
        signals,
        &ScoringConfig::default(),
    )
}

/// Scoring with an explicit parameter set.
pub fn compute_score_with(
    rule_hits: &[RuleHit],
    patterns: &[GraphPatternMatch],
    graph: &ResponsibilityGraph,
    evidence_records: &[CorrelationEvidence],
    repetition_count: u32,
    intent_count: u32,
    signals: ScoreSignals,
    config: &ScoringConfig,
) -> f32 {
    compute_score_parts_with(
        rule_hits,
        patterns,
        graph,
        evidence_records,
        repetition_count,
        intent_count,
        signals,
        config,
    )
    .final_score
}

/// Evidence-sufficiency gate.
///
/// A rule hit is direct evidence of a prohibited action and must stand on its
/// own: the previous gate multiplied it by at most 0.3 when PBC < 10, which
/// made a single `/etc/shadow` read structurally unable to reach the unsafe
/// threshold (0.70). Pattern-only signals still need corroboration, because
/// one suspicious string is not a violation.
pub fn evidence_gate(actual_pbc: usize, has_unsafe_rules: bool) -> f32 {
    evidence_gate_with(actual_pbc, has_unsafe_rules, &ScoringConfig::default())
}

/// Evidence gate with an explicit parameter set.
pub fn evidence_gate_with(
    actual_pbc: usize,
    has_unsafe_rules: bool,
    config: &ScoringConfig,
) -> f32 {
    if has_unsafe_rules {
        return 1.0;
    }
    // Pattern/boundary-only signals: a single crossing is weak evidence, ten
    // or more is strong. Continuous, no hard cliff.
    let raw = 1.0 / (1.0 + (-(actual_pbc as f64 - config.gate_center) / config.gate_slope).exp());
    raw as f32
}

/// Route a computed score to the appropriate classification path.
///
/// Per spec §11.1:
/// - score > 0.70 → DirectUnsafe
/// - score < 0.15 → DirectSafe
/// - 0.15..=0.70 → ToObserver (requires LLM arbitration)
///
/// Returns the route; caller provides the associated ObserverOutput/ObserverSignature.
pub fn route_score(score: f32, config: &ClassificationConfig) -> RouteSelection {
    if score > config.threshold_unsafe {
        RouteSelection::DirectUnsafe
    } else if score < config.threshold_safe {
        RouteSelection::DirectSafe
    } else {
        RouteSelection::ToObserver
    }
}

/// Result of routing: which branch to take.
#[derive(Debug, Clone, PartialEq)]
pub enum RouteSelection {
    DirectSafe,
    DirectUnsafe,
    ToObserver,
}

/// Heuristic fallback decision when the Observer LLM is unavailable.
///
/// Per spec §11.3:
/// - score > 0.70 → Unsafe/Critical
/// - score > 0.15 → Uncertain/Warning
/// - else → Safe/Info
pub fn heuristic_decide(score: f32) -> ObserverOutput {
    let (decision, severity) = if score > 0.70 {
        (ObserverDecision::Unsafe, ClaimSeverity::Critical)
    } else if score > 0.15 {
        (ObserverDecision::Uncertain, ClaimSeverity::Warning)
    } else {
        (ObserverDecision::Safe, ClaimSeverity::Info)
    };

    let reasoning = format!(
        "Heuristic fallback: score={:.2} → {} ({:?})",
        score,
        decision.as_str(),
        severity
    );

    ObserverOutput {
        decision,
        severity,
        confidence: score,
        cache_ttl: CacheTTL::Once,
        authority: AlertAuthority::Unavailable,
        reasoning,
        attacked_flag: false,
        evidence_cited: vec![],
        rule_verdicts: vec![],
        pattern_verdicts: vec![],
        candidate_verdicts: vec![],
        review_provenance: vec![],
    }
}

// ── Sub-score functions (per spec §11.1) ─────────────────────────────

/// rule score: whether any rule on a confirmed action is Unsafe.
///
/// The old formula divided unsafe hits by *all* hits, including Safe ones:
/// one `/etc/shadow` read plus nine ordinary file reads scored 0.10 while the
/// same shadow read alone scored 1.0. That made the dimension *decrease* as
/// benign activity increased, which is the opposite of a risk signal and tied
/// the score to task size. A rule hit is direct evidence; its presence, not
/// the amount of unrelated traffic, is what the dimension measures.
fn sub_score_rule(rule_hits: &[RuleHit]) -> f32 {
    if rule_hits.iter().any(|h| h.decision == RuleDecision::Unsafe) {
        1.0
    } else {
        0.0
    }
}

/// pattern score: mean confidence of matched templates (0.0 if none)
fn sub_score_pattern(patterns: &[GraphPatternMatch]) -> f32 {
    if patterns.is_empty() {
        return 0.0;
    }
    patterns.iter().map(|p| p.confidence).sum::<f32>() / patterns.len() as f32
}

/// boundary score: anomaly_edges / max(total_edges, 1)
///
/// Anomaly edges are: PolicyBoundaryCrossing + TaintPropagatesTo +
/// AgentDelegatesTo + ContextFlowsFrom + CompetingExplanation
fn sub_score_boundary(graph: &ResponsibilityGraph) -> f32 {
    let total = graph.edge_count().max(1);
    let anomaly_count = graph
        .all_edges()
        .filter(|e| {
            matches!(
                e.edge_type,
                ResponsibilityEdgeType::PolicyBoundaryCrossing
                    | ResponsibilityEdgeType::TaintPropagatesTo
                    | ResponsibilityEdgeType::AgentDelegatesTo
                    | ResponsibilityEdgeType::ContextFlowsFrom
                    | ResponsibilityEdgeType::CompetingExplanation
            )
        })
        .count();
    anomaly_count as f32 / total as f32
}

/// evidence score: match-level-weighted (v14). L5=0.02, L1=1.0, L3=0.7.
fn sub_score_evidence(evidence_records: &[CorrelationEvidence]) -> f32 {
    const MIN: f32 = 0.45;
    let mut wc: f32 = 0.0;
    let mut tw: f32 = 0.0;
    for e in evidence_records {
        if e.confidence < MIN {
            continue;
        }
        let w = match e.match_level {
            MatchLevel::L1Exact => 1.0,
            MatchLevel::L2Normalized => 0.85,
            MatchLevel::L3Containment => 0.7,
            MatchLevel::L4Basename => 0.15,
            MatchLevel::L5Substring => 0.02,
            // Lineage evidence corroborates weakly on its own: the program
            // the agent ran produced this action, but no argument ties them.
            MatchLevel::ProcessLineage => 0.30,
        };
        wc += e.confidence * w;
        tw += w;
    }
    if tw < 1e-9 { 0.0 } else { wc / tw }
}

/// role score: sigmoid(PBC/intent) × evidence_quality (v14).
///
/// v9-v11: normalized PBC by agent_count — failed to discriminate because
/// benign file I/O and multi-agent attacks produce comparable agent-level
/// PBC counts.
///
/// v14: normalize by INTENT count (tool calls). Attacks concentrate PBC
/// into fewer intents (ratio=10-25), benign tasks spread PBC across many
/// tool calls (ratio=1-3). Multiplied by evidence quality (L1+L3 ratio)
/// for corroboration — benign tasks produce mostly low-quality L5 evidence.
/// Falls back to PBC/agent sigmoid when intent_count is unavailable.
fn sub_score_role(
    graph: &ResponsibilityGraph,
    evidence_records: &[CorrelationEvidence],
    intent_count: u32,
) -> f32 {
    let agent_count = graph
        .all_nodes()
        .filter(|n| n.node_type == ResponsibilityNodeType::Agent)
        .filter(|n| {
            n.agent_id.as_ref().map(|a| a.as_str())
                != Some(crate::responsibility::graph::UNATTRIBUTED_AGENT_ID)
        })
        .count()
        .max(1) as f32;

    let pbc_count = graph
        .all_edges()
        .filter(|e| e.edge_type == ResponsibilityEdgeType::PolicyBoundaryCrossing)
        .count() as f32;

    if pbc_count <= 0.0 {
        return 0.0;
    }

    // Compute evidence quality: precise matches, including process lineage.
    // Lineage is exact evidence that a process descends from the agent; the old
    // filter dropped it and made a lineage-heavy multi-agent run score as if it
    // had almost no high-quality evidence (role 0.06 vs 0.38 on the same attack).
    let l1_l3 = evidence_records
        .iter()
        .filter(|e| {
            matches!(
                e.match_level,
                MatchLevel::L1Exact | MatchLevel::L3Containment | MatchLevel::ProcessLineage
            )
        })
        .count() as f32;
    let ev_total = evidence_records.len().max(1) as f32;
    let evidence_quality = l1_l3 / ev_total;

    // v14: PBC/intent sigmoid when intent data is available
    if intent_count > 0 && evidence_quality > 0.0 {
        let ratio = pbc_count / intent_count.max(1) as f32;
        let sig = 1.0 / (1.0 + (-(ratio - 4.0) / 1.5).exp());
        return sig * evidence_quality;
    }

    // Fallback: PBC/agent sigmoid
    let center = 100.0 * agent_count;
    let scale = 20.0 * agent_count;
    1.0 / (1.0 + (-(pbc_count - center) / scale).exp())
}

/// repetition score: min(rep_count/10, 1.0)
fn sub_score_repetition(repetition_count: u32, denominator: f64) -> f32 {
    // Repetition score saturates at the configured denominator (default 50).
    (repetition_count as f64 / denominator).min(1.0) as f32
}

/// Behavioral anomaly score: credential scanning, data exfiltration,
/// suspicious network patterns detected in the responsibility graph.
///
/// Weights PBC edges with evidence binding and credential/sensitive
/// resource patterns. Saturates at 1.0 for graphs with strong behavioral
/// signals.
fn sub_score_behavioral(
    graph: &ResponsibilityGraph,
    evidence_records: &[CorrelationEvidence],
    denominator: f64,
) -> f32 {
    // Count PBC edges that have evidence binding (confirmed violations).
    let confirmed_pbc = graph
        .all_edges()
        .filter(|e| {
            e.edge_type == ResponsibilityEdgeType::PolicyBoundaryCrossing
                && !e.evidence_refs.is_empty()
        })
        .count();

    // Count high-confidence evidence records.
    let high_conf_evidence = evidence_records
        .iter()
        .filter(|ev| ev.confidence >= 0.80)
        .count();

    // Behavioral score: confirmed PBC edges + high-confidence evidence.
    // Saturates at the configured denominator (default 50).
    let combined = (confirmed_pbc + high_conf_evidence) as f64;
    (combined / denominator).min(1.0) as f32
}

// ═══════════════════════════════════════════════════════════════════════════
//  Score breakdown helper (public, for AlertBuilder)
// ═══════════════════════════════════════════════════════════════════════════

/// Compute a full ScoreBreakdown with all dimensional sub-scores.
pub fn compute_score_breakdown(
    rule_hits: &[RuleHit],
    patterns: &[GraphPatternMatch],
    graph: &ResponsibilityGraph,
    evidence_records: &[CorrelationEvidence],
    repetition_count: u32,
    intent_count: u32,
    signals: ScoreSignals,
) -> ScoreBreakdown {
    compute_score_breakdown_with(
        rule_hits,
        patterns,
        graph,
        evidence_records,
        repetition_count,
        intent_count,
        signals,
        &ScoringConfig::default(),
    )
}

/// Score breakdown with an explicit parameter set.
pub fn compute_score_breakdown_with(
    rule_hits: &[RuleHit],
    patterns: &[GraphPatternMatch],
    graph: &ResponsibilityGraph,
    evidence_records: &[CorrelationEvidence],
    repetition_count: u32,
    intent_count: u32,
    signals: ScoreSignals,
    config: &ScoringConfig,
) -> ScoreBreakdown {
    let rule_score = sub_score_rule(rule_hits);
    let pattern_score = sub_score_pattern(patterns);
    let boundary_score = sub_score_boundary(graph);
    let evidence_score = sub_score_evidence(evidence_records);
    let role_score = sub_score_role(graph, evidence_records, intent_count);
    let repetition_score = sub_score_repetition(repetition_count, config.repetition_denominator);
    let behavioral_score = match signals.baseline_deviation {
        Some(deviation) => deviation.clamp(0.0, 1.0),
        None => sub_score_behavioral(graph, evidence_records, config.evidence_denominator),
    };

    let parts = compute_score_parts_with(
        rule_hits,
        patterns,
        graph,
        evidence_records,
        repetition_count,
        intent_count,
        signals,
        config,
    );
    ScoreBreakdown {
        rule_score,
        pattern_score,
        boundary_score,
        evidence_score,
        role_score,
        repetition_score,
        behavioral_score,
        total: parts.final_score,
        raw_score: parts.raw,
        calibrated_score: parts.calibrated,
        gate_factor: parts.gate,
        gate_version: "v2".to_string(),
        platt_a: parts.platt_a,
        platt_b: parts.platt_b,
        observer_adjusted: false,
    }
}

// ═══════════════════════════════════════════════════════════════════════════
//  Observer signature builder (for LLM arbitration)
// ═══════════════════════════════════════════════════════════════════════════

/// Build an ObserverSignature from decomposed classification signals.
pub fn build_observer_signature(
    graph: &ResponsibilityGraph,
    evidence_records: &[CorrelationEvidence],
    rule_hits: &[RuleHit],
    patterns: &[GraphPatternMatch],
    score: f32,
) -> ObserverSignature {
    let breakdown = compute_score_breakdown(
        rule_hits,
        patterns,
        graph,
        evidence_records,
        0, // repetition_count not available at this level
        0, // intent_count not available at this level (observer signature context)
        ScoreSignals::default(),
    );

    let evidence_sig = {
        let total = evidence_records.len();
        let direct = evidence_records
            .iter()
            .filter(|e| e.match_level >= MatchLevel::L3Containment)
            .count();
        let inferred = total.saturating_sub(direct);
        let avg_conf = if total > 0 {
            evidence_records.iter().map(|e| e.confidence).sum::<f32>() / total as f32
        } else {
            0.0
        };
        let max_conf = evidence_records
            .iter()
            .map(|e| e.confidence)
            .fold(0.0f32, f32::max);
        EvidenceSignature {
            total,
            direct,
            inferred,
            avg_confidence: avg_conf,
            max_confidence: max_conf,
            match_levels: evidence_records
                .iter()
                .map(|e| format!("{:?}", e.match_level))
                .collect(),
        }
    };

    let graph_sig = {
        let edge_types: Vec<String> = graph
            .all_edges()
            .map(|e| format!("{:?}", e.edge_type))
            .collect();
        let node_types: Vec<String> = graph
            .all_nodes()
            .map(|n| format!("{:?}", n.node_type))
            .collect();
        let bc = graph
            .all_edges()
            .filter(|e| e.edge_type == ResponsibilityEdgeType::PolicyBoundaryCrossing)
            .count();
        let dd = graph
            .all_edges()
            .filter(|e| e.edge_type == ResponsibilityEdgeType::AgentDelegatesTo)
            .count();
        let ca = graph
            .all_edges()
            .filter(|e| {
                matches!(
                    e.edge_type,
                    ResponsibilityEdgeType::AgentDelegatesTo
                        | ResponsibilityEdgeType::ContextFlowsFrom
                        | ResponsibilityEdgeType::TaintPropagatesTo
                )
            })
            .count();
        let ac = graph
            .all_nodes()
            .filter(|n| n.node_type == ResponsibilityNodeType::Agent)
            .filter(|n| {
                n.agent_id.as_ref().map(|a| a.as_str())
                    != Some(crate::responsibility::graph::UNATTRIBUTED_AGENT_ID)
            })
            .count();
        GraphSignature {
            node_count: graph.node_count(),
            edge_count: graph.edge_count(),
            edge_types,
            node_types,
            boundary_crossings: bc,
            delegation_depth: dd,
            cross_agent_edges: ca,
            agent_count: ac,
            temporal_span_ms: 0,
        }
    };

    let pattern_sigs: Vec<PatternSignal> = patterns
        .iter()
        .map(|p| PatternSignal {
            template_id: p.template_id.clone(),
            match_confidence: p.confidence,
            severity: format!("{:?}", p.severity),
        })
        .collect();

    let role_sig = RoleSignal {
        claimed_role: None,
        role_mismatches: vec![],
        subject: graph
            .all_nodes()
            .filter(|n| n.node_type == ResponsibilityNodeType::Agent)
            .find(|n| {
                n.agent_id.as_ref().map(|a| a.as_str())
                    != Some(crate::responsibility::graph::UNATTRIBUTED_AGENT_ID)
            })
            .and_then(|n| n.agent_id.as_ref().map(|a| a.as_str().to_string())),
    };

    let resource_sigs: Vec<ResourceSignal> = graph
        .all_edges()
        .filter_map(|e| {
            graph.get_node(&e.target).map(|target| {
                let (rt, path) = match &target.resource {
                    Some(NormalizedResource::File { canonical_path }) => {
                        ("file", canonical_path.clone())
                    }
                    Some(NormalizedResource::Network { host, port, scheme }) => {
                        ("network", format!("{}://{}:{}", scheme, host, port))
                    }
                    Some(NormalizedResource::Context { context_id }) => {
                        ("context", context_id.clone())
                    }
                    Some(NormalizedResource::Credential { credential_id }) => {
                        ("credential", credential_id.clone())
                    }
                    _ => ("unknown", target.node_id.as_str().to_string()),
                };
                let op = match e.edge_type {
                    ResponsibilityEdgeType::IntentCausesAction => "use",
                    ResponsibilityEdgeType::PolicyBoundaryCrossing => "cross",
                    ResponsibilityEdgeType::TaintPropagatesTo => "taint",
                    ResponsibilityEdgeType::AgentDelegatesTo => "delegate",
                    _ => "other",
                };
                ResourceSignal {
                    resource_type: rt.into(),
                    path,
                    operation: op.into(),
                }
            })
        })
        .collect();

    let corr_sig = CorrelationSignal {
        has_delegation_chain: graph
            .all_edges()
            .any(|e| e.edge_type == ResponsibilityEdgeType::AgentDelegatesTo),
        has_tainted_context: graph
            .all_edges()
            .any(|e| e.edge_type == ResponsibilityEdgeType::TaintPropagatesTo),
        has_credential_pattern: graph
            .all_nodes()
            .any(|n| matches!(&n.resource, Some(NormalizedResource::Credential { .. }))),
        has_concurrent_access: false,
    };

    let rule_hit_sigs: Vec<RuleHitSignal> = rule_hits
        .iter()
        .map(|h| RuleHitSignal {
            rule_id: h.rule_id.clone(),
            matched_resource: h.matched_resource.clone(),
        })
        .collect();

    ObserverSignature {
        classification: ClassificationResult {
            score,
            route: "observer".into(),
            score_breakdown: breakdown,
            cusum_triggered: false,
            cusum_dimensions: vec![],
            progressive_fused: 0.0,
            observer_trigger_reason: String::new(),
            pattern_verdicts: patterns
                .iter()
                .map(|pattern| PatternVerdict {
                    template_id: pattern.template_id.clone(),
                    match_confidence: pattern.confidence,
                    llm_agreed: None,
                    llm_comment: None,
                })
                .collect(),
        },
        rule_hits: rule_hit_sigs,
        evidence: evidence_sig,
        graph: graph_sig,
        patterns: pattern_sigs,
        roles: role_sig,
        resources_touched: resource_sigs,
        correlation_evidence: corr_sig,
    }
}

// ── Tests ──────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use crate::responsibility::graph::ResponsibilityGraph;
    use crate::responsibility::types::evidence_types::{CorrelationEvidence, MatchLevel};
    use crate::responsibility::types::{
        ActionId, AgentId, EdgeId, EvidenceId, IntentId, NodeId, NormalizedResource,
        PhaseIndicator, ResponsibilityEdgeType as NewEdgeType,
        ResponsibilityNodeType as NewNodeType,
    };

    fn make_node(
        id: &str,
        node_type: NewNodeType,
        agent: Option<&str>,
        resource: Option<NormalizedResource>,
    ) -> crate::responsibility::types::ResponsibilityNode {
        crate::responsibility::types::ResponsibilityNode {
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
        edge_type: NewEdgeType,
        source: &str,
        target: &str,
    ) -> crate::responsibility::types::ResponsibilityEdge {
        crate::responsibility::types::ResponsibilityEdge {
            edge_id: EdgeId::from(id),
            edge_type,
            operation: None,
            source: NodeId::from(source),
            target: NodeId::from(target),
            evidence_refs: Vec::new(),
            confidence: 0.9,
            phase: PhaseIndicator::Provisional { timestamp_ns: 1000 },
        }
    }

    fn make_evidence(confidence: f32) -> CorrelationEvidence {
        CorrelationEvidence {
            evidence_id: EvidenceId::new(),
            intent_id: IntentId::new(),
            action_id: ActionId::new(),
            match_level: MatchLevel::L3Containment,
            confidence,
            matched_fields: crate::responsibility::types::evidence_types::NormalizedArgs {
                canonical_path: None,
                host: None,
                port: None,
                command: None,
                raw_args: vec![],
            },
            derivation_mode:
                crate::responsibility::types::evidence_types::CandidateDerivationMode::Direct,
            timestamp_ns: 1000,
            phase: PhaseIndicator::Provisional { timestamp_ns: 1000 },
        }
    }

    #[test]
    fn test_sub_score_rule_empty() {
        assert_eq!(sub_score_rule(&[]), 0.0);
    }

    #[test]
    fn test_sub_score_rule_all_safe() {
        let hits = vec![RuleHit {
            rule_id: "safe.a".into(),
            decision: RuleDecision::Safe,
            matched_resource: "/tmp/a".into(),
        }];
        assert_eq!(sub_score_rule(&hits), 0.0);
    }

    #[test]
    fn test_sub_score_rule_mixed() {
        let hits = vec![
            RuleHit {
                rule_id: "safe.a".into(),
                decision: RuleDecision::Safe,
                matched_resource: "/tmp/a".into(),
            },
            RuleHit {
                rule_id: "unsafe.b".into(),
                decision: RuleDecision::Unsafe,
                matched_resource: "/etc/shadow".into(),
            },
        ];
        // Any unsafe hit is direct evidence. The presence of Safe hits must
        // not dilute it (the old formula returned 0.5 here and 0.1 with nine
        // benign reads).
        assert_eq!(sub_score_rule(&hits), 1.0);
        let mut many_safe = vec![RuleHit {
            rule_id: "unsafe.b".into(),
            decision: RuleDecision::Unsafe,
            matched_resource: "/etc/shadow".into(),
        }];
        for i in 0..9 {
            many_safe.push(RuleHit {
                rule_id: format!("safe.{i}"),
                decision: RuleDecision::Safe,
                matched_resource: "/tmp/x".into(),
            });
        }
        assert_eq!(sub_score_rule(&many_safe), 1.0);
    }

    #[test]
    fn test_sub_score_pattern_empty() {
        assert_eq!(sub_score_pattern(&[]), 0.0);
    }

    #[test]
    fn test_sub_score_repetition() {
        assert_eq!(sub_score_repetition(0, 50.0), 0.0);
        assert_eq!(sub_score_repetition(5, 50.0), 0.1);
        assert_eq!(sub_score_repetition(25, 50.0), 0.5);
        assert_eq!(sub_score_repetition(50, 50.0), 1.0);
        assert_eq!(sub_score_repetition(100, 50.0), 1.0); // capped at 1.0
    }

    #[test]
    fn test_route_score_unsafe() {
        let config = ClassificationConfig::default();
        assert_eq!(route_score(0.85, &config), RouteSelection::DirectUnsafe);
        assert_eq!(route_score(0.71, &config), RouteSelection::DirectUnsafe);
    }

    #[test]
    fn test_route_score_safe() {
        let config = ClassificationConfig::default();
        assert_eq!(route_score(0.10, &config), RouteSelection::DirectSafe);
        assert_eq!(route_score(0.0, &config), RouteSelection::DirectSafe);
    }

    #[test]
    fn test_route_score_to_observer() {
        let config = ClassificationConfig::default();
        assert_eq!(route_score(0.50, &config), RouteSelection::ToObserver);
        assert_eq!(route_score(0.15, &config), RouteSelection::ToObserver); // inclusive lower
        assert_eq!(route_score(0.70, &config), RouteSelection::ToObserver); // inclusive upper
    }

    #[test]
    fn test_heuristic_decide_unsafe() {
        let output = heuristic_decide(0.85);
        assert_eq!(output.decision, ObserverDecision::Unsafe);
        assert_eq!(output.severity, ClaimSeverity::Critical);
    }

    #[test]
    fn test_heuristic_decide_uncertain() {
        let output = heuristic_decide(0.50);
        assert_eq!(output.decision, ObserverDecision::Uncertain);
        assert_eq!(output.severity, ClaimSeverity::Warning);
    }

    #[test]
    fn test_heuristic_decide_safe() {
        let output = heuristic_decide(0.05);
        assert_eq!(output.decision, ObserverDecision::Safe);
        assert_eq!(output.severity, ClaimSeverity::Info);
    }

    #[test]
    fn test_role_score_treats_process_lineage_as_high_quality_evidence() {
        let mut graph = ResponsibilityGraph::default();
        graph.add_node(make_node("a1", NewNodeType::Agent, Some("agent-1"), None));
        graph.add_node(make_node("r1", NewNodeType::Resource, None, None));
        graph.add_edge(make_edge(
            "e1",
            NewEdgeType::PolicyBoundaryCrossing,
            "a1",
            "r1",
        ));
        let mut evidence = make_evidence(0.4);
        evidence.match_level = MatchLevel::ProcessLineage;
        let role = sub_score_role(&graph, &[evidence], 1);
        assert!(role > 0.0, "lineage-only evidence must count as quality");
    }

    #[test]
    fn test_compute_score_integration() {
        let mut g = ResponsibilityGraph::default();
        g.add_node(make_node("a1", NewNodeType::Agent, Some("agent-1"), None));
        g.add_node(make_node(
            "r1",
            NewNodeType::Resource,
            Some("agent-1"),
            Some(NormalizedResource::File {
                canonical_path: "/etc/shadow".into(),
            }),
        ));
        g.add_edge(make_edge(
            "e1",
            NewEdgeType::PolicyBoundaryCrossing,
            "a1",
            "r1",
        ));

        let rule_hits = vec![RuleHit {
            rule_id: "unsafe.shadow".into(),
            decision: RuleDecision::Unsafe,
            matched_resource: "/etc/shadow".into(),
        }];

        let evidence = vec![make_evidence(0.8), make_evidence(0.6)];

        let score = compute_score(
            &rule_hits,
            &[],
            &g,
            &evidence,
            0,
            0,
            ScoreSignals::default(),
        );
        // A single `/etc/shadow` read with an Unsafe rule is direct evidence and
        // must not be crushed below the actionable threshold by a PBC count
        // gate (the old assertion locked exactly that bug).
        assert!(
            score > 0.30,
            "single high-severity rule was suppressed: {score}"
        );
    }

    #[test]
    fn test_compute_score_safe() {
        let mut g = ResponsibilityGraph::default();
        g.add_node(make_node("a1", NewNodeType::Agent, Some("agent-1"), None));
        g.add_node(make_node(
            "r1",
            NewNodeType::Resource,
            Some("agent-1"),
            Some(NormalizedResource::File {
                canonical_path: "/workspace/project/src/main.rs".into(),
            }),
        ));
        g.add_edge(make_edge("e1", NewEdgeType::IntentCausesAction, "a1", "r1"));

        let rule_hits = vec![RuleHit {
            rule_id: "safe.project_read".into(),
            decision: RuleDecision::Safe,
            matched_resource: "/workspace/project/src/main.rs".into(),
        }];

        let score = compute_score(&rule_hits, &[], &g, &[], 0, 0, ScoreSignals::default());
        // rule=0.0, pattern=0.0, boundary=0.0, evidence=0.0, role=0.0, rep=0
        assert!(score < 0.10);
    }

    #[test]
    fn unsafe_rules_are_not_suppressed_by_low_pbc() {
        // A single high-severity rule hit is direct evidence. The old gate
        // capped it at 0.3, making a one-event attack structurally unable to
        // reach the 0.70 actionable threshold.
        assert_eq!(evidence_gate(0, true), 1.0);
        assert_eq!(evidence_gate(3, true), 1.0);
    }

    #[test]
    fn score_breakdown_records_its_own_intermediates() {
        let graph = ResponsibilityGraph::default();
        let breakdown =
            compute_score_breakdown(&[], &[], &graph, &[], 0, 0, ScoreSignals::default());
        assert_eq!(breakdown.gate_version, "v2");
        assert_eq!(breakdown.platt_a, 7.0);
        assert_eq!(breakdown.platt_b, -3.0);
        let recomputed = breakdown.calibrated_score * breakdown.gate_factor;
        assert!(
            (breakdown.total - recomputed).abs() < 1e-6,
            "total={} calibrated={} gate={}",
            breakdown.total,
            breakdown.calibrated_score,
            breakdown.gate_factor
        );
    }

    /// A legacy ONNX score may exist in old JSON, but it is not an input to
    /// the current engine: deserializing it must not produce a signal that
    /// changes the score.
    #[test]
    fn legacy_external_classifier_signal_cannot_change_the_score() {
        let legacy: ScoreSignals = serde_json::from_str(
            r#"{"baseline_deviation":null,"cusum_triggered":false,"cusum_dimensions":[],"progressive_fused":0.0,"observer_trigger_reason":"","content_threat":0.95,"deberta_score":0.95}"#,
        )
        .expect("legacy unknown extras must remain readable");
        let current = ScoreSignals::default();
        assert_eq!(
            serde_json::to_value(&legacy).expect("must serialize"),
            serde_json::to_value(&current).expect("must serialize"),
            "legacy ONNX keys must be dropped, not carried as current signals"
        );

        let graph = ResponsibilityGraph::default();
        assert_eq!(
            compute_score(&[], &[], &graph, &[], 0, 0, legacy),
            compute_score(&[], &[], &graph, &[], 0, 0, current),
            "an external classifier signal must not affect the current score"
        );
    }

    #[test]
    fn behavioral_baseline_signal_raises_d6() {
        let graph = ResponsibilityGraph::default();
        let base = compute_score(&[], &[], &graph, &[], 0, 0, ScoreSignals::default());
        let with_baseline = compute_score(
            &[],
            &[],
            &graph,
            &[],
            0,
            0,
            ScoreSignals {
                baseline_deviation: Some(0.9),
                ..Default::default()
            },
        );
        assert!(
            with_baseline > base,
            "baseline deviation must change the score ({with_baseline} vs {base})"
        );
    }

    #[test]
    fn pattern_only_signals_still_need_corroboration() {
        assert!(evidence_gate(0, false) < 0.05);
        assert!(evidence_gate(5, false) < 0.5);
        assert!(evidence_gate(10, false) > 0.45 && evidence_gate(10, false) < 0.55);
        assert!(evidence_gate(30, false) > 0.95);
    }
}
