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
<a href="#性能"><img src="https://img.shields.io/badge/probe_latency-%7E101ns-60a5fa" alt="Probe latency ~101ns"></a>
<a href="#性能"><img src="https://img.shields.io/badge/overhead-6.24%25_CPU_%2F_1.42_GiB_RSS-amber" alt="Measured overhead: 6.24% CPU / 1,424.8 MiB RSS"></a>
<a href="#FuxiBench"><img src="https://img.shields.io/badge/FuxiBench-binary_TPR_0.896-f97316" alt="Delivered binary: Unsafe TPR 0.8955, FPR 0"></a>

---

## 全国大学生计算机系统能力大赛 · 操作系统设计赛 — OS 功能挑战赛

**proj32 · 基于 eBPF 的系统级多智能体异常监测框架与方法 · 队名：看月亮**

### 决赛提交材料

| 材料 | 内容 |
|------|------|
| [**决赛答辩视频以及功能演示视频**](https://pan.baidu.com/s/1qCwLZJx9t-0eQwKk7uUoJg?pwd=moon)（百度网盘 · 提取码 `moon`） | 决赛答辩全程视频 + 端到端功能演示：eBPF 零插桩采集 → 责任图构建 → 告警与证据回溯，含典型攻击场景回放 |
| [**决赛答辩 PPT**](./docs/决赛文档/看月亮-proj32-决赛答辩PPT.pptx) | 项目背景、核心技术与 FuxiBench 评估结果的答辩演示 |
| [**设计开发文档**](./docs/决赛文档/看月亮-proj32-设计开发文档.pdf) | 系统架构、模块设计、实验方法学与评估数据完整记录 |

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
- **低开销运行**：将观测开销控制在端侧可接受范围内，适合真实 Agent 执行环境。

与依赖 prompt 分类或应用日志的方案相比，MtAgentLens 的差异在于将观测位置置于 Agent 之下、将归因对象置于任务链与责任链之上。其产出不是“这段文本可疑”，而是“该行为由谁触发、经历了何种路径、证据留存何处、应归责于谁”。

## 功能演示

[![MtAgentLens 完整功能演示（约 1 分 48 秒）](docs/demo/demo.png)](docs/demo/demo.mp4)

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
  |  | decrypt plain |   | bind / connect|  | uprobe capture|       |
  |  +---------------+   +-------------+   +----------------+       |
  |                                                                 |
  |  process also emits file mutations (unlinkat / truncate)         |
  |                                                                 |
  |  userspace: system  — CPU / memory sampled from /proc            |
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
  |  | Analyzer Pipeline (29 analyzers, add_analyzer() chain)    |  |
  |  |                                                           |  |
  |  |  +-----------+  +-----------+  +------------------------+ |  |
  |  |  | SSE reass |  | HTTP parse|  | TimestampNormalizer    | |  |
  |  |  | chunk mg  |  | req / resp| | btime -> epoch ms       | |  |
  |  |  +-----+-----+  +-----+-----+  +-----------+------------+ |  |
  |  |        +------------------+--------------------+          |  |
  |  |                           v                               |  |
  |  |  +---------------------------------------------------+    |  |
  |  |  |            ResponsibilityRuntime                  |    |  |
  |  |  |                                                   |    |  |
  |  |  |  Phase 1 (streaming, per-event)                   |    |  |
  |  |  |  +---------------------------------------------+  |    |  |
  |  |  |  | PhaseClassificationEngine                   |  |    |  |
  |  |  |  |   -> 25 rules (5 safe + 20 unsafe), 6-dim score |  |    |  |
  |  |  |  |   -> Process Boundary Crossing (PBC) detect |  |    |  |
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
  |  |  |  |   -> CachePrior (L1 exact / L2 semantic)    |  |    |  |
  |  |  |  |   -> 34/86 scenarios triggered (FuxiBench)  |  |    |  |
  |  |  |  |   -> 84.2% directional accuracy             |  |    |  |
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
  |  /api/runs/{id}/{alerts,evidence,events,snapshots,db}            |
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

**L3 Observer LLM** 仅在 L1/L2 出现矛盾信号时介入。例如，当 eBPF 检测到进程读取 `/etc/passwd`，而 HTTP 请求体中并无对应敏感字段时，Observer 负责语义仲裁。系统通过 **CachePrior** 将历史结果缓存至 L1（精确匹配）与 L2（语义相似）两级缓存，以降低 LLM 调用率。

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
MTAGENTLENS_AGENT_CGROUP=openclaw-gateway.service \
MTAGENTLENS_AGENT_EXE=openclaw \
  sudo -E ./collector/build/release/mtagentlens record --binary-path <agent ELF>
```

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
  MTAGENTLENS_AGENT_CGROUP=openclaw-gateway.service \
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

- 显式 cgroup/exe 边界是**严格模式**：只有匹配边界的进程才算 Agent，其他
  Node 应用的 TLS 会话不会因“有 TLS”被误判为 Agent。
- 带外 intent 在 `[diagnostics:json]` 的 `openclaw_tool_calls` 中计数；数据库
  以只读方式打开，schema 版本不匹配时自动降级，不影响 eBPF 路径。
- 已知残余：asserted `agent_key`（如 `agent:main:main`）尚未成为独立的图
  AgentId；当前进程归属由 Gateway PID + cgroup/exe 边界决定。
- 自检：`./scripts/e2e/openclaw-outofband.sh`（需 sudo）会用一个最小
  `openclaw-agent.sqlite` fixture 断言 `tool.call → OutOfBandAsserted intent`。

> [!NOTE]
> **当前验证状态**：MtAgentLens 已在 **Claude Code**（Bun 运行时，静态链接 BoringSSL）上完成全面测试。对于动态链接系统 `libssl.so` 的 Agent（大多数 Python/Node.js AI 工具），eBPF uprobe 机制在架构上同样适用，但尚未进行系统性验证。其他 LLM Agent 可通过 `-c <进程名>` 参数启用实验性支持。

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

```bash
# 灵活组合监控
sudo mtagentlens trace --ssl true --process true --system --server

# 独立子命令
sudo mtagentlens ssl --http-parser --http-filter "POST /api"
sudo mtagentlens process -- -c python -d 1000
sudo mtagentlens stdio --pid 12345
```

---

## 项目结构

```
MtAgentLens/
│
├── collector/                     Rust 引擎 — 编译为单二进制 mtagentlens
│   ├── src/main.rs                CLI 入口 (clap derive)，8 个子命令
│   ├── src/framework/             流式数据采集管线
│   │   ├── runners/               7 个 Runner：Ssl, Process, Stdio, System, Agent, OpenClaw, Fake
│   │   ├── analyzers/             30+ 个 Analyzer：SSE, HTTP, HandoffInjection, ...
│   │   └── binary_extractor.rs    include_bytes! 内嵌 BPF 二进制
│   ├── src/responsibility/        因果分析引擎 (17 个子模块)
│   │   ├── graph/                 ResponsibilityGraph：节点·边·污点·模式匹配·简化器
│   │   ├── identity/              IdentityResolver：并查集，三信号融合
│   │   ├── claims/                ClaimBuilder · TracebackBuilder · AlertBuilder
│   │   ├── classification/        PhaseClassificationEngine · 25 条规则 · 6 维评分
│   │   ├── observer/              Observer LLM：CachePrior (L1/L2 缓存) · 语义仲裁
│   │   ├── outofband/             OpenClaw 轨迹只读 → OutOfBandAsserted intent
│   │   ├── persistence/           SQLite 单写（单一 schema + 版本门）· 只读查询
│   │   ├── replay/                回放执行器 · 跨运行差异
│   │   ├── judgment/              策略评估 · 委派追踪
│   │   ├── runtime/               事件循环编排 (阶段 1 流式 + 阶段 2 批量终结)
│   │   ├── server/                legacy 路由树（`serve_request` 零调用；生产用 src/server）
│   │   └── signals/               SignalGraph：实时信号聚合
│   ├── src/server/                Hyper 1.0 HTTP 服务器 + rust-embed 内嵌前端
│   └── src/auth/                  TOTP 2FA 认证
│
├── bpftools/                      eBPF 程序 (C, libbpf + bpftool)
│   ├── tls_capture/               SSL_read/write uprobe → 解密明文
│   ├── process/                   tracepoint + kprobe → 进程生命周期
│   ├── stdiocap/                  read/write uprobe → MCP stdio 捕获
│   └── process_ext/               仅供测试的 header (mem_info · map_flush)
│
├── frontend/                      Next.js 16 · React 19 · TypeScript 5 · Tailwind 4
│   ├── 编译为静态 HTML/CSS/JS (next build → out/)
│   └── 通过 rust-embed 内嵌到 mtagentlens 二进制
│
├── env/                           基准测试环境
│   ├── src/mtagentlens_benchmark/ Python 包 (CLI, Engine, Evaluator, Verifier)
│   ├── scenarios/                 按类别组织的场景定义 (36 类) + fixtures
│   ├── scripts/verifiers/         73 个场景验证器
│   └── runs/                      运行工件 (gitignored)
│
├── compare/                       实验与论文产出
│   ├── Dataset/FuxiBench/         86 场景冻结基准 + 完整场景目录
│   ├── results/paper/             主评估 · 消融 · 性能 · InjecAgent · LaTeX 表格
│   ├── results/external/          外部基线对比
│   └── scripts/                   build · audit · run · vis · perf
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
| [评分引擎](docs/design/scoring-engine.md) | 6 维组合评分 · PBC 检测 · Platt 校准 |
| [SAFI 框架](docs/safi/README.md) | 安全不对称文件注入——攻击载荷迁移方法论 |
| [文档索引](docs/README.md) | 全部文档导航 |

---

## FuxiBench

FuxiBench 是 MtAgentLens 的评估基准，用于覆盖多 Agent 安全观测中的主要场景。全部 86 个场景以 Claude Code 为 Agent 运行时采集，涵盖单 Agent、Agent Teams、Subagents 三种协作模式。

| 维度 | 数据 |
|------|------|
| 总场景 | **86**（67 攻击 + 19 良性） |
| 攻击类别 | **10**（manifest 实际分类数） |
| Type A（直接注入） | 10 — 攻击载荷在 task prompt 中 |
| Type B（间接注入） | 34 — 攻击载荷隐藏在 fixture 文件中 |
| Type C（多 Agent） | 23 — 跨进程协同攻击（**核心贡献**） |
| 标注 | 3 名独立标注者，交叉验证 + 共识裁判 |

**评估结果（按“测的是什么”分列，避免把脚本重打分当成二进制能力）**：

| 指标 | 值 | 口径与出处 |
|------|:--:|------|
| 攻击集 TPR / F1 | 0.9851 / 0.9925 | `compare/results/paper/main_evaluation.json` 的 `full_metrics`；由评估脚本的 `full_scores` 计算（引擎分 + 脚本重打分）。**不是**交付二进制的原始 `rust_score`。 |
| 二进制自身 `rust_score` | 11/67 攻击场景 < 0.70 | 同一 `main_evaluation.json` 的 `per_scenario`；因此“二进制召回 0.985”不成立，二者不可混用。 |
| 二进制自身裁决（FuxiBench 86 个 DB） | **Unsafe**: TPR 0.8955（Wilson 0.7997–0.9485）FPR 0.000（0–0.168）；**Warning+**: TPR 0.9851（0.9202–0.9974）**FPR 0.8947（0.6861–0.9706）** | `compare/results/paper/standard_evaluation_v5.json`；直接读取 `responsibility_alerts` 的 `decision`/`severity`，BCa 10,000 次 + Wilson 区间。**Warning+ 在旧语料上不是可用口径**（17/19 良性场景产生 Uncertain/Warning）。 |
| 良性 FPR（Warning+，隔离） | **0.00（0/20；Wilson 95% 0–0.161）** | `docs/evaluation/fpr-baseline.json`；20/20 观测到；每个场景用 clean profile + **inode 独立副本 + 唯一 comm**，`process_comms` 不含外层 `claude`。旧 0/18 为未隔离采集，仅作历史。 |
| 良性 FPR（actionable / Unsafe，隔离） | **0.00（0/20；Wilson 95% 0–0.161）** | 同上；全部 20 个告警为 `Info` / `Safe`，0 claims。 |
| Observer 路由覆盖 | 63/86 场景出现 `observer_*` 路由，其中仅 6/86 得到非 `observer_failed` 裁决 | 从 86 个 DB 的 `engine_section_json.classification_route` 直接统计；59 个场景的 observer 调用失败（API 不可用），不能作为“Observer 有效性”的证据 |

> 隔离 FPR 的采集方式见 `docs/evaluation/measurement-isolation.md`：被测 Agent 以 inode 独立副本运行，collector 的 `-c` 与 TLS uprobe 只挂到该副本，外层交互会话被排除（已用内层/外层 PID 对照验证）。仓库内早期宣传的 `TPR 0.896 / FPR 0.000` 是 2026-06-25 旧采集，不再作为头条数字。

消融实验的结论以 `compare/results/paper/ablation_summary_v16.json` 为准：该文件中 L2 的 ΔAUROC 为 0.0（不显著），与早先“L2 是主要增益来源”的表述冲突；在补做一致的消融重跑之前，不应把 L2 写成主增益来源。

详见 [`compare/Dataset/FuxiBench/SCENARIOS.md`](compare/Dataset/FuxiBench/SCENARIOS.md) 和
[`compare/results/paper/EXPERIMENTS.md`](compare/results/paper/EXPERIMENTS.md)。

---

## 性能

微基准测试（Linux 6.17，本地 bpftool stat + pidstat）：

| 指标 | 数值 | 测量方法 |
|------|------|---------|
| SSL enter probe | **101.3 ns/op** | bpftool stat，2,000,000 次运行 |
| SSL read exit probe | **57.6 ns/op** | bpftool stat，1,000,000 次运行 |
| SSL write exit probe | **59.4 ns/op** | bpftool stat，1,000,000 次运行 |
| 单次 SSL read/write 总 probe 成本 | **218.3 ns/op** | enter + read_exit + write_exit |
| 空闲 RSS | **36.8 MB** | 60s pidstat 采样 |

端到端开销（`scripts/wire/measure-overhead.sh`，一次 `claude -p` 回合，45s）：
**平均单核 CPU 6.24%**、**峰值 RSS 1,424.8 MiB**（其中 ONNX 推理约 1,327.3 MiB）。
仓库中“<3% 吞吐开销”的数字来自合成 FakeRunner 管线，生成脚本的
`claim_boundary.not_supported` 明确声明它不支持生产 agent 的端到端延迟结论；
在没有对应 JSON 的端到端测量前，不应把它当作产品开销。

---

## 关键设计

### 时间戳

eBPF 使用 `bpf_ktime_get_ns()`（启动时间纳秒），`Event` 存储原始值。`TimestampNormalizer` analyzer 通过 `/proc/stat` btime 计算 UTC epoch 毫秒，管线中所有后续 analyzer 使用统一时间基准。

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
make test               # 全量测试
make lint               # 代码质量检查
make fmt                # 格式化所有代码
make help               # 显示所有可用目标

cd collector
cargo build --release   # → build/release/mtagentlens（见 collector/.cargo/config.toml）
cargo test              # 869+ 测试
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
| Linux 内核 | 4.1+ (推荐 5.0+，需 `CONFIG_DEBUG_INFO_BTF`) |
| Rust | 1.85+ (edition 2024) |
| Node.js | 18+ (推荐 22 LTS) |
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

### 技术文档与答辩材料

技术文档（含设计开发文档）、答辩 PPT、演示视频依据
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
- OpenSSF 自评：[`docs/security/openSSF-self-assessment.md`](docs/security/openSSF-self-assessment.md)
- 贡献流程与唯一门禁：[`CONTRIBUTING.md`](CONTRIBUTING.md)、`./scripts/verify.sh`
