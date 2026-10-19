# SessionBoundaryAnalyzer — 设计文档

> **作者**: lzkk  
> **日期**: 2026-06-03  
> **状态**: 设计阶段，待评审  

## 1. 动机

在多 Agent 协作场景中，一个 LLM 会话（conversation session）理论上应只处理单一用户的请求。当攻击者通过中间人、prompt injection 或共享文件投毒等手段，将恶意请求"走私"进合法会话时，整个 Agent 团队的信任链就会被污染。

**SessionBoundaryAnalyzer** 检测以下异常模式：

| 异常类型 | 描述 | 示例 |
|----------|------|------|
| **跨用户请求混入** | 同一 TLS session 中出现多个不同 `user_id` 的请求 | Agent A 正在执行用户 X 的任务，突然出现发往用户 Y 的 API 调用 |
| **会话上下文突变** | SSE 流中 `conversation_id` 或其他会话标识在单个连接内发生突变 | `conversation_id` 从 `conv_abc` 突变为 `conv_xyz` |
| **未授权 handoff 接收** | 某 Agent 接受了不是发给它的 handoff 消息 | Agent B 消费了只有 Agent A 应该处理的 `handoff_injection` 事件 |

## 2. 接口设计

### 2.1 Analyzer Trait 实现

```rust
/// 会话边界分析器：检测单个 LLM 会话中的跨用户/跨上下文异常。
pub struct SessionBoundaryAnalyzer {
    /// 配置：冷却窗口、最大缓冲大小、检测阈值
    config: SessionBoundaryConfig,

    /// 按 (pid, tls_session_id) 维度的会话状态
    /// Key: "pid:tls_session_id"
    sessions: Arc<Mutex<HashMap<String, SessionBoundaryState>>>,

    /// 全局去重冷却表：key = (anomaly_type, source_pid, detail_hash)
    dedup_cooldown: Arc<Mutex<HashMap<String, u64>>>,
}
```

### 2.2 配置类型

```rust
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SessionBoundaryConfig {
    /// 是否启用
    pub enabled: bool,

    /// 会话状态在无活动后的过期时间 (纳秒)，默认 300s
    pub session_ttl_ns: u64,

    /// 同一异常的去重冷却时间 (纳秒)，默认 30s
    pub dedup_cooldown_ns: u64,

    /// 单会话内允许的最大不同 user_id 数，超过触发告警
    pub max_distinct_users_per_session: usize,  // 默认 1

    /// 单会话内 conversation_id 突变的最大容忍次数
    pub max_conversation_mutations: usize,       // 默认 0

    /// 最大缓存的活跃会话数
    pub max_active_sessions: usize,              // 默认 256
}
```

### 2.3 会话状态

```rust
struct SessionBoundaryState {
    /// 首次观测到的时间戳
    first_seen_ns: u64,
    /// 最后活跃时间戳
    last_active_ns: u64,
    /// 此会话中已出现的所有 user_id
    seen_user_ids: HashSet<String>,
    /// 当前 conversation_id（用于检测突变）
    current_conversation_id: Option<String>,
    /// conversation_id 突变次数
    conversation_mutations: usize,
    /// 此会话关联的 Agent root_pid 集合
    agent_roots: HashSet<u32>,
}
```

### 2.4 Analyzer trait

```rust
#[async_trait]
impl Analyzer for SessionBoundaryAnalyzer {
    /// 输入: 所有上游事件流 (ssl, sse_processor, handoff_injection, http_parser)
    /// 输出: 原始事件 + 插入了 session_boundary 告警事件
    async fn process(&mut self, stream: EventStream) -> Result<EventStream, AnalyzerError>;

    fn name(&self) -> &str {
        "SessionBoundaryAnalyzer"
    }
}
```

## 3. 数据流

```
EventStream (ssl, sse, handoff_injection, http_parser)
  │
  ▼  stream.filter_map(move |event|)
  │
  ├── 非关注 source → 直接透传 (Some(event))
  │
  ├── source == "ssl":
  │     ├── 提取 tls_session_id (来自 event.data.connection_id)
  │     ├── 提取 user_id (来自 HTTP 请求体中的 user 字段，需 upstream HTTPParser 已解析)
  │     ├── 查找/创建 SessionBoundaryState (keyed by "pid:tls_session_id")
  │     ├── 检测: seen_user_ids.len() > max_distinct_users_per_session
  │     │   └── 触发: SESSION_CROSS_USER 告警事件
  │     └── 透传原始 ssl 事件
  │
  ├── source == "sse_processor":
  │     ├── 提取 conversation_id (来自 event.data.conversation_id)
  │     ├── 查找 SessionBoundaryState
  │     ├── 检测: conversation_id 与 current_conversation_id 不同
  │     │   └── mutations++ ；若 > max_conversation_mutations → SESSION_CONTEXT_MUTATION 告警
  │     └── 透传原始 sse 事件
  │
  ├── source == "handoff_injection":
  │     ├── 提取 target_agent_root_pid (来自 event.data.ppid 或 handoff 目标字段)
  │     ├── 查找 target 对应的 SessionBoundaryState
  │     ├── 检测: target 不在 agent_roots 中
  │     │   └── 触发: UNAUTHORIZED_HANDOFF_RECEIVE 告警
  │     └── 透传原始 handoff_injection 事件
  │
  └── 定时清理: 每个事件处理时顺便清理过期 session (last_active_ns + ttl < now)
        │
        ▼
  EventStream (原始事件 + session_boundary 告警事件)
```

### 3.1 流式处理决策

**选择**: `.filter_map()` — 每个事件 1:1 或 1:2 转换（正常事件不变，异常时额外插入告警事件）。

**理由**:
- 遵循现有模式（LatencyAnalyzer、FileAccessAuditAnalyzer 均为 filter_map/map）
- 无界缓冲 — 不会在无限实时流上挂起
- `Arc<Mutex<>>` 锁持有时间 ~微秒级（HashMap 查找 + HashSet 插入 + 字符串比较）

**替代方案已排除**:
- `.scan()` — 适合有状态聚合但返回 `Option<Event>` 限制了多事件输出的灵活性
- `.collect()` — 对无限流不可行

### 3.2 告警事件 Schema

```json
{
  "source": "session_boundary",
  "event_type": "SESSION_CROSS_USER",
  "timestamp": 1717430000000000,
  "pid": 12345,
  "comm": "claude",
  "data": {
    "anomaly_type": "cross_user",
    "tls_session_id": "tls_0xffff9999",
    "offending_user_id": "user_z",
    "previously_seen_users": ["user_x"],
    "agent_root_pid": 100,
    "severity": "high",
    "detail": "Session tls_0xffff9999 for pid 12345 received request from user_z, but already handled requests from user_x"
  }
}
```

## 4. Pipeline 集成

### 4.1 阶段定位

`PipelineStage::AnomalyDetection` (stage 3) — 在 InjectionDetection (stage 2) 之后，AlertEnrichment (stage 4) 之前。

**理由**: 需要 upstream HandoffInjectionAnalyzer 和 HTTPParser/SSEProcessor 已产生的事件，属于异常检测层而非预处理层。

### 4.2 事件消费/生产契约

| 维度 | 值 |
|------|-----|
| **消费** | `ssl`, `sse_processor`, `http_parser`, `handoff_injection` |
| **生产** | `session_boundary` (告警), 原始事件透传 |
| **阶段** | `PipelineStage::AnomalyDetection` |
| **前置依赖** | Preprocessing (HTTPParser, SSEProcessor, TimestampNormalizer) |

### 4.3 注册方式

```rust
// 在 main.rs 或 pipeline builder 中:
agent = agent.add_global_analyzer(Box::new(
    SessionBoundaryAnalyzer::new(SessionBoundaryConfig::default())
));
```

## 5. 与现有 Analyzer 的关系

```
Preprocessing (stage 0)
  ├── TimestampNormalizer
  ├── SSEProcessor          ← 产生 sse_processor 事件
  ├── HTTPParser            ← 产生 http_parser 事件
  └── SSLFilter
       │
CausalLinking (stage 1)
       │
InjectionDetection (stage 2)
  └── HandoffInjectionAnalyzer ← 产生 handoff_injection 事件
       │
AnomalyDetection (stage 3)
  ├── MultiAgentAnomalyAnalyzer
  ├── ContextPollutionAnalyzer
  └── SessionBoundaryAnalyzer  ← **新增**
       │
AlertEnrichment (stage 4)
  └── AlertEnrichmentAnalyzer ← 消费 session_boundary 告警并归并
       │
Output (stage 5)
```

## 6. 测试策略

### 单元测试

| 测试 | 验证内容 |
|------|----------|
| `single_user_session_no_alert` | 同一 user_id 的多次请求不触发告警 |
| `cross_user_session_triggers_alert` | 第二个不同 user_id 出现时触发 SESSION_CROSS_USER |
| `conversation_id_mutation_alert` | conversation_id 突变超过阈值时触发 SESSION_CONTEXT_MUTATION |
| `unauthorized_handoff_alert` | 目标不在 agent_roots 中的 handoff 触发 UNAUTHORIZED_HANDOFF_RECEIVE |
| `session_ttl_expiry` | 过期 session 被清理，旧 key 不再匹配 |
| `dedup_cooldown` | 冷却窗口内同类型异常不重复告警 |
| `non_target_sources_passthrough` | process、system 等非关注事件直接透传 |
| `empty_pipeline_passthrough` | 无相关事件时全部透传 |

### 集成测试场景

1. **两用户混入**: 模拟同一 TLS session 中出现 user_a 和 user_b 的请求 → 应产生 1 条 SESSION_CROSS_USER 告警
2. **多 Agent 正常协作**: 3 个 Agent 各自在自己的 session 中处理自己的用户 → 0 条告警
3. **上下文走私**: SSE 流中 conversation_id 从 conv_a 突变为 conv_b → 1 条 SESSION_CONTEXT_MUTATION 告警

## 7. 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| **HTTPParser 未解析 user_id** | 跨用户检测失效（漏报） | 在 `process()` 中静默跳过缺少 user_id 的 ssl 事件；不阻塞 pipeline |
| **高吞吐下 HashMap 膨胀** | 内存泄漏 | `max_active_sessions` 限制 + 每次事件处理时附带 TTL 淘汰；最坏情况 256 × ~1KB = 256KB |
| **冷却表膨胀** | 去重键累积 | 同样附带 TTL 淘汰；每个告警类型 + 源 PID 维度有限 |
| **锁竞争** | 高并发下延迟 | 当前架构单 analyzer 串行处理事件，实际无竞争；未来若改为并行，用 `DashMap` 替代 `Arc<Mutex<HashMap>>` |
| **误报：合法多用户 Gateway** | 正常流量被标记 | `max_distinct_users_per_session` 可配置为 > 1（如 API Gateway 场景设为 10）|

## 8. 待决策事项

1. **user_id 提取路径**: 依赖 HTTPParser 将请求体中的 `user` / `user_id` 字段提升到 `event.data` 顶层。需要确认 HTTPParser 当前是否已做此提取，或是否需要在 HTTPParser 中新增此逻辑。

2. **conversation_id 来源**: SSEProcessor 是否已在事件中产出 `conversation_id` 字段？需要实地确认。

3. **handoff 目标解析**: HandoffInjectionAnalyzer 当前的输出事件中 `target_agent_root_pid` 字段名和语义需对齐。

---

**请团队成员评审以上设计，重点关注**:
- 消费/生产契约是否与现有 pipeline 兼容
- `user_id` / `conversation_id` 的提取路径是否可行
- 是否有遗漏的异常模式
