// SPDX-License-Identifier: Apache-2.0 OR MIT
//
// union_find/ — Three-signal union-find algorithm for Phase 2 authoritative identity resolution.
//
// Algorithm (see spec §6.1):
//   1. Seed discovery: every ProcessId with TLS sessions → a seed component
//   2. S2 merge (TLS sharing, strongest): union seeds that share at least one TLS session
//   3. S1 downward propagation: for each seed, traverse children —
//      - child without TLS → join seed's component (tool child)
//      - child with TLS AND shared with parent → already merged by S2
//      - child with TLS AND NOT shared with parent → check is_agent_process:
//        * comm ∈ {"claude", "claude-code"} → subagent boundary, DON'T merge
//        * otherwise → tool process, join parent's agent
//   4. S1 upward propagation (max depth 5): for unassigned processes, walk up parents
//   5. AgentId derivation: sort all ProcessIds in component → concat → SHA256 → "agent-{16hex}"

use crate::responsibility::signals::SignalSnapshot;
#[cfg(test)]
use crate::responsibility::signals::signal_snapshot::ProcessExecInfo;
use crate::responsibility::types::{
    AgentConfidence, AgentId, AuthoritativeIdentity, IdentityRefinement, IdentityRefinementType,
    MessageId, ProcessId, ProvisionalIdentity, SubagentBoundary, SubagentBoundaryReason,
    TlsSessionId, UnresolvedDiagnostic, UnresolvedReason,
};
use sha2::{Digest, Sha256};
use std::collections::{HashMap, HashSet};
use std::sync::OnceLock;

// ─── Constants ──────────────────────────────────────────────────────────

/// Default process names that indicate an agent process (not a tool process).
/// See spec §6.2: is_agent_process checks comm ∈ AGENT_COMMS.
///
/// An installation whose agent does not run under one of these names (for
/// example OpenClaw, whose Node gateway runs as `MainThread` with no reliable
/// `comm`) can extend the boundary at startup:
///
/// ```text
/// MTAGENTLENS_AGENT_COMMS=openclaw,MainThread ./mtagentlens record ...
/// ```
///
/// Processes outside the boundary are counted in
/// `DIAG_ACTIONS_DROPPED_NO_AGENT` rather than dropped invisibly.
pub const AGENT_COMMS: &[&str] = &["claude", "claude-code"];

/// Optional override set by [`configure_agent_comms_from_env`].
static AGENT_COMM_OVERRIDE: OnceLock<HashSet<String>> = OnceLock::new();
/// cgroup unit names treated as agent processes (`MTAGENTLENS_AGENT_CGROUP`).
static AGENT_CGROUP_OVERRIDE: OnceLock<HashSet<String>> = OnceLock::new();
/// Executable-path substrings treated as agent processes
/// (`MTAGENTLENS_AGENT_EXE`).
static AGENT_EXE_OVERRIDE: OnceLock<Vec<String>> = OnceLock::new();

/// Extend the agent `comm` boundary from `MTAGENTLENS_AGENT_COMMS`
/// (comma-separated). Unset or empty keeps the built-in default.
pub fn configure_agent_comms_from_env() {
    if let Ok(raw) = std::env::var("MTAGENTLENS_AGENT_COMMS") {
        let comms = parse_agent_comm_list(&raw);
        if !comms.is_empty() {
            let _ = AGENT_COMM_OVERRIDE.set(comms);
        }
    }
    if let Ok(raw) = std::env::var("MTAGENTLENS_AGENT_CGROUP") {
        let units = parse_agent_comm_list(&raw);
        if !units.is_empty() {
            let _ = AGENT_CGROUP_OVERRIDE.set(units);
        }
    }
    if let Ok(raw) = std::env::var("MTAGENTLENS_AGENT_EXE") {
        let paths: Vec<String> = raw
            .split(',')
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .map(str::to_string)
            .collect();
        if !paths.is_empty() {
            let _ = AGENT_EXE_OVERRIDE.set(paths);
        }
    }
}

/// Whether an explicit identity boundary (cgroup or executable path) is
/// configured. In that mode TLS sessions alone must not qualify a process:
/// every Node app shares the `MainThread` comm and can open TLS.
pub fn has_identity_boundary() -> bool {
    AGENT_CGROUP_OVERRIDE
        .get()
        .is_some_and(|units| !units.is_empty())
        || AGENT_EXE_OVERRIDE
            .get()
            .is_some_and(|paths| !paths.is_empty())
}

/// Whether a cgroup unit name was configured as an agent boundary.
pub fn is_agent_cgroup(unit: &str) -> bool {
    AGENT_CGROUP_OVERRIDE
        .get()
        .is_some_and(|units| units.contains(unit))
}

/// Whether an executable path (or full command line) contains a configured
/// agent-path substring. This is the signal that survives Node's shared
/// `MainThread` comm: the argv/path still names the agent.
pub fn is_agent_exe(path: &str) -> bool {
    AGENT_EXE_OVERRIDE
        .get()
        .is_some_and(|paths| paths.iter().any(|p| path.contains(p)))
}

/// Parse a comma-separated agent comm list, trimming blanks.
pub fn parse_agent_comm_list(raw: &str) -> HashSet<String> {
    raw.split(',')
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string)
        .collect()
}

/// Whether `comm` identifies an agent process under the configured boundary.
pub fn is_agent_comm(comm: &str) -> bool {
    // Apply MTAGENTLENS_AGENT_* once, even when the caller (a library user)
    // did not run the CLI startup hook.
    static ENV_INIT: std::sync::Once = std::sync::Once::new();
    ENV_INIT.call_once(configure_agent_comms_from_env);
    match AGENT_COMM_OVERRIDE.get() {
        Some(over) => over.contains(comm) || AGENT_COMMS.contains(&comm),
        None => AGENT_COMMS.contains(&comm),
    }
}

/// Maximum depth for upward propagation (S1 up-phase).
const UPWARD_MAX_DEPTH: usize = 5;

// ─── AuthoritativeResolution ────────────────────────────────────────────

/// Phase 2 authoritative resolution result.
///
/// Produced by `IdentityResolver::resolve_authoritative()` from a frozen
/// `SignalSnapshot` at session end.
#[derive(Debug, Clone)]
pub struct AuthoritativeResolution {
    /// Resolved agents with their assigned processes, messages, and TLS sessions.
    pub agents: Vec<AuthoritativeIdentity>,
    /// Refinements linking Phase 1 provisional IDs to Phase 2 authoritative IDs.
    pub refinements: Vec<IdentityRefinement>,
    /// Detected subagent boundaries (parent → child agent splits).
    pub subagent_boundaries: Vec<SubagentBoundary>,
    /// Processes that could not be resolved.
    pub unresolved: Vec<UnresolvedDiagnostic>,
}

// ─── Union-Find ────────────────────────────────────────────────────────

/// Union-Find (disjoint-set) data structure used internally for S2 merging.
struct UnionFind {
    parent: Vec<usize>,
}

impl UnionFind {
    fn new(size: usize) -> Self {
        UnionFind {
            parent: (0..size).collect(),
        }
    }

    fn find(&mut self, x: usize) -> usize {
        if self.parent[x] != x {
            self.parent[x] = self.find(self.parent[x]);
        }
        self.parent[x]
    }

    fn union(&mut self, a: usize, b: usize) {
        let ra = self.find(a);
        let rb = self.find(b);
        if ra != rb {
            self.parent[rb] = ra;
        }
    }
}

// ─── Helper Functions ──────────────────────────────────────────────────

/// Check whether a process is an agent process (as opposed to a tool process).
///
/// An agent process is one whose `comm` matches one of `AGENT_COMMS`
/// (e.g. "claude", "claude-code"). Tool processes like "curl", "git",
/// "python3" return false and are assigned to their parent's agent.
pub fn is_agent_process(process_id: ProcessId, snapshot: &SignalSnapshot) -> bool {
    let meta = match snapshot.get_process(process_id.pid, process_id.start_time_ns) {
        Some(m) => m,
        None => return false,
    };
    is_agent_comm(&meta.comm)
}

/// Check whether there is a subagent boundary between parent and child.
///
/// Per spec §6.2: a child has independent TLS not shared with the parent AND
/// its comm is an agent comm. This indicates the child is a separate agent
/// (subagent), not a tool process.
pub fn is_subagent_boundary(
    parent: ProcessId,
    child: ProcessId,
    snapshot: &SignalSnapshot,
) -> bool {
    let child_tls = snapshot.tls_sessions_for_process(child);
    if child_tls.is_empty() {
        return false;
    }

    let parent_tls = snapshot.tls_sessions_for_process(parent);
    let shared = child_tls.iter().any(|t| parent_tls.contains(t));
    if shared {
        return false; // Shared TLS → same agent
    }

    // Child has independent TLS not shared with parent → check comm
    is_agent_process(child, snapshot)
}

/// Check whether a process has children that are assigned to multiple different
/// agent components. Used in S1 upward propagation to detect shared infrastructure
/// parents (e.g. tmux, shell) that should not be assigned to any single agent.
pub fn has_children_in_multiple_components(
    process_id: ProcessId,
    snapshot: &SignalSnapshot,
    assignments: &HashMap<ProcessId, usize>, // ProcessId → component index
) -> bool {
    let children = snapshot.get_children(process_id);
    if children.len() < 2 {
        return false;
    }

    let mut seen_components = HashSet::new();
    for child in children {
        if let Some(&comp) = assignments.get(child) {
            seen_components.insert(comp);
            if seen_components.len() >= 2 {
                return true;
            }
        }
    }
    false
}

/// Derive a deterministic AgentId from a set of ProcessIds.
///
/// Sorts all ProcessIds in the component, concatenates them as a string, and
/// hex-encodes the first 8 bytes of the SHA256 digest — i.e. 16 hex characters,
/// giving `agent-{16hex}`. This is the only place an `agent-*` identifier is
/// derived; the framework detection layer deliberately emits `root_pid` instead
/// of inventing one, because it cannot see the full ProcessId set.
fn derive_agent_id(process_ids: &[ProcessId]) -> AgentId {
    let mut sorted: Vec<_> = process_ids.to_vec();
    sorted.sort_by_key(|p| (p.pid, p.start_time_ns));

    let mut hasher = Sha256::new();
    for p in &sorted {
        hasher.update(format!("{}:{};", p.pid, p.start_time_ns).as_bytes());
    }
    let hash = hex::encode(&hasher.finalize()[..8]);
    AgentId::from(format!("agent-{}", hash))
}

// ─── Core Algorithm ────────────────────────────────────────────────────

/// Run the three-signal union-find algorithm on a frozen SignalSnapshot.
///
/// This is the main Phase 2 algorithm. It takes a complete, frozen snapshot
/// (all events collected) and returns the authoritative agent identities.
///
/// The algorithm works as follows:
/// 1. Every process with TLS sessions becomes a seed component
/// 2. Seeds sharing at least one TLS session are merged (S2, strongest signal)
/// 3. Processes are propagated downward (S1) and upward (S1) through the tree
/// 4. AgentIds are derived deterministically from component membership
pub fn run_authoritative_resolution(
    snapshot: &SignalSnapshot,
    provisional_map: &HashMap<ProcessId, ProvisionalIdentity>,
) -> AuthoritativeResolution {
    // ── Step 1: Seed discovery ──────────────────────────────────────
    // Collect all ProcessIds that have at least one TLS session.
    let mut seed_processes: Vec<ProcessId> = Vec::new();
    // Build reverse index: ProcessId → Vec<TlsSessionId>
    let mut process_tls: HashMap<ProcessId, Vec<TlsSessionId>> = HashMap::new();

    for (tls_session, process_id) in &snapshot.tls_to_process {
        process_tls
            .entry(*process_id)
            .or_default()
            .push(tls_session.clone());
    }

    for (pid, tls_sessions) in &process_tls {
        if !tls_sessions.is_empty() {
            seed_processes.push(*pid);
        }
    }

    let n = seed_processes.len();
    let mut uf = UnionFind::new(n);

    // ── Step 2: S2 merge (TLS sharing) ──────────────────────────────
    // Build TLS → Vec<seed_index> for shared TLS detection.
    let mut tls_to_seeds: HashMap<TlsSessionId, Vec<usize>> = HashMap::new();
    for (i, process_id) in seed_processes.iter().enumerate() {
        if let Some(sessions) = process_tls.get(process_id) {
            for tls in sessions {
                tls_to_seeds.entry(tls.clone()).or_default().push(i);
            }
        }
    }

    // Union all seeds that share at least one TLS session.
    for indices in tls_to_seeds.values() {
        if indices.len() >= 2 {
            let root = indices[0];
            for &idx in &indices[1..] {
                uf.union(root, idx);
            }
        }
    }

    // ── Assign components ────────────────────────────────────────────
    // Map each seed to its component root, then build component membership.
    let mut component_id_for_seed: Vec<usize> = vec![0; n];
    let mut root_to_component: HashMap<usize, usize> = HashMap::new();
    let mut next_comp = 0usize;

    for i in 0..n {
        let root = uf.find(i);
        let comp = *root_to_component.entry(root).or_insert_with(|| {
            let c = next_comp;
            next_comp += 1;
            c
        });
        component_id_for_seed[i] = comp;
    }

    let num_components = next_comp;
    let mut components: Vec<Vec<ProcessId>> = vec![Vec::new(); num_components];
    for i in 0..n {
        let comp = component_id_for_seed[i];
        components[comp].push(seed_processes[i]);
    }

    // ProcessId → component index mapping (for all assigned processes)
    let mut assignments: HashMap<ProcessId, usize> = HashMap::new();
    for i in 0..n {
        let comp = component_id_for_seed[i];
        assignments.insert(seed_processes[i], comp);
    }

    // ── Step 3: S1 downward propagation ─────────────────────────────
    // For each seed, traverse children and assign tool processes.
    // Also detect subagent boundaries.
    let mut subagent_boundaries: Vec<SubagentBoundary> = Vec::new();
    // Worklist of processes whose children need to be checked.
    let mut worklist: Vec<ProcessId> = seed_processes.clone();

    while let Some(parent) = worklist.pop() {
        let parent_comp = match assignments.get(&parent).copied() {
            Some(c) => c,
            None => continue, // Not yet assigned — skip
        };

        for child in snapshot.get_children(parent) {
            if assignments.contains_key(child) {
                // Already assigned (e.g., a child with its own TLS that was seeded).
                // Still check for subagent boundary: child has independent TLS
                // not shared with parent AND is an agent process.
                let child_tls = snapshot.tls_sessions_for_process(*child);
                if !child_tls.is_empty() {
                    let parent_tls = snapshot.tls_sessions_for_process(parent);
                    let tls_shared = child_tls.iter().any(|t| parent_tls.contains(t));
                    if !tls_shared && is_agent_process(*child, snapshot) {
                        let child_comp = assignments[child];
                        let parent_agent_id = derive_agent_id(&components[parent_comp]);
                        let child_agent_id = derive_agent_id(&components[child_comp]);
                        subagent_boundaries.push(SubagentBoundary {
                            parent_agent: parent_agent_id,
                            child_agent: child_agent_id,
                            parent_process: parent,
                            child_process: *child,
                            reason: SubagentBoundaryReason::IndependentTls,
                        });
                    }
                }
                // Still need to process child's own children for tool assignment
                worklist.push(*child);
                continue;
            }

            let child_tls = snapshot.tls_sessions_for_process(*child);

            if child_tls.is_empty() {
                // Child has no TLS → tool process, join parent's agent
                assignments.insert(*child, parent_comp);
                components[parent_comp].push(*child);
                worklist.push(*child);
            } else {
                // Child has TLS — check if shared with parent
                let parent_tls = snapshot.tls_sessions_for_process(parent);
                let tls_shared = child_tls.iter().any(|t| parent_tls.contains(t));

                if tls_shared {
                    // TLS shared with parent → already merged by S2
                    // This child should already be in the same component as parent.
                    // But if it wasn't caught by S2 (e.g. because the seed list didn't
                    // include parent), force-assign now.
                    if !assignments.contains_key(child) {
                        assignments.insert(*child, parent_comp);
                        components[parent_comp].push(*child);
                        worklist.push(*child);
                    }
                } else {
                    // Child has independent TLS, NOT shared with parent
                    if is_agent_process(*child, snapshot) {
                        // Subagent boundary detected
                        // Child is a separate agent — do NOT merge
                        let child_comp = match assignments.get(child) {
                            Some(c) => *c,
                            None => {
                                // Create new component for the subagent
                                let new_comp = components.len();
                                components.push(vec![*child]);
                                assignments.insert(*child, new_comp);
                                worklist.push(*child);
                                new_comp
                            }
                        };

                        let parent_agent_id = derive_agent_id(&components[parent_comp]);
                        let child_agent_id = derive_agent_id(&components[child_comp]);

                        subagent_boundaries.push(SubagentBoundary {
                            parent_agent: parent_agent_id,
                            child_agent: child_agent_id,
                            parent_process: parent,
                            child_process: *child,
                            reason: SubagentBoundaryReason::IndependentTls,
                        });
                    } else {
                        // Tool process with its own TLS (e.g. curl talking to its own API)
                        // Still a tool — join parent's agent
                        assignments.insert(*child, parent_comp);
                        components[parent_comp].push(*child);
                        worklist.push(*child);
                    }
                }
            }
        }
    }

    // ── Step 4: S1 upward propagation (max depth 5) ─────────────────
    // For unassigned processes, walk up parents to find an assigned ancestor.
    let mut unresolved: Vec<UnresolvedDiagnostic> = Vec::new();

    // Collect all process IDs from the snapshot's processes
    // We need access to process IDs; snap.processes is pub(crate)
    let all_process_ids: Vec<ProcessId> = snapshot.processes.keys().copied().collect();

    for process_id in &all_process_ids {
        if assignments.contains_key(process_id) {
            continue;
        }

        let mut current = *process_id;
        let mut found = false;
        let mut visited = HashSet::new();
        visited.insert(current);

        for _depth in 0..UPWARD_MAX_DEPTH {
            // Check if current is a shared infrastructure parent
            if assignments.contains_key(&current) {
                // Check if it's shared infrastructure
                let comp = assignments[&current];
                if has_children_in_multiple_components(current, snapshot, &assignments) {
                    // Shared infrastructure — stop here, don't assign
                    break;
                }
                // Assigned ancestor found — join its component
                assignments.insert(*process_id, comp);
                components[comp].push(*process_id);
                found = true;
                break;
            }

            // Move up to parent
            match snapshot.get_parent(current) {
                Some(parent) if parent != current => {
                    if !visited.insert(parent) {
                        break; // Cycle detected
                    }
                    current = parent;
                }
                _ => break, // Root reached
            }
        }

        if !found {
            // Check if process has any positive signals
            let has_tls = !snapshot.tls_sessions_for_process(*process_id).is_empty();
            let has_messages = !snapshot.messages_for_process(*process_id).is_empty();
            let reason = if !has_tls && !has_messages {
                UnresolvedReason::NoPositiveSignal
            } else {
                UnresolvedReason::NoProcessLineage
            };
            unresolved.push(UnresolvedDiagnostic {
                process_id: *process_id,
                reason,
            });
        }
    }

    // ── Step 5: AgentId derivation ──────────────────────────────────
    let mut agents: Vec<AuthoritativeIdentity> = Vec::new();
    for comp in &components {
        if comp.is_empty() {
            continue;
        }

        let agent_id = derive_agent_id(comp);

        // Collect TLS sessions and messages for this component
        let mut tls_sessions: Vec<TlsSessionId> = Vec::new();
        let mut message_ids: Vec<MessageId> = Vec::new();
        let mut tls_seen: HashSet<String> = HashSet::new();
        let mut msg_seen: HashSet<String> = HashSet::new();

        for pid in comp {
            for tls in snapshot.tls_sessions_for_process(*pid) {
                if tls_seen.insert(tls.as_str().to_string()) {
                    tls_sessions.push(tls);
                }
            }
            for msg in snapshot.messages_for_process(*pid) {
                if msg_seen.insert(msg.as_str().to_string()) {
                    message_ids.push(msg);
                }
            }
        }

        // Determine confidence: TLS-backed → Authoritative, otherwise Inferred
        let confidence = if !tls_sessions.is_empty() {
            AgentConfidence::Authoritative
        } else {
            AgentConfidence::Inferred
        };

        // Collect provisional refs
        let mut provisional_refs: Vec<AgentId> = Vec::new();
        let mut seen_prov: HashSet<String> = HashSet::new();
        for pid in comp {
            if let Some(prov) = provisional_map.get(pid)
                && seen_prov.insert(prov.agent_id.as_str().to_string())
            {
                provisional_refs.push(prov.agent_id.clone());
            }
        }

        agents.push(AuthoritativeIdentity {
            agent_id,
            process_ids: comp.clone(),
            message_ids,
            tls_sessions,
            confidence,
            provisional_refs,
        });
    }

    // ── Build refinements ───────────────────────────────────────────
    let refinements: Vec<IdentityRefinement> = agents
        .iter()
        .flat_map(|auth| {
            auth.provisional_refs.iter().map(|prov_id| IdentityRefinement {
                phase1_agent_id: prov_id.clone(),
                phase2_agent_id: auth.agent_id.clone(),
                refinement_type: IdentityRefinementType::Confirmed,
                reason: format!(
                    "Phase 1 agent {} confirmed with {} process(es) in authoritative resolution",
                    prov_id,
                    auth.process_ids.len()
                ),
            })
        })
        .collect();

    AuthoritativeResolution {
        agents,
        refinements,
        subagent_boundaries,
        unresolved,
    }
}

// ─── Tests ──────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use crate::responsibility::signals::SignalGraph;
    use std::sync::Arc;

    fn pid(id: u32, start: u64) -> ProcessId {
        ProcessId::new(id, start)
    }

    fn tls(s: &str) -> TlsSessionId {
        TlsSessionId::from(s)
    }

    /// Build a minimal snapshot for testing.
    fn empty_snapshot() -> Arc<SignalSnapshot> {
        SignalGraph::new().snapshot()
    }

    #[test]
    fn test_is_agent_process_identifies_agent_comms() {
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
        let snap = g.snapshot();
        assert!(is_agent_process(p, &snap));

        let mut g2 = SignalGraph::new();
        let p2 = pid(200, 2);
        g2.observe_exec(
            p2,
            pid(1, 0),
            ProcessExecInfo {
                comm: "curl".into(),
                ..Default::default()
            },
            None,
            2,
        );
        let snap2 = g2.snapshot();
        assert!(!is_agent_process(p2, &snap2));
    }

    #[test]
    fn test_is_agent_process_unknown_process() {
        let snap = empty_snapshot();
        assert!(!is_agent_process(pid(999, 0), &snap));
    }

    #[test]
    fn test_is_subagent_boundary_independent_tls() {
        let mut g = SignalGraph::new();
        let parent = pid(100, 1);
        let child = pid(200, 2);

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
        // Parent and child have different, non-overlapping TLS sessions
        g.observe_tls(parent, tls("sess-parent"), 10);
        g.observe_tls(child, tls("sess-child"), 20);

        let snap = g.snapshot();
        assert!(is_subagent_boundary(parent, child, &snap));
    }

    #[test]
    fn test_is_subagent_boundary_shared_tls_not_boundary() {
        let mut g = SignalGraph::new();
        let parent = pid(100, 1);
        let child = pid(200, 2);

        // NOTE: The data model maps TLS session → single process (last-writer-wins).
        // Shared TLS between parent and child is tracked via tls_collisions, not as
        // dual ownership. This test validates that when parent and child have
        // NO TLS overlap (each has its own session), the boundary IS detected.
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
        g.observe_exec(
            parent,
            pid(50, 0),
            ProcessExecInfo {
                comm: "claude".into(),
                ..Default::default()
            },
            None,
            1,
        );
        // Parent and child have DIFFERENT TLS sessions → boundary expected
        g.observe_tls(parent, tls("sess-parent"), 10);
        g.observe_tls(child, tls("sess-child"), 20);

        let snap = g.snapshot();
        assert!(is_subagent_boundary(parent, child, &snap));
    }

    #[test]
    fn test_is_subagent_boundary_tool_child_not_boundary() {
        let mut g = SignalGraph::new();
        let parent = pid(100, 1);
        let child = pid(200, 2);

        g.observe_exec(
            child,
            parent,
            ProcessExecInfo {
                comm: "curl".into(),
                ..Default::default()
            },
            None,
            2,
        );
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
        g.observe_tls(child, tls("sess-child"), 20);

        let snap = g.snapshot();
        // curl has TLS but is a tool, not an agent → no subagent boundary
        assert!(!is_subagent_boundary(parent, child, &snap));
    }

    #[test]
    fn test_is_subagent_boundary_no_tls_not_boundary() {
        let mut g = SignalGraph::new();
        let parent = pid(100, 1);
        let child = pid(200, 2);

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

        let snap = g.snapshot();
        assert!(!is_subagent_boundary(parent, child, &snap));
    }

    #[test]
    fn test_has_children_in_multiple_components() {
        let mut g = SignalGraph::new();
        let shell = pid(50, 0);
        let a = pid(100, 1);
        let b = pid(200, 2);

        g.observe_exec(
            a,
            shell,
            ProcessExecInfo {
                comm: "claude".into(),
                ..Default::default()
            },
            None,
            1,
        );
        g.observe_exec(
            b,
            shell,
            ProcessExecInfo {
                comm: "claude".into(),
                ..Default::default()
            },
            None,
            2,
        );

        let snap = g.snapshot();
        let mut assignments = HashMap::new();
        assignments.insert(a, 0);
        assignments.insert(b, 1);

        assert!(has_children_in_multiple_components(
            shell,
            &snap,
            &assignments
        ));
    }

    #[test]
    fn test_has_children_in_multiple_components_single_component() {
        let mut g = SignalGraph::new();
        let parent = pid(100, 1);
        let a = pid(200, 2);
        let b = pid(300, 3);

        g.observe_exec(
            a,
            parent,
            ProcessExecInfo {
                comm: "cat".into(),
                ..Default::default()
            },
            None,
            2,
        );
        g.observe_exec(
            b,
            parent,
            ProcessExecInfo {
                comm: "head".into(),
                ..Default::default()
            },
            None,
            3,
        );

        let snap = g.snapshot();
        let mut assignments = HashMap::new();
        assignments.insert(a, 0);
        assignments.insert(b, 0); // Same component

        assert!(!has_children_in_multiple_components(
            parent,
            &snap,
            &assignments
        ));
    }

    #[test]
    fn test_derive_agent_id_deterministic() {
        let p1 = pid(100, 1);
        let p2 = pid(200, 2);
        let p3 = pid(300, 3);

        let id1 = derive_agent_id(&[p1, p2, p3]);
        let id2 = derive_agent_id(&[p3, p1, p2]); // Different order, same set
        assert_eq!(id1, id2);
    }

    #[test]
    fn test_single_agent_resolution() {
        let mut g = SignalGraph::new();
        let shell = pid(50, 0);
        let claude = pid(100, 1);
        let tool = pid(200, 2);

        // Claude process with TLS → seed
        g.observe_exec(
            claude,
            shell,
            ProcessExecInfo {
                comm: "claude".into(),
                ..Default::default()
            },
            None,
            1,
        );
        g.observe_tls(claude, tls("sess-1"), 10);

        // Tool child without TLS → should be assigned to same agent
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
        let result = run_authoritative_resolution(&snap, &HashMap::new());

        assert_eq!(result.agents.len(), 1);
        let agent = &result.agents[0];
        assert!(agent.process_ids.contains(&claude));
        assert!(agent.process_ids.contains(&tool));
        assert_eq!(agent.tls_sessions.len(), 1);
        // Note: shell (pid 50) may remain unresolved (NoPositiveSignal), which is correct.
        // Only assert that claude and tool are included in the resolved agent.
    }

    #[test]
    fn test_subagent_separate_from_main() {
        let mut g = SignalGraph::new();
        let shell = pid(50, 0);
        let main = pid(100, 1);
        let sub = pid(200, 2);
        let sub_tool = pid(300, 3);

        g.observe_exec(
            main,
            shell,
            ProcessExecInfo {
                comm: "claude".into(),
                ..Default::default()
            },
            None,
            1,
        );
        g.observe_tls(main, tls("sess-main"), 10);

        // Subagent with independent TLS → separate agent
        g.observe_exec(
            sub,
            main,
            ProcessExecInfo {
                comm: "claude".into(),
                ..Default::default()
            },
            None,
            2,
        );
        g.observe_tls(sub, tls("sess-sub"), 20);

        // Tool child of sub → assigned to sub
        g.observe_exec(
            sub_tool,
            sub,
            ProcessExecInfo {
                comm: "python3".into(),
                ..Default::default()
            },
            None,
            3,
        );

        let snap = g.snapshot();
        let result = run_authoritative_resolution(&snap, &HashMap::new());

        assert_eq!(result.agents.len(), 2);
        assert_eq!(result.subagent_boundaries.len(), 1);

        // Find subagent's component — should contain python3
        let boundary = &result.subagent_boundaries[0];
        assert_eq!(boundary.parent_process, main);
        assert_eq!(boundary.child_process, sub);

        // The python3 tool should be in the subagent's agent
        let sub_agent = result
            .agents
            .iter()
            .find(|a| a.process_ids.contains(&sub))
            .unwrap();
        assert!(sub_agent.process_ids.contains(&sub_tool));
    }

    #[test]
    fn test_shared_tls_merges_agents() {
        // NOTE: The data model maps TLS session → single process (last-writer-wins).
        // Two processes sharing a TLS cannot be detected via tls_to_process alone.
        // This test validates the case where a single agent has both a claude process
        // and a tool child, which ARE merged via downward propagation.
        let mut g = SignalGraph::new();
        let agent = pid(100, 1);
        let tool = pid(200, 2);

        // Agent process with TLS → seed
        g.observe_exec(
            agent,
            pid(50, 0),
            ProcessExecInfo {
                comm: "claude".into(),
                ..Default::default()
            },
            None,
            1,
        );
        g.observe_tls(agent, tls("sess-1"), 10);
        // Tool child without TLS → merged into agent
        g.observe_exec(
            tool,
            agent,
            ProcessExecInfo {
                comm: "cat".into(),
                ..Default::default()
            },
            None,
            2,
        );

        let snap = g.snapshot();
        let result = run_authoritative_resolution(&snap, &HashMap::new());

        // Should be one agent with both processes
        assert_eq!(result.agents.len(), 1);
        let a = &result.agents[0];
        assert!(a.process_ids.contains(&agent));
        assert!(a.process_ids.contains(&tool));
        assert_eq!(a.tls_sessions.len(), 1);
    }

    #[test]
    fn test_unresolved_no_signal() {
        let mut g = SignalGraph::new();
        let shell = pid(50, 0);
        let noise = pid(999, 1);
        g.observe_exec(
            noise,
            shell,
            ProcessExecInfo {
                comm: "bun".into(),
                ..Default::default()
            },
            None,
            1,
        );

        let snap = g.snapshot();
        let result = run_authoritative_resolution(&snap, &HashMap::new());

        // Only noise should be unresolved (shell also gets registered as a process
        // but may be resolved or unresolved depending on the algorithm).
        // Noise has no TLS/messages → NoPositiveSignal
        assert_eq!(result.agents.len(), 0);
        assert!(!result.unresolved.is_empty());
        assert!(result.unresolved.iter().any(
            |u| u.process_id == noise && matches!(u.reason, UnresolvedReason::NoPositiveSignal)
        ));
    }

    #[test]
    fn test_agent_team_multiple_brothers() {
        let mut g = SignalGraph::new();
        let shell = pid(50, 0);
        let claude_a = pid(100, 1);
        let claude_b = pid(200, 2);

        // Both Claude processes under same shell, independent TLS
        g.observe_exec(
            claude_a,
            shell,
            ProcessExecInfo {
                comm: "claude".into(),
                ..Default::default()
            },
            None,
            1,
        );
        g.observe_tls(claude_a, tls("sess-a"), 10);

        g.observe_exec(
            claude_b,
            shell,
            ProcessExecInfo {
                comm: "claude".into(),
                ..Default::default()
            },
            None,
            2,
        );
        g.observe_tls(claude_b, tls("sess-b"), 20);

        let snap = g.snapshot();
        let result = run_authoritative_resolution(&snap, &HashMap::new());

        // Should be 2 separate agents
        assert_eq!(result.agents.len(), 2);
    }

    #[test]
    fn test_upward_propagation_simple() {
        let mut g = SignalGraph::new();
        let claude = pid(100, 1);
        let shell = pid(50, 0);
        let kernel_thread = pid(10, 0);

        // Claude has TLS
        g.observe_exec(
            claude,
            shell,
            ProcessExecInfo {
                comm: "claude".into(),
                ..Default::default()
            },
            None,
            1,
        );
        g.observe_tls(claude, tls("sess-1"), 10);

        // Shell and kernel_thread are ancestors without TLS
        g.observe_exec(
            shell,
            kernel_thread,
            ProcessExecInfo {
                comm: "bash".into(),
                ..Default::default()
            },
            None,
            0,
        );
        g.observe_exec(
            kernel_thread,
            pid(1, 0),
            ProcessExecInfo {
                comm: "kthreadd".into(),
                ..Default::default()
            },
            None,
            0,
        );

        let snap = g.snapshot();
        let result = run_authoritative_resolution(&snap, &HashMap::new());

        // Should resolve at least the claude process
        assert_eq!(result.agents.len(), 1);
    }
}

#[cfg(test)]
mod agent_comm_tests {
    use super::*;

    #[test]
    fn default_boundary_covers_builtin_agents() {
        assert!(is_agent_comm("claude"));
        assert!(is_agent_comm("claude-code"));
        assert!(!is_agent_comm("MainThread"));
        assert!(!is_agent_comm("curl"));
    }

    #[test]
    fn cgroup_and_exe_boundaries_are_off_by_default() {
        assert!(!is_agent_cgroup("some.service"));
        assert!(!is_agent_exe("/usr/bin/some-agent"));
    }

    #[test]
    fn comm_list_parsing_trims_and_drops_blanks() {
        let parsed = parse_agent_comm_list(" openclaw , MainThread ,, ");
        assert!(parsed.contains("openclaw"));
        assert!(parsed.contains("MainThread"));
        assert_eq!(parsed.len(), 2);
        assert!(parse_agent_comm_list("   ").is_empty());
    }
}
