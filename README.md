```text
 /$$      /$$   /$$      /$$$$$$                                  /$$     /$$
| $$$    /$$$  | $$     /$$__  $$                                | $$    | $$
| $$$$  /$$$$ /$$$$$$  | $$  \ $$  /$$$$$$   /$$$$$$  /$$$$$$$  /$$$$$$  | $$        /$$$$$$  /$$$$$$$   /$$$$$$$
| $$ $$/$$ $$|_  $$_/  | $$$$$$$$ /$$__  $$ /$$__  $$| $$__  $$|_  $$_/  | $$       /$$__  $$| $$__  $$ /$$_____/
| $$  $$$| $$  | $$    | $$__  $$| $$  \ $$| $$$$$$$$| $$  \ $$  | $$    | $$      | $$$$$$$$| $$  \ $$|  $$$$$$
| $$\  $ | $$  | $$ /$$| $$  | $$| $$  | $$| $$_____/| $$  | $$  | $$ /$$| $$      | $$_____/| $$  | $$ \____  $$
| $$ \/  | $$  |  $$$$/| $$  | $$|  $$$$$$$|  $$$$$$$| $$  | $$  |  $$$$/| $$$$$$$$|  $$$$$$$| $$  | $$ /$$$$$$$/
|__/     |__/   \___/  |__/  |__/ \____  $$ \_______/|__/  |__/   \___/  |________/ \_______/|__/  |__/|_______/
                                  /$$  \ $$
                                 |  $$$$$$/
                                  \______/
```

**<img src="frontend/public/brand/mtl-logo.svg" alt="" width="51" height="51" style="vertical-align:middle" />&nbsp;零插桩 · 内核级 · 多 Agent 攻击可观测性**

从系统调用到责任归因 —— 在 OS 边界采集可验证的运行事实

<a href="LICENSE-APACHE"><img src="https://img.shields.io/badge/license-Apache_2.0-007ec6?logo=apache&logoColor=white" alt="Apache 2.0"></a>
<a href="#架构"><img src="https://img.shields.io/badge/eBPF-0a0b10?logo=linux&logoColor=white" alt="eBPF"></a>
<a href="#架构"><img src="https://img.shields.io/badge/Rust-CE422B?logo=rust&logoColor=white" alt="Rust"></a>
<a href="#架构"><img src="https://img.shields.io/badge/Next.js-000000?logo=nextdotjs&logoColor=white" alt="Next.js"></a>

[冻结实验结果](docs/evaluation/CURRENT-EVIDENCE.md) · [开销测量范围与结果](compare/results/overhead/README.md) · [FuxiBench 数据卡](datasets/FuxiBench/DATASET-CARD.md)

---

## 背景

大语言模型正在将软件系统的人机交互方式从对话层推进至执行层。以 Claude Code、Gemini CLI 为代表的端侧 Agent 不再局限于生成文本，而是能够读取仓库、调用工具、执行命令、修改文件，并通过多 Agent 协作完成编程、运维、测试与发布等复杂任务。当此类系统进入真实运行环境后，Agent 已不再是被动的“问答界面”，而是会消耗系统资源、访问敏感资产并影响工程状态的执行主体。

能力边界的变化直接带来安全边界的变化。模型幻觉可能诱发逻辑死循环、重复构建或无效搜索，造成计算资源浪费；提示词注入、供应链污染和恶意上下文则可能诱导 Agent 执行越权访问、凭证读取、数据外传或破坏性操作。更重要的是，此类风险已不再局限于用户输入，还可能隐藏在 README、构建脚本、配置文件、交接文档、工具返回值以及子 Agent 的中间结果中，并沿执行链路逐级放大。

公开披露的安全事件表明，上述风险并非停留在理论层面。AWS 安全公告确认，Amazon Q Developer for Visual Studio Code Extension **1.84.0** 曾分发包含恶意代码的版本，随后被移除并发布修复版本；GitHub Advisory 将其记录为 **CVE-2025-8217**。2025 年 7 月，SaaStr 创始人 Jason Lemkin 公开披露 Replit AI Agent 在 code freeze 期间仍执行破坏性数据库操作，AI Incident Database 将其记录为 Incident 1152。这两类事件共同表明，AI 工具链的风险已进入实际执行层，对事件的调查不能止步于对话文本，而必须溯源至实际命令、文件与数据库操作、网络访问、时间线以及责任主体。参见 [AWS-2025-015](https://aws.amazon.com/security/security-bulletins/AWS-2025-015/)、[GHSA-7g7f-ff96-5gcw](https://github.com/aws/aws-toolkit-vscode/security/advisories/GHSA-7g7f-ff96-5gcw) 与 [AI Incident Database 1152](https://incidentdatabase.ai/cite/1152/)。

多 Agent 协作进一步加剧了问题的复杂性。一次任务可能由父 Agent 发起，再分派至多个 worker、工具进程或子 Agent；部分进程生命周期极短，仅在临时目录写入证据文件后即退出；部分上下文则通过 handoff 文件、summary、角色声明或共享工作区继续传播。在此阶段，单条日志、单个 PID 或单段 prompt 已不足以解释异常，系统真正需要回答的核心问题是：任务由谁发起，哪些进程与文件参与执行，风险沿何种路径传播，证据留存于何处，最终责任应归属于哪个 Agent 或任务链。

现有监测手段均只能覆盖问题的一个侧面。应用层调用链、日志分析与高层指标监控易于接入，但依赖 Agent 框架主动生成日志，容易遗漏短命进程、真实文件操作与底层网络行为，亦无法应对日志缺失或被绕过的情形。传统系统调用审计能够观测底层动作，却缺乏 Agent 身份、任务语义与委派关系，难以将“某个进程读取了文件”还原为“某个 Agent 在某次任务中触发了风险”。Prompt scanner 与内容分类器主要处理文本输入，对隐藏在文件、工具输出、构建脚本及跨进程传播中的间接异常缺乏有效的检测能力。

MtAgentLens 正是针对上述缺口而构建。项目的观测对象不是一段可疑文本，而是 Agent 执行任务后产生的真实系统行为：进程如何派生，文件如何被读写，网络连接由谁触发，上下文如何跨 Agent 传播，异常如何回溯至责任主体。通过 eBPF 采集 OS 层运行事实、Rust 流式引擎构建责任图、前端提供可审计的调查路径，MtAgentLens 将观测位置置于 Agent 之下，将归因对象置于任务链与责任链之上，使告警可回溯至证据，使异常可回溯至过程，使单点行为可回溯至完整责任链。

---

## 介绍

MtAgentLens 是一个面向多 Agent 运行系统的 **零插桩 OS 级可观测与责任归因工具**。它在 Linux 内核边界采集进程、文件、网络、stdio 和 TLS 明文片段等运行事实，不依赖 Agent 框架，不修改目标代码，也不要求接入 SDK。

它解决的不是单点日志聚合，而是多 Agent 时代最难处理的三类断层：

1. **应用层与内核层的断层**：文本日志无法反映真实的进程、文件与网络行为。
2. **行为与主体的断层**：系统调用能够观测动作，但难以还原具体由哪个 Agent、哪次任务触发。
3. **事件与责任的断层**：多个 worker、交接文件、共享上下文与短命进程交织后，单条记录不足以解释异常。

MtAgentLens 将这些事实统一到一张**跨进程因果责任图**中，并在此基础上提供结构化告警、证据回溯和任务级归因。系统的核心特点包括：

- **内核边界采集**：从 eBPF 直接获取可信运行事实。
- **多信号身份归并**：结合 root_pid、tls_session、conversation_id 等线索识别同一执行链。
- **跨进程污染追踪**：跟踪文件、上下文和工具输出在多个 Agent 之间的传播路径。
- **结构化责任输出**：将行为、证据和责任主体组织为可复核结果，而不是孤立日志。
- **可测量的运行质量**：保留采集损失和排空状态，性能结论由冻结版本后的实测给出。

与依赖 prompt 分类或应用日志的方案相比，MtAgentLens 的差异在于将观测位置置于 Agent 之下、将归因对象置于任务链与责任链之上。其产出不是“这段文本可疑”，而是“该行为由谁触发、经历了何种路径、证据留存何处、应归责于谁”。

## 功能演示

[![MtAgentLens 完整功能演示（约 1 分 48 秒）](docs/materials/demo/demo.png)](docs/materials/demo/demo.mp4)

*完整功能演示（约 1 分 48 秒）：eBPF 内核级零插桩采集 → 跨进程责任图构建 → 结构化告警与证据回溯的端到端流程*

---

## 架构

```
                          USER SPACE
  +-----------------------------------------------------------------+
  |  Verified: Claude Code                                          |
  |  Expected: any LLM Agent process (Python, Node.js, ...)         |
  |                                                                 |
  |  +----------+  +----------+  +----------+  +----------+         |
  |  | Agent A  |  | Agent B  |  | Agent C  |  |   ...    |         |
  |  +----+-----+  +----+-----+  +----+-----+  +----+-----+         |
  |       |              |              |              |            |
  |       |  App-level logs (mutable, bypassable) — untrusted       |
  +-------+--------------+--------------+--------------+------------+
          |              |              |              |
  ========+==============+==============+==============+=============
          |              |  SYSCALL BOUNDARY           |
  ========+==============+==============+==============+=============
          |              |              |              |
  +-------+--------------+--------------+--------------+------------+
  |                      eBPF PROBES                                |
  |                                                                 |
  |  tls_capture         process           stdiocap                 |
  |  +---------------+   +-------------+   +----------------+       |
  |  | SSL_read      |   | exec / exit |   | read / write   |       |
  |  | SSL_write     |   | fork / clone|   | stdin / stdout |       |
  |  | uprobe (L3)   |   | open / unlink|  | MCP plaintext  |       |
  |  | decrypt plain |   | bind / connect|  | syscall trace |       |
  |  +---------------+   +-------------+   +----------------+       |
  |                                                                 |
  |  process also emits file mutations (unlinkat / truncate)        |
  |                                                                 |
  |  userspace: system  — CPU / memory sampled from /proc           |
  |                                                                 |
  +---------------------------------+-------------------------------+
                                    | JSON event stream (stdout)
  ==================================+================================
                                    |
  +---------------------------------+-------------------------------+
  |                    RUST STREAMING ENGINE                        |
  |                                                                 |
  |  +-----------------------------------------------------------+  |
  |  | Runner Layer (7 runners)                                  |  |
  |  | SslRunner  ProcessRunner  StdioRunner                     |  |
  |  | SystemRunner  AgentRunner  FakeRunner (test)              |  |
  |  | -> Pin<Box<dyn Stream<Item = Event> + Send>>              |  |
  |  +-----------------------------+-----------------------------+  |
  |                                | EventStream                    |
  |  +-----------------------------+-----------------------------+  |
  |  | Analyzer Pipeline (add_analyzer() chain)                  |  |
  |  |                                                           |  |
  |  |  +-----------+  +-----------+  +------------------------+ |  |
  |  |  | SSE reass |  | HTTP parse|  | TimestampNormalizer    | |  |
  |  |  | chunk mg  |  | req / resp| | btime -> epoch ns       | |  |
  |  |  +-----+-----+  +-----+-----+  +-----------+------------+ |  |
  |  |        +------------------+--------------------+          |  |
  |  |                           v                               |  |
  |  |  +---------------------------------------------------+    |  |
  |  |  |            ResponsibilityRuntime                  |    |  |
  |  |  |                                                   |    |  |
  |  |  |  Phase 1 (streaming, per-event)                   |    |  |
  |  |  |  +---------------------------------------------+  |    |  |
  |  |  |  | ClassificationEngine                        |  |    |  |
  |  |  |  | > 19 rules (5 safe + 14 unsafe), 6-dim score|  |    |  |
  |  |  |  | -> Process Boundary Crossing (PBC) detect   |  |    |  |
  |  |  |  +---------------------+-----------------------+  |    |  |
  |  |  |                        v                          |    |  |
  |  |  |  +---------------------------------------------+  |    |  |
  |  |  |  | GraphBuilder -> IdentityResolver            |  |    |  |
  |  |  |  |   -> nodes + edges (responsibility_graph)   |  |    |  |
  |  |  |  |   -> UnionFind identity resolution          |  |    |  |
  |  |  |  |     root_pid | tls_session | conversation_id|  |    |  |
  |  |  |  |   -> TaintTracker cross-process propagation |  |    |  |
  |  |  |  +---------------------+-----------------------+  |    |  |
  |  |  |                        v                          |    |  |
  |  |  |  +---------------------------------------------+  |    |  |
  |  |  |  | Observer LLM (triggered on conflict signals)|  |    |  |
  |  |  |  |   -> ObserverCache (L1 exact-signature)     |  |    |  |
  |  |  |  |   -> measured on a frozen run only          |  |    |  |
  |  |  |  |   -> no historical accuracy number          |  |    |  |
  |  |  |  +---------------------------------------------+  |    |  |
  |  |  |                                                   |    |  |
  |  |  |  Phase 2 (on SIGINT, batch finalization)          |    |  |
  |  |  |  +---------------------------------------------+  |    |  |
  |  |  |  | TracebackBuilder -> ClaimBuilder             | |    |  |
  |  |  |  |   -> AlertBuilder (structured security alert)| |    |  |
  |  |  |  |   -> evidence correlation + causal chain     | |    |  |
  |  |  |  +---------------------------------------------+  |    |  |
  |  |  +---------------------------------------------------+    |  |
  |  +-----------------------------+-----------------------------+  |
  |                                |                                |
  |  SQLite single-writer (responsibility.db); raw events via FileLogger |
  +---------------------------------+-------------------------------+
                                   |
  =================================+================================
                                   |
  +-----------------------------------------------------------------+
  |  FRONTEND + HTTP API (:7395)                                    |
  |                                                                 |
  |  Next.js 16 · React 19 · TypeScript 5 · Tailwind CSS 4          |
  |  rust-embed: compiled into mtagentlens binary at build time     |
  |                                                                 |
  |  /                   Landing · brand intro + demo entry         |
  |  /console            Command Center · 3D responsibility stage   |
  |  /overview           Dashboard · posture, probes & clusters     |
  |  /investigations     Alerts · claims, evidence & traceback      |
  |  /traces             Tracing · workbench & replay timeline      |
  |  /research/fuxibench FuxiBench · dataset & evaluation           |
  |                                                                 |
  |  db deep-links: forensic chain · identity lineage · raw events  |
  |               · graph time evolution                            |
  |                                                                 |
  |  /api/live/events  live SSE: alerts + raw events + heartbeat    |
  |  /api/runs/{id}/{alerts,evidence,graph,events,snapshots,db}     |
  |  /api/runs/active  live run (`live`) or state=running, else null|
  |  /api/events  legacy log-backed stream (deprecated)             |
  +-----------------------------------------------------------------+
```

**实时模式**：`record`/`trace` 直播中，前端自动跟随活跃 run——SSE 推送告警（含图快照 → 态势图实时演化）与原始事件（环形缓冲 + 虚拟化 live-tail），运行监控派生视角与图时间演化时间轴周期刷新；run 结束自动转回放深度调查。后端由 `start_web_server_if_enabled` 把 `alert_tx` 接入 `/api/live/events`（`server/live_sse.rs`）。

---

## 检测层次

LLM Guard、Meta Prompt Guard 等工具主要扫描 prompt 文本中的恶意模式，覆盖范围通常局限于文本输入本身。对于多 Agent 运行场景，主要问题有三类：

| 盲区 | 攻击实例 | MtAgentLens 如何应对 |
|------|---------|---------------------|
| **文本绕过** — 文言文、Unicode 同形字、语义遮蔽可轻易绕过文本分类器 | `prompt-injection-classical`（纯文言文·墨子·棋局对弈）被所有 prompt 工具漏检 | **L1** 不读取 prompt 文本——直接在 uprobe 点拦截 SSL 明文，还原实际的 HTTP 请求体与响应 |
| **间接注入** — fixture 文件中的恶意指令不经过 prompt，文本扫描器无法感知 | `supplychain-malicious-build` 的 `build.sh` 中包含 `curl POST /exfil`，prompt 中只写"请运行构建脚本" | **L2** 追踪文件读取→进程执行→网络出站的完整链路，在进程边界跨越（PBC）时标记 |
| **多 Agent 协同** — 攻击指令通过共享文件、交接文档、委派令牌在进程间传递，没有单一文本可供扫描 | `leader-betrayal` 中 Leader 向 Worker 下达恶意指令，Worker 盲从执行 | **L2** 并查集身份解析 + TaintTracker 跨进程追踪污染的传播路径 |

**L2 内容来源**：从 LLM 请求的 `messages[]` 区分用户/系统文本与 tool result；tool result 标为 Retrieved/Untrusted，用户点名的文件标为 UserNamed；后续 tool call 复现 untrusted 内容时生成 `UntrustedInstructionFlow` 边界。权限包络对未授权的网络/写入/shell 生成 `CapabilityExpansion`。

**L3 Observer LLM** 仅在存在候选（边界/模式/硬规则）时介入，对结构化 OS 证据做语义仲裁；其 Unsafe 判定必须引用具体证据，否则降为 Uncertain。缓存只做精确签名去重并**原样复用**裁决，不再有 L2 语义先验。最终判定由硬证据、Observer 一致意见与未决候选聚合而成（`classification/final_decision.rs`），每个 run 写入 `runs.final_decision`。

---

## 快速开始

```bash
# 克隆（注意 --recursive，项目包含子模块）
git clone --recursive https://github.com/mtagentlens/mtagentlens.git
cd mtagentlens
```

### 1. 安装依赖

```bash
./install.sh        # 一键安装（支持 Ubuntu/Debian/Fedora/Arch/macOS）
```

脚本会自动检测系统并安装所需依赖：clang, llvm, libelf, zlib, Rust, Node.js 22。

### 2. 检查环境

```bash
make check          # 验证所有依赖就绪
```

### 3. 构建

```bash
make build          # 全量构建（BPF → 前端 → Rust）
```

### 4. 首次运行自检（推荐）

```bash
sudo ./collector/build/release/mtagentlens doctor
```

逐项检查内核版本（≥5.8）、eBPF 权限（root 或 `CAP_BPF`+`CAP_PERFMON`）、
BTF、内置探针、syscall tracepoint 与可见的 agent 进程，并给出可操作的修复命令。
`doctor` 在任一项 FAIL 时以非零码退出；`--json` 供脚本使用。

`record` / `trace` 及各原始子命令在启动前会自动执行同一组关键检查，
所以权限或内核不满足时，你看到的第一条输出是诊断和修复建议，而不是
libbpf/verifier 的错误堆栈。

### 5. 运行采集

```bash
sudo ./collector/build/release/mtagentlens record -c claude \
  --binary-path "$(readlink -f "$(command -v claude)")"
```

### 6. 访问前端

浏览器访问 `http://127.0.0.1:7395`

#### 非 Claude Agent 的身份边界

当 Agent 的进程名不是 `claude`/`claude-code`（例如 OpenClaw 的 Node Gateway 进程名是
`MainThread`），用以下环境变量扩展边界；它们同时作用于事件路由与 eBPF 探针的
`-c` 过滤：

```bash
MTAGENTLENS_AGENT_COMMS=openclaw,MainThread \
MTAGENTLENS_AGENT_EXE=openclaw \
  sudo -E ./collector/build/release/mtagentlens record --binary-path <agent ELF>
```

`MTAGENTLENS_AGENT_CGROUP` **当前不支持**：移除未核验 procfs PID 读取后，cgroup
unit 名称没有可核验来源；设置该变量会在启动时告警并被忽略，避免启用一个永远
无法匹配的严格边界。需要按容器/单元限定 Agent 时，用 `MTAGENTLENS_AGENT_EXE`
或 `MTAGENTLENS_AGENT_COMMS`。

被边界挡下的 action 会计入 run 结束时的 `[diagnostics:json]`：
`actions_dropped_no_agent`（边界未覆盖）与 `actions_dropped_bpf_noise`
（采集器自身的 `/sys/kernel/` 读取）。

#### OpenClaw 完整采集（eBPF + 带外轨迹）

OpenClaw 的 Node Gateway 以 `MainThread` 运行，同时把每次工具调用写进
`~/.openclaw/agents/<agent>/agent/openclaw-agent.sqlite`。两者可以同时使用：
eBPF 观察实际 syscall，带外读取提供模型选择的结构化工具参数（标记为
`OutOfBandAsserted`）。

```bash
GW=$(pgrep -f 'openclaw.*gateway' | head -1)   # long-running gateway
sudo -E env \
  MTAGENTLENS_AGENT_EXE=openclaw \
  MTAGENTLENS_OPENCLAW_STATE="$HOME/.openclaw" \
  MTAGENTLENS_OPENCLAW_AGENTS=main \
  ./collector/build/release/mtagentlens trace \
    -p "$GW" -c MainThread --process=true \
    --binary-path "$(readlink -f ~/.nvm/versions/node/v24.19.0/bin/node)"
```

`-p "$GW"` is required for a gateway that started before the collector: FILTER
mode has no EXEC event for it, so the probe now treats the target PID's direct
children as tracked (and their descendants inherit tracking).

- 显式 exe 边界是**严格模式**：只有匹配边界的进程才算 Agent，其他 Node
  应用的 TLS 会话不会因“有 TLS”被误判为 Agent；comm/exe 可组合使用。
- 带外 intent 在 `[diagnostics:json]` 的 `openclaw_tool_calls` 中计数；数据库
  以只读方式打开，schema 版本不匹配时自动降级，不影响 eBPF 路径。
- gateway 自动发现：唯一 cgroup 命中优先，其次唯一命令行命中；若多个进程同时命中
  （多用户/多容器主机），按“未找到”处理（pid=0）并记录 degraded，不随意选一个。
- 已知残余：asserted `agent_key`（如 `agent:main:main`）尚未成为独立的图
  AgentId；当前进程归属由 Gateway PID + comm/exe 边界决定，cgroup unit 暂不可用。
- 自检：`./scripts/e2e/openclaw-outofband.sh`（需 sudo）会用一个最小
  `openclaw-agent.sqlite` fixture 断言 `tool.call → OutOfBandAsserted intent`。

> [!NOTE]
> **当前验证状态**：MtAgentLens 当前主要验证对象是 **Claude Code**（Bun 运行时，静态链接 BoringSSL）；具体版本与实验范围见[当前证据入口](docs/evaluation/CURRENT-EVIDENCE.md)。对于动态链接系统 `libssl.so` 的 Agent（大多数 Python/Node.js AI 工具），eBPF uprobe 机制在架构上同样适用，但尚未进行系统性验证。其他 LLM Agent 可通过 `-c <进程名>` 参数启用实验性支持。

### Docker（无需本地安装依赖）

```bash
docker build -t mtagentlens .
docker run --rm -it --privileged --pid=host \
  -v /sys:/sys:ro -v /usr:/usr:ro -v /lib:/lib:ro \
  -p 7395:7395 \
  mtagentlens record -c claude --binary-path /path/to/claude/binary
```

### VS Code / GitHub Codespaces

项目包含 `.devcontainer/devcontainer.json`，打开后自动构建 eBPF 环境。

---

## 命令

| 命令 | 输入源 | 说明 |
|------|--------|------|
| `record` | SSL + Process + System | 一键监控，自动启动 Web 界面 |
| `trace` | 多源可选组合 | `--ssl` `--process` `--stdio` `--system` 独立开关 |
| `ssl` | `tls_capture` uprobe | 原始 SSL 解密明文流，支持 SSE 合并和 HTTP 解析 |
| `process` | `process` tracepoint/kprobe | 进程 exec/exit、文件 open/unlink、网络 bind/connect |
| `stdio` | `stdiocap` uprobe | 指定 PID 的 stdin/stdout/stderr 内容，用于本地 MCP |
| `system` | `/proc` 轮询 | CPU 使用率和 RSS 内存采样 |
| `export-genai` | 责任数据库 | 导出 GenAI 格式事件 |
| `replay-sensitivity` | 已记录的责任数据库 | 离线参数敏感性回放 |
| `doctor` | 本机环境 | 检查权限、依赖与采集前置条件 |

```bash
# 灵活组合监控
sudo mtagentlens trace --ssl true --process true --system --server

# 独立子命令
sudo mtagentlens ssl --http-parser --http-filter "POST /api"
sudo mtagentlens process -- -c python -d 1000
sudo mtagentlens stdio --pid 12345
```

`trace --capture-only` 保留所选采集与原始日志，不创建责任数据库，也不加载责任分析器或 Observer。`--disable-responsibility-observer` 用于 runtime+DB 的无模型对照；ONNX 分类器已从产品移除，不存在 `--disable-classifier`。默认 `record`／`trace` 行为保持不变。这些模式尚不能代替完整的并发开销驱动或实测结果。

---

## 项目结构

```
MtAgentLens/
│
├── collector/                     Rust 引擎 — 编译为单二进制 mtagentlens
│   ├── src/main.rs                CLI 入口 (clap derive)，9 个子命令
│   ├── src/framework/             流式数据采集管线
│   │   ├── runners/               6 个生产 Runner（Ssl, Process, Stdio, System, Agent, OpenClaw）+ Fake 测试替身
│   │   ├── analyzers/             框架 Analyzer + ResponsibilityRuntime：SSE, HTTP, HandoffInjection, ...
│   │   └── binary_extractor.rs    include_bytes! 内嵌 BPF 二进制
│   ├── src/responsibility/        因果分析引擎
│   │   ├── graph/                 ResponsibilityGraph：节点·边·污点·模式匹配
│   │   ├── provenance/            OS 级内容来源标签 · 权限包络 · span 匹配
│   │   ├── identity/              IdentityResolver：并查集（进程树 + TLS 会话）
│   │   ├── claims/                ClaimBuilder · TracebackBuilder · AlertBuilder
│   │   ├── classification/        判定引擎 · 规则 · 6 维分诊评分 · final_decision 聚合
│   │   ├── observer/              Observer LLM：精确签名缓存 · 语义仲裁
│   │   ├── outofband/             OpenClaw 轨迹只读 → OutOfBandAsserted intent
│   │   ├── persistence/           SQLite 单写（单一 schema + 版本门）· 只读查询
│   │   ├── replay/                回放执行器 · 跨运行差异
│   │   ├── judgment/              策略评估 · 委派追踪
│   │   ├── runtime/               事件循环编排 (阶段 1 流式 + 阶段 2 批量终结)
│   │   └── signals/               SignalGraph：实时信号聚合
│   └── src/server/                Hyper 1.0 HTTP 服务器 + rust-embed 内嵌前端（无内置鉴权；默认仅绑回环）
│
├── bpftools/                      eBPF 程序 (C, libbpf + bpftool)
│   ├── tls_capture/               SSL_read/write uprobe → 解密明文
│   ├── process/                   tracepoint + kprobe → 进程生命周期
│   ├── stdiocap/                  read/write tracepoint → MCP stdio 捕获
│   └── process_ext/               仅供测试的 header (mem_info · map_flush)
│
├── frontend/                      Next.js 16 · React 19 · TypeScript 5 · Tailwind 4
│   ├── 编译为静态 HTML/CSS/JS (next build → out/)
│   └── 通过 rust-embed 内嵌到 mtagentlens 二进制
│
├── env/                           基准测试环境
│   ├── src/mtagentlens_benchmark/ Python 包 (CLI, Engine, Evaluator, Verifier)
│   ├── scenarios/                 按类别组织的场景定义 + fixtures
│   ├── scripts/verifiers/         场景验证器
│   └── runs/                      运行工件 (gitignored)
│
├── datasets/FuxiBench/            93 场景基准（58 攻击 + 35 良性）+ 完整场景目录
├── compare/                       实验与论文产出
│   ├── results/                   检测 · 身份 · 归因 · 消融 · 敏感性 · 开销
│   ├── results/external/          外部基线对比
│   ├── scripts/                   build · audit · run · external · vis · perf
│   └── tools/                     离线工具对比框架
│
├── scripts/                       唯一门禁 · 发布 · E2E · 基线工具
├── docs/                          文档索引 (docs/README.md)
├── libbpf/ · bpftool/             上游 submodule (eBPF 构建依赖)
├── vmlinux/                       内核 BTF 头
├── rust-toolchain.toml
├── Dockerfile / Dockerfile.dev
├── Makefile
├── LICENSE-APACHE (Apache-2.0 + BPF GPL 例外)
└── LICENSE-MIT
```

## 文档

| 文档 | 说明 |
|------|------|
| [架构设计](docs/architecture.md) | 系统架构全貌——eBPF → Rust → 因果分析 → 前端 |
| [实验设计](docs/design/experiment-design.md) | RQ 驱动的实验方案 |
| [评分引擎](docs/design/scoring-engine.md) | 6 维分诊评分 · PBC 图事实 · 固定 logistic 压缩（非拟合校准） |
| [SAFI 框架](docs/materials/safi/README.md) | 安全不对称文件注入——攻击载荷迁移方法论 |
| [文档索引](docs/README.md) | 全部文档导航 |

---

## FuxiBench

当前数据范围为 **93 个场景（58 攻击、35 良性）**；当前两类型分类为 A 47 / B 11。场景、fixture、prompt 与 GT 按场景对应；历史 86 场景的 prompt 和实验输出按原有记录解释。

场景标签来自 AI 盲标与裁决，尚无人工标注、人工 κ 或人工金标。设计级标签不能当作运行根因真值；归因实验 T6 的 suite 状态仍为 `not_estimable`。数据契约、标注来源和限制见 [数据卡](datasets/FuxiBench/DATASET-CARD.md)与 [GT 来源说明](datasets/FuxiBench/docs/provenance.md)，冻结实验范围见 [当前证据入口](docs/evaluation/CURRENT-EVIDENCE.md)。

```bash
python3 scripts/evaluation/validate_gt.py --dataset datasets/FuxiBench
python3 scripts/evaluation/check_scenario_integrity.py --strict
python3 scripts/evaluation/build_dataset_release.py --out /tmp/mtagentlens-dataset-release
```

正式实验统一读取指定 run 的 `runs.final_decision`，独立记录执行资格、拒绝执行、采集损失和不确定性。官方 baseline 只能由相应 SDK、模型或服务产生；依赖缺失必须报告不可用。当前工程回归不提供新的准确率、召回率或消融结论。

## 性能

[开销报告](compare/results/overhead/README.md)分别给出 2026-10-28 冻结完整系统批次与 2026-10-29 Collector 稳态补测。固定本地负载下，三次 Collector 稳态单核 CPU 均值为 0.661%；该结果包含 Rust Collector 和两个采集子进程，不代表完整系统或任意真实 Agent 流量。完整系统批次另按并发档位报告 CPU、内存与证据关联情况，具体负载、测量窗口和组件分账见报告。

冻结结果不等于当前新构建已重新测量；测试中的合成吞吐预算仅用于回归检查。[当前实验状态](docs/evaluation/CURRENT-EVIDENCE.md)与[工程维护](docs/superpowers/README.md)分别记录实验依据和开发材料；[实验文档](docs/evaluation/README.md)仅供追溯。

---

## 关键设计

### 时间戳

eBPF 使用 `bpf_ktime_get_ns()`（启动时间纳秒），`Event` 存储原始值。`TimestampNormalizer` analyzer 通过 `/proc/stat` btime 计算 UTC epoch 纳秒，管线中后续 analyzer 使用 epoch 纳秒；展示 JSON 的 `timestamp` 字段另转为毫秒。

### 二进制内嵌

```rust
// collector/src/framework/binary_extractor.rs
const PROCESS_BINARY: &[u8] = include_bytes!("../../../bpftools/.output/bin/process");
const TLS_CAPTURE_BINARY: &[u8] = include_bytes!("../../../bpftools/.output/bin/tls_capture");
```

eBPF 程序和前端静态资源在编译时嵌入 Rust 二进制。运行时提取到临时目录，注册 `SIGINT`/`SIGTERM` 处理器确保退出时清理。构建顺序固化为 `BPF → 前端 → Rust`。

### 信号处理与关闭

`SIGINT` → 设置 `SHUTDOWN_REQUESTED: AtomicBool` → 触发 `RUNTIME_SHUTDOWN` 通知 → ResponsibilityRuntime 执行阶段 2 批量终结化（traceback + claims + alerts）→ 指标输出 → 清理临时文件。

### 身份解析

三个独立信号融合为统一 Agent 身份：

```
root_pid ──────────┐
tls_session ───────┼──→ UnionFind IdentityResolver ──→ canonical_agent_id
conversation_id ───┘
```

每个新事件根据已有信号渐进式合并身份。即使某个信号缺失（如进程已退出、TLS 会话已关闭），其他信号仍可维持身份连续性。

### `--binary-path` 与静态链接 SSL

部分 LLM 工具静态链接 SSL 库（如 Claude Code 的 Bun 运行时使用 BoringSSL，NVM 安装的 Node.js 使用静态 OpenSSL），不使用系统 `libssl.so`。当指定 `--binary-path` 时，`tls_capture` 跳过 `--comm` 过滤，通过字节模式匹配自动定位静态链接的 SSL 函数。动态链接 `libssl.so` 的工具无需此参数。

---

## 开发

```bash
make build              # 全量构建 (BPF → 前端 → Rust)
make dev                # 开发构建 (debug, 增量编译)
make check              # 环境预检
make test               # 快速测试 (BPF+Rust+前端构建)
./scripts/verify.sh     # 唯一完整门禁（18 道检查）
make lint               # 代码质量检查
make fmt                # 格式化所有代码
make help               # 显示所有可用目标

cd collector
cargo build --release   # → build/release/mtagentlens（见 collector/.cargo/config.toml）
cargo test              # 全 target；实际计数以本次输出为准
cargo clippy            # Lint

cd frontend
npm run dev             # 开发服务器 (:7395)
```

Docker 开发环境：

```bash
make docker-dev         # 构建开发镜像（含完整编译工具链）
docker run --rm -it -v $(pwd):/workspace -w /workspace \
  --privileged --pid=host \
  -v /sys:/sys:ro -v /usr:/usr:ro -v /lib:/lib:ro \
  -p 7395:7395 \
  mtagentlens:dev bash
```

---

## 环境要求

| 组件 | 要求 |
|------|------|
| Linux 内核 | 5.8+（ringbuf 要求；需 BTF，兼容性仍须逐内核验证） |
| Rust | 1.85+ (edition 2024) |
| Node.js | 22.13.0+（项目工具链最低要求） |
| 构建工具 | clang, llvm, libelf-dev, zlib1g-dev, make, pkg-config |
| 运行时权限 | root 或 `CAP_BPF` + `CAP_SYS_ADMIN` |

---

## 引用

```bibtex
@software{mtagentlens2026,
  author = {刘周康 and MtAgentLens contributors},
  title = {{MtAgentLens}: Zero-Instrumentation Multi-Agent LLM Attack
           Observability with eBPF},
  year = {2026},
  url = {https://github.com/mtagentlens/mtagentlens}
}
```

---

## 许可证

### 源代码

本项目源代码采用 **Apache-2.0 OR MIT** 双许可。详见 [LICENSE-APACHE](LICENSE-APACHE) 与 [LICENSE-MIT](LICENSE-MIT)。

eBPF 内核程序（`bpftools/*.bpf.c`）按文件头 SPDX 声明使用 **GPL-2.0 OR BSD-3-Clause**。用户态 eBPF 加载器跟随 libbpf 的双许可证（**LGPL-2.1 OR BSD-2-Clause**）。

### 技术文档与演示材料

技术文档（含设计开发文档）、演示文稿、演示视频依据
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/deed.zh)
授权。分享或改编须保留署名，衍生作品须以相同协议发布。

---

## 第三方开源组件使用声明

| 组件 | 来源 | 许可证 | 用途 |
|------|------|--------|------|
| libbpf | [github.com/libbpf/libbpf](https://github.com/libbpf/libbpf) | LGPL-2.1 / BSD-2-Clause | eBPF 程序加载、BPF map 管理、内核接口封装 |
| bpftool | [github.com/libbpf/bpftool](https://github.com/libbpf/bpftool) | GPL-2.0 / BSD-2-Clause | eBPF skeleton 代码生成 |

## 安全与贡献

- 漏洞报告与安全默认值：[`SECURITY.md`](SECURITY.md)
- 威胁模型（STRIDE + 代码坐标）：[`docs/security/threat-model.md`](docs/security/threat-model.md)
- 安全设计： [威胁模型](docs/security/threat-model.md)
- 贡献流程与唯一门禁：[`CONTRIBUTING.md`](CONTRIBUTING.md)、`./scripts/verify.sh`
