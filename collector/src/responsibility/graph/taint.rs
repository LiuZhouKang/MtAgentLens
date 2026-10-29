// SPDX-License-Identifier: Apache-2.0 OR MIT
//
// taint.rs — TaintTracker: cross-agent contamination propagation tracking.
//
// Tracks which artifacts (files) have been contaminated by an agent and
// detects when a different agent accesses a contaminated artifact. Supports
// propagation of contamination status between agents (e.g., when an agent
// reads a contaminated file, it becomes contaminated itself).

use crate::responsibility::types::AgentId;
use crate::responsibility::types::ids::ProcessId;
use serde::{Deserialize, Serialize};
use std::collections::{HashMap, HashSet};

// ─── TaintRecord ───────────────────────────────────────────────────────────

/// A record of a contaminated artifact.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TaintRecord {
    /// The agent that contaminated this artifact.
    pub source_agent: AgentId,
    /// The OS process that performed the observed write, when known.
    ///
    /// Phase 1 mints one provisional AgentId per process when TLS/ancestor
    /// evidence is missing. Without the process, two IDs of the same process
    /// look like two agents and a read of one's own file becomes a false
    /// cross-agent taint.
    #[serde(default)]
    pub source_process: Option<ProcessId>,
    /// Canonical path of the contaminated artifact.
    pub artifact: String,
    /// When the contamination was first detected.
    pub timestamp_ns: u64,
    /// Supporting evidence references.
    pub evidence_refs: Vec<String>,
    /// Number of times this artifact has been accessed by other agents.
    pub access_count: u64,
}

// ─── TaintAccess ───────────────────────────────────────────────────────────

/// Result of checking whether an agent accesses a contaminated artifact.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TaintAccess {
    /// The agent that originally contaminated the artifact.
    pub contaminated_by: AgentId,
    /// The contaminated artifact path.
    pub artifact: String,
    /// When the contamination was first recorded.
    pub first_contaminated_at_ns: u64,
    /// When this access was detected.
    pub accessed_at_ns: u64,
    /// Time delta between contamination and access (ns).
    pub propagation_dt_ns: u64,
}

// ─── TaintTracker ──────────────────────────────────────────────────────────

/// Tracks cross-agent artifact contamination and propagation.
///
/// When Agent A writes a file and Agent B reads it within the propagation
/// window, the file is marked as contaminated. Any subsequent access by a
/// third agent triggers a taint alert.
pub struct TaintTracker {
    /// Artifact path → contamination record.
    contaminated_artifacts: HashMap<String, TaintRecord>,
    /// Set of agents known to be contaminated.
    contaminated_agents: HashSet<AgentId>,
    /// Agent → set of agents it has propagated contamination to.
    propagation_edges: HashMap<AgentId, HashSet<AgentId>>,
    /// Maximum number of artifacts to track (evicts oldest when exceeded).
    max_artifacts: usize,
    /// Insertion order for FIFO eviction.
    insertion_order: Vec<String>,
}

impl TaintTracker {
    /// Create a new TaintTracker with default capacity.
    pub fn new() -> Self {
        Self {
            contaminated_artifacts: HashMap::new(),
            contaminated_agents: HashSet::new(),
            propagation_edges: HashMap::new(),
            max_artifacts: 4096,
            insertion_order: Vec::new(),
        }
    }

    /// Create a TaintTracker with a custom artifact capacity.
    pub fn with_capacity(max_artifacts: usize) -> Self {
        Self {
            max_artifacts,
            ..Self::new()
        }
    }

    /// Rewrite identity references together with graph and delegation state.
    pub fn remap_agents(&mut self, mapping: &HashMap<AgentId, AgentId>) {
        for record in self.contaminated_artifacts.values_mut() {
            if let Some(agent) = mapping.get(&record.source_agent) {
                record.source_agent = agent.clone();
            }
        }
        self.contaminated_agents = self
            .contaminated_agents
            .drain()
            .map(|agent| mapping.get(&agent).unwrap_or(&agent).clone())
            .collect();
        let previous = std::mem::take(&mut self.propagation_edges);
        for (source, targets) in previous {
            let canonical = mapping.get(&source).unwrap_or(&source).clone();
            let entry = self.propagation_edges.entry(canonical).or_default();
            entry.extend(
                targets
                    .into_iter()
                    .map(|agent| mapping.get(&agent).unwrap_or(&agent).clone()),
            );
        }
    }

    // ── Core API (per spec §10.4) ──────────────────────────────────────

    /// Mark an artifact as contaminated by a given agent at a given time.
    ///
    /// If the artifact is already tracked, the record is updated with the
    /// new timestamp only if it is earlier (first contamination wins).
    pub fn mark_contaminated(&mut self, source: AgentId, artifact: &str, ts: u64) {
        self.mark_contaminated_inner(source, None, artifact, ts);
    }

    /// Mark contamination and remember which OS process performed the write.
    pub fn mark_contaminated_with_process(
        &mut self,
        source: AgentId,
        source_process: ProcessId,
        artifact: &str,
        ts: u64,
    ) {
        self.mark_contaminated_inner(source, Some(source_process), artifact, ts);
    }

    fn mark_contaminated_inner(
        &mut self,
        source: AgentId,
        source_process: Option<ProcessId>,
        artifact: &str,
        ts: u64,
    ) {
        // A taint source must be a concrete file. The 2026-07 benign corpus
        // recorded `/` as the contaminated artifact for 15/19 scenarios;
        // every later read then looked like cross-agent taint. Sentinel and
        // directory-shaped strings are routing artifacts, not content.
        if !is_concrete_file_artifact(artifact) {
            return;
        }

        let key = artifact.to_string();

        if let Some(existing) = self.contaminated_artifacts.get_mut(&key) {
            // Update if this is an earlier contamination time.
            if ts < existing.timestamp_ns {
                existing.timestamp_ns = ts;
                existing.source_agent = source.clone();
                existing.source_process = source_process;
            }
            existing.access_count += 1;
        } else {
            // Evict oldest if at capacity.
            self.maybe_evict();

            let record = TaintRecord {
                source_agent: source.clone(),
                source_process,
                artifact: key.clone(),
                timestamp_ns: ts,
                evidence_refs: Vec::new(),
                access_count: 1,
            };
            self.contaminated_artifacts.insert(key.clone(), record);
            self.contaminated_agents.insert(source);
            self.insertion_order.push(key);
        }
    }

    /// Check whether an agent accessing an artifact constitutes a taint access.
    ///
    /// Returns `Some(TaintAccess)` if:
    /// - The artifact is known-contaminated
    /// - The accessing agent is different from the contaminating agent
    /// - The access happens after contamination
    ///
    /// Returns `None` if the artifact is clean, the agent is the same,
    /// or the access happened before the contamination.
    pub fn check_access(
        &self,
        agent: &AgentId,
        artifact: &str,
        access_ts_ns: u64,
    ) -> Option<TaintAccess> {
        self.check_access_inner(agent, None, artifact, access_ts_ns)
    }

    /// Check an access while supplying the reader's OS process.
    ///
    /// A writer and reader that are the same process cannot be distinguished
    /// as two logical agents in Phase 1, so no cross-agent taint is claimed.
    pub fn check_access_from(
        &self,
        agent: &AgentId,
        accessor_process: ProcessId,
        artifact: &str,
        access_ts_ns: u64,
    ) -> Option<TaintAccess> {
        self.check_access_inner(agent, Some(accessor_process), artifact, access_ts_ns)
    }

    fn check_access_inner(
        &self,
        agent: &AgentId,
        accessor_process: Option<ProcessId>,
        artifact: &str,
        access_ts_ns: u64,
    ) -> Option<TaintAccess> {
        let record = self.contaminated_artifacts.get(artifact)?;

        // Same agent accessing its own contaminated artifact: not a cross-agent taint.
        if record.source_agent == *agent {
            return None;
        }

        // Same OS process: the identity split cannot separate two logical
        // agents inside one process, so a cross-agent claim would be unfounded.
        if let (Some(writer), Some(reader)) = (record.source_process, accessor_process)
            && writer == reader
        {
            return None;
        }

        // Access before contamination: temporal ordering violated.
        if access_ts_ns < record.timestamp_ns {
            return None;
        }

        Some(TaintAccess {
            contaminated_by: record.source_agent.clone(),
            artifact: artifact.to_string(),
            first_contaminated_at_ns: record.timestamp_ns,
            accessed_at_ns: access_ts_ns,
            propagation_dt_ns: access_ts_ns.saturating_sub(record.timestamp_ns),
        })
    }

    /// Propagate contamination status from one agent to another.
    ///
    /// If `from` is contaminated, `to` becomes contaminated as well.
    /// Records the propagation edge for later analysis.
    pub fn propagate(&mut self, from: &AgentId, to: &AgentId) {
        if self.contaminated_agents.contains(from) {
            self.contaminated_agents.insert(to.clone());
            self.propagation_edges
                .entry(from.clone())
                .or_default()
                .insert(to.clone());
        }
    }

    // ── Query API ──────────────────────────────────────────────────────

    /// Check whether an agent is marked as contaminated.
    pub fn is_contaminated(&self, agent: &AgentId) -> bool {
        self.contaminated_agents.contains(agent)
    }

    /// Get all contaminated artifacts and their records.
    pub fn contaminated_artifacts(&self) -> &HashMap<String, TaintRecord> {
        &self.contaminated_artifacts
    }

    /// Get all agents marked as contaminated.
    pub fn contaminated_agents(&self) -> &HashSet<AgentId> {
        &self.contaminated_agents
    }

    /// Get the propagation chain for a contaminated agent.
    ///
    /// Returns the set of agents that received contamination from the given agent.
    pub fn propagation_targets(&self, agent: &AgentId) -> Option<&HashSet<AgentId>> {
        self.propagation_edges.get(agent)
    }

    /// Check if contamination has propagated from `from` to `to`.
    pub fn has_propagated_to(&self, from: &AgentId, to: &AgentId) -> bool {
        self.propagation_edges
            .get(from)
            .map(|targets| targets.contains(to))
            .unwrap_or(false)
    }

    /// Number of contaminated artifacts currently tracked.
    pub fn artifact_count(&self) -> usize {
        self.contaminated_artifacts.len()
    }

    /// Number of contaminated agents.
    pub fn agent_count(&self) -> usize {
        self.contaminated_agents.len()
    }

    /// Number of propagation edges.
    pub fn propagation_edge_count(&self) -> usize {
        self.propagation_edges.values().map(|s| s.len()).sum()
    }

    // ── Mutation ───────────────────────────────────────────────────────

    /// Remove an artifact from the contaminated set (e.g., after it is cleaned).
    pub fn clear_artifact(&mut self, artifact: &str) -> bool {
        self.insertion_order.retain(|a| a != artifact);
        self.contaminated_artifacts.remove(artifact).is_some()
    }

    /// Remove an agent from the contaminated set.
    pub fn clear_agent(&mut self, agent: &AgentId) {
        self.contaminated_agents.remove(agent);
        self.propagation_edges.remove(agent);
        for targets in self.propagation_edges.values_mut() {
            targets.remove(agent);
        }
    }

    /// Clear all contamination state.
    pub fn clear(&mut self) {
        self.contaminated_artifacts.clear();
        self.contaminated_agents.clear();
        self.propagation_edges.clear();
        self.insertion_order.clear();
    }

    // ── Internal ───────────────────────────────────────────────────────

    /// Evict the oldest artifact if at capacity.
    fn maybe_evict(&mut self) {
        while self.contaminated_artifacts.len() >= self.max_artifacts {
            if let Some(oldest) = self.insertion_order.first().cloned() {
                self.contaminated_artifacts.remove(&oldest);
                self.insertion_order.remove(0);
            } else {
                break;
            }
        }
    }
}

impl Default for TaintTracker {
    fn default() -> Self {
        Self::new()
    }
}

/// Whether `path` names a concrete file that content can taint.
///
/// Rejects empties, the root `/`, the routing sentinel used when an action
/// carries no path, and directory-shaped paths. A directory is not a content
/// artifact: contamination is meaningful per file.
fn is_concrete_file_artifact(path: &str) -> bool {
    !path.is_empty()
        && path != "/"
        && path != "(unknown path)"
        && path != "."
        && path != ".."
        && !path.ends_with('/')
}

// ─── Tests ─────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    fn agent(id: &str) -> AgentId {
        AgentId::from(id)
    }

    #[test]
    fn test_mark_and_check_contamination() {
        let mut tracker = TaintTracker::new();
        let a = agent("agent-a");
        let b = agent("agent-b");

        tracker.mark_contaminated(a.clone(), "/tmp/evil.txt", 1000);

        // Same agent accessing own artifact: no cross-agent taint.
        let same = tracker.check_access(&a, "/tmp/evil.txt", 2000);
        assert!(same.is_none());

        // Different agent accessing: taint detected.
        let cross = tracker.check_access(&b, "/tmp/evil.txt", 2000);
        assert!(cross.is_some());
        let taint = cross.unwrap();
        assert_eq!(taint.contaminated_by, a);
        assert_eq!(taint.artifact, "/tmp/evil.txt");
        assert_eq!(taint.propagation_dt_ns, 1000);
    }

    #[test]
    fn test_access_before_contamination() {
        let tracker = TaintTracker::new();
        let b = agent("agent-b");

        // Access before contamination is impossible.
        let result = tracker.check_access(&b, "/tmp/clean.txt", 500);
        assert!(result.is_none());
    }

    #[test]
    fn test_propagate_contamination() {
        let mut tracker = TaintTracker::new();
        let a = agent("agent-a");
        let b = agent("agent-b");
        let c = agent("agent-c");

        tracker.mark_contaminated(a.clone(), "/tmp/bad.txt", 1000);
        assert!(tracker.is_contaminated(&a));
        assert!(!tracker.is_contaminated(&b));

        // Propagate from a to b.
        tracker.propagate(&a, &b);
        assert!(tracker.is_contaminated(&b));

        // Propagate from b to c (transitive).
        tracker.propagate(&b, &c);
        assert!(tracker.is_contaminated(&c));
        assert!(tracker.has_propagated_to(&b, &c));
    }

    #[test]
    fn test_propagate_only_from_contaminated() {
        let mut tracker = TaintTracker::new();
        let a = agent("agent-a");
        let b = agent("agent-b");

        // a is not contaminated → propagation to b should not mark b.
        tracker.propagate(&a, &b);
        assert!(!tracker.is_contaminated(&b));
    }

    #[test]
    fn test_clear_artifact() {
        let mut tracker = TaintTracker::new();
        let a = agent("agent-a");
        let b = agent("agent-b");

        tracker.mark_contaminated(a, "/tmp/evil.txt", 1000);

        let before = tracker.check_access(&b, "/tmp/evil.txt", 2000);
        assert!(before.is_some());

        tracker.clear_artifact("/tmp/evil.txt");

        let after = tracker.check_access(&b, "/tmp/evil.txt", 3000);
        assert!(after.is_none());
    }

    #[test]
    fn test_clear_agent() {
        let mut tracker = TaintTracker::new();
        let a = agent("agent-a");
        let b = agent("agent-b");

        tracker.mark_contaminated(a.clone(), "/tmp/evil.txt", 1000);
        tracker.propagate(&a, &b);
        assert!(tracker.is_contaminated(&b));

        tracker.clear_agent(&b);
        assert!(!tracker.is_contaminated(&b));
    }

    #[test]
    fn test_earliest_contamination_wins() {
        let mut tracker = TaintTracker::new();
        let a = agent("agent-a");
        let b = agent("agent-b");

        tracker.mark_contaminated(a.clone(), "/tmp/shared.txt", 2000);
        tracker.mark_contaminated(b.clone(), "/tmp/shared.txt", 1000);

        let record = tracker
            .contaminated_artifacts()
            .get("/tmp/shared.txt")
            .unwrap();
        // Earliest timestamp (1000) should win.
        assert_eq!(record.timestamp_ns, 1000);
        assert_eq!(record.source_agent, b);
    }
    /// Regression for the 2026-07 benign corpus false positives: the tracker
    /// accepted `/` (and other non-file strings) as an artifact, so every
    /// later file read looked like a cross-agent taint access. A taint source
    /// must name a concrete file.
    #[test]
    fn non_file_artifacts_are_not_taint_sources() {
        let mut tracker = TaintTracker::new();
        let a = agent("agent-a");

        for path in ["", "/", "(unknown path)", "/home/user/dir/", "."] {
            tracker.mark_contaminated(a.clone(), path, 1_000);
        }

        assert_eq!(
            tracker.artifact_count(),
            0,
            "non-file paths must not become taint sources: {:?}",
            tracker.contaminated_artifacts().keys().collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_same_process_access_is_not_cross_agent_taint() {
        let mut tracker = TaintTracker::new();
        let writer = agent("agent-writer");
        let reader = agent("agent-reader");
        let pid = ProcessId::new(100, 1);

        tracker.mark_contaminated_with_process(writer, pid, "/tmp/handoff.txt", 1_000);

        // Same process under two provisional IDs: no cross-agent claim.
        assert!(
            tracker
                .check_access_from(&reader, pid, "/tmp/handoff.txt", 2_000)
                .is_none()
        );
        // A genuinely different process still propagates.
        assert!(
            tracker
                .check_access_from(&reader, ProcessId::new(200, 2), "/tmp/handoff.txt", 2_000)
                .is_some()
        );
    }
}
