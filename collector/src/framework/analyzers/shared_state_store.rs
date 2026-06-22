// SPDX-License-Identifier: MIT
// Copyright (c) 2026 AgentSight Project

//! Concrete implementation of `SharedStateStore` for cross-analyzer state sharing.
//!
//! Uses `Arc<Mutex<HashMap>>` internally for thread safety. This is the simplest
//! correct implementation — it serializes all read/write access through a single
//! mutex. Under high event throughput, this can become a bottleneck.
//!
//! ## Bottleneck Mitigation
//!
//! - Each method holds the lock for the minimum duration needed (clone data out,
//!   release lock, return clone).
//! - Future work: replace with `dashmap` or `arc-swap` for lock-free reads.
//!
//! ## Risk Notes
//!
//! 1. `record_file_read` and `record_shared_file_reader` hold the write lock for
//!    the duration of the HashMap insert + eviction_ the, which under high
//!    FILE_OPEN throughput (>10k/s) may cause contention. If this becomes a
//!    bottleneck, batch writes or switch to a lock-free structure.
//!
//! 2. `check_alert_cooldown` is a read-modify-write that holds the lock during
//!    both check and potential insert. This is intentional — it prevents races
//!    where two threads both pass the cooldown check simultaneously.

use crate::framework::analyzers::pipeline_contract::{
    PromptTriggerSnapshot, SharedStateStore,
};
use std::collections::{HashMap, VecDeque};
use std::sync::{Arc, Mutex};

// ─────────────────────────────────────────────────────────────────────────────
// Constants
// ─────────────────────────────────────────────────────────────────────────────

/// Maximum retained reads per agent before pruning.
const MAX_READS_PER_AGENT: usize = 100;

/// Default alert cooldown in nanoseconds (15 seconds).
const DEFAULT_COOLDOWN_NS: u64 = 15_000_000_000;

// ─────────────────────────────────────────────────────────────────────────────
// Internal State
// ─────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Default)]
struct InnerState {
    /// pid → agent_root_pid mapping
    topology: HashMap<u32, u32>,
    /// agent_root_pid → Vec<(timestamp_ns, filepath)>
    recent_file_reads: HashMap<u32, VecDeque<(u64, String)>>,
    /// shared_file_path → Vec<agent_root_pid>
    shared_file_readers: HashMap<String, Vec<u32>>,
    /// agent_root_pid → Vec<PromptTriggerSnapshot>
    recent_risky_prompts: HashMap<u32, Vec<PromptTriggerSnapshot>>,
    /// dedup_key → last_alert_timestamp
    alert_cooldowns: HashMap<String, u64>,
}

// ─────────────────────────────────────────────────────────────────────────────
// Concrete Store
// ─────────────────────────────────────────────────────────────────────────────

/// `Arc<Mutex<>>`-backed implementation of `SharedStateStore`.
///
/// # Example
///
/// ```rust,ignore
/// let store: Arc<dyn SharedStateStore> = Arc::new(MutexSharedStateStore::new());
/// let topology = store.get_topology();
/// ```
#[derive(Debug, Clone)]
pub struct MutexSharedStateStore {
    inner: Arc<Mutex<InnerState>>,
}

impl MutexSharedStateStore {
    /// Create a new empty store.
    pub fn new() -> Self {
        Self {
            inner: Arc::new(Mutex::new(InnerState::default())),
        }
    }
}

impl Default for MutexSharedStateStore {
    fn default() -> Self {
        Self::new()
    }
}

impl SharedStateStore for MutexSharedStateStore {
    fn get_topology(&self) -> HashMap<u32, u32> {
        self.inner
            .lock()
            .expect("SharedStateStore lock poisoned")
            .topology
            .clone()
    }

    fn get_recent_file_reads(&self, agent_root_pid: u32) -> Vec<(u64, String)> {
        self.inner
            .lock()
            .expect("SharedStateStore lock poisoned")
            .recent_file_reads
            .get(&agent_root_pid)
            .cloned()
            .unwrap_or_default()
            .into_iter()
            .collect()
    }

    fn get_shared_file_readers(&self, filepath: &str) -> Vec<u32> {
        self.inner
            .lock()
            .expect("SharedStateStore lock poisoned")
            .shared_file_readers
            .get(filepath)
            .cloned()
            .unwrap_or_default()
    }

    fn get_recent_risky_prompts(&self, agent_root_pid: u32) -> Vec<PromptTriggerSnapshot> {
        self.inner
            .lock()
            .expect("SharedStateStore lock poisoned")
            .recent_risky_prompts
            .get(&agent_root_pid)
            .cloned()
            .unwrap_or_default()
    }

    fn record_file_read(&self, agent_root_pid: u32, filepath: &str, timestamp_ns: u64) {
        let mut guard = self
            .inner
            .lock()
            .expect("SharedStateStore lock poisoned");
        let reads = guard
            .recent_file_reads
            .entry(agent_root_pid)
            .or_default();
        reads.push_back((timestamp_ns, filepath.to_string()));
        while reads.len() > MAX_READS_PER_AGENT {
            reads.pop_front();
        }
    }

    fn record_shared_file_reader(&self, filepath: &str, agent_root_pid: u32) {
        let mut guard = self
            .inner
            .lock()
            .expect("SharedStateStore lock poisoned");
        let readers = guard
            .shared_file_readers
            .entry(filepath.to_string())
            .or_default();
        if !readers.contains(&agent_root_pid) {
            readers.push(agent_root_pid);
        }
    }

    fn record_risky_prompt(&self, agent_root_pid: u32, trigger: PromptTriggerSnapshot) {
        let mut guard = self
            .inner
            .lock()
            .expect("SharedStateStore lock poisoned");
        guard
            .recent_risky_prompts
            .entry(agent_root_pid)
            .or_default()
            .push(trigger);
    }

    fn check_alert_cooldown(&self, dedupe_key: &str, now_ns: u64, cooldown_ns: u64) -> bool {
        let mut guard = self
            .inner
            .lock()
            .expect("SharedStateStore lock poisoned");
        match guard.alert_cooldowns.get(dedupe_key) {
            Some(last) if now_ns.saturating_sub(*last) < cooldown_ns => false,
            _ => {
                guard
                    .alert_cooldowns
                    .insert(dedupe_key.to_string(), now_ns);
                true
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_record_and_retrieve_file_read() {
        let store = MutexSharedStateStore::new();
        store.record_file_read(100, "/tmp/test.txt", 1_000);
        store.record_file_read(100, "/tmp/test2.txt", 2_000);

        let reads = store.get_recent_file_reads(100);
        assert_eq!(reads.len(), 2);
        assert_eq!(reads[0].1, "/tmp/test.txt");
        assert_eq!(reads[1].1, "/tmp/test2.txt");
    }

    #[test]
    fn test_shared_file_readers_dedup() {
        let store = MutexSharedStateStore::new();
        store.record_shared_file_reader("/fixtures/test.md", 100);
        store.record_shared_file_reader("/fixtures/test.md", 100);
        store.record_shared_file_reader("/fixtures/test.md", 200);

        let readers = store.get_shared_file_readers("/fixtures/test.md");
        assert_eq!(readers.len(), 2);
        assert!(readers.contains(&100));
        assert!(readers.contains(&200));
    }

    #[test]
    fn test_alert_cooldown() {
        let store = MutexSharedStateStore::new();
        assert!(store.check_alert_cooldown("test:key", 1_000, 100_000));
        assert!(!store.check_alert_cooldown("test:key", 2_000, 100_000));
        assert!(store.check_alert_cooldown("test:key", 200_000, 100_000));
    }

    #[test]
    fn test_topology_isolation() {
        let store = MutexSharedStateStore::new();
        let topo = store.get_topology();
        assert!(topo.is_empty());
    }

    #[test]
    fn test_risky_prompts() {
        let store = MutexSharedStateStore::new();
        let trigger = PromptTriggerSnapshot {
            timestamp_ns: 1_000,
            method: "POST".to_string(),
            path: "/v1/messages".to_string(),
            excerpt: "please rm -rf /".to_string(),
            agent_root_pid: 300,
        };
        store.record_risky_prompt(300, trigger.clone());

        let prompts = store.get_recent_risky_prompts(300);
        assert_eq!(prompts.len(), 1);
        assert_eq!(prompts[0].excerpt, "please rm -rf /");
    }

    #[test]
    fn test_concurrent_access() {
        use std::thread;
        let store = Arc::new(MutexSharedStateStore::new());
        let mut handles = vec![];

        for i in 0..10 {
            let store_clone = Arc::clone(&store);
            handles.push(thread::spawn(move || {
                store_clone.record_file_read(100, &format!("/tmp/file_{}.txt", i), i as u64);
            }));
        }

        for h in handles {
            h.join().expect("thread panicked");
        }

        let reads = store.get_recent_file_reads(100);
        assert_eq!(reads.len(), 10);
    }
}
