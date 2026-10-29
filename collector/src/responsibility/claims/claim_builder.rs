// SPDX-License-Identifier: Apache-2.0 OR MIT
//
// claim_builder.rs — reads PolicyBoundaryCrossing edges from the
// ResponsibilityGraph and builds ResponsibilityClaims (new types/ system).

use std::collections::HashSet;

use serde::{Deserialize, Serialize};

use crate::responsibility::graph::ResponsibilityGraph;
use crate::responsibility::judgment::delegation::DelegationObservation;
use crate::responsibility::types::{
    AgentId, AlertSeverity, BoundaryId, ClaimId, CompetingExplanation, EdgeId, NormalizedResource,
    PolicyId, ResponsibilityClaim, ResponsibilityClaimState, ResponsibilityClaimType,
    ResponsibilityEdge, ResponsibilityEdgeType, ResponsibilityNodeType,
};

use super::authorization::{ContextShareAuthorization, authorize_context_share};

// ═════════════════════════════════════════════════════════════════════════════
// Build diagnostics
// ═════════════════════════════════════════════════════════════════════════════

/// Minimum crossing confidence for a `TaintedContextUse` claim.
///
/// A taint claim asserts that the reader *used* tainted context, not merely
/// that a crossing was plausible. Measured on the 2026-07 corpus (scenario
/// maximum TCU confidence): all 15 benign TaintedContextUse claims are
/// `Inferred` (0.60–0.67, maximum < 0.68), while 43/67 attack scenarios have
/// a TCU claim at >= 0.70. Sensitivity: 0.60 gives 10/19 benign and 59/67
/// attack; 0.85 gives 0/19 and 33/67. The attack-side claim states are
/// Inferred 532, Confirmed 208, Observed 22, Ambiguous 51 — the separating
/// fact is the benign maximum, not an attack-side `Confirmed` majority. Keep
/// the value paired with `confidence_to_claim_state`'s `Observed` boundary.
pub const TAINT_CLAIM_MIN_CONFIDENCE: f32 = 0.70;

/// Counters that make the taint authorization gate observable.
///
/// A run that produced no `TaintedContextUse` claim must be distinguishable
/// from a run where taint edges were authorized or unattributable. Without
/// these numbers the gate is a silent filter.
#[derive(Debug, Default, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default)]
pub struct ClaimBuildDiagnostics {
    /// Taint edges in the graph at the latest build (snapshot, not a sum).
    pub taint_edges: u64,
    /// Taint edges whose writer delegated to the reader (snapshot).
    pub taint_authorized: u64,
    /// Taint edges with no authorizing relation (snapshot).
    pub taint_unauthorized: u64,
    /// Taint edges whose endpoints carry no agent identity (snapshot). The
    /// live producer always writes agent→agent edges, so this is normally 0
    /// and is kept for replay and constructed graphs.
    pub taint_unattributed: u64,
    /// TaintedContextUse claims emitted, counted per unclaimed boundary
    /// crossing (accumulated across builds, not per unique taint edge).
    pub taint_claims_emitted: u64,
    /// Unauthorized crossings kept generic because their confidence was below
    /// [`TAINT_CLAIM_MIN_CONFIDENCE`] (accumulated per boundary crossing).
    pub taint_claims_below_confidence: u64,
}

impl ClaimBuildDiagnostics {
    /// Fold one build's diagnostics into a run-level accumulator.
    ///
    /// Taint-edge counts describe the graph, which only grows and dedupes
    /// edges — the latest build sees all of them, so it replaces the previous
    /// snapshot. Summing per trigger cycle made these numbers scale with run
    /// length. Claim counters are emitted once per unclaimed crossing, so they
    /// accumulate across builds.
    pub fn observe(&mut self, build: &Self) {
        self.taint_edges = build.taint_edges;
        self.taint_authorized = build.taint_authorized;
        self.taint_unauthorized = build.taint_unauthorized;
        self.taint_unattributed = build.taint_unattributed;
        self.taint_claims_emitted += build.taint_claims_emitted;
        self.taint_claims_below_confidence += build.taint_claims_below_confidence;
    }
}

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
    #[cfg(test)]
    pub fn build_claims(
        graph: &ResponsibilityGraph,
        claimed_edges: &HashSet<EdgeId>,
    ) -> Vec<ResponsibilityClaim> {
        // Test-only convenience: it deliberately sees no delegation records,
        // so every cross-agent share is unauthorized. Production callers must
        // pass the runtime's records to `build_claims_with_delegations`;
        // keeping this out of non-test builds removes the silent footgun.
        Self::build_claims_with_delegations(graph, claimed_edges, &[]).0
    }

    /// Build claims with an explicit authorization context.
    ///
    /// Returns the claims and the taint-gate counters. A taint edge is only
    /// allowed to relabel a boundary crossing as `TaintedContextUse` when
    /// `authorize_context_share` says the reader was not authorized to
    /// receive the writer's context.
    pub fn build_claims_with_delegations(
        graph: &ResponsibilityGraph,
        claimed_edges: &HashSet<EdgeId>,
        delegations: &[DelegationObservation],
    ) -> (Vec<ResponsibilityClaim>, ClaimBuildDiagnostics) {
        let mut diagnostics = ClaimBuildDiagnostics::default();

        // Evaluate each taint edge once. Counting per boundary edge would
        // multiply one taint edge by the number of evidence-sharing crossings.
        let taint_edges: Vec<(ResponsibilityEdge, ContextShareAuthorization)> = graph
            .edges_of_type(&ResponsibilityEdgeType::TaintPropagatesTo)
            .iter()
            .filter_map(|edge_id| graph.get_edge(edge_id))
            .map(|taint| {
                let writer = graph
                    .get_node(&taint.source)
                    .and_then(|node| node.agent_id.as_ref());
                let reader = graph
                    .get_node(&taint.target)
                    .and_then(|node| node.agent_id.as_ref());
                let verdict = authorize_context_share(writer, reader, delegations);
                diagnostics.taint_edges += 1;
                match verdict {
                    ContextShareAuthorization::Authorized => diagnostics.taint_authorized += 1,
                    ContextShareAuthorization::Unauthorized => {
                        diagnostics.taint_unauthorized += 1;
                    }
                    ContextShareAuthorization::Unattributed => {
                        diagnostics.taint_unattributed += 1;
                    }
                }
                (taint.clone(), verdict)
            })
            .collect();

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
            let claim_type = classify_claim_type(graph, &edge, &taint_edges, &mut diagnostics);

            // Map confidence to claim state. Evidence is part of the state:
            // a boundary edge minted from an intent alone (empty
            // `evidence_refs`, confidence 0.60) used to produce an Observed
            // claim, and at 0.85 a Confirmed Critical one — a claim about
            // something no syscall had confirmed. Such a claim is Unproven
            // until an observed action backs it.
            let claim_state = if edge.evidence_refs.is_empty() {
                ResponsibilityClaimState::Unproven
            } else {
                confidence_to_claim_state(edge.confidence)
            };

            // Derive severity from claim type and confidence. An unproven
            // claim stays a review candidate: it may not carry Critical.
            let mut severity = severity_for_claim_type(&claim_type, edge.confidence);
            if edge.evidence_refs.is_empty() && matches!(severity, AlertSeverity::Critical) {
                severity = AlertSeverity::Warning;
            }

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

        (claims, diagnostics)
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

/// Tool names whose step-5b policy boundary is the delegation boundary.
/// Mirrors the orchestration-tool check in `GraphBuilder::handle`.
const DELEGATION_BOUNDARY_TOOLS: &[&str] = &["TaskCreate", "Agent", "SendMessage", "Task", "Skill"];

/// Two edges are the same correlated fact when they share an evidence id.
fn shares_evidence(left: &ResponsibilityEdge, right: &ResponsibilityEdge) -> bool {
    left.evidence_refs
        .iter()
        .any(|evidence| right.evidence_refs.contains(evidence))
}

/// A `SuspiciousDelegation` claim must name the delegation boundary and be
/// backed by the same correlated fact.
///
/// An `AgentDelegatesTo` edge from the same actor only says that the actor
/// delegated at some point; it does not connect that delegation to this
/// crossing (the `exfil-env-secrets` network capability crossing was renamed
/// for exactly that reason). The crossing must therefore target a delegation
/// tool boundary (the shape step 5b produces) and share evidence with the
/// handoff: either the delegation edge itself, or the `ContextFlowsFrom` edge
/// the producer writes for the same cross-agent action. The latter is how a
/// process-spawn delegation edge (which carries no evidence) is tied back to
/// the observed fact. Without either link there is no support to classify.
fn has_supported_delegation(graph: &ResponsibilityGraph, edge: &ResponsibilityEdge) -> bool {
    let Some(boundary) = graph.get_node(&edge.target) else {
        return false;
    };
    let is_delegation_boundary = boundary.node_type == ResponsibilityNodeType::PolicyBoundary
        && matches!(
            &boundary.resource,
            Some(NormalizedResource::ToolCall { tool_name, .. })
                if DELEGATION_BOUNDARY_TOOLS.contains(&tool_name.as_str())
        );
    if !is_delegation_boundary {
        return false;
    }

    let context_flows: Vec<&ResponsibilityEdge> = graph
        .edges_of_type(&ResponsibilityEdgeType::ContextFlowsFrom)
        .iter()
        .filter_map(|eid| graph.get_edge(eid))
        .collect();

    graph
        .edges_of_type(&ResponsibilityEdgeType::AgentDelegatesTo)
        .iter()
        .filter_map(|eid| graph.get_edge(eid))
        .any(|delegation| {
            delegation.source == edge.source
                && graph.get_node(&delegation.target).is_some_and(|node| {
                    node.node_type == ResponsibilityNodeType::Agent && node.agent_id.is_some()
                })
                && (shares_evidence(delegation, edge)
                    || context_flows.iter().any(|flow| {
                        flow.source == edge.source
                            && flow.target == delegation.target
                            && shares_evidence(flow, edge)
                    }))
        })
}

/// Classify a `PolicyBoundaryCrossing` edge into a `ResponsibilityClaimType`
/// by inspecting the surrounding graph context.
fn classify_claim_type(
    graph: &ResponsibilityGraph,
    edge: &ResponsibilityEdge,
    taint_edges: &[(ResponsibilityEdge, ContextShareAuthorization)],
    diagnostics: &mut ClaimBuildDiagnostics,
) -> ResponsibilityClaimType {
    // A taint claim names the agent that *used* tainted context, so the
    // boundary crosser must be the taint receiver (`taint.target ==
    // edge.source`), and the two must share the observation that produced the
    // crossing. Without both conditions one taint edge relabels every
    // unrelated boundary crossing of the same agents (the P1 amplification),
    // or blames the writer for crossing its own boundary.
    //
    // The authorization gate is the second half: a direct observed delegation
    // between writer and reader means the share was expected. Unattributed
    // endpoints cannot support a claim at all.
    let has_unauthorized_taint = taint_edges.iter().any(|(taint, verdict)| {
        *verdict == ContextShareAuthorization::Unauthorized
            && taint.target == edge.source
            && taint
                .evidence_refs
                .iter()
                .any(|evidence| edge.evidence_refs.contains(evidence))
    });

    if has_unauthorized_taint {
        if edge.confidence >= TAINT_CLAIM_MIN_CONFIDENCE {
            diagnostics.taint_claims_emitted += 1;
            return ResponsibilityClaimType::TaintedContextUse;
        }
        // The crossing is weaker than `Observed`: keep the generic policy
        // classification rather than asserting context *use*.
        diagnostics.taint_claims_below_confidence += 1;
    }

    // A delegation claim must be tied to a delegation boundary and the same
    // correlated fact; an AgentDelegatesTo edge from the same actor is only
    // co-occurrence.
    if has_supported_delegation(graph, edge) {
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
    use crate::responsibility::judgment::delegation::DelegationObservation;
    use crate::responsibility::types::{EvidenceId, NormalizedResource, PhaseIndicator};

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

    fn make_resource_boundary_node(
        id: &str,
        resource: NormalizedResource,
    ) -> crate::responsibility::types::ResponsibilityNode {
        let mut node = make_boundary_node(id);
        node.resource = Some(resource);
        node
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
            operation: None,
            source: crate::responsibility::types::NodeId::from(source),
            target: crate::responsibility::types::NodeId::from(target),
            // A claim is about an observed crossing by default; tests that
            // need an intent-only edge clear this explicitly.
            evidence_refs: vec![crate::responsibility::types::EvidenceId::from(format!(
                "ev-{id}"
            ))],
            confidence,
            phase: PhaseIndicator::Authoritative {
                timestamp_ns: 1000,
                overrides: None,
            },
        }
    }

    /// Edge counters describe the graph, so a later build (which sees the
    /// whole graph) must replace them, not add to them: summing per trigger
    /// cycle made the numbers scale with run length. Claim counters are
    /// produced once per unclaimed crossing and must accumulate.
    #[test]
    fn diagnostics_observe_snapshots_edges_and_accumulates_claims() {
        let mut run = ClaimBuildDiagnostics::default();
        run.observe(&ClaimBuildDiagnostics {
            taint_edges: 2,
            taint_authorized: 1,
            taint_unauthorized: 1,
            taint_unattributed: 0,
            taint_claims_emitted: 1,
            taint_claims_below_confidence: 0,
        });
        run.observe(&ClaimBuildDiagnostics {
            taint_edges: 2,
            taint_authorized: 1,
            taint_unauthorized: 1,
            taint_unattributed: 0,
            taint_claims_emitted: 0,
            taint_claims_below_confidence: 1,
        });
        assert_eq!(run.taint_edges, 2, "edge counters are a snapshot");
        assert_eq!(run.taint_authorized, 1);
        assert_eq!(run.taint_claims_emitted, 1, "claims accumulate");
        assert_eq!(run.taint_claims_below_confidence, 1);
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

    /// Build the graph shape the live producer actually emits: the taint edge
    /// runs writer-agent → reader-agent, and the boundary-crossing edge runs
    /// reader-agent → boundary. Returns the reader's crossing evidence id.
    fn graph_with_taint_at(
        delegations_are_authorized: bool,
        crossing_confidence: f32,
    ) -> (ResponsibilityGraph, Vec<DelegationObservation>) {
        let mut g = ResponsibilityGraph::default();
        g.add_node(make_agent_node("agent-w", "agent-writer"));
        g.add_node(make_agent_node("agent-r", "agent-reader"));
        g.add_node(make_boundary_node("boundary1"));
        let evidence = EvidenceId::from("ev-shared");

        let mut pb = make_edge(
            "edge_pb",
            ResponsibilityEdgeType::PolicyBoundaryCrossing,
            "agent-r",
            "boundary1",
            crossing_confidence,
        );
        pb.evidence_refs = vec![evidence.clone()];
        g.add_edge(pb);

        let mut taint = make_edge(
            "edge_taint",
            ResponsibilityEdgeType::TaintPropagatesTo,
            "agent-w",
            "agent-r",
            0.70,
        );
        taint.evidence_refs = vec![evidence];
        g.add_edge(taint);

        let delegations = if delegations_are_authorized {
            vec![DelegationObservation {
                delegation_id: "delegation:writer->reader".into(),
                from_agent_id: "agent-writer".into(),
                to_agent_id: "agent-reader".into(),
                parent_pid: 1,
                parent_start_time_ns: 1,
                child_pid: 2,
                child_start_time_ns: 2,
                timestamp_ns: 1_000,
                child_comm: "child".into(),
                confidence: 0.85,
            }]
        } else {
            Vec::new()
        };
        (g, delegations)
    }

    #[test]
    fn test_tainted_context_detection() {
        let (g, _) = graph_with_taint_at(false, 0.80);
        let claims = ClaimBuilder::build_claims(&g, &HashSet::new());
        assert_eq!(claims.len(), 1);
        assert!(matches!(
            claims[0].claim_type,
            ResponsibilityClaimType::TaintedContextUse
        ));
    }

    /// The epistemic gate. On the 2026-07 corpus every benign
    /// TaintedContextUse claim is `Inferred` (confidence 0.60–0.67), while
    /// attack-side claims are mostly `Confirmed` (0.90). Requiring the
    /// crossing to reach `Observed` (>= 0.70) yields 0/19 benign and 43/67
    /// attack scenarios on that corpus. A weaker crossing keeps its generic
    /// policy classification instead of asserting context *use*.
    #[test]
    fn taint_claim_requires_observed_confidence() {
        let (g, _) = graph_with_taint_at(false, 0.60);
        let (claims, diagnostics) =
            ClaimBuilder::build_claims_with_delegations(&g, &HashSet::new(), &[]);
        assert_eq!(claims.len(), 1);
        assert!(matches!(
            claims[0].claim_type,
            ResponsibilityClaimType::PolicyViolation
        ));
        assert_eq!(diagnostics.taint_claims_below_confidence, 1);
        assert_eq!(diagnostics.taint_claims_emitted, 0);
    }

    #[test]
    fn observed_confidence_taint_crossing_is_a_taint_claim() {
        let (g, _) = graph_with_taint_at(false, TAINT_CLAIM_MIN_CONFIDENCE);
        let (claims, diagnostics) =
            ClaimBuilder::build_claims_with_delegations(&g, &HashSet::new(), &[]);
        assert_eq!(claims.len(), 1);
        assert!(matches!(
            claims[0].claim_type,
            ResponsibilityClaimType::TaintedContextUse
        ));
        assert_eq!(diagnostics.taint_claims_emitted, 1);
        assert_eq!(diagnostics.taint_claims_below_confidence, 0);
    }

    /// The authorization gate: a direct observed delegation between writer
    /// and reader means the handoff was set up by the system under
    /// observation. The crossing keeps its own classification instead of
    /// being labeled a tainted-context violation.
    #[test]
    fn authorized_context_share_is_not_a_taint_claim() {
        let (g, delegations) = graph_with_taint_at(true, 0.80);
        let (claims, diagnostics) =
            ClaimBuilder::build_claims_with_delegations(&g, &HashSet::new(), &delegations);
        assert_eq!(claims.len(), 1);
        assert!(matches!(
            claims[0].claim_type,
            ResponsibilityClaimType::PolicyViolation
        ));
        assert_eq!(diagnostics.taint_authorized, 1);
        assert_eq!(diagnostics.taint_unauthorized, 0);
    }

    /// A taint claim names the agent that *used* the tainted context, so the
    /// boundary crosser must be the taint receiver. A crossing by the writer
    /// itself is not a tainted-context claim.
    #[test]
    fn taint_claim_requires_reader_as_boundary_crosser() {
        let mut g = ResponsibilityGraph::default();
        g.add_node(make_agent_node("agent-w", "agent-writer"));
        g.add_node(make_agent_node("agent-r", "agent-reader"));
        g.add_node(make_boundary_node("boundary1"));
        let evidence = EvidenceId::from("ev-shared");

        // The writer crosses the boundary; the reader is only the taint target.
        let mut pb = make_edge(
            "edge_pb",
            ResponsibilityEdgeType::PolicyBoundaryCrossing,
            "agent-w",
            "boundary1",
            0.80,
        );
        pb.evidence_refs = vec![evidence.clone()];
        g.add_edge(pb);
        let mut taint = make_edge(
            "edge_taint",
            ResponsibilityEdgeType::TaintPropagatesTo,
            "agent-w",
            "agent-r",
            0.70,
        );
        taint.evidence_refs = vec![evidence];
        g.add_edge(taint);

        let claims = ClaimBuilder::build_claims(&g, &HashSet::new());
        assert_eq!(claims.len(), 1);
        assert!(matches!(
            claims[0].claim_type,
            ResponsibilityClaimType::PolicyViolation
        ));
    }

    /// A taint edge whose endpoint carries no agent identity cannot support a
    /// responsibility claim. Falling back to "unauthorized" would invent an
    /// actor; falling back to "authorized" would hide a real crossing.
    #[test]
    fn unattributed_taint_endpoint_is_not_a_claim() {
        let mut g = ResponsibilityGraph::default();
        g.add_node(make_agent_node("agent-r", "agent-reader"));
        g.add_node(make_boundary_node("boundary1"));
        g.add_node(make_boundary_node("boundary2"));
        let evidence = EvidenceId::from("ev-shared");

        let mut pb = make_edge(
            "edge_pb",
            ResponsibilityEdgeType::PolicyBoundaryCrossing,
            "agent-r",
            "boundary1",
            0.80,
        );
        pb.evidence_refs = vec![evidence.clone()];
        g.add_edge(pb);
        // Source is a boundary node with no agent_id.
        let mut taint = make_edge(
            "edge_taint",
            ResponsibilityEdgeType::TaintPropagatesTo,
            "boundary2",
            "agent-r",
            0.70,
        );
        taint.evidence_refs = vec![evidence];
        g.add_edge(taint);

        let (claims, diagnostics) =
            ClaimBuilder::build_claims_with_delegations(&g, &HashSet::new(), &[]);
        assert_eq!(claims.len(), 1);
        assert!(matches!(
            claims[0].claim_type,
            ResponsibilityClaimType::PolicyViolation
        ));
        assert_eq!(diagnostics.taint_unattributed, 1);
    }

    #[test]
    fn test_taint_claim_requires_shared_evidence() {
        let mut g = ResponsibilityGraph::default();
        g.add_node(make_agent_node("agent-w", "agent-writer"));
        g.add_node(make_agent_node("agent-r", "agent-reader"));
        g.add_node(make_boundary_node("boundary1"));
        g.add_node(make_boundary_node("boundary2"));

        let mut pb1 = make_edge(
            "edge_pb1",
            ResponsibilityEdgeType::PolicyBoundaryCrossing,
            "agent-r",
            "boundary1",
            0.80,
        );
        pb1.evidence_refs = vec![EvidenceId::from("ev-1")];
        g.add_edge(pb1);
        let mut pb2 = make_edge(
            "edge_pb2",
            ResponsibilityEdgeType::PolicyBoundaryCrossing,
            "agent-r",
            "boundary2",
            0.80,
        );
        pb2.evidence_refs = vec![EvidenceId::from("ev-2")];
        g.add_edge(pb2);
        // The taint edge shares no evidence with either crossing.
        let mut taint = make_edge(
            "edge_taint",
            ResponsibilityEdgeType::TaintPropagatesTo,
            "agent-w",
            "agent-r",
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

    /// A network capability crossing must keep its own classification even
    /// when the same actor also has a delegation edge. `exfil-env-secrets`
    /// produced a pending egress to an unnamed host and the claim builder
    /// called it a SuspiciousDelegation solely because the source agent had a
    /// process-spawn delegation edge.
    #[test]
    fn unrelated_delegation_does_not_relabel_network_capability_crossing() {
        let mut g = ResponsibilityGraph::default();
        g.add_node(make_agent_node("agent-sender", "agent-sender"));
        g.add_node(make_agent_node("agent-delegate", "agent-delegate"));
        g.add_node(make_resource_boundary_node(
            "boundary-egress",
            NormalizedResource::Network {
                host: "192.0.2.10".into(),
                port: 9000,
                scheme: "tcp".into(),
            },
        ));
        // Same evidence on both edges is the strongest co-occurrence: the
        // delegation is still about a different boundary fact.
        let evidence = EvidenceId::from("ev-egress");
        let mut crossing = make_edge(
            "edge-egress",
            ResponsibilityEdgeType::PolicyBoundaryCrossing,
            "agent-sender",
            "boundary-egress",
            0.70,
        );
        crossing.evidence_refs = vec![evidence.clone()];
        g.add_edge(crossing);
        let mut delegation = make_edge(
            "edge-delegation",
            ResponsibilityEdgeType::AgentDelegatesTo,
            "agent-sender",
            "agent-delegate",
            0.85,
        );
        delegation.evidence_refs = vec![evidence];
        g.add_edge(delegation);

        let claims = ClaimBuilder::build_claims(&g, &HashSet::new());
        assert_eq!(claims.len(), 1);
        assert!(
            !matches!(
                claims[0].claim_type,
                ResponsibilityClaimType::SuspiciousDelegation
            ),
            "an unrelated delegation must not rename an egress crossing: {:?}",
            claims[0].claim_type
        );
    }

    /// The positive case: a PBC at the delegation tool boundary, backed by a
    /// delegation edge from the same actor and the same correlated evidence,
    /// is a genuine delegation claim.
    #[test]
    fn delegation_boundary_with_shared_delegation_evidence_is_suspicious() {
        let mut g = ResponsibilityGraph::default();
        g.add_node(make_agent_node("agent-lead", "agent-lead"));
        g.add_node(make_agent_node("agent-worker", "agent-worker"));
        g.add_node(make_resource_boundary_node(
            "boundary-task",
            NormalizedResource::ToolCall {
                tool_name: "TaskCreate".into(),
                call_id: "intent-1".into(),
            },
        ));
        let evidence = EvidenceId::from("ev-delegation");
        let mut crossing = make_edge(
            "edge-task",
            ResponsibilityEdgeType::PolicyBoundaryCrossing,
            "agent-lead",
            "boundary-task",
            0.80,
        );
        crossing.evidence_refs = vec![evidence.clone()];
        g.add_edge(crossing);
        let mut delegation = make_edge(
            "edge-delegation",
            ResponsibilityEdgeType::AgentDelegatesTo,
            "agent-lead",
            "agent-worker",
            0.85,
        );
        delegation.evidence_refs = vec![evidence];
        g.add_edge(delegation);

        let claims = ClaimBuilder::build_claims(&g, &HashSet::new());
        assert_eq!(claims.len(), 1);
        assert!(
            matches!(
                claims[0].claim_type,
                ResponsibilityClaimType::SuspiciousDelegation
            ),
            "a delegation boundary backed by the same fact must stay a delegation claim"
        );
    }

    /// Process-spawn delegation edges carry no evidence, but step 5b writes a
    /// `ContextFlowsFrom` edge on the same fact. That context handoff keeps a
    /// genuine delegation claim supported without inventing evidence on the
    /// spawn edge.
    #[test]
    fn process_spawn_delegation_with_shared_context_flow_is_suspicious() {
        let mut g = ResponsibilityGraph::default();
        g.add_node(make_agent_node("agent-lead", "agent-lead"));
        g.add_node(make_agent_node("agent-worker", "agent-worker"));
        g.add_node(make_resource_boundary_node(
            "boundary-task",
            NormalizedResource::ToolCall {
                tool_name: "TaskCreate".into(),
                call_id: "intent-1".into(),
            },
        ));
        let evidence = EvidenceId::from("ev-delegation");
        let mut crossing = make_edge(
            "edge-task",
            ResponsibilityEdgeType::PolicyBoundaryCrossing,
            "agent-lead",
            "boundary-task",
            0.80,
        );
        crossing.evidence_refs = vec![evidence.clone()];
        g.add_edge(crossing);
        let mut delegation = make_edge(
            "edge-delegation",
            ResponsibilityEdgeType::AgentDelegatesTo,
            "agent-lead",
            "agent-worker",
            0.85,
        );
        delegation.evidence_refs.clear();
        g.add_edge(delegation);
        let mut context = make_edge(
            "edge-context",
            ResponsibilityEdgeType::ContextFlowsFrom,
            "agent-lead",
            "agent-worker",
            0.85,
        );
        context.evidence_refs = vec![evidence];
        g.add_edge(context);

        let claims = ClaimBuilder::build_claims(&g, &HashSet::new());
        assert_eq!(claims.len(), 1);
        assert!(
            matches!(
                claims[0].claim_type,
                ResponsibilityClaimType::SuspiciousDelegation
            ),
            "the context handoff ties the spawn delegation to this crossing"
        );
    }

    /// The delegation-tool boundary alone is not enough: a delegation edge
    /// recording a different fact must not rename this crossing.
    #[test]
    fn delegation_boundary_with_unrelated_delegation_evidence_is_not_suspicious() {
        let mut g = ResponsibilityGraph::default();
        g.add_node(make_agent_node("agent-lead", "agent-lead"));
        g.add_node(make_agent_node("agent-worker", "agent-worker"));
        g.add_node(make_resource_boundary_node(
            "boundary-task",
            NormalizedResource::ToolCall {
                tool_name: "TaskCreate".into(),
                call_id: "intent-1".into(),
            },
        ));
        let mut crossing = make_edge(
            "edge-task",
            ResponsibilityEdgeType::PolicyBoundaryCrossing,
            "agent-lead",
            "boundary-task",
            0.80,
        );
        crossing.evidence_refs = vec![EvidenceId::from("ev-crossing")];
        g.add_edge(crossing);
        let mut delegation = make_edge(
            "edge-delegation",
            ResponsibilityEdgeType::AgentDelegatesTo,
            "agent-lead",
            "agent-worker",
            0.85,
        );
        delegation.evidence_refs = vec![EvidenceId::from("ev-other-fact")];
        g.add_edge(delegation);

        let claims = ClaimBuilder::build_claims(&g, &HashSet::new());
        assert_eq!(claims.len(), 1);
        assert!(
            !matches!(
                claims[0].claim_type,
                ResponsibilityClaimType::SuspiciousDelegation
            ),
            "a delegation edge from a different fact must not rename this crossing"
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
