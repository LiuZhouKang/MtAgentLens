// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 MtAgentLens Project

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RunRecord {
    pub run_id: String,
    pub started_at_ns: u64,
    pub ended_at_ns: Option<u64>,
    pub scenario_id: Option<String>,
    pub mode: Option<String>,
    pub collector_version: Option<String>,
    /// The run's single aggregated verdict (schema v3). `None` for runs that
    /// never finalized or predate v3 — consumers must show "unknown", not
    /// "safe".
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub final_decision: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub final_severity: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub final_authority: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub final_alert_id: Option<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub decision_provenance: Vec<String>,
}
