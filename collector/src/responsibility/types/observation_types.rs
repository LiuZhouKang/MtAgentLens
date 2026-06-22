// Legacy observation types — migrated from observation/event_model.rs.
// These are the old-architecture NormalizedResource and related types
// used by backward-compat modules. New code uses types/resource.rs.
use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ProcessIdentity {
    pub pid: u32,
    pub start_time_ns: u64,
    pub parent_pid: Option<u32>,
    pub parent_start_time_ns: Option<u64>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ObservationSource {
    Process, Ssl, Stdio, Http, Sse, File, Network, System, Unknown,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ObservationType {
    ProcessExec, ProcessExit, FileRead, FileWrite,
    NetworkConnect, NetworkSend, NetworkRecv,
    SslRead, SslWrite, StdioRead, StdioWrite,
    HttpRequest, HttpResponse, SseEvent, SystemMetric, Unknown,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum RedactionStatus { NotRequired, Redacted, Failed }

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case", tag = "kind")]
pub enum NormalizedResource {
    File { canonical_path: String },
    Network { host: String, port: u16, scheme: Option<String> },
    Process { pid: u32, start_time_ns: u64 },
    Conversation { provider: String, conversation_id: String },
    ToolCall { tool_name: String, call_id: Option<String> },
    Credential { credential_id: String },
    Context { context_id: String },
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ObservationRecord {
    pub observation_id: String,
    pub source: ObservationSource,
    pub event_type: ObservationType,
    pub timestamp_ns: u64,
    pub process_identity: ProcessIdentity,
    pub raw_event_ref: String,
    pub normalized_resource: Option<NormalizedResource>,
    pub payload: Value,
    pub redaction_status: RedactionStatus,
}
