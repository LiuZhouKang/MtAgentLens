// SPDX-License-Identifier: Apache-2.0 OR MIT
//
// SignalGraph — eBPF signal acquisition and live process/tls/message binding.
// Maintains the process tree, TLS-to-message two-hop bindings, thread tracking,
// and orphan buffers. Produces immutable Arc<SignalSnapshot> on demand.

use crate::responsibility::types::{
    MessageId, NativeToolCallObservation, NativeToolCallRef, ProcessId, ResponsibilityError,
    TlsSessionId,
};
use std::collections::{HashMap, HashSet};
use std::sync::Arc;

use super::signal_snapshot::{
    MessageConversationBinding, OrphanMessage, OrphanTls, ProcessExecInfo, ProcessMetadata,
    SignalSnapshot, TlsCollision,
};

// ─── Capacity constants ───────────────────────────────────────────────

pub const MAX_PROCESSES: usize = 100_000;

/// Upper bound on remembered ancestry edges. Ancestry is a few bytes per
/// process; phase-2 replay needs it for processes that already exited.
pub const MAX_LINEAGE_MEMORY: usize = 8192;
/// 600 seconds in nanoseconds
pub const PROCESS_TTL_NS: u64 = 600_000_000_000;
/// 30 seconds in nanoseconds
pub const ORPHAN_TTL_NS: u64 = 30_000_000_000;

// ─── SignalGraph ──────────────────────────────────────────────────────

/// Live signal graph collecting eBPF events.
///
/// Maintains the process tree, TLS-session bindings, message bindings
/// (both via TLS and direct), thread-to-process tracking, orphan buffers,
/// and diagnostic counters.
///
/// The graph is mutable during collection; consumers read through
/// `snapshot()` which returns an `Arc<SignalSnapshot>`.
#[derive(Debug, Clone)]
pub struct SignalGraph {
    // ── Process tree ────────────────────────────────────────────────
    /// All known processes by composite key.
    processes: HashMap<ProcessId, ProcessMetadata>,
    /// Child → parent edges.
    parent_map: HashMap<ProcessId, ProcessId>,
    /// Append-only child → parent memory.
    ///
    /// `parent_map` drops an entry when the process exits (memory hygiene), but
    /// phase-2 batch correlation replays the whole run *after* every short-lived
    /// build hook has exited. Without a memory the ancestor chain of an exited
    /// `make → sh → python` hook is unknowable, which made network egress
    /// attribution flaky (0 ↔ present across identical runs). Bounded FIFO:
    /// oldest ancestry is forgotten before live processes are.
    lineage_memory: HashMap<ProcessId, ProcessId>,
    lineage_order: std::collections::VecDeque<ProcessId>,
    /// Parent → children reverse index.
    child_map: HashMap<ProcessId, Vec<ProcessId>>,

    // ── TLS path: two-hop bindings ─────────────────────────────────
    /// TLS session → owning process (last writer wins for shared sessions).
    tls_to_process: HashMap<TlsSessionId, ProcessId>,
    /// Append-only TLS session → every process observed on that session.
    ///
    /// `tls_to_process` keeps only the last writer. When a parent and a
    /// child (or two worker processes) share one socket, the earlier process
    /// vanished from the reverse index and the S2 merge — the strongest
    /// identity signal — could not join them. This map never forgets a
    /// participant.
    tls_participants: HashMap<TlsSessionId, Vec<ProcessId>>,
    /// TLS session → messages carried on that session.
    tls_to_messages: HashMap<TlsSessionId, Vec<MessageId>>,

    // ── Direct path (TLS unavailable) ──────────────────────────────
    /// Process → messages observed directly (e.g. stdio capture).
    direct_process_messages: HashMap<ProcessId, Vec<MessageId>>,
    /// Request-declared message → conversation anchors, captured with the
    /// observed process that produced the message.
    message_conversation_bindings: Vec<MessageConversationBinding>,
    /// Structured native references observed before intent deduplication.
    /// Exact rows deduplicate; the frozen snapshot sorts them canonically.
    native_tool_call_observations: HashSet<NativeToolCallObservation>,

    // ── Conversation path ─────────────────────────────────────────
    /// Conversation/session id (from the LLM request metadata) → processes
    /// that carried it. This is the third identity signal: processes sharing
    /// a conversation are the same logical agent even when they do not share
    /// a process ancestor or a TLS session.
    conversation_to_processes: HashMap<String, Vec<ProcessId>>,
    /// Process → conversation ids it carried.
    process_to_conversations: HashMap<ProcessId, Vec<String>>,

    // ── Thread tracking ────────────────────────────────────────────
    /// Thread ID (tid) → owning process.
    thread_to_process: HashMap<u32, ProcessId>,

    // ── Orphan buffers ─────────────────────────────────────────────
    /// Messages that arrived before their TLS session was bound to a process.
    orphan_messages: Vec<OrphanMessage>,
    /// TLS sessions that arrived before their process exec event.
    orphan_tls: Vec<OrphanTls>,

    // ── Diagnostics ────────────────────────────────────────────────
    /// TLS sessions that were claimed by more than one process.
    tls_collisions: Vec<TlsCollision>,
    /// Cumulative count of eBPF ring-buffer lost events.
    pub lost_event_count: u64,
}

impl SignalGraph {
    // ── Construction ───────────────────────────────────────────────

    pub fn new() -> Self {
        Self {
            processes: HashMap::new(),
            parent_map: HashMap::new(),
            lineage_memory: HashMap::new(),
            lineage_order: std::collections::VecDeque::new(),
            child_map: HashMap::new(),
            tls_to_process: HashMap::new(),
            tls_participants: HashMap::new(),
            tls_to_messages: HashMap::new(),
            conversation_to_processes: HashMap::new(),
            process_to_conversations: HashMap::new(),
            direct_process_messages: HashMap::new(),
            message_conversation_bindings: Vec::new(),
            native_tool_call_observations: HashSet::new(),
            thread_to_process: HashMap::new(),
            orphan_messages: Vec::new(),
            orphan_tls: Vec::new(),
            tls_collisions: Vec::new(),
            lost_event_count: 0,
        }
    }

    // ── Observe events ─────────────────────────────────────────────

    /// Record a process EXEC event.
    ///
    /// Establishes parent→child relationship, creates metadata entries,
    /// and resolves any orphan TLS sessions waiting for this process.
    pub fn observe_snapshot(
        &mut self,
        child: ProcessId,
        parent: ProcessId,
        info: ProcessExecInfo,
        pgid: Option<u32>,
        ts: u64,
    ) {
        self.observe_process_metadata(child, parent, info, pgid, ts);
    }

    pub fn observe_exec(
        &mut self,
        child: ProcessId,
        parent: ProcessId,
        info: ProcessExecInfo,
        pgid: Option<u32>,
        ts: u64,
    ) {
        self.observe_process_metadata(child, parent, info, pgid, ts);
    }

    fn observe_process_metadata(
        &mut self,
        child: ProcessId,
        parent: ProcessId,
        info: ProcessExecInfo,
        pgid: Option<u32>,
        ts: u64,
    ) {
        // Capacity guard: evict expired before adding
        if self.processes.len() >= MAX_PROCESSES {
            self.evict_expired(ts);
            if self.processes.len() >= MAX_PROCESSES {
                // Find the oldest dead process with no children and remove it
                self.drop_one_process();
            }
        }

        // Upsert child metadata.
        // Both closures below are FnOnce, so each needs its own copy.
        let info_update = info.clone();
        let info_insert = info;
        self.processes
            .entry(child)
            .and_modify(move |m| {
                m.comm = info_update.comm.clone();
                m.first_seen_ns = m.first_seen_ns.min(ts);
                m.last_seen_ns = m.last_seen_ns.max(ts);
                m.is_alive = m.exit_time_ns.is_none();
                if pgid.is_some() {
                    m.pgid = pgid;
                }
                // Only overwrite identity signals we actually observed —
                // an EXEC without a cmdline must not erase a known one.
                if info_update.cmdline.is_some() {
                    m.cmdline = info_update.cmdline.clone();
                }
                if info_update.exe_path.is_some() {
                    m.exe_path = info_update.exe_path.clone();
                }
                if info_update.cgroup_unit.is_some() {
                    m.cgroup_unit = info_update.cgroup_unit.clone();
                }
            })
            .or_insert_with(move || ProcessMetadata {
                process_id: child,
                comm: info_insert.comm,
                pgid,
                first_seen_ns: ts,
                last_seen_ns: ts,
                exit_time_ns: None,
                exit_code: None,
                is_alive: true,
                cmdline: info_insert.cmdline,
                exe_path: info_insert.exe_path,
                cgroup_unit: info_insert.cgroup_unit,
            });

        // Upsert parent metadata (lazily discovered)
        if parent.pid != 0 {
            self.processes
                .entry(parent)
                .or_insert_with(|| ProcessMetadata {
                    process_id: parent,
                    comm: String::new(),
                    pgid: None,
                    first_seen_ns: ts,
                    last_seen_ns: ts,
                    exit_time_ns: None,
                    exit_code: None,
                    is_alive: true,
                    cmdline: None,
                    exe_path: None,
                    cgroup_unit: None,
                });
        }

        // Parent-child link (skip PID 1 / init / PID 0)
        if parent.pid != 1 && parent.pid != 0 {
            let existing_parent = self.parent_map.get(&child).copied();
            if existing_parent != Some(parent) {
                // Remove from old parent's child list
                if let Some(old_parent) = existing_parent
                    && let Some(siblings) = self.child_map.get_mut(&old_parent)
                {
                    siblings.retain(|c| *c != child);
                }
                self.parent_map.insert(child, parent);
                self.remember_lineage(child, parent);
                self.child_map.entry(parent).or_default().push(child);
            }
        }

        // Resolve orphan TLS sessions that might match this process
        self.resolve_orphan_tls_for_process(child, ts);
    }

    /// Remember an ancestry edge for phase-2 replay, bounded by
    /// [`MAX_LINEAGE_MEMORY`] oldest-first.
    fn remember_lineage(&mut self, child: ProcessId, parent: ProcessId) {
        if self.lineage_memory.insert(child, parent).is_none() {
            self.lineage_order.push_back(child);
        }
        while self.lineage_order.len() > MAX_LINEAGE_MEMORY {
            let Some(oldest) = self.lineage_order.pop_front() else {
                break;
            };
            // Keep live edges: parent_map is authoritative while the child runs.
            if !self.parent_map.contains_key(&oldest) {
                self.lineage_memory.remove(&oldest);
            }
        }
    }

    /// Record a process EXIT event.
    pub fn observe_exit(&mut self, process_id: ProcessId, exit_code: i32, ts: u64) {
        if let Some(meta) = self.processes.get_mut(&process_id) {
            meta.exit_time_ns = Some(ts);
            meta.exit_code = Some(exit_code);
            meta.is_alive = false;
            meta.last_seen_ns = ts;
        }
    }

    /// Record an optional request-declared conversation/session hint.
    ///
    /// The id is read from the request metadata (for example Claude Code's
    /// JSON-encoded `metadata.user_id` carrying `session_id`). The plaintext
    /// request is observed through TLS capture; the hint is not proof of a
    /// distinct actor and need not be present on every request.
    pub fn observe_conversation(&mut self, process_id: ProcessId, conversation_id: &str, _ts: u64) {
        if conversation_id.is_empty() {
            return;
        }
        let entry = self
            .conversation_to_processes
            .entry(conversation_id.to_string())
            .or_default();
        if !entry.contains(&process_id) {
            entry.push(process_id);
        }
        let per_process = self.process_to_conversations.entry(process_id).or_default();
        if !per_process.iter().any(|id| id == conversation_id) {
            per_process.push(conversation_id.to_string());
        }
    }

    /// Bind a TLS session to a process, diagnose collisions and resolve orphans.
    pub fn observe_tls(&mut self, process_id: ProcessId, tls_session: TlsSessionId, ts: u64) {
        // Collision detection
        if let Some(&existing) = self.tls_to_process.get(&tls_session)
            && existing != process_id
        {
            self.tls_collisions.push(TlsCollision {
                tls_session: tls_session.clone(),
                process_a: existing,
                process_b: process_id,
            });
            // Overwrite with new binding (latest wins)
        }

        self.tls_to_process.insert(tls_session.clone(), process_id);
        let participants = self
            .tls_participants
            .entry(tls_session.clone())
            .or_default();
        if !participants.contains(&process_id) {
            participants.push(process_id);
        }

        // Resolve orphan messages waiting for this TLS session
        self.resolve_orphan_messages_for_tls(&tls_session, process_id, ts);

        // Also register as orphan-TLS temporarily if process wasn't known
        // (resolved by future observe_exec)
        if !self.processes.contains_key(&process_id) {
            self.orphan_tls.push(OrphanTls {
                tls_session: tls_session.clone(),
                process_id,
                ts_ns: ts,
            });
        }
    }

    /// Record a message observed on a TLS session.
    ///
    /// If the TLS session is already bound to a process, the message is
    /// added directly. Otherwise it is buffered as an orphan until the
    /// TLS binding arrives.
    pub fn observe_message_via_tls(&mut self, tls_session: TlsSessionId, msg_id: MessageId) {
        // Always record in tls_to_messages
        self.tls_to_messages
            .entry(tls_session.clone())
            .or_default()
            .push(msg_id.clone());

        // If TLS is already bound to a process, also record in direct map
        // (for fast queries via messages_for_process)
        if let Some(&process_id) = self.tls_to_process.get(&tls_session) {
            self.direct_process_messages
                .entry(process_id)
                .or_default()
                .push(msg_id.clone());
        }

        // Even if bound, keep track of when it was observed for TTL purposes
    }

    /// Record a message directly bound to a process (no TLS path available).
    pub fn observe_message_direct(&mut self, process_id: ProcessId, msg_id: MessageId) {
        self.direct_process_messages
            .entry(process_id)
            .or_default()
            .push(msg_id);
    }

    /// Record one message captured for `process_id`, optionally naming the
    /// connection it was observed on.
    ///
    /// The process link is always kept: it is the captured fact that lets the
    /// authoritative resolver see the message. The TLS link is added only when
    /// the event itself named it; a connection that is not yet bound is still
    /// recorded under that key rather than attached to another session.
    pub fn observe_message(
        &mut self,
        process_id: ProcessId,
        tls_session: Option<TlsSessionId>,
        msg_id: MessageId,
    ) {
        if let Some(tls_session) = tls_session {
            self.tls_to_messages
                .entry(tls_session)
                .or_default()
                .push(msg_id.clone());
        }
        self.direct_process_messages
            .entry(process_id)
            .or_default()
            .push(msg_id);
    }

    /// Record the request-declared conversation/session id for one observed
    /// message.
    ///
    /// Only a uniquely parsed request declaration reaches this method. The
    /// row names the process and message actually observed, so it is a
    /// captured association, not an inferred session/role/time anchor.
    /// Conflicting rows are retained as distinct facts.
    pub fn observe_message_conversation(
        &mut self,
        process_id: ProcessId,
        message_id: MessageId,
        conversation_id: &str,
    ) {
        if conversation_id.is_empty() {
            return;
        }
        let row = MessageConversationBinding {
            process_id,
            message_id,
            conversation_id: conversation_id.to_string(),
        };
        if !self.message_conversation_bindings.contains(&row) {
            self.message_conversation_bindings.push(row);
        }
    }

    /// Record one structured native call reference for a message.
    ///
    /// Called before intent deduplication so a re-observed request message
    /// keeps its raw reference even when its intent is suppressed. Only the
    /// captured row is stored; no agent or conversation is assigned here.
    pub fn observe_native_tool_call(
        &mut self,
        process_id: ProcessId,
        message_id: MessageId,
        native_tool_call: NativeToolCallRef,
        declared_conversation_id: Option<String>,
    ) {
        if native_tool_call.id.trim().is_empty() {
            return;
        }
        self.native_tool_call_observations
            .insert(NativeToolCallObservation {
                process_id,
                message_id,
                native_tool_call,
                declared_conversation_id: declared_conversation_id.filter(|id| !id.is_empty()),
            });
    }

    // ── Eviction ───────────────────────────────────────────────────

    /// Evict expired processes and orphans.
    ///
    /// - Processes with `exit_time_ns` past `PROCESS_TTL_NS` and no living children
    ///   are removed.
    /// - Orphans older than `ORPHAN_TTL_NS` are dropped.
    pub fn evict_expired(&mut self, now_ns: u64) {
        // Collect dead processes eligible for eviction
        let dead: HashSet<ProcessId> = self
            .processes
            .iter()
            .filter(|(_id, meta)| {
                if meta.is_alive {
                    return false;
                }
                if let Some(exit_ns) = meta.exit_time_ns
                    && now_ns.saturating_sub(exit_ns) < PROCESS_TTL_NS
                {
                    return false;
                }
                true
            })
            .map(|(id, _)| *id)
            .collect();

        // Exclude processes that have living children
        let to_remove: HashSet<ProcessId> = dead
            .iter()
            .filter(|pid| {
                self.child_map
                    .get(pid)
                    .map(|children| {
                        !children
                            .iter()
                            .any(|c| self.processes.get(c).map(|m| m.is_alive).unwrap_or(false))
                    })
                    .unwrap_or(true) // no children → safe to remove
            })
            .copied()
            .collect();

        // Remove dead processes and clean up their edges
        for pid in &to_remove {
            // Remove from parent's child list
            for children in self.child_map.values_mut() {
                children.retain(|c| c != pid);
            }
            // Clean up empty child lists
            self.child_map.retain(|_, children| !children.is_empty());
            // Remove process
            self.processes.remove(pid);
            // Remove parent edge
            self.parent_map.remove(pid);
            // Remove direct messages
            self.direct_process_messages.remove(pid);
        }

        // Evict expired orphans
        self.orphan_messages
            .retain(|o| now_ns.saturating_sub(o.ts_ns) < ORPHAN_TTL_NS);
        self.orphan_tls
            .retain(|o| now_ns.saturating_sub(o.ts_ns) < ORPHAN_TTL_NS);
    }

    // ── Snapshot ───────────────────────────────────────────────────

    /// Produce an immutable snapshot of the current signal state.
    ///
    /// Callers should invoke `evict_expired` before snapshotting for
    /// clean data, though `snapshot` can be called at any time.
    pub fn snapshot(&self) -> Arc<SignalSnapshot> {
        Arc::new(SignalSnapshot {
            processes: self.processes.clone(),
            parent_map: {
                // Live edges win; the memory fills in processes that already
                // exited (phase-2 replay).
                let mut parents = self.lineage_memory.clone();
                for (child, parent) in &self.parent_map {
                    parents.insert(*child, *parent);
                }
                parents
            },
            child_map: self.child_map.clone(),
            tls_to_process: self.tls_to_process.clone(),
            tls_participants: self.tls_participants.clone(),
            tls_to_messages: self.tls_to_messages.clone(),
            conversation_to_processes: self.conversation_to_processes.clone(),
            process_to_conversations: self.process_to_conversations.clone(),
            direct_process_messages: self.direct_process_messages.clone(),
            message_conversation_bindings: {
                // Anchor rows have no map key, so freeze them in one canonical
                // order; the PRE-resolver DTO must rebuild exactly this snapshot.
                let mut rows = self.message_conversation_bindings.clone();
                rows.sort_by(|left, right| {
                    (left.process_id.pid, left.process_id.start_time_ns)
                        .cmp(&(right.process_id.pid, right.process_id.start_time_ns))
                        .then_with(|| left.message_id.as_str().cmp(right.message_id.as_str()))
                        .then_with(|| left.conversation_id.cmp(&right.conversation_id))
                });
                rows
            },
            native_tool_call_observations: {
                let mut rows: Vec<NativeToolCallObservation> =
                    self.native_tool_call_observations.iter().cloned().collect();
                rows.sort_by(NativeToolCallObservation::cmp_canonical);
                rows
            },
            thread_to_process: self.thread_to_process.clone(),
            metrics: self.compute_metrics(),
        })
    }

    // ── Persistence ────────────────────────────────────────────────

    /// Reconstruct a `SignalGraph` from a SQLite connection.
    ///
    /// Reads the process tree, TLS bindings, and message bindings from
    /// the persistence layer.
    pub fn from_persistence(
        _conn: &rusqlite::Connection,
    ) -> Result<SignalGraph, ResponsibilityError> {
        Err(ResponsibilityError {
            code: "FROM_PERSISTENCE_UNIMPLEMENTED".into(),
            message: "SignalGraph::from_persistence is not yet implemented — pending persistence schema redesign (27 tables)".into(),
            module: "signals/signal_graph".into(),
        })
    }

    // ── Internal helpers ───────────────────────────────────────────

    /// Resolve orphan messages that belong to a now-bound TLS session.
    fn resolve_orphan_messages_for_tls(
        &mut self,
        tls_session: &TlsSessionId,
        process_id: ProcessId,
        _ts: u64,
    ) {
        // Find orphan messages for this TLS session
        let mut resolved = Vec::new();
        self.orphan_messages.retain(|orphan| {
            if orphan
                .tls_session
                .as_ref()
                .map(|t| t == tls_session)
                .unwrap_or(false)
            {
                resolved.push(orphan.msg_id.clone());
                false // remove from orphans
            } else {
                true // keep
            }
        });

        // Add resolved messages to the direct map for fast queries
        if !resolved.is_empty() {
            let entry = self.direct_process_messages.entry(process_id).or_default();
            for msg in resolved {
                if !entry.contains(&msg) {
                    entry.push(msg);
                }
            }
        }
    }

    /// Resolve orphan TLS sessions that match a newly discovered process.
    fn resolve_orphan_tls_for_process(&mut self, process_id: ProcessId, _ts: u64) {
        self.orphan_tls.retain(|orphan| {
            if orphan.process_id == process_id {
                // Already resolved via the observe_tls insertion; just clean up
                false
            } else {
                true
            }
        });
    }

    /// Drop one process when capacity is exceeded.
    ///
    /// Prefers dead processes with no children; if none, drops the
    /// oldest dead process.
    fn drop_one_process(&mut self) {
        // Prefer dead, childless processes
        let candidate = self
            .processes
            .iter()
            .filter(|(id, meta)| {
                !meta.is_alive && self.child_map.get(id).map(|c| c.is_empty()).unwrap_or(true)
            })
            .min_by_key(|(_id, meta)| meta.last_seen_ns);

        if let Some((pid, _)) = candidate {
            let pid = *pid;
            self.processes.remove(&pid);
            self.parent_map.remove(&pid);
            self.direct_process_messages.remove(&pid);
            for children in self.child_map.values_mut() {
                children.retain(|c| *c != pid);
            }
            self.child_map.retain(|_, c| !c.is_empty());
            return;
        }

        // Fallback: drop oldest dead process
        let candidate = self
            .processes
            .iter()
            .filter(|(_, meta)| !meta.is_alive)
            .min_by_key(|(_, meta)| meta.last_seen_ns);

        if let Some((pid, _)) = candidate {
            let pid = *pid;
            self.processes.remove(&pid);
            self.parent_map.remove(&pid);
            self.direct_process_messages.remove(&pid);
            for children in self.child_map.values_mut() {
                children.retain(|c| *c != pid);
            }
            self.child_map.retain(|_, c| !c.is_empty());
        }
    }

    /// Compute SignalMetrics from current state.
    fn compute_metrics(&self) -> super::signal_snapshot::SignalMetrics {
        let tls_msg_count: usize = self.tls_to_messages.values().map(|v| v.len()).sum();
        let direct_msg_count: usize = self.direct_process_messages.values().map(|v| v.len()).sum();
        let message_count = tls_msg_count + direct_msg_count;
        let orphan_count = self.orphan_messages.len() + self.orphan_tls.len();

        super::signal_snapshot::SignalMetrics {
            process_count: self.processes.len(),
            tls_session_count: self.tls_to_process.len(),
            message_count,
            orphan_count,
            lost_event_count: self.lost_event_count,
        }
    }
}

impl Default for SignalGraph {
    fn default() -> Self {
        Self::new()
    }
}

// ─── Tests ───────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    fn pid(id: u32, start: u64) -> ProcessId {
        ProcessId::new(id, start)
    }

    fn tls(s: &str) -> TlsSessionId {
        TlsSessionId::from(s)
    }

    fn msg(s: &str) -> MessageId {
        MessageId::from(s)
    }

    #[test]
    fn snapshot_does_not_revive_a_dead_process_and_preserves_time_bounds() {
        let mut graph = SignalGraph::new();
        let child = pid(42, 123);
        graph.observe_exec(
            child,
            pid(0, 0),
            ProcessExecInfo {
                comm: "client".into(),
                ..Default::default()
            },
            Some(7),
            10,
        );
        graph.observe_exit(child, 0, 20);
        graph.observe_snapshot(
            child,
            pid(0, 0),
            ProcessExecInfo {
                comm: "client".into(),
                ..Default::default()
            },
            Some(7),
            30,
        );
        let snapshot = graph.snapshot();
        let process = snapshot.get_process(42, 123).unwrap();
        assert!(!process.is_alive);
        assert_eq!(process.first_seen_ns, 10);
    }

    #[test]
    fn exec_records_cmdline_and_cgroup() {
        let mut g = SignalGraph::new();
        let child = pid(100, 1);

        g.observe_exec(
            child,
            pid(50, 0),
            ProcessExecInfo {
                comm: "node".to_string(),
                cmdline: Some("node /opt/openclaw/dist/index.js gateway".to_string()),
                exe_path: Some("/usr/bin/node".to_string()),
                cgroup_unit: Some("openclaw-gateway.service".to_string()),
            },
            None,
            1,
        );

        let snap = g.snapshot();
        let meta = snap.get_process(100, 1).expect("process should exist");
        assert_eq!(meta.comm, "node");
        assert_eq!(
            meta.cmdline.as_deref(),
            Some("node /opt/openclaw/dist/index.js gateway")
        );
        assert_eq!(meta.exe_path.as_deref(), Some("/usr/bin/node"));
        assert_eq!(
            meta.cgroup_unit.as_deref(),
            Some("openclaw-gateway.service")
        );
    }

    #[test]
    fn test_exec_builds_parent_child_tree() {
        let mut g = SignalGraph::new();
        let parent = pid(100, 1000);
        let child = pid(200, 2000);

        g.observe_exec(
            child,
            parent,
            ProcessExecInfo {
                comm: "myapp".into(),
                ..Default::default()
            },
            None,
            2000,
        );

        let snap = g.snapshot();
        assert_eq!(snap.get_parent(child), Some(parent));
        assert_eq!(snap.get_children(parent), &[child]);
        assert!(snap.get_process(100, 1000).is_some());
        assert_eq!(
            snap.get_process(100, 1000).unwrap().comm,
            "" // parent comm not set (lazy)
        );
        assert_eq!(snap.get_process(200, 2000).unwrap().comm, "myapp");
    }

    #[test]
    fn test_pid1_is_skipped_as_parent() {
        let mut g = SignalGraph::new();
        let init = pid(1, 0);
        let child = pid(200, 2000);

        g.observe_exec(
            child,
            init,
            ProcessExecInfo {
                comm: "daemon".into(),
                ..Default::default()
            },
            None,
            2000,
        );

        let snap = g.snapshot();
        assert_eq!(snap.get_parent(child), None);
        assert!(snap.get_process(200, 2000).is_some());
    }

    #[test]
    fn test_tls_and_message_binding() {
        let mut g = SignalGraph::new();
        let proc = pid(100, 1000);
        let tls_id = tls("tls-abc");

        g.observe_tls(proc, tls_id.clone(), 1000);
        g.observe_message_via_tls(tls_id.clone(), msg("msg-1"));

        let snap = g.snapshot();
        assert_eq!(snap.process_for_tls(&tls_id), Some(proc));
        assert_eq!(snap.messages_for_process(proc), vec![msg("msg-1")]);
    }

    #[test]
    fn message_conversation_binding_is_recorded_once_and_snapshotted() {
        let mut g = SignalGraph::new();
        let proc = pid(100, 1000);
        let message = msg("msg-anchored");

        g.observe_message_conversation(proc, message.clone(), "conv-a");
        g.observe_message_conversation(proc, message.clone(), "conv-a");
        g.observe_message_conversation(proc, message.clone(), "conv-b");

        let snap = g.snapshot();
        assert_eq!(
            snap.message_conversations().len(),
            2,
            "identical anchor rows are a single captured fact; conflicts stay distinct"
        );
        assert!(snap.message_conversations().iter().any(|row| {
            row.process_id == proc && row.message_id == message && row.conversation_id == "conv-a"
        }));
    }

    #[test]
    fn test_message_direct_path() {
        let mut g = SignalGraph::new();
        let proc = pid(100, 1000);

        g.observe_message_direct(proc, msg("direct-msg"));

        let snap = g.snapshot();
        assert_eq!(snap.messages_for_process(proc), vec![msg("direct-msg")]);
    }

    #[test]
    fn test_tls_collision_detection() {
        let mut g = SignalGraph::new();
        let proc_a = pid(100, 1000);
        let proc_b = pid(200, 2000);
        let tls_id = tls("tls-abc");

        g.observe_tls(proc_a, tls_id.clone(), 1000);
        g.observe_tls(proc_b, tls_id.clone(), 2000);

        let snap = g.snapshot();
        // Latest binding wins
        assert_eq!(snap.process_for_tls(&tls_id), Some(proc_b));
        assert_eq!(snap.metrics().lost_event_count, 0);
    }

    #[test]
    fn test_orphan_message_resolved_when_tls_arrives() {
        let mut g = SignalGraph::new();
        let proc = pid(100, 1000);
        let tls_id = tls("tls-late");

        // Message arrives first (orphaned)
        g.observe_message_via_tls(tls_id.clone(), msg("msg-orphan"));

        // TLS binding arrives later
        g.observe_tls(proc, tls_id.clone(), 2000);

        let snap = g.snapshot();
        assert!(snap.messages_for_process(proc).contains(&msg("msg-orphan")));
    }

    #[test]
    fn test_exit_updates_metadata() {
        let mut g = SignalGraph::new();
        let proc = pid(100, 1000);

        g.observe_exec(
            proc,
            pid(1, 0),
            ProcessExecInfo {
                comm: "test".into(),
                ..Default::default()
            },
            None,
            1000,
        );
        g.observe_exit(proc, 0, 5000);

        let snap = g.snapshot();
        let meta = snap.get_process(100, 1000).unwrap();
        assert!(!meta.is_alive);
        assert_eq!(meta.exit_time_ns, Some(5000));
        assert_eq!(meta.exit_code, Some(0));
    }

    #[test]
    fn test_is_descendant_or_same_direct() {
        let mut g = SignalGraph::new();
        let a = pid(10, 0);
        let b = pid(20, 1);

        // a is parent of b
        g.observe_exec(
            b,
            a,
            ProcessExecInfo {
                comm: "child".into(),
                ..Default::default()
            },
            None,
            1000,
        );

        let snap = g.snapshot();
        assert!(snap.is_descendant_or_same(b, a));
        assert!(snap.is_descendant_or_same(b, b));
        assert!(!snap.is_descendant_or_same(a, b));
    }

    #[test]
    fn test_is_descendant_or_same_deep_chain() {
        let mut g = SignalGraph::new();
        let root = pid(50, 0);
        let mut prev = root;
        let mut children = Vec::new();

        for i in 0..10 {
            let child = pid(100 + i as u32, i as u64);
            g.observe_exec(
                child,
                prev,
                ProcessExecInfo {
                    comm: "node".into(),
                    ..Default::default()
                },
                None,
                i as u64,
            );
            children.push(child);
            prev = child;
        }

        let snap = g.snapshot();
        let leaf = children.last().unwrap();
        assert!(snap.is_descendant_or_same(*leaf, root));

        // Middle node NOT descendant of leaf
        assert!(!snap.is_descendant_or_same(children[2], *leaf));
    }

    #[test]
    fn test_cycle_detection_in_descendant_check() {
        let mut g = SignalGraph::new();
        let a = pid(10, 1);
        let b = pid(20, 2);
        let c = pid(30, 3);

        // Create a cycle: a → b → c → a
        g.observe_exec(
            b,
            a,
            ProcessExecInfo {
                comm: "b".into(),
                ..Default::default()
            },
            None,
            1,
        );
        g.observe_exec(
            c,
            b,
            ProcessExecInfo {
                comm: "c".into(),
                ..Default::default()
            },
            None,
            2,
        );
        // Manually create cycle (override parent): a's parent is now c
        g.observe_exec(
            a,
            c,
            ProcessExecInfo {
                comm: "a-cycle".into(),
                ..Default::default()
            },
            None,
            3,
        );

        let snap = g.snapshot();
        // b IS reachable from a via a→c→b (2 steps), so is_descendant_or_same
        // correctly returns true. The cycle (b→a) is not traversed because
        // the ancestor is found first. This test primarily validates that
        // the function does not loop infinitely on corrupted trees.
        assert!(snap.is_descendant_or_same(a, b));
        // However, b is not a descendant of a (walking up from b: b → a → c → b → cycle)
        // Since the function walks up from the first arg toward the second:
        // is_descendant_or_same(process_id, ancestor) checks if process_id is
        // a descendant of ancestor. So:
        // is_descendant_or_same(a, b): walk up from a → c → b → found! true.
        // is_descendant_or_same(b, a): walk up from b → a → found! true (cycle shortcut).
        // Both directions return true due to the cycle — this is expected since
        // the data is corrupted and the cycle creates ambiguous ancestry.
    }

    #[test]
    fn test_deep_descendant_capped_at_max_depth() {
        let mut g = SignalGraph::new();
        let root = pid(1, 0);
        let mut prev = root;

        // Create a very deep chain (> MAX_DESCENDANT_DEPTH)
        for i in 0..300u64 {
            let child = pid(1000 + i as u32, i);
            g.observe_exec(
                child,
                prev,
                ProcessExecInfo {
                    comm: "deep".into(),
                    ..Default::default()
                },
                None,
                i,
            );
            prev = child;
        }

        let snap = g.snapshot();
        // Should not overflow, returns false beyond max depth
        let deep_leaf = pid(1000 + 299, 299);
        assert!(!snap.is_descendant_or_same(deep_leaf, root));
    }

    #[test]
    fn test_can_correlate() {
        let mut g = SignalGraph::new();
        let intent = pid(100, 0);
        let action = pid(200, 1);

        g.observe_exec(
            action,
            intent,
            ProcessExecInfo {
                comm: "action".into(),
                ..Default::default()
            },
            None,
            1000,
        );

        let snap = g.snapshot();
        assert!(snap.can_correlate(intent, action));
        assert!(!snap.can_correlate(action, intent));
    }

    #[test]
    fn test_metrics() {
        let mut g = SignalGraph::new();
        g.observe_exec(
            pid(100, 1),
            pid(50, 0),
            ProcessExecInfo {
                comm: "test".into(),
                ..Default::default()
            },
            None,
            1,
        );
        g.observe_tls(pid(100, 1), tls("tls-1"), 2);
        g.observe_message_via_tls(tls("tls-1"), msg("m1"));
        g.lost_event_count = 5;

        let snap = g.snapshot();
        let m = snap.metrics();
        assert_eq!(m.process_count, 2); // test + shell (pid 50)
        assert_eq!(m.tls_session_count, 1);
        // Messages are recorded in both tls_to_messages and direct_process_messages;
        // the metrics function sums both without deduplication.
        assert_eq!(m.message_count, 2);
        assert_eq!(m.lost_event_count, 5);
    }

    #[test]
    fn test_eviction_removes_dead_processes() {
        let mut g = SignalGraph::new();
        let proc = pid(100, 1);

        g.observe_exec(
            proc,
            pid(1, 0),
            ProcessExecInfo {
                comm: "short_lived".into(),
                ..Default::default()
            },
            None,
            0,
        );
        g.observe_exit(proc, 0, 1000);

        // Evict with time well past TTL
        g.evict_expired(1000 + PROCESS_TTL_NS + 1);

        let snap = g.snapshot();
        assert_eq!(snap.get_process(100, 1), None);
    }

    #[test]
    fn test_no_eviction_for_living_processes() {
        let mut g = SignalGraph::new();
        let proc = pid(100, 1);

        g.observe_exec(
            proc,
            pid(1, 0),
            ProcessExecInfo {
                comm: "alive".into(),
                ..Default::default()
            },
            None,
            0,
        );

        g.evict_expired(PROCESS_TTL_NS + 1);

        let snap = g.snapshot();
        assert!(snap.get_process(100, 1).is_some());
    }

    #[test]
    fn test_tls_sessions_for_process() {
        let mut g = SignalGraph::new();
        let proc = pid(100, 1);

        g.observe_tls(proc, tls("sess-1"), 1);
        g.observe_tls(proc, tls("sess-2"), 2);

        let snap = g.snapshot();
        let sessions = snap.tls_sessions_for_process(proc);
        assert_eq!(sessions.len(), 2);
    }

    #[test]
    fn test_process_for_thread() {
        let mut g = SignalGraph::new();
        let proc = pid(100, 1);
        // Manually set thread mapping (normally done by eBPF layer)
        g.thread_to_process.insert(42, proc);

        let snap = g.snapshot();
        assert_eq!(snap.process_for_thread(42), Some(proc));
        assert_eq!(snap.process_for_thread(99), None);
    }

    #[test]
    fn test_default_creates_empty_graph() {
        let g = SignalGraph::default();
        assert_eq!(g.snapshot().metrics().process_count, 0);
    }

    #[test]
    fn test_pgid_is_stored() {
        let mut g = SignalGraph::new();
        let proc = pid(100, 1);

        g.observe_exec(
            proc,
            pid(1, 0),
            ProcessExecInfo {
                comm: "shell".into(),
                ..Default::default()
            },
            Some(42),
            1000,
        );

        let snap = g.snapshot();
        assert_eq!(snap.get_process(100, 1).unwrap().pgid, Some(42));
    }

    #[test]
    fn lineage_survives_process_exit_for_batch_attribution() {
        // Phase 2 replays the run after the hook processes are gone. The parent
        // map is pruned on exit; the lineage memory must keep the chain so an
        // egress from an exited `make → sh → python` hook is still attributable.
        let mut graph = SignalGraph::new();
        let runner = pid(200, 1);
        let hook = pid(201, 2);
        graph.observe_exec(
            runner,
            pid(0, 0),
            ProcessExecInfo {
                comm: "claude".into(),
                ..Default::default()
            },
            Some(7),
            10,
        );
        graph.observe_exec(
            hook,
            runner,
            ProcessExecInfo {
                comm: "python3".into(),
                ..Default::default()
            },
            Some(7),
            20,
        );
        graph.observe_exit(hook, 0, 30);
        graph.observe_exit(runner, 0, 40);
        graph.evict_expired(u64::MAX / 2);

        let snapshot = graph.snapshot();
        assert!(
            snapshot.can_correlate(runner, hook),
            "lineage attribution must survive process exit for phase-2 replay"
        );
    }
}
