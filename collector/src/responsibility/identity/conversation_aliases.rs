// SPDX-License-Identifier: Apache-2.0 OR MIT
//
// identity/conversation_aliases.rs — observed native-call co-reference.
//
// A structured native call id is a protocol correlation key, not an identity.
// This module turns it into a scoped, explicitly inferred message→conversation
// alias only when a same-process direct request anchor supports the same key
// and every positive hint agrees on one conversation. The original snapshot
// stays untouched; callers receive an overlay snapshot whose conversation maps
// include the inferred seeds. No alias is ever written as a direct binding.

use crate::responsibility::signals::signal_snapshot::SignalSnapshot;
use crate::responsibility::types::{
    MessageId, NativeToolCallFormat, NativeToolCallObservation, ProcessId,
};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeSet, HashMap, HashSet};

/// One message→conversation relation inferred from native-call co-reference.
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct InferredConversationAlias {
    pub process_id: ProcessId,
    pub message_id: MessageId,
    pub conversation_id: String,
}

/// A scoped snapshot plus the effective inferred aliases.
pub(crate) struct ConversationAliasScope {
    pub snapshot: SignalSnapshot,
    pub aliases: Vec<InferredConversationAlias>,
    /// Messages whose native references point at more than one conversation.
    pub ambiguous_messages: Vec<MessageId>,
}

/// Compute the effective aliases and message conflicts without mutating input.
pub(crate) fn inferred_aliases(
    snapshot: &SignalSnapshot,
) -> (Vec<InferredConversationAlias>, Vec<MessageId>) {
    let observed = observed_process_messages(snapshot);

    // Exactly one direct (process, conversation) pair per source message is
    // required. A source bound to several conversations cannot disambiguate
    // its native key, so the key is poisoned for every dependent message.
    let mut direct_conversations: HashMap<MessageId, BTreeSet<(ProcessId, String)>> =
        HashMap::new();
    for row in snapshot.message_conversations() {
        direct_conversations
            .entry(row.message_id.clone())
            .or_default()
            .insert((row.process_id, row.conversation_id.clone()));
    }

    // A message observed under conflicting process owners cannot seed a hint.
    let mut observed_owners: HashMap<MessageId, BTreeSet<ProcessId>> = HashMap::new();
    for (process_id, messages) in &observed {
        for message_id in messages {
            observed_owners
                .entry(message_id.clone())
                .or_default()
                .insert(*process_id);
        }
    }

    // Positive hints need one consistent direct source, a unique observed
    // owner and a conversation already registered for that process. The
    // declared conversation is validated, never invented.
    let mut hints: HashMap<(ProcessId, NativeToolCallFormat, String), BTreeSet<String>> =
        HashMap::new();
    let mut poisoned: HashSet<(ProcessId, NativeToolCallFormat, String)> = HashSet::new();
    for observation in snapshot.native_tool_call_observations() {
        if observation.native_tool_call.id.trim().is_empty() {
            continue;
        }
        let Some(declared) = observation
            .declared_conversation_id
            .as_deref()
            .filter(|id| !id.is_empty())
        else {
            continue;
        };
        if !snapshot.processes.contains_key(&observation.process_id) {
            continue;
        }
        let key = (
            observation.process_id,
            observation.native_tool_call.format,
            observation.native_tool_call.id.clone(),
        );
        let Some(source_conversations) = direct_conversations.get(&observation.message_id) else {
            continue;
        };
        if source_conversations.len() != 1
            || !source_conversations.contains(&(observation.process_id, declared.to_string()))
        {
            poisoned.insert(key);
            continue;
        }
        if !observed_owners
            .get(&observation.message_id)
            .is_some_and(|owners| owners.len() == 1 && owners.contains(&observation.process_id))
        {
            continue;
        }
        let registered = snapshot
            .conversations_for_process(observation.process_id)
            .iter()
            .any(|id| id == declared)
            && snapshot
                .processes_for_conversation(declared)
                .contains(&observation.process_id);
        if !registered {
            continue;
        }
        hints.entry(key).or_default().insert(declared.to_string());
    }

    let direct_anchored: HashSet<MessageId> = snapshot
        .message_conversations()
        .iter()
        .map(|row| row.message_id.clone())
        .collect();

    let mut by_message: HashMap<MessageId, Vec<&NativeToolCallObservation>> = HashMap::new();
    for observation in snapshot.native_tool_call_observations() {
        if observation.native_tool_call.id.trim().is_empty() {
            continue;
        }
        by_message
            .entry(observation.message_id.clone())
            .or_default()
            .push(observation);
    }
    let mut message_ids: Vec<MessageId> = by_message.keys().cloned().collect();
    message_ids.sort_by(|left, right| left.as_str().cmp(right.as_str()));

    let mut aliases = Vec::new();
    let mut ambiguous_messages = Vec::new();
    for message_id in message_ids {
        // A direct capture anchor always wins; a reused native id elsewhere
        // must not overwrite it or make it ambiguous.
        if direct_anchored.contains(&message_id) {
            continue;
        }
        let rows = &by_message[&message_id];
        let mut processes: Vec<ProcessId> = rows
            .iter()
            .map(|row| row.process_id)
            .collect::<HashSet<_>>()
            .into_iter()
            .collect();
        processes.sort_by_key(|process| (process.pid, process.start_time_ns));
        if processes.len() != 1 {
            ambiguous_messages.push(message_id);
            continue;
        }
        let process_id = processes[0];
        if !snapshot.processes.contains_key(&process_id)
            || !observed
                .get(&process_id)
                .is_some_and(|messages| messages.contains(&message_id))
        {
            ambiguous_messages.push(message_id);
            continue;
        }

        let mut candidates: BTreeSet<String> = BTreeSet::new();
        let mut key_poisoned = false;
        for row in rows.iter().filter(|row| row.process_id == process_id) {
            let key = (
                process_id,
                row.native_tool_call.format,
                row.native_tool_call.id.clone(),
            );
            if poisoned.contains(&key) {
                key_poisoned = true;
                continue;
            }
            if let Some(conversations) = hints.get(&key) {
                candidates.extend(conversations.iter().cloned());
            }
        }
        if key_poisoned {
            ambiguous_messages.push(message_id);
            continue;
        }
        match candidates.len() {
            0 => {}
            1 => aliases.push(InferredConversationAlias {
                process_id,
                message_id,
                conversation_id: candidates.into_iter().next().expect("one candidate"),
            }),
            _ => ambiguous_messages.push(message_id),
        }
    }

    aliases.sort_by(|left, right| {
        (left.process_id.pid, left.process_id.start_time_ns)
            .cmp(&(right.process_id.pid, right.process_id.start_time_ns))
            .then_with(|| left.message_id.as_str().cmp(right.message_id.as_str()))
            .then_with(|| left.conversation_id.cmp(&right.conversation_id))
    });
    aliases.dedup();
    ambiguous_messages.sort_by(|left, right| left.as_str().cmp(right.as_str()));
    ambiguous_messages.dedup();
    (aliases, ambiguous_messages)
}

/// Apply the effective aliases as conversation seeds on a scoped clone.
///
/// The rows are not inserted into `message_conversation_bindings`: they remain
/// inferred overlay facts, never captured anchors.
pub(crate) fn apply(snapshot: &SignalSnapshot) -> ConversationAliasScope {
    let (aliases, ambiguous_messages) = inferred_aliases(snapshot);
    let mut scoped = snapshot.clone();
    for alias in &aliases {
        let conversations = scoped
            .conversation_to_processes
            .entry(alias.conversation_id.clone())
            .or_default();
        if !conversations.contains(&alias.process_id) {
            conversations.push(alias.process_id);
        }
        let per_process = scoped
            .process_to_conversations
            .entry(alias.process_id)
            .or_default();
        if !per_process.iter().any(|id| id == &alias.conversation_id) {
            per_process.push(alias.conversation_id.clone());
        }
    }
    ConversationAliasScope {
        snapshot: scoped,
        aliases,
        ambiguous_messages,
    }
}

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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::responsibility::signals::SignalGraph;
    use crate::responsibility::signals::signal_snapshot::ProcessExecInfo;
    use crate::responsibility::types::NativeToolCallRef;

    fn pid(pid: u32, start: u64) -> ProcessId {
        ProcessId::new(pid, start)
    }

    fn exec(graph: &mut SignalGraph, process: ProcessId) {
        graph.observe_exec(
            process,
            pid(1, 0),
            ProcessExecInfo {
                comm: "claude".into(),
                ..Default::default()
            },
            None,
            process.start_time_ns,
        );
    }

    fn native(format: NativeToolCallFormat, id: &str) -> NativeToolCallRef {
        NativeToolCallRef {
            format,
            id: id.into(),
        }
    }

    #[test]
    fn native_ids_never_cross_process_or_protocol_boundaries() {
        let left = pid(100, 1);
        let right = pid(200, 2);
        let mut graph = SignalGraph::new();
        exec(&mut graph, left);
        exec(&mut graph, right);
        graph.observe_tls(
            left,
            crate::responsibility::types::TlsSessionId::from("left"),
            1,
        );
        graph.observe_tls(
            right,
            crate::responsibility::types::TlsSessionId::from("right"),
            2,
        );

        let left_history = MessageId::from("left-history");
        let left_response = MessageId::from("left-response");
        let right_history = MessageId::from("right-history");
        graph.observe_message(left, None, left_history.clone());
        graph.observe_message(left, None, left_response.clone());
        graph.observe_message(right, None, right_history.clone());
        graph.observe_message_conversation(right, right_history.clone(), "conv-right");
        graph.observe_conversation(right, "conv-right", 3);
        graph.observe_native_tool_call(
            right,
            right_history,
            native(NativeToolCallFormat::Anthropic, "shared-id"),
            Some("conv-right".into()),
        );
        // Same id and format on another process must not supply a hint.
        graph.observe_native_tool_call(
            left,
            left_response.clone(),
            native(NativeToolCallFormat::Anthropic, "shared-id"),
            None,
        );
        assert!(
            inferred_aliases(&graph.snapshot()).0.is_empty(),
            "a native id is scoped to its process instance"
        );

        // Same process and id, but a different wire format, is another key.
        let mut graph = SignalGraph::new();
        exec(&mut graph, left);
        graph.observe_tls(
            left,
            crate::responsibility::types::TlsSessionId::from("left"),
            1,
        );
        graph.observe_message(left, None, left_history.clone());
        graph.observe_message(left, None, left_response.clone());
        graph.observe_message_conversation(left, left_history.clone(), "conv-a");
        graph.observe_conversation(left, "conv-a", 1);
        graph.observe_native_tool_call(
            left,
            left_history,
            native(NativeToolCallFormat::OpenAi, "shared-id"),
            Some("conv-a".into()),
        );
        graph.observe_native_tool_call(
            left,
            left_response,
            native(NativeToolCallFormat::Anthropic, "shared-id"),
            None,
        );
        assert!(
            inferred_aliases(&graph.snapshot()).0.is_empty(),
            "protocol format namespaces the native id"
        );
    }

    #[test]
    fn contradictory_anchor_poison_key_for_dependents() {
        let process = pid(100, 1);
        let mut graph = SignalGraph::new();
        exec(&mut graph, process);
        graph.observe_tls(
            process,
            crate::responsibility::types::TlsSessionId::from("agent"),
            1,
        );
        let history = MessageId::from("history");
        let good = MessageId::from("good-request");
        let response = MessageId::from("response");
        graph.observe_message(process, None, history.clone());
        graph.observe_message(process, None, good.clone());
        graph.observe_message(process, None, response.clone());
        // The source message is directly bound to two conversations; its
        // declared hint picks one of them but cannot disambiguate the key.
        graph.observe_message_conversation(process, history.clone(), "conv-a");
        graph.observe_message_conversation(process, history.clone(), "conv-b");
        graph.observe_conversation(process, "conv-a", 1);
        graph.observe_conversation(process, "conv-b", 1);
        // A second, internally consistent hint agrees with conv-a.
        graph.observe_message_conversation(process, good.clone(), "conv-a");
        graph.observe_native_tool_call(
            process,
            history,
            native(NativeToolCallFormat::Anthropic, "shared"),
            Some("conv-a".into()),
        );
        graph.observe_native_tool_call(
            process,
            good,
            native(NativeToolCallFormat::Anthropic, "shared"),
            Some("conv-a".into()),
        );
        graph.observe_native_tool_call(
            process,
            response.clone(),
            native(NativeToolCallFormat::Anthropic, "shared"),
            None,
        );

        let (aliases, ambiguous) = inferred_aliases(&graph.snapshot());
        assert!(
            aliases.is_empty(),
            "a contradictory source poisons the key for dependents"
        );
        assert_eq!(ambiguous, vec![response]);
    }

    #[test]
    fn conflicted_message_owners_cannot_seed() {
        let left = pid(100, 1);
        let right = pid(200, 2);
        let mut graph = SignalGraph::new();
        exec(&mut graph, left);
        exec(&mut graph, right);
        graph.observe_tls(
            left,
            crate::responsibility::types::TlsSessionId::from("left"),
            1,
        );
        graph.observe_tls(
            right,
            crate::responsibility::types::TlsSessionId::from("right"),
            2,
        );
        let history = MessageId::from("history");
        let response = MessageId::from("response");
        graph.observe_message(left, None, history.clone());
        graph.observe_message(right, None, history.clone());
        graph.observe_message(left, None, response.clone());
        graph.observe_message_conversation(left, history.clone(), "conv-a");
        graph.observe_message_conversation(right, history.clone(), "conv-b");
        graph.observe_conversation(left, "conv-a", 1);
        graph.observe_conversation(right, "conv-b", 2);
        graph.observe_native_tool_call(
            left,
            history,
            native(NativeToolCallFormat::Anthropic, "shared"),
            Some("conv-a".into()),
        );
        graph.observe_native_tool_call(
            left,
            response.clone(),
            native(NativeToolCallFormat::Anthropic, "shared"),
            None,
        );

        let (aliases, ambiguous) = inferred_aliases(&graph.snapshot());
        assert!(
            aliases.is_empty(),
            "a message observed under two processes cannot seed a hint"
        );
        assert_eq!(ambiguous, vec![response]);
    }

    #[test]
    fn conflicting_direct_owner_without_duplicate_observation_cannot_seed() {
        let left = pid(100, 1);
        let right = pid(200, 2);
        let mut graph = SignalGraph::new();
        exec(&mut graph, left);
        exec(&mut graph, right);
        graph.observe_tls(
            left,
            crate::responsibility::types::TlsSessionId::from("left"),
            1,
        );
        graph.observe_tls(
            right,
            crate::responsibility::types::TlsSessionId::from("right"),
            2,
        );
        let history = MessageId::from("history");
        let response = MessageId::from("response");
        graph.observe_message(left, None, history.clone());
        graph.observe_message(left, None, response.clone());
        graph.observe_message_conversation(left, history.clone(), "conv-a");
        graph.observe_message_conversation(right, history.clone(), "conv-b");
        graph.observe_conversation(left, "conv-a", 1);
        graph.observe_conversation(right, "conv-b", 2);
        graph.observe_native_tool_call(
            left,
            history,
            native(NativeToolCallFormat::Anthropic, "shared"),
            Some("conv-a".into()),
        );
        graph.observe_native_tool_call(
            left,
            response,
            native(NativeToolCallFormat::Anthropic, "shared"),
            None,
        );

        let (aliases, _) = inferred_aliases(&graph.snapshot());
        assert!(
            aliases.is_empty(),
            "a message observed under two processes cannot seed a hint"
        );
    }

    #[test]
    fn empty_imported_native_id_is_unavailable() {
        let process = pid(100, 1);
        let mut graph = SignalGraph::new();
        exec(&mut graph, process);
        graph.observe_tls(
            process,
            crate::responsibility::types::TlsSessionId::from("agent"),
            1,
        );
        let history = MessageId::from("history");
        let response = MessageId::from("response");
        graph.observe_message(process, None, history.clone());
        graph.observe_message(process, None, response.clone());
        graph.observe_message_conversation(process, history.clone(), "conv-a");
        graph.observe_conversation(process, "conv-a", 1);

        // Replay-imported DTOs may bypass the producer-side empty-id filter.
        let mut snapshot = (*graph.snapshot()).clone();
        snapshot
            .native_tool_call_observations
            .push(NativeToolCallObservation {
                process_id: process,
                message_id: history,
                native_tool_call: native(NativeToolCallFormat::Anthropic, ""),
                declared_conversation_id: Some("conv-a".into()),
            });
        snapshot
            .native_tool_call_observations
            .push(NativeToolCallObservation {
                process_id: process,
                message_id: response,
                native_tool_call: native(NativeToolCallFormat::Anthropic, ""),
                declared_conversation_id: None,
            });

        assert!(
            inferred_aliases(&snapshot).0.is_empty(),
            "an empty native id carries no correlation"
        );
    }

    #[test]
    fn no_hint_means_no_selection_even_with_nearby_history() {
        let process = pid(100, 1);
        let mut graph = SignalGraph::new();
        exec(&mut graph, process);
        graph.observe_tls(
            process,
            crate::responsibility::types::TlsSessionId::from("agent"),
            1,
        );
        let response = MessageId::from("response");
        graph.observe_message(process, None, response.clone());
        graph.observe_native_tool_call(
            process,
            response,
            native(NativeToolCallFormat::Anthropic, "unseen"),
            None,
        );
        assert!(
            inferred_aliases(&graph.snapshot()).0.is_empty(),
            "temporal proximity is not a conversation hint"
        );
    }
}
