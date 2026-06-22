// SPDX-License-Identifier: MIT
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

use crate::responsibility::evidence::action_classifier::ActionClassifier;
use crate::responsibility::evidence::correlator::EvidenceCorrelator;
use crate::responsibility::evidence::intent_extractor::IntentExtractor;
use crate::responsibility::identity::resolver::IdentityResolver;
use crate::responsibility::runtime::buffer::GraphDeltaBuffer;
use crate::responsibility::signals::SignalGraph;
use crate::responsibility::types::alert_types::BehaviorTimelineEntry;
use crate::responsibility::types::evidence_types::IntentAuthoritySource;
use crate::responsibility::types::Intent;
use crate::responsibility::types::ids::{MessageId, ProcessId, TlsSessionId};
use crate::framework::Event;
use std::sync::atomic::{AtomicU64, Ordering};

/// Diagnostic counters for EvidenceIntent processing.
pub static DIAG_EVIDENCE_ATTEMPTED: AtomicU64 = AtomicU64::new(0);
pub static DIAG_EVIDENCE_INTENTS: AtomicU64 = AtomicU64::new(0);
pub static DIAG_ACTIONS_PRODUCED: AtomicU64 = AtomicU64::new(0);

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
#[derive(Debug, Clone, Default)]
pub struct DispatchResult {
    /// Output events to forward to downstream analyzers.
    pub output_events: Vec<Event>,
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
    let result = DispatchResult::default();

    // ── Phase 1: Determine event type ──────────────────────────────────
    let event_type = event
        .data
        .get("event")
        .and_then(|v| v.as_str());

    let route = classify_event(event, event_type);

    // ── Route 1: Signals & Identity (always process for process/tls events) ──
    if matches!(route, RouteTarget::Signals | RouteTarget::Identity) {
        match event_type {
            Some("EXEC") => {
                if let Some((child, parent, comm, pgid)) = parse_exec_event(event) {
                    signal_graph.observe_exec(child, parent, &comm, pgid, event.timestamp);
                }
            }
            Some("EXIT") => {
                if let Some((process_id, exit_code)) = parse_exit_event(event) {
                    signal_graph.observe_exit(process_id, exit_code, event.timestamp);
                }
            }
            _ => {
                // TLS events: ssl_*/tls_* sources or events with connection_id
                if event.source.contains("ssl") || event.source.contains("tls")
                   || event.data.get("connection_id").is_some()
                {
                    if let Some(process_id) = extract_process_id(event) {
                        // Resolve the correct ProcessId BEFORE observe_tls.
                        // TLS events carry start_time_ns=0. Without this fix,
                        // the signal graph stores ProcessId(pid,0) in its TLS
                        // map, which never matches ProcessId(pid,real_start)
                        // from the EXEC event. This breaks process-tree queries
                        // and downstream identity resolution (union-find).
                        let snapshot = signal_graph.snapshot();
                        let resolved_pid = resolve_process_id(process_id, &snapshot);
                        let tls_id = event.data.get("connection_id")
                            .or_else(|| event.data.get("tls_session"))
                            .and_then(|v| v.as_str())
                            .map(|s| TlsSessionId::from(s))
                            .unwrap_or_else(TlsSessionId::new);
                        signal_graph.observe_tls(resolved_pid, tls_id, event.timestamp);
                    }
                }
            }
        }

        // After signal update, resolve identity
        let snapshot = signal_graph.snapshot();
        if let Some(process_id) = extract_process_id(event).map(|pid| resolve_process_id(pid, &snapshot)) {
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
    let body_candidate: Option<&str> = event.data.get("body").and_then(|v| v.as_str())
        .or_else(|| event.data.get("content").and_then(|v| v.as_str()))
        .or_else(|| sse_body.as_deref())
        .or_else(|| event.data.get("json_content").and_then(|v| v.as_str()))
        .or_else(|| event.data.get("text_content").and_then(|v| v.as_str()))
        .or_else(|| event.data.get("data").and_then(|v| v.as_str()));

    // Try evidence extraction for:
    // - Events explicitly classified as EvidenceIntent (http_parser, sse_processor)
    // - Raw SSL events with non-empty data payload (captured HTTP traffic)
    let is_evidence_route = matches!(route, RouteTarget::EvidenceIntent);
    let has_payload = body_candidate.is_some_and(|b| !b.is_empty());

    if is_evidence_route || has_payload {
        if let Some(body) = body_candidate {
            DIAG_EVIDENCE_ATTEMPTED.fetch_add(1, Ordering::Relaxed);

            let snapshot = signal_graph.snapshot();
            let process_id = resolve_process_id(
                extract_or_default_process_id(event), &snapshot);

            let message_id = event
                .data
                .get("message_id")
                .and_then(|v| v.as_str())
                .map(|s| MessageId::from(s))
                .unwrap_or_else(MessageId::new);

            let msg_id_for_extract = message_id.clone();
            let intents = IntentExtractor::extract(
                body,
                IntentAuthoritySource::SseResponseToolUse,
                process_id,
                msg_id_for_extract,
                event.timestamp,
            );

            // Also extract user messages from request bodies so the
            // ContentBoundary can detect injection patterns in the
            // initial task prompt.
            let user_prompt_intents = extract_request_user_messages(
                body, process_id, message_id, event.timestamp,
            );

            let total_intents = intents.len() + user_prompt_intents.len();
            if total_intents > 0 {
                DIAG_EVIDENCE_INTENTS.fetch_add(total_intents as u64, Ordering::Relaxed);
            }

            for intent in intents.into_iter().chain(user_prompt_intents) {
                let intent_clone = intent.clone();
                buffer.push_intent(intent_clone);
                let evidence_list = evidence_correlator.on_intent(intent, &snapshot);
                for ev in evidence_list {
                    buffer.push_evidence(ev);
                }
            }
        }
    }

    // ── Route 3: EvidenceAction (syscall + process BPF events) ──
    // Runs for events explicitly classified as EvidenceAction, AND for
    // EXEC/FILE_OPEN/etc. events that classify_event() routes to Signals
    // (early return). This ensures process events produce actions even
    // when they also go through the Signals path.
    let has_actionable_event = event.data.get("event").and_then(|v| v.as_str())
        .map(|e| matches!(e, "EXEC" | "PROCESS_EXEC" | "FILE_OPEN" | "FILE_DELETE"
            | "FILE_WRITE" | "FILE_TRUNCATE" | "FILE_RENAME"
            | "NETWORK_CONNECT" | "NETWORK_SEND"))
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
        let process_id = resolve_process_id(
            extract_or_default_process_id(event), &snapshot);

        if let Some((syscall_name, raw_args)) = parse_process_event_to_syscall(event) {
            if let Some(action) = ActionClassifier::classify(
                &syscall_name,
                &raw_args,
                process_id,
                event.timestamp,
            ) {
                // Filter BPF/eBPF self-access noise: paths under /sys/kernel/
                // are the BPF programs reading trace event IDs, not agent actions.
                let is_bpf_noise = action.normalized_args.iter().any(|a| {
                    if let crate::responsibility::types::NormalizedArg::Path(p) = a {
                        p.starts_with("/sys/kernel/")
                    } else { false }
                });
                // Only keep actions from processes with agent ancestry.
                // A process has agent ancestry if it (or an ancestor) has TLS
                // sessions. This filters out system noise from unrelated
                // processes that happen to be captured by BPF.
                let has_agent = snapshot.has_agent_ancestry(process_id);
                if !is_bpf_noise && has_agent {
                    DIAG_ACTIONS_PRODUCED.fetch_add(1, Ordering::Relaxed);
                    let action_clone = action.clone();
                    buffer.push_action(action_clone);
                }
                if has_agent {
                    let evidence_list = evidence_correlator.on_action(action, &snapshot);
                    for ev in evidence_list {
                        buffer.push_evidence(ev);
                    }
                }
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
            "FILE_OPEN" | "FILE_DELETE" | "FILE_WRITE" | "FILE_TRUNCATE"
            | "FILE_RENAME" | "NETWORK_CONNECT" | "NETWORK_SEND"
            | "EXEC" | "PROCESS_EXEC" => {
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
fn parse_exec_event(event: &Event) -> Option<(ProcessId, ProcessId, String, Option<u32>)> {
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
    let pgid = event
        .data
        .get("pgid")
        .and_then(|v| v.as_u64())
        .map(|v| v as u32);

    let child = ProcessId::new(pid, start_time_ns);
    let parent = ProcessId::new(ppid, parent_start_time_ns);

    Some((child, parent, comm, pgid))
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
pub fn resolve_process_id(pid: ProcessId, snapshot: &crate::responsibility::signals::SignalSnapshot) -> ProcessId {
    if pid.start_time_ns != 0 { return pid; }
    // Find the real start_time_ns from the snapshot. Intent events from HTTP
    // layer have start_time_ns=0. The snapshot's process HashMap is keyed by
    // ProcessId(pid, real_start) — we need to find by PID alone.
    for (proc_id, _meta) in snapshot.processes.iter() {
        if proc_id.pid == pid.pid {
            return *proc_id;
        }
    }
    pid
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
            .map(|arr| arr.iter().filter_map(|a| a.as_str().map(String::from)).collect())
            .unwrap_or_default();
        return Some((name.to_string(), args));
    }

    // Process BPF event types → syscall mapping
    match event.data.get("event").and_then(|v| v.as_str()) {
        // File operations
        Some("FILE_OPEN") => {
            let path = event.data.get("filepath").and_then(|v| v.as_str()).unwrap_or("");
            let flags = event.data.get("flags")
                .and_then(|v| v.as_i64())
                .map(|f| f.to_string())
                .unwrap_or_else(|| "0".to_string());
            Some(("openat".to_string(), vec![path.to_string(), flags]))
        }
        Some("FILE_DELETE") => {
            let path = event.data.get("filepath").and_then(|v| v.as_str()).unwrap_or("");
            Some(("unlink".to_string(), vec![path.to_string()]))
        }
        Some("FILE_WRITE") | Some("FILE_TRUNCATE") | Some("FILE_RENAME") => {
            let path = event.data.get("filepath").and_then(|v| v.as_str()).unwrap_or("");
            Some(("write".to_string(), vec![path.to_string()]))
        }

        // Network operations
        Some("NETWORK_CONNECT") => {
            let host = event.data.get("host").and_then(|v| v.as_str()).unwrap_or("");
            let port = event.data.get("port").and_then(|v| v.as_u64()).unwrap_or(0);
            Some(("connect".to_string(), vec![format!("{}:{}", host, port)]))
        }
        Some("NETWORK_SEND") => {
            let host = event.data.get("host").and_then(|v| v.as_str()).unwrap_or("");
            let port = event.data.get("port").and_then(|v| v.as_u64()).unwrap_or(0);
            Some(("sendto".to_string(), vec![format!("{}:{}", host, port)]))
        }

        // Process operations
        Some("EXEC") | Some("PROCESS_EXEC") => {
            let filename = event.data.get("filename").and_then(|v| v.as_str()).unwrap_or("");
            let comm = event.data.get("comm").and_then(|v| v.as_str()).unwrap_or("");
            let full_cmd = event.data.get("full_command").and_then(|v| v.as_str()).unwrap_or("");
            let argv0 = if !filename.is_empty() { filename } else { comm };
            Some(("execve".to_string(), vec![argv0.to_string(), full_cmd.to_string()]))
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
    if body.len() > 2_000_000 {
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
    IntentExtractor::extract_from_request(&messages_str, process_id, message_id.clone(), timestamp_ns)
}
