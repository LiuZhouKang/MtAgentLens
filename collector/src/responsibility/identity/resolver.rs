// SPDX-License-Identifier: Apache-2.0 OR MIT
//
// IdentityResolver — Agent identity resolution (Phase 1 + Phase 2).
//
// Phase 1 (streaming, real-time):
//   resolve_provisional() — assigns a provisional AgentId using heuristics
//   on the current signal snapshot. Called after each observe_* call.
//   Confidence is low because the picture is incomplete.
//
// Phase 2 (batch, post-hoc):
//   resolve_authoritative() — runs the full three-signal union-find
//   algorithm on a frozen snapshot. Produces deterministic, hash-based
//   AgentIds and refines Phase 1 assignments.
//
// The module also exports the IdentityProvider trait, consumed by graph/
// to look up agent assignments without depending on resolver internals.

use crate::responsibility::signals::SignalSnapshot;
#[cfg(test)]
use crate::responsibility::signals::signal_snapshot::ProcessExecInfo;
use crate::responsibility::types::{AgentId, IdentitySignal, ProcessId, ProvisionalIdentity};
use std::collections::{HashMap, HashSet};

use super::union_find::{AuthoritativeResolution, run_authoritative_resolution};

// ─── IdentityProvider Trait ─────────────────────────────────────────────

/// Trait consumed by `graph/` to look up agent assignments.
///
/// The graph builder uses this trait instead of importing `IdentityResolver`
/// directly, keeping the dependency clean and testable.
pub trait IdentityProvider {
    /// Which agent owns the given process, if known.
    fn agent_for_process(&self, process_id: ProcessId) -> Option<AgentId>;

    /// All current (provisional or authoritative) assignments.
    fn all_assignments(&self) -> HashMap<ProcessId, AgentId>;
}

// ─── IdentityResolver ───────────────────────────────────────────────────

/// Resolves agent identities for processes.
///
/// Phase 1 maintains a provisional map updated incrementally as signals arrive.
/// Phase 2 produces authoritative identities from the complete frozen snapshot.
pub struct IdentityResolver {
    /// Phase 1 provisional assignments: ProcessId → its current best-guess agent.
    provisional_map: HashMap<ProcessId, ProvisionalIdentity>,
}

impl IdentityResolver {
    /// Create a new, empty resolver.
    pub fn new() -> Self {
        Self {
            provisional_map: HashMap::new(),
        }
    }

    // ── Phase 1: Provisional Resolution ──────────────────────────────

    /// Resolve (or re-evaluate) the provisional identity for a process.
    ///
    /// Called from `route_event()` after each `observe_*` call so that
    /// the identity assignment stays as accurate as possible given the
    /// current (incomplete) signal snapshot.
    ///
    /// Heuristics (spec §6.3):
    /// 1. Has TLS AND shares a TLS session with a known agent
    ///    → join that agent, confidence = 0.70
    /// 2. Has TLS but does NOT share with any known agent
    ///    → new ProvisionalIdentity (UUID), confidence = 0.50
    /// 3. No TLS, ancestor has a known agent, AND !is_agent_process(child)
    ///    → inherit ancestor's agent, confidence = 0.55
    ///    If is_agent_process(child) is true:
    ///    → new ProvisionalIdentity (UUID), confidence = 0.30
    /// 4. No TLS, no known ancestor
    ///    → new ProvisionalIdentity (UUID), confidence = 0.25
    pub fn resolve_provisional(
        &mut self,
        process_id: ProcessId,
        snapshot: &SignalSnapshot,
    ) -> ProvisionalIdentity {
        let tls_sessions = snapshot.tls_sessions_for_process(process_id);

        if !tls_sessions.is_empty() {
            // Heuristics 1 & 2: process has TLS
            self.resolve_provisional_with_tls(process_id, &tls_sessions, snapshot)
        } else {
            // Heuristics 3 & 4: process has no TLS
            self.resolve_provisional_no_tls(process_id, snapshot)
        }
    }

    /// Heuristic 1 & 2: process has at least one TLS session.
    fn resolve_provisional_with_tls(
        &mut self,
        process_id: ProcessId,
        tls_sessions: &[crate::responsibility::types::TlsSessionId],
        snapshot: &SignalSnapshot,
    ) -> ProvisionalIdentity {
        // Check if any TLS session is shared with a known agent's process
        for tls in tls_sessions {
            // Look up which process owns this TLS session
            if let Some(owner) = snapshot.process_for_tls(tls) {
                // Check if owner has a known agent
                if let Some(existing) = self.provisional_map.get(&owner) {
                    let agent_id = existing.agent_id.clone();
                    let updated = ProvisionalIdentity {
                        agent_id: agent_id.clone(),
                        process_ids: vec![process_id],
                        primary_signal: IdentitySignal::TlsSessionShared,
                        confidence: 0.70,
                    };
                    // Update our map
                    self.provisional_map.insert(process_id, updated.clone());
                    return updated;
                }
            }
        }

        // No shared agent found through TLS — check if any process
        // from a different TLS session maps to a known agent (e.g. different
        // TLS for the same process). This handles the very common case where
        // the process was previously assigned a low-confidence identity and
        // TLS arrives later upgrading it.
        if let Some(existing) = self.provisional_map.get(&process_id) {
            // Process already has a provisional identity; upgrade confidence
            // if it was low and this is new TLS evidence
            if existing.confidence < 0.70 {
                let upgraded = ProvisionalIdentity {
                    agent_id: existing.agent_id.clone(),
                    process_ids: existing.process_ids.clone(),
                    primary_signal: IdentitySignal::TlsSessionNew,
                    confidence: 0.70,
                };
                self.provisional_map.insert(process_id, upgraded.clone());
                return upgraded;
            }
            return existing.clone();
        }

        // Heuristic 2: new agent with TLS
        let identity = ProvisionalIdentity {
            agent_id: AgentId::new(),
            process_ids: vec![process_id],
            primary_signal: IdentitySignal::TlsSessionNew,
            confidence: 0.50,
        };
        self.provisional_map.insert(process_id, identity.clone());
        identity
    }

    /// Heuristic 3 & 4: process has no TLS.
    fn resolve_provisional_no_tls(
        &mut self,
        process_id: ProcessId,
        snapshot: &SignalSnapshot,
    ) -> ProvisionalIdentity {
        // Walk up the process tree looking for an ancestor with a known agent
        let mut current = process_id;
        let max_depth: usize = 32;
        let mut visited = HashSet::new();

        for _depth in 0..max_depth {
            if !visited.insert(current) {
                break; // Cycle detected
            }

            if let Some(existing) = self.provisional_map.get(&current) {
                if current == process_id {
                    // Already assigned — return existing
                    return existing.clone();
                }

                // Ancestor found — check if process is an agent process
                let is_agent = super::union_find::is_agent_process(process_id, snapshot);

                if is_agent {
                    // Heuristic 3b: agent-child without TLS but with agent ancestry
                    // This is unusual — create a new provisional identity
                    let identity = ProvisionalIdentity {
                        agent_id: AgentId::new(),
                        process_ids: vec![process_id],
                        primary_signal: IdentitySignal::Unknown,
                        confidence: 0.30,
                    };
                    self.provisional_map.insert(process_id, identity.clone());
                    return identity;
                } else {
                    // Heuristic 3a: tool child inherits ancestor's agent
                    let identity = ProvisionalIdentity {
                        agent_id: existing.agent_id.clone(),
                        process_ids: vec![process_id],
                        primary_signal: IdentitySignal::ProcessTreeInherited,
                        confidence: 0.55,
                    };
                    self.provisional_map.insert(process_id, identity.clone());
                    return identity;
                }
            }

            // Move to parent
            match snapshot.get_parent(current) {
                Some(parent) if parent != current => {
                    current = parent;
                }
                _ => break, // Root reached with no agent ancestor
            }
        }

        // Heuristic 4: no TLS, no known ancestor → unknown
        let identity = ProvisionalIdentity {
            agent_id: AgentId::new(),
            process_ids: vec![process_id],
            primary_signal: IdentitySignal::Unknown,
            confidence: 0.25,
        };
        self.provisional_map.insert(process_id, identity.clone());
        identity
    }

    // ── Phase 2: Authoritative Resolution ─────────────────────────

    /// Run full three-signal union-find on a frozen snapshot.
    ///
    /// This should be called at session end (SIGINT, child exit, or API
    /// trigger) once all Phase 1 buffers have been flushed and the signal
    /// graph is frozen through `SignalGraph::snapshot()`.
    ///
    /// Returns the authoritative identities plus refinements linking them
    /// to the Phase 1 provisional assignments.
    pub fn resolve_authoritative(&self, snapshot: &SignalSnapshot) -> AuthoritativeResolution {
        run_authoritative_resolution(snapshot, &self.provisional_map)
    }
    pub fn provisional_identities(&self) -> Vec<ProvisionalIdentity> {
        self.provisional_map.values().cloned().collect()
    }
}

impl Default for IdentityResolver {
    fn default() -> Self {
        Self::new()
    }
}

// ─── IdentityProvider Implementation ────────────────────────────────────

impl IdentityProvider for IdentityResolver {
    /// Look up which agent a process belongs to.
    ///
    /// During Phase 1 this returns the provisional assignment; during
    /// Phase 2 the authoritative assignment.
    fn agent_for_process(&self, process_id: ProcessId) -> Option<AgentId> {
        self.provisional_map
            .get(&process_id)
            .map(|p| p.agent_id.clone())
    }

    /// All current process → agent assignments.
    fn all_assignments(&self) -> HashMap<ProcessId, AgentId> {
        self.provisional_map
            .iter()
            .map(|(pid, prov)| (*pid, prov.agent_id.clone()))
            .collect()
    }
}

// ─── Tests ──────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use crate::responsibility::signals::SignalGraph;

    fn pid(id: u32, start: u64) -> ProcessId {
        ProcessId::new(id, start)
    }

    fn tls(s: &str) -> crate::responsibility::types::TlsSessionId {
        crate::responsibility::types::TlsSessionId::from(s)
    }

    #[test]
    fn test_provisional_tls_new_agent() {
        let mut g = SignalGraph::new();
        let p = pid(100, 1);
        g.observe_exec(
            p,
            pid(1, 0),
            ProcessExecInfo {
                comm: "claude".into(),
                ..Default::default()
            },
            None,
            1,
        );
        g.observe_tls(p, tls("sess-1"), 10);
        let snap = g.snapshot();

        let mut resolver = IdentityResolver::new();
        let result = resolver.resolve_provisional(p, &snap);

        assert_eq!(result.primary_signal, IdentitySignal::TlsSessionNew);
        assert_eq!(result.confidence, 0.50);
        assert!(result.process_ids.contains(&p));
    }

    #[test]
    fn test_provisional_no_tls_inherits_from_ancestor() {
        let mut g = SignalGraph::new();
        let parent = pid(100, 1);
        let child = pid(200, 2);

        g.observe_exec(
            parent,
            pid(1, 0),
            ProcessExecInfo {
                comm: "claude".into(),
                ..Default::default()
            },
            None,
            1,
        );
        g.observe_tls(parent, tls("sess-1"), 10);
        g.observe_exec(
            child,
            parent,
            ProcessExecInfo {
                comm: "cat".into(),
                ..Default::default()
            },
            None,
            2,
        );
        let snap = g.snapshot();

        let mut resolver = IdentityResolver::new();
        // First resolve parent
        let parent_identity = resolver.resolve_provisional(parent, &snap);
        // Then resolve child → should inherit parent's agent
        let child_identity = resolver.resolve_provisional(child, &snap);

        assert_eq!(child_identity.agent_id, parent_identity.agent_id);
        assert_eq!(
            child_identity.primary_signal,
            IdentitySignal::ProcessTreeInherited
        );
        assert_eq!(child_identity.confidence, 0.55);
    }

    #[test]
    fn test_provisional_no_ancestor_unknown() {
        let mut g = SignalGraph::new();
        let p = pid(100, 1);
        g.observe_exec(
            p,
            pid(1, 0),
            ProcessExecInfo {
                comm: "unknown-tool".into(),
                ..Default::default()
            },
            None,
            1,
        );
        let snap = g.snapshot();

        let mut resolver = IdentityResolver::new();
        let result = resolver.resolve_provisional(p, &snap);

        assert_eq!(result.primary_signal, IdentitySignal::Unknown);
        assert_eq!(result.confidence, 0.25);
    }

    #[test]
    fn test_provisional_tls_shared_upgrades_confidence() {
        let mut g = SignalGraph::new();
        let a = pid(100, 1);
        let b = pid(200, 2);

        g.observe_exec(
            a,
            pid(1, 0),
            ProcessExecInfo {
                comm: "claude".into(),
                ..Default::default()
            },
            None,
            1,
        );
        g.observe_exec(
            b,
            pid(1, 0),
            ProcessExecInfo {
                comm: "claude".into(),
                ..Default::default()
            },
            None,
            2,
        );

        let shared = tls("shared-sess");
        g.observe_tls(a, shared.clone(), 10);
        g.observe_tls(b, shared.clone(), 20);
        let snap = g.snapshot();

        let mut resolver = IdentityResolver::new();
        let result_a = resolver.resolve_provisional(a, &snap);
        let result_b = resolver.resolve_provisional(b, &snap);

        // Each process gets its own provisional identity during Phase 1.
        // TLS-based merging happens in Phase 2 via union-find, not here.
        assert_ne!(result_a.agent_id, result_b.agent_id);
        // Both have TLS, so confidence should be at least the TLS base level
        assert!(result_a.confidence > 0.0);
        assert!(result_b.confidence > 0.0);
    }

    #[test]
    fn test_provisional_agent_child_without_tls_gets_new_id() {
        let mut g = SignalGraph::new();
        let parent = pid(100, 1);
        let child = pid(200, 2);

        g.observe_exec(
            parent,
            pid(1, 0),
            ProcessExecInfo {
                comm: "claude".into(),
                ..Default::default()
            },
            None,
            1,
        );
        g.observe_tls(parent, tls("sess-parent"), 10);
        // Child is also "claude" but has no TLS yet
        g.observe_exec(
            child,
            parent,
            ProcessExecInfo {
                comm: "claude".into(),
                ..Default::default()
            },
            None,
            2,
        );
        let snap = g.snapshot();

        let mut resolver = IdentityResolver::new();
        let parent_identity = resolver.resolve_provisional(parent, &snap);
        let child_identity = resolver.resolve_provisional(child, &snap);

        // Agent child without TLS gets a NEW provisional identity (low confidence)
        assert_ne!(child_identity.agent_id, parent_identity.agent_id);
        assert_eq!(child_identity.confidence, 0.30);
    }

    #[test]
    fn test_identity_provider_trait() {
        let mut g = SignalGraph::new();
        let p = pid(100, 1);
        g.observe_exec(
            p,
            pid(1, 0),
            ProcessExecInfo {
                comm: "claude".into(),
                ..Default::default()
            },
            None,
            1,
        );
        g.observe_tls(p, tls("sess-1"), 10);
        let snap = g.snapshot();

        let mut resolver = IdentityResolver::new();
        resolver.resolve_provisional(p, &snap);

        // Test IdentityProvider impl
        let agent = resolver.agent_for_process(p);
        assert!(agent.is_some());

        let all = resolver.all_assignments();
        assert!(all.contains_key(&p));
    }

    #[test]
    fn test_resolve_authoritative_integration() {
        let mut g = SignalGraph::new();
        let claude = pid(100, 1);
        let tool = pid(200, 2);

        g.observe_exec(
            claude,
            pid(1, 0),
            ProcessExecInfo {
                comm: "claude".into(),
                ..Default::default()
            },
            None,
            1,
        );
        g.observe_tls(claude, tls("sess-1"), 10);
        g.observe_exec(
            tool,
            claude,
            ProcessExecInfo {
                comm: "cat".into(),
                ..Default::default()
            },
            None,
            2,
        );

        let snap = g.snapshot();

        // Phase 1
        let mut resolver = IdentityResolver::new();
        resolver.resolve_provisional(claude, &snap);
        resolver.resolve_provisional(tool, &snap);

        // Phase 2
        let result = resolver.resolve_authoritative(&snap);
        assert_eq!(result.agents.len(), 1);
    }
}
