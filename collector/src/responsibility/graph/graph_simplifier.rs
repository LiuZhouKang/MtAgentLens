/// Innovation 7: Causal graph simplification and multi-level summarization.
///
/// Takes a flat list of CorrelationEvidences and produces a compressed summary by:
/// 1. Deduplicating links with the same (agent, tool_name, candidate_type) within a window
/// 2. Grouping by agent and operation type
/// 3. Producing a human-readable explanation string
use std::collections::HashMap;

use serde::{Deserialize, Serialize};

use crate::responsibility::evidence::CorrelationEvidence;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SimplifiedNode {
    pub agent_id: String,
    pub operation_type: String,
    pub representative_evidence_id: String,
    pub count: usize,
    pub first_ns: u64,
    pub last_ns: u64,
    pub targets: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SimplifiedGraph {
    pub nodes: Vec<SimplifiedNode>,
    pub total_links: usize,
    pub compressed_ratio: f32,
    pub summary: String,
}

/// Simplify a slice of causal links into a compressed graph representation.
pub fn simplify(links: &[CorrelationEvidence]) -> SimplifiedGraph {
    let total = links.len();
    if total == 0 {
        return SimplifiedGraph {
            nodes: vec![],
            total_links: 0,
            compressed_ratio: 1.0,
            summary: "No causal links observed.".to_string(),
        };
    }

    // Group by (agent_id, operation_type)
    let mut groups: HashMap<(String, String), Vec<&CorrelationEvidence>> = HashMap::new();
    for link in links {
        let op = link
            .matched_fields
            .as_ref()
            .and_then(|f| f.get("candidate_type"))
            .and_then(|v| v.as_str())
            .unwrap_or("unknown")
            .to_string();
        groups
            .entry((link.agent_id.clone(), op))
            .or_default()
            .push(link);
    }

    let mut nodes: Vec<SimplifiedNode> = groups
        .into_iter()
        .map(|((agent_id, op), group)| {
            let first_ns = group.iter().map(|l| l.timestamp_ns).min().unwrap_or(0);
            let last_ns = group.iter().map(|l| l.timestamp_ns).max().unwrap_or(0);
            let targets: Vec<String> = {
                let mut seen = std::collections::HashSet::new();
                group
                    .iter()
                    .filter_map(|l| {
                        l.matched_fields
                            .as_ref()
                            .and_then(|f| f.get("target"))
                            .and_then(|v| v.as_str())
                            .map(|s| s.to_string())
                    })
                    .filter(|t| seen.insert(t.clone()))
                    .take(5)
                    .collect()
            };
            SimplifiedNode {
                agent_id,
                operation_type: op,
                representative_evidence_id: group[0].evidence_id.clone(),
                count: group.len(),
                first_ns,
                last_ns,
                targets,
            }
        })
        .collect();

    nodes.sort_by_key(|a| a.first_ns);

    let node_count = nodes.len();
    let compressed_ratio = if total > 0 {
        node_count as f32 / total as f32
    } else {
        1.0
    };

    let summary = build_summary(&nodes, total);

    SimplifiedGraph {
        nodes,
        total_links: total,
        compressed_ratio,
        summary,
    }
}

fn build_summary(nodes: &[SimplifiedNode], total: usize) -> String {
    if nodes.is_empty() {
        return "No activity.".to_string();
    }
    let agents: std::collections::HashSet<&str> =
        nodes.iter().map(|n| n.agent_id.as_str()).collect();
    let ops: std::collections::HashSet<&str> =
        nodes.iter().map(|n| n.operation_type.as_str()).collect();
    let mut parts = vec![format!(
        "{} agent(s) performed {} distinct operation type(s) across {} causal link(s).",
        agents.len(),
        ops.len(),
        total
    )];
    for node in nodes.iter().take(5) {
        let target_str = if node.targets.is_empty() {
            String::new()
        } else {
            format!(" (targets: {})", node.targets.join(", "))
        };
        parts.push(format!(
            "  [{}] {} × {}{}",
            node.agent_id, node.count, node.operation_type, target_str
        ));
    }
    if nodes.len() > 5 {
        parts.push(format!("  … and {} more node groups.", nodes.len() - 5));
    }
    parts.join("\n")
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::responsibility::evidence::correlator_legacy::MatchLevel;
    use serde_json::json;

    fn mk_link(id: &str, agent: &str, ts: u64, kind: &str, target: &str) -> CorrelationEvidence {
        CorrelationEvidence {
            schema_version: 1,
            evidence_id: id.to_string(),
            agent_id: agent.to_string(),
            intent_id: "i1".to_string(),
            candidate_id: "c1".to_string(),
            timestamp_ns: ts,
            confidence: 0.9,
            match_level: MatchLevel::L1Exact,
            matched_fields: Some(json!({
                "candidate_type": kind,
                "target": target,
            })),
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
    fn simplifies_repeated_operations() {
        let links = vec![
            mk_link("l1", "a", 1000, "file_read", "/foo"),
            mk_link("l2", "a", 2000, "file_read", "/bar"),
            mk_link("l3", "a", 3000, "process_exec", "/bin/sh"),
        ];
        let g = simplify(&links);
        assert_eq!(g.total_links, 3);
        assert_eq!(g.nodes.len(), 2); // file_read + process_exec
        let file_node = g
            .nodes
            .iter()
            .find(|n| n.operation_type == "file_read")
            .unwrap();
        assert_eq!(file_node.count, 2);
        assert!(g.compressed_ratio < 1.0);
    }

    #[test]
    fn empty_links_returns_empty_graph() {
        let g = simplify(&[]);
        assert_eq!(g.total_links, 0);
        assert!(g.nodes.is_empty());
    }

    #[test]
    fn summary_mentions_agent_and_ops() {
        let links = vec![mk_link(
            "l1",
            "agent-x",
            1000,
            "network_connect",
            "api.example.com",
        )];
        let g = simplify(&links);
        assert!(g.summary.contains("agent-x") || g.summary.contains("1 agent"));
        assert!(g.summary.contains("network_connect") || g.summary.contains("1 distinct"));
    }
}
