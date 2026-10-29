// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 MtAgentLens Project

use crate::responsibility::claims::ClaimSeverity;
use crate::responsibility::graph::graph::ResponsibilityGraph;
use crate::responsibility::types::graph_types::{
    ResourceKind, ResponsibilityEdgeType, ResponsibilityNodeType,
};
use crate::responsibility::types::{NodeId, ResponsibilityEdge, ResponsibilityNode};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GraphPatternTemplate {
    pub template_id: String,
    pub description: String,
    pub severity: ClaimSeverity,
    pub required_node_types: Vec<ResponsibilityNodeType>,
    pub required_edge_types: Vec<ResponsibilityEdgeType>,
    /// Resource kinds that must each be present. Node types alone cannot tell
    /// a file write from a network send, so this is what makes the templates
    /// distinguishable.
    #[serde(default)]
    pub required_resource_kinds: Vec<ResourceKind>,
    /// Edges that must target a specific resource kind (e.g. the boundary
    /// crossing in an exfiltration must be the network one).
    #[serde(default)]
    pub required_edge_target_kinds: Vec<(ResponsibilityEdgeType, ResourceKind)>,
    /// Optional path-token constraint. A template that claims "log
    /// manipulation" must match a log-like target, not any file: the
    /// detection-evasion template used to fire on every file write that
    /// crossed a boundary.
    #[serde(default)]
    pub resource_path_tokens: Vec<String>,
    pub min_matches: usize,
    pub min_distinct_agents: usize,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct GraphPatternMatch {
    pub template_id: String,
    pub matched_node_ids: Vec<String>,
    pub matched_edge_ids: Vec<String>,
    pub total_edges_in_template: usize,
    pub confidence: f32,
    pub severity: ClaimSeverity,
    pub description: String,
}

pub struct GraphPatternMatcher {
    templates: Vec<GraphPatternTemplate>,
}

impl GraphPatternMatcher {
    pub fn new(templates: Vec<GraphPatternTemplate>) -> Self {
        Self { templates }
    }

    pub fn match_graph(&self, graph: &ResponsibilityGraph) -> Vec<GraphPatternMatch> {
        self.templates
            .iter()
            .flat_map(|template| self.try_match(graph, template))
            .collect()
    }

    fn try_match(
        &self,
        graph: &ResponsibilityGraph,
        template: &GraphPatternTemplate,
    ) -> Vec<GraphPatternMatch> {
        use std::collections::{BTreeMap, HashMap};

        // The evidence must be one connected subgraph, not types that merely
        // exist somewhere in the graph. Without this, a benign file read in
        // one action and a network connect in another satisfied
        // `data_exfiltration`.
        let required_edges: Vec<&ResponsibilityEdge> = graph
            .all_edges()
            .filter(|edge| template.required_edge_types.contains(&edge.edge_type))
            .collect();
        if !template.required_edge_types.is_empty() && required_edges.is_empty() {
            return Vec::new();
        }

        fn find(parent: &mut HashMap<NodeId, NodeId>, node: &NodeId) -> NodeId {
            let mut current = node.clone();
            let mut path = Vec::new();
            while let Some(next) = parent.get(&current).filter(|next| **next != current) {
                path.push(current.clone());
                current = next.clone();
            }
            parent
                .entry(current.clone())
                .or_insert_with(|| current.clone());
            for item in path {
                parent.insert(item, current.clone());
            }
            current
        }
        let mut parent = HashMap::new();
        for edge in &required_edges {
            let source = find(&mut parent, &edge.source);
            let target = find(&mut parent, &edge.target);
            if source != target {
                parent.insert(source, target);
            }
        }
        let mut components: BTreeMap<String, Vec<&ResponsibilityEdge>> = BTreeMap::new();
        for edge in required_edges {
            components
                .entry(find(&mut parent, &edge.source).to_string())
                .or_default()
                .push(edge);
        }
        components
            .into_values()
            .filter_map(|mut edges| {
                edges.sort_by(|a, b| a.edge_id.as_str().cmp(b.edge_id.as_str()));
                self.match_component(graph, template, &edges)
            })
            .collect()
    }

    fn match_component(
        &self,
        graph: &ResponsibilityGraph,
        template: &GraphPatternTemplate,
        required_edges: &[&ResponsibilityEdge],
    ) -> Option<GraphPatternMatch> {
        use std::collections::HashSet;
        let endpoints: HashSet<&NodeId> = required_edges
            .iter()
            .flat_map(|edge| [&edge.source, &edge.target])
            .collect();
        let endpoint_nodes: Vec<&ResponsibilityNode> = graph
            .all_nodes()
            .filter(|node| endpoints.contains(&node.node_id))
            .collect();

        if template
            .required_node_types
            .iter()
            .any(|kind| !endpoint_nodes.iter().any(|node| node.node_type == *kind))
        {
            return None;
        }

        // Every required resource kind must be carried by a node that is part
        // of the evidence, not by an unrelated node elsewhere in the graph.
        for kind in &template.required_resource_kinds {
            let present = endpoint_nodes.iter().any(|node| {
                node.resource
                    .as_ref()
                    .is_some_and(|resource| kind.matches(resource))
            });
            if !present {
                return None;
            }
        }

        // Tie specific edges to the resource they act on: the boundary
        // crossing in an exfiltration must be the network one, not a benign
        // SSH read.
        for (edge_type, kind) in &template.required_edge_target_kinds {
            let matched = required_edges
                .iter()
                .filter(|edge| edge.edge_type == *edge_type)
                .any(|edge| {
                    endpoint_nodes
                        .iter()
                        .find(|node| node.node_id == edge.target)
                        .and_then(|node| node.resource.as_ref())
                        .is_some_and(|resource| kind.matches(resource))
                });
            if !matched {
                return None;
            }
        }

        let found_edge_types = template
            .required_edge_types
            .iter()
            .filter(|kind| required_edges.iter().any(|edge| edge.edge_type == **kind))
            .count();
        if found_edge_types < template.min_matches {
            return None;
        }

        let distinct_agents: HashSet<&str> = endpoint_nodes
            .iter()
            .filter(|node| node.node_type == ResponsibilityNodeType::Agent)
            .filter_map(|node| node.agent_id.as_ref().map(|agent| agent.as_str()))
            // Unresolved evidence is not a second agent. Counting it
            // satisfied `min_distinct_agents=2` in multi-agent templates.
            .filter(|agent| *agent != crate::responsibility::graph::UNATTRIBUTED_AGENT_ID)
            .collect();
        if distinct_agents.len() < template.min_distinct_agents {
            return None;
        }

        if template.min_distinct_agents >= 2 {
            // The cross-agent relationship must connect two nodes of the
            // matched subgraph. Scanning only `required_edges` made this
            // check dead: those are filtered to the template's own edge
            // types (never a delegation edge), so `has_cross_agent_edge` was
            // always false and no multi-agent template could ever match.
            let has_cross_agent_edge = graph.all_edges().any(|edge| {
                matches!(
                    edge.edge_type,
                    ResponsibilityEdgeType::Delegation
                        | ResponsibilityEdgeType::AgentDelegatesTo
                        | ResponsibilityEdgeType::ContextInheritance
                        | ResponsibilityEdgeType::ContextFlowsFrom
                        | ResponsibilityEdgeType::TaintPropagation
                        | ResponsibilityEdgeType::TaintPropagatesTo
                ) && endpoints.contains(&edge.source)
                    && endpoints.contains(&edge.target)
            });
            if !has_cross_agent_edge {
                return None;
            }
        }

        if !template.resource_path_tokens.is_empty() {
            let matches_a_token = endpoint_nodes.iter().any(|node| {
                node.resource.as_ref().is_some_and(|resource| {
                    let text = format!("{resource:?}").to_lowercase();
                    template
                        .resource_path_tokens
                        .iter()
                        .any(|token| text.contains(&token.to_lowercase()))
                })
            });
            if !matches_a_token {
                return None;
            }
        }

        // Confidence describes how completely the template was satisfied and
        // how strong the edges behind it are. It used to be the constant 1.0,
        // so one weak edge scored the same as a full, high-confidence match —
        // D2 (content) then read 1.0 for every run that matched anything.
        let required_count = template.required_edge_types.len().max(1);
        let completeness = found_edge_types as f32 / required_count as f32;
        let best_edge_confidence = template
            .required_edge_types
            .iter()
            .filter_map(|kind| {
                required_edges
                    .iter()
                    .filter(|edge| edge.edge_type == *kind)
                    .map(|edge| edge.confidence)
                    .reduce(f32::max)
            })
            .fold(0.0f32, f32::max);
        let confidence = (completeness * best_edge_confidence).clamp(0.0, 1.0);
        Some(GraphPatternMatch {
            template_id: template.template_id.clone(),
            matched_node_ids: endpoint_nodes
                .iter()
                .map(|node| node.node_id.to_string())
                .collect(),
            matched_edge_ids: required_edges
                .iter()
                .map(|edge| edge.edge_id.to_string())
                .collect(),
            total_edges_in_template: template.required_edge_types.len(),
            confidence,
            severity: template.severity.clone(),
            description: template.description.clone(),
        })
    }
}

pub fn default_templates() -> Vec<GraphPatternTemplate> {
    use ResourceKind as RK;
    use ResponsibilityEdgeType::*;
    use ResponsibilityNodeType::*;

    // Vocabulary rule: every node type, edge type and resource kind here must
    // have a producer in graph_builder.rs. `collector/tests/graph_pattern_contract.rs`
    // fails otherwise. `IntentCausesAction` is the observable "capability use"
    // (an intent caused an action on a resource); `AgentDelegatesTo` and
    // `ContextFlowsFrom` are emitted for observed cross-agent orchestration;
    // `TaintPropagatesTo` and `PolicyBoundaryCrossing` come from the builder and
    // the policy engine.
    vec![
        GraphPatternTemplate {
            template_id: "indirect_prompt_injection_tool_abuse".into(),
            description: "Indirect prompt injection causes agent to misuse tools".into(),
            severity: ClaimSeverity::Critical,
            required_node_types: vec![Agent, Resource],
            required_edge_types: vec![IntentCausesAction, PolicyBoundaryCrossing],
            required_resource_kinds: vec![RK::ToolCall],
            required_edge_target_kinds: vec![(PolicyBoundaryCrossing, RK::ToolCall)],
            resource_path_tokens: Vec::new(),
            min_matches: 2,
            min_distinct_agents: 1,
        },
        GraphPatternTemplate {
            template_id: "data_exfiltration".into(),
            description: "Agent reads sensitive resource and sends data externally".into(),
            severity: ClaimSeverity::Critical,
            required_node_types: vec![Agent, Resource],
            required_edge_types: vec![IntentCausesAction, PolicyBoundaryCrossing],
            // File + Network is what separates exfiltration from ordinary use.
            required_resource_kinds: vec![RK::File, RK::Network],
            required_edge_target_kinds: vec![(PolicyBoundaryCrossing, RK::Network)],
            resource_path_tokens: Vec::new(),
            min_matches: 2,
            min_distinct_agents: 1,
        },
        GraphPatternTemplate {
            template_id: "credential_theft_delegation".into(),
            description: "Agent reads credentials and delegates to another agent".into(),
            severity: ClaimSeverity::Critical,
            required_node_types: vec![Agent, Resource],
            required_edge_types: vec![IntentCausesAction, AgentDelegatesTo, PolicyBoundaryCrossing],
            required_resource_kinds: vec![RK::Credential, RK::Network],
            required_edge_target_kinds: vec![
                (IntentCausesAction, RK::Credential),
                (PolicyBoundaryCrossing, RK::Network),
            ],
            resource_path_tokens: Vec::new(),
            min_matches: 2,
            min_distinct_agents: 2,
        },
        GraphPatternTemplate {
            template_id: "context_pollution_confused_deputy".into(),
            description: "Agent pollutes context causing downstream agent to act on tainted data"
                .into(),
            severity: ClaimSeverity::Warning,
            required_node_types: vec![Agent, Resource],
            required_edge_types: vec![TaintPropagatesTo, ContextFlowsFrom],
            required_resource_kinds: vec![RK::File],
            required_edge_target_kinds: vec![(TaintPropagatesTo, RK::File)],
            resource_path_tokens: Vec::new(),
            min_matches: 2,
            min_distinct_agents: 2,
        },
        GraphPatternTemplate {
            template_id: "supply_chain_attack".into(),
            description: "Compromised tool or dependency introduces malicious behavior".into(),
            severity: ClaimSeverity::Critical,
            required_node_types: vec![Agent, Resource],
            required_edge_types: vec![IntentCausesAction, PolicyBoundaryCrossing],
            required_resource_kinds: vec![RK::File, RK::Process],
            required_edge_target_kinds: vec![(IntentCausesAction, RK::Process)],
            resource_path_tokens: Vec::new(),
            min_matches: 2,
            min_distinct_agents: 2,
        },
        GraphPatternTemplate {
            template_id: "persistence_backdoor".into(),
            description: "Agent creates persistent backdoor through resource modification".into(),
            severity: ClaimSeverity::Critical,
            required_node_types: vec![Agent, Resource],
            required_edge_types: vec![IntentCausesAction, PolicyBoundaryCrossing],
            required_resource_kinds: vec![RK::File, RK::Process],
            required_edge_target_kinds: vec![(IntentCausesAction, RK::File)],
            resource_path_tokens: Vec::new(),
            min_matches: 2,
            min_distinct_agents: 1,
        },
        GraphPatternTemplate {
            template_id: "reconnaissance".into(),
            description: "Agent probes resources across a policy boundary".into(),
            severity: ClaimSeverity::Info,
            required_node_types: vec![Agent, Resource],
            // A plain file read is not reconnaissance: it fired in 5/6 benign
            // scenarios of the 2026-10-19 sample. Requiring a boundary
            // crossing makes the template say "probing that matters".
            // `min_matches` counts distinct edge types, not occurrences;
            // counting repeated probing still needs an occurrence-based
            // matcher (recorded in ADR 0012).
            required_edge_types: vec![IntentCausesAction, PolicyBoundaryCrossing],
            required_resource_kinds: vec![RK::File],
            required_edge_target_kinds: vec![
                (IntentCausesAction, RK::File),
                (PolicyBoundaryCrossing, RK::File),
            ],
            resource_path_tokens: Vec::new(),
            min_matches: 2,
            min_distinct_agents: 1,
        },
        GraphPatternTemplate {
            template_id: "detection_evasion".into(),
            description: "Agent attempts to evade detection through log manipulation".into(),
            severity: ClaimSeverity::Warning,
            required_node_types: vec![Agent, Resource],
            required_edge_types: vec![IntentCausesAction, PolicyBoundaryCrossing],
            required_resource_kinds: vec![RK::File],
            required_edge_target_kinds: vec![(IntentCausesAction, RK::File)],
            resource_path_tokens: vec![
                "log".into(),
                "history".into(),
                "audit".into(),
                "wtmp".into(),
                "utmp".into(),
            ],
            min_matches: 2,
            min_distinct_agents: 1,
        },
        GraphPatternTemplate {
            template_id: "multi_agent_collusion".into(),
            description: "Multiple agents collude to share resources and delegate authority".into(),
            severity: ClaimSeverity::Critical,
            required_node_types: vec![Agent, Resource],
            required_edge_types: vec![IntentCausesAction, AgentDelegatesTo],
            required_resource_kinds: vec![RK::File],
            required_edge_target_kinds: vec![(IntentCausesAction, RK::File)],
            resource_path_tokens: Vec::new(),
            min_matches: 2,
            min_distinct_agents: 2,
        },
        GraphPatternTemplate {
            template_id: "trust_boundary_crossing".into(),
            description: "Agent crosses trust boundary through delegation chain".into(),
            severity: ClaimSeverity::Warning,
            required_node_types: vec![Agent, PolicyBoundary],
            required_edge_types: vec![AgentDelegatesTo, PolicyBoundaryCrossing],
            required_resource_kinds: vec![RK::ToolCall],
            required_edge_target_kinds: vec![(PolicyBoundaryCrossing, RK::ToolCall)],
            resource_path_tokens: Vec::new(),
            min_matches: 2,
            min_distinct_agents: 2,
        },
        GraphPatternTemplate {
            template_id: "session_hijacking".into(),
            description: "Agent hijacks another session via context inheritance".into(),
            severity: ClaimSeverity::Critical,
            required_node_types: vec![Agent, Resource],
            required_edge_types: vec![ContextFlowsFrom, IntentCausesAction],
            required_resource_kinds: vec![RK::ToolCall],
            required_edge_target_kinds: vec![(IntentCausesAction, RK::ToolCall)],
            resource_path_tokens: Vec::new(),
            min_matches: 2,
            min_distinct_agents: 2,
        },
        GraphPatternTemplate {
            template_id: "cross_modal_attack".into(),
            description: "Attack propagates across modalities via tainted resources".into(),
            severity: ClaimSeverity::Critical,
            required_node_types: vec![Agent, Resource],
            required_edge_types: vec![IntentCausesAction, TaintPropagatesTo],
            required_resource_kinds: vec![RK::File, RK::Network],
            required_edge_target_kinds: vec![(IntentCausesAction, RK::File)],
            resource_path_tokens: Vec::new(),
            min_matches: 2,
            min_distinct_agents: 1,
        },
    ]
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::responsibility::graph::config::GraphConfig;
    use crate::responsibility::types::{
        AgentId, EdgeId, EvidenceId, NodeId, NormalizedResource, PhaseIndicator,
        ResponsibilityEdge, ResponsibilityNode,
    };

    fn phase() -> PhaseIndicator {
        PhaseIndicator::Provisional { timestamp_ns: 1 }
    }

    fn agent(graph: &mut ResponsibilityGraph, id: &str) -> NodeId {
        let node_id = NodeId::new();
        graph.add_node(ResponsibilityNode {
            node_id: node_id.clone(),
            node_type: ResponsibilityNodeType::Agent,
            agent_id: Some(AgentId::from(id)),
            resource: None,
            evidence_refs: vec![EvidenceId::new()],
            timestamp_ns: 1,
            phase: phase(),
        });
        node_id
    }

    fn resource(graph: &mut ResponsibilityGraph, kind: ResourceKind) -> NodeId {
        let resource = match kind {
            ResourceKind::File => NormalizedResource::File {
                canonical_path: "/tmp/secret.txt".to_string(),
            },
            ResourceKind::Network => NormalizedResource::Network {
                host: "198.51.100.7".to_string(),
                port: 443,
                scheme: "https".to_string(),
            },
            ResourceKind::Process => NormalizedResource::Process {
                process_id: crate::responsibility::types::ProcessId::new(7, 1),
            },
            ResourceKind::ToolCall => NormalizedResource::ToolCall {
                tool_name: "TaskCreate".to_string(),
                call_id: "call-1".to_string(),
            },
            ResourceKind::Credential => NormalizedResource::Credential {
                credential_id: "/home/agent/.ssh/id_rsa".to_string(),
            },
            ResourceKind::Conversation => NormalizedResource::LlmConversation {
                provider: "anthropic".to_string(),
                model: "claude".to_string(),
                message_id: crate::responsibility::types::MessageId::new(),
            },
            ResourceKind::Context => NormalizedResource::Context {
                context_id: "ctx-1".to_string(),
            },
        };
        let node_id = NodeId::new();
        // The builder puts ToolCall resources on PolicyBoundary nodes (the
        // boundary the call crossed); mirror that here.
        let node_type = if kind == ResourceKind::ToolCall {
            ResponsibilityNodeType::PolicyBoundary
        } else {
            ResponsibilityNodeType::Resource
        };
        graph.add_node(ResponsibilityNode {
            node_id: node_id.clone(),
            node_type,
            agent_id: None,
            resource: Some(resource),
            evidence_refs: vec![EvidenceId::new()],
            timestamp_ns: 1,
            phase: phase(),
        });
        node_id
    }

    fn edge(
        graph: &mut ResponsibilityGraph,
        edge_type: ResponsibilityEdgeType,
        source: NodeId,
        target: NodeId,
    ) {
        graph.add_edge(ResponsibilityEdge {
            edge_id: EdgeId::new(),
            edge_type,
            operation: None,
            source,
            target,
            evidence_refs: vec![EvidenceId::new()],
            confidence: 0.9,
            phase: phase(),
        });
    }

    fn matched_ids(graph: &ResponsibilityGraph) -> Vec<String> {
        GraphPatternMatcher::new(default_templates())
            .match_graph(graph)
            .into_iter()
            .map(|m| m.template_id)
            .collect()
    }

    #[test]
    fn detection_evasion_requires_a_log_like_target() {
        let mut graph = ResponsibilityGraph::new(GraphConfig::default());
        let a = agent(&mut graph, "agent-a");
        let file = resource(&mut graph, ResourceKind::File); // /tmp/secret.txt
        edge(
            &mut graph,
            ResponsibilityEdgeType::IntentCausesAction,
            a.clone(),
            file.clone(),
        );
        edge(
            &mut graph,
            ResponsibilityEdgeType::PolicyBoundaryCrossing,
            a.clone(),
            file.clone(),
        );
        assert!(
            !matched_ids(&graph).contains(&"detection_evasion".to_string()),
            "an ordinary file write is not log manipulation"
        );

        let mut log_graph = ResponsibilityGraph::new(GraphConfig::default());
        let a = agent(&mut log_graph, "agent-a");
        let log = resource_with_path(&mut log_graph, "/tmp/project/logs/audit.log");
        edge(
            &mut log_graph,
            ResponsibilityEdgeType::IntentCausesAction,
            a.clone(),
            log.clone(),
        );
        edge(
            &mut log_graph,
            ResponsibilityEdgeType::PolicyBoundaryCrossing,
            a.clone(),
            log.clone(),
        );
        assert!(
            matched_ids(&log_graph).contains(&"detection_evasion".to_string()),
            "log manipulation must still match"
        );
    }

    fn resource_with_path(graph: &mut ResponsibilityGraph, path: &str) -> NodeId {
        let node_id = NodeId::new();
        graph.add_node(ResponsibilityNode {
            node_id: node_id.clone(),
            node_type: ResponsibilityNodeType::Resource,
            agent_id: None,
            resource: Some(NormalizedResource::File {
                canonical_path: path.to_string(),
            }),
            evidence_refs: vec![EvidenceId::new()],
            timestamp_ns: 1,
            phase: phase(),
        });
        node_id
    }

    /// The discriminating requirement: file + network. A file-only graph must
    /// not be called exfiltration.
    #[test]
    fn data_exfiltration_requires_both_file_and_network() {
        let mut graph = ResponsibilityGraph::new(GraphConfig::default());
        let a = agent(&mut graph, "agent-a");
        let file = resource(&mut graph, ResourceKind::File);
        let net = resource(&mut graph, ResourceKind::Network);
        edge(
            &mut graph,
            ResponsibilityEdgeType::IntentCausesAction,
            a.clone(),
            file.clone(),
        );
        edge(
            &mut graph,
            ResponsibilityEdgeType::PolicyBoundaryCrossing,
            a.clone(),
            net.clone(),
        );
        assert!(matched_ids(&graph).contains(&"data_exfiltration".to_string()));

        let mut file_only = ResponsibilityGraph::new(GraphConfig::default());
        let a2 = agent(&mut file_only, "agent-a");
        let f2 = resource(&mut file_only, ResourceKind::File);
        edge(
            &mut file_only,
            ResponsibilityEdgeType::IntentCausesAction,
            a2.clone(),
            f2.clone(),
        );
        edge(
            &mut file_only,
            ResponsibilityEdgeType::PolicyBoundaryCrossing,
            a2,
            f2,
        );
        assert!(!matched_ids(&file_only).contains(&"data_exfiltration".to_string()));
    }

    /// Reconnaissance must include a policy-boundary crossing. The template
    /// used to require only `Agent --IntentCausesAction--> File`, i.e. any
    /// file read, and it fired in 5/6 benign scenarios of the 2026-10-19
    /// sample. A file action that never crosses a boundary is not
    /// security-relevant probing.
    #[test]
    fn reconnaissance_requires_a_boundary_crossing() {
        let mut file_only = ResponsibilityGraph::new(GraphConfig::default());
        let a = agent(&mut file_only, "agent-a");
        let file = resource(&mut file_only, ResourceKind::File);
        edge(
            &mut file_only,
            ResponsibilityEdgeType::IntentCausesAction,
            a,
            file,
        );
        assert!(
            !matched_ids(&file_only).contains(&"reconnaissance".to_string()),
            "a plain file action must not be reconnaissance"
        );

        let mut crossing = ResponsibilityGraph::new(GraphConfig::default());
        let agent_id = agent(&mut crossing, "agent-a");
        let file = resource(&mut crossing, ResourceKind::File);
        edge(
            &mut crossing,
            ResponsibilityEdgeType::IntentCausesAction,
            agent_id.clone(),
            file.clone(),
        );
        edge(
            &mut crossing,
            ResponsibilityEdgeType::PolicyBoundaryCrossing,
            agent_id,
            file,
        );
        assert!(
            matched_ids(&crossing).contains(&"reconnaissance".to_string()),
            "probing a file across a policy boundary is reconnaissance"
        );
    }

    /// Evidence must be one connected subgraph. A file action by agent A and
    /// a network boundary crossing by an unrelated agent B are two separate
    /// facts, not an exfiltration.
    #[test]
    fn disconnected_evidence_does_not_satisfy_a_template() {
        let mut graph = ResponsibilityGraph::new(GraphConfig::default());
        let a = agent(&mut graph, "agent-a");
        let file = resource(&mut graph, ResourceKind::File);
        edge(
            &mut graph,
            ResponsibilityEdgeType::IntentCausesAction,
            a,
            file,
        );
        let b = agent(&mut graph, "agent-b");
        let net = resource(&mut graph, ResourceKind::Network);
        edge(
            &mut graph,
            ResponsibilityEdgeType::PolicyBoundaryCrossing,
            b,
            net,
        );
        assert!(
            !matched_ids(&graph).contains(&"data_exfiltration".to_string()),
            "unrelated components must not combine into exfiltration"
        );
    }

    /// Cross-agent delegation + a tool-call boundary is the trust-boundary
    /// template; a single agent must not match it.
    #[test]
    fn trust_boundary_crossing_requires_two_agents_and_delegation() {
        let mut graph = ResponsibilityGraph::new(GraphConfig::default());
        let a = agent(&mut graph, "agent-a");
        let b = agent(&mut graph, "agent-b");
        let tool = resource(&mut graph, ResourceKind::ToolCall);
        edge(
            &mut graph,
            ResponsibilityEdgeType::AgentDelegatesTo,
            a.clone(),
            b.clone(),
        );
        edge(
            &mut graph,
            ResponsibilityEdgeType::PolicyBoundaryCrossing,
            a,
            tool,
        );
        assert!(matched_ids(&graph).contains(&"trust_boundary_crossing".to_string()));

        let mut single = ResponsibilityGraph::new(GraphConfig::default());
        let only = agent(&mut single, "agent-a");
        let tool2 = resource(&mut single, ResourceKind::ToolCall);
        edge(
            &mut single,
            ResponsibilityEdgeType::AgentDelegatesTo,
            only.clone(),
            only.clone(),
        );
        edge(
            &mut single,
            ResponsibilityEdgeType::PolicyBoundaryCrossing,
            only,
            tool2,
        );
        assert!(!matched_ids(&single).contains(&"trust_boundary_crossing".to_string()));
    }

    /// A credential resource plus a delegation is the credential-theft shape.
    #[test]
    fn credential_theft_requires_a_credential_resource() {
        let mut graph = ResponsibilityGraph::new(GraphConfig::default());
        let a = agent(&mut graph, "agent-a");
        let b = agent(&mut graph, "agent-b");
        let cred = resource(&mut graph, ResourceKind::Credential);
        let net = resource(&mut graph, ResourceKind::Network);
        edge(
            &mut graph,
            ResponsibilityEdgeType::IntentCausesAction,
            a.clone(),
            cred,
        );
        edge(
            &mut graph,
            ResponsibilityEdgeType::AgentDelegatesTo,
            a,
            b.clone(),
        );
        edge(
            &mut graph,
            ResponsibilityEdgeType::PolicyBoundaryCrossing,
            b,
            net,
        );
        assert!(matched_ids(&graph).contains(&"credential_theft_delegation".to_string()));
    }
}
