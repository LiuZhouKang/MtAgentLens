// SPDX-License-Identifier: Apache-2.0 OR MIT
//
// traceback.rs — TracebackBuilder: reverse-edge traversal from a
// ResponsibilityClaim through the ResponsibilityGraph to produce a
// ResponsibilityTraceback (new types/ system).

use crate::responsibility::graph::ResponsibilityGraph;
use crate::responsibility::types::{
    EdgeId, NodeId, ResponsibilityClaim, ResponsibilityClaimType, ResponsibilityEdgeType,
    ResponsibilityTraceback, TracebackId,
};
use sha2::{Digest, Sha256};

// ═════════════════════════════════════════════════════════════════════════════
// TracebackBuilder
// ═════════════════════════════════════════════════════════════════════════════

/// Collects a bounded evidence subgraph for a claim and an inferred root.
/// The legacy `chain_*_ids` fields contain membership, not an ordered path:
/// actions can be parallel branches from the same actor.
/// Common bound for the legacy traceback subgraph and the attribution node set.
pub(crate) const MAX_CHAIN_NODES: usize = 64;

pub struct TracebackBuilder;

impl TracebackBuilder {
    /// Build a traceback for the given claim.
    ///
    /// 1. Find the `PolicyBoundaryCrossing` edge associated with this claim
    ///    by looking for an edge whose source matches the claim's subject agent
    ///    and whose evidence refs overlap.
    /// 2. Collect evidence-backed actions and observed upstream relationships.
    /// 3. Retain their actual endpoints and describe each edge independently.
    pub fn build(
        claim: &ResponsibilityClaim,
        graph: &ResponsibilityGraph,
    ) -> Option<ResponsibilityTraceback> {
        // Find the policy boundary crossing edge for this claim.
        let boundary_edge = find_boundary_edge_for_claim(claim, graph)?;

        // These IDs form a subgraph, with independent action branches and
        // upstream delegations. Their vector order does not establish a path.
        const MAX_ACTION_BRANCHES: usize = 8;
        const MAX_UPSTREAM_DEPTH: usize = 10;

        let agent_node_id = boundary_edge.source.clone();
        let mut chain_nodes: Vec<NodeId> = vec![agent_node_id.clone()];
        let mut chain_edges: Vec<EdgeId> = Vec::new();
        let mut seen_nodes: std::collections::HashSet<NodeId> =
            std::iter::once(agent_node_id.clone()).collect();

        // Use only actions whose evidence this claim actually cites. The old
        // fallback appended up to eight unrelated actions, so "chain depth"
        // measured how busy the agent was, not causality. An unbacked claim
        // keeps the agent→boundary edge and reports a short chain; that is
        // the honest reading of the evidence.
        let mut actions: Vec<crate::responsibility::types::ResponsibilityEdge> = graph
            .outgoing_from(&agent_node_id)
            .iter()
            .filter_map(|edge_id| graph.get_edge(edge_id))
            .filter(|edge| edge.edge_type == ResponsibilityEdgeType::IntentCausesAction)
            .filter(|edge| {
                edge.evidence_refs
                    .iter()
                    .any(|evidence| claim.evidence_refs.contains(evidence))
            })
            .cloned()
            .collect();
        actions.sort_by(|a, b| a.edge_id.as_str().cmp(b.edge_id.as_str()));
        actions.truncate(MAX_ACTION_BRANCHES);
        for edge in actions {
            if seen_nodes.insert(edge.target.clone()) {
                chain_nodes.push(edge.target.clone());
            }
            chain_edges.push(edge.edge_id.clone());
        }

        // Append the PolicyBoundary node (target of PBC edge).
        if seen_nodes.insert(boundary_edge.target.clone()) {
            chain_nodes.push(boundary_edge.target.clone());
        }
        chain_edges.push(boundary_edge.edge_id.clone());

        // Prefer the external-content origin for a provenance boundary;
        // the actor that executed the derived call remains an executor.
        let provenance_root = graph
            .get_node(&boundary_edge.target)
            .and_then(|node| node.resource.as_ref())
            .and_then(|resource| match resource {
                crate::responsibility::types::NormalizedResource::ToolCall {
                    tool_name,
                    call_id,
                } if tool_name == "UntrustedInstructionFlow" => Some(call_id.clone()),
                _ => None,
            });

        // For tainted-context claims, prefer the observed contamination
        // source over the downstream reader as the root candidate.
        let mut root_cause_node_id = provenance_root
            .as_ref()
            .map(|_| boundary_edge.target.clone())
            .unwrap_or_else(|| agent_node_id.clone());
        let mut has_specific_origin = provenance_root.is_some();
        if claim.claim_type == ResponsibilityClaimType::TaintedContextUse {
            let taint = graph
                .incoming_to(&agent_node_id)
                .iter()
                .filter_map(|edge_id| graph.get_edge(edge_id))
                .find(|edge| edge.edge_type == ResponsibilityEdgeType::TaintPropagatesTo)
                .cloned()
                .or_else(|| {
                    graph
                        .incoming_to(&boundary_edge.target)
                        .iter()
                        .filter_map(|edge_id| graph.get_edge(edge_id))
                        .find(|edge| edge.edge_type == ResponsibilityEdgeType::TaintPropagatesTo)
                        .cloned()
                });
            if let Some(edge) = taint {
                if seen_nodes.insert(edge.source.clone()) {
                    chain_nodes.insert(0, edge.source.clone());
                }
                if !chain_edges.contains(&edge.edge_id) {
                    chain_edges.insert(0, edge.edge_id.clone());
                }
                root_cause_node_id = edge.source;
                has_specific_origin = true;
            }
        }

        // Retain upstream delegation context. It supplies a fallback root
        // only when no specific provenance/taint origin has been identified.
        let mut current = agent_node_id.clone();
        let mut traversed = std::collections::HashSet::from([current.clone()]);
        for _ in 0..MAX_UPSTREAM_DEPTH {
            let parent = graph
                .incoming_to(&current)
                .iter()
                .filter_map(|edge_id| graph.get_edge(edge_id))
                .find(|edge| edge.edge_type == ResponsibilityEdgeType::AgentDelegatesTo)
                .cloned();
            let Some(edge) = parent else { break };
            if !chain_edges.contains(&edge.edge_id) {
                chain_edges.push(edge.edge_id.clone());
            }
            if !traversed.insert(edge.source.clone()) {
                break;
            }
            if seen_nodes.insert(edge.source.clone()) {
                chain_nodes.push(edge.source.clone());
            }
            if !has_specific_origin {
                root_cause_node_id = edge.source.clone();
            }
            current = edge.source;
        }

        chain_nodes.truncate(MAX_CHAIN_NODES);
        chain_edges.truncate(MAX_CHAIN_NODES);

        // Generate explanation summary.
        let explanation_summary =
            generate_explanation(graph, claim, &chain_edges, &root_cause_node_id);

        // Compute chain confidence from the collected edges.
        let chain_confidence = compute_chain_confidence(graph, &chain_edges);

        // A replay of the same evidence revision must reference the record
        // produced live, rather than mint an unresolvable fresh UUID.
        let mut node_members: Vec<_> = chain_nodes.iter().map(NodeId::as_str).collect();
        let mut edge_members: Vec<_> = chain_edges.iter().map(EdgeId::as_str).collect();
        node_members.sort_unstable();
        edge_members.sort_unstable();
        let reference = serde_json::json!({
            "contract": "traceback-evidence-reference/1",
            "claim": claim.claim_id,
            "phase": claim.phase,
            "root_candidate": root_cause_node_id,
            "nodes": node_members,
            "edges": edge_members,
        });
        let traceback_id = TracebackId::from(format!(
            "traceback-{}",
            hex::encode(Sha256::digest(reference.to_string().as_bytes()))
        ));
        Some(ResponsibilityTraceback {
            traceback_id,
            claim_id: claim.claim_id.clone(),
            chain_node_ids: chain_nodes,
            chain_edge_ids: chain_edges,
            root_cause_node_id,
            explanation_summary,
            confidence: chain_confidence,
        })
    }

    /// Build tracebacks for every claim and bind each claim to the exact
    /// traceback that was emitted for it. A claim whose boundary cannot be
    /// resolved keeps no stale reference.
    pub fn build_and_attach(
        claims: &mut [ResponsibilityClaim],
        graph: &ResponsibilityGraph,
    ) -> Vec<ResponsibilityTraceback> {
        let mut tracebacks = Vec::new();
        for claim in claims.iter_mut() {
            match Self::build(claim, graph) {
                Some(traceback) => {
                    claim.traceback_ref = Some(traceback.traceback_id.clone());
                    tracebacks.push(traceback);
                }
                None => claim.traceback_ref = None,
            }
        }
        tracebacks
    }
}

// ═════════════════════════════════════════════════════════════════════════════
// Boundary edge finder
// ═════════════════════════════════════════════════════════════════════════════

/// Restrict crossings to the claim's subject and prefer a uniquely supported
/// evidence overlap. An ambiguous tie does not establish a traceback.
fn find_boundary_edge_for_claim(
    claim: &ResponsibilityClaim,
    graph: &ResponsibilityGraph,
) -> Option<crate::responsibility::types::ResponsibilityEdge> {
    // Collect all valid PBC edges from the graph (skip any stale index entries).
    let pbc_edges: Vec<&crate::responsibility::types::ResponsibilityEdge> = graph
        .edges_of_type(&ResponsibilityEdgeType::PolicyBoundaryCrossing)
        .iter()
        .filter_map(|eid| graph.get_edge(eid))
        .collect();

    if pbc_edges.is_empty() {
        return None;
    }

    let mut best: Option<&crate::responsibility::types::ResponsibilityEdge> = None;
    let mut best_score: Option<usize> = None;
    let mut tied = false;

    for edge in &pbc_edges {
        if graph
            .get_node(&edge.source)
            .and_then(|node| node.agent_id.as_ref())
            != Some(&claim.subject)
        {
            continue;
        }
        let overlap: std::collections::HashSet<_> = edge
            .evidence_refs
            .iter()
            .filter(|reference| claim.evidence_refs.contains(reference))
            .collect();
        let score = overlap.len();
        if best_score.is_none_or(|previous| score > previous) {
            best_score = Some(score);
            best = Some(edge);
            tied = false;
        } else if best_score == Some(score) {
            tied = true;
        }
    }

    // No fallback to an arbitrary edge. Returning the first PBC in graph order
    // attached a traceback for agent A to a claim about agent B whenever the
    // claim's own edge could not be found; an unattributed claim must say so.
    if tied { None } else { best.cloned() }
}

// ═════════════════════════════════════════════════════════════════════════════
// Explanation summary
// ═════════════════════════════════════════════════════════════════════════════

fn generate_explanation(
    graph: &ResponsibilityGraph,
    claim: &ResponsibilityClaim,
    chain_edges: &[EdgeId],
    root_cause_id: &NodeId,
) -> String {
    let mut lines: Vec<String> = Vec::new();

    // Claim header.
    let claim_type_label = match claim.claim_type {
        crate::responsibility::types::ResponsibilityClaimType::PolicyViolation => {
            "Policy Violation"
        }
        crate::responsibility::types::ResponsibilityClaimType::ResponsibilityAnomaly => {
            "Responsibility Anomaly"
        }
        crate::responsibility::types::ResponsibilityClaimType::SuspiciousDelegation => {
            "Suspicious Delegation"
        }
        crate::responsibility::types::ResponsibilityClaimType::TaintedContextUse => {
            "Tainted Context Use"
        }
        crate::responsibility::types::ResponsibilityClaimType::UnauthorizedCapabilityUse => {
            "Unauthorized Capability Use"
        }
        crate::responsibility::types::ResponsibilityClaimType::BlockedAttempt => "Blocked Attempt",
    };

    lines.push(format!(
        "Traceback for claim {}: {} (confidence {:.0}%)",
        claim.claim_id,
        claim_type_label,
        claim.confidence * 100.0,
    ));

    // Do not infer adjacency from parallel vectors: report real endpoints.
    lines.push("Evidence relationships:".to_string());
    for edge_id in chain_edges {
        if let Some(edge) = graph.get_edge(edge_id) {
            lines.push(format!(
                "  {} {} {}",
                node_label_for_display(graph, &edge.source),
                edge_label_for_display(graph, edge_id),
                node_label_for_display(graph, &edge.target),
            ));
        }
    }

    // Root cause.
    let root_label = node_label_for_display(graph, root_cause_id);
    lines.push(format!("Root cause candidate: {}", root_label));

    // Evidence summary.
    if !claim.evidence_refs.is_empty() {
        lines.push(format!(
            "Supporting evidence: {} record(s)",
            claim.evidence_refs.len()
        ));
    }

    // Competing explanations.
    if !claim.competing_explanations.is_empty() {
        lines.push("Competing explanations:".to_string());
        for comp in &claim.competing_explanations {
            lines.push(format!("  - {}: {}", comp.label, comp.rationale));
        }
    }

    lines.join("\n")
}

fn node_label_for_display(graph: &ResponsibilityGraph, node_id: &NodeId) -> String {
    match graph.get_node(node_id) {
        Some(node) => {
            if let Some(ref agent) = node.agent_id {
                format!("Agent({})", truncate_id(agent.as_str(), 12))
            } else if let Some(ref resource) = node.resource {
                format!("Resource({:?})", resource)
            } else {
                format!(
                    "{:?}({})",
                    node.node_type,
                    truncate_id(node_id.as_str(), 12)
                )
            }
        }
        None => format!("<unknown:{}>", truncate_id(node_id.as_str(), 12)),
    }
}

fn edge_label_for_display(graph: &ResponsibilityGraph, edge_id: &EdgeId) -> String {
    match graph.get_edge(edge_id) {
        Some(edge) => match edge.edge_type {
            ResponsibilityEdgeType::IntentCausesAction => "action link→".to_string(),
            ResponsibilityEdgeType::AgentDelegatesTo => "delegated to→".to_string(),
            ResponsibilityEdgeType::ContextFlowsFrom => "context flowed→".to_string(),
            ResponsibilityEdgeType::TaintPropagatesTo => "tainted→".to_string(),
            ResponsibilityEdgeType::PolicyBoundaryCrossing => "crossed boundary→".to_string(),
            ResponsibilityEdgeType::CompetingExplanation => "competing→".to_string(),
            _ => "→".to_string(),
        },
        None => "→".to_string(),
    }
}

// ═════════════════════════════════════════════════════════════════════════════
// Chain confidence
// ═════════════════════════════════════════════════════════════════════════════

/// Edge-product heuristic for the collected subgraph, not a calibrated
/// probability or a causal-path correctness estimate. Each edge counts once.
fn compute_chain_confidence(graph: &ResponsibilityGraph, chain_edges: &[EdgeId]) -> f32 {
    let mut product = 1.0;
    let mut seen = std::collections::HashSet::new();
    for eid in chain_edges {
        if seen.insert(eid)
            && let Some(edge) = graph.get_edge(eid)
        {
            product *= edge.confidence;
        }
    }
    // Clamp to [0, 1].
    product.clamp(0.0, 1.0)
}

// ═════════════════════════════════════════════════════════════════════════════
// Helpers
// ═════════════════════════════════════════════════════════════════════════════

fn truncate_id(s: &str, max_len: usize) -> &str {
    if s.len() > max_len { &s[..max_len] } else { s }
}

// ═════════════════════════════════════════════════════════════════════════════
// Tests
// ═════════════════════════════════════════════════════════════════════════════

#[cfg(test)]
mod tests {
    use super::*;
    use crate::responsibility::types::{
        AgentId, AlertSeverity, BoundaryId, ClaimId, PhaseIndicator, PolicyId,
        ResponsibilityClaimState, ResponsibilityClaimType, ResponsibilityEdge,
        ResponsibilityNodeType,
    };

    fn make_agent_node(id: &str, agent: &str) -> crate::responsibility::types::ResponsibilityNode {
        crate::responsibility::types::ResponsibilityNode {
            node_id: NodeId::from(id),
            node_type: ResponsibilityNodeType::Agent,
            agent_id: Some(AgentId::from(agent)),
            resource: None,
            evidence_refs: vec![],
            timestamp_ns: 1000,
            phase: PhaseIndicator::Provisional { timestamp_ns: 1000 },
        }
    }

    fn make_resource_node(id: &str) -> crate::responsibility::types::ResponsibilityNode {
        crate::responsibility::types::ResponsibilityNode {
            node_id: NodeId::from(id),
            node_type: ResponsibilityNodeType::Resource,
            agent_id: None,
            resource: Some(crate::responsibility::types::NormalizedResource::File {
                canonical_path: "/tmp/test".into(),
            }),
            evidence_refs: vec![],
            timestamp_ns: 1000,
            phase: PhaseIndicator::Provisional { timestamp_ns: 1000 },
        }
    }

    fn make_boundary_node(id: &str) -> crate::responsibility::types::ResponsibilityNode {
        crate::responsibility::types::ResponsibilityNode {
            node_id: NodeId::from(id),
            node_type: ResponsibilityNodeType::PolicyBoundary,
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
            edge_id: EdgeId::from(id),
            edge_type,
            operation: None,
            source: NodeId::from(source),
            target: NodeId::from(target),
            evidence_refs: vec![],
            confidence,
            phase: PhaseIndicator::Provisional { timestamp_ns: 1000 },
        }
    }

    fn make_claim(
        subject: &str,
        evidence_refs: Vec<crate::responsibility::types::EvidenceId>,
    ) -> ResponsibilityClaim {
        ResponsibilityClaim {
            claim_id: ClaimId::new(),
            claim_type: ResponsibilityClaimType::PolicyViolation,
            subject: AgentId::from(subject),
            policy_id: PolicyId::from("policy.test"),
            boundary_id: BoundaryId::from("boundary.test"),
            claim_state: ResponsibilityClaimState::Observed,
            severity: AlertSeverity::Warning,
            confidence: 0.80,
            evidence_refs,
            competing_explanations: vec![],
            traceback_ref: None,
            phase: PhaseIndicator::Provisional { timestamp_ns: 1000 },
        }
    }

    #[test]
    fn test_traceback_simple_chain() {
        let mut g = ResponsibilityGraph::default();
        // agent → resource (via IntentCausesAction)
        // agent → boundary (via PolicyBoundaryCrossing, per spec §10.2)
        g.add_node(make_agent_node("na", "agent-a"));
        g.add_node(make_resource_node("nr"));
        g.add_node(make_boundary_node("nb"));

        g.add_edge(make_edge(
            "e_ica",
            ResponsibilityEdgeType::IntentCausesAction,
            "na",
            "nr",
            0.90,
        ));
        g.add_edge(make_edge(
            "e_pbc",
            ResponsibilityEdgeType::PolicyBoundaryCrossing,
            "na",
            "nb",
            0.85,
        ));

        let claim = make_claim("agent-a", vec![]);

        let traceback = TracebackBuilder::build(&claim, &g);
        assert!(traceback.is_some());
        let tb = traceback.unwrap();
        assert_eq!(tb.claim_id, claim.claim_id);
        // The chain should include the boundary edge target and backward path.
        assert!(!tb.chain_node_ids.is_empty());
        assert!(!tb.chain_edge_ids.is_empty());
        // Root cause should be the agent node.
        assert_eq!(tb.root_cause_node_id, NodeId::from("na"));
    }

    #[test]
    fn test_traceback_no_boundary_edge() {
        let g = ResponsibilityGraph::default();
        let claim = make_claim("agent-x", vec![]);

        let traceback = TracebackBuilder::build(&claim, &g);
        assert!(traceback.is_none());
    }

    #[test]
    fn build_and_attach_sets_the_exact_emitted_traceback_id() {
        let mut graph = ResponsibilityGraph::default();
        graph.add_node(make_agent_node("agent", "agent-a"));
        graph.add_node(make_boundary_node("boundary"));
        graph.add_edge(make_edge(
            "pbc",
            ResponsibilityEdgeType::PolicyBoundaryCrossing,
            "agent",
            "boundary",
            0.8,
        ));
        let mut claims = vec![make_claim("agent-a", vec![])];

        let tracebacks = TracebackBuilder::build_and_attach(&mut claims, &graph);

        assert_eq!(tracebacks.len(), 1);
        assert_eq!(
            claims[0].traceback_ref,
            Some(tracebacks[0].traceback_id.clone())
        );
        assert_eq!(tracebacks[0].claim_id, claims[0].claim_id);
    }

    #[test]
    fn build_and_attach_clears_stale_ref_for_unsupported_or_ambiguous_boundaries() {
        let mut unsupported = make_claim("agent-a", vec![]);
        unsupported.traceback_ref = Some(TracebackId::new());
        let emitted = TracebackBuilder::build_and_attach(
            std::slice::from_mut(&mut unsupported),
            &ResponsibilityGraph::default(),
        );
        assert!(emitted.is_empty());
        assert!(unsupported.traceback_ref.is_none());

        let mut graph = ResponsibilityGraph::default();
        graph.add_node(make_agent_node("agent", "agent-a"));
        for id in ["first", "second"] {
            graph.add_node(make_boundary_node(id));
            graph.add_edge(make_edge(
                id,
                ResponsibilityEdgeType::PolicyBoundaryCrossing,
                "agent",
                id,
                0.9,
            ));
        }
        let mut ambiguous = make_claim("agent-a", vec![]);
        ambiguous.traceback_ref = Some(TracebackId::new());
        let emitted =
            TracebackBuilder::build_and_attach(std::slice::from_mut(&mut ambiguous), &graph);
        assert!(emitted.is_empty());
        assert!(ambiguous.traceback_ref.is_none());
    }

    #[test]
    fn test_traceback_explanation_summary() {
        let mut g = ResponsibilityGraph::default();
        g.add_node(make_agent_node("na", "agent-root"));
        g.add_node(make_resource_node("nr"));
        g.add_node(make_boundary_node("nb"));

        g.add_edge(make_edge(
            "e1",
            ResponsibilityEdgeType::IntentCausesAction,
            "na",
            "nr",
            0.90,
        ));
        g.add_edge(make_edge(
            "e2",
            ResponsibilityEdgeType::PolicyBoundaryCrossing,
            "na",
            "nb",
            0.80,
        ));

        let claim = make_claim("agent-root", vec![]);

        let traceback = TracebackBuilder::build(&claim, &g).unwrap();
        assert!(
            traceback
                .explanation_summary
                .contains("Traceback for claim")
        );
        assert!(
            traceback
                .explanation_summary
                .contains("Evidence relationships")
        );
        assert!(traceback.explanation_summary.contains("Root cause"));
        assert!(traceback.explanation_summary.contains("Agent(agent-root)"));
    }

    #[test]
    fn parallel_actions_are_explained_with_their_actual_endpoints() {
        let mut graph = ResponsibilityGraph::default();
        graph.add_node(make_agent_node("agent", "agent-a"));
        graph.add_node(make_boundary_node("boundary"));
        for (id, path) in [("first", "/tmp/first"), ("second", "/tmp/second")] {
            let mut node = make_resource_node(id);
            node.resource = Some(crate::responsibility::types::NormalizedResource::File {
                canonical_path: path.into(),
            });
            graph.add_node(node);
            let mut edge = make_edge(
                id,
                ResponsibilityEdgeType::IntentCausesAction,
                "agent",
                id,
                0.9,
            );
            edge.evidence_refs = vec![crate::responsibility::types::EvidenceId::from("ev")];
            graph.add_edge(edge);
        }
        graph.add_edge(make_edge(
            "pbc",
            ResponsibilityEdgeType::PolicyBoundaryCrossing,
            "agent",
            "boundary",
            0.8,
        ));
        let claim = make_claim(
            "agent-a",
            vec![crate::responsibility::types::EvidenceId::from("ev")],
        );
        let traceback = TracebackBuilder::build(&claim, &graph).unwrap();
        let summary = &traceback.explanation_summary;
        assert!(summary.contains("Evidence relationships:"), "{summary}");
        assert!(
            !summary.contains("Causal chain:"),
            "a branch is not a path: {summary}"
        );
        for path in ["/tmp/first", "/tmp/second"] {
            assert!(
                summary
                    .lines()
                    .any(|line| line.contains("Agent(agent-a)") && line.contains(path)),
                "each relationship must name its source and target: {summary}"
            );
        }
    }

    #[test]
    fn boundary_confidence_is_not_counted_twice() {
        let mut graph = ResponsibilityGraph::default();
        graph.add_node(make_agent_node("agent", "agent-a"));
        graph.add_node(make_boundary_node("boundary"));
        graph.add_edge(make_edge(
            "pbc",
            ResponsibilityEdgeType::PolicyBoundaryCrossing,
            "agent",
            "boundary",
            0.8,
        ));
        let traceback = TracebackBuilder::build(&make_claim("agent-a", vec![]), &graph).unwrap();
        assert!((traceback.confidence - 0.8).abs() < f32::EPSILON);
    }

    #[test]
    fn shared_evidence_cannot_attach_another_actors_boundary() {
        let mut graph = ResponsibilityGraph::default();
        graph.add_node(make_agent_node("a", "actor-a"));
        graph.add_node(make_agent_node("b", "actor-b"));
        graph.add_node(make_boundary_node("boundary-a"));
        graph.add_node(make_boundary_node("boundary-b"));
        graph.add_edge(make_edge(
            "pbc-a",
            ResponsibilityEdgeType::PolicyBoundaryCrossing,
            "a",
            "boundary-a",
            0.8,
        ));
        let evidence: Vec<_> = ["ev-1", "ev-2", "ev-3"]
            .into_iter()
            .map(crate::responsibility::types::EvidenceId::from)
            .collect();
        let mut other = make_edge(
            "pbc-b",
            ResponsibilityEdgeType::PolicyBoundaryCrossing,
            "b",
            "boundary-b",
            0.9,
        );
        other.evidence_refs = evidence.clone();
        graph.add_edge(other);
        let traceback = TracebackBuilder::build(&make_claim("actor-a", evidence), &graph).unwrap();
        assert_eq!(traceback.root_cause_node_id, NodeId::from("a"));
        assert!(!traceback.chain_edge_ids.contains(&EdgeId::from("pbc-b")));
    }

    #[test]
    fn tied_boundary_candidates_do_not_depend_on_graph_iteration_order() {
        let mut graph = ResponsibilityGraph::default();
        graph.add_node(make_agent_node("a", "actor-a"));
        for id in ["first", "second"] {
            graph.add_node(make_boundary_node(id));
            graph.add_edge(make_edge(
                id,
                ResponsibilityEdgeType::PolicyBoundaryCrossing,
                "a",
                id,
                0.9,
            ));
        }
        assert!(
            TracebackBuilder::build(&make_claim("actor-a", vec![]), &graph).is_none(),
            "ambiguous boundaries must remain unresolved"
        );
    }

    #[test]
    fn test_find_root_cause_deepest_agent() {
        let mut g = ResponsibilityGraph::default();
        g.add_node(make_agent_node("agent_deep", "deep-agent"));
        g.add_node(make_resource_node("res1"));
        g.add_node(make_agent_node("agent_shallow", "shallow-agent"));
        g.add_node(make_resource_node("res2"));
        g.add_node(make_boundary_node("bnd"));

        // Build chain: agent_shallow crosses boundary (PBC) after performing
        // an action (IntentCausesAction to res1).
        g.add_edge(make_edge(
            "e1",
            ResponsibilityEdgeType::IntentCausesAction,
            "agent_shallow",
            "res1",
            0.85,
        ));
        g.add_edge(make_edge(
            "e2",
            ResponsibilityEdgeType::PolicyBoundaryCrossing,
            "agent_shallow",
            "bnd",
            0.75,
        ));

        let claim = make_claim("shallow-agent", vec![]);

        let traceback = TracebackBuilder::build(&claim, &g).unwrap();
        // Root cause is the agent that crossed the boundary (agent_shallow).
        assert_eq!(traceback.root_cause_node_id, NodeId::from("agent_shallow"));
    }

    #[test]
    fn provenance_boundary_is_the_root_cause() {
        use crate::responsibility::types::NormalizedResource;

        let mut g = ResponsibilityGraph::default();
        g.add_node(make_agent_node("a", "agent-a"));
        let mut boundary = make_boundary_node("p");
        boundary.resource = Some(NormalizedResource::ToolCall {
            tool_name: "UntrustedInstructionFlow".into(),
            call_id: "span-1".into(),
        });
        g.add_node(boundary);
        g.add_edge(make_edge(
            "pbc",
            ResponsibilityEdgeType::PolicyBoundaryCrossing,
            "a",
            "p",
            0.85,
        ));

        g.add_node(make_agent_node("upstream", "agent-upstream"));
        g.add_edge(make_edge(
            "delegate",
            ResponsibilityEdgeType::AgentDelegatesTo,
            "upstream",
            "a",
            0.9,
        ));

        let claim = make_claim("agent-a", vec![]);
        let tb = TracebackBuilder::build(&claim, &g).expect("traceback");
        assert_eq!(
            tb.root_cause_node_id,
            NodeId::from("p"),
            "external content, not the executor, is the root cause"
        );
        assert!(tb.chain_node_ids.contains(&NodeId::from("a")));
        assert!(tb.chain_edge_ids.contains(&EdgeId::from("delegate")));
    }

    #[test]
    fn taint_writer_is_not_overwritten_by_an_unrelated_delegator() {
        let mut graph = ResponsibilityGraph::default();
        for (id, actor) in [
            ("writer", "actor-writer"),
            ("reader", "actor-reader"),
            ("delegator", "actor-delegator"),
        ] {
            graph.add_node(make_agent_node(id, actor));
        }
        graph.add_node(make_boundary_node("boundary"));
        graph.add_edge(make_edge(
            "pbc",
            ResponsibilityEdgeType::PolicyBoundaryCrossing,
            "reader",
            "boundary",
            0.9,
        ));
        graph.add_edge(make_edge(
            "taint",
            ResponsibilityEdgeType::TaintPropagatesTo,
            "writer",
            "reader",
            0.9,
        ));
        graph.add_edge(make_edge(
            "delegate",
            ResponsibilityEdgeType::AgentDelegatesTo,
            "delegator",
            "reader",
            0.9,
        ));
        let mut claim = make_claim("actor-reader", vec![]);
        claim.claim_type = ResponsibilityClaimType::TaintedContextUse;
        let traceback = TracebackBuilder::build(&claim, &graph).unwrap();
        assert_eq!(traceback.root_cause_node_id, NodeId::from("writer"));
        assert!(traceback.chain_edge_ids.contains(&EdgeId::from("delegate")));
    }

    #[test]
    fn taint_source_also_delegating_keeps_both_relationships_and_upstream_context() {
        let mut graph = ResponsibilityGraph::default();
        for (id, actor) in [
            ("writer", "actor-writer"),
            ("reader", "actor-reader"),
            ("upstream", "actor-upstream"),
        ] {
            graph.add_node(make_agent_node(id, actor));
        }
        graph.add_node(make_boundary_node("boundary"));
        for (id, kind, source, target) in [
            (
                "pbc",
                ResponsibilityEdgeType::PolicyBoundaryCrossing,
                "reader",
                "boundary",
            ),
            (
                "taint",
                ResponsibilityEdgeType::TaintPropagatesTo,
                "writer",
                "reader",
            ),
            (
                "delegate",
                ResponsibilityEdgeType::AgentDelegatesTo,
                "writer",
                "reader",
            ),
            (
                "upstream-delegate",
                ResponsibilityEdgeType::AgentDelegatesTo,
                "upstream",
                "writer",
            ),
        ] {
            graph.add_edge(make_edge(id, kind, source, target, 0.9));
        }
        let mut claim = make_claim("actor-reader", vec![]);
        claim.claim_type = ResponsibilityClaimType::TaintedContextUse;
        let traceback = TracebackBuilder::build(&claim, &graph).unwrap();
        for id in ["taint", "delegate", "upstream-delegate"] {
            assert!(
                traceback.chain_edge_ids.contains(&EdgeId::from(id)),
                "lost {id}"
            );
        }
        assert_eq!(traceback.root_cause_node_id, NodeId::from("writer"));
    }

    #[test]
    fn traceback_follows_delegation_chain_to_the_root_cause() {
        let mut g = ResponsibilityGraph::default();
        for (id, agent) in [("a", "agent-a"), ("b", "agent-b"), ("c", "agent-c")] {
            g.add_node(make_agent_node(id, agent));
        }
        g.add_node(make_resource_node("r"));
        g.add_node(make_boundary_node("p"));
        g.add_edge(make_edge(
            "d1",
            ResponsibilityEdgeType::AgentDelegatesTo,
            "a",
            "b",
            0.9,
        ));
        g.add_edge(make_edge(
            "d2",
            ResponsibilityEdgeType::AgentDelegatesTo,
            "b",
            "c",
            0.9,
        ));
        let mut action = make_edge(
            "act",
            ResponsibilityEdgeType::IntentCausesAction,
            "c",
            "r",
            0.9,
        );
        action.evidence_refs = vec![crate::responsibility::types::EvidenceId::from("ev-act")];
        g.add_edge(action);
        g.add_edge(make_edge(
            "pbc",
            ResponsibilityEdgeType::PolicyBoundaryCrossing,
            "c",
            "p",
            0.9,
        ));

        // Only evidence-backed actions belong to the chain; the claim must
        // cite the action it is tracing.
        let claim = make_claim(
            "agent-c",
            vec![crate::responsibility::types::EvidenceId::from("ev-act")],
        );
        let tb = TracebackBuilder::build(&claim, &g).expect("traceback");
        let ids: Vec<String> = tb
            .chain_node_ids
            .iter()
            .map(|n| n.as_str().to_string())
            .collect();
        for expected in ["a", "b", "c", "r", "p"] {
            assert!(
                ids.contains(&expected.to_string()),
                "missing {expected}: {ids:?}"
            );
        }
        assert_eq!(tb.root_cause_node_id, NodeId::from("a"));
        let edge_ids: Vec<String> = tb
            .chain_edge_ids
            .iter()
            .map(|e| e.as_str().to_string())
            .collect();
        assert!(edge_ids.contains(&"d1".to_string()));
        assert!(edge_ids.contains(&"d2".to_string()));
    }
}
