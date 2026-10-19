// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 MtAgentLens Project

use serde::{Deserialize, Serialize};
use std::fmt;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ResponsibilityError {
    pub code: String,
    pub message: String,
    pub module: String,
}

impl fmt::Display for ResponsibilityError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "[{}] {}: {}", self.module, self.code, self.message)
    }
}

impl std::error::Error for ResponsibilityError {}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PersistenceError {
    pub code: String,
    pub message: String,
}
