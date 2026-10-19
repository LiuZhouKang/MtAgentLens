// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 MtAgentLens Project

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub enum PhaseIndicator {
    Provisional {
        timestamp_ns: u64,
    },
    Authoritative {
        timestamp_ns: u64,
        overrides: Option<Box<PhaseIndicator>>,
    },
}
