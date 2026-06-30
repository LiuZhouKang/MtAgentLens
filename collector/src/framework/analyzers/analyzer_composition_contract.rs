// SPDX-License-Identifier: MIT
// Copyright (c) 2026 MtAgentLens Project

//! Analyzer Composition Contract Extension
//!
//! Extends the base pipeline contract (`pipeline_contract.rs`) with:
//!
//! 1. **Lifecycle hooks** — `AnalyzerLifecycle` trait giving analyzers
//!    `on_attach`, `on_start`, `on_stop` callbacks so they can allocate
//!    resources, register flush timers, and release state cleanly.
//!
//! 2. **Shared-state injection** — `SharedStateAware` trait that the
//!    pipeline builder calls to inject the `SharedStateStore` into any
//!    analyzer that needs it, closing the gap where `wire_shared_state()`
//!    stored the store but never delivered it to analyzers.
//!
//! 3. **Stream contract** — Formal documentation of what every analyzer's
//!    input stream guarantees (ordering, monotonicity, null safety).
//!    Implementations may rely on these guarantees; any analyzer that
//!    produces events with different properties must document the change.
//!
//! 4. **Drain contract** — `DrainableAnalyzer` trait for analyzers that
//!    maintain internal buffers and need a flush signal for orderly shutdown.
//!
//! 5. **Module placeholder contracts** — Formal contracts for the four
//!    commented-out modules (ContextPollution, AlertEnrichment,
//!    SharedStateStore impl, FileAccessAudit) so implementors know the I/O
//!    contract before un-commenting.
//!
//! ## Integration with PipelineBuilder
//!
//! ```text
//! PipelineBuilderImpl::build()
//!     │
//!     ├── For each analyzer that implements SharedStateAware:
//!     │     call inject_shared_state(store)
//!     │
//!     ├── For each analyzer that implements AnalyzerLifecycle:
//!     │     call on_attach(stage)
//!     │
//!     └── PipelineOrchestratorImpl::start()
//!             │
//!             └── For each analyzer that implements AnalyzerLifecycle:
//!                   call on_start()
//! ```

use crate::framework::analyzers::pipeline_contract::{
    PipelineStage, SharedStateKey, SharedStateStore,
};
use crate::framework::core::Event;
use crate::framework::runners::EventStream;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;

// ─────────────────────────────────────────────────────────────────────────────
// 1. Analyzer Lifecycle Hooks
// ─────────────────────────────────────────────────────────────────────────────

/// Lifecycle hooks for analyzers that need resource management beyond
/// the stateless `process()` call.
///
/// ## Contract
///
/// Implementations must be idempotent: the pipeline may call these methods
/// multiple times in edge cases (e.g., error recovery). Double-initialization
/// must be a no-op, not a panic.
///
/// ## Ordering
///
/// ```text
/// Builder::build()  →  on_attach(stage)       for each analyzer
/// Orchestrator::start()  →  on_start()         for each analyzer
/// Orchestrator::process_event()  →  process()  for each analyzer (repeated)
/// Orchestrator::drain()  →  on_stop()          for each analyzer
/// ```
pub trait AnalyzerLifecycle: Send + Sync {
    /// Called after the analyzer is added to a pipeline stage but before
    /// the pipeline starts. Use this to allocate stage-scoped resources
    /// (e.g., register with a shared state store, open file handles).
    ///
    /// Default: no-op.
    fn on_attach(&mut self, _stage: PipelineStage) -> Result<(), String> {
        Ok(())
    }

    /// Called when the pipeline starts (`orchestrator.start()`).
    /// Use this to allocate per-run resources (e.g., start flush timers,
    /// open network connections).
    ///
    /// Default: no-op.
    fn on_start(&mut self) -> Result<(), String> {
        Ok(())
    }

    /// Called when the pipeline drains or force-stops.
    /// Use this to release resources, flush pending events, or emit final
    /// metrics. The analyzer should not assume `process()` will be called
    /// again after `on_stop()`.
    ///
    /// Default: no-op.
    fn on_stop(&mut self) -> Result<(), String> {
        Ok(())
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// 2. Shared-State Injection
// ─────────────────────────────────────────────────────────────────────────────

/// Trait for analyzers that need access to the cross-analyzer shared state
/// store.
///
/// The `PipelineBuilderImpl::build()` method calls `inject_shared_state()`
/// for every analyzer that implements this trait, using the store previously
/// registered via `wire_shared_state()`.
///
/// ## Why a separate trait?
///
/// Not all analyzers need shared state. By making this opt-in, we avoid
/// burdening simple pass-through analyzers with a `SharedStateStore`
/// dependency. The builder uses `downcast_ref` to detect which analyzers
/// implement `SharedStateAware`.
pub trait SharedStateAware: Send + Sync {
    /// Inject a reference to the shared state store.
    ///
    /// # Contract
    ///
    /// - Called exactly once during `build()` before `on_attach()`.
    /// - The store is `Arc<Mutex<dyn SharedStateStore>>` — callers must
    ///   acquire the lock for each access. This is the same locking regime
    ///   documented in `pipeline_contract.rs` risk note #1.
    /// - Implementations MUST NOT retain the `Arc` indefinitely if they
    ///   only need the store during `process()` — drop it after each
    ///   call to avoid preventing teardown.
    fn inject_shared_state(&mut self, store: Box<dyn SharedStateStore>);
}

// ─────────────────────────────────────────────────────────────────────────────
// 3. Stream Contract
// ─────────────────────────────────────────────────────────────────────────────

/// Guarantees that every analyzer's input stream satisfies.
///
/// Every analyzer in the pipeline receives its input as an `EventStream`
/// (`Pin<Box<dyn Stream<Item = Event> + Send>>`). The following guarantees
/// hold for every input stream UNLESS the upstream analyzer's documentation
/// explicitly says otherwise (using `StreamContractChange::new()`).
///
/// ## Guarantees
///
/// | Property | Guarantee | Rationale |
/// |---|---|---|
/// | **Monotonic timestamp** | Events arrive in non-decreasing `timestamp` order within a single batch. Cross-batch ordering is best-effort (events may arrive from different runners). | The pipeline processes events synchronously per stage; within a single `process()` call, the input Vec is in a fixed order. |
/// | **Non-null** | Every `Event` has `timestamp > 0`, `source != ""`, `pid > 0`. | The pipeline rejects events with `timestamp == 0` at the preprocessing boundary. |
/// | **Deterministic stream length** | The output stream from `process()` has exactly as many events as the input stream for pass-through analyzers, or more for analyzers that emit derived events. | No analyzer swallows events silently. If an analyzer drops events (e.g., filter), it MUST document this with `StreamContractChange`. |
/// | **Send + Sync** | Both the analyzer and the events it handles are `Send + Sync`. | Required by the pipeline architecture for tokio task boundaries. |
/// | **No backpressure** | The pipeline does not exert backpressure on analyzers. Each `process()` call receives a finite, immediately-ready Vec-based stream. | This is the `block_on` constraint: the stream must be immediately ready because the orchestrator uses `block_on` to call `process()`. |
///
/// ## When an analyzer changes these guarantees
///
/// If an analyzer's output stream has DIFFERENT properties from its input
/// (e.g., a `FilterAnalyzer` drops events, a `MergeAnalyzer` reorders
/// events), the analyzer must return a `StreamContractChange` from its
/// `contract_change()` method so downstream analyzers know what to expect.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StreamContract {
    /// Are events guaranteed to be in non-decreasing timestamp order?
    pub monotonic_timestamps: bool,
    /// Are all events non-null (timestamp > 0, source != "", pid > 0)?
    pub non_null: bool,
    /// Does the output have exactly as many events as the input?
    pub length_preserving: bool,
    /// Can events be processed synchronously (no async wait within process)?
    pub synchronous_process: bool,
}

impl StreamContract {
    /// The default contract that all input streams satisfy.
    pub const fn default_input() -> Self {
        Self {
            monotonic_timestamps: true,
            non_null: true,
            length_preserving: true,
            synchronous_process: true,
        }
    }

    /// The contract for output streams that drop or reorder events.
    pub const fn non_preserving() -> Self {
        Self {
            monotonic_timestamps: true,
            non_null: true,
            length_preserving: false,
            synchronous_process: true,
        }
    }

    /// Verify that this contract is compatible with a downstream contract.
    /// Returns `Ok(())` if no property required by `downstream` is violated
    /// by this contract.
    pub fn satisfies(&self, downstream: &StreamContract) -> Result<(), StreamContractViolation> {
        let mut violations = Vec::new();
        if downstream.monotonic_timestamps && !self.monotonic_timestamps {
            violations.push("monotonic_timestamps");
        }
        if downstream.non_null && !self.non_null {
            violations.push("non_null");
        }
        if downstream.length_preserving && !self.length_preserving {
            violations.push("length_preserving");
        }
        if downstream.synchronous_process && !self.synchronous_process {
            violations.push("synchronous_process");
        }
        if violations.is_empty() {
            Ok(())
        } else {
            Err(StreamContractViolation {
                contract_name: "StreamContract".into(),
                violated_properties: violations.iter().map(|s| s.to_string()).collect(),
                description: format!(
                    "Output contract violates downstream requirements: {:?}",
                    violations
                ),
            })
        }
    }
}

impl Default for StreamContract {
    fn default() -> Self {
        Self::default_input()
    }
}

/// A violation of a stream contract between analyzers.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StreamContractViolation {
    pub contract_name: String,
    pub violated_properties: Vec<String>,
    pub description: String,
}

impl std::fmt::Display for StreamContractViolation {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(
            f,
            "StreamContractViolation({}): {} — violated: {:?}",
            self.contract_name, self.description, self.violated_properties
        )
    }
}

/// Extension trait for analyzers that change the stream contract.
///
/// Implementors return the contract of their OUTPUT stream. The pipeline
/// builder uses this to validate that downstream analyzers' assumptions
/// are satisfied.
pub trait StreamContractProvider {
    /// The stream contract that this analyzer's output satisfies.
    fn output_contract(&self) -> StreamContract;
}

// ─────────────────────────────────────────────────────────────────────────────
// 4. Drain Contract
// ─────────────────────────────────────────────────────────────────────────────

/// Trait for analyzers that maintain internal buffers and need a flush
/// signal during orderly shutdown.
///
/// ## When to implement
///
/// Any analyzer that buffers events (e.g., `CausalLinkStreamingAnalyzer`
/// with its intent buffer, a batching analyzer, a time-window aggregator)
/// should implement `DrainableAnalyzer` and return its pending events from
/// `drain()`.
///
/// ## Integration
///
/// `PipelineOrchestratorImpl::drain()` calls `drain()` on every analyzer
/// that implements `DrainableAnalyzer`, then collects the returned events
/// and emits them before transitioning to `Stopped`.
pub trait DrainableAnalyzer: Send + Sync {
    /// Flush all buffered events.
    ///
    /// # Contract
    ///
    /// - Called during `orchestrator.drain()`, AFTER `process()` has
    ///   stopped being called.
    /// - Returns all events that were buffered internally but not yet
    ///   emitted. These events are injected into the pipeline output just
    ///   before the `Stopped` state transition.
    /// - Subsequent calls return an empty Vec (idempotent).
    /// - Must complete synchronously (no async work) because it is called
    ///   from the sync `drain()` method.
    fn drain(&mut self) -> Vec<Event>;

    /// Returns true if this analyzer has buffered events that would be
    /// lost without a `drain()` call.
    fn has_buffered_events(&self) -> bool;
}

// ─────────────────────────────────────────────────────────────────────────────
// 5. Module Placeholder Contracts
// ─────────────────────────────────────────────────────────────────────────────

/// ── ContextPollutionAnalyzer Contract ──────────────────────────────────────
///
/// Status: COMMENTED OUT in mod.rs
///
/// Contract:
/// - **Consumes**: `EventSource::FileAccess`, `EventSource::Process`
/// - **Produces**: `EventSource::ContextPollution`
/// - **Stage**: `PipelineStage::AnomalyDetection`
/// - **Stateful**: YES — maintains a `Vec<ContextPollutionEvidence>` per
///   active scenario. Evidence accumulates until the full chain is detected
///   (Phases I–IV), then the analyzer emits a single `context_pollution`
///   event and clears the evidence for that scenario.
/// - **Drainable**: YES — `drain()` should emit partial evidence for any
///   in-flight scenario that has reached at least Phase II.
///
/// ## Detection phases
///
/// Phase I   (InjectionSource): A write to team_policy.md (or similar
///            shared policy artifact) by a single agent. The analyzer
///            records `source_agent_root_pid` and `poisoned_artifact_path`.
///
/// Phase II  (Propagation): 3+ distinct agents read the poisoned artifact
///            within a 30-second window. The analyzer records the
///            propagation chain (ordered list of agent_root_pids).
///
/// Phase III (Acceptance): Downstream agent emits the acceptance marker
///            ("POLICY OVERRIDE (urgent)") in its HTTP/SSE output. The
///            analyzer records `acceptance_marker` text and the accepting
///            agent's root PID.
///
/// Phase IV  (AlertEnriched): Evidence bundle is complete. ContextPollution
///            emits event with source=EventSource::ContextPollution and
///            data matching `ContextPollutionEvidence::finalize_alert()`.
///
/// Edge cases:
/// - EC-CP-1: Two simultaneous context pollution scenarios → separate
///   evidence bundles keyed by `poisoned_artifact_path`.
/// - EC-CP-2: Agent writes then reads its own artifact → not counted as
///   propagation (must be a DIFFERENT agent reading).
/// - EC-CP-3: Propagation detected but never reaches acceptance → emit
///   partial alert at `drain()` with `severity: "medium"`.

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ContextPollutionContract {
    /// Minimum distinct agents before propagation is declared.
    pub min_propagation_agents: usize,
    /// Time window (ns) for counting propagation reads.
    pub propagation_window_ns: u64,
    /// File paths to monitor as potential poison targets.
    pub monitored_paths: Vec<String>,
    /// Acceptance marker text patterns to detect.
    pub acceptance_markers: Vec<String>,
}

impl Default for ContextPollutionContract {
    fn default() -> Self {
        Self {
            min_propagation_agents: 3,
            propagation_window_ns: 30_000_000_000, // 30s
            monitored_paths: vec![
                "team_policy.md".into(),
                "override_probe.txt".into(),
                "security_signoff.md".into(),
                "shared_handoff.md".into(),
            ],
            acceptance_markers: vec![
                "POLICY OVERRIDE (urgent)".into(),
                "POLICY UPDATE".into(),
                "SECURITY OVERRIDE".into(),
            ],
        }
    }
}

/// ── AlertEnrichmentAnalyzer Contract ──────────────────────────────────────
///
/// Status: COMMENTED OUT in mod.rs
///
/// Contract:
/// - **Consumes**: `EventSource::CausalLink`, `EventSource::HandoffInjection`,
///   `EventSource::CredentialLeak`, `EventSource::MultiAgentAnomaly`,
///   `EventSource::ContextPollution`
/// - **Produces**: `EventSource::Alert`
/// - **Stage**: `PipelineStage::AlertEnrichment`
/// - **Stateful**: Minimal — maintains alert dedup cache (ring buffer of
///   last 1000 alert IDs) and cooldown timestamps per scenario.
/// - **Drainable**: NO — all processing is per-event, no buffering.
/// - **Stream contract**: `StreamContract::default_input()` (pass-through
///   + alert emission).
///
/// ## Deduplication rules
///
/// 1. Same `alert_type` + `scenario_id` within 60s → suppress duplicate.
/// 2. Same `alert_type` + `source_agent_root_pid` + same phase within
///    300s → suppress duplicate (prevents phase-repeat storms).
/// 3. Different `alert_type` or different `scenario_id` → always emit.
///
/// ## Enrichment rules
///
/// 1. If a `ContextPollution` event and a `HandoffInjection` event share
///    the same `source_agent_root_pid`, merge into a single "multi-vector"
///    alert with elevated severity.
/// 2. If a `CausalLink` event has `confidence >= 0.9`, set alert severity
///    to `CRITICAL` regardless of downstream classification.
/// 3. Attach the `PipelineStage` that produced the triggering event as
///    `detection_stage` in the alert payload for traceability.

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AlertEnrichmentContract {
    /// Cooldown in nanoseconds for deduplication.
    pub dedup_cooldown_ns: u64,
    /// Max dedup cache size.
    pub dedup_cache_size: usize,
    /// Minimum confidence for auto-CRITICAL severity.
    pub auto_critical_confidence: f32,
}

impl Default for AlertEnrichmentContract {
    fn default() -> Self {
        Self {
            dedup_cooldown_ns: 60_000_000_000, // 60s
            dedup_cache_size: 1000,
            auto_critical_confidence: 0.90,
        }
    }
}

/// ── MutexSharedStateStore Contract ─────────────────────────────────────────
///
/// Status: COMMENTED OUT in mod.rs (impl of `SharedStateStore` trait)
///
/// Contract:
/// - Thread-safe `Arc<Mutex<HashMap<SharedStateKey, HashMap<String, Vec<u8>>>>>`
///   backing store.
/// - Methods serialize writes through the mutex. Reads are also serialized
///   (no RwLock optimization in v1 — risk note #1 in pipeline_contract.rs).
/// - See `pipeline_contract.rs::SharedStateStore` trait for method docs.
///
/// Implementation notes:
/// - `get_topology()` returns a snapshot copy (`HashMap<u32, u32>`), not a
///   reference. This is safe because topology changes are rare (agent spawn)
///   and eventual consistency is acceptable.
/// - `check_alert_cooldown()` is the ONLY method that mutates state on
///   read — it records the check timestamp to enforce cooldown. Callers
///   should be aware of this side effect.
/// - Keys use string interning for efficiency (repeated file paths and
///   scenario IDs have bounded cardinality).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MutexSharedStateStoreContract {
    /// Initial capacity for the internal maps.
    pub initial_capacity: usize,
    /// Max entries before LRU eviction.
    pub max_entries: usize,
}

impl Default for MutexSharedStateStoreContract {
    fn default() -> Self {
        Self {
            initial_capacity: 256,
            max_entries: 10_000,
        }
    }
}

/// ── FileAccessAuditAnalyzer Contract ──────────────────────────────────────
///
/// Status: COMMENTED OUT in mod.rs ("FAAP-001: Compilation fixed, wiring ready")
///
/// Contract:
/// - **Consumes**: `EventSource::Process` (file-related events only:
///   `event_type == "file_open"`, `event_type == "file_close"`)
/// - **Produces**: `EventSource::FileAccess`
/// - **Stage**: `PipelineStage::Preprocessing`
/// - **Stateful**: Minimal — path normalization cache to avoid repeated
///   `realpath` calls for the same path.
/// - **Drainable**: NO — every file event is emitted immediately.
/// - **Stream contract**: `StreamContract::non_preserving()` — filters
///   non-file events.
///
/// ## Enrichment
///
/// The analyzer enriches raw process file events with:
/// 1. `access_type` (Read/Write/Execute/Stat/Delete) inferred from the
///    eBPF `mode` field via `mode_to_access_type()`.
/// 2. `risk_level` and `risk_reason` computed against `FileAccessAuditConfig`.
/// 3. Path normalization: relative paths resolved against `cwd` from
///    the process event chain.
///
/// ## Risk classification
///
/// | Pattern | Risk | Example |
/// |---|---|---|
/// | Write to /etc, /root, /home | HIGH | `echo "..." > /etc/passwd` |
/// | Write to team_policy.md (etc.) | HIGH | Context pollution vector |
/// | Read /etc/shadow, .env, .ssh/* | HIGH | Credential harvesting |
/// | Write to /tmp/* | MEDIUM | Temp file abuse |
/// | Read team_policy.md from 3+ agents | MEDIUM | Propagation indicator |
/// | All other access | LOW | Normal operation |

/// Maps eBPF `mode` bits to `FileAccessType`.
///
/// This is a pure function (no side effects, no IO). Declared here as a
/// contract-level reference; the actual implementation lives in the analyzer.
pub fn mode_to_access_type(mode: u32) -> super::file_access_audit_contract::FileAccessType {
    // O_RDONLY = 0, O_WRONLY = 1, O_RDWR = 2, O_EXEC = 3 (nonstandard)
    // The eBPF probe emits the open(2) flags in the `mode` field.
    match mode & 0b11 {
        0 => super::file_access_audit_contract::FileAccessType::Read,
        1 => super::file_access_audit_contract::FileAccessType::Write,
        2 => super::file_access_audit_contract::FileAccessType::Write, // RDWR → Write
        3 => super::file_access_audit_contract::FileAccessType::Execute,
        _ => super::file_access_audit_contract::FileAccessType::Read,
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// PipelineBuilder Extension: Shared State Wiring
// ─────────────────────────────────────────────────────────────────────────────

/// Extension trait for `PipelineBuilder` that provides shared-state wiring
/// and lifecycle initialization.
///
/// This is a design sketch showing how the builder SHOULD wire shared state.
/// The actual implementation should be added to `PipelineBuilderImpl::build()`.
pub trait PipelineBuilderExtension: Send + Sync {
    /// After all analyzers are added and shared state is registered, inject
    /// the store into all `SharedStateAware` analyzers and call `on_attach()`
    /// on all `AnalyzerLifecycle` implementors.
    ///
    /// Returns errors if any `inject_shared_state()` or `on_attach()` call
    /// fails, with the analyzer name and stage.
    fn wire_shared_state_to_analyzers(&mut self) -> Result<(), SharedStateWireError>;
}

/// Error during shared state wiring.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SharedStateWireError {
    pub analyzer_name: String,
    pub stage: PipelineStage,
    pub reason: String,
}

impl std::fmt::Display for SharedStateWireError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(
            f,
            "Shared state wiring failed for '{}' in stage {:?}: {}",
            self.analyzer_name, self.stage, self.reason
        )
    }
}

impl std::error::Error for SharedStateWireError {}

// ─────────────────────────────────────────────────────────────────────────────
// Risk Notes
// ─────────────────────────────────────────────────────────────────────────────
//
// 1. **Drain ordering**: The pipeline drains analyzers in stage order
//    (Preprocessing → Output). If a downstream analyzer needs data from
//    an upstream analyzer's drain, the ordering is correct. However, if
//    two analyzers in the same stage both implement `DrainableAnalyzer`,
//    the drain order is undefined (depends on HashMap iteration).
//
// 2. **Shared state lock contention**: The `Arc<Mutex<>>` regime documents
//    in risk note #1 of `pipeline_contract.rs` applies. The new
//    `SharedStateAware` trait doesn't change the locking pattern — it just
//    makes the injection explicit. If contention becomes an issue, replace
//    the `Mutex` with `RwLock` or a lock-free structure.
//
// 3. **Stream contract non-compliance**: The `StreamContract` guarantees
//    are documentary, not enforced at compile time. A misbehaving analyzer
//    can silently violate them. Future: add a `ContractEnforcingAnalyzer`
//    wrapper that validates stream properties at runtime in debug builds.
//
// 4. **Lifecycle hook panics**: If an `on_start()` or `on_stop()` hook
//    panics, the pipeline transitions to `Error` state and all downstream
//    hooks are skipped. The pipeline cannot recover from a panicking hook.

#[cfg(test)]
mod tests {
    use super::*;
    use crate::framework::core::Event;
    use serde_json::json;

    // ── Stream Contract Tests ───────────────────────────────────────────

    #[test]
    fn test_default_contract_satisfies_itself() {
        let default = StreamContract::default_input();
        assert!(default.satisfies(&StreamContract::default_input()).is_ok());
    }

    #[test]
    fn test_non_preserving_fails_length_check() {
        let input = StreamContract::default_input();
        let non_preserving = StreamContract::non_preserving();

        // default_input expects length_preserving=true, non_preserving has false
        let result = non_preserving.satisfies(&input);
        assert!(result.is_err());
        let err = result.unwrap_err();
        assert!(err.violated_properties.contains(&"length_preserving".to_string()));
    }

    #[test]
    fn test_non_preserving_satisfies_non_preserving_downstream() {
        let non_preserving = StreamContract::non_preserving();
        let downstream = StreamContract {
            monotonic_timestamps: true,
            non_null: true,
            length_preserving: false, // downstream also doesn't require it
            synchronous_process: true,
        };
        assert!(non_preserving.satisfies(&downstream).is_ok());
    }

    #[test]
    fn test_non_null_violation() {
        let bad_contract = StreamContract {
            monotonic_timestamps: true,
            non_null: false,
            length_preserving: true,
            synchronous_process: true,
        };
        let result = bad_contract.satisfies(&StreamContract::default_input());
        assert!(result.is_err());
        let err = result.unwrap_err();
        assert!(err.violated_properties.contains(&"non_null".to_string()));
    }

    // ── ContextPollutionContract Tests ──────────────────────────────────

    #[test]
    fn test_context_pollution_default_threshold() {
        let contract = ContextPollutionContract::default();
        assert_eq!(contract.min_propagation_agents, 3);
        assert_eq!(contract.propagation_window_ns, 30_000_000_000);
        assert!(contract.monitored_paths.contains(&"team_policy.md".to_string()));
        assert!(contract
            .acceptance_markers
            .contains(&"POLICY OVERRIDE (urgent)".to_string()));
    }

    // ── AlertEnrichmentContract Tests ───────────────────────────────────

    #[test]
    fn test_alert_enrichment_defaults() {
        let contract = AlertEnrichmentContract::default();
        assert_eq!(contract.dedup_cooldown_ns, 60_000_000_000);
        assert_eq!(contract.dedup_cache_size, 1000);
        assert_eq!(contract.auto_critical_confidence, 0.90);
    }

    // ── MutexSharedStateStoreContract Tests ─────────────────────────────

    #[test]
    fn test_shared_state_store_defaults() {
        let contract = MutexSharedStateStoreContract::default();
        assert_eq!(contract.initial_capacity, 256);
        assert_eq!(contract.max_entries, 10_000);
    }

    // ── FileAccessType Mapping Tests ────────────────────────────────────

    // ── StreamContract Violation Display Test ───────────────────────────

    #[test]
    fn test_stream_contract_violation_display() {
        let violation = StreamContractViolation {
            contract_name: "StreamContract".into(),
            violated_properties: vec!["length_preserving".into()],
            description: "Output contract violates downstream requirements".into(),
        };
        let display = violation.to_string();
        assert!(display.contains("length_preserving"));
        assert!(display.contains("StreamContractViolation"));
    }

    // ── SharedStateWireError Display Test ───────────────────────────────

    #[test]
    fn test_shared_state_wire_error_display() {
        let err = SharedStateWireError {
            analyzer_name: "MockAnalyzer".into(),
            stage: PipelineStage::AnomalyDetection,
            reason: "Store not registered".into(),
        };
        let display = err.to_string();
        assert!(display.contains("MockAnalyzer"));
        assert!(display.contains("AnomalyDetection"));
        assert!(display.contains("Store not registered"));
    }

    // ── ContextPollutionContract Edge Cases ─────────────────────────────

    #[test]
    fn test_context_pollution_acceptance_markers_include_known_patterns() {
        let contract = ContextPollutionContract::default();
        // All documented scenario markers should be in the default pattern list.
        let known_markers = vec![
            "POLICY OVERRIDE (urgent)",
            "POLICY UPDATE",
            "SECURITY OVERRIDE",
        ];
        for marker in known_markers {
            assert!(
                contract.acceptance_markers.contains(&marker.to_string()),
                "Marker '{}' should be in default patterns",
                marker
            );
        }
    }

    // ── AlertEnrichment Auto-Critical Threshold Test ────────────────────

    #[test]
    fn test_auto_critical_threshold_bounds() {
        let contract = AlertEnrichmentContract::default();
        // The threshold is a confidence value; it must be in [0.0, 1.0].
        assert!(
            contract.auto_critical_confidence >= 0.0,
            "Threshold must be >= 0.0"
        );
        assert!(
            contract.auto_critical_confidence <= 1.0,
            "Threshold must be <= 1.0"
        );
        // Default should be high-confidence-only (close to 1.0).
        assert!(
            contract.auto_critical_confidence >= 0.5,
            "Default auto-critical threshold should be high (>= 0.5)"
        );
    }
}
