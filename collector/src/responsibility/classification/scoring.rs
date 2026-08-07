use crate::responsibility::classification::pattern_matcher::{
    GraphPatternMatch, GraphPatternMatcher, default_templates,
};
use crate::responsibility::classification::rule_engine::RuleEngine;
use crate::responsibility::classification::rules::{RuleDecision, RuleHit};
use crate::responsibility::classification::types::{
    AlertAuthority, CacheTTL, ClassificationConfig, ClassificationResult,
    CorrelationSignal, EvidenceSignature, GraphSignature,
    ObserverDecision, ObserverOutput, ObserverSignature, PatternSignal,
    ResourceSignal, RoleSignal, RuleHitSignal,
};
use crate::responsibility::claims::ClaimSeverity;
use crate::responsibility::types::{
    NormalizedResource, ResponsibilityEdgeType, ResponsibilityNodeType, ScoreBreakdown,
};
use crate::responsibility::graph::ResponsibilityGraph;
use crate::responsibility::evidence::EvidenceRecordNew;
use crate::responsibility::types::evidence_types::{CorrelationEvidence, MatchLevel};


// ═══════════════════════════════════════════════════════════════════════════
//  Standalone scoring functions per spec §11.1
// ═══════════════════════════════════════════════════════════════════════════

/// Compute the classification score using v9 six-dimension fusion.
///
/// The old 7-dim weighted sum is replaced by OWA(0.80) + GeoMean_active(0.20)
/// fusion followed by Platt sigmoid calibration.  This gives balanced
/// scoring that requires corroboration (GeoMean) while still detecting
/// single-dimension strong signals (OWA).
///
/// Each sub-score is normalized to [0, 1].
pub fn compute_score(
    rule_hits: &[RuleHit],
    patterns: &[GraphPatternMatch],
    graph: &ResponsibilityGraph,
    evidence_records: &[CorrelationEvidence],
    repetition_count: u32,
    intent_count: u32,
) -> f32 {
    // Compute all 7 sub-scores (kept for ScoreBreakdown)
    let rule = sub_score_rule(rule_hits);
    let pattern = sub_score_pattern(patterns);
    let boundary = sub_score_boundary(graph);
    let evidence = sub_score_evidence(evidence_records);
    let role = sub_score_role(graph, evidence_records, intent_count);
    let repetition = sub_score_repetition(repetition_count);
    let behavioral = sub_score_behavioral(graph, evidence_records);

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

    // v9 fusion weights match synthesis spec
    let weights: [f64; 6] = [0.28, 0.25, 0.20, 0.10, 0.10, 0.07];

    // OWA+GeoMean fusion → Platt calibration
    let raw = crate::responsibility::scoring::fusion::fuse_dimensions(&dims, &weights);
    let calibrated = crate::responsibility::scoring::fusion::platt_sigmoid(raw);

    let score = (calibrated as f32).min(1.0);

    // Minimum evidence gate: PBC < 10 with no unsafe rules → hard cap at 0.10.
    //
    // This is the optimal operating point (TPR=1.000, FPR=0.263 on 79 scenarios).
    // A composite gate (PBC + total_edges) was tested and rejected — total_edges
    // is contaminated by Claude infrastructure activity (config loading, git ops)
    // and does not discriminate attack from benign. The correct path to lower FPR
    // is Observer LLM semantic analysis, not additional statistical proxies.
    let has_unsafe_rules = rule_hits.iter().any(|h| h.decision == RuleDecision::Unsafe);
    let actual_pbc = graph.all_edges()
        .filter(|e| e.edge_type == ResponsibilityEdgeType::PolicyBoundaryCrossing)
        .count();
    let gate_factor = 1.0 / (1.0 + ((-(actual_pbc as f64 - 10.0) / 0.5)).exp());
    let effective_gate = if has_unsafe_rules {
        gate_factor.max(0.3)
    } else {
        gate_factor
    };
    (score * effective_gate as f32).min(1.0)
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
    }
}

// ── Sub-score functions (per spec §11.1) ─────────────────────────────

/// rule score: unsafe_hits / max(total_hits, 1)
fn sub_score_rule(rule_hits: &[RuleHit]) -> f32 {
    let total = rule_hits.len().max(1);
    let unsafe_count = rule_hits
        .iter()
        .filter(|h| h.decision == RuleDecision::Unsafe)
        .count();
    unsafe_count as f32 / total as f32
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
    const MIN: f32 = 0.45; let mut wc: f32=0.0; let mut tw: f32=0.0;
    for e in evidence_records { if e.confidence < MIN { continue; }
        let w = match e.match_level { MatchLevel::L1Exact=>1.0, MatchLevel::L2Normalized=>0.85, MatchLevel::L3Containment=>0.7, MatchLevel::L4Basename=>0.15, MatchLevel::L5Substring=>0.02 };
        wc += e.confidence * w; tw += w;
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
fn sub_score_role(graph: &ResponsibilityGraph, evidence_records: &[CorrelationEvidence], intent_count: u32) -> f32 {
    let agent_count = graph
        .all_nodes()
        .filter(|n| n.node_type == ResponsibilityNodeType::Agent)
        .count()
        .max(1) as f32;

    let pbc_count = graph
        .all_edges()
        .filter(|e| e.edge_type == ResponsibilityEdgeType::PolicyBoundaryCrossing)
        .count() as f32;

    if pbc_count <= 0.0 {
        return 0.0;
    }

    // Compute evidence quality: fraction of L1+L3 evidence (v14)
    let l1_l3 = evidence_records.iter().filter(|e| {
        matches!(e.match_level, MatchLevel::L1Exact | MatchLevel::L3Containment)
    }).count() as f32;
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
    (1.0 / (1.0 + (-(pbc_count - center) / scale).exp())) as f32
}

/// repetition score: min(rep_count/10, 1.0)
fn sub_score_repetition(repetition_count: u32) -> f32 {
    // Repetition score saturates at 50+ repeated observations. The previous
    // divisor of 10 meant any graph with >10 nodes got the full 1.0 weight,
    // inflating safe-graph scores into the ambiguous range.
    (repetition_count as f32 / 50.0).min(1.0)
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
    // Saturates at 50 combined signals → 1.0.
    let combined = (confirmed_pbc + high_conf_evidence) as f32;
    (combined / 50.0).min(1.0)
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
) -> ScoreBreakdown {
    let rule_score = sub_score_rule(rule_hits);
    let pattern_score = sub_score_pattern(patterns);
    let boundary_score = sub_score_boundary(graph);
    let evidence_score = sub_score_evidence(evidence_records);
    let role_score = sub_score_role(graph, evidence_records, intent_count);
    let repetition_score = sub_score_repetition(repetition_count);
    let behavioral_score = sub_score_behavioral(graph, evidence_records);

    ScoreBreakdown {
        rule_score,
        pattern_score,
        boundary_score,
        evidence_score,
        role_score,
        repetition_score,
        behavioral_score,
        total: compute_score(rule_hits, patterns, graph, evidence_records, repetition_count, intent_count),
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
    );

    let evidence_sig = {
        let total = evidence_records.len();
        let direct = evidence_records.iter().filter(|e| e.match_level >= MatchLevel::L3Containment).count();
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
            .find(|n| n.node_type == ResponsibilityNodeType::Agent)
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
                    Some(NormalizedResource::Network { host, port, scheme }) => (
                        "network",
                        format!("{}://{}:{}", scheme, host, port),
                    ),
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
            deberta_score: 0.0,
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
    use crate::responsibility::types::{
        ActionId, AgentId, EdgeId, EvidenceId, IntentId, NodeId, NormalizedResource,
        PhaseIndicator, ResponsibilityEdgeType as NewEdgeType,
        ResponsibilityNodeType as NewNodeType,
    };
    use crate::responsibility::graph::{GraphConfig, ResponsibilityGraph};
    use crate::responsibility::types::evidence_types::{CorrelationEvidence, MatchLevel};

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
            source: NodeId::from(source),
            target: NodeId::from(target),
            evidence_refs: Vec::new(),
            confidence: 0.9,
            phase: PhaseIndicator::Provisional { timestamp_ns: 1000 },
            signal: None,
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
            derivation_mode: crate::responsibility::types::evidence_types::CandidateDerivationMode::Direct,
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
        let hits = vec![
            RuleHit {
                rule_id: "safe.a".into(),
                decision: RuleDecision::Safe,
                matched_resource: "/tmp/a".into(),
            },
        ];
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
        // 1 unsafe / 2 total = 0.5
        assert!((sub_score_rule(&hits) - 0.5).abs() < 0.01);
    }

    #[test]
    fn test_sub_score_pattern_empty() {
        assert_eq!(sub_score_pattern(&[]), 0.0);
    }

    #[test]
    fn test_sub_score_repetition() {
        assert_eq!(sub_score_repetition(0), 0.0);
        assert_eq!(sub_score_repetition(5), 0.1);
        assert_eq!(sub_score_repetition(25), 0.5);
        assert_eq!(sub_score_repetition(50), 1.0);
        assert_eq!(sub_score_repetition(100), 1.0); // capped at 1.0
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

        let score = compute_score(&rule_hits, &[], &g, &evidence, 0, 0);
        // rule=1.0*0.30=0.30, pattern=0.0, boundary=1.0*0.20=0.20, evidence=0.7*0.10=0.07, role=1/1*0.05=0.05, rep=0
        // total = 0.30+0.20+0.07+0.05=0.62
        assert!(score > 0.10);
        assert!(score < 0.30);
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
                canonical_path: "/home/lzk/mtagentlens/src/main.rs".into(),
            }),
        ));
        g.add_edge(make_edge(
            "e1",
            NewEdgeType::IntentCausesAction,
            "a1",
            "r1",
        ));

        let rule_hits = vec![RuleHit {
            rule_id: "safe.project_read".into(),
            decision: RuleDecision::Safe,
            matched_resource: "/home/lzk/mtagentlens/src/main.rs".into(),
        }];

        let score = compute_score(&rule_hits, &[], &g, &[], 0, 0);
        // rule=0.0, pattern=0.0, boundary=0.0, evidence=0.0, role=0.0, rep=0
        assert!(score < 0.10);
    }
}
