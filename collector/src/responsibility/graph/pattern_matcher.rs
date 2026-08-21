use crate::responsibility::claims::ClaimSeverity;
use crate::responsibility::graph::graph::ResponsibilityGraph;
use crate::responsibility::types::graph_types::{ResponsibilityEdgeType, ResponsibilityNodeType};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GraphPatternTemplate {
    pub template_id: String,
    pub description: String,
    pub severity: ClaimSeverity,
    pub required_node_types: Vec<ResponsibilityNodeType>,
    pub required_edge_types: Vec<ResponsibilityEdgeType>,
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
        let node_types: Vec<ResponsibilityNodeType> =
            graph.all_nodes().map(|n| n.node_type.clone()).collect();
        let edge_types: Vec<ResponsibilityEdgeType> =
            graph.all_edges().map(|e| e.edge_type.clone()).collect();

        if template
            .required_node_types
            .iter()
            .any(|rt| !node_types.contains(rt))
        {
            return None;
        }

        let found_edge_types = template
            .required_edge_types
            .iter()
            .filter(|et| edge_types.contains(et))
            .count();

        if !template.required_edge_types.is_empty() && found_edge_types < template.min_matches {
            return None;
        }

        if template.min_distinct_agents >= 2 {
            let distinct_agents: std::collections::HashSet<&str> = graph
                .all_nodes()
                .filter(|n| n.node_type == ResponsibilityNodeType::Agent)
                .filter_map(|n| n.agent_id.as_ref().map(|a| a.as_str()))
                .collect();
            if distinct_agents.len() < template.min_distinct_agents {
                return None;
            }

            let has_cross_agent_edge = graph.all_edges().any(|e| {
                matches!(
                    e.edge_type,
                    ResponsibilityEdgeType::Delegation
                        | ResponsibilityEdgeType::ContextInheritance
                        | ResponsibilityEdgeType::TaintPropagation
                )
            }) && distinct_agents.len() >= 2;

            if !has_cross_agent_edge {
                return None;
            }
        }

        let required_types =
            template.required_node_types.len() + template.required_edge_types.len();
        let found_node_types = template.required_node_types.len();
        let confidence =
            (found_node_types + found_edge_types) as f32 / required_types.max(1) as f32;

        Some(GraphPatternMatch {
            template_id: template.template_id.clone(),
            matched_node_ids: graph.all_nodes().map(|n| n.node_id.to_string()).collect(),
            matched_edge_ids: graph.all_edges().map(|e| e.edge_id.to_string()).collect(),
            total_edges_in_template: template.required_edge_types.len(),
            confidence,
            severity: template.severity.clone(),
            description: template.description.clone(),
        })
    }
}

pub fn default_templates() -> Vec<GraphPatternTemplate> {
    use ResponsibilityEdgeType::*;
    use ResponsibilityNodeType::*;

    vec![
        GraphPatternTemplate {
            template_id: "indirect_prompt_injection_tool_abuse".into(),
            description: "Indirect prompt injection causes agent to misuse tools".into(),
            severity: ClaimSeverity::Critical,
            required_node_types: vec![Agent, ToolCall, Resource],
            required_edge_types: vec![CapabilityUse, PolicyBoundaryCrossing],
            min_matches: 2,
            min_distinct_agents: 1,
        },
        GraphPatternTemplate {
            template_id: "data_exfiltration".into(),
            description: "Agent reads sensitive resource and sends data externally".into(),
            severity: ClaimSeverity::Critical,
            required_node_types: vec![Agent, Resource],
            required_edge_types: vec![CapabilityUse, PolicyBoundaryCrossing],
            min_matches: 2,
            min_distinct_agents: 1,
        },
        GraphPatternTemplate {
            template_id: "privilege_escalation".into(),
            description: "Agent escalates privileges through process manipulation".into(),
            severity: ClaimSeverity::Critical,
            required_node_types: vec![Agent, Process, PolicyBoundary],
            required_edge_types: vec![CapabilityUse, AuthorityTransfer],
            min_matches: 2,
            min_distinct_agents: 1,
        },
        GraphPatternTemplate {
            template_id: "credential_theft_delegation".into(),
            description: "Agent steals credentials and delegates to another agent".into(),
            severity: ClaimSeverity::Critical,
            required_node_types: vec![Agent, Credential],
            required_edge_types: vec![CapabilityUse, Delegation],
            min_matches: 2,
            min_distinct_agents: 2,
        },
        GraphPatternTemplate {
            template_id: "context_pollution_confused_deputy".into(),
            description: "Agent pollutes context causing downstream agent to act on tainted data"
                .into(),
            severity: ClaimSeverity::Warning,
            required_node_types: vec![Agent, Context],
            required_edge_types: vec![TaintPropagation, ContextInheritance],
            min_matches: 2,
            min_distinct_agents: 2,
        },
        GraphPatternTemplate {
            template_id: "supply_chain_attack".into(),
            description: "Compromised tool or dependency introduces malicious behavior".into(),
            severity: ClaimSeverity::Critical,
            required_node_types: vec![Agent, ToolCall, Resource],
            required_edge_types: vec![Delegation, CapabilityUse],
            min_matches: 2,
            min_distinct_agents: 2,
        },
        GraphPatternTemplate {
            template_id: "persistence_backdoor".into(),
            description: "Agent creates persistent backdoor through resource modification".into(),
            severity: ClaimSeverity::Critical,
            required_node_types: vec![Agent, Resource, Process],
            required_edge_types: vec![CapabilityUse, PolicyBoundaryCrossing],
            min_matches: 2,
            min_distinct_agents: 1,
        },
        GraphPatternTemplate {
            template_id: "reconnaissance".into(),
            description: "Agent systematically probes resources and credentials".into(),
            severity: ClaimSeverity::Info,
            required_node_types: vec![Agent, Resource, Credential],
            required_edge_types: vec![CapabilityUse],
            min_matches: 3,
            min_distinct_agents: 1,
        },
        GraphPatternTemplate {
            template_id: "detection_evasion".into(),
            description: "Agent attempts to evade detection through log manipulation".into(),
            severity: ClaimSeverity::Warning,
            required_node_types: vec![Agent, Process],
            required_edge_types: vec![CapabilityUse, CompetingExplanation],
            min_matches: 2,
            min_distinct_agents: 1,
        },
        GraphPatternTemplate {
            template_id: "multi_agent_collusion".into(),
            description: "Multiple agents collude to share resources and delegate authority".into(),
            severity: ClaimSeverity::Critical,
            required_node_types: vec![Agent, Resource],
            required_edge_types: vec![CapabilityUse, Delegation],
            min_matches: 3,
            min_distinct_agents: 2,
        },
        GraphPatternTemplate {
            template_id: "trust_boundary_crossing".into(),
            description: "Agent crosses trust boundary through delegation chain".into(),
            severity: ClaimSeverity::Warning,
            required_node_types: vec![Agent, PolicyBoundary],
            required_edge_types: vec![Delegation, PolicyBoundaryCrossing],
            min_matches: 2,
            min_distinct_agents: 2,
        },
        GraphPatternTemplate {
            template_id: "recursive_self_improvement".into(),
            description: "Agent recursively calls tools to self-improve without oversight".into(),
            severity: ClaimSeverity::Warning,
            required_node_types: vec![Agent, ToolCall, Process],
            required_edge_types: vec![CapabilityUse, ViolationProvenance],
            min_matches: 2,
            min_distinct_agents: 1,
        },
        GraphPatternTemplate {
            template_id: "session_hijacking".into(),
            description: "Agent hijacks another session via context inheritance".into(),
            severity: ClaimSeverity::Critical,
            required_node_types: vec![Agent, Context, ToolCall],
            required_edge_types: vec![ContextInheritance, CapabilityUse],
            min_matches: 2,
            min_distinct_agents: 2,
        },
        GraphPatternTemplate {
            template_id: "cross_modal_attack".into(),
            description: "Attack propagates across modalities via tainted resources".into(),
            severity: ClaimSeverity::Critical,
            required_node_types: vec![Agent, Resource],
            required_edge_types: vec![CapabilityUse, TaintPropagation],
            min_matches: 2,
            min_distinct_agents: 1,
        },
    ]
}
