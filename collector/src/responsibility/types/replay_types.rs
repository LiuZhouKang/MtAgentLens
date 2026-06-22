use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RunRecord {
    pub run_id: String,
    pub started_at_ns: u64,
    pub ended_at_ns: Option<u64>,
    pub scenario_id: Option<String>,
    pub mode: Option<String>,
    pub collector_version: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RunDiff {
    pub op_diffs: Vec<OpDiff>,
    pub total_before: usize,
    pub total_after: usize,
    pub summary: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OpDiff {
    pub op_type: String,
    pub count_before: usize,
    pub count_after: usize,
    pub delta: i64,
    pub change: DiffChange,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum DiffChange { Added, Removed, Increased, Decreased, Unchanged }
