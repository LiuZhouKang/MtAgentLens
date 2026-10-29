// SPDX-License-Identifier: Apache-2.0 OR MIT
//
// identity/view.rs — deterministic per-unit identity predictions.
//
// The view is a pure function of the frozen PRE-resolver input, the caller's
// unit cohort, the visibility condition and the frozen boundary policy. It
// never reads checkpoint identities, provisional assignments, detector graphs,
// references or the current environment.

use super::input::RawIdentityInput;
use super::policy::IdentityBoundaryPolicy;
use super::union_find::{
    AuthoritativeIdentityProvider, IdentityLookup, run_authoritative_resolution_with_policy,
};
use crate::responsibility::signals::signal_snapshot::SignalSnapshot;
use crate::responsibility::types::{AgentId, MessageId, ProcessId, UnresolvedReason};
use serde::{Deserialize, Serialize};
use std::collections::{HashMap, HashSet};
use std::fmt;

/// Bounded upward traversal for the process-only control conditions.
const CONTROL_MAX_ANCESTOR_DEPTH: usize = 32;

/// Signal visibility for one identity ablation condition.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum IdentityCondition {
    #[serde(rename = "pid-instance-only")]
    PidInstanceOnly,
    #[serde(rename = "pid-instance-plus-ppid")]
    PidInstancePlusPpid,
    #[serde(rename = "pid-instance-plus-tls")]
    PidInstancePlusTls,
    #[serde(rename = "pid-instance-plus-conversation")]
    PidInstancePlusConversation,
    #[serde(rename = "full")]
    Full,
}

impl IdentityCondition {
    /// Stable evaluation order (C0..C4).
    pub const ALL: [Self; 5] = [
        Self::PidInstanceOnly,
        Self::PidInstancePlusPpid,
        Self::PidInstancePlusTls,
        Self::PidInstancePlusConversation,
        Self::Full,
    ];

    pub fn as_str(self) -> &'static str {
        match self {
            Self::PidInstanceOnly => "pid-instance-only",
            Self::PidInstancePlusPpid => "pid-instance-plus-ppid",
            Self::PidInstancePlusTls => "pid-instance-plus-tls",
            Self::PidInstancePlusConversation => "pid-instance-plus-conversation",
            Self::Full => "full",
        }
    }
}

/// The three locally addressable unit kinds in the shared cohort.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum IdentityUnitKind {
    Action,
    Intent,
    Message,
}

impl IdentityUnitKind {
    fn namespace(self) -> &'static str {
        match self {
            Self::Action => "action:",
            Self::Intent => "intent:",
            Self::Message => "message:",
        }
    }
}

/// One unit to predict. `unit_id` is local to this view and always carries a
/// kind namespace; it is not a global evidence identifier.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct IdentityUnit {
    pub kind: IdentityUnitKind,
    pub unit_id: String,
    pub process_id: Option<ProcessId>,
    pub message_id: Option<MessageId>,
}

impl IdentityUnit {
    pub fn action(unit_id: impl Into<String>, process_id: ProcessId) -> Self {
        Self {
            kind: IdentityUnitKind::Action,
            unit_id: namespaced(IdentityUnitKind::Action, unit_id.into()),
            process_id: Some(process_id),
            message_id: None,
        }
    }

    pub fn intent(
        unit_id: impl Into<String>,
        process_id: ProcessId,
        message_id: MessageId,
    ) -> Self {
        Self {
            kind: IdentityUnitKind::Intent,
            unit_id: namespaced(IdentityUnitKind::Intent, unit_id.into()),
            process_id: Some(process_id),
            message_id: Some(message_id),
        }
    }

    pub fn message(
        unit_id: impl Into<String>,
        message_id: MessageId,
        process_id: Option<ProcessId>,
    ) -> Self {
        Self {
            kind: IdentityUnitKind::Message,
            unit_id: namespaced(IdentityUnitKind::Message, unit_id.into()),
            process_id,
            message_id: Some(message_id),
        }
    }
}

fn namespaced(kind: IdentityUnitKind, unit_id: String) -> String {
    let namespace = kind.namespace();
    if unit_id.starts_with(namespace) {
        unit_id
    } else {
        format!("{namespace}{unit_id}")
    }
}

/// Why a unit has no actor. This is a typed abstention, not a probability.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum IdentityUnknownReason {
    MissingProcessId,
    MissingMessageId,
    ParentGraphCycle,
    ParentTraversalDepthExceeded,
    AmbiguousMessage,
    MessageNotBound,
    AmbiguousMultipleSessions,
    NoPositiveSignal,
    NoProcessLineage,
    ProcessNotAssigned,
}

/// Per-unit actor partition or an explicit abstention.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "state", rename_all = "snake_case")]
pub enum UnitActor {
    Assigned { partition: String },
    Unknown { reason: IdentityUnknownReason },
}

impl UnitActor {
    pub fn partition(&self) -> Option<&str> {
        match self {
            Self::Assigned { partition } => Some(partition),
            Self::Unknown { .. } => None,
        }
    }
}

/// Explicit limits of a control condition, carried with the prediction.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum IdentityViewLimitation {
    /// C0/C1 are process-only control methods, not the full seeding algorithm.
    ProcessOnlyControl,
    NoAgentAncestor,
    ProcessMetadataMissing,
    /// The message/intent actor came from an inferred native-call
    /// co-reference, not from authentication or a direct capture anchor.
    InferredNativeConversationAlias,
}

/// A per-conversation candidate surfaced separately from unit truth.
///
/// A shared process can carry several conversations; these candidates do not
/// assign any message, intent or action to an agent.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ConversationCandidate {
    pub process_id: ProcessId,
    pub conversation_id: String,
    pub candidate_agent_id: AgentId,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct UnitIdentity {
    pub kind: IdentityUnitKind,
    pub unit_id: String,
    pub process_id: Option<ProcessId>,
    pub message_id: Option<MessageId>,
    pub actor: UnitActor,
    pub limitations: Vec<IdentityViewLimitation>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct IdentityView {
    pub condition: IdentityCondition,
    pub units: Vec<UnitIdentity>,
    pub conversation_candidates: Vec<ConversationCandidate>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum IdentityViewError {
    UnavailableIdentityInput,
    UnavailableIdentityPolicy,
    UnsupportedPolicySchema(String),
    InvalidRawInput(String),
    InvalidUnitCohort,
}

impl fmt::Display for IdentityViewError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::UnavailableIdentityInput => write!(
                f,
                "identity view unavailable: checkpoint has no frozen PRE-resolver input"
            ),
            Self::UnavailableIdentityPolicy => write!(
                f,
                "identity view unavailable: checkpoint has no frozen boundary policy"
            ),
            Self::UnsupportedPolicySchema(detail) => {
                write!(f, "identity view unavailable: {detail}")
            }
            Self::InvalidRawInput(detail) => {
                write!(f, "identity view unavailable: invalid raw input: {detail}")
            }
            Self::InvalidUnitCohort => write!(
                f,
                "identity view unavailable: unit addresses must be nonempty, kind-namespaced and unique"
            ),
        }
    }
}

impl std::error::Error for IdentityViewError {}

impl IdentityView {
    /// Predict one actor partition per unit under `condition`.
    pub fn resolve(
        raw: &RawIdentityInput,
        units: &[IdentityUnit],
        condition: IdentityCondition,
        policy: &IdentityBoundaryPolicy,
    ) -> Result<Self, IdentityViewError> {
        policy
            .validate()
            .map_err(IdentityViewError::UnsupportedPolicySchema)?;
        let mut seen = HashSet::new();
        for unit in units {
            if unit
                .unit_id
                .strip_prefix(unit.kind.namespace())
                .is_none_or(|suffix| suffix.trim().is_empty())
                || !seen.insert(unit.unit_id.as_str())
            {
                return Err(IdentityViewError::InvalidUnitCohort);
            }
        }
        let snapshot = raw
            .to_snapshot()
            .map_err(IdentityViewError::InvalidRawInput)?;

        match condition {
            IdentityCondition::PidInstanceOnly => Ok(process_control_view(
                &snapshot, units, condition, policy, false,
            )),
            IdentityCondition::PidInstancePlusPpid => Ok(process_control_view(
                &snapshot, units, condition, policy, true,
            )),
            IdentityCondition::PidInstancePlusTls
            | IdentityCondition::PidInstancePlusConversation
            | IdentityCondition::Full => Ok(full_view(raw, &snapshot, units, condition, policy)),
        }
    }
}

fn process_partition(process_id: ProcessId) -> String {
    format!("process:{}:{}", process_id.pid, process_id.start_time_ns)
}

fn agent_process_partition(process_id: ProcessId) -> String {
    format!(
        "agent-process:{}:{}",
        process_id.pid, process_id.start_time_ns
    )
}

enum AncestorLookup {
    Found(ProcessId),
    NoAgent { metadata_missing: bool },
    Cycle,
    DepthExceeded,
}

fn nearest_agent_ancestor(
    start: ProcessId,
    snapshot: &SignalSnapshot,
    policy: &IdentityBoundaryPolicy,
) -> AncestorLookup {
    let mut current = start;
    let mut visited = HashSet::new();
    let mut metadata_missing = false;
    let mut depth = 0usize;

    loop {
        if depth >= CONTROL_MAX_ANCESTOR_DEPTH {
            return AncestorLookup::DepthExceeded;
        }
        if !visited.insert(current) {
            return AncestorLookup::Cycle;
        }
        match snapshot.get_process(current.pid, current.start_time_ns) {
            Some(metadata) if policy.matches_process(metadata) => {
                return AncestorLookup::Found(current);
            }
            Some(_) => {}
            None => metadata_missing = true,
        }
        match snapshot.get_parent(current) {
            Some(parent) if parent != current => {
                current = parent;
                depth += 1;
            }
            Some(_) => return AncestorLookup::Cycle,
            None => return AncestorLookup::NoAgent { metadata_missing },
        }
    }
}

fn process_control_view(
    snapshot: &SignalSnapshot,
    units: &[IdentityUnit],
    condition: IdentityCondition,
    policy: &IdentityBoundaryPolicy,
    with_ppid: bool,
) -> IdentityView {
    let mut predicted = Vec::with_capacity(units.len());
    for unit in units {
        let mut limitations = vec![IdentityViewLimitation::ProcessOnlyControl];
        let actor = match (unit.process_id, with_ppid) {
            (None, _) => UnitActor::Unknown {
                reason: IdentityUnknownReason::MissingProcessId,
            },
            (Some(process_id), false) => UnitActor::Assigned {
                partition: process_partition(process_id),
            },
            (Some(process_id), true) => {
                match nearest_agent_ancestor(process_id, snapshot, policy) {
                    AncestorLookup::Found(agent_process) => UnitActor::Assigned {
                        partition: agent_process_partition(agent_process),
                    },
                    AncestorLookup::NoAgent { metadata_missing } => {
                        limitations.push(IdentityViewLimitation::NoAgentAncestor);
                        if metadata_missing {
                            limitations.push(IdentityViewLimitation::ProcessMetadataMissing);
                        }
                        UnitActor::Assigned {
                            partition: process_partition(process_id),
                        }
                    }
                    AncestorLookup::Cycle => UnitActor::Unknown {
                        reason: IdentityUnknownReason::ParentGraphCycle,
                    },
                    AncestorLookup::DepthExceeded => UnitActor::Unknown {
                        reason: IdentityUnknownReason::ParentTraversalDepthExceeded,
                    },
                }
            }
        };
        predicted.push(UnitIdentity {
            kind: unit.kind,
            unit_id: unit.unit_id.clone(),
            process_id: unit.process_id,
            message_id: unit.message_id.clone(),
            actor,
            limitations,
        });
    }
    finish(condition, predicted, Vec::new())
}

fn full_view(
    raw: &RawIdentityInput,
    snapshot: &SignalSnapshot,
    units: &[IdentityUnit],
    condition: IdentityCondition,
    policy: &IdentityBoundaryPolicy,
) -> IdentityView {
    let mut scoped = snapshot.clone();
    let delegated_pairs: HashSet<(ProcessId, ProcessId)> = match condition {
        IdentityCondition::PidInstancePlusTls => {
            // C2: no conversation veto and no delegation constraint. The TLS
            // and PPID/tree behavior of the production resolver is retained.
            scoped.conversation_to_processes.clear();
            scoped.process_to_conversations.clear();
            scoped.message_conversation_bindings.clear();
            scoped.native_tool_call_observations.clear();
            HashSet::new()
        }
        IdentityCondition::PidInstancePlusConversation => {
            // C3: conversation evidence is visible, delegation is not.
            HashSet::new()
        }
        IdentityCondition::Full => raw
            .delegations
            .iter()
            .map(|row| (row.parent, row.child))
            .collect(),
        IdentityCondition::PidInstanceOnly | IdentityCondition::PidInstancePlusPpid => {
            unreachable!("control conditions do not use the full resolver")
        }
    };

    let provisional = HashMap::new();
    let resolution =
        run_authoritative_resolution_with_policy(&scoped, &provisional, &delegated_pairs, policy);
    let provider = AuthoritativeIdentityProvider::new(&resolution);

    let mut unresolved: HashMap<ProcessId, IdentityUnknownReason> = HashMap::new();
    for diagnostic in &resolution.unresolved {
        let reason = match diagnostic.reason {
            UnresolvedReason::NoPositiveSignal => IdentityUnknownReason::NoPositiveSignal,
            UnresolvedReason::NoProcessLineage => IdentityUnknownReason::NoProcessLineage,
            UnresolvedReason::AmbiguousMultipleSessions => {
                IdentityUnknownReason::AmbiguousMultipleSessions
            }
        };
        match unresolved.get(&diagnostic.process_id) {
            Some(IdentityUnknownReason::AmbiguousMultipleSessions) => {}
            _ => {
                unresolved.insert(diagnostic.process_id, reason);
            }
        }
    }

    // A message is "assigned via the alias" only when a logical conversation
    // agent actually claims it; a single-conversation physical assignment does
    // not depend on the inference. C2 cleared the observations above, so this
    // set is empty there.
    let inferred_alias_messages: HashSet<MessageId> =
        super::conversation_aliases::inferred_aliases(&scoped)
            .0
            .into_iter()
            .filter(|alias| {
                resolution.conversation_agents.iter().any(|identity| {
                    identity.process_id == alias.process_id
                        && identity.conversation_id == alias.conversation_id
                        && identity.message_ids.contains(&alias.message_id)
                })
            })
            .map(|alias| alias.message_id)
            .collect();

    let mut predicted = Vec::with_capacity(units.len());
    for unit in units {
        let actor = match unit.kind {
            IdentityUnitKind::Message => message_actor(&provider, &unresolved, unit),
            IdentityUnitKind::Intent => intent_actor(&provider, &unresolved, unit),
            IdentityUnitKind::Action => process_actor(&provider, &unresolved, unit),
        };
        let mut limitations = Vec::new();
        if matches!(
            unit.kind,
            IdentityUnitKind::Message | IdentityUnitKind::Intent
        ) && actor.partition().is_some()
            && unit
                .message_id
                .as_ref()
                .is_some_and(|message| inferred_alias_messages.contains(message))
        {
            limitations.push(IdentityViewLimitation::InferredNativeConversationAlias);
        }
        predicted.push(UnitIdentity {
            kind: unit.kind,
            unit_id: unit.unit_id.clone(),
            process_id: unit.process_id,
            message_id: unit.message_id.clone(),
            actor,
            limitations,
        });
    }

    let mut candidates: Vec<ConversationCandidate> = resolution
        .conversation_agents
        .iter()
        .map(|identity| ConversationCandidate {
            process_id: identity.process_id,
            conversation_id: identity.conversation_id.clone(),
            candidate_agent_id: identity.agent_id.clone(),
        })
        .collect();
    candidates.sort_by(|left, right| {
        (
            left.process_id.pid,
            left.process_id.start_time_ns,
            left.conversation_id.as_str(),
        )
            .cmp(&(
                right.process_id.pid,
                right.process_id.start_time_ns,
                right.conversation_id.as_str(),
            ))
    });

    finish(condition, predicted, candidates)
}

fn finish(
    condition: IdentityCondition,
    mut units: Vec<UnitIdentity>,
    conversation_candidates: Vec<ConversationCandidate>,
) -> IdentityView {
    units.sort_by(|left, right| {
        left.kind
            .cmp(&right.kind)
            .then_with(|| left.unit_id.cmp(&right.unit_id))
    });
    IdentityView {
        condition,
        units,
        conversation_candidates,
    }
}

fn bound_partition(agent: AgentId) -> UnitActor {
    UnitActor::Assigned {
        partition: agent.as_str().to_string(),
    }
}

fn message_actor(
    provider: &AuthoritativeIdentityProvider,
    unresolved: &HashMap<ProcessId, IdentityUnknownReason>,
    unit: &IdentityUnit,
) -> UnitActor {
    let Some(message_id) = unit.message_id.as_ref() else {
        return UnitActor::Unknown {
            reason: IdentityUnknownReason::MissingMessageId,
        };
    };
    match provider.message_identity(message_id) {
        IdentityLookup::Bound(agent) => bound_partition(agent),
        IdentityLookup::Ambiguous => UnitActor::Unknown {
            reason: IdentityUnknownReason::AmbiguousMessage,
        },
        IdentityLookup::Unbound => {
            let ambiguous_process = unit
                .process_id
                .and_then(|process_id| unresolved.get(&process_id))
                .is_some_and(|reason| *reason == IdentityUnknownReason::AmbiguousMultipleSessions);
            UnitActor::Unknown {
                reason: if ambiguous_process {
                    IdentityUnknownReason::AmbiguousMultipleSessions
                } else {
                    IdentityUnknownReason::MessageNotBound
                },
            }
        }
    }
}

fn intent_actor(
    provider: &AuthoritativeIdentityProvider,
    unresolved: &HashMap<ProcessId, IdentityUnknownReason>,
    unit: &IdentityUnit,
) -> UnitActor {
    let Some(process_id) = unit.process_id else {
        return UnitActor::Unknown {
            reason: IdentityUnknownReason::MissingProcessId,
        };
    };
    let lookup = match unit.message_id.as_ref() {
        Some(message_id) => provider.intent_identity(process_id, message_id),
        None => provider.process_identity(process_id),
    };
    match lookup {
        IdentityLookup::Bound(agent) => bound_partition(agent),
        IdentityLookup::Ambiguous => UnitActor::Unknown {
            reason: IdentityUnknownReason::AmbiguousMessage,
        },
        IdentityLookup::Unbound => UnitActor::Unknown {
            reason: unassigned_process_reason(unresolved, process_id),
        },
    }
}

fn process_actor(
    provider: &AuthoritativeIdentityProvider,
    unresolved: &HashMap<ProcessId, IdentityUnknownReason>,
    unit: &IdentityUnit,
) -> UnitActor {
    let Some(process_id) = unit.process_id else {
        return UnitActor::Unknown {
            reason: IdentityUnknownReason::MissingProcessId,
        };
    };
    match provider.process_identity(process_id) {
        IdentityLookup::Bound(agent) => bound_partition(agent),
        IdentityLookup::Ambiguous | IdentityLookup::Unbound => UnitActor::Unknown {
            reason: unassigned_process_reason(unresolved, process_id),
        },
    }
}

fn unassigned_process_reason(
    unresolved: &HashMap<ProcessId, IdentityUnknownReason>,
    process_id: ProcessId,
) -> IdentityUnknownReason {
    unresolved
        .get(&process_id)
        .copied()
        .unwrap_or(IdentityUnknownReason::ProcessNotAssigned)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::responsibility::identity::input::RawIdentityInput;
    use crate::responsibility::identity::policy::IdentityBoundaryPolicy;
    use crate::responsibility::signals::SignalGraph;
    use crate::responsibility::signals::signal_snapshot::{
        ProcessExecInfo, ProcessMetadata, SignalMetrics, SignalSnapshot,
    };
    use crate::responsibility::types::{
        MessageId, NativeToolCallFormat, NativeToolCallRef, ProcessId, TlsSessionId,
    };
    use std::collections::HashMap;

    fn pid(pid: u32, start: u64) -> ProcessId {
        ProcessId::new(pid, start)
    }

    fn tls(session: &str) -> TlsSessionId {
        TlsSessionId::from(session)
    }

    fn exec(graph: &mut SignalGraph, process: ProcessId, parent: ProcessId, comm: &str) {
        graph.observe_exec(
            process,
            parent,
            ProcessExecInfo {
                comm: comm.into(),
                ..Default::default()
            },
            None,
            process.start_time_ns,
        );
    }

    fn process(process_id: ProcessId, comm: &str) -> ProcessMetadata {
        ProcessMetadata {
            process_id,
            comm: comm.into(),
            pgid: None,
            first_seen_ns: process_id.start_time_ns,
            last_seen_ns: process_id.start_time_ns + 1,
            exit_time_ns: None,
            exit_code: None,
            is_alive: true,
            cmdline: None,
            exe_path: None,
            cgroup_unit: None,
        }
    }

    fn raw(graph: &SignalGraph, delegations: Vec<(ProcessId, ProcessId)>) -> RawIdentityInput {
        let snapshot = graph.snapshot();
        RawIdentityInput::from_snapshot(&snapshot, delegations)
    }

    fn policy(comms: &[&str]) -> IdentityBoundaryPolicy {
        IdentityBoundaryPolicy::new(comms.iter().copied())
    }

    fn unit<'a>(view: &'a IdentityView, unit_id: &str) -> &'a UnitIdentity {
        view.units
            .iter()
            .find(|unit| unit.unit_id == unit_id)
            .unwrap_or_else(|| panic!("missing unit {unit_id}: {:?}", view.units))
    }

    fn actor<'a>(view: &'a IdentityView, unit_id: &str) -> Option<&'a str> {
        unit(view, unit_id).actor.partition()
    }

    fn reason<'a>(view: &'a IdentityView, unit_id: &str) -> &'a IdentityUnknownReason {
        match &unit(view, unit_id).actor {
            UnitActor::Assigned { partition } => panic!("{unit_id} assigned to {partition}"),
            UnitActor::Unknown { reason } => reason,
        }
    }

    #[test]
    fn malformed_or_duplicate_unit_ids_are_rejected() {
        let mut graph = SignalGraph::new();
        let process = pid(100, 1);
        exec(&mut graph, process, pid(1, 0), "claude");
        let raw = raw(&graph, Vec::new());
        let policy = policy(&["claude"]);
        let valid = IdentityUnit::action("one", process);
        let mut malformed = valid.clone();
        for id in ["", "action:", "message:wrong-kind"] {
            malformed.unit_id = id.into();
            assert!(
                IdentityView::resolve(&raw, &[malformed.clone()], IdentityCondition::Full, &policy)
                    .is_err(),
                "invalid unit id accepted: {id}"
            );
        }
        assert!(
            IdentityView::resolve(
                &raw,
                &[valid.clone(), valid],
                IdentityCondition::Full,
                &policy
            )
            .is_err(),
            "duplicate unit could inflate the scoring cohort"
        );
    }

    #[test]
    fn c0_and_c1_differ_for_a_tool_child() {
        let mut graph = SignalGraph::new();
        let agent = pid(100, 1);
        let tool = pid(200, 2);
        exec(&mut graph, agent, pid(1, 0), "claude");
        exec(&mut graph, tool, agent, "cat");
        let raw = raw(&graph, Vec::new());
        let units = vec![IdentityUnit::action("action:tool", tool)];
        let policy = policy(&["claude"]);

        let c0 = IdentityView::resolve(&raw, &units, IdentityCondition::PidInstanceOnly, &policy)
            .unwrap();
        let c1 = IdentityView::resolve(
            &raw,
            &units,
            IdentityCondition::PidInstancePlusPpid,
            &policy,
        )
        .unwrap();

        let c0_actor = actor(&c0, "action:tool").expect("C0 keeps the observed process");
        let c1_actor = actor(&c1, "action:tool").expect("C1 shares the agent ancestor");
        assert_ne!(c0_actor, c1_actor);
        assert!(c0_actor.contains("200"), "{c0_actor}");
        assert!(c1_actor.contains("100"), "{c1_actor}");
        assert!(
            unit(&c1, "action:tool")
                .limitations
                .contains(&IdentityViewLimitation::ProcessOnlyControl)
        );
    }

    #[test]
    fn c1_and_c2_differ_for_shared_tls_across_roots() {
        let mut graph = SignalGraph::new();
        let left = pid(100, 1);
        let right = pid(300, 3);
        exec(&mut graph, left, pid(1, 0), "claude");
        exec(&mut graph, right, pid(1, 0), "claude");
        graph.observe_tls(left, tls("shared"), 10);
        graph.observe_tls(right, tls("shared"), 11);
        let raw = raw(&graph, Vec::new());
        let units = vec![
            IdentityUnit::action("action:left", left),
            IdentityUnit::action("action:right", right),
        ];
        let policy = policy(&["claude"]);

        let c1 = IdentityView::resolve(
            &raw,
            &units,
            IdentityCondition::PidInstancePlusPpid,
            &policy,
        )
        .unwrap();
        let c2 =
            IdentityView::resolve(&raw, &units, IdentityCondition::PidInstancePlusTls, &policy)
                .unwrap();

        assert_ne!(
            actor(&c1, "action:left"),
            actor(&c1, "action:right"),
            "C1 is process-only"
        );
        assert_eq!(
            actor(&c2, "action:left"),
            actor(&c2, "action:right"),
            "C2 joins through observed shared TLS"
        );
    }

    #[test]
    fn c2_and_c3_differ_when_disjoint_conversations_veto_shared_tls() {
        let mut graph = SignalGraph::new();
        let left = pid(100, 1);
        let right = pid(300, 3);
        exec(&mut graph, left, pid(1, 0), "claude");
        exec(&mut graph, right, pid(1, 0), "claude");
        graph.observe_tls(left, tls("shared"), 10);
        graph.observe_tls(right, tls("shared"), 11);
        graph.observe_conversation(left, "conv-a", 12);
        graph.observe_conversation(right, "conv-b", 13);
        let raw = raw(&graph, Vec::new());
        let units = vec![
            IdentityUnit::action("action:left", left),
            IdentityUnit::action("action:right", right),
        ];
        let policy = policy(&["claude"]);

        let c2 =
            IdentityView::resolve(&raw, &units, IdentityCondition::PidInstancePlusTls, &policy)
                .unwrap();
        let c3 = IdentityView::resolve(
            &raw,
            &units,
            IdentityCondition::PidInstancePlusConversation,
            &policy,
        )
        .unwrap();

        assert_eq!(
            actor(&c2, "action:left"),
            actor(&c2, "action:right"),
            "C2 removes the conversation veto"
        );
        assert_ne!(
            actor(&c3, "action:left"),
            actor(&c3, "action:right"),
            "C3 keeps disjoint conversations apart"
        );
    }

    #[test]
    fn transitive_disjoint_conversations_never_merge_through_unconversational_bridge() {
        let policy = policy(&["claude"]);
        // C has no conversation of its own. The old edge-local veto rejected
        // A-B but let both merge with C, so A and B met transitively.
        for [a_pid, b_pid, c_pid] in [
            [100, 300, 200],
            [100, 200, 300],
            [200, 100, 300],
            [200, 300, 100],
            [300, 100, 200],
            [300, 200, 100],
        ] {
            let a = pid(a_pid, 1);
            let b = pid(b_pid, 1);
            let c = pid(c_pid, 1);
            let mut graph = SignalGraph::new();
            for process in [a, b, c] {
                exec(&mut graph, process, pid(1, 0), "claude");
                graph.observe_tls(process, tls("shared"), 10);
            }
            graph.observe_conversation(a, "conv-a", 11);
            graph.observe_conversation(b, "conv-b", 12);
            let raw = raw(&graph, Vec::new());
            let units = vec![
                IdentityUnit::action("action:a", a),
                IdentityUnit::action("action:b", b),
                IdentityUnit::action("action:c", c),
            ];

            let full =
                IdentityView::resolve(&raw, &units, IdentityCondition::Full, &policy).unwrap();
            let c3 = IdentityView::resolve(
                &raw,
                &units,
                IdentityCondition::PidInstancePlusConversation,
                &policy,
            )
            .unwrap();
            for view in [&full, &c3] {
                assert_ne!(
                    actor(view, "action:a"),
                    actor(view, "action:b"),
                    "declared disjoint conversations must not meet through a bridge"
                );
            }

            // The unresolved bridge keeps the existing deterministic policy:
            // it joins the first seed in ProcessId order of the conflicting pair.
            let first = if a_pid < b_pid {
                "action:a"
            } else {
                "action:b"
            };
            assert_eq!(actor(&full, "action:c"), actor(&full, first));
            assert_eq!(actor(&c3, "action:c"), actor(&c3, first));

            let c2 =
                IdentityView::resolve(&raw, &units, IdentityCondition::PidInstancePlusTls, &policy)
                    .unwrap();
            assert_eq!(
                actor(&c2, "action:a"),
                actor(&c2, "action:b"),
                "C2 drops the conversation veto and merges through shared TLS"
            );
            assert_eq!(actor(&c2, "action:a"), actor(&c2, "action:c"));
        }
    }

    #[test]
    fn overlapping_multi_session_bridge_keeps_disjoint_ends_apart() {
        let mut graph = SignalGraph::new();
        let a = pid(100, 1);
        let b = pid(200, 2);
        let c = pid(300, 3);
        for (process, session) in [(a, "tls-a"), (b, "tls-b"), (c, "tls-c")] {
            exec(&mut graph, process, pid(1, 0), "claude");
            graph.observe_tls(process, tls(session), 10);
        }
        // A{a}, B{a,b}, C{b}: A and C declare disjoint sets, B bridges them.
        for (process, conversation) in [(a, "conv-a"), (b, "conv-a"), (b, "conv-b"), (c, "conv-b")]
        {
            graph.observe_conversation(process, conversation, 11);
        }
        let raw = raw(&graph, Vec::new());
        let units = vec![
            IdentityUnit::action("action:a", a),
            IdentityUnit::action("action:b", b),
            IdentityUnit::action("action:c", c),
        ];
        let policy = policy(&["claude"]);

        for condition in [
            IdentityCondition::Full,
            IdentityCondition::PidInstancePlusConversation,
        ] {
            let view = IdentityView::resolve(&raw, &units, condition, &policy).unwrap();
            assert_eq!(
                actor(&view, "action:a"),
                actor(&view, "action:b"),
                "{condition:?} must merge a shared compatible conversation"
            );
            assert_ne!(
                actor(&view, "action:a"),
                actor(&view, "action:c"),
                "{condition:?} must keep disjoint conversations apart"
            );
        }
    }

    #[test]
    fn c3_and_c4_differ_on_an_explicit_delegation_veto() {
        let mut graph = SignalGraph::new();
        let parent = pid(100, 1);
        let child = pid(200, 2);
        exec(&mut graph, parent, pid(1, 0), "claude");
        exec(&mut graph, child, parent, "claude");
        graph.observe_tls(parent, tls("shared"), 10);
        graph.observe_tls(child, tls("shared"), 11);
        graph.observe_conversation(parent, "conv-shared", 12);
        graph.observe_conversation(child, "conv-shared", 13);
        let raw = raw(&graph, vec![(parent, child)]);
        let units = vec![
            IdentityUnit::action("action:parent", parent),
            IdentityUnit::action("action:child", child),
        ];
        let policy = policy(&["claude"]);

        let c3 = IdentityView::resolve(
            &raw,
            &units,
            IdentityCondition::PidInstancePlusConversation,
            &policy,
        )
        .unwrap();
        let c4 = IdentityView::resolve(&raw, &units, IdentityCondition::Full, &policy).unwrap();

        assert_eq!(
            actor(&c3, "action:parent"),
            actor(&c3, "action:child"),
            "C3 discards observed delegation constraints"
        );
        assert_ne!(
            actor(&c4, "action:parent"),
            actor(&c4, "action:child"),
            "C4 keeps the explicit delegation veto"
        );
    }

    #[test]
    fn c0_and_c1_ignore_masked_tls_conversation_and_delegation_signals() {
        let mut graph = SignalGraph::new();
        let agent = pid(100, 1);
        let tool = pid(200, 2);
        exec(&mut graph, agent, pid(1, 0), "claude");
        exec(&mut graph, tool, agent, "cat");
        graph.observe_tls(agent, tls("s"), 10);
        graph.observe_tls(tool, tls("s"), 11);
        graph.observe_conversation(agent, "conv", 12);
        graph.observe_conversation(tool, "conv", 13);
        let raw = raw(&graph, vec![(agent, tool)]);
        let units = vec![
            IdentityUnit::action("action:agent", agent),
            IdentityUnit::action("action:tool", tool),
        ];
        let policy = policy(&["claude"]);

        let mut masked = raw.clone();
        masked.tls_owners.clear();
        masked.tls_participants.clear();
        masked.tls_messages.clear();
        masked.conversation_processes.clear();
        masked.process_conversations.clear();
        masked.delegations.clear();

        for condition in [
            IdentityCondition::PidInstanceOnly,
            IdentityCondition::PidInstancePlusPpid,
        ] {
            let base = IdentityView::resolve(&raw, &units, condition, &policy).unwrap();
            let after = IdentityView::resolve(&masked, &units, condition, &policy).unwrap();
            assert_eq!(
                serde_json::to_vec(&base).unwrap(),
                serde_json::to_vec(&after).unwrap(),
                "{condition:?} changed after masked signals were removed"
            );
        }

        // C0 does not use the parent graph either.
        let mut reparented = raw.clone();
        for edge in &mut reparented.parent_edges {
            if edge.child == tool {
                edge.parent = pid(999, 9);
            }
        }
        let c0 = IdentityView::resolve(&raw, &units, IdentityCondition::PidInstanceOnly, &policy)
            .unwrap();
        let reparented_c0 = IdentityView::resolve(
            &reparented,
            &units,
            IdentityCondition::PidInstanceOnly,
            &policy,
        )
        .unwrap();
        assert_eq!(
            serde_json::to_vec(&c0).unwrap(),
            serde_json::to_vec(&reparented_c0).unwrap(),
            "C0 must ignore the parent graph"
        );
    }

    #[test]
    fn identity_view_bytes_are_stable_across_repeated_calls() {
        let mut graph = SignalGraph::new();
        let agent = pid(100, 1);
        exec(&mut graph, agent, pid(1, 0), "claude");
        graph.observe_tls(agent, tls("s"), 10);
        let raw = raw(&graph, Vec::new());
        let units = vec![IdentityUnit::action("action:a", agent)];
        let policy = policy(&["claude"]);

        let first = IdentityView::resolve(&raw, &units, IdentityCondition::Full, &policy).unwrap();
        let second = IdentityView::resolve(&raw, &units, IdentityCondition::Full, &policy).unwrap();
        assert_eq!(
            serde_json::to_vec(&first).unwrap(),
            serde_json::to_vec(&second).unwrap()
        );
        let one: IdentityCondition =
            serde_json::from_str(&serde_json::to_string(&IdentityCondition::Full).unwrap())
                .unwrap();
        assert_eq!(one, IdentityCondition::Full);
        assert_eq!(IdentityCondition::ALL.len(), 5);
    }

    #[test]
    fn pid_birth_reuse_keeps_process_partitions_separate() {
        let mut graph = SignalGraph::new();
        let first = pid(500, 10);
        let reused = pid(500, 20);
        exec(&mut graph, first, pid(1, 0), "cat");
        exec(&mut graph, reused, pid(1, 0), "cat");
        let raw = raw(&graph, Vec::new());
        let units = vec![
            IdentityUnit::action("action:first", first),
            IdentityUnit::action("action:reused", reused),
        ];
        let policy = policy(&["claude"]);

        let c0 = IdentityView::resolve(&raw, &units, IdentityCondition::PidInstanceOnly, &policy)
            .unwrap();
        assert_ne!(
            actor(&c0, "action:first"),
            actor(&c0, "action:reused"),
            "same pid with a different birth is a different partition"
        );
    }

    #[test]
    fn a_message_claimed_by_two_agents_stays_unknown() {
        let mut graph = SignalGraph::new();
        let left = pid(100, 1);
        let right = pid(300, 3);
        exec(&mut graph, left, pid(1, 0), "claude");
        exec(&mut graph, right, pid(1, 0), "claude");
        graph.observe_tls(left, tls("left"), 10);
        graph.observe_tls(right, tls("right"), 11);
        let message = MessageId::from("msg-shared");
        graph.observe_message_direct(left, message.clone());
        graph.observe_message_direct(right, message.clone());
        let raw = raw(&graph, Vec::new());
        let units = vec![IdentityUnit::message("message:msg-shared", message, None)];
        let policy = policy(&["claude"]);

        let view =
            IdentityView::resolve(&raw, &units, IdentityCondition::PidInstancePlusTls, &policy)
                .unwrap();
        assert_eq!(
            reason(&view, "message:msg-shared"),
            &IdentityUnknownReason::AmbiguousMessage
        );
    }

    #[test]
    fn same_pid_multiple_conversations_abstain_and_expose_candidates() {
        let mut graph = SignalGraph::new();
        let process = pid(600, 1);
        exec(&mut graph, process, pid(1, 0), "claude");
        graph.observe_tls(process, tls("process"), 10);
        let message = MessageId::from("msg-process");
        graph.observe_message_via_tls(tls("process"), message.clone());
        graph.observe_conversation(process, "conv-a", 11);
        graph.observe_conversation(process, "conv-b", 12);
        let raw = raw(&graph, Vec::new());
        let units = vec![
            IdentityUnit::action("action:process", process),
            IdentityUnit::message("message:msg-process", message, Some(process)),
        ];
        let policy = policy(&["claude"]);

        let view = IdentityView::resolve(&raw, &units, IdentityCondition::Full, &policy).unwrap();
        assert_eq!(
            reason(&view, "action:process"),
            &IdentityUnknownReason::AmbiguousMultipleSessions
        );
        assert!(
            matches!(
                unit(&view, "message:msg-process").actor,
                UnitActor::Unknown { .. }
            ),
            "a message without an anchor must not inherit the ambiguous process"
        );
        assert_eq!(view.conversation_candidates.len(), 2);
        assert!(
            view.conversation_candidates
                .iter()
                .all(|candidate| candidate.process_id == process)
        );
    }

    #[test]
    fn c2_masks_message_anchors_while_c3_and_c4_consume_them() {
        let mut graph = SignalGraph::new();
        let process = pid(700, 1);
        exec(&mut graph, process, pid(1, 0), "claude");
        graph.observe_tls(process, tls("process"), 10);
        let message_a = MessageId::from("msg-a");
        let message_b = MessageId::from("msg-b");
        let unanchored = MessageId::from("msg-unanchored");
        for message in [&message_a, &message_b, &unanchored] {
            graph.observe_message_via_tls(tls("process"), message.clone());
        }
        graph.observe_conversation(process, "conv-a", 11);
        graph.observe_conversation(process, "conv-b", 12);
        graph.observe_message_conversation(process, message_a.clone(), "conv-a");
        graph.observe_message_conversation(process, message_b.clone(), "conv-b");
        let raw = raw(&graph, Vec::new());
        let units = vec![
            IdentityUnit::action("action:process", process),
            IdentityUnit::message("message:msg-a", message_a.clone(), Some(process)),
            IdentityUnit::message("message:msg-b", message_b.clone(), Some(process)),
            IdentityUnit::message("message:msg-unanchored", unanchored, Some(process)),
        ];
        let policy = policy(&["claude"]);

        let c2 =
            IdentityView::resolve(&raw, &units, IdentityCondition::PidInstancePlusTls, &policy)
                .unwrap();
        let c3 = IdentityView::resolve(
            &raw,
            &units,
            IdentityCondition::PidInstancePlusConversation,
            &policy,
        )
        .unwrap();
        let c4 = IdentityView::resolve(&raw, &units, IdentityCondition::Full, &policy).unwrap();

        assert!(
            actor(&c2, "action:process").is_some(),
            "without conversation evidence the shared process stays one physical agent"
        );
        assert_eq!(
            actor(&c2, "message:msg-a"),
            actor(&c2, "message:msg-b"),
            "C2 masks the anchors, so both messages follow the physical process"
        );
        for view in [&c3, &c4] {
            assert_eq!(
                reason(view, "action:process"),
                &IdentityUnknownReason::AmbiguousMultipleSessions,
                "anchors do not make shared-process actions attributable"
            );
            assert_ne!(
                actor(view, "message:msg-a"),
                actor(view, "message:msg-b"),
                "C3/C4 split anchored messages by conversation"
            );
            assert!(matches!(
                unit(view, "message:msg-unanchored").actor,
                UnitActor::Unknown { .. }
            ));
        }

        // Removing or rewriting the anchor rows must not change C2, and must
        // change C3/C4: the rows are the only conversation-message evidence.
        let mut mutated = raw.clone();
        mutated.message_conversations.clear();
        let c2_after = IdentityView::resolve(
            &mutated,
            &units,
            IdentityCondition::PidInstancePlusTls,
            &policy,
        )
        .unwrap();
        let c3_after = IdentityView::resolve(
            &mutated,
            &units,
            IdentityCondition::PidInstancePlusConversation,
            &policy,
        )
        .unwrap();
        assert_eq!(
            serde_json::to_vec(&c2).unwrap(),
            serde_json::to_vec(&c2_after).unwrap(),
            "C2 must ignore every conversation anchor row"
        );
        assert_ne!(
            serde_json::to_vec(&c3).unwrap(),
            serde_json::to_vec(&c3_after).unwrap(),
            "C3 must consume the captured anchor rows"
        );
        assert!(matches!(
            unit(&c3_after, "message:msg-a").actor,
            UnitActor::Unknown { .. }
        ));
    }

    #[test]
    fn passed_policy_controls_agent_boundary_without_ambient_defaults() {
        let mut graph = SignalGraph::new();
        let parent = pid(100, 1);
        let tool = pid(200, 2);
        exec(&mut graph, parent, pid(1, 0), "claude");
        exec(&mut graph, tool, parent, "cat");
        let raw = raw(&graph, Vec::new());
        let units = vec![IdentityUnit::action("action:tool", tool)];

        let without_agent = IdentityView::resolve(
            &raw,
            &units,
            IdentityCondition::PidInstancePlusPpid,
            &policy(&["unrelated"]),
        )
        .unwrap();
        let with_agent = IdentityView::resolve(
            &raw,
            &units,
            IdentityCondition::PidInstancePlusPpid,
            &policy(&["claude"]),
        )
        .unwrap();

        assert_ne!(
            actor(&without_agent, "action:tool"),
            actor(&with_agent, "action:tool"),
            "the passed policy, not the ambient built-in default, decides the boundary"
        );
    }

    #[test]
    fn c4_uses_the_passed_custom_comm_boundary() {
        let parent = pid(100, 1);
        let child = pid(200, 2);
        let mut processes = HashMap::new();
        processes.insert(parent, process(parent, "my-agent"));
        processes.insert(child, process(child, "my-agent"));
        let mut parent_map = HashMap::new();
        parent_map.insert(child, parent);
        let mut child_map = HashMap::new();
        child_map.insert(parent, vec![child]);
        let mut tls_to_process = HashMap::new();
        tls_to_process.insert(tls("parent"), parent);
        tls_to_process.insert(tls("child"), child);
        // The child has a TLS session in tls_to_process but is absent from the
        // participant list, so it is not a seed and the downward policy check
        // decides whether it is a separate agent.
        let mut tls_participants = HashMap::new();
        tls_participants.insert(tls("parent"), vec![parent]);
        let snapshot = SignalSnapshot {
            processes,
            parent_map,
            child_map,
            tls_to_process,
            tls_participants,
            tls_to_messages: HashMap::new(),
            conversation_to_processes: HashMap::new(),
            process_to_conversations: HashMap::new(),
            direct_process_messages: HashMap::new(),
            message_conversation_bindings: Vec::new(),
            native_tool_call_observations: Vec::new(),
            thread_to_process: HashMap::new(),
            metrics: SignalMetrics {
                process_count: 2,
                tls_session_count: 2,
                message_count: 0,
                orphan_count: 0,
                lost_event_count: 0,
            },
        };
        let raw = RawIdentityInput::from_snapshot(&snapshot, Vec::new());
        let units = vec![IdentityUnit::action("action:child", child)];

        let custom = IdentityView::resolve(
            &raw,
            &units,
            IdentityCondition::Full,
            &policy(&["my-agent"]),
        )
        .unwrap();
        let other =
            IdentityView::resolve(&raw, &units, IdentityCondition::Full, &policy(&["claude"]))
                .unwrap();

        assert_ne!(
            actor(&custom, "action:child"),
            actor(&other, "action:child"),
            "a frozen custom comm boundary must separate the child agent"
        );
    }

    fn native(id: &str) -> NativeToolCallRef {
        NativeToolCallRef {
            format: NativeToolCallFormat::Anthropic,
            id: id.into(),
        }
    }

    fn shared_pid_history_and_responses() -> (
        SignalGraph,
        ProcessId,
        MessageId,
        MessageId,
        MessageId,
        MessageId,
    ) {
        let mut graph = SignalGraph::new();
        let agent = pid(100, 1);
        exec(&mut graph, agent, pid(1, 0), "claude");
        graph.observe_tls(agent, tls("agent-tls"), 5);
        let history_a = MessageId::from("history-a");
        let history_b = MessageId::from("history-b");
        let response_a = MessageId::from("response-a");
        let response_b = MessageId::from("response-b");
        for message in [&history_a, &history_b, &response_a, &response_b] {
            graph.observe_message(agent, None, message.clone());
        }
        graph.observe_message_conversation(agent, history_a.clone(), "conv-a");
        graph.observe_message_conversation(agent, history_b.clone(), "conv-b");
        graph.observe_conversation(agent, "conv-a", 10);
        graph.observe_conversation(agent, "conv-b", 11);
        graph.observe_native_tool_call(
            agent,
            history_a.clone(),
            native("toolu_a"),
            Some("conv-a".into()),
        );
        graph.observe_native_tool_call(
            agent,
            history_b.clone(),
            native("toolu_b"),
            Some("conv-b".into()),
        );
        graph.observe_native_tool_call(agent, response_a.clone(), native("toolu_a"), None);
        graph.observe_native_tool_call(agent, response_b.clone(), native("toolu_b"), None);
        (graph, agent, history_a, history_b, response_a, response_b)
    }

    #[test]
    fn shared_pid_native_alias_separates_unanchored_responses() {
        let (graph, agent, history_a, history_b, response_a, response_b) =
            shared_pid_history_and_responses();
        let raw = raw(&graph, Vec::new());
        let units = vec![
            IdentityUnit::message("history-a", history_a, Some(agent)),
            IdentityUnit::message("history-b", history_b, Some(agent)),
            IdentityUnit::message("response-a", response_a, Some(agent)),
            IdentityUnit::message("response-b", response_b, Some(agent)),
            IdentityUnit::action("shared-process", agent),
        ];
        let policy = policy(&["claude"]);

        for condition in [
            IdentityCondition::PidInstancePlusConversation,
            IdentityCondition::Full,
        ] {
            let view = IdentityView::resolve(&raw, &units, condition, &policy).unwrap();
            assert_ne!(
                actor(&view, "message:history-a"),
                actor(&view, "message:history-b"),
                "{condition:?} keeps the two directly anchored conversations apart"
            );
            assert_eq!(
                actor(&view, "message:response-a"),
                actor(&view, "message:history-a"),
                "{condition:?} infers response-a's logical conversation"
            );
            assert_eq!(
                actor(&view, "message:response-b"),
                actor(&view, "message:history-b"),
                "{condition:?} infers response-b's logical conversation"
            );
            assert_eq!(
                reason(&view, "action:shared-process"),
                &IdentityUnknownReason::AmbiguousMultipleSessions,
                "{condition:?} must not assign physical actions to one session"
            );
            assert!(
                unit(&view, "message:response-a")
                    .limitations
                    .contains(&IdentityViewLimitation::InferredNativeConversationAlias),
                "{condition:?} must disclose the inferred basis"
            );
            assert!(
                !unit(&view, "message:history-a")
                    .limitations
                    .contains(&IdentityViewLimitation::InferredNativeConversationAlias),
                "a direct anchor is not an inferred alias"
            );
        }

        let c2 =
            IdentityView::resolve(&raw, &units, IdentityCondition::PidInstancePlusTls, &policy)
                .unwrap();
        assert_eq!(
            actor(&c2, "message:response-a"),
            actor(&c2, "message:response-b"),
            "C2 masks the co-reference observations and cannot split the sessions"
        );
        assert!(actor(&c2, "action:shared-process").is_some());
    }

    #[test]
    fn reused_native_id_makes_the_unanchored_response_ambiguous() {
        let mut graph = SignalGraph::new();
        let agent = pid(100, 1);
        exec(&mut graph, agent, pid(1, 0), "claude");
        graph.observe_tls(agent, tls("agent-tls"), 5);
        let history_a = MessageId::from("history-a");
        let history_b = MessageId::from("history-b");
        let response = MessageId::from("response-shared");
        for message in [&history_a, &history_b, &response] {
            graph.observe_message(agent, None, message.clone());
        }
        graph.observe_message_conversation(agent, history_a.clone(), "conv-a");
        graph.observe_message_conversation(agent, history_b.clone(), "conv-b");
        graph.observe_conversation(agent, "conv-a", 10);
        graph.observe_conversation(agent, "conv-b", 11);
        for message in [&history_a, &history_b, &response] {
            graph.observe_native_tool_call(
                agent,
                message.clone(),
                native("toolu_shared"),
                if message == &history_a {
                    Some("conv-a".into())
                } else if message == &history_b {
                    Some("conv-b".into())
                } else {
                    None
                },
            );
        }
        let raw = raw(&graph, Vec::new());
        let units = vec![
            IdentityUnit::message("history-a", history_a, Some(agent)),
            IdentityUnit::message("history-b", history_b, Some(agent)),
            IdentityUnit::message("response", response, Some(agent)),
        ];
        let view =
            IdentityView::resolve(&raw, &units, IdentityCondition::Full, &policy(&["claude"]))
                .unwrap();

        assert_ne!(
            actor(&view, "message:history-a"),
            actor(&view, "message:history-b")
        );
        assert_eq!(
            reason(&view, "message:response"),
            &IdentityUnknownReason::AmbiguousMessage,
            "one native id mapped to two conversations must abstain"
        );
    }
}
