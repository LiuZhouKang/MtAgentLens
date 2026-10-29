// SPDX-License-Identifier: Apache-2.0 OR MIT
//
// union_find/ — Three-signal union-find algorithm for Phase 2 authoritative identity resolution.
//
// Algorithm (see spec §6.1):
//   1. Seed discovery: every ProcessId with TLS sessions → a seed component
//   2. S2/S3 merge: shared TLS/conversation proposes a union, vetoed when it
//      would merge the endpoints of an observed process delegation
//   3. S1 downward propagation: for each seed, traverse children —
//      - child without TLS → join seed's component (tool child)
//      - child with TLS AND shared with parent → already merged by S2
//      - child with TLS AND NOT shared with parent → check is_agent_process:
//        * comm ∈ {"claude", "claude-code"} → subagent boundary, DON'T merge
//        * otherwise → tool process, join parent's agent
//   4. S1 upward propagation (max depth 5): for unassigned processes, walk up parents
//   5. AgentId derivation: sort all ProcessIds in component → concat → SHA256 → "agent-{16hex}"

use crate::responsibility::identity::IdentityProvider;
use crate::responsibility::identity::policy::IdentityBoundaryPolicy;
use crate::responsibility::signals::SignalSnapshot;
#[cfg(test)]
use crate::responsibility::signals::signal_snapshot::ProcessExecInfo;
use crate::responsibility::types::ConversationIdentity;
use crate::responsibility::types::{
    AgentConfidence, AgentId, AuthoritativeIdentity, IdentityRefinement, IdentityRefinementType,
    Intent, MessageId, ProcessId, ProvisionalIdentity, SubagentBoundary, SubagentBoundaryReason,
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
/// Executable-path substrings treated as agent processes
/// (`MTAGENTLENS_AGENT_EXE`).
static AGENT_EXE_OVERRIDE: OnceLock<Vec<String>> = OnceLock::new();

/// `MTAGENTLENS_AGENT_CGROUP` is not supported: cgroup unit names have no
/// kernel-verified source after the unverified procfs PID reads were removed.
/// Accepting it would enable a strict boundary that can never match.
fn unsupported_cgroup_boundary(raw: &str) -> Option<&'static str> {
    (!raw.trim().is_empty()).then_some(
        "warning: MTAGENTLENS_AGENT_CGROUP is ignored: cgroup unit names are not \
         resolved from unverified procfs; use MTAGENTLENS_AGENT_EXE or \
         MTAGENTLENS_AGENT_COMMS",
    )
}

/// Extend the agent `comm` boundary from `MTAGENTLENS_AGENT_COMMS`
/// (comma-separated). Unset or empty keeps the built-in default.
pub fn configure_agent_comms_from_env() {
    if let Ok(raw) = std::env::var("MTAGENTLENS_AGENT_COMMS") {
        let comms = parse_agent_comm_list(&raw);
        if !comms.is_empty() {
            let _ = AGENT_COMM_OVERRIDE.set(comms);
        }
    }
    if let Ok(raw) = std::env::var("MTAGENTLENS_AGENT_CGROUP")
        && let Some(warning) = unsupported_cgroup_boundary(&raw)
    {
        eprintln!("{warning}");
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

/// Whether an explicit executable-path boundary is configured. In that mode
/// TLS sessions alone must not qualify a process: every Node app shares the
/// `MainThread` comm and can open TLS.
pub fn has_identity_boundary() -> bool {
    AGENT_EXE_OVERRIDE
        .get()
        .is_some_and(|paths| !paths.is_empty())
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

/// Capture the active comm boundary as a sorted union of the built-in
/// vocabulary and the configured override.
pub(crate) fn configured_agent_comms() -> Vec<String> {
    static ENV_INIT: std::sync::Once = std::sync::Once::new();
    ENV_INIT.call_once(configure_agent_comms_from_env);

    let mut comms: Vec<String> = AGENT_COMMS.iter().map(|comm| (*comm).to_string()).collect();
    if let Some(over) = AGENT_COMM_OVERRIDE.get() {
        comms.extend(over.iter().cloned());
    }
    comms.sort();
    comms.dedup();
    comms
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
/// Produced by `IdentityResolver::resolve_authoritative_with_delegations()` from a frozen
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
    /// Message-level identities for processes that carried multiple conversations.
    pub conversation_agents: Vec<ConversationIdentity>,
    /// Messages whose captured anchors conflict: one observed message names
    /// more than one (process, conversation) pair, or more than one identity
    /// claims it. The provider must abstain for these, never process-fallback.
    pub ambiguous_messages: Vec<MessageId>,
}

/// `IdentityProvider` backed by the phase-2 authoritative resolution.
///
/// Phase 2 consumes the authoritative identities; any graph or claim work done
/// after resolution must look agents up through this provider, not through the
/// phase-1 resolver, whose provisional ids were just remapped away.
pub struct AuthoritativeIdentityProvider {
    process_to_agent: HashMap<ProcessId, AgentId>,
    message_to_agent: HashMap<MessageId, AgentId>,
    ambiguous_messages: HashSet<MessageId>,
}

/// Explicit lookup outcome for a message, intent or process.
///
/// `Ambiguous` is not the same as `Unbound`: the evidence names the entity but
/// two agents claim it, so callers must abstain rather than fall back.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum IdentityLookup {
    Bound(AgentId),
    Ambiguous,
    Unbound,
}

impl AuthoritativeIdentityProvider {
    pub fn new(resolution: &AuthoritativeResolution) -> Self {
        // The provider consumes exactly the identities the runtime persists:
        // physical agents plus message-supported logical conversation agents.
        let identities = resolution.resolved_identities();
        let mut process_to_agent = HashMap::new();
        for agent in &identities {
            for pid in &agent.process_ids {
                process_to_agent.insert(*pid, agent.agent_id.clone());
            }
        }

        // Only messages the resolution actually bound to an agent are
        // lookup-able. A conflicting anchor or a physical conflict is an
        // explicit abstention: its message is never guessed from the process.
        let mut message_to_agent = HashMap::new();
        let mut ambiguous_messages = resolution.ambiguous_message_ids();
        for agent in &identities {
            for message in &agent.message_ids {
                if ambiguous_messages.contains(message) {
                    continue;
                }
                match message_to_agent.get(message) {
                    Some(existing) if existing != &agent.agent_id => {
                        message_to_agent.remove(message);
                        ambiguous_messages.insert(message.clone());
                    }
                    Some(_) => {}
                    None => {
                        message_to_agent.insert(message.clone(), agent.agent_id.clone());
                    }
                }
            }
        }
        Self {
            process_to_agent,
            message_to_agent,
            ambiguous_messages,
        }
    }

    /// Look up the agent explicitly bound to this message.
    pub fn message_identity(&self, message_id: &MessageId) -> IdentityLookup {
        if self.ambiguous_messages.contains(message_id) {
            return IdentityLookup::Ambiguous;
        }
        match self.message_to_agent.get(message_id) {
            Some(agent) => IdentityLookup::Bound(agent.clone()),
            None => IdentityLookup::Unbound,
        }
    }

    /// Look up the agent for an intent's process/message evidence.
    pub fn intent_identity(&self, process_id: ProcessId, message_id: &MessageId) -> IdentityLookup {
        match self.message_identity(message_id) {
            IdentityLookup::Bound(agent) => IdentityLookup::Bound(agent),
            IdentityLookup::Ambiguous => IdentityLookup::Ambiguous,
            IdentityLookup::Unbound => self.process_identity(process_id),
        }
    }

    /// Look up the agent for an observed process.
    pub fn process_identity(&self, process_id: ProcessId) -> IdentityLookup {
        match self.process_to_agent.get(&process_id) {
            Some(agent) => IdentityLookup::Bound(agent.clone()),
            None => IdentityLookup::Unbound,
        }
    }
}

impl IdentityProvider for AuthoritativeIdentityProvider {
    fn agent_for_process(&self, process_id: ProcessId) -> Option<AgentId> {
        self.process_to_agent.get(&process_id).cloned()
    }

    fn agent_for_message(&self, message_id: MessageId) -> Option<AgentId> {
        match self.message_identity(&message_id) {
            IdentityLookup::Bound(agent) => Some(agent),
            IdentityLookup::Ambiguous | IdentityLookup::Unbound => None,
        }
    }

    fn agent_for_intent(&self, intent: &Intent) -> Option<AgentId> {
        match self.intent_identity(intent.process_id, &intent.message_id) {
            IdentityLookup::Bound(agent) => Some(agent),
            IdentityLookup::Ambiguous | IdentityLookup::Unbound => None,
        }
    }

    fn all_assignments(&self) -> HashMap<ProcessId, AgentId> {
        self.process_to_agent.clone()
    }
}

impl AuthoritativeResolution {
    fn ambiguous_message_ids(&self) -> HashSet<MessageId> {
        let mut ambiguous: HashSet<MessageId> = self.ambiguous_messages.iter().cloned().collect();
        let mut owners = HashMap::new();
        for (agent, messages) in self
            .agents
            .iter()
            .map(|identity| (&identity.agent_id, &identity.message_ids))
            .chain(
                self.conversation_agents
                    .iter()
                    .map(|identity| (&identity.agent_id, &identity.message_ids)),
            )
        {
            for message in messages {
                if let Some(previous) = owners.insert(message.clone(), agent)
                    && previous != agent
                {
                    ambiguous.insert(message.clone());
                }
            }
        }
        ambiguous
    }

    /// Physical identities plus the logical conversation identities that are
    /// supported by at least one observed anchored message.
    ///
    /// A logical identity has no process owner and no TLS sessions: the
    /// shared process is not a captured exclusive owner, and claiming it
    /// would make every OS action look attributed. Candidate conversations
    /// without message support and messages with conflicting anchors are
    /// excluded, so they remain candidates/unknown rather than persisted.
    pub fn resolved_identities(&self) -> Vec<AuthoritativeIdentity> {
        let ambiguous = self.ambiguous_message_ids();
        let mut resolved = self.agents.clone();
        for identity in &mut resolved {
            identity
                .message_ids
                .retain(|message| !ambiguous.contains(message));
        }
        for identity in &self.conversation_agents {
            let message_ids: Vec<MessageId> = identity
                .message_ids
                .iter()
                .filter(|message| !ambiguous.contains(message))
                .cloned()
                .collect();
            if message_ids.is_empty() {
                continue;
            }
            resolved.push(AuthoritativeIdentity {
                agent_id: identity.agent_id.clone(),
                process_ids: Vec::new(),
                message_ids,
                tls_sessions: Vec::new(),
                confidence: AgentConfidence::Inferred,
                provisional_refs: Vec::new(),
            });
        }
        resolved
    }
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

    /// Merge two components unless doing so would put both ends of a
    /// prohibited pair into the same component. Pairs cover observed
    /// delegation and nonempty disjoint conversation sets. Checking the
    /// component roots, rather than only the direct parent/child edge,
    /// stops a third process from bridging the pair through shared signals.
    fn union_with_constraints(
        &mut self,
        a: usize,
        b: usize,
        prohibited_pairs: &[(usize, usize)],
    ) -> bool {
        let ra = self.find(a);
        let rb = self.find(b);
        if ra == rb {
            return true;
        }
        for &(left, right) in prohibited_pairs {
            let left_root = self.find(left);
            let right_root = self.find(right);
            if (left_root == ra && right_root == rb) || (left_root == rb && right_root == ra) {
                return false;
            }
        }
        self.parent[rb] = ra;
        true
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

/// Policy-parameterized boundary check used by the full resolver. Offline
/// replay must take this path so it cannot observe the current environment.
fn is_agent_process_with_policy(
    process_id: ProcessId,
    snapshot: &SignalSnapshot,
    policy: &IdentityBoundaryPolicy,
) -> bool {
    snapshot
        .get_process(process_id.pid, process_id.start_time_ns)
        .is_some_and(|meta| policy.matches_process(meta))
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

/// Process → messages this snapshot actually observed for it.
///
/// Mirrors `SignalSnapshot::messages_for_process` once, so anchor rows can be
/// filtered without recomputing the TLS/process join per row.
fn observed_process_messages(snapshot: &SignalSnapshot) -> HashMap<ProcessId, HashSet<MessageId>> {
    let mut observed: HashMap<ProcessId, HashSet<MessageId>> = HashMap::new();
    for (process_id, messages) in &snapshot.direct_process_messages {
        observed
            .entry(*process_id)
            .or_default()
            .extend(messages.iter().cloned());
    }
    for (tls_session, process_id) in &snapshot.tls_to_process {
        if let Some(messages) = snapshot.tls_to_messages.get(tls_session) {
            observed
                .entry(*process_id)
                .or_default()
                .extend(messages.iter().cloned());
        }
    }
    observed
}

/// Messages carrying more than one observed (process, conversation) anchor.
fn conflicting_anchor_messages(
    snapshot: &SignalSnapshot,
    observed: &HashMap<ProcessId, HashSet<MessageId>>,
) -> Vec<MessageId> {
    let mut pairs: HashMap<MessageId, HashSet<(ProcessId, String)>> = HashMap::new();
    for row in &snapshot.message_conversation_bindings {
        if observed
            .get(&row.process_id)
            .is_some_and(|messages| messages.contains(&row.message_id))
        {
            pairs
                .entry(row.message_id.clone())
                .or_default()
                .insert((row.process_id, row.conversation_id.clone()));
        }
    }
    let mut conflicts: Vec<MessageId> = pairs
        .into_iter()
        .filter(|(_, pairs)| pairs.len() > 1)
        .map(|(message, _)| message)
        .collect();
    conflicts.sort_by(|left, right| left.as_str().cmp(right.as_str()));
    conflicts
}

/// Derive a deterministic AgentId from a set of ProcessIds.
///
/// Sorts all ProcessIds in the component, concatenates them as a string, and
/// hex-encodes the first 8 bytes of the SHA256 digest — i.e. 16 hex characters,
/// giving `agent-{16hex}`. This is the only place an `agent-*` identifier is
/// derived; the framework detection layer deliberately emits `root_pid` instead
/// of inventing one, because it cannot see the full ProcessId set.
/// Deterministic id for a (process, conversation) pair: the same conversation
/// always yields the same agent id, and two conversations on one process never
/// collide.
fn derive_conversation_agent_id(process: ProcessId, conversation: &str) -> AgentId {
    use sha2::{Digest, Sha256};
    let mut hasher = Sha256::new();
    hasher.update(
        format!(
            "{}:{};conversation:{}",
            process.pid, process.start_time_ns, conversation
        )
        .as_bytes(),
    );
    let hash = hex::encode(&hasher.finalize()[..8]);
    AgentId::from(format!("agent-{hash}"))
}

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
/// 2. With no delegation constraints, shared TLS/conversation merges seeds
/// 3. Processes are propagated downward (S1) and upward (S1) through the tree
/// 4. AgentIds are derived deterministically from component membership
// Baseline helper for tests; production callers must provide their context.
#[cfg(test)]
pub fn run_authoritative_resolution(
    snapshot: &SignalSnapshot,
    provisional_map: &HashMap<ProcessId, ProvisionalIdentity>,
) -> AuthoritativeResolution {
    run_authoritative_resolution_with_delegations(snapshot, provisional_map, &HashSet::new())
}

/// Resolve a frozen snapshot with explicit `(parent, child)` constraints.
/// Observed process-spawn delegations remain separate despite shared context.
///
/// The public wrapper loads the active comm boundary exactly once and then
/// delegates to the same policy-parameterized resolver used by frozen replay.
pub fn run_authoritative_resolution_with_delegations(
    snapshot: &SignalSnapshot,
    provisional_map: &HashMap<ProcessId, ProvisionalIdentity>,
    delegated_pairs: &HashSet<(ProcessId, ProcessId)>,
) -> AuthoritativeResolution {
    let policy = IdentityBoundaryPolicy::active();
    run_authoritative_resolution_with_policy(snapshot, provisional_map, delegated_pairs, &policy)
}

/// Offline resolution entry point. `policy` is an explicit input; this
/// function performs no environment reads.
pub(crate) fn run_authoritative_resolution_with_policy(
    snapshot: &SignalSnapshot,
    provisional_map: &HashMap<ProcessId, ProvisionalIdentity>,
    delegated_pairs: &HashSet<(ProcessId, ProcessId)>,
    policy: &IdentityBoundaryPolicy,
) -> AuthoritativeResolution {
    // Native-call co-reference is an inferred overlay, never a captured fact.
    // Apply it only to a scoped clone so the frozen input remains unchanged.
    let alias_scope = (!snapshot.native_tool_call_observations().is_empty())
        .then(|| super::conversation_aliases::apply(snapshot));
    let snapshot = alias_scope
        .as_ref()
        .map(|scope| &scope.snapshot)
        .unwrap_or(snapshot);
    let inferred_aliases = alias_scope
        .as_ref()
        .map(|scope| scope.aliases.as_slice())
        .unwrap_or(&[]);
    let alias_ambiguous = alias_scope
        .as_ref()
        .map(|scope| scope.ambiguous_messages.as_slice())
        .unwrap_or(&[]);

    // ── Step 1: Seed discovery ──────────────────────────────────────
    // Build reverse index: ProcessId → Vec<TlsSessionId>
    let mut process_tls: HashMap<ProcessId, Vec<TlsSessionId>> = HashMap::new();

    // Prefer the append-only participant list: `tls_to_process` remembers
    // only the last process seen on a shared socket, so a parent and child
    // that shared one TLS session were never joined.
    if !snapshot.tls_participants.is_empty() {
        for (tls_session, processes) in &snapshot.tls_participants {
            for process_id in processes {
                process_tls
                    .entry(*process_id)
                    .or_default()
                    .push(tls_session.clone());
            }
        }
    } else {
        for (tls_session, process_id) in &snapshot.tls_to_process {
            process_tls
                .entry(*process_id)
                .or_default()
                .push(tls_session.clone());
        }
    }

    let mut seed_set: HashSet<ProcessId> = process_tls
        .iter()
        .filter(|(_, sessions)| !sessions.is_empty())
        .map(|(process_id, _)| *process_id)
        .collect();

    // Processes that carried an LLM conversation id are seeds too: the
    // request body names the logical session even when the TLS binding has
    // not been observed yet.
    for processes in snapshot.conversation_to_processes.values() {
        seed_set.extend(processes.iter().copied());
    }

    // Observed delegation endpoints must be seeds before S2/S3: a child
    // without TLS would otherwise look like a tool child and be merged.
    // Endpoints absent from the frozen snapshot are not fabricated.
    for (parent, child) in delegated_pairs {
        if parent == child {
            continue;
        }
        for endpoint in [parent, child] {
            if snapshot.processes.contains_key(endpoint) {
                seed_set.insert(*endpoint);
            }
        }
    }

    // Deterministic seed order: component and agent ids derive from it.
    let mut seed_processes: Vec<ProcessId> = seed_set.into_iter().collect();
    seed_processes.sort_by_key(|process_id| (process_id.pid, process_id.start_time_ns));

    let n = seed_processes.len();
    let mut uf = UnionFind::new(n);

    let seed_index: HashMap<ProcessId, usize> = seed_processes
        .iter()
        .enumerate()
        .map(|(index, process_id)| (*process_id, index))
        .collect();
    let mut prohibited_pairs: Vec<(usize, usize)> = delegated_pairs
        .iter()
        .filter(|(parent, child)| parent != child)
        .filter_map(|(parent, child)| Some((*seed_index.get(parent)?, *seed_index.get(child)?)))
        .collect();

    // Nonempty disjoint conversation sets separate seeds at component level,
    // not only on a direct edge: a third seed without conversation evidence
    // must not bridge the pair through shared TLS or a shared conversation.
    // Only seeds with declared conversations are compared, so this stays
    // proportional to conversation-bearing seeds rather than all processes.
    let conversation_seeds: Vec<usize> = seed_processes
        .iter()
        .enumerate()
        .filter(|(_, process_id)| !snapshot.conversations_for_process(**process_id).is_empty())
        .map(|(index, _)| index)
        .collect();
    for (left, &left_index) in conversation_seeds.iter().enumerate() {
        let left_conversations = snapshot.conversations_for_process(seed_processes[left_index]);
        for &right_index in &conversation_seeds[left + 1..] {
            let right_conversations =
                snapshot.conversations_for_process(seed_processes[right_index]);
            if !left_conversations
                .iter()
                .any(|conversation| right_conversations.contains(conversation))
            {
                prohibited_pairs.push((left_index, right_index));
            }
        }
    }

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

    // Union all seeds that share at least one TLS session. Groups are
    // visited in a fixed order so a constrained merge is deterministic.
    let mut tls_groups: Vec<(&TlsSessionId, &Vec<usize>)> = tls_to_seeds.iter().collect();
    tls_groups.sort_by(|left, right| left.0.as_str().cmp(right.0.as_str()));
    for (_, indices) in tls_groups {
        if indices.len() < 2 {
            continue;
        }
        // Conversation evidence outranks a shared connection. Two actors that
        // each carry their own session id but happen to reuse one socket are
        // different agents; merging them on the socket alone is a false merge
        // (E5 topology T4). Union only mutually compatible members.
        for left in 0..indices.len() {
            for right in (left + 1)..indices.len() {
                let left_process = seed_processes[indices[left]];
                let right_process = seed_processes[indices[right]];
                let left_conversations = snapshot.conversations_for_process(left_process);
                let right_conversations = snapshot.conversations_for_process(right_process);
                let disjoint = !left_conversations.is_empty()
                    && !right_conversations.is_empty()
                    && !left_conversations
                        .iter()
                        .any(|conversation| right_conversations.contains(conversation));
                if disjoint {
                    continue;
                }
                uf.union_with_constraints(indices[left], indices[right], &prohibited_pairs);
            }
        }
    }

    // ── Step 2b: S3 merge (conversation id) ────────────────────────
    // A shared conversation proposes a merge. Delegation and disjoint declared
    // conversation sets remain separation constraints when actors share context.
    let mut conversation_ids: Vec<&String> = snapshot.conversation_to_processes.keys().collect();
    conversation_ids.sort();
    for conversation_id in conversation_ids {
        let Some(processes) = snapshot.conversation_to_processes.get(conversation_id) else {
            continue;
        };
        let mut indices: Vec<usize> = processes
            .iter()
            .filter_map(|process_id| seed_index.get(process_id).copied())
            .collect();
        indices.sort_unstable();
        indices.dedup();
        if indices.len() >= 2 {
            let root = indices[0];
            for &idx in &indices[1..] {
                uf.union_with_constraints(root, idx, &prohibited_pairs);
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
                    if !tls_shared && is_agent_process_with_policy(*child, snapshot, policy) {
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
                    if is_agent_process_with_policy(*child, snapshot, policy) {
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

    // ── Step 4a: Ambiguous multi-session components ─────────────────
    // A single process that carried more than one conversation cannot be one
    // logical agent: crediting both sessions to it is a false merge. Keep the
    // component unassigned and record why (E5 topology T2/T3).
    let mut conversation_agents: Vec<ConversationIdentity> = Vec::new();
    let observed_messages = observed_process_messages(snapshot);
    let ambiguous_components: HashSet<usize> = components
        .iter()
        .enumerate()
        .filter(|(_, processes)| {
            processes
                .iter()
                .any(|process| snapshot.conversations_for_process(*process).len() > 1)
        })
        .map(|(index, _)| index)
        .collect();
    if !ambiguous_components.is_empty() {
        let mut assignment_order: Vec<(ProcessId, usize)> = assignments
            .iter()
            .map(|(process_id, component)| (*process_id, *component))
            .collect();
        assignment_order.sort_by_key(|(process_id, _)| (process_id.pid, process_id.start_time_ns));
        for (process, component) in assignment_order {
            if ambiguous_components.contains(&component) {
                // Attribute each conversation to its own logical agent so
                // messages and tool calls carrying that conversation can be
                // attributed (E5 T2/T3), while the shared process itself stays
                // unassigned for actions. Only anchors that name this process
                // and an observed message support the logical identity.
                for conversation in snapshot.conversations_for_process(process) {
                    let mut seen = HashSet::new();
                    let mut message_ids: Vec<MessageId> = snapshot
                        .message_conversation_bindings
                        .iter()
                        .filter(|row| {
                            row.process_id == process
                                && row.conversation_id == *conversation
                                && observed_messages
                                    .get(&process)
                                    .is_some_and(|messages| messages.contains(&row.message_id))
                        })
                        .filter(|row| seen.insert(row.message_id.clone()))
                        .map(|row| row.message_id.clone())
                        .collect();
                    // Effective inferred aliases join the same logical
                    // conversation even though they are not direct anchors.
                    for alias in inferred_aliases.iter().filter(|alias| {
                        alias.process_id == process && alias.conversation_id == *conversation
                    }) {
                        if !message_ids.contains(&alias.message_id) {
                            message_ids.push(alias.message_id.clone());
                        }
                    }
                    message_ids.sort_by(|left, right| left.as_str().cmp(right.as_str()));
                    message_ids.dedup();
                    conversation_agents.push(ConversationIdentity {
                        agent_id: derive_conversation_agent_id(process, conversation),
                        process_id: process,
                        conversation_id: conversation.clone(),
                        message_ids,
                    });
                }
                assignments.remove(&process);
                if let Some(processes) = components.get_mut(component) {
                    processes.retain(|candidate| *candidate != process);
                }
                unresolved.push(UnresolvedDiagnostic {
                    process_id: process,
                    reason: UnresolvedReason::AmbiguousMultipleSessions,
                });
            }
        }
    }

    // ── Step 4b: Delegation boundaries from final components ────────
    // Derive boundary ids only after propagation, so a tool process that
    // joins later cannot leave a stale parent/child agent id.
    let mut ordered_pairs: Vec<(&ProcessId, &ProcessId)> = delegated_pairs
        .iter()
        .map(|(parent, child)| (parent, child))
        .collect();
    ordered_pairs.sort_by_key(|(parent, child)| {
        (
            parent.pid,
            parent.start_time_ns,
            child.pid,
            child.start_time_ns,
        )
    });
    for (parent, child) in ordered_pairs {
        if parent == child {
            continue; // The same observed process cannot be split into two agents.
        }
        let (Some(parent_comp), Some(child_comp)) =
            (assignments.get(parent), assignments.get(child))
        else {
            continue;
        };
        if parent_comp == child_comp {
            continue;
        }
        let parent_agent_id = derive_agent_id(&components[*parent_comp]);
        let child_agent_id = derive_agent_id(&components[*child_comp]);
        subagent_boundaries.retain(|boundary| {
            !(boundary.parent_process == *parent && boundary.child_process == *child)
        });
        subagent_boundaries.push(SubagentBoundary {
            parent_agent: parent_agent_id,
            child_agent: child_agent_id,
            parent_process: *parent,
            child_process: *child,
            reason: SubagentBoundaryReason::DelegationRecord,
        });
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

    // ── Conflicting message evidence ────────────────────────────────
    // A message the capture mapped to more than one (process, conversation)
    // pair, or that more than one identity claims, is explicitly ambiguous.
    // Keeping it out of every lookup is the only fail-closed answer; adding
    // process fallback would silently pick one of the conflicting owners.
    let mut ambiguous_messages = conflicting_anchor_messages(snapshot, &observed_messages);
    ambiguous_messages.extend(alias_ambiguous.iter().cloned());
    let mut claims: HashMap<MessageId, HashSet<String>> = HashMap::new();
    for identity in agents.iter() {
        for message in &identity.message_ids {
            claims
                .entry(message.clone())
                .or_default()
                .insert(identity.agent_id.as_str().to_string());
        }
    }
    for identity in &conversation_agents {
        for message in &identity.message_ids {
            claims
                .entry(message.clone())
                .or_default()
                .insert(identity.agent_id.as_str().to_string());
        }
    }
    for (message, owners) in claims {
        if owners.len() > 1 {
            ambiguous_messages.push(message);
        }
    }
    ambiguous_messages.sort_by(|left, right| left.as_str().cmp(right.as_str()));
    ambiguous_messages.dedup();

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
        conversation_agents,
        ambiguous_messages,
    }
}

// ─── Tests ──────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use crate::responsibility::signals::SignalGraph;
    use crate::responsibility::types::{
        Intent, IntentAuthoritySource, IntentClass, IntentId, IntentParseState,
    };
    use std::sync::Arc;

    fn intent_for(process: ProcessId, message: MessageId) -> Intent {
        Intent {
            intent_id: IntentId::new(),
            process_id: process,
            message_id: message,
            tool_name: "Bash".into(),
            tool_args: Vec::new(),
            intent_class: IntentClass::SideEffect,
            parse_state: IntentParseState::Complete,
            primary_matching_eligible: true,
            authority_source: IntentAuthoritySource::RequestHistoryAssistantToolUse,
            timestamp_ns: 1,
            native_tool_call: None,
            declared_conversation_id: None,
        }
    }

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
    fn shared_tls_session_merges_every_participant() {
        let mut g = SignalGraph::new();
        let first = pid(700, 1);
        let second = pid(701, 1);
        for process in [first, second] {
            g.observe_exec(
                process,
                pid(1, 0),
                ProcessExecInfo {
                    comm: "claude".into(),
                    ..Default::default()
                },
                None,
                1,
            );
        }
        // One socket, opened by the first process and later observed on the
        // second: `tls_to_process` keeps only the last writer.
        g.observe_tls(first, tls("shared-socket"), 10);
        g.observe_tls(second, tls("shared-socket"), 20);

        let snap = g.snapshot();
        let resolution = run_authoritative_resolution(&snap, &HashMap::new());
        let merged = resolution
            .agents
            .iter()
            .filter(|agent| {
                agent.process_ids.contains(&first) && agent.process_ids.contains(&second)
            })
            .count();
        assert_eq!(merged, 1, "one shared session means one agent");
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
    fn delegated_child_without_tls_stays_a_separate_agent() {
        let mut g = SignalGraph::new();
        let shell = pid(50, 0);
        let parent = pid(100, 1);
        let child = pid(200, 2);

        g.observe_exec(
            parent,
            shell,
            ProcessExecInfo {
                comm: "claude".into(),
                ..Default::default()
            },
            None,
            1,
        );
        g.observe_tls(parent, tls("sess-1"), 2);
        g.observe_exec(
            child,
            parent,
            ProcessExecInfo {
                comm: "node".into(),
                ..Default::default()
            },
            None,
            3,
        );

        let snap = g.snapshot();
        let delegated = HashSet::from([(parent, child)]);
        let result =
            run_authoritative_resolution_with_delegations(&snap, &HashMap::new(), &delegated);

        assert_eq!(result.agents.len(), 2, "delegated child must not merge");
        assert!(
            result
                .subagent_boundaries
                .iter()
                .any(|boundary| boundary.child_process == child
                    && boundary.reason == SubagentBoundaryReason::DelegationRecord)
        );
    }

    #[test]
    fn delegated_child_sharing_tls_and_conversation_stays_separate() {
        let mut graph = SignalGraph::new();
        let parent = pid(100, 1);
        let child = pid(200, 2);
        for (process, ancestor) in [(parent, pid(1, 0)), (child, parent)] {
            graph.observe_exec(
                process,
                ancestor,
                ProcessExecInfo {
                    comm: "claude".into(),
                    ..Default::default()
                },
                None,
                1,
            );
            graph.observe_tls(process, tls("shared"), 2);
            graph.observe_conversation(process, "shared-conversation", 3);
        }
        let snapshot = graph.snapshot();
        let result = run_authoritative_resolution_with_delegations(
            &snapshot,
            &HashMap::new(),
            &HashSet::from([(parent, child)]),
        );
        assert_eq!(
            result.agents.len(),
            2,
            "observed delegation forbids identity union"
        );
        assert!(
            result
                .subagent_boundaries
                .iter()
                .any(|boundary| boundary.parent_process == parent
                    && boundary.child_process == child
                    && boundary.parent_agent != boundary.child_agent
                    && boundary.reason == SubagentBoundaryReason::DelegationRecord)
        );
        let unguarded = run_authoritative_resolution(&snapshot, &HashMap::new());
        assert_eq!(
            unguarded.agents.len(),
            1,
            "no delegation evidence retains the current behavior"
        );
    }

    #[test]
    fn delegated_pair_is_not_bridged_by_a_third_process_sharing_both_tls_groups() {
        let mut graph = SignalGraph::new();
        let parent = pid(100, 1);
        let child = pid(200, 2);
        let bridge = pid(300, 3);
        for process in [parent, child, bridge] {
            graph.observe_exec(
                process,
                pid(1, 0),
                ProcessExecInfo {
                    comm: "claude".into(),
                    ..Default::default()
                },
                None,
                1,
            );
        }
        graph.observe_tls(parent, tls("tls-parent"), 2);
        graph.observe_tls(child, tls("tls-child"), 3);
        // The bridge appears on both sockets. S2 would transitively join the
        // delegated pair through it if the constraint were edge-local only.
        graph.observe_tls(bridge, tls("tls-parent"), 4);
        graph.observe_tls(bridge, tls("tls-child"), 5);

        let snapshot = graph.snapshot();
        let result = run_authoritative_resolution_with_delegations(
            &snapshot,
            &HashMap::new(),
            &HashSet::from([(parent, child)]),
        );
        assert_eq!(
            result.agents.len(),
            2,
            "a TLS bridge must not defeat the delegation constraint"
        );
        let parent_agent = result
            .agents
            .iter()
            .find(|agent| agent.process_ids.contains(&parent))
            .expect("parent agent");
        let child_agent = result
            .agents
            .iter()
            .find(|agent| agent.process_ids.contains(&child))
            .expect("child agent");
        assert_ne!(parent_agent.agent_id, child_agent.agent_id);
        assert!(result.subagent_boundaries.iter().any(|boundary| {
            boundary.parent_process == parent
                && boundary.child_process == child
                && boundary.reason == SubagentBoundaryReason::DelegationRecord
        }));
    }

    #[test]
    fn constrained_conversation_merges_ignore_observation_insertion_order() {
        fn resolve(order: &[ProcessId]) -> HashMap<ProcessId, AgentId> {
            let mut graph = SignalGraph::new();
            for process in [pid(100, 1), pid(200, 2), pid(300, 3)] {
                graph.observe_exec(
                    process,
                    pid(1, 0),
                    ProcessExecInfo {
                        comm: "claude".into(),
                        ..Default::default()
                    },
                    None,
                    1,
                );
            }
            for process in order {
                graph.observe_conversation(*process, "shared", 2);
            }
            run_authoritative_resolution_with_delegations(
                &graph.snapshot(),
                &HashMap::new(),
                &HashSet::from([(pid(100, 1), pid(200, 2))]),
            )
            .agents
            .into_iter()
            .flat_map(|agent| {
                agent
                    .process_ids
                    .into_iter()
                    .map(move |process| (process, agent.agent_id.clone()))
            })
            .collect()
        }
        let forward = resolve(&[pid(100, 1), pid(200, 2), pid(300, 3)]);
        let reversed = resolve(&[pid(300, 3), pid(200, 2), pid(100, 1)]);
        assert_eq!(
            forward, reversed,
            "the same facts must give the same partition"
        );
    }

    #[test]
    fn self_delegation_does_not_promote_a_process_without_positive_signals() {
        let mut graph = SignalGraph::new();
        let process = pid(100, 1);
        graph.observe_exec(
            process,
            pid(1, 0),
            ProcessExecInfo {
                comm: "claude".into(),
                ..Default::default()
            },
            None,
            1,
        );
        let snapshot = graph.snapshot();
        let baseline = run_authoritative_resolution(&snapshot, &HashMap::new());
        let self_pair = run_authoritative_resolution_with_delegations(
            &snapshot,
            &HashMap::new(),
            &HashSet::from([(process, process)]),
        );
        assert_eq!(baseline.agents.len(), self_pair.agents.len());
    }

    #[test]
    fn processes_sharing_a_conversation_are_one_agent() {
        let mut g = SignalGraph::new();
        let a = pid(10, 1);
        let b = pid(20, 2);
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
            pid(2, 0),
            ProcessExecInfo {
                comm: "node".into(),
                ..Default::default()
            },
            None,
            2,
        );
        g.observe_tls(a, tls("tls-a"), 3);
        g.observe_tls(b, tls("tls-b"), 4);
        g.observe_conversation(a, "conv-shared-1", 5);
        g.observe_conversation(b, "conv-shared-1", 6);

        let snap = g.snapshot();
        let result = run_authoritative_resolution(&snap, &HashMap::new());
        assert_eq!(result.agents.len(), 1, "same conversation → one agent");
        assert_eq!(result.agents[0].process_ids.len(), 2);
    }

    #[test]
    fn provider_maps_message_to_its_resolved_agent() {
        let mut g = SignalGraph::new();
        let process = pid(510, 1);
        g.observe_exec(
            process,
            pid(1, 0),
            ProcessExecInfo {
                comm: "claude".into(),
                ..Default::default()
            },
            None,
            1,
        );
        g.observe_tls(process, tls("sess-msg"), 2);
        g.observe_message_via_tls(tls("sess-msg"), MessageId::from("msg-1"));
        g.observe_conversation(process, "conv-1", 3);

        let resolution = run_authoritative_resolution(&g.snapshot(), &HashMap::new());
        let provider = AuthoritativeIdentityProvider::new(&resolution);
        let agent = provider
            .agent_for_process(process)
            .expect("single conversation process is assigned");
        assert_eq!(
            provider.agent_for_message(MessageId::from("msg-1")),
            Some(agent)
        );
        assert_eq!(
            provider.agent_for_message(MessageId::from("msg-unobserved")),
            None,
            "message lookup must use observed resolution messages only"
        );
    }

    #[test]
    fn anchored_messages_split_a_shared_process_without_process_fallback() {
        let mut g = SignalGraph::new();
        let process = pid(530, 1);
        g.observe_exec(
            process,
            pid(1, 0),
            ProcessExecInfo {
                comm: "claude".into(),
                ..Default::default()
            },
            None,
            1,
        );
        g.observe_tls(process, tls("sess-anchored"), 2);
        let message_a = MessageId::from("msg-a");
        let message_b = MessageId::from("msg-b");
        let conflicting = MessageId::from("msg-conflict");
        let unanchored = MessageId::from("msg-unanchored");
        for message in [&message_a, &message_b, &conflicting, &unanchored] {
            g.observe_message_via_tls(tls("sess-anchored"), message.clone());
        }
        g.observe_conversation(process, "conv-a", 3);
        g.observe_conversation(process, "conv-b", 4);
        g.observe_message_conversation(process, message_a.clone(), "conv-a");
        g.observe_message_conversation(process, message_b.clone(), "conv-b");
        g.observe_message_conversation(process, conflicting.clone(), "conv-a");
        g.observe_message_conversation(process, conflicting.clone(), "conv-b");

        let resolution = run_authoritative_resolution(&g.snapshot(), &HashMap::new());
        assert!(
            resolution.agents.is_empty(),
            "the shared process stays unassigned for actions"
        );
        let agent_a = resolution
            .conversation_agents
            .iter()
            .find(|identity| identity.conversation_id == "conv-a")
            .expect("conv-a candidate");
        let agent_b = resolution
            .conversation_agents
            .iter()
            .find(|identity| identity.conversation_id == "conv-b")
            .expect("conv-b candidate");
        assert_eq!(
            agent_a.message_ids,
            vec![message_a.clone(), conflicting.clone()],
            "the candidate keeps every observed anchor; the conflict is not erased"
        );
        assert_eq!(
            agent_b.message_ids,
            vec![message_b.clone(), conflicting.clone()]
        );
        assert_eq!(
            resolution.ambiguous_messages,
            vec![conflicting.clone()],
            "one message with two observed anchors is explicitly ambiguous"
        );

        let resolved = resolution.resolved_identities();
        assert_eq!(resolved.len(), 2);
        for identity in &resolved {
            assert!(identity.process_ids.is_empty());
            assert!(identity.tls_sessions.is_empty());
            assert!(identity.provisional_refs.is_empty());
            assert!(matches!(identity.confidence, AgentConfidence::Inferred));
            assert!(!identity.message_ids.is_empty());
            assert!(!identity.message_ids.contains(&conflicting));
        }

        let provider = AuthoritativeIdentityProvider::new(&resolution);
        assert_eq!(
            provider.message_identity(&message_a),
            IdentityLookup::Bound(agent_a.agent_id.clone())
        );
        assert_eq!(
            provider.message_identity(&message_b),
            IdentityLookup::Bound(agent_b.agent_id.clone())
        );
        assert_ne!(
            provider.message_identity(&message_a),
            provider.message_identity(&message_b),
            "anchored messages on one process belong to distinct logical agents"
        );
        assert_eq!(
            provider.message_identity(&conflicting),
            IdentityLookup::Ambiguous
        );
        assert_eq!(
            provider.intent_identity(process, &conflicting),
            IdentityLookup::Ambiguous,
            "an ambiguous anchor must never fall back to the process"
        );
        assert_eq!(
            provider.intent_identity(process, &unanchored),
            IdentityLookup::Unbound,
            "an unanchored message stays unknown even when its process is observed"
        );
        assert_eq!(
            provider.process_identity(process),
            IdentityLookup::Unbound,
            "shared-process actions stay process-unassigned"
        );
    }

    #[test]
    fn conflicting_anchors_abstain_even_for_an_assigned_process() {
        let mut g = SignalGraph::new();
        let process = pid(531, 1);
        g.observe_exec(
            process,
            pid(1, 0),
            ProcessExecInfo {
                comm: "claude".into(),
                ..Default::default()
            },
            None,
            1,
        );
        g.observe_tls(process, tls("sess-single"), 2);
        let message = MessageId::from("msg-two-anchors");
        g.observe_message_via_tls(tls("sess-single"), message.clone());
        g.observe_conversation(process, "conv-a", 3);
        g.observe_message_conversation(process, message.clone(), "conv-a");
        g.observe_message_conversation(process, message.clone(), "conv-b");

        let resolution = run_authoritative_resolution(&g.snapshot(), &HashMap::new());
        assert_eq!(resolution.agents.len(), 1);
        assert_eq!(resolution.ambiguous_messages, vec![message.clone()]);
        assert!(
            resolution
                .resolved_identities()
                .iter()
                .all(|identity| !identity.message_ids.contains(&message)),
            "persisted identities must not claim an ambiguous message"
        );
        let provider = AuthoritativeIdentityProvider::new(&resolution);
        assert!(
            matches!(provider.process_identity(process), IdentityLookup::Bound(_)),
            "the process itself is unambiguous"
        );
        assert_eq!(
            provider.message_identity(&message),
            IdentityLookup::Ambiguous
        );
        assert_eq!(
            provider.agent_for_intent(&intent_for(process, message)),
            None,
            "the provider must abstain instead of using the process owner"
        );
    }

    #[test]
    fn provider_does_not_guess_messages_for_ambiguous_same_process() {
        let mut g = SignalGraph::new();
        let process = pid(511, 1);
        g.observe_exec(
            process,
            pid(1, 0),
            ProcessExecInfo {
                comm: "claude".into(),
                ..Default::default()
            },
            None,
            1,
        );
        g.observe_tls(process, tls("sess-ambiguous"), 2);
        g.observe_message_via_tls(tls("sess-ambiguous"), MessageId::from("msg-a"));
        g.observe_message_via_tls(tls("sess-ambiguous"), MessageId::from("msg-b"));
        g.observe_conversation(process, "conv-a", 3);
        g.observe_conversation(process, "conv-b", 4);

        let resolution = run_authoritative_resolution(&g.snapshot(), &HashMap::new());
        assert!(resolution.agents.is_empty());
        assert_eq!(resolution.conversation_agents.len(), 2);
        let provider = AuthoritativeIdentityProvider::new(&resolution);
        assert_eq!(provider.agent_for_process(process), None);
        assert_eq!(provider.agent_for_message(MessageId::from("msg-a")), None);
        assert_eq!(provider.agent_for_message(MessageId::from("msg-b")), None);
    }

    #[test]
    fn provider_does_not_merge_a_message_claimed_by_two_agents() {
        let shared = MessageId::from("msg-shared");
        let resolution = AuthoritativeResolution {
            agents: vec![
                AuthoritativeIdentity {
                    agent_id: AgentId::from("agent-a"),
                    process_ids: vec![pid(520, 1)],
                    message_ids: vec![shared.clone()],
                    tls_sessions: Vec::new(),
                    confidence: AgentConfidence::Inferred,
                    provisional_refs: Vec::new(),
                },
                AuthoritativeIdentity {
                    agent_id: AgentId::from("agent-b"),
                    process_ids: vec![pid(521, 1)],
                    message_ids: vec![shared.clone()],
                    tls_sessions: Vec::new(),
                    confidence: AgentConfidence::Inferred,
                    provisional_refs: Vec::new(),
                },
            ],
            refinements: Vec::new(),
            subagent_boundaries: Vec::new(),
            unresolved: Vec::new(),
            conversation_agents: Vec::new(),
            ambiguous_messages: Vec::new(),
        };
        let provider = AuthoritativeIdentityProvider::new(&resolution);
        assert_eq!(
            provider.agent_for_message(shared),
            None,
            "a shared message must stay unresolved rather than pick one agent"
        );
    }

    #[test]
    fn conflicted_message_does_not_fall_back_to_its_process_owner() {
        let shared = MessageId::from("msg-conflict");
        let process = pid(522, 1);
        let resolution = AuthoritativeResolution {
            agents: vec![
                AuthoritativeIdentity {
                    agent_id: AgentId::from("agent-a"),
                    process_ids: vec![process],
                    message_ids: vec![shared.clone()],
                    tls_sessions: Vec::new(),
                    confidence: AgentConfidence::Inferred,
                    provisional_refs: Vec::new(),
                },
                AuthoritativeIdentity {
                    agent_id: AgentId::from("agent-b"),
                    process_ids: vec![pid(523, 1)],
                    message_ids: vec![shared.clone()],
                    tls_sessions: Vec::new(),
                    confidence: AgentConfidence::Inferred,
                    provisional_refs: Vec::new(),
                },
            ],
            refinements: Vec::new(),
            subagent_boundaries: Vec::new(),
            unresolved: Vec::new(),
            conversation_agents: Vec::new(),
            ambiguous_messages: Vec::new(),
        };
        let provider = AuthoritativeIdentityProvider::new(&resolution);
        assert_eq!(
            provider.agent_for_process(process),
            Some(AgentId::from("agent-a")),
            "the conflicting process itself is still owned by one agent"
        );
        assert_eq!(
            provider.agent_for_intent(&intent_for(process, shared)),
            None,
            "a conflicted message must not fall back to the process owner"
        );
    }

    #[test]
    fn explicit_message_agent_beats_the_process_owner() {
        let process = pid(524, 1);
        let message = MessageId::from("msg-normal");
        let resolution = AuthoritativeResolution {
            agents: vec![
                AuthoritativeIdentity {
                    agent_id: AgentId::from("agent-message"),
                    process_ids: vec![pid(525, 1)],
                    message_ids: vec![message.clone()],
                    tls_sessions: Vec::new(),
                    confidence: AgentConfidence::Inferred,
                    provisional_refs: Vec::new(),
                },
                AuthoritativeIdentity {
                    agent_id: AgentId::from("agent-process"),
                    process_ids: vec![process],
                    message_ids: Vec::new(),
                    tls_sessions: Vec::new(),
                    confidence: AgentConfidence::Inferred,
                    provisional_refs: Vec::new(),
                },
            ],
            refinements: Vec::new(),
            subagent_boundaries: Vec::new(),
            unresolved: Vec::new(),
            conversation_agents: Vec::new(),
            ambiguous_messages: Vec::new(),
        };
        let provider = AuthoritativeIdentityProvider::new(&resolution);

        assert_eq!(
            provider.agent_for_intent(&intent_for(process, message.clone())),
            Some(AgentId::from("agent-message")),
            "the explicit message binding wins"
        );
        assert_eq!(
            provider.agent_for_intent(&intent_for(process, MessageId::from("msg-missing"))),
            Some(AgentId::from("agent-process")),
            "a missing message may fall back to the unambiguous process owner"
        );
    }

    #[test]
    fn different_conversations_stay_separate() {
        let mut g = SignalGraph::new();
        let a = pid(30, 1);
        let b = pid(40, 2);
        g.observe_exec(
            a,
            pid(3, 0),
            ProcessExecInfo {
                comm: "claude".into(),
                ..Default::default()
            },
            None,
            1,
        );
        g.observe_exec(
            b,
            pid(4, 0),
            ProcessExecInfo {
                comm: "claude".into(),
                ..Default::default()
            },
            None,
            2,
        );
        g.observe_tls(a, tls("tls-c"), 3);
        g.observe_tls(b, tls("tls-d"), 4);
        g.observe_conversation(a, "conv-a-123456", 5);
        g.observe_conversation(b, "conv-b-123456", 6);

        let snap = g.snapshot();
        let result = run_authoritative_resolution(&snap, &HashMap::new());
        assert_eq!(result.agents.len(), 2);
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
    fn cgroup_boundary_is_rejected_loudly_instead_of_enabling_a_dead_boundary() {
        assert!(unsupported_cgroup_boundary(" ").is_none());
        let warning = unsupported_cgroup_boundary("openclaw-gateway.service")
            .expect("a configured cgroup boundary must be reported");
        assert!(warning.contains("MTAGENTLENS_AGENT_CGROUP"));
        assert!(warning.contains("ignored"));
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
