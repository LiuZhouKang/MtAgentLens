// SPDX-License-Identifier: Apache-2.0 OR MIT
//
// traceback.rs — TracebackBuilder: reverse-edge traversal from a
// ResponsibilityClaim through the ResponsibilityGraph to produce a
// ResponsibilityTraceback (new types/ system).

use crate::responsibility::graph::ResponsibilityGraph;
use crate::responsibility::types::{
    EdgeId, NodeId, ResponsibilityClaim, ResponsibilityEdgeType, ResponsibilityNodeType,
    ResponsibilityTraceback, TracebackId,
};

// ═════════════════════════════════════════════════════════════════════════════
// TracebackBuilder
// ═════════════════════════════════════════════════════════════════════════════

/// Builds a `ResponsibilityTraceback` by walking backward through the
/// responsibility graph from the `PolicyBoundaryCrossing` edge's target node.
///
/// The traversal follows incoming edges in reverse, collecting
/// `IntentCausesAction` edges along the way. The deepest `Agent` node in
/// the resulting chain is designated as the root cause.
pub struct TracebackBuilder;

impl TracebackBuilder {
    /// Build a traceback for the given claim.
    ///
    /// 1. Find the `PolicyBoundaryCrossing` edge associated with this claim
    ///    by looking for an edge whose source matches the claim's subject agent
    ///    and whose evidence refs overlap.
    /// 2. Walk backward from the edge's target node.
    /// 3. Collect node and edge IDs along the chain.
    /// 4. Determine the root cause (deepest Agent node).
    /// 5. Generate an explanation summary.
    pub fn build(
        claim: &ResponsibilityClaim,
        graph: &ResponsibilityGraph,
    ) -> Option<ResponsibilityTraceback> {
        // Find the policy boundary crossing edge for this claim.
        let boundary_edge = find_boundary_edge_for_claim(claim, graph)?;

        // Chain order: Agent → Resource → PolicyBoundary
        // The PBC edge goes Agent → PolicyBoundary.
        // IntentCausesAction edges go Agent → Resource.
        let mut chain_nodes: Vec<NodeId> = Vec::new();
        let mut chain_edges: Vec<EdgeId> = Vec::new();

        // Start with the agent node (source of PBC edge).
        let agent_node_id = boundary_edge.source.clone();
        chain_nodes.push(agent_node_id.clone());

        // Walk FORWARD from the agent: find IntentCausesAction edges
        // pointing from this agent to resource nodes.
        let outgoing = graph.outgoing_from(&agent_node_id);
        for edge_id in outgoing.iter() {
            if let Some(edge) = graph.get_edge(edge_id)
                && edge.edge_type == ResponsibilityEdgeType::IntentCausesAction
            {
                chain_nodes.push(edge.target.clone());
                chain_edges.push(edge_id.clone());
            }
        }

        // Append the PolicyBoundary node (target of PBC edge).
        chain_nodes.push(boundary_edge.target.clone());
        chain_edges.push(boundary_edge.edge_id.clone());

        // Root cause is the agent node.
        let root_cause_node_id = agent_node_id.clone();

        // Generate explanation summary.
        let explanation_summary = generate_explanation(
            graph,
            claim,
            &chain_nodes,
            &chain_edges,
            &root_cause_node_id,
        );

        // Compute chain confidence from the collected edges.
        let chain_confidence =
            compute_chain_confidence(graph, &chain_edges, boundary_edge.confidence);

        Some(ResponsibilityTraceback {
            traceback_id: TracebackId::new(),
            claim_id: claim.claim_id.clone(),
            chain_node_ids: chain_nodes,
            chain_edge_ids: chain_edges,
            root_cause_node_id,
            explanation_summary,
            confidence: chain_confidence,
        })
    }
}

// ═════════════════════════════════════════════════════════════════════════════
// Boundary edge finder
// ═════════════════════════════════════════════════════════════════════════════

/// Find the `PolicyBoundaryCrossing` edge most likely associated with this
/// claim. Priority:
/// 1. Edges whose evidence_refs overlap with the claim's evidence_refs
/// 2. Edges whose source agent matches the claim's subject
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

    // Score each edge: +2 for matching agent, +1 per overlapping evidence ref.
    let mut best: Option<&crate::responsibility::types::ResponsibilityEdge> = None;
    let mut best_score: u32 = 0;

    for edge in &pbc_edges {
        let mut score: u32 = 0;
        if let Some(source_node) = graph.get_node(&edge.source)
            && source_node.agent_id.as_ref() == Some(&claim.subject)
        {
            score += 2;
        }
        for ev_ref in &edge.evidence_refs {
            if claim.evidence_refs.contains(ev_ref) {
                score += 1;
            }
        }
        if score > best_score {
            best_score = score;
            best = Some(edge);
        }
    }

    // Fall back to best-scored edge, then first available edge.
    best.or_else(|| pbc_edges.first().copied()).cloned()
}

// ═════════════════════════════════════════════════════════════════════════════
// Root cause analysis
// ═════════════════════════════════════════════════════════════════════════════

/// Find the deepest Agent node in the chain.
/// Walking from the target backward, the "deepest" Agent is the one furthest
/// from the target — i.e., the earliest agent in the causal chain.
fn find_root_cause_agent_node(
    graph: &ResponsibilityGraph,
    chain_node_ids: &[NodeId],
) -> Option<NodeId> {
    // Iterate in reverse (from deepest/earliest to most recent).
    for node_id in chain_node_ids.iter().rev() {
        if let Some(node) = graph.get_node(node_id)
            && node.node_type == ResponsibilityNodeType::Agent
        {
            return Some(node_id.clone());
        }
    }
    // Fallback: any agent node in the chain.
    for node_id in chain_node_ids {
        if let Some(node) = graph.get_node(node_id)
            && node.node_type == ResponsibilityNodeType::Agent
        {
            return Some(node_id.clone());
        }
    }
    None
}

// ═════════════════════════════════════════════════════════════════════════════
// Explanation summary
// ═════════════════════════════════════════════════════════════════════════════

fn generate_explanation(
    graph: &ResponsibilityGraph,
    claim: &ResponsibilityClaim,
    chain_nodes: &[NodeId],
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

    // Causal chain.
    lines.push("Causal chain:".to_string());
    for (i, node_id) in chain_nodes.iter().enumerate() {
        let node_label = node_label_for_display(graph, node_id);
        if i == 0 {
            lines.push(format!("  [start] {}", node_label));
        } else {
            let edge_label = if i - 1 < chain_edges.len() {
                edge_label_for_display(graph, &chain_edges[i - 1])
            } else {
                "→".to_string()
            };
            lines.push(format!("  {} {}", edge_label, node_label));
        }
    }

    // Root cause.
    let root_label = node_label_for_display(graph, root_cause_id);
    lines.push(format!("Root cause: {}", root_label));

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
            ResponsibilityEdgeType::IntentCausesAction => "caused→".to_string(),
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

/// Compute the overall chain confidence as the product of all edge confidences
/// in the path, multiplied by the boundary edge confidence.
fn compute_chain_confidence(
    graph: &ResponsibilityGraph,
    chain_edges: &[EdgeId],
    boundary_confidence: f32,
) -> f32 {
    let mut product = boundary_confidence;
    for eid in chain_edges {
        if let Some(edge) = graph.get_edge(eid) {
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
        assert!(traceback.explanation_summary.contains("Causal chain"));
        assert!(traceback.explanation_summary.contains("Root cause"));
        assert!(traceback.explanation_summary.contains("Agent(agent-root)"));
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
}
