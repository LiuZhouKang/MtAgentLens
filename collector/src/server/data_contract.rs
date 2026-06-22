// SPDX-License-Identifier: MIT
// Data Contract v2: Timeline + ProcessTree + AnomalyStream
//
// Cross-reference design:
//   TimelineStep ──(pid)──> ProcessNode
//   TimelineStep ──(anomaly_refs)──> AnomalyStreamEvent
//   AnomalyStreamEvent ──(linked_timeline_steps)──> TimelineStep
//   AnomalyStreamEvent ──(linked_process_pids)──> ProcessNode
//
// Stream version carried per-event for gradual rollout.
// Sequence number enables dedup / gap detection on the client.

use serde::{Deserialize, Serialize};
use std::collections::HashMap;

// ═══════════════════════════════════════════════════════════════════════════
// SCHEMA VERSION CONSTANTS
// ═══════════════════════════════════════════════════════════════════════════

pub const TIMELINE_SCHEMA_VERSION: &str = "2.0.0";
pub const PROCESS_TREE_SCHEMA_VERSION: &str = "2.0.0";
pub const ANOMALY_STREAM_SCHEMA_VERSION: &str = "2.0.0";
pub const UNIFIED_STREAM_SCHEMA_VERSION: &str = "2.0.0";

// ═══════════════════════════════════════════════════════════════════════════
// TIMELINE CONTRACT
// ═══════════════════════════════════════════════════════════════════════════

/// Full timeline snapshot for a single agent.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TimelineContract {
    pub timeline_id: String,
    pub agent_id: String,
    pub start_time_ns: u64,
    pub end_time_ns: u64,
    pub steps: Vec<TimelineStep>,
    pub total_duration_ms: u64,
    /// v2: metrics for quick dashboard summary without walking all steps.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub metrics: Option<TimelineMetrics>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TimelineMetrics {
    pub step_count: usize,
    pub file_ops: usize,
    pub network_ops: usize,
    pub exec_ops: usize,
    pub delegation_ops: usize,
    pub max_gap_ms: u64,
    pub anomaly_linked_count: usize,
}

/// Single step in a timeline.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TimelineStep {
    pub step_id: String,
    pub timestamp_ns: u64,
    /// Human-readable event category.
    /// v1 values: "FileRead" | "FileWrite" | "NetworkConnect" | "ProcessExec" | "Delegation"
    /// v2 additions: "FileDelete" | "NetworkSend" | "NetworkRecv" | "CredentialAccess" | "ContextInjection"
    pub event_type: String,
    pub description: String,
    /// Canonical path, host:port, or other resource identifier.
    pub resource: Option<String>,
    pub agent_id: Option<String>,
    pub source_event_id: Option<String>,
    pub evidence_refs: Vec<String>,
    pub operation: Option<String>,

    // ── v2 fields ──
    /// Monotonic index within this timeline (0-based).
    #[serde(default)]
    pub step_index: u32,
    /// Nanoseconds since previous step; None for the first step.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub duration_since_prev_ns: Option<u64>,
    /// PID that generated this event — links to ProcessNode.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub pid: Option<u32>,
    /// Anomaly IDs that reference this step — links to AnomalyStreamEvent.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub anomaly_refs: Vec<String>,
    /// Extensible metadata bag (e.g. file size, HTTP status code).
    #[serde(default, skip_serializing_if = "HashMap::is_empty")]
    pub metadata: HashMap<String, serde_json::Value>,
}

// ═══════════════════════════════════════════════════════════════════════════
// PROCESS TREE CONTRACT
// ═══════════════════════════════════════════════════════════════════════════

/// Full process tree snapshot.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProcessTreeContract {
    pub tree_id: String,
    pub root_pid: u32,
    pub root_comm: String,
    pub snapshot_time_ns: u64,
    pub nodes: Vec<ProcessNode>,
    pub edges: Vec<ProcessEdge>,
    /// v2: summary stats to avoid full-tree traversal on the client.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub metrics: Option<ProcessTreeMetrics>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProcessTreeMetrics {
    pub total_nodes: usize,
    pub total_edges: usize,
    pub active_nodes: usize,
    pub exited_nodes: usize,
    pub max_depth: usize,
    pub agent_count: usize,
    pub anomaly_linked_count: usize,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProcessNode {
    pub node_id: String,
    pub pid: u32,
    pub ppid: u32,
    pub comm: String,
    pub start_time_ns: u64,
    pub end_time_ns: Option<u64>,
    pub exit_code: Option<i32>,
    pub resource_usage: ProcessResourceUsage,

    // ── v2 fields ──
    /// Agent identity resolved for this process (may be None for kernel threads).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub agent_id: Option<String>,
    /// Number of anomalies linked to this process.
    #[serde(default)]
    pub anomaly_count: u32,
    /// Whether any linked anomalies are still active.
    #[serde(default)]
    pub has_active_anomalies: bool,
    /// User-defined labels for grouping/filtering (e.g. "worker", "browser").
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub labels: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProcessResourceUsage {
    pub cpu_time_us: u64,
    pub memory_peak_kb: u64,
    pub file_ops_count: u32,
    pub network_ops_count: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProcessEdge {
    pub edge_id: String,
    pub parent_pid: u32,
    pub child_pid: u32,
    /// "fork" | "exec" | "thread"
    pub edge_type: String,
    pub timestamp_ns: u64,

    // ── v2 fields ──
    /// The agent that triggered this spawn.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub agent_id: Option<String>,
}

/// Incremental update to the process tree.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProcessTreeUpdate {
    pub tree_id: String,
    /// "node_added" | "node_removed" | "node_updated" | "edge_added" | "edge_removed"
    pub update_type: String,
    pub timestamp_ns: u64,
    pub node: Option<ProcessNode>,
    pub edge: Option<ProcessEdge>,

    // ── v2 fields ──
    /// Previous node state (for "node_updated" — enables diff rendering).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub previous_node: Option<ProcessNode>,
    /// Monotonic sequence within this tree's update stream.
    #[serde(default)]
    pub update_index: u32,
}

// ═══════════════════════════════════════════════════════════════════════════
// ANOMALY STREAM CONTRACT
// ═══════════════════════════════════════════════════════════════════════════

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum AnomalySeverity {
    Info,
    Warning,
    Critical,
}

/// Lifecycle status for an anomaly.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum AnomalyStatus {
    Active,
    Resolved,
    Dismissed,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AnomalyStreamEvent {
    pub anomaly_id: String,
    pub timestamp_ns: u64,
    pub agent_id: String,
    /// "PolicyViolation" | "UnexpectedBehavior" | "ResourceExhaustion" | "DelegationAnomaly" | "ContextPollution"
    pub anomaly_type: String,
    pub severity: AnomalySeverity,
    /// 0.0 – 1.0
    pub confidence: f32,
    pub classification: AnomalyClassification,
    pub affected_resources: Vec<AffectedResource>,
    pub evidence_chain: Vec<String>,
    pub remediation_hint: Option<String>,

    // ── v2 fields ──
    /// Lifecycle status.
    #[serde(default = "default_anomaly_status")]
    pub status: AnomalyStatus,
    /// When the anomaly was resolved/dismissed.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub resolved_at_ns: Option<u64>,
    /// Timeline step IDs this anomaly references — cross-stream link.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub linked_timeline_steps: Vec<String>,
    /// Process PIDs involved — cross-stream link.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub linked_process_pids: Vec<u32>,
    /// LLM observer verdict (if observer is enabled).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub observer_verdict: Option<ObserverVerdict>,
    /// Extensible metadata.
    #[serde(default, skip_serializing_if = "HashMap::is_empty")]
    pub metadata: HashMap<String, serde_json::Value>,
}

fn default_anomaly_status() -> AnomalyStatus {
    AnomalyStatus::Active
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AnomalyClassification {
    pub primary_class: String,
    pub sub_class: Option<String>,
    pub rule_ids: Vec<String>,
    pub pattern_ids: Vec<String>,
    pub score_breakdown: HashMap<String, f32>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AffectedResource {
    /// "File" | "Network" | "Process" | "Credential" | "Context"
    pub resource_type: String,
    pub resource_id: String,
    /// "read" | "write" | "connect" | "execute" | "delete" | "inject"
    pub operation: String,
}

/// LLM observer judgment for an anomaly.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ObserverVerdict {
    pub decision: String,       // "Safe" | "Unsafe" | "Uncertain"
    pub confidence: f32,
    pub reasoning: String,
    pub attacked_flag: bool,
    pub cache_ttl: String,      // "Once" | "Task" | "All"
}

// ═══════════════════════════════════════════════════════════════════════════
// UNIFIED SSE STREAM CONTRACT
// ═══════════════════════════════════════════════════════════════════════════

/// Batch marker for multi-message delivery of a single logical snapshot.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BatchMarker {
    pub batch_id: String,
    pub batch_index: u32,
    pub batch_total: u32,
}

/// Envelope wrapping every SSE event.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "stream_type")]
pub enum UnifiedStreamEvent {
    #[serde(rename = "timeline")]
    Timeline(TimelineStep),
    #[serde(rename = "process_tree")]
    ProcessTree(ProcessTreeUpdate),
    #[serde(rename = "anomaly")]
    Anomaly(AnomalyStreamEvent),
}

/// v2 envelope: carries stream_version and sequence_number at the top level.
/// Serializes as flat JSON so SSE consumers read one field to decide routing.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StreamEnvelope {
    /// "2.0.0" — all events in a run share the same version.
    pub stream_version: String,
    /// Monotonically increasing across all stream types in this run.
    pub sequence_number: u64,
    /// The run this event belongs to.
    pub run_id: String,
    /// Wall-clock epoch milliseconds when this envelope was emitted.
    pub emitted_at_ms: u64,
    /// Non-None when this event is part of a multi-message batch.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub batch: Option<BatchMarker>,
    /// The wrapped stream event.
    #[serde(flatten)]
    pub event: UnifiedStreamEvent,
}

// ═══════════════════════════════════════════════════════════════════════════
// SNAPSHOT RESPONSE (REST endpoint: GET /api/runs/:id/snapshot)
// ═══════════════════════════════════════════════════════════════════════════

/// Full snapshot returned by the snapshot REST endpoint.
/// Combines all three streams at a point in time.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RunSnapshot {
    pub run_id: String,
    pub snapshot_time_ns: u64,
    pub stream_version: String,
    pub timeline: Option<TimelineContract>,
    pub process_tree: Option<ProcessTreeContract>,
    pub anomalies: Vec<AnomalyStreamEvent>,
    /// Last seen sequence_number — clients resume SSE from here.
    pub last_sequence_number: u64,
}

// ═══════════════════════════════════════════════════════════════════════════
// TESTS
// ═══════════════════════════════════════════════════════════════════════════

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_timeline_step_v2_defaults() {
        let json = serde_json::json!({
            "step_id": "s1",
            "timestamp_ns": 1000000000u64,
            "event_type": "FileRead",
            "description": "read config",
            "resource": "/etc/hosts",
            "agent_id": null,
            "source_event_id": null,
            "evidence_refs": [],
            "operation": "read"
        });
        let step: TimelineStep = serde_json::from_value(json).unwrap();
        assert_eq!(step.step_index, 0); // default
        assert!(step.duration_since_prev_ns.is_none());
        assert!(step.pid.is_none());
        assert!(step.anomaly_refs.is_empty());
    }

    #[test]
    fn test_timeline_step_v2_full() {
        let step = TimelineStep {
            step_id: "s2".into(),
            timestamp_ns: 2000000000,
            event_type: "NetworkConnect".into(),
            description: "connect to api".into(),
            resource: Some("api.example.com:443".into()),
            agent_id: Some("agent-1".into()),
            source_event_id: Some("ev-42".into()),
            evidence_refs: vec!["e-1".into()],
            operation: Some("connect".into()),
            step_index: 5,
            duration_since_prev_ns: Some(150_000_000),
            pid: Some(12345),
            anomaly_refs: vec!["a-1".into()],
            metadata: {
                let mut m = HashMap::new();
                m.insert("http_status".into(), 200.into());
                m
            },
        };
        let json = serde_json::to_string(&step).unwrap();
        assert!(json.contains("\"step_index\":5"));
        assert!(json.contains("\"pid\":12345"));
        assert!(json.contains("\"anomaly_refs\":[\"a-1\"]"));
        assert!(json.contains("\"http_status\":200"));
    }

    #[test]
    fn test_v1_backward_compat() {
        // v1 JSON (no step_index, pid, anomaly_refs, metadata) deserializes to v2
        let v1_json = r#"{
            "step_id": "s1",
            "timestamp_ns": 1000000000,
            "event_type": "FileRead",
            "description": "read",
            "resource": "/tmp/x",
            "agent_id": "a1",
            "source_event_id": null,
            "evidence_refs": [],
            "operation": "read"
        }"#;
        let step: TimelineStep = serde_json::from_str(v1_json).unwrap();
        assert_eq!(step.step_index, 0);
        assert!(step.pid.is_none());
        assert!(step.anomaly_refs.is_empty());
        assert!(step.metadata.is_empty());
    }

    #[test]
    fn test_anomaly_severity_enum() {
        let sev = AnomalySeverity::Critical;
        let json = serde_json::to_string(&sev).unwrap();
        assert_eq!(json, "\"critical\"");
    }

    #[test]
    fn test_anomaly_status_enum() {
        let status = AnomalyStatus::Active;
        let json = serde_json::to_string(&status).unwrap();
        assert_eq!(json, "\"active\"");
    }

    #[test]
    fn test_unified_stream_tagging() {
        let event = UnifiedStreamEvent::Anomaly(AnomalyStreamEvent {
            anomaly_id: "a1".into(),
            timestamp_ns: 1000000000,
            agent_id: "agent1".into(),
            anomaly_type: "PolicyViolation".into(),
            severity: AnomalySeverity::Warning,
            confidence: 0.95,
            classification: AnomalyClassification {
                primary_class: "unauthorized_access".into(),
                sub_class: None,
                rule_ids: vec!["r1".into()],
                pattern_ids: vec![],
                score_breakdown: HashMap::new(),
            },
            affected_resources: vec![],
            evidence_chain: vec![],
            remediation_hint: None,
            status: AnomalyStatus::Active,
            resolved_at_ns: None,
            linked_timeline_steps: vec!["s1".into()],
            linked_process_pids: vec![12345],
            observer_verdict: None,
            metadata: HashMap::new(),
        });
        let json = serde_json::to_string(&event).unwrap();
        assert!(json.contains("\"stream_type\":\"anomaly\""));
        assert!(json.contains("\"status\":\"active\""));
    }

    #[test]
    fn test_stream_envelope() {
        let step = TimelineStep {
            step_id: "s1".into(),
            timestamp_ns: 1000000000,
            event_type: "FileRead".into(),
            description: "test".into(),
            resource: None,
            agent_id: None,
            source_event_id: None,
            evidence_refs: vec![],
            operation: None,
            step_index: 0,
            duration_since_prev_ns: None,
            pid: None,
            anomaly_refs: vec![],
            metadata: HashMap::new(),
        };
        let envelope = StreamEnvelope {
            stream_version: "2.0.0".into(),
            sequence_number: 42,
            run_id: "run-1".into(),
            emitted_at_ms: 1717000000000,
            batch: Some(BatchMarker {
                batch_id: "b1".into(),
                batch_index: 0,
                batch_total: 3,
            }),
            event: UnifiedStreamEvent::Timeline(step),
        };
        let json = serde_json::to_string(&envelope).unwrap();
        assert!(json.contains("\"stream_version\":\"2.0.0\""));
        assert!(json.contains("\"sequence_number\":42"));
        assert!(json.contains("\"stream_type\":\"timeline\""));
        assert!(json.contains("\"batch_id\":\"b1\""));
    }

    #[test]
    fn test_process_tree_update_v2() {
        let update = ProcessTreeUpdate {
            tree_id: "tree-1".into(),
            update_type: "node_updated".into(),
            timestamp_ns: 3000000000,
            node: Some(ProcessNode {
                node_id: "n1".into(),
                pid: 100,
                ppid: 1,
                comm: "test".into(),
                start_time_ns: 1000000000,
                end_time_ns: None,
                exit_code: None,
                resource_usage: ProcessResourceUsage {
                    cpu_time_us: 5000,
                    memory_peak_kb: 1024,
                    file_ops_count: 10,
                    network_ops_count: 0,
                },
                agent_id: Some("agent-1".into()),
                anomaly_count: 2,
                has_active_anomalies: true,
                labels: vec!["worker".into()],
            }),
            edge: None,
            previous_node: Some(ProcessNode {
                node_id: "n1".into(),
                pid: 100,
                ppid: 1,
                comm: "test".into(),
                start_time_ns: 1000000000,
                end_time_ns: None,
                exit_code: None,
                resource_usage: ProcessResourceUsage {
                    cpu_time_us: 1000,
                    memory_peak_kb: 512,
                    file_ops_count: 2,
                    network_ops_count: 0,
                },
                agent_id: None,
                anomaly_count: 0,
                has_active_anomalies: false,
                labels: vec![],
            }),
            update_index: 7,
        };
        let json = serde_json::to_string(&update).unwrap();
        assert!(json.contains("\"update_type\":\"node_updated\""));
        assert!(json.contains("\"previous_node\""));
        assert!(json.contains("\"update_index\":7"));
    }

    #[test]
    fn test_run_snapshot() {
        let snapshot = RunSnapshot {
            run_id: "run-1".into(),
            snapshot_time_ns: 5000000000,
            stream_version: "2.0.0".into(),
            timeline: None,
            process_tree: None,
            anomalies: vec![],
            last_sequence_number: 99,
        };
        let json = serde_json::to_string(&snapshot).unwrap();
        assert!(json.contains("\"last_sequence_number\":99"));
    }
}
