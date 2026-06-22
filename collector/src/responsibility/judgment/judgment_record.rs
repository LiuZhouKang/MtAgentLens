use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum PolicyJudgmentResult {
    Allowed,
    Violation,
    Ambiguous,
    Unproven,
    BlockedAttempt,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct PolicyJudgment {
    pub judgment_id: String,
    pub policy_id: Option<String>,
    pub boundary_id: Option<String>,
    pub subject: Option<String>,
    pub graph_ref: String,
    pub evidence_refs: Vec<String>,
    pub result: PolicyJudgmentResult,
    pub reason: String,
    pub confidence: f32,
}
