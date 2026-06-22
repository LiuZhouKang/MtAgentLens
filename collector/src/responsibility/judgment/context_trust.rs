// SPDX-License-Identifier: MIT
// Judgment boundary 3: Context trust evaluation.
//
// Evaluates whether a shared artifact (e.g., file) handoff between agents
// is trusted. A write-then-read sequence across agent boundaries can indicate
// context pollution (e.g., handoff injection) if the agents are not
// authorized to share context.

use crate::responsibility::types::{
    AgentId, BoundaryId, EvidenceId, PolicyBoundary, PolicyId, PolicyJudgment,
    PolicyJudgmentResult, SubagentBoundary,
};

/// Context trust evaluation windows (in nanoseconds).
const VIOLATION_WINDOW_NS: u64 = 60_000_000_000; // 60 seconds
const AMBIGUOUS_WINDOW_NS: u64 = 300_000_000_000; // 300 seconds (5 minutes)

/// Evaluates cross-agent artifact (file) handoff for context trust.
///
/// Returns a `PolicyJudgment` based on:
/// - Same agent read/write → Allowed
/// - Authorized subagent boundary between writer and reader → Allowed
/// - Cross-agent without authorization:
///   - dt < 60s  → Violation (immediate propagation)
///   - dt < 300s → Ambiguous (possible delayed propagation)
///   - dt >= 300s → Unproven (too old to determine)
pub struct ContextTrustEvaluator;

impl ContextTrustEvaluator {
    /// Evaluate whether a cross-agent file write→read sequence is trusted.
    ///
    /// # Arguments
    /// * `reader` - The agent that read the file.
    /// * `writer` - The agent that wrote the file.
    /// * `path` - The canonical path of the shared artifact.
    /// * `write_ts` - Nanosecond timestamp when the file was written.
    /// * `read_ts` - Nanosecond timestamp when the file was read.
    /// * `subagent_boundaries` - Known authorized subagent boundaries.
    pub fn evaluate_artifact(
        reader: &AgentId,
        writer: &AgentId,
        path: &str,
        write_ts: u64,
        read_ts: u64,
        subagent_boundaries: &[SubagentBoundary],
    ) -> PolicyJudgment {
        // Same agent: self-read after self-write is always allowed.
        if reader == writer {
            return PolicyJudgment {
                judgment_id: uuid::Uuid::new_v4().to_string(),
                policy_id: PolicyId::from("policy.context.trust"),
                boundary_id: BoundaryId::from("boundary.context_trust"),
                subject: reader.clone(),
                graph_ref: String::new(),
                evidence_refs: vec![],
                result: PolicyJudgmentResult::Allowed,
                reason: format!(
                    "same-agent access: {} wrote and read {} — no trust boundary crossed",
                    reader.as_str(),
                    path
                ),
                confidence: 1.0,
            };
        }

        // Trivial path: /dev/null or empty paths are not meaningful artifacts.
        if path.is_empty() || path == "/dev/null" {
            return PolicyJudgment {
                judgment_id: uuid::Uuid::new_v4().to_string(),
                policy_id: PolicyId::from("policy.context.trust"),
                boundary_id: BoundaryId::from("boundary.context_trust"),
                subject: reader.clone(),
                graph_ref: String::new(),
                evidence_refs: vec![],
                result: PolicyJudgmentResult::Allowed,
                reason: format!(
                    "trivial artifact path '{}' — no meaningful context propagation",
                    path
                ),
                confidence: 1.0,
            };
        }

        // Authorized subagent boundary: parent-child or child-parent relationship.
        let is_authorized = subagent_boundaries.iter().any(|b| {
            (b.parent_agent == *writer && b.child_agent == *reader)
                || (b.parent_agent == *reader && b.child_agent == *writer)
        });
        if is_authorized {
            return PolicyJudgment {
                judgment_id: uuid::Uuid::new_v4().to_string(),
                policy_id: PolicyId::from("policy.context.trust"),
                boundary_id: BoundaryId::from("boundary.context_trust"),
                subject: reader.clone(),
                graph_ref: String::new(),
                evidence_refs: vec![],
                result: PolicyJudgmentResult::Allowed,
                reason: format!(
                    "authorized subagent boundary: {} ↔ {} via {}",
                    writer.as_str(),
                    reader.as_str(),
                    path
                ),
                confidence: 0.90,
            };
        }

        // Temporal ordering check: read before write is impossible.
        if read_ts < write_ts {
            return PolicyJudgment {
                judgment_id: uuid::Uuid::new_v4().to_string(),
                policy_id: PolicyId::from("policy.context.trust"),
                boundary_id: BoundaryId::from("boundary.context_trust"),
                subject: reader.clone(),
                graph_ref: String::new(),
                evidence_refs: vec![],
                result: PolicyJudgmentResult::Unproven,
                reason: format!(
                    "read before write (read_ts={}, write_ts={}) — temporal ordering violated",
                    read_ts, write_ts
                ),
                confidence: 0.0,
            };
        }

        // Cross-agent, no authorization → evaluate time window.
        let dt = read_ts.saturating_sub(write_ts);

        if dt < VIOLATION_WINDOW_NS {
            // < 60s: immediate propagation → Violation
            PolicyJudgment {
                judgment_id: uuid::Uuid::new_v4().to_string(),
                policy_id: PolicyId::from("policy.context.trust"),
                boundary_id: BoundaryId::from("boundary.context_trust"),
                subject: reader.clone(),
                graph_ref: String::new(),
                evidence_refs: vec![],
                result: PolicyJudgmentResult::Violation,
                reason: format!(
                    "cross-agent artifact propagation within {}ms: {} → {} via {} (dt={}ns)",
                    VIOLATION_WINDOW_NS / 1_000_000,
                    writer.as_str(),
                    reader.as_str(),
                    path,
                    dt
                ),
                confidence: 0.85,
            }
        } else if dt < AMBIGUOUS_WINDOW_NS {
            // 60s - 300s: possible delayed propagation → Ambiguous
            PolicyJudgment {
                judgment_id: uuid::Uuid::new_v4().to_string(),
                policy_id: PolicyId::from("policy.context.trust"),
                boundary_id: BoundaryId::from("boundary.context_trust"),
                subject: reader.clone(),
                graph_ref: String::new(),
                evidence_refs: vec![],
                result: PolicyJudgmentResult::Ambiguous,
                reason: format!(
                    "cross-agent artifact propagation within {}s: {} → {} via {} (dt={}ns)",
                    AMBIGUOUS_WINDOW_NS / 1_000_000_000,
                    writer.as_str(),
                    reader.as_str(),
                    path,
                    dt
                ),
                confidence: 0.50,
            }
        } else {
            // > 300s: too old to determine → Unproven
            PolicyJudgment {
                judgment_id: uuid::Uuid::new_v4().to_string(),
                policy_id: PolicyId::from("policy.context.trust"),
                boundary_id: BoundaryId::from("boundary.context_trust"),
                subject: reader.clone(),
                graph_ref: String::new(),
                evidence_refs: vec![],
                result: PolicyJudgmentResult::Unproven,
                reason: format!(
                    "cross-agent artifact propagation outside window (>{}s): {} → {} via {} (dt={}ns)",
                    AMBIGUOUS_WINDOW_NS / 1_000_000_000,
                    writer.as_str(),
                    reader.as_str(),
                    path,
                    dt
                ),
                confidence: 0.10,
            }
        }
    }
}

// ── Tests ──────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use crate::responsibility::types::{ProcessId, SubagentBoundaryReason};

    fn make_agent(id: &str) -> AgentId {
        AgentId::from(id)
    }

    fn make_boundary(parent: &str, child: &str) -> SubagentBoundary {
        SubagentBoundary {
            parent_agent: make_agent(parent),
            child_agent: make_agent(child),
            parent_process: ProcessId::new(100, 1),
            child_process: ProcessId::new(200, 1),
            reason: SubagentBoundaryReason::ClaudeChildOfClaude,
        }
    }

    #[test]
    fn same_agent_allowed() {
        let a = make_agent("agent-1");
        let result = ContextTrustEvaluator::evaluate_artifact(
            &a, &a, "/tmp/data.txt", 1000, 2000, &[],
        );
        assert_eq!(result.result, PolicyJudgmentResult::Allowed);
        assert!(result.reason.contains("same-agent"));
    }

    #[test]
    fn authorized_subagent_boundary_allowed() {
        let parent = make_agent("parent-agent");
        let child = make_agent("child-agent");
        let boundaries = vec![make_boundary("parent-agent", "child-agent")];

        // Parent wrote, child read — authorized.
        let result = ContextTrustEvaluator::evaluate_artifact(
            &child, &parent, "/tmp/handoff.md", 1000, 2000, &boundaries,
        );
        assert_eq!(result.result, PolicyJudgmentResult::Allowed);
        assert!(result.reason.contains("authorized subagent boundary"));
    }

    #[test]
    fn authorized_reverse_direction_allowed() {
        let parent = make_agent("parent-agent");
        let child = make_agent("child-agent");
        let boundaries = vec![make_boundary("parent-agent", "child-agent")];

        // Child wrote, parent read — also authorized (symmetric check).
        let result = ContextTrustEvaluator::evaluate_artifact(
            &parent, &child, "/tmp/result.txt", 1000, 2000, &boundaries,
        );
        assert_eq!(result.result, PolicyJudgmentResult::Allowed);
    }

    #[test]
    fn cross_agent_within_60s_violation() {
        let a = make_agent("agent-a");
        let b = make_agent("agent-b");
        let result = ContextTrustEvaluator::evaluate_artifact(
            &b, &a, "/tmp/shared.txt", 1000, 1000 + 30_000_000_000, &[],
        );
        assert_eq!(result.result, PolicyJudgmentResult::Violation);
        assert!(result.reason.contains("cross-agent"));
    }

    #[test]
    fn cross_agent_60s_to_300s_ambiguous() {
        let a = make_agent("agent-a");
        let b = make_agent("agent-b");
        let result = ContextTrustEvaluator::evaluate_artifact(
            &b, &a, "/tmp/shared.txt", 1000, 1000 + 65_000_000_000, &[],
        );
        assert_eq!(result.result, PolicyJudgmentResult::Ambiguous);
    }

    #[test]
    fn cross_agent_beyond_300s_unproven() {
        let a = make_agent("agent-a");
        let b = make_agent("agent-b");
        let result = ContextTrustEvaluator::evaluate_artifact(
            &b, &a, "/tmp/shared.txt", 1000, 1000 + 350_000_000_000, &[],
        );
        assert_eq!(result.result, PolicyJudgmentResult::Unproven);
        assert!(result.reason.contains("outside window"));
    }

    #[test]
    fn trivial_path_allowed() {
        let a = make_agent("agent-a");
        let b = make_agent("agent-b");
        let result = ContextTrustEvaluator::evaluate_artifact(
            &b, &a, "/dev/null", 1000, 2000, &[],
        );
        assert_eq!(result.result, PolicyJudgmentResult::Allowed);
        assert!(result.reason.contains("trivial artifact"));
    }

    #[test]
    fn empty_path_allowed() {
        let a = make_agent("agent-a");
        let b = make_agent("agent-b");
        let result = ContextTrustEvaluator::evaluate_artifact(
            &b, &a, "", 1000, 2000, &[],
        );
        assert_eq!(result.result, PolicyJudgmentResult::Allowed);
    }

    #[test]
    fn read_before_write_unproven() {
        let a = make_agent("agent-a");
        let b = make_agent("agent-b");
        // read_ts < write_ts is impossible.
        let result = ContextTrustEvaluator::evaluate_artifact(
            &b, &a, "/tmp/data.txt", 2000, 1000, &[],
        );
        assert_eq!(result.result, PolicyJudgmentResult::Unproven);
        assert!(result.reason.contains("temporal ordering violated"));
    }

    #[test]
    fn boundary_not_matching_other_pair() {
        let parent = make_agent("parent");
        let child = make_agent("child");
        let other = make_agent("other-agent");
        let boundaries = vec![make_boundary("parent", "child")];

        // "other-agent" is not in any boundary — should fall through to violation.
        let result = ContextTrustEvaluator::evaluate_artifact(
            &other, &parent, "/tmp/data.txt", 1000, 2000, &boundaries,
        );
        // parent→other is NOT in boundaries, other→parent is NOT in boundaries.
        // So it's cross-agent without authorization, dt=1000ns < 60s → Violation.
        assert_eq!(result.result, PolicyJudgmentResult::Violation);
    }

    #[test]
    fn dt_exactly_at_violation_boundary() {
        let a = make_agent("agent-a");
        let b = make_agent("agent-b");
        // dt = 59_999_999_999 ns (just under 60s).
        let result = ContextTrustEvaluator::evaluate_artifact(
            &b, &a, "/tmp/data.txt", 1, 60_000_000_000, &[],
        );
        // 60_000_000_000 - 1 = 59_999_999_999 < 60_000_000_000 → Violation.
        assert_eq!(result.result, PolicyJudgmentResult::Violation);
    }

    #[test]
    fn dt_exactly_at_ambiguous_boundary() {
        let a = make_agent("agent-a");
        let b = make_agent("agent-b");
        // dt = 60_000_000_000 ns (exactly 60s).
        let result = ContextTrustEvaluator::evaluate_artifact(
            &b, &a, "/tmp/data.txt", 0, 60_000_000_000, &[],
        );
        // 60_000_000_000 is NOT < 60_000_000_000 → falls to Ambiguous check.
        // 60_000_000_000 < 300_000_000_000 → Ambiguous.
        assert_eq!(result.result, PolicyJudgmentResult::Ambiguous);
    }
}
