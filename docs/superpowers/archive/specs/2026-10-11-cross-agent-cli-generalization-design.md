# MtAgentLens 跨 Agent CLI 通用化设计（v2）

- 日期：2026-10-11
- 状态：待评审
- 目标部署环境：使用 OpenClaw（非 Claude Code）的单位环境
- 复杂度定级：**重量级**（触及 collector 核心归因算法、持久化 schema、前端归因链、bpftools）
- v2 变更说明：v1 的三处核心假设经代码核实与真实环境实测**被证伪**，本版据实重写（见 §0 修订记录）

## 证据等级标注约定

| 标记 | 含义 |
|------|------|
| 【实测】 | 2026-10-11 在本机真实 OpenClaw 环境运行采集器所得 |
| 【代码核实】 | 逐行核实过 `file:line`，非推测 |
| 【文档】 | 来自官方文档/规范原文 |
| 【待验证】 | **尚未证实**的假设，实现前必须先用最小成本实验证实或证伪 |

---

## 0. v1 → v2 修订记录（被证伪的假设）

v1 曾声称以下三点，经核实与实测**全部不成立**。此处保留记录，避免后续再犯同类错误。

| v1 的说法 | 实际情况 | 证据 |
|-----------|----------|------|
| 「`cgroup_id` 已在 eBPF 层采集（`process.h:38`）」 | 该字段只是结构体声明，`process.bpf.c` 从未填充；`enrichment.h` 位于未接入主程序的 `process_ext/`。实测 `process` 输出字段无 `cgroup_id` | 【实测】+【代码核实】 |
| 「`IdentityResolver` 依赖 `root_pid`/`tls_session`/`conversation_id` 三信号」 | 实际只用 **TLS 会话共享 / 进程树祖先链 / `comm ∈ AGENT_COMMS`**。`conversation_id` 不是身份信号（在线路径从不设置）；`root_pid` 只被 legacy analyzer 使用 | 【代码核实】`resolver.rs:76-200` |
| 「`tls_session` 因连接池复用而可能相同」 | 更糟：`tls_capture` **不输出任何连接标识**，`routing.rs:111` 逐事件回退 `TlsSessionId::new()` → **每事件一个随机 UUID**。唯一那处 `uf.union`（`union_find.rs:213-221`）在生产中**从不触发** | 【实测】15 事件 = 15 个互异 UUID |
| 「L2 作为新 `IdentityProvider` 实现接入，内核归因代码不改」 | trait 是 `agent_for_process(ProcessId) -> Option<AgentId>` 单值映射，无法表达"同一 ProcessId 对应多个 agent"；调用点也只传 `ProcessId` | 【代码核实】`resolver.rs:32,35`、`graph_builder.rs:126,481,545` |
| 「边界规则与 `is_subagent_boundary` 同构扩展」 | `is_subagent_boundary`（`union_find.rs:103`）是**死代码**（生产无调用，仅 4 个单测）。真正的边界抑制**内联**在 `run_authoritative_resolution` 的 5 处合并动作里 | 【代码核实】 |
| 「`record` 的 `-c` 有默认值 claude」 | `comm: String` 无 `default_value` ⇒ clap 视为必填，**没有默认值**；"defaults to claude" 只是与代码不符的陈旧注释 | 【代码核实】`main.rs:296-298` |

---

## 1. 背景与问题

MtAgentLens 要部署到使用 **OpenClaw** 的单位环境。两者架构根本差异：

| 维度 | Claude Code | OpenClaw |
|------|-------------|----------|
| 进程形态 | 每会话一个短命 CLI 进程 | **一个长驻 Gateway 进程**，内部跑 N 个 agent |
| Agent 粒度 | 1 进程 = 1 agent | **1 进程 = N 个 agent**（`agents.entries.<id>`，按 session 路由）【文档】 |
| 身份信号 | `comm == "claude"` | comm 无区分度（见 §3.1） |
| 后端协议 | Anthropic Messages | 6 种协议任选（含 OpenAI 兼容）【代码核实】 |

现有身份判定的唯一入口是两份重复定义：

```rust
// collector/src/responsibility/identity/union_find.rs:30
// collector/src/responsibility/signals/signal_snapshot.rs:203（第二份副本）
pub const AGENT_COMMS: &[&str] = &["claude", "claude-code"];
```

**这不是"缺个白名单"。** Claude Code 恰好让「进程」与「agent」合一，OpenClaw 把它们解耦；任何基于 comm 白名单的补丁都会在下一个 agent CLI 上再次坍塌。

---

## 2. 调研结论（学术与工业现状）

【文档/论文】深度调研（学界 + 工业标准 + OpenClaw 实现）结论：

1. **学术界没有任何顶会论文解决「同一 OS 进程内多个逻辑 agent 的零插桩归因」**。AgentSight（arXiv:2508.02736，PACMI'25，MtAgentLens 的学术原型）的归因依赖**进程谱系**（fork/execve 血缘树），共享进程会让该信号必然折叠；其多子代理案例未说明区分机制。
2. **工业标准（OTel GenAI / OpenInference / Langfuse / LangSmith / Weave）全部是"agent 自报身份 + in-band 传播"范式**，对零插桩不可用——它们给出的是**命名法，不是检测法**。
3. 传统 provenance 系统（SPADE/PASS/LPM/CamFlow）实体粒度是 **OS 进程**，不存在"逻辑 agent"概念；除 SPADE 外均已停止维护。
4. **破局点在 LLM 请求体本身**：system prompt 是共享进程内唯一「每请求都携带、且语义指向具体 agent」的信号。

### 2.1 现有身份机制在生产中的真实行为【代码核实】

`IdentityResolver` 的实际输入是三项：**TLS 会话共享**、**进程树祖先链**、**`comm ∈ AGENT_COMMS`**。

并查集的 **seed 定义**是「拥有 ≥1 个 TLS 会话的**进程**」（`union_find.rs:181-200`）。全文件**唯一**的 `uf.union` 调用（`:213-221`）只在"两个 seed 进程共享至少一个 TLS 会话"时触发。

由于 `tls_capture` 不输出连接标识、`TlsSessionId` 逐事件随机（`routing.rs:109-114`、`runtime.rs:485-490`），**两进程共享同一 `TlsSessionId` 在数学上不可能发生** ⇒ 该 `uf.union` 永不触发，`is_subagent_boundary` 的 `shared` 判定永不成立。**整个"三信号"算法在生产中退化为单信号（进程树 + comm）。**

---

## 3. 实测证据（本机真实 OpenClaw 环境）

2026-10-11 在本机运行中的 OpenClaw（Gateway PID 280268，Node v24.19.0，端口 18789，provider `deepseek/deepseek-v4-pro`）实测。

### 3.1 进程形态【实测】

```
280268  MainThread  node …/openclaw/dist/index.js gateway --port 18789
280482  MainThread  node …/openclaw/dist/process/spawn-broker/worker.js
280496  MainThread  node …/openclaw/dist/infra/sqlite-readonly-location.worker.js
        /proc/280268/cgroup → 0::/user.slice/…/app.slice/openclaw-gateway.service
```

- **comm 全是 `MainThread`**（Node 主线程名；机器上 8 个进程都如此）→ `-c claude` / `-c node` **全部失效**
- **cgroup 精确区分**：Gateway = `openclaw-gateway.service`（`/sys/fs/cgroup` 下 inode `152359` = cgroup v2 id，与 `bpf_get_current_cgroup_id()` 语义一致）
- **Claude Code 的 cgroup 不可作身份**：`…/vte-spawn-<uuid>.scope`，**每终端会话都不同** → 反证两层信号的必要性

### 3.2 cgroup 采集现状【实测 + 代码核实】

`process/process.h:38` 的 `cgroup_id` / `parent_cgroup_id` **只是声明**，`process.bpf.c` 只 include `process.h`、从未填充。实测 `process` 输出字段：

```
EXEC:      comm, event, filename, full_command, parent_start_time_ns, pid, ppid, start_time_ns, timestamp
FILE_OPEN: comm, count, event, filepath, flags, parent_start_time_ns, pid, start_time_ns, timestamp
EXIT:      comm, event, exit_code, parent_start_time_ns, pid, ppid, start_time_ns, timestamp
```

**结论**：`cgroup` 需新增采集路径（用户态读 `/proc/<pid>/cgroup` 即可，见 §5 L1）；`full_command`（=cmdline）与 `filename`（exe 路径）**已在 EXEC 事件中现成可用**。

### 3.3 TLS 抓取可行性【实测】

Node v24 静态链接 OpenSSL **但导出全部符号**：`SSL_write` / `SSL_read` / `SSL_write_ex` / `SSL_read_ex` / `SSL_do_handshake`（GLOBAL DEFAULT），**另有 `SSL_write_ex2`（当前未挂载）**。→ 走**符号 uProbe 即可**，不需要 Claude Code 那套 BoringSSL 字节模式逆向。

### 3.4 协议失配【实测 + 代码核实】

OpenClaw 内建 6 种协议：`openai-responses` / `openai-completions` / `anthropic-messages` / `google-generative-ai` / `bedrock` / `azure`。本机 `deepseek-v4-pro` → `isDeepSeekV4OpenAICompletionsModel()` ⇒ **OpenAI Chat Completions**。

实测请求：

```
POST /chat/completions HTTP/1.1
host: api.deepseek.com
traceparent: 00-dbfd23e4411c3fe6c98f1c5af6f33ec3-…    ← W3C Trace Context
user-agent: OpenAI/JS 7.23.0
content-length: 47526
```

响应为 chunked `text/event-stream`，结束标记是 **`data: [DONE]`**，**无 `message_stop`**。

> ⚠️ **实测修正（v2 追加）**：本条**不导致意图丢失**。`IntentExtractor::extract` 的 **Step 0 是 SSE fast-path**（`intent_extractor.rs:242-271`）：`looks_like_sse(body)` 成立时直接依次调用 `extract_from_sse`（Anthropic）与 `extract_from_openai_sse`（OpenAI），**完全绕过 SSEProcessor 的 finalize**；而 `routing.rs:165` 的 `body_candidate` 链最后一项直接取 **raw SSL 明文**（`event.data.data`）。
> 实测结果：`intents.authority_source` = `RequestHistoryAssistantToolUse` **6** 条 + `SseResponseToolUse` **8** 条，**两条路径均工作**。故 L3 的真实影响是**聚合能力降级**（§5 L3），**不是功能丧失**。

### 3.5 system prompt 指纹【实测】

- system prompt = **31,820 字符**；请求体 48,118 字节，**单条 SSL_write 事件**（未分片）
- OpenClaw **自己标注稳定边界**：

```
<!-- openclaw:attempt:STABLE -->    行 1
   … Workspace / Project Context / AGENTS.md / IDENTITY.md / SOUL.md / USER.md
<!-- /openclaw:attempt:STABLE -->   行 428   ← STABLE 区 26,829 字符 (84%)
<!-- openclaw:attempt:DYNAMIC -->   行 429
   … Temporal Context（日期）/ Runtime attempt 状态
<!-- /openclaw:attempt:DYNAMIC -->  行 475   ← DYNAMIC 区 4,926 字符 (15%)
```

- 身份锚点全在 STABLE 区内：
  - `## Workspace` → `Working directory: /home/lzk/.openclaw/workspace`
  - `# Project Context` 注入 4 个**绝对路径**：`workspace/{AGENTS,IDENTITY,SOUL,USER}.md`

### 3.6 指纹可区分性与稳定性【实测】

创建第二 agent（`probe`，workspace `~/.openclaw/workspace-probe`）后：

| 验证项 | 结果 |
|--------|------|
| main STABLE 指纹 | `sha256:50b5ceca7c52ac88`（26,829 字符） |
| probe STABLE 指纹 | `sha256:877b4276ecf2ac39`（33,539 字符） |
| **跨 agent 可区分** | ✅ |
| **同 agent 跨请求稳定** | ✅ probe 两次完全一致 |
| Workspace 段 | `…/workspace` vs `…/workspace-probe` |
| 注入文件 | main 4 个；probe 5 个（多 `BOOTSTRAP.md`） |

**重要精化**：STABLE 区差异不仅来自 workspace，也来自 **skills 可见性**（main 有 `custodian-skills`，probe 没有）。因此**整体 hash 不宜直接作身份键**——任何 prompt 变化都会使同一 agent"变成另一个"。正确做法是**结构化提取 `workspace` 路径作主键，整体 hash 作辅助校验**。

### 3.7 端到端归因实测：多 agent 未被分离【实测】

用现有 release 二进制对真实 OpenClaw 跑一次采集（`trace --ssl=true --process=true --binary-path <node v24>`），期间分别触发 `main` 与 `probe` 两个 agent 的真实 LLM 调用。采集器落库后判读：

| 判读项 | 实测值 | 含义 |
|--------|--------|------|
| `agent_identities` | **1 行**，`process_ids_json = [{"pid":280268,…}]` | **两个 agent 被合并成一个** |
| `identity_process_map` | 152 行，PK = `(pid, start_time_ns)` | 一进程一 agent 的单值结构 |
| `subagent_boundaries` | **0 行** | 边界规则**无对象可作用** |
| `tls_sessions` | 15 条事件 = **15 个互异 UUID**，全挂 pid 280268 | TLS 信号在生产中是死的 |
| `intents` | 14 | 意图抽取确有发生（数据链本身通） |

**这是本设计最重要的实证**：它同时证实了 §2.1 的机制推断，并证明**"加边界规则"路线在单进程 N agent 场景下无效**。

### 3.8 因果关联层实测：intent → action 关联失败【实测】

同一轮采集的其余判读：

| 项 | 实测值 | 含义 |
|----|--------|------|
| `correlation_evidence` | **0 条** | 因果链**完全没建立** |
| `non_link_diagnostics` | 20 条，reason = `NoArgumentMatch`（多数）/ `IntentNotPrimaryEligible` | 意图与动作**匹配不上** |
| `identity_refinements` | 1 条，类型**全为 `Confirmed`** | 证实 `Merged`/`Split` 从未产出 |
| `provisional_identities` | 103 条，`primary_signal` **全为 `Unknown`**，confidence 0.25 | 无任何进程被识别为 agent |

**三项独立含义**：

1. **`correlation_evidence = 0` 是一个独立于身份问题的缺陷。** `NoArgumentMatch` 指意图声明的参数与实际 syscall 参数对不上——很可能因为 OpenClaw 的工具是 **Code Mode（`exec` 执行 JavaScript）**，其真实 syscall 行为与 LLM 声明的参数不直接对应。而 `intent → action` 的因果关联**正是多 agent 攻击检测的核心**。⇒ **即便 L2 完美落地，攻击链仍建立不起来。此层为 v2 早期稿完全未覆盖，须作为独立分期项。**

2. `identity_refinements` 全为 `Confirmed` ⇒ L2 需**同时补** `Split`/`Reassigned` 的产出逻辑，否则"禁止合并"在 refinement 模型里**没有表达位置**。

3. `primary_signal` 全为 `Unknown` ⇒ comm 不匹配的直接后果，与 §3.7 同源。

---

## 4. 设计目标与非目标（v2 已修订）

### 目标

1. **架构级通用化**：任意 agent CLI 通过一份声明式 profile 接入
2. **正确支撑多 agent 攻击检测**：同进程内 N 个 agent 必须被正确分离，**绝不退化合并**
3. **不破坏现有架构统一性**：Claude Code 路径行为不变，现有测试与 FuxiBench 评估零回归
4. **静默失效转为显式降级**：抓不到 / 识别不了 / 被淘汰时必须告警
5. 对外命名具备对齐 OTel GenAI 的能力（**受 §5 L5 的落地位置约束，见该节**）

### 非目标

- 不做 OpenClaw 的 in-band 集成（plugin hook / OTLP 接收）—— 坚持零插桩
- 不做 profile 的动态热加载
- 不做跨主机的分布式归因
- ~~不重写归因算法~~ ← **v2 撤销**。§3.7 实测证明 seed 单位必须从 `ProcessId` 改为 `(ProcessId, SessionKey)`，这已是重写算法 Step 1/2。详见 §5 L2。

---

## 5. 架构

```
┌─ L0a 前置：采集可用性 ─────────────────────────────────────┐
│  符号挂载 best-effort + 失败告警；cgroup/cmdline 贯通到 Rust │
└───────────────────────┬───────────────────────────────────┘
┌─ L0b 前置：传输层分片正确性 ───────────────────────────────┐
│  ConnectionBuffer 按连接（非 tid）分键；补会话标识           │
└───────────────────────┬───────────────────────────────────┘
                        ▼
┌─ L0 Agent Profile 层（声明式）────────────────────────────┐
└───────────────────────┬───────────────────────────────────┘
                        ▼
┌─ L1 运行时身份 RuntimeIdentity ─「谁在跑」────────────────┐
│  cgroup unit > cmdline > exe basename > comm > 父进程链     │
└───────────────────────┬───────────────────────────────────┘
                        ▼
┌─ L2 会话身份 AgentIdentity ─「哪个 agent 在说话」★核心─────┐
│  seed 单位改为 (ProcessId, SessionKey)                      │
│  主键 workspace 路径；加固文件路径；强键 traceparent        │
│  IdentityProvider 上下文化扩展 + 持久化/前端归因链改造      │
└───────────────────────┬───────────────────────────────────┘
                        ▼
┌─ L3 协议适配 Provider-Agnostic ───────────────────────────┐
│  OpenAI 累积路径 + finalize 策略表 + 工具名归一化           │
└───────────────────────┬───────────────────────────────────┘
                        ▼
┌─ L4 误报治理 + L4b 降级可观测性 ──────────────────────────┐
└───────────────────────┬───────────────────────────────────┘
                        ▼
┌─ L5 对外命名 ── 见 §5 L5（当前无落地位置，建议降级）────────┘
```

### L0a：采集可用性（**新增前置，必须最先做**）

1. **符号挂载改为逐符号 best-effort + 汇总失败告警**
   现状【代码核实】：`tls_capture.c:353-369` 的 `attach_openssl` 内部用 `ATTACH_UPROBE_CHECKED` → `:38-43 __CHECK_PROGRAM` 失败即 `perror` + `return -errno`；且调用处 `:687-691` **丢弃返回值**。
   ⇒ **不能把 `SSL_write_ex2` 直接追加进现有 CHECKED 链**：任一符号缺失会提前 return，导致排在后面的符号（含 `SSL_do_handshake`）全部不挂载。必须实现为"逐符号探测 + 汇总告警"。

2. **`cgroup` / `cmdline` 贯通到 Rust 侧**（当前未贯通）
   现状【代码核实】：`ProcessMetadata`（`signal_snapshot.rs:13-32`）只有 `process_id/comm/pgid/first_seen/last_seen/exit_time/exit_code/is_alive`；`observe_exec`（`signal_graph.rs:186-192`）只收 `comm + pgid`。
   ⇒ 需新增一条 `eBPF JSON → runner 解析 → ProcessMetadata → observe_exec` 的字段贯通链路。`full_command`/`filename` 已在 EXEC JSON 中（§3.2），**只需贯通无需改 eBPF**；`cgroup` 由用户态读 `/proc/<pid>/cgroup` 获得（**零 eBPF 改动**）。
   ⚠️ `/proc/<pid>/cgroup` 在进程退出后消失 → 在 EXEC 事件到达时即读取并缓存 `pid → runtime`。

3. **`-c` 语义修正**：`comm` 对 OpenClaw 无效（`MainThread`），选择器需支持 cgroup / cmdline / binary-path。

### L0b：传输层分片正确性（**新增前置，L2/L3 的共同依赖**）

**缺陷**【代码核实 + 部分实测】：`connection_buffer.rs:80-100` 用 **`tid`** 作 buffer key 重组 SSL_write 分片。Node 是单线程事件循环，同一 tid 上可能有多个 socket 并发 ⇒ `bufs.entry(tid).extend_from_slice()` 会把不同连接的请求体**粘在一起**。Claude Code 每会话一进程、并发度低，所以该缺陷从未暴露。
✅ **并发实测已复现（2026-10-12）**：6 路并发调用（3×main + 3×probe，`start` 时间戳完全相同）产生 1432 条 SSL 事件，集中在**仅 2 个 tid** 上；`tid=280268` 承载 **11 个请求首行**。用真实数据**逐行复刻本文件算法**（`buffers[tid] += data` → 遇响应 `pop`）后：吐出 17 个合成请求事件，**仅 4 个可解析为合法单一 JSON，13 个解析失败**；其中 1 个 body 内嵌 **2 个 `{"model"`**，原始数据含 **6 处 `}{`**。
⇒ **判定：串包必然发生，L0b 为【必须前置】。**

**修法**：按连接而非 tid 分键。但 `tls_capture` 当前**不输出任何连接标识**（§3.2 字段清单），故需先补会话标识输出（`SSL*` 指针或 `(pid, fd)` 组合）；过渡期可用 `(pid, function, 首行)` 兜底。

【待验证】补标识后 OpenClaw 请求体能否被完整解析（验收见 §8）。

### L0：Agent Profile 层【待验证：profile schema 的最终字段集】

声明式 profile（内置默认 + 用户可覆盖），字段示意：

```toml
[profile.openclaw]
runtime.cgroup_unit_pattern = "openclaw-*.service"
runtime.cmdline_contains     = "openclaw/dist/index.js gateway"
runtime.exe_basename         = ["node"]

tls.attach      = "exe_symbols"
tls.symbols     = ["SSL_write","SSL_read","SSL_write_ex","SSL_read_ex","SSL_do_handshake","SSL_write_ex2"]
tls.fail_policy = "warn_per_symbol"     # 逐符号 best-effort，汇总告警（不可用 error-early）

protocol.accept    = ["openai-completions","openai-responses","anthropic-messages"]
protocol.api_paths = ["/chat/completions","/responses","/messages"]

identity.primary_key   = { type = "prompt_field", marker = "Working directory: ", normalize = "workspace_path" }
identity.stable_zone   = { begin = "<!-- openclaw:attempt:STABLE -->", end = "<!-- /openclaw:attempt:STABLE -->" }
identity.corroborators = [ { type = "path_prefix", pattern = "~/.openclaw/agents/{agentId}/" },
                           { type = "header", name = "traceparent", bind = "run" } ]

infrastructure_paths = ["~/.openclaw/", "/tmp/openclaw-*", "**/openclaw-agent.sqlite"]
```

`profile.claude-code` 保留现有行为，确保零回归。

### L1：运行时身份

- 从 `AGENT_COMMS` 抽象为 **`RuntimeResolver`**，按 profile 声明的优先级解析：`cgroup unit` > `cmdline` > `exe basename` > `comm` > 父链
- 信号可用性：`comm` 已有；`full_command`/`filename` 已在 EXEC JSON（需贯通，见 L0a）；`cgroup` 用户态读 `/proc`（零 eBPF 改动）
- **消除 `signal_snapshot.rs:203` 的第二份 `AGENT_COMMS` 副本**

### L2：会话身份（核心，范围已扩容）

**两层模型**：`RuntimeIdentity`（进程级）× `AgentIdentity`（会话级）。

**★ 算法级改动（不可回避）**

seed 定义必须从「拥有 TLS 会话的**进程**」改为「**`(ProcessId, SessionKey)`**」。理由：§3.7 实测——N 个 agent 共享一个 Gateway 进程时 `seed_processes.len() == 1` ⇒ 恰好 1 个 component ⇒ **没有第二个 component 可供"禁止合并"**。

**"禁止合并"必须落在 5 处合并动作上**（只改 `is_subagent_boundary` 等于没改，因为它是死代码）：

| 位置 | 语义 |
|------|------|
| `union_find.rs:213-221` | 共享 TLS 的 seed 合并（唯一 `uf.union`） |
| `:297-299` | 无 TLS 的工具子进程并入父 agent |
| `:310-314` | 有 TLS 且与父共享 → **force-assign 并入父** |
| `:345-347` | 有 TLS 不共享、非 agent → 并入父 |
| Step 4 上行传播（`~:395-400`） | 未分配进程并入已分配的祖先 |

**主键提取**（零插桩可得）：
1. **主键**：system prompt 稳定区内的 `Working directory: <path>` → 归一化 `workspace_path`
2. **加固**：per-agent 文件访问路径（`~/.openclaw/agents/<id>/…`）
3. **强键**（若存在）：`traceparent` trace-id、`x-session-id`
4. **辅助校验**：稳定区整体 hash，检测"同 workspace 但配置漂移"

**身份诚实性**：推断出的会话身份标 `provenance = inferred`；显式字段来源标 `asserted`。

**★ `IdentityProvider` 必须扩展**（v1 的"内核不改"不成立）
新增带上下文的方法（保留旧方法作默认实现以兼容 replay/mock）：

```rust
fn agent_for_subject(&self, process_id: ProcessId, session_key: Option<&SessionKey>) -> Option<AgentId>;
```

需同步改动：`graph_builder.rs:126-127`（intent/action 两处）、`:481`（handle_intent_pbc）、`:545`；`replay/executor.rs:626` 的实现；`graph_builder.rs:1116` 的 mock。

**★ 单值结构需一并改造**（否则分离结果会被静默覆盖）

| # | 位置 | 形态 | 后果 |
|---|------|------|------|
| 1 | `resolver.rs:32,35` | `agent_for_process` / `all_assignments` | 一进程只能映射一个 agent |
| 2 | `union_find.rs:236` | `assignments: HashMap<ProcessId, usize>` | 组件划分最小单位是进程 |
| 3 | `union_find.rs:152-166` | `derive_agent_id(&[ProcessId])` | 无法把会话编进 ID，需加 salt |
| 4 | `schema.rs:137-143` | `PRIMARY KEY(pid, start_time_ns)` + 单 `agent_id`；`writer.rs:867 INSERT OR REPLACE` | N agent/进程时**后写覆盖先写** |
| 5 | `raw-event-browser.ts:59,99` | 前端按 `pid + start_time → agent_id` JOIN | UI 无法展示会话级分离 |
| 6 | `multi_agent_anomaly.rs:80-92,797` | 自建 `pid_root` / `format!("agent-{}", root_pid)` | **独立塌缩**，即使 L2 修好它仍会出错 |

**建议**：新增 `RuntimeId(String)` 与 `SessionKey(String)` 两个 newtype；`AgentId` **保持不动**（已是 `newtype_id!` 生成的 `AgentId(pub String)`，全仓无 `agent-` 前缀解析逻辑，可承载 `openclaw/main`）。派生公式改为 `{runtime}/{session_key}`（Claude Code profile 下退化，保证零回归）。

**类型系统**：`SubagentBoundaryReason`（`identity_types.rs:52-57`）**新增**变体（如 `IndependentSession`）而非改名——生产从不构造 `ClaudeChildOfClaude`/`InfrastructureSpawn`，新增零风险；改名会与历史 DB 字符串混杂。注意 `IdentityRefinementType` 的穷尽匹配在 `replay/evaluation.rs:268-272` 需同步；并需补 `Split`/`Reassigned` 的产出（`union_find.rs:466-483` 目前清一色 `Confirmed`）。

### L3：协议适配（**v2 重新定级：能力恢复，非致命断点**）

**先说清楚 L3 不是什么**（v1/v2 早期稿的错误断言，已由实测推翻）：**不是"意图全丢"**。`IntentExtractor` 的 SSE fast-path 已覆盖 OpenAI 格式（见 §3.4 修正框），实测两条提取路径均工作。**L3 的目标是恢复 SSEProcessor 的聚合能力**，其失效影响的是聚合产物而非意图提取本身。

**受影响的下游**（【代码核实】，需在实现时逐一确认影响面）：`SSEProcessor` 产出的 `source="sse_processor"` 事件携带 `json_content` / `text_content` / `start_time`-`end_time` 时序 / `event_count` / `has_message_start`。这些被 `conversation_flow` 分析、token/时长统计与前端展示消费；OpenAI 协议下该事件**产不出来**。

**具体失效点**【代码核实】——SSEProcessor 内有三处 Anthropic 事件名硬编码：

| 位置 | 问题 |
|------|------|
| `sse_processor.rs:260-300` `has_meaningful_content` | 只认 `accumulated_text`/`accumulated_json` 或 `content_block_delta`/`message_start` |
| `:330-430` `accumulate_content` | 只在 `content_block_delta`（`text_delta`/`thinking_delta`/`partial_json`）下累积；OpenAI 的 `choices[].delta.content` / `delta.tool_calls` **一条都不会被累积** |
| `:560-600` `has_content_potential` | 按 `event.event` 字段分类，而 OpenAI SSE **没有 `event:` 行**（`event=None`），全部落入兜底 |

⇒ 即便 `[DONE]` 触发 finalize，`has_meaningful_content` 仍返回 false，**聚合事件**在 `:701-703` 被**静默丢弃**（注意：此丢弃不影响 fast-path 的意图提取，但会使该事件对下游"消失得无声无息"，正是目标 4 要治理的形态）。

**必须实现**：① OpenAI 累积路径（`delta.content` 拼接、`delta.tool_calls` 按 index 累积）；② finalize 策略表（`message_stop` / `[DONE]` / `response.completed` / 空闲超时）；③ 工具名表 profile 化 + 未知工具保留原名。
⚠️ **finalize 兜底与 preset filter 冲突**：`record` 预设的 `data=0\r\n\r\n`（经 `ssl_filter.rs` 转义为 chunked 终止块）会**删掉**"chunked 终止"这个兜底信号本身——二者只能取一。

**已有基础**：`extract_from_openai_json`（`intent_extractor.rs:558`）与 `extract_from_openai_sse`（`:631`）**实现完整**（含 `arguments` 二次 parse、SSE delta 按 index 累积），需补齐的是接入与归一化。

### L4：误报治理（**范围已扩大**）

| 位置 | 问题 |
|------|------|
| `graph_builder.rs:869-878` | `AGENT_INFRASTRUCTURE_PREFIXES`（Claude 专属） |
| `graph_builder.rs:947-953` | `WORKSPACE_PATH_PREFIXES`——含 `/tmp/claude-` 与**硬编码 `/home/lzk/MtAgentLens/collector/target/`**（换机必失效的既有 bug） |
| `policy.rs:454-490` | `is_safe_internal` 白名单 |
| **`policy.rs:492-509`** | **`SENSITIVE` 裸子串表**（`credentials`/`endpoint`/`exfil`/`/.env`/`/proc/`/`/root/`）——**OpenClaw 的最大误报源**，v1 遗漏 |
| `correlator_legacy.rs:1171` | Claude Edit 临时文件后缀 `{target}.tmp.{pid}.{timestamp}` |

⚠️ **`HOME` 陷阱**【代码核实】：`is_safe_internal` 用 `std::env::var("HOME")`，而采集器在基准中是**经 sudo 拉起**（`collector_manager.py:657`），此时 `HOME=/root` ⇒ 针对被观测用户 home 的豁免**静默失效**。应改为从 `process_events.uid` / `/etc/passwd` 解析被观测用户的 home。

### L4b：降级可观测性（**新增，建议与 L0a 合并**）

1. **`record`/`trace` 预设 filter 修正**【代码核实】：`http_filter.rs:350-360` 对 `body` 求值走 `body.contains(value)`，而预设 `response.body=` 的 value 为空串 ⇒ `"".contains("")` **恒真**，且整个表达式是 **OR** ⇒ **每一条 `message_type == "response"` 的事件都被丢弃**。这解释了为何无人察觉：Claude Code 的 tool_use 主要来自 `source="sse_processor"`（不受 HTTPFilter 影响）。对 OpenClaw 则是响应数据**双重死亡**。
2. **SSEProcessor 30 秒静默淘汰告警**【代码核实】：`sse_processor.rs:543` 的 `buffers_lock.retain(...)` 丢弃累积内容且**无任何输出**，与目标 4 直接冲突。
3. **`routing.rs:276` 判否时计数 + 告警**：当前 `if has_agent` 不成立时 action/evidence 被静默丢弃且无反向计数器。

### L2b：带外数据源（**v2 新增，2026-10-12 实测验证**）

**背景**：v1/v2 早期稿把 L2 身份与 P2 因果关联分别设计成「system prompt 指纹推断」与「Code Mode 语义解析」。实测推翻了这两个前提——**agent 自己把结构化的 ground truth 落盘了**。

**实测证据**（触发一次工具任务后，读 `~/.openclaw/agents/<id>/agent/openclaw-agent.sqlite` → `trajectory_runtime_events`）：

```json
{"type":"tool.call", "ts":"2026-10-12T06:09:58.375Z",
 "sessionKey":"agent:main:main", "runId":"1fa1bfe5-…",
 "workspaceDir":"/home/lzk/.openclaw/workspace",
 "data":{"toolCallId":"tool_call:call_00_ET_…:ls:1",
         "name":"ls", "args":{"path":"/home/lzk/MtAgentLens/collector/src/responsibility/runtime"}}}
```

**一条记录同时给出四样东西**：

| 用途 | 字段 | 价值 |
|------|------|------|
| **意图（P2）** | `data.name` + `data.args` | **结构化**，现有 `extract_intent_args` 恰好认识 `path` 字段 → **现有 L1–L5 匹配器直接可用** |
| **身份（L2）** | `sessionKey` = `agent:main:main` | **`asserted`**（agent 自报），优于指纹推断的 `inferred` |
| **时间对齐** | `ts` | ISO-8601，可与 eBPF 的 epoch-ns 对齐 |
| **运行分组** | `runId` | 会话级/运行级聚合键 |

**⇒ P2 从「学术界无成熟解法的研究难题」降级为「一个数据源接入」**：不需要解析 JS 代码、不需要语义相似度、不需要 taint 追踪。`exec` 的 `code` 字段被 redact（`[Malfo…ted]`）**完全不影响**——一个 `exec` 展开的每个嵌套调用都被单独记录。

**架构定位（关键）**：

```
数据源层   ├─ eBPF（通用基座）      → 所有 agent CLI
          └─ 带外读取（精确模式）  → profile 声明的 agent（当前仅 OpenClaw）

意图层     ├─ eBPF TLS 明文 → IntentExtractor（现有，通用）
          └─ 带外 tool.call → 结构化 Intent（新增，精确）

身份层     ├─ eBPF 进程/TLS 信号（现有）
          ├─ system prompt 指纹（inferred）
          └─ sessionKey（asserted，带外）  ← 最高优先级
```

**设计约束**：
1. **profile 驱动**：只有 profile 声明支持带外读取的 agent 才启用（避免与未知格式耦合）
2. **来源标注**：每份 Intent/Identity 标 `source`（ebpf / out_of_band）与 `provenance`（asserted / inferred）；**asserted 优先，但不覆盖 eBPF 的独立观测**（两者互为交叉验证）
3. **不做唯一依赖**：eBPF 路径必须独立可用；带外缺失时降级而非失败
4. **版本漂移防御**：读取前校验 `traceSchema`/`schemaVersion` 字段，不匹配时告警并退回 eBPF 路径

**已知风险**：平台耦合（OpenClaw 文件格式升级即可能失效）；需在 profile 里记录已适配的 schemaVersion。

**其他可用表**（同库）：`transcript_events`（树形 `id`+`parentId`）、`session_transcript_archives`、`session_nodes`、`conversations`。

> **查询陷阱**：`trajectory_runtime_events.seq` 是**每 session 独立**的计数，跨 session 用 `order by seq desc` 会拿到旧 session 的记录。必须按 `created_at` 排序。

### L5：对外命名（**建议降级为待定**）

【代码核实】全仓 grep `gen_ai` / `otel` / `langfuse` / `openinference` 在 `collector/src`、`frontend/lib`、`env/src` **零命中**；`responsibility/output.rs` 只是文本摘要 formatter。**"导出层"不存在**，L5 是净新增模块且当前没有确定的消费方。建议本轮降级为非目标，或先明确 exporter 契约与消费方。

---

## 6. 改造清单

### 必须修改项（blocking）

| # | 项 | 依据 |
|---|----|------|
| 1 | 撤销"L2 = 新增 trait 实现、内核不改"；扩展 `IdentityProvider` + 改 `graph_builder.rs:126/481/545` 三调用点 + `replay/executor.rs:626` + mock | 【代码核实】 |
| 2 | "禁止合并"落在 **5 处**合并动作；**seed 单位改为 `(ProcessId, SessionKey)`** | 【实测 §3.7】+【代码核实】 |
| 3 | 重写 §2.1 三信号描述与失效理由 | 【代码核实】 |
| 4 | `ConnectionBuffer` 按 `tid` 分键的串包缺陷 → 改为按连接分键（**条件性**：严重度取决于 §7.1 并发验证结果） | 【代码核实】+【部分实测】 |
| 5 | 修 `record` 预设 filter：删 `response.body=`；`data=0\r\n\r\n` 与 L3 兜底二选一 | 【代码核实】 |
| 6 | `identity_process_map` 加 `agent_key` 并改 PK + 前端 JOIN 同步（含 schema 版本迁移） | 【代码核实】 |
| 7 | `MultiAgentAnomalyAnalyzer` 的 `pid_root` 改用同一 subject key | 【代码核实】 |
| 8 | L1 的 cgroup/cmdline 贯通链路（新增，`ProcessMetadata`/`observe_exec` 扩展） | 【代码核实】+【实测】 |
| 9 | 修正断点"`-c` 有默认值"（实际为必填无默认） | 【代码核实】 |
| 10 | L3 补 OpenAI `delta.content`/`delta.tool_calls` 累积路径 | 【代码核实】 |
| 11 | `tls_capture` 符号挂载改逐符号 best-effort（**不可**直接追加 `SSL_write_ex2` 到 CHECKED 链） | 【代码核实】 |
| 12 | `policy.rs` `SENSITIVE` 子串表 + `HOME` 取值一并 profile 化/修正 | 【代码核实】 |
| 13 | `graph_builder.rs:947-953` 的硬编码绝对路径 `WORKSPACE_PATH_PREFIXES` 一并处理 | 【代码核实】 |

### 建议修改项（non-blocking）

1. `is_subagent_boundary`（`union_find.rs:103`）标注为测试专用或直接删除，避免后续误当生产落点
2. `SubagentBoundaryReason` 用**新增变体**而非改名；同步 `replay/evaluation.rs:268-272` 的穷尽匹配；补 `Split`/`Reassigned` 产出
3. SSEProcessor 静默淘汰加告警（`sse_processor.rs:543`）
4. `AlertEnrichmentAnalyzer` 是**未注册的死代码**（`main.rs` 无引用），可从风险清单移除
5. 清理陈旧注释：`http_parser.rs:257`、`pipeline_orchestrator.rs:354`、`causal_schema.rs:2` 引用的 `CausalTraceAnalyzer` **已不存在**
6. 行号校正：`graph_builder.rs:863-877`→`869-878`；`policy.rs:461-465`→`454-509`；`tls_capture.c:675-744`→`682-745`；`sse_processor.rs:233-256`→`234-257`
7. 敏感配置入库（`env/config/vendors/deepseek/claude.env`、`observer.toml` 已 git 跟踪且含密钥样式明文）→ 转模板 + `.gitignore`（独立安全事项）

---

## 7. 尚未验证的关键假设（实现前必须证实或证伪）

按用户要求，此处显式列出**尚无证据**的假设，不得当作事实使用：

1. **【已实测确认】L0b 的必要性 —— 必须前置**
   - 串行场景**不**串包（请求/响应交替，实测 `intents` 14 条且工具名正确）→ 这解释了为何 Claude Code 既有使用中从未暴露该缺陷
   - 并发场景**必然**串包（6 路并发实测：17 个合成事件仅 4 个合法，见 §5 L0b）
   - **余项待验证**：补上连接标识后能否完全消除串包（验收见 §8）
2. **【待验证】gzip/压缩路径**：请求或响应若被压缩，`data.type=binary`（`common.rs:27-36` 判"含 `\n\r\t` 以外控制字符"）会**静默丢弃整条链路**。v1 完全未覆盖此空白。需实测确认 OpenClaw + DeepSeek 链路是否压缩。
3. **【待验证】共享 workspace 的降级行为**：若两个 agent 共用 workspace 与 skills，主键会退化为相同。当前设计为"回退到 `traceparent`/文件路径；仍不能分则标 `ambiguous` 并告警，**绝不静默合并**"，但该降级路径未经测试。
4. **【待验证】其他 CLI 无 STABLE/DYNAMIC 标记时的稳定前缀定位**：OpenClaw 的标记是其特有实现，profile 需支持启发式（取首个动态段之前 / 首 N KB），精度未验证。
5. **【待验证】长驻进程的存量可见性**：Gateway 在采集器启动前已运行，其 `ProcessId` 是通过 SSL 事件带 pid 进入 signal graph 的（实测 `agent_identities.process_ids` 含 280268），但**不依赖 EXEC 事件的存量扫描机制是否完备、是否有竞态**，未验证。

---

## 8. 验证策略

按用户授权：可创建临时 agent、反复真实抓取、跑基准回归。

| 阶段 | 最小可验证闭环 |
|------|----------------|
| **L0a** | `sudo ./mtagentlens trace --ssl=true --process=true --binary-path $(node v24) -k 30` → 出现 OpenClaw 的 `ssl` 事件；**符号挂载失败时打印非零失败告警**；`process_events` 能查到 Gateway pid 且带 cgroup/cmdline 字段 |
| **L0b** | 抓一次真实 OpenClaw 请求 → HTTPParser 产出的 request 事件中 `body` 可被 `serde_json` 解析、`messages[0].role == "system"`、system prompt 长度 ≈ 31,820 |
| **L3** | 出现 `source="sse_processor"` 的 finalize 事件；`intents` 有非空工具名结果；Anthropic 相关单测全绿 |
| **L2**（核心验收） | 真实跑 `main` 与 `probe` → `agent_identities` **2 行且同一 pid**；`identity_process_map` 有 ≥2 个 agent_key；前端责任图出现两个节点。反向：profile 配错时**必须显式告警**而非空图 |
| **L4** | 无 OpenClaw 相关的虚假 PolicyBoundary 告警；Claude Code 告警用例不变 |
| **L4b** | 断开 key/网络跑一次 → 日志出现明确失败条目；`intents` 为 0 时仍有告警 |
| **回归** | `cargo test`、前端 250 项 vitest、FuxiBench 评估不回归 |

---

## 9. 风险

| 风险 | 缓解 |
|------|------|
| 指纹可被伪装（agent 可伪造他人 system prompt） | 定位为**识别/分离**手段，**不作信任根**；与文件路径、cgroup 交叉验证 |
| 共享 workspace 导致主键退化 | 回退 `traceparent`/文件路径；仍不能分则标 `ambiguous` + 告警，**绝不静默合并** |
| schema 迁移影响既有 DB 与 replay | `schema_version` 表已存在，走版本升级路径；迁移前后各跑一次回归 |
| 算法重写的回归面 | Claude Code 路径行为冻结；每层完成即跑全量测试 |
| 性能：48KB 请求 × 每请求解析 | 稳定区解析只做一次前缀匹配 + 一次 hash；缓存 workspace→AgentId |
| **L2 触及核心归因的连锁编译影响** | `IdentityRefinementType` 穷尽匹配等会在编译期报错（好事），需在拆解时显式列出受影响点 |

---

## 10. 分期计划（依 §7 的验证结论可能调整）

**原则**：先把"看不见"变成"看得见且可证伪"，再动身份。

分期依据三条**互相独立**的缺陷层，且它们**串联依赖**（数据 → 归属 → 因果）：

| 缺陷层 | 缺陷 | 实测证据 | 若不修的后果 |
|--------|------|----------|--------------|
| **传输层** | `ConnectionBuffer` 按 tid 分键 → 并发串包 | 17 合成事件仅 4 合法，6 处 `}{`（§3.7/§5 L0b） | 数据源即被污染，一切下游建立在坏数据上 |
| **身份层** | comm 单点 + seed=`ProcessId` + 边界 no-op | `agent_identities`=1、`subagent_boundaries`=0、15/15 独立 UUID（§3.7） | 多 agent 攻击检测失效 |
| **关联层** | `intent → action` 匹配失败 | `correlation_evidence`=0、20 条 `NoArgumentMatch`（§3.8） | **即使身份修好，攻击链仍建立不起来** |

**Phase 0 · 数据地基**（✅ **已完成 2026-10-12**）
1. ✅ **P0.1 传输层分片正确性（L0b）**——`ssl_ptr` 连接标识 + `ConnectionBuffer` 连接状态机
   *实测验收*：并发场景合成请求合法率 **33% → 100%**（真实 OpenClaw 流量，6/6）
2. ✅ **P0.2 采集可用性（L0a）**——符号挂载 best-effort + 失败可见；cgroup/cmdline 贯通到 `ProcessMetadata`；`-c` 默认值
   *实测验收*：`OpenSSL 符号全部挂载成功` 可见；cgroup/cmdline 字段入 `ProcessMetadata`；894 lib 测试全绿

**Phase 1 · 正确归属**（核心）
3. **P1.1 L0 Profile + L1 运行时身份**（profile schema + `RuntimeResolver` + 消除 `AGENT_COMMS` 双副本）
4. **P1.2 L2b 带外数据源**（读 OpenClaw `trajectory_runtime_events` → 结构化 Intent + `asserted` AgentId；profile 驱动；schema 版本校验）
   *验收*：真实采集下 `sessionKey` 出现在身份的 `asserted` 来源中
5. **P1.3 L2 会话身份**（seed 重构 + `IdentityProvider` 扩展 + schema 迁移 + 前端 JOIN + `MultiAgentAnomalyAnalyzer` 改键 + `Split`/`Reassigned` 产出）
   *验收*：真实跑 main 与 probe → `agent_identities` **2 行且同一 pid**

**Phase 2 · 建立因果**（**范围已由 L2b 大幅缩减**）
6. **P2.1 带外 Intent 接入关联链**——把 L2b 的结构化 `tool.call` 转成 `Intent` 喂给现有匹配器
   *验收*：真实采集 `correlation_evidence > 0`
   > v2 早期稿把此项列为「Code Mode 语义解析」的研究难题；实测证明 agent 已落盘结构化 ground truth，**该难题不存在**（见 §5 L2b）。

**Phase 3 · 能力恢复与治理**
6. **P3.1 L3 协议适配**（SSEProcessor 的 OpenAI 聚合路径，**非意图提取**——后者已工作）
7. **P3.2 L4 误报治理 + L4b 降级可观测性**
8. **收尾**：前端展示、文档、配置安全

> **顺序说明**：① v1 称"L3 必须先于 L2"不成立——system prompt 属**请求路径**，L3 的 finalize 属**响应路径**，二者不共享代码。② L0b 已由并发实测确认**必须前置**（不再是"条件性"）。③ L4 必须在 L2 之后，否则无法区分"分离失败"与"白名单误伤"。④ P2 独立于身份层，可在 P1 后并行。

---

## 11. 待确认事项

1. L5 是否降级为非目标（当前无落地位置与消费方）
2. profile 是否需暴露 CLI 参数（`--agent-profile`）还是仅配置文件
3. 前端是否显式区分 `asserted` / `inferred` 身份来源
4. §7 五项待验证假设的验证顺序与是否阻塞分期推进
