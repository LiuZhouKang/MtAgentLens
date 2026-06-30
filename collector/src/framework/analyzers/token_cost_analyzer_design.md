# TokenCostAnalyzer — 设计文档

> **作者**: lzkk
> **日期**: 2026-06-03
> **状态**: 设计阶段，待评审

## 1. 动机

MtAgentLens 已捕获并解析所有 LLM API 的 HTTP 流量（`http_parser` 事件），但目前没有任何 analyzer 从这些流量中提取 **token 用量和成本信息**。现有异常检测（`LLMApiUsageAnomalyAnalyzer` 设计提案）侧重调用频率和 payload 去重，不涉及 token 维度的分析。

Token 维度的异常往往是严重问题的先兆：

| 异常模式 | 真实场景 |
|----------|----------|
| **单次请求 token 暴增** | 上下文窗口被污染（大量无关内容被注入），或 prompt 注入攻击将超长 payload 塞入会话 |
| **累计 token 消耗异常增长** | Agent 陷入无产出的推理循环（"思考风暴"），每次请求 token 消耗线性增长 |
| **输出 token 远大于输入** | Agent 可能在输出中泄露大量内部状态（数据外泄） |
| **短时间内 token 消耗突增** | 多 Agent 并行失控，或单个 Agent 被利用为 DDoS 跳板 |
| **成本异常** | 按 token 计价的场景下，异常行为直接转化为经济损失 |

### 设计目标

1. 从 `http_parser` 事件中提取 token 用量信息（优先从响应 header `x-*-tokens` 等字段，其次通过 body 估算）
2. 按 agent（`root_pid`）和 API provider 维度聚合 token 消耗
3. 当 token 消耗超过可配置阈值时，发出 `token_cost_anomaly` 事件
4. 无异常时零开销透传——不转换、不缓冲原始事件
5. 会话结束时输出每 agent 的 token 消耗摘要

## 2. 接口设计

### 2.1 Analyzer Trait 实现

```rust
/// Token 用量与成本分析器：从 LLM API 响应中提取 token 信息并检测异常。
pub struct TokenCostAnalyzer {
    /// 配置
    config: TokenCostConfig,

    /// 按 agent root_pid 维度的 token 用量历史
    /// Key: agent_root_pid
    agent_usage: Arc<Mutex<HashMap<u32, AgentTokenUsage>>>,

    /// 已知的 LLM API provider → 价格映射 (USD per 1K tokens)
    pricing: HashMap<String, ProviderPricing>,

    /// 全局累计统计
    global_stats: Arc<Mutex<GlobalTokenStats>>,

    /// 去重冷却：key = (anomaly_type, agent_root_pid), value = last_emission_ns
    dedup_cooldown: Arc<Mutex<HashMap<String, u64>>>,
}
```

### 2.2 配置类型

```rust
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TokenCostConfig {
    /// 是否启用
    pub enabled: bool,

    /// 单 agent 单次请求的 warning 阈值（输入 token 数），超过则触发 LargeRequest 告警
    pub max_input_tokens_per_request: usize,       // 默认 50_000

    /// 单 agent 单次请求的 critical 阈值（输入 token 数）
    pub critical_input_tokens_per_request: usize,   // 默认 100_000

    /// 单 agent 单次请求的输出 token 超过输入 N 倍时触发 OutputBloat 告警
    pub output_to_input_ratio_threshold: f64,       // 默认 3.0

    /// 单 agent 在观测窗口内累计 token 消耗的 warning 阈值
    pub max_cumulative_tokens_per_window: u64,      // 默认 1_000_000

    /// 累计窗口大小（纳秒），默认 5 分钟
    pub cumulative_window_ns: u64,

    /// 全局 token 消耗速率的 warning 阈值（tokens/sec），跨所有 agent
    pub max_global_token_rate: f64,                 // 默认 50_000.0

    /// 异常冷却时间（纳秒），同类型异常不重复告警
    pub anomaly_cooldown_ns: u64,                   // 默认 30s

    /// 最大追踪的 agent 数
    pub max_tracked_agents: usize,                  // 默认 128

    /// token 提取的 header 名称候选列表（按优先级排序）
    pub token_header_names: Vec<String>,
    // 默认: ["x-openai-usage", "anthropic-usage", "x-ratelimit-*-tokens",
    //        "x-total-tokens", "x-token-count", "openai-usage"]

    /// 是否启用 body-based token 估算（当 header 不可用时）
    pub enable_body_estimation: bool,               // 默认 true

    /// body 估算的粗略比例：约每 N 个字符 ≈ 1 token
    pub chars_per_token_estimate: f64,             // 默认 4.0

    /// LLM API 路径模式（用于识别 API 调用），编译后的 regex
    #[serde(skip)]
    pub api_path_patterns: Vec<Regex>,
}
```

### 2.3 核心数据结构

```rust
/// 单个 agent 的 token 用量追踪窗口
struct AgentTokenUsage {
    /// 此 agent 首次观测到的时间
    first_seen_ns: u64,
    /// 最后活跃时间
    last_active_ns: u64,
    /// 最近 N 次 API 调用的 token 记录（环形缓冲区）
    recent_calls: VecDeque<TokenRecord>,
    /// 累计统计
    cumulative: CumulativeTokenStats,
    /// 按 provider 分组的统计
    by_provider: HashMap<String, ProviderTokenStats>,
}

/// 单次 API 调用的 token 记录
struct TokenRecord {
    timestamp_ns: u64,
    /// API provider 标识（从 host + path 推断）
    provider: String,
    /// 模型名称（如 "gpt-4", "claude-sonnet-4-6"）
    model: Option<String>,
    /// 输入 token 数
    input_tokens: u64,
    /// 输出 token 数
    output_tokens: u64,
    /// token 来源：Header / BodyEstimate
    source: TokenCountSource,
    /// 本次调用估算成本 (USD)
    estimated_cost_usd: f64,
    /// 端点路径
    endpoint: String,
    /// HTTP 状态码
    status_code: u16,
}

enum TokenCountSource {
    /// 从 HTTP 响应 header 中精确提取
    Header,
    /// 基于 body 长度估算（精确度较低）
    BodyEstimate,
}

struct CumulativeTokenStats {
    total_input_tokens: u64,
    total_output_tokens: u64,
    total_cost_usd: f64,
    call_count: u64,
    /// 滑动窗口内的 token 数（用于速率检测）
    windowed_tokens: u64,
    window_start_ns: u64,
}

struct GlobalTokenStats {
    total_input_tokens: u64,
    total_output_tokens: u64,
    total_cost_usd: f64,
    total_calls: u64,
    /// 每秒 token 速率（近期）
    recent_token_rate: f64,
    /// 按 provider 分组的全局统计
    by_provider: HashMap<String, ProviderTokenStats>,
}

struct ProviderPricing {
    /// 输入价格 (USD per 1K tokens)
    input_price_per_1k: f64,
    /// 输出价格 (USD per 1K tokens)
    output_price_per_1k: f64,
    /// 缓存命中输入价格 (USD per 1K tokens)，如果有
    cache_input_price_per_1k: Option<f64>,
}

struct ProviderTokenStats {
    input_tokens: u64,
    output_tokens: u64,
    cost_usd: f64,
    call_count: u64,
}
```

### 2.4 Analyzer trait 实现概要

```rust
#[async_trait]
impl Analyzer for TokenCostAnalyzer {
    /// 输入: 上游事件流（主要消费 http_parser 事件）
    /// 输出: 原始事件 + token_cost_anomaly 告警事件（异常时）
    async fn process(&mut self, stream: EventStream) -> Result<EventStream, AnalyzerError>;

    fn name(&self) -> &str {
        "TokenCostAnalyzer"
    }
}

impl Drop for TokenCostAnalyzer {
    fn drop(&mut self) {
        // 输出全局 token 用量摘要
        log_global_token_summary(&self.global_stats);
    }
}
```

## 3. 数据流

```
EventStream (http_parser, sse_processor, ... )
  │
  ▼  stream.flat_map(move |event|)
  │
  ├── 非 http_parser 事件 → 直接透传
  │
  ├── source == "http_parser":
  │     │
  │     ├── 判断是否为 LLM API 请求:
  │     │   - data.method == "POST"
  │     │   - data.path 匹配 api_path_patterns 中的任一 regex
  │     │   — 非 LLM API → 透传，不追踪
  │     │
  │     ├── 提取 token 用量:
  │     │   │
  │     │   ├── 策略 1 (优先): 从 data.headers 中查找 token 相关字段
  │     │   │   - 按 token_header_names 列表顺序尝试匹配
  │     │   │   - 支持 JSON 格式: {"input_tokens": 1500, "output_tokens": 800}
  │     │   │   - 支持简单格式: "1500" (单值 = 总 token)
  │     │   │
  │     │   └── 策略 2 (fallback): 基于 body 长度估算
  │     │       - 仅当 enable_body_estimation == true
  │     │       - input_tokens ≈ request_body.len() / chars_per_token_estimate
  │     │       - output_tokens ≈ response_body.len() / chars_per_token_estimate
  │     │       - source 标记为 BodyEstimate（下游可据此降低置信度）
  │     │
  │     ├── 识别 provider 和模型:
  │     │   - provider: 从 data.headers.host 推断
  │     │     (api.openai.com → "openai", api.anthropic.com → "anthropic")
  │     │   - model: 从 data.body 中提取 "model" 字段
  │     │
  │     ├── 查找/创建 AgentTokenUsage (keyed by event.root_pid())
  │     │
  │     ├── 追加 TokenRecord 到 agent 环形缓冲区
  │     │   + 更新 cumulative 统计
  │     │   + 更新 global_stats
  │     │   + 计算 estimated_cost_usd (查 pricing 表)
  │     │
  │     ├── 运行检测规则:
  │     │   │
  │     │   ├── LargeRequest:
  │     │   │   input_tokens > max_input_tokens_per_request
  │     │   │   → warning; input_tokens > critical → critical
  │     │   │
  │     │   ├── OutputBloat:
  │     │   │   output_tokens / input_tokens > output_to_input_ratio_threshold
  │     │   │   → warning
  │     │   │
  │     │   ├── CumulativeOverrun:
  │     │   │   滑动窗口内累计 input_tokens > max_cumulative_tokens_per_window
  │     │   │   → warning
  │     │   │
  │     │   └── GlobalRateSpike:
  │     │       全局 token 速率 > max_global_token_rate tokens/sec
  │     │       → critical (可能的多 agent 并行失控)
  │     │
  │     ├── 若匹配任一规则且超过冷却时间:
  │     │   └── 发出 token_cost_anomaly 事件 (插入到 stream)
  │     │
  │     └── 透传原始 http_parser 事件
  │
  └── 定时清理: 过期 agent (last_active + TTL) + 冷却表
        │
        ▼
  EventStream (原始事件 + token_cost_anomaly 事件)
```

### 3.1 流式处理决策

**选择**: `.flat_map()` — 每个事件 1:1（正常）或 1:2（异常时插入告警事件）转换。

**理由**:
- `.filter_map()` 只能返回 `Option<Event>`，无法在保留原事件的同时额外插入新事件
- `.scan()` 适合有状态聚合，但与 `.flat_map()` 配合 `Arc<Mutex<>>` 的模式更一致
- `Arc<Mutex<>>` 锁持有时间极短（HashMap 查找 + 环形缓冲区 push + 规则评估 < 10µs）

### 3.2 告警事件 Schema

```json
{
  "source": "token_cost_anomaly",
  "event_type": "TOKEN_LARGE_REQUEST",
  "timestamp": 1717430000000000,
  "pid": 12345,
  "comm": "claude",
  "data": {
    "anomaly_type": "large_request",
    "agent_root_pid": 100,
    "severity": "warning",
    "input_tokens": 85000,
    "output_tokens": 1200,
    "threshold": 50000,
    "provider": "anthropic",
    "model": "claude-sonnet-4-6",
    "estimated_cost_usd": 0.27,
    "token_source": "header",
    "cumulative_session_input_tokens": 450000,
    "cumulative_session_cost_usd": 1.35,
    "description": "Agent 100 sent request with 85000 input tokens (threshold: 50000)"
  }
}
```

## 4. Pipeline 集成

### 4.1 阶段定位

`PipelineStage::AnomalyDetection` (Stage 3) — 与 `MultiAgentAnomalyAnalyzer` 和 `LLMApiUsageAnomalyAnalyzer`（提案中）并列。

**理由**:
- 需要 upstream HTTPParser 已解析的 `http_parser` 事件
- 产出 `token_cost_anomaly` 告警供 downstream AlertEnrichment 归并
- 不属于预处理层（它在检测，而非转换）
- 与 `LLMApiUsageAnomalyAnalyzer` 互补：一个看调用频率/模式，一个看 token 维度

### 4.2 事件消费/生产契约

| 维度 | 值 |
|------|-----|
| **消费** | `http_parser`（主），`sse_processor`（辅助，提取 conversation_id） |
| **生产** | `token_cost_anomaly`（告警），原始事件透传 |
| **阶段** | `PipelineStage::AnomalyDetection` |
| **前置依赖** | Preprocessing（HTTPParser, TimestampNormalizer） |
| **pass_through** | true（不消费原始事件，仅从中检测） |

### 4.3 与 LLMApiUsageAnomalyAnalyzer 的分工

| 维度 | LLMApiUsageAnomalyAnalyzer | TokenCostAnalyzer |
|------|---------------------------|-------------------|
| 关注点 | API 调用频率、payload 重复、响应大小 | Token 用量、成本、输入/输出比例 |
| 检测模式 | RateBurst, RepeatedRequest, OversizedRequest, NewDestination, ErrorStorm | LargeRequest, OutputBloat, CumulativeOverrun, GlobalRateSpike |
| 成本感知 | 无 | 按 provider 和模型计算 USD 成本 |
| 会话摘要 | 无 | 会话结束时输出完整 token 消耗 report |
| 告警源标签 | `llm_api_anomaly` | `token_cost_anomaly` |

两者互补：同一个 LLM API 调用既可能触发频率告警（LAUA），也可能同时触发 token 告警（TCA）。

### 4.4 Pipeline 架构图（含新 analyzer）

```
Preprocessing (stage 0)
  ├── TimestampNormalizer
  ├── SSEProcessor
  ├── HTTPParser              ← 产生 http_parser 事件
  ├── SSLFilter
  └── HTTPFilter
       │
CausalLinking (stage 1)
       │
InjectionDetection (stage 2)
  ├── HandoffInjectionAnalyzer
  └── CredentialLeakAnalyzer
       │
AnomalyDetection (stage 3)
  ├── MultiAgentAnomalyAnalyzer   ← 进程级异常
  ├── ContextPollutionAnalyzer    ← 上下文污染
  ├── SessionBoundaryAnalyzer     ← 会话边界（提案中）
  ├── LLMApiUsageAnomalyAnalyzer  ← API 调用频率（提案中）
  └── TokenCostAnalyzer           ← **新增：token 用量/成本**
       │
AlertEnrichment (stage 4)
  └── AlertEnrichmentAnalyzer     ← 消费 token_cost_anomaly 并归并
       │
Output (stage 5)
```

### 4.5 注册方式

```rust
// 在 pipeline builder 中:
agent = agent.add_global_analyzer(Box::new(
    TokenCostAnalyzer::new(TokenCostConfig::default())
));
```

## 5. 提取 token 信息的策略细节

### 5.1 已知 LLM API 的 Response Header 格式

| Provider | Header 字段 | 格式 |
|----------|------------|------|
| OpenAI | `x-openai-usage` 或 `openai-usage` | JSON: `{"prompt_tokens": 1500, "completion_tokens": 800, "total_tokens": 2300}` |
| Anthropic | `anthropic-usage` 或 `request-id` 同级的 usage 在 body 中 | body JSON: `{"usage": {"input_tokens": 1500, "output_tokens": 800}}` |
| Google (Gemini) | 在 response body 中 | `{"usageMetadata": {"promptTokenCount": 1500, "candidatesTokenCount": 800}}` |
| 通用 | `x-total-tokens`, `x-token-count` | 单数值 |

### 5.2 提取优先级

```
1. Header 明确字段 (准确度 100%)
   ↓ 失败
2. Response body JSON 中的 usage 字段 (准确度 100%)
   ↓ 失败
3. 基于 body 字符数估算 (准确度 ~80%)
   - 输入: request body length / 4.0
   - 输出: response body length / 4.0
   ↓ 失败
4. 跳过 (无可用 token 信息，仍然透传事件但不追踪)
```

### 5.3 Provider 定价表（默认配置）

```rust
fn default_pricing() -> HashMap<String, ProviderPricing> {
    let mut m = HashMap::new();
    m.insert("openai".into(), ProviderPricing {
        input_price_per_1k: 0.0025,    // GPT-4o
        output_price_per_1k: 0.01,
        cache_input_price_per_1k: Some(0.00125),
    });
    m.insert("anthropic".into(), ProviderPricing {
        input_price_per_1k: 0.003,     // Claude Sonnet 4
        output_price_per_1k: 0.015,
        cache_input_price_per_1k: Some(0.0003),
    });
    m.insert("google".into(), ProviderPricing {
        input_price_per_1k: 0.00125,   // Gemini 1.5 Pro
        output_price_per_1k: 0.005,
        cache_input_price_per_1k: None,
    });
    m
}
```

## 6. 测试策略

### 6.1 单元测试

| 测试 | 验证内容 |
|------|----------|
| `extract_tokens_from_openai_header` | 从 `x-openai-usage` JSON 解析 prompt_tokens, completion_tokens |
| `extract_tokens_from_anthropic_body` | 从 response body JSON `usage.input_tokens` 提取 |
| `extract_tokens_from_gemini_body` | 从 `usageMetadata.promptTokenCount` 提取 |
| `extract_tokens_from_simple_header` | 从 `x-total-tokens: 2300` 提取单值 |
| `fallback_to_body_estimation` | 无 header 时基于 body 长度估算 |
| `skip_non_llm_api_request` | GET /static/style.css → 不追踪 |
| `skip_missing_token_info` | POST /v1/chat 但无 header 且无 body → 透传不追踪 |
| `large_request_detected` | input_tokens=80000 > threshold=50000 → 触发 LargeRequest warning |
| `large_request_critical` | input_tokens=120000 > critical=100000 → 触发 critical |
| `output_bloat_detected` | output/input = 5.0 > threshold=3.0 → 触发 OutputBloat |
| `cumulative_overrun_detected` | 5 分钟内累计 input=1200000 > threshold=1000000 → 触发 |
| `global_rate_spike_detected` | 全局速率 75000 tok/s > threshold=50000 → 触发 |
| `cooldown_prevents_duplicate` | 冷却窗口内同类型异常不重复告警 |
| `per_agent_isolation` | Agent A 的大量请求不触发 Agent B 的累计告警 |
| `cost_calculation_openai` | 1500 input + 800 output → $0.01175 (OpenAI 定价) |
| `cost_zero_for_unknown_provider` | 未知 provider → estimated_cost_usd = 0.0 |
| `non_http_events_passthrough` | process、system 等事件直接透传 |
| `global_stats_session_summary` | Drop 时输出正确的累计统计 |

### 6.2 集成测试场景

1. **正常 LLM 调用**: 模拟 10 次 GPT-4o 调用，每次 ~2000 input + ~800 output → 0 条告警，累计 cost 正确
2. **上下文窗口溢出**: 单次请求 input_tokens=120000 → 1 条 TOKEN_LARGE_REQUEST (critical)
3. **数据外泄模式**: 连续 3 次请求 output_tokens 远大于 input_tokens (ratio > 5.0) → 3 条 TOKEN_OUTPUT_BLOAT
4. **多 Agent 并行**: 3 个 Agent 各自正常调用，全局速率正常 → 0 条告警
5. **Agent 思考风暴**: 单 Agent 在 5 分钟内累计消耗 2M input tokens → 1 条 TOKEN_CUMULATIVE_OVERRUN
6. **Header 缺失 → Body 估算**: 模拟无 token header 的 API → BodyEstimate source, 较低置信度但仍追踪

## 7. 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| **token header 格式变更** | 提取失败，回退到 body 估算或跳过 | 多级 fallback 策略 + 可配置 header 名称列表 |
| **body 估算不准确** | 告警不可靠（误报/漏报） | BodyEstimate 事件的 severity 降一级；`chars_per_token_estimate` 可配置 |
| **定价表过时** | 成本计算不准 | 定价表在 config 中可覆盖；零依赖定价信息，不影响检测逻辑 |
| **高吞吐下 HashMap 膨胀** | 内存泄漏 | `max_tracked_agents` 硬上限 + LRU 淘汰；最坏情况 128 × ~20KB = ~2.5MB |
| **锁竞争** | 延迟增加 | 当前架构单 analyzer 串行处理事件，实际无竞争；未来可用 `DashMap` |
| **非 LLM HTTP 流量被误分类** | 误将静态资源等计为 token 消耗 | `api_path_patterns` regex 白名单 + method=POST 前置过滤 |
| **流式响应 (SSE) 场景** | SSE 分片导致单次请求的 token 信息分散在多个事件中 | 依赖上游 SSEProcessor 已合并的完整响应；若未合并，则仅在最终 chunk 中提取 |

## 8. 待决策事项

1. **token header 解析逻辑的模块化**: 是否将 token header 解析抽取为 `common.rs` 中的公共函数，
   供 `LLMApiUsageAnomalyAnalyzer` 等其他 analyzer 复用？建议：先内联实现，后续按需抽取。

2. **模型级别的精细化定价**: 当前默认定价表只区分 provider (openai/anthropic/google)，
   不区分同 provider 下的不同模型（如 GPT-4o vs GPT-4o-mini）。是否需要从 event data 中提取
   模型名并做精细化定价？建议：v2 迭代，先按 provider 简化实现。

3. **会话摘要的输出格式**: Drop 时的 token 消耗摘要是否需要通过 `ObservabilityMetricsAnalyzer`
   以结构化指标形式发出（而非仅 stderr 输出）？建议：v1 先 stderr + `print_global_*` 模式与
   `SSLFilter` 保持一致，v2 接入指标系统。

---

**请团队成员评审以上设计，重点关注**:
- Token 提取的多级 fallback 策略是否覆盖了已知 LLM API 格式
- `token_cost_anomaly` 事件 schema 是否与 `AlertEnrichment` 兼容
- 与 `LLMApiUsageAnomalyAnalyzer` 的分工边界是否清晰
- 是否有遗漏的 token 维度异常模式
