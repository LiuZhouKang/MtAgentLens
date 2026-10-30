# AgentStateTransitionTracker 设计文档

> **状态:** 设计提案  
> **日期:** 2026-06-04  
> **追踪编号:** ASTP-001  
> **目标阶段:** AnomalyDetection (Stage 3)  
> **输出 source tag:** `agent_state`

---

## 1. 动机

在 LLM Agent 的故障模式中，"卡住"比"做错事"更常见也更隐蔽——agent 没有产生任何异常事件，只是永远停留在某个内部状态中。当前框架的 `ConversationFlowAnalyzer` 关注会话级的对话流（active/stalled/completed），但不追踪 agent 在单次推理中的操作状态机。

**需要检测的场景：**

| 故障模式 | 症状 | 危害 |
|---------|------|------|
| **思考死循环** | agent 停留在 thinking 状态 > 60s | 消耗 API token，用户无感知 |
| **工具调用黑洞** | 发出 tool_use 后 30s 无响应 | agent 可能等待已崩溃的工具 |
| **缺失工具执行** | thinking 后直接 responding，跳过 tool 阶段 | 模型幻觉——声称调用了工具但实际没调 |
| **状态回跳** | responding → thinking → responding | 模型在响应中修正自己，可能表示内部矛盾 |
| **过长响应** | responding 状态 > 120s | 流式响应卡住或模型生成了异常长的输出 |

**为什么现有 analyzer 不够：**
- `ConversationFlowAnalyzer` 只在消息级别追踪（message 之间的间隔），不深入单个推理周期
- `LatencyAnalyzer` 测量端到端延迟但不理解语义状态
- `MultiAgentAnomalyAnalyzer` 关注跨 agent 模式而非单 agent 状态机

---

## 2. 接口设计

### 2.1 Analyzer trait

```rust
#[async_trait]
impl Analyzer for AgentStateTransitionTracker {
    async fn process(&mut self, stream: EventStream) -> Result<EventStream, AnalyzerError>;
    fn name(&self) -> &str { "AgentStateTransitionTracker" }
}
```

### 2.2 扩展 trait

| Trait | 是否实现 | 理由 |
|-------|---------|------|
| `AnalyzerLifecycle` | ✅ | `on_start` 初始化 per-agent 状态表；`on_stop` 刷新所有活跃状态 |
| `DrainableAnalyzer` | ✅ | drain 时输出所有仍处于非 idle 状态的 agent 的快照事件 |
| `StreamContractProvider` | ✅ | `output_contract()` 返回 `non_preserving()`——额外 emit `agent_state` 事件 |
| `SharedStateAware` | ❌ | 不需要跨 analyzer 状态；自身状态足够 |

### 2.3 Pipeline 归属

**阶段:** `AnomalyDetection` (Stage 3)

**理由:**
- 需要上游 `HTTPParser`、`SSEProcessor` 已处理的格式化事件
- 需要 `HandoffInjectionAnalyzer` 产出的 handoff 事件来区分思考与 handoff 处理
- 产出 `agent_state` 事件给下游 `AlertEnrichment` 消费

### 2.4 消费/生产契约

| 维度 | 值 |
|------|-----|
| **消费** | `http_parser`, `sse_processor`, `process` |
| **生产** | `agent_state` |
| **Pass-through** | ✅ 所有原始事件透传 |
| **长度保持** | ❌ 额外 emit 状态转换事件 |

---

## 3. 状态机模型

### 3.1 状态定义

```rust
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub enum AgentOperationalState {
    /// Agent 未在执行推理（等待用户输入或上游 handoff）
    Idle,
    /// Agent 正在调用 LLM API（推理中）
    Thinking,
    /// Agent 正在执行工具（等待工具返回）
    ToolExecuting,
    /// Agent 正在流式输出响应
    Responding,
}
```

### 3.2 合法状态转换

```
                    ┌──────────────────┐
                    │      Idle        │ ← 等待触发
                    └───────┬──────────┘
                            │ POST /v1/messages 或 /v1/chat/completions
                            ▼
                    ┌──────────────────┐
                    │    Thinking      │ ← LLM 推理中
                    └───┬──────────┬───┘
                        │          │
          SSE content_  │          │ SSE text_delta
          block (tool)  │          │ (纯文本，无 tool)
                        ▼          ▼
              ┌─────────────┐  ┌──────────────┐
              │ToolExecuting│  │  Responding   │
              └──────┬──────┘  └──────┬───────┘
                     │               │
       POST 请求中    │               │ SSE message_stop
       包含 tool_result│              │ 或下个请求开始
                     │               │
                     ▼               ▼
              ┌──────────────────────────────┐
              │        Thinking              │ ← 将 tool_result 发送给 LLM
              └───┬──────────────────────┬───┘
                  │ 不再有 tool_use       │ SSE text_delta
                  ▼                      ▼
              ┌──────────────┐   ┌──────────────┐
              │  Responding   │   │  Responding   │
              └──────┬───────┘   └──────┬───────┘
                     │                  │
                     ▼                  ▼
              ┌──────────────────────────────┐
              │           Idle               │ ← 等待下一轮
              └──────────────────────────────┘
```

### 3.3 异常状态检测

| 异常 | 检测规则 | 严重度 | 事件类型 |
|------|---------|--------|---------|
| **思考卡死** | Thinking 状态持续 > `thinking_timeout_ns` | high | `THINKING_STUCK` |
| **工具超时** | ToolExecuting 状态持续 > `tool_timeout_ns` | critical | `TOOL_EXECUTION_TIMEOUT` |
| **响应卡死** | Responding 状态持续 > `responding_timeout_ns` | high | `RESPONSE_STUCK` |
| **跳过工具** | Thinking → Responding 但前一个响应包含 tool_use | medium | `MISSING_TOOL_EXECUTION` |
| **状态抖动** | Idle → Thinking → Idle → Thinking（30s 内 3 次） | medium | `STATE_FLAPPING` |
| **异常回跳** | Responding → Thinking（无 tool_result 触发） | low | `UNEXPECTED_REGRESSION` |

---

## 4. 数据流

```
EventStream (http_parser, sse_processor, process)
    │
    ▼   stream.filter_map(move |event|) — 每个事件 1:1 或 1:2 转换
    │
    ├── 事件分类 → 状态转换信号
    │   ├── http_parser + POST + /v1/messages|/v1/chat → TRANSITION_TO_THINKING
    │   ├── sse_processor + content_block_start(tool_use) → TRANSITION_TO_TOOL
    │   ├── sse_processor + content_block_start(text) / text_delta → TRANSITION_TO_RESPONDING
    │   ├── http_parser + POST + body 含 tool_result → TRANSITION_TO_THINKING (下一轮)
    │   ├── sse_processor + message_stop → TRANSITION_TO_IDLE
    │   └── 非关注事件 → pass-through (不产生状态变化)
    │
    ├── 状态管理 (AgentStateTracker)
    │   ├── 对每个 agent_root_pid 维护: HashMap<u32, AgentStateEntry>
    │   ├── 收到转换信号 → 验证合法性 → 更新状态
    │   ├── 记录状态持续时间
    │   └── 检测异常 (超时/跳过/抖动)
    │
    └── 事件发射
        ├── 状态转换成功 → emit agent_state 事件 (event_type: "state_transition")
        ├── 异常检测触发 → emit agent_state 事件 (event_type: "state_anomaly")
        └── 原始事件全部 pass-through
    │
    ▼
EventStream (原始 + agent_state)
```

---

## 5. 核心类型

### 5.1 配置

```rust
#[derive(Debug, Clone)]
pub struct AgentStateTransitionConfig {
    /// 是否启用
    pub enabled: bool,
    /// Thinking 状态超时 (ns)，默认 120s
    pub thinking_timeout_ns: u64,
    /// ToolExecuting 状态超时 (ns)，默认 60s
    pub tool_timeout_ns: u64,
    /// Responding 状态超时 (ns)，默认 180s
    pub responding_timeout_ns: u64,
    /// 最大追踪的 agent 数
    pub max_tracked_agents: usize,        // 默认: 64
    /// 抖动检测窗口 (ns)
    pub flapping_window_ns: u64,          // 默认: 30s
    /// 抖动次数阈值
    pub flapping_threshold: u32,          // 默认: 3
    /// 每 agent 状态历史环形缓冲区大小
    pub history_size_per_agent: usize,    // 默认: 100
}
```

### 5.2 状态条目

```rust
struct AgentStateEntry {
    /// 当前操作状态
    state: AgentOperationalState,
    /// 当前状态开始时间 (epoch ns)
    state_started_at: u64,
    /// 状态转换历史 (环形缓冲区)
    history: VecDeque<StateTransitionRecord>,
    /// PID → agent_root_pid 的映射可能需要上游提供
    agent_root_pid: u32,
}

struct StateTransitionRecord {
    from: AgentOperationalState,
    to: AgentOperationalState,
    timestamp_ns: u64,
    /// 触发此次转换的事件摘要
    trigger_summary: String,
}
```

### 5.3 输出事件格式

```json
// 状态转换事件
{
  "source": "agent_state",
  "event_type": "state_transition",
  "data": {
    "agent_root_pid": 12345,
    "from_state": "Idle",
    "to_state": "Thinking",
    "state_duration_ms": 2350,
    "trigger": "POST /v1/messages",
    "cycle_index": 3,
    "session_duration_ms": 12000
  }
}

// 状态异常事件
{
  "source": "agent_state",
  "event_type": "state_anomaly",
  "data": {
    "agent_root_pid": 12345,
    "anomaly_type": "THINKING_STUCK",
    "current_state": "Thinking",
    "stuck_duration_ms": 125000,
    "threshold_ms": 120000,
    "severity": "high",
    "last_state_change": {
      "from": "Idle",
      "timestamp_ns": 1717400000000000000
    }
  }
}
```

---

## 6. 与现有 Analyzer 的关系

```
Preprocessing (stage 0)
    ├── HTTPParser       → http_parser 事件
    └── SSEProcessor     → sse_processor 事件
         │
InjectionDetection (stage 2)
    └── HandoffInjectionAnalyzer → handoff_injection 事件
         │
AnomalyDetection (stage 3)
    ├── ConversationFlowAnalyzer   ← 会话级: active/stalled/completed
    ├── AgentStateTransitionTracker ← **新增**: idle/thinking/tool/responding
    ├── MultiAgentAnomalyAnalyzer   ← 跨 agent 异常
    └── AgentLoopDetector          ← 工具调用循环
         │
AlertEnrichment (stage 4)
    └── AlertEnrichmentAnalyzer     ← 消费 agent_state 告警
```

**关键设计决策:** ASTT 和 ConversationFlowAnalyzer 分工明确：
- CFA 回答"这次对话是否还在进行？"（粗粒度，分钟级）
- ASTT 回答"agent 现在在做什么？"（细粒度，秒级）
- 两者不重叠：CFA 的 stalled 是指对话静默 > 5 分钟；ASTT 的 thinking_stuck 是指单次推理持续 > 2 分钟

---

## 7. 测试策略

| 层级 | 测试 | 验证点 |
|------|------|--------|
| 单元 | 合法状态转换 | Idle→Thinking→Tool→Thinking→Responding→Idle 全路径 |
| 单元 | 非法状态转换 | Tool→Idle（跳过 thinking/responding）→ 标记异常 |
| 单元 | 超时检测 | 注入 Thinking 状态超过 threshold → THINKING_STUCK |
| 单元 | 抖动检测 | 3 次 Idle↔Thinking 在 30s 内 → STATE_FLAPPING |
| 单元 | 跳过工具 | Thinking→Responding 但 history 显示有 pending tool → MISSING_TOOL_EXECUTION |
| 单元 | 非关注事件透传 | process/system 事件不改变状态，原始事件无损 |
| 集成 | 完整 Anthropic 对话 | 真实 SSE 流 → 正确追踪所有状态转换 |
| 集成 | 多 agent 并发 | 2 个 agent 同时推理 → 状态独立，不混淆 |
| 集成 | Drain 刷新 | 关闭时活跃状态生成快照事件 |

---

## 8. 风险

| 风险 | 缓解 |
|------|------|
| **HTTPParser 未解析 LLM API 路径** | 状态转换可能不触发。静默降级——agent 永远停留在 Idle，不产生告警 |
| **SSE chunk 未完全重组** | 可能漏掉 content_block_start。用超时兜底（Thinking 超过阈值 → 告警）|
| **多框架识别** | v1 仅识别 Anthropic + OpenAI 格式。`process` 事件可作为备选信号 |
| **时钟偏差** | eBPF 事件时间戳一致性由 TimestampNormalizer 保证 |

---

## 变更记录

| 日期 | 变更 |
|------|------|
| 2026-06-04 | 初始设计提案 |
