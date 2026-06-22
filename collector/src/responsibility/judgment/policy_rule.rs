use crate::responsibility::graph::ResponsibilityEdgeType;
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum PolicyBoundaryType {
    File,
    Network,
    Process,
    Credential,
    Context,
    ToolCall,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct PolicyRule {
    pub policy_id: String,
    pub boundary_id: String,
    pub boundary_type: PolicyBoundaryType,
    pub subject_selector: Option<String>,
    pub resource_selector: Option<String>,
    pub action_selector: Option<ResponsibilityEdgeType>,
    pub severity: String,
}
