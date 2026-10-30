# PrivilegeEscalationDetector — 设计文档

> **作者**: lzkk
> **日期**: 2026-06-04
> **状态**: 设计阶段，待评审

## 1. 动机

在多 Agent 协作场景中，每个 Agent 被赋予特定的能力边界（如只读文件访问、受限命令执行、指定工作区）。当 Agent 由于 prompt injection、handoff 投毒或模型幻觉等原因，尝试超出其授权范围的操作时，即构成**权限提升**风险。

**PrivilegeEscalationDetector** 检测以下异常模式：

| 异常类型 | 描述 | 示例 |
|----------|------|------|
| **提权命令执行** | Agent 执行了需要 root/sudo 的命令 | `sudo rm -rf /`、`chown root:root /etc/passwd` |
| **工作区外文件访问** | Agent 访问了其声明工作区之外的文件 | Agent 工作区为 `/home/user/project`，却读取 `/etc/shadow` |
| **敏感路径写入** | 向系统关键路径写入文件 | 写入 `/etc/systemd/system/`、`~/.ssh/authorized_keys` |
| **能力边界突破** | Agent 尝试使用未授权 tool 或 capability | 声明为 read-only 的 Agent 调用了 `write_file` tool |
| **网络越权** | Agent 向未授权的远程地址发起连接 | Agent 连接内网数据库而非声明的 API endpoint |

## 2. 接口设计

### 2.1 Analyzer Trait 实现

```rust
/// 权限提升检测器：检测 Agent 超出授权范围的操作。
pub struct PrivilegeEscalationDetector {
    /// 配置：授权策略、敏感路径列表、检测阈值
    config: PrivilegeEscalationConfig,

    /// 按 agent_root_pid 维度的授权档案
    /// Key: agent_root_pid
    agent_profiles: Arc<Mutex<HashMap<u32, AgentAuthorizationProfile>>>,

    /// 全局去重冷却表
    dedup_cooldown: Arc<Mutex<HashMap<String, u64>>>,

    /// 敏感路径匹配器（编译后的 glob 模式）
    sensitive_path_patterns: Vec<glob::Pattern>,

    /// 提权命令关键词集合
    privileged_commands: HashSet<String>,
}
```

### 2.2 配置类型

```rust
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PrivilegeEscalationConfig {
    /// 是否启用
    pub enabled: bool,

    /// 同一异常的去重冷却时间 (纳秒)，默认 60s
    pub dedup_cooldown_ns: u64,

    /// 敏感系统路径模式列表（glob）
    /// 默认: ["/etc/**", "/boot/**", "/root/**", "~/.ssh/**", "/var/spool/cron/**"]
    pub sensitive_path_patterns: Vec<String>,

    /// 提权命令关键词
    /// 默认: ["sudo", "su", "chown", "chmod 777", "setuid", "pkexec", "doas"]
    pub privileged_command_keywords: Vec<String>,

    /// 已知安全的网络地址白名单 (host:port 格式)
    /// 默认: ["api.anthropic.com:443", "api.openai.com:443"]
    pub allowed_network_targets: Vec<String>,

    /// 是否启用能力边界检测
    pub enforce_capability_boundary: bool,

    /// 是否启用工作区边界检测
    pub enforce_workspace_boundary: bool,

    /// Agent 授权档案的 TTL (纳秒)，默认 600s
    pub profile_ttl_ns: u64,

    /// 最大缓存的 Agent 档案数
    pub max_profiles: usize,  // 默认 128
}
```

### 2.3 授权档案

```rust
/// Agent 的授权边界档案
struct AgentAuthorizationProfile {
    /// Agent root PID
    root_pid: u32,

    /// 声明的工作区路径（来自进程 cwd 或 handoff 元数据）
    declared_workspace: Option<PathBuf>,

    /// 授权的 tool/capability 名称集合
    /// e.g., {"read_file", "search_code", "grep"}
    authorized_tools: HashSet<String>,

    /// 声明的能力级别
    capability_level: CapabilityLevel,

    /// 首次观测时间
    first_seen_ns: u64,

    /// 最后活跃时间
    last_active_ns: u64,

    /// 已触发的告警计数（防止告警风暴）
    alert_count: u32,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
enum CapabilityLevel {
    /// 只读：仅允许 read/search/grep 类操作
    ReadOnly,
    /// 受限写入：允许在指定工作区内写入
    WorkspaceWrite,
    /// 系统级：允许执行命令但不允许提权
    SystemNoPrivilege,
    /// 未声明：从行为推断
    Unknown,
}
```

### 2.4 Analyzer trait

```rust
#[async_trait]
impl Analyzer for PrivilegeEscalationDetector {
    /// 输入: 所有上游事件流 (process, sse_processor, handoff_injection, http_parser, ssl)
    /// 输出: 原始事件 + 插入了 privilege_escalation 告警事件
    async fn process(&mut self, stream: EventStream) -> Result<EventStream, AnalyzerError>;

    fn name(&self) -> &str {
        "PrivilegeEscalationDetector"
    }
}
```

## 3. 数据流

```
EventStream (process, sse, handoff_injection, http_parser, ssl)
  │
  ▼  stream.filter_map(move |event|)
  │
  ├── 非关注 source → 直接透传 (Some(event))
  │
  ├── source == "handoff_injection":
  │     ├── 提取 agent_root_pid, declared_workspace, authorized_tools
  │     ├── 创建/更新 AgentAuthorizationProfile
  │     └── 透传原始事件
  │
  ├── source == "process" (event_type == "exec"):
  │     ├── 提取执行的命令和参数
  │     ├── 按 agent_root_pid 查找 profile
  │     ├── 检测 1: 命令包含 privileged_command_keywords
  │     │   └── 触发: PRIVILEGED_COMMAND_EXEC 告警
  │     ├── 检测 2: 命令涉及敏感路径参数
  │     │   └── 触发: SENSITIVE_PATH_OPERATION 告警
  │     └── 透传原始事件
  │
  ├── source == "process" (event_type == "file_access"):
  │     ├── 提取访问路径 (event.data.path)
  │     ├── 按 agent_root_pid 查找 profile
  │     ├── 检测 3: 路径在工作区外 + enforce_workspace_boundary
  │     │   └── 触发: WORKSPACE_VIOLATION 告警
  │     ├── 检测 4: 路径匹配 sensitive_path_patterns
  │     │   └── 触发: SENSITIVE_PATH_ACCESS 告警
  │     └── 透传原始事件
  │
  ├── source == "sse_processor" (tool_call 事件):
  │     ├── 提取 tool_name
  │     ├── 按 agent_root_pid 查找 profile
  │     ├── 检测 5: tool_name 不在 authorized_tools 中 + enforce_capability_boundary
  │     │   └── 触发: CAPABILITY_BOUNDARY_BREACH 告警
  │     └── 透传原始事件
  │
  ├── source == "ssl" (网络连接):
  │     ├── 提取目标 host:port
  │     ├── 按 pid 查找 profile
  │     ├── 检测 6: 目标不在 allowed_network_targets 中
  │     │   └── 触发: NETWORK_EGRESS_VIOLATION 告警
  │     └── 透传原始事件
  │
  └── 定时清理: 过期 profile 淘汰 (last_active_ns + ttl < now)
        │
        ▼
  EventStream (原始事件 + privilege_escalation 告警事件)
```

### 3.1 流式处理决策

**选择**: `.filter_map()` — 每个事件 1:1 或 1:2 转换。正常事件不变，异常时额外插入告警事件。

**理由**:
- 遵循现有模式（与 LatencyAnalyzer、FileAccessAuditAnalyzer 一致）
- 无界缓冲，适合无限实时流
- `Arc<Mutex<>>` 锁持有时间可控（HashMap 查找 + HashSet 插入 + glob 匹配）
- glob 匹配是唯一可能昂贵的操作；通过预编译 Pattern 和限制匹配路径长度来缓解

**替代方案已排除**:
- `.scan()` — 无法方便地输出多事件（原始 + 告警）
- 批量缓冲 — 延迟告警，不适合实时安全检测场景

### 3.2 告警事件 Schema

```json
{
  "source": "privilege_escalation",
  "event_type": "PRIVILEGED_COMMAND_EXEC",
  "timestamp": 1717430000000000,
  "pid": 12345,
  "comm": "bash",
  "data": {
    "anomaly_type": "privileged_command",
    "agent_root_pid": 100,
    "command": "sudo rm -rf /var/log",
    "matched_keyword": "sudo",
    "agent_workspace": "/home/user/project",
    "capability_level": "WorkspaceWrite",
    "severity": "critical",
    "detail": "Agent pid=100 (capability=WorkspaceWrite) executed privileged command 'sudo rm -rf /var/log' matching keyword 'sudo'"
  }
}
```

```json
{
  "source": "privilege_escalation",
  "event_type": "WORKSPACE_VIOLATION",
  "timestamp": 1717430000000000,
  "pid": 12345,
  "comm": "claude",
  "data": {
    "anomaly_type": "workspace_violation",
    "agent_root_pid": 100,
    "accessed_path": "/etc/passwd",
    "declared_workspace": "/home/user/project",
    "capability_level": "ReadOnly",
    "severity": "high",
    "detail": "Agent pid=100 (workspace=/home/user/project) accessed path '/etc/passwd' outside declared workspace"
  }
}
```

## 4. Pipeline 集成

### 4.1 阶段定位

`PipelineStage::AnomalyDetection` (stage 3) — 在 InjectionDetection (stage 2) 之后，AlertEnrichment (stage 4) 之前。

**理由**: 需要 upstream HandoffInjectionAnalyzer 提供的 Agent 授权元数据（workspace、tools），以及 ProcessRunner 和 SslRunner 的原始事件。属于行为异常检测层。

### 4.2 事件消费/生产契约

| 维度 | 值 |
|------|-----|
| **消费** | `process`, `ssl`, `sse_processor`, `handoff_injection` |
| **生产** | `privilege_escalation` (告警), 原始事件透传 |
| **阶段** | `PipelineStage::AnomalyDetection` |
| **前置依赖** | Preprocessing (TimestampNormalizer), InjectionDetection (HandoffInjectionAnalyzer) |

### 4.3 注册方式

```rust
// 在 PipelineBuilder 中:
builder.add_anomaly_detector(Box::new(
    PrivilegeEscalationDetector::new(PrivilegeEscalationConfig {
        enforce_workspace_boundary: true,
        enforce_capability_boundary: true,
        ..Default::default()
    })
));
```

## 5. 与现有 Analyzer 的关系

```
Preprocessing (stage 0)
  ├── TimestampNormalizer
  ├── SSEProcessor          ← 产生 tool_call 事件
  ├── HTTPParser
  └── FileAccessAuditAnalyzer ← 可共享 file_access 事件
       │
CausalLinking (stage 1)
       │
InjectionDetection (stage 2)
  └── HandoffInjectionAnalyzer ← 产生 Agent 授权元数据
       │
AnomalyDetection (stage 3)
  ├── MultiAgentAnomalyAnalyzer
  ├── SessionBoundaryAnalyzer
  └── PrivilegeEscalationDetector  ← **新增**
       │
AlertEnrichment (stage 4)
  └── AlertEnrichmentAnalyzer ← 归并 privilege_escalation 告警
       │
Output (stage 5)
```

### 与 FileAccessAuditAnalyzer 的差异

| 维度 | FileAccessAuditAnalyzer | PrivilegeEscalationDetector |
|------|------------------------|---------------------------|
| **关注点** | 文件访问审计日志 | 越权检测 |
| **判断依据** | 文件访问模式统计 | Agent 授权档案 vs 实际行为 |
| **输出** | 审计事件 | 安全告警 |
| **依赖** | 仅需 process 事件 | 需 handoff 元数据 + process 事件 |
| **误报容忍** | 低（审计须精确） | 中（安全检测可接受适度误报） |

## 6. 测试策略

### 单元测试

| 测试 | 验证内容 |
|------|----------|
| `normal_exec_no_alert` | Agent 在工作区内执行普通命令不触发告警 |
| `sudo_exec_triggers_critical_alert` | 检测到 `sudo` 命令触发 PRIVILEGED_COMMAND_EXEC |
| `chmod_777_triggers_alert` | `chmod 777 /etc/passwd` 同时触发 PRIVILEGED_COMMAND_EXEC 和 SENSITIVE_PATH_OPERATION |
| `workspace_file_access_no_alert` | 访问工作区内文件不触发告警 |
| `outside_workspace_access_triggers_alert` | 访问 `/etc/shadow` 触发 WORKSPACE_VIOLATION |
| `unknown_tool_call_triggers_alert` | read_only Agent 调用 write_file 触发 CAPABILITY_BOUNDARY_BREACH |
| `unauthorized_network_triggers_alert` | 连接未授权地址触发 NETWORK_EGRESS_VIOLATION |
| `dedup_cooldown` | 冷却窗口内同一异常不重复告警 |
| `non_target_sources_passthrough` | system、latency 等非关注事件直接透传 |
| `profile_ttl_expiry` | 过期 profile 被清理，后续事件创建新 profile |
| `empty_pipeline_passthrough` | 无相关事件时全部透传 |
| `handle_missing_profile_gracefully` | 缺少 profile 时静默跳过检测（不 panic） |

### 集成测试场景

1. **提权命令链**: Agent 先 `curl` 下载脚本 → `chmod +x` → `sudo ./script.sh` → 应产生 3 条告警（其中最后一条为 critical）
2. **工作区遍历**: Agent 逐级访问 `/` → `/etc` → `/etc/shadow` → 每条触发 WORKSPACE_VIOLATION（受去重冷却限制）
3. **多 Agent 正常协作**: Agent A（WorkspaceWrite）在工作区内正常操作，Agent B（ReadOnly）只读访问 → 0 条告警
4. **非法 tool 使用**: read_only Agent 在 SSE 流中尝试调用 `bash` tool → CAPABILITY_BOUNDARY_BREACH 告警

## 7. 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| **授权档案缺失** | 检测失效（漏报） | Agent 行为无 profile 时使用 Unknown 级别 + 宽松策略；在 `process()` 中记录 warning 级别日志 |
| **误报：合法 sudo 操作** | 正常 CI/CD 流程被标记 | `privileged_command_keywords` 可配置移除特定命令；dedup_cooldown 防止告警风暴 |
| **glob 匹配性能** | 高吞吐下延迟增加 | 预编译 `glob::Pattern`；敏感路径匹配前先做前缀快速排除；限制匹配路径长度 < 4096 |
| **profile 表膨胀** | 内存泄漏 | `max_profiles` 限制 + TTL 淘汰；最坏情况 128 × ~2KB = 256KB |
| **handoff 元数据不可靠** | 授权边界被绕过 | `CapabilityLevel::Unknown` 作为默认值，Agent 行为无声明时使用启发式推断（观察到 exec 则至少 SystemNoPrivilege） |
| **命令注入绕过关键词** | 攻击者使用 base64 编码或别名 | 对命令参数做 base64 解码后再匹配；记录所有 exec 事件的原始命令供离线分析 |

## 8. 实现决策

### 8.1 授权档案的来源

授权档案通过以下途径建立（优先级递减）：

1. **Handoff 元数据** — HandoffInjectionAnalyzer 输出的 `handoff_metadata` 事件中的 `authorized_tools`、`workspace` 字段（最可靠）
2. **进程 cwd 推断** — Agent 进程的初始工作目录作为 `declared_workspace` 的 fallback
3. **启发式推断** — 观察到 `read_file` tool 调用 → 推断 `CapabilityLevel::ReadOnly`；观察到 `write_file` + `bash` → 推断至少 `WorkspaceWrite`

### 8.2 敏感路径匹配优化

```rust
impl PrivilegeEscalationDetector {
    fn is_sensitive_path(&self, path: &str) -> bool {
        // 快速前缀排除（大多数文件访问在工作区内）
        if !path.starts_with('/') && !path.starts_with('~') {
            return false;
        }
        // 长度限制
        if path.len() > 4096 {
            return false;
        }
        // glob 匹配
        self.sensitive_path_patterns.iter().any(|p| p.matches(path))
    }
}
```

### 8.3 告警严重度规则

| 异常类型 | 默认严重度 | 升级条件 |
|----------|-----------|---------|
| PRIVILEGED_COMMAND_EXEC | critical | N/A（任何提权命令均为 critical） |
| SENSITIVE_PATH_ACCESS | high | 涉及写入操作 → critical |
| WORKSPACE_VIOLATION | medium | 访问系统关键路径 (/etc/shadow, /root/.ssh) → high |
| CAPABILITY_BOUNDARY_BREACH | high | 涉及文件写入 tool → critical |
| NETWORK_EGRESS_VIOLATION | medium | 目标为内网地址 → high |

## 9. 待决策事项

1. **handoff 元数据字段对齐**: 需要确认 HandoffInjectionAnalyzer 当前输出的 `handoff_metadata` 事件中是否包含 `workspace` 和 `authorized_tools` 字段。若无，需在 HandoffInjectionAnalyzer 中新增此提取逻辑。

2. **tool_call 事件格式**: SSEProcessor 产生的 tool_call 事件中 tool_name 的字段路径需确认 (`event.data.tool_name` vs `event.data.name` vs `event.data.function.name`)。

3. **与 FileAccessAuditAnalyzer 去重**: file_access 事件被两个 analyzer 消费时，如果 FileAccessAuditAnalyzer 也产生告警，需要确保告警事件不会重复。建议两个 analyzer 各自输出不同 source 前缀（`file_access_audit` vs `privilege_escalation`）。

4. **DSL 规则引擎**: 长期来看，授权策略应该用规则 DSL 表达而非硬编码关键词。建议 v1 先用关键词 + glob，v2 引入规则引擎。

---

**请团队成员评审以上设计，重点关注**:
- 授权档案的建立路径是否可靠（handoff 元数据 → cwd → 启发式）
- 敏感路径 glob 列表是否完备
- 与 HandoffInjectionAnalyzer / FileAccessAuditAnalyzer 的交互边界是否清晰
- 是否有遗漏的权限提升场景
