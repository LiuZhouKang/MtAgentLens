# AgentDriftAnalyzer 设计文档

> **设计状态**: 提案阶段
> **目标阶段**: AnomalyDetection (Stage 3)
> **模块**: `collector/src/framework/analyzers/agent_drift.rs`
> **输出事件源标签**: `agent_drift`

---

## 1. 名称与目的

**AgentDriftAnalyzer (ADA)**

检测 LLM agent 行为随时间的**渐进漂移**（gradual drift）。现有 anomaly
检测器（MultiAgentAnomaly、ConversationFlow）擅长捕捉突发异常——但 agent
性能退化常常不是突发的，而是缓慢积累的。ADA 填补这一盲区。

### 问题

| 漂移类型 | 现实场景 |
|---|---|
| 工具偏好漂移 | Agent 在 30 分钟内从使用 `read_file` 为主，逐渐转为只使用 `grep`——搜索深度退化 |
| 响应延迟漂移 | API 响应时延从 200ms 的中位数缓慢攀升至 800ms——后端过载或上下文膨胀 |
| 输出体积漂移 | Agent 单次响应从 2KB 中位数增长至 12KB——提示词注入引发的冗余输出 |
| 错误率漂移 | 4xx/5xx 错误率从 0.5% 逐渐升至 8%——API 配额接近耗尽或模型降级 |
| 静默漂移 | Agent 事件频率从 50/min 降至 5/min——agent 可能卡死或进入死循环思考 |

### 目标

1. 维护每个 agent（以 root_pid 为键）的 6 维滑动窗口统计（工具分布、延迟、
   输出体积、错误率、事件频率、HTTP 方法分布）
2. 每 N 个事件（可配置）计算与基线窗口的 KL 散度 / 均值偏移
3. 漂移超过阈值时发出 `agent_drift` 事件，携带漂移评分和趋势方向
4. 基线窗口自动从首段稳定期学习，无需人工配置
5. 零开销透传——所有原始事件不受影响

---

## 2. 在分析器链中的位置

```
Raw eBPF events
    │
    ▼
[Preprocessing] TimestampNormalizer → SSEProcessor → HTTPParser → HTTPFilter
    │
    ▼
[CausalLinking]  (将 prompt 链接到 action)
    │
    ▼
[InjectionDetection] HandoffInjection, CredentialLeak
    │
    ▼
[AnomalyDetection] MultiAgentAnomaly, ConversationFlow, LatencyAnalyzer,
                   **AgentDriftAnalyzer ← 此处**
    │
    ▼
[AlertEnrichment] → [Output]
```

### 为何在此阶段

- 需要已解析的 HTTP 事件（`http_parser`）获取方法、状态码、响应体积
- 需要 `latency` 事件获取请求-响应时延
- 需要 `sse_processor` 事件获取 agent 工具调用分布
- 需要 `process` 事件获取事件频率基线
- 与 `MultiAgentAnomalyAnalyzer` 同层互补：前者捕捉尖峰异常，后者捕捉渐进漂移
- 下游 `AlertEnrichment` 统一处理两种告警事件源

### 阶段合约修订

| 属性 | 变更前 | 变更后（含 ADA） |
|---|---|---|
| `produces` | `multi_agent_anomaly`, `security_signoff`, `context_pollution` | 新增 `agent_drift` |
| `pass_through` | `true` | `true`（原始事件全部透传） |
| `stateless` | `false` | `false`（维护滚动窗口状态） |

---

## 3. 接口定义

```rust
// collector/src/framework/analyzers/agent_drift.rs

use std::collections::VecDeque;
use std::sync::Arc;
use tokio::sync::Mutex;
use async_trait::async_trait;
use serde::{Deserialize, Serialize};

use super::{Analyzer, AnalyzerError};
use crate::framework::core::Event;
use crate::framework::runners::EventStream;

// ── 配置 ──────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AgentDriftConfig {
    /// 基线窗口大小（事件数）
    pub baseline_window_size: usize,          // 默认 200
    /// 检测窗口大小（事件数）
    pub detection_window_size: usize,         // 默认 50
    /// 基线稳定所需的最小事件数（不足则跳过漂移计算）
    pub min_baseline_events: usize,           // 默认 100
    /// 漂移告警阈值（0.0–1.0，综合 KL 散度归一化）
    pub drift_threshold: f64,                 // 默认 0.25
    /// 每个 agent 的最大内存事件数（环形缓冲区）
    pub max_events_per_agent: usize,          // 默认 500
    /// 统计快照间隔（每 N 个事件计算一次漂移）
    pub snapshot_interval: usize,             // 默认 50
}

impl Default for AgentDriftConfig {
    fn default() -> Self {
        Self {
            baseline_window_size: 200,
            detection_window_size: 50,
            min_baseline_events: 100,
            drift_threshold: 0.25,
            max_events_per_agent: 500,
            snapshot_interval: 50,
        }
    }
}

// ── 漂移维度 ──────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum DriftDimension {
    /// 工具使用频率分布偏移
    ToolUsage,
    /// 请求-响应时延偏移
    Latency,
    /// 响应体积偏移
    OutputSize,
    /// HTTP 错误率偏移
    ErrorRate,
    /// 事件频率偏移
    EventFrequency,
    /// HTTP 方法分布偏移
    HttpMethodDistribution,
}

// ── 漂移评分 ──────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DriftScore {
    /// 综合漂移评分 0.0–1.0
    pub overall: f64,
    /// 各维度独立评分
    pub dimensions: HashMap<DriftDimension, f64>,
    /// 漂移趋势：正值为恶化，负值为恢复
    pub trend: f64,
    /// 漂移方向描述
    pub direction: DriftDirection,
    /// 时间窗口起始（epoch ms）
    pub window_start_ms: u64,
    /// 时间窗口结束（epoch ms）
    pub window_end_ms: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum DriftDirection {
    /// 持续恶化
    Worsening,
    /// 漂移后趋于稳定
    Plateaued,
    /// 正在恢复
    Recovering,
    /// 方向不明
    Uncertain,
}

// ── 内部统计快照 ──────────────────────────────────────

#[derive(Debug, Clone)]
struct EventStats {
    /// 工具名 → 出现次数
    tool_counts: HashMap<String, u64>,
    /// 延迟值列表（微秒）
    latencies_us: Vec<u64>,
    /// 响应体积列表（字节）
    output_sizes: Vec<usize>,
    /// HTTP 状态码计数
    status_codes: HashMap<u16, u64>,
    /// HTTP 方法计数
    http_methods: HashMap<String, u64>,
    /// 窗口内事件总数
    total_events: usize,
    /// 窗口起始时间戳（ns）
    window_start_ns: u64,
    /// 窗口结束时间戳（ns）
    window_end_ns: u64,
}

// ── Per-agent 状态 ────────────────────────────────────

struct AgentDriftState {
    /// 事件环形缓冲区
    events: VecDeque<Event>,
    /// 基线快照（学习完成后锁定，定期刷新）
    baseline: Option<EventStats>,
    /// 当前检测窗口的临时统计
    current_stats: EventStats,
    /// 自上次快照以来的事件计数
    event_count_since_snapshot: usize,
    /// 上一次漂移评分（用于趋势计算）
    previous_score: Option<f64>,
    /// 连续漂移周期的计数（用于方向判断）
    consecutive_drift_periods: u32,
}

// ── Analyzer 主体 ─────────────────────────────────────

/// 检测 agent 行为随时间渐进漂移的流式分析器。
///
/// 工作流程：
/// 1. 接收混合事件流（http_parser、sse_processor、latency、process）
/// 2. 按 agent_root_pid 分组，每个 agent 维护独立环形缓冲区和统计快照
/// 3. 当事件数达到 baseline_window_size 时，计算基线统计分布
/// 4. 之后每 snapshot_interval 个事件，计算检测窗口与基线的 KL 散度
/// 5. KL 散度超过 drift_threshold 时，发出 agent_drift 事件
pub struct AgentDriftAnalyzer {
    config: AgentDriftConfig,
    /// agent_root_pid → AgentDriftState
    states: Arc<Mutex<HashMap<u32, AgentDriftState>>>,
}

impl AgentDriftAnalyzer {
    pub fn new(config: AgentDriftConfig) -> Self {
        Self {
            config,
            states: Arc::new(Mutex::new(HashMap::new())),
        }
    }

    /// 从 event.data 中提取 agent_root_pid
    fn extract_agent_root_pid(event: &Event) -> Option<u32> {
        event.data.get("agent_root_pid")
            .and_then(|v| v.as_u64())
            .map(|n| n as u32)
            .or(Some(event.pid)) // 回退：若 event 无此字段则用 pid
    }

    /// 从 event.data 中提取工具名称（sse_processor 事件）
    fn extract_tool_name(event: &Event) -> Option<&str> {
        if event.source != "sse_processor" {
            return None;
        }
        event.data.get("tool_name")?.as_str()
    }

    /// 从 event.data 中提取 HTTP 方法（http_parser 事件）
    fn extract_http_method(event: &Event) -> Option<&str> {
        if event.source != "http_parser" {
            return None;
        }
        event.data.get("method")?.as_str()
    }

    /// 从 event.data 中提取 HTTP 状态码（http_parser 事件）
    fn extract_status_code(event: &Event) -> Option<u16> {
        if event.source != "http_parser" {
            return None;
        }
        event.data.get("status_code")?.as_u64().map(|n| n as u16)
    }

    /// 从 event.data 中提取延迟值（latency 事件）
    fn extract_latency_us(event: &Event) -> Option<u64> {
        if event.source != "latency" {
            return None;
        }
        event.data.get("latency_us")?.as_u64()
    }

    /// 从 event.data 中提取输出体积
    fn extract_output_size(event: &Event) -> Option<usize> {
        event.data.get("body_size")
            .or_else(|| event.data.get("len"))
            .or_else(|| event.data.get("content_length"))
            .and_then(|v| v.as_u64())
            .map(|n| n as usize)
    }
}

// ── 漂移计算 ──────────────────────────────────────────

impl AgentDriftAnalyzer {
    /// 计算工具分布 KL 散度
    fn compute_tool_kl_divergence(
        baseline: &HashMap<String, u64>,
        current: &HashMap<String, u64>,
    ) -> f64 {
        // 将计数归一化为概率分布
        let baseline_total: u64 = baseline.values().sum::<u64>().max(1);
        let current_total: u64 = current.values().sum::<u64>().max(1);

        // 收集所有键的并集
        let all_keys: HashSet<&String> = baseline.keys().chain(current.keys()).collect();
        let mut kl = 0.0_f64;

        for key in all_keys {
            let p = *baseline.get(key).unwrap_or(&0) as f64 / baseline_total as f64;
            let q = *current.get(key).unwrap_or(&0) as f64 / current_total as f64;
            // 加平滑避免 log(0)
            let p_smooth = p.max(1e-10);
            let q_smooth = q.max(1e-10);
            kl += p_smooth * (p_smooth / q_smooth).ln();
        }
        // 归一化到 [0, 1]
        (kl / (1.0 + kl)).min(1.0)
    }

    /// 计算延迟的归一化均值偏移
    fn compute_latency_shift(baseline_latencies: &[u64], current_latencies: &[u64]) -> f64 {
        if baseline_latencies.is_empty() || current_latencies.is_empty() {
            return 0.0;
        }
        let baseline_mean = baseline_latencies.iter().sum::<u64>() as f64 / baseline_latencies.len() as f64;
        let current_mean = current_latencies.iter().sum::<u64>() as f64 / current_latencies.len() as f64;
        if baseline_mean < 1.0 {
            return 0.0;
        }
        // 相对变化率，tanh 归一化到 [0, 1]
        let ratio = (current_mean / baseline_mean).abs();
        let shift = (ratio - 1.0).abs();
        (shift / (0.5 + shift)).min(1.0)
    }

    /// 计算错误率偏移
    fn compute_error_rate_shift(
        baseline_codes: &HashMap<u16, u64>,
        current_codes: &HashMap<u16, u64>,
    ) -> f64 {
        let error_rate = |codes: &HashMap<u16, u64>| -> f64 {
            let total: u64 = codes.values().sum::<u64>().max(1);
            let errors: u64 = codes.iter()
                .filter(|(code, _)| **code >= 400)
                .map(|(_, count)| count)
                .sum();
            errors as f64 / total as f64
        };
        let baseline_err = error_rate(baseline_codes);
        let current_err = error_rate(current_codes);
        (current_err - baseline_err).max(0.0).min(1.0)
    }

    /// 计算事件频率偏移
    fn compute_frequency_shift(
        baseline_events: usize,
        baseline_duration_ns: u64,
        current_events: usize,
        current_duration_ns: u64,
    ) -> f64 {
        if baseline_duration_ns == 0 || current_duration_ns == 0 {
            return 0.0;
        }
        let baseline_rate = baseline_events as f64 / (baseline_duration_ns as f64 / 1e9);
        let current_rate = current_events as f64 / (current_duration_ns as f64 / 1e9);
        if baseline_rate < 0.001 {
            return 0.0;
        }
        let change = (current_rate / baseline_rate - 1.0).abs();
        (change / (0.5 + change)).min(1.0)
    }

    /// 综合评分：加权平均各维度
    fn compute_overall_drift(dimensions: &HashMap<DriftDimension, f64>) -> f64 {
        // 各维度权重
        let weights: HashMap<DriftDimension, f64> = [
            (DriftDimension::ToolUsage, 0.30),
            (DriftDimension::Latency, 0.20),
            (DriftDimension::ErrorRate, 0.25),
            (DriftDimension::OutputSize, 0.10),
            (DriftDimension::EventFrequency, 0.10),
            (DriftDimension::HttpMethodDistribution, 0.05),
        ].into_iter().collect();

        let weighted_sum: f64 = dimensions.iter()
            .map(|(dim, score)| weights.get(dim).unwrap_or(&0.0) * score)
            .sum();
        let total_weight: f64 = weights.values().sum();
        weighted_sum / total_weight
    }

    /// 判断漂移趋势
    fn determine_direction(
        current_score: f64,
        previous_score: Option<f64>,
        consecutive_periods: u32,
    ) -> DriftDirection {
        match previous_score {
            None => DriftDirection::Uncertain,
            Some(prev) if current_score > prev * 1.1 => {
                if consecutive_periods >= 3 {
                    DriftDirection::Worsening
                } else {
                    DriftDirection::Uncertain
                }
            }
            Some(prev) if current_score < prev * 0.9 => DriftDirection::Recovering,
            _ => DriftDirection::Plateaued,
        }
    }
}

// ── Analyzer trait 实现 ────────────────────────────────

#[async_trait]
impl Analyzer for AgentDriftAnalyzer {
    async fn process(&mut self, stream: EventStream) -> Result<EventStream, AnalyzerError> {
        let states = Arc::clone(&self.states);
        let config = self.config.clone();

        let out = stream.filter_map(move |event| {
            let states = Arc::clone(&states);
            let config = &config;
            async move {
                let agent_pid = Self::extract_agent_root_pid(&event)?;
                let mut map = states.lock().await;
                let state = map.entry(agent_pid).or_insert_with(|| AgentDriftState {
                    events: VecDeque::with_capacity(config.max_events_per_agent),
                    baseline: None,
                    current_stats: EventStats::default(),
                    event_count_since_snapshot: 0,
                    previous_score: None,
                    consecutive_drift_periods: 0,
                });

                // 1. 将事件推入环形缓冲区
                if state.events.len() >= config.max_events_per_agent {
                    state.events.pop_front();
                }
                state.events.push_back(event.clone());

                // 2. 更新当前窗口统计
                Self::update_stats(&event, &mut state.current_stats);

                state.event_count_since_snapshot += 1;

                // 3. 基线学习
                if state.baseline.is_none()
                    && state.events.len() >= config.min_baseline_events
                {
                    state.baseline = Some(Self::build_stats_from_window(
                        &state.events,
                        config.baseline_window_size,
                    ));
                }

                // 4. 漂移检测
                if state.event_count_since_snapshot >= config.snapshot_interval
                    && state.baseline.is_some()
                {
                    let baseline = state.baseline.as_ref().unwrap();
                    let drift = Self::compute_drift(baseline, &state.current_stats);

                    // 判断方向
                    let direction = Self::determine_direction(
                        drift.overall,
                        state.previous_score,
                        state.consecutive_drift_periods,
                    );

                    let mut final_drift = drift;
                    final_drift.direction = direction;

                    // 更新连续周期计数
                    if final_drift.overall > config.drift_threshold {
                        state.consecutive_drift_periods += 1;
                    } else {
                        state.consecutive_drift_periods = 0;
                    }

                    state.previous_score = Some(final_drift.overall);

                    // 重置当前窗口
                    state.current_stats = EventStats::default();
                    state.event_count_since_snapshot = 0;

                    // 发出漂移事件
                    if final_drift.overall > config.drift_threshold {
                        let drift_event = Event::new_with_timestamp(
                            event.timestamp,
                            "agent_drift".to_string(),
                            agent_pid,
                            event.comm.clone(),
                            json!({
                                "overall_score": final_drift.overall,
                                "dimensions": final_drift.dimensions,
                                "trend": final_drift.trend,
                                "direction": final_drift.direction,
                                "window_start_ms": final_drift.window_start_ms,
                                "window_end_ms": final_drift.window_end_ms,
                                "agent_root_pid": agent_pid,
                            }),
                        );
                        return Some(Event::composite(vec![event, drift_event]));
                    }
                }

                Some(event)
            }
        });

        Ok(Box::pin(out))
    }

    fn name(&self) -> &str {
        "agent_drift"
    }
}

impl Default for AgentDriftAnalyzer {
    fn default() -> Self {
        Self::new(AgentDriftConfig::default())
    }
}
```

---

## 4. 数据流

```
Event Stream（混合源）
    │
    │  source: http_parser, sse_processor, latency, process
    │
    ▼
AgentDriftAnalyzer
    │
    ├─ 1. 按 agent_root_pid 分组 ─────────────────────────┐
    │                                                      │
    ├─ 2. 更新环形缓冲区 + 当前窗口统计                      │
    │                                                      │
    ├─ 3. 基线学习（达到 min_baseline_events 时）            │
    │      ┌─ 工具分布 → HashMap<tool_name, count>          │
    │      ├─ 延迟分布 → Vec<u64>（微秒）                   │
    │      ├─ 输出体积 → Vec<usize>（字节）                  │
    │      ├─ HTTP 状态码 → HashMap<u16, count>              │
    │      ├─ HTTP 方法   → HashMap<method, count>           │
    │      └─ 事件频率   → rate (events/sec)                │
    │                                                      │
    ├─ 4. 每 snapshot_interval 事件计算漂移                   │
    │      ├─ 工具分布 KL 散度                               │
    │      ├─ 延迟均值偏移（归一化）                          │
    │      ├─ 错误率差值                                     │
    │      ├─ 输出体积均值偏移                                │
    │      ├─ 事件频率变化                                   │
    │      ├─ HTTP 方法分布 KL 散度                           │
    │      └─ → 加权综合评分                                 │
    │                                                      │
    └─ 5. 漂移 > threshold → 发出 agent_drift 事件           │
           │
           ▼
       原始事件（透传）+ agent_drift 事件（附加）
```

### 事件生命周期

```
时间 →
────────────────────────────────────────────────────────────→
│ 基线学习期          │ 稳定监控期（漂移检测激活）              │
│ (0 - 100 events)    │                                       │
│                     │   snapshot_0  snapshot_1  snapshot_2  │
│ baseline = None     │   ───┬───    ───┬───    ───┬───      │
│                     │      │           │           │         │
│                     │    drift=0.12  drift=0.18  drift=0.31 │
│                     │    (透传)      (透传)      (告警!)     │
└─────────────────────┴───────────────────────────────────────┘
```

---

## 5. 与其他模块的关系

### 消费的事件源

| 事件源 | 提取字段 | 用于维度 |
|---|---|---|
| `http_parser` | `method`, `status_code`, `body_size` | HttpMethodDistribution, ErrorRate, OutputSize |
| `sse_processor` | `tool_name`, `content_length` | ToolUsage, OutputSize |
| `latency` | `latency_us` | Latency |
| `process` | (时间戳用于频率计算) | EventFrequency |

### 与其他 analyzer 的交互

- **LatencyAnalyzer**: ADA 消费 LatencyAnalyzer 产出的 `latency` 事件，无需重复解析原始 SSL 事件
- **HTTPParser**: ADA 消费已解析的 HTTP 事件，假设 `method`/`status_code` 字段已存在
- **MultiAgentAnomalyAnalyzer**: 互补关系——MAA 捕捉突发尖峰，ADA 捕捉渐进趋势。二者告警可被 AlertEnrichment 合并
- **ObservabilityMetricsAnalyzer**: ADA 使用 `agent_root_pid` 分组，需确保该字段在进入 Stage 3 前已填充

### 共享状态

ADA **不**使用 `SharedStateStore`——所有状态自包含在 `HashMap<u32, AgentDriftState>` 中。
理由：每个 agent 的状态独立且无跨 agent 关联需求；使用自己的锁避免与 SharedStateStore 争夺 Mutex。

---

## 6. 配置参数

| 参数 | 默认值 | 说明 |
|---|---|---|
| `baseline_window_size` | 200 | 基线学习窗口大小（事件数） |
| `detection_window_size` | 50 | 检测窗口大小（事件数） |
| `min_baseline_events` | 100 | 开始漂移检测前最少收集的事件数 |
| `drift_threshold` | 0.25 | 漂移告警阈值（0.0–1.0） |
| `max_events_per_agent` | 500 | 每 agent 环形缓冲区容量 |
| `snapshot_interval` | 50 | 统计快照间隔（每 N 个事件计算一次） |

### 调参建议

| 场景 | 建议配置 |
|---|---|
| 高频 agent（>100 events/min） | `baseline_window_size=500`, `snapshot_interval=100` |
| 低频 agent（<10 events/min） | `baseline_window_size=50`, `snapshot_interval=20`, `min_baseline_events=30` |
| 延迟敏感监控 | 降低 `drift_threshold` 至 0.15 |
| 减少告警噪音 | 提高 `drift_threshold` 至 0.40 |

---

## 7. 内存约束

每个 agent 的 `AgentDriftState` 内存估算：

| 组件 | 大小 |
|---|---|
| 环形缓冲区（500 Event，每 Event ~2KB） | ~1 MB |
| 基线 EventStats | ~10 KB |
| 当前 EventStats | ~10 KB |
| 总计 / agent | **~1 MB** |

假设最多 50 个活跃 agent，总内存 **~50 MB**——在目标 ≤1GB 约束之内。

环形缓冲区自动驱逐旧事件（`VecDeque::pop_front`），防止内存无限增长。
不活跃 agent 的状态在检测到 agent 退出时清理（通过 `process` 事件的 `exit` action 触发）。

---

## 8. 风险与缓解

| 风险 | 缓解 |
|---|---|
| 基线被异常行为"污染" | 基线在学习期（前 N 个事件）一次性捕获并锁定；仅当 agent 明确"重置"时刷新 |
| 短期波动触发误报 | 使用连续周期计数（≥3 次连续漂移才判定为 `Worsening`），避免单点误报 |
| 不同 agent 行为差异无法统一阈值 | 每个 agent 独立基线独立评分，不横向比较 |
| 环形缓冲区在大规模部署中内存压力 | `max_events_per_agent` 可配置；超出时驱逐旧事件 |
| KL 散度在小样本下不稳定 | `min_baseline_events` 确保足够样本后再启用检测 |

---

## 9. 测试策略

1. **单元测试**: 合成事件流，验证每种漂移维度的计算正确性
2. **基线学习测试**: 少于 `min_baseline_events` 时不产生漂移事件
3. **阈值测试**: 漂移评分略低于阈值 → 不透出；略高于阈值 → 透出
4. **趋势测试**: 连续 3 次递增漂移 → `direction = Worsening`；递减 → `Recovering`
5. **内存测试**: 超过 `max_events_per_agent` 后旧事件被驱逐
6. **混合事件源测试**: 不同类型事件（http_parser、sse_processor、latency、process）正确路由到对应维度
7. **多 agent 隔离测试**: agent A 的基线不影响 agent B 的漂移计算
8. **回归测试**: 对已知的渐进退化场景（上下文膨胀、API 降级）验证告警产生

---

## 10. 实现优先级

1. **P0**: 核心结构体 + `Analyzer` trait 实现 + 环形缓冲区
2. **P0**: 基线学习逻辑 + 工具分布/错误率 KL 散度计算
3. **P1**: 延迟偏移 + 事件频率偏移计算
4. **P1**: 趋势判定（Worsening/Recovering/Plateaued）
5. **P2**: 输出体积 + HTTP 方法分布维度
6. **P2**: `AgentDriftConfig` serde 支持 + 配置文件加载
7. **P3**: 不活跃 agent 状态清理
8. **P3**: 基线刷新策略（长期运行 agent 的基线老化问题）
