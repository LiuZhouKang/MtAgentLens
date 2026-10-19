// SPDX-License-Identifier: Apache-2.0 OR MIT
//
// routing.rs — FrameworkEvent stringly-typed dispatch to signals, identity,
// and evidence subsystems.
//
// Per spec §15, dispatches on event.source and event.data fields using
// pattern matching on "event" type, source name, and data keys:
//
//   EXEC/EXIT → signals + identity
//   TLS       → signals
//   SSE/HTTP  → evidence (IntentExtractor + EvidenceCorrelator)
//   Syscall   → evidence (ActionClassifier + EvidenceCorrelator)

use crate::framework::Event;
use crate::responsibility::evidence::action_classifier::ActionClassifier;
use crate::responsibility::evidence::correlator::EvidenceCorrelator;
use crate::responsibility::evidence::intent_extractor::IntentExtractor;
use crate::responsibility::identity::resolver::IdentityResolver;
use crate::responsibility::runtime::buffer::GraphDeltaBuffer;
use crate::responsibility::signals::SignalGraph;
use crate::responsibility::signals::signal_snapshot::ProcessExecInfo;
use crate::responsibility::types::Intent;
use crate::responsibility::types::alert_types::BehaviorTimelineEntry;
use crate::responsibility::types::evidence_types::IntentAuthoritySource;
use crate::responsibility::types::ids::{MessageId, ProcessId, TlsSessionId};
use serde_json::Value;
use std::sync::atomic::{AtomicU64, Ordering};

/// Diagnostic counters for EvidenceIntent processing.
pub static DIAG_EVIDENCE_ATTEMPTED: AtomicU64 = AtomicU64::new(0);
pub static DIAG_EVIDENCE_INTENTS: AtomicU64 = AtomicU64::new(0);
/// Intents dropped because an identical (process, tool, args) intent was
/// already accepted. Visible so a multi-agent run can distinguish "the second
/// agent did nothing" from "its intent was deduplicated away".
pub static DIAG_INTENTS_DEDUPED: AtomicU64 = AtomicU64::new(0);
/// Structured tool calls read from the agent's own on-disk trajectory.
pub static DIAG_OPENCLAW_TOOL_CALLS: AtomicU64 = AtomicU64::new(0);
pub static DIAG_ACTIONS_PRODUCED: AtomicU64 = AtomicU64::new(0);
/// Actions discarded because the process had no agent ancestry. A non-zero
/// value on a run that clearly contains agent activity means the agent `comm`
/// boundary does not cover this agent — extend it with
/// `MTAGENTLENS_AGENT_COMMS`.
pub static DIAG_ACTIONS_DROPPED_NO_AGENT: AtomicU64 = AtomicU64::new(0);
/// Actions discarded as eBPF self-access noise (`/sys/kernel/` paths).
pub static DIAG_ACTIONS_DROPPED_BPF_NOISE: AtomicU64 = AtomicU64::new(0);

/// Destination tags used by `route_event` to indicate which subsystems
/// should process the event.
#[derive(Debug, Clone, PartialEq)]
pub enum RouteTarget {
    /// Feed into SignalGraph (EXEC, EXIT, TLS events).
    Signals,
    /// Feed into IdentityResolver (after signal update).
    Identity,
    /// Feed into EvidenceCorrelator via IntentExtractor (SSE/HTTP events).
    EvidenceIntent,
    /// Feed into EvidenceCorrelator via ActionClassifier (syscall events).
    EvidenceAction,
    /// Skip routing — event is informational / not actionable.
    None,
}

/// Result of dispatching one event through the signal/id/evidence subsystems.
///
/// Contains any output events the runtime should forward downstream,
/// plus accumulated graph/timeline data for the buffer.
/// A message→TLS binding for the runtime to persist.
///
/// A dedicated struct rather than `PersistRecord`: the latter is neither
/// `Debug` nor `Clone`, and `DispatchResult` derives both.
#[derive(Debug, Clone)]
pub struct PendingMessageBinding {
    pub message_id: MessageId,
    pub pid: u32,
    pub start_time_ns: u64,
    pub tls_session_id: Option<TlsSessionId>,
    pub timestamp_ns: u64,
}

/// Derive the TLS session identity for an event.
///
/// Order of preference:
///   1. an explicit `connection_id` / `tls_session` field (if a producer ever
///      emits one),
///   2. the `ssl_ptr` connection pointer emitted by `tls_capture` — stable for
///      a connection's lifetime, distinct between concurrent connections,
///   3. a fresh random id, which matches nothing and is a last resort only.
///
/// Shared by every consumer so the two call sites cannot drift apart again.
pub fn resolve_tls_session_id(event: &Event) -> TlsSessionId {
    event
        .data
        .get("connection_id")
        .or_else(|| event.data.get("tls_session"))
        .and_then(|v| v.as_str())
        .map(TlsSessionId::from)
        .or_else(|| {
            event
                .data
                .get("ssl_ptr")
                .and_then(|v| v.as_u64())
                .filter(|p| *p != 0)
                .map(|p| TlsSessionId::from(format!("ssl-{p}")))
        })
        .unwrap_or_else(TlsSessionId::new)
}

#[derive(Debug, Clone, Default)]
pub struct DispatchResult {
    /// Output events to forward to downstream analyzers.
    pub output_events: Vec<Event>,
    /// Message→TLS-session bindings discovered while extracting intents.
    ///
    /// Routing has no persistence handle, so it hands these back to the
    /// runtime, which owns the writer. This replaces the second intent path
    /// (runtime's `flush_conn_buffers`) that used to produce bindings as a
    /// side effect of re-extracting the same payload, duplicating every
    /// intent because `MessageId` is freshly generated each run.
    pub message_bindings: Vec<PendingMessageBinding>,
}

/// Route a single framework event into the appropriate subsystems.
///
/// This is the main entry point called from `ResponsibilityRuntime::route_event()`.
/// It inspects the event's `source` and `data` fields to determine which
/// subsystem(s) should process it, then calls them.
///
/// # Return
///
/// A `DispatchResult` containing output events for downstream consumption.
pub fn dispatch_event(
    event: &Event,
    signal_graph: &mut SignalGraph,
    identity_resolver: &mut IdentityResolver,
    evidence_correlator: &mut EvidenceCorrelator,
    buffer: &mut GraphDeltaBuffer,
) -> DispatchResult {
    let mut result = DispatchResult::default();

    // Raw fragments buffered by PayloadNormalizer are not authoritative
    // messages. Feeding them to intent/action extraction produced phantom
    // intents from compressed bytes once the record preset stopped dropping
    // them; the decoded replacement is dispatched when the message completes.
    if event.data.get("partial").and_then(|value| value.as_bool()) == Some(true) {
        return result;
    }

    // ── Out-of-band tool calls (agent's own trajectory) ────────────────
    //
    // These events carry no wire body: the structured `name` + `args` come
    // from the agent's record. They are labelled `OutOfBandAsserted` and are
    // correlated with the gateway's observed actions exactly like an intent
    // extracted from the wire.
    if event.source == "openclaw" {
        if event.data.get("event").and_then(Value::as_str) == Some("TOOL_CALL") {
            let snapshot = signal_graph.snapshot();
            let process_id = resolve_process_id(extract_or_default_process_id(event), &snapshot);
            if let Some(intent) = outofband_intent(event, process_id) {
                if buffer.accept_intent_once(&intent) {
                    DIAG_EVIDENCE_INTENTS.fetch_add(1, Ordering::Relaxed);
                    DIAG_OPENCLAW_TOOL_CALLS.fetch_add(1, Ordering::Relaxed);
                    buffer.push_intent(intent.clone());
                    for ev in evidence_correlator.on_intent(intent, &snapshot) {
                        buffer.push_evidence(ev);
                    }
                } else {
                    DIAG_INTENTS_DEDUPED.fetch_add(1, Ordering::Relaxed);
                }
            }
        }
        return result;
    }

    // ── Phase 1: Determine event type ──────────────────────────────────
    let event_type = event.data.get("event").and_then(|v| v.as_str());

    let route = classify_event(event, event_type);

    // ── Route 1: Signals & Identity (always process for process/tls events) ──
    if matches!(route, RouteTarget::Signals | RouteTarget::Identity) {
        match event_type {
            Some("EXEC") => {
                if let Some((child, parent, info, pgid)) = parse_exec_event(event) {
                    signal_graph.observe_exec(child, parent, info, pgid, event.timestamp);
                }
            }
            Some("EXIT") => {
                if let Some((process_id, exit_code)) = parse_exit_event(event) {
                    signal_graph.observe_exit(process_id, exit_code, event.timestamp);
                }
            }
            _ => {
                // TLS events: ssl_*/tls_* sources or events with connection_id
                if (event.source.contains("ssl")
                    || event.source.contains("tls")
                    || event.data.get("connection_id").is_some())
                    && let Some(process_id) = extract_process_id(event)
                {
                    // Resolve the correct ProcessId BEFORE observe_tls.
                    // TLS events carry start_time_ns=0. Without this fix,
                    // the signal graph stores ProcessId(pid,0) in its TLS
                    // map, which never matches ProcessId(pid,real_start)
                    // from the EXEC event. This breaks process-tree queries
                    // and downstream identity resolution (union-find).
                    let snapshot = signal_graph.snapshot();
                    let resolved_pid = resolve_process_id(process_id, &snapshot);
                    let tls_id = resolve_tls_session_id(event);
                    signal_graph.observe_tls(resolved_pid, tls_id, event.timestamp);
                }
            }
        }

        // After signal update, resolve identity
        let snapshot = signal_graph.snapshot();
        if let Some(process_id) =
            extract_process_id(event).map(|pid| resolve_process_id(pid, &snapshot))
        {
            let _identity = identity_resolver.resolve_provisional(process_id, &snapshot);

            let desc = describe_signal_event(event, event_type);
            if !desc.is_empty() {
                buffer.push_timeline(BehaviorTimelineEntry {
                    timestamp_ns: event.timestamp,
                    event_type: event_type.unwrap_or("signal").to_string(),
                    description: desc,
                    resource: None,
                    evidence_ref: None,
                    ..Default::default()
                });
            }
        }
    }

    // ── Route 2: Evidence (may run in addition to Signals) ──
    // Extract message body from any event that might carry LLM response data.
    // SSEProcessorEvent → json_content/text_content/sse_events; HTTPEvent → body;
    // raw SSL events → "data" field (captured plaintext HTTP traffic).
    //
    // Priority order:
    //   1. body         — HTTP parser raw response body (may be truncated)
    //   2. content      — generic content field
    //   3. sse_events[] — SSE processor raw event array → reconstructed SSE text
    //   4. json_content — SSE processor simplified extract (may lose tool_use envelope)
    //   5. text_content — SSE processor text response
    //   6. data         — raw SSL plaintext
    //
    // sse_events has HIGHER priority than json_content because it preserves the
    // original Anthropic content_block_start/delta/stop format that
    // IntentExtractor::extract_from_sse requires for tool_use detection.
    // json_content is a simplified {command, description} summary that lacks the
    // standard type: "tool_use" envelope, making it invisible to the SSE parser.
    let sse_body: Option<String> = reconstruct_sse_from_events(event);
    let body_candidate: Option<&str> = event
        .data
        .get("body")
        .and_then(|v| v.as_str())
        .or_else(|| event.data.get("content").and_then(|v| v.as_str()))
        .or(sse_body.as_deref())
        .or_else(|| event.data.get("json_content").and_then(|v| v.as_str()))
        .or_else(|| event.data.get("text_content").and_then(|v| v.as_str()))
        .or_else(|| event.data.get("data").and_then(|v| v.as_str()));

    // Try evidence extraction for:
    // - Events explicitly classified as EvidenceIntent (http_parser, sse_processor)
    // - Raw SSL events with non-empty data payload (captured HTTP traffic)
    let is_evidence_route = matches!(route, RouteTarget::EvidenceIntent);
    let has_payload = body_candidate.is_some_and(|b| !b.is_empty());

    if (is_evidence_route || has_payload)
        && let Some(body) = body_candidate
    {
        DIAG_EVIDENCE_ATTEMPTED.fetch_add(1, Ordering::Relaxed);

        let snapshot = signal_graph.snapshot();
        let process_id = resolve_process_id(extract_or_default_process_id(event), &snapshot);

        let message_id = event
            .data
            .get("message_id")
            .and_then(|v| v.as_str())
            .map(MessageId::from)
            .unwrap_or_else(MessageId::new);

        let msg_id_for_extract = message_id.clone();
        let authority_source = determine_authority_source(event, body);
        let intents = IntentExtractor::extract(
            body,
            authority_source,
            process_id,
            msg_id_for_extract,
            event.timestamp,
        );

        // Also extract user messages from request bodies so the
        // ContentBoundary can detect injection patterns in the
        // initial task prompt.
        let user_prompt_intents =
            extract_request_user_messages(body, process_id, message_id.clone(), event.timestamp);

        let total_intents = intents.len() + user_prompt_intents.len();
        if total_intents > 0 {
            // Record message→TLS binding here, where the intent is actually
            // extracted. Previously this was produced by the runtime's second
            // extraction path as a side effect of re-parsing the same payload,
            // which duplicated every intent (MessageId is freshly generated
            // per run, so the correlator's (intent_id, action_id) dedup key
            // never matched). Binding to the TLS session of the owning
            // process keeps the replay chain intact.
            if let Some(tls_sid) = snapshot
                .tls_sessions_for_process(process_id)
                .into_iter()
                .next()
            {
                result.message_bindings.push(PendingMessageBinding {
                    message_id: message_id.clone(),
                    pid: process_id.pid,
                    start_time_ns: process_id.start_time_ns,
                    tls_session_id: Some(tls_sid),
                    timestamp_ns: event.timestamp,
                });
            }
        }

        for intent in intents.into_iter().chain(user_prompt_intents) {
            // The request body carries the full conversation history, so the
            // same tool call reappears in every later turn. Accept each
            // distinct intent once; otherwise evidence, claims and alerts are
            // all multiplied by the number of subsequent requests.
            if !buffer.accept_intent_once(&intent) {
                DIAG_INTENTS_DEDUPED.fetch_add(1, Ordering::Relaxed);
                continue;
            }
            DIAG_EVIDENCE_INTENTS.fetch_add(1, Ordering::Relaxed);
            let intent_clone = intent.clone();
            buffer.push_intent(intent_clone);
            let evidence_list = evidence_correlator.on_intent(intent, &snapshot);
            for ev in evidence_list {
                buffer.push_evidence(ev);
            }
        }
    }

    // ── Route 3: EvidenceAction (syscall + process BPF events) ──
    // Runs for events explicitly classified as EvidenceAction, AND for
    // EXEC/FILE_OPEN/etc. events that classify_event() routes to Signals
    // (early return). This ensures process events produce actions even
    // when they also go through the Signals path.
    let has_actionable_event = event
        .data
        .get("event")
        .and_then(|v| v.as_str())
        .map(|e| {
            matches!(
                e,
                "EXEC"
                    | "PROCESS_EXEC"
                    | "FILE_OPEN"
                    | "FILE_DELETE"
                    | "FILE_WRITE"
                    | "FILE_TRUNCATE"
                    | "FILE_RENAME"
                    | "NETWORK_CONNECT"
                    | "NETWORK_SEND"
            )
        })
        .unwrap_or(false);

    static DIAG_EV_ACT: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
    let dn = DIAG_EV_ACT.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    if has_actionable_event && dn < 5 {
        let evt = event.data.get("event").and_then(|v| v.as_str());
        let syscall = parse_process_event_to_syscall(event);
        eprintln!("[EvAct] {} evt={:?} syscall={:?}", dn, evt, syscall);
    }

    if matches!(route, RouteTarget::EvidenceAction) || has_actionable_event {
        let snapshot = signal_graph.snapshot();
        let process_id = resolve_process_id(extract_or_default_process_id(event), &snapshot);

        if let Some((syscall_name, raw_args)) = parse_process_event_to_syscall(event)
            && let Some(action) =
                ActionClassifier::classify(&syscall_name, &raw_args, process_id, event.timestamp)
        {
            // Filter BPF/eBPF self-access noise: paths under /sys/kernel/
            // are the BPF programs reading trace event IDs, not agent actions.
            let is_bpf_noise = action.normalized_args.iter().any(|a| {
                if let crate::responsibility::types::NormalizedArg::Path(p) = a {
                    p.starts_with("/sys/kernel/")
                } else {
                    false
                }
            });
            // Only keep actions from processes with agent ancestry.
            // A process has agent ancestry if it (or an ancestor) has TLS
            // sessions or an agent comm. The event's own comm is also checked:
            // ring-buffer events from different CPUs are not globally ordered,
            // so an agent's first connect/file op can arrive before its EXEC
            // has been ingested into the signal graph. Dropping those actions
            // loses the start of every agent run.
            let has_agent = snapshot.has_agent_ancestry(process_id)
                || crate::responsibility::identity::union_find::is_agent_comm(&event.comm);
            if !is_bpf_noise && has_agent {
                DIAG_ACTIONS_PRODUCED.fetch_add(1, Ordering::Relaxed);
                let action_clone = action.clone();
                buffer.push_action(action_clone);
                let evidence_list = evidence_correlator.on_action(action, &snapshot);
                for ev in evidence_list {
                    buffer.push_evidence(ev);
                }
            } else if is_bpf_noise {
                // The collector's own /sys/kernel reads. Counted separately so
                // the no-agent counter stays a signal about the agent boundary
                // rather than noise from unrelated processes.
                DIAG_ACTIONS_DROPPED_BPF_NOISE.fetch_add(1, Ordering::Relaxed);
            } else {
                DIAG_ACTIONS_DROPPED_NO_AGENT.fetch_add(1, Ordering::Relaxed);
            }
        }
    }

    result
}

// ─── Event Classification ─────────────────────────────────────────────

/// Public diagnostic wrapper for classify_event (used by runtime counters).
pub fn classify_event_public(event: &Event, event_type: Option<&str>) -> RouteTarget {
    classify_event(event, event_type)
}

/// Sources whose raw payload must never enter intent extraction.
///
/// `stdio` events carry captured file/pipe bytes; treating them as an LLM
/// response lets a file's contents become tool intents.
pub fn is_terminal_runtime_source(source: &str) -> bool {
    source == "stdio"
}

/// Classify an event into a routing target based on its source and data shape.
fn classify_event(event: &Event, event_type: Option<&str>) -> RouteTarget {
    // 1. Explicit event type
    match event_type {
        Some("EXEC") | Some("EXIT") => return RouteTarget::Signals,
        _ => {}
    }

    // 2. TLS events: connection_id or tid in data, or source indicates TLS
    if event.data.get("connection_id").is_some()
        || event.source.contains("ssl")
        || event.source.contains("tls")
    {
        return RouteTarget::Signals;
    }

    // 3. SSE/HTTP events: source from sse_processor or http_parser
    if event.source == "sse_processor"
        || event.source.contains("http")
        || event.data.get("body").is_some()
        || event.data.get("tool_use").is_some()
    {
        return RouteTarget::EvidenceIntent;
    }

    // 4. Syscall/process BPF file&net events → EvidenceAction
    if event.data.get("syscall").is_some() {
        return RouteTarget::EvidenceAction;
    }
    if let Some(evt) = event.data.get("event").and_then(|v| v.as_str()) {
        match evt {
            "FILE_OPEN" | "FILE_DELETE" | "FILE_WRITE" | "FILE_TRUNCATE" | "FILE_RENAME"
            | "NETWORK_CONNECT" | "NETWORK_SEND" | "EXEC" | "PROCESS_EXEC" => {
                return RouteTarget::EvidenceAction;
            }
            _ => {}
        }
    }

    RouteTarget::None
}

/// Reconstruct a full SSE text body from an SSEProcessorEvent's `sse_events` array.
///
/// SSEProcessorEvent stores parsed raw SSE events as `[{"data": "..."}, ...]`.
/// This function converts them back to standard SSE text format:
///
/// ```text
/// data: {"type":"content_block_start","content_block":{...}}
///
/// data: {"type":"content_block_delta","delta":{...}}
///
/// ```
///
/// The reconstructed body is compatible with IntentExtractor::extract_from_sse(),
/// which expects `data: <json>` lines separated by blank lines.
///
/// This is the universal bridge between the SSE processor's parsed output and
/// the intent extractor's Anthropic SSE parser — it preserves the original
/// content_block_start/delta/stop envelope that json_content loses.
fn reconstruct_sse_from_events(event: &Event) -> Option<String> {
    event
        .data
        .get("sse_events")
        .and_then(|v| v.as_array())
        .map(|events| {
            events
                .iter()
                .filter_map(|e| {
                    let data = e.get("data").and_then(|d| d.as_str())?;
                    let event_type = e
                        .get("event")
                        .and_then(|t| t.as_str())
                        .map(|t| format!("event: {}\n", t))
                        .unwrap_or_default();
                    Some(format!("{}data: {}\n", event_type, data))
                })
                .collect::<Vec<_>>()
                .join("\n")
        })
        .filter(|s| !s.is_empty())
}

// ─── Event Parsing Helpers ────────────────────────────────────────────

/// Parse an EXEC event into (child ProcessId, parent ProcessId, comm, pgid).
fn parse_exec_event(event: &Event) -> Option<(ProcessId, ProcessId, ProcessExecInfo, Option<u32>)> {
    let pid = event
        .data
        .get("pid")
        .and_then(|v| v.as_u64())
        .unwrap_or(event.pid as u64) as u32;
    let start_time_ns = event
        .data
        .get("start_time_ns")
        .and_then(|v| v.as_u64())
        .unwrap_or(0);
    let ppid = event
        .data
        .get("ppid")
        .and_then(|v| v.as_u64())
        .map(|v| v as u32)
        .unwrap_or(0);
    let parent_start_time_ns = event
        .data
        .get("parent_start_time_ns")
        .and_then(|v| v.as_u64())
        .unwrap_or(0);
    let comm = event
        .data
        .get("comm")
        .and_then(|v| v.as_str())
        .unwrap_or(&event.comm)
        .to_string();
    // pgid is NOT emitted by the process BPF program: `include/process.h`
    // declares the field, but `process.bpf.c` never fills it and
    // `process.c` never prints it, so reading it off the EXEC event always
    // produced `None` — a field that looked plumbed while carrying nothing.
    // Read it from /proc instead, at the one moment the process is guaranteed
    // to exist (we are handling its EXEC) — the same approach as cgroup below.
    let pgid = crate::responsibility::runtime::proc_stat::read_pgid(pid);

    // `full_command` and `filename` are already emitted by the process BPF
    // program for EXEC events (see bpftools/process/process.c), so no
    // eBPF change is needed to obtain them.
    let cmdline = event
        .data
        .get("full_command")
        .and_then(|v| v.as_str())
        .map(str::to_string);
    let exe_path = event
        .data
        .get("filename")
        .and_then(|v| v.as_str())
        .map(str::to_string);

    // cgroup is NOT collectable from eBPF here — read it from /proc while the
    // process is definitely still alive (we are handling its EXEC).
    let cgroup_unit = crate::responsibility::runtime::cgroup::read_unit(pid);

    let child = ProcessId::new(pid, start_time_ns);
    let parent = ProcessId::new(ppid, parent_start_time_ns);

    let info = ProcessExecInfo {
        comm,
        cmdline,
        exe_path,
        cgroup_unit,
    };

    Some((child, parent, info, pgid))
}

/// Parse an EXIT event into (ProcessId, exit_code).
fn parse_exit_event(event: &Event) -> Option<(ProcessId, i32)> {
    let pid = event
        .data
        .get("pid")
        .and_then(|v| v.as_u64())
        .unwrap_or(event.pid as u64) as u32;
    let start_time_ns = event
        .data
        .get("start_time_ns")
        .and_then(|v| v.as_u64())
        .unwrap_or(0);
    let exit_code = event
        .data
        .get("exit_code")
        .and_then(|v| v.as_i64())
        .unwrap_or(0) as i32;

    let process_id = ProcessId::new(pid, start_time_ns);
    Some((process_id, exit_code))
}

/// Extract a ProcessId from any event type that carries pid/start_time_ns.
fn extract_process_id(event: &Event) -> Option<ProcessId> {
    let pid = event
        .data
        .get("pid")
        .and_then(|v| v.as_u64())
        .unwrap_or(event.pid as u64) as u32;
    let start_time_ns = event
        .data
        .get("start_time_ns")
        .and_then(|v| v.as_u64())
        .unwrap_or(0);
    Some(ProcessId::new(pid, start_time_ns))
}

/// Extract a ProcessId from the event. Falls back to event.pid with start_time_ns=0.
/// Callers should resolve this against the SignalSnapshot to get the real start_time_ns.
fn extract_or_default_process_id(event: &Event) -> ProcessId {
    extract_process_id(event).unwrap_or_else(|| ProcessId::new(event.pid, 0))
}

/// Resolve a ProcessId against the SignalSnapshot to correct start_time_ns.
/// Events from HTTP layers have start_time_ns=0. The SignalGraph has the real
/// start_time from the EXEC event. Without this correction, process tree
/// correlation (can_correlate) always fails.
pub fn resolve_process_id(
    pid: ProcessId,
    snapshot: &crate::responsibility::signals::SignalSnapshot,
) -> ProcessId {
    if pid.start_time_ns != 0 {
        return pid;
    }
    // Find the real start_time_ns from the snapshot. Intent events from HTTP
    // layer have start_time_ns=0. The snapshot's process HashMap is keyed by
    // ProcessId(pid, real_start) — we need to find by PID alone.
    for proc_id in snapshot.processes.keys() {
        if proc_id.pid == pid.pid {
            return *proc_id;
        }
    }
    pid
}

/// Where the intents in this body came from.
///
/// A single constant here used to stamp every evidence-routed body as
/// `SseResponseToolUse` — including non-streamed HTTP responses and raw
/// request plaintext — so the recorded provenance did not describe the data.
/// The event is the evidence: its source, its `message_type`, or (for raw SSL
/// plaintext) the HTTP start line decides.
///
/// Request JSON with a `messages` array is re-labelled inside
/// `IntentExtractor::extract_from_request` as `RequestHistoryAssistantToolUse`;
/// this function's job is the response/stream distinction and the raw-bytes
/// fallback. Unknown framing defaults to a non-streamed HTTP body, never to
/// the streamed-response label.
fn determine_authority_source(event: &Event, body: &str) -> IntentAuthoritySource {
    let trimmed = body.trim_start();
    if event.source == "sse_processor"
        || event.data.get("sse_events").is_some()
        || trimmed.starts_with("event:")
        || trimmed.starts_with("data:")
    {
        return IntentAuthoritySource::SseResponseToolUse;
    }

    match event.data.get("message_type").and_then(Value::as_str) {
        Some("request") => IntentAuthoritySource::RequestHistoryAssistantToolUse,
        Some("response") => IntentAuthoritySource::HttpResponseToolUse,
        _ => {
            if trimmed.starts_with("HTTP/") {
                IntentAuthoritySource::HttpResponseToolUse
            } else if trimmed
                .split_whitespace()
                .nth(2)
                .is_some_and(|version| version.starts_with("HTTP/"))
            {
                // `POST /v1/messages HTTP/1.1`
                IntentAuthoritySource::RequestHistoryAssistantToolUse
            } else {
                IntentAuthoritySource::HttpResponseToolUse
            }
        }
    }
}

/// Map a process BPF event to a canonical syscall name + arguments.
///
/// Process BPF events use semantic event types ("FILE_OPEN", "FILE_DELETE",
/// "NETWORK_CONNECT") instead of raw syscall names. This function maps them
/// to the syscall names that ActionClassifier expects.
///
/// Also handles direct syscall events (with "syscall" + "args" keys)
/// for backward compatibility.
fn parse_process_event_to_syscall(event: &Event) -> Option<(String, Vec<String>)> {
    // Direct syscall event (has "syscall" key)
    if let Some(name) = event.data.get("syscall").and_then(|v| v.as_str()) {
        let args: Vec<String> = event
            .data
            .get("args")
            .and_then(|v| v.as_array())
            .map(|arr| {
                arr.iter()
                    .filter_map(|a| a.as_str().map(String::from))
                    .collect()
            })
            .unwrap_or_default();
        return Some((name.to_string(), args));
    }

    // Process BPF event types → syscall mapping
    match event.data.get("event").and_then(|v| v.as_str()) {
        // File operations
        Some("FILE_OPEN") => {
            let path = event
                .data
                .get("filepath")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let flags = event
                .data
                .get("flags")
                .and_then(|v| v.as_i64())
                .map(|f| f.to_string())
                .unwrap_or_else(|| "0".to_string());
            Some(("openat".to_string(), vec![path.to_string(), flags]))
        }
        Some("FILE_DELETE") => {
            let path = event
                .data
                .get("filepath")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            Some(("unlink".to_string(), vec![path.to_string()]))
        }
        Some("FILE_WRITE") | Some("FILE_TRUNCATE") => {
            let path = event
                .data
                .get("filepath")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            Some(("write".to_string(), vec![path.to_string()]))
        }
        Some("FILE_RENAME") => {
            // A rename has two resources; passing both keeps the destination
            // in the evidence even though the action layer has no rename type.
            let old = event
                .data
                .get("filepath")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let new = event
                .data
                .get("filepath2")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            Some(("rename".to_string(), vec![old.to_string(), new.to_string()]))
        }

        // Network operations. The producer emits
        //   "net_op": {"family": "AF_INET", "host": "…", "port": 443}
        // and, for an address family it has not decoded,
        //   "net_op": {"family": "AF_INET6", "unsupported_family": true}
        // with no host/port. The second shape must not become a connection to
        // ":0" — decline to classify it and leave the raw event as evidence.
        Some(network_event @ ("NETWORK_CONNECT" | "NETWORK_SEND")) => {
            let net = event.data.get("net_op")?;
            if net
                .get("unsupported_family")
                .and_then(Value::as_bool)
                .unwrap_or(false)
            {
                return None;
            }
            let host = net.get("host").and_then(Value::as_str)?;
            let port = net.get("port").and_then(Value::as_u64)?;
            if host.is_empty() {
                return None;
            }
            let syscall = if network_event == "NETWORK_CONNECT" {
                "connect"
            } else {
                "sendto"
            };
            Some((syscall.to_string(), vec![format!("{host}:{port}")]))
        }

        // Process operations
        Some("EXEC") | Some("PROCESS_EXEC") => {
            let filename = event
                .data
                .get("filename")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let comm = event
                .data
                .get("comm")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let full_cmd = event
                .data
                .get("full_command")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let argv0 = if !filename.is_empty() { filename } else { comm };
            Some((
                "execve".to_string(),
                vec![argv0.to_string(), full_cmd.to_string()],
            ))
        }

        _ => None,
    }
}

/// Build a human-readable description for a signal event (for timeline entries).
fn describe_signal_event(event: &Event, event_type: Option<&str>) -> String {
    let comm = event
        .data
        .get("comm")
        .and_then(|v| v.as_str())
        .unwrap_or(&event.comm);

    match event_type {
        Some("EXEC") => format!("process exec: {comm} (pid={})", event.pid),
        Some("EXIT") => {
            let code = event
                .data
                .get("exit_code")
                .and_then(|v| v.as_i64())
                .unwrap_or(0);
            format!("process exit: {comm} (pid={}, code={code})", event.pid)
        }
        _ => {
            if event.data.get("connection_id").is_some() {
                format!("tls session: {comm} (pid={})", event.pid)
            } else {
                format!("signal: {} (pid={})", event.source, event.pid)
            }
        }
    }
}

/// Extract UserPrompt intents from request bodies that contain a
/// `messages` array (the initial POST to the Anthropic API). This
/// enables ContentBoundary detection of prompt injection and other
/// attacks embedded in the initial task description.
fn extract_request_user_messages(
    body: &str,
    process_id: ProcessId,
    message_id: MessageId,
    timestamp_ns: u64,
) -> Vec<Intent> {
    if body.len() > crate::responsibility::evidence::intent_extractor::MAX_JSON_PARSE_BYTES {
        crate::responsibility::evidence::intent_extractor::DIAG_BODY_TOO_LARGE
            .fetch_add(1, Ordering::Relaxed);
        return Vec::new();
    }
    let root: serde_json::Value = match serde_json::from_str(body) {
        Ok(v) => v,
        Err(_) => return Vec::new(),
    };
    let messages = root
        .get("messages")
        .and_then(|m| m.as_array())
        .cloned()
        .unwrap_or_else(|| root.as_array().cloned().unwrap_or_default());
    if messages.is_empty() {
        return Vec::new();
    }
    let messages_str = serde_json::to_string(&messages).unwrap_or_default();
    IntentExtractor::extract_from_request(
        &messages_str,
        process_id,
        message_id.clone(),
        timestamp_ns,
    )
}

/// Build an intent from an out-of-band tool-call event.
///
/// The agent's argument names differ from Claude Code's (`path` vs
/// `file_path`, `workdir`); map them onto the shared extractor so one
/// normalisation path serves every agent.
fn outofband_intent(event: &Event, process_id: ProcessId) -> Option<Intent> {
    let tool_name = event.data.get("tool_name")?.as_str()?.to_string();
    let raw_args = event.data.get("args").cloned().unwrap_or(Value::Null);
    let input = map_openclaw_args(&raw_args);
    let message_id = event
        .data
        .get("agent_key")
        .and_then(Value::as_str)
        .map(MessageId::from)
        .unwrap_or_else(MessageId::new);
    Some(Intent {
        intent_id: crate::responsibility::types::ids::IntentId::new(),
        process_id,
        message_id,
        tool_args: crate::responsibility::evidence::args::extract_intent_args(&tool_name, &input),
        tool_name: tool_name.clone(),
        intent_class: crate::responsibility::types::tool_vocabulary::classify_intent_class(
            &tool_name,
        ),
        parse_state: crate::responsibility::types::evidence_types::IntentParseState::Complete,
        primary_matching_eligible: true,
        authority_source: IntentAuthoritySource::OutOfBandAsserted,
        timestamp_ns: event.timestamp,
    })
}

/// Map agent-specific argument names onto the shared extractor's names.
fn map_openclaw_args(args: &Value) -> Value {
    let mut input = args.clone();
    if input.get("file_path").is_none() {
        let path = input
            .get("path")
            .and_then(Value::as_str)
            .or_else(|| input.get("workdir").and_then(Value::as_str));
        if let Some(path) = path {
            input["file_path"] = Value::String(path.to_string());
        }
    }
    input
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The BPF producer emits FILE_DELETE / FILE_TRUNCATE; the action layer
    /// classifies the syscall names this mapping produces (`unlink` →
    /// FileDelete, `write` → FileWrite). A change on either side breaks the
    /// chain silently, so the mapping is pinned here.
    #[test]
    fn file_mutation_events_map_to_classifiable_syscalls() {
        let delete = crate::framework::core::Event::new(
            "process".into(),
            42,
            "rm".into(),
            serde_json::json!({"event": "FILE_DELETE", "filepath": "/tmp/secret"}),
        );
        assert_eq!(
            parse_process_event_to_syscall(&delete),
            Some(("unlink".to_string(), vec!["/tmp/secret".to_string()]))
        );

        let truncate = crate::framework::core::Event::new(
            "process".into(),
            42,
            "truncate".into(),
            serde_json::json!({"event": "FILE_TRUNCATE", "filepath": "/var/log/auth.log"}),
        );
        assert_eq!(
            parse_process_event_to_syscall(&truncate),
            Some(("write".to_string(), vec!["/var/log/auth.log".to_string()]))
        );

        let rename = crate::framework::core::Event::new(
            "process".into(),
            42,
            "mv".into(),
            serde_json::json!({
                "event": "FILE_RENAME",
                "filepath": "/tmp/source.txt",
                "filepath2": "/tmp/destination.txt",
            }),
        );
        let (syscall, args) =
            parse_process_event_to_syscall(&rename).expect("FILE_RENAME must map");
        assert_eq!(syscall, "rename");
        assert_eq!(
            args,
            vec![
                "/tmp/source.txt".to_string(),
                "/tmp/destination.txt".to_string()
            ]
        );
        let action = ActionClassifier::classify(&syscall, &args, ProcessId::new(42, 1), 1000)
            .expect("rename is actionable");
        assert_eq!(
            action.action_type,
            crate::responsibility::types::ActionCandidateType::FileRename
        );
        assert_eq!(
            action.normalized_args,
            vec![
                crate::responsibility::types::NormalizedArg::Path("/tmp/source.txt".to_string()),
                crate::responsibility::types::NormalizedArg::Path(
                    "/tmp/destination.txt".to_string()
                ),
            ]
        );
    }

    /// Behavioural guard for the no-agent counter: an action from a process
    /// with no agent ancestry must be counted, and an agent's action must be
    /// produced. The source-text gate in `silent_failure_contract.rs` cannot
    /// prove the branch it names is the branch that runs.
    #[test]
    fn dispatch_counts_dropped_and_produced_actions() {
        use crate::responsibility::evidence::correlator::EvidenceCorrelator;
        use crate::responsibility::identity::resolver::IdentityResolver;
        use crate::responsibility::runtime::buffer::GraphDeltaBuffer;
        use crate::responsibility::signals::SignalGraph;

        let make = |comm: &str| {
            crate::framework::core::Event::new(
                "process".into(),
                4242,
                comm.into(),
                serde_json::json!({
                    "event": "FILE_OPEN",
                    "filepath": "/tmp/mtl-routing-test",
                    "flags": 0,
                }),
            )
        };

        let mut graph = SignalGraph::new();
        let mut identity = IdentityResolver::new();
        let mut correlator = EvidenceCorrelator::new();
        let mut buffer = GraphDeltaBuffer::new();

        let dropped_before = DIAG_ACTIONS_DROPPED_NO_AGENT.load(Ordering::Relaxed);
        let _ = dispatch_event(
            &make("curl"),
            &mut graph,
            &mut identity,
            &mut correlator,
            &mut buffer,
        );
        assert!(
            DIAG_ACTIONS_DROPPED_NO_AGENT.load(Ordering::Relaxed) > dropped_before,
            "a non-agent comm must increment the no-agent counter"
        );

        let produced_before = DIAG_ACTIONS_PRODUCED.load(Ordering::Relaxed);
        let _ = dispatch_event(
            &make("claude"),
            &mut graph,
            &mut identity,
            &mut correlator,
            &mut buffer,
        );
        assert!(
            DIAG_ACTIONS_PRODUCED.load(Ordering::Relaxed) > produced_before,
            "an agent comm must produce an action"
        );
    }

    #[test]
    fn outofband_tool_call_becomes_an_asserted_intent() {
        use crate::responsibility::evidence::correlator::EvidenceCorrelator;
        use crate::responsibility::identity::resolver::IdentityResolver;
        use crate::responsibility::runtime::buffer::GraphDeltaBuffer;
        use crate::responsibility::signals::SignalGraph;
        use crate::responsibility::types::NormalizedArg;

        let mut graph = SignalGraph::new();
        let mut identity = IdentityResolver::new();
        let mut correlator = EvidenceCorrelator::new();
        let mut buffer = GraphDeltaBuffer::new();
        let event = crate::framework::core::Event::new(
            "openclaw".into(),
            0,
            "openclaw".into(),
            serde_json::json!({
                "event": "TOOL_CALL",
                "tool_name": "ls",
                "args": {"path": "/tmp"},
                "agent_key": "agent:main:main",
                "run_id": "r1",
            }),
        );

        let _ = dispatch_event(
            &event,
            &mut graph,
            &mut identity,
            &mut correlator,
            &mut buffer,
        );

        assert_eq!(buffer.intents.len(), 1, "one asserted intent");
        let intent = &buffer.intents[0];
        assert_eq!(intent.tool_name, "ls");
        assert_eq!(
            intent.authority_source,
            IntentAuthoritySource::OutOfBandAsserted
        );
        assert!(
            intent
                .tool_args
                .iter()
                .any(|a| matches!(a, NormalizedArg::Path(p) if p == "/tmp")),
            "OpenClaw's `path` must map onto the shared file_path extractor: {:?}",
            intent.tool_args
        );
    }

    #[test]
    fn stdio_is_terminal_for_intent_routing() {
        assert!(is_terminal_runtime_source("stdio"));
        assert!(!is_terminal_runtime_source("ssl"));
        assert!(!is_terminal_runtime_source("sse_processor"));
    }

    /// The network producer emits `net_op: { family, host, port }`. Routing
    /// must read that shape and hand the classifier a `host:port` argument;
    /// the classifier already knows `connect` → NetworkConnect and
    /// `sendto` → NetworkSend. Pinning the whole chain here means a change to
    /// either the wire format or the mapping fails one test.
    #[test]
    fn network_events_map_to_classifiable_syscalls() {
        let connect = crate::framework::core::Event::new(
            "process".into(),
            42,
            "curl".into(),
            serde_json::json!({
                "event": "NETWORK_CONNECT",
                "net_op": {"family": "AF_INET", "host": "93.184.216.34", "port": 443},
            }),
        );
        let (syscall, args) = parse_process_event_to_syscall(&connect).expect("connect maps");
        assert_eq!(syscall, "connect");
        assert_eq!(args, vec!["93.184.216.34:443".to_string()]);
        let action = ActionClassifier::classify(&syscall, &args, ProcessId::new(42, 1), 1000)
            .expect("connect is actionable");
        assert_eq!(
            action.action_type,
            crate::responsibility::types::ActionCandidateType::NetworkConnect
        );

        let send = crate::framework::core::Event::new(
            "process".into(),
            42,
            "dig".into(),
            serde_json::json!({
                "event": "NETWORK_SEND",
                "net_op": {"family": "AF_INET", "host": "8.8.8.8", "port": 53},
            }),
        );
        let (syscall, args) = parse_process_event_to_syscall(&send).expect("sendto maps");
        assert_eq!(syscall, "sendto");
        assert_eq!(args, vec!["8.8.8.8:53".to_string()]);
        let action = ActionClassifier::classify(&syscall, &args, ProcessId::new(42, 1), 1000)
            .expect("sendto is actionable");
        assert_eq!(
            action.action_type,
            crate::responsibility::types::ActionCandidateType::NetworkSend
        );
    }

    /// AF_INET6 is decoded by the producer now; an event that still marks
    /// `unsupported_family` (short address) must not become a connection to
    /// ":0".
    #[test]
    fn unsupported_network_family_is_not_classified_as_an_action() {
        let ipv6 = crate::framework::core::Event::new(
            "process".into(),
            42,
            "curl".into(),
            serde_json::json!({
                "event": "NETWORK_CONNECT",
                "net_op": {"family": "AF_INET6", "unsupported_family": true},
            }),
        );
        assert_eq!(parse_process_event_to_syscall(&ipv6), None);

        let decoded = crate::framework::core::Event::new(
            "process".into(),
            42,
            "curl".into(),
            serde_json::json!({
                "event": "NETWORK_CONNECT",
                "net_op": {"family": "AF_INET6", "host": "::1", "port": 443},
            }),
        );
        let (syscall, args) = parse_process_event_to_syscall(&decoded).expect("decoded IPv6 maps");
        assert_eq!(syscall, "connect");
        assert_eq!(args, vec!["::1:443".to_string()]);
        let action = ActionClassifier::classify(&syscall, &args, ProcessId::new(42, 1), 1000)
            .expect("IPv6 connect is actionable");
        assert_eq!(
            action.action_type,
            crate::responsibility::types::ActionCandidateType::NetworkConnect
        );
    }

    /// Provenance used to be a single constant: every evidence-routed body was
    /// stamped `SseResponseToolUse`, including non-streamed HTTP responses and
    /// raw request plaintext. The recorded authority must describe where the
    /// intents actually came from.
    #[test]
    fn authority_source_follows_the_event_not_a_constant() {
        let event = |source: &str, data: serde_json::Value| {
            crate::framework::core::Event::new(source.into(), 1, "claude".into(), data)
        };

        let sse = event(
            "sse_processor",
            serde_json::json!({"sse_events": [{"event": "content_block_delta"}]}),
        );
        assert_eq!(
            determine_authority_source(&sse, "data: {}"),
            IntentAuthoritySource::SseResponseToolUse
        );

        let response = event(
            "http_parser",
            serde_json::json!({"message_type": "response", "body": "{}"}),
        );
        assert_eq!(
            determine_authority_source(&response, "{}"),
            IntentAuthoritySource::HttpResponseToolUse
        );

        let request = event(
            "http_parser",
            serde_json::json!({"message_type": "request", "body": "{}"}),
        );
        assert_eq!(
            determine_authority_source(&request, "{}"),
            IntentAuthoritySource::RequestHistoryAssistantToolUse
        );

        // Raw SSL plaintext: infer from the HTTP start line.
        let raw_response = event("ssl", serde_json::json!({"data": "HTTP/1.1 200 OK\r\n"}));
        assert_eq!(
            determine_authority_source(&raw_response, "HTTP/1.1 200 OK\r\n\r\n{}"),
            IntentAuthoritySource::HttpResponseToolUse
        );
        let raw_request = event(
            "ssl",
            serde_json::json!({"data": "POST /v1/messages HTTP/1.1"}),
        );
        assert_eq!(
            determine_authority_source(&raw_request, "POST /v1/messages HTTP/1.1\r\n\r\n{}"),
            IntentAuthoritySource::RequestHistoryAssistantToolUse
        );

        // Unknown framing must not claim SSE provenance.
        let unknown = event("ssl", serde_json::json!({"data": "{\"x\":1}"}));
        assert_eq!(
            determine_authority_source(&unknown, "{\"x\":1}"),
            IntentAuthoritySource::HttpResponseToolUse
        );
    }
}
