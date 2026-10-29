// SPDX-License-Identifier: Apache-2.0 OR MIT
//
// identity/input.rs — reconstructible PRE-resolver identity input.
//
// The checkpoint must preserve the exact signal facts identity resolution saw,
// not the resolver's answer. This DTO is captured from the frozen
// `SignalSnapshot` plus the observed process-delegation pairs before any
// authoritative identity exists, so ablations can rebuild the same input.
//
// Every snapshot map becomes a sorted row vector: JSON object keys cannot
// carry a composite `ProcessId` deterministically, and HashMap iteration order
// must not affect the serialized bytes. Missing facts stay `None`; nothing is
// synthesized from the final identities.

use crate::responsibility::signals::signal_snapshot::{
    MessageConversationBinding, ProcessMetadata, SignalMetrics, SignalSnapshot,
};
use crate::responsibility::types::NativeToolCallObservation;
use crate::responsibility::types::ids::{MessageId, ProcessId, TlsSessionId};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::collections::hash_map::Entry;

/// Version tag for the serialized identity input.
pub const RAW_IDENTITY_INPUT_SCHEMA: &str = "mtagentlens.raw-identity-input/v1";

/// A process-to-process fact: a parent edge or an observed delegation.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ProcessPairRow {
    pub parent: ProcessId,
    pub child: ProcessId,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ChildProcessRow {
    pub parent: ProcessId,
    pub children: Vec<ProcessId>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct TlsOwnerRow {
    pub tls_session_id: TlsSessionId,
    pub process_id: ProcessId,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct TlsParticipantsRow {
    pub tls_session_id: TlsSessionId,
    pub process_ids: Vec<ProcessId>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct TlsMessagesRow {
    pub tls_session_id: TlsSessionId,
    pub message_ids: Vec<MessageId>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ConversationProcessesRow {
    pub conversation_id: String,
    pub process_ids: Vec<ProcessId>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ProcessConversationsRow {
    pub process_id: ProcessId,
    pub conversation_ids: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ProcessMessagesRow {
    pub process_id: ProcessId,
    pub message_ids: Vec<MessageId>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ThreadOwnerRow {
    pub tid: u32,
    pub process_id: ProcessId,
}

/// All PRE-resolver identity facts, in a deterministic serialized form.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RawIdentityInput {
    pub schema: String,
    pub processes: Vec<ProcessMetadata>,
    pub parent_edges: Vec<ProcessPairRow>,
    pub child_processes: Vec<ChildProcessRow>,
    pub tls_owners: Vec<TlsOwnerRow>,
    pub tls_participants: Vec<TlsParticipantsRow>,
    pub tls_messages: Vec<TlsMessagesRow>,
    pub conversation_processes: Vec<ConversationProcessesRow>,
    pub process_conversations: Vec<ProcessConversationsRow>,
    pub direct_process_messages: Vec<ProcessMessagesRow>,
    /// Request-declared message → conversation anchors. Historical DTOs lack
    /// this field and stay empty; anchors are never inferred from identities.
    #[serde(default)]
    pub message_conversations: Vec<MessageConversationBinding>,
    /// Structured native-call observations frozen before intent dedup.
    /// Historical DTOs lack this field and stay empty; references are never
    /// reconstructed from final identities.
    #[serde(default)]
    pub native_tool_call_observations: Vec<NativeToolCallObservation>,
    pub threads: Vec<ThreadOwnerRow>,
    /// Metrics as captured when the snapshot was frozen, not recomputed.
    pub metrics: SignalMetrics,
    /// Process-spawn pairs observed before authoritative resolution.
    pub delegations: Vec<ProcessPairRow>,
}

fn process_key(process: ProcessId) -> (u32, u64) {
    (process.pid, process.start_time_ns)
}

fn pair_key(row: &ProcessPairRow) -> (u32, u64, u32, u64) {
    (
        row.parent.pid,
        row.parent.start_time_ns,
        row.child.pid,
        row.child.start_time_ns,
    )
}

impl RawIdentityInput {
    /// Freeze every snapshot field and the observed delegation pairs.
    ///
    /// Insertion order is the caller's; the result is sorted, so two snapshots
    /// with equal content serialize to equal bytes.
    pub fn from_snapshot(
        snapshot: &SignalSnapshot,
        delegations: impl IntoIterator<Item = (ProcessId, ProcessId)>,
    ) -> Self {
        let mut processes: Vec<ProcessMetadata> = snapshot.processes.values().cloned().collect();
        processes.sort_by_key(|process| process_key(process.process_id));

        let mut parent_edges: Vec<ProcessPairRow> = snapshot
            .parent_map
            .iter()
            .map(|(child, parent)| ProcessPairRow {
                parent: *parent,
                child: *child,
            })
            .collect();
        parent_edges.sort_by_key(|row| process_key(row.child));

        let mut child_processes: Vec<ChildProcessRow> = snapshot
            .child_map
            .iter()
            .map(|(parent, children)| ChildProcessRow {
                parent: *parent,
                children: children.clone(),
            })
            .collect();
        child_processes.sort_by_key(|row| process_key(row.parent));

        let mut tls_owners: Vec<TlsOwnerRow> = snapshot
            .tls_to_process
            .iter()
            .map(|(tls_session_id, process_id)| TlsOwnerRow {
                tls_session_id: tls_session_id.clone(),
                process_id: *process_id,
            })
            .collect();
        tls_owners.sort_by(|a, b| a.tls_session_id.as_str().cmp(b.tls_session_id.as_str()));

        let mut tls_participants: Vec<TlsParticipantsRow> = snapshot
            .tls_participants
            .iter()
            .map(|(tls_session_id, process_ids)| TlsParticipantsRow {
                tls_session_id: tls_session_id.clone(),
                process_ids: process_ids.clone(),
            })
            .collect();
        tls_participants.sort_by(|a, b| a.tls_session_id.as_str().cmp(b.tls_session_id.as_str()));

        let mut tls_messages: Vec<TlsMessagesRow> = snapshot
            .tls_to_messages
            .iter()
            .map(|(tls_session_id, message_ids)| TlsMessagesRow {
                tls_session_id: tls_session_id.clone(),
                message_ids: message_ids.clone(),
            })
            .collect();
        tls_messages.sort_by(|a, b| a.tls_session_id.as_str().cmp(b.tls_session_id.as_str()));

        let mut conversation_processes: Vec<ConversationProcessesRow> = snapshot
            .conversation_to_processes
            .iter()
            .map(|(conversation_id, process_ids)| ConversationProcessesRow {
                conversation_id: conversation_id.clone(),
                process_ids: process_ids.clone(),
            })
            .collect();
        conversation_processes.sort_by(|a, b| a.conversation_id.cmp(&b.conversation_id));

        let mut process_conversations: Vec<ProcessConversationsRow> = snapshot
            .process_to_conversations
            .iter()
            .map(|(process_id, conversation_ids)| ProcessConversationsRow {
                process_id: *process_id,
                conversation_ids: conversation_ids.clone(),
            })
            .collect();
        process_conversations.sort_by_key(|row| process_key(row.process_id));

        let mut direct_process_messages: Vec<ProcessMessagesRow> = snapshot
            .direct_process_messages
            .iter()
            .map(|(process_id, message_ids)| ProcessMessagesRow {
                process_id: *process_id,
                message_ids: message_ids.clone(),
            })
            .collect();
        direct_process_messages.sort_by_key(|row| process_key(row.process_id));

        let mut threads: Vec<ThreadOwnerRow> = snapshot
            .thread_to_process
            .iter()
            .map(|(tid, process_id)| ThreadOwnerRow {
                tid: *tid,
                process_id: *process_id,
            })
            .collect();
        threads.sort_by_key(|row| row.tid);

        let mut message_conversations = snapshot.message_conversation_bindings.clone();
        message_conversations.sort_by(|left, right| {
            process_key(left.process_id)
                .cmp(&process_key(right.process_id))
                .then_with(|| left.message_id.as_str().cmp(right.message_id.as_str()))
                .then_with(|| left.conversation_id.cmp(&right.conversation_id))
        });
        message_conversations.dedup();

        let mut native_tool_call_observations = snapshot.native_tool_call_observations.clone();
        native_tool_call_observations.sort_by(NativeToolCallObservation::cmp_canonical);
        native_tool_call_observations.dedup();

        let mut delegations: Vec<ProcessPairRow> = delegations
            .into_iter()
            .map(|(parent, child)| ProcessPairRow { parent, child })
            .collect();
        delegations.sort_by_key(pair_key);
        delegations.dedup();

        Self {
            schema: RAW_IDENTITY_INPUT_SCHEMA.to_string(),
            processes,
            parent_edges,
            child_processes,
            tls_owners,
            tls_participants,
            tls_messages,
            conversation_processes,
            process_conversations,
            direct_process_messages,
            message_conversations,
            native_tool_call_observations,
            threads,
            metrics: snapshot.metrics.clone(),
            delegations,
        }
    }

    /// Rebuild the exact snapshot, rejecting contradictory duplicate rows.
    ///
    /// Identical duplicates are harmless; a repeated key with a different
    /// value is an error rather than a silent last-write-wins.
    pub fn to_snapshot(&self) -> Result<SignalSnapshot, String> {
        if self.schema != RAW_IDENTITY_INPUT_SCHEMA {
            return Err(format!(
                "unsupported raw identity input schema {:?}",
                self.schema
            ));
        }

        let mut processes = HashMap::new();
        for row in &self.processes {
            insert_unique(&mut processes, row.process_id, row.clone(), "process")?;
        }

        let mut parent_map = HashMap::new();
        for row in &self.parent_edges {
            insert_unique(&mut parent_map, row.child, row.parent, "parent edge")?;
        }

        let mut child_map = HashMap::new();
        for row in &self.child_processes {
            insert_unique(
                &mut child_map,
                row.parent,
                row.children.clone(),
                "child list",
            )?;
        }

        let mut tls_to_process = HashMap::new();
        for row in &self.tls_owners {
            insert_unique(
                &mut tls_to_process,
                row.tls_session_id.clone(),
                row.process_id,
                "TLS owner",
            )?;
        }

        let mut tls_participants = HashMap::new();
        for row in &self.tls_participants {
            insert_unique(
                &mut tls_participants,
                row.tls_session_id.clone(),
                row.process_ids.clone(),
                "TLS participants",
            )?;
        }

        let mut tls_to_messages = HashMap::new();
        for row in &self.tls_messages {
            insert_unique(
                &mut tls_to_messages,
                row.tls_session_id.clone(),
                row.message_ids.clone(),
                "TLS messages",
            )?;
        }

        let mut conversation_to_processes = HashMap::new();
        for row in &self.conversation_processes {
            insert_unique(
                &mut conversation_to_processes,
                row.conversation_id.clone(),
                row.process_ids.clone(),
                "conversation processes",
            )?;
        }

        let mut process_to_conversations = HashMap::new();
        for row in &self.process_conversations {
            insert_unique(
                &mut process_to_conversations,
                row.process_id,
                row.conversation_ids.clone(),
                "process conversations",
            )?;
        }

        let mut direct_process_messages = HashMap::new();
        for row in &self.direct_process_messages {
            insert_unique(
                &mut direct_process_messages,
                row.process_id,
                row.message_ids.clone(),
                "direct process messages",
            )?;
        }

        let mut thread_to_process = HashMap::new();
        for row in &self.threads {
            insert_unique(
                &mut thread_to_process,
                row.tid,
                row.process_id,
                "thread owner",
            )?;
        }

        Ok(SignalSnapshot {
            processes,
            parent_map,
            child_map,
            tls_to_process,
            tls_participants,
            tls_to_messages,
            conversation_to_processes,
            process_to_conversations,
            direct_process_messages,
            message_conversation_bindings: self.message_conversations.clone(),
            native_tool_call_observations: self.native_tool_call_observations.clone(),
            thread_to_process,
            metrics: self.metrics.clone(),
        })
    }
}

fn insert_unique<K, V>(map: &mut HashMap<K, V>, key: K, value: V, field: &str) -> Result<(), String>
where
    K: std::hash::Hash + Eq + std::fmt::Debug,
    V: PartialEq,
{
    match map.entry(key) {
        Entry::Occupied(entry) => {
            if entry.get() != &value {
                return Err(format!(
                    "{field} key {:?} appears twice with conflicting values",
                    entry.key()
                ));
            }
            Ok(())
        }
        Entry::Vacant(entry) => {
            entry.insert(value);
            Ok(())
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::responsibility::types::NativeToolCallFormat;
    use crate::responsibility::types::NativeToolCallRef;
    use crate::responsibility::types::ids::TlsSessionId;
    use std::collections::HashMap;

    fn pid(pid: u32, start: u64) -> ProcessId {
        ProcessId::new(pid, start)
    }

    fn process(id: ProcessId, comm: &str, alive: bool) -> ProcessMetadata {
        ProcessMetadata {
            process_id: id,
            comm: comm.into(),
            pgid: Some(7),
            first_seen_ns: id.start_time_ns,
            last_seen_ns: id.start_time_ns + 10,
            exit_time_ns: if alive {
                None
            } else {
                Some(id.start_time_ns + 20)
            },
            exit_code: if alive { None } else { Some(0) },
            is_alive: alive,
            cmdline: Some(format!("{comm} --serve")),
            exe_path: Some(format!("/usr/bin/{comm}")),
            cgroup_unit: None,
        }
    }

    /// Every SignalSnapshot field is populated, including missing facts, so a
    /// roundtrip cannot pass by carrying empty maps.
    fn sample_snapshot() -> SignalSnapshot {
        let agent = pid(10, 100);
        let child = pid(11, 200);
        let tool = pid(12, 300);
        let tls_a = TlsSessionId::from("tls-a");
        let tls_b = TlsSessionId::from("tls-b");
        let msg_a = MessageId::from("msg-a");
        let msg_b = MessageId::from("msg-b");
        let msg_c = MessageId::from("msg-c");

        let mut processes = HashMap::new();
        processes.insert(agent, process(agent, "claude", true));
        processes.insert(child, process(child, "bash", false));
        processes.insert(tool, process(tool, "git", true));

        let mut parent_map = HashMap::new();
        parent_map.insert(child, agent);
        parent_map.insert(tool, child);

        let mut child_map = HashMap::new();
        child_map.insert(agent, vec![child]);
        child_map.insert(child, vec![tool]);

        let mut tls_to_process = HashMap::new();
        tls_to_process.insert(tls_a.clone(), agent);
        tls_to_process.insert(tls_b.clone(), child);

        let mut tls_participants = HashMap::new();
        tls_participants.insert(tls_a.clone(), vec![agent, child]);
        tls_participants.insert(tls_b.clone(), vec![child]);

        let mut tls_to_messages = HashMap::new();
        tls_to_messages.insert(tls_a, vec![msg_a.clone()]);
        tls_to_messages.insert(tls_b, vec![msg_b.clone(), msg_c.clone()]);

        let mut conversation_to_processes = HashMap::new();
        conversation_to_processes.insert("conv-1".into(), vec![agent, child]);

        let mut process_to_conversations = HashMap::new();
        process_to_conversations.insert(agent, vec!["conv-1".into()]);
        process_to_conversations.insert(child, vec!["conv-1".into()]);

        let mut direct_process_messages = HashMap::new();
        direct_process_messages.insert(agent, vec![msg_a.clone()]);
        direct_process_messages.insert(child, vec![msg_b, msg_c]);

        let mut thread_to_process = HashMap::new();
        thread_to_process.insert(77, agent);
        thread_to_process.insert(78, child);

        let message_conversation_bindings = vec![MessageConversationBinding {
            process_id: agent,
            message_id: msg_a.clone(),
            conversation_id: "conv-1".into(),
        }];
        let native_tool_call_observations = vec![NativeToolCallObservation {
            process_id: agent,
            message_id: msg_a.clone(),
            native_tool_call: NativeToolCallRef {
                format: NativeToolCallFormat::Anthropic,
                id: "toolu_a".into(),
            },
            declared_conversation_id: Some("conv-1".into()),
        }];

        SignalSnapshot {
            processes,
            parent_map,
            child_map,
            tls_to_process,
            tls_participants,
            tls_to_messages,
            conversation_to_processes,
            process_to_conversations,
            direct_process_messages,
            message_conversation_bindings,
            native_tool_call_observations,
            thread_to_process,
            metrics: SignalMetrics {
                process_count: 3,
                tls_session_count: 2,
                message_count: 3,
                orphan_count: 1,
                lost_event_count: 4,
            },
        }
    }

    fn reverse_map<K: std::hash::Hash + Eq, V>(map: &mut HashMap<K, V>) {
        let rows: Vec<(K, V)> = map.drain().collect();
        for (key, value) in rows.into_iter().rev() {
            map.insert(key, value);
        }
    }

    fn reversed_snapshot(base: &SignalSnapshot) -> SignalSnapshot {
        let mut snapshot = base.clone();
        reverse_map(&mut snapshot.processes);
        reverse_map(&mut snapshot.parent_map);
        reverse_map(&mut snapshot.child_map);
        reverse_map(&mut snapshot.tls_to_process);
        reverse_map(&mut snapshot.tls_participants);
        reverse_map(&mut snapshot.tls_to_messages);
        reverse_map(&mut snapshot.conversation_to_processes);
        reverse_map(&mut snapshot.process_to_conversations);
        reverse_map(&mut snapshot.direct_process_messages);
        reverse_map(&mut snapshot.thread_to_process);
        snapshot.native_tool_call_observations.reverse();
        snapshot
    }

    #[test]
    fn json_roundtrip_restores_every_snapshot_fact() {
        let snapshot = sample_snapshot();
        let delegations = vec![(pid(10, 100), pid(11, 200))];
        let input = RawIdentityInput::from_snapshot(&snapshot, delegations.clone());

        let json = serde_json::to_string(&input).expect("identity input serializes");
        let decoded: RawIdentityInput =
            serde_json::from_str(&json).expect("identity input deserializes");

        assert_eq!(decoded, input);
        assert_eq!(decoded.to_snapshot().expect("roundtrip"), snapshot);
        assert_eq!(
            decoded.delegations,
            vec![ProcessPairRow {
                parent: pid(10, 100),
                child: pid(11, 200),
            }]
        );
        assert_eq!(json, serde_json::to_string(&decoded).unwrap());
    }

    #[test]
    fn serialization_is_independent_of_hashmap_insertion_order() {
        let snapshot = sample_snapshot();
        let reordered = reversed_snapshot(&snapshot);
        let input = RawIdentityInput::from_snapshot(&snapshot, Vec::new());
        let reordered_input = RawIdentityInput::from_snapshot(&reordered, Vec::new());

        assert_eq!(
            serde_json::to_vec(&input).unwrap(),
            serde_json::to_vec(&reordered_input).unwrap()
        );
    }

    #[test]
    fn message_conversation_anchors_roundtrip_and_legacy_rows_stay_empty() {
        let snapshot = sample_snapshot();
        let input = RawIdentityInput::from_snapshot(&snapshot, Vec::new());
        assert_eq!(input.message_conversations.len(), 1);
        assert_eq!(input.native_tool_call_observations.len(), 1);

        let json = serde_json::to_value(&input).unwrap();
        let decoded: RawIdentityInput = serde_json::from_value(json.clone()).unwrap();
        assert_eq!(decoded, input);
        assert_eq!(decoded.to_snapshot().unwrap(), snapshot);

        // A checkpoint written before anchors were captured has no field. It
        // must deserialize as no evidence; the rows are never inferred.
        let mut legacy = json;
        legacy
            .as_object_mut()
            .unwrap()
            .remove("message_conversations");
        legacy
            .as_object_mut()
            .unwrap()
            .remove("native_tool_call_observations");
        let legacy: RawIdentityInput = serde_json::from_value(legacy).unwrap();
        assert!(legacy.message_conversations.is_empty());
        assert!(legacy.native_tool_call_observations.is_empty());
        let mut anchorless = snapshot;
        anchorless.message_conversation_bindings.clear();
        anchorless.native_tool_call_observations.clear();
        assert_eq!(legacy.to_snapshot().unwrap(), anchorless);
    }

    #[test]
    fn graph_anchor_rows_are_canonical_and_roundtrip_exactly() {
        use crate::responsibility::signals::SignalGraph;

        let mut graph = SignalGraph::new();
        let high = pid(200, 2);
        let low = pid(100, 1);
        graph.observe_message_conversation(high, MessageId::from("msg-z"), "conv-z");
        graph.observe_message_conversation(low, MessageId::from("msg-a"), "conv-a");

        let snapshot = graph.snapshot();
        assert_eq!(
            snapshot.message_conversation_bindings,
            vec![
                MessageConversationBinding {
                    process_id: low,
                    message_id: MessageId::from("msg-a"),
                    conversation_id: "conv-a".into(),
                },
                MessageConversationBinding {
                    process_id: high,
                    message_id: MessageId::from("msg-z"),
                    conversation_id: "conv-z".into(),
                },
            ],
            "the frozen snapshot uses the same canonical row order as the DTO"
        );
        let input = RawIdentityInput::from_snapshot(&snapshot, Vec::new());
        assert_eq!(input.to_snapshot().unwrap(), *snapshot);
    }

    #[test]
    fn conflicting_duplicate_key_is_rejected() {
        let snapshot = sample_snapshot();
        let mut input = RawIdentityInput::from_snapshot(&snapshot, Vec::new());
        let mut impostor = input.processes[0].clone();
        impostor.comm = "impostor".into();
        input.processes.push(impostor);

        let error = input.to_snapshot().expect_err("conflict must be rejected");
        assert!(error.contains("conflicting"), "{error}");
    }

    #[test]
    fn identical_duplicate_key_is_not_a_conflict() {
        let snapshot = sample_snapshot();
        let mut input = RawIdentityInput::from_snapshot(&snapshot, Vec::new());
        input.processes.push(input.processes[0].clone());

        assert_eq!(input.to_snapshot().unwrap(), snapshot);
    }
}
