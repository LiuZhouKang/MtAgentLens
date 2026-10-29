// SPDX-License-Identifier: Apache-2.0 OR MIT
//
// authorization.rs — who is allowed to share context with whom.
//
// A taint observation is not by itself a responsibility claim. The product
// only claims a `TaintedContextUse` when the reader was *not* authorized to
// receive the writer's context. Today the authorization signal is the
// observed process-spawn delegation record. Direction matters: a parent
// delegating to a child grants the child the parent's context. It does not
// grant the child's new output the parent's trust — the child→parent flow is
// exactly the multi-agent injection channel, so it stays unauthorized.
//
// Deliberately narrow:
//   * direct relations only, no transitive closure — a grandchild reading a
//     grandparent's artifact stays unauthorized;
//   * no role/capability vocabulary is invented — `AgentId` carries no trust
//     domain, and the fail-open capability boundary is not authorization;
//   * unattributed endpoints are reported as such, never silently treated as
//     authorized or unauthorized.

use crate::responsibility::judgment::delegation::DelegationObservation;
use crate::responsibility::types::AgentId;

/// Result of checking whether a cross-agent context share is authorized.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ContextShareAuthorization {
    /// Same agent, or a direct observed delegation relates writer and reader.
    Authorized,
    /// No observed relation authorizes the share.
    Unauthorized,
    /// One or both endpoints carry no agent identity; no claim may be made.
    Unattributed,
}

/// Decide whether `writer` may share context with `reader`.
pub fn authorize_context_share(
    writer: Option<&AgentId>,
    reader: Option<&AgentId>,
    delegations: &[DelegationObservation],
) -> ContextShareAuthorization {
    let (Some(writer), Some(reader)) = (writer, reader) else {
        return ContextShareAuthorization::Unattributed;
    };

    if writer == reader {
        return ContextShareAuthorization::Authorized;
    }

    // Only writer -> reader: the writer delegated the work, so the reader may
    // consume the writer's context. The reverse relation does not authorize
    // the reader to trust data produced by the agent it delegated to.
    let direct = delegations.iter().any(|record| {
        record.from_agent_id == writer.as_str() && record.to_agent_id == reader.as_str()
    });

    if direct {
        ContextShareAuthorization::Authorized
    } else {
        ContextShareAuthorization::Unauthorized
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::responsibility::judgment::delegation::DelegationObservation;

    fn agent(id: &str) -> AgentId {
        AgentId::from(id)
    }

    fn delegation(from: &str, to: &str) -> DelegationObservation {
        DelegationObservation {
            delegation_id: format!("delegation:{from}->{to}"),
            from_agent_id: from.to_string(),
            to_agent_id: to.to_string(),
            parent_pid: 1,
            parent_start_time_ns: 1,
            child_pid: 2,
            child_start_time_ns: 2,
            timestamp_ns: 1_000,
            child_comm: "child".into(),
            confidence: 0.85,
        }
    }

    #[test]
    fn unrelated_agents_are_unauthorized() {
        let writer = agent("writer");
        let reader = agent("reader");
        assert_eq!(
            authorize_context_share(Some(&writer), Some(&reader), &[]),
            ContextShareAuthorization::Unauthorized
        );
        // A record that involves neither endpoint authorizes nothing.
        assert_eq!(
            authorize_context_share(
                Some(&writer),
                Some(&reader),
                &[delegation("other-a", "other-b")]
            ),
            ContextShareAuthorization::Unauthorized
        );
    }

    #[test]
    fn only_writer_to_reader_delegation_authorizes() {
        let writer = agent("writer");
        let reader = agent("reader");
        assert_eq!(
            authorize_context_share(
                Some(&writer),
                Some(&reader),
                &[delegation("writer", "reader")]
            ),
            ContextShareAuthorization::Authorized
        );
        // The child's output flowing back to the delegating parent is the
        // injection channel, not an authorized context inheritance.
        assert_eq!(
            authorize_context_share(
                Some(&writer),
                Some(&reader),
                &[delegation("reader", "writer")]
            ),
            ContextShareAuthorization::Unauthorized
        );
    }

    #[test]
    fn transitive_delegation_is_not_authorization() {
        let writer = agent("writer");
        let reader = agent("reader");
        let records = [
            delegation("writer", "middle"),
            delegation("middle", "reader"),
        ];
        assert_eq!(
            authorize_context_share(Some(&writer), Some(&reader), &records),
            ContextShareAuthorization::Unauthorized
        );
    }

    #[test]
    fn same_agent_is_authorized() {
        let writer = agent("same");
        let reader = agent("same");
        assert_eq!(
            authorize_context_share(Some(&writer), Some(&reader), &[]),
            ContextShareAuthorization::Authorized
        );
    }

    #[test]
    fn missing_endpoint_is_unattributed() {
        let reader = agent("reader");
        assert_eq!(
            authorize_context_share(None, Some(&reader), &[]),
            ContextShareAuthorization::Unattributed
        );
        assert_eq!(
            authorize_context_share(Some(&reader), None, &[]),
            ContextShareAuthorization::Unattributed
        );
    }
}
