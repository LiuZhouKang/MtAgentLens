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
            .filter_map(|template| self.try_match(graph, template))
            .collect()
    }

    fn try_match(
        &self,
        graph: &ResponsibilityGraph,
        template: &GraphPatternTemplate,
    ) -> Option<GraphPatternMatch> {
        use std::collections::{HashMap, HashSet};

        // The evidence must be one connected subgraph, not types that merely
        // exist somewhere in the graph. Without this, a benign file read in
        // one action and a network connect in another satisfied
        // `data_exfiltration`.
        let required_edges: Vec<&ResponsibilityEdge> = graph
            .all_edges()
            .filter(|edge| template.required_edge_types.contains(&edge.edge_type))
            .collect();
        if !template.required_edge_types.is_empty() && required_edges.is_empty() {
            return None;
        }

        fn find(parent: &mut HashMap<NodeId, NodeId>, node: &NodeId) -> NodeId {
            let current = parent
                .entry(node.clone())
                .or_insert_with(|| node.clone())
                .clone();
            if &current == node {
                return current;
            }
            let root = find(parent, &current);
            parent.insert(node.clone(), root.clone());
            root
        }

        let mut parent: HashMap<NodeId, NodeId> = HashMap::new();
        for edge in &required_edges {
            let source = find(&mut parent, &edge.source);
            let target = find(&mut parent, &edge.target);
            if source != target {
                parent.insert(source, target);
            }
        }
        let components: HashSet<NodeId> = required_edges
            .iter()
            .map(|edge| find(&mut parent, &edge.source))
            .collect();
        if components.len() > 1 {
            return None;
        }

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
            .collect();
        if distinct_agents.len() < template.min_distinct_agents {
            return None;
        }

        if template.min_distinct_agents >= 2 {
            // The cross-agent relationship must be one of the evidence edges.
            let has_cross_agent_edge = required_edges.iter().any(|edge| {
                matches!(
                    edge.edge_type,
                    ResponsibilityEdgeType::Delegation
                        | ResponsibilityEdgeType::AgentDelegatesTo
                        | ResponsibilityEdgeType::ContextInheritance
                        | ResponsibilityEdgeType::ContextFlowsFrom
                        | ResponsibilityEdgeType::TaintPropagation
                        | ResponsibilityEdgeType::TaintPropagatesTo
                )
            });
            if !has_cross_agent_edge {
                return None;
            }
        }

        let confidence = 1.0;
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
            min_matches: 2,
            min_distinct_agents: 1,
        },
        GraphPatternTemplate {
            template_id: "privilege_escalation".into(),
            description: "Agent escalates privileges through process manipulation".into(),
            severity: ClaimSeverity::Critical,
            required_node_types: vec![Agent, PolicyBoundary],
            required_edge_types: vec![IntentCausesAction, PolicyBoundaryCrossing],
            required_resource_kinds: vec![RK::Process],
            required_edge_target_kinds: vec![(IntentCausesAction, RK::Process)],
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
            min_matches: 2,
            min_distinct_agents: 1,
        },
        GraphPatternTemplate {
            template_id: "reconnaissance".into(),
            description: "Agent systematically probes resources".into(),
            severity: ClaimSeverity::Info,
            required_node_types: vec![Agent, Resource],
            required_edge_types: vec![IntentCausesAction],
            required_resource_kinds: vec![RK::File],
            required_edge_target_kinds: vec![(IntentCausesAction, RK::File)],
            // `min_matches` counts distinct edge types, not occurrences;
            // counting repeated probing needs an occurrence-based matcher
            // (recorded in ADR 0012).
            min_matches: 1,
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
            min_matches: 2,
            min_distinct_agents: 2,
        },
        GraphPatternTemplate {
            template_id: "recursive_self_improvement".into(),
            description: "Agent recursively calls tools to self-improve without oversight".into(),
            severity: ClaimSeverity::Warning,
            required_node_types: vec![Agent, Resource],
            required_edge_types: vec![IntentCausesAction, PolicyBoundaryCrossing],
            required_resource_kinds: vec![RK::Process],
            required_edge_target_kinds: vec![(IntentCausesAction, RK::Process)],
            min_matches: 2,
            min_distinct_agents: 1,
        },
        GraphPatternTemplate {
            template_id: "session_hijacking".into(),
            description: "Agent hijacks another session via context inheritance".into(),
            severity: ClaimSeverity::Critical,
            required_node_types: vec![Agent, Resource],
            required_edge_types: vec![ContextFlowsFrom, IntentCausesAction],
            required_resource_kinds: vec![RK::ToolCall],
            required_edge_target_kinds: vec![(IntentCausesAction, RK::ToolCall)],
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
