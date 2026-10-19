// SPDX-License-Identifier: Apache-2.0 OR MIT
//
// claim_builder.rs — reads PolicyBoundaryCrossing edges from the
// ResponsibilityGraph and builds ResponsibilityClaims (new types/ system).

use std::collections::HashSet;

use crate::responsibility::graph::ResponsibilityGraph;
use crate::responsibility::types::{
    AgentId, AlertSeverity, BoundaryId, ClaimId, CompetingExplanation, EdgeId, PolicyId,
    ResponsibilityClaim, ResponsibilityClaimState, ResponsibilityClaimType, ResponsibilityEdge,
    ResponsibilityEdgeType,
};

// ═════════════════════════════════════════════════════════════════════════════
// ClaimBuilder
// ═════════════════════════════════════════════════════════════════════════════

/// Builds `ResponsibilityClaim`s by iterating `PolicyBoundaryCrossing` edges
/// in the responsibility graph.
pub struct ClaimBuilder;

impl ClaimBuilder {
    /// Build responsibility claims from `PolicyBoundaryCrossing` edges.
    ///
    /// `claimed_edges` tracks which PBC edges have already produced claims
    /// in previous trigger cycles. Edges in this set are skipped to prevent
    /// duplicate claims and the resulting alert inflation.
    ///
    /// For each UNCLAIMED boundary-crossing edge:
    /// 1. Maps it to a `ResponsibilityClaimType` based on surrounding graph context
    /// 2. Sets `claim_state` from the edge's confidence
    /// 3. Finds competing explanations from `CompetingExplanation` edges
    pub fn build_claims(
        graph: &ResponsibilityGraph,
        claimed_edges: &HashSet<EdgeId>,
    ) -> Vec<ResponsibilityClaim> {
        let boundary_edge_ids: Vec<_> = graph
            .edges_of_type(&ResponsibilityEdgeType::PolicyBoundaryCrossing)
            .iter()
            .filter(|eid| !claimed_edges.contains(eid))
            .cloned()
            .collect();

        let mut claims = Vec::with_capacity(boundary_edge_ids.len());

        for eid in &boundary_edge_ids {
            let Some(edge) = graph.get_edge(eid) else {
                continue;
            };
            let edge = edge.clone();

            // Determine the subject agent from the source node.
            let subject = graph
                .get_node(&edge.source)
                .and_then(|n| n.agent_id.clone())
                .unwrap_or_else(|| AgentId::from("unknown"));

            // Determine claim type from graph context around this edge.
            let claim_type = classify_claim_type(graph, &edge);

            // Map confidence to claim state.
            let claim_state = confidence_to_claim_state(edge.confidence);

            // Derive severity from claim type and confidence.
            let severity = severity_for_claim_type(&claim_type, edge.confidence);

            // Find competing explanations.
            let competing = find_competing_explanations(graph, &edge);

            // Derive policy/boundary IDs from the affected resource node.
            let (policy_id, boundary_id) = derive_policy_ids(graph, &edge);

            // Deterministic ClaimId from composite logical key. Same
            // (subject, policy, boundary, type) always maps to the same
            // id — cross-cycle dedup works without extra tracking.
            let claim_id = ClaimId::from_key(&[
                subject.as_str(),
                policy_id.as_str(),
                boundary_id.as_str(),
                &format!("{:?}", claim_type),
            ]);

            let claim = ResponsibilityClaim {
                claim_id,
                claim_type,
                subject,
                policy_id,
                boundary_id,
                claim_state,
                severity,
                confidence: edge.confidence,
                evidence_refs: edge.evidence_refs.clone(),
                competing_explanations: competing,
                traceback_ref: None, // filled later by TracebackBuilder
                phase: edge.phase.clone(),
            };

            claims.push(claim);
        }

        // Coalesce: merge claims for same (subject, policy, boundary, claim_type)
        // into a single claim with aggregated evidence refs.
        coalesce_claims(&mut claims);

        claims
    }
}

// ═════════════════════════════════════════════════════════════════════════════
// Claim coalescing
// ═════════════════════════════════════════════════════════════════════════════

/// Merge duplicate claims that share the same (subject, policy, boundary, type).
///
/// Multiple PBC edges can target the same resource from the same agent,
/// each generating an individual claim. This collapses them into one claim
/// per unique (agent, policy, boundary, type) tuple, keeping the highest
/// confidence/severity and aggregating all evidence refs.
fn coalesce_claims(claims: &mut Vec<ResponsibilityClaim>) {
    use std::collections::HashMap;

    let mut groups: HashMap<
        (AgentId, PolicyId, BoundaryId, ResponsibilityClaimType),
        (usize, f32, AlertSeverity),
    > = HashMap::new();

    // First pass: find the best claim per group
    for (i, claim) in claims.iter().enumerate() {
        let key = (
            claim.subject.clone(),
            claim.policy_id.clone(),
            claim.boundary_id.clone(),
            claim.claim_type.clone(),
        );
        groups
            .entry(key)
            .and_modify(|(best_idx, best_conf, best_sev)| {
                let sev_rank = |s: &AlertSeverity| match s {
                    AlertSeverity::Critical => 3,
                    AlertSeverity::Warning => 2,
                    AlertSeverity::Info => 1,
                };
                if claim.confidence > *best_conf
                    || (claim.confidence == *best_conf
                        && sev_rank(&claim.severity) > sev_rank(best_sev))
                {
                    *best_idx = i;
                    *best_conf = claim.confidence;
                    *best_sev = claim.severity.clone();
                }
            })
            .or_insert_with(|| (i, claim.confidence, claim.severity.clone()));
    }

    // Second pass: collect all evidence refs into the best claim per group
    let best_indices: HashSet<usize> = groups.values().map(|(i, _, _)| *i).collect();
    let mut merged_claims: Vec<ResponsibilityClaim> = Vec::with_capacity(best_indices.len());

    for (key, (best_idx, _, _)) in groups {
        let mut merged = claims[best_idx].clone();
        // Aggregate all evidence refs from claims in this group
        let all_evidence: Vec<_> = claims
            .iter()
            .filter(|c| {
                (
                    c.subject.clone(),
                    c.policy_id.clone(),
                    c.boundary_id.clone(),
                    c.claim_type.clone(),
                ) == key
            })
            .flat_map(|c| c.evidence_refs.iter().cloned())
            .collect();
        // Deduplicate
        let unique: Vec<_> = {
            let mut seen = HashSet::new();
            all_evidence
                .into_iter()
                .filter(|e| seen.insert(e.clone()))
                .collect()
        };
        merged.evidence_refs = unique;
        merged_claims.push(merged);
    }

    *claims = merged_claims;
}

// ═════════════════════════════════════════════════════════════════════════════
// Claim type classification
// ═════════════════════════════════════════════════════════════════════════════

/// Classify a `PolicyBoundaryCrossing` edge into a `ResponsibilityClaimType`
/// by inspecting the surrounding graph context.
fn classify_claim_type(
    graph: &ResponsibilityGraph,
    edge: &ResponsibilityEdge,
) -> ResponsibilityClaimType {
    // A taint label is only valid when the taint edge is incident to this
    // boundary AND shares the observation that produced the crossing. Without
    // the shared evidence, one taint edge relabels every unrelated boundary
    // crossing of the same two agents (the P1 claim amplification).
    let has_taint = graph
        .edges_of_type(&ResponsibilityEdgeType::TaintPropagatesTo)
        .iter()
        .filter_map(|edge_id| graph.get_edge(edge_id))
        .any(|taint| {
            let incident = taint.source == edge.source
                || taint.target == edge.target
                || taint.source == edge.target
                || taint.target == edge.source;
            incident
                && taint
                    .evidence_refs
                    .iter()
                    .any(|evidence| edge.evidence_refs.contains(evidence))
        });

    if has_taint {
        return ResponsibilityClaimType::TaintedContextUse;
    }

    // Check: does an AgentDelegatesTo edge start from the source?
    let has_delegation = graph
        .edges_of_type(&ResponsibilityEdgeType::AgentDelegatesTo)
        .iter()
        .any(|eid| graph.get_edge(eid).is_some_and(|e| e.source == edge.source));

    if has_delegation {
        return ResponsibilityClaimType::SuspiciousDelegation;
    }

    // Check: is there an IntentCausesAction edge from the source to a
    // resource that matches a capability-boundary resource?
    let has_capability_use = graph
        .edges_of_type(&ResponsibilityEdgeType::IntentCausesAction)
        .iter()
        .any(|eid| {
            graph
                .get_edge(eid)
                .is_some_and(|e| e.source == edge.source && e.target == edge.target)
        });

    if has_capability_use {
        return ResponsibilityClaimType::UnauthorizedCapabilityUse;
    }

    // Low-confidence boundary crossings are treated as blocked attempts.
    if edge.confidence < 0.35 {
        return ResponsibilityClaimType::BlockedAttempt;
    }

    // Default: generic policy violation.
    ResponsibilityClaimType::PolicyViolation
}

// ═════════════════════════════════════════════════════════════════════════════
// Confidence → claim state mapping
// ═════════════════════════════════════════════════════════════════════════════

fn confidence_to_claim_state(confidence: f32) -> ResponsibilityClaimState {
    if confidence.is_nan() {
        return ResponsibilityClaimState::Unproven;
    }
    match confidence {
        c if c >= 0.85 => ResponsibilityClaimState::Confirmed,
        c if c >= 0.70 => ResponsibilityClaimState::Observed,
        c if c >= 0.50 => ResponsibilityClaimState::Inferred,
        c if c >= 0.30 => ResponsibilityClaimState::Ambiguous,
        _ => ResponsibilityClaimState::Unproven,
    }
}

// ═════════════════════════════════════════════════════════════════════════════
// Severity derivation
// ═════════════════════════════════════════════════════════════════════════════

fn severity_for_claim_type(claim_type: &ResponsibilityClaimType, confidence: f32) -> AlertSeverity {
    match claim_type {
        ResponsibilityClaimType::PolicyViolation => {
            if confidence > 0.80 {
                AlertSeverity::Critical
            } else if confidence > 0.50 {
                AlertSeverity::Warning
            } else {
                AlertSeverity::Info
            }
        }
        ResponsibilityClaimType::TaintedContextUse
        | ResponsibilityClaimType::SuspiciousDelegation
        | ResponsibilityClaimType::UnauthorizedCapabilityUse => {
            if confidence > 0.70 {
                AlertSeverity::Critical
            } else {
                AlertSeverity::Warning
            }
        }
        ResponsibilityClaimType::BlockedAttempt => {
            if confidence > 0.60 {
                AlertSeverity::Warning
            } else {
                AlertSeverity::Info
            }
        }
        ResponsibilityClaimType::ResponsibilityAnomaly => {
            if confidence > 0.80 {
                AlertSeverity::Warning
            } else {
                AlertSeverity::Info
            }
        }
    }
}

// ═════════════════════════════════════════════════════════════════════════════
// Competing explanations
// ═════════════════════════════════════════════════════════════════════════════

fn find_competing_explanations(
    graph: &ResponsibilityGraph,
    edge: &ResponsibilityEdge,
) -> Vec<CompetingExplanation> {
    let mut explanations = Vec::new();

    // 1. Explicit CompetingExplanation edges pointing to the same target.
    for comp_eid in graph.edges_of_type(&ResponsibilityEdgeType::CompetingExplanation) {
        if let Some(comp_edge) = graph.get_edge(comp_eid)
            && comp_edge.target == edge.target
        {
            let src_label = graph
                .get_node(&comp_edge.source)
                .and_then(|n| n.agent_id.as_ref())
                .map(|a| a.to_string())
                .unwrap_or_else(|| comp_edge.source.to_string());

            explanations.push(CompetingExplanation {
                label: format!("Competing: {}", src_label),
                evidence_refs: comp_edge.evidence_refs.clone(),
                rule_id: Some("rule.competing_explanation".into()),
                rationale: format!(
                    "Alternative explanation from {} (confidence {:.2})",
                    src_label, comp_edge.confidence
                ),
            });
        }
    }

    // 2. ContextFlowsFrom edges touching the source agent may explain the
    //    boundary crossing as inherited context rather than direct violation.
    if explanations.is_empty() {
        for cf_eid in graph.edges_of_type(&ResponsibilityEdgeType::ContextFlowsFrom) {
            if let Some(cf_edge) = graph.get_edge(cf_eid)
                && (cf_edge.target == edge.source || cf_edge.source == edge.source)
            {
                let other = graph
                    .get_node(if cf_edge.target == edge.source {
                        &cf_edge.source
                    } else {
                        &cf_edge.target
                    })
                    .and_then(|n| n.agent_id.as_ref())
                    .map(|a| a.to_string())
                    .unwrap_or_default();

                if !other.is_empty() {
                    explanations.push(CompetingExplanation {
                        label: format!("Context flow from {}", other),
                        evidence_refs: cf_edge.evidence_refs.clone(),
                        rule_id: Some("rule.context_flow".into()),
                        rationale: format!(
                            "Boundary crossing may be explained by context inherited from agent {}",
                            other
                        ),
                    });
                    break;
                }
            }
        }
    }

    explanations
}

// ═════════════════════════════════════════════════════════════════════════════
// Policy / boundary ID derivation
// ═════════════════════════════════════════════════════════════════════════════

fn derive_policy_ids(
    graph: &ResponsibilityGraph,
    edge: &ResponsibilityEdge,
) -> (PolicyId, BoundaryId) {
    // Derive from the target node's resource type.
    if let Some(target_node) = graph.get_node(&edge.target)
        && let Some(ref resource) = target_node.resource
    {
        let (category, detail) = resource_summary(resource);
        let policy_id = PolicyId::from(format!("policy.{}.access", category));
        let boundary_id = BoundaryId::from(format!("boundary.{}.{}", category, detail));
        return (policy_id, boundary_id);
    }

    // Fallback: use the edge ID for derivation.
    let fallback = edge.edge_id.as_str().trim_start_matches("edge:");
    (
        PolicyId::from(format!("policy.{}", fallback)),
        BoundaryId::from(format!("boundary.{}", fallback)),
    )
}

/// Lightweight resource summary for policy naming.
fn resource_summary(
    resource: &crate::responsibility::types::NormalizedResource,
) -> (&'static str, String) {
    match resource {
        crate::responsibility::types::NormalizedResource::File { canonical_path } => {
            // Show last 2 path components for readability (dir/filename)
            let p = std::path::Path::new(canonical_path);
            let parent = p
                .parent()
                .and_then(|d| d.file_name())
                .and_then(|n| n.to_str())
                .unwrap_or("");
            let fname = p
                .file_name()
                .and_then(|n| n.to_str())
                .unwrap_or(canonical_path);
            let detail = if parent.is_empty() || parent == "/" {
                fname.to_string()
            } else {
                format!("{}/{}", parent, fname)
            };
            ("file", detail)
        }
        crate::responsibility::types::NormalizedResource::Network { host, port, .. } => {
            ("network", format!("{}:{}", host, port))
        }
        crate::responsibility::types::NormalizedResource::Process { .. } => {
            ("process", "exec".into())
        }
        crate::responsibility::types::NormalizedResource::LlmConversation { provider, .. } => {
            ("llm", provider.clone())
        }
        crate::responsibility::types::NormalizedResource::ToolCall { tool_name, .. } => {
            ("tool", tool_name.clone())
        }
        crate::responsibility::types::NormalizedResource::Credential { .. } => {
            ("credential", "access".into())
        }
        crate::responsibility::types::NormalizedResource::Context { .. } => {
            ("context", "inheritance".into())
        }
    }
}

// ═════════════════════════════════════════════════════════════════════════════
// Tests
// ═════════════════════════════════════════════════════════════════════════════

#[cfg(test)]
mod tests {
    use super::*;
    use crate::responsibility::graph::ResponsibilityGraph;
    use crate::responsibility::types::{EvidenceId, PhaseIndicator};

    fn make_agent_node(
        id: &str,
        agent_id: &str,
    ) -> crate::responsibility::types::ResponsibilityNode {
        crate::responsibility::types::ResponsibilityNode {
            node_id: crate::responsibility::types::NodeId::from(id),
            node_type: crate::responsibility::types::ResponsibilityNodeType::Agent,
            agent_id: Some(crate::responsibility::types::AgentId::from(agent_id)),
            resource: None,
            evidence_refs: vec![],
            timestamp_ns: 1000,
            phase: PhaseIndicator::Provisional { timestamp_ns: 1000 },
        }
    }

    fn make_boundary_node(id: &str) -> crate::responsibility::types::ResponsibilityNode {
        crate::responsibility::types::ResponsibilityNode {
            node_id: crate::responsibility::types::NodeId::from(id),
            node_type: crate::responsibility::types::ResponsibilityNodeType::PolicyBoundary,
            agent_id: None,
            resource: None,
            evidence_refs: vec![],
            timestamp_ns: 1000,
            phase: PhaseIndicator::Provisional { timestamp_ns: 1000 },
        }
    }

    fn make_edge(
        id: &str,
        edge_type: ResponsibilityEdgeType,
        source: &str,
        target: &str,
        confidence: f32,
    ) -> ResponsibilityEdge {
        ResponsibilityEdge {
            edge_id: crate::responsibility::types::EdgeId::from(id),
            edge_type,
            source: crate::responsibility::types::NodeId::from(source),
            target: crate::responsibility::types::NodeId::from(target),
            evidence_refs: vec![],
            confidence,
            phase: PhaseIndicator::Provisional { timestamp_ns: 1000 },
        }
    }

    #[test]
    fn test_empty_graph_produces_no_claims() {
        let g = ResponsibilityGraph::default();
        let claims = ClaimBuilder::build_claims(&g, &HashSet::new());
        assert!(claims.is_empty());
    }

    #[test]
    fn test_single_boundary_crossing_produces_claim() {
        let mut g = ResponsibilityGraph::default();
        g.add_node(make_agent_node("agent1", "agent-aaa"));
        g.add_node(make_boundary_node("boundary1"));
        g.add_edge(make_edge(
            "edge1",
            ResponsibilityEdgeType::PolicyBoundaryCrossing,
            "agent1",
            "boundary1",
            0.75,
        ));

        let claims = ClaimBuilder::build_claims(&g, &HashSet::new());
        assert_eq!(claims.len(), 1);
        assert_eq!(claims[0].subject, AgentId::from("agent-aaa"));
        assert_eq!(claims[0].claim_state, ResponsibilityClaimState::Observed);
        // Without taint/delegation context, defaults to PolicyViolation.
        assert!(matches!(
            claims[0].claim_type,
            ResponsibilityClaimType::PolicyViolation
        ));
    }

    #[test]
    fn test_confidence_to_state_mapping() {
        assert_eq!(
            confidence_to_claim_state(0.90),
            ResponsibilityClaimState::Confirmed
        );
        assert_eq!(
            confidence_to_claim_state(0.75),
            ResponsibilityClaimState::Observed
        );
        assert_eq!(
            confidence_to_claim_state(0.55),
            ResponsibilityClaimState::Inferred
        );
        assert_eq!(
            confidence_to_claim_state(0.35),
            ResponsibilityClaimState::Ambiguous
        );
        assert_eq!(
            confidence_to_claim_state(0.10),
            ResponsibilityClaimState::Unproven
        );
    }

    #[test]
    fn test_tainted_context_detection() {
        let mut g = ResponsibilityGraph::default();
        g.add_node(make_agent_node("agent1", "agent-a"));
        g.add_node(make_boundary_node("boundary1"));
        let evidence = EvidenceId::from("ev-shared");
        let mut pb = make_edge(
            "edge_pb",
            ResponsibilityEdgeType::PolicyBoundaryCrossing,
            "agent1",
            "boundary1",
            0.80,
        );
        pb.evidence_refs = vec![evidence.clone()];
        g.add_edge(pb);
        let mut taint = make_edge(
            "edge_taint",
            ResponsibilityEdgeType::TaintPropagatesTo,
            "agent1",
            "boundary1",
            0.70,
        );
        taint.evidence_refs = vec![evidence];
        g.add_edge(taint);

        let claims = ClaimBuilder::build_claims(&g, &HashSet::new());
        assert_eq!(claims.len(), 1);
        assert!(matches!(
            claims[0].claim_type,
            ResponsibilityClaimType::TaintedContextUse
        ));
    }

    #[test]
    fn test_taint_claim_requires_shared_evidence() {
        let mut g = ResponsibilityGraph::default();
        g.add_node(make_agent_node("agent1", "agent-a"));
        g.add_node(make_boundary_node("boundary1"));
        g.add_node(make_boundary_node("boundary2"));

        let mut pb1 = make_edge(
            "edge_pb1",
            ResponsibilityEdgeType::PolicyBoundaryCrossing,
            "agent1",
            "boundary1",
            0.80,
        );
        pb1.evidence_refs = vec![EvidenceId::from("ev-1")];
        g.add_edge(pb1);
        let mut pb2 = make_edge(
            "edge_pb2",
            ResponsibilityEdgeType::PolicyBoundaryCrossing,
            "agent1",
            "boundary2",
            0.80,
        );
        pb2.evidence_refs = vec![EvidenceId::from("ev-2")];
        g.add_edge(pb2);
        // The taint edge shares no evidence with either crossing.
        let mut taint = make_edge(
            "edge_taint",
            ResponsibilityEdgeType::TaintPropagatesTo,
            "agent1",
            "boundary1",
            0.70,
        );
        taint.evidence_refs = vec![EvidenceId::from("ev-3")];
        g.add_edge(taint);

        let claims = ClaimBuilder::build_claims(&g, &HashSet::new());
        assert_eq!(claims.len(), 2);
        assert!(
            claims
                .iter()
                .all(|claim| matches!(claim.claim_type, ResponsibilityClaimType::PolicyViolation))
        );
    }

    #[test]
    fn test_blocked_attempt_for_low_confidence() {
        let mut g = ResponsibilityGraph::default();
        g.add_node(make_agent_node("agent1", "agent-a"));
        g.add_node(make_boundary_node("boundary1"));
        g.add_edge(make_edge(
            "edge_low",
            ResponsibilityEdgeType::PolicyBoundaryCrossing,
            "agent1",
            "boundary1",
            0.25,
        ));

        let claims = ClaimBuilder::build_claims(&g, &HashSet::new());
        assert_eq!(claims.len(), 1);
        assert!(matches!(
            claims[0].claim_type,
            ResponsibilityClaimType::BlockedAttempt
        ));
        assert_eq!(claims[0].claim_state, ResponsibilityClaimState::Unproven);
    }
}
