# LLMApiUsageAnomalyAnalyzer 设计文档

> **设计状态**: 提案阶段
> **目标阶段**: AnomalyDetection (Stage 3)
> **模块**: `collector/src/framework/analyzers/llm_api_anomaly.rs`
> **输出事件源标签**: `llm_api_anomaly`

---

## 1. 名称与目的

**LLMApiUsageAnomalyAnalyzer (LAUA)**

监控 LLM API 调用模式，基于已解析的 HTTP 流量检测用量异常。AgentSight 已捕获并解析所有
SSL/TLS HTTP 流量——但现有的 anomaly 检测侧重于进程行为（命令循环、资源风暴），而非
API 用量模式。LAUA 填补了这一空白。

### 问题

以 LLM 为核心的自主 agent 通过 HTTP API 调用 LLM 来执行推理。异常 API 行为通常预示着严重问题：

| 异常模式 | 真实场景 |
|---|---|
| 突发请求（10 秒内 >20 次调用） | Agent 陷入重试循环，无退避，消耗 API 额度 |
| 相同请求重复（>5 次 payload 相同） | Agent 陷入确定性的重试-失败循环 |
| 单次请求 payload 远超平均值 | 上下文窗口膨胀（上下文污染），或数据外泄尝试 |
| 新发现的目的地（首次出现的 API host） | Agent 调用未预期的端点——供应链风险 |
| 持续高频率（5 分钟内 >100 次调用） | 失控 agent 进程，"失控循环"信号 |

不具备此类检测时，这些模式仅以原始 HTTP 事件形式呈现，淹没在时间线中，直至
API 账单到达才会被发现。

### 目标

1. 维护每个 agent（以 root_pid 为键）的滚动窗口 API 调用历史
2. 当检测到异常模式时，发出带严重级别的 `llm_api_anomaly` 事件
3. 无异常时零开销透传——不转换、不缓冲
4. 有界内存：每个 agent 的窗口环形缓冲区容量固定，防止 agent 泄漏

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
[AnomalyDetection] MultiAgentAnomaly,  **LLMApiUsageAnomalyAnalyzer ← 此处**
    │
    ▼
[AlertEnrichment] → [Output]
```

### 为何在此阶段

- LAUA 需要*已解析的 HTTP 事件*（`source == "http_parser"`），其中 `method`、`path`、
  `body` 和 `status_code` 均已从原始 SSL 载荷中提取。这些事件需经 Preprocessing 阶段
  处理后才能使用。
- 将 LAUA 置于 AnomalyDetection 阶段，使其与 `MultiAgentAnomalyAnalyzer` 并列——下游
  `AlertEnrichment` 同时处理两种异常事件源。
- 若 LAUA 置于 Preprocessing 阶段，则无法区分 API 调用与其他 HTTP 流量（静态资源等）；
  HTTPFilter 之后、AnomalyDetection 阶段的位置使其能假设事件格式干净。

### 阶段合约修订

| 属性 | 变更前 | 变更后（含 LAUA） |
|---|---|---|
| `produces` | `multi_agent_anomaly`, `security_signoff`, `context_pollution` | 新增 `llm_api_anomaly` |
| `length_preserving` | `true` | `false`（声明 `StreamContractProvider`） |
| `pass_through` | `true` | `true`（原始 HTTP 事件不受影响） |

---

## 3. 接口：EventStream 合约

### 3.1 消费的事件格式

LAUA 检查 `event.source == "http_parser"` 的事件。非 HTTP 事件直接透传。

| 字段 | 用途 | 缺失时的处理 |
|---|---|---|
| `data.method` | HTTP 方法（通常为 POST） | 非 POST 时跳过 |
| `data.path` | URL 路径（如 `/v1/chat/completions`） | 用于端点指纹识别 |
| `data.headers.host` | 目标主机 | 用于目的地追踪 |
| `data.body` | 请求载荷（LLM 提示词/消息） | 用于 payload 指纹哈希 |
| `data.status_code` | HTTP 状态码 | 用于检测错误风暴 |
| `event.pid` | 发起进程 PID | 用于 agent 关联 |
| `event.timestamp` | 事件时间戳（epoch ns） | 用于基于时间的窗口 |

**"API 调用"的判定标准**：HTTP POST/PATCH 请求，`body` 或 `content_length > 0`，
路径匹配 LLM API 模式（`/v1/chat/*`、`/v1/completions`、`/api/generate` 等——可通过
配置扩展）。

### 3.2 输出流

- **非 HTTP 事件及正常的 API 调用**：原样透传（相同 `Event` 对象按值传递）
- **检测到异常时**：发出 `source = "llm_api_anomaly"` 的事件，包含结构化异常载荷。
  原始 HTTP 事件*同时*透传——LAUA 不消费事件，仅从中检测。
- **异常事件在原始事件之后发出**，因此下游分析器先看到触发事件的上下文，再看到异常。

### 3.3 异常事件载荷结构

```json
{
  "anomaly_type": "rate_burst",
  "agent_root_pid": 12345,
  "severity": "warning",
  "window_start_ns": 1700000000000000000,
  "window_end_ns": 1700000001000000000,
  "call_count": 25,
  "threshold": 20,
  "avg_interval_us": 40000,
  "affected_endpoints": ["api.openai.com/v1/chat/completions"],
  "sample_payload_hash": "a1b2c3d4...",
  "description": "Rate burst: 25 API calls in 1.0s window (threshold: 20)"
}
```

### 3.4 Trait 实现

```rust
impl Analyzer for LLMApiUsageAnomalyAnalyzer { ... }
impl AnalyzerLifecycle for LLMApiUsageAnomalyAnalyzer {
    // on_stop(): 刷新并记录每 agent 的统计信息
}
impl StreamContractProvider for LLMApiUsageAnomalyAnalyzer {
    fn output_contract(&self) -> StreamContract {
        StreamContract::non_preserving()
    }
}
impl DrainableAnalyzer for LLMApiUsageAnomalyAnalyzer {
    // drain(): 返回部分窗口的异常（若 within-window 计数达阈值但尚未发出）
}
```

`SharedStateAware` 未实现——LAUA 无跨分析器状态依赖。`AgentTopology` 映射通过
event 的 `root_pid` 字段访问（由 runner 提供）。

---

## 4. 关键数据结构

### 4.1 每 Agent 调用窗口

```rust
/// 单个 agent 最近 API 调用的环形缓冲区。
struct AgentCallWindow {
    /// 固定容量的环形缓冲区，存储 (timestamp_ns, endpoint_hash, payload_hash)。
    calls: VecDeque<CallRecord>,
    /// 每 agent 的统计累加器。
    stats: AgentApiStats,
    /// 上次异常发出的时间戳（用于冷却）。
    last_anomaly_emission: HashMap<AnomalyType, u64>,
}

struct CallRecord {
    timestamp_ns: u64,
    /// "host + path" 的哈希值，用于端点去重。
    endpoint_hash: u64,
    /// 截断后 body 的哈希值，用于重复检测。
    payload_hash: u64,
    /// 响应状态码。
    status_code: u16,
    /// Content-Length（若已解析），否则为 0。
    response_size: usize,
}

struct AgentApiStats {
    total_calls: u64,
    total_response_bytes: u64,
    error_count: u64,        // status >= 400
    unique_endpoints: HashSet<u64>,
}
```

### 4.2 检测规则引擎

```rust
/// 异常检测规则。每条规则是一个 (condition, AnomalyType, severity) 三元组。
struct DetectionRule {
    anomaly_type: AnomalyType,
    /// 触发规则的最小调用次数。
    min_calls: usize,
    /// 评估的时间窗口（纳秒）。
    window_ns: u64,
    /// 该窗口内的最大调用次数。
    max_calls_in_window: usize,
    /// 被视为"相同请求"的 payload 哈希精确匹配次数。
    max_duplicate_payloads: usize,
    /// 单次请求的最大响应大小（字节）。
    max_single_response_bytes: usize,
    /// 异常严重级别。
    severity: Severity,
    /// 连续异常发出的最小冷却时间。
    cooldown_ns: u64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
enum AnomalyType {
    /// 短时间内调用次数过多。
    RateBurst,
    /// 相同 payload 重复发送（可能为无限循环）。
    RepeatedIdenticalRequest,
    /// 单次请求异常庞大（上下文污染/数据外泄）。
    OversizedRequest,
    /// 连接了之前未见过的 LLM API 端点。
    NewDestination,
    /// 持续高频率（持续高调用频率）。
    SustainedHighFrequency,
    /// 错误状态码比例过高。
    ErrorStorm,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
enum Severity {
    Info,
    Warning,
    Critical,
}
```

### 4.3 分析器状态

```rust
pub struct LLMApiUsageAnomalyAnalyzer {
    /// 每 agent 的窗口，以 root_pid 为键。
    windows: Arc<Mutex<HashMap<u32, AgentCallWindow>>>,
    /// 检测规则（可配置）。
    rules: Vec<DetectionRule>,
    /// 可识别的 LLM API 端点路径模式。
    api_path_patterns: Vec<regex::Regex>,
    /// 已知目标白名单（在这些目标之外触发 NewDestination）。
    known_destinations: HashSet<String>,
    /// 全局统计信息。
    global_stats: Arc<Mutex<GlobalStats>>,
    /// 每个窗口的最大 agent 数（逐出策略：LRU）。
    max_tracked_agents: usize,
    /// 配置。
    config: LlmApiAnomalyConfig,
}
```

### 4.4 配置

```rust
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LlmApiAnomalyConfig {
    /// 每 agent 环形缓冲区的容量。
    pub window_capacity: usize,
    /// 速率突发：时间窗口及最大调用次数。
    pub rate_burst_window_ns: u64,
    pub rate_burst_max_calls: usize,
    /// 重复请求：触发前需连续相同 payload 的次数。
    pub repeated_request_threshold: usize,
    /// 超大请求：触发异常的最小响应体大小（字节）。
    pub oversized_request_threshold_bytes: usize,
    /// 持续高频：长时间窗口及最大调用次数。
    pub sustained_high_freq_window_ns: u64,
    pub sustained_high_freq_max_calls: usize,
    /// 错误风暴：视为异常的最小错误比例。
    pub error_storm_threshold_ratio: f64,
    /// 已知的 LLM API 端点（正则模式）。
    pub api_path_patterns: Vec<String>,
    /// 已知目标主机（新目标检测）。
    pub known_destinations: Vec<String>,
    /// 异常间最小冷却时间（纳秒）。
    pub anomaly_cooldown_ns: u64,
    /// 追踪的最大 agent 数。
    pub max_tracked_agents: usize,
}

impl Default for LlmApiAnomalyConfig {
    fn default() -> Self {
        Self {
            window_capacity: 200,
            rate_burst_window_ns: 10_000_000_000,   // 10 秒
            rate_burst_max_calls: 20,
            repeated_request_threshold: 5,
            oversized_request_threshold_bytes: 1_048_576, // 1 MB
            sustained_high_freq_window_ns: 300_000_000_000, // 5 分钟
            sustained_high_freq_max_calls: 100,
            error_storm_threshold_ratio: 0.5,        // 50%+ 为错误
            api_path_patterns: vec![
                r"/v1/chat/completions".into(),
                r"/v1/completions".into(),
                r"/api/generate".into(),
                r"/v1/messages".into(),
            ],
            known_destinations: vec![
                "api.openai.com".into(),
                "api.anthropic.com".into(),
                "generativelanguage.googleapis.com".into(),
            ],
            anomaly_cooldown_ns: 30_000_000_000,     // 每种异常类型 30 秒
            max_tracked_agents: 128,
        }
    }
}
```

---

## 5. 数据流

### 5.1 每事件处理

```
HTTP event 到达 (source = "http_parser")
    │
    ├─ 非 POST 或非 LLM API 路径？ → 透传，不追踪
    │
    ├─ 提取：endpoint_hash, payload_hash, status_code, response_size
    │
    ├─ 查找/创建 AgentCallWindow（以 root_pid 为键）
    │
    ├─ 追加 CallRecord 到环形缓冲区
    │
    ├─ 运行每条 DetectionRule：
    │   │
    │   ├─ RateBurst：统计窗口内调用次数，与阈值比较
    │   ├─ RepeatedIdenticalRequest：统计连续相同 payload_hash 次数
    │   ├─ OversizedRequest：检查 response_size
    │   ├─ NewDestination：检查 endpoint_hash 是否在已知目标集合中
    │   ├─ SustainedHighFrequency：统计长时间窗口内调用次数
    │   └─ ErrorStorm：计算窗口内 error_count / total_calls
    │
    ├─ 若匹配任一规则（且超过冷却时间）：
    │   └─ 发出 llm_api_anomaly 事件
    │
    └─ 透传原始 HTTP 事件
```

### 5.2 流处理伪代码

```rust
async fn process(&mut self, stream: EventStream) -> Result<EventStream, AnalyzerError> {
    let windows = Arc::clone(&self.windows);
    let rules = self.rules.clone();
    let config = self.config.clone();

    let out = stream.flat_map(move |event| {
        let windows = Arc::clone(&windows);
        let rules = rules.clone();
        async move {
            let mut results = vec![event.clone()];

            // 仅处理已解析的 HTTP 请求
            if event.source != "http_parser" {
                return futures::stream::iter(results);
            }

            // 检查是否为 LLM API 调用
            let Some(call_record) = extract_call_record(&event, &config) else {
                return futures::stream::iter(results);
            };

            // 更新 agent 窗口
            let mut map = windows.lock().await;
            let window = map.entry(event.root_pid())
                .or_insert_with(|| AgentCallWindow::new(config.window_capacity));

            window.push(call_record);
            window.stats.increment(&call_record);

            // 执行检测规则
            let anomalies = detect_anomalies(window, &rules, event.timestamp);
            for anomaly in anomalies {
                results.push(anomaly.to_event(&event));
            }

            futures::stream::iter(results)
        }
    });

    Ok(Box::pin(out))
}
```

---

## 6. 错误处理策略

| 场景 | 行为 |
|---|---|
| `data.body` 缺失或为空 | 跳过 API 调用分类（非 LLM 请求） |
| `data.headers` 非 JSON 对象 | 跳过请求，递增 `unparseable_count` |
| Agent 窗口达到容量上限 | 逐出最旧记录（环形缓冲区语义） |
| 追踪的 agent 数超过 `max_tracked_agents` | 逐出最不活跃的 agent（LRU） |
| 规则正则编译失败 | 启动时 panic（配置错误，非运行时） |
| 锁争用（高吞吐量） | 窗口更新为 O(1) 追加 + O(rules) 扫描；锁持有时间 < 10µs |
| 时间戳为 0（无效事件） | 跳过，记录 diagnostic |

### 自愈特性

所有 agent 窗口均为纯内存结构。若新 agent 出现，旧的逐出 agent 在容量满时
自然被逐出。重新关联至同一 PID 时，agent 的状态完全重建——无残留腐败。

### 指标（通过 `ObservabilityMetricsAnalyzer` 对外暴露）

| 指标 | 类型 | 说明 |
|---|---|---|
| `laur_events_inspected` | Counter | 检查的事件总数 |
| `laur_api_calls_tracked` | Counter | 分类为 LLM API 调用的事件数 |
| `laur_anomalies_emitted` | Counter | 发出的异常事件总数（按 anomaly_type 细分） |
| `laur_agents_tracked` | Gauge | 当前活跃的 agent 窗口数 |
| `laur_window_evictions` | Counter | 因容量满导致的环形缓冲区逐出次数 |
| `laur_unparseable_http` | Counter | 包含无法解析的 body/headers 的事件数 |

---

## 7. 测试方案

### 7.1 单元测试（模块内，`#[cfg(test)]`）

**A 类：API 调用提取**
- `test_identifies_openai_chat_request` — POST /v1/chat/completions，含 body → 分类为 API 调用
- `test_ignores_get_request` — GET /v1/models → 跳过
- `test_ignores_static_asset` — GET /favicon.ico → 跳过
- `test_missing_body_still_classified` — POST 但无 body → 仍追踪（仅端点）

**B 类：窗口管理**
- `test_window_push_and_query` — 插入 3 个调用记录，窗口返回所有 3 条
- `test_window_capacity_eviction` — 插入 N+1 条到 N 容量窗口，最旧记录被逐出
- `test_per_agent_isolation` — Agent A 和 Agent B 拥有独立窗口

**C 类：异常检测规则**
- `test_rate_burst_detected` — 1 秒内 25 次调用 → 触发 RateBurst
- `test_rate_burst_not_triggered_below_threshold` — 1 秒内 10 次调用 → 不触发
- `test_repeated_identical_request` — 6 次相同 payload_hash → 触发
- `test_repeated_different_payloads_no_trigger` — 5 次不同 payload → 不触发
- `test_oversized_request_detected` — 2MB 响应 → 触发
- `test_new_destination_detected` — 未知主机 → 触发 NewDestination
- `test_known_destination_no_trigger` — api.openai.com → 不触发
- `test_error_storm_detected` — 10 次调用中 8 次状态码为 5xx → 触发
- `test_cooldown_prevents_duplicate_anomaly` — 第二次速率突发在 30 秒内 → 抑制

### 7.2 合约测试（在 `pipeline_contract.rs` 中）

- 验证 AnomalyDetection 的 `StageContract::produces` 包含 `"llm_api_anomaly"`
- 验证 `StreamContract::non_preserving()` 满足下游 AlertEnrichment 的要求

### 7.3 集成测试

- **真实流量回放**：重放包含 LLM API 调用的已录制 HTTP 会话。验证 LAUA 正确
  分类调用并检测注入的异常模式。
- **多 agent 场景**：模拟 5 个 agent 并发调用 LLM API，其中一个陷入循环。
  验证仅触发循环 agent 的异常，其他 4 个不受影响。
- **反回归检查**：启用 LAUA 运行完整的攻击场景套件（上下文污染、交接注入）。
  验证现有所有检测仍能正常触发——LAUA 不得干扰任何现有分析器。

---

## 8. 风险评估

| 风险 | 严重级别 | 缓解措施 |
|---|---|---|
| 误报——合法的高频调用被标记 | 中等（用户信任度） | 可配置的阈值；30 秒异常冷却；已知目标白名单 |
| 漏报——新 LLM API 模式未被识别 | 中等（覆盖率） | 可配置的 `api_path_patterns`；默认支持主流 LLM 厂商；未知端点按 `NewDestination` 处理 |
| 锁争用——高吞吐量下 >50k 事件/秒 | 低（性能） | O(1) 追加 + O(rules) 扫描（通常 6 条规则）；每 agent 隔离的锁粒度；可按 agent PID 分片 |
| 内存增长——长期追踪众多 agent | 低（资源） | 固定每 agent 容量（默认 200）；agent 数硬上限（默认 128）；LRU 逐出 |
| 跨会话 agent PID 复用 | 低（正确性） | Agent 窗口在 `on_start()` 时清空；每个会话创建新的分析器实例 |

---

## 9. 摘要（供可观测性报告使用）

**LLMApiUsageAnomalyAnalyzer (LAUA)** 用于解决 AgentSight 分析器套件中缺失的
问题：现有系统捕获所有 LLM HTTP 流量，但没有分析器检测 API *用量*层面的异常。
LAUA 维护每个 agent 的滚动窗口记录，追踪 API 调用频率、payload 去重、响应大小
及端点多样性，并在模式表明 agent 行为异常时发出结构化异常事件。

关键设计特性：
- **无侵入**：透传架构不对正常事件进行任何转换。在无异常的稳态下，开销接近于零。
- **资源有界**：每 agent 固定容量环形缓冲区（默认 200 条记录，~2.5 KB/agent）。
  追踪上限 128 个 agent（~320 KB 总内存）。
- **可配置检测**：6 条规则（RateBurst、RepeatedIdenticalRequest、OversizedRequest、
  NewDestination、SustainedHighFrequency、ErrorStorm），每一条均可通过
  `LlmApiAnomalyConfig` 调整阈值。
- **Multi-agent 安全**：每 agent 的隔离窗口确保 Agent A 的行为绝不会触发 Agent B
  的异常。
- **可观测**：对外暴露 6 项指标，通过现有的 ObservabilityMetricsAnalyzer 管道
  接入前端。

预估影响：
- **检测覆盖率**：填补 6 种全新的异常类别（此前均不可见）
- **CPU 开销**：每个 HTTP 事件 < 5µs 用于分类和规则评估（无 LLM 调用、无 IO）
- **误报率**：由可配置阈值和冷却机制控制在低水平

LAUA 计划置于 6 阶段管道的 AnomalyDetection 阶段（Stage 3），与现有的
`MultiAgentAnomalyAnalyzer` 并列。两个分析器共同提供互补的信号：
MultiAgent 覆盖进程级异常，LAUA 覆盖 API 级异常。实现预计约 350 行 Rust 代码
加 250 行测试，通过 `add_anomaly_detector()` 调用集成至 `PipelineBuilder`。
