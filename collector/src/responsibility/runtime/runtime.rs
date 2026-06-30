// SPDX-License-Identifier: MIT
//
// runtime.rs — ResponsibilityRuntime: event loop orchestrator.
//
// Per spec §15, the runtime only wires together subsystems; it contains
// no business logic. It manages the Phase 1 (streaming) event loop and
// triggers Phase 2 (batch) at session end.
//
// Key methods:
//   run()         — main event loop (tokio::select! over event_rx + end_rx)
//   route_event() — stringly-typed dispatch to signals/identity/evidence
//   trigger()     — debounced classification pipeline
//   finalize()    — Phase 2 authoritative batch refinement

use tokio::sync::{broadcast, mpsc, watch};
use tokio::time::{interval, Duration};

use crate::responsibility::claims::{
    AlertBuilder, ClaimBuilder, TracebackBuilder,
};
use crate::responsibility::classification::pattern_matcher::{
    default_templates, GraphPatternMatcher,
};
use crate::responsibility::classification::PhaseClassificationEngine;
use crate::responsibility::evidence::correlator::EvidenceCorrelator;
use crate::responsibility::evidence::intent_extractor::IntentExtractor;
use crate::responsibility::graph::graph_builder::GraphBuilder;
use crate::responsibility::graph::simplifier::GraphSimplifier;
use crate::responsibility::identity::resolver::IdentityResolver;
use crate::responsibility::observer::cache::ObserverCache;
use crate::responsibility::observer::types::{CacheKey, CacheTTL};
use crate::responsibility::observer::ObserverProvider;
use crate::responsibility::classification::types::Route as ClassRoute;
use crate::responsibility::persistence::{
    PersistenceWriter, PersistRecord, ProcessMetadata, TlsSession, BindingType,
};
use crate::responsibility::runtime::buffer::GraphDeltaBuffer;
use crate::responsibility::runtime::routing::{dispatch_event, DIAG_ACTIONS_PRODUCED, DIAG_EVIDENCE_INTENTS};
use crate::responsibility::signals::SignalGraph;
use crate::responsibility::types::alert_types::{
    BehaviorTimeline, BehaviorTimelineEntry, ResponsibilityAlert,
};
use crate::responsibility::scoring::progressive::DimIndex;
use crate::responsibility::cross_layer_bus::{
    CrossLayerBus, ScoringSignal, ClassificationSignal, ClaimSignal,
};
use crate::responsibility::output::{EvaluationSummary, OutputWriter, OutputFormat};
use crate::responsibility::types::graph_types::{
    GraphSnapshot, ResponsibilityEdgeType as NewEdgeType,
    ResponsibilityNodeType as NewNodeType,
};
use crate::responsibility::types::refinement::{GraphRefinement, GraphRefinementType};
use crate::responsibility::types::evidence_types::IntentAuthoritySource;
use crate::responsibility::types::ids::{AlertId, BoundaryId, ClaimId, EdgeId, MessageId, PolicyId, ProcessId, TlsSessionId};
use crate::framework::analyzers::Analyzer;
use crate::framework::analyzers::AnalyzerError;
use crate::framework::runners::EventStream;
use crate::framework::Event;
use futures::StreamExt;
use std::collections::HashSet;
use std::sync::atomic::{AtomicBool, Ordering};

/// Global shutdown signal. Set by the SIGINT handler in main.rs.
/// The runtime polls this in its event loop to trigger Phase 2 finalization.
pub static RUNTIME_SHUTDOWN: AtomicBool = AtomicBool::new(false);

// ─── Session End Signal ───────────────────────────────────────────────

/// Session end signal types per spec §3.3.
///
/// When received, the runtime stops accepting new events and triggers
/// Phase 2 authoritative refinement.
#[derive(Debug, Clone)]
pub enum SessionEndSignal {
    /// SIGINT received.
    Sigint,
    /// Monitored child process exited.
    Sigchld(u32),
    /// POST /api/sessions/{id}/end was called.
    ApiCall,
    /// Session timeout.
    Timeout(u64),
}

// ─── Runtime Configuration ────────────────────────────────────────────

/// Runtime configuration per spec §15.
#[derive(Debug, Clone)]
pub struct RuntimeConfig {
    /// Minimum time between classification triggers (ms).
    pub debounce_ms: u64,
    /// Force a trigger every N events.
    pub trigger_every_n: usize,
    /// Maximum Observer LLM calls per session.
    pub max_observer_calls: usize,
    /// Optional ONNX DeBERTa classifier for Content Boundary Layer 3.
    /// When set, tool input text is classified for prompt injection.
    pub classifier: Option<std::sync::Arc<crate::responsibility::judgment::classifier::OnnxClassifier>>,
    /// Disable the responsibility-layer Observer LLM (→ heuristic only).
    /// Used for ablation A4: measures Observer's marginal contribution.
    pub disable_observer: bool,
}

impl Default for RuntimeConfig {
    fn default() -> Self {
        Self {
            // 满血默认: 2 秒去抖, 每 50 事件强制触发, 最多 100 次 Observer 调用
            debounce_ms: 2000,
            trigger_every_n: 50,
            max_observer_calls: 100,
            classifier: None,
            disable_observer: false,
        }
    }
}

// ─── ResponsibilityRuntime ────────────────────────────────────────────

/// Central orchestrator that wires together all Phase 1 and Phase 2
/// subsystems.
///
/// # Architecture
///
/// ```text
/// Event → route_event() → signals/identity/evidence
///        ↑
/// trigger() → classification → claims → persistence → broadcast
///        ↑
/// finalize() → Phase 2 authoritative resolution
/// ```
///
/// Per spec §2.1 hard rule: the runtime only wires — it contains no
/// business logic. All analysis is delegated to the submodules.
pub struct ResponsibilityRuntime {
    /// Live signal graph (process tree, TLS bindings, message bindings).
    signal_graph: SignalGraph,
    /// Phase 1 provisional identity resolver.
    identity_resolver: IdentityResolver,
    /// Streaming evidence correlator (intent-action matching).
    evidence_correlator: EvidenceCorrelator,
    /// Incremental graph builder (handles correlations one at a time).
    graph_builder: GraphBuilder,
    /// Graph simplifier — removes low-confidence edges and merges duplicate
    /// resource nodes after each trigger cycle (§10.4 in architecture spec).
    graph_simplifier: GraphSimplifier,
    /// Phase-aware classification engine (route + score).
    classification_engine: PhaseClassificationEngine,
    /// Observer LLM provider (may be mock for development).
    observer: Box<dyn ObserverProvider>,
    /// L1+L2 cache for observer decisions.
    observer_cache: ObserverCache,
    /// Fire-and-forget persistence writer (bounded mpsc channel).
    persistence: PersistenceWriter,
    /// Unified cross-layer bus (scoring / classification / claims / alerts).
    bus: CrossLayerBus,
    /// Running count of events processed.
    event_count: usize,
    /// v9 Progressive threat scorer — six-dimension per-agent EWMA/CUSUM.
    progressive_scorer: crate::responsibility::scoring::progressive::ProgressiveScorer,
    /// v9 Behavioral baseline tracker — per-agent syscall/file/network profiling.
    baseline_tracker: crate::responsibility::scoring::baseline::BaselineTracker,
    /// v9 Calibration monitor — tracks ECE and triggers re-calibration.
    calibration_monitor: crate::responsibility::scoring::calibration::CalibrationMonitor,
    /// Running count of Observer LLM calls (capped by config).
    observer_calls: usize,
    /// Runtime configuration.
    config: RuntimeConfig,
    /// Thin graph delta buffer (accumulates between triggers).
    graph_buffer: GraphDeltaBuffer,
    /// Phase 1 alert IDs (for refinement comparison in Phase 2).
    phase1_alert_ids: Vec<AlertId>,
    /// Phase 1 claim IDs already alerted. ClaimId is now deterministically
    /// derived from the logical key, so the Set inherently deduplicates
    /// across trigger cycles.
    phase1_claim_ids: HashSet<ClaimId>,
    /// PBC edge IDs that have already been claimed in a previous trigger cycle.
    /// Prevents duplicate claims and alert inflation.
    claimed_edge_ids: HashSet<EdgeId>,
    /// Behavior timeline (accumulated across triggers).
    timeline: Vec<BehaviorTimelineEntry>,
    /// Whether new data has arrived since the last trigger.
    dirty: bool,
    /// Diagnostic: TLS event count.
    diag_tls_count: u64,
    /// Per-connection accumulated SSL data buffers.
    /// Keyed by connection identifier (pid:tid or connection_id).
    /// Accumulates small chunks into complete messages for intent extraction.
    conn_buffers: std::collections::HashMap<String, ConnBuffer>,
    /// Maps PID → most recent TLS session. Used to create message_bindings
    /// when flushing connection buffers, regardless of rw-direction key
    /// mismatches between TLS events and data events.
    pid_tls_session: std::collections::HashMap<u32, TlsSessionId>,
}

/// Accumulated raw data for a single TLS connection.
struct ConnBuffer {
    data: String,
    last_update_ns: u64,
    rw: u64, // 0=read(incoming/response), 1=write(outgoing/request)
    tls_session_id: Option<TlsSessionId>,
}

impl ConnBuffer {
    const MAX_SIZE: usize = 512 * 1024; // 512KB max per connection
    const TTL_NS: u64 = 60_000_000_000;  // 60s TTL

    fn append(&mut self, chunk: &str, ts: u64) {
        if self.data.len() + chunk.len() <= Self::MAX_SIZE {
            self.data.push_str(chunk);
        }
        self.last_update_ns = ts;
    }
}

impl ResponsibilityRuntime {
    // ── Construction ─────────────────────────────────────────────────

    /// Create a new ResponsibilityRuntime.
    ///
    /// The `persistence` writer should already be spawned (it runs its own
    /// tokio task). The `alert_tx` is used to push alerts to frontend SSE
    /// consumers. The `observer` provides LLM arbitration for ambiguous
    /// classifications (may be a MockObserverProvider in dev).
    pub fn new(
        config: RuntimeConfig,
        persistence: PersistenceWriter,
        alert_tx: broadcast::Sender<ResponsibilityAlert>,
        observer: Box<dyn ObserverProvider>,
    ) -> Self {
        // Build a CrossLayerBus that wraps the provided alert channel.
        // Downstream SSE subscribers receive alerts through this channel.
        let bus = CrossLayerBus::with_alert_tx(alert_tx);
        Self {
            signal_graph: SignalGraph::new(),
            identity_resolver: IdentityResolver::new(),
            evidence_correlator: EvidenceCorrelator::new(),
            graph_builder: GraphBuilder::with_default_config(),
            graph_simplifier: GraphSimplifier::with_default_config(),
            classification_engine: PhaseClassificationEngine::new(),
            observer,
            observer_cache: ObserverCache::new(),
            persistence,
            bus,
            event_count: 0,
            observer_calls: 0,
            config,
            graph_buffer: GraphDeltaBuffer::new(),
            phase1_alert_ids: Vec::new(),
            phase1_claim_ids: HashSet::new(),
            claimed_edge_ids: HashSet::new(),
            timeline: Vec::new(),
            progressive_scorer: crate::responsibility::scoring::progressive::ProgressiveScorer::new(
                crate::responsibility::scoring::progressive::ProgressiveConfig::default(),
            ),
            baseline_tracker: crate::responsibility::scoring::baseline::BaselineTracker::new(),
            calibration_monitor: crate::responsibility::scoring::calibration::CalibrationMonitor::default(),
            dirty: false,
            diag_tls_count: 0,
            conn_buffers: std::collections::HashMap::new(),
            pid_tls_session: std::collections::HashMap::new(),
        }
    }

    // ── Session Initialization (shared by run() and process()) ──────

    /// Initialize a new monitoring session.
    ///
    /// Bootstraps the signal graph from /proc and writes a RunStart marker
    /// to persistence so the writer task has an active `current_run_id`.
    /// Returns the generated run_id for RunEnd / cleanup use.
    fn init_session(&mut self) -> String {
        let now_ns = current_time_ns();
        self.signal_graph.bootstrap_from_proc(now_ns);
        let run_id = uuid::Uuid::new_v4().to_string();
        let _ = self.persistence.write(PersistRecord::RunStart {
            run_id: run_id.clone(),
            timestamp_ns: now_ns,
        });
        run_id
    }

    // ── Main Event Loop ──────────────────────────────────────────────

    /// Run the main event loop.
    ///
    /// Consumes events from `event_rx` and watches for session end on
    /// `end_rx`. Routes each event through `route_event()`, triggers
    /// classification on debounce or every N events, and calls
    /// `finalize()` when the session ends.
    ///
    /// # Channels
    ///
    /// - `event_rx` — unbounded receiver of framework events
    /// - `end_rx` — watch receiver for SessionEndSignal
    pub async fn run(
        &mut self,
        mut event_rx: mpsc::UnboundedReceiver<Event>,
        mut end_rx: watch::Receiver<Option<SessionEndSignal>>,
    ) {
        let run_id = self.init_session();

        let debounce = Duration::from_millis(self.config.debounce_ms);
        let mut tick = interval(debounce);

        loop {
            tokio::select! {
                // ── Event received ──────────────────────────────────
                Some(event) = event_rx.recv() => {
                    self.event_count += 1;
                    self.dirty = true;

                    // Route event to subsystems (raw events persisted inside)
                    let _output = self.route_event(event);

                    // Trigger on count threshold
                    if self.event_count % self.config.trigger_every_n == 0 {
                        self.trigger().await;
                    }
                }

                // ── Debounce timer fired ────────────────────────────
                _ = tick.tick() => {
                    if self.dirty {
                        self.trigger().await;
                    }
                }

                // ── Session end signal ──────────────────────────────
                Ok(()) = end_rx.changed() => {
                    // Complete any pending trigger, then finalize
                    if self.dirty {
                        self.trigger().await;
                    }
                    self.finalize().await;

                    // Write run end marker
                    let _ = self.persistence.write(PersistRecord::RunEnd {
                        run_id: run_id.clone(),
                        timestamp_ns: current_time_ns(),
                    });

                    // Flush persistence before exit
                    let _ = self.persistence.flush().await;
                    break;
                }

                else => break,
            }
        }
    }

    // ── Event Routing ────────────────────────────────────────────────

    /// Route a single framework event to the appropriate subsystems.
    ///
    /// Inspects the event's `source` and `data` fields to determine
    /// routing (EXEC/EXIT → signals, TLS → signals, HTTP → evidence,
    /// Syscall → evidence). Delegates to `routing::dispatch_event()`.
    ///
    /// Before dispatching, persists raw events (process, TLS) to the
    /// async writer per spec §13.1 Phase 1 persistence contract.
    ///
    /// Returns a `Vec<Event>` of output events for downstream framework
    /// analyzers (e.g., signal events, alert events).
    pub fn route_event(&mut self, event: Event) -> Vec<Event> {
        let intents_before = self.graph_buffer.intents.len();
        let actions_before = self.graph_buffer.actions.len();

        self.persist_raw_event(&event);

        let output = dispatch_event(
            &event,
            &mut self.signal_graph,
            &mut self.identity_resolver,
            &mut self.evidence_correlator,
            &mut self.graph_buffer,
        )
        .output_events;

        // Persist new intents and actions for replay support.
        // Phase 2 replay reads these from SQLite to re-run batch correlation.
        for intent in &self.graph_buffer.intents[intents_before..] {
            let _ = self.persistence.write(PersistRecord::Intent(intent.clone()));
        }
        for action in &self.graph_buffer.actions[actions_before..] {
            let _ = self.persistence.write(PersistRecord::Action(action.clone()));
        }

        // Persist provisional identities and process→agent mappings
        for pi in self.identity_resolver.provisional_identities() {
            let _ = self.persistence.write(PersistRecord::ProvisionalIdentity(pi.clone()));
            for pid in &pi.process_ids {
                let _ = self.persistence.write(PersistRecord::IdentityMapping {
                    agent_id: pi.agent_id.clone(),
                    pid: pid.pid,
                    start_time_ns: pid.start_time_ns,
                });
            }
        }

        output
    }

    /// Persist raw eBPF events (EXEC, TLS) and accumulate SSL data.
    ///
    /// Fire-and-forget per spec §13.1: uses try_send, drops silently
    /// on full channel. Also accumulates SSL data chunks into per-connection
    /// buffers for later intent extraction (handles chunked/streaming APIs).
    fn persist_raw_event(&mut self, event: &Event) {
        let event_type = event.data.get("event").and_then(|v| v.as_str());

        // ── Process event persistence ──────────────────────────────
        if let Some("EXEC") = event_type {
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
            let parent_pid = event
                .data
                .get("ppid")
                .and_then(|v| v.as_u64())
                .map(|v| v as u32);
            let parent_start_time_ns = event
                .data
                .get("parent_start_time_ns")
                .and_then(|v| v.as_u64());
            let comm = event
                .data
                .get("comm")
                .and_then(|v| v.as_str())
                .unwrap_or(&event.comm)
                .to_string();

            let _ = self.persistence.write(PersistRecord::Process(ProcessMetadata {
                pid,
                start_time_ns,
                parent_pid,
                parent_start_time_ns,
                comm,
                timestamp_ns: event.timestamp,
            }));
            return;
        }

        // ── TLS event persistence ──────────────────────────────────
        let is_tls = event.data.get("connection_id").is_some()
            || event.source.contains("ssl")
            || event.source.contains("tls");
        if is_tls {
            self.diag_tls_count += 1;
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
            // Resolve correct ProcessId from signal graph — TLS events carry
            // start_time_ns=0. Without this fix, identity resolution and
            // process tree correlation fail because ProcessId(pid,0)
            // never matches ProcessId(pid,real_start).
            let resolved_id = crate::responsibility::runtime::routing::resolve_process_id(
                ProcessId::new(pid, start_time_ns),
                &self.signal_graph.snapshot(),
            );
            let tls_session_id = event
                .data
                .get("connection_id")
                .or_else(|| event.data.get("tls_session"))
                .and_then(|v| v.as_str())
                .map(|s| TlsSessionId::from(s))
                .unwrap_or_else(TlsSessionId::new);

            let _ = self.persistence.write(PersistRecord::Tls(TlsSession {
                tls_session_id: tls_session_id.clone(),
                pid: resolved_id.pid,
                start_time_ns: resolved_id.start_time_ns,
                timestamp_ns: event.timestamp,
            }));

            // Store PID→TLS mapping so flush_conn_buffers can create
            // message_bindings regardless of rw-direction key mismatches.
            self.pid_tls_session.insert(resolved_id.pid, tls_session_id);
        }

        // ── Cross-event payload accumulation (all sources) ────────
        let chunk: Option<&str> = event.data.get("data").and_then(|v| v.as_str())
            .or_else(|| event.data.get("body").and_then(|v| v.as_str()));
        if let Some(chunk) = chunk {
            if !chunk.is_empty() {
                let pid = event.data.get("pid")
                    .and_then(|v| v.as_u64())
                    .unwrap_or(event.pid as u64) as u32;
                let rw = event.data.get("function")
                    .and_then(|v| v.as_str())
                    .map(|f| if f.to_lowercase().contains("read") { 0u64 } else { 1u64 })
                    .unwrap_or(1);
                // Separate buffers per PID and direction (read=response, write=request)
                let conn_key = format!("{}:rw{}", pid, rw);
                self.conn_buffers
                    .entry(conn_key)
                    .or_insert_with(|| ConnBuffer { data: String::new(), last_update_ns: 0, rw, tls_session_id: None })
                    .append(chunk, event.timestamp);
            }
        }
    }

    /// Flush accumulated connection buffers through intent extraction.
    ///
    /// Called periodically from trigger() to process accumulated SSL data
    /// into intents. Drains each buffer and resets it.
    fn flush_conn_buffers(&mut self) {
        let snapshot = self.signal_graph.snapshot();
        let now = current_time_ns();

        // Expire old buffers first
        self.conn_buffers.retain(|_, buf| {
            now.saturating_sub(buf.last_update_ns) < ConnBuffer::TTL_NS
        });



        for (conn_key, buf) in &mut self.conn_buffers {
            if buf.data.is_empty() || buf.data.len() < 64 {
                continue;
            }


            // Parse connection key: "pid:rwX"
            let pid: u32 = match conn_key.split(':').next().and_then(|s| s.parse().ok()) {
                Some(p) => p,
                None => continue,
            };
            let process_id = crate::responsibility::runtime::routing::resolve_process_id(
                ProcessId::new(pid, 0), &snapshot);

            let source = if buf.rw == 1 {
                IntentAuthoritySource::SseResponseToolUse // outgoing — has request context
            } else {
                IntentAuthoritySource::HttpResponseToolUse
            };
            let msg_id = MessageId::new();

            let intents = IntentExtractor::extract(
                &buf.data,
                source,
                process_id,
                msg_id.clone(),
                buf.last_update_ns,
            );

            if !intents.is_empty() {
                // Persist message binding — links TLS session to parsed message.
                // This enables end-to-end traceability: TLS connection → HTTP
                // message → intent → action. Uses the PID→TLS map populated
                // when TLS events arrive (separately from data chunks).
                if let Some(tls_sid) = self.pid_tls_session.get(&process_id.pid) {
                    let _ = self.persistence.write(PersistRecord::MessageBinding {
                        message_id: msg_id.clone(),
                        pid: process_id.pid,
                        start_time_ns: process_id.start_time_ns,
                        tls_session_id: Some(tls_sid.clone()),
                        binding_type: BindingType::TlsShared,
                        timestamp_ns: buf.last_update_ns,
                    });
                }

                DIAG_EVIDENCE_INTENTS.fetch_add(intents.len() as u64, Ordering::Relaxed);
                for intent in intents {
                    let intent_clone = intent.clone();
                    self.graph_buffer.push_intent(intent_clone);
                    let evidence_list = self.evidence_correlator.on_intent(intent, &snapshot);
                    for ev in evidence_list {
                        self.graph_buffer.push_evidence(ev);
                    }
                }
                // Only clear if extraction succeeded; otherwise accumulate more data
                buf.data.clear();
            }
            // Note: if no intents found, data stays for next trigger
        }
    }

    // ── Classification Trigger ───────────────────────────────────────

    /// Trigger the classification pipeline.
    ///
    /// Per spec §3.1 (Phase 1 streaming):
    /// 1. Snapshot the signal graph
    /// 2. Drain the evidence correlator's diagnostics
    /// 3. Feed accumulated evidence through graph builder
    /// 4. Pattern-match the responsibility graph
    /// 5. Classify (may call Observer LLM)
    /// 6. Build claims, alerts, tracebacks
    /// 7. Persist and broadcast
    pub async fn trigger(&mut self) {
        // ── 0. Flush accumulated connection buffers → intents ──────
        self.flush_conn_buffers();

        // ── 1. Signal snapshot ─────────────────────────────────────
        let _snapshot = self.signal_graph.snapshot();

        // ── 2. Drain and persist evidence correlator diagnostics ──
        for diag in self.evidence_correlator.drain_diagnostics() {
            let _ = self.persistence.write(PersistRecord::NonLink(diag));
        }

        // ── 3a. Process standalone intents (UserPrompt etc.) through
        //      content/delegation boundary checks. These don't require
        //      correlation evidence (no matching syscall action).
        let standalone_intents: Vec<_> = self.graph_buffer.intents.drain(..).collect();
        for intent in &standalone_intents {
            // Content/delegation boundary check (tool-level: "Bash was used")
            let delta = self.graph_builder.handle_intent_content(
                intent,
                &self.identity_resolver,
                intent.timestamp_ns,
            );
            for node in &delta.new_nodes {
                self.graph_buffer.push_node(node.clone());
            }
            for edge in &delta.new_edges {
                self.graph_buffer.push_edge(edge.clone());
            }

            // Resource-level PBC: extract specific file paths / URLs from
            // the intent's tool_args and create PolicyBoundary nodes for
            // each (e.g. /etc/hostname, credential patterns). This gives
            // claims granularity beyond just "Bash was used".
            if let Some(pbc_delta) = self.graph_builder.handle_intent_pbc(
                intent,
                &self.identity_resolver,
            ) {
                for node in &pbc_delta.new_nodes {
                    self.graph_buffer.push_node(node.clone());
                }
                for edge in &pbc_delta.new_edges {
                    self.graph_buffer.push_edge(edge.clone());
                }
            }

            // Re-push intent so correlator can still use it
            self.graph_buffer.push_intent(intent.clone());
        }


        // ── 3. Feed correlation evidence through graph builder ─────
        // Process each accumulated evidence item
        let evidence_snapshot: Vec<_> = self.graph_buffer.evidence.drain(..).collect();
        for ev in &evidence_snapshot {
            // Persist the evidence link to DB
            let _ = self.persistence.write(PersistRecord::Evidence(ev.clone()));

            // Look up the intent and action that produced this evidence
            let intent = self.graph_buffer.find_intent_by_id(&ev.intent_id).cloned();
            let action = self.graph_buffer.find_action_by_id(&ev.action_id).cloned();

            if let (Some(intent), Some(action)) = (&intent, &action) {
                let delta = self.graph_builder.handle(
                    ev,
                    intent,
                    action,
                    &self.identity_resolver, // IdentityResolver impls IdentityProvider
                );

                // Accumulate nodes and edges from the delta into the buffer
                for node in &delta.new_nodes {
                    self.graph_buffer.push_node(node.clone());
                }
                for edge in &delta.new_edges {
                    self.graph_buffer.push_edge(edge.clone());
                }
            }
        }

        // ── Simplify the graph ───────────────────────────────────────
        // Remove low-confidence edges and merge duplicate resource nodes
        // before classification. This reduces noise from weak substring
        // matches and duplicate resources that inflate edge counts.
        let simplify_delta = self.graph_simplifier.simplify(self.graph_builder.graph_mut());
        if !simplify_delta.removed_edges.is_empty() || !simplify_delta.removed_nodes.is_empty() {
            let _ = self.persistence.write(PersistRecord::GraphRefinement(
                GraphRefinement {
                    refinement_type: GraphRefinementType::EdgeRemoved,
                    affected_nodes: simplify_delta.removed_nodes.clone(),
                    affected_edges: simplify_delta.removed_edges.clone(),
                    description: format!(
                        "simplifier: removed {} nodes, {} edges — {}",
                        simplify_delta.removed_nodes.len(),
                        simplify_delta.removed_edges.len(),
                        simplify_delta.diagnostics.join("; ")
                    ),
                }));
        }

        // ── 4. Pattern match the graph ─────────────────────────────
        let graph = self.graph_builder.graph();
        let matcher = GraphPatternMatcher::new(default_templates());
        let patterns = matcher.match_graph(graph);

        // ── 4.5 Feed progressive scorer with dimension signals ──────
        // Extract per-dimension raw scores from the accumulated graph
        // and feed them into the six-dimension EWMA/CUSUM tracker.
        // Also read back fused scores and CUSUM alarms for downstream
        // observer trigger and alert composition.
        let pbc_count = graph.edges_of_type(&NewEdgeType::PolicyBoundaryCrossing).len();
        let evidence_conf = evidence_snapshot.iter().map(|e| e.confidence as f64).sum::<f64>()
            / evidence_snapshot.len().max(1) as f64;
        let agent_count = graph.all_nodes().filter(|n| matches!(n.node_type, NewNodeType::Agent)).count();

        // Feed each dimension (using first agent's identity as proxy)
        // and capture the fused progressive score.
        let mut progressive_fused: Option<f64> = None;
        let mut cusum_triggered: bool = false;
        let mut cusum_alarm_dimensions: Vec<usize> = Vec::new();
        let mut evidence_conflict_flag: bool = false;
        let mut deberta_score: f64 = 0.0;
        if let Some(agent_id) = graph.all_nodes()
            .filter_map(|n| n.agent_id.clone())
            .next()
        {
            let now_ns = current_time_ns();
            let scorer = &mut self.progressive_scorer;
            scorer.update_dimension(&agent_id, DimIndex::D1Boundary, (pbc_count as f64 / 10.0).min(1.0), now_ns);
            scorer.update_dimension(&agent_id, DimIndex::D2Content, patterns.iter().map(|p| p.confidence as f64).sum::<f64>() / patterns.len().max(1) as f64, now_ns);
            scorer.update_dimension(&agent_id, DimIndex::D3Agentic, (agent_count as f64 / 5.0).min(1.0), now_ns);
            scorer.update_dimension(&agent_id, DimIndex::D5CrossContext, evidence_conf.min(1.0), now_ns);

            // D2 Content: DeBERTa augmentation (async, non-blocking)
            if let Some(ref _classifier) = self.config.classifier {
                for intent in &standalone_intents {
                    if let Some(text) = intent.tool_args.iter().find_map(|a| match a {
                        crate::responsibility::types::NormalizedArg::Content(c) if c.len() > 20 => Some(c.as_str()),
                        _ => None,
                    }) {
                        if let Some(score) = _classifier.predict(text) {
                            scorer.update_dimension(&agent_id, DimIndex::D2Content, score as f64, now_ns);
                        }
                    }
                }
            }

            // ── Read back progressive scores for downstream use ──────
            progressive_fused = scorer.get_agent_fused_score(&agent_id);
            // Capture DeBERTa D2Content dimension for ablation persistence
            deberta_score = scorer.agents.get(&agent_id)
                .map(|s| s.dims[1].ewma)
                .unwrap_or(0.0);
            let (alarm_count, alarms) = scorer.cusum_alarms(&agent_id);
            cusum_alarm_dimensions = alarms.clone();

            // Smart CUSUM escalation: only escalate when at least 2 dimensions
            // alarm, OR when D1 (Boundary) or D2 (Content) alarms alongside any
            // other dimension.  Pure D3 (Agentic) alarms alone are expected in
            // multi-agent scenarios and should not trigger observer calls.
            let has_boundary_or_content_alarm = alarms.contains(&0) || alarms.contains(&1); // D1=0, D2=1
            let smart_cusum = alarm_count >= 2 || (alarm_count >= 1 && has_boundary_or_content_alarm);
            cusum_triggered = smart_cusum;
            evidence_conflict_flag = scorer.evidence_conflict(&agent_id);

            // ── Compute D6 Behavioral from baseline ──────────────────
            // Read the behavioral baseline for this agent and compute
            // deviation scores, then feed to D6.
            if let Some(baseline) = self.baseline_tracker.get(agent_id.as_str()) {
                if baseline.is_established() {
                    // Compute aggregate deviation from recent behavior
                    let syscall_devs: Vec<f64> = baseline.syscall_profile.keys()
                        .map(|name| baseline.syscall_deviation(name, 0.1))
                        .collect();
                    let file_devs: Vec<f64> = baseline.file_access_dirs.keys()
                        .map(|path| baseline.file_deviation(path))
                        .collect();
                    let net_devs: Vec<f64> = baseline.network_ports.keys()
                        .map(|port| baseline.network_deviation(*port))
                        .collect();
                    let d6_raw = baseline.aggregate_deviation(&syscall_devs, &file_devs, &net_devs);
                    scorer.update_dimension(&agent_id, DimIndex::D6Behavioral, d6_raw, now_ns);
                }
            }

            // Feed behavior samples to baseline tracker
            for edge in graph.all_edges() {
                if edge.edge_type == NewEdgeType::IntentCausesAction {
                    if let Some(target) = graph.get_node(&edge.target) {
                        let path = target.resource.as_ref().map(|r| match r {
                            crate::responsibility::types::NormalizedResource::File { canonical_path } =>
                                canonical_path.clone(),
                            crate::responsibility::types::NormalizedResource::Network { host, port, .. } =>
                                format!("{}:{}", host, port),
                            _ => String::new(),
                        });
                        self.baseline_tracker.observe(agent_id.as_str(),
                            crate::responsibility::scoring::baseline::BehaviorSample {
                                syscall_name: None,
                                file_path: path,
                                port: None,
                                is_tool_call: true,
                                timestamp_ns: now_ns,
                            });
                    }
                }
            }
            self.baseline_tracker.rebuild_baseline(agent_id.as_str(), now_ns);

            // ── Emit scoring signal to cross-layer bus ──────────────
            self.bus.emit_scoring(ScoringSignal {
                agent_id: agent_id.clone(),
                fused_score: progressive_fused.unwrap_or(0.0),
                dimension_scores: [
                    scorer.agents.get(&agent_id).map(|s| s.dims[0].ewma).unwrap_or(0.0),
                    scorer.agents.get(&agent_id).map(|s| s.dims[1].ewma).unwrap_or(0.0),
                    scorer.agents.get(&agent_id).map(|s| s.dims[2].ewma).unwrap_or(0.0),
                    scorer.agents.get(&agent_id).map(|s| s.dims[3].ewma).unwrap_or(0.0),
                    scorer.agents.get(&agent_id).map(|s| s.dims[4].ewma).unwrap_or(0.0),
                    scorer.agents.get(&agent_id).map(|s| s.dims[5].ewma).unwrap_or(0.0),
                ],
                cusum_triggered,
                evidence_conflict: evidence_conflict_flag,
            });
        }

        // ── 5. Classify (Phase 1: may call Observer) ───────────────
        let repetition_count = self.graph_buffer.node_count() as u32;

        // Check L1+L2 cache before calling observer (if quota allows)
        let cache_key = {
            let edge_types: Vec<String> = graph.all_edges().map(|e| format!("{:?}", e.edge_type)).collect();
            let node_types: Vec<String> = graph.all_nodes().map(|n| format!("{:?}", n.node_type)).collect();
            CacheKey::compute(&edge_types, &node_types, "", "")
        };
        let cached = self.observer_cache.lookup(cache_key);

        // ── Four-condition Observer trigger (§16.2) ─────────────
        // Triggers LLM arbitration when ANY of these pre-classification
        // conditions is met (grey zone is handled by classify_provisional):
        //   C2: CUSUM drift — any dimension has sustained upward shift
        //   C3: Evidence conflict — D1 Boundary high but D5 CrossContext low
        //   C4: Cross-layer boost — progressive fused score > 0.4
        //   C5: High boundary density — PBC > 50 means ambiguous structural
        //       signals (could be attack or legitimate multi-agent work).
        //       Force Observer to semantically distinguish intent.
        let needs_observer_extra = cusum_triggered || evidence_conflict_flag
            || progressive_fused.map_or(false, |s| s > 0.4)
            || pbc_count > 50;

        let observer_for_call: Option<&dyn ObserverProvider> =
            if self.config.disable_observer {
                None // Ablation A4: Observer disabled → heuristic fallback
            } else if cached.is_some() {
                None // Cache hit — skip observer call
            } else if self.observer_calls < self.config.max_observer_calls {
                // Pre-classification override: if CUSUM/conflict/boost triggers,
                // pass observer even if the engine scoring might have fallen
                // into DirectSafe/DirectUnsafe territory.
                if needs_observer_extra {
                    eprintln!("[runtime:trigger] observer forced by extra conditions: cusum={} conflict={} pfuse={:?}",
                        cusum_triggered, evidence_conflict_flag, progressive_fused);
                }
                Some(self.observer.as_ref())
            } else {
                None // Fall back to heuristic when quota exhausted
            };

        let (mut classification_result, route) = self
            .classification_engine
            .classify_provisional(
                graph,
                &patterns,
                &evidence_snapshot,
                observer_for_call,
                repetition_count,
                needs_observer_extra, // force observer when CUSUM/conflict/boost
            )
            .await;

        // ── Feed calibration monitor ──────────────────────────────
        // Record predictions for ECE tracking. When observer was called,
        // its decision serves as a pseudo-label for calibration.
        let is_observer_unsafe = route_is_unsafe(&route);
        self.calibration_monitor.record(
            classification_result.score as f64,
            is_observer_unsafe,
        );
        if self.calibration_monitor.needs_recalibration() {
            eprintln!(
                "[runtime:trigger] calibration ECE={:.4} exceeds threshold — recalibration recommended",
                self.calibration_monitor.ece()
            );
        }

        // ── Emit classification signal to cross-layer bus ─────────
        let trigger_reason = if cusum_triggered { "cusum_drift".to_string() }
            else if evidence_conflict_flag { "evidence_conflict".to_string() }
            else if needs_observer_extra { "cross_layer_boost".to_string() }
            else if classification_result.route.starts_with("observer") { "grey_zone".to_string() }
            else { String::new() };

        // Persist CUSUM data into classification_result for DB storage
        classification_result.cusum_triggered = cusum_triggered;
        classification_result.cusum_dimensions = cusum_alarm_dimensions.iter()
            .map(|&i| DimIndex::from_usize(i).name().to_string())
            .collect();
        classification_result.progressive_fused = progressive_fused.unwrap_or(0.0);
        classification_result.observer_trigger_reason = trigger_reason.clone();
        classification_result.deberta_score = deberta_score;

        self.bus.emit_classification(ClassificationSignal {
            score: classification_result.score,
            route: classification_result.route.clone(),
            observer_called: classification_result.route.starts_with("observer"),
            observer_trigger_reason: trigger_reason,
        });

        // Count both successful ("observer") and failed ("observer_failed") attempts.
        // Both mean we tried to call the LLM — the count tracks quota consumption.
        if classification_result.route.starts_with("observer") {
            self.observer_calls += 1;
            if let Some(output) = match &route {
                ClassRoute::DirectSafe(o) | ClassRoute::DirectUnsafe(o) => Some(o),
                _ => None,
            } {
                // Now that the type system is unified, ObserverOutput is the same
                // type across classification and observer modules.
                self.observer_cache.store(cache_key, crate::responsibility::observer::types::ObserverOutput {
                    decision: output.decision.clone(),
                    severity: output.severity.clone(),
                    confidence: output.confidence,
                    cache_ttl: output.cache_ttl.clone(),
                    authority: output.authority.clone(),
                    reasoning: output.reasoning.clone(),
                    attacked_flag: output.attacked_flag,
                    rule_verdicts: output.rule_verdicts.clone(),
                    pattern_verdicts: output.pattern_verdicts.clone(),
                }, CacheTTL::Task);
            }
        }

        // ── 6. Build claims, alerts, tracebacks ────────────────────
        let claims = ClaimBuilder::build_claims(graph, &self.claimed_edge_ids);

        // Mark these PBC edges as claimed so future trigger cycles skip them.
        // Without this, each trigger() call would re-claim the same edges,
        // causing alert inflation (e.g., 76 duplicate alerts for 1 PBC edge).
        for eid in graph.edges_of_type(&NewEdgeType::PolicyBoundaryCrossing) {
            if !self.claimed_edge_ids.contains(eid) {
                self.claimed_edge_ids.insert(eid.clone());
            }
        }

        // Build the behavior timeline from accumulated entries
        let mut timeline_steps = std::mem::take(&mut self.graph_buffer.timeline);
        self.timeline.append(&mut timeline_steps);

        let timeline = BehaviorTimeline {
            steps: self.timeline.clone(),
            total_duration_ms: if let (Some(first), Some(last)) =
                (self.timeline.first(), self.timeline.last())
            {
                (last.timestamp_ns.saturating_sub(first.timestamp_ns) / 1_000_000)
                    .min(u64::MAX)
            } else {
                0
            },
        };

        // Only pass an ObserverOutput when the route actually went through
        // semantic Observer arbitration. Direct engine routes also carry an
        // ObserverOutput-shaped value, but treating that as Observer judgment
        // makes direct_safe claims look like reviewed safe baselines.
        let observer_output = if classification_result.route.starts_with("observer_") {
            match &route {
                crate::responsibility::classification::types::Route::DirectSafe(output)
                | crate::responsibility::classification::types::Route::DirectUnsafe(output) => {
                    Some(output.clone())
                }
                crate::responsibility::classification::types::Route::ToObserver(_) => None,
            }
        } else {
            None
        };

        let fallback_agent = self
            .identity_resolver
            .provisional_identities()
            .first()
            .map(|p| p.agent_id.clone());

        // Only build an alert when we can identify a subject agent.
        // During the first trigger cycle, the identity resolver may not have
        // processed any processes yet (race between debounce timer and event
        // arrival). Skipping the alert here is safe — the data remains in
        // the buffer and will be picked up on the next trigger.
        let has_graph_agent = graph
            .all_nodes()
            .any(|n| n.node_type == NewNodeType::Agent);
        let can_identify_agent = fallback_agent.is_some() || has_graph_agent || !claims.is_empty();

        // Deterministic ClaimId: same logical claim → same id across cycles.
        // The Set automatically filters duplicates without a separate key field.
        let new_claims: Vec<_> = claims
            .iter()
            .filter(|c| self.phase1_claim_ids.insert(c.claim_id.clone()))
            .cloned()
            .collect();

        if can_identify_agent && !new_claims.is_empty() {
            // ── Emit claim signals to cross-layer bus ──────────────
            for claim in &new_claims {
                self.bus.emit_claim(ClaimSignal {
                    claim_id: claim.claim_id.clone(),
                    agent_id: claim.subject.clone(),
                    edge_id: None, // PBC edge-id tracked separately in claimed_edge_ids
                    confidence: claim.confidence,
                    is_new: true,
                });
            }

            let alert = AlertBuilder::compose(
                &new_claims,
                &classification_result,
                observer_output.as_ref(),
                graph,
                &timeline,
                fallback_agent.as_ref(),
            );

            // Persist claims FIRST (traceback has FOREIGN KEY to claim)
            for claim in &new_claims {
                self.phase1_claim_ids.insert(claim.claim_id.clone());
                let _ = self
                    .persistence
                    .write(PersistRecord::Claim(claim.clone()));
            }

            // Build and persist tracebacks AFTER claims
            for claim in &new_claims {
                if let Some(traceback) = TracebackBuilder::build(claim, graph) {
                    let _ = self
                        .persistence
                        .write(PersistRecord::Traceback(traceback));
                }
            }

            self.phase1_alert_ids.push(alert.alert_id.clone());
            let _ = self
                .persistence
                .write(PersistRecord::Alert(alert.clone()));

            // Broadcast alert to frontend SSE consumers
            let _ = self.bus.emit_alert(alert);
        }

        // ── 7. Persist graph nodes and edges (always, even without alert) ──
        for node in &self.graph_buffer.nodes {
            let _ = self
                .persistence
                .write(PersistRecord::GraphNode(node.clone()));
        }
        for edge in &self.graph_buffer.edges {
            let _ = self
                .persistence
                .write(PersistRecord::GraphEdge(edge.clone()));
        }

        // Persist a graph snapshot for replay and Phase 2 diff.
        // Per spec §10.4, snapshots are saved at each trigger() cycle
        // so that the replay system can reconstruct graph state at any
        // point in time for evaluation and audit.
        let snapshot = GraphSnapshot {
            snapshot_id: uuid::Uuid::new_v4().to_string(),
            timestamp_ns: current_time_ns(),
            nodes: graph.all_nodes().cloned().collect(),
            edges: graph.all_edges().cloned().collect(),
            metrics: graph.metrics(),
        };
        let _ = self.persistence.write(PersistRecord::GraphSnapshot(snapshot));

        // ── Persist policy/judgment data ────────────────────────────
        // Policy evaluations: one per PolicyBoundaryCrossing edge
        for edge in &self.graph_buffer.edges {
            if edge.edge_type == crate::responsibility::types::graph_types::ResponsibilityEdgeType::PolicyBoundaryCrossing {
                let _ = self.persistence.write(PersistRecord::PolicyEvaluation(
                    crate::responsibility::types::policy_types::PolicyEvaluation {
                        policy_id: crate::responsibility::types::ids::PolicyId::new(),
                        boundary: crate::responsibility::types::policy_types::PolicyBoundary::Capability,
                        judgment: crate::responsibility::types::policy_types::PolicyJudgment {
                            judgment_id: uuid::Uuid::new_v4().to_string(),
                            policy_id: crate::responsibility::types::ids::PolicyId::new(),
                            boundary_id: crate::responsibility::types::ids::BoundaryId::from(edge.edge_id.to_string()),
                            subject: crate::responsibility::types::ids::AgentId::from(edge.source.to_string()),
                            graph_ref: edge.edge_id.to_string(),
                            evidence_refs: edge.evidence_refs.clone(),
                            result: crate::responsibility::types::policy_types::PolicyJudgmentResult::Violation,
                            reason: format!("Policy boundary crossed: {} -> {}", edge.source, edge.target),
                            confidence: edge.confidence,
                        },
                        subject: crate::responsibility::types::ids::AgentId::from(edge.source.to_string()),
                        resource: None,
                        evidence: edge.evidence_refs.clone(),
                        confidence: edge.confidence,
                        reason: "PolicyBoundaryCrossing edge created".to_string(),
                    }));
            }
        }
        // Context pollution from taint tracker
        let taint = self.graph_builder.taint_tracker();
        for (path, record) in taint.contaminated_artifacts() {
            let _ = self.persistence.write(PersistRecord::ContextPollution(
                crate::responsibility::types::policy_types::ContextPollution {
                    source_agent: record.source_agent.clone(),
                    contaminated_artifact: path.clone(),
                    propagation_path: vec![],
                    detected_at_ns: record.timestamp_ns,
                }));
        }

        // ── Cleanup ────────────────────────────────────────────────
        // Clear only processed items: nodes, edges, evidence (persisted),
        // and timeline (consumed). Keep intents and actions for Phase 2
        // batch correlation in finalize().
        self.graph_buffer.nodes.clear();
        self.graph_buffer.edges.clear();
        self.graph_buffer.evidence.clear();
        self.graph_buffer.timeline.clear();
        self.dirty = false;
    }

    // ── Phase 2: Batch Finalization ──────────────────────────────────

    /// Finalize the session with Phase 2 authoritative refinement.
    ///
    /// Per spec §3.2:
    /// 1. Get frozen signal snapshot
    /// 2. Run authoritative identity resolution
    /// 3. Run batch evidence correlation (all intents × all actions)
    /// 4. Finalize graph with identity + evidence refinements
    /// 5. Run authoritative classification (no observer)
    /// 6. Build authoritative claims/alerts/tracebacks
    /// 7. Persist all authoritative data + refinements
    ///
    /// # Safety
    ///
    /// This method holds `&mut self`, so it is mutually exclusive with
    /// `trigger()`. The `run()` loop ensures `finalize()` only executes
    /// after the last `trigger()` completes.
    pub async fn finalize(&mut self) {
        // ── 1. Frozen signal snapshot ──────────────────────────────
        self.signal_graph.evict_expired(current_time_ns());
        let snapshot = self.signal_graph.snapshot();

        // ── 2. Authoritative identity resolution ───────────────────
        let authoritative_identity = self.identity_resolver.resolve_authoritative(&snapshot);

        // Persist authoritative identities and refinements.
        // Also persist authoritative process→agent mappings so that
        // identity_process_map uses deterministic hashes instead of
        // provisional UUIDs. Phase 2 is the source of truth.
        for agent in &authoritative_identity.agents {
            let _ = self
                .persistence
                .write(PersistRecord::AuthoritativeIdentity(agent.clone()));
            for pid in &agent.process_ids {
                let _ = self.persistence.write(PersistRecord::IdentityMapping {
                    agent_id: agent.agent_id.clone(),
                    pid: pid.pid,
                    start_time_ns: pid.start_time_ns,
                });
            }
        }
        for refinement in &authoritative_identity.refinements {
            let _ = self
                .persistence
                .write(PersistRecord::IdentityRefinement(refinement.clone()));
        }
        for boundary in &authoritative_identity.subagent_boundaries {
            let _ = self
                .persistence
                .write(PersistRecord::SubagentBoundary(boundary.clone()));
        }

        // ── 3. Batch evidence correlation ──────────────────────────
        // Collect all intents and actions from the buffer
        let all_intents: Vec<_> = std::mem::take(&mut self.graph_buffer.intents);
        let all_actions: Vec<_> = std::mem::take(&mut self.graph_buffer.actions);
        let batch_evidence =
            self.evidence_correlator
                .correlate_batch(&all_intents, &all_actions, &snapshot);

        // Persist batch evidence and refinements
        for ev in &batch_evidence.evidence {
            let _ = self
                .persistence
                .write(PersistRecord::Evidence(ev.clone()));
        }
        for refinement in &batch_evidence.refinements {
            let _ = self
                .persistence
                .write(PersistRecord::EvidenceRefinement(refinement.clone()));
        }

        // ── 4. Finalize graph ──────────────────────────────────────
        let graph = self.graph_builder.graph();
        let snapshot_nodes: Vec<_> = graph.all_nodes().cloned().collect();
        let snapshot_edges: Vec<_> = graph.all_edges().cloned().collect();
        let metrics = graph.metrics();
        let graph_snapshot = GraphSnapshot {
            snapshot_id: uuid::Uuid::new_v4().to_string(),
            timestamp_ns: current_time_ns(),
            nodes: snapshot_nodes,
            edges: snapshot_edges,
            metrics,
        };
        let _ = self.persistence.write(PersistRecord::GraphSnapshot(graph_snapshot.clone()));

        let _frozen = self.graph_builder.finalize(
            &authoritative_identity,
            &batch_evidence,
            graph_snapshot,
        );

        // Persist graph refinements
        for refinement in &_frozen.refinements {
            let _ = self
                .persistence
                .write(PersistRecord::GraphRefinement(refinement.clone()));
        }

        // ── 5. Authoritative classification ────────────────────────
        let graph = self.graph_builder.graph();
        let matcher = GraphPatternMatcher::new(default_templates());
        let patterns = matcher.match_graph(graph);
        let repetition_count = all_intents.len() as u32;

        let (classification_result, _route) = self
            .classification_engine
            .classify_authoritative(
                graph,
                &patterns,
                &batch_evidence.evidence,
                repetition_count,
            );

        // ── 6. Build authoritative claims/alerts/tracebacks ────────
        let claims = ClaimBuilder::build_claims(graph, &self.claimed_edge_ids);

        let timeline = BehaviorTimeline {
            steps: self.timeline.clone(),
            total_duration_ms: if let (Some(first), Some(last)) =
                (self.timeline.first(), self.timeline.last())
            {
                (last.timestamp_ns.saturating_sub(first.timestamp_ns) / 1_000_000)
                    .min(u64::MAX)
            } else {
                0
            },
        };

        let fallback_agent = self
            .identity_resolver
            .provisional_identities()
            .first()
            .map(|p| p.agent_id.clone());

        let has_graph_agent = graph
            .all_nodes()
            .any(|n| n.node_type == NewNodeType::Agent);
        let can_identify_agent = fallback_agent.is_some() || has_graph_agent || !claims.is_empty();

        if can_identify_agent {
            let alert = AlertBuilder::compose(
                &claims,
                &classification_result,
                None, // Phase 2 never uses observer
                graph,
                &timeline,
                fallback_agent.as_ref(),
            );

            // Persist claims FIRST (traceback has FOREIGN KEY to claim)
            for claim in &claims {
                let _ = self
                    .persistence
                    .write(PersistRecord::Claim(claim.clone()));
            }

            // Build and persist tracebacks AFTER claims
            for claim in &claims {
                if let Some(traceback) = TracebackBuilder::build(claim, graph) {
                    let _ = self
                        .persistence
                        .write(PersistRecord::Traceback(traceback));
                }
            }
            let _ = self
                .persistence
                .write(PersistRecord::Alert(alert.clone()));

            // Broadcast final authoritative alert
            let phase2_alert_id = alert.alert_id.clone();
            let _ = self.bus.emit_alert(alert);

            // ── Persist Phase1→Phase2 refinements ──────────────────────
            for p1_id in self.phase1_alert_ids.drain(..) {
                let _ = self.persistence.write(PersistRecord::ClassificationRefinement(
                    crate::responsibility::types::refinement::ClassificationRefinement {
                        phase1_alert_id: p1_id,
                        phase2_alert_id: phase2_alert_id.clone(),
                        refinement_type: crate::responsibility::types::refinement::ClassificationRefinementType::Dismissed,
                        reason: "Phase 2 authoritative re-evaluation".into(),
                        score_delta: 0.0,
                    }));
            }
            for c1_id in self.phase1_claim_ids.drain() {
                let _ = self.persistence.write(PersistRecord::ClaimRefinement(
                    crate::responsibility::types::refinement::ClaimRefinement {
                        phase1_claim_id: c1_id,
                        phase2_claim_id: ClaimId::from("phase2-authoritative"),
                        refinement_type: crate::responsibility::types::refinement::ClaimRefinementType::Dismissed,
                        reason: "Phase 2 authoritative re-evaluation".into(),
                    }));
            }
        } // can_identify_agent

        // ── Build and emit structured evaluation summary ──────────
        let summary = EvaluationSummary {
            session_id: uuid::Uuid::new_v4().to_string(),
            run_duration_ms: 0,
            total_events: self.event_count as u64,
            total_intents: all_intents.len() as u64,
            total_actions: all_actions.len() as u64,
            pbc_edge_count: graph.edges_of_type(&NewEdgeType::PolicyBoundaryCrossing).len() as u64,
            claim_count: claims.len() as u64,
            alert_count: self.phase1_alert_ids.len() as u64,
            observer_calls: self.observer_calls as u64,
            classification_route: classification_result.route.clone(),
            final_score: classification_result.score,
            per_agent_scores: self.progressive_scorer.agents.iter().map(|(aid, state)| {
                crate::responsibility::output::AgentScoreSummary {
                    agent_id: aid.to_string(),
                    fused_score: self.progressive_scorer.get_agent_fused_score(aid).unwrap_or(0.0),
                    dimension_scores: [
                        state.dims[0].ewma, state.dims[1].ewma,
                        state.dims[2].ewma, state.dims[3].ewma,
                        state.dims[4].ewma, state.dims[5].ewma,
                    ],
                    cusum_triggered: state.dims.iter().any(|d|
                        d.cusum > self.progressive_scorer.config.cusum_h
                            && d.consecutive >= self.progressive_scorer.config.consecutive_k),
                    evidence_conflict: state.dims[DimIndex::D1Boundary as usize].ewma > 0.5
                        && state.dims[DimIndex::D5CrossContext as usize].ewma < 0.2,
                    baseline_established: self.baseline_tracker.get(aid.as_str())
                        .map(|b| b.is_established()).unwrap_or(false),
                }
            }).collect(),
            alerts: vec![],
        };
        let writer = OutputWriter::new(OutputFormat::Text);
        eprintln!("{}", writer.format_summary(&summary));

        // Also emit as JSON for structured consumers
        let json_writer = OutputWriter::new(OutputFormat::Json);
        eprintln!("[evaluation:json] {}", json_writer.format_summary(&summary));

        // Flush all pending persistence writes
        let _ = self.persistence.flush().await;
    }
}

// ─── Helpers ─────────────────────────────────────────────────────────

/// Check whether a classification route is unsafe (for calibration labels).
fn route_is_unsafe(route: &ClassRoute) -> bool {
    match route {
        ClassRoute::DirectUnsafe(output) => output.decision.as_str() == "unsafe",
        ClassRoute::DirectSafe(output) => output.decision.as_str() == "unsafe",
        ClassRoute::ToObserver(_) => false,
    }
}

// ─── Time Helper ─────────────────────────────────────────────────────

/// Current time in nanoseconds since UNIX epoch.
fn current_time_ns() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos() as u64
}

// ─── Analyzer Trait Integration ────────────────────────────────────────

/// Implements the framework `Analyzer` trait so ResponsibilityRuntime can
/// be plugged into the AgentRunner global analyzer chain.
///
/// The `process()` method:
/// 1. Initializes the session (signal graph bootstrap + RunStart marker)
/// 2. Drains the input `EventStream`, routing each event through Phase 1
/// 3. Triggers debounced classification (every N events or on tick)
/// 4. Runs Phase 2 authoritative batch refinement when the stream ends
/// 5. Writes RunEnd + flushes persistence before returning
#[async_trait::async_trait]
impl Analyzer for ResponsibilityRuntime {
    fn name(&self) -> &str {
        "responsibility_runtime"
    }

    async fn process(&mut self, input: EventStream) -> Result<EventStream, AnalyzerError> {
        let run_id = self.init_session();
        let mut output = Vec::new();
        let mut input = Box::pin(input);
        let mut tick =
            tokio::time::interval(std::time::Duration::from_millis(self.config.debounce_ms));
        tick.tick().await; // suppress immediate first tick

        // Poll interval for shutdown check (every 500ms)
        let mut shutdown_poll =
            tokio::time::interval(std::time::Duration::from_millis(500));

        loop {
            tokio::select! {
                event_opt = input.next() => {
                    match event_opt {
                        Some(event) => {
                            let emitted = self.route_event(event);
                            output.extend(emitted);
                            self.event_count += 1;
                            self.dirty = true;
                            if self.event_count % self.config.trigger_every_n == 0 {
                                self.trigger().await;
                            }
                        }
                        None => {
                            if self.dirty {
                                self.trigger().await;
                            }
                            self.finalize().await;
                            let _ = self.persistence.write(PersistRecord::RunEnd {
                                run_id: run_id.clone(),
                                timestamp_ns: current_time_ns(),
                            });
                            let _ = self.persistence.flush().await;
                            break;
                        }
                    }
                }
                _ = tick.tick() => {
                    if self.dirty {
                        self.trigger().await;
                    }
                }
                _ = shutdown_poll.tick() => {
                    if RUNTIME_SHUTDOWN.load(Ordering::Relaxed) {
                        if self.dirty {
                            self.trigger().await;
                        }
                        self.finalize().await;
                        let _ = self.persistence.write(PersistRecord::RunEnd {
                            run_id: run_id.clone(),
                            timestamp_ns: current_time_ns(),
                        });
                        let _ = self.persistence.flush().await;
                        break;
                    }
                }
            }
        }

        Ok(Box::pin(futures::stream::iter(output)))
    }
}
