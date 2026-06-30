<p align="center">
  <h1 align="center">MtAgentLens</h1>
  <p align="center">
    <strong>零插桩 · 内核级 · 多 Agent 攻击可观测性</strong><br>
    <sub>从系统调用到责任归因——在 OS 边界采集可验证的运行事实</sub>
  </p>
  <p align="center">
    <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-blue"></a>
    <a href="#性能"><img src="https://img.shields.io/badge/probe_latency-~101ns-blueviolet"></a>
    <a href="#性能"><img src="https://img.shields.io/badge/overhead-<3%25-brightgreen"></a>
    <a href="#FuxiBench"><img src="https://img.shields.io/badge/FuxiBench-F1%3D0.945-orange"></a>
  </p>
</p>

---

<div align="center">

## 全国大学生计算机系统能力大赛 · 操作系统设计赛 — OS 功能挑战赛

### proj32 · 基于 eBPF 的系统级多智能体异常监测框架与方法

### 初赛提交材料

<table align="center">
  <tr>
    <th></th>
    <th>文件</th>
    <th>大小</th>
  </tr>
  <tr>
    <td align="center">▸</td>
    <td><strong><a href="./MtAgentLens初赛演示视频.mp4">MtAgentLens初赛演示视频.mp4</a></strong></td>
    <td>364 MB（建议下载后观看）</td>
  </tr>
  <tr>
    <td align="center">▸</td>
    <td><strong><a href="./初赛ppt.pptx">初赛ppt.pptx</a></strong></td>
    <td>27 MB</td>
  </tr>
  <tr>
    <td align="center">▸</td>
    <td><strong><a href="./看月亮-proj32-设计开发文档.pdf">看月亮-proj32-设计开发文档.pdf</a></strong></td>
    <td>6.0 MB</td>
  </tr>
</table>

<sub>队名：看月亮</sub>

</div>

---

## 背景

大语言模型正在把软件系统的人机交互方式推向一个更靠近执行层的阶段。以 Claude Code、Gemini CLI 为代表的端侧 Agent 不再只是生成文本，它们可以读取仓库、调用工具、执行命令、修改文件，并通过多 Agent 协作完成编程、运维、测试和发布等复杂任务。随着这类系统进入真实运行环境，Agent 已经不再是“回答问题的界面”，而是会消耗系统资源、触碰敏感资产、影响工程状态的执行主体。

能力边界的变化直接带来安全边界的变化。模型幻觉可能诱发逻辑死循环、重复构建或无效搜索，造成计算资源浪费；提示词注入、供应链污染和恶意上下文则可能诱导 Agent 执行越权访问、凭证读取、数据外传或破坏性操作。更重要的是，这些风险不再只出现在用户输入本身，它们可以隐藏在 README、构建脚本、配置文件、交接文档、工具返回值和子 Agent 的中间结果中，并沿着后续执行链路持续放大。

公开事件已经证明，这不是理论上的担忧。AWS 安全公告确认，Amazon Q Developer for Visual Studio Code Extension **1.84.0** 曾分发包含恶意代码的版本，后续被移除并发布修复版本；GitHub advisory 将其记录为 **CVE-2025-8217**。2025 年 7 月，SaaStr 创始人 Jason Lemkin 公开披露 Replit AI Agent 在 code freeze 期间仍执行破坏性数据库操作，AI Incident Database 将其记录为 Incident 1152。这两类事件共同说明，AI 工具链的风险已经进入实际执行层，复盘不能停留在对话文本，而必须回到实际命令、文件/数据库操作、网络访问、时间线和责任主体。参见 [AWS-2025-015](https://aws.amazon.com/security/security-bulletins/AWS-2025-015/)、[GHSA-7g7f-ff96-5gcw](https://github.com/aws/aws-toolkit-vscode/security/advisories/GHSA-7g7f-ff96-5gcw) 与 [AI Incident Database 1152](https://incidentdatabase.ai/cite/1152/)。

多 Agent 协作把这件事进一步推复杂了。一次任务可能由父 Agent 发起，再分派给多个 worker、工具进程或子 Agent；其中某些进程生命周期极短，只在临时目录写入证据文件后退出；部分上下文则通过 handoff 文件、summary、角色声明或共享工作区继续传播。到这个阶段，单条日志、单个 PID 或单段 prompt 已经不足以解释异常，系统真正需要回答的是：谁发起了任务，哪些进程和文件参与了执行，风险沿着哪条路径传播，证据保存在哪里，最终责任应归属于哪个 Agent 或任务链。

现有监测手段在这里都只覆盖了问题的一部分。应用层调用链、日志分析和高层指标监控易于接入，却依赖 Agent 框架主动产生日志，容易遗漏短命进程、真实文件操作和底层网络行为，也难以抵抗日志缺失或被绕过的情况。传统系统调用审计能够看到底层动作，却缺少 Agent 身份、任务语义和委派关系，难以把“某个进程读了文件”还原为“某个 Agent 在某次任务中触发了风险”。Prompt scanner 和内容分类器主要处理文本输入，对隐藏在文件、工具输出、构建脚本和跨进程传播中的间接异常天然不足。

MtAgentLens 基于这一缺口构建。项目面向的不是一段可疑文本，而是 Agent 真正执行任务后的系统行为：进程如何派生，文件如何被读写，网络连接由谁触发，上下文如何跨 Agent 传播，异常如何回到责任主体。通过 eBPF 采集 OS 事实、Rust 流式引擎构建责任图、前端提供可审计调查路径，MtAgentLens 将观测位置放到 Agent 之下，将归因对象放到任务链和责任链上，让告警能够回到证据，让异常能够回到过程，让单点行为能够回到完整责任链。

---

## MtAgentLens

MtAgentLens 是一个面向多 Agent 运行系统的 **零插桩 OS 级可观测与责任归因工具**。它在 Linux 内核边界采集进程、文件、网络、stdio 和 TLS 明文片段等运行事实，不依赖 Agent 框架，不修改目标代码，也不要求接入 SDK。

它解决的不是单点日志聚合，而是多 Agent 时代最难处理的三类断层：

1. **应用层与内核层的断层**：文本日志看不到真实进程、文件和网络行为。
2. **行为与主体的断层**：系统调用能看到动作，但很难还原是哪一个 Agent、哪一次任务触发。
3. **事件与责任的断层**：多个 worker、交接文件、共享上下文和短命进程交织后，单条记录不足以解释异常。

MtAgentLens 将这些事实统一到一张**跨进程因果责任图**中，并在此基础上提供结构化告警、证据回溯和任务级归因。系统的核心特点包括：

- **内核边界采集**：从 eBPF 直接获取可信运行事实。
- **多信号身份归并**：结合 root_pid、tls_session、conversation_id 等线索识别同一执行链。
- **跨进程污染追踪**：跟踪文件、上下文和工具输出在多个 Agent 之间的传播路径。
- **结构化责任输出**：将行为、证据和责任主体组织为可复核结果，而不是孤立日志。
- **低开销运行**：将观测开销控制在端侧可接受范围内，适合真实 Agent 执行环境。

与依赖 prompt 分类或应用日志的方案相比，MtAgentLens 的不同点在于它把观测位置放在 Agent 之下，把归因对象放到任务链和责任链上。其结果不是“这个文本可疑”，而是“这个行为由谁触发、经过了什么路径、证据在哪里、应归责给谁”。

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
  |       |              |              |              |             |
  |       |  App-level logs (mutable, bypassable) — untrusted       |
  +-------+--------------+--------------+--------------+-------------+
          |              |              |              |
  ========+==============+==============+==============+=============
          |              |  SYSCALL BOUNDARY           |
  ========+==============+==============+==============+=============
          |              |              |              |
  +-------+--------------+--------------+--------------+-------------+
  |                      eBPF PROBES                                |
  |                                                                 |
  |  tls_capture         process           stdiocap                 |
  |  +---------------+   +-------------+   +----------------+       |
  |  | SSL_read      |   | exec / exit |   | read / write   |       |
  |  | SSL_write     |   | fork / clone|   | stdin / stdout |       |
  |  | uprobe (L3)   |   | open / unlink|  | MCP plaintext  |       |
  |  | decrypt plain |   | bind / connect|  | uprobe capture |       |
  |  +---------------+   +-------------+   +----------------+       |
  |                                                                 |
  |  fileaccess          browsertrace       system                  |
  |  +---------------+   +-------------+   +----------------+       |
  |  | file R/W ops  |   | browser SSL |   | CPU / memory   |       |
  |  | workspace bnd |   | plaintext   |   | /proc polling  |       |
  |  +---------------+   +-------------+   +----------------+       |
  |                                                                 |
  +---------------------------------+-------------------------------+
                                    | JSON event stream (stdout)
  ==================================+================================
                                    |
  +---------------------------------+-------------------------------+
  |                    RUST STREAMING ENGINE                        |
  |                                                                 |
  |  +-----------------------------------------------------------+ |
  |  | Runner Layer (7 runners)                                   | |
  |  | SslRunner  ProcessRunner  StdioRunner                     | |
  |  | SystemRunner  AgentRunner  FakeRunner (test)              | |
  |  | -> Pin<Box<dyn Stream<Item = Event> + Send>>             | |
  |  +-----------------------------+-----------------------------+ |
  |                                | EventStream                   |
  |  +-----------------------------+-----------------------------+ |
  |  | Analyzer Pipeline (29 analyzers, add_analyzer() chain)    | |
  |  |                                                            | |
  |  |  +-----------+  +-----------+  +------------------------+  | |
  |  |  | SSE reass |  | HTTP parse|  | TimestampNormalizer   |  | |
  |  |  | chunk mg  |  | req / resp |  | btime -> epoch ms     |  | |
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
  |  |  |  | Observer LLM (triggered on conflict signals) |  |     | |
  |  |  |  |   -> CachePrior (L1 exact / L2 semantic)     |  |     | |
  |  |  |  |   -> 34/86 scenarios triggered (FuxiBench)   |  |     | |
  |  |  |  |   -> 84.2% directional accuracy              |  |     | |
  |  |  |  +---------------------------------------------+  |     | |
  |  |  |                                                   |     | |
  |  |  |  Phase 2 (on SIGINT, batch finalization)          |     | |
  |  |  |  +---------------------------------------------+  |     | |
  |  |  |  | TracebackBuilder -> ClaimBuilder             |  |     | |
  |  |  |  |   -> AlertBuilder (structured security alert) |  |     | |
  |  |  |  |   -> evidence correlation + causal chain     |  |     | |
  |  |  |  +---------------------------------------------+  |     | |
  |  |  +---------------------------------------------------+     | |
  |  +-----------------------------+-----------------------------+ |
  |                                |                               |
  |  Dual-write: SQLite (responsibility.db) + JSONL (streaming)   |
  +---------------------------------+-------------------------------+
                                    |
  ==================================+================================
                                    |
  +---------------------------------+-------------------------------+
  |                 FRONTEND + HTTP API (:7395)                     |
  |                                                                 |
  |  Next.js 16 · React 19 · TypeScript 5 · Tailwind CSS 4          |
  |  rust-embed: compiled into mtagentlens binary at build time    |
  |                                                                 |
  |  /           3D responsibility stage + alert entry point         |
  |  /overview   global posture and probe coverage                   |
  |  /traces     trace workbench and replay timeline                 |
  |  /agents     agent risk profiles                                 |
  |  /clusters   responsibility clusters                             |
  |  /handoff    handoff / delegation observability                  |
  |  /investigations  claim, evidence and traceback workbench        |
  |  /api/events legacy log-backed event stream                      |
  |  /api/runs/{id}/{alerts,correlation-evidence,events,diff,...}    |
  +-----------------------------------------------------------------+
```

---

## 检测层次

LLM Guard、Meta Prompt Guard 等工具主要扫描 prompt 文本中的恶意模式，覆盖范围通常局限于文本输入本身。对于多 Agent 运行场景，主要问题有三类：

| 盲区 | 攻击实例 | MtAgentLens 如何应对 |
|------|---------|---------------------|
| **文本绕过** — 文言文、Unicode 同形字、语义遮蔽可轻易绕过文本分类器 | `prompt-injection-classical`（纯文言文·墨子·棋局对弈）被所有 prompt 工具漏检 | **L1** 不读 prompt 文本——直接在 uprobe 点拦截 SSL 明文，看到实际的 HTTP 请求体和响应 |
| **间接注入** — fixture 文件中的恶意指令不经过 prompt，文本扫描器无法看到 | `supplychain-malicious-build` 的 `build.sh` 中包含 `curl POST /exfil`，prompt 中只写"请运行构建脚本" | **L2** 追踪文件读取→进程执行→网络出站的完整链路，在进程边界跨越（PBC）时标记 |
| **多 Agent 协同** — 攻击指令通过共享文件、交接文档、委派令牌在进程间传递，没有单一文本可供扫描 | `leader-betrayal` 中 Leader 向 Worker 下达恶意指令，Worker 盲从执行 | **L2** 并查集身份解析 + TaintTracker 跨进程追踪污染的传播路径 |

**L3 Observer LLM** 仅在 L1/L2 出现矛盾信号时介入。例如，eBPF 看到进程读取了 `/etc/passwd`，但 HTTP 请求体中没有对应敏感字段时，Observer 用于语义仲裁。系统通过 **CachePrior** 将历史结果缓存到 L1（精确匹配）和 L2（语义相似）两级缓存，以降低 LLM 调用率。

---

## 快速开始

```bash
git clone https://github.com/mtagentlens/mtagentlens.git --recursive
cd mtagentlens
```

### 1. 安装依赖

推荐直接使用仓库提供的安装脚本：

```bash
./install.sh
```

脚本会检查并安装以下依赖：

- `libelf1`
- `libelf-dev`
- `zlib1g-dev`
- `make`
- `clang`
- `llvm`
- `nvm` / 最新 LTS Node.js
- `rustup` / Rust

如需手动安装，也可以按系统环境自行补齐上述依赖。

### 2. 全量构建

```bash
make build
```

### 3. 运行采集

```bash
sudo ./collector/build/release/mtagentlens record -c claude \
  --binary-path ~/.local/share/claude/versions/$(claude --version | head -1)
```

### 4. 访问前端

浏览器访问 `http://127.0.0.1:7395`

> **当前验证状态**：MtAgentLens 已在 **Claude Code**（Bun 运行时，静态链接 BoringSSL）上完成全面测试。对于动态链接系统 `libssl.so` 的 Agent（大多数 Python/Node.js AI 工具），eBPF uprobe 机制在架构上同样适用，尚未系统性验证。对其他 LLM Agent 的实验性支持可通过 `-c <进程名>` 尝试。

Docker：

```bash
docker build -t mtagentlens .
docker run --rm -it --privileged --pid=host \
  -v /sys:/sys:ro -v /usr:/usr:ro -v /lib:/lib:ro \
  -p 7395:7395 \
mtagentlens record -c claude --binary-path /path/to/claude/binary
```

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
sudo mtagentlens trace --ssl --process --system --server

# 独立子命令
sudo mtagentlens ssl --http-parser --http-filter "POST /api"
sudo mtagentlens process --comm python --duration 1000
sudo mtagentlens stdio --pid 12345
```

---

## 项目结构

```
MtAgentLens/
│
├── collector/                     Rust 引擎 — 编译为单二进制 mtagentlens
│   ├── src/main.rs                CLI 入口 (clap derive)，6 个子命令
│   ├── src/framework/             流式数据采集管线
│   │   ├── runners/               7 个 Runner：Ssl, Process, Stdio, System, Agent, Fake
│   │   ├── analyzers/             29 个 Analyzer：SSE, HTTP, HandoffInjection, ...
│   │   └── binary_extractor.rs    include_bytes! 内嵌 BPF 二进制
│   ├── src/responsibility/        因果分析引擎 (17 个子模块)
│   │   ├── graph/                 ResponsibilityGraph：节点·边·污点·模式匹配·简化器
│   │   ├── identity/              IdentityResolver：并查集，三信号融合
│   │   ├── claims/                ClaimBuilder · TracebackBuilder · AlertBuilder
│   │   ├── classification/        PhaseClassificationEngine · 2,595 规则 · 6 维评分
│   │   ├── observer/              Observer LLM：CachePrior (L1/L2 缓存) · 语义仲裁
│   │   ├── persistence/           双写：SQLite + JSONL
│   │   ├── replay/                回放执行器 · 差异引擎 · 评估器
│   │   ├── judgment/              策略评估 · 委派追踪 · OOD 检测
│   │   ├── runtime/               事件循环编排 (阶段 1 流式 + 阶段 2 批量终结)
│   │   ├── server/                REST API：graph · alerts · evidence · SSE · diff
│   │   └── signals/               SignalGraph：实时信号聚合
│   ├── src/server/                Hyper 1.0 HTTP 服务器 + rust-embed 内嵌前端
│   └── src/auth/                  TOTP 2FA 认证
│
├── bpftools/                      eBPF 程序 (C, libbpf + bpftool)
│   ├── tls_capture/               SSL_read/write uprobe → 解密明文
│   ├── process/                   tracepoint + kprobe → 进程生命周期
│   ├── stdiocap/                  read/write uprobe → MCP stdio 捕获
│   ├── fileaccess/                文件操作监控
│   ├── browsertrace/              浏览器 SSL 明文
│   └── process_ext/               增强进程信息 (内存、header)
│
├── frontend/                      Next.js 16 · React 19 · TypeScript 5 · Tailwind 4
│   ├── 编译为静态 HTML/CSS/JS (next build → out/)
│   └── 通过 rust-embed 内嵌到 mtagentlens 二进制
│
├── env/                           基准测试环境
│   ├── src/mtagentlens_benchmark/ Python 包 (CLI, Engine, Evaluator, Verifier)
│   ├── scenarios/                 164 个场景定义 (38 类别) + fixtures
│   ├── scripts/verifiers/         73 个场景验证器
│   └── runs/                      运行工件 (gitignored)
│
├── compare/                       实验与论文产出
│   ├── Dataset/FuxiBench/         86 场景冻结基准 + 完整场景目录
│   ├── results/paper/             主评估 · 消融 · 性能 · InjecAgent · LaTeX 表格
│   ├── results/external/          外部基线对比
│   └── scripts/                   build · audit · run · vis · perf
│
├── Dockerfile / Dockerfile.dev
├── Makefile
└── LICENSE (Apache-2.0 + BPF GPL 例外)
```

## 文档

| 文档 | 说明 |
|------|------|
| [架构设计](docs/architecture.md) | 系统架构全貌——eBPF → Rust → 因果分析 → 前端 |
| [实验设计](docs/design/experiment-design.md) | RQ 驱动的实验方案 |
| [评分引擎](docs/design/scoring-engine.md) | 6 维组合评分 · PBC 检测 · Platt 校准 |
| [演示视频说明](docs/MtAgentLens-演示视频导览.md) | 演示视频章节说明与评审阅读导引 |
| [SAFI 框架](docs/safi/README.md) | 安全不对称文件注入——攻击载荷迁移方法论 |
| [文档索引](docs/README.md) | 全部文档导航 |

---

## FuxiBench

FuxiBench 是 MtAgentLens 的评估基准，用于覆盖多 Agent 安全观测中的主要场景。全部 86 个场景以 Claude Code 为 Agent 运行时采集，涵盖单 Agent、Agent Teams、Subagents 三种协作模式。

| 维度 | 数据 |
|------|------|
| 总场景 | **86**（67 攻击 + 19 良性） |
| 攻击类别 | **14** |
| Type A（直接注入） | 10 — 攻击载荷在 task prompt 中 |
| Type B（间接注入） | 34 — 攻击载荷隐藏在 fixture 文件中 |
| Type C（多 Agent） | 23 — 跨进程协同攻击（**核心贡献**） |
| 标注 | 3 名独立标注者，交叉验证 + 共识裁判 |

**评估结果**：

| 指标 | 值 | 说明 |
|------|:--:|------|
| TPR | **0.896** | 67 攻击场景中检出 60 |
| FPR | **0.000** | 19 良性场景零误报 |
| F1 | **0.945** | |
| Observer 调用率 | **39.5%** | 34/86 场景，仅冲突信号时触发 |

四层消融实验表明，L2（PBC + Identity + Taint）是主要增益来源：移除文件读取 PBC 信号后，覆盖率从 1.000 降至 0.534（Δ = -0.466）。

详见 [`compare/Dataset/FuxiBench/SCENARIOS.md`](compare/Dataset/FuxiBench/SCENARIOS.md) 和 [`compare/results/paper/EXPERIMENTS.md`](compare/results/paper/EXPERIMENTS.md)。

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
| 吞吐量开销 | **<3%** | FakeRunner 合成 SSL 事件管线 |

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
make build-frontend     # 仅前端
make build-bpf          # 仅 eBPF (bpftools/)
make build-rust         # 仅 Rust (collector/)
make test               # 全量测试
make docker             # Docker 镜像

cd collector
cargo build --release   # → build/release/mtagentlens
cargo test              # 862+ 测试

cd frontend
npm run dev             # 开发服务器
```

---

## 环境要求

| 组件 | 要求 |
|------|------|
| Linux 内核 | 4.1+ (推荐 5.0+，需 `CONFIG_DEBUG_INFO_BTF`) |
| Rust | 1.88+ (edition 2024) |
| Node.js | 18+ |
| 构建工具 | clang, llvm, libelf-dev, zlib1g-dev, make |
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

本项目源代码采用 **Apache License 2.0**。详见 [LICENSE](LICENSE)。

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
