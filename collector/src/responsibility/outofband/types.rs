// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 MtAgentLens Project

//! Facts recovered from an agent's own on-disk records.
//!
//! These are *asserted* by the agent (it wrote them itself), as opposed to the
//! *inferred* facts derived from eBPF observation. Both are kept: they
//! cross-check each other rather than one replacing the other.

/// A tool invocation the agent recorded for itself.
#[derive(Debug, Clone, PartialEq)]
pub struct OutOfBandToolCall {
    /// Tool name as the agent recorded it, e.g. `ls`.
    pub tool_name: String,
    /// Structured arguments, e.g. `{"path": "/x"}`.
    pub args: serde_json::Value,
    /// Agent key from the record, e.g. `agent:main:main`.
    pub agent_key: String,
    /// Run identifier, when present.
    pub run_id: Option<String>,
    /// Event time in epoch nanoseconds (converted from the record's ISO ts).
    pub ts_ns: u64,
}

/// Identity the agent asserted about itself.
#[derive(Debug, Clone, PartialEq)]
pub struct OutOfBandIdentity {
    /// e.g. `agent:main:main`.
    pub agent_key: String,
    /// Working directory the agent recorded, when present.
    pub workspace_dir: Option<String>,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tool_call_carries_structured_args() {
        let c = OutOfBandToolCall {
            tool_name: "ls".into(),
            args: serde_json::json!({"path": "/x"}),
            agent_key: "agent:main:main".into(),
            run_id: Some("run-1".into()),
            ts_ns: 1,
        };
        assert_eq!(c.args["path"], "/x");
        assert_eq!(c.agent_key, "agent:main:main");
    }
}
