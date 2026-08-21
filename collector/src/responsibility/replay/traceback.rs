use anyhow::{Result, bail};

use crate::responsibility::graph::graph_store::{GraphNode, GraphStore};
use crate::responsibility::types::support_types::{
    AccountabilityTraceback, AccountabilityTracebackReplay, AlertId, EvidenceId, NodeId,
    TracebackId,
};

/// Build an accountable chain by walking backward through the graph store.
///
/// Starting from `start_candidate_id`, walks backward through the causal graph
/// to find the deepest reachable upstream node as the root cause. Falls back to
/// a minimal chain if no GraphStore is available.
pub fn build_minimal_accountable_chain(
    alert_id: AlertId,
    chain_node_ids: Vec<NodeId>,
) -> Result<AccountabilityTraceback> {
    let root_cause_node_id = chain_node_ids.first().cloned().ok_or_else(|| {
        anyhow::anyhow!("malformed accountability traceback: chain_node_ids must not be empty")
    })?;

    Ok(AccountabilityTraceback {
        traceback_id: TracebackId(uuid::Uuid::new_v4().to_string()),
        alert_id,
        chain_node_ids,
        root_cause_node_id,
        explanation_summary: "minimal accountable chain (no graph store available)".to_string(),
    })
}

/// Build an accountable chain using the GraphStore for backward causal walking.
///
/// Walks backward from the candidate to find the deepest reachable upstream node
/// as the root cause, then constructs the traceback with a meaningful summary.
pub fn build_accountable_chain_from_graphstore(
    graph_store: &GraphStore,
    alert_id: AlertId,
    agent_id: &str,
    candidate_id: &str,
    max_depth: usize,
) -> Result<AccountabilityTraceback> {
    let walked = graph_store.walk_backward(agent_id, candidate_id, max_depth)?;

    if walked.is_empty() {
        bail!(
            "walk_backward returned empty for agent={}, candidate={}",
            agent_id,
            candidate_id
        );
    }

    // The deepest depth node is the root cause
    let max_depth_reached = walked
        .iter()
        .map(|(depth, _)| *depth)
        .max()
        .expect("walked is non-empty");

    // Sort walked by depth ascending
    let mut walked_sorted: Vec<(usize, &GraphNode)> = walked.iter().map(|(d, n)| (*d, n)).collect();
    walked_sorted.sort_by_key(|(depth, _)| *depth);

    let chain_node_ids: Vec<NodeId> = walked_sorted
        .iter()
        .map(|(_, node)| NodeId(node.to_string()))
        .collect();

    // Root cause is the deepest node found
    let root_node = walked_sorted
        .last()
        .map(|(_, node)| NodeId(node.to_string()))
        .expect("walked is non-empty");

    let chain_len = chain_node_ids.len();
    let explanation_summary = format!(
        "causal chain depth={} (max back-walk depth={}), agent={}, root={}",
        chain_len, max_depth_reached, agent_id, root_node.0
    );

    Ok(AccountabilityTraceback {
        traceback_id: TracebackId(uuid::Uuid::new_v4().to_string()),
        alert_id,
        chain_node_ids,
        root_cause_node_id: root_node,
        explanation_summary,
    })
}

pub fn build_accountability_traceback_replay(
    traceback: AccountabilityTraceback,
    supporting_evidence_ids: Vec<EvidenceId>,
) -> Result<AccountabilityTracebackReplay> {
    if traceback.chain_node_ids.is_empty() {
        bail!("malformed accountability traceback: chain_node_ids must not be empty");
    }

    if !traceback
        .chain_node_ids
        .contains(&traceback.root_cause_node_id)
    {
        bail!(
            "malformed accountability traceback: root_cause_node_id must belong to chain_node_ids"
        );
    }

    Ok(AccountabilityTracebackReplay {
        traceback_id: traceback.traceback_id,
        alert_id: traceback.alert_id,
        chain_node_ids: traceback.chain_node_ids,
        root_cause_node_id: traceback.root_cause_node_id,
        supporting_evidence_ids,
        explanation_summary: traceback.explanation_summary,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_build_minimal_chain_with_single_node() {
        let chain = vec![NodeId("node-1".to_string())];
        let result = build_minimal_accountable_chain(AlertId("alert-1".to_string()), chain.clone());
        assert!(result.is_ok());
        let traceback = result.unwrap();
        assert_eq!(traceback.root_cause_node_id, NodeId("node-1".to_string()));
        assert_eq!(traceback.chain_node_ids, chain);
    }

    #[test]
    fn test_build_minimal_chain_empty_chain_fails() {
        let result = build_minimal_accountable_chain(AlertId("alert-1".to_string()), vec![]);
        assert!(result.is_err());
    }

    #[test]
    fn test_traceback_replay_requires_root_in_chain() {
        let traceback = AccountabilityTraceback {
            traceback_id: TracebackId("tb-1".to_string()),
            alert_id: AlertId("alert-1".to_string()),
            chain_node_ids: vec![NodeId("node-1".to_string())],
            root_cause_node_id: NodeId("node-2".to_string()),
            explanation_summary: "test".to_string(),
        };
        let result = build_accountability_traceback_replay(traceback, vec![]);
        assert!(result.is_err());
    }

    #[test]
    fn test_traceback_replay_empty_chain_fails() {
        let traceback = AccountabilityTraceback {
            traceback_id: TracebackId("tb-1".to_string()),
            alert_id: AlertId("alert-1".to_string()),
            chain_node_ids: vec![],
            root_cause_node_id: NodeId("node-1".to_string()),
            explanation_summary: "test".to_string(),
        };
        let result = build_accountability_traceback_replay(traceback, vec![]);
        assert!(result.is_err());
    }

    #[test]
    fn test_traceback_replay_success() {
        let traceback = AccountabilityTraceback {
            traceback_id: TracebackId("tb-1".to_string()),
            alert_id: AlertId("alert-1".to_string()),
            chain_node_ids: vec![NodeId("node-1".to_string()), NodeId("node-2".to_string())],
            root_cause_node_id: NodeId("node-1".to_string()),
            explanation_summary: "test chain".to_string(),
        };
        let result =
            build_accountability_traceback_replay(traceback, vec![EvidenceId("ev-1".to_string())]);
        assert!(result.is_ok());
        let replay = result.unwrap();
        assert_eq!(replay.supporting_evidence_ids.len(), 1);
        assert_eq!(replay.explanation_summary, "test chain");
    }
}
