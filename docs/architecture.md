# MtAgentLens 系统架构

## 概述

MtAgentLens 是一个基于 eBPF 的零插桩 LLM Agent 可观测性系统。它在 Linux 内核系统调用边界拦截 SSL/TLS 明文、进程生命周期、文件操作和网络连接，通过 Rust 流式引擎进行事件处理与因果分析，最终在 Next.js 前端呈现。

**证据原则**：采集到的系统调用事实、推导的关联和语义裁决分层保留。缺失观测不能证明未发生；管理员能够停用探针，采集损失必须进入质量报告。TLS 通过用户态函数 uprobe 观测，不等于系统调用截获。

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
  |  process (file mutations: unlinkat / truncate)                   |
  |  +---------------+                                                |
  |  | file R/W ops  |                                                |
  |  | workspace bnd |                                                |
  |  +---------------+                                                |
  |                                                                 |
  |  userspace: system  — CPU / memory sampled from /proc            |
  |                                                                 |
  +---------------------------------+-------------------------------+
                                    | JSON event stream (stdout)
  ==================================+================================
                                    |
  +---------------------------------+-------------------------------+
  |                    RUST STREAMING ENGINE                        |
  |  +-----------------------------------------------------------+ |
  |  | Runner Layer                                   | |
  |  | SslRunner  ProcessRunner  StdioRunner                     | |
  |  | SystemRunner  AgentRunner  FakeRunner (test)              | |
  |  +-----------------------------+-----------------------------+ |
  |                                | EventStream                   |
  |  +-----------------------------+-----------------------------+ |
  |  | Analyzer Pipeline                           | |
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
  |  |  |  | ClassificationEngine                   |  |     | |
  |  |  |  |   -> rules + evidence, 6-dim triage score  |  |     | |
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
  |  |  |  |   -> exact evidence-version verdict cache     |  |     | |
  |  |  |  +---------------------------------------------+  |     | |
  |  |  |                                                   |     | |
  |  |  |  Phase 2 (SIGINT, batch finalization)             |     | |
  |  |  |  +---------------------------------------------+  |     | |
  |  |  |  | TracebackBuilder -> ClaimBuilder             |  |     | |
  |  |  |  |   -> AlertBuilder (structured security alert) |  |     | |
  |  |  |  +---------------------------------------------+  |     | |
  |  |  +---------------------------------------------------+     | |
  |  +-----------------------------+-----------------------------+ |
  |  SQLite single-writer (responsibility.db); raw events via FileLogger |
  +---------------------------------+-------------------------------+
                                    |
  ==================================+================================
                                    |
  +---------------------------------+-------------------------------+
  |                 FRONTEND + HTTP API (:7395)                     |
  |  Next.js 16 · React 19 · TypeScript 5 · Tailwind CSS 4          |
  |  rust-embed: compiled into mtagentlens binary at build time    |
  |  /           landing page                                       |
  |  /console    live posture, 3D responsibility stage              |
  |  /overview   global posture and probe coverage                  |
  |  /traces     trace workbench and replay timeline                |
  |  /investigations  claim, evidence and traceback workbench       |
  |  /research/fuxibench  dataset documentation                     |
  |  /api/events legacy log-backed event stream                     |
  |  /api/runs/{id}/{alerts,evidence,events,snapshots,db}            |
  +-----------------------------------------------------------------+
```

---

## 第一层：eBPF 内核探针

eBPF 程序是独立编译的 C 程序，通过 libbpf 加载到内核 hook 点。每个程序以 JSON 行形式向 stdout 输出事件。Rust Runner 通过管道捕获这些输出。

### 探针矩阵

| 程序 | Hook 类型 | 捕获内容 | 关键场景 |
|------|---------|---------|---------|
| `tls_capture` | `SSL_read`/`SSL_write` uprobe | SSL/TLS 解密后明文 HTTP 流量 | Agent-API 通信、间接注入检测 |
| `process` | raw syscall enter/exit + lifecycle tracepoint | `exec`/`exit`/`open`/文件变更/`connect`/`sendto` 及返回值 | 进程血缘、边界检测、文件操作 |
| `stdiocap` | `read`/`write` uprobe | 本地 MCP 进程的 stdin/stdout/stderr | MCP 协议明文捕获 |
| `system` | `/proc` 轮询 | CPU 使用率、RSS 内存 | 性能监控、资源滥用检测 |

### `--binary-path` 与静态链接 SSL

部分 LLM 工具静态链接 SSL 库（Claude Code 的 Bun 运行时使用 BoringSSL，NVM Node.js 使用静态 OpenSSL），不使用系统 `libssl.so`。指定 `--binary-path` 时，`tls_capture` 通过字节模式匹配自动定位 SSL 函数，跳过 `--comm` 过滤。

### 关键设计决策

**时间戳**：eBPF 使用 `bpf_ktime_get_ns()`（启动时间纳秒）。`TimestampNormalizer` 通过 `/proc/stat` 的 `btime` 计算 UTC epoch 纳秒；展示层用 `epoch_ns_to_epoch_ms` 转为毫秒。管道中所有后续 analyzer 使用统一纳秒基准。

**二进制内嵌**：
```rust
// collector/src/framework/binary_extractor.rs
const PROCESS_BINARY: &[u8] = include_bytes!("../../../bpftools/.output/bin/process");
const TLS_CAPTURE_BINARY: &[u8] = include_bytes!("../../../bpftools/.output/bin/tls_capture");
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
| `TimestampNormalizer` | btime → epoch ns 转换 | 早期 |
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

以下历史实现已删除；不属于可调用能力：

| Analyzer | 状态 | 阻塞问题 |
|----------|------|---------|
| `EventDedup` | 已删除 | 能力被意图级内容去重覆盖；原文件从未参与编译 |
| `SharedStateStore` / `pipeline_contract.rs` | **已删除（2026-10-22）** | 该契约无生产者/消费者，只被自身测试引用；由 `producer_wiring_contract.rs` 取代 |
| `ContextPollutionAnalyzer` | 已删除 | 编译错误 ×6（`get_or_create_scenario()` 双重可变借用）；判定门写死 Benchmark 场景字面量，能力已由 `graph/taint.rs` + `graph_builder` 通用污点传播覆盖（门控语义见 ADR-0015：未授权共享 + Observed 置信度才产生 `TaintedContextUse`） |
| `CredentialLeakAnalyzer` | 已删除 | 440 行是 `handoff_injection.rs` 的逐行克隆；闸门只认一个 fixture 路径 |

### 事件格式

```rust
pub struct Event {
    pub timestamp: u64,   // normalized epoch ns after TimestampNormalizer
    pub source: String,   // "ssl" | "process" | "stdio" | "system" | "openclaw"
    pub pid: u32,
    pub comm: String,
    pub data: serde_json::Value,
}
```

`Event::datetime()` 直接把 `timestamp` 当作 epoch 纳秒转换为 UTC。

---

## 第三层：因果分析引擎（ResponsibilityRuntime）

ResponsibilityRuntime 是 MtAgentLens 的核心——它将原始事件流转化为结构化的责任归属。以全局 analyzer 身份注册到管道中。

### 两阶段架构

**阶段 1（流式）**——每个事件到达时立即处理：
1. **ClassificationEngine**：19 条规则（5 safe + 14 unsafe）+ 6 维分诊评分；分数只负责把候选路由给 Observer
2. **GraphBuilder**：事件 → 责任图节点和边
3. **IdentityResolver**：并查集（UnionFind），进程树 + TLS 会话两信号
4. **TaintTracker**：跨进程污点传播
5. **Observer LLM**：仅有边界/模式/硬规则候选时触发；精确签名去重并复用裁决

**阶段 2（SIGINT 批量终结）**——Agent 会话结束时执行：
1. **TracebackBuilder**：从告警反向构建因果链
2. **ClaimBuilder**：从图状态生成结构化的责任声明
3. **AlertBuilder**：生成结构化安全告警

### 子系统详解

#### ResponsibilityGraph

核心图数据结构，支持：
- **生产节点**：Agent、Resource、PolicyBoundary；文件、网络、进程等以 Resource 中的类型区分
- **生产边**：IntentCausesAction、AgentDelegatesTo、ContextFlowsFrom、TaintPropagatesTo、PolicyBoundaryCrossing；动作语义由 operation 标识
- **污点传播**：TaintTracker 追踪攻击载荷跨进程边界的传播路径；只有具体文件可作为污点源，`TaintPropagatesTo` 仅在跨 agent、跨进程、写后读时产生，`TaintedContextUse` 还需未授权共享与 Observed 置信度（ADR-0015）
- **模式匹配**：预定义的攻击模式模板（context_pollution、leader_betrayal 等）
- **模式匹配范围**：按连通分量计算，禁止将互不相关的事件拼成攻击链

#### IdentityResolver（身份解析）

三个独立信号融合为统一 Agent 身份：

```
root_pid ──────────┐
tls_session ───────┼──→ UnionFind IdentityResolver ──→ canonical_agent_id
conversation_id ───┘
```

每个新事件根据已有信号渐进式合并身份。即使某个信号缺失（进程退出、TLS 关闭），其他信号仍可维持身份连续性。

#### Observer LLM

**仅在存在候选时介入**——例如 eBPF 观测到进程读取私钥或连接外部端点，而静态规则无法判断其是否与任务一致。Observer 的输入是结构化 OS 证据（路径/端点/规则/委派），观测文本被标记为不可信数据。

**裁决复用边界**：只复用绑定同一候选 ID、事实版本和证据集的裁决。没有真实候选或引证不属于候选范围时，Unsafe 不获得权威性；超出审阅上限的候选仍未审阅。外部请求有总超时和响应体大小限制。

观察者输出是语义意见，不能替代 syscall 成功证明或独立执行审计；其效果需要新实验验证。

#### 带外数据源（Out-of-band，当前仅 OpenClaw）

`OpenClawRunner` 以只读方式轮询 agent 自己落盘的
`trajectory_runtime_events.tool.call`，把结构化工具调用作为
`OutOfBandAsserted` intent 注入同一相关器；eBPF 仍独立可用，带外缺失时降级。
身份边界由 `MTAGENTLENS_AGENT_COMMS/_EXE` 声明；显式 exe 边界为严格模式
（TLS 不再单独判定 Agent）。`MTAGENTLENS_AGENT_CGROUP` 当前不支持：cgroup unit
名称没有经内核核验的来源，设置后启动告警并忽略，避免启用永远无法匹配的边界。

#### 持久化

- **SQLite**（`responsibility.db`）：唯一结构化真源；批次写入、schema 版本门、
  丢弃/写失败计数，run 结束输出 `[diagnostics:json]`。
- 原始事件流由 `FileLogger` 写入行式 JSONL 日志；前端实时更新走
  `/api/live/events` 的 SSE 通道，不经过持久化层。

---

## 第四层：HTTP 服务器与前端

### HTTP Server

基于 Hyper 1.0 的异步 HTTP 服务器，端口 7395。

**核心端点**：

| 端点 | 方法 | 功能 |
|------|------|------|
| `/` | GET | 内嵌前端 SPA |
| `/api/events` | GET | legacy log-backed event stream |
| `/api/runs` | GET | 运行历史（最新优先） |
| `/api/runs/active` | GET | 当前活动 run：本进程 live 产物，或 `state=running` 的归档；否则 `{data:null}` |
| `/api/runs/live` | GET | 本进程 `record`/`trace` 的实时 run 摘要（保留 id `live`） |
| `/api/runs/{id}` | GET | 单个 run 摘要（含 `state`/`started_at_ns`/`artifact_availability`） |
| `/api/runs/{id}/alerts` | GET | 安全告警列表（按 DB `runs` 表解析；多 run 歧义返回 409） |
| `/api/runs/{id}/evidence` | GET | 意图/动作/证据关联（`evidence/intents/actions/non_links`） |
| `/api/runs/{id}/graph` | GET | 最新责任图快照（`graph_snapshots`，无快照则回退节点/边表） |
| `/api/runs/{id}/events` | GET | run-scoped 原始事件 |
| `/api/runs/{id}/snapshots` | GET | 图演化快照（读 `graph_snapshots`） |
| `/api/runs/{id}/db` | GET | 指定 run 的一致 SQLite 快照（WAL backup、跨 run 清理、外键检查、VACUUM） |

### 前端

Next.js 16 + React 19 + TypeScript 5 + Tailwind CSS 4。

通过 `rust-embed` 在 Rust 编译时内嵌静态资源（`frontend/out/`），编译为 `mtagentlens` 二进制的一部分——部署时无需独立的 Node.js 服务。

**核心页面**（实际路由）：
- `/`：Landing 初始进入页
- `/console`：实时态势、3D responsibility stage、告警入口
- `/overview`：全局态势、探针覆盖和责任集群摘要
- `/traces`：trace workbench 和回放时间线
- `/investigations`：claim、evidence 和 traceback 调查工作台
- `/research/fuxibench`：数据集说明页
- 已删除：`/agents`、`/clusters`、`/handoff`、`/nebula`（无重定向）

---

## CLI 命令体系

| 命令 | 输入源 | 说明 |
|------|--------|------|
| `record` | SSL + Process + System | 一键监控，自动启动 Web 界面 |
| `trace` | 多源可选组合 | `--ssl` `--process` `--stdio` `--system` 独立开关 |
| `ssl` | `tls_capture` uprobe | 原始 SSL 解密明文流 + HTTP 解析 |
| `process` | `process` raw syscall/lifecycle tracepoint | 进程 exec/exit、文件、网络事件 |
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
6. SQLite 单写 + SSE（原始事件另经 FileLogger 落 JSONL 行日志）
7. SIGINT → 阶段 2 批量终结 → Traceback + Claims + Alerts
8. 清理临时文件，输出指标
```

### 动作完成态与回放

- 动作保留 `outcome`、`syscall_ret`、`syscall_name`。failed、pending、unknown 不生成完成事实边；成功 `FileOpen` 也不能证明内容已读写。
- schema v4 的实体主键、外键及身份索引按 run 隔离。多 run 请求必须明确选择；不存在或歧义返回错误。
- live 终结保存版本化 `analysis_checkpoints`，包括分析输入、配置、工作区和实现指纹；回放调用相同纯裁决内核。这是冻结证据重新分析，不是重新执行网络、模型或内核事件。
- 缺 checkpoint 或实现不匹配时拒绝声称同构重算；旧 DB 仍可查看已记录结果。没有 GT 的回放不产生 precision/recall/F1。
- 独立 `replay --sqlite <db> --validate-recording` 只读检查全部 run 的当前 schema／checkpoint、终结标记、最终裁决、claims／tracebacks、归因与指标及 evidence／alert 回放一致性；失败退出非零。它不改写旧录制，也不证明独立参考、检测效果或最终 flush 成功。
- `trace --capture-only` 在创建责任 writer／runtime 前返回采集模式，跳过四个安全全局分析器，仍保留原始 FileLogger。普通模式的 runtime 仍最后挂载；`--disable-responsibility-observer` 控制 Observer，ONNX 分类器已从产品移除。四条件开销驱动和真实并发测量另行验收。

### 信号处理与关闭

`SIGINT` → `SHUTDOWN_REQUESTED: AtomicBool` → `RUNTIME_SHUTDOWN` 通知 → ResponsibilityRuntime 执行阶段 2 → 指标输出 → 临时文件清理。

---

## 项目结构映射

```
collector/src/
├── main.rs                    CLI 入口（clap derive）
├── framework/                 流式引擎
│   ├── runners/               组合 Runner
│   ├── analyzers/             流式 Analyzer
│   └── binary_extractor.rs    编译期 BPF 内嵌
├── responsibility/            因果引擎（17 个子模块）
│   ├── graph/                 ResponsibilityGraph
│   ├── identity/              IdentityResolver (UnionFind)
│   ├── claims/                ClaimBuilder · TracebackBuilder · AlertBuilder
│   ├── classification/        ClassificationEngine
│   ├── observer/              Observer LLM (CachePrior)
│   ├── persistence/           SQLite（单一 schema + 版本门）
│   ├── replay/                回放 + 差异引擎
│   ├── judgment/              策略评估 · 委派追踪
│   ├── runtime/               事件循环编排
│   ├── signals/               SignalGraph
│   └── types/                 共享类型（40+ 结构体）
├── server/                    Hyper 1.0 HTTP 服务器
│   ├── web.rs                 路由处理
│   ├── assets.rs              rust-embed 前端内嵌
│   └── api_types.rs           请求/响应类型
```
