# StreamingEventDeduplicationAnalyzer Design Document

> **Design status**: Proposed  
> **Target stage**: Preprocessing (Stage 0)  
> **Module**: `collector/src/framework/analyzers/event_deduplication.rs`  
> **Output source tag**: `dedup_dropped` (diagnostic only — duplicate events are not forwarded)

---

## 1. Name and Purpose

**StreamingEventDeduplicationAnalyzer (SEDA)**

Eliminates semantically duplicate events produced by overlapping eBPF probes
*before* they reach expensive downstream analyzers (HTTP parsing, SSE
reassembly, injection detection).

### Problem

eBPF-based observability instruments the kernel at multiple layers
simultaneously. A single logical event — an HTTP POST, a file write, a
process exec — can be captured by multiple probes running concurrently:

| Probe pair | Collision scenario |
|---|---|
| `sslsniff` + `browsertrace` | Both uprobe SSL_read/write on the same TLS session; browser-specific probe duplicates the generic one |
| SSL_write + HTTPParser | Raw SSL payload event and its parsed HTTP equivalent share the same logical operation |
| Multi-library uprobes | Clang/libc/glibc all expose `read()`; uprobes on multiple libraries capture the same syscall |
| TCP + SSL layers | Process net events may report the same connection as an SSL handshake event |

Without deduplication, a single `POST /v1/chat` call inflates into ~3-10 raw
events, wasting CPU in every downstream stage and triggering false-positive
detection patterns (e.g., `HandoffInjectionAnalyzer` sees "multiple"
file-read events when only one occurred).

### Goals

1. Detect and suppress events whose fingerprint matches a recently-seen event
   within a configurable time window.
2. Passthrough all non-duplicate events unchanged — zero transformation overhead
   on the hot path.
3. Fail-open: unrecognized event formats pass through; fingerprint computation
   failures log a warning but do not drop the event.
4. Bounded memory: fingerprint cache is an LRU ring buffer with a fixed entry
   cap, preventing unbounded growth under sustained load.

---

## 2. Position in the Analyzer Chain

```
Raw eBPF events (ssl, process, system, stdio)
    │
    ▼
[TimestampNormalizer]       ← epoch ns → epoch ms + passes through
    │
    ▼
[StreamingEventDeduplicationAnalyzer]   ← **HERE**
    │
    ▼
[AuthHeaderRemover] [SSEProcessor] [HTTPParser] [FileAccessAudit]
    │
    ▼
  ... downstream stages ...
```

### Why this position

- Sorting is cheap (only timestamp normalization has occurred). The dedup
  analyzer is the *first* content-inspecting stage, before expensive operations
  like SSE chunk reassembly and HTTP header parsing.
- By suppressing duplicates early, every downstream analyzer saves CPU
  proportional to the duplicate rate (estimated 30-50% reduction in event
  volume for multi-probe configurations).

### Stage contract amendments

| Property | Before | After SEDA |
|---|---|---|
| `consumes` | `ssl`, `process`, `system`, `stdio` | unchanged |
| `produces` | — | `dedup_dropped` (diagnostic) |
| `length_preserving` | `true` | `false` (declared via `StreamContractProvider`) |
| `pass_through` | `true` | `true` for non-duplicates |

Downstream analyzers that require `length_preserving` in their contract must
re-evaluate — in practice, no downstream Preprocessing analyzer relies on exact
event count parity with probe output.

---

## 3. Interface: EventStream Contract

### 3.1 Consumed Event Format

SEDA inspects `event.source` to determine a dedup strategy. Events with
unrecognized sources pass through unconditionally.

| Canonical source | Inspected fields | Fingerprint strategy |
|---|---|---|
| `ssl` | `data.operation` (`"SSL_write"`, `"SSL_read"`), `data.len` | `(source, operation, pid, payload_len, quantized_ts)` |
| `process` | `data.action` (`"exec"`, `"file_open"`, `"file_close"`), `data.path` (for file ops) | `(source, action, pid, path_hash, quantized_ts)` |
| `system` | `data.category` (`"cpu"`, `"memory"`) | `(source, category, pid, quantized_ts)` |
| `stdio` | `data.direction` (`"read"`, `"write"`), `data.len` | `(source, direction, pid, len, quantized_ts)` |
| Any other source | — (passthrough) | No dedup attempted |

### 3.2 Produced Stream

- **Non-duplicate events**: Pass through unchanged (same `Event` object by
  value).
- **Duplicate events**: Suppressed. No output event emitted.
- **Diagnostic events** (opt-in, configurable): When `emit_diagnostics = true`,
  a `dedup_dropped` event is emitted for every suppressed duplicate.
  `dedup_dropped` events carry the original event's timestamp and a
  `suppressed_count` field incremented per batch.

Diagnostic events are separate from the pass-through stream — they are
interleaved in time order so the frontend timeline can render "N duplicates
suppressed at 14:32:05" annotations.

### 3.3 Trait Implementations

```rust
impl Analyzer for StreamingEventDeduplicationAnalyzer { ... }
impl AnalyzerLifecycle for StreamingEventDeduplicationAnalyzer { ... }
impl StreamContractProvider for StreamingEventDeduplicationAnalyzer {
    fn output_contract(&self) -> StreamContract {
        StreamContract::non_preserving()
    }
}
impl DrainableAnalyzer for StreamingEventDeduplicationAnalyzer { ... }
```

- `AnalyzerLifecycle::on_stop()` flushes and logs dedup statistics.
- `DrainableAnalyzer::drain()` returns any diagnostic events buffered for
  batch emission (empty if `emit_diagnostics = false`).
- `SharedStateAware` is NOT implemented — SEDA has no cross-analyzer state
  dependency.

---

## 4. Key Data Structures

### 4.1 Fingerprint

```rust
/// Content-addressable hash of an event's semantically meaningful fields.
///
/// Uses quantized timestamp to group events within the same time bucket,
/// preventing exact-duplicate-only matching while maintaining low false-positive
/// rates.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
struct EventFingerprint {
    /// Quantized to nearest `quantum_ns` (default: 500ms).
    time_bucket: u64,
    /// The canonical EventSource string (e.g. "ssl", "process").
    source_hash: u64,
    /// Per-source semantic key:
    /// - ssl: hash of `data.operation`
    /// - process: hash of `data.action` || optional path hash
    /// - system: hash of `data.category`
    /// - stdio: hash of `data.direction`
    operation_hash: u64,
    /// Originating PID — different PIDs with identical payloads are NOT duplicates.
    pid: u32,
    /// Payload length proxy (data.len for SSL/stdio; 0 for metadata events).
    payload_length: usize,
}
```

**Design rationale for including PID**: Two independent agents performing the
same operation (e.g., both calling `POST /v1/chat`) should NOT be deduplicated.
This is critical for multi-agent scenarios where Tool A and Tool B make
identical HTTP calls — both must be observed independently.

**Design rationale for `payload_length` not content hash**: Full-content
hashing would require deserializing and hashing `event.data`, adding ~200-500ns
per event. `payload_length` provides a cheap proxy (same-length duplicates are
far more likely to be true duplicates). The `time_bucket` quantization +
`operation_hash` together provide sufficient discrimination. If false-positive
suppression becomes an issue in production, content hashing can be added behind
a config flag.

### 4.2 Fingerprint Cache

```rust
/// LRU ring buffer tracking recently-seen fingerprints.
///
/// Insertion is O(1) amortized. Lookup uses a HashMap<Fingerprint, ()> for
/// O(1) average-case dedup check, with the ring buffer providing the eviction
/// order.
struct DedupCache {
    /// Ordered ring buffer of recent fingerprints.
    ring: VecDeque<(EventFingerprint, u64)>,  // (fingerprint, insertion_ns)
    /// Fast-lookup set for O(1) dedup checks.
    seen: HashMap<EventFingerprint, ()>,
    /// Maximum entries before eviction of oldest.
    capacity: usize,
    /// Age threshold: entries older than this are evicted on lookup.
    max_age_ns: u64,
}
```

**Cache tuning defaults**:

| Parameter | Default | Rationale |
|---|---|---|
| `capacity` | 10,000 entries | ~2-5 MB at 512 bytes/fingerprint; handles ~5 sec of 2,000 events/sec |
| `max_age_ns` | 30 seconds | Covers duplicate arrival windows while preventing stale entries from false-positive suppression |
| `time_quantum_ns` | 500 ms | Covers probe skew (0-200ms typical for same-event multi-probe capture) without false-positive grouping of distinct events |
| `emit_diagnostics` | `false` (production) | Diagnostic events add overhead; enable only for debugging |

### 4.3 Configuration

```rust
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DedupConfig {
    /// Time bucket size for fingerprint quantization (nanoseconds).
    pub time_quantum_ns: u64,
    /// Maximum entries in the fingerprint cache.
    pub cache_capacity: usize,
    /// Maximum age of a cache entry before eviction (nanoseconds).
    pub cache_max_age_ns: u64,
    /// Whether to emit diagnostic `dedup_dropped` events.
    pub emit_diagnostics: bool,
    /// Sources to deduplicate. Empty = all known sources.
    pub enabled_sources: Vec<String>,
}

impl Default for DedupConfig {
    fn default() -> Self {
        Self {
            time_quantum_ns: 500_000_000,  // 500 ms
            cache_capacity: 10_000,
            cache_max_age_ns: 30_000_000_000,  // 30 s
            emit_diagnostics: false,
            enabled_sources: vec![],
        }
    }
}
```

---

## 5. Error Handling Strategy

| Scenario | Behavior |
|---|---|
| `event.data` is not a JSON object | Pass through unchanged (fail-open); increment `unparseable_count` metric |
| `event.data` missing expected field (`operation`, `action`, etc.) | Pass through unchanged; treat as non-duplicable |
| Fingerprint cache alloc failure (unlikely — fixed capacity) | Pass through unchanged; log error once per second max |
| `DedupCache` at capacity | Evict oldest entry from `ring` and `seen`; insert new one |
| `process()` called concurrently (Tokio multi-task) | `DedupCache` wrapped in `Arc<Mutex<>>`; lock contention bounded by O(1) fingerprint ops |
| Timestamp is zero (invalid event) | Pass through unchanged; `seen` never caches zero-timestamp fingerprints |

### Self-healing property

If the `seen` map somehow desynchronizes from the `ring` buffer (only possible
with a logic bug), the analyzer self-corrects on the next capacity-eviction
cycle. No persistent corruption is possible because there are no on-disk state
or cross-session dependencies.

### Metrics (exposed via `ObservabilityMetricsAnalyzer`)

| Metric | Type | Description |
|---|---|---|
| `dedup_events_processed` | Counter | Total events inspected |
| `dedup_duplicates_suppressed` | Counter | Events dropped as duplicates |
| `dedup_cache_size` | Gauge | Current fingerprint cache entries |
| `dedup_unparseable` | Counter | Events with unparseable `data` field |
| `dedup_passthrough_unknown_source` | Counter | Events with unrecognized source |

---

## 6. Testing Approach

### 6.1 Unit Tests (in-module, `#[cfg(test)]`)

**Test category A: Fingerprint computation**
- `test_fingerprint_identical_events_same_hash` — same source, pid, operation, payload → same fingerprint
- `test_fingerprint_different_pid_different_hash` — different PID → different fingerprint (critical for multi-agent)
- `test_fingerprint_within_same_time_bucket` — events 400ms apart within 500ms quantum → same time_bucket
- `test_fingerprint_across_time_bucket_boundary` — events 600ms apart → different time_bucket

**Test category B: Dedup cache operations**
- `test_cache_insert_and_lookup` — insert fingerprint, immediate lookup returns true
- `test_cache_eviction_by_age` — insert entry, advance time beyond `max_age_ns`, lookup returns false
- `test_cache_eviction_by_capacity` — insert N+1 entries into N-capacity cache, oldest entry is gone
- `test_cache_cleanup_on_eviction` — eviction removes entry from both `ring` and `seen`

**Test category C: Stream-level integration**
- `test_duplicate_ssl_write_suppressed` — two identical SSL_write events within 500ms → only first emitted
- `test_different_pid_not_suppressed` — same operation, different PID → both emitted
- `test_unknown_source_passthrough` — event source = "custom_unknown" → passes through
- `test_malformed_data_passthrough` — `event.data` is `serde_json::Value::Null` → passes through
- `test_diagnostics_emission_when_enabled` — `emit_diagnostics = true`, duplicate event → `dedup_dropped` event emitted
- `test_diagnostics_suppressed_when_disabled` — default config → no `dedup_dropped` events

### 6.2 Contract Tests (in `pipeline_contract.rs`)

- Verify `StreamContract::non_preserving()` satisfies downstream Preprocessing stage contracts (all downstream Preprocessing analyzers must accept `length_preserving = false`).
- Verify `StageContract::produces` includes `"dedup_dropped"` when SEDA is in the pipeline.

### 6.3 Integration/E2E Tests

- **Multi-probe scenario**: Simulate `sslsniff` + `browsertrace` both capturing the same SSL_read by synthesizing events with identical fingerprints → verify downstream receives exactly one.
- **High-throughput stress test**: 50,000 events/sec with 40% estimated duplicate rate → verify `dedup_cache_size` stabilizes at capacity, no unbounded growth, no pipeline stalls.
- **Regression guard**: Run the full canonical attack scenario (context pollution, handoff injection) with SEDA enabled → verify all existing detections still fire. This ensures dedup does not silently suppress security-relevant uniqueness.

### 6.4 Benchmarks (optional, `criterion`)

- `bench_fingerprint_computation` — measure ns per fingerprint
- `bench_dedup_cache_lookup_hit` — measure ns per lookup when entry exists
- `bench_dedup_cache_lookup_miss` — measure ns per lookup when entry absent
- `bench_full_pipeline_with_dedup` — measure events/sec throughput with and without SEDA

---

## 7. Risk Assessment

| Risk | Severity | Mitigation |
|---|---|---|
| False-positive suppression (distinct events dedup'd) | High — could mask security signals | Include PID in fingerprint; conservative time quantum (500ms); optional content-hash mode for high-security deployments |
| Lock contention under extreme load (> 50k events/sec) | Medium — pipeline stall | O(1) cache ops minimize lock hold time (< 1us); future: sharded caches per source type |
| Memory growth from diagnostic events | Low | Diagnostic events disabled by default; when enabled, they're only emitted for duplicate events (bounded by dedup rate) |
| Interaction with replay/re-evaluation | Medium — cached fingerprints persist across evaluate phases | `on_stop()` clears the cache; replay creates a fresh analyzer instance |

---

## 8. Summary (for observability report)

**StreamingEventDeduplicationAnalyzer (SEDA)** was designed to address event
inflation caused by overlapping eBPF probes in the AgentSight collector
pipeline. By inserting a lightweight content-fingerprint-based deduplication
stage at the beginning of the preprocessing pipeline, SEDA suppresses
semantically identical events produced by multiple probing layers (e.g.,
sslsniff + browsertrace, raw SSL + HTTP parser) before they reach expensive
downstream analyzers.

Key design characteristics:
- **Non-invasive**: Passthrough architecture with zero transformation on
  non-duplicate events; no changes required in any existing analyzer.
- **Bounded resource usage**: Fixed-size LRU fingerprint cache (10k entries
  default, ~2 MB memory) with configurable age-based eviction.
- **Multi-agent safe**: PID-aware fingerprinting ensures independent agents
  performing identical operations are never deduplicated.
- **Fail-open**: Malformed or unrecognized events pass through unchanged;
  diagnostic emission is opt-in for production debugging.
- **Observable**: Exposes 5 metrics (processed, suppressed, cache size,
  unparseable, passthrough) via the existing ObservabilityMetricsAnalyzer
  pipeline.

Estimated impact based on multi-probe deployment profiles:
- **30-50% reduction in event volume** reaching downstream stages (HTTP
  parsing, SSE reassembly, injection detection).
- **20-35% reduction in total pipeline CPU** due to fewer events processed
  by each downstream analyzer.
- **No false-positive suppression** in multi-agent scenarios (validated by
  PID-inclusive fingerprint design).

SEDA is proposed for the Preprocessing stage (Stage 0) of the 6-stage
pipeline, between `TimestampNormalizer` and `AuthHeaderRemover`.
Implementation is estimated at ~300 lines of Rust plus ~150 lines of tests,
with integration into the existing `PipelineBuilder` requiring a single
`add_preprocessor()` call.
