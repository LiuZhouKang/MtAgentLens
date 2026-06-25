# MtAgentLens 系统架构

## 概述

MtAgentLens 是一个基于 eBPF 的零插桩 LLM Agent 可观测性系统。它在 Linux 内核系统调用边界拦截 SSL/TLS 明文、进程生命周期、文件操作和网络连接，通过 Rust 流式引擎进行事件处理与因果分析，最终在 Next.js 前端呈现。

**核心设计原则**：所有攻击最终都表现为系统调用。MtAgentLens 在攻击触碰 OS 之前就已经看到了它——而攻击者无法关闭内核。

---

## 全局架构

```
                          USER SPACE
  +-----------------------------------------------------------------+
  |  Verified: Claude Code                                          |
  |  Expected: any LLM Agent process (Python, Node.js, ...)         |
  |  +----------+  +----------+  +----------+  +----------+         |
  |  | Agent A  |  | Agent B  |  | Agent C  |  |   ...    |         |
  |  +----+-----+  +----+-----+  +----+-----+  +----+-----+         |
  |       |  App-level logs (mutable, bypassable) — untrusted       |
  +-------+--------------+--------------+--------------+-------------+
          |              |              |              |
  ========+==============+==============+==============+=============
          |              |  SYSCALL BOUNDARY           |
  ========+==============+==============+==============+=============
          |              |              |              |
  +-------+--------------+--------------+--------------+-------------+
  |                      eBPF PROBES                                |
  |  tls_capture         process           stdiocap                 |
  |  +---------------+   +-------------+   +----------------+       |
  |  | SSL_read      |   | exec / exit |   | read / write   |       |
  |  | SSL_write     |   | fork / clone|   | stdin / stdout |       |
  |  | uprobe (L3)   |   | open / unlink|  | MCP plaintext  |       |
  |  +---------------+   +-------------+   +----------------+       |
  |  fileaccess          browsertrace       system                  |
  |  +---------------+   +-------------+   +----------------+       |
  |  | file R/W ops  |   | browser SSL |   | CPU / memory   |       |
  |  +---------------+   +-------------+   +----------------+       |
  +---------------------------------+-------------------------------+
                                    | JSON event stream (stdout)
  ==================================+================================
                                    |
  +---------------------------------+-------------------------------+
  |                    RUST STREAMING ENGINE                        |
  |  +-----------------------------------------------------------+ |
  |  | Runner Layer (7 runners)                                   | |
  |  | SslRunner  ProcessRunner  StdioRunner                     | |
  |  | SystemRunner  AgentRunner  FakeRunner (test)              | |
  |  +-----------------------------+-----------------------------+ |
  |                                | EventStream                   |
  |  +-----------------------------+-----------------------------+ |
  |  | Analyzer Pipeline (29 analyzers)                           | |
  |  |                                                            | |
  |  |  +-----------+  +-----------+  +------------------------+  | |
  |  |  | SSE reass |  | HTTP parse|  | TimestampNormalizer   |  | |
  |  |  +-----+-----+  +-----+-----+  +-----------+------------+  | |
  |  |        +--------------+--------------------+                | |
  |  |                      v                                      | |
  |  |  +---------------------------------------------------+     | |
  |  |  |            ResponsibilityRuntime                   |     | |
  |  |  |                                                   |     | |
  |  |  |  Phase 1 (streaming, per-event)                   |     | |
  |  |  |  +---------------------------------------------+  |     | |
  |  |  |  | PhaseClassificationEngine                   |  |     | |
  |  |  |  |   -> 2,595 rules, 6-dim combinatorial score  |  |     | |
  |  |  |  |   -> Process Boundary Crossing (PBC) detect  |  |     | |
  |  |  |  +---------------------+-----------------------+  |     | |
  |  |  |                        v                          |     | |
  |  |  |  +---------------------------------------------+  |     | |
  |  |  |  | GraphBuilder -> IdentityResolver             |  |     | |
  |  |  |  |   -> nodes + edges (responsibility_graph)     |  |     | |
  |  |  |  |   -> UnionFind identity resolution           |  |     | |
  |  |  |  |      root_pid | tls_session | conversation_id |  |     | |
  |  |  |  |   -> TaintTracker cross-process propagation  |  |     | |
  |  |  |  +---------------------+-----------------------+  |     | |
  |  |  |                        v                          |     | |
  |  |  |  +---------------------------------------------+  |     | |
  |  |  |  | Observer LLM (conflict signals only)         |  |     | |
  |  |  |  |   -> CachePrior (L1 exact / L2 semantic)     |  |     | |
  |  |  |  +---------------------------------------------+  |     | |
  |  |  |                                                   |     | |
  |  |  |  Phase 2 (SIGINT, batch finalization)             |     | |
  |  |  |  +---------------------------------------------+  |     | |
  |  |  |  | TracebackBuilder -> ClaimBuilder             |  |     | |
  |  |  |  |   -> AlertBuilder (structured security alert) |  |     | |
  |  |  |  +---------------------------------------------+  |     | |
  |  |  +---------------------------------------------------+     | |
  |  +-----------------------------+-----------------------------+ |
  |  Dual-write: SQLite (responsibility.db) + JSONL (streaming)   |
  +---------------------------------+-------------------------------+
                                    |
  ==================================+================================
                                    |
  +---------------------------------+-------------------------------+
  |                 FRONTEND + HTTP API (:7395)                     |
  |  Next.js 16 · React 19 · TypeScript 5 · Tailwind CSS 4          |
  |  rust-embed: compiled into mtagentlens binary at build time    |
  |  /           Timeline (scrollable, 10+ event type renderers)    |
  |  /tree       Process tree (hierarchical)                        |
  |  /alerts     Alert panel (causal chain + evidence)              |
  |  /api/events SSE real-time push                                 |
  |  /api/graph  Responsibility graph REST API                     |
  +-----------------------------------------------------------------+
```

---

## 第一层：eBPF 内核探针

eBPF 程序是独立编译的 C 程序，通过 libbpf 加载到内核 hook 点。每个程序以 JSON 行形式向 stdout 输出事件。Rust Runner 通过管道捕获这些输出。

### 探针矩阵

| 程序 | Hook 类型 | 捕获内容 | 关键场景 |
|------|---------|---------|---------|
| `tls_capture` | `SSL_read`/`SSL_write` uprobe | SSL/TLS 解密后明文 HTTP 流量 | Agent-API 通信、间接注入检测 |
| `process` | tracepoint + kprobe | `exec`/`exit`/`fork`/`open`/`bind`/`connect` | 进程血缘、PBC 检测、文件操作 |
| `stdiocap` | `read`/`write` uprobe | 本地 MCP 进程的 stdin/stdout/stderr | MCP 协议明文捕获 |
| `fileaccess` | 文件操作 kprobe | 文件读/写/删除/重命名 | 工作区边界违规检测 |
| `browsertrace` | 浏览器 SSL uprobe | 浏览器中的 LLM 对话明文 | Web 版 Agent 监控 |
| `system` | `/proc` 轮询 | CPU 使用率、RSS 内存 | 性能监控、资源滥用检测 |

### `--binary-path` 与静态链接 SSL

部分 LLM 工具静态链接 SSL 库（Claude Code 的 Bun 运行时使用 BoringSSL，NVM Node.js 使用静态 OpenSSL），不使用系统 `libssl.so`。指定 `--binary-path` 时，`tls_capture` 通过字节模式匹配自动定位 SSL 函数，跳过 `--comm` 过滤。

### 关键设计决策

**时间戳**：eBPF 使用 `bpf_ktime_get_ns()`（启动时间纳秒）。`TimestampNormalizer` analyzer 通过 `/proc/stat` 的 `btime` 字段计算 UTC epoch 毫秒。管道中所有后续 analyzer 使用统一时间基准。

**二进制内嵌**：
```rust
// collector/src/framework/binary_extractor.rs
const PROCESS_BINARY: &[u8] = include_bytes!("../../../bpftools/.output/bin/process");
const SSLSNIFF_BINARY: &[u8] = include_bytes!("../../../bpftools/.output/bin/tls_capture");
```

eBPF 程序在 Rust 编译时通过 `include_bytes!` 嵌入二进制。运行时提取到临时目录，注册 `SIGINT`/`SIGTERM` 处理器确保退出时清理。构建顺序固化为：**BPF → 前端 → Rust**。

---

## 第二层：Rust 流式引擎

### Runner 层

Runner 执行 eBPF 程序并产生异步事件流。每个 Runner 返回 `Pin<Box<dyn Stream<Item = Event> + Send>>`——一个可组合的异步流。

| Runner | 数据源 | 产生的事件类型 |
|--------|--------|-------------|
| `SslRunner` | `tls_capture` eBPF 程序 | SSL 解密事件（HTTP 请求/响应） |
| `ProcessRunner` | `process` eBPF 程序 | 进程 exec/exit、文件操作、网络连接 |
| `StdioRunner` | `stdiocap` eBPF 程序 | stdin/stdout/stderr 内容 |
| `SystemRunner` | `/proc` 文件系统 | CPU 使用率、内存 RSS |
| `AgentRunner` | 组合多个 Runner | 统一事件流 + 全局 analyzer 管理 |
| `FakeRunner` | 合成数据 | 测试和性能基准 |

### Analyzer 管道

Analyzer 采用**管道-过滤器模式**。每个 analyzer 接收一个 `EventStream`，处理后返回新的 `EventStream`。通过 `add_analyzer()` 链式组装。

#### 核心 Analyzer

| Analyzer | 功能 | 管道位置 |
|----------|------|---------|
| `SSEProcessor` | 重组分块传输的 SSE 流（`Content-Type: text/event-stream`） | 早期 |
| `HTTPParser` | 从 SSL 载荷解析 HTTP 请求行/头/体 | 早期 |
| `TimestampNormalizer` | btime → epoch ms 转换 | 早期 |
| `AuthHeaderRemover` | 剥离 `Authorization` 头 | 中期 |
| `HTTPFilter` | 基于正则的 HTTP 事件过滤 | 中期 |
| `SSLFilter` | 基于正则的 SSL 事件过滤 | 中期 |
| `HandoffInjectionAnalyzer` | 检测交接文档注入攻击 | 后期 |
| `MultiAgentAnomalyAnalyzer` | 检测多 Agent 异常模式 | 后期 |
| `FileAccessAuditAnalyzer` | 工作区边界的文件访问审计 | 后期 |
| `ConnectionBuffer` | 连接级别的缓冲管理 | 全管道 |
| `FileLogger` | 文件日志（支持轮转） | 终端 |
| `OutputAnalyzer` | 控制台输出 | 终端 |

#### 未激活的 Analyzer

以下 4 个 analyzer 实现完整但因特定问题未激活：

| Analyzer | 状态 | 阻塞问题 |
|----------|------|---------|
| `EventDedup` | 可直接激活 | 无技术阻塞 |
| `SharedStateStore` | 可直接激活 | 无技术阻塞 |
| `ContextPollutionAnalyzer` | 编译错误 ×6 | `get_or_create_scenario()` 双重可变借用 |
| `CredentialLeakAnalyzer` | 编译错误 ×1 | `CredentialLeakRecord`/`CredentialType` 未定义 |

### 事件格式

```rust
pub struct Event {
    pub id: u64,
    pub timestamp: u64,   // bpf_ktime_get_ns() 原始值
    pub source: String,   // "ssl" | "process" | "stdio" | "system"
    pub event_type: String,
    pub data: serde_json::Value,
}
```

`Event::datetime()` 通过 `/proc/stat` btime 将 `timestamp` 转换为 UTC 时间。

---

## 第三层：因果分析引擎（ResponsibilityRuntime）

ResponsibilityRuntime 是 MtAgentLens 的核心——它将原始事件流转化为结构化的责任归属。以全局 analyzer 身份注册到管道中。

### 两阶段架构

**阶段 1（流式）**——每个事件到达时立即处理：
1. **PhaseClassificationEngine**：2,595 条规则 + 6 维组合评分，检测进程边界跨越（PBC）
2. **GraphBuilder**：事件 → 责任图节点和边
3. **IdentityResolver**：并查集（UnionFind），三信号融合
4. **TaintTracker**：跨进程污点传播
5. **Observer LLM**：仅在分类信号冲突时触发（CachePrior 缓存）

**阶段 2（SIGINT 批量终结）**——Agent 会话结束时执行：
1. **TracebackBuilder**：从告警反向构建因果链
2. **ClaimBuilder**：从图状态生成结构化的责任声明
3. **AlertBuilder**：生成结构化安全告警

### 子系统详解

#### ResponsibilityGraph

核心图数据结构，支持：
- **节点类型**：Process、File、NetworkEndpoint、Intent、Action
- **边类型**：SpawnedBy、ReadFile、WroteTo、ConnectedTo、CausedBy
- **污点传播**：TaintTracker 追踪攻击载荷跨进程边界的传播路径
- **模式匹配**：预定义的攻击模式模板（context_pollution、leader_betrayal 等）
- **图简化**：去除冗余边和传递闭包

#### IdentityResolver（身份解析）

三个独立信号融合为统一 Agent 身份：

```
root_pid ──────────┐
tls_session ───────┼──→ UnionFind IdentityResolver ──→ canonical_agent_id
conversation_id ───┘
```

每个新事件根据已有信号渐进式合并身份。即使某个信号缺失（进程退出、TLS 关闭），其他信号仍可维持身份连续性。

#### Observer LLM

**仅在分类引擎产生冲突信号时介入**——例如 eBPF 看到进程读取了 `/etc/passwd`，但 HTTP 请求体中未出现敏感字段。

**CachePrior 自学习**：
- **L1 缓存**（精确匹配）：相同的事件指纹 → 直接复用历史仲裁结果
- **L2 缓存**（语义相似）：相近的事件模式 → 参考历史结果降低 LLM 调用

FuxiBench 86 场景中仅触发 34 次（39.5% 调用率），方向准确率 84.2%。

#### 双写持久化

- **SQLite**（`responsibility.db`）：结构化查询、回放审计、图遍历
- **JSONL**（流式推送）：SSE → 前端实时更新

---

## 第四层：HTTP 服务器与前端

### HTTP Server

基于 Hyper 1.0 的异步 HTTP 服务器，端口 7395。

**核心端点**：

| 端点 | 方法 | 功能 |
|------|------|------|
| `/` | GET | 内嵌前端 SPA |
| `/api/events` | SSE | 实时事件推送 |
| `/api/graph` | GET | 责任图查询 |
| `/api/alerts` | GET | 安全告警列表 |
| `/api/evidence` | GET | 证据关联 |
| `/api/diff` | GET | 回放差异对比 |
| `/api/runs` | GET | 运行历史 |
| `/api/auth` | POST | TOTP 2FA 认证 |

### 前端

Next.js 16 + React 19 + TypeScript 5 + Tailwind CSS 4。

通过 `rust-embed` 在 Rust 编译时内嵌静态资源（`frontend/out/`），编译为 `mtagentlens` 二进制的一部分——部署时无需独立的 Node.js 服务。

**核心组件**：
- `TimelinePanel`：可滚动时间线，每种事件类型有专门的行渲染器
- `ProcessTreeView`：层级进程可视化
- `DetailPanel`：异常详情、handoff 注入检测、因果链
- `DashboardShell`：布局外壳

---

## CLI 命令体系

| 命令 | 输入源 | 说明 |
|------|--------|------|
| `record` | SSL + Process + System | 一键监控，自动启动 Web 界面 |
| `trace` | 多源可选组合 | `--ssl` `--process` `--stdio` `--system` 独立开关 |
| `ssl` | `tls_capture` uprobe | 原始 SSL 解密明文流 + HTTP 解析 |
| `process` | `process` tracepoint/kprobe | 进程 exec/exit、文件、网络事件 |
| `stdio` | `stdiocap` uprobe | MCP/CLI 进程的 stdio 捕获 |
| `system` | `/proc` 轮询 | CPU 使用率和 RSS 内存 |
| `replay` | SQLite DB | 从责任数据库回放事件流 + 差异对比 |

---

## 关键数据流

### Agent 会话的完整生命周期

```
1. 启动: mtagentlens record -c claude --binary-path <path>
2. eBPF 探针挂载到内核 hook 点
3. Agent 进程产生系统调用 → eBPF 捕获 → JSON 事件 → stdout
4. Runner 读取 stdout → EventStream
5. Analyzer 管道处理:
   SSE 重组 → HTTP 解析 → 时间戳标准化 → 认证头剥离
   → HandoffInjection 检测 → MultiAgentAnomaly 检测
   → ResponsibilityRuntime（分类 + 图构建 + 身份解析 + 污点追踪）
6. 双写: SQLite + JSONL → SSE → 前端实时更新
7. SIGINT → 阶段 2 批量终结 → Traceback + Claims + Alerts
8. 清理临时文件，输出指标
```

### 信号处理与关闭

`SIGINT` → `SHUTDOWN_REQUESTED: AtomicBool` → `RUNTIME_SHUTDOWN` 通知 → ResponsibilityRuntime 执行阶段 2 → 指标输出 → 临时文件清理。

---

## 项目结构映射

```
collector/src/
├── main.rs                    CLI 入口（clap derive）
├── framework/                 流式引擎
│   ├── runners/               7 个 Runner
│   ├── analyzers/             29 个 Analyzer
│   └── binary_extractor.rs    编译期 BPF 内嵌
├── responsibility/            因果引擎（17 个子模块）
│   ├── graph/                 ResponsibilityGraph
│   ├── identity/              IdentityResolver (UnionFind)
│   ├── claims/                ClaimBuilder · TracebackBuilder · AlertBuilder
│   ├── classification/        PhaseClassificationEngine
│   ├── observer/              Observer LLM (CachePrior)
│   ├── persistence/           SQLite + JSONL 双写
│   ├── replay/                回放 + 差异引擎
│   ├── judgment/              策略评估 · 委派追踪
│   ├── runtime/               事件循环编排
│   ├── server/                REST API 路由
│   ├── signals/               SignalGraph
│   └── types/                 共享类型（40+ 结构体）
├── server/                    Hyper 1.0 HTTP 服务器
│   ├── web.rs                 路由处理
│   ├── assets.rs              rust-embed 前端内嵌
│   └── api_types.rs           请求/响应类型
└── auth/                      TOTP 2FA 认证
```
