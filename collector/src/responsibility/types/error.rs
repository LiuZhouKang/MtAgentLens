use std::fmt;
use serde::{Deserialize, Serialize};

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
