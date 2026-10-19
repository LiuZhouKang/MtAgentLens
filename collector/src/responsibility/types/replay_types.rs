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
}
