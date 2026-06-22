// SPDX-License-Identifier: MIT
//
// SignalSnapshot — immutable point-in-time view of the signal graph.
// Packaged in Arc for shared ownership across async tasks and queries.

use crate::responsibility::types::{MessageId, ProcessId, TlsSessionId};
use std::collections::{HashMap, HashSet};

// ─── ProcessMetadata ─────────────────────────────────────────────────

/// Metadata tracked for a single process.
#[derive(Debug, Clone, PartialEq)]
pub struct ProcessMetadata {
    /// Composite process identity (pid + start_time_ns).
    pub process_id: ProcessId,
    /// Process name (comm).
    pub comm: String,
    /// Process group ID, if known.
    pub pgid: Option<u32>,
    /// Timestamp (monotonic ns) when this process was first observed.
    pub first_seen_ns: u64,
    /// Timestamp (monotonic ns) when this process was last observed.
    pub last_seen_ns: u64,
    /// When the process exited, if it has exited.
    pub exit_time_ns: Option<u64>,
    /// Exit code, if the process has exited.
    pub exit_code: Option<i32>,
    /// Whether the process is still running.
    pub is_alive: bool,
}

// ─── Orphan types ────────────────────────────────────────────────────

/// A message that arrived before its TLS session was bound to a process.
#[derive(Debug, Clone)]
pub struct OrphanMessage {
    /// The orphaned message ID.
    pub msg_id: MessageId,
    /// The TLS session it belongs to, if known.
    pub tls_session: Option<TlsSessionId>,
    /// Monotonic timestamp when observed.
    pub ts_ns: u64,
}

/// A TLS session that arrived before its owning process was discovered.
#[derive(Debug, Clone)]
pub struct OrphanTls {
    /// The TLS session.
    pub tls_session: TlsSessionId,
    /// The process that is expected to own this session.
    pub process_id: ProcessId,
    /// Monotonic timestamp when observed.
    pub ts_ns: u64,
}

/// A diagnostic record for when a TLS session is claimed by multiple processes.
#[derive(Debug, Clone)]
pub struct TlsCollision {
    /// The contested TLS session.
    pub tls_session: TlsSessionId,
    /// First process that claimed it.
    pub process_a: ProcessId,
    /// Second process that also claimed it.
    pub process_b: ProcessId,
}

// ─── SignalMetrics ───────────────────────────────────────────────────

/// Aggregate metrics for a signal snapshot.
#[derive(Debug, Clone)]
pub struct SignalMetrics {
    /// Total processes tracked.
    pub process_count: usize,
    /// Total TLS sessions bound.
    pub tls_session_count: usize,
    /// Total messages observed (deduplicated across paths).
    pub message_count: usize,
    /// Total orphaned events (messages + TLS).
    pub orphan_count: usize,
    /// Cumulative lost events from eBPF ring buffer overflows.
    pub lost_event_count: u64,
}

// ─── SignalSnapshot ──────────────────────────────────────────────────

/// Immutable point-in-time snapshot of the signal graph.
///
/// All query methods are read-only. The snapshot is cheap to clone
/// (Arc ref-count) and safe to share across threads.
#[derive(Debug, Clone)]
pub struct SignalSnapshot {
    /// All known processes.
    pub(crate) processes: HashMap<ProcessId, ProcessMetadata>,
    /// Child → parent edges.
    pub(crate) parent_map: HashMap<ProcessId, ProcessId>,
    /// Parent → children reverse index.
    pub(crate) child_map: HashMap<ProcessId, Vec<ProcessId>>,
    /// TLS session → owning process.
    pub(crate) tls_to_process: HashMap<TlsSessionId, ProcessId>,
    /// TLS session → messages.
    pub(crate) tls_to_messages: HashMap<TlsSessionId, Vec<MessageId>>,
    /// Process → directly observed messages.
    pub(crate) direct_process_messages: HashMap<ProcessId, Vec<MessageId>>,
    /// Thread ID → owning process.
    pub(crate) thread_to_process: HashMap<u32, ProcessId>,
    /// Pre-computed metrics.
    pub(crate) metrics: SignalMetrics,
}

/// Maximum traversal depth for `is_descendant_or_same` to prevent
/// infinite loops on cyclic graphs.
const MAX_DESCENDANT_DEPTH: usize = 256;

impl SignalSnapshot {
    // ── Process lookups ────────────────────────────────────────────

    /// Look up process metadata by raw pid and start_time_ns.
    pub fn get_process(&self, pid: u32, start_time_ns: u64) -> Option<&ProcessMetadata> {
        self.processes.get(&ProcessId::new(pid, start_time_ns))
    }

    /// Get the parent of a process.
    pub fn get_parent(&self, process_id: ProcessId) -> Option<ProcessId> {
        self.parent_map.get(&process_id).copied()
    }

    /// Get the children of a process.
    pub fn get_children(&self, process_id: ProcessId) -> &[ProcessId] {
        self.child_map
            .get(&process_id)
            .map(|v| v.as_slice())
            .unwrap_or(&[])
    }

    // ── Correlation queries ────────────────────────────────────────

    /// Whether an intent process can be responsible for an action process.
    ///
    /// Returns true if the action process is a descendant of (or the same as)
    /// the intent process in the process tree.
    pub fn can_correlate(&self, intent: ProcessId, action: ProcessId) -> bool {
        self.is_descendant_or_same(action, intent)
    }

    /// Whether `process_id` is equal to `ancestor` or a descendant of it.
    ///
    /// Traversal is bounded to `MAX_DESCENDANT_DEPTH` (256) and detects
    /// cycles to prevent infinite loops.
    pub fn is_descendant_or_same(&self, process_id: ProcessId, ancestor: ProcessId) -> bool {
        if process_id == ancestor {
            return true;
        }

        let mut visited = HashSet::new();
        let mut current = process_id;
        visited.insert(current);

        for _depth in 0..MAX_DESCENDANT_DEPTH {
            let parent = match self.parent_map.get(&current) {
                Some(&p) => p,
                None => {
                    // Parent EXEC was missed (BPF probe startup window).
                    // The placeholder is a VALUE in parent_map from a
                    // captured child. If any other child of this parent
                    // reaches the ancestor, this intermediate node is
                    // in the chain.
                    if self.parent_map.values().any(|v| v.pid == current.pid) {
                        // Intermediate node confirmed — continue walking
                        // upward. We don't know the exact parent ProcessId,
                        // but if any ancestor exists we'll find it.
                        return self.parent_map.values().any(|v| *v == ancestor);
                    }
                    return false;
                }
            };

            // Cycle detection (before ancestor match, to catch corrupted chains)
            if !visited.insert(parent) {
                return false;
            }

            if parent == ancestor {
                return true;
            }

            current = parent;
        }

        // Exceeded max depth — not a descendant within the search limit
        false
    }

    /// Whether a process belongs to an agent's process tree.
    ///
    /// Uses a dual signal to avoid timing-dependent detection:
    /// 1. TLS-based: the process (or ancestor) has TLS sessions (reliable but late)
    /// 2. Comm-based: the process (or ancestor) has a known agent comm
    ///    (available immediately after EXEC, before TLS is established)
    ///
    /// Both signals independently indicate agent ownership. Tool-child
    /// processes (bash, git, etc.) are covered via ancestor traversal.
    pub fn has_agent_ancestry(&self, process_id: ProcessId) -> bool {
        const AGENT_COMMS: &[&str] = &["claude", "claude-code"];

        let is_agent = |pid: ProcessId| -> bool {
            if !self.tls_sessions_for_process(pid).is_empty() {
                return true;
            }
            if let Some(meta) = self.get_process(pid.pid, pid.start_time_ns) {
                if AGENT_COMMS.contains(&meta.comm.as_str()) {
                    return true;
                }
            }
            false
        };

        if is_agent(process_id) {
            return true;
        }

        let mut visited = HashSet::new();
        let mut current = process_id;
        visited.insert(current);

        for _depth in 0..MAX_DESCENDANT_DEPTH {
            let parent = match self.parent_map.get(&current) {
                Some(&p) => p,
                None => {
                    // Parent EXEC event was missed (e.g. BPF probe startup
                    // window).  The placeholder was created by observe_exec()
                    // with the child's ppid.  If the parent appears as a
                    // VALUE anywhere in parent_map (i.e. some captured child
                    // references it), it is part of the agent's process tree
                    // — BPF only captures processes in the filtered tree.
                    if self.parent_map.values().any(|v| v.pid == current.pid) {
                        return true;
                    }
                    return false;
                }
            };

            if !visited.insert(parent) {
                return false;
            }

            if is_agent(parent) {
                return true;
            }

            current = parent;
        }

        false
    }

    // ── TLS queries ────────────────────────────────────────────────

    /// All TLS sessions observed for a given process.
    pub fn tls_sessions_for_process(&self, process_id: ProcessId) -> Vec<TlsSessionId> {
        self.tls_to_process
            .iter()
            .filter(|(_, pid)| **pid == process_id)
            .map(|(session, _)| session.clone())
            .collect()
    }

    /// The process that owns a given TLS session.
    pub fn process_for_tls(&self, tls_session: &TlsSessionId) -> Option<ProcessId> {
        self.tls_to_process.get(tls_session).copied()
    }

    // ── Message queries ────────────────────────────────────────────

    /// All messages associated with a process.
    ///
    /// Merges messages from both TLS-path and direct-path bindings.
    /// Results are deduplicated.
    pub fn messages_for_process(&self, process_id: ProcessId) -> Vec<MessageId> {
        let mut seen: HashSet<&str> = HashSet::new();
        let mut result = Vec::new();

        // 1. Messages from TLS sessions owned by this process
        for (tls_session, &owner) in &self.tls_to_process {
            if owner == process_id {
                if let Some(msgs) = self.tls_to_messages.get(tls_session) {
                    for m in msgs {
                        if seen.insert(m.as_str()) {
                            result.push(m.clone());
                        }
                    }
                }
            }
        }

        // 2. Direct process messages
        if let Some(msgs) = self.direct_process_messages.get(&process_id) {
            for m in msgs {
                if seen.insert(m.as_str()) {
                    result.push(m.clone());
                }
            }
        }

        result
    }

    // ── Thread queries ─────────────────────────────────────────────

    /// The process that owns a given thread (by tid).
    pub fn process_for_thread(&self, tid: u32) -> Option<ProcessId> {
        self.thread_to_process.get(&tid).copied()
    }

    // ── Metrics ────────────────────────────────────────────────────

    /// Aggregate metrics for this snapshot.
    pub fn metrics(&self) -> SignalMetrics {
        self.metrics.clone()
    }
}

// ─── Tests ───────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    fn pid(id: u32, start: u64) -> ProcessId {
        ProcessId::new(id, start)
    }

    #[test]
    fn test_get_process_not_found() {
        let snap = SignalSnapshot {
            processes: HashMap::new(),
            parent_map: HashMap::new(),
            child_map: HashMap::new(),
            tls_to_process: HashMap::new(),
            tls_to_messages: HashMap::new(),
            direct_process_messages: HashMap::new(),
            thread_to_process: HashMap::new(),
            metrics: SignalMetrics {
                process_count: 0,
                tls_session_count: 0,
                message_count: 0,
                orphan_count: 0,
                lost_event_count: 0,
            },
        };
        assert_eq!(snap.get_process(42, 100), None);
    }

    #[test]
    fn test_get_children_empty_for_unknown() {
        let snap = SignalSnapshot {
            processes: HashMap::new(),
            parent_map: HashMap::new(),
            child_map: HashMap::new(),
            tls_to_process: HashMap::new(),
            tls_to_messages: HashMap::new(),
            direct_process_messages: HashMap::new(),
            thread_to_process: HashMap::new(),
            metrics: SignalMetrics {
                process_count: 0,
                tls_session_count: 0,
                message_count: 0,
                orphan_count: 0,
                lost_event_count: 0,
            },
        };
        assert!(snap.get_children(pid(1, 0)).is_empty());
    }

    #[test]
    fn test_is_descendant_or_same_same_process() {
        let mut parent_map = HashMap::new();
        parent_map.insert(pid(2, 1), pid(1, 0));
        let snap = SignalSnapshot {
            processes: HashMap::new(),
            parent_map,
            child_map: HashMap::new(),
            tls_to_process: HashMap::new(),
            tls_to_messages: HashMap::new(),
            direct_process_messages: HashMap::new(),
            thread_to_process: HashMap::new(),
            metrics: SignalMetrics {
                process_count: 0,
                tls_session_count: 0,
                message_count: 0,
                orphan_count: 0,
                lost_event_count: 0,
            },
        };
        assert!(snap.is_descendant_or_same(pid(2, 1), pid(1, 0)));
        assert!(snap.is_descendant_or_same(pid(1, 0), pid(1, 0)));
    }

    #[test]
    fn test_is_descendant_or_same_unrelated() {
        let snap = SignalSnapshot {
            processes: HashMap::new(),
            parent_map: HashMap::new(),
            child_map: HashMap::new(),
            tls_to_process: HashMap::new(),
            tls_to_messages: HashMap::new(),
            direct_process_messages: HashMap::new(),
            thread_to_process: HashMap::new(),
            metrics: SignalMetrics {
                process_count: 0,
                tls_session_count: 0,
                message_count: 0,
                orphan_count: 0,
                lost_event_count: 0,
            },
        };
        assert!(!snap.is_descendant_or_same(pid(2, 1), pid(1, 0)));
    }

    #[test]
    fn test_cycle_detection() {
        // a → b → c → b  (cycle)
        let mut parent_map = HashMap::new();
        parent_map.insert(pid(2, 2), pid(1, 1)); // b → a
        parent_map.insert(pid(3, 3), pid(2, 2)); // c → b
        parent_map.insert(pid(2, 2), pid(3, 3)); // b → c (cycle)
        let snap = SignalSnapshot {
            processes: HashMap::new(),
            parent_map,
            child_map: HashMap::new(),
            tls_to_process: HashMap::new(),
            tls_to_messages: HashMap::new(),
            direct_process_messages: HashMap::new(),
            thread_to_process: HashMap::new(),
            metrics: SignalMetrics {
                process_count: 0,
                tls_session_count: 0,
                message_count: 0,
                orphan_count: 0,
                lost_event_count: 0,
            },
        };
        // Cycle should be detected, return false
        assert!(!snap.is_descendant_or_same(pid(3, 3), pid(1, 1)));
    }
}
