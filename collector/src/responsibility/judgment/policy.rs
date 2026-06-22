use std::collections::HashSet;

use crate::responsibility::judgment::delegation::DelegationRecord;
use crate::responsibility::types::ids::{BoundaryId, EvidenceId, PolicyId, ProcessId};
use crate::responsibility::types::policy_types::{EvidenceRef, PolicyEvaluationResult, ResponsibilityClaimState};
use crate::responsibility::types::signal_types::{DETECTION_CONFIDENCE_POLICY, MatchLayer};
use crate::responsibility::types::support_types::ResourceId;

/// Default propagation window: 30 seconds.
/// If Agent B reads a file within this window after Agent A wrote it,
/// we flag it as potential context pollution.
pub const CONTEXT_PROPAGATION_WINDOW_NS: u64 = 30_000_000_000;

// ── 4-Boundary Policy Evaluation ────────────────────────────────────────

/// Evaluate a delegation boundary: was agent-to-agent delegation authorized?
///
/// Checks whether a delegation record exists between the two agents.
/// Returns `Observed` if a matching record is found, `Ambiguous` if
/// delegation_records is empty (caller couldn't check), `Unproven` otherwise.
pub fn evaluate_delegation_boundary(
    from_agent: &str,
    to_agent: &str,
    delegation_records: &[DelegationRecord],
) -> PolicyEvaluationResult {
    if delegation_records.is_empty() {
        return PolicyEvaluationResult {
            policy_id: Some(PolicyId("policy.delegation".to_string())),
            boundary_id: Some(BoundaryId("boundary.delegation".to_string())),
            claim_state: ResponsibilityClaimState::Ambiguous,
            matched: false,
            reason: "no delegation records available to verify cross-agent authority".to_string(),
        };
    }

    let found = delegation_records
        .iter()
        .any(|r| r.from_agent_id == from_agent && r.to_agent_id == to_agent);

    if found {
        PolicyEvaluationResult {
            policy_id: Some(PolicyId("policy.delegation".to_string())),
            boundary_id: Some(BoundaryId("boundary.delegation".to_string())),
            claim_state: ResponsibilityClaimState::Observed,
            matched: true,
            reason: format!(
                "delegation from {} to {} verified via process-spawn record",
                from_agent, to_agent
            ),
        }
    } else {
        PolicyEvaluationResult {
            policy_id: Some(PolicyId("policy.delegation".to_string())),
            boundary_id: Some(BoundaryId("boundary.delegation".to_string())),
            claim_state: ResponsibilityClaimState::Unproven,
            matched: false,
            reason: format!(
                "no delegation record found from {} to {}",
                from_agent, to_agent
            ),
        }
    }
}

/// Evaluate a capability boundary: is this agent allowed to use this resource?
///
/// Maps ResourceId variants to capability checks. Currently implements a
/// fail-open allowlist model (everything is allowed unless explicitly blocked)
/// — the framework is structured so that explicit capability rules can be
/// added without changing the call sites.
///
/// Boundary IDs are resource-type-specific for audit granularity.
pub fn evaluate_capability_boundary(
    _agent_id: &str,
    resource: &ResourceId,
) -> PolicyEvaluationResult {
    match resource {
        ResourceId::File { canonical_path } => {
            // Phase 1 capability rule: sensitive paths always trigger a
            // capability check that marks the boundary as observed.
            if is_sensitive_path(canonical_path) {
                PolicyEvaluationResult {
                    policy_id: Some(PolicyId("policy.file.read".to_string())),
                    boundary_id: Some(BoundaryId("boundary.file_read".to_string())),
                    claim_state: ResponsibilityClaimState::Observed,
                    matched: true,
                    reason: format!("sensitive file access: {}", canonical_path),

                }
            } else {
                PolicyEvaluationResult {
                    policy_id: Some(PolicyId("policy.file.read".to_string())),
                    boundary_id: Some(BoundaryId("boundary.file_read".to_string())),
                    claim_state: ResponsibilityClaimState::Inferred,
                    matched: true,
                    reason: format!("file access within capability bounds: {}", canonical_path),

                }
            }
        }
        ResourceId::Network {
            host,
            port,
            scheme: _,
        } => {
            if is_suspicious_target(host, *port) {
                PolicyEvaluationResult {
                    policy_id: Some(PolicyId("policy.network.connect".to_string())),
                    boundary_id: Some(BoundaryId("boundary.network_connect".to_string())),
                    claim_state: ResponsibilityClaimState::Observed,
                    matched: true,
                    reason: format!("suspicious network target: {}:{}", host, port),

                }
            } else {
                PolicyEvaluationResult {
                    policy_id: Some(PolicyId("policy.network.connect".to_string())),
                    boundary_id: Some(BoundaryId("boundary.network_connect".to_string())),
                    claim_state: ResponsibilityClaimState::Inferred,
                    matched: true,
                    reason: format!("network access within capability bounds: {}:{}", host, port),

                }
            }
        }
        ResourceId::Process { process_id } => PolicyEvaluationResult {
            policy_id: Some(PolicyId("policy.process.exec".to_string())),
            boundary_id: Some(BoundaryId("boundary.process_exec".to_string())),
            claim_state: ResponsibilityClaimState::Inferred,
            matched: true,
            reason: format!(
                "process exec capability check: pid={} start_time={}",
                process_id.pid, process_id.start_time_ns
            ),

        },
        ResourceId::LlmConversation {
            provider,
            model,
            conversation_id: _,
        } => PolicyEvaluationResult {
            policy_id: Some(PolicyId("policy.capability.llm".to_string())),
            boundary_id: Some(BoundaryId("boundary.capability.llm".to_string())),
            claim_state: ResponsibilityClaimState::Inferred,
            matched: true,

            reason: format!("LLM conversation capability: {}/{}", provider, model),
        },
        ResourceId::ToolCall {
            tool_name,
            call_id: _,
        } => {
            let is_orchestration = matches!(
                tool_name.as_str(),
                "TaskCreate" | "TaskUpdate" | "Agent" | "SendMessage" | "Skill" | "AskUserQuestion"
            );
            if is_orchestration {
                PolicyEvaluationResult {
                    policy_id: Some(PolicyId("policy.capability.tool".to_string())),
                    boundary_id: Some(BoundaryId("boundary.capability.tool".to_string())),
                    claim_state: ResponsibilityClaimState::Observed,
                    matched: true,
                    reason: format!("orchestration tool {} may indicate delegation", tool_name),

                }
            } else {
                PolicyEvaluationResult {
                    policy_id: Some(PolicyId("policy.capability.tool".to_string())),
                    boundary_id: Some(BoundaryId("boundary.capability.tool".to_string())),
                    claim_state: ResponsibilityClaimState::Inferred,
                    matched: true,
                    reason: format!("tool use within capability bounds: {}", tool_name),

                }
            }
        }
    }
}

/// Evaluate a context trust boundary: should a shared artifact handoff be trusted?
///
/// Wraps the context pollution detection logic but returns a PolicyEvaluationResult
/// following the 4-boundary contract instead of an Option<ContextPollutionResult>.
pub fn evaluate_context_trust_boundary(
    writer_agent_id: &str,
    reader_agent_id: &str,
    shared_path: &str,
    write_ts_ns: u64,
    read_ts_ns: u64,
) -> PolicyEvaluationResult {
    if writer_agent_id == reader_agent_id {
        return PolicyEvaluationResult {
            policy_id: Some(PolicyId("policy.context.trust".to_string())),
            boundary_id: Some(BoundaryId("boundary.context_trust".to_string())),
            claim_state: ResponsibilityClaimState::Unproven,
            matched: false,
            reason: "same-agent access: no cross-agent trust boundary".to_string(),

        };
    }

    if shared_path.is_empty() || shared_path == "/dev/null" {
        return PolicyEvaluationResult {
            policy_id: Some(PolicyId("policy.context.trust".to_string())),
            boundary_id: Some(BoundaryId("boundary.context_trust".to_string())),
            claim_state: ResponsibilityClaimState::Unproven,
            matched: false,
            reason: "trivial path: no meaningful shared artifact".to_string(),

        };
    }

    if read_ts_ns < write_ts_ns {
        return PolicyEvaluationResult {
            policy_id: Some(PolicyId("policy.context.trust".to_string())),
            boundary_id: Some(BoundaryId("boundary.context_trust".to_string())),
            claim_state: ResponsibilityClaimState::Unproven,
            matched: false,
            reason: "read before write: temporal ordering violated".to_string(),

        };
    }

    let dt_ns = read_ts_ns - write_ts_ns;
    if dt_ns > CONTEXT_PROPAGATION_WINDOW_NS {
        return PolicyEvaluationResult {
            policy_id: Some(PolicyId("policy.context.trust".to_string())),
            boundary_id: Some(BoundaryId("boundary.context_trust".to_string())),
            claim_state: ResponsibilityClaimState::Unproven,
            matched: false,
            reason: format!(
                "propagation window exceeded: dt={}ns > threshold={}ns",
                dt_ns, CONTEXT_PROPAGATION_WINDOW_NS
            ),

        };
    }

    let claim_state = if dt_ns <= 5_000_000_000 {
        ResponsibilityClaimState::Observed
    } else {
        ResponsibilityClaimState::Inferred
    };

    PolicyEvaluationResult {
        policy_id: Some(PolicyId("policy.context.trust".to_string())),
        boundary_id: Some(BoundaryId("boundary.context_trust".to_string())),
        claim_state,
        matched: true,
        reason: format!(
            "cross-agent artifact propagation: {} -> {} via {} (dt={}ns)",
            writer_agent_id, reader_agent_id, shared_path, dt_ns
        ),

    }
}

/// Evaluate a taint boundary: is a known-contaminated context driving further actions?
///
/// Checks whether a resource path appears in the set of known tainted paths
/// (e.g., handoff files that were injected with malicious content).
pub fn evaluate_taint_boundary(
    resource: &ResourceId,
    known_tainted_paths: &HashSet<String>,
) -> PolicyEvaluationResult {
    let path = match resource {
        ResourceId::File { canonical_path } => Some(canonical_path.as_str()),
        _ => None,
    };

    match path {
        Some(p) if known_tainted_paths.contains(p) => PolicyEvaluationResult {
            policy_id: Some(PolicyId("policy.taint".to_string())),
            boundary_id: Some(BoundaryId("boundary.taint".to_string())),
            claim_state: ResponsibilityClaimState::Observed,
            matched: true,
            reason: format!(
                "taint boundary violated: known-contaminated path {} was accessed",
                p
            ),

        },
        Some(p) => PolicyEvaluationResult {
            policy_id: Some(PolicyId("policy.taint".to_string())),
            boundary_id: Some(BoundaryId("boundary.taint".to_string())),
            claim_state: ResponsibilityClaimState::Unproven,
            matched: false,
            reason: format!("path {} is not in the known-tainted set", p),

        },
        None => PolicyEvaluationResult {
            policy_id: Some(PolicyId("policy.taint".to_string())),
            boundary_id: Some(BoundaryId("boundary.taint".to_string())),
            claim_state: ResponsibilityClaimState::Unproven,
            matched: false,
            reason: "non-file resource: taint boundary not applicable".to_string(),

        },
    }
}

// ── ResourceId-based boundary dispatch ──────────────────────────────────

pub fn evaluate_accountability_policy(
    agent_id: &str,
    resource: &ResourceId,
    delegation_records: &[DelegationRecord],
    known_tainted_paths: &HashSet<String>,
) -> PolicyEvaluationResult {
    evaluate_boundary_policy_with_resource(
        agent_id,
        resource,
        delegation_records,
        known_tainted_paths,
    )

}

/// Evaluate all applicable boundaries for a resource access.
///
/// This is the primary entry point for policy evaluation in Phase 1.
/// It dispatches on the ResourceId variant and evaluates all relevant
/// boundaries, returning the strongest match found.
pub fn evaluate_boundary_policy_with_resource(
    agent_id: &str,
    resource: &ResourceId,
    delegation_records: &[DelegationRecord],
    known_tainted_paths: &HashSet<String>,
) -> PolicyEvaluationResult {
    // Always check capability boundary (applies to all resource types)
    let capability = evaluate_capability_boundary(agent_id, resource);

    // Check taint boundary for file resources
    if let ResourceId::File { .. } = resource {
        let taint = evaluate_taint_boundary(resource, known_tainted_paths);
        if taint.matched && taint.claim_state == ResponsibilityClaimState::Observed {
            return taint; // Taint violation is the strongest signal
        }
    }

    // Check delegation boundary for orchestration tool calls
    if let ResourceId::ToolCall { tool_name, .. } = resource {
        if matches!(tool_name.as_str(), "TaskCreate" | "Agent" | "SendMessage") {
            // If we have delegation records, check them
            if !delegation_records.is_empty() {
                for record in delegation_records {
                    if record.from_agent_id == agent_id {
                        let delegation = evaluate_delegation_boundary(
                            &record.from_agent_id,
                            &record.to_agent_id,
                            delegation_records,
                        );
                        if delegation.matched {
                            return delegation;
                        }
                    }
                }
            }
        }
    }

    capability

}

// ── Legacy compatibility wrapper ────────────────────────────────────────

/// Policy evaluation by candidate_type string, upgraded to construct a
/// ResourceId and delegate to the full boundary-policy engine when possible.
///
/// Callers that pass `target` (file path, host:port, tool name) automatically
/// benefit from the richer 4-boundary evaluation without changing their call
/// sites. Unknown candidate types are forwarded as ResourceId::ToolCall so
/// orchestration-tool detection still fires.
pub fn evaluate_boundary_policy(
    agent_id: &str,
    _link_id: &str,
    candidate_type: Option<&str>,
    derivation_mode: Option<&str>,
    target: Option<&str>,
) -> PolicyEvaluationResult {
    // Attempt to construct a ResourceId for full boundary-policy evaluation.
    if let Some(resource) = candidate_type_to_resource(candidate_type, target) {
        let mut base = evaluate_boundary_policy_with_resource(
            agent_id,
            &resource,
            &[],             // legacy path has no delegation records
            &HashSet::new(), // legacy path has no known-tainted paths
        );

        // Refine claim state from derivation_mode so the caller's
        // direct/aggregated/multi_emit semantics survive the dispatch.
        if base.matched && derivation_mode == Some("direct") {
            base.claim_state = ResponsibilityClaimState::Observed;
        } else if base.matched && derivation_mode.is_some() {
            // Non-direct derivation is at best inferred.
            if base.claim_state == ResponsibilityClaimState::Observed {
                base.claim_state = ResponsibilityClaimState::Inferred;
            }
        }

        return base;
    }

    // No ResourceId could be constructed: fail-closed.
    PolicyEvaluationResult {
        policy_id: None,
        boundary_id: None,
        claim_state: ResponsibilityClaimState::Unproven,
        matched: false,
        reason: format!(
            "no matching phase1b policy for candidate_type={:?} target={:?}",
            candidate_type, target
        ),

    }
}

/// Construct a ResourceId from legacy candidate_type + target strings.
///
/// File paths become `ResourceId::File`, host:port strings become
/// `ResourceId::Network`, process operations become `ResourceId::Process`,
/// and unrecognised types are assumed to be tool calls.
fn candidate_type_to_resource(
    candidate_type: Option<&str>,
    target: Option<&str>,
) -> Option<ResourceId> {
    match candidate_type {
        Some("file_read") | Some("file_write") | Some("file_overwrite") => Some(ResourceId::File {
            canonical_path: target.unwrap_or("(unknown path)").to_string(),
        }),
        Some("network_connect") => {
            let target_str = target.unwrap_or("unknown:0");
            let (host, port) = parse_host_port(target_str);
            Some(ResourceId::Network {
                host: host.to_string(),
                port,
                scheme: None,
            })
        }
        Some("process_exec") | Some("process_spawn") => Some(ResourceId::Process {
            process_id: ProcessId {
                pid: 0,
                start_time_ns: 0,
            },
        }),
        // Unknown candidate types -- treat as tool calls so
        // orchestration-tool detection in evaluate_capability_boundary
        // still fires for e.g. "Bash", "TaskCreate", "Agent".
        Some(other) if !other.is_empty() => Some(ResourceId::ToolCall {
            tool_name: other.to_string(),
            call_id: None,
        }),
        _ => None,
    }
}

/// Parse "host:port" or "host" into (&str, u16).
fn parse_host_port(addr: &str) -> (&str, u16) {
    if let Some(idx) = addr.rfind(':') {
        let host = &addr[..idx];
        if let Ok(port) = addr[idx + 1..].parse::<u16>() {
            return (host, port);
        }
    }
    (addr, 0)
}

// ── Helpers ─────────────────────────────────────────────────────────────

fn is_sensitive_path(path: &str) -> bool {
    /// Whitelist: known-safe paths that match sensitive patterns but are
    /// not security boundaries. These are internal files that agents must
    /// access during normal operation.
    fn is_safe_internal(path: &str) -> bool {
        // Git object store — every git operation reads these
        path.contains("/.git/objects/")
        // Claude Code internal cache (MCP logs, telemetry, sessions)
        || path.contains("/.cache/claude-cli-nodejs/")
        || path.contains("/telemetry/")
        // Claude Code internal auth file — every Claude process reads this
        || path.ends_with(".credentials.json")
        // Current user's own SSH keys — normal for git/ssh operations.
        // Other users' .ssh/ directories remain sensitive.
        || (path.contains("/.ssh/") && std::env::var("HOME")
            .map(|home| path.starts_with(&home))
            .unwrap_or(false))
        // Current user's own .env files — normal project configuration.
        // .env files outside HOME (e.g. /root/.env) remain sensitive.
        || (path.ends_with("/.env") && std::env::var("HOME")
            .map(|home| path.starts_with(&home))
            .unwrap_or(false))
        // Standard /proc process status files — every process reads these
        || (path.starts_with("/proc/") && {
            let rest = &path[6..]; // after "/proc/"
            rest == "stat" || rest == "statm" || rest == "status"
            || rest == "maps" || rest == "cgroup" || rest == "mountinfo"
            || rest == "io" || rest == "limits"
            || rest.ends_with("/stat") || rest.ends_with("/statm")
            || rest.ends_with("/status") || rest.ends_with("/maps")
            || rest.ends_with("/cgroup") || rest.ends_with("/mountinfo")
            || rest.ends_with("/loginuid")
        })
    }
    if is_safe_internal(path) {
        return false;
    }

    const SENSITIVE: &[&str] = &[
        // Credential files (8)
        "/etc/shadow",
        "/etc/sudoers",
        "/etc/passwd",
        "/.ssh/",
        "/root/",
        // System security (3)
        "/proc/",
        "/sys/kernel/",
        "/var/log/auth.log",
        "/var/log/syslog",
        // Environment secrets (2)
        "/.env",
        "/.git-credentials",
        // SSH config (2)
        "/authorized_keys",
        "/id_rsa",
        // Credential patterns (1)
        "credentials",
        // Exfiltration configuration (2)
        "endpoint",
        "exfil",
    ];
    SENSITIVE.iter().any(|s| path.contains(s))
}

fn is_suspicious_target(host: &str, port: u16) -> bool {
    // Non-standard high-risk ports
    if port == 4444 || port == 31337 || port == 1337 || port == 6666 || port == 8080 {
        return true;
    }
    let h = host.to_lowercase();
    // Known exfiltration hosts
    h.contains("malicious")
        || h.contains("paste")
        || h.contains("pastebin")
        || h.contains("hastebin")
        || h.contains("ngrok")
        // Data exfiltration / shadow collection
        || h.contains("exfil")
        || h.contains("audit-collector")
        || h.contains("external.example")
        || h.contains("c2.")
        || h.contains("beacon")
}

// ── Context Pollution Result (for persistence) ──────────────────────────

/// Context pollution result — emitted when a cross-agent shared-artifact
/// propagation is detected within the propagation window.
#[derive(Debug, Clone)]
pub struct ContextPollutionResult {
    pub matched: bool,
    pub policy_id: PolicyId,
    pub boundary_id: BoundaryId,
    pub claim_state: ResponsibilityClaimState,
    pub writer_agent_id: String,
    pub reader_agent_id: String,
    pub shared_path: String,
    pub write_ts_ns: u64,
    pub read_ts_ns: u64,
    pub dt_ns: u64,
    pub evidence: Vec<EvidenceRef>,
    pub reason: String,
}

/// Evaluate whether a cross-agent file write->read sequence constitutes
/// context pollution (handoff injection, shared artifact propagation).
///
/// Returns `Some(ContextPollutionResult)` if:
/// - The writer and reader are different agents
/// - The read occurs within `CONTEXT_PROPAGATION_WINDOW_NS` after the write
/// - The shared path is non-trivial (not `/dev/null`, not empty)
pub fn evaluate_context_pollution(
    writer_agent_id: &str,
    reader_agent_id: &str,
    shared_path: &str,
    write_ts_ns: u64,
    read_ts_ns: u64,
) -> Option<ContextPollutionResult> {
    if writer_agent_id == reader_agent_id {
        return None;
    }

    if shared_path.is_empty() || shared_path == "/dev/null" {
        return None;
    }

    if read_ts_ns < write_ts_ns {
        return None;
    }
    let dt_ns = read_ts_ns - write_ts_ns;
    if dt_ns > CONTEXT_PROPAGATION_WINDOW_NS {
        return None;
    }

    let claim_state = if dt_ns <= 5_000_000_000 {
        ResponsibilityClaimState::Observed
    } else {
        ResponsibilityClaimState::Inferred
    };

    let evidence = vec![EvidenceRef {
        evidence_id: EvidenceId(format!(
            "evidence-ctx-pollution-{}-{}-{}",
            writer_agent_id,
            reader_agent_id,
            shared_path.replace('/', "-")
        )),
    }];

    Some(ContextPollutionResult {
        matched: true,
        policy_id: PolicyId("policy.context.pollution".to_string()),
        boundary_id: BoundaryId("boundary.cross_agent_artifact".to_string()),
        claim_state,
        writer_agent_id: writer_agent_id.to_string(),
        reader_agent_id: reader_agent_id.to_string(),
        shared_path: shared_path.to_string(),
        write_ts_ns,
        read_ts_ns,
        dt_ns,
        evidence,
        reason: format!(
            "cross-agent artifact propagation: {} -> {} via {} (dt={}ns)",
            writer_agent_id, reader_agent_id, shared_path, dt_ns
        ),
    })
}

// ── Tests ────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    // ── Delegation boundary tests ──────────────────────────────────

    #[test]
    fn test_delegation_boundary_found() {
        let records = vec![DelegationRecord {
            delegation_id: "deleg-1".to_string(),
            from_agent_id: "agent-a".to_string(),
            to_agent_id: "agent-b".to_string(),
            parent_pid: 100,
            child_pid: 200,
            timestamp_ns: 1000,
            child_comm: "claude".to_string(),
            confidence: 0.85,
        }];
        let result = evaluate_delegation_boundary("agent-a", "agent-b", &records);
        assert!(result.matched);
        assert_eq!(result.claim_state, ResponsibilityClaimState::Observed);
        assert_eq!(result.policy_id.unwrap().0, "policy.delegation");
    }

    #[test]
    fn test_delegation_boundary_not_found() {
        let records = vec![DelegationRecord {
            delegation_id: "deleg-1".to_string(),
            from_agent_id: "agent-a".to_string(),
            to_agent_id: "agent-b".to_string(),
            parent_pid: 100,
            child_pid: 200,
            timestamp_ns: 1000,
            child_comm: "claude".to_string(),
            confidence: 0.85,
        }];
        let result = evaluate_delegation_boundary("agent-c", "agent-d", &records);
        assert!(!result.matched);
        assert_eq!(result.claim_state, ResponsibilityClaimState::Unproven);
    }

    #[test]
    fn test_delegation_boundary_empty_records() {
        let result = evaluate_delegation_boundary("agent-a", "agent-b", &[]);
        assert!(!result.matched);
        assert_eq!(result.claim_state, ResponsibilityClaimState::Ambiguous);
        assert!(result.reason.contains("no delegation records"));
    }

    // ── Capability boundary tests ──────────────────────────────────

    #[test]
    fn test_capability_boundary_sensitive_file() {
        let resource = ResourceId::File {
            canonical_path: "/etc/shadow".to_string(),
        };
        let result = evaluate_capability_boundary("agent-1", &resource);
        assert!(result.matched);
        assert_eq!(result.claim_state, ResponsibilityClaimState::Observed);
        assert_eq!(result.boundary_id.unwrap().0, "boundary.file_read");
    }

    #[test]
    fn test_capability_boundary_normal_file() {
        let resource = ResourceId::File {
            canonical_path: "/tmp/output.txt".to_string(),
        };
        let result = evaluate_capability_boundary("agent-1", &resource);
        assert!(result.matched);
        assert_eq!(result.claim_state, ResponsibilityClaimState::Inferred);
    }

    #[test]
    fn test_capability_boundary_suspicious_network() {
        let resource = ResourceId::Network {
            host: "malicious.example.com".to_string(),
            port: 4444,
            scheme: None,
        };
        let result = evaluate_capability_boundary("agent-1", &resource);
        assert!(result.matched);
        assert_eq!(result.claim_state, ResponsibilityClaimState::Observed);
    }

    #[test]
    fn test_capability_boundary_orchestration_tool() {
        let resource = ResourceId::ToolCall {
            tool_name: "TaskCreate".to_string(),
            call_id: None,
        };
        let result = evaluate_capability_boundary("agent-1", &resource);
        assert!(result.matched);
        assert_eq!(result.claim_state, ResponsibilityClaimState::Observed);
    }

    // ── Context trust boundary tests ───────────────────────────────

    #[test]
    fn test_context_trust_boundary_cross_agent_within_window() {
        let result = evaluate_context_trust_boundary(
            "agent-1",
            "agent-2",
            "/tmp/shared/handoff.md",
            1000,
            2000,
        );
        assert!(result.matched);
        assert_eq!(result.claim_state, ResponsibilityClaimState::Observed);
        assert_eq!(result.boundary_id.unwrap().0, "boundary.context_trust");
    }

    #[test]
    fn test_context_trust_boundary_same_agent() {
        let result = evaluate_context_trust_boundary(
            "agent-1",
            "agent-1",
            "/tmp/shared/handoff.md",
            1000,
            2000,
        );
        assert!(!result.matched);
        assert_eq!(result.claim_state, ResponsibilityClaimState::Unproven);
        assert!(result.reason.contains("same-agent"));
    }

    #[test]
    fn test_context_trust_boundary_outside_window() {
        let result = evaluate_context_trust_boundary(
            "agent-1",
            "agent-2",
            "/tmp/shared/handoff.md",
            1000,
            1000 + CONTEXT_PROPAGATION_WINDOW_NS + 1,
        );
        assert!(!result.matched);
        assert!(result.reason.contains("propagation window exceeded"));
    }

    // ── Taint boundary tests ───────────────────────────────────────

    #[test]
    fn test_taint_boundary_known_tainted() {
        let mut tainted = HashSet::new();
        tainted.insert("/tmp/handoff_injection.md".to_string());
        let resource = ResourceId::File {
            canonical_path: "/tmp/handoff_injection.md".to_string(),
        };
        let result = evaluate_taint_boundary(&resource, &tainted);
        assert!(result.matched);
        assert_eq!(result.claim_state, ResponsibilityClaimState::Observed);
        assert_eq!(result.boundary_id.unwrap().0, "boundary.taint");
    }

    #[test]
    fn test_taint_boundary_clean_path() {
        let tainted = HashSet::new();
        let resource = ResourceId::File {
            canonical_path: "/tmp/clean.txt".to_string(),
        };
        let result = evaluate_taint_boundary(&resource, &tainted);
        assert!(!result.matched);
        assert_eq!(result.claim_state, ResponsibilityClaimState::Unproven);
    }

    #[test]
    fn test_taint_boundary_non_file_resource() {
        let tainted = HashSet::new();
        let resource = ResourceId::ToolCall {
            tool_name: "Bash".to_string(),
            call_id: None,
        };
        let result = evaluate_taint_boundary(&resource, &tainted);
        assert!(!result.matched);
        assert!(result.reason.contains("non-file"));
    }

    // ── Resource dispatch tests ────────────────────────────────────

    #[test]
    fn test_evaluate_boundary_with_resource_taint_priority() {
        let mut tainted = HashSet::new();
        tainted.insert("/etc/shadow".to_string());
        let resource = ResourceId::File {
            canonical_path: "/etc/shadow".to_string(),
        };
        let result = evaluate_boundary_policy_with_resource("agent-1", &resource, &[], &tainted);
        assert!(result.matched);
        assert_eq!(result.claim_state, ResponsibilityClaimState::Observed);
        // Taint should win over capability for sensitive files
        assert_eq!(result.boundary_id.unwrap().0, "boundary.taint");
    }

    #[test]
    fn test_evaluate_boundary_with_resource_falls_back_to_capability() {
        let resource = ResourceId::File {
            canonical_path: "/tmp/normal.txt".to_string(),
        };
        let result =
            evaluate_boundary_policy_with_resource("agent-1", &resource, &[], &HashSet::new());
        assert!(result.matched);
        assert_eq!(result.boundary_id.unwrap().0, "boundary.file_read");
    }

    #[test]
    fn evaluate_accountability_policy_uses_resource_context() {
        let resource = ResourceId::File {
            canonical_path: "/env/scenarios/fixtures/secret.txt".to_string(),
        };
        let mut tainted = HashSet::new();
        tainted.insert("/env/scenarios/fixtures/secret.txt".to_string());

        let result = evaluate_accountability_policy("agent-1", &resource, &[], &tainted);

        assert!(result.matched);
        assert_eq!(result.claim_state, ResponsibilityClaimState::Observed);
        assert!(result.reason.contains("taint"));
    }

    // ── Legacy compatibility tests ─────────────────────────────────

    #[test]
    fn test_legacy_evaluate_boundary_policy_file_read_direct() {
        let result = evaluate_boundary_policy(
            "agent-1",
            "link-1",
            Some("file_read"),
            Some("direct"),
            Some("/etc/shadow"),
        );
        assert!(result.matched);
        assert_eq!(result.claim_state, ResponsibilityClaimState::Observed);
        // ResourceId-based dispatch: file → file_read boundary
        assert_eq!(result.policy_id.unwrap().0, "policy.file.read");
    }

    #[test]
    fn test_legacy_evaluate_boundary_policy_file_read_no_target() {
        let result =
            evaluate_boundary_policy("agent-1", "link-1", Some("file_read"), Some("direct"), None);
        assert!(result.matched);
        // derivation_mode="direct" promotes to Observed even with unknown path
        assert_eq!(result.claim_state, ResponsibilityClaimState::Observed);
        assert_eq!(result.policy_id.unwrap().0, "policy.file.read");
    }

    #[test]
    fn test_legacy_evaluate_boundary_policy_file_write_inferred() {
        let result = evaluate_boundary_policy(
            "agent-1",
            "link-1",
            Some("file_write"),
            Some("aggregated"),
            Some("/tmp/output.txt"),
        );
        assert!(result.matched);
        assert_eq!(result.claim_state, ResponsibilityClaimState::Inferred);
    }

    #[test]
    fn test_legacy_evaluate_boundary_policy_network_with_target() {
        let result = evaluate_boundary_policy(
            "agent-1",
            "link-1",
            Some("network_connect"),
            Some("direct"),
            Some("malicious.example.com:4444"),
        );
        assert!(result.matched);
        assert_eq!(result.claim_state, ResponsibilityClaimState::Observed);
        assert_eq!(result.policy_id.unwrap().0, "policy.network.connect");
    }

    #[test]
    fn test_legacy_evaluate_boundary_policy_tool_call_orchestration() {
        let result = evaluate_boundary_policy(
            "agent-1",
            "link-1",
            Some("TaskCreate"),
            Some("direct"),
            None,
        );
        assert!(result.matched);
        assert_eq!(result.claim_state, ResponsibilityClaimState::Observed);
        assert_eq!(result.policy_id.unwrap().0, "policy.capability.tool");
    }

    #[test]
    fn test_legacy_evaluate_boundary_policy_unknown_type_becomes_tool_call() {
        let result =
            evaluate_boundary_policy("agent-1", "link-1", Some("unknown_type"), None, None);
        // Unknown types are now treated as ToolCall ResourceId
        assert!(result.matched);
        assert_eq!(result.claim_state, ResponsibilityClaimState::Inferred);
    }

    #[test]
    fn test_legacy_evaluate_boundary_policy_none_candidate_type() {
        let result = evaluate_boundary_policy("agent-1", "link-1", None, None, None);
        assert!(!result.matched);
        assert_eq!(result.claim_state, ResponsibilityClaimState::Unproven);
    }

    // ── Context pollution tests ────────────────────────────────────

    #[test]
    fn test_evaluate_context_pollution_cross_agent() {
        let result =
            evaluate_context_pollution("agent-1", "agent-2", "/tmp/shared/handoff.md", 1000, 2000);
        assert!(result.is_some());
        let r = result.unwrap();
        assert!(r.matched);
        assert_eq!(r.claim_state, ResponsibilityClaimState::Observed);
        assert_eq!(r.shared_path, "/tmp/shared/handoff.md");
    }

    #[test]
    fn test_evaluate_context_pollution_same_agent_returns_none() {
        let result =
            evaluate_context_pollution("agent-1", "agent-1", "/tmp/shared/handoff.md", 1000, 2000);
        assert!(result.is_none());
    }

    #[test]
    fn test_evaluate_context_pollution_outside_window_returns_none() {
        let result = evaluate_context_pollution(
            "agent-1",
            "agent-2",
            "/tmp/shared/handoff.md",
            1000,
            1000 + CONTEXT_PROPAGATION_WINDOW_NS + 1,
        );
        assert!(result.is_none());
    }

    #[test]
    fn test_evaluate_context_pollution_empty_path_returns_none() {
        let result = evaluate_context_pollution("agent-1", "agent-2", "", 1000, 2000);
        assert!(result.is_none());
    }

    #[test]
    fn test_evaluate_context_pollution_within_tight_window_is_observed() {
        let result =
            evaluate_context_pollution("agent-1", "agent-2", "/tmp/shared/handoff.md", 1000, 2000);
        assert!(result.is_some());
        assert_eq!(
            result.unwrap().claim_state,
            ResponsibilityClaimState::Observed
        );
    }
}
