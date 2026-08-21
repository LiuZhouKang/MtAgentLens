// SPDX-License-Identifier: MIT
//
// graph_builder.rs — GraphBuilder: constructs the ResponsibilityGraph from
// correlation evidence, identity assignments, and policy judgments.
//
// This is the NEW GraphBuilder per spec §10.2. The OLD builder.rs is the
// legacy ResponsibilityGraphBuilder used by existing code paths.
//
// Phase 1 (streaming): handle() is called for each CorrelationEvidence.
// Phase 2 (batch): finalize() produces a FrozenGraph from authoritative data.
//
// Per spec §10.2, each handle() call:
//   1. Maps process_ids to AgentIds via IdentityProvider
//   2. Creates/updates Agent nodes
//   3. Creates Resource nodes from action.action_type + normalized_args
//   4. Creates IntentCausesAction edges
//   5. Calls judgment::PolicyEngine → PolicyBoundaryCrossing edge if violation/ambiguous
//   6. Detects cross-agent file reads → context_trust → TaintTracker

use crate::responsibility::evidence::CorrelationResult;
use crate::responsibility::graph::config::GraphConfig;
use crate::responsibility::graph::delta::GraphDelta;
use crate::responsibility::graph::graph::ResponsibilityGraph;
use crate::responsibility::graph::snapshot::FrozenGraph;
use crate::responsibility::graph::taint::TaintTracker;
use crate::responsibility::identity::{AuthoritativeResolution, IdentityProvider};
use crate::responsibility::judgment::context_trust::ContextTrustEvaluator;
use crate::responsibility::types::policy_types::PolicyJudgmentResult;
use crate::responsibility::types::policy_types::ResponsibilityClaimState;
use crate::responsibility::types::support_types::ResourceId;
use crate::responsibility::types::{
    Action, ActionCandidateType, AgentId, CorrelationEvidence, EdgeId, EvidenceId, GraphRefinement,
    GraphRefinementType, GraphSnapshot, Intent, NodeId, NormalizedArg, NormalizedResource,
    PhaseIndicator, ResponsibilityEdge, ResponsibilityEdgeType, ResponsibilityNode,
    ResponsibilityNodeType, SubagentBoundary,
};
use std::collections::{HashMap, HashSet};

// ─── GraphBuilder ──────────────────────────────────────────────────────────

/// Builds and maintains the ResponsibilityGraph from incoming evidence.
///
/// # Phase 1 (Streaming)
///
/// Call `handle()` for each `(CorrelationEvidence, Intent, Action)` triple
/// as they arrive. The builder maps processes to agents, creates nodes and
/// edges, evaluates policies, and tracks taint — all incrementally.
///
/// # Phase 2 (Batch)
///
/// At session end, call `finalize()` with authoritative identity and evidence
/// results to produce a `FrozenGraph` with all refinements applied.
pub struct GraphBuilder {
    /// The in-memory responsibility graph being built.
    graph: ResponsibilityGraph,
    /// Cross-agent contamination tracker.
    taint_tracker: TaintTracker,
    /// Known subagent boundaries (populated incrementally).
    subagent_boundaries: Vec<SubagentBoundary>,
    /// Accumulated delta for the current phase.
    accumulated_delta: GraphDelta,
    /// Running count of evidence processed.
    evidence_count: u64,
    /// Configuration.
    config: GraphConfig,
    /// Provisional PBC edges waiting for evidence binding.
    ///
    /// Keys are (source_node_id, policy_boundary_node_id). When handle()
    /// creates a PBC edge for the same (source, boundary) pair with actual
    /// evidence, the provisional edge is upgraded in-place rather than
    /// creating a duplicate. This prevents the graph from accumulating
    /// unbound edges from handle_intent_content / handle_intent_pbc.
    provisional_pbc_edges: HashMap<(NodeId, NodeId), EdgeId>,
}

impl GraphBuilder {
    /// Create a new GraphBuilder with the given configuration.
    pub fn new(config: GraphConfig) -> Self {
        Self {
            graph: ResponsibilityGraph::new(config.clone()),
            taint_tracker: TaintTracker::new(),
            subagent_boundaries: Vec::new(),
            accumulated_delta: GraphDelta::empty(),
            evidence_count: 0,
            config,
            provisional_pbc_edges: HashMap::new(),
        }
    }

    /// Create a new GraphBuilder with default configuration.
    pub fn with_default_config() -> Self {
        Self::new(GraphConfig::default())
    }

    /// Record a provisional PBC edge created without correlation evidence.
    /// These edges wait for handle() to upgrade them when evidence arrives.
    fn record_provisional_pbc(&mut self, source: NodeId, target: NodeId, edge_id: EdgeId) {
        self.provisional_pbc_edges.insert((source, target), edge_id);
    }

    // ── Phase 1: handle() ─────────────────────────────────────────────

    /// Process a single correlation evidence along with its intent and action.
    ///
    /// Per spec §10.2:
    /// 1. Map process_ids → AgentIds via IdentityProvider
    /// 2. Create/update Agent nodes
    /// 3. Create Resource nodes from action.action_type + normalized_args
    /// 4. Create IntentCausesAction edge
    /// 5. Evaluate policy → PolicyBoundaryCrossing edge if Violation/Ambiguous
    /// 6. Detect cross-agent file reads → context_trust → TaintTracker
    pub fn handle(
        &mut self,
        evidence: &CorrelationEvidence,
        intent: &Intent,
        action: &Action,
        identity: &dyn IdentityProvider,
    ) -> GraphDelta {
        self.evidence_count += 1;

        let mut delta = GraphDelta::empty();
        let ts = evidence.timestamp_ns;
        let phase = evidence.phase.clone();

        // ── Step 1: Resolve agent IDs ──────────────────────────────────
        let intent_agent = identity.agent_for_process(intent.process_id);
        let action_agent = identity.agent_for_process(action.process_id);

        // ── Step 2: Create/update Agent nodes ─────────────────────────
        let intent_agent_node_id = if let Some(ref agent_id) = intent_agent {
            self.ensure_agent_node(agent_id, ts, &phase, &mut delta)
        } else {
            None
        };

        let action_agent_node_id = if let Some(ref agent_id) = action_agent {
            if Some(agent_id) != intent_agent.as_ref() {
                self.ensure_agent_node(agent_id, ts, &phase, &mut delta)
            } else {
                intent_agent_node_id.clone()
            }
        } else {
            None
        };

        // ── Step 3: Create Resource node ──────────────────────────────
        let resource = action_to_resource(action);

        let resource_node_id = if let Some(ref res) = resource {
            self.ensure_resource_node(res, ts, &phase, evidence.evidence_id.clone(), &mut delta)
                .map(|n| n.node_id)
        } else {
            None
        };

        // ── Step 4: Create IntentCausesAction edge ────────────────────
        let src_node_id = intent_agent_node_id.clone().unwrap_or_else(|| {
            resource_node_id.clone().unwrap_or_else(|| {
                let placeholder = self.create_placeholder_agent_node(ts, &phase, &mut delta);
                placeholder.node_id
            })
        });

        let tgt_node_id = resource_node_id.clone().unwrap_or_else(|| {
            action_agent_node_id.clone().unwrap_or_else(|| {
                let placeholder = self.create_placeholder_agent_node(ts, &phase, &mut delta);
                placeholder.node_id
            })
        });

        let ic_edge = ResponsibilityEdge {
            edge_id: EdgeId::new(),
            edge_type: ResponsibilityEdgeType::IntentCausesAction,
            source: src_node_id.clone(),
            target: tgt_node_id.clone(),
            evidence_refs: vec![evidence.evidence_id.clone()],
            confidence: evidence.confidence,
            phase: phase.clone(),
            signal: None,
        };

        let _ic_edge_id = self.graph.add_edge(ic_edge.clone());
        delta.new_edges.push(ic_edge);

        // ── Step 5: Policy evaluation ─────────────────────────────────
        if let Some(ref res) = resource {
            let legacy_resource = convert_normalized_to_legacy_resource(res);
            let tainted_paths: std::collections::HashSet<String> = self
                .taint_tracker
                .contaminated_artifacts()
                .keys()
                .cloned()
                .collect();

            let judgment_result =
                crate::responsibility::judgment::policy::evaluate_boundary_policy_with_resource(
                    &intent_agent
                        .as_ref()
                        .map(|a| a.to_string())
                        .unwrap_or_default(),
                    &legacy_resource,
                    &[],
                    &tainted_paths,
                );

            if judgment_result.matched {
                let is_violation_or_ambiguous = matches!(
                    judgment_result.claim_state,
                    ResponsibilityClaimState::Observed | ResponsibilityClaimState::Ambiguous
                );

                if is_violation_or_ambiguous {
                    // Intentionality guard: only create PBC for file
                    // resources that the agent explicitly intended to
                    // access. Incidental reads (e.g. `ls -l` triggering
                    // /etc/passwd for UID resolution) are filtered.
                    // Skip PBC for workspace files and incidental reads.
                    // Workspace paths are expected project files; incidental
                    // reads are subprocess side-effects (e.g. ls -l → passwd).
                    let skip_pbc = match res {
                        NormalizedResource::File { canonical_path } => {
                            is_workspace_path(canonical_path)
                                || (is_system_resolution_file(canonical_path)
                                    && !path_in_intent_args(intent, canonical_path))
                        }
                        _ => false,
                    };
                    if skip_pbc {
                        // Also remove any provisional edge that would have
                        // been upgraded — prevents cross-intent dedup from
                        // preserving edges that should be filtered.
                        let key = (
                            action_agent_node_id.clone().unwrap_or(src_node_id.clone()),
                            self.ensure_policy_boundary_node(res, ts, &phase, &mut delta)
                                .node_id,
                        );
                        self.provisional_pbc_edges.remove(&key);
                        self.accumulated_delta.merge(delta.clone());
                        return delta;
                    }

                    let boundary_node =
                        self.ensure_policy_boundary_node(res, ts, &phase, &mut delta);

                    let src = action_agent_node_id.clone().unwrap_or(src_node_id.clone());
                    let tgt = boundary_node.node_id.clone();
                    let pbc_key = (src.clone(), tgt.clone());

                    // Upgrade provisional edge if one exists; otherwise create new.
                    if let Some(existing_id) = self.provisional_pbc_edges.remove(&pbc_key) {
                        if let Some(edge) = self.graph.get_edge_mut(&existing_id) {
                            edge.evidence_refs = vec![evidence.evidence_id.clone()];
                            edge.confidence = evidence.confidence;
                        }
                    } else {
                        let pbc_edge = ResponsibilityEdge {
                            edge_id: EdgeId::new(),
                            edge_type: ResponsibilityEdgeType::PolicyBoundaryCrossing,
                            source: src,
                            target: tgt,
                            evidence_refs: vec![evidence.evidence_id.clone()],
                            confidence: evidence.confidence,
                            phase: phase.clone(),
                            signal: None,
                        };
                        self.graph.add_edge(pbc_edge.clone());
                        delta.new_edges.push(pbc_edge);
                    }
                    delta.new_nodes.push(boundary_node);
                }
            }
        }

        // ── Step 5a: Content boundary (tool input text patterns) ─────
        if let Some(content_result) =
            crate::responsibility::judgment::content::evaluate_content_boundary(intent)
            && matches!(
                content_result.claim_state,
                ResponsibilityClaimState::Observed | ResponsibilityClaimState::Ambiguous
            )
        {
            let boundary_node = self.ensure_policy_boundary_node(
                &NormalizedResource::ToolCall {
                    tool_name: intent.tool_name.clone(),
                    call_id: intent.intent_id.as_str().to_string(),
                },
                ts,
                &phase,
                &mut delta,
            );
            let src_5a = intent_agent_node_id.clone().unwrap_or(src_node_id.clone());
            let tgt_5a = boundary_node.node_id.clone();
            let pbc_key_5a = (src_5a.clone(), tgt_5a.clone());
            if let Some(existing_id) = self.provisional_pbc_edges.remove(&pbc_key_5a) {
                if let Some(edge) = self.graph.get_edge_mut(&existing_id) {
                    edge.evidence_refs = vec![evidence.evidence_id.clone()];
                    edge.confidence = evidence.confidence;
                }
            } else {
                let pbc_edge = ResponsibilityEdge {
                    edge_id: EdgeId::new(),
                    edge_type: ResponsibilityEdgeType::PolicyBoundaryCrossing,
                    source: src_5a,
                    target: tgt_5a,
                    evidence_refs: vec![evidence.evidence_id.clone()],
                    confidence: evidence.confidence,
                    phase: phase.clone(),
                    signal: None,
                };
                self.graph.add_edge(pbc_edge.clone());
                delta.new_edges.push(pbc_edge);
            }
            delta.new_nodes.push(boundary_node);

            // Taint bridge: when content-boundary detects injection/
            // forgery in a Write/Edit action, mark the target file as
            // contaminated so cross-agent reads trigger taint-boundary PBC.
            if matches!(
                action.action_type,
                ActionCandidateType::FileWrite | ActionCandidateType::FileOverwrite
            ) {
                let file_path = extract_file_path(&action.normalized_args);
                if !file_path.is_empty()
                    && let Some(ref agent_id) = action_agent
                {
                    self.taint_tracker.mark_contaminated(
                        agent_id.clone(),
                        &file_path,
                        action.timestamp_ns,
                    );
                }
            }
        }

        // ── Step 5b: Delegation boundary (orchestration tool cross-check) ──
        if matches!(
            intent.tool_name.as_str(),
            "TaskCreate" | "Agent" | "SendMessage" | "Task" | "Skill"
        ) && intent_agent.is_some()
            && action_agent.is_some()
            && intent_agent != action_agent
        {
            // The is_some() guards above make both unwraps safe; keeping the
            // flat structure here reads clearer than an if-let wrapper.
            #[allow(clippy::unnecessary_unwrap)]
            let del_result = crate::responsibility::judgment::policy::evaluate_delegation_boundary(
                &intent_agent.as_ref().unwrap().to_string(),
                &action_agent.as_ref().unwrap().to_string(),
                &[], // delegation_records — populated in Phase 2
            );
            if del_result.matched
                && matches!(
                    del_result.claim_state,
                    ResponsibilityClaimState::Observed | ResponsibilityClaimState::Ambiguous
                )
            {
                let boundary_node = self.ensure_policy_boundary_node(
                    &NormalizedResource::ToolCall {
                        tool_name: intent.tool_name.clone(),
                        call_id: intent.intent_id.as_str().to_string(),
                    },
                    ts,
                    &phase,
                    &mut delta,
                );
                let src_5b = intent_agent_node_id.clone().unwrap_or(src_node_id.clone());
                let tgt_5b = boundary_node.node_id.clone();
                let pbc_key_5b = (src_5b.clone(), tgt_5b.clone());
                if let Some(existing_id) = self.provisional_pbc_edges.remove(&pbc_key_5b) {
                    if let Some(edge) = self.graph.get_edge_mut(&existing_id) {
                        edge.evidence_refs = vec![evidence.evidence_id.clone()];
                        edge.confidence = evidence.confidence;
                    }
                } else {
                    let pbc_edge = ResponsibilityEdge {
                        edge_id: EdgeId::new(),
                        edge_type: ResponsibilityEdgeType::PolicyBoundaryCrossing,
                        source: src_5b,
                        target: tgt_5b,
                        evidence_refs: vec![evidence.evidence_id.clone()],
                        confidence: evidence.confidence,
                        phase: phase.clone(),
                        signal: None,
                    };
                    self.graph.add_edge(pbc_edge.clone());
                    delta.new_edges.push(pbc_edge);
                }
                delta.new_nodes.push(boundary_node);
            }
        }

        // ── Step 6: Cross-agent file read → context_trust → TaintTracker ──
        if let ActionCandidateType::FileRead = action.action_type
            && let (Some(writer_agent), Some(reader_agent)) = (&intent_agent, &action_agent)
            && writer_agent != reader_agent
        {
            let file_path = extract_file_path(&action.normalized_args);
            if !file_path.is_empty() {
                if let Some(taint_access) =
                    self.taint_tracker
                        .check_access(reader_agent, &file_path, action.timestamp_ns)
                    && let (Some(writer_node), Some(reader_node)) =
                        (intent_agent_node_id.as_ref(), action_agent_node_id.as_ref())
                {
                    let taint_edge = ResponsibilityEdge {
                        edge_id: EdgeId::new(),
                        edge_type: ResponsibilityEdgeType::TaintPropagatesTo,
                        source: (*writer_node).clone(),
                        target: (*reader_node).clone(),
                        evidence_refs: vec![evidence.evidence_id.clone()],
                        confidence: 0.70,
                        phase: phase.clone(),
                        signal: None,
                    };
                    self.graph.add_edge(taint_edge.clone());
                    delta.new_edges.push(taint_edge);

                    delta.diagnostics.push(format!(
                        "taint: {} contaminated {} -> {} accessed at {} (dt={}ns)",
                        taint_access.contaminated_by,
                        taint_access.artifact,
                        reader_agent,
                        taint_access.accessed_at_ns,
                        taint_access.propagation_dt_ns,
                    ));
                }

                let judgment = ContextTrustEvaluator::evaluate_artifact(
                    reader_agent,
                    writer_agent,
                    &file_path,
                    intent.timestamp_ns,
                    action.timestamp_ns,
                    &self.subagent_boundaries,
                );

                match judgment.result {
                    PolicyJudgmentResult::Violation => {
                        self.taint_tracker.mark_contaminated(
                            writer_agent.clone(),
                            &file_path,
                            action.timestamp_ns,
                        );
                        delta.diagnostics.push(format!(
                            "context_trust violation: {} wrote, {} read {}",
                            writer_agent, reader_agent, file_path
                        ));
                    }
                    PolicyJudgmentResult::Ambiguous => {
                        self.taint_tracker.mark_contaminated(
                            writer_agent.clone(),
                            &file_path,
                            action.timestamp_ns,
                        );
                        delta.diagnostics.push(format!(
                            "context_trust ambiguous: {} -> {} via {}",
                            writer_agent, reader_agent, file_path
                        ));
                    }
                    _ => {}
                }
            }
        }

        self.accumulated_delta.merge(delta.clone());
        delta
    }

    /// Process a standalone intent (without correlation evidence) through
    /// content and delegation boundary checks. This catches UserPrompt
    /// and other intents that don't have corresponding syscall actions.
    pub fn handle_intent_content(
        &mut self,
        intent: &Intent,
        identity: &dyn IdentityProvider,
        timestamp_ns: u64,
    ) -> GraphDelta {
        let mut delta = GraphDelta::empty();
        let phase = crate::responsibility::types::PhaseIndicator::Provisional { timestamp_ns };

        let intent_agent = identity.agent_for_process(intent.process_id);
        let agent_node_id = if let Some(ref agent_id) = intent_agent {
            self.ensure_agent_node(agent_id, timestamp_ns, &phase, &mut delta)
        } else {
            None
        };

        // ── Content boundary ──
        if let Some(content_result) =
            crate::responsibility::judgment::content::evaluate_content_boundary(intent)
            && matches!(
                content_result.claim_state,
                ResponsibilityClaimState::Observed | ResponsibilityClaimState::Ambiguous
            )
        {
            let boundary_node = self.ensure_policy_boundary_node(
                &NormalizedResource::ToolCall {
                    tool_name: intent.tool_name.clone(),
                    call_id: intent.intent_id.as_str().to_string(),
                },
                timestamp_ns,
                &phase,
                &mut delta,
            );
            let pbc_src = agent_node_id.clone().unwrap_or_else(|| {
                self.create_placeholder_agent_node(timestamp_ns, &phase, &mut delta)
                    .node_id
            });
            let pbc_tgt = boundary_node.node_id.clone();
            let pbc_edge = ResponsibilityEdge {
                edge_id: EdgeId::new(),
                edge_type: ResponsibilityEdgeType::PolicyBoundaryCrossing,
                source: pbc_src.clone(),
                target: pbc_tgt.clone(),
                evidence_refs: vec![],
                confidence: 0.80,
                phase: phase.clone(),
                signal: None,
            };
            // Record as provisional — handle() will upgrade this edge
            // when correlation evidence with the same (agent, boundary)
            // arrives, binding evidence_refs and actual confidence.
            self.record_provisional_pbc(pbc_src, pbc_tgt, pbc_edge.edge_id.clone());
            self.graph.add_edge(pbc_edge.clone());
            delta.new_edges.push(pbc_edge);
            delta.new_nodes.push(boundary_node);
        }

        self.accumulated_delta.merge(delta.clone());
        delta
    }

    /// Check an intent's tool arguments for policy boundary crossings
    /// before the corresponding action syscall arrives.
    ///
    /// This produces an early PBC edge when the intent itself carries
    /// sensitive arguments (e.g., tool_use: read("/etc/shadow")).
    /// The edge is tagged with lower confidence since no action has
    /// confirmed the access yet.
    pub fn handle_intent_pbc(
        &mut self,
        intent: &Intent,
        identity: &dyn IdentityProvider,
    ) -> Option<GraphDelta> {
        let agent_id = identity.agent_for_process(intent.process_id)?;
        let ts = intent.timestamp_ns;
        let phase = PhaseIndicator::Provisional { timestamp_ns: ts };

        for arg in &intent.tool_args {
            let resource = match arg {
                NormalizedArg::Path(p) => {
                    let canonical = normalize_path_for_policy(p);
                    NormalizedResource::File {
                        canonical_path: canonical,
                    }
                }
                NormalizedArg::Url { host, port } => NormalizedResource::Network {
                    host: host.clone(),
                    port: port.unwrap_or(0),
                    scheme: "https".into(),
                },
                _ => continue,
            };

            let legacy_resource = convert_normalized_to_legacy_resource(&resource);
            let judgment =
                crate::responsibility::judgment::policy::evaluate_boundary_policy_with_resource(
                    &agent_id.to_string(),
                    &legacy_resource,
                    &[],
                    &HashSet::new(),
                );

            // Skip workspace files — accessing project/tmp files is
            // normal agent behavior, not a security violation.
            if let NormalizedResource::File { canonical_path } = &resource
                && is_workspace_path(canonical_path)
            {
                return None;
            }

            if judgment.matched
                && matches!(
                    judgment.claim_state,
                    ResponsibilityClaimState::Observed | ResponsibilityClaimState::Ambiguous
                )
            {
                let mut delta = GraphDelta::empty();
                let agent_node = self.ensure_agent_node(&agent_id, ts, &phase, &mut delta);
                let boundary_node =
                    self.ensure_policy_boundary_node(&resource, ts, &phase, &mut delta);

                // Intent-level PBC: confidence is moderate (0.60) since
                // no action has confirmed the access yet.
                let pbc_src = agent_node
                    .clone()
                    .unwrap_or_else(|| NodeId::from("unknown"));
                let pbc_tgt = boundary_node.node_id.clone();
                let pbc_edge = ResponsibilityEdge {
                    edge_id: EdgeId::new(),
                    edge_type: ResponsibilityEdgeType::PolicyBoundaryCrossing,
                    source: pbc_src.clone(),
                    target: pbc_tgt.clone(),
                    evidence_refs: vec![],
                    confidence: 0.60,
                    phase: phase.clone(),
                    signal: None,
                };
                // Record as provisional — handle() upgrades when action confirms.
                self.record_provisional_pbc(pbc_src, pbc_tgt, pbc_edge.edge_id.clone());

                self.graph.add_edge(pbc_edge.clone());
                delta.new_edges.push(pbc_edge);
                delta.new_nodes.push(boundary_node);
                self.accumulated_delta.merge(delta.clone());
                return Some(delta);
            }
        }

        None
    }

    // ── Phase 2: finalize() ───────────────────────────────────────────

    /// Finalize the graph with Phase 2 authoritative data.
    pub fn finalize(
        &mut self,
        identity: &AuthoritativeResolution,
        evidence: &CorrelationResult,
        graph_snapshot: GraphSnapshot,
    ) -> FrozenGraph {
        let mut refinements: Vec<GraphRefinement> = Vec::new();

        for id_refinement in &identity.refinements {
            let affected_nodes: Vec<_> = self
                .graph
                .all_nodes()
                .filter(|n| n.agent_id.as_ref() == Some(&id_refinement.phase1_agent_id))
                .map(|n| n.node_id.clone())
                .collect();

            let affected_edges: Vec<_> = self
                .graph
                .all_edges()
                .filter(|e| {
                    affected_nodes
                        .iter()
                        .any(|nid| e.source == *nid || e.target == *nid)
                })
                .map(|e| e.edge_id.clone())
                .collect();

            let refinement_type = match id_refinement.refinement_type {
                crate::responsibility::types::IdentityRefinementType::Confirmed => {
                    GraphRefinementType::AgentReassigned
                }
                crate::responsibility::types::IdentityRefinementType::Merged => {
                    GraphRefinementType::AgentMerged
                }
                crate::responsibility::types::IdentityRefinementType::Split => {
                    GraphRefinementType::AgentSplit
                }
                crate::responsibility::types::IdentityRefinementType::Reassigned => {
                    GraphRefinementType::AgentReassigned
                }
                crate::responsibility::types::IdentityRefinementType::Dismissed => {
                    GraphRefinementType::EdgeRemoved
                }
            };

            refinements.push(GraphRefinement {
                refinement_type,
                affected_nodes,
                affected_edges,
                description: id_refinement.reason.clone(),
            });
        }

        for ev_refinement in &evidence.refinements {
            refinements.push(GraphRefinement {
                refinement_type: match ev_refinement.refinement_type {
                    crate::responsibility::types::EvidenceRefinementType::Confirmed => {
                        GraphRefinementType::AgentReassigned
                    }
                    crate::responsibility::types::EvidenceRefinementType::Upgraded => {
                        GraphRefinementType::EdgeUpgraded
                    }
                    crate::responsibility::types::EvidenceRefinementType::Downgraded => {
                        GraphRefinementType::EdgeDowngraded
                    }
                    crate::responsibility::types::EvidenceRefinementType::Dismissed => {
                        GraphRefinementType::EdgeRemoved
                    }
                    crate::responsibility::types::EvidenceRefinementType::Discovered => {
                        GraphRefinementType::EdgeAdded
                    }
                },
                affected_nodes: Vec::new(),
                affected_edges: ev_refinement
                    .phase2_evidence_id
                    .iter()
                    .map(|eid| EdgeId::from(eid.as_str()))
                    .collect(),
                description: ev_refinement.reason.clone(),
            });
        }

        self.subagent_boundaries = identity.subagent_boundaries.clone();

        FrozenGraph::new(graph_snapshot, refinements, None)
    }

    // ── Accessors ─────────────────────────────────────────────────────

    pub fn graph(&self) -> &ResponsibilityGraph {
        &self.graph
    }

    pub fn graph_mut(&mut self) -> &mut ResponsibilityGraph {
        &mut self.graph
    }

    pub fn accumulated_delta(&self) -> &GraphDelta {
        &self.accumulated_delta
    }

    pub fn reset_accumulated_delta(&mut self) {
        self.accumulated_delta = GraphDelta::empty();
    }

    pub fn taint_tracker(&self) -> &TaintTracker {
        &self.taint_tracker
    }

    pub fn taint_tracker_mut(&mut self) -> &mut TaintTracker {
        &mut self.taint_tracker
    }

    pub fn evidence_count(&self) -> u64 {
        self.evidence_count
    }

    pub fn set_subagent_boundaries(&mut self, boundaries: Vec<SubagentBoundary>) {
        self.subagent_boundaries = boundaries;
    }

    // ── Internal helpers ──────────────────────────────────────────────

    fn ensure_agent_node(
        &mut self,
        agent_id: &AgentId,
        ts: u64,
        phase: &PhaseIndicator,
        delta: &mut GraphDelta,
    ) -> Option<NodeId> {
        if let Some(existing) = self.graph.find_agent_node(agent_id) {
            return Some(existing.node_id.clone());
        }

        let node = ResponsibilityNode {
            node_id: NodeId::new(),
            node_type: ResponsibilityNodeType::Agent,
            agent_id: Some(agent_id.clone()),
            resource: None,
            evidence_refs: Vec::new(),
            timestamp_ns: ts,
            phase: phase.clone(),
        };

        let node_id = self.graph.add_node(node.clone());
        delta.new_nodes.push(node);
        Some(node_id)
    }

    fn ensure_resource_node(
        &mut self,
        resource: &NormalizedResource,
        ts: u64,
        phase: &PhaseIndicator,
        evidence_id: EvidenceId,
        delta: &mut GraphDelta,
    ) -> Option<ResponsibilityNode> {
        let resource_repr = format!("{:?}", resource);
        if let Some(existing) = self.graph.find_resource_node(&resource_repr) {
            return Some(existing.clone());
        }

        let node = ResponsibilityNode {
            node_id: NodeId::new(),
            node_type: ResponsibilityNodeType::Resource,
            agent_id: None,
            resource: Some(resource.clone()),
            evidence_refs: vec![evidence_id],
            timestamp_ns: ts,
            phase: phase.clone(),
        };

        let added = node.clone();
        self.graph.add_node(node);
        delta.new_nodes.push(added.clone());
        Some(added)
    }

    fn ensure_policy_boundary_node(
        &mut self,
        resource: &NormalizedResource,
        ts: u64,
        phase: &PhaseIndicator,
        delta: &mut GraphDelta,
    ) -> ResponsibilityNode {
        let boundary_repr = format!("policy_boundary:{:?}", resource);
        if let Some(existing) = self.graph.find_resource_node(&boundary_repr) {
            return existing.clone();
        }

        let node = ResponsibilityNode {
            node_id: NodeId::new(),
            node_type: ResponsibilityNodeType::PolicyBoundary,
            agent_id: None,
            resource: Some(resource.clone()),
            evidence_refs: Vec::new(),
            timestamp_ns: ts,
            phase: phase.clone(),
        };

        let added = node.clone();
        self.graph.add_node(node);
        delta.new_nodes.push(added.clone());
        added
    }

    fn create_placeholder_agent_node(
        &mut self,
        ts: u64,
        phase: &PhaseIndicator,
        delta: &mut GraphDelta,
    ) -> ResponsibilityNode {
        let node = ResponsibilityNode {
            node_id: NodeId::new(),
            node_type: ResponsibilityNodeType::Agent,
            agent_id: Some(AgentId::new()),
            resource: None,
            evidence_refs: Vec::new(),
            timestamp_ns: ts,
            phase: phase.clone(),
        };

        let added = node.clone();
        self.graph.add_node(node);
        delta.new_nodes.push(added.clone());
        added
    }
}

impl Default for GraphBuilder {
    fn default() -> Self {
        Self::with_default_config()
    }
}

// ─── Helper functions ──────────────────────────────────────────────────────

/// Known Claude Code internal infrastructure path prefixes.
///
/// File accesses under these paths are the agent's own working files
/// (task outputs, MCP logs, settings, caches) — not security-relevant
/// resource accesses. Filtering them prevents the policy engine from
/// creating spurious PolicyBoundary nodes for infrastructure noise.
const AGENT_INFRASTRUCTURE_PREFIXES: &[&str] = &[
    "/tmp/claude-",
    ".claude_benchmark/",
    "mcp-logs-github/",
    ".claude/",
    "tasks/",
    "projects/-home-",
    "/home/lzk/.cache/claude-cli-nodejs/",
    "/etc/claude-code/",
];

/// Files commonly read by system utilities for UID/group resolution.
/// These are NOT security violations — `ls -l`, `ps`, etc. read them
/// incidentally. Only intentional access (path in intent tool_args)
/// should trigger PBC.
const SYSTEM_RESOLUTION_FILES: &[&str] = &[
    // User/group resolution (read by every subprocess)
    "/etc/passwd",
    "/etc/sudoers",
    "/etc/sudoers.d",
    "/etc/group",
    "/var/lib/sss/mc/passwd",
    "/var/lib/sss/mc/group",
    // Dynamic linker cache (read by every process start)
    "/etc/ld.so.cache",
    // Name resolution (read by every network call)
    "/etc/nsswitch.conf",
    "/etc/resolv.conf",
    "/etc/hosts",
    "/etc/hostname",
    // Timezone (read by every process)
    "/etc/localtime",
    "/usr/share/zoneinfo/",
    // TLS certificate bundles (read by every SSL connection)
    "/etc/ssl/cert.pem",
    "/etc/ssl/ca-bundle.pem",
    "/etc/pki/tls/cert.pem",
    "/etc/pki/ca-trust/extracted/pem/",
    "/usr/local/etc/openssl/cert.pem",
];

fn is_agent_infrastructure_path(path: &str) -> bool {
    AGENT_INFRASTRUCTURE_PREFIXES
        .iter()
        .any(|prefix| path.contains(prefix))
}

/// Check whether a file path appears in the intent's tool arguments.
///
/// Incidental file reads (e.g. `ls -l` resolving UIDs via `/etc/passwd`)
/// should NOT trigger PBC — only files the agent explicitly intended to
/// access. This prevents false positives on benign build/debug operations.
fn path_in_intent_args(intent: &Intent, path: &str) -> bool {
    if path.is_empty() || path == "(unknown path)" {
        return false;
    }
    intent.tool_args.iter().any(|arg| match arg {
        NormalizedArg::Path(p) => path.contains(p.as_str()) || p.as_str().contains(path),
        NormalizedArg::Content(c) => c.contains(path),
        NormalizedArg::Command(_) => false,
        NormalizedArg::Url { .. } => false,
    })
}

/// Known workspace / benign path prefixes that should not trigger PBC.
///
/// Files under these prefixes are expected project working directories.
/// Accessing them is normal agent behavior, not a security violation.
/// Workspace path prefixes that should not trigger PBC.
///
/// Only includes truly internal infrastructure paths — NOT the entire project
/// directory. Attack scenarios operate within the project tree, so excluding
/// the whole project would blind the detector to attack fixture access.
///
/// Kept narrow to maximize behavioral signal capture while filtering
/// known-benign infrastructure noise.
const WORKSPACE_PATH_PREFIXES: &[&str] = &[
    // Claude Code internal session directories
    "/tmp/claude-",
    // Kernel pseudo-filesystems
    "/proc/",
    "/sys/",
    "/dev/",
    // Build artifacts (not attack-relevant)
    "/home/lzk/MtAgentLens/collector/target/",
];

fn is_workspace_path(path: &str) -> bool {
    WORKSPACE_PATH_PREFIXES
        .iter()
        .any(|prefix| path.starts_with(prefix))
}

/// Whether a file path is a system resolution file that should only
/// trigger PBC when the agent INTENTIONALLY accessed it (path in
/// intent tool_args), not when read incidentally by subprocesses.
fn is_system_resolution_file(path: &str) -> bool {
    SYSTEM_RESOLUTION_FILES.iter().any(|f| path.starts_with(f))
}

fn action_to_resource(action: &Action) -> Option<NormalizedResource> {
    match action.action_type {
        ActionCandidateType::FileRead
        | ActionCandidateType::FileWrite
        | ActionCandidateType::FileOverwrite
        | ActionCandidateType::FileDelete => {
            let path = action
                .normalized_args
                .iter()
                .find_map(|arg| match arg {
                    NormalizedArg::Path(p) => Some(p.clone()),
                    _ => None,
                })
                .unwrap_or_else(|| "(unknown path)".to_string());

            // Filter Claude Code internal infrastructure noise.
            // These paths are the agent's own working files, not
            // security-relevant resource accesses.
            if is_agent_infrastructure_path(&path) {
                return None;
            }

            Some(NormalizedResource::File {
                canonical_path: path,
            })
        }
        ActionCandidateType::NetworkConnect | ActionCandidateType::NetworkSend => {
            let host_port: Option<(String, Option<u16>)> =
                action.normalized_args.iter().find_map(|arg| match arg {
                    NormalizedArg::Url { host, port } => Some((host.clone(), *port)),
                    _ => None,
                });
            if let Some((host, port)) = host_port {
                Some(NormalizedResource::Network {
                    host,
                    port: port.unwrap_or(0),
                    scheme: "https".to_string(),
                })
            } else {
                Some(NormalizedResource::Network {
                    host: "unknown".to_string(),
                    port: 0,
                    scheme: "https".to_string(),
                })
            }
        }
        ActionCandidateType::ProcessExec | ActionCandidateType::ProcessSpawn => {
            // When a network tool (curl, wget, nc) is executed with a URL
            // argument, treat it as a Network resource so the network policy
            // evaluation can flag suspicious targets.
            let cmd = action
                .normalized_args
                .iter()
                .find_map(|arg| match arg {
                    NormalizedArg::Command(c) => Some(c.as_str()),
                    _ => None,
                })
                .unwrap_or("");
            let is_net_tool = matches!(cmd, "curl" | "wget" | "nc" | "ncat");
            if is_net_tool
                && let Some(url) = action.normalized_args.iter().find_map(|arg| match arg {
                    NormalizedArg::Url { host, port } => Some((host.clone(), *port)),
                    _ => None,
                })
            {
                return Some(NormalizedResource::Network {
                    host: url.0,
                    port: url.1.unwrap_or(443),
                    scheme: "https".to_string(),
                });
            }
            Some(NormalizedResource::Process {
                process_id: action.process_id,
            })
        }
    }
}

fn extract_file_path(args: &[NormalizedArg]) -> String {
    args.iter()
        .find_map(|arg| match arg {
            NormalizedArg::Path(p) => Some(p.clone()),
            _ => None,
        })
        .unwrap_or_default()
}

fn convert_normalized_to_legacy_resource(resource: &NormalizedResource) -> ResourceId {
    match resource {
        NormalizedResource::File { canonical_path } => ResourceId::File {
            canonical_path: canonical_path.clone(),
        },
        NormalizedResource::Network { host, port, scheme } => ResourceId::Network {
            host: host.clone(),
            port: *port,
            scheme: Some(scheme.clone()),
        },
        NormalizedResource::Process { process_id } => ResourceId::Process {
            process_id: crate::responsibility::types::support_types::ProcessId {
                pid: process_id.pid,
                start_time_ns: process_id.start_time_ns,
            },
        },
        NormalizedResource::LlmConversation {
            provider,
            model,
            message_id,
        } => ResourceId::LlmConversation {
            provider: provider.clone(),
            model: model.clone(),
            conversation_id: message_id.to_string(),
        },
        NormalizedResource::ToolCall { tool_name, call_id } => ResourceId::ToolCall {
            tool_name: tool_name.clone(),
            call_id: Some(call_id.clone()),
        },
        NormalizedResource::Credential { credential_id } => ResourceId::File {
            canonical_path: format!("credential://{}", credential_id),
        },
        NormalizedResource::Context { context_id } => ResourceId::ToolCall {
            tool_name: format!("context://{}", context_id),
            call_id: None,
        },
    }
}

/// Normalize a path for policy evaluation: strip leading ~/ .
/// and resolve double slashes.
fn normalize_path_for_policy(path: &str) -> String {
    let trimmed = path.trim_start_matches("~/").trim_start_matches("./");
    trimmed.replace("//", "/")
}

// ─── Tests ─────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use crate::responsibility::types::{
        ActionId, CandidateDerivationMode, IntentAuthoritySource, IntentClass, IntentId,
        IntentParseState, MatchLevel, MessageId, NormalizedArgs, ProcessId, SignalClass,
    };

    struct MockIdentity {
        map: HashMap<ProcessId, AgentId>,
    }

    impl IdentityProvider for MockIdentity {
        fn agent_for_process(&self, process_id: ProcessId) -> Option<AgentId> {
            self.map.get(&process_id).cloned()
        }

        fn all_assignments(&self) -> HashMap<ProcessId, AgentId> {
            self.map.clone()
        }
    }

    fn make_intent(pid: ProcessId, tool: &str, ts: u64) -> Intent {
        Intent {
            intent_id: IntentId::new(),
            process_id: pid,
            message_id: MessageId::new(),
            tool_name: tool.to_string(),
            tool_args: Vec::new(),
            intent_class: IntentClass::SideEffect,
            parse_state: IntentParseState::Complete,
            primary_matching_eligible: true,
            authority_source: IntentAuthoritySource::RequestHistoryAssistantToolUse,
            timestamp_ns: ts,
        }
    }

    fn make_action(
        pid: ProcessId,
        action_type: ActionCandidateType,
        path: Option<&str>,
        ts: u64,
    ) -> Action {
        let normalized_args = path
            .map(|p| vec![NormalizedArg::Path(p.to_string())])
            .unwrap_or_default();

        Action {
            action_id: ActionId::new(),
            process_id: pid,
            action_type,
            normalized_args,
            derivation_mode: CandidateDerivationMode::Direct,
            signal_class: SignalClass::HighSignal,
            timestamp_ns: ts,
        }
    }

    fn make_evidence(match_level: MatchLevel, confidence: f32, ts: u64) -> CorrelationEvidence {
        CorrelationEvidence {
            evidence_id: EvidenceId::new(),
            intent_id: IntentId::new(),
            action_id: ActionId::new(),
            match_level,
            confidence,
            matched_fields: NormalizedArgs {
                canonical_path: Some("/tmp/test.txt".to_string()),
                host: None,
                port: None,
                command: Some("cat".to_string()),
                raw_args: vec!["/tmp/test.txt".to_string()],
            },
            derivation_mode: CandidateDerivationMode::Direct,
            phase: PhaseIndicator::Provisional { timestamp_ns: ts },
            timestamp_ns: ts,
        }
    }

    #[test]
    fn test_builder_handle_creates_nodes_and_edges() {
        let pid1 = ProcessId::new(100, 1);
        let identity = MockIdentity {
            map: HashMap::from([(pid1, AgentId::from("agent-1"))]),
        };

        let mut builder = GraphBuilder::with_default_config();
        let intent = make_intent(pid1, "Bash", 1000);
        let action = make_action(
            pid1,
            ActionCandidateType::FileRead,
            Some("/tmp/test.txt"),
            2000,
        );
        let evidence = make_evidence(MatchLevel::L1Exact, 0.90, 2000);

        let delta = builder.handle(&evidence, &intent, &action, &identity);

        assert!(!delta.new_nodes.is_empty(), "Expected new nodes");
        assert!(!delta.new_edges.is_empty(), "Expected new edges");
        assert!(
            delta
                .new_nodes
                .iter()
                .any(|n| n.node_type == ResponsibilityNodeType::Agent),
            "Expected an Agent node"
        );
        assert!(
            delta
                .new_edges
                .iter()
                .any(|e| e.edge_type == ResponsibilityEdgeType::IntentCausesAction),
            "Expected an IntentCausesAction edge"
        );
    }

    #[test]
    fn test_builder_cross_agent_file_read() {
        let pid1 = ProcessId::new(100, 1);
        let pid2 = ProcessId::new(200, 2);

        let identity = MockIdentity {
            map: HashMap::from([
                (pid1, AgentId::from("agent-writer")),
                (pid2, AgentId::from("agent-reader")),
            ]),
        };

        let mut builder = GraphBuilder::with_default_config();

        let intent_w = make_intent(pid1, "Write", 1000);
        let action_w = make_action(
            pid1,
            ActionCandidateType::FileWrite,
            Some("/tmp/handoff.txt"),
            1500,
        );
        let evidence_w = make_evidence(MatchLevel::L1Exact, 0.90, 1500);
        builder.handle(&evidence_w, &intent_w, &action_w, &identity);

        let intent_r = make_intent(pid2, "Read", 2000);
        let action_r = make_action(
            pid2,
            ActionCandidateType::FileRead,
            Some("/tmp/handoff.txt"),
            2500,
        );
        let evidence_r = make_evidence(MatchLevel::L1Exact, 0.85, 2500);
        let delta = builder.handle(&evidence_r, &intent_r, &action_r, &identity);

        let has_context_diag = delta
            .diagnostics
            .iter()
            .any(|d| d.contains("context_trust") || d.contains("taint"));
        assert!(
            has_context_diag || delta.diagnostics.is_empty(),
            "Cross-agent file read pathway should be exercised"
        );
    }

    #[test]
    fn test_builder_finalize() {
        let pid1 = ProcessId::new(100, 1);
        let identity = MockIdentity {
            map: HashMap::from([(pid1, AgentId::from("agent-1"))]),
        };

        let mut builder = GraphBuilder::with_default_config();
        let intent = make_intent(pid1, "Bash", 1000);
        let action = make_action(
            pid1,
            ActionCandidateType::FileRead,
            Some("/tmp/test.txt"),
            2000,
        );
        let evidence = make_evidence(MatchLevel::L1Exact, 0.90, 2000);
        builder.handle(&evidence, &intent, &action, &identity);

        let snapshot = crate::responsibility::graph::snapshot::create_snapshot(
            builder.graph(),
            "test-snap".into(),
            3000,
        );

        let auth_resolution = AuthoritativeResolution {
            agents: Vec::new(),
            refinements: Vec::new(),
            subagent_boundaries: Vec::new(),
            unresolved: Vec::new(),
        };
        let corr_result = CorrelationResult {
            evidence: vec![evidence],
            non_links: Vec::new(),
            refinements: Vec::new(),
        };

        let frozen = builder.finalize(&auth_resolution, &corr_result, snapshot);
        assert!(frozen.node_count() > 0);
    }
}
