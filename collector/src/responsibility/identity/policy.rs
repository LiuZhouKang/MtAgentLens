// SPDX-License-Identifier: Apache-2.0 OR MIT
//
// identity/policy.rs — frozen, typed agent-comm boundary policy.

use crate::responsibility::signals::signal_snapshot::ProcessMetadata;
use serde::{Deserialize, Serialize};

/// Version tag for the serialized boundary policy.
pub const IDENTITY_BOUNDARY_POLICY_SCHEMA: &str = "mtagentlens.identity-boundary-policy/v1";

/// The exact `comm` vocabulary used by an authoritative resolution.
///
/// This is a value, not a getter: replay must consume the policy captured
/// before resolution, and must not read the current environment.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct IdentityBoundaryPolicy {
    pub schema: String,
    /// Sorted, deduplicated process names (built-in vocabulary plus override).
    pub agent_comms: Vec<String>,
}

impl IdentityBoundaryPolicy {
    /// Build a deterministic policy from a comm vocabulary.
    pub fn new<I, S>(agent_comms: I) -> Self
    where
        I: IntoIterator<Item = S>,
        S: Into<String>,
    {
        let mut agent_comms: Vec<String> = agent_comms.into_iter().map(Into::into).collect();
        agent_comms.sort();
        agent_comms.dedup();
        Self {
            schema: IDENTITY_BOUNDARY_POLICY_SCHEMA.to_string(),
            agent_comms,
        }
    }

    /// Capture the policy currently configured for this process.
    ///
    /// The built-in vocabulary always remains valid; an
    /// `MTAGENTLENS_AGENT_COMMS` override extends it exactly as
    /// `is_agent_comm` does.
    pub fn active() -> Self {
        Self::new(super::union_find::configured_agent_comms())
    }

    /// Pure boundary check for one process name.
    pub fn matches_comm(&self, comm: &str) -> bool {
        self.agent_comms
            .binary_search_by(|candidate| candidate.as_str().cmp(comm))
            .is_ok()
    }

    /// Pure boundary check for a captured process. This is the only check the
    /// offline resolver is allowed to use.
    pub fn matches_process(&self, process: &ProcessMetadata) -> bool {
        self.matches_comm(&process.comm)
    }

    /// Reject a policy whose schema or ordering is not the frozen contract.
    pub fn validate(&self) -> Result<(), String> {
        if self.schema != IDENTITY_BOUNDARY_POLICY_SCHEMA {
            return Err(format!(
                "unsupported identity boundary policy schema {:?}",
                self.schema
            ));
        }
        if self
            .agent_comms
            .windows(2)
            .any(|pair| pair[0].as_str() >= pair[1].as_str())
        {
            return Err("identity boundary policy comms must be sorted and unique".into());
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::responsibility::signals::signal_snapshot::ProcessMetadata;
    use crate::responsibility::types::ProcessId;

    fn process(comm: &str) -> ProcessMetadata {
        ProcessMetadata {
            process_id: ProcessId::new(10, 1),
            comm: comm.into(),
            pgid: None,
            first_seen_ns: 1,
            last_seen_ns: 2,
            exit_time_ns: None,
            exit_code: None,
            is_alive: true,
            cmdline: None,
            exe_path: None,
            cgroup_unit: None,
        }
    }

    #[test]
    fn policy_is_sorted_versioned_and_pure() {
        let policy = IdentityBoundaryPolicy::new(["z-agent", "claude", "a-agent", "claude"]);
        assert_eq!(policy.schema, IDENTITY_BOUNDARY_POLICY_SCHEMA);
        assert_eq!(
            policy.agent_comms,
            vec!["a-agent".to_string(), "claude".into(), "z-agent".into()]
        );
        assert!(policy.validate().is_ok());
        assert!(policy.matches_process(&process("claude")));
        assert!(!policy.matches_process(&process("bash")));

        let json = serde_json::to_string(&policy).unwrap();
        let decoded: IdentityBoundaryPolicy = serde_json::from_str(&json).unwrap();
        assert_eq!(decoded, policy);
        assert_eq!(serde_json::to_string(&decoded).unwrap(), json);
    }

    #[test]
    fn active_policy_is_a_sorted_union_with_the_builtin_boundary() {
        let active = IdentityBoundaryPolicy::active();
        assert_eq!(active.schema, IDENTITY_BOUNDARY_POLICY_SCHEMA);
        assert!(active.matches_comm("claude"));
        assert!(active.matches_comm("claude-code"));
        let mut sorted = active.agent_comms.clone();
        sorted.sort();
        sorted.dedup();
        assert_eq!(active.agent_comms, sorted);
    }

    #[test]
    fn unknown_policy_schema_is_rejected() {
        let mut policy = IdentityBoundaryPolicy::new(["claude"]);
        policy.schema = "mtagentlens.identity-boundary-policy/v999".into();
        assert!(policy.validate().is_err());
    }
}
