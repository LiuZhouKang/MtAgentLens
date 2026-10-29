// SPDX-License-Identifier: Apache-2.0 OR MIT
//! E5 identity microbenchmark generator.
//!
//! Builds the six topologies from `docs/paper/身份解析.md` (T1–T6) as synthetic
//! signal graphs with a *pre-declared* ground-truth partition, runs the real
//! `IdentityResolver`, and prints one JSON line per trace. The Python evaluator
//! (`scripts/evaluation/e5_identity_microbenchmark.py`) computes the metrics.
//!
//! Ground truth comes from the specification table, never from the resolver
//! output: a topology the resolver collapses is a finding, not a reason to
//! rewrite the label.

use std::collections::{HashMap, HashSet};

use mtagentlens::responsibility::identity::IdentityResolver;
use mtagentlens::responsibility::signals::SignalGraph;
use mtagentlens::responsibility::signals::signal_snapshot::ProcessExecInfo;
use mtagentlens::responsibility::types::{ProcessId, TlsSessionId};

const TRACES_PER_TOPOLOGY: u32 = 30;
const AGENT_COMM: &str = "claude";
const TOOL_COMM: &str = "python3";

struct EventSpec {
    event_id: String,
    process: ProcessId,
    truth_actor: &'static str,
    /// Conversation carried by this event, when it has one. Events with a
    /// conversation use the conversation-level agent; actions do not.
    conversation: Option<&'static str>,
}

struct Trace {
    name: &'static str,
    graph: SignalGraph,
    events: Vec<EventSpec>,
    delegations: Vec<(ProcessId, ProcessId)>,
}

fn pid(offset: u32, base: u32, start: u64) -> ProcessId {
    ProcessId::new(base + offset, start)
}

fn exec(graph: &mut SignalGraph, process: ProcessId, parent: ProcessId, comm: &str, ts: u64) {
    graph.observe_exec(
        process,
        parent,
        ProcessExecInfo {
            comm: comm.into(),
            ..Default::default()
        },
        None,
        ts,
    );
}

fn event(event_id: String, process: ProcessId, truth_actor: &'static str) -> EventSpec {
    EventSpec {
        event_id,
        process,
        truth_actor,
        conversation: None,
    }
}

fn conversation_event(
    event_id: String,
    process: ProcessId,
    truth_actor: &'static str,
    conversation: &'static str,
) -> EventSpec {
    EventSpec {
        event_id,
        process,
        truth_actor,
        conversation: Some(conversation),
    }
}

fn build(name: &'static str, t: u32) -> Trace {
    let base = 10_000 + t * 10;
    let ts = 1_000 + u64::from(t) * 100;
    let mut graph = SignalGraph::new();
    let mut events = Vec::new();
    let mut delegations = Vec::new();

    match name {
        // T1: one root with an LLM conversation; tool children inherit through
        // the process tree. Expected: all processes are the same agent.
        "T1_single_agent" => {
            let root = pid(0, base, 1);
            let tool1 = pid(1, base, 2);
            let tool2 = pid(2, base, 3);
            exec(&mut graph, root, pid(1, 0, 0), AGENT_COMM, ts);
            exec(&mut graph, tool1, root, TOOL_COMM, ts + 1);
            exec(&mut graph, tool2, tool1, TOOL_COMM, ts + 2);
            graph.observe_conversation(root, "conv-a", ts);
            events.push(event(format!("{t}-root-1"), root, "agent-A"));
            events.push(event(format!("{t}-root-2"), root, "agent-A"));
            events.push(event(format!("{t}-tool1"), tool1, "agent-A"));
            events.push(event(format!("{t}-tool2"), tool2, "agent-A"));
        }
        // T2: one process, two conversations in sequence. Expected: split at the
        // switch point (the reviewer's "same process, sequential sessions" case).
        "T2_same_process_sequential" => {
            let process = pid(0, base, 1);
            exec(&mut graph, process, pid(1, 0, 0), AGENT_COMM, ts);
            graph.observe_conversation(process, "conv-a", ts);
            graph.observe_conversation(process, "conv-b", ts + 10);
            events.push(conversation_event(
                format!("{t}-conv-a-1"),
                process,
                "agent-A",
                "conv-a",
            ));
            events.push(conversation_event(
                format!("{t}-conv-a-2"),
                process,
                "agent-A",
                "conv-a",
            ));
            events.push(conversation_event(
                format!("{t}-conv-b-1"),
                process,
                "agent-B",
                "conv-b",
            ));
            events.push(conversation_event(
                format!("{t}-conv-b-2"),
                process,
                "agent-B",
                "conv-b",
            ));
        }
        // T3: one process, two conversations interleaved. Expected: ambiguous;
        // the metric treats the two sessions as distinct actors.
        "T3_same_process_concurrent" => {
            let process = pid(0, base, 1);
            exec(&mut graph, process, pid(1, 0, 0), AGENT_COMM, ts);
            graph.observe_conversation(process, "conv-a", ts);
            graph.observe_conversation(process, "conv-b", ts + 5);
            events.push(conversation_event(
                format!("{t}-conv-a-1"),
                process,
                "agent-A",
                "conv-a",
            ));
            events.push(conversation_event(
                format!("{t}-conv-a-2"),
                process,
                "agent-A",
                "conv-a",
            ));
            events.push(conversation_event(
                format!("{t}-conv-b-1"),
                process,
                "agent-B",
                "conv-b",
            ));
            events.push(conversation_event(
                format!("{t}-conv-b-2"),
                process,
                "agent-B",
                "conv-b",
            ));
        }
        // T4: two agents reuse one TLS session. Expected: keep them separate;
        // a connection is not an identity.
        "T4_connection_reuse" => {
            let first = pid(0, base, 1);
            let second = pid(1, base, 2);
            exec(&mut graph, first, pid(1, 0, 0), AGENT_COMM, ts);
            exec(&mut graph, second, pid(1, 0, 0), AGENT_COMM, ts + 1);
            graph.observe_tls(first, TlsSessionId::from("shared-session"), ts);
            graph.observe_tls(second, TlsSessionId::from("shared-session"), ts + 2);
            graph.observe_conversation(first, "conv-a", ts);
            graph.observe_conversation(second, "conv-b", ts + 1);
            events.push(event(format!("{t}-first-1"), first, "agent-A"));
            events.push(event(format!("{t}-first-2"), first, "agent-A"));
            events.push(event(format!("{t}-second-1"), second, "agent-B"));
            events.push(event(format!("{t}-second-2"), second, "agent-B"));
        }
        // T5: subagent forks with its own TLS session. Expected: split at exec.
        "T5_subagent_fork" => {
            let parent = pid(0, base, 1);
            let child = pid(1, base, 2);
            exec(&mut graph, parent, pid(1, 0, 0), AGENT_COMM, ts);
            exec(&mut graph, child, parent, AGENT_COMM, ts + 1);
            graph.observe_tls(parent, TlsSessionId::from("parent-session"), ts);
            graph.observe_tls(child, TlsSessionId::from("child-session"), ts + 2);
            graph.observe_conversation(parent, "conv-a", ts);
            graph.observe_conversation(child, "conv-b", ts + 1);
            events.push(event(format!("{t}-parent-1"), parent, "agent-A"));
            events.push(event(format!("{t}-parent-2"), parent, "agent-A"));
            events.push(event(format!("{t}-child-1"), child, "agent-B"));
            events.push(event(format!("{t}-child-2"), child, "agent-B"));
        }
        // T6: parent hands off to a child sharing context and TLS. Expected: the
        // delegation keeps two agents even though the context is shared.
        "T6_delegated_handoff" => {
            let parent = pid(0, base, 1);
            let child = pid(1, base, 2);
            exec(&mut graph, parent, pid(1, 0, 0), AGENT_COMM, ts);
            exec(&mut graph, child, parent, AGENT_COMM, ts + 1);
            graph.observe_tls(parent, TlsSessionId::from("session-a"), ts);
            graph.observe_tls(child, TlsSessionId::from("session-a"), ts + 2);
            graph.observe_conversation(parent, "conv-shared", ts);
            graph.observe_conversation(child, "conv-shared", ts + 1);
            delegations.push((parent, child));
            events.push(event(format!("{t}-parent-1"), parent, "agent-A"));
            events.push(event(format!("{t}-parent-2"), parent, "agent-A"));
            events.push(event(format!("{t}-child-1"), child, "agent-B"));
            events.push(event(format!("{t}-child-2"), child, "agent-B"));
        }
        other => panic!("unknown topology: {other}"),
    }

    Trace {
        name,
        graph,
        events,
        delegations,
    }
}

fn run(name: &'static str) -> Vec<serde_json::Value> {
    let mut out = Vec::new();
    for t in 0..TRACES_PER_TOPOLOGY {
        let trace = build(name, t);
        let snapshot = trace.graph.snapshot();
        let mut resolver = IdentityResolver::new();
        let mut processes: Vec<ProcessId> =
            trace.events.iter().map(|event| event.process).collect();
        processes.sort_by_key(|process| (process.pid, process.start_time_ns));
        processes.dedup();
        for process in &processes {
            resolver.resolve_provisional(*process, &snapshot);
        }
        let delegated: HashSet<(ProcessId, ProcessId)> =
            trace.delegations.iter().copied().collect();
        let resolution = resolver.resolve_authoritative_with_delegations(&snapshot, &delegated);
        let mut predicted_by_process: HashMap<ProcessId, String> = HashMap::new();
        for agent in &resolution.agents {
            for process in &agent.process_ids {
                predicted_by_process.insert(*process, agent.agent_id.to_string());
            }
        }
        let mut predicted_by_conversation: HashMap<(ProcessId, String), String> = HashMap::new();
        for identity in &resolution.conversation_agents {
            predicted_by_conversation.insert(
                (identity.process_id, identity.conversation_id.clone()),
                identity.agent_id.to_string(),
            );
        }
        let unresolved = trace
            .events
            .iter()
            .filter(|event| {
                let via_conversation = event.conversation.is_some_and(|conversation| {
                    predicted_by_conversation
                        .contains_key(&(event.process, conversation.to_string()))
                });
                !via_conversation && !predicted_by_process.contains_key(&event.process)
            })
            .count();
        let events: Vec<serde_json::Value> = trace
            .events
            .iter()
            .map(|event| {
                let predicted = event
                    .conversation
                    .and_then(|conversation| {
                        predicted_by_conversation
                            .get(&(event.process, conversation.to_string()))
                            .cloned()
                    })
                    .or_else(|| predicted_by_process.get(&event.process).cloned());
                serde_json::json!({
                    "event_id": event.event_id,
                    "truth_actor": event.truth_actor,
                    "predicted_actor": predicted,
                })
            })
            .collect();
        out.push(serde_json::json!({
            "schema": "mtagentlens.e5-identity-trace/1",
            "trace_id": format!("{}#{t}", trace.name),
            "topology": trace.name,
            "events": events,
            "unresolved_processes": unresolved,
            "subagent_boundaries": resolution.subagent_boundaries.len(),
        }));
    }
    out
}

fn main() {
    let names = [
        "T1_single_agent",
        "T2_same_process_sequential",
        "T3_same_process_concurrent",
        "T4_connection_reuse",
        "T5_subagent_fork",
        "T6_delegated_handoff",
    ];
    for name in names {
        for value in run(name) {
            println!("{value}");
        }
    }
}
