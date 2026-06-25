<p align="center">
  <h1 align="center">MtAgentLens</h1>
  <p align="center">
    <strong>零插桩 · 内核级 · 多 Agent 攻击可观测性</strong><br>
    <sub>从系统调用到攻击因果链——在 Agent 无法触及的 OS 边界看见一切</sub>
  </p>
  <p align="center">
    <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-blue"></a>
    <a href="#性能"><img src="https://img.shields.io/badge/probe_latency-~101ns-blueviolet"></a>
    <a href="#性能"><img src="https://img.shields.io/badge/overhead-<3%25-brightgreen"></a>
    <a href="#FuxiBench"><img src="https://img.shields.io/badge/FuxiBench-F1%3D0.945-orange"></a>
  </p>
</p>

---

## 问题

2026 年，LLM Agent 不再是一个进程。它们是**多进程、多身份、协同工作的软件系统**——Leader 分发任务给 Worker、父 Agent spawn 子 Agent、Agent 通过 MCP 协议调用外部工具。Claude Code 的 Agent Teams 和 Subagents 模式是这一趋势的代表。

当攻击发生时，应用层日志无法给出答案：

- Agent A 读取了一份"发布说明"文档，然后向外部服务器发送了 `POST /exfil`——它被间接注入了吗？
- Agent B 通过共享文件向 Agent C 传递了一条"配置更新"——这是正常的交接还是恶意指令？
- 三个 Worker 同时访问了 `/etc/shadow`——谁下的指令？污染源在哪里？

**应用层日志运行在 Agent 内部**——可以被修改、关闭、甚至从未被 Agent 框架生成。你需要一个运行在 Agent **之下**、攻击者无法触碰的观测层。

---

## MtAgentLens

MtAgentLens 在 **Linux 内核系统调用边界**通过 eBPF 观测一切。它不读 prompt、不依赖 Agent 框架、不修改任何代码。它看到的是 Agent 无法隐藏的事实——SSL/TLS 解密后的明文、进程的 `exec`/`fork`/`open` 序列、文件在进程间的传递路径、网络连接的源与目标。

然后它构建一张**跨进程因果责任图**——不仅告诉你"发生了什么"，还告诉你"谁引起的、经过谁、影响了谁"。

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
  |  /           Timeline (scrollable, 10+ event type renderers)    |
  |  /tree       Process tree (hierarchical visualization)          |
  |  /alerts     Alert panel (causal chain + evidence)              |
  |  /api/events SSE real-time event push                           |
  |  /api/graph  Responsibility graph REST API                     |
  +-----------------------------------------------------------------+
```

---

## 三层检测：为什么不是又一个 Prompt Scanner

LLM Guard、Meta Prompt Guard 等工具扫描 prompt 文本寻找恶意模式。这有三个致命盲区：

| 盲区 | 攻击实例 | MtAgentLens 如何应对 |
|------|---------|---------------------|
| **文本绕过** — 文言文、Unicode 同形字、语义遮蔽可轻易绕过文本分类器 | `prompt-injection-classical`（纯文言文·墨子·棋局对弈）被所有 prompt 工具漏检 | **L1** 不读 prompt 文本——直接在 uprobe 点拦截 SSL 明文，看到实际的 HTTP 请求体和响应 |
| **间接注入** — fixture 文件中的恶意指令不经过 prompt，文本扫描器无法看到 | `supplychain-malicious-build` 的 `build.sh` 中包含 `curl POST /exfil`，prompt 中只写"请运行构建脚本" | **L2** 追踪文件读取→进程执行→网络出站的完整链路，在进程边界跨越（PBC）时标记 |
| **多 Agent 协同** — 攻击指令通过共享文件、交接文档、委派令牌在进程间传递，没有单一文本可供扫描 | `leader-betrayal` 中 Leader 向 Worker 下达恶意指令，Worker 盲从执行 | **L2** 并查集身份解析 + TaintTracker 跨进程追踪污染的传播路径 |

**L3 Observer LLM** 仅在 L1/L2 出现矛盾信号时介入——例如 eBPF 看到一个进程读取了 `/etc/passwd`，但该进程的 HTTP 请求体中没有出现敏感字段。Observer 做语义仲裁，通过 **CachePrior** 自学习机制将历史结果缓存到 L1（精确匹配）和 L2（语义相似）两级缓存，大幅降低 LLM 调用率。

---

## 快速开始

```bash
git clone https://github.com/mtagentlens/mtagentlens.git --recursive
cd mtagentlens

# 安装依赖 (Ubuntu/Debian)
sudo apt-get install -y clang llvm libelf-dev zlib1g-dev make

# 全量构建 (BPF → 前端 → Rust)
make build

# 监控 Claude Code（已验证）
sudo ./collector/build/release/mtagentlens record -c claude \
  --binary-path ~/.local/share/claude/versions/$(claude --version | head -1)

# 浏览器访问 http://127.0.0.1:7395
```

> **当前验证状态**：MtAgentLens 已在 **Claude Code**（Bun 运行时，静态链接 BoringSSL）上完成全面测试。对于动态链接系统 `libssl.so` 的 Agent（大多数 Python/Node.js AI 工具），eBPF uprobe 机制在架构上同样适用，尚未系统性验证。对其他 LLM Agent 的实验性支持可通过 `-c <进程名>` 尝试——欢迎反馈测试结果。

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
| [SAFI 框架](docs/safi/README.md) | 安全不对称文件注入——攻击载荷迁移方法论 |
| [文档索引](docs/README.md) | 全部文档导航 |

---

## FuxiBench

> **伏羲**——上古圣人观天地万象而创八卦，以八种符号分类万物。FuxiBench 以同样的系统性观测精神，通过 OS 级 eBPF 信号将 LLM Agent 攻击面分解为可检测的维度。

**首个多 Agent LLM 攻击检测基准数据集**，专门设计用于暴露纯 prompt 检测工具的盲区。全部 86 个场景以 Claude Code 为 Agent 运行时采集，涵盖单 Agent、Agent Teams、Subagents 三种协作模式。

| 维度 | 数据 |
|------|------|
| 总场景 | **86**（67 攻击 + 19 良性） |
| 攻击类别 | **14** |
| Type A（直接注入） | 10 — 攻击载荷在 task prompt 中 |
| Type B（间接注入） | 48 — 攻击载荷隐藏在 fixture 文件中 |
| Type C（多 Agent） | 23 — 跨进程协同攻击（**核心贡献**） |
| 标注 | 3 名独立标注者，交叉验证 + 共识裁判 |

**MtAgentLens 在 FuxiBench 上的表现**：

| 指标 | 值 | 说明 |
|------|:--:|------|
| TPR | **0.896** | 67 攻击场景中检出 60 |
| FPR | **0.000** | 19 良性场景零误报 |
| F1 | **0.945** | |
| Observer 调用率 | **39.5%** | 34/86 场景，仅冲突信号时触发 |

四层消融实验确认 L2（PBC + Identity + Taint）是核心贡献源：移除文件读取 PBC 信号后覆盖率从 1.000 降至 0.534（Δ = -0.466）。

详见 [`compare/Dataset/FuxiBench/SCENARIOS.md`](compare/Dataset/FuxiBench/SCENARIOS.md) 和 [`compare/results/paper/EXPERIMENTS.md`](compare/results/paper/EXPERIMENTS.md)。

---

## 性能

微基准测试（Linux 6.17，本地 bpftool stat + pidstat）：

| 指标 | 数值 | 测量方法 |
|------|------|---------|
| exec 探针延迟 | **101.3 ns/op** | bpftool stat，配对壁钟 |
| 文件探针延迟 | **57.6 ns/op** | bpftool stat，配对壁钟 |
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
const SSLSNIFF_BINARY: &[u8] = include_bytes!("../../../bpftools/.output/bin/tls_capture");
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

本项目采用 **Apache License 2.0**。详见 [LICENSE](LICENSE)。

eBPF 内核程序（`bpftools/*.bpf.c`）按文件头 SPDX 声明使用 **GPL-2.0 OR BSD-3-Clause**——这是 Linux 内核 GPL-only BPF helper 的合规要求，不影响用户态代码的 Apache-2.0 许可。用户态 eBPF 加载器跟随 libbpf 的双许可证（**LGPL-2.1 OR BSD-2-Clause**）。
