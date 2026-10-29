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
/// EXEC events whose parent process had no resolved agent identity. The
/// spawn is not recorded as a delegation, so the taint authorization gate
/// will see the pair as unauthorized; this counter keeps that "evidence was
/// never captured" case distinguishable from "no delegation exists".
pub static DIAG_DELEGATION_PARENT_UNRESOLVED: AtomicU64 = AtomicU64::new(0);
/// Intents dropped because an identical (process, tool, args) intent was
/// already accepted. Visible so a multi-agent run can distinguish "the second
/// agent did nothing" from "its intent was deduplicated away".
pub static DIAG_INTENTS_DEDUPED: AtomicU64 = AtomicU64::new(0);
/// Structured tool calls read from the agent's own on-disk trajectory.
pub static DIAG_OPENCLAW_TOOL_CALLS: AtomicU64 = AtomicU64::new(0);
/// Classified raw Actions produced into the buffer, before identity gating.
/// A missing agent does not reduce this count.
pub static DIAG_ACTIONS_PRODUCED: AtomicU64 = AtomicU64::new(0);
/// Legacy counter, name retained for consumers. It counts Actions whose
/// streaming correlation was skipped because the process had no agent
/// ancestry. The raw Action itself is still retained in the buffer,
/// persistence and checkpoint; this does not mean it was dropped. A non-zero
/// value on an agent run means the streaming subject is unresolved at that
/// moment, not that the action was lost.
pub static DIAG_ACTIONS_DROPPED_NO_AGENT: AtomicU64 = AtomicU64::new(0);
/// Legacy diagnostic retained for consumers. Path-only noise suppression was
/// removed because a resource prefix does not identify the collector process.
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
///   1. an explicit `connection_id` / `tls_session` field, used verbatim
///      because its producer already defines its scope,
///   2. the `ssl_ptr` connection pointer emitted by `tls_capture`, namespaced
///      by the observing process. `SSL*` is process-local, so a bare pointer
///      would merge unrelated connections from different processes; a known
///      process start time also separates PID reuse,
///   3. a fresh random id, which matches nothing and is a last resort only.
///
/// Shared by every consumer so the two call sites cannot drift apart again.
/// This is a correlation key, not an authenticated identity.
///
/// Limitations: `tls_capture` emits neither the process start time nor any
/// `SSL*` generation/close event, so PID reuse without a start time and a
/// reallocated `SSL*` address cannot be distinguished here. Missing data is
/// treated as unknown, never as a known generation.
pub fn resolve_tls_session_id(event: &Event) -> TlsSessionId {
    explicit_tls_session_id(event).unwrap_or_else(TlsSessionId::new)
}

/// The TLS key this event itself names, if any.
///
/// Unlike [`resolve_tls_session_id`], this never invents an id: callers that
/// associate a message with a connection must leave it unassigned rather than
/// borrow an unrelated session. Prefer `captured_tls` from transformed
/// HTTP/SSE events; a display-stream `connection_id` is a last-resort lookup
/// that the caller may use only against an already observed owner.
pub fn explicit_tls_session_id(event: &Event) -> Option<TlsSessionId> {
    let data = event
        .data
        .get("captured_tls")
        .filter(|value| value.is_object())
        .unwrap_or(&event.data);
    tls_session_from_data(data, event.pid)
}

/// A key that may seed a TLS owner: only raw TLS facts, never a derived
/// HTTP/SSE display stream id.
fn captured_tls_session_id(event: &Event) -> Option<TlsSessionId> {
    if let Some(captured) = event
        .data
        .get("captured_tls")
        .filter(|value| value.is_object())
    {
        return tls_session_from_data(captured, event.pid);
    }
    if is_derived_stream_source(event) {
        return None;
    }
    tls_session_from_data(&event.data, event.pid)
}

fn is_derived_stream_source(event: &Event) -> bool {
    event.source == "sse_processor"
        || event.source == "http_parser"
        || event.source.contains("http")
}

fn tls_session_from_data(data: &Value, fallback_pid: u32) -> Option<TlsSessionId> {
    data.get("connection_id")
        .or_else(|| data.get("tls_session"))
        .and_then(Value::as_str)
        .map(TlsSessionId::from)
        .or_else(|| {
            let ssl_ptr = data
                .get("ssl_ptr")
                .and_then(Value::as_u64)
                .filter(|p| *p != 0)?;
            let (pid, start_time_ns) = tls_process_instance(data, fallback_pid)?;
            let id = if start_time_ns == 0 {
                format!("ssl-{pid}-{ssl_ptr}")
            } else {
                format!("ssl-{pid}-{start_time_ns}-{ssl_ptr}")
            };
            Some(TlsSessionId::from(id))
        })
}

/// Process-instance witness for a process-local pointer.
///
/// Falls back to the top-level `Event.pid` when the producer omitted
/// `data.pid`. `start_time_ns == 0` means the producer did not observe a start
/// time, so it is omitted from the id rather than pretending to be a known
/// generation.
fn tls_process_instance(data: &Value, fallback_pid: u32) -> Option<(u32, u64)> {
    let pid = data
        .get("pid")
        .and_then(Value::as_u64)
        .and_then(|pid| u32::try_from(pid).ok())
        .filter(|pid| *pid != 0)
        .unwrap_or(fallback_pid);
    if pid == 0 {
        return None;
    }
    let start_time_ns = data
        .get("start_time_ns")
        .and_then(Value::as_u64)
        .filter(|start| *start != 0)
        .unwrap_or(0);
    Some((pid, start_time_ns))
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
            Some("PROCESS_SNAPSHOT") if event.source == "process" => {
                if let Some((child, parent, mut info, pgid)) = parse_exec_event(event)
                    && child.pid > 0
                    && child.start_time_ns > 0
                {
                    info.cmdline = None;
                    info.exe_path = None;
                    let parent = if parent.start_time_ns > 0 {
                        parent
                    } else {
                        ProcessId::new(0, 0)
                    };
                    signal_graph.observe_snapshot(child, parent, info, pgid, event.timestamp);
                }
            }
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
                // Observe a TLS owner only from raw producer facts. A
                // transformed display stream id must not seed one.
                let captured_tls = captured_tls_session_id(event);
                let derived = is_derived_stream_source(event);
                let has_raw_tls_source =
                    event.source.contains("ssl") || event.source.contains("tls");
                let should_observe_tls = if derived {
                    captured_tls.is_some()
                } else {
                    captured_tls.is_some() || has_raw_tls_source
                };
                if should_observe_tls && let Some(process_id) = extract_process_id(event) {
                    // Resolve the correct ProcessId BEFORE observe_tls.
                    // TLS events carry start_time_ns=0. Without this fix,
                    // the signal graph stores ProcessId(pid,0) in its TLS
                    // map, which never matches ProcessId(pid,real_start)
                    // from the EXEC event. This breaks process-tree queries
                    // and downstream identity resolution (union-find).
                    let snapshot = signal_graph.snapshot();
                    let resolved_pid = resolve_process_id(process_id, &snapshot);
                    let tls_id = captured_tls.unwrap_or_else(|| resolve_tls_session_id(event));
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

    if event_type == Some("PROCESS_SNAPSHOT") {
        return result;
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
        let user_prompt_intents = extract_request_user_messages(
            body,
            authority_source,
            process_id,
            message_id.clone(),
            event.timestamp,
        );

        let total_intents = intents.len() + user_prompt_intents.len();
        // The request body may uniquely declare the logical session. Only a
        // request is eligible: a response has no anchor of its own, and role,
        // time, runner and connection are never used to infer one. The same
        // current capture is the only source assigned to these intents.
        // Only an actual captured wire request may assert a conversation
        // anchor. Payload text from stdio/out-of-band/unknown producers is
        // not a network capture and supplies no declaration.
        let declared_conversation_id =
            if captured_llm_transport(event) && is_llm_request(event, body) {
                crate::responsibility::runtime::provenance_tracker::parse_conversation_id(
                    strip_http_headers(body),
                )
            } else {
                None
            };
        // Preserve structured native references before intent dedup. A
        // re-observed request history message must keep its raw reference even
        // when its intent is suppressed as a duplicate.
        if captured_llm_transport(event) {
            for intent in intents.iter().chain(user_prompt_intents.iter()) {
                let Some(native) = intent.native_tool_call.as_ref() else {
                    continue;
                };
                if !matches!(
                    intent.authority_source,
                    IntentAuthoritySource::RequestHistoryAssistantToolUse
                        | IntentAuthoritySource::SseResponseToolUse
                        | IntentAuthoritySource::HttpResponseToolUse
                ) {
                    continue;
                }
                signal_graph.observe_native_tool_call(
                    process_id,
                    intent.message_id.clone(),
                    native.clone(),
                    declared_conversation_id.clone(),
                );
            }
        }
        if total_intents > 0 {
            // Record the message exactly once, using the process instance this
            // event was captured for. The connection key, when present, must
            // come from this event: picking any TLS session the process owns
            // would attach the message to an unrelated conversation. A key
            // not bound to this process is not TLS evidence: a transformed
            // event may only carry a display-stream id. Preserve its direct
            // process association without inventing a TLS owner.
            let tls_session_id = explicit_tls_session_id(event).filter(|tls_sid| {
                snapshot
                    .process_for_tls(tls_sid)
                    .is_some_and(|owner| owner == process_id)
            });
            signal_graph.observe_message(process_id, tls_session_id.clone(), message_id.clone());
            // The request body may uniquely declare the logical session. Only
            // a request is eligible: a response has no anchor of its own, and
            // role, time, runner and connection are never used to infer one.
            if let Some(conversation_id) = declared_conversation_id.as_deref() {
                signal_graph.observe_message_conversation(
                    process_id,
                    message_id.clone(),
                    conversation_id,
                );
            }
            result.message_bindings.push(PendingMessageBinding {
                message_id: message_id.clone(),
                pid: process_id.pid,
                start_time_ns: process_id.start_time_ns,
                tls_session_id,
                timestamp_ns: event.timestamp,
            });
        }

        for mut intent in intents.into_iter().chain(user_prompt_intents) {
            // The request body carries the full conversation history, so the
            // same tool call reappears in every later turn. Accept each
            // distinct intent once; otherwise evidence, claims and alerts are
            // all multiplied by the number of subsequent requests.
            intent.declared_conversation_id = declared_conversation_id.clone();
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
            && let Some(mut action) =
                ActionClassifier::classify(&syscall_name, &raw_args, process_id, event.timestamp)
        {
            action.syscall_ret = event.data.get("syscall_ret").and_then(Value::as_i64);
            action.outcome = match action.syscall_ret {
                Some(result) if result >= 0 => {
                    crate::responsibility::types::ActionOutcome::Succeeded
                }
                Some(-115 | -114) if syscall_name == "connect" => {
                    crate::responsibility::types::ActionOutcome::Pending
                }
                Some(_) => crate::responsibility::types::ActionOutcome::Failed,
                None => match event.data.get("outcome").and_then(Value::as_str) {
                    Some("succeeded") => crate::responsibility::types::ActionOutcome::Succeeded,
                    Some("failed") => crate::responsibility::types::ActionOutcome::Failed,
                    Some("pending") => crate::responsibility::types::ActionOutcome::Pending,
                    Some("attempted") => crate::responsibility::types::ActionOutcome::Attempted,
                    _ => crate::responsibility::types::ActionOutcome::Unknown,
                },
            };
            // Classified raw actions are retained before identity gating.
            // `has_agent` only decides whether streaming correlation may run:
            // an unknown process still reaches the buffer, persistence and the
            // final checkpoint, where a later frozen snapshot can resolve it.
            // This is not a guarantee that every raw syscall was captured.
            let has_agent = snapshot.has_agent_ancestry(process_id)
                || crate::responsibility::identity::union_find::is_agent_comm(&event.comm);
            DIAG_ACTIONS_PRODUCED.fetch_add(1, Ordering::Relaxed);
            buffer.push_action(action.clone());
            if has_agent {
                let evidence_list = evidence_correlator.on_action(action, &snapshot);
                for ev in evidence_list {
                    buffer.push_evidence(ev);
                }
            } else {
                // Legacy counter, name retained for consumers: the raw action
                // was kept; only streaming correlation was skipped because no
                // agent subject exists yet.
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
        Some("PROCESS_SNAPSHOT") if event.source == "process" => return RouteTarget::Signals,
        _ => {}
    }

    // 2. Transformed HTTP/SSE events carry a display stream id. Only an
    // explicit captured_tls witness may route them as TLS signal events.
    if is_derived_stream_source(event) {
        return if captured_tls_session_id(event).is_some() {
            RouteTarget::Signals
        } else {
            RouteTarget::EvidenceIntent
        };
    }

    // 3. Raw TLS events: connection_id or source indicates TLS
    if event.data.get("connection_id").is_some()
        || event.source.contains("ssl")
        || event.source.contains("tls")
    {
        return RouteTarget::Signals;
    }

    // 4. Other body-bearing events still reach evidence extraction.
    if event.data.get("body").is_some() || event.data.get("tool_use").is_some() {
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
    // Kernel PGID shares the event's initial/kernel PID space. A post-event
    // procfs lookup can address another namespace or a recycled process.
    let pgid = (event.source == "process")
        .then(|| event.data.get("pgid").and_then(|v| v.as_u64()))
        .flatten()
        .and_then(|value| u32::try_from(value).ok())
        .filter(|value| *value > 0);

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

    // A named unit needs an independently verified cgroup-object resolver.
    // Keep it unknown instead of reading a numeric kernel PID through procfs.
    let cgroup_unit = None;

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

/// The raw body of an event that might carry an LLM request.
///
/// Mirrors the payload priority used by `dispatch_event`; kept separate so the
/// runtime can feed the provenance tracker before intent extraction.
pub fn llm_request_body(event: &Event) -> Option<&str> {
    event
        .data
        .get("body")
        .and_then(|v| v.as_str())
        .or_else(|| event.data.get("content").and_then(|v| v.as_str()))
        .or_else(|| event.data.get("data").and_then(|v| v.as_str()))
}

/// Whether this event is an LLM request (as opposed to a response).
pub fn is_llm_request(event: &Event, body: &str) -> bool {
    if event
        .data
        .get("message_type")
        .and_then(|v| v.as_str())
        .is_some_and(|kind| kind == "request")
    {
        return true;
    }
    let first = body.trim_start().lines().next().unwrap_or("");
    first.starts_with("POST ") || first.starts_with("PUT ")
}

/// The body of an actual LLM request captured by a canonical TLS converter.
///
/// Capture provenance is a property of the producer, not of payload text: only
/// `ssl`, `http_parser`, and `sse_processor` events (the converters in
/// `framework/analyzers/event.rs`) can supply this fact. Claims inside `data`
/// such as `original_source` are ignored. `llm_request_body` remains the
/// generic payload accessor for callers that do not assert capture.
pub fn captured_llm_transport(event: &Event) -> bool {
    matches!(
        event.source.as_str(),
        "ssl" | "http_parser" | "sse_processor"
    )
}

pub fn captured_llm_request_body(event: &Event) -> Option<&str> {
    if !captured_llm_transport(event) {
        return None;
    }
    let body = llm_request_body(event)?;
    is_llm_request(event, body).then_some(body)
}

#[cfg(test)]
mod captured_llm_request_body_tests {
    use super::*;

    fn event(source: &str, message_type: &str, body: &str) -> Event {
        Event::new_with_timestamp(
            1,
            source.into(),
            42,
            "mtl".into(),
            serde_json::json!({
                "message_type": message_type,
                "body": body,
                "original_source": "ssl",
            }),
        )
    }

    #[test]
    fn canonical_tls_producers_supply_a_request_body() {
        let body = r#"{"messages":[{"role":"tool","content":"x"}]}"#;
        for source in ["ssl", "http_parser", "sse_processor"] {
            let event = event(source, "request", body);
            assert_eq!(captured_llm_request_body(&event), Some(body), "{source}");
        }
    }

    #[test]
    fn non_tls_or_response_events_supply_nothing() {
        let body = r#"{"messages":[{"role":"tool","content":"x"}]}"#;
        for source in ["stdio", "outofband", "test"] {
            let event = event(source, "request", body);
            assert_eq!(captured_llm_request_body(&event), None, "{source}");
        }
        let response = event("ssl", "response", body);
        assert_eq!(captured_llm_request_body(&response), None);
    }
}

/// Drop HTTP request headers so the JSON body can be parsed.
pub fn strip_http_headers(body: &str) -> &str {
    let trimmed = body.trim_start();
    if trimmed.starts_with('{') || trimmed.starts_with('[') {
        return trimmed;
    }
    if let Some(idx) = trimmed.find("\r\n\r\n") {
        return trimmed[idx + 4..].trim_start();
    }
    if let Some(idx) = trimmed.find("\n\n") {
        return trimmed[idx + 2..].trim_start();
    }
    trimmed
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
        Some("PROCESS_SNAPSHOT") => format!("process snapshot: {comm} (pid={})", event.pid),
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
///
/// The `messages` array is body shape, not capture direction. Only a body the
/// wire framing proved to be a request may emit `UserPrompt`; a response or an
/// unframed payload that echoes messages keeps its own authority.
fn extract_request_user_messages(
    body: &str,
    authority_source: IntentAuthoritySource,
    process_id: ProcessId,
    message_id: MessageId,
    timestamp_ns: u64,
) -> Vec<Intent> {
    if authority_source != IntentAuthoritySource::RequestHistoryAssistantToolUse {
        return Vec::new();
    }
    // Raw SSL plaintext still carries the start line and headers; strip them
    // with the same helper the provenance path uses before parsing JSON.
    let body = strip_http_headers(body);
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
        native_tool_call: None,
        declared_conversation_id: None,
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

    #[test]
    fn kernel_snapshot_discovers_process_without_fabricating_execution_or_actions() {
        let event = Event::new(
            "process".into(),
            42,
            "resident".into(),
            serde_json::json!({
                "event":"PROCESS_SNAPSHOT","pid":42,"ppid":1,"start_time_ns":123,
                "parent_start_time_ns":0,"pgid":7,"comm":"resident"
            }),
        );
        assert!(matches!(
            classify_event(&event, Some("PROCESS_SNAPSHOT")),
            RouteTarget::Signals
        ));
        let mut graph = SignalGraph::new();
        let mut identity = IdentityResolver::new();
        let mut correlator = EvidenceCorrelator::new();
        let mut buffer = GraphDeltaBuffer::new();
        dispatch_event(
            &event,
            &mut graph,
            &mut identity,
            &mut correlator,
            &mut buffer,
        );
        let snapshot = graph.snapshot();
        assert!(snapshot.get_process(42, 123).is_some());
        assert!(snapshot.get_process(1, 0).is_none());
        assert!(buffer.intents.is_empty());
        assert!(buffer.actions.is_empty());
        assert!(buffer.timeline.iter().all(|item| item.event_type != "EXEC"));
    }

    #[test]
    fn exec_uses_kernel_pgid_and_never_reads_procfs_by_kernel_pid() {
        let own = std::fs::read_to_string("/proc/self/stat").unwrap();
        let actual_pgid: u32 = own[own.rfind(')').unwrap() + 1..]
            .split_whitespace()
            .nth(2)
            .unwrap()
            .parse()
            .unwrap();
        let expected = actual_pgid + 1;
        let event = Event::new(
            "process".into(),
            std::process::id(),
            "client".into(),
            serde_json::json!({
                "event":"EXEC", "pid":std::process::id(), "start_time_ns":123,
                "pgid":expected, "cgroup_id":9876543210u64,
                "cgroup_unit":"unverified.service"
            }),
        );
        let (child, _, info, pgid) = parse_exec_event(&event).unwrap();
        assert_eq!(child.pid, std::process::id());
        assert_eq!(pgid, Some(expected));
        assert!(info.cgroup_unit.is_none());
        let mut legacy = event.clone();
        legacy.data.as_object_mut().unwrap().remove("pgid");
        assert_eq!(parse_exec_event(&legacy).unwrap().3, None);
    }

    #[test]
    fn tracked_agent_kernel_paths_reach_the_real_action_buffer() {
        use crate::responsibility::types::{ActionOutcome, NormalizedArg};
        let mut graph = SignalGraph::new();
        let mut identity = IdentityResolver::new();
        let mut correlator = EvidenceCorrelator::new();
        let mut buffer = GraphDeltaBuffer::new();
        for (event_name, path) in [
            ("FILE_OPEN", "/sys/kernel/security/lsm"),
            ("FILE_OPEN", "/sys/kernel/btf/vmlinux"),
            ("FILE_TRUNCATE", "/sys/kernel/debug/tracing/tracing_on"),
        ] {
            let event = Event::new(
                "process".into(),
                4242,
                "claude".into(),
                serde_json::json!({
                    "event": event_name, "filepath": path, "flags": 0,
                    "start_time_ns": 1, "outcome": "succeeded",
                    "syscall_ret": if event_name == "FILE_OPEN" { 3 } else { 0 },
                }),
            );
            dispatch_event(
                &event,
                &mut graph,
                &mut identity,
                &mut correlator,
                &mut buffer,
            );
        }
        assert_eq!(
            buffer.actions.len(),
            3,
            "a resource prefix cannot establish helper ownership"
        );
        for action in &buffer.actions {
            assert_eq!(action.outcome, ActionOutcome::Succeeded);
            assert!(action.normalized_args.iter().any(|arg| matches!(arg,
                NormalizedArg::Path(path) if path.starts_with("/sys/kernel/"))));
        }
    }

    #[test]
    fn already_pending_connect_is_not_reclassified_as_a_completed_failure() {
        let mut graph = SignalGraph::new();
        let mut identity = IdentityResolver::new();
        let mut correlator = EvidenceCorrelator::new();
        let mut buffer = GraphDeltaBuffer::new();
        let event = Event::new(
            "process".into(),
            4242,
            "claude".into(),
            serde_json::json!({
                "event": "NETWORK_CONNECT", "start_time_ns": 1,
                "net_op": {"host": "127.0.0.1", "port": 7395},
                "outcome": "pending", "syscall_ret": -114,
            }),
        );
        dispatch_event(
            &event,
            &mut graph,
            &mut identity,
            &mut correlator,
            &mut buffer,
        );
        assert_eq!(buffer.actions.len(), 1);
        assert_eq!(
            buffer.actions[0].outcome,
            crate::responsibility::types::ActionOutcome::Pending
        );
        assert_eq!(buffer.actions[0].syscall_ret, Some(-114));
    }

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

    #[test]
    fn unknown_ancestry_action_is_retained_and_streaming_skipped() {
        use crate::responsibility::evidence::correlator::EvidenceCorrelator;
        use crate::responsibility::identity::resolver::IdentityResolver;
        use crate::responsibility::runtime::buffer::GraphDeltaBuffer;
        use crate::responsibility::signals::SignalGraph;
        use crate::responsibility::signals::signal_snapshot::ProcessExecInfo;

        let mut graph = SignalGraph::new();
        let mut identity = IdentityResolver::new();
        let mut correlator = EvidenceCorrelator::new();
        let mut buffer = GraphDeltaBuffer::new();
        let process = ProcessId::new(4242, 7);
        graph.observe_exec(
            process,
            ProcessId::new(1, 0),
            ProcessExecInfo {
                comm: "curl".into(),
                ..Default::default()
            },
            None,
            1,
        );
        assert!(!graph.snapshot().has_agent_ancestry(process));

        let event = Event::new(
            "process".into(),
            4242,
            "curl".into(),
            serde_json::json!({
                "event": "FILE_OPEN",
                "filepath": "/tmp/raw-before-identity",
                "flags": 0,
                "start_time_ns": 7,
                "outcome": "succeeded",
                "syscall_ret": 3,
            }),
        );
        dispatch_event(
            &event,
            &mut graph,
            &mut identity,
            &mut correlator,
            &mut buffer,
        );

        assert_eq!(
            buffer.actions.len(),
            1,
            "a classified raw action must survive identity gating"
        );
        assert!(
            buffer.evidence.is_empty(),
            "no streaming correlation may run without an agent subject"
        );
    }

    /// Behavioural guard for the action counters. `produced` now counts the
    /// raw action retained before identity gating, while the legacy
    /// `dropped_no_agent` counter means only that streaming correlation was
    /// skipped. The source-text gate in `silent_failure_contract.rs` cannot
    /// prove the branch it names is the branch that runs.
    #[test]
    fn dispatch_counts_retained_and_streaming_skipped_actions() {
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
        let produced_before = DIAG_ACTIONS_PRODUCED.load(Ordering::Relaxed);
        let _ = dispatch_event(
            &make("curl"),
            &mut graph,
            &mut identity,
            &mut correlator,
            &mut buffer,
        );
        assert!(
            DIAG_ACTIONS_PRODUCED.load(Ordering::Relaxed) > produced_before,
            "a non-agent raw action must still be produced and retained"
        );
        assert!(
            DIAG_ACTIONS_DROPPED_NO_AGENT.load(Ordering::Relaxed) > dropped_before,
            "the legacy counter records skipped streaming correlation"
        );
        assert_eq!(
            buffer.actions.len(),
            1,
            "the legacy counter must not mean the raw action was dropped"
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
        assert_eq!(buffer.actions.len(), 2);
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

    fn conversation_body() -> String {
        serde_json::json!({
            "messages": [
                {"role": "user", "content": "delete the production database"},
                {"role": "assistant", "content": [
                    {"type": "tool_use", "name": "Bash", "id": "toolu_routing",
                     "input": {"command": "rm -rf /srv/prod"}}
                ]}
            ]
        })
        .to_string()
    }

    fn dispatch_one(event: &crate::framework::core::Event) -> GraphDeltaBuffer {
        let mut graph = SignalGraph::new();
        let mut identity = IdentityResolver::new();
        let mut correlator = EvidenceCorrelator::new();
        let mut buffer = GraphDeltaBuffer::new();
        let _ = dispatch_event(
            event,
            &mut graph,
            &mut identity,
            &mut correlator,
            &mut buffer,
        );
        buffer
    }

    /// The direction comes from the wire framing, not from a `messages` array
    /// in the body. A parsed HTTP response that echoes messages stays a
    /// response: its user text is not the captured task.
    #[test]
    fn http_response_messages_do_not_masquerade_as_a_captured_user_task() {
        let event = Event::new(
            "http_parser".into(),
            42,
            "claude".into(),
            serde_json::json!({
                "message_type": "response",
                "body": conversation_body(),
            }),
        );
        let buffer = dispatch_one(&event);

        assert!(
            buffer
                .intents
                .iter()
                .all(|intent| intent.tool_name != "UserPrompt"),
            "a response body must not be captured as the user task: {:?}",
            buffer
                .intents
                .iter()
                .map(|intent| intent.tool_name.as_str())
                .collect::<Vec<_>>()
        );
        assert!(
            buffer.intents.iter().all(|intent| intent.authority_source
                != IntentAuthoritySource::RequestHistoryAssistantToolUse),
            "a response body must not upgrade authority to the request source"
        );
        let bash = buffer
            .intents
            .iter()
            .find(|intent| intent.tool_name == "Bash")
            .expect("echoed history may remain a text hypothesis");
        assert_eq!(bash.authority_source, IntentAuthoritySource::TextFallback);
        assert!(!bash.primary_matching_eligible);
        assert!(bash.native_tool_call.is_none());
    }

    /// Bare JSON has no wire framing. Its `messages` array is not proof of a
    /// request, so it must not claim request authority either.
    #[test]
    fn unframed_json_messages_do_not_claim_a_request_source() {
        let event = Event::new(
            "ssl".into(),
            42,
            "claude".into(),
            serde_json::json!({"data": conversation_body()}),
        );
        let buffer = dispatch_one(&event);

        assert!(
            buffer
                .intents
                .iter()
                .all(|intent| intent.tool_name != "UserPrompt"),
            "unframed JSON must not masquerade as the user task"
        );
        assert!(
            buffer.intents.iter().all(|intent| intent.authority_source
                != IntentAuthoritySource::RequestHistoryAssistantToolUse),
            "unframed JSON must not upgrade authority to the request source"
        );
    }

    /// A request start line plus headers is still a request: strip the headers
    /// and capture exactly one user task plus the assistant tool call.
    #[test]
    fn raw_http_request_still_captures_one_user_task_and_tool_call() {
        let body = format!(
            "POST /v1/messages HTTP/1.1\r\nHost: api.anthropic.com\r\n\r\n{}",
            conversation_body()
        );
        let event = Event::new(
            "ssl".into(),
            42,
            "claude".into(),
            serde_json::json!({"data": body}),
        );
        let buffer = dispatch_one(&event);

        let prompts: Vec<_> = buffer
            .intents
            .iter()
            .filter(|intent| intent.tool_name == "UserPrompt")
            .collect();
        assert_eq!(prompts.len(), 1, "one captured task, not a duplicate");
        assert_eq!(
            prompts[0].authority_source,
            IntentAuthoritySource::RequestHistoryAssistantToolUse
        );
        assert!(prompts[0].tool_args.iter().any(|arg| matches!(
            arg,
            crate::responsibility::types::NormalizedArg::Content(text)
                if text.contains("delete the production database")
        )));
        assert!(
            buffer
                .intents
                .iter()
                .any(|intent| intent.tool_name == "Bash"
                    && intent.authority_source
                        == IntentAuthoritySource::RequestHistoryAssistantToolUse)
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

    fn tls_event(pid: u32, data: serde_json::Value) -> Event {
        Event::new_with_timestamp(1, "ssl".into(), pid, "curl".into(), data)
    }

    /// `SSL*` is process-local. Two processes can hold the same address, so
    /// the id must include the observing process.
    #[test]
    fn tls_ssl_ptr_is_namespaced_by_pid() {
        let a = tls_event(100, serde_json::json!({"ssl_ptr": 4096, "pid": 100}));
        let b = tls_event(200, serde_json::json!({"ssl_ptr": 4096, "pid": 200}));
        assert_ne!(
            resolve_tls_session_id(&a),
            resolve_tls_session_id(&b),
            "the same SSL* address in different processes is not one session"
        );

        // The Event field is the fallback when the producer omits data.pid.
        let from_event_field = tls_event(100, serde_json::json!({"ssl_ptr": 4096}));
        assert_eq!(
            resolve_tls_session_id(&a),
            resolve_tls_session_id(&from_event_field)
        );
    }

    /// A known process start time separates PID reuse; an unknown one must not
    /// be treated as a known generation.
    #[test]
    fn tls_ssl_ptr_is_namespaced_by_known_process_start_time() {
        let first = tls_event(
            100,
            serde_json::json!({"ssl_ptr": 4096, "pid": 100, "start_time_ns": 111}),
        );
        let reused = tls_event(
            100,
            serde_json::json!({"ssl_ptr": 4096, "pid": 100, "start_time_ns": 222}),
        );
        assert_ne!(
            resolve_tls_session_id(&first),
            resolve_tls_session_id(&reused),
            "the same PID with a different start time is a different process"
        );

        let zero = tls_event(
            100,
            serde_json::json!({"ssl_ptr": 4096, "pid": 100, "start_time_ns": 0}),
        );
        let missing = tls_event(100, serde_json::json!({"ssl_ptr": 4096, "pid": 100}));
        assert_eq!(
            resolve_tls_session_id(&zero),
            resolve_tls_session_id(&missing),
            "zero start time is an unknown generation, not proof of one"
        );
        assert_ne!(
            resolve_tls_session_id(&zero),
            resolve_tls_session_id(&first)
        );
    }

    /// Every chunk of one observed connection must keep the same id, or the
    /// signal graph splits a stream into unrelated sessions.
    #[test]
    fn tls_same_connection_chunks_are_stable() {
        let first = tls_event(
            100,
            serde_json::json!({
                "ssl_ptr": 4096, "pid": 100, "start_time_ns": 111,
                "function": "READ/RECV", "data": "first"
            }),
        );
        let second = tls_event(
            100,
            serde_json::json!({
                "ssl_ptr": 4096, "pid": 100, "start_time_ns": 111,
                "function": "READ/RECV", "data": "second"
            }),
        );
        assert_eq!(
            resolve_tls_session_id(&first),
            resolve_tls_session_id(&second)
        );
    }

    /// Without a process witness there is no safe way to merge pointer-based
    /// observations, so do not fall back to a globally shared `ssl-<ptr>`.
    #[test]
    fn tls_ssl_ptr_without_process_witness_does_not_merge() {
        let event = tls_event(0, serde_json::json!({"ssl_ptr": 4096}));
        assert_ne!(
            resolve_tls_session_id(&event),
            resolve_tls_session_id(&event),
            "an unscoped pointer must fail closed instead of sharing one id"
        );
    }

    /// Explicit producer ids are already scoped by their producer and are used
    /// verbatim so continuation does not depend on local event metadata.
    #[test]
    fn tls_explicit_connection_ids_are_not_renamespaced() {
        let connection = tls_event(
            100,
            serde_json::json!({"connection_id": "42:7:msg_1", "ssl_ptr": 4096}),
        );
        assert_eq!(resolve_tls_session_id(&connection).as_str(), "42:7:msg_1");

        let session = tls_event(
            100,
            serde_json::json!({"tls_session": "producer-session-1", "ssl_ptr": 4096}),
        );
        assert_eq!(
            resolve_tls_session_id(&session).as_str(),
            "producer-session-1"
        );
    }

    fn graph_with_two_sessions() -> (SignalGraph, ProcessId, TlsSessionId, TlsSessionId) {
        let process = ProcessId::new(42, 1000);
        let other = TlsSessionId::from("tls-other");
        let source = TlsSessionId::from("tls-source");
        let mut graph = SignalGraph::new();
        graph.observe_exec(
            process,
            ProcessId::new(1, 1),
            ProcessExecInfo {
                comm: "claude".into(),
                ..Default::default()
            },
            None,
            10,
        );
        graph.observe_tls(process, other.clone(), 11);
        graph.observe_tls(process, source.clone(), 12);
        (graph, process, other, source)
    }

    #[test]
    fn message_binds_to_the_explicit_source_connection_not_a_process_session() {
        let (mut graph, process, other, source) = graph_with_two_sessions();
        let event = Event::new_with_timestamp(
            20,
            "sse_processor".into(),
            42,
            "claude".into(),
            serde_json::json!({
                "connection_id": source.as_str(),
                "message_id": "msg-source",
                "body": conversation_body(),
            }),
        );
        let mut identity = IdentityResolver::new();
        let mut correlator = EvidenceCorrelator::new();
        let mut buffer = GraphDeltaBuffer::new();
        let dispatch = dispatch_event(
            &event,
            &mut graph,
            &mut identity,
            &mut correlator,
            &mut buffer,
        );

        assert_eq!(dispatch.message_bindings.len(), 1);
        assert_eq!(
            dispatch.message_bindings[0].tls_session_id.as_ref(),
            Some(&source),
            "the captured source connection must be used verbatim"
        );
        let snapshot = graph.snapshot();
        let message = MessageId::from("msg-source");
        assert!(
            snapshot
                .tls_to_messages
                .get(&source)
                .is_some_and(|messages| messages.contains(&message)),
            "the message must enter the frozen graph under its source connection"
        );
        assert!(
            !snapshot.tls_to_messages.contains_key(&other),
            "a different TLS session of the same process must stay unassigned"
        );
        assert!(
            snapshot.messages_for_process(process).contains(&message),
            "the authoritative resolver reads messages through the process map"
        );
    }

    fn conversation_body_with_id(conversation_id: &str) -> String {
        serde_json::json!({
            "conversation_id": conversation_id,
            "messages": [
                {"role": "user", "content": "delete the production database"},
                {"role": "assistant", "content": [
                    {"type": "tool_use", "name": "Bash", "id": "toolu_anchor",
                     "input": {"command": "rm -rf /srv/prod"}}
                ]}
            ]
        })
        .to_string()
    }

    #[test]
    fn request_conversation_declaration_anchors_the_observed_message() {
        let (mut graph, process, _other, _source) = graph_with_two_sessions();
        let event = Event::new_with_timestamp(
            20,
            "http_parser".into(),
            42,
            "claude".into(),
            serde_json::json!({
                "message_type": "request",
                "message_id": "msg-request",
                "body": conversation_body_with_id("conv-request"),
            }),
        );
        let mut identity = IdentityResolver::new();
        let mut correlator = EvidenceCorrelator::new();
        let mut buffer = GraphDeltaBuffer::new();
        let dispatch = dispatch_event(
            &event,
            &mut graph,
            &mut identity,
            &mut correlator,
            &mut buffer,
        );
        assert_eq!(dispatch.message_bindings.len(), 1);

        let snapshot = graph.snapshot();
        let rows = snapshot.message_conversations();
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0].process_id, process);
        assert_eq!(rows[0].message_id, MessageId::from("msg-request"));
        assert_eq!(rows[0].conversation_id, "conv-request");
    }

    #[test]
    fn response_conversation_declaration_is_not_an_anchor() {
        let (mut graph, _process, _other, _source) = graph_with_two_sessions();
        let event = Event::new_with_timestamp(
            20,
            "http_parser".into(),
            42,
            "claude".into(),
            serde_json::json!({
                "message_type": "response",
                "message_id": "msg-response",
                "body": conversation_body_with_id("conv-response"),
            }),
        );
        let mut identity = IdentityResolver::new();
        let mut correlator = EvidenceCorrelator::new();
        let mut buffer = GraphDeltaBuffer::new();
        let dispatch = dispatch_event(
            &event,
            &mut graph,
            &mut identity,
            &mut correlator,
            &mut buffer,
        );
        assert_eq!(
            dispatch.message_bindings.len(),
            1,
            "the response message is still observed; only its anchor is unknown"
        );
        assert!(
            graph.snapshot().message_conversations().is_empty(),
            "a response body never supplies the request's conversation anchor"
        );
    }

    #[test]
    fn message_without_a_connection_binds_directly_to_the_process() {
        let (mut graph, process, _other, _source) = graph_with_two_sessions();
        let event = Event::new_with_timestamp(
            20,
            "http_parser".into(),
            42,
            "claude".into(),
            serde_json::json!({
                "message_id": "msg-direct",
                "body": conversation_body(),
            }),
        );
        let mut identity = IdentityResolver::new();
        let mut correlator = EvidenceCorrelator::new();
        let mut buffer = GraphDeltaBuffer::new();
        let dispatch = dispatch_event(
            &event,
            &mut graph,
            &mut identity,
            &mut correlator,
            &mut buffer,
        );

        assert_eq!(dispatch.message_bindings.len(), 1);
        assert!(
            dispatch.message_bindings[0].tls_session_id.is_none(),
            "an event with no connection field must not borrow a process TLS session"
        );
        let snapshot = graph.snapshot();
        assert!(
            snapshot
                .direct_process_messages
                .get(&process)
                .is_some_and(|messages| messages.contains(&MessageId::from("msg-direct")))
        );
        assert!(
            snapshot.tls_to_messages.is_empty(),
            "a connection-less message must not be attached to any TLS session"
        );
    }

    /// SSEProcessor's `connection_id` is not the raw TLS producer's `ssl_ptr`
    /// key (the flattened event drops `ssl_ptr`). A stream key without an
    /// observed TLS binding must remain a direct process association.
    #[test]
    fn explicit_sse_connection_keeps_the_message_without_borrowing_a_tls_session() {
        let (mut graph, process, other, source) = graph_with_two_sessions();
        let connection = TlsSessionId::from("42:7:msg-sse");
        let event = Event::new_with_timestamp(
            20,
            "sse_processor".into(),
            42,
            "claude".into(),
            serde_json::json!({
                "connection_id": connection.as_str(),
                "message_id": "msg-sse",
                "body": conversation_body(),
            }),
        );
        let mut identity = IdentityResolver::new();
        let mut correlator = EvidenceCorrelator::new();
        let mut buffer = GraphDeltaBuffer::new();
        let dispatch = dispatch_event(
            &event,
            &mut graph,
            &mut identity,
            &mut correlator,
            &mut buffer,
        );

        assert_eq!(dispatch.message_bindings[0].tls_session_id, None);
        let snapshot = graph.snapshot();
        let message = MessageId::from("msg-sse");
        assert!(
            snapshot
                .direct_process_messages
                .get(&process)
                .is_some_and(|messages| messages.contains(&message)),
            "the captured process must keep the message even when its connection is unbound"
        );
        assert!(!snapshot.tls_to_messages.contains_key(&connection));
        assert!(
            !snapshot.tls_to_messages.contains_key(&source)
                && !snapshot.tls_to_messages.contains_key(&other),
            "an unbound explicit key must not be rerouted to a process TLS session"
        );
    }

    fn http_projection(raw: &Event) -> Event {
        crate::framework::analyzers::event::HTTPEvent::new(
            7,
            "request".into(),
            "POST /v1/messages HTTP/1.1".into(),
            Some("POST".into()),
            Some("/v1/messages".into()),
            Some("HTTP/1.1".into()),
            None,
            None,
            std::collections::HashMap::new(),
            Some(conversation_body()),
            conversation_body().len(),
            true,
            false,
            None,
            "ssl".into(),
        )
        .to_event(raw)
    }

    fn sse_projection(raw: &Event, stream_id: &str, message_id: &str) -> Event {
        crate::framework::analyzers::event::SSEProcessorEvent::new(
            stream_id.into(),
            Some(message_id.into()),
            20,
            21,
            "ssl".into(),
            "READ/RECV".into(),
            7,
            conversation_body(),
            conversation_body(),
            conversation_body().len(),
            1,
            true,
            vec![],
        )
        .to_event(raw)
    }

    #[test]
    fn projected_http_keeps_the_raw_tls_key_and_message() {
        let (mut graph, process, other, source) = graph_with_two_sessions();
        let raw = tls_event(
            42,
            serde_json::json!({
                "connection_id": source.as_str(),
                "ssl_ptr": 4096,
                "pid": 42,
                "start_time_ns": 1000,
                "data": "POST /v1/messages HTTP/1.1",
            }),
        );
        let event = http_projection(&raw);
        assert!(event.data.get("captured_tls").is_some());
        let mut identity = IdentityResolver::new();
        let mut correlator = EvidenceCorrelator::new();
        let mut buffer = GraphDeltaBuffer::new();
        let dispatch = dispatch_event(
            &event,
            &mut graph,
            &mut identity,
            &mut correlator,
            &mut buffer,
        );

        assert_eq!(
            dispatch.message_bindings[0].tls_session_id.as_ref(),
            Some(&source)
        );
        let message = dispatch.message_bindings[0].message_id.clone();
        let snapshot = graph.snapshot();
        assert!(
            snapshot
                .tls_to_messages
                .get(&source)
                .is_some_and(|messages| messages.contains(&message))
        );
        assert_eq!(snapshot.process_for_tls(&source), Some(process));
        assert!(!snapshot.tls_to_messages.contains_key(&other));
    }

    #[test]
    fn projected_sse_display_stream_does_not_seed_a_phantom_tls_session() {
        let (mut graph, _process, _other, source) = graph_with_two_sessions();
        let raw = tls_event(
            42,
            serde_json::json!({
                "connection_id": source.as_str(),
                "ssl_ptr": 4096,
                "pid": 42,
                "start_time_ns": 1000,
            }),
        );
        let stream = TlsSessionId::from("42:7:display-stream");
        let event = sse_projection(&raw, stream.as_str(), "msg-projected");
        assert_eq!(event.data["connection_id"].as_str(), Some(stream.as_str()));
        let mut identity = IdentityResolver::new();
        let mut correlator = EvidenceCorrelator::new();
        let mut buffer = GraphDeltaBuffer::new();
        let dispatch = dispatch_event(
            &event,
            &mut graph,
            &mut identity,
            &mut correlator,
            &mut buffer,
        );

        assert_eq!(
            dispatch.message_bindings[0].tls_session_id.as_ref(),
            Some(&source),
            "the captured producer key must win over the display stream id"
        );
        let snapshot = graph.snapshot();
        assert!(!snapshot.tls_to_messages.contains_key(&stream));
        assert_eq!(snapshot.process_for_tls(&stream), None);
        assert!(
            snapshot
                .tls_to_messages
                .get(&source)
                .is_some_and(|messages| messages.contains(&MessageId::from("msg-projected")))
        );
    }

    #[test]
    fn projected_sse_without_capture_context_stays_direct() {
        let (mut graph, process, _other, _source) = graph_with_two_sessions();
        let raw = tls_event(42, serde_json::json!({"data": "POST /v1/messages"}));
        let stream = TlsSessionId::from("42:7:missing-context");
        let event = sse_projection(&raw, stream.as_str(), "msg-missing");
        let mut identity = IdentityResolver::new();
        let mut correlator = EvidenceCorrelator::new();
        let mut buffer = GraphDeltaBuffer::new();
        let dispatch = dispatch_event(
            &event,
            &mut graph,
            &mut identity,
            &mut correlator,
            &mut buffer,
        );

        assert_eq!(dispatch.message_bindings[0].tls_session_id, None);
        let snapshot = graph.snapshot();
        assert!(
            snapshot
                .direct_process_messages
                .get(&process)
                .is_some_and(|messages| messages.contains(&MessageId::from("msg-missing")))
        );
        assert!(!snapshot.tls_to_messages.contains_key(&stream));
        assert_eq!(snapshot.process_for_tls(&stream), None);
    }

    fn llm_request_event(conversation_id: &str, body_id: &str) -> Event {
        let body = format!(
            "POST /v1/messages HTTP/1.1\r\nContent-Type: application/json\r\n\r\n\
             {{\"conversation_id\":\"{conversation_id}\",\"messages\":[\
             {{\"role\":\"user\",\"content\":\"identical task\"}}]}}"
        );
        Event::new_with_timestamp(
            1,
            "ssl".into(),
            42,
            "claude".into(),
            serde_json::json!({"data": body, "message_id": body_id, "message_type": "request"}),
        )
    }

    #[test]
    fn same_pid_declared_conversations_survive_routing_with_identical_content() {
        let mut graph = SignalGraph::new();
        let mut identity = IdentityResolver::new();
        let mut correlator = EvidenceCorrelator::new();
        let mut buffer = GraphDeltaBuffer::new();
        for (conversation, message) in [("conv-a", "msg-a"), ("conv-b", "msg-b")] {
            dispatch_event(
                &llm_request_event(conversation, message),
                &mut graph,
                &mut identity,
                &mut correlator,
                &mut buffer,
            );
        }
        assert_eq!(buffer.intents.len(), 2);
        let conversations: Vec<_> = buffer
            .intents
            .iter()
            .map(|intent| intent.declared_conversation_id.as_deref())
            .collect();
        assert_eq!(conversations, vec![Some("conv-a"), Some("conv-b")]);
    }

    #[test]
    fn response_declaration_is_not_assigned_to_extracted_intents() {
        let body = "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n\
                    {\"conversation_id\":\"conv-a\",\"content\":[\
                    {\"type\":\"tool_use\",\"id\":\"toolu_resp\",\"name\":\"Bash\",\
                    \"input\":{\"command\":\"ls\"}}]}";
        let event = Event::new_with_timestamp(
            1,
            "ssl".into(),
            42,
            "claude".into(),
            serde_json::json!({"data": body, "message_type": "response"}),
        );
        let mut graph = SignalGraph::new();
        let mut identity = IdentityResolver::new();
        let mut correlator = EvidenceCorrelator::new();
        let mut buffer = GraphDeltaBuffer::new();
        dispatch_event(
            &event,
            &mut graph,
            &mut identity,
            &mut correlator,
            &mut buffer,
        );
        assert_eq!(buffer.intents.len(), 1);
        assert_eq!(
            buffer.intents[0]
                .native_tool_call
                .as_ref()
                .map(|r| r.id.as_str()),
            Some("toolu_resp")
        );
        assert_eq!(buffer.intents[0].declared_conversation_id, None);
    }

    fn request_history_native_event(message_id: &str, source: &str) -> Event {
        let body = "POST /v1/messages HTTP/1.1\r\nContent-Type: application/json\r\n\r\n\
                    {\"conversation_id\":\"conv-a\",\"messages\":[{\"role\":\"assistant\",\
                    \"content\":[{\"type\":\"tool_use\",\"id\":\"toolu_hist\",\
                    \"name\":\"Bash\",\"input\":{\"command\":\"ls\"}}]}]}";
        Event::new_with_timestamp(
            1,
            source.into(),
            42,
            "claude".into(),
            serde_json::json!({
                "data": body,
                "message_id": message_id,
                "message_type": "request",
            }),
        )
    }

    #[test]
    fn native_reference_is_kept_before_intent_dedup() {
        let mut graph = SignalGraph::new();
        let mut identity = IdentityResolver::new();
        let mut correlator = EvidenceCorrelator::new();
        let mut buffer = GraphDeltaBuffer::new();
        for message in ["msg-1", "msg-2"] {
            dispatch_event(
                &request_history_native_event(message, "ssl"),
                &mut graph,
                &mut identity,
                &mut correlator,
                &mut buffer,
            );
        }
        assert_eq!(
            buffer.intents.len(),
            1,
            "the re-observed history intent is deduplicated"
        );
        let snapshot = graph.snapshot();
        assert_eq!(
            snapshot.native_tool_call_observations().len(),
            2,
            "the raw reference survives even when the intent is suppressed"
        );
        assert!(
            snapshot
                .native_tool_call_observations()
                .iter()
                .all(|row| row.declared_conversation_id.as_deref() == Some("conv-a"))
        );
        let messages: Vec<&str> = snapshot
            .native_tool_call_observations()
            .iter()
            .map(|row| row.message_id.as_str())
            .collect();
        assert_eq!(messages, vec!["msg-1", "msg-2"]);
    }

    #[test]
    fn non_tls_asserted_request_supplies_no_native_reference_or_anchor() {
        let mut graph = SignalGraph::new();
        let mut identity = IdentityResolver::new();
        let mut correlator = EvidenceCorrelator::new();
        let mut buffer = GraphDeltaBuffer::new();
        dispatch_event(
            &request_history_native_event("msg-test", "test"),
            &mut graph,
            &mut identity,
            &mut correlator,
            &mut buffer,
        );
        let snapshot = graph.snapshot();
        assert!(snapshot.native_tool_call_observations().is_empty());
        assert!(snapshot.message_conversations().is_empty());
        assert_eq!(snapshot.conversation_ids().count(), 0);
    }
}
