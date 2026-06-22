# AgentLoopDetector Design Document

> **Design status**: Proposed  
> **Target stage**: AnomalyDetection (Stage 3)  
> **Module**: `collector/src/framework/analyzers/agent_loop_detector.rs`  
> **Output source tag**: `agent_loop`

---

## 1. Name and Purpose

**AgentLoopDetector (ALD)**

检测 LLM agent 陷入工具调用死循环的流式分析器。通过跟踪近期
tool-call 事件的指纹，当同一工具+参数组合在时间窗口内重复超过阈值时，
发出 `agent_loop` 告警事件。

### 问题

LLM agent 的常见故障模式之一是"工具循环"——agent 调用一个工具，
得到不符合预期的结果，然后再次用相同或几乎相同的参数调用同一工具，
反复重试直到上下文窗口耗尽或达到 max_turns 上限。表现形式包括：

| 循环模式 | 示例 | 根因 |
|---------|------|------|
| 精确重复 | `read_file("src/main.rs")` 连续调用 8 次 | 输出被截断，agent 反复尝试获取完整内容 |
| 参数微变 | `search("error X")` → `search("error X fix")` → `search("error X solution")` | agent 认为信息不足，逐次扩宽搜索 |
| 乒乓切换 | `read_file("a.rs")` → `read_file("b.rs")` → `read_file("a.rs")` → ... | agent 在两个文件间反复横跳，无法决策 |
| 重试风暴 | `bash("npm test")` 失败 → 立即再次 `bash("npm test")` → ... | 没有在重试之间修改任何代码 |

如果不检测，这些循环会导致：
- **eBPF 事件洪流**：每个工具调用产生 5-20 个底层事件（SSL、HTTP、process），
  循环会快速放大事件量。
- **Token 浪费**：对 LLM API 提供商产生不必要的费用。
- **用户体验差**：用户等待超时后才意识到 agent 卡住了。
- **下游分析器误报**：循环产生的大量重复事件可能触发了其他异常检测
  的阈值。

### 目标

1. **精确重复检测**：对完全相同的工具调用（相同工具名+相同参数哈希）
   进行 O(1) 查重。
2. **模糊重复检测**：对参数仅微小变化的工具调用（编辑距离或其他
   相似度度量）进行模式识别。
3. **乒乓检测**：检测 A→B→A→B 的交替调用模式。
4. **分级告警**：根据循环严重程度发出不同级别的 loop 事件（`warning`、
   `critical`、`emergency`）。
5. **低误报率**：区分正常重试（如网络错误后的合理重试）和异常循环。

---

## 2. Position in the Analyzer Chain

```
Preprocessing → CausalLinking → InjectionDetection
    │
    ▼
[AgentLoopDetector]   ← **HERE** (AnomalyDetection stage)
    │
    ▼
AlertEnrichment → Output
```

### 为什么放在此处

- **位于 InjectionDetection 之后**：此时 tool-call 事件已经过解析和标注，
  ALD 可利用 handoff_injection 和 file_access 等上游分析器标注的信息来
  判断某次工具调用是否"有意义"（例如，如果`read_file` 多次调用同一个文件
  且中间没有修改该文件，则更可能是循环）。

- **位于 AlertEnrichment 之前**：ALD 产生的 `agent_loop` 事件可作为
  AlertEnrichment 阶段的输入，与其他异常事件一起进行去重和丰富。

- **需要足够的上下文窗口**：与更早的阶段不同，ALD 需要积累一定数量
  的历史事件才能做出判断，因此在 AnomalyDetection 阶段运行是合适的。

### Stage contract amendments

| 属性 | 修改前 | 修改后 |
|-----|--------|--------|
| `consumes` | `http_parser`, `process`, `sse_processor` | 新增 `tool_call` (source tag) |
| `produces` | — | `agent_loop`, `agent_loop_resolved` |
| `length_preserving` | `true` | `false` (loop events 是额外 emit 的) |
| `pass_through` | `true` | `true` (原始事件全部通过) |

---

## 3. Interface: EventStream Contract

### 3.1 消费的事件格式

ALD 关注具有明确"tool call 语义"的事件。具体来说：

| 来源 | 检测条件 | 提取字段 |
|-----|---------|---------|
| `http_parser` | `data.method == "POST"` 且 `data.path` 匹配 LLM API 模式（`/v1/chat`, `/v1/messages` 等） | `data.body` 中解析 tool_use 块 |
| `sse_processor` | `data.json_content` 包含 `tool_use` 或 `function_call` | `json_content` 中提取 tool name + arguments |
| `process` | `data.action == "exec"` 且命令匹配已知工具模式（bash, python 等） | `data.full_command` 哈希 |

对于无法从事件中提取工具调用信息的事件，直接透传，不参与循环检测。

### 3.2 产出的事件

```rust
// agent_loop 事件 data 结构
{
    "loop_id": "uuid-v4",
    "loop_type": "exact_repeat | fuzzy_repeat | ping_pong | retry_storm",
    "tool_name": "read_file",
    "repeat_count": 8,
    "window_duration_ms": 12000,
    "severity": "warning | critical | emergency",
    "first_occurrence_ns": 1733000000000000000,
    "last_occurrence_ns": 1733000012000000000,
    "affected_pids": [1234, 5678],
    "sample_payloads": [
        {"args": {"path": "src/main.rs"}, "timestamp_ns": ...},
        {"args": {"path": "src/main.rs"}, "timestamp_ns": ...}
    ],
    "context": {
        "interleaved_different_calls": 0,    // 期间是否有其他工具调用打破循环
        "error_responses": 3,                 // 循环中有多少次调用返回了错误
        "file_modifications": 0               // 循环期间是否修改了文件（区分合理重试）
    }
}
```

#### 事件类型定义

```rust
#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum LoopType {
    /// 完全相同参数的重复调用
    ExactRepeat,
    /// 参数仅微小变化（编辑距离 < 阈值）
    FuzzyRepeat,
    /// A→B→A→B 交替模式
    PingPong,
    /// 快速连续重试（每次间隔 < 500ms 且无中间修改）
    RetryStorm,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum LoopSeverity {
    /// 重复 3-5 次：需要关注但可能自愈
    Warning,
    /// 重复 6-10 次：明确循环，建议干预
    Critical,
    /// 重复 > 10 次：紧急，agent 已失控
    Emergency,
}
```

### 3.3 循环解决事件

当检测到的循环被打破（出现了新的不同工具调用，或循环停止超过冷却时间），
ALD 发出 `agent_loop_resolved` 事件：

```rust
{
    "loop_id": "uuid-v4",              // 对应原始循环的 ID
    "resolution": "self_recovery | user_intervention | timeout | new_tool_invoked",
    "total_repeats": 8,
    "duration_ms": 12000,
    "final_outcome": "task_completed | abandoned | error"
}
```

### 3.4 Trait 实现

```rust
impl Analyzer for AgentLoopDetector { ... }
impl Default for AgentLoopDetector { ... }
impl StreamContractProvider for AgentLoopDetector {
    fn output_contract(&self) -> StreamContract {
        StreamContract {
            preserves_length: false,
            preserves_order: true,
            sources_produced: hashset!["agent_loop".into(), "agent_loop_resolved".into()],
        }
    }
}
```

---

## 4. Key Data Structures

### 4.1 工具调用指纹

```rust
/// 一次工具调用的轻量指纹，用于快速查重。
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
struct ToolCallFingerprint {
    /// 工具名称的 FxHash（如 "read_file", "bash", "search"）
    tool_name_hash: u64,
    /// 参数的规范化哈希
    /// - 对于 JSON 参数：按 key 排序后序列化 + hash
    /// - 对于命令行：拆分为 (binary, args[]) → 规范化 + hash
    args_hash: u64,
    /// 调用进程 PID
    pid: u32,
}

impl ToolCallFingerprint {
    /// 从 http_parser 事件提取
    fn from_http_event(event: &Event) -> Option<Self> { ... }

    /// 从 sse_processor 事件提取
    fn from_sse_event(event: &Event) -> Option<Self> { ... }

    /// 从 process exec 事件提取
    fn from_process_event(event: &Event) -> Option<Self> { ... }
}
```

### 4.2 滑动窗口追踪器

```rust
/// 追踪单个工具在滑动窗口内的调用历史。
struct ToolCallWindow {
    /// 工具名称（人类可读，用于日志/告警）
    tool_name: String,
    /// 有序的调用记录列表（按时间排序）
    calls: VecDeque<CallRecord>,
    /// 窗口大小（纳秒）
    window_duration_ns: u64,
}

struct CallRecord {
    /// 调用时间戳
    timestamp_ns: u64,
    /// 参数字符串（用于模糊匹配和采样输出）
    args_snapshot: String,
    /// 参数指纹（用于精确匹配）
    fingerprint: ToolCallFingerprint,
    /// PID
    pid: u32,
    /// 该次调用是否返回了错误
    had_error: bool,
}
```

### 4.3 乒乓检测器

```rust
/// 检测 A→B→A→B 交替模式。
///
/// 维护最近 N 个不同工具调用的序列，当检测到至少两次
/// 交替（即 A→B→A→B）时触发。
struct PingPongDetector {
    /// 最近调用的工具名序列（去重连续重复后）
    recent_alternations: VecDeque<String>,
    /// 检测交替所需的最小序列长度
    min_sequence_length: usize,
}
```

### 4.4 配置

```rust
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AgentLoopDetectorConfig {
    // ── 精确重复 ──
    /// 触发 exact_repeat 告警的最小重复次数
    pub exact_repeat_threshold: usize,          // 默认: 5
    /// 精确重复的滑动窗口大小（纳秒）
    pub exact_repeat_window_ns: u64,             // 默认: 30s

    // ── 模糊重复 ──
    /// 模糊匹配的编辑距离阈值（归一化后，0.0-1.0）
    pub fuzzy_similarity_threshold: f64,         // 默认: 0.85
    /// 触发 fuzzy_repeat 告警的最小重复次数
    pub fuzzy_repeat_threshold: usize,           // 默认: 6

    // ── 乒乓检测 ──
    /// 触发 ping_pong 的最小交替次数
    pub ping_pong_min_alternations: usize,       // 默认: 3 (A→B→A→B→A→B)
    /// 乒乓检测的序列窗口大小
    pub ping_pong_sequence_length: usize,        // 默认: 20

    // ── 重试风暴 ──
    /// 重试风暴的最大间隔（纳秒，超过此间隔不视为风暴）
    pub retry_storm_max_interval_ns: u64,        // 默认: 500ms
    /// 触发 retry_storm 的最小重试次数
    pub retry_storm_threshold: usize,            // 默认: 4

    // ── 严重度阈值 ──
    /// warning 级别的最小重复次数
    pub severity_warning_threshold: usize,       // 默认: 3
    /// critical 级别的最小重复次数
    pub severity_critical_threshold: usize,      // 默认: 6
    /// emergency 级别的最小重复次数
    pub severity_emergency_threshold: usize,     // 默认: 10

    // ── 冷却与抑制 ──
    /// 同一 tool 的告警抑制冷却时间（纳秒）
    pub cooldown_ns: u64,                        // 默认: 60s
    /// 循环解决所需的"静默"时间（纳秒）
    pub resolution_silence_ns: u64,              // 默认: 15s

    // ── 过滤 ──
    /// 忽略的工具名列表（不参与循环检测的工具）
    pub ignored_tools: Vec<String>,              // 默认: []
    /// 告警需要的最小不同 PID 数（0 = 不要求）
    pub min_distinct_pids: usize,                // 默认: 0
}

impl Default for AgentLoopDetectorConfig {
    fn default() -> Self {
        Self {
            exact_repeat_threshold: 5,
            exact_repeat_window_ns: 30_000_000_000,
            fuzzy_similarity_threshold: 0.85,
            fuzzy_repeat_threshold: 6,
            ping_pong_min_alternations: 3,
            ping_pong_sequence_length: 20,
            retry_storm_max_interval_ns: 500_000_000,
            retry_storm_threshold: 4,
            severity_warning_threshold: 3,
            severity_critical_threshold: 6,
            severity_emergency_threshold: 10,
            cooldown_ns: 60_000_000_000,
            resolution_silence_ns: 15_000_000_000,
            ignored_tools: vec![],
            min_distinct_pids: 0,
        }
    }
}
```

---

## 5. Data Flow

```
EventStream (http_parser, sse_processor, process sources)
  │
  ▼
AgentLoopDetector::process()
  │
  ├── 对每个事件:
  │     1. 尝试提取 ToolCallFingerprint
  │        ├── 来自 http_parser → 解析 body 中的 tool_use 块
  │        ├── 来自 sse_processor → 解析 json_content 中的 function_call
  │        ├── 来自 process → 对 exec 命令做规范化哈希
  │        └── 无法提取 → 透传（不参与循环检测）
  │
  │     2. 如果提取成功:
  │        ├── 检查 ignored_tools 列表 → 如果在列表中，跳过
  │        ├── 检查冷却期 → 如果在冷却中，跳过告警但继续更新窗口
  │        │
  │        ├── 更新 ToolCallWindow（按 tool_name_hash 分组）
  │        │   ├── 添加新的 CallRecord
  │        │   └── 淘汰窗口外的旧记录
  │        │
  │        ├── 更新 PingPongDetector 序列
  │        │   ├── 追加工具名到 recent_alternations
  │        │   └── 去重连续重复
  │        │
  │        ├── 运行检测逻辑:
  │        │   ├── ExactRepeat: 在窗口中计数相同指纹的出现次数
  │        │   │   └── count >= threshold → emit LoopType::ExactRepeat
  │        │   │
  │        │   ├── FuzzyRepeat: 在窗口中计数 args_snapshot 相似度 > threshold 的次数
  │        │   │   └── count >= threshold → emit LoopType::FuzzyRepeat
  │        │   │
  │        │   ├── PingPong: 检查 recent_alternations 中的交替模式
  │        │   │   └── 检测到 N 次交替 → emit LoopType::PingPong
  │        │   │
  │        │   └── RetryStorm: 检查连续调用的时间间隔
  │        │       └── interval < max_interval 且 count >= threshold → emit LoopType::RetryStorm
  │        │
  │        └── 如果需要告警:
  │            ├── 根据 repeat_count 确定 severity
  │            ├── 生成 agent_loop 事件
  │            ├── 设置冷却时间戳
  │            └── 将 loop_id 加入 active_loops
  │
  │     3. 循环解决检测:
  │        ├── 对 active_loops 中的每个循环
  │        ├── 检查最后一次重复后的时间间隔
  │        └── 如果 >= resolution_silence_ns → emit agent_loop_resolved
  │
  │     4. 将原始事件 + 所有衍生事件合并输出
  │
  └── flatten → EventStream (输出)
```

### 可视化流程图

```
                    ┌──────────────────────────┐
                    │     Input EventStream      │
                    │  (http_parser, sse, proc) │
                    └────────────┬─────────────┘
                                 │
                    ┌────────────▼─────────────┐
                    │   Extract ToolCallInfo    │
                    │   (fingerprint + args)    │
                    └──────┬──────────┬────────┘
                           │          │
                    ┌──────▼──┐  ┌───▼──────────┐
                    │ 可以提取 │  │ 无法提取      │
                    └──────┬──┘  └───┬──────────┘
                           │          │
              ┌────────────▼──┐       │
              │ Ignored tool?  │       │
              └──┬─────────┬──┘       │
                 │Yes      │No         │
                 │         │           │
         ┌───────▼──┐ ┌───▼────────────▼──┐
         │  跳过     │ │  Update Windows   │
         └───────┬──┘ │  ├─ToolCallWindow │
                 │    │  ├─PingPongDetect │
                 │    │  └─RetryStormBuf  │
                 │    └────────┬──────────┘
                 │             │
                 │    ┌────────▼──────────┐
                 │    │  Run Detectors    │
                 │    │  ├─ExactRepeat    │
                 │    │  ├─FuzzyRepeat    │
                 │    │  ├─PingPong       │
                 │    │  └─RetryStorm     │
                 │    └───┬──────┬────────┘
                 │        │      │
                 │   ┌────▼──┐ ┌▼─────────┐
                 │   │No loop│ │Loop found│
                 │   └───┬───┘ └────┬──────┘
                 │       │          │
                 │       │   ┌──────▼──────┐
                 │       │   │Check cooldown│
                 │       │   └──┬──────┬───┘
                 │       │      │cool  │active
                 │       │  ┌───▼──┐ ┌─▼────────┐
                 │       │  │Skip  │ │Emit alert │
                 │       │  └──┬───┘ │+ cooldown │
                 │       │     │     └─────┬─────┘
                 │       │     │           │
                 └───────┴─────┴───────────┘
                                 │
                    ┌────────────▼─────────────┐
                    │  Resolve stale loops     │
                    │  (silence > threshold)   │
                    └────────────┬─────────────┘
                                 │
                    ┌────────────▼─────────────┐
                    │   Output EventStream      │
                    │   (original + derived)   │
                    └──────────────────────────┘
```

---

## 6. Key Implementation Decisions

### 6a. 多源工具调用提取

不同 agent 框架使用不同的工具调用协议。ALD 需要在多个事件源中检测：

| Agent 框架 | 工具调用格式 | 检测方式 |
|-----------|------------|---------|
| Claude (Anthropic) | SSE `content_block_start` → `tool_use` | 解析 sse_processor 中的 JSON |
| OpenAI | HTTP response body 中的 `tool_calls[]` | 解析 http_parser 中的 body |
| MCP (stdio) | JSON-RPC `tools/call` | 解析 stdio 事件 |
| Shell agent | `bash("some command")` | 解析 process exec 事件 |

提取逻辑封装在 `ToolCallExtractor` trait 中：

```rust
trait ToolCallExtractor {
    fn extract(event: &Event) -> Option<ToolCallInfo>;
}

struct AnthropicToolCallExtractor;
struct OpenAIToolCallExtractor;
struct BashToolCallExtractor;
struct MCPToolCallExtractor;
```

通过 `event.source` 自动路由到对应的 extractor。

### 6b. 参数哈希规范化

对于 JSON 参数，不同的 key 顺序会导致不同的字符串表示。规范化策略：

1. 将 `serde_json::Value` 中的所有 object key 按字母序排序
2. 移除所有 `null` 值（它们在语义上是等价的）
3. 对重构后的 JSON 做 FxHash
4. 同时保存规范化后的字符串快照（用于模糊匹配和采样输出）

```rust
fn normalize_args(args: &Value) -> (String, u64) {
    let normalized = sort_keys_recursive(args);
    let snapshot = serde_json::to_string(&normalized).unwrap_or_default();
    let hash = fxhash::hash64(&snapshot);
    (snapshot, hash)
}
```

### 6c. 模糊匹配

使用归一化编辑距离（Normalized Levenshtein Distance）：

```rust
fn args_similarity(a: &str, b: &str) -> f64 {
    let dist = levenshtein::levenshtein(a, b) as f64;
    let max_len = a.len().max(b.len()).max(1) as f64;
    1.0 - (dist / max_len)
}
```

对于长参数字符串，先截断到 512 字符以避免 O(n²) 开销。

### 6d. 冷却与去重告警

为了避免同一个循环在每次事件到达时重复发出告警：

- 每个 `(tool_name_hash, loop_type)` 对维护一个冷却时间戳
- 在冷却期内，即使循环仍在继续，也不会发出新的同类型告警
- 冷却期结束后，如果循环仍未解决，可以发出升级告警（severity 升级）

这确保了：
- 同一个 5 次重复的循环只产生一条 warning 告警
- 如果循环继续到 10 次，会升级为 critical
- 不会产生告警风暴

### 6e. 内存管理

- `ToolCallWindow` 按 tool_name_hash 分组，每个窗口的最大条目数 = `exact_repeat_threshold * 2`（留一点余量用于模糊匹配）。超过上限时淘汰最旧条目。
- `PingPongDetector` 固定大小 `VecDeque<String>`，限于 `ping_pong_sequence_length`（默认 20）。
- 不活跃的窗口（15 秒无新调用）会被清理。

总内存估算：假设 50 个活跃工具，每个窗口 20 条记录，每条 ~200 bytes → ~200 KB。

### 6f. 与下游集成

`agent_loop` 事件携带足够的信息供 AlertEnrichment 阶段使用：

- `loop_id` 用于去重和关联 `agent_loop_resolved` 事件
- `severity` 用于 AlertEnrichment 的优先级排序
- `sample_payloads` 用于前端 TimelinePanel 展示循环示例
- `context.error_responses` 和 `.file_modifications` 帮助 Judgment 模块区分合理重试和异常循环

---

## 7. Registering in the Analyzer Module

编辑 `analyzers/mod.rs`：

```rust
pub mod agent_loop_detector;
pub use agent_loop_detector::{AgentLoopDetector, AgentLoopDetectorConfig,
    LoopType, LoopSeverity, ToolCallFingerprint};
```

在 `PipelineOrchestratorImpl` 的 AnomalyDetection 阶段注册：

```rust
// AnomalyDetection stage
pipeline.add_analyzer(
    PipelineStage::AnomalyDetection,
    Box::new(AgentLoopDetector::new(AgentLoopDetectorConfig::default())),
)?;
```

---

## 8. Test Strategy

### 8.1 单元测试

| 测试用例 | 验证内容 |
|---------|---------|
| `test_exact_repeat_detection` | 5 次相同 read_file("a.rs") → 产生 ExactRepeat 告警 |
| `test_exact_repeat_below_threshold` | 4 次相同调用 → 无告警 |
| `test_fuzzy_repeat_detection` | "search error X", "search error X fix", ... 相似度 > 0.85 × 6 → FuzzyRepeat |
| `test_ping_pong_detection` | read_file("a.rs")→read_file("b.rs")→read_file("a.rs")→... → PingPong |
| `test_retry_storm_detection` | 连续 5 次 bash("npm test") 间隔 < 200ms → RetryStorm |
| `test_cooldown_suppression` | 告警发出后 60s 内不重复发出同类型告警 |
| `test_severity_escalation` | 3 次 → warning, 6 次 → critical, 10 次 → emergency |
| `test_ignored_tools` | ignored_tools 中的工具不参与检测 |
| `test_normal_retry_not_flagged` | 2 次正常重试（间隔 > 5s）→ 无告警 |
| `test_loop_resolution` | 循环停止 15s 后 → agent_loop_resolved 事件 |
| `test_multi_pid_independent` | 不同 PID 的相同调用不合并计数 |
| `test_non_tool_events_passthrough` | 非工具事件（process, system 等）透传 |

### 8.2 集成测试

- **端到端循环场景**：构造 Claude agent 卡在 read_file 循环的完整事件流，
  验证 ALD 在 5 次调用后发出告警，在停止 15s 后发出解析。
- **与其他异常分析器共存**：同时运行 MultiAgentAnomalyAnalyzer 和 ALD，
  验证两者不会交叉触发误报。
- **高吞吐场景**：1000 events/sec 混合流量（80% 非工具事件，20% 工具事件），
  验证 ALD 不影响管道吞吐。

### 8.3 回归防护

- 在已有的 6 个异常检测场景中启用 ALD，验证现有检测不受影响
  （上下文污染、handoff 注入、文件越权访问、凭证泄露、角色劫持、agent 团队异常）

---

## 9. Risk Assessment

| 风险 | 严重度 | 缓解措施 |
|-----|--------|---------|
| 工具调用提取失败（新型 agent 框架） | 中 | 事件透传，不影响管道；新增 extractor 无需改动核心逻辑 |
| 参数哈希碰撞导致误合并不同调用 | 低 | FxHash 64 位碰撞概率极低；模糊匹配使用完整参数字符串 |
| 冷却期过长导致漏报（升级循环被静默） | 中 | 冷却期内仍更新窗口计数；冷却结束后检查是否需要升级告警 |
| 大量不同工具导致窗口膨胀 | 低 | 固定每个窗口的最大条目数；不活跃窗口定时清理；总内存 ~200KB |
| 模糊匹配在长参数下性能退化 | 低 | 截断参数到 512 字符；编辑距离计算 O(n²) 但 n ≤ 512 |
| agent_loop 事件被下游过滤 | 低 | agent_loop source tag 不在 HTTPFilter/SSLFilter 的过滤范围内 |

---

## 10. Summary

**AgentLoopDetector (ALD)** 是一个专门针对 LLM agent 循环故障模式的流式
异常分析器。它消费 http_parser、sse_processor 和 process 事件，提取工具调用
指纹，通过四种检测策略（精确重复、模糊重复、乒乓交替、重试风暴）识别循环模式，
并发出分级告警。

关键设计特性：
- **非侵入**：所有原始事件透传，不修改、不过滤。循环事件是额外的衍生事件。
- **多框架兼容**：通过 ToolCallExtractor trait 支持 Anthropic、OpenAI、MCP、
  Shell 等多种 agent 框架的工具调用格式。
- **分级告警 + 冷却**：warning → critical → emergency 三级严重度，冷却机制
  避免告警风暴。
- **低误报**：区分正常重试（错误后合理重试、间隔较长的重试）和异常循环。
- **内存友好**：固定窗口大小 + 惰性清理，总额 < 300KB。
- **可观测**：暴露 metrics（loops_detected, loops_resolved, false_positives,
  events_processed）供 ObservabilityMetricsAnalyzer 消费。

ALD 位于 6 阶段管道的 AnomalyDetection（Stage 3），在 InjectionDetection
之后、AlertEnrichment 之前。实现估计约 400 行 Rust + 200 行测试。
