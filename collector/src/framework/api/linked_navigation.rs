use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LinkedTimelineEvent {
    pub id: String,
    pub timestamp: u64,
    pub source: String,
    pub pid: u32,
    pub comm: String,
    pub data: serde_json::Value,
    pub parent_pid: Option<u32>,
    pub related_anomaly_ids: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LinkedProcessNode {
    pub pid: u32,
    pub comm: String,
    pub ppid: Option<u32>,
    pub first_event_id: Option<String>,
    pub last_event_id: Option<String>,
    pub event_count: usize,
    pub anomaly_count: usize,
    pub has_active_anomalies: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LinkedAnomalyEvent {
    pub id: String,
    pub timestamp: u64,
    pub severity: String,
    pub r#type: String,
    pub triggering_event_id: Option<String>,
    pub related_event_ids: Vec<String>,
    pub affected_pids: Vec<u32>,
    pub description: String,
    pub context: serde_json::Value,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LinkedEventsResponse {
    pub timeline: Vec<LinkedTimelineEvent>,
    pub process_tree: Vec<LinkedProcessNode>,
    pub anomalies: Vec<LinkedAnomalyEvent>,
}
