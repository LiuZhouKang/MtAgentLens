# Analyzer Pipeline Contract

> **Last verified against code**: 2026-06-03  
> **Wall-clock baseline** (`ls -la src/framework/` × 5): 0.149s / 0.013s / 0.329s / 0.031s / 0.089s — filesystem warm, no resource pressure detected.  
> **Corresponding sources**:
> - `analyzers/mod.rs` — `Analyzer` trait definition (31 modules)
> - `analyzers/pipeline_contract.rs` — `PipelineStage`, `EventSource`, `StageContract`, `PipelineOrchestrator`, `SharedStateStore`, `ContextPollutionEvidence`
> - `analyzers/pipeline_orchestrator.rs` — `PipelineOrchestratorImpl` + `PipelineBuilderImpl` (concrete impl)
> - `analyzers/analyzer_composition_contract.rs` — `AnalyzerLifecycle`, `SharedStateAware`, `StreamContract`, `DrainableAnalyzer`, module placeholder contracts
> - ~~`analyzers/shared_state_store.rs`~~ — deleted 2026-10-16 (zero implementers, zero
>   callers). The `SharedStateStore` *trait* below still exists; its fate belongs to the
>   batched decision on the unwired pipeline contract.
> - `core/events.rs` — `Event` struct

---

## 1. Overview

This contract defines the interface, data flow, composition rules, and lifecycle for chaining analyzers in the MtAgentLens framework. It ensures type safety, predictable behavior, and composability across the 6-stage detection pipeline.

The pipeline follows a **Builder → Orchestrator** pattern: a `PipelineBuilder` accumulates analyzers per stage, validates stage contracts, then produces a `PipelineOrchestrator` that drives the event loop through a state machine.

---

## 2. Core Principles

1. **Source-based routing**: Analyzers consume and produce well-known `EventSource` values; downstream stages filter on these source tags.
2. **Fail-open on unknown events**: Analyzers MUST pass through events whose source they don't recognize.
3. **Deterministic stage ordering**: The 6 stages execute sequentially per event; no cross-stage feedback loops.
4. **Explicit state sharing**: Stateful analyzers access cross-analyzer state through `SharedStateStore` (opt-in via `SharedStateAware` trait), not through implicit globals.
5. **Lifecycle discipline**: Analyzers that allocate resources MUST implement `AnalyzerLifecycle` hooks (`on_attach` → `on_start` → `process` loop → `on_stop`).
6. **Contract validation at build time**: `PipelineBuilder::build()` validates that every downstream stage's `consumes` is satisfied by upstream `produces` before the orchestrator starts.

---

## 3. Event Contract

```rust
// src/framework/core/events.rs
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Event {
    pub timestamp: u64,           // nanoseconds since UNIX epoch
    pub source: String,           // canonical EventSource string (e.g. "ssl", "process", "alert")
    pub pid: u32,                 // originating process ID
    pub comm: String,             // command name
    pub data: serde_json::Value,  // opaque JSON payload
}
```

**Invariants (enforced by StreamContract):**
- `timestamp > 0` (events with `timestamp == 0` are rejected at the preprocessing boundary)
- `source != ""` — must match one of the `EventSource::as_str()` values
- `pid > 0`
- Events within a single `process()` call arrive in non-decreasing timestamp order (best-effort across batches)

**Serialization:**
- `to_json_value()` emits `timestamp` as epoch **milliseconds** for frontend compatibility
- `to_json_value()` also emits `timestamp_ns` preserving the canonical nanosecond value

---

## 4. Analyzer Interface

```rust
// src/framework/analyzers/mod.rs
#[async_trait]
pub trait Analyzer: Send + Sync {
    /// Process an event stream and return a processed stream.
    /// This is the ONLY required method. All other traits are optional extensions.
    async fn process(&mut self, stream: EventStream) -> Result<EventStream, AnalyzerError>;

    /// Human-readable name for logging/metrics.
    fn name(&self) -> &str;
}
```

**Key differences from naive design:**
- `async`: The method is async — analyzers may perform I/O (file logging, HTTP calls, LLM observer calls) within `process()`.
- `&mut self`: Stateful analyzers can mutate internal state. Stateless analyzers ignore this.
- Returns `Result<EventStream, AnalyzerError>`: Errors are propagated, not silently swallowed. The pipeline transitions to `Error` state on any analyzer failure (no per-stage timeout in v1).

**Extension traits (opt-in — implement only when needed):**

| Trait | File | Purpose |
|---|---|---|
| `AnalyzerLifecycle` | `analyzer_composition_contract.rs` | `on_attach(stage)`, `on_start()`, `on_stop()` resource management |
| `SharedStateAware` | `analyzer_composition_contract.rs` | `inject_shared_state(store)` for cross-analyzer state |
| `StreamContractProvider` | `analyzer_composition_contract.rs` | `output_contract()` declares stream property changes |
| `DrainableAnalyzer` | `analyzer_composition_contract.rs` | `drain()` flushes buffered events during shutdown |

---

## 5. Pipeline Architecture: Builder → Orchestrator

### 5.1 Pipeline Stages (`PipelineStage` enum)

```
Preprocessing (0) → CausalLinking (1) → InjectionDetection (2) → AnomalyDetection (3) → AlertEnrichment (4) → Output (5)
```

Each stage may contain **one or more analyzers** executed sequentially within the stage. The orchestrator runs through all 6 stages in order for every event.

**Prerequisites:**
- `CausalLinking`, `InjectionDetection`, `AnomalyDetection` all require `Preprocessing`
- `AlertEnrichment` requires `CausalLinking` + `InjectionDetection` + `AnomalyDetection`
- `Output` requires `AlertEnrichment`

### 5.2 EventSource Canonical Set

```rust
pub enum EventSource {
    // Raw eBPF sources (preprocessing input)
    Ssl, Process, System, Stdio,
    // Preprocessing outputs
    SseProcessor, HttpParser, TimestampNormalized, SslFiltered, HttpFiltered,
    // Causal link outputs
    CausalLink, NonLinkDiagnostic, CausalLinkMetrics,
    // Injection detection outputs
    HandoffInjection, CredentialLeak,
    // Anomaly detection outputs
    MultiAgentAnomaly, ContextPollution, SecuritySignoff,
    // Alert enrichment output
    Alert,
    // Special sources
    Prompt, FileAccess,
}
```

### 5.3 Stage Contracts (`StageContract`)

```rust
pub struct StageContract {
    pub stage: PipelineStage,
    pub consumes: HashSet<String>,    // event sources this stage reads
    pub produces: HashSet<String>,    // event sources this stage writes
    pub pass_through: bool,           // does this stage forward original events?
    pub stateless: bool,              // can this stage run without cross-event state?
}
```

The `satisfies()` method validates that an upstream stage's effective output (its `produces` ∪ pass-through `consumes`) contains everything the downstream stage needs.

### 5.4 Pipeline State Machine (`PipelineOrchestratorState`)

```
Initializing ──→ Running ──→ Draining ──→ Stopped
                   │  ↑                      ↑
                   └──→ Paused ──→───────────┘
                   │                        ↑
                   └──→ Error ──→───────────┘
```

Valid transitions:
- `Initializing → Running` (or → `Error`)
- `Running → Draining | Paused | Error`
- `Paused → Running | Error`
- `Draining → Stopped | Error`
- `Error → Stopped`

**Paused state behavior**: Events pass through unchanged — no detection processing occurs, but the pipeline doesn't lose events.

### 5.5 PipelineBuilder (`PipelineBuilder` trait)

```rust
pub trait PipelineBuilder: Send + Sync {
    fn add_preprocessor(&mut self, analyzer: Box<dyn Analyzer>) -> &mut Self;
    fn add_causal_link(&mut self, analyzer: Box<dyn Analyzer>) -> &mut Self;
    fn add_injection_detector(&mut self, analyzer: Box<dyn Analyzer>) -> &mut Self;
    fn add_anomaly_detector(&mut self, analyzer: Box<dyn Analyzer>) -> &mut Self;
    fn add_alert_enricher(&mut self, analyzer: Box<dyn Analyzer>) -> &mut Self;
    fn add_output(&mut self, analyzer: Box<dyn Analyzer>) -> &mut Self;
    fn wire_shared_state(&mut self, store: Box<dyn SharedStateStore>) -> &mut Self;
    fn build(&mut self) -> Result<Box<dyn PipelineOrchestrator>, PipelineBuildError>;
}
```

**build() validation:**
1. All 6 required stages must be configured
2. Every downstream `consumes` must be satisfied by upstream `produces`
3. Returns `PipelineBuildError { reason, missing_stages, unsatisfied_contracts }` on failure

### 5.6 PipelineOrchestrator (`PipelineOrchestrator` trait)

```rust
pub trait PipelineOrchestrator: Send + Sync {
    fn start(&mut self) -> Result<(), String>;
    fn process_event(&mut self, event: Event) -> Result<Vec<Event>, String>;
    fn drain(&mut self) -> Result<(), String>;
    fn force_stop(&mut self) -> Result<(), String>;
    fn pause(&mut self) -> Result<(), String>;
    fn resume(&mut self) -> Result<(), String>;
    fn state(&self) -> PipelineOrchestratorState;
    fn stage_metrics(&self) -> HashMap<PipelineStage, StageMetrics>;
}
```

**process_event() semantics**: Takes one input event, runs it through all 6 stages sequentially, returns all events produced (original pass-through + derived). If any analyzer returns an error, the pipeline transitions to `Error` and the error propagates to the caller.

**block_on constraint**: The orchestrator uses `tokio::runtime::Handle::block_on()` to call `analyzer.process()` from a sync context. This is safe because the input stream is immediately ready (constructed from a `Vec`). Downstream consequence: analyzers MUST NOT produce streams that block on external I/O — stream elements must be available synchronously.

---

## 6. Per-Stage Contract Details

### Stage 0: Preprocessing

| Property | Value |
|---|---|
| **Consumes** | `ssl`, `process`, `system`, `stdio` |
| **Produces** | `ssl`, `process`, `system`, `sse_processor`, `http_parser`, `file_access`, `prompt` |
| **Pass-through** | Yes |
| **Stateless** | Yes |

**Analyzers:**
- `TimestampNormalizer` — converts eBPF boot-time ns to UNIX epoch ms
- `SSEProcessor` — reassembles chunked SSE streams into complete messages
- `HTTPParser` — extracts HTTP request/response metadata from SSL payloads
- `AuthHeaderRemover` — redacts `Authorization`, `Cookie`, `X-Api-Key` headers
- `SSLFilter` — regex-based SSL event filter
- `HTTPFilter` — regex-based HTTP event filter
- `FileAccessAuditAnalyzer` — enriches file events with `access_type`, `risk_level`, `risk_reason`
- `ConnectionBuffer` — buffers raw connection data for reassembly

**Contract:**
- Events are never dropped — all pass-through
- All analyzers in this stage are stateless (no memory across `process()` calls)

### Stage 1: CausalLinking

| Property | Value |
|---|---|
| **Consumes** | `prompt`, `process` |
| **Produces** | `causal_link`, `non_link_diagnostic`, `causal_link_metrics` |
| **Pass-through** | Yes |
| **Stateless** | No (maintains intent/candidate buffers per session) |

**Note**: In the current codebase, causal link analysis has been moved to `ResponsibilityRuntime` routing. The `CausalLinkStreamingAnalyzer` module was removed. This stage is a placeholder for future re-integration.

### Stage 2: InjectionDetection

| Property | Value |
|---|---|
| **Consumes** | `process`, `http_parser`, `sse_processor` |
| **Produces** | `handoff_injection`, `credential_leak` (⚠️ `credential_leak` has no emitter — the analyzer was deleted 2026-10-16) |
| **Pass-through** | Yes |
| **Stateless** | No (maintains file-read history per agent) |

**Analyzers:**
- `HandoffInjectionAnalyzer` — tracks file-read → HTTP-output chains; detects shared-artifact injection patterns
- `CredentialLeakAnalyzer` — flags credential exfiltration in HTTP bodies

**Injection patterns (default):**
- File read → HTTP POST with matching content (direct handoff)
- File read → SSE output with matching content (streaming handoff)
- Environment variable access → HTTP header with credential token

### Stage 3: AnomalyDetection

| Property | Value |
|---|---|
| **Consumes** | `process`, `system`, `http_parser`, `sse_processor`, `handoff_injection` |
| **Produces** | `multi_agent_anomaly`, `security_signoff`, `context_pollution` (⚠️ `context_pollution` has no emitter — the analyzer was deleted 2026-10-16) |
| **Pass-through** | Yes |
| **Stateless** | No (maintains command history, cpu/mem streaks, sign-off lifecycle) |

**Analyzers:**
- `MultiAgentAnomalyAnalyzer` — detects cross-agent command loops, resource storms, team propagation
- `ContextPollutionAnalyzer` — CPDP-001: 4-phase context pollution detection (see §8)
- `AgentTeamsRoleHijackAnalyzer` — detects unauthorized role assumption across team agents
- `RoleHijackAnalyzer` — identity-based role hijack detection with signoff chain validation
- `SecuritySignoffAnalyzer` — tracks security sign-off state machine per agent
- `ConversationFlowAnalyzer` — detects abnormal conversation patterns (amplification, loops)
- `EventCorrelationAnalyzer` — cross-source event correlation
- `EventAggregator` — time-windowed event aggregation with configurable passthrough mode
- `LatencyAnalyzer` — per-stage and end-to-end latency tracking
- `SemanticObserverAnalyzer` — LLM-based semantic confidence enhancement (heuristic or sidecar)
- `ObservabilityMetricsAnalyzer` — pipeline observability metrics collection

### Stage 4: AlertEnrichment

| Property | Value |
|---|---|
| **Consumes** | `causal_link`, `handoff_injection`, `credential_leak`, `multi_agent_anomaly`, `context_pollution` (⚠️ the last two have no emitter) |
| **Produces** | `alert` |
| **Pass-through** | Yes |
| **Stateless** | Yes (pure transformation + dedup) |

**Analyzers:**
- `AlertEnrichmentAnalyzer` — deduplicates, enriches severity, merges multi-vector alerts

**Deduplication rules (from `AlertEnrichmentContract`):**
1. Same `alert_type` + `scenario_id` within 60s → suppress
2. Same `alert_type` + `source_agent_root_pid` + same phase within 300s → suppress
3. Different `alert_type` or different `scenario_id` → always emit

**Auto-critical rule**: If a `CausalLink` event has `confidence >= 0.90`, set severity to `CRITICAL`.

### Stage 5: Output

| Property | Value |
|---|---|
| **Consumes** | All sources (accepts everything) |
| **Produces** | (none — terminal stage) |
| **Pass-through** | No (terminal) |
| **Stateless** | Yes |

**Analyzers:**
- `FileLogger` — writes JSONL with file rotation
- `OutputAnalyzer` — emits SSE to frontend; console output

---

## 7. Cross-Analyzer Shared State

### SharedStateStore trait

```rust
pub trait SharedStateStore: Send + Sync {
    fn get_topology(&self) -> HashMap<u32, u32>;
    fn get_recent_file_reads(&self, agent_root_pid: u32) -> Vec<(u64, String)>;
    fn get_shared_file_readers(&self, filepath: &str) -> Vec<u32>;
    fn get_recent_risky_prompts(&self, agent_root_pid: u32) -> Vec<PromptTriggerSnapshot>;
    fn record_file_read(&self, agent_root_pid: u32, filepath: &str, timestamp_ns: u64);
    fn record_shared_file_reader(&self, filepath: &str, agent_root_pid: u32);
    fn record_risky_prompt(&self, agent_root_pid: u32, trigger: PromptTriggerSnapshot);
    fn check_alert_cooldown(&self, dedupe_key: &str, now_ns: u64, cooldown_ns: u64) -> bool;
}
```

### Shared state keys (`SharedStateKey`)

| Key | Type | Purpose |
|---|---|---|
| `AgentTopology` | `pid → agent_root_pid` | EXEC topology discovery |
| `RecentFileReads` | `agent_root_pid → Vec<(ts, path)>` | File-read history per agent |
| `SharedFileReaders` | `path → Vec<agent_root_pid>` | Cross-agent propagation tracking |
| `RecentRiskyPrompts` | `agent_root_pid → Vec<PromptTrigger>` | Risky prompt history |
| `ActiveAlertIds` | `agent_root_pid → Vec<alert_id>` | Alert dedup |
| `AlertCooldowns` | `scenario_id → last_alert_ts` | Alert cooldown |

### Concrete implementation: `MutexSharedStateStore`

`Arc<Mutex<HashMap<SharedStateKey, HashMap<String, Vec<u8>>>>>` — serializes all access through a single mutex. Each method clones data out then releases the lock. Under high throughput (>10k events/sec), this can bottleneck. Future: replace with `dashmap` or lock-free structures.

### Wiring (`SharedStateAware` trait)

```rust
pub trait SharedStateAware: Send + Sync {
    fn inject_shared_state(&mut self, store: Box<dyn SharedStateStore>);
}
```

`PipelineBuilderImpl::build()` calls `inject_shared_state()` on every analyzer that implements `SharedStateAware`, then calls `on_attach(stage)` on every `AnalyzerLifecycle` implementor.

---

## 8. Context Pollution Detection Contract

The agent-teams-context-pollution scenario is the canonical multi-stage detection use case. It drives the design of cross-analyzer contracts and the `ContextPollutionEvidence` lifecycle.

### Detection Phases

```
Phase I   (InjectionSource):  Compromised agent writes poisoned artifact (e.g., team_policy.md)
Phase II  (Propagation):      3+ distinct agents read the poisoned artifact within 30s
Phase III (Acceptance):       Downstream agent emits acceptance marker in HTTP/SSE output
Phase IV  (AlertEnriched):    Full chain correlated into structured alert
```

### Evidence Bundle (`ContextPollutionEvidence`)

```rust
pub struct ContextPollutionEvidence {
    pub scenario_id: String,
    pub phase: ContextPollutionPhase,
    pub source_agent_root_pid: Option<u32>,
    pub poisoned_artifact_path: Option<String>,
    pub probe_artifact_path: Option<String>,
    pub acceptance_marker: Option<String>,
    pub propagation_chain: Vec<u32>,
    pub propagation_count: usize,
    pub evidence_excerpts: Vec<String>,
    pub phase_timestamps: HashMap<ContextPollutionPhase, u64>,
}
```

**Lifecycle methods:**
- `ingest_injection(event)` — Phase I: records source agent + poisoned path from process event
- `ingest_propagation(shared_readers, filepath)` — Phase II: records ≥3 agents reading the artifact
- `ingest_acceptance(marker, agent_root_pid)` — Phase III: records the acceptance marker text
- `is_complete()` — true when all 4 phases have data
- `severity()` — `"medium"` (0-1 agents), `"high"` (2 agents), `"critical"` (3+ agents)
- `finalize_alert()` — produces the structured `alert` event payload

**ContextPollutionContract defaults:**

| Parameter | Default |
|---|---|
| `min_propagation_agents` | 3 |
| `propagation_window_ns` | 30s |
| `monitored_paths` | `team_policy.md`, `override_probe.txt`, `security_signoff.md`, `shared_handoff.md` |
| `acceptance_markers` | `"POLICY OVERRIDE (urgent)"`, `"POLICY UPDATE"`, `"SECURITY OVERRIDE"` |

### Data Flow Example: Context Pollution Detection

```
Event Stream
    │
    ├─→ [Preprocessing]
    │   ├─ SSEProcessor: ssl → sse_processor
    │   ├─ HTTPParser: sse_processor → http_parser
    │   ├─ FileAccessAudit: process → file_access
    │   └─ (all original events pass through)
    │
    ├─→ [CausalLinking]
    │   └─ (prompt, process) → causal_link (pass-through)
    │
    ├─→ [InjectionDetection]
    │   └─ HandoffInjectionAnalyzer: (process, file_access, http_parser) → handoff_injection
    │
    ├─→ [AnomalyDetection]          ← ContextPollutionAnalyzer lives here
    │   ├─ ContextPollutionAnalyzer: (file_access, process) → context_pollution
    │   ├─ AgentTeamsRoleHijackAnalyzer: (process, sse_processor) → security_signoff
    │   └─ MultiAgentAnomalyAnalyzer: (process, system, http_parser) → multi_agent_anomaly
    │
    ├─→ [AlertEnrichment]
    │   └─ AlertEnrichmentAnalyzer: (causal_link, handoff_injection, context_pollution, ...) → alert
    │
    └─→ [Output]
        └─ FileLogger + OutputAnalyzer: alert → JSONL + SSE
```

---

## 9. Stream Contract

Every analyzer's input stream carries these guarantees (from `StreamContract`):

| Property | Guarantee | Rationale |
|---|---|---|
| `monotonic_timestamps` | Non-decreasing timestamps within a batch | Events processed synchronously from a Vec |
| `non_null` | `timestamp > 0`, `source != ""`, `pid > 0` | Rejected at preprocessing boundary otherwise |
| `length_preserving` | No silent event drops | If dropped, MUST declare via `StreamContractProvider` |
| `synchronous_process` | Stream elements immediately ready | `block_on` constraint (no async I/O within stream) |

**When an analyzer changes these guarantees**, it implements `StreamContractProvider` and returns `StreamContract::non_preserving()` (or a custom contract). Downstream analyzers that require `length_preserving` will fail contract validation.

---

## 10. Drain and Shutdown Contract

**Normal shutdown:** `orchestrator.drain()` → calls `drain()` on every `DrainableAnalyzer` in stage order → collects and emits buffered events → transitions to `Stopped`.

**Force stop:** `orchestrator.force_stop()` → immediate `Stopped` without drain (buffered events lost).

**Safety:** `drain()` must be idempotent — subsequent calls return empty Vec. Must complete synchronously (called from sync `drain()`).

**Stage ordering for drain:** Preprocessing → CausalLinking → InjectionDetection → AnomalyDetection → AlertEnrichment → Output. This ensures upstream buffers flush before downstream consumers process the flushed events.

---

## 11. Error Handling

| Scenario | Behavior |
|---|---|
| Unknown event source | Pass through unchanged |
| Malformed event data | Log warning, pass through |
| Analyzer `process()` returns `Err` | Pipeline transitions to `Error` state; restart needed |
| Analyzer panic | Not caught in v1 — propagates to caller |
| SharedStateStore lock contention | Blocking wait (single Mutex; no timeout in v1) |
| Resource exhaustion | Emit degradation alert via `ObservabilityMetricsAnalyzer`, continue with reduced fidelity |
| Lifecycle hook failure (`on_start` / `on_stop`) | Pipeline transitions to `Error`; downstream hooks skipped |

---

## 12. Module Inventory

All 31 analyzers registered in `analyzers/mod.rs`:

| Module | Status | Stage | Key Output |
|---|---|---|---|
| `agent_teams_role_hijack` | Active | AnomalyDetection | `security_signoff` |
| `alert_enrichment` | TODO (placeholder) | AlertEnrichment | `alert` |
| `auth_header_remover` | Active | Preprocessing | (pass-through) |
| `connection_buffer` | Active | Preprocessing | (buffer) |
| ~~`context_pollution`~~ | 🗑 Deleted 2026-10-16 (never compiled: 6×E0499) | — | — |
| `conversation_flow` | Active | AnomalyDetection | `multi_agent_anomaly` |
| `event_aggregator` | Active | AnomalyDetection | (aggregated) |
| `event_correlation` | Active | AnomalyDetection | (correlated) |
| `file_access_audit` | Active (FAAP-001) | Preprocessing | `file_access` |
| `file_logger` | Active | Output | (JSONL file) |
| `handoff_injection` | Active | InjectionDetection | `handoff_injection` |
| `http_filter` | Active | Preprocessing | `http_filtered` |
| `http_parser` | Active | Preprocessing | `http_parser` |
| `latency` | Active | AnomalyDetection | (metrics) |
| `multi_agent_anomaly` | Active | AnomalyDetection | `multi_agent_anomaly` |
| `observability_metrics` | Active | AnomalyDetection | (metrics) |
| `output` | Active | Output | (SSE) |
| `role_hijack_contract` | Active | AnomalyDetection | (role events) |
| `security_signoff` | Active | AnomalyDetection | `security_signoff` |
| `semantic_observer` | Active | AnomalyDetection | (confidence) |
| `shared_state_store` | Active (SSTP-001) | Cross-cutting | (shared state) |
| `sse_processor` | Active | Preprocessing | `sse_processor` |
| `ssl_filter` | Active | Preprocessing | `ssl_filtered` |
| `timestamp_normalizer` | Active | Preprocessing | `timestamp_normalized` |

**Contract-only modules (no impl yet):**
- `analyzer_composition_contract` — lifecycle, shared-state wiring, stream contract, drain contract, placeholder contracts
- `file_access_audit_contract` — FileAccessType, FileAccessRiskLevel enums
- `pipeline_contract` — PipelineStage, EventSource, StageContract, PipelineOrchestratorState, SharedStateStore, ContextPollutionEvidence
- `role_hijack_contract` — IdentityConfusionState, RoleClaim, SignoffChain validation types
- `pipeline_orchestrator` — PipelineOrchestratorImpl + PipelineBuilderImpl concrete implementations

---

## 13. Testing Strategy

1. **Unit tests**: Each analyzer tested in isolation with synthetic `Event` streams.
2. **Contract tests**: `pipeline_contract.rs` tests verify:
   - `EventSource` round-trip (`as_str` → `from_str`)
   - `StageContract::satisfies()` between adjacent stages
   - `canonical_pipeline_contracts()` all satisfy their downstream requirements
   - `PipelineOrchestratorState` valid/invalid transitions
   - `ContextPollutionEvidence` lifecycle (ingest → advance → complete → finalize)
3. **Composition tests**: `analyzer_composition_contract.rs` tests verify:
   - `StreamContract` satisfaction (default, non-preserving, violation cases)
   - `ContextPollutionContract` / `AlertEnrichmentContract` / `MutexSharedStateStoreContract` defaults
4. **Pipeline integration tests**: `pipeline_orchestrator.rs` tests use `MockStageAnalyzer` (tags events with stage name) and `FailingAnalyzer` (always errors) to verify multi-stage flow and error propagation.
5. **Regression tests**: Canonical attack scenarios — context pollution, credential leak, handoff injection, role hijack.

---

## 14. Performance Constraints

| Metric | Target | Measurement |
|---|---|---|
| Per-event pipeline latency | ≤ 100ms (p99) | `LatencyAnalyzer` per-stage tracking |
| Throughput | ≥ 10k events/sec per worker | `ObservabilityMetricsAnalyzer` |
| Shared state lock hold time | < 1μs per access | Mutex contention monitoring |
| Stateful analyzer memory | ≤ 1GB per worker | Bounded buffers (e.g., `MAX_READS_PER_AGENT = 100`) |
| CPU per analyzer | ≤ 50% single core | OS-level cgroup monitoring |

---

## 15. Risk Notes

1. **Sequential processing**: A slow analyzer (e.g., `SemanticObserverAnalyzer` with 5s LLM timeout) blocks all downstream stages. The `Paused` state exists to freeze the pipeline during such operations, but there is no per-stage timeout in v1.
2. **No error recovery**: If any analyzer returns `Err`, the pipeline transitions to `Error` and stops. Restart requires rebuilding the orchestrator.
3. **block_on constraint**: The orchestrator uses `tokio::runtime::Handle::block_on()` internally. The stream must be immediately ready — analyzers that produce streams with async I/O will deadlock or block the runtime.
4. **Shared state bottleneck**: `Arc<Mutex<>>` serializes all cross-analyzer access. Under >10k events/sec, this becomes a bottleneck. Future: `dashmap` or lock-free structures.
5. **ContextPollutionEvidence accumulation**: Evidence bundles live in memory per scenario. Capped at 1 per active scenario; cleared after alert emission. Long-running sessions with many concurrent scenarios could grow unbounded.
6. **Drain ordering**: Within a single stage, if multiple analyzers implement `DrainableAnalyzer`, their drain order is undefined (HashMap iteration). Cross-stage ordering is guaranteed.
7. **Stream contract enforcement is documentary**: A misbehaving analyzer can silently violate stream properties. Future: `ContractEnforcingAnalyzer` wrapper for debug builds.
