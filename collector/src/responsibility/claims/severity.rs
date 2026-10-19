// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 MtAgentLens Project

use serde::{Deserialize, Serialize};

/// Severity attached to a claim and to an observer decision.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ClaimSeverity {
    Info,
    Warning,
    Critical,
}

impl ClaimSeverity {
    pub fn as_str(&self) -> &'static str {
        match self {
            ClaimSeverity::Info => "info",
            ClaimSeverity::Warning => "warning",
            ClaimSeverity::Critical => "critical",
        }
    }
}
