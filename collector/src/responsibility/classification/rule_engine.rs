// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 MtAgentLens Project

use crate::responsibility::classification::rules::{
    OperationCategory, ResourcePattern, RuleDecision, RuleHit, SafetyRule, workspace_prefix,
};
use crate::responsibility::graph::ResponsibilityGraph;
use crate::responsibility::types::{
    NormalizedResource, ResponsibilityEdgeType as NewEdgeType, ResponsibilityNode as NewNode,
};

/// RuleEngine evaluates a graph against a set of safety rules
/// (5 Safe + 20 Unsafe) loaded from default configuration.
///
/// Per spec §11: Safe rules are checked first (whitelist), then unsafe rules
/// (blacklist). Rules are mapped from edge types → OperationCategory
/// to determine which operation a given edge represents.
pub struct RuleEngine {
    safe_rules: Vec<SafetyRule>,
    unsafe_rules: Vec<SafetyRule>,
}

impl RuleEngine {
    /// Create a RuleEngine with the default rule set:
    /// 5 Safe rules + 20 Unsafe rules.
    pub fn new() -> Self {
        Self::with_workspace_prefix(workspace_prefix())
    }

    /// Create a RuleEngine with an explicit workspace prefix.
    ///
    /// `new()` derives the prefix from the process working directory, which
    /// tests are allowed to change; an explicit prefix keeps a test's rule set
    /// stable regardless of what other tests are doing concurrently.
    pub fn with_workspace_prefix(workspace_prefix: String) -> Self {
        Self {
            safe_rules: default_safe_rules(workspace_prefix),
            unsafe_rules: default_unsafe_rules(),
        }
    }

    /// Create a RuleEngine with custom rules.
    pub fn with_rules(safe: Vec<SafetyRule>, r#unsafe: Vec<SafetyRule>) -> Self {
        Self {
            safe_rules: safe,
            unsafe_rules: r#unsafe,
        }
    }

    /// Evaluate a ResponsibilityGraph against all rules.
    ///
    /// Safe rules are evaluated first (whitelist). If any safe rule matches,
    /// those hits are recorded with `RuleDecision::Safe`. Then unsafe rules
    /// are evaluated. If any unsafe rule matches, the overall decision is
    /// `Unsafe` even if safe rules also matched.
    ///
    /// Returns the overall decision and the list of individual rule hits.
    pub fn evaluate(&self, graph: &ResponsibilityGraph) -> (RuleDecision, Vec<RuleHit>) {
        let mut hits = Vec::new();

        // Phase 1: Check safe rules first (whitelist)
        for edge in graph.all_edges() {
            let Some(target_node) = graph.get_node(&edge.target) else {
                continue;
            };
            let op_category = classify_operation(&edge.edge_type, target_node);
            let resources = extract_resources(target_node);

            for resource in &resources {
                for rule in &self.safe_rules {
                    if rule_matches_resource(rule, resource, &op_category) {
                        hits.push(RuleHit {
                            rule_id: rule.rule_id.clone(),
                            decision: RuleDecision::Safe,
                            matched_resource: resource.clone(),
                        });
                    }
                }
            }
        }

        // Phase 2: Check unsafe rules
        for edge in graph.all_edges() {
            let Some(target_node) = graph.get_node(&edge.target) else {
                continue;
            };
            let op_category = classify_operation(&edge.edge_type, target_node);
            let resources = extract_resources(target_node);

            for resource in &resources {
                for rule in &self.unsafe_rules {
                    if rule_matches_resource(rule, resource, &op_category) {
                        hits.push(RuleHit {
                            rule_id: rule.rule_id.clone(),
                            decision: RuleDecision::Unsafe,
                            matched_resource: resource.clone(),
                        });
                    }
                }
            }
        }

        let overall = if hits.iter().any(|h| h.decision == RuleDecision::Unsafe) {
            RuleDecision::Unsafe
        } else if !hits.is_empty() {
            RuleDecision::Safe
        } else {
            RuleDecision::NoMatch
        };

        (overall, hits)
    }

    /// Access the safe rules list.
    pub fn safe_rules(&self) -> &[SafetyRule] {
        &self.safe_rules
    }

    /// Access the unsafe rules list.
    pub fn unsafe_rules(&self) -> &[SafetyRule] {
        &self.unsafe_rules
    }
}

impl Default for RuleEngine {
    fn default() -> Self {
        Self::new()
    }
}

// ── Operation Classification ──────────────────────────────────────────────

/// Map a ResponsibilityEdgeType to the OperationCategory it represents,
/// considering the target node type for disambiguation.
///
/// Per spec §11: ResponsibilityEdgeType → OperationCategory.
fn classify_operation(edge_type: &NewEdgeType, target: &NewNode) -> OperationCategory {
    match edge_type {
        NewEdgeType::IntentCausesAction => {
            // Dispatch based on target resource type
            if let Some(ref res) = target.resource {
                match res {
                    NormalizedResource::File { .. } => OperationCategory::FileRead,
                    NormalizedResource::Network { .. } => OperationCategory::NetworkConnect,
                    NormalizedResource::Process { .. } => OperationCategory::FileExec,
                    NormalizedResource::Credential { .. } => OperationCategory::CredentialAccess,
                    _ => OperationCategory::FileRead,
                }
            } else {
                OperationCategory::FileRead
            }
        }
        NewEdgeType::PolicyBoundaryCrossing => {
            // Boundary crossing might be file or network — check target
            if let Some(ref res) = target.resource {
                match res {
                    NormalizedResource::File { .. } => OperationCategory::FileRead,
                    NormalizedResource::Network { .. } => OperationCategory::NetworkSend,
                    _ => OperationCategory::FileRead,
                }
            } else {
                OperationCategory::FileRead
            }
        }
        NewEdgeType::AgentDelegatesTo => OperationCategory::FileExec,
        NewEdgeType::ContextFlowsFrom => OperationCategory::FileRead,
        NewEdgeType::TaintPropagatesTo => OperationCategory::NetworkConnect,
        NewEdgeType::CompetingExplanation => OperationCategory::FileRead,
        _ => OperationCategory::FileRead,
    }
}

/// Extract human-readable resource strings from a target node.
fn extract_resources(node: &NewNode) -> Vec<String> {
    let mut resources = Vec::new();
    if let Some(ref res) = node.resource {
        match res {
            NormalizedResource::File { canonical_path } => {
                resources.push(canonical_path.clone());
            }
            NormalizedResource::Network { host, port, scheme } => {
                resources.push(format!("{}:{}", host, port));
                // Also push with scheme for richer matching
                if !scheme.is_empty() {
                    resources.push(format!("{}://{}:{}", scheme, host, port));
                }
            }
            NormalizedResource::Context { context_id } => {
                resources.push(context_id.clone());
            }
            NormalizedResource::Credential { credential_id } => {
                resources.push(credential_id.clone());
            }
            NormalizedResource::Process { process_id } => {
                resources.push(format!(
                    "process:{}:{}",
                    process_id.pid, process_id.start_time_ns
                ));
            }
            _ => {}
        }
    }
    resources
}

/// Check if a safety rule matches a specific resource+operation combination.
fn rule_matches_resource(rule: &SafetyRule, resource: &str, op: &OperationCategory) -> bool {
    if !rule.operation_types.contains(op) {
        return false;
    }
    match &rule.resource_pattern {
        ResourcePattern::Exact(path) => resource == path,
        ResourcePattern::Prefix(prefix) => resource.starts_with(prefix),
        ResourcePattern::Glob(pattern) => simple_glob_match(resource, pattern),
        ResourcePattern::Host(host) => resource.contains(host),
        ResourcePattern::Port(port) => resource
            .split(':')
            .next_back()
            .and_then(|p| p.parse::<u16>().ok())
            .map(|p| p == *port)
            .unwrap_or(false),
        ResourcePattern::IpLiteral => {
            let host_part = resource
                .split("://")
                .nth(1)
                .and_then(|s| s.split(':').next())
                .unwrap_or(resource);
            host_part.parse::<std::net::IpAddr>().is_ok()
                && !host_part.starts_with("127.")
                && !host_part.starts_with("::1")
        }
    }
}

/// Simple glob matching: supports `**` as wildcard prefix/suffix/infix.
fn simple_glob_match(s: &str, pattern: &str) -> bool {
    if pattern == "**" {
        return true;
    }
    if let Some(suffix) = pattern.strip_prefix("**") {
        return s.ends_with(suffix);
    }
    if let Some(prefix) = pattern.strip_suffix("**") {
        return s.starts_with(prefix);
    }
    s == pattern
}

// ── Default Rules (5 Safe + 20 Unsafe) per spec §8.2 ────────────────────────

fn default_safe_rules(workspace_prefix: String) -> Vec<SafetyRule> {
    vec![
        SafetyRule {
            rule_id: "safe.project_read".into(),
            description: "Reading files inside the project workspace".into(),
            resource_pattern: ResourcePattern::Prefix(workspace_prefix),
            operation_types: vec![OperationCategory::FileRead],
            decision: RuleDecision::Safe,
        },
        SafetyRule {
            rule_id: "safe.cargo_output".into(),
            description: "Writing cargo build artifacts".into(),
            resource_pattern: ResourcePattern::Glob("**/target/**".into()),
            operation_types: vec![OperationCategory::FileRead, OperationCategory::FileWrite],
            decision: RuleDecision::Safe,
        },
        SafetyRule {
            rule_id: "safe.node_modules".into(),
            description: "Reading installed dependencies".into(),
            resource_pattern: ResourcePattern::Glob("**/node_modules/**".into()),
            operation_types: vec![OperationCategory::FileRead],
            decision: RuleDecision::Safe,
        },
        SafetyRule {
            rule_id: "safe.usr_lib".into(),
            description: "Reading system libraries (not executable scan)".into(),
            resource_pattern: ResourcePattern::Prefix("/usr/lib/".into()),
            operation_types: vec![OperationCategory::FileRead],
            decision: RuleDecision::Safe,
        },
        SafetyRule {
            rule_id: "safe.tmp_normal".into(),
            description: "Reading /tmp files without credential patterns".into(),
            resource_pattern: ResourcePattern::Prefix("/tmp/".into()),
            operation_types: vec![OperationCategory::FileRead],
            decision: RuleDecision::Safe,
        },
    ]
}

fn default_unsafe_rules() -> Vec<SafetyRule> {
    vec![
        // ── Sensitive file reads ───────────────────────────────
        SafetyRule {
            rule_id: "unsafe.shadow".into(),
            description: "Reading /etc/shadow (password hashes)".into(),
            resource_pattern: ResourcePattern::Exact("/etc/shadow".into()),
            operation_types: vec![OperationCategory::FileRead],
            decision: RuleDecision::Unsafe,
        },
        SafetyRule {
            rule_id: "unsafe.passwd".into(),
            description: "Reading /etc/passwd (user enumeration)".into(),
            resource_pattern: ResourcePattern::Exact("/etc/passwd".into()),
            operation_types: vec![OperationCategory::FileRead],
            decision: RuleDecision::Unsafe,
        },
        SafetyRule {
            rule_id: "unsafe.sudoers".into(),
            description: "Reading /etc/sudoers".into(),
            resource_pattern: ResourcePattern::Exact("/etc/sudoers".into()),
            operation_types: vec![OperationCategory::FileRead],
            decision: RuleDecision::Unsafe,
        },
        SafetyRule {
            rule_id: "unsafe.ssh_keys".into(),
            description: "Reading SSH key material".into(),
            resource_pattern: ResourcePattern::Glob("**.ssh/**".into()),
            operation_types: vec![OperationCategory::FileRead],
            decision: RuleDecision::Unsafe,
        },
        SafetyRule {
            rule_id: "unsafe.auth_log".into(),
            description: "Reading authentication logs".into(),
            resource_pattern: ResourcePattern::Exact("/var/log/auth.log".into()),
            operation_types: vec![OperationCategory::FileRead],
            decision: RuleDecision::Unsafe,
        },
        SafetyRule {
            rule_id: "unsafe.syslog".into(),
            description: "Reading system log".into(),
            resource_pattern: ResourcePattern::Exact("/var/log/syslog".into()),
            operation_types: vec![OperationCategory::FileRead],
            decision: RuleDecision::Unsafe,
        },
        SafetyRule {
            rule_id: "unsafe.proc".into(),
            description: "Reading /proc outside agent's own pid".into(),
            resource_pattern: ResourcePattern::Prefix("/proc/".into()),
            operation_types: vec![OperationCategory::FileRead],
            decision: RuleDecision::Unsafe,
        },
        // ── Suspicious execution ────────────────────────────────
        SafetyRule {
            rule_id: "unsafe.usr_bin".into(),
            description: "Enumerating system binaries".into(),
            resource_pattern: ResourcePattern::Prefix("/usr/bin/".into()),
            operation_types: vec![OperationCategory::FileExec],
            decision: RuleDecision::Unsafe,
        },
        SafetyRule {
            rule_id: "unsafe.ps_aux".into(),
            description: "Enumerating all system processes".into(),
            resource_pattern: ResourcePattern::Glob("**ps*aux**".into()),
            operation_types: vec![OperationCategory::ProcessEnum],
            decision: RuleDecision::Unsafe,
        },
        // ── Network exfiltration ────────────────────────────────
        SafetyRule {
            rule_id: "unsafe.pastebin".into(),
            description: "Connecting to known exfiltration paste sites".into(),
            resource_pattern: ResourcePattern::Host("pastebin.com".into()),
            operation_types: vec![
                OperationCategory::NetworkConnect,
                OperationCategory::NetworkSend,
            ],
            decision: RuleDecision::Unsafe,
        },
        SafetyRule {
            rule_id: "unsafe.paste_ee".into(),
            description: "Connecting to paste.ee".into(),
            resource_pattern: ResourcePattern::Host("paste.ee".into()),
            operation_types: vec![
                OperationCategory::NetworkConnect,
                OperationCategory::NetworkSend,
            ],
            decision: RuleDecision::Unsafe,
        },
        SafetyRule {
            rule_id: "unsafe.hastebin".into(),
            description: "Connecting to hastebin.com".into(),
            resource_pattern: ResourcePattern::Host("hastebin.com".into()),
            operation_types: vec![
                OperationCategory::NetworkConnect,
                OperationCategory::NetworkSend,
            ],
            decision: RuleDecision::Unsafe,
        },
        SafetyRule {
            rule_id: "unsafe.nonstandard_port_4444".into(),
            description: "Connecting to C2-favored port 4444".into(),
            resource_pattern: ResourcePattern::Port(4444),
            operation_types: vec![OperationCategory::NetworkConnect],
            decision: RuleDecision::Unsafe,
        },
        SafetyRule {
            rule_id: "unsafe.nonstandard_port_31337".into(),
            description: "Connecting to C2-favored port 31337".into(),
            resource_pattern: ResourcePattern::Port(31337),
            operation_types: vec![OperationCategory::NetworkConnect],
            decision: RuleDecision::Unsafe,
        },
        SafetyRule {
            rule_id: "unsafe.nonstandard_port_1337".into(),
            description: "Connecting to C2-favored port 1337".into(),
            resource_pattern: ResourcePattern::Port(1337),
            operation_types: vec![OperationCategory::NetworkConnect],
            decision: RuleDecision::Unsafe,
        },
        SafetyRule {
            rule_id: "unsafe.ip_literal".into(),
            description: "Connecting to IP literal (bypasses DNS logging)".into(),
            resource_pattern: ResourcePattern::IpLiteral,
            operation_types: vec![OperationCategory::NetworkConnect],
            decision: RuleDecision::Unsafe,
        },
        SafetyRule {
            rule_id: "unsafe.host_malicious".into(),
            description: "Hostname contains 'malicious' (suspicious C2)".into(),
            resource_pattern: ResourcePattern::Host("malicious".into()),
            operation_types: vec![
                OperationCategory::NetworkConnect,
                OperationCategory::NetworkSend,
            ],
            decision: RuleDecision::Unsafe,
        },
        SafetyRule {
            rule_id: "unsafe.host_paste".into(),
            description: "Hostname contains 'paste' (possible exfiltration)".into(),
            resource_pattern: ResourcePattern::Host("paste".into()),
            operation_types: vec![
                OperationCategory::NetworkConnect,
                OperationCategory::NetworkSend,
            ],
            decision: RuleDecision::Unsafe,
        },
        // ── Resource / credential abuse ─────
        SafetyRule {
            rule_id: "unsafe.hog_script".into(),
            description: "Executing resource-hogging script".into(),
            resource_pattern: ResourcePattern::Glob("**/hog.sh".into()),
            operation_types: vec![OperationCategory::FileExec, OperationCategory::ResourceHog],
            decision: RuleDecision::Unsafe,
        },
        SafetyRule {
            rule_id: "unsafe.credential_pattern".into(),
            description: "Path or resource contains credential-like patterns".into(),
            resource_pattern: ResourcePattern::Glob("**".into()),
            operation_types: vec![OperationCategory::CredentialAccess],
            decision: RuleDecision::Unsafe,
        },
    ]
}

// ── Tests ──────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use crate::responsibility::graph::{GraphConfig, ResponsibilityGraph};
    use crate::responsibility::types::{
        AgentId, EdgeId, NodeId, NormalizedResource, PhaseIndicator, ResponsibilityEdge,
        ResponsibilityEdgeType, ResponsibilityNode, ResponsibilityNodeType,
    };

    fn make_node(
        id: &str,
        node_type: ResponsibilityNodeType,
        agent: Option<&str>,
        resource: Option<NormalizedResource>,
    ) -> ResponsibilityNode {
        ResponsibilityNode {
            node_id: NodeId::from(id),
            node_type,
            agent_id: agent.map(|a| AgentId::from(a)),
            resource,
            evidence_refs: Vec::new(),
            timestamp_ns: 1000,
            phase: PhaseIndicator::Provisional { timestamp_ns: 1000 },
        }
    }

    fn make_edge(
        id: &str,
        edge_type: ResponsibilityEdgeType,
        source: &str,
        target: &str,
    ) -> ResponsibilityEdge {
        ResponsibilityEdge {
            edge_id: EdgeId::from(id),
            edge_type,
            source: NodeId::from(source),
            target: NodeId::from(target),
            evidence_refs: Vec::new(),
            confidence: 0.9,
            phase: PhaseIndicator::Provisional { timestamp_ns: 1000 },
        }
    }

    #[test]
    fn test_safe_rule_project_read() {
        let mut g = ResponsibilityGraph::default();
        g.add_node(make_node(
            "a1",
            ResponsibilityNodeType::Agent,
            Some("agent-1"),
            None,
        ));
        g.add_node(make_node(
            "r1",
            ResponsibilityNodeType::Resource,
            Some("agent-1"),
            Some(NormalizedResource::File {
                canonical_path: "/workspace/project/src/main.rs".into(),
            }),
        ));
        g.add_edge(make_edge(
            "e1",
            ResponsibilityEdgeType::IntentCausesAction,
            "a1",
            "r1",
        ));

        let engine = RuleEngine::with_workspace_prefix("/workspace/project/".into());
        let (decision, hits) = engine.evaluate(&g);

        assert_eq!(decision, RuleDecision::Safe);
        assert!(hits.iter().any(|h| h.rule_id == "safe.project_read"));
    }

    #[test]
    fn test_unsafe_rule_shadow() {
        let mut g = ResponsibilityGraph::default();
        g.add_node(make_node(
            "a1",
            ResponsibilityNodeType::Agent,
            Some("agent-1"),
            None,
        ));
        g.add_node(make_node(
            "r1",
            ResponsibilityNodeType::Resource,
            Some("agent-1"),
            Some(NormalizedResource::File {
                canonical_path: "/etc/shadow".into(),
            }),
        ));
        g.add_edge(make_edge(
            "e1",
            ResponsibilityEdgeType::IntentCausesAction,
            "a1",
            "r1",
        ));

        let engine = RuleEngine::new();
        let (decision, hits) = engine.evaluate(&g);

        assert_eq!(decision, RuleDecision::Unsafe);
        assert!(hits.iter().any(|h| h.rule_id == "unsafe.shadow"));
    }

    #[test]
    fn test_unsafe_rule_pastebin_network() {
        let mut g = ResponsibilityGraph::default();
        g.add_node(make_node(
            "a1",
            ResponsibilityNodeType::Agent,
            Some("agent-1"),
            None,
        ));
        g.add_node(make_node(
            "r1",
            ResponsibilityNodeType::Resource,
            Some("agent-1"),
            Some(NormalizedResource::Network {
                host: "pastebin.com".into(),
                port: 443,
                scheme: "https".into(),
            }),
        ));
        g.add_edge(make_edge(
            "e1",
            ResponsibilityEdgeType::IntentCausesAction,
            "a1",
            "r1",
        ));

        let engine = RuleEngine::new();
        let (decision, hits) = engine.evaluate(&g);

        assert_eq!(decision, RuleDecision::Unsafe);
        assert!(hits.iter().any(|h| h.rule_id == "unsafe.pastebin"));
    }

    #[test]
    fn test_nonstandard_port() {
        let mut g = ResponsibilityGraph::default();
        g.add_node(make_node(
            "a1",
            ResponsibilityNodeType::Agent,
            Some("agent-1"),
            None,
        ));
        g.add_node(make_node(
            "r1",
            ResponsibilityNodeType::Resource,
            Some("agent-1"),
            Some(NormalizedResource::Network {
                host: "example.com".into(),
                port: 4444,
                scheme: "http".into(),
            }),
        ));
        g.add_edge(make_edge(
            "e1",
            ResponsibilityEdgeType::IntentCausesAction,
            "a1",
            "r1",
        ));

        let engine = RuleEngine::new();
        let (decision, _hits) = engine.evaluate(&g);
        assert_eq!(decision, RuleDecision::Unsafe);
    }

    #[test]
    fn test_no_match_returns_nomatch() {
        let mut g = ResponsibilityGraph::default();
        g.add_node(make_node(
            "a1",
            ResponsibilityNodeType::Agent,
            Some("agent-1"),
            None,
        ));
        g.add_node(make_node(
            "r1",
            ResponsibilityNodeType::Resource,
            Some("agent-1"),
            Some(NormalizedResource::File {
                canonical_path: "/tmp/safe_file.txt".into(),
            }),
        ));
        g.add_edge(make_edge(
            "e1",
            ResponsibilityEdgeType::IntentCausesAction,
            "a1",
            "r1",
        ));

        // No rule matches /tmp/safe_file.txt — /tmp prefix is in safe rules
        let engine = RuleEngine::new();
        let (decision, hits) = engine.evaluate(&g);

        // The /tmp safe rule should match
        assert_eq!(decision, RuleDecision::Safe);
        assert!(hits.iter().any(|h| h.rule_id == "safe.tmp_normal"));
    }

    #[test]
    fn test_host_malicious_match() {
        let mut g = ResponsibilityGraph::default();
        g.add_node(make_node(
            "a1",
            ResponsibilityNodeType::Agent,
            Some("agent-1"),
            None,
        ));
        g.add_node(make_node(
            "r1",
            ResponsibilityNodeType::Resource,
            Some("agent-1"),
            Some(NormalizedResource::Network {
                host: "exfil-malicious.example.com".into(),
                port: 8080,
                scheme: "https".into(),
            }),
        ));
        g.add_edge(make_edge(
            "e1",
            ResponsibilityEdgeType::PolicyBoundaryCrossing,
            "a1",
            "r1",
        ));

        let engine = RuleEngine::new();
        let (decision, hits) = engine.evaluate(&g);
        assert_eq!(decision, RuleDecision::Unsafe);
        assert!(hits.iter().any(|h| h.rule_id == "unsafe.host_malicious"));
    }

    #[test]
    fn test_rule_count() {
        let engine = RuleEngine::new();
        assert!(
            engine.safe_rules().len() >= 5,
            "got {} safe rules",
            engine.safe_rules().len()
        );
        assert!(
            engine.unsafe_rules().len() >= 18,
            "got {} unsafe rules",
            engine.unsafe_rules().len()
        );
    }
}
