# LLMApiTracker 设计文档

## 概述

**LLMApiTracker** 是一个有状态的流式 analyzer，检测并追踪 LLM API 调用（OpenAI、Anthropic 等），从 HTTP 事件中提取模型、token 用量、延迟等结构化元数据，并将请求-响应对合并为单一富事件。

### 动机

MtAgentPrism 监控 AI Agent，但当前流水线对 LLM API 调用的可见性仅限于原始 HTTP 报文。没有结构化的：
- 模型识别（用的哪个模型？）
- Token 消耗追踪（prompt/completion tokens）
- 请求-响应延迟配对（单次调用花了多久？）
- 流式 vs 非流式请求区分

LLMApiTracker 填补这个空白，让下游分析器（异常检测、责任追溯）能直接消费结构化的 LLM 调用数据。

---

## 接口定义

### Analyzer trait 实现

```rust
#[async_trait]
impl Analyzer for LLMApiTracker {
    /// 处理输入流：
    ///   - source != "http_parser" 的事件：直接透传
    ///   - source == "http_parser" 的事件：检测 LLM API 调用
    ///     - 请求事件：存入待匹配表，不透传（等待响应）
    ///     - 响应事件：与请求配对，生成 source="llm_tracker" 的富事件
    ///     - 非 LLM API 的 HTTP 事件：直接透传
    ///   - 流结束时：超时的待匹配请求生成 source="llm_tracker" 
    ///     且 status="orphaned" 的事件
    async fn process(&mut self, stream: EventStream) -> Result<EventStream, AnalyzerError>;

    fn name(&self) -> &str {
        "LLMApiTracker"
    }
}
```

### 配置结构

```rust
pub struct LLMApiTrackerConfig {
    /// 已知 LLM API 端点模式。key = provider name, value = path prefix
    pub api_patterns: HashMap<String, String>,
    /// 待匹配请求的超时时间（秒），超时后生成 orphaned 事件
    pub request_timeout_secs: u64,
    /// 是否透传原始 HTTP 事件（默认 false，减少下游噪音）
    pub passthrough_raw_http: bool,
    /// 最大待匹配请求数（防止内存泄漏）
    pub max_pending_requests: usize,
}

impl Default for LLMApiTrackerConfig {
    fn default() -> Self {
        Self {
            api_patterns: HashMap::from([
                ("openai".into(), "/v1/chat/completions".into()),
                ("openai-legacy".into(), "/v1/completions".into()),
                ("anthropic".into(), "/v1/messages".into()),
                ("google".into(), "/v1beta/models/".into()),
                ("openrouter".into(), "/api/v1/chat/completions".into()),
            ]),
            request_timeout_secs: 120,
            passthrough_raw_http: false,
            max_pending_requests: 4096,
        }
    }
}
```

### 输出事件结构

```rust
/// LLM API 调用追踪事件（序列化为 event.data）
struct LLMCallEvent {
    // --- 请求侧 ---
    provider: String,               // "openai", "anthropic", etc.
    model: Option<String>,          // 从请求体 JSON 提取
    request_path: String,           // "/v1/chat/completions"
    request_timestamp_ns: u64,
    is_streaming: bool,             // 请求体中 stream: true
    prompt_tokens: Option<u64>,     // 从请求体提取（如 Anthropic 会在请求中预估）
    
    // --- 响应侧 ---
    status_code: Option<u16>,       // HTTP 状态码
    response_timestamp_ns: Option<u64>,
    latency_ms: Option<f64>,        // 响应时间 - 请求时间
    completion_tokens: Option<u64>, // 从响应体 JSON 提取
    total_tokens: Option<u64>,
    
    // --- 元数据 ---
    http_method: String,            // 通常是 "POST"
    pid: u32,
    tid: u64,
    status: String,                 // "complete" | "orphaned" | "error"
    error_message: Option<String>,
}
```

---

## 数据流

### 整体流程

```
EventStream (source="http_parser" 的事件)
    │
    ▼
┌─────────────────────────────────────┐
│         LLMApiTracker               │
│                                     │
│  source != "http_parser" ?          │
│    ├─ 是 → 直接透传                 │
│    └─ 否 → 进入 HTTP 事件检查       │
│                                     │
│  message_type == "request" ?        │
│    ├─ 是 → 匹配 API pattern         │
│    │   ├─ 命中 → 存入 pending_map   │
│    │   │         (key: pid+tid+path)│
│    │   │         不透传原始事件      │
│    │   └─ 未命中 → 透传（非 LLM）   │
│    └─ 否 → message_type=="response"?│
│        ├─ 是 → 在 pending_map 中    │
│        │       查找匹配请求          │
│        │   ├─ 找到 → 合并生成       │
│        │   │        LLMCallEvent    │
│        │   │        删除 pending 条目│
│        │   │        输出到下游       │
│        │   └─ 未找到 → 透传         │
│        └─ 否 → 透传                 │
│                                     │
│  定时清理（基于事件时间戳驱动）：    │
│    超时 pending 条目 → 生成          │
│    status="orphaned" 事件           │
└─────────────────────────────────────┘
    │
    ▼
下游分析器（异常检测 / 责任追溯 / 日志）
```

### 请求-响应配对策略

配对使用复合键 `(pid, tid, request_path)`：

- **pid + tid**：同一线程内的 HTTP 请求通常是串行的（HTTP/1.1 管道化在同一连接上也是有序的），这对 `(pid, tid)` 足以区分不同线程的并发请求。
- **request_path**：同一线程可能对不同端点发起请求，加上 path 避免误匹配。

**限制**：同一线程对同一端点发起多个并发请求时（如 HTTP/2 多路复用），此策略可能误配对。对于当前监控场景（Agent 的 LLM 调用通常是串行的），这是可接受的简化。后续可升级为基于 `(pid, connection_fd)` 的追踪。

### 超时清理机制

```
不是独立的后台线程，而是利用流式处理的特性：
  - 每个到达的事件，在处理前先检查其 timestamp
  - 扫描 pending_map，将所有 pending_timestamp + timeout < current_timestamp 的条目标记为 orphaned
  - 生成 source="llm_tracker", status="orphaned" 的事件
  - 从 pending_map 中移除

优点：
  - 无额外线程/定时器
  - 与流处理的背压自然一致
  - 清理开销均摊到每个事件上
```

### 流结束时的收尾

```rust
// 在 process() 返回的流上附加一个 on_termination 钩子
// 当上游流返回 None（结束）时：
//   遍历 pending_map 中所有剩余条目
//   逐个生成 status="orphaned" 事件
//   将它们作为流的尾部元素输出
```

---

## 与现有架构的关系

### 在流水线中的位置

```
HTTPParser (source="http_parser")
    │
    ▼
AuthHeaderRemover (去除敏感头)
    │
    ▼
LLMApiTracker ◄── 新 analyzer，插在这里
    │
    ├─ 透传: source="http_parser" 的非 LLM HTTP 事件
    └─ 生成: source="llm_tracker" 的 LLM 调用富事件
    │
    ▼
下游（异常检测 / 日志 / 责任追溯）
```

**为什么放在 AuthHeaderRemover 之后？**
- Token 用量信息在响应体中，不在 header 中，不会被 AuthHeaderRemover 影响
- 但我们仍需要在 LLMApiTracker 之前去除敏感 header（如 `authorization`），因为 `raw_data` 可能包含它
- LLMApiTracker 自身不访问 header 中的敏感信息

### 与现有 analyzer 的对比

| 维度 | 现有 analyzer | LLMApiTracker |
|------|--------------|---------------|
| 状态 | 无状态 | 有状态 (HashMap) |
| 输入:输出 | 1:1 或 1:0 | 2:1（配对）或 1:0（等待中） |
| 时间语义 | 逐事件处理 | 跨事件配对 |
| 流结束行为 | 立即结束 | 需要收尾（flush pending） |
| source 变换 | 通常不变或改为固定值 | 增加新的 source 类型 |

---

## 边界情况与错误处理

| 场景 | 行为 |
|------|------|
| 响应先于请求到达 | 透传响应（等不到配对则下游自行处理） |
| 请求体 JSON 解析失败 | 仍生成事件，model/tokens 字段为 None，status="complete" |
| pending_map 达到 max_pending_requests | 驱逐最旧的条目，生成 status="orphaned" 事件 |
| 响应体是 SSE 流（streaming） | 检测 `text/event-stream` content-type，标记 is_streaming=true，从最后一个 SSE 事件提取 completion_tokens |
| 非 200 响应 | 正常配对，status="error"，记录 status_code 和 error_message |
| 空 body 的请求/响应 | 对应字段为 None，不报错 |

---

## 测试策略

### 单元测试

1. **模式匹配测试** — 各种 LLM API 路径的识别（命中/未命中）
2. **JSON 提取测试** — 从请求/响应 body 中提取 model、tokens 字段
3. **配对逻辑测试** — 相同 (pid, tid, path) 的请求-响应对正确合并
4. **超时清理测试** — 超过 timeout 的 pending 条目生成 orphaned 事件
5. **并发同端点测试** — 同一 (pid, tid, path) 的多个请求按 FIFO 配对

### 集成测试

1. **端到端流测试** — 构造 HTTPParser 输出流，验证 LLMApiTracker 输出
2. **非 LLM HTTP 透传** — 普通 HTTP 事件不受影响
3. **流结束收尾** — 流结束时所有 pending 条目被 flush

### 测试工具

- `stream::iter()` 构造模拟 EventStream
- `futures::StreamExt::collect()` 收集输出验证
- 使用 `tokio::test` 异步测试框架

---

## 未来扩展

1. **连接级别追踪** — 使用 eBPF 提供的 fd 信息，支持 HTTP/2 多路复用场景的精确配对
2. **流式响应重组** — 对 SSE 流式响应，实时累积 token 计数（而非仅最终事件）
3. **成本估算** — 根据 model + token 用量计算 API 调用成本
4. **速率限制检测** — 检测 429 响应并追踪限流模式
5. **Provider 特定解析器** — 可插拔的 body 解析策略，处理不同 provider 的 JSON 结构差异

---

## 实现注意事项

- **内存安全**：pending_map 使用 `HashMap<String, PendingRequest>`，key 为 `"{pid}:{tid}:{path}"`
- **并发安全**：`process()` 持有 `&mut self`，天然保证单线程访问 pending_map
- **JSON 解析**：使用 `serde_json::Value` 的指针访问（`body_json.pointer("/model")`），避免为每个 provider 定义完整 struct
- **性能**：每次清理扫描 pending_map 是 O(n)。对于 4096 上限和事件速率 ~1K/s，开销可忽略。如果事件速率达到 ~100K/s，可改用 `BTreeMap<timestamp, Vec<key>>` 索引优化
- **遵循现有约定**：所有事件透传保持 `source` 不变，新事件使用唯一 `source` 名称，便于下游 analyzer 按 source 过滤
