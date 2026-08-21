use mtagentlens::responsibility::evidence::correlator_legacy::edge_has_non_temporal_support;
use mtagentlens::responsibility::evidence::normalizer::normalize_file_resource;
use mtagentlens::responsibility::types::support_types::{
    AgentId, AlertId, BoundaryId, CausalEdge, CausalNode, CausalNodeType, CausalityType, EdgeId,
    EventId, EvidenceDirectness, EvidenceId, EvidenceRecord, NodeId, PolicyId, ProcessId,
    ResourceId,
};
use serde_json::json;

#[test]
fn typed_identifier_domains_are_distinct_wrapper_types() {
    let event = EventId("evt-1".to_string());
    let agent = AgentId("agent-1".to_string());
    let node = NodeId("node-1".to_string());

    assert_eq!(event.0, "evt-1");
    assert_eq!(agent.0, "agent-1");
    assert_eq!(node.0, "node-1");
}

#[test]
fn evidence_record_references_typed_identifiers() {
    let record = EvidenceRecord {
        evidence_id: EvidenceId("e-1".to_string()),
        source_event_id: EventId("evt-1".to_string()),
        directness: EvidenceDirectness::Direct,
        rule_or_inference_id: "rule.direct.read".to_string(),
        resource_id: None,
        boundary_id: Some(BoundaryId("boundary.read".to_string())),
        confidence: 0.9,
        redaction_status: "none".to_string(),
        payload_ref: None,
    };

    assert_eq!(record.evidence_id.0, "e-1");
    assert_eq!(record.source_event_id.0, "evt-1");
    assert_eq!(record.boundary_id.unwrap().0, "boundary.read");
}

#[test]
fn resource_id_distinguishes_display_target_from_join_key() {
    let node = CausalNode {
        node_id: NodeId("node-1".to_string()),
        node_type: CausalNodeType::FileRead,
        timestamp_ns: 1,
        agent_id: AgentId("agent-1".to_string()),
        process_id: ProcessId {
            pid: 1,
            start_time_ns: 2,
        },
        operation: "read".to_string(),
        target: Some("/tmp/demo.txt".to_string()),
        resource_id: Some(ResourceId::File {
            canonical_path: "/tmp/demo.txt".to_string(),
        }),
        event_id: EventId("evt-1".to_string()),
        metadata: json!({}),
    };

    assert_eq!(node.target.as_deref(), Some("/tmp/demo.txt"));
    match node.resource_id {
        Some(ResourceId::File { canonical_path }) => assert_eq!(canonical_path, "/tmp/demo.txt"),
        _ => panic!("expected normalized file resource"),
    }
}

#[test]
fn normalize_file_resource_returns_file_resource_id() {
    match normalize_file_resource("/tmp/demo.txt") {
        ResourceId::File { canonical_path } => assert_eq!(canonical_path, "/tmp/demo.txt"),
        _ => panic!("expected file resource id"),
    }
}

#[test]
fn normalize_file_resource_canonicalizes_relative_paths() {
    let cwd = std::env::current_dir().expect("current dir");
    match normalize_file_resource("./tmp/demo.txt") {
        ResourceId::File { canonical_path } => {
            assert_eq!(
                canonical_path,
                cwd.join("./tmp/demo.txt").to_string_lossy().to_string()
            )
        }
        _ => panic!("expected file resource id"),
    }
}

#[test]
fn responsibility_edges_require_non_temporal_support() {
    let edge = CausalEdge {
        edge_id: EdgeId("edge-1".to_string()),
        from_node_id: NodeId("node-a".to_string()),
        to_node_id: NodeId("node-b".to_string()),
        causality_type: CausalityType::Indirect,
        confidence: 0.7,
        evidence: vec![],
    };

    assert!(edge_has_non_temporal_support(&edge).is_err());
}

#[test]
fn responsibility_alert_ids_use_newtype_pattern() {
    // The old support alert DTO has been removed.
    // The new claims::alert::ResponsibilityAlert uses String-based IDs.
    // This test verifies the newtype ID pattern is still used elsewhere (PolicyId, EvidenceId).
    let policy_id = PolicyId("policy.read".to_string());
    let evidence_id = EvidenceId("e-1".to_string());
    assert_eq!(policy_id.0, "policy.read");
    assert_eq!(evidence_id.0, "e-1");
}
