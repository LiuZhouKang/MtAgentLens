// SPDX-License-Identifier: Apache-2.0 OR MIT
// Innovation B2: Team-Scale Responsibility Propagation — Delegation edge tracking.
//
// Detects cross-agent delegation by monitoring process parent-child
// relationships between different agents. When Agent A spawns a process
// that becomes Agent B, a delegation edge is created.

use serde::{Deserialize, Serialize};
use std::collections::{HashMap, HashSet};
use std::time::{Duration, Instant};

/// Maximum entries in process_origins before oldest entries are evicted.
const MAX_PROCESS_ORIGINS: usize = 4096;

/// TTL for unclaimed process origins (30 seconds).
const PROCESS_ORIGIN_TTL: Duration = Duration::from_secs(30);

/// A delegation relationship between two agents via process spawning.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
/// An observed process-spawn handoff between two agents.
///
/// Named `Observation` to distinguish it from the persisted
/// `types::policy_types::DelegationRecord` (typed IDs, no comm); the runtime
/// converts one into the other when writing the delegation table.
pub struct DelegationObservation {
    pub delegation_id: String,
    pub from_agent_id: String,
    pub to_agent_id: String,
    pub parent_pid: u32,
    pub parent_start_time_ns: u64,
    pub child_pid: u32,
    pub child_start_time_ns: u64,
    pub timestamp_ns: u64,
    pub child_comm: String,
    pub confidence: f32,
}

struct ProcessOrigin {
    parent_pid: u32,
    parent_start_time_ns: u64,
    parent_agent_id: String,
    timestamp_ns: u64,
    child_start_time_ns: u64,
    child_comm: String,
    recorded_at: Instant,
}

/// One observed process spawn.
///
/// Grouped instead of passed as seven positional arguments: two PIDs, two
/// start times, an agent id, a timestamp and a comm are easy to transpose.
pub struct SpawnObservation<'a> {
    pub parent_pid: u32,
    pub parent_start_time_ns: u64,
    pub child_pid: u32,
    pub child_start_time_ns: u64,
    pub parent_agent_id: &'a str,
    pub timestamp_ns: u64,
    pub child_comm: &'a str,
}

/// Tracks process parent-child relationships to detect delegation between agents.
///
/// When a known agent process spawns a child that is later resolved to a different
/// agent, a delegation edge is emitted. This captures subagent, agent-teams, and
/// other multi-agent work-sharing patterns.
pub struct DelegationTracker {
    /// pid → origin info (capped with TTL eviction to prevent memory leak).
    process_origins: HashMap<u32, ProcessOrigin>,
    /// agent_id → set of delegated agent_ids (O(1) dedup via HashSet).
    known_delegations: HashMap<String, HashSet<String>>,
}

impl Default for DelegationTracker {
    fn default() -> Self {
        Self::new()
    }
}

impl DelegationTracker {
    pub fn new() -> Self {
        Self {
            process_origins: HashMap::new(),
            known_delegations: HashMap::new(),
        }
    }

    /// Record a process spawn: a parent pid created a child pid.
    /// `parent_agent_id` is the agent responsible for the parent process.
    pub fn record_spawn(&mut self, spawn: SpawnObservation<'_>) {
        let SpawnObservation {
            parent_pid,
            parent_start_time_ns,
            child_pid,
            child_start_time_ns,
            parent_agent_id,
            timestamp_ns,
            child_comm,
        } = spawn;
        // Evict expired entries before inserting new ones.
        self.evict_expired();

        // Cap the map size: evict oldest entries if at capacity.
        if self.process_origins.len() >= MAX_PROCESS_ORIGINS {
            // Find and remove the oldest entry.
            let oldest = self
                .process_origins
                .iter()
                .min_by_key(|(_, o)| o.recorded_at)
                .map(|(k, _)| *k);
            if let Some(old_key) = oldest {
                self.process_origins.remove(&old_key);
            }
        }

        self.process_origins.insert(
            child_pid,
            ProcessOrigin {
                parent_pid,
                parent_start_time_ns,
                parent_agent_id: parent_agent_id.to_string(),
                timestamp_ns,
                child_start_time_ns,
                child_comm: child_comm.to_string(),
                recorded_at: Instant::now(),
            },
        );
    }

    /// Check if a process was spawned by a different agent.
    /// Returns a DelegationRecord if `child_agent_id` differs from the parent agent.
    pub fn try_delegation(
        &mut self,
        child_pid: u32,
        child_agent_id: &str,
    ) -> Option<DelegationObservation> {
        let origin = self.process_origins.remove(&child_pid)?;

        // Only an agent process can be a delegation target. A tool child
        // (curl, git, python) that received a TLS event before its EXEC was
        // resolved can acquire a distinct provisional agent id; recording it
        // as a delegated agent produced false `DelegationRecord`s and false
        // "verified via process-spawn" boundaries.
        if !crate::responsibility::identity::union_find::is_agent_comm(&origin.child_comm) {
            return None;
        }

        // Self-delegation (same agent) is not a delegation.
        if origin.parent_agent_id == child_agent_id {
            return None;
        }

        // O(1) dedup via HashSet instead of O(n) Vec scan.
        let seen = self
            .known_delegations
            .entry(origin.parent_agent_id.clone())
            .or_default();
        if !seen.insert(child_agent_id.to_string()) {
            return None; // Already recorded this delegation pair.
        }

        Some(DelegationObservation {
            delegation_id: format!(
                "deleg-{}-{}-{}",
                origin.parent_agent_id, child_agent_id, origin.timestamp_ns
            ),
            from_agent_id: origin.parent_agent_id,
            to_agent_id: child_agent_id.to_string(),
            parent_pid: origin.parent_pid,
            parent_start_time_ns: origin.parent_start_time_ns,
            child_pid,
            child_start_time_ns: origin.child_start_time_ns,
            timestamp_ns: origin.timestamp_ns,
            child_comm: origin.child_comm,
            confidence: 0.85,
        })
    }

    /// Check all pending spawns for a given agent assignment.
    pub fn drain_delegations(
        &mut self,
        pid_agent_map: &HashMap<u32, String>,
    ) -> Vec<DelegationObservation> {
        let pids: Vec<u32> = self.process_origins.keys().copied().collect();
        let mut records = Vec::new();
        for child_pid in pids {
            if let Some(child_agent_id) = pid_agent_map.get(&child_pid) {
                let child_agent_id = child_agent_id.clone();
                if let Some(record) = self.try_delegation(child_pid, &child_agent_id) {
                    records.push(record);
                }
            }
        }
        // Bound `known_delegations` by agent-pair count rather than by pending
        // origins. The previous prune removed a pair as soon as its spawn
        // origin was consumed, so the same delegation was re-emitted on every
        // later spawn and the graph accumulated duplicate edges.
        self.cap_known_delegations();
        records
    }

    /// Remove expired process origins (TTL-based eviction).
    fn evict_expired(&mut self) {
        let now = Instant::now();
        self.process_origins
            .retain(|_, origin| now.duration_since(origin.recorded_at) < PROCESS_ORIGIN_TTL);
    }

    /// Keep the dedup table bounded without reopening consumed pairs.
    fn cap_known_delegations(&mut self) {
        const MAX_KNOWN_DELEGATION_PAIRS: usize = 4096;
        let total: usize = self.known_delegations.values().map(HashSet::len).sum();
        if total > MAX_KNOWN_DELEGATION_PAIRS {
            // The cap is far above any real agent population; clearing is the
            // only bounded option and is preferable to unbounded growth.
            self.known_delegations.clear();
        }
    }

    /// Number of pending process origins (for diagnostics).
    pub fn pending_count(&self) -> usize {
        self.process_origins.len()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn spawn<'a>(
        parent_pid: u32,
        parent_start_time_ns: u64,
        child_pid: u32,
        child_start_time_ns: u64,
        parent_agent_id: &'a str,
        timestamp_ns: u64,
        child_comm: &'a str,
    ) -> SpawnObservation<'a> {
        SpawnObservation {
            parent_pid,
            parent_start_time_ns,
            child_pid,
            child_start_time_ns,
            parent_agent_id,
            timestamp_ns,
            child_comm,
        }
    }

    #[test]
    fn detects_cross_agent_delegation() {
        let mut tracker = DelegationTracker::new();
        tracker.record_spawn(spawn(100, 11, 200, 22, "agent-alpha", 1000, "claude"));
        let record = tracker.try_delegation(200, "agent-beta");
        assert!(record.is_some());
        let r = record.unwrap();
        assert_eq!(r.from_agent_id, "agent-alpha");
        assert_eq!(r.to_agent_id, "agent-beta");
        assert_eq!(r.child_pid, 200);
    }

    #[test]
    fn ignores_same_agent_spawn() {
        let mut tracker = DelegationTracker::new();
        tracker.record_spawn(spawn(100, 11, 200, 22, "agent-alpha", 1000, "bash"));
        let record = tracker.try_delegation(200, "agent-alpha");
        assert!(record.is_none());
    }

    /// A tool child can transiently resolve to a different agent id (TLS
    /// arrives before EXEC). Only an agent comm counts as a delegated agent.
    #[test]
    fn ignores_a_non_agent_child() {
        let mut tracker = DelegationTracker::new();
        tracker.record_spawn(spawn(100, 11, 200, 22, "agent-alpha", 1000, "curl"));
        assert!(
            tracker.try_delegation(200, "agent-beta").is_none(),
            "curl must not become a delegated agent"
        );
    }

    #[test]
    fn deduplicates_repeated_delegation() {
        let mut tracker = DelegationTracker::new();
        tracker.record_spawn(spawn(100, 11, 200, 22, "agent-a", 1000, "claude"));
        let r1 = tracker.try_delegation(200, "agent-b");
        assert!(r1.is_some());
        // Second check with same pair should not produce another record.
        tracker.record_spawn(spawn(100, 11, 200, 22, "agent-a", 2000, "claude"));
        let r2 = tracker.try_delegation(200, "agent-b");
        assert!(r2.is_none());
    }

    /// The previous pruning removed the dedup entry as soon as the spawn
    /// origin was consumed, so a later spawn re-emitted the same pair.
    #[test]
    fn drain_does_not_reeemit_a_consumed_pair() {
        use std::collections::HashMap;

        let mut tracker = DelegationTracker::new();
        tracker.record_spawn(spawn(100, 11, 200, 22, "agent-a", 1000, "claude"));
        let map = HashMap::from([(200u32, "agent-b".to_string())]);
        assert_eq!(tracker.drain_delegations(&map).len(), 1);

        tracker.record_spawn(spawn(100, 11, 200, 22, "agent-a", 2000, "claude"));
        assert!(
            tracker.drain_delegations(&map).is_empty(),
            "the same (from, to) pair must not be emitted twice"
        );
    }

    #[test]
    fn drains_pending_delegations() {
        let mut tracker = DelegationTracker::new();
        tracker.record_spawn(spawn(100, 11, 200, 22, "agent-a", 1000, "claude"));
        tracker.record_spawn(spawn(100, 11, 300, 33, "agent-a", 2000, "claude"));

        let mut pid_map = HashMap::new();
        pid_map.insert(200, "agent-b".to_string());
        pid_map.insert(300, "agent-c".to_string());

        let records = tracker.drain_delegations(&pid_map);
        assert_eq!(records.len(), 2);
    }

    #[test]
    fn size_cap_evicts_oldest() {
        let mut tracker = DelegationTracker::new();
        // Fill past MAX_PROCESS_ORIGINS (4096)
        for i in 0..MAX_PROCESS_ORIGINS + 100 {
            tracker.record_spawn(spawn(
                100,
                11,
                (1000 + i) as u32,
                (2000 + i) as u64,
                "agent-a",
                i as u64,
                "bash",
            ));
        }
        // Should not panic and should be capped at or below MAX_PROCESS_ORIGINS
        assert!(tracker.pending_count() <= MAX_PROCESS_ORIGINS);
    }
}
