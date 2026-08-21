# ToolExecutionTracker Analyzer 设计文档

## 概述

**名称**: ToolExecutionTracker  
**目的**: 从 LLM API 的 HTTP 请求/响应流中提取工具调用（tool call）生命周期事件，追踪工具的定义、调用、执行结果和耗时。  
**集成点**: 插入 analyzer 管道的 `CausalLinking` 阶段，位于 `HTTPParser` 之后、`MultiAgentAnomaly` 之前。

## 动机

当前框架已具备 HTTP 解析（HTTPParser）、SSE 重组（SSEProcessor）、异常检测（MultiAgentAnomaly）等能力，但缺少对 Agent 工具调用行为的结构化追踪。ToolExecutionTracker 填补这一空白：

- **工具可见性**：Agent 调用了哪些工具？哪些工具从未被调用（定义了但未使用）？
- **成功率追踪**：哪些工具调用成功/失败？失败率是否异常？
- **延迟分析**：单次工具调用的端到端延迟分布
- **并行调用检测**：Agent 是否在单轮对话中并行调用了多个工具？
- **工具使用模式**：高频工具、低频工具、工具组合模式

## 接口设计

### 核心结构

```rust
use std::collections::{HashMap, VecDeque};
use std::time::Duration;

/// 一次工具调用的完整生命周期记录
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolCallRecord {
    /// 唯一标识符: "{request_id}:{tool_call_index}"
    pub call_id: String,
    /// 工具名称 (e.g., "read_file", "execute_command")
    pub tool_name: String,
    /// 工具参数 (JSON object)
    pub arguments: serde_json::Value,
    /// 工具调用在单次请求中的序号 (0-based)
    pub call_index: usize,
    /// 发起调用的 Agent PID
    pub agent_pid: u32,
    /// 请求时间戳 (epoch ns)
    pub request_timestamp_ns: u64,
    /// 响应时间戳 (epoch ns) — 完成后填充
    pub response_timestamp_ns: Option<u64>,
    /// 调用状态
    pub status: ToolCallStatus,
    /// 工具执行结果 (成功时) 或错误信息 (失败时)
    pub result: Option<ToolCallResult>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub enum ToolCallStatus {
    /// 工具调用已发出，尚未收到结果
    Pending,
    /// 工具执行成功
    Success,
    /// 工具执行失败 (模型报告错误)
    Failed,
    /// 超时 — 在 timeout_ms 内未收到结果
    Timeout,
    /// 被中断 — 用户或上游取消了请求
    Cancelled,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolCallResult {
    /// 结果内容 (工具返回的文本/JSON)
    pub content: String,
    /// 是否为错误结果 (工具层的 is_error 标志)
    pub is_error: bool,
    /// 结果长度 (字节)
    pub content_length: usize,
}

/// 单次 LLM API 请求中携带的工具定义
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolDefinitionSnapshot {
    /// 请求 ID (从 HTTP 事件派生)
    pub request_id: String,
    /// 该请求中声明的所有工具名称列表
    pub tool_names: Vec<String>,
    /// 工具定义总数
    pub tool_count: usize,
}

/// 分析器配置
#[derive(Debug, Clone)]
pub struct ToolExecutionTrackerConfig {
    /// 工具调用超时 (毫秒) — 超出后标记为 Timeout
    pub timeout_ms: u64,
    /// 最大并发追踪数 (超出后 LRU 淘汰最旧条目)
    pub max_concurrent_tracks: usize,
    /// 是否解析 Anthropic API 格式 (content_block_start/stop/delta)
    pub parse_anthropic_format: bool,
    /// 是否解析 OpenAI API 格式 (tool_calls delta)
    pub parse_openai_format: bool,
    /// 是否发出工具定义快照事件
    pub emit_tool_definitions: bool,
    /// 是否发出单次工具调用事件
    pub emit_tool_calls: bool,
    /// 是否发出聚合统计事件
    pub emit_aggregate_stats: bool,
}

impl Default for ToolExecutionTrackerConfig {
    fn default() -> Self {
        Self {
            timeout_ms: 120_000,               // 2 分钟
            max_concurrent_tracks: 10_000,
            parse_anthropic_format: true,
            parse_openai_format: true,
            emit_tool_definitions: true,
            emit_tool_calls: true,
            emit_aggregate_stats: false,        // 默认关闭，在 drain() 时集中输出
        }
    }
}

/// 分析器主体
pub struct ToolExecutionTracker {
    config: ToolExecutionTrackerConfig,
    /// 待完成的工具调用: call_id -> ToolCallRecord
    pending_tools: HashMap<String, ToolCallRecord>,
    /// 已完成的工具调用历史 (环形缓冲区，用于统计)
    completed_history: VecDeque<ToolCallRecord>,
    /// 每 Agent 的工具统计
    agent_stats: HashMap<u32, AgentToolStats>,
    /// 请求 ID 计数器
    request_counter: u64,
}

#[derive(Debug, Clone, Default, Serialize)]
pub struct AgentToolStats {
    pub total_calls: u64,
    pub successful_calls: u64,
    pub failed_calls: u64,
    pub timed_out_calls: u64,
    pub unique_tools_used: HashSet<String>,
    /// 每种工具的调用次数
    pub tool_frequency: HashMap<String, u64>,
    /// 工具调用延迟分布 (微秒): tool_name -> Vec<latency_us>
    pub tool_latencies: HashMap<String, Vec<u64>>,
}
```

### Analyzer trait 实现

```rust
#[async_trait]
impl Analyzer for ToolExecutionTracker {
    async fn process(
        &mut self,
        stream: EventStream,
    ) -> Result<EventStream, AnalyzerError>;

    fn name(&self) -> &str {
        "ToolExecutionTracker"
    }
}

impl AnalyzerLifecycle for ToolExecutionTracker {
    fn on_attach(&mut self, stage: PipelineStage) -> Result<(), String>;
    fn on_stop(&mut self) -> Result<(), String>;
}

impl DrainableAnalyzer for ToolExecutionTracker {
    fn drain(&mut self) -> Vec<Event>;
    fn has_buffered_events(&self) -> bool;
}

impl StreamContractProvider for ToolExecutionTracker {
    fn output_contract(&self) -> StreamContract {
        StreamContract::non_preserving()  // 会额外发出 tool_call 事件
    }
}
```

## 数据流

```
                    Input Stream (HTTP Events)
                    source: "http_parser"
                            │
              ┌─────────────┼─────────────┐
              ▼             ▼             ▼
         [检测 API       [检测 SSE      [检测 API
         请求路径]       text delta]   响应结束]
              │             │             │
              ▼             ▼             │
    ┌─────────────────┐ ┌─────────────┐   │
    │ 解析请求体 JSON  │ │ 提取 tool_  │   │
    │ 提取 tools[] 定义│ │ use 块内容  │   │
    │ 提取 tool_choice  │ │ 匹配到 pending│  │
    └────────┬────────┘ └──────┬──────┘   │
             │                 │          │
             ▼                 ▼          ▼
    ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
    │ 记录工具定义  │  │ 创建/更新    │  │ 标记完成/超时│
    │ 快照事件     │  │ ToolCallRecord│  │ 计算延迟    │
    └──────────────┘  └──────────────┘  └──────────────┘
             │                 │          │
             └─────────┬───────┘          │
                       ▼                  ▼
              ┌────────────────────────────────┐
              │  合并: 原始事件 + tool_call    │
              │  事件 + tool_definition 事件   │
              └────────────────────────────────┘
                       │
                       ▼
              Output Stream
              source: "tool_call" / "tool_definition"
```

### 处理流程详解

#### 阶段 1: 检测 LLM API 请求

从 HTTP 事件中识别 LLM API 请求：

| 条件 | Anthropic API | OpenAI API |
|------|--------------|------------|
| 请求方法 | POST | POST |
| URL 路径 | `/v1/messages` | `/v1/chat/completions` |
| Content-Type | `application/json` | `application/json` |

#### 阶段 2: 解析工具定义 (请求方向)

从 HTTP 请求体 JSON 中提取 `tools[]` 数组：

```json
// Anthropic format
{
  "tools": [
    {"name": "read_file", "description": "...", "input_schema": {...}},
    {"name": "execute_command", "description": "...", "input_schema": {...}}
  ],
  "tool_choice": {"type": "auto"}
}

// OpenAI format
{
  "tools": [
    {"type": "function", "function": {"name": "read_file", ...}},
    {"type": "function", "function": {"name": "execute_command", ...}}
  ],
  "tool_choice": "auto"
}
```

提取后发出 `ToolDefinitionSnapshot` 事件 (source: `tool_definition`)。

#### 阶段 3: 解析工具调用 (响应方向)

##### Anthropic SSE 格式

```
event: content_block_start
data: {"type":"content_block_start","content_block":{"type":"tool_use","id":"toolu_xxx","name":"read_file","input":{}}}

event: content_block_delta
data: {"type":"content_block_delta","delta":{"type":"input_json_delta","partial_json":"{\"file_path\": \"/et"}}

event: content_block_stop  ← 工具调用参数接收完毕
```

##### OpenAI SSE 格式

```
data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_xxx","function":{"name":"read_file","arguments":"{\"file_path\":\"/etc/passwd\"}"}}]}}]}
```

解析流程：
1. `content_block_start` (tool_use) → 创建 `ToolCallRecord`，状态 `Pending`
2. `content_block_delta` (input_json_delta) → 累积 `arguments`
3. `content_block_stop` → 参数完整，等待工具执行结果

#### 阶段 4: 解析工具结果 (请求方向 — 下一轮)

当 Agent 发起下一次 API 请求时，请求体中的 `messages[]` 包含 `tool_result` 内容块：

```json
{
  "messages": [
    ...,
    {
      "role": "user",
      "content": [
        {
          "type": "tool_result",
          "tool_use_id": "toolu_xxx",
          "content": "file contents here...",
          "is_error": false
        }
      ]
    }
  ]
}
```

将 `tool_use_id` 匹配到 `pending_tools` 中的记录，填充 `result` 和 `response_timestamp_ns`，计算延迟，状态更新为 `Success` 或 `Failed`。

#### 阶段 5: 超时清理

每次 `process()` 调用时，遍历 `pending_tools`，将超过 `timeout_ms` 的记录标记为 `Timeout` 并移入 `completed_history`。

## 输出事件格式

### tool_definition 事件

```json
{
  "timestamp": 1717000000000,
  "source": "tool_definition",
  "pid": 12345,
  "comm": "claude",
  "data": {
    "request_id": "req_abc123",
    "tool_names": ["read_file", "execute_command", "search"],
    "tool_count": 3,
    "tool_choice": "auto",
    "model": "claude-sonnet-4-6"
  }
}
```

### tool_call 事件

```json
{
  "timestamp": 1717000001000,
  "source": "tool_call",
  "pid": 12345,
  "comm": "claude",
  "data": {
    "call_id": "req_abc123:0",
    "request_id": "req_abc123",
    "tool_name": "read_file",
    "arguments": {"file_path": "/home/user/config.toml"},
    "call_index": 0,
    "status": "Success",
    "latency_us": 15234,
    "result_is_error": false,
    "result_length": 1024,
    "agent_pid": 12345
  }
}
```

### tool_stats 事件 (drain 时发出)

```json
{
  "timestamp": 1717000100000,
  "source": "tool_stats",
  "pid": 0,
  "comm": "",
  "data": {
    "agent_pid": 12345,
    "total_calls": 42,
    "successful_calls": 38,
    "failed_calls": 3,
    "timed_out_calls": 1,
    "unique_tools_used": ["read_file", "execute_command", "search"],
    "tool_frequency": {"read_file": 20, "execute_command": 15, "search": 7},
    "avg_latency_us": {"read_file": 8234, "execute_command": 45120, "search": 3290},
    "p95_latency_us": {"read_file": 15000, "execute_command": 120000, "search": 8000}
  }
}
```

## 管道集成

### 阶段分配

```rust
// PipelineBuilderImpl 中注册
builder.add_injection_detector(Box::new(ToolExecutionTracker::new(
    ToolExecutionTrackerConfig::default()
)));
```

放在 `InjectionDetection` 阶段（stage 2），与 `HandoffInjectionAnalyzer` 同级：
- 上游依赖：`Preprocessing`（需要 HTTPParser 输出）
- 下游消费者：`AnomalyDetection`（MultiAgentAnomalyAnalyzer 可利用工具调用数据检测异常模式）、`AlertEnrichment`

### I/O 契约

| 维度 | 输入 | 输出 |
|------|------|------|
| 消费 | `EventSource::HttpParser` | — |
| 生产 | — | `EventSource::ToolCall`, `EventSource::ToolDefinition` |
| 流长度 | — | 非保持（额外发出 tool_call/definition 事件） |
| 单调性 | 保持 | 保持（不重排事件） |

## 配置示例

```rust
// 默认配置 — 适用于大多数场景
let tracker = ToolExecutionTracker::new(ToolExecutionTrackerConfig::default());

// 生产环境高性能配置
let tracker = ToolExecutionTracker::new(ToolExecutionTrackerConfig {
    timeout_ms: 60_000,
    max_concurrent_tracks: 5_000,
    parse_anthropic_format: true,
    parse_openai_format: false,    // 仅追踪 Anthropic
    emit_tool_definitions: true,
    emit_tool_calls: true,
    emit_aggregate_stats: false,
});
```

## 边界情况与错误处理

| 场景 | 处理策略 |
|------|---------|
| **EC-TT-1**: 同一请求中多个并行工具调用 | 每个工具调用独立分配 `call_index`（0, 1, 2...） |
| **EC-TT-2**: 工具调用无对应 tool_result | `timeout_ms` 后标记为 `Timeout`，发出告警事件 |
| **EC-TT-3**: tool_result 无对应 tool_use_id | 创建独立记录，标记 `status: Failed`，记录 `orphaned_result: true` |
| **EC-TT-4**: HTTP 请求体超过 1MB 不完整 | 跳过该事件的解析，记录 `parse_error` 计数器 |
| **EC-TT-5**: 非 JSON 请求体（二进制/流式） | 静默跳过，不做解析 |
| **EC-TT-6**: 快速连续请求导致 pending 溢出 | LRU 淘汰最旧记录，记录 `dropped_tracks` 计数器 |
| **EC-TT-7**: Anthropic 和 OpenAI 格式混合 | 优先尝试 Anthropic 格式解析，失败后回退 OpenAI |

## 性能考量

- **内存**: O(max_concurrent_tracks + completed_history 大小)
  - 每个 `ToolCallRecord` ~2KB（含 arguments 和 result）
  - 默认配置峰值内存 ~20MB (10,000 tracks × 2KB)
- **CPU**: 
  - 非 LLM API 事件：O(1) — 仅做 URL 路径前缀匹配
  - LLM API 事件：O(n) 其中 n 为事件 payload 大小（JSON 解析）
- **清理策略**: 每次 `process()` 调用时 O(pending_tools) 超时扫描，低频使用场景可降级为定时器触发

## 测试策略

### 单元测试

1. **Anthropic content_block 解析** — content_block_start/delta/stop 序列正确创建 ToolCallRecord
2. **OpenAI tool_calls delta 解析** — 增量 JSON 累积正确
3. **tool_result 匹配** — tool_use_id 匹配到正确的 pending 记录
4. **超时淘汰** — 超过 timeout_ms 的记录正确标记 Timeout
5. **并行工具调用** — 单请求中多个工具独立追踪
6. **非 LLM API 事件** — 非 /v1/messages 路径静默透传
7. **LRU 淘汰** — pending 溢出时正确淘汰最旧条目

### 集成测试

1. **完整 Anthropic 对话流** — 请求→工具调用→工具结果→下一请求
2. **完整 OpenAI 对话流** — 同上
3. **混合场景** — 工具调用穿插普通文本消息
4. **高并发** — 100 并发请求，验证无竞态
5. **中断恢复** — drain() 正确输出所有 pending 记录

### 边界测试

1. 空 tools[] 数组 → 无工具定义事件
2. 超大工具参数 (1MB+) → 截断或跳过
3. 畸形 JSON → 优雅降级，不 panic
4. 超长工具名称 → 截断

## 未来扩展

- **工具调用链追踪**: 从 tool_result 的输出中检测到其他工具的 invocation，建立调用链 (tool A → tool B → tool C)
- **工具权限审计**: 检测 Agent 是否调用了不应有权访问的工具（基于工具名称模式匹配）
- **工具使用异常检测**: 对比历史基线，标记异常高频或异常低频的工具使用模式
- **跨 Agent 工具共享分析**: 多 Agent 场景下，追踪同一个工具被不同 Agent 调用的模式差异
- **与 ResponsibilityGraph 集成**: 将工具调用作为因果链路节点，增强责任追溯的粒度
