/// Innovation 21: Automatic root-cause analysis over causal links and graph topology.
///
/// Provides two analysis modes:
/// 1. `analyse()` — score CorrelationEvidences by confidence + recency + downstream count
/// 2. `analyse_from_graph()` — score graph nodes by outgoing edge confidence + count
///    for top-K root cause ranking using the in-memory ResponsibilityGraph.
use serde::{Deserialize, Serialize};

use crate::responsibility::evidence::correlator_legacy::CorrelationEvidence;
use crate::responsibility::graph::graph::ResponsibilityGraph;
use crate::responsibility::types::{AgentId, NodeId};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RootCauseResult {
    pub root_evidence_id: String,
    pub agent_id: String,
    pub score: f32,
    pub explanation: String,
    pub downstream_count: usize,
}

/// Score each node in the responsibility graph by a combination of:
/// - Edge confidence (outgoing edges with higher confidence boost score)
/// - Downstream count (nodes with more downstream edges are more impactful)
///
/// Returns top-K root cause candidates.
pub fn analyse_from_graph(
    graph: &ResponsibilityGraph,
    agent_id: &AgentId,
    top_k: usize,
) -> Vec<RootCauseResult> {
    let node_ids: Vec<NodeId> = graph.nodes_for_agent(agent_id).to_vec();
    if node_ids.is_empty() {
        return vec![];
    }

    // Build per-node scores
    struct NodeScore {
        node_id: NodeId,
        outgoing_count: usize,
        avg_confidence: f32,
    }

    let mut node_scores: Vec<NodeScore> = Vec::new();

    for node_id in &node_ids {
        let outgoing_edges = graph.outgoing_from(node_id);
        let outgoing_count = outgoing_edges.len();
        let avg_confidence = if outgoing_count > 0 {
            outgoing_edges
                .iter()
                .filter_map(|eid| graph.get_edge(eid))
                .map(|e| e.confidence)
                .sum::<f32>()
                / outgoing_count as f32
        } else {
            0.0
        };

        node_scores.push(NodeScore {
            node_id: node_id.clone(),
            outgoing_count,
            avg_confidence,
        });
    }

    // Score: confidence * 0.5 + outgoing_count_normalized * 0.3 + 0.2 (depth bonus for non-leaf)
    let max_outgoing = node_scores
        .iter()
        .map(|s| s.outgoing_count)
        .max()
        .unwrap_or(1)
        .max(1) as f32;

    let mut scored: Vec<(f32, NodeId, usize)> = node_scores
        .iter()
        .map(|s| {
            let conf_score = s.avg_confidence * 0.5;
            let out_score = (s.outgoing_count as f32 / max_outgoing) * 0.3;
            // Nodes that have downstream edges are closer to root cause
            let depth_bonus = if s.outgoing_count > 0 { 0.2 } else { 0.0 };
            let score = conf_score + out_score + depth_bonus;
            (score, s.node_id.clone(), s.outgoing_count)
        })
        .collect();

    scored.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap_or(std::cmp::Ordering::Equal));

    scored
        .into_iter()
        .take(top_k)
        .map(|(score, node_id, downstream)| {
            let explanation = format!(
                "Graph-based root cause: node={} score={:.3} downstream={}",
                node_id.0, score, downstream
            );
            RootCauseResult {
                root_evidence_id: node_id.0.clone(),
                agent_id: agent_id.0.clone(),
                score,
                explanation,
                downstream_count: downstream,
            }
        })
        .collect()
}

pub fn rank_legacy_correlation_evidence(
    links: &[CorrelationEvidence],
    top_k: usize,
) -> Vec<RootCauseResult> {
    analyse(links, top_k)
}

/// Analyse links for a single agent and return the top-K root causes.
pub fn analyse(links: &[CorrelationEvidence], top_k: usize) -> Vec<RootCauseResult> {
    if links.is_empty() {
        return vec![];
    }

    // Score each link: higher confidence + earlier timestamp + more links after it = higher score
    let max_ts = links.iter().map(|l| l.timestamp_ns).max().unwrap_or(1);
    let min_ts = links.iter().map(|l| l.timestamp_ns).min().unwrap_or(0);
    let ts_range = (max_ts - min_ts).max(1) as f32;

    let mut scored: Vec<(f32, &CorrelationEvidence, usize)> = links
        .iter()
        .map(|link| {
            // Recency penalty: earlier links score higher
            let recency = 1.0 - (link.timestamp_ns - min_ts) as f32 / ts_range;
            // Downstream count: links that come after this one within 30s
            let window = 30_000_000_000u64;
            let downstream = links
                .iter()
                .filter(|l| {
                    l.evidence_id != link.evidence_id
                        && l.timestamp_ns > link.timestamp_ns
                        && l.timestamp_ns - link.timestamp_ns <= window
                })
                .count();
            let score = link.confidence * 0.5 + recency * 0.3 + (downstream as f32 * 0.2).min(0.2);
            (score, link, downstream)
        })
        .collect();

    scored.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap_or(std::cmp::Ordering::Equal));

    scored
        .into_iter()
        .take(top_k)
        .map(|(score, link, downstream)| {
            let op = link
                .matched_fields
                .as_ref()
                .and_then(|f| f.get("candidate_type"))
                .and_then(|v| v.as_str())
                .unwrap_or("unknown");
            let target = link
                .matched_fields
                .as_ref()
                .and_then(|f| f.get("target"))
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let explanation = format!(
                "Root cause: {} operation on '{}' (confidence={:.2}, {} downstream links within 30s)",
                op, target, link.confidence, downstream
            );
            RootCauseResult {
                root_evidence_id: link.evidence_id.clone(),
                agent_id: link.agent_id.clone(),
                score,
                explanation,
                downstream_count: downstream,
            }
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::responsibility::evidence::MatchLevel;
    use serde_json::json;

    fn mk_link(id: &str, agent: &str, ts: u64, kind: &str, confidence: f32) -> CorrelationEvidence {
        CorrelationEvidence {
            schema_version: 1,
            evidence_id: id.to_string(),
            agent_id: agent.to_string(),
            intent_id: "i1".to_string(),
            candidate_id: "c1".to_string(),
            timestamp_ns: ts,
            confidence,
            match_level: MatchLevel::L1Exact,
            matched_fields: Some(json!({"candidate_type": kind, "target": "/foo"})),
            supporting_raw_event_refs: None,
            conversation_id: None,
            authority_source: None,
            message_id: None,
            source_event_ref: None,
            debug_notes: None,
            derivation_mode: None,
        }
    }

    #[test]
    fn returns_earliest_high_confidence_as_root() {
        let links = vec![
            mk_link("l1", "a", 1_000, "file_read", 0.9),
            mk_link("l2", "a", 5_000, "process_exec", 0.8),
            mk_link("l3", "a", 10_000, "network_connect", 0.7),
        ];
        let results = analyse(&links, 1);
        assert_eq!(results.len(), 1);
        // l1 is earliest and has most downstream links
        assert_eq!(results[0].root_evidence_id, "l1");
    }

    #[test]
    fn empty_links_returns_empty() {
        assert!(analyse(&[], 3).is_empty());
    }

    #[test]
    fn top_k_limits_results() {
        let links = vec![
            mk_link("l1", "a", 1_000, "file_read", 0.9),
            mk_link("l2", "a", 2_000, "file_read", 0.8),
            mk_link("l3", "a", 3_000, "file_read", 0.7),
        ];
        assert_eq!(analyse(&links, 2).len(), 2);
    }

    #[test]
    fn analyse_from_graph_returns_root_causes() {
        use crate::responsibility::graph::config::GraphConfig;
        use crate::responsibility::types::{
            AgentId, EdgeId, NodeId, PhaseIndicator, ResponsibilityEdge, ResponsibilityEdgeType,
            ResponsibilityNode, ResponsibilityNodeType,
        };

        let mut graph = ResponsibilityGraph::new(GraphConfig::default());
        let agent_id = AgentId::from("agent-1");

        let agent_node = ResponsibilityNode {
            node_id: NodeId::from("node-agent"),
            node_type: ResponsibilityNodeType::Agent,
            agent_id: Some(agent_id.clone()),
            resource: None,
            evidence_refs: vec![],
            timestamp_ns: 1000,
            phase: PhaseIndicator::Provisional { timestamp_ns: 1000 },
        };
        graph.add_node(agent_node);

        let resource_node = ResponsibilityNode {
            node_id: NodeId::from("node-resource"),
            node_type: ResponsibilityNodeType::Resource,
            agent_id: Some(agent_id.clone()),
            resource: None,
            evidence_refs: vec![],
            timestamp_ns: 1100,
            phase: PhaseIndicator::Provisional { timestamp_ns: 1100 },
        };
        graph.add_node(resource_node);

        let edge = ResponsibilityEdge {
            edge_id: EdgeId::from("edge-1"),
            edge_type: ResponsibilityEdgeType::IntentCausesAction,
            source: NodeId::from("node-agent"),
            target: NodeId::from("node-resource"),
            evidence_refs: vec![],
            confidence: 0.9,
            phase: PhaseIndicator::Provisional { timestamp_ns: 1100 },
            signal: None,
        };
        graph.add_edge(edge);

        let results = analyse_from_graph(&graph, &agent_id, 1);
        assert!(
            !results.is_empty(),
            "should have at least one root cause result"
        );
    }

    #[test]
    fn empty_graph_returns_empty() {
        use crate::responsibility::graph::config::GraphConfig;
        let graph = ResponsibilityGraph::new(GraphConfig::default());
        let agent_id = AgentId::from("nonexistent");
        let results = analyse_from_graph(&graph, &agent_id, 3);
        assert!(results.is_empty());
    }
}
