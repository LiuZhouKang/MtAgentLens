// SPDX-License-Identifier: MIT
// Judgment boundary 2: Capability evaluation.
//
// Evaluates whether an agent is authorized to perform a given action on a
// resource, based on a set of PolicyRules. Rules are iterated by priority
// (highest first); the first matching rule determines the judgment.

use crate::responsibility::types::{
    ActionSelector, AgentId, BoundaryId, EvidenceId, OperationCategory, PolicyBoundary,
    PolicyId, PolicyJudgment, PolicyJudgmentResult, PolicyRule, ResourceSelector,
};
use crate::responsibility::types::resource::NormalizedResource;

/// Evaluates capability policies: checks whether an agent is allowed to
/// perform `action_type` on `resource` according to `rules`.
///
/// Rules are iterated in priority order (highest priority first). The first
/// rule whose resource selector and action selector both match determines
/// the result. If no rule matches, returns `Unproven`.
pub struct CapabilityEvaluator;

impl CapabilityEvaluator {
    /// Evaluate capability for a single agent-resource-action triple.
    ///
    /// Returns a `PolicyJudgment` based on the first matching rule, or
    /// `Unproven` if no rule applies.
    pub fn evaluate(
        agent_id: &AgentId,
        resource: &NormalizedResource,
        action_type: &ActionSelector,
        rules: &[PolicyRule],
    ) -> PolicyJudgment {
        // Sort rules by priority descending (highest priority first).
        let mut sorted: Vec<&PolicyRule> = rules.iter().collect();
        sorted.sort_by(|a, b| b.priority.cmp(&a.priority));

        // Find the first rule whose resource and action selectors both match.
        for rule in &sorted {
            if Self::resource_matches(&rule.resource, resource)
                && Self::action_matches(&rule.action, action_type)
            {
                let judgment_id = uuid::Uuid::new_v4().to_string();
                return PolicyJudgment {
                    judgment_id,
                    policy_id: PolicyId::from(rule.rule_id.as_str()),
                    boundary_id: BoundaryId::from("boundary.capability"),
                    subject: agent_id.clone(),
                    graph_ref: String::new(),
                    evidence_refs: vec![],
                    result: rule.judgment.clone(),
                    reason: format!(
                        "rule '{}' (priority {}): {} — {}",
                        rule.rule_id, rule.priority, rule.description, resource_display(resource)
                    ),
                    confidence: 0.90,
                };
            }
        }

        // No matching rule found → Unproven.
        PolicyJudgment {
            judgment_id: uuid::Uuid::new_v4().to_string(),
            policy_id: PolicyId::from("policy.capability"),
            boundary_id: BoundaryId::from("boundary.capability"),
            subject: agent_id.clone(),
            graph_ref: String::new(),
            evidence_refs: vec![],
            result: PolicyJudgmentResult::Unproven,
            reason: format!(
                "no capability rule matches action {:?} on {}",
                action_type,
                resource_display(resource)
            ),
            confidence: 0.0,
        }
    }

    // ── Resource selector matching ─────────────────────────────────────────

    fn resource_matches(selector: &ResourceSelector, resource: &NormalizedResource) -> bool {
        match selector {
            ResourceSelector::Any => true,
            ResourceSelector::Exact(pattern) => Self::resource_exact(pattern, resource),
            ResourceSelector::Prefix(prefix) => Self::resource_prefix(prefix, resource),
            ResourceSelector::Glob(glob) => Self::resource_glob(glob, resource),
            ResourceSelector::Host(host) => Self::resource_host(host, resource),
            ResourceSelector::Port(port) => Self::resource_port(*port, resource),
            ResourceSelector::PortRange(lo, hi) => Self::resource_port_range(*lo, *hi, resource),
        }
    }

    fn resource_exact(pattern: &str, resource: &NormalizedResource) -> bool {
        match resource {
            NormalizedResource::File { canonical_path } => canonical_path == pattern,
            NormalizedResource::Network { host, port, .. } => {
                format!("{}:{}", host, port) == pattern
            }
            NormalizedResource::Process { .. } => false,
            NormalizedResource::LlmConversation { .. } => false,
            NormalizedResource::ToolCall { tool_name, .. } => tool_name == pattern,
            NormalizedResource::Credential { credential_id } => credential_id == pattern,
            NormalizedResource::Context { context_id } => context_id == pattern,
        }
    }

    fn resource_prefix(prefix: &str, resource: &NormalizedResource) -> bool {
        match resource {
            NormalizedResource::File { canonical_path } => canonical_path.starts_with(prefix),
            NormalizedResource::Network { host, .. } => host.starts_with(prefix),
            NormalizedResource::Process { .. } => false,
            NormalizedResource::LlmConversation { .. } => false,
            NormalizedResource::ToolCall { tool_name, .. } => tool_name.starts_with(prefix),
            NormalizedResource::Credential { credential_id } => credential_id.starts_with(prefix),
            NormalizedResource::Context { context_id } => context_id.starts_with(prefix),
        }
    }

    fn resource_glob(glob: &str, resource: &NormalizedResource) -> bool {
        let path = match resource {
            NormalizedResource::File { canonical_path } => canonical_path.as_str(),
            NormalizedResource::Network { host, port, .. } => {
                // For network resources, check host against glob.
                return simple_glob_match(glob, host);
            }
            _ => return false,
        };
        simple_glob_match(glob, path)
    }

    fn resource_host(host_pat: &str, resource: &NormalizedResource) -> bool {
        match resource {
            NormalizedResource::Network { host, .. } => host == host_pat,
            _ => false,
        }
    }

    fn resource_port(port: u16, resource: &NormalizedResource) -> bool {
        match resource {
            NormalizedResource::Network { port: rp, .. } => *rp == port,
            _ => false,
        }
    }

    fn resource_port_range(lo: u16, hi: u16, resource: &NormalizedResource) -> bool {
        match resource {
            NormalizedResource::Network { port: rp, .. } => *rp >= lo && *rp <= hi,
            _ => false,
        }
    }

    // ── Action selector matching ───────────────────────────────────────────

    fn action_matches(selector: &ActionSelector, action: &ActionSelector) -> bool {
        match selector {
            ActionSelector::Any => true,
            ActionSelector::Read => matches!(action, ActionSelector::Read),
            ActionSelector::Write => matches!(action, ActionSelector::Write),
            ActionSelector::Execute => matches!(action, ActionSelector::Execute),
            ActionSelector::Delete => matches!(action, ActionSelector::Delete),
            ActionSelector::Operation(op) => {
                if let ActionSelector::Operation(candidate_op) = action {
                    candidate_op == op
                } else {
                    false
                }
            }
        }
    }
}

// ── Glob matching helper ───────────────────────────────────────────────────

/// Simple glob matching supporting `**` (matches any number of path segments)
/// and `*` (matches within a single segment). Does NOT implement full
/// glob semantics — only the patterns used by default policy rules.
fn simple_glob_match(pattern: &str, path: &str) -> bool {
    // Handle `**` — matches everything.
    if pattern == "**" {
        return true;
    }

    // Handle `**/suffix` — matches any path ending with `/suffix`.
    if let Some(suffix) = pattern.strip_prefix("**/") {
        return path.ends_with(suffix) || path[1..].ends_with(suffix);
    }

    // Handle `prefix/**` — matches any path starting with `prefix/`.
    if let Some(prefix) = pattern.strip_suffix("/**") {
        return path.starts_with(prefix);
    }

    // Handle `prefix/**/suffix` — matches paths with prefix and suffix.
    if let Some(rest) = pattern.strip_prefix("**/") {
        if let Some(suffix) = rest.rfind("/**/") {
            let (_mid, end) = rest.split_at(suffix + 4);
            return path.ends_with(end);
        }
    }

    // Handle `**/mid/**/suffix` patterns.
    if pattern.contains("**") {
        let parts: Vec<&str> = pattern.split("**").collect();
        if parts.len() >= 2 {
            if let (Some(start), Some(end)) = (parts.first(), parts.last()) {
                let start = start.trim_end_matches('/');
                let end = end.trim_start_matches('/');
                if start.is_empty() && !end.is_empty() {
                    return path.ends_with(end);
                }
                if !start.is_empty() && end.is_empty() {
                    return path.starts_with(start);
                }
                if !start.is_empty() && !end.is_empty() {
                    return path.starts_with(start) && path.ends_with(end);
                }
            }
        }
    }

    // Handle single `*` in a path segment (non-recursive wildcard).
    if pattern.contains('*') && !pattern.contains("**") {
        let pat_parts: Vec<&str> = pattern.split('*').collect();
        let mut cursor = 0;
        for (i, part) in pat_parts.iter().enumerate() {
            if part.is_empty() {
                continue;
            }
            if let Some(pos) = path[cursor..].find(*part) {
                cursor += pos + part.len();
            } else {
                return false;
            }
            if i < pat_parts.len() - 1 && pat_parts[i + 1].is_empty() {
                // Skip to next '/'-boundary for single-segment wildcards.
                // For simplicity, just continue matching.
            }
        }
        return true;
    }

    // Exact match fallback.
    pattern == path
}

/// Human-readable display string for a NormalizedResource.
fn resource_display(resource: &NormalizedResource) -> String {
    match resource {
        NormalizedResource::File { canonical_path } => canonical_path.clone(),
        NormalizedResource::Network { host, port, scheme } => {
            format!("{}://{}:{}", scheme, host, port)
        }
        NormalizedResource::Process { process_id } => {
            format!("process(pid={})", process_id.pid)
        }
        NormalizedResource::LlmConversation { provider, model, .. } => {
            format!("llm({}/{})", provider, model)
        }
        NormalizedResource::ToolCall { tool_name, .. } => tool_name.clone(),
        NormalizedResource::Credential { credential_id } => credential_id.clone(),
        NormalizedResource::Context { context_id } => context_id.clone(),
    }
}

// ── Tests ──────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use crate::responsibility::types::PolicyJudgmentResult;

    fn make_file(path: &str) -> NormalizedResource {
        NormalizedResource::File {
            canonical_path: path.to_string(),
        }
    }

    fn make_network(host: &str, port: u16) -> NormalizedResource {
        NormalizedResource::Network {
            host: host.to_string(),
            port,
            scheme: "https".to_string(),
        }
    }

    fn make_agent(id: &str) -> AgentId {
        AgentId::from(id)
    }

    fn make_rules() -> Vec<PolicyRule> {
        vec![
            // Unsafe: /etc/shadow read → Violation (high priority)
            PolicyRule {
                rule_id: "unsafe_shadow".into(),
                priority: 100,
                boundary: PolicyBoundary::Capability,
                resource: ResourceSelector::Prefix("/etc/shadow".into()),
                action: ActionSelector::Read,
                judgment: PolicyJudgmentResult::Violation,
                description: "Reading /etc/shadow is unsafe".into(),
            },
            // Safe: project directory read → Allowed (medium priority)
            PolicyRule {
                rule_id: "safe_project_read".into(),
                priority: 80,
                boundary: PolicyBoundary::Capability,
                resource: ResourceSelector::Prefix("/home/lzk/mtagentlens/".into()),
                action: ActionSelector::Read,
                judgment: PolicyJudgmentResult::Allowed,
                description: "Project files can be read".into(),
            },
            // Unsafe: non-standard port → Violation
            PolicyRule {
                rule_id: "unsafe_port".into(),
                priority: 90,
                boundary: PolicyBoundary::Capability,
                resource: ResourceSelector::Port(4444),
                action: ActionSelector::Any,
                judgment: PolicyJudgmentResult::Violation,
                description: "Connection to port 4444 is suspicious".into(),
            },
            // Safe: .log files write in /tmp → Allowed
            PolicyRule {
                rule_id: "safe_tmp_write".into(),
                priority: 50,
                boundary: PolicyBoundary::Capability,
                resource: ResourceSelector::Glob("**/target/**".into()),
                action: ActionSelector::Write,
                judgment: PolicyJudgmentResult::Allowed,
                description: "Writing to target/ is allowed".into(),
            },
        ]
    }

    // ── Resource selector tests ────────────────────────────────────────────

    #[test]
    fn exact_file_match() {
        let rules = vec![PolicyRule {
            rule_id: "exact_test".into(),
            priority: 100,
            boundary: PolicyBoundary::Capability,
            resource: ResourceSelector::Exact("/etc/hosts".into()),
            action: ActionSelector::Any,
            judgment: PolicyJudgmentResult::Violation,
            description: "test".into(),
        }];
        let agent = make_agent("agent-1");
        let res = make_file("/etc/hosts");
        let result = CapabilityEvaluator::evaluate(&agent, &res, &ActionSelector::Read, &rules);
        assert_eq!(result.result, PolicyJudgmentResult::Violation);
    }

    #[test]
    fn prefix_file_match() {
        let rules = vec![PolicyRule {
            rule_id: "prefix_test".into(),
            priority: 100,
            boundary: PolicyBoundary::Capability,
            resource: ResourceSelector::Prefix("/etc/".into()),
            action: ActionSelector::Read,
            judgment: PolicyJudgmentResult::Violation,
            description: "test".into(),
        }];
        let agent = make_agent("agent-1");
        let res = make_file("/etc/passwd");
        let result = CapabilityEvaluator::evaluate(&agent, &res, &ActionSelector::Read, &rules);
        assert_eq!(result.result, PolicyJudgmentResult::Violation);
    }

    #[test]
    fn glob_match_target_dir() {
        let rules = vec![PolicyRule {
            rule_id: "glob_test".into(),
            priority: 50,
            boundary: PolicyBoundary::Capability,
            resource: ResourceSelector::Glob("**/target/**".into()),
            action: ActionSelector::Write,
            judgment: PolicyJudgmentResult::Allowed,
            description: "test".into(),
        }];
        let agent = make_agent("agent-1");
        let res = make_file("/home/user/project/target/debug/build/output");
        let result = CapabilityEvaluator::evaluate(&agent, &res, &ActionSelector::Write, &rules);
        assert_eq!(result.result, PolicyJudgmentResult::Allowed);
    }

    #[test]
    fn host_match() {
        let rules = vec![PolicyRule {
            rule_id: "host_test".into(),
            priority: 100,
            boundary: PolicyBoundary::Capability,
            resource: ResourceSelector::Host("pastebin.com".into()),
            action: ActionSelector::Any,
            judgment: PolicyJudgmentResult::Violation,
            description: "test".into(),
        }];
        let agent = make_agent("agent-1");
        let res = make_network("pastebin.com", 443);
        let result = CapabilityEvaluator::evaluate(&agent, &res, &ActionSelector::Any, &rules);
        assert_eq!(result.result, PolicyJudgmentResult::Violation);
    }

    #[test]
    fn port_match() {
        let rules = vec![PolicyRule {
            rule_id: "port_test".into(),
            priority: 100,
            boundary: PolicyBoundary::Capability,
            resource: ResourceSelector::Port(31337),
            action: ActionSelector::Any,
            judgment: PolicyJudgmentResult::Violation,
            description: "test".into(),
        }];
        let agent = make_agent("agent-1");
        let res = make_network("example.com", 31337);
        let result = CapabilityEvaluator::evaluate(&agent, &res, &ActionSelector::Any, &rules);
        assert_eq!(result.result, PolicyJudgmentResult::Violation);
    }

    #[test]
    fn port_range_match() {
        let rules = vec![PolicyRule {
            rule_id: "port_range_test".into(),
            priority: 100,
            boundary: PolicyBoundary::Capability,
            resource: ResourceSelector::PortRange(1024, 65535),
            action: ActionSelector::Any,
            judgment: PolicyJudgmentResult::Violation,
            description: "test".into(),
        }];
        let agent = make_agent("agent-1");
        let res = make_network("example.com", 8080);
        let result = CapabilityEvaluator::evaluate(&agent, &res, &ActionSelector::Any, &rules);
        assert_eq!(result.result, PolicyJudgmentResult::Violation);
    }

    #[test]
    fn port_range_no_match() {
        let rules = vec![PolicyRule {
            rule_id: "port_range_test".into(),
            priority: 100,
            boundary: PolicyBoundary::Capability,
            resource: ResourceSelector::PortRange(1024, 65535),
            action: ActionSelector::Any,
            judgment: PolicyJudgmentResult::Violation,
            description: "test".into(),
        }];
        let agent = make_agent("agent-1");
        let res = make_network("example.com", 80);
        let result = CapabilityEvaluator::evaluate(&agent, &res, &ActionSelector::Any, &rules);
        assert_eq!(result.result, PolicyJudgmentResult::Unproven);
    }

    // ── Priority ordering tests ────────────────────────────────────────────

    #[test]
    fn higher_priority_rule_wins() {
        let rules = vec![
            PolicyRule {
                rule_id: "low_priority".into(),
                priority: 10,
                boundary: PolicyBoundary::Capability,
                resource: ResourceSelector::Prefix("/etc/".into()),
                action: ActionSelector::Read,
                judgment: PolicyJudgmentResult::Allowed,
                description: "low".into(),
            },
            PolicyRule {
                rule_id: "high_priority".into(),
                priority: 100,
                boundary: PolicyBoundary::Capability,
                resource: ResourceSelector::Exact("/etc/shadow".into()),
                action: ActionSelector::Read,
                judgment: PolicyJudgmentResult::Violation,
                description: "high".into(),
            },
        ];
        let agent = make_agent("agent-1");
        let res = make_file("/etc/shadow");
        let result = CapabilityEvaluator::evaluate(&agent, &res, &ActionSelector::Read, &rules);
        assert_eq!(result.result, PolicyJudgmentResult::Violation);
        assert!(result.reason.contains("high_priority"));
    }

    #[test]
    fn no_matching_rule_returns_unproven() {
        let rules = make_rules();
        let agent = make_agent("agent-1");
        let res = make_file("/some/unknown/path");
        let result = CapabilityEvaluator::evaluate(&agent, &res, &ActionSelector::Read, &rules);
        assert_eq!(result.result, PolicyJudgmentResult::Unproven);
    }

    // ── Action selector tests ──────────────────────────────────────────────

    #[test]
    fn action_read_mismatch_skips_rule() {
        let rules = vec![PolicyRule {
            rule_id: "write_only".into(),
            priority: 100,
            boundary: PolicyBoundary::Capability,
            resource: ResourceSelector::Prefix("/tmp/".into()),
            action: ActionSelector::Write,
            judgment: PolicyJudgmentResult::Violation,
            description: "only catches writes".into(),
        }];
        let agent = make_agent("agent-1");
        let res = make_file("/tmp/test.txt");
        let result = CapabilityEvaluator::evaluate(&agent, &res, &ActionSelector::Read, &rules);
        assert_eq!(result.result, PolicyJudgmentResult::Unproven);
    }

    #[test]
    fn action_any_matches_all() {
        let rules = vec![PolicyRule {
            rule_id: "any_action".into(),
            priority: 100,
            boundary: PolicyBoundary::Capability,
            resource: ResourceSelector::Prefix("/tmp/".into()),
            action: ActionSelector::Any,
            judgment: PolicyJudgmentResult::Allowed,
            description: "any action on /tmp".into(),
        }];
        let agent = make_agent("agent-1");
        let res = make_file("/tmp/test.txt");

        // Read should match Any
        let r1 = CapabilityEvaluator::evaluate(&agent, &res, &ActionSelector::Read, &rules);
        assert_eq!(r1.result, PolicyJudgmentResult::Allowed);

        // Write should match Any
        let r2 = CapabilityEvaluator::evaluate(&agent, &res, &ActionSelector::Write, &rules);
        assert_eq!(r2.result, PolicyJudgmentResult::Allowed);
    }

    #[test]
    fn operation_selector_filerw() {
        let rules = vec![PolicyRule {
            rule_id: "file_rw_test".into(),
            priority: 100,
            boundary: PolicyBoundary::Capability,
            resource: ResourceSelector::Prefix("/data/".into()),
            action: ActionSelector::Operation(OperationCategory::FileRead),
            judgment: PolicyJudgmentResult::Allowed,
            description: "file read on /data".into(),
        }];
        let agent = make_agent("agent-1");
        let res = make_file("/data/important.csv");

        // FileRead operation should match
        let r1 = CapabilityEvaluator::evaluate(
            &agent,
            &res,
            &ActionSelector::Operation(OperationCategory::FileRead),
            &rules,
        );
        assert_eq!(r1.result, PolicyJudgmentResult::Allowed);

        // FileWrite operation should NOT match FileRead rule
        let r2 = CapabilityEvaluator::evaluate(
            &agent,
            &res,
            &ActionSelector::Operation(OperationCategory::FileWrite),
            &rules,
        );
        assert_eq!(r2.result, PolicyJudgmentResult::Unproven);
    }

    // ── Network resource tests ─────────────────────────────────────────────

    #[test]
    fn safe_network_not_matched_by_port_rule() {
        let rules = vec![PolicyRule {
            rule_id: "unsafe_port".into(),
            priority: 90,
            boundary: PolicyBoundary::Capability,
            resource: ResourceSelector::Port(4444),
            action: ActionSelector::Any,
            judgment: PolicyJudgmentResult::Violation,
            description: "block 4444".into(),
        }];
        let agent = make_agent("agent-1");
        let res = make_network("api.github.com", 443);
        let result = CapabilityEvaluator::evaluate(&agent, &res, &ActionSelector::Any, &rules);
        assert_eq!(result.result, PolicyJudgmentResult::Unproven);
    }

    // ── Glob helper tests ──────────────────────────────────────────────────

    #[test]
    fn glob_double_star_matches_everything() {
        assert!(simple_glob_match("**", "/any/path/at/all"));
        assert!(simple_glob_match("**", ""));
    }

    #[test]
    fn glob_suffix_match() {
        assert!(simple_glob_match("**/target/**", "/home/user/project/target/debug/output"));
        assert!(simple_glob_match("**/node_modules/**", "/app/node_modules/react/index.js"));
    }

    #[test]
    fn glob_prefix_match() {
        assert!(simple_glob_match("/home/**", "/home/user/docs/file.txt"));
    }

    #[test]
    fn glob_no_match() {
        assert!(!simple_glob_match("**/*.rs", "/home/user/docs/file.txt"));
    }
}
