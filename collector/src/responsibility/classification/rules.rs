// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 MtAgentLens Project

#[derive(Debug, Clone)]
pub enum ResourcePattern {
    Exact(String),
    Prefix(String),
    Glob(String),
    Host(String),
    Port(u16),
    IpLiteral,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum OperationCategory {
    FileRead,
    FileWrite,
    FileExec,
    NetworkConnect,
    NetworkSend,
    ProcessEnum,
    CredentialAccess,
    ResourceHog,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RuleDecision {
    Safe,
    Unsafe,
    NoMatch,
}

#[derive(Debug, Clone)]
pub struct SafetyRule {
    pub rule_id: String,
    pub description: String,
    pub resource_pattern: ResourcePattern,
    pub operation_types: Vec<OperationCategory>,
    pub decision: RuleDecision,
}

#[derive(Debug, Clone)]
pub struct RuleHit {
    pub rule_id: String,
    pub decision: RuleDecision,
    pub matched_resource: String,
}

/// The project workspace the collector is judging, derived at runtime.
///
/// A hardcoded checkout path (`/home/<user>/<repo>/`) makes the "safe project
/// read" rule meaningless for every other installation: reading one's own
/// project would not be recognised as safe. The collector is started from the
/// workspace root, so the current directory *is* the workspace.
///
/// Returns a NUL-prefixed string when the working directory is unavailable so
/// the rule matches nothing instead of matching everything.
pub fn workspace_prefix() -> String {
    match std::env::current_dir() {
        Ok(dir) => format!("{}/", dir.display()),
        Err(_) => "\u{0}".to_string(),
    }
}
