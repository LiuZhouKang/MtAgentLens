# SessionHealthMonitor Design Document

> **Design status**: Proposed  
> **Target stage**: AnomalyDetection (Stage 3)  
> **Module**: `collector/src/framework/analyzers/session_health_monitor.rs`  
> **Output source tag**: `session_health`, `session_health_alert`

---

## 1. Name and Purpose

**SessionHealthMonitor (SHM)**

A streaming analyzer that aggregates signals from multiple upstream stages
(latency, tool calls, errors, loops, HTTP status) into a unified per-session
health score. It emits periodic `session_health` snapshots and `session_health_alert`
events when the health score crosses configurable degradation thresholds.

### Problem

AgentSight's pipeline produces rich, independent signals — latency spikes,
tool-call loops, 5xx API errors, credential leaks — but these exist in
isolation. An operator monitoring a fleet of agents must manually correlate
disparate alert types to answer a single question: *"Is this agent session
healthy right now?"*

Without a unified health view:

| Pain point | Consequence |
|---|---|
| Alert fatigue | 5 independent analyzers each emit their own alerts; no single severity ranking |
| Delayed diagnosis | Operator notices a tool-loop alert but misses the concurrent latency degradation that explains it |
| No baseline | What is "normal" for this agent? Is 3 errors in 10 minutes acceptable? |
| Fleet blindness | Which of 50 running agents needs immediate attention? |

### Goals

1. **Unified health score**: 0–100 scalar per agent session, derived from
   weighted sub-scores across 5 dimensions (latency, error rate, tool health,
   API health, resource health).
2. **Configurable weights**: Operators tune dimension importance per use case
   (e.g., latency-critical vs. error-critical deployments).
3. **Periodic snapshots**: Every N seconds, emit a `session_health` event with
   the current score and dimensional breakdown.
4. **Threshold-based alerting**: When the health score drops below configurable
   thresholds (`warning < 60`, `critical < 30`), emit `session_health_alert`.
5. **Non-blocking passthrough**: All original events pass through unchanged;
   health events are additive derivations.

---

## 2. Position in the Analyzer Chain

```
InjectionDetection → [AgentLoopDetector] → [ToolExecutionTracker]
                                                │
                                                ▼
                                 [SessionHealthMonitor]  ← **HERE**
                                                │
                                                ▼
                                AlertEnrichment → Output
```

### Why this position

- **Downstream of all signal producers**: By Stage 3, events from latency
  analysis, tool tracking, loop detection, HTTP parsing, and process monitoring
  are all available. SHM consumes the *derived* events, not raw eBPF output.
- **Upstream of AlertEnrichment**: SHM's `session_health_alert` events feed
  into the enrichment stage, where they can be deduplicated, correlated with
  other alerts, and prioritized for the frontend.
- **Late enough for context**: SHM needs a sliding window of recent events
  (default 60s) to compute meaningful statistics. Earlier stages are too
  volatile for stable health scoring.

### Stage contract amendments

| Property | Before | After SHM |
|---|---|---|
| `consumes` | `latency`, `tool_call`, `http_parser`, `process`, `agent_loop`, `credential_leak` | unchanged |
| `produces` | — | `session_health`, `session_health_alert` |
| `length_preserving` | `true` | `false` |
| `pass_through` | `true` | `true` |

---

## 3. Interface: EventStream Contract

### 3.1 Consumed Event Sources

SHM processes events from these upstream sources. Unrecognized sources
pass through unchanged.

| Source | Inspected fields | Extracted signal |
|---|---|---|
| `latency` | `data.latency_us`, `data.pid` | Request-response latency per agent |
| `tool_call` | `data.status` (`Success`/`Failed`/`Timeout`), `data.tool_name` | Tool success rate, timeout count |
| `agent_loop` | `data.loop_type`, `data.severity` | Loop severity (warning/critical/emergency) |
| `http_parser` | `data.status_code`, `data.method`, `data.path` | API error rate (5xx, 429) |
| `process` | `data.action` (`exec`), `data.exit_code` | Process crash count |
| `credential_leak` | presence of event | Security incident count |

### 3.2 Produced Events

#### session_health (periodic snapshot)

```json
{
  "timestamp": 1717000000000,
  "source": "session_health",
  "pid": 12345,
  "comm": "claude",
  "data": {
    "session_id": "session-uuid",
    "health_score": 78,
    "window_duration_ms": 60000,
    "events_observed": 342,
    "dimensions": {
      "latency": {
        "score": 85,
        "weight": 0.25,
        "p50_us": 8200,
        "p95_us": 45000,
        "p99_us": 120000,
        "samples": 45
      },
      "error_rate": {
        "score": 70,
        "weight": 0.30,
        "total_operations": 120,
        "failed_operations": 8,
        "error_rate": 0.067,
        "recent_5xx_count": 2,
        "recent_429_count": 1
      },
      "tool_health": {
        "score": 90,
        "weight": 0.20,
        "total_calls": 35,
        "successful": 32,
        "failed": 2,
        "timed_out": 1,
        "active_loops": 0
      },
      "api_health": {
        "score": 65,
        "weight": 0.15,
        "total_requests": 28,
        "status_5xx": 2,
        "status_429": 1,
        "avg_response_bytes": 12400
      },
      "resource_health": {
        "score": 95,
        "weight": 0.10,
        "process_crashes": 0,
        "memory_mb": 245,
        "cpu_percent": 12.3
      }
    },
    "active_alerts": ["tool_timeout_high", "api_5xx_spike"]
  }
}
```

#### session_health_alert (threshold breach)

```json
{
  "timestamp": 1717000005000,
  "source": "session_health_alert",
  "pid": 12345,
  "comm": "claude",
  "data": {
    "session_id": "session-uuid",
    "alert_type": "health_degraded",
    "severity": "warning",
    "health_score": 52,
    "previous_score": 78,
    "degraded_dimensions": ["error_rate", "api_health"],
    "message": "Health score dropped from 78 to 52. Degraded: error_rate (70→40), api_health (65→35)."
  }
}
```

### 3.3 Trait Implementations

```rust
impl Analyzer for SessionHealthMonitor { ... }
impl AnalyzerLifecycle for SessionHealthMonitor {
    fn on_stop(&mut self) -> Result<(), String> {
        // Flush final health snapshot before shutdown
        self.emit_snapshot();
        Ok(())
    }
}
impl StreamContractProvider for SessionHealthMonitor {
    fn output_contract(&self) -> StreamContract {
        StreamContract {
            preserves_length: false,
            preserves_order: true,
            sources_produced: hashset!["session_health".into(), "session_health_alert".into()],
        }
    }
}
impl DrainableAnalyzer for SessionHealthMonitor {
    fn drain(&mut self) -> Vec<Event> {
        // Emit any pending health alerts not yet flushed
        self.pending_alerts.drain(..).collect()
    }
}
```

---

## 4. Key Data Structures

### 4.1 Health Score Computation

```rust
/// Per-agent sliding-window accumulator for health scoring.
struct AgentHealthWindow {
    /// Agent PID (session key)
    pid: u32,
    /// Session identifier
    session_id: String,

    // ── Latency dimension ──
    /// Recent latency samples (microseconds), capped at max_samples
    latency_samples: VecDeque<u64>,

    // ── Error dimension ──
    /// Total operations observed in window
    total_operations: u64,
    /// Failed operations (tool failures + 5xx + process crashes)
    failed_operations: u64,

    // ── Tool health dimension ──
    /// Tool call outcomes in window
    tool_successes: u64,
    tool_failures: u64,
    tool_timeouts: u64,
    /// Active agent loops (count, not detailed)
    active_loops: u32,

    // ── API health dimension ──
    api_requests: u64,
    api_5xx: u64,
    api_429: u64,

    // ── Resource health dimension ──
    process_crashes: u64,

    /// Timestamp of the most recent event in this window
    last_event_ns: u64,
    /// Total events observed (for diagnostics)
    events_observed: u64,
}

impl AgentHealthWindow {
    /// Compute the composite health score (0–100).
    fn compute_score(&self, weights: &HealthWeights) -> HealthScore {
        let dims = Dimensions {
            latency: self.latency_score(),
            error_rate: self.error_score(),
            tool_health: self.tool_score(),
            api_health: self.api_score(),
            resource_health: self.resource_score(),
        };
        let composite = dims.latency * weights.latency
            + dims.error_rate * weights.error_rate
            + dims.tool_health * weights.tool_health
            + dims.api_health * weights.api_health
            + dims.resource_health * weights.resource_health;
        HealthScore {
            composite: (composite * 100.0).round().clamp(0.0, 100.0) as u8,
            dimensions: dims,
        }
    }

    fn latency_score(&self) -> f64 {
        if self.latency_samples.is_empty() { return 1.0; }
        let p95 = percentile(&self.latency_samples, 95.0);
        // Score decays from 1.0 (fast) to 0.0 (slow):
        //   p95 < 100ms  → 1.0
        //   p95 = 500ms  → 0.7
        //   p95 = 2s     → 0.3
        //   p95 > 10s    → 0.0
        let ratio = p95 as f64 / 10_000_000.0; // relative to 10s
        (1.0 - ratio).clamp(0.0, 1.0)
    }

    fn error_score(&self) -> f64 {
        if self.total_operations == 0 { return 1.0; }
        let rate = self.failed_operations as f64 / self.total_operations as f64;
        // Score: 0% errors → 1.0, 10% errors → 0.5, 50%+ errors → 0.0
        (1.0 - rate * 2.0).clamp(0.0, 1.0)
    }

    fn tool_score(&self) -> f64 {
        let total = self.tool_successes + self.tool_failures + self.tool_timeouts;
        if total == 0 { return 1.0; }
        let mut score = self.tool_successes as f64 / total as f64;
        // Active loops heavily penalize tool health
        if self.active_loops > 0 {
            score *= 0.5_f64.powi(self.active_loops as i32);
        }
        score.clamp(0.0, 1.0)
    }

    fn api_score(&self) -> f64 {
        if self.api_requests == 0 { return 1.0; }
        let error_ratio = (self.api_5xx + self.api_429) as f64 / self.api_requests as f64;
        (1.0 - error_ratio * 3.0).clamp(0.0, 1.0)
    }

    fn resource_score(&self) -> f64 {
        if self.process_crashes == 0 { return 1.0; }
        // Each crash deducts 0.3 from the score
        (1.0 - self.process_crashes as f64 * 0.3).clamp(0.0, 1.0)
    }
}

#[derive(Debug, Clone)]
struct Dimensions {
    latency: f64,
    error_rate: f64,
    tool_health: f64,
    api_health: f64,
    resource_health: f64,
}

#[derive(Debug, Clone)]
struct HealthScore {
    composite: u8,
    dimensions: Dimensions,
}
```

### 4.2 Configuration

```rust
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SessionHealthMonitorConfig {
    // ── Window ──
    /// Sliding window duration for statistics (nanoseconds)
    pub window_duration_ns: u64,           // default: 60s

    // ── Snapshot ──
    /// Interval between periodic health snapshots (nanoseconds)
    pub snapshot_interval_ns: u64,         // default: 10s
    /// Maximum latency samples retained per window
    pub max_latency_samples: usize,        // default: 1000

    // ── Weights (must sum to 1.0) ──
    pub latency_weight: f64,               // default: 0.25
    pub error_rate_weight: f64,            // default: 0.30
    pub tool_health_weight: f64,           // default: 0.20
    pub api_health_weight: f64,            // default: 0.15
    pub resource_health_weight: f64,       // default: 0.10

    // ── Alerting ──
    /// Health score below which a warning alert is emitted
    pub warning_threshold: u8,             // default: 60
    /// Health score below which a critical alert is emitted
    pub critical_threshold: u8,            // default: 30
    /// Minimum score drop to trigger a degradation alert
    pub degradation_min_drop: u8,          // default: 15
    /// Cooldown between alerts of the same type (nanoseconds)
    pub alert_cooldown_ns: u64,            // default: 30s

    // ── Session ──
    /// Max tracked sessions (LRU eviction)
    pub max_sessions: usize,               // default: 100
    /// Inactive session TTL (nanoseconds)
    pub session_ttl_ns: u64,               // default: 300s
}

impl Default for SessionHealthMonitorConfig {
    fn default() -> Self {
        Self {
            window_duration_ns: 60_000_000_000,
            snapshot_interval_ns: 10_000_000_000,
            max_latency_samples: 1000,
            latency_weight: 0.25,
            error_rate_weight: 0.30,
            tool_health_weight: 0.20,
            api_health_weight: 0.15,
            resource_health_weight: 0.10,
            warning_threshold: 60,
            critical_threshold: 30,
            degradation_min_drop: 15,
            alert_cooldown_ns: 30_000_000_000,
            max_sessions: 100,
            session_ttl_ns: 300_000_000_000,
        }
    }
}
```

---

## 5. Data Flow

```
EventStream (latency, tool_call, agent_loop, http_parser, process, credential_leak)
  │
  ▼
SessionHealthMonitor::process()
  │
  ├── 对每个事件:
  │     1. 提取 agent PID → 查找/创建 AgentHealthWindow
  │        └── 如果窗口不存在 → 创建新窗口
  │        └── 如果窗口过期（超过 TTL）→ 清理旧窗口
  │
  │     2. 按事件源分发更新:
  │        ├── source = "latency"
  │        │   └── 追加 latency_us 到 latency_samples
  │        │
  │        ├── source = "tool_call"
  │        │   ├── status = "Success" → tool_successes++
  │        │   ├── status = "Failed"  → tool_failures++, failed_operations++
  │        │   └── status = "Timeout" → tool_timeouts++
  │        │
  │        ├── source = "agent_loop"
  │        │   └── 更新 active_loops 计数
  │        │
  │        ├── source = "http_parser"
  │        │   ├── status_code 5xx → api_5xx++, failed_operations++
  │        │   ├── status_code 429 → api_429++
  │        │   └── api_requests++ (any request)
  │        │
  │        ├── source = "process"
  │        │   └── exit_code != 0 → process_crashes++, failed_operations++
  │        │
  │        ├── source = "credential_leak"
  │        │   └── failed_operations++ (security incidents count as failures)
  │        │
  │        └── 其他 source → 透传（不参与健康计算）
  │
  │     3. 更新窗口元数据:
  │        ├── events_observed++
  │        └── last_event_ns = event.timestamp
  │
  │     4. 淘汰过期数据:
  │        └── 移除 windows_duration_ns 之前的旧样本
  │
  ├── 定期快照（基于 snapshot_interval_ns）:
  │     1. 对每个活跃窗口计算 health_score
  │     2. 检查阈值:
  │        ├── score < critical_threshold → emit critical alert
  │        ├── score < warning_threshold → emit warning alert
  │        └── score_drop > degradation_min_drop → emit degradation alert
  │     3. 如果冷却期已过 → 发出 alert 事件
  │     4. 发出 session_health 快照事件
  │
  └── 将原始事件 + 健康事件合并输出
      └── flatten → EventStream (输出)
```

### Flow Diagram

```
                         ┌─────────────────────────────┐
                         │     Input EventStream         │
                         │  (latency, tool_call,         │
                         │   agent_loop, http_parser,    │
                         │   process, credential_leak)   │
                         └────────────┬────────────────┘
                                      │
                         ┌────────────▼────────────────┐
                         │   Extract PID + source       │
                         │   Route to handler           │
                         └──────┬──────────┬───────────┘
                                │          │
                    ┌───────────▼──┐  ┌───▼──────────────┐
                    │ Known source  │  │ Unknown source    │
                    └───────┬──────┘  └───┬──────────────┘
                            │              │
              ┌─────────────▼──────┐       │
              │ Update per-PID     │       │
              │ AgentHealthWindow  │       │
              │                    │       │
              │ ├─latency_samples  │       │
              │ ├─tool counters    │       │
              │ ├─api counters     │       │
              │ ├─error counters   │       │
              │ └─loop state       │       │
              └─────────┬──────────┘       │
                        │                  │
              ┌─────────▼──────────┐       │
              │ Evict stale data   │       │
              │ (older than window)│       │
              └─────────┬──────────┘       │
                        │                  │
              ┌─────────▼──────────┐       │
              │ Periodic snapshot?  │       │
              └──┬─────────────┬───┘       │
                 │Yes          │No          │
          ┌──────▼──────┐     │            │
          │Compute score │     │            │
          │per session   │     │            │
          └──────┬──────┘     │            │
                 │            │            │
          ┌──────▼──────┐     │            │
          │Threshold     │     │            │
          │check + alert │     │            │
          └──────┬──────┘     │            │
                 │            │            │
          ┌──────▼──────┐     │            │
          │Emit health   │     │            │
          │snapshot event│     │            │
          └──────┬──────┘     │            │
                 │            │            │
                 └────────────┴────────────┘
                              │
                         ┌────▼───────────────────────┐
                         │   Output EventStream         │
                         │   (original + session_health │
                         │    + session_health_alert)   │
                         └──────────────────────────────┘
```

---

## 6. Error Handling Strategy

| Scenario | Behavior |
|---|---|
| `event.data` missing expected field | Skip that dimension update; log warning once per minute |
| No events in window for a session | Health score defaults to 100 (no news is good news) |
| `snapshot_interval_ns` fires but no active windows | No snapshot emitted (silent skip) |
| Weight sum ≠ 1.0 | Normalize weights on construction; log warning |
| Session count exceeds `max_sessions` | Evict the least-recently-updated session (LRU) |
| Concurrent access (Tokio multi-task) | `AgentHealthWindow` map wrapped in `Arc<Mutex<>>`; per-window updates are < 1μs |
| `percentile()` called on empty sample set | Return 0 (defensive; guarded by `is_empty()` check) |

### Self-healing properties

- Stale sessions (no events for `session_ttl_ns`) are cleaned on each `process()` call.
- Latency sample buffer is trimmed to `max_latency_samples` on each insert.
- Window counters are bounded by the event rate × window duration; no unbounded growth.
- Cooldown timestamps prevent alert storms; a stuck session emits at most one alert per `alert_cooldown_ns`.

---

## 7. Testing Strategy

### 7.1 Unit Tests

| Test | Verifies |
|---|---|
| `test_health_score_perfect` | Zero errors, fast latency → score = 100 |
| `test_health_score_degraded` | 50% tool failures + high latency → score < 50 |
| `test_latency_score_decay` | p95=100ms → ~1.0, p95=5s → ~0.5, p95=20s → 0.0 |
| `test_error_score_boundary` | 0% → 1.0, 5% → 0.9, 50% → 0.0 |
| `test_tool_score_loop_penalty` | 1 active loop → score × 0.5, 2 loops → × 0.25 |
| `test_window_eviction` | Events older than `window_duration_ns` excluded from score |
| `test_weight_normalization` | Non-1.0 weights normalized on construction |
| `test_unknown_source_passthrough` | Events with unknown source pass through unchanged |
| `test_alert_cooldown` | Alert emitted → no duplicate within `alert_cooldown_ns` |
| `test_degradation_alert` | Score drops by `degradation_min_drop` → degradation alert |
| `test_session_ttl_cleanup` | Inactive session removed after `session_ttl_ns` |
| `test_lru_session_eviction` | `max_sessions + 1` → oldest session evicted |

### 7.2 Integration Tests

- **Full pipeline integration**: Run with LatencyAnalyzer + ToolExecutionTracker +
  AgentLoopDetector producing events → verify SHM correctly aggregates all
  dimensions into health snapshots.
- **Degradation scenario**: Simulate progressive API failures → verify health
  score trends downward and crosses warning/critical thresholds.
- **Recovery scenario**: Simulate error spike → recovery → verify health score
  returns to normal after the window clears.
- **Multi-agent isolation**: Two agents, one healthy + one degraded → verify
  independent health scores per PID.

### 7.3 Contract Tests

- Verify `StreamContract::non_preserving()` with `sources_produced` containing
  `session_health` and `session_health_alert`.
- Verify downstream `AlertEnrichment` stage accepts both new source tags.

---

## 8. Registering in the Module

Edit `analyzers/mod.rs`:

```rust
pub mod session_health_monitor;
pub use session_health_monitor::{
    SessionHealthMonitor, SessionHealthMonitorConfig,
    HealthScore, HealthWeights,
};
```

Register in `PipelineOrchestratorImpl` at the AnomalyDetection stage:

```rust
pipeline.add_analyzer(
    PipelineStage::AnomalyDetection,
    Box::new(SessionHealthMonitor::new(SessionHealthMonitorConfig::default())),
)?;
```

---

## 9. Performance and Resource Budget

| Metric | Estimate | Notes |
|---|---|---|
| Per-event CPU | < 200 ns (known source) / < 50 ns (unknown source) | Hash lookup + counter increment; no allocation on hot path |
| Per-snapshot CPU | ~500 μs per active session | Score computation + JSON serialization |
| Memory per session | ~80 KB | 1000 × 8 bytes (latency samples) + counters + metadata |
| Total memory (100 sessions) | ~8 MB | Bounded by `max_sessions` |
| Lock contention | Negligible | Per-window update < 1 μs; snapshots amortized over `snapshot_interval_ns` |

---

## 10. Risk Assessment

| Risk | Severity | Mitigation |
|---|---|---|
| Health score is misleading (false confidence) | Medium | Dimensional breakdown included in every snapshot; frontend can drill down |
| Alert fatigue from flapping scores | Medium | Cooldown mechanism; `degradation_min_drop` prevents noise |
| Missing signal (new upstream analyzer not consumed) | Low | New source tags pass through; SHM gracefully skips unknowns; extend handler match arms |
| Weight misconfiguration | Low | Normalize on construction; log warning if weights don't sum to 1.0 |
| Window too short for slow operations | Low | `window_duration_ns` configurable; 60s default is generous for LLM operations |

---

## 11. Summary

**SessionHealthMonitor (SHM)** is a streaming health aggregation analyzer that
consumes derived events from latency, tool-execution, loop-detection, HTTP-parsing,
process-monitoring, and credential-leak analyzers to produce a unified 0–100
health score per agent session. It emits periodic `session_health` snapshots and
threshold-driven `session_health_alert` events.

Key design characteristics:

- **Aggregation, not detection**: SHM doesn't find new anomalies — it synthesizes
  existing signals into an operator-friendly health view.
- **Dimensional scoring**: 5 independent sub-scores (latency, error rate, tool
  health, API health, resource health) with configurable weights provide
  transparent scoring.
- **Passthrough architecture**: Zero transformation on original events; health
  events are additive derivations.
- **Bounded resources**: Fixed-size latency buffer per session + LRU session
  eviction caps memory at ~8 MB for 100 concurrent sessions.
- **Fleet-ready**: Per-PID session isolation enables monitoring dozens of agents
  simultaneously.
- **Observable**: Exposes metrics (sessions_active, snapshots_emitted,
  alerts_warning, alerts_critical) for self-monitoring.

SHM is proposed for the AnomalyDetection stage (Stage 3), consuming events
from ToolExecutionTracker and AgentLoopDetector, and feeding into
AlertEnrichment. Implementation is estimated at ~350 lines of Rust plus
~200 lines of tests.
