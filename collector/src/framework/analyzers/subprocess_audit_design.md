# SubprocessAuditAnalyzer — Rust 设计文档

## 1. 概述

**SubprocessAuditAnalyzer** 是一个有状态的流式 analyzer，消费 eBPF `process` 源中的 `EXEC` 事件，维护进程父子关系拓扑，对 Agent 产生的子进程执行进行安全审计，按风险等级分类并发出结构化告警。

### 动机

LLM Agent 在执行任务时会通过 tool-use 机制启动子进程（shell 命令、脚本、编译器、网络工具等）。当前流水线对子进程执行缺乏结构化安全审计，无法回答：

- Agent 是否执行了意料之外的命令？
- 是否存在权限提升尝试（sudo/su/doas）？
- 进程链是否出现异常模式（如 python → /bin/sh → curl）？
- 是否触碰了敏感系统工具（iptables、chmod、mount）？

SubprocessAuditAnalyzer 填补这个空白，为安全运维和合规审计提供结构化的子进程执行记录。

---

## 2. 接口定义

### Analyzer trait 实现

```rust
#[async_trait]
impl Analyzer for SubprocessAuditAnalyzer {
    /// 处理输入流：
    ///   - source != "process" 的事件：直接透传
    ///   - source == "process" 且 event == "EXEC" 的事件：
    ///     1. 更新进程拓扑（pid→ppid 映射、pid→root_pid 映射）
    ///     2. 对命令进行分类和风险评估
    ///     3. 高风险命令发出 source="subprocess_audit" 的告警事件
    ///   - source == "process" 且 event == "EXIT" 的事件：
    ///     清理进程拓扑
    ///   - 所有事件均透传，原始 process 事件不受影响
    async fn process(&mut self, stream: EventStream) -> Result<EventStream, AnalyzerError>;

    fn name(&self) -> &str {
        "SubprocessAuditAnalyzer"
    }
}
```

### 配置结构

```rust
pub struct SubprocessAuditConfig {
    /// 视为「敏感系统工具」的命令名集合。
    /// 执行这些命令会产生 risk_level="high" 的告警。
    pub sensitive_binaries: Vec<String>,

    /// 视为「网络工具」的命令名集合。
    /// Agent 启动网络工具时可能表示数据外传尝试。
    pub network_tools: Vec<String>,

    /// 视为「权限提升」的命令名集合。
    /// 如 sudo, su, doas, pkexec。
    pub privilege_escalation_binaries: Vec<String>,

    /// 视为「Shell 解释器」的命令名集合。
    /// Agent 启动 shell 可能表示命令注入或沙箱逃逸。
    pub shell_binaries: Vec<String>,

    /// 视为「包管理器」的命令名集合。
    /// 非预期的包安装可能是供应链攻击信号。
    pub package_managers: Vec<String>,

    /// 敏感路径前缀（如 /etc/passwd, /root/）。
    /// 当命令行参数中包含这些路径时，提升风险等级。
    pub sensitive_path_prefixes: Vec<String>,

    /// 最大进程树深度，超过此深度发出告警。
    pub max_process_tree_depth: usize,

    /// 同一 (root_pid, command) 的告警冷却时间（纳秒）。
    pub alert_cooldown_ns: u64,

    /// 最低告警级别，低于此级别的告警被静默丢弃。
    /// "info" | "low" | "medium" | "high" | "critical"
    pub min_alert_level: String,
}

impl Default for SubprocessAuditConfig {
    fn default() -> Self {
        Self {
            sensitive_binaries: vec![
                "iptables".into(), "ip6tables".into(), "nft".into(),
                "chmod".into(), "chown".into(), "mount".into(),
                "umount".into(), "mkfs".into(), "dd".into(),
                "systemctl".into(), "modprobe".into(), "insmod".into(),
            ],
            network_tools: vec![
                "curl".into(), "wget".into(), "nc".into(), "ncat".into(),
                "socat".into(), "telnet".into(), "ssh".into(),
                "scp".into(), "rsync".into(), "ftp".into(),
                "nslookup".into(), "dig".into(), "nmap".into(),
            ],
            privilege_escalation_binaries: vec![
                "sudo".into(), "su".into(), "doas".into(),
                "pkexec".into(), "newgrp".into(),
            ],
            shell_binaries: vec![
                "bash".into(), "sh".into(), "dash".into(),
                "zsh".into(), "fish".into(), "csh".into(),
                "tcsh".into(), "ksh".into(),
            ],
            package_managers: vec![
                "apt".into(), "apt-get".into(), "dpkg".into(),
                "yum".into(), "dnf".into(), "rpm".into(),
                "pacman".into(), "apk".into(), "pip".into(),
                "pip3".into(), "npm".into(), "npx".into(),
                "pnpm".into(), "yarn".into(), "cargo".into(),
                "gem".into(), "go".into(),
            ],
            sensitive_path_prefixes: vec![
                "/etc/passwd".into(), "/etc/shadow".into(),
                "/etc/sudoers".into(), "/root/".into(),
                "/home/*/.ssh/".into(), "/etc/ssl/private/".into(),
            ],
            max_process_tree_depth: 8,
            alert_cooldown_ns: 30_000_000_000, // 30 seconds
            min_alert_level: "low".into(),
        }
    }
}
```

### 输出事件结构

source 为 `"subprocess_audit"`，`data` 字段结构：

```json
{
    "event": "SUBPROCESS_ALERT",
    "rule_id": "privilege_escalation",
    "severity": "critical",
    "risk_level": "high",
    "exec": {
        "pid": 12345,
        "ppid": 12300,
        "root_pid": 10000,
        "command": "/usr/bin/sudo",
        "full_command": "sudo cat /etc/shadow",
        "filename": "/usr/bin/sudo",
        "args": ["cat", "/etc/shadow"],
        "cwd": "/home/user/project"
    },
    "process_chain": {
        "depth": 4,
        "ancestors": [
            {"pid": 10000, "comm": "claude"},
            {"pid": 11000, "comm": "node"},
            {"pid": 12300, "comm": "bash"},
            {"pid": 12345, "comm": "sudo"}
        ],
        "chain_exceeded_max_depth": false
    },
    "classification": {
        "is_sensitive_binary": false,
        "is_network_tool": false,
        "is_privilege_escalation": true,
        "is_shell": false,
        "is_package_manager": false,
        "has_sensitive_path_args": true,
        "matched_rules": ["privilege_escalation", "sensitive_path_in_args"]
    },
    "agent": {
        "agent_id": "agent-10000",
        "root_pid": 10000,
        "root_comm": "claude"
    },
    "source_event": {
        "timestamp_ns": 1718000000000000000
    }
}
```

---

## 3. 数据流

```
EventStream (source="process", event="EXEC" 的事件)
    │
    ▼
┌──────────────────────────────────────────────┐
│         SubprocessAuditAnalyzer               │
│                                              │
│  source != "process" ?                       │
│    ├─ 是 → 直接透传，不做处理                  │
│    └─ 否 → 进入 process 事件分类              │
│                                              │
│  event == "EXEC" ?                           │
│    ├─ 是 →                                   │
│    │   1. 提取 pid, ppid, filename,           │
│    │      full_command, cwd                   │
│    │   2. 更新进程拓扑：                       │
│    │      - pid_parent[pid] = ppid            │
│    │      - pid_root[pid] = resolve_root(ppid) │
│    │   3. 构建 process_chain（向根遍历祖先）   │
│    │   4. 分类命令：                           │
│    │      - 命令名匹配 sensitive_binaries      │
│    │      - 命令名匹配 network_tools           │
│    │      - 命令名匹配 privilege_escalation    │
│    │      - 命令名匹配 shell_binaries          │
│    │      - 命令名匹配 package_managers        │
│    │      - 参数中包含 sensitive_path_prefixes │
│    │   5. 计算 risk_level（高/中/低/信息）     │
│    │   6. 若 risk_level >= min_alert_level     │
│    │      且未在冷却期内：                      │
│    │      → 构建 source="subprocess_audit" 事件 │
│    │      → 插入到输出流中                      │
│    │   7. 原始 EXEC 事件透传                    │
│    │                                           │
│    ├─ event == "EXIT" →                       │
│    │   清理 pid_parent[pid] 和 pid_root[pid]   │
│    │   原始 EXIT 事件透传                       │
│    │                                           │
│    └─ 其他 event → 直接透传                     │
│                                              │
│  输出: 原始事件 + subprocess_audit 告警事件     │
└──────────────────────────────────────────────┘
    │
    ▼
下游（日志 / 责任追溯 / 前端 TimelinePanel）
```

### 进程拓扑追踪

```
EXEC(pid=10000, ppid=1, comm="claude")
  → pid_parent[10000] = 1
  → pid_root[10000] = 10000  (ppid=1 是 init，自为根)

EXEC(pid=11000, ppid=10000, comm="bash")
  → pid_parent[11000] = 10000
  → pid_root[11000] = 10000  (向上追溯到 root)

EXEC(pid=12345, ppid=11000, comm="sudo")
  → pid_parent[12345] = 11000
  → pid_root[12345] = 10000  (向上追溯到 root)

构建 process_chain:
  12345 → 11000 → 10000 → 1 (init，截止)
  结果: [{pid: 10000, comm: "claude"}, {pid: 11000, comm: "bash"}, {pid: 12345, comm: "sudo"}]
  depth = 3
```

### 风险等级计算逻辑

```
对每个 EXEC 事件，按顺序检查以下规则，取最高匹配等级的规则：

┌──────────────────────────────────────────────┬──────────┬──────────┐
│ 匹配规则                                      │ risk_level│ severity │
├──────────────────────────────────────────────┼──────────┼──────────┤
│ 权限提升 + 敏感路径参数                        │ critical │ critical │
│ 权限提升 (sudo/su/doas)                       │ high     │ critical │
│ 敏感系统工具 (iptables/mount/chmod)            │ high     │ high     │
│ 网络工具 + 进程链深度 > 3                      │ high     │ high     │
│ 包管理器 (apt/pip/npm)                        │ medium   │ medium   │
│ Shell 解释器 (bash/sh/zsh)                    │ medium   │ low      │
│ 网络工具 (curl/wget/nc)                       │ medium   │ medium   │
│ 敏感路径参数                                  │ medium   │ medium   │
│ 进程树深度超过 max_process_tree_depth          │ low      │ low      │
│ 其他 EXEC（未被任何规则匹配）                  │ info     │ —        │
└──────────────────────────────────────────────┴──────────┴──────────┘

只有 risk_level >= min_alert_level 的才发出 subprocess_audit 事件。
info 级别的命令（正常命令）不发出告警事件，减少噪音。
```

---

## 4. 内部状态结构

```rust
#[derive(Debug, Default)]
struct SubprocessAuditState {
    /// pid → parent_pid 映射（EXEC 时填充，EXIT 时清理）
    pid_parent: HashMap<u32, u32>,

    /// pid → agent_root_pid 映射（向根追溯，直到 pid_parent[pid] == 1 或无记录）
    pid_root: HashMap<u32, u32>,

    /// pid → (comm, filename, full_command, timestamp) 缓存，
    /// 用于构建 process_chain 时获取已退出进程的信息。
    pid_info: HashMap<u32, ProcessInfo>,

    /// 去重键 → 上次告警时间戳，用于冷却控制。
    last_alert_ts: HashMap<String, u64>,

    /// 计数器，生成唯一告警 ID。
    alert_counter: u64,
}

struct ProcessInfo {
    comm: String,
    filename: String,
    full_command: String,
    exec_timestamp: u64,
}
```

### 内存管理

- `pid_parent` 和 `pid_root`：EXEC 时插入，EXIT 时删除。通常 Agent 生命周期内的进程数在 10²~10³ 级别，内存占用可忽略。
- `pid_info`：进程退出后保留用于构建祖先链。使用 LRU 淘汰策略（上限 1024 条），防止长期运行会话的内存泄漏。
- `last_alert_ts`：使用与告警冷却时间相关的条目数上限（4096 条），超限时清理最旧条目。

---

## 5. 关键实现决策

### 5a. 命令分类为精确匹配

使用 `Path::file_name()` 提取命令名（如 `/usr/bin/sudo` → `sudo`），与配置的分类集合做精确字符串匹配。不使用正则或模糊匹配——保持确定性和低开销。

### 5b. 进程链深度的含义

进程链深度 = 从当前进程到 agent root 的跳数（不含 init）。深度 1 表示直接子进程，深度 > 3 表示「壳套壳」模式（如 python → bash → curl），这是典型的注入后行为特征。

### 5c. 不解析命令行参数语义

仅做字符串级别的参数检查（路径前缀匹配）。不尝试解析 shell 语法的语义（如 `$()`、反引号、重定向），这属于 shell 解析器的工作范围，超出本 analyzer 的职责。

### 5d. 冷却策略

去重键：`"{root_pid}:{rule_id}:{filename}"`。同一 Agent 对同一命令的同一规则，30 秒内只告警一次。防止循环调用或高频命令产生告警风暴。

### 5e. 与现有 analyzer 的关系

- **FileAccessAuditAnalyzer**：处理 FILE_OPEN 事件，关注文件访问边界；SubprocessAuditAnalyzer 处理 EXEC 事件，关注命令执行安全。两者互不重叠，可以在流水线中并行或串行放置。
- **CredentialLeakAnalyzer**：关注凭证泄露；SubprocessAuditAnalyzer 关注命令执行模式。两者消费不同的事件源字段。
- **HandoffInjectionAnalyzer**：关注跨 Agent 的注入传播；SubprocessAuditAnalyzer 关注单 Agent 内的进程行为。

### 5f. 在流水线中的推荐位置

```
TimestampNormalizer (时间戳标准化)
    │
    ▼
SubprocessAuditAnalyzer ← 插入在此处（尽早获得进程拓扑）
    │
    ▼
FileAccessAuditAnalyzer（可能需要进程拓扑信息）
    │
    ▼
其他下游分析器
```

原因：尽早建立进程拓扑，让后续 analyzer 可以通过 `pid_root` 将事件归属到正确的 Agent。

---

## 6. 集成清单

### mod.rs 注册

```rust
// analyzers/mod.rs
pub mod subprocess_audit;
pub use subprocess_audit::{SubprocessAuditAnalyzer, SubprocessAuditConfig};
```

### pipeline_contract.rs 更新

```
Preprocessing 阶段的 produces 集合添加 "subprocess_audit"
ProcessAudit 阶段的 consumes 集合添加 "process" (EXEC 事件)
```

### AgentRunner 集成

```rust
// 在 build_pipeline() 中：
runner.add_analyzer(Box::new(SubprocessAuditAnalyzer::new(SubprocessAuditConfig::default())));
```

---

## 7. 测试策略

| 测试用例 | 验证点 |
|---------|--------|
| 权限提升命令（sudo）产生 critical 告警 | 分类正确性 |
| 普通命令（ls, cat, echo）不产生告警 | 噪音控制 |
| 敏感路径参数（/etc/shadow）提升风险等级 | 参数审计 |
| 进程链深度 > max 产生 low 告警 | 边界条件 |
| 子进程归属到正确的 root_pid | 进程拓扑正确性 |
| EXIT 事件清理 pid_parent/pid_root | 内存管理 |
| 冷却期内重复命令不产生重复告警 | 去重逻辑 |
| 冷却期后同一命令再次产生告警 | 冷却过期 |
| 空 full_command 事件不崩溃 | 健壮性 |
| 非 process 源事件透传不受影响 | 透传契约 |

### 测试工具

- `futures::stream::iter()` 构造模拟 EventStream
- `StreamExt::collect()` 收集输出验证
- `tokio::test` 异步测试框架

---

## 8. 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| eBPF probe 漏报 EXEC 事件 | 进程拓扑不完整，归属错误 | pid_root 回退到 pid 自身；对无 ppid 的 EXEC 事件，默认以自身为 root |
| 高频 EXEC 事件导致告警风暴 | 下游日志/存储过载 | 冷却机制 + min_alert_level 过滤 |
| pid 复用导致拓扑错乱 | 进程链构建错误 | EXIT 事件主动清理；pid_info LRU 上限防止旧数据残留 |
| 长生命周期会话内存增长 | 内存泄漏 | pid_info LRU 上限 1024；last_alert_ts 上限 4096 |
| 自定义 Agent 使用非标准命令名 | 分类遗漏 | 配置完全可定制，用户可通过 SubprocessAuditConfig 添加自定义分类 |
