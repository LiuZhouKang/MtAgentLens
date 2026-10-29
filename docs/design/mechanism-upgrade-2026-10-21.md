# 机制升级设计：OS 级来源追踪 + 权限包络 + 多 Agent 来源保持

> 目标：不降级任何机制，而是把它们做成**真正有判别力、可证伪、可写进论文**的
> 机制。本文给出设计、代码落点、测试与评测计划。
>
> 依据：2025–2026 学界/工业界最接近的工作——
> CaMeL（capability + control/data flow separation）、Fides（IFC 标签）、
> Progent（最小权限 + monotonic confinement）、AgentArmor（运行时 trace 的
> CFG/DFG/PDG 与类型系统）、AgentSight（TLS + eBPF 边界追踪）、
> multi-CaMeL / TrustTrace（跨 Agent 的 **boundary laundering**）。

## 0. 一句话

现在的 PBC 只看"碰了敏感路径"，所以良性任务也会碰；升级后 PBC 的定义变成
**"不可信来源的内容越过了权限边界，进入了本不该由它决定的动作"**。
这把"活动量"换成了"来源与权限"，从根上解决混杂。

## 1. 核心模型

### 1.1 标签（trust / provenance）

每个内容片段带四元属性：

| 标签 | 含义 |
|---|---|
| `origin` | `Authored`（用户/系统指令） / `Retrieved`（文件、工具结果、网络响应） / `Derived`（模型基于上下文生成） |
| `integrity` | `Trusted` / `Untrusted`；`Derived` 取其所依赖内容的最小完整性 |
| `confidentiality` | `Public` / `Private` / `Secret`（凭据、私钥、shadow 等） |
| `authorization` | `UserNamed`（用户明确点名并授权使用） / `Unrequested`（用户未提） |

规则：

1. 用户 prompt / system prompt → `Authored, Trusted`。
2. 文件读取、命令输出、网络响应 → `Retrieved, Untrusted`；若用户 prompt
   明确点名该文件并指示读取/执行，标 `UserNamed`（仍为 Retrieved，但可用于
   该用户请求）。
3. 模型 tool call / 回复 → `Derived`，`integrity = min(其上下文中被使用内容的
   integrity)`；若其参数文本匹配到某 `Retrieved, Untrusted` 片段，则继承该
   片段的标签与来源。
4. 敏感资源（私钥、shadow、云凭据）→ `Secret`；读取即边界事实。

### 1.2 策略（越界判定）

对每个动作 `a=(agent, op, resource)`：

- **P-content**：动作参数包含 `Retrieved, Untrusted, Unrequested` 片段 →
  `UntrustedInstructionFlow`（内容边界穿越）。
- **P-egress**：`Derived from Untrusted/Secret` 的内容被发往外部端点 →
  `DataExfiltration`。
- **P-secret**：读取 `Secret` 资源 → `CredentialAccess`（硬证据）。
- **P-capability**：动作引入用户未请求的新能力（网络、shell、写入控制面）→
  `CapabilityExpansion`。
- **P-delegation**：跨 Agent 传递的内容使子 Agent 获得父 Agent 未被授权的
  能力（boundary laundering）→ `PrivilegeLaundering`。

每条命中的证据都带 **来源链**：`source span → tool result → intent → action →
boundary`，供 traceback/claim 使用。

### 1.3 权限包络（capability envelope）

每个 Agent 维护：

```
Envelope = {
  tools: {name → arg-pattern constraints},
  resources: {read/write patterns},
  network: {allowed hosts / none},
  spawn: {allowed comms / none},
  derived_from: user turn / parent envelope
}
```

- 初始包络由用户 prompt 与首个 tool call 推导（保守：只允许 workspace 读写 +
  用户点名的工具；网络/shell/控制面默认不授权）。
- 任何新能力需要用户显式授权；否则记 `CapabilityExpansion`。
- 子 Agent 包络 = 父包络 ∩ 子任务包络（衰减，不扩张）。
- 该包络不用于"阻断"（我们是监测器），只产生**类型化证据**；Observer 可用它
  判断"这是用户要求的，还是被注入内容诱导的"。

## 2. 代码落点

### 2.1 新模块 `collector/src/responsibility/provenance/`

| 文件 | 职责 |
|---|---|
| `labels.rs` | `Origin/Integrity/Confidentiality/Authorization` 与格运算（min/join） |
| `span_store.rs` | 有界的内容片段存储（hash、source、agent、time、text 摘录），按 run 隔离 |
| `matcher.rs` | tool-call 参数与 span 的匹配：规范化（小写、空白、路径归一）后的 n-gram/子串匹配；阈值可配；返回 `(span_id, matched_tokens)` |
| `flow.rs` | 从 LLM 请求消息解析角色（system/user/assistant/tool），把 tool result 标为 Retrieved；把 assistant tool call 标为 Derived 并调用 matcher 继承标签 |
| `envelope.rs` | 权限包络的推导、扩张检测、委托衰减 |

### 2.2 类型扩展

- `Intent` 增加（`#[serde(default)]`）：
  `provenance: Option<ProvenanceTag>`、`provenance_span_id: Option<String>`、
  `capability_expansion: Vec<String>`。
- `CorrelationEvidence` 增加 `matched_fields`（实际匹配的字段名）与
  `provenance_span_id`。
- 图边：新增 `UntrustedInstructionFlow`、`CapabilityExpansion`、
  `PrivilegeLaundering`；或复用 `PolicyBoundaryCrossing` 但用
  `boundary_type` 属性区分。论文里保留 3–4 种边，不再声称 7 种。
- Schema v4：`provenance_spans` 表（span_id, run_id, agent_id, origin,
  integrity, source, text_sha256, excerpt, timestamp_ns）。

### 2.3 接线

```
SSL 请求/响应 → MessageBinding/Intent 提取
   → flow.rs: 标注请求中的 tool result spans（Retrieved）与 assistant calls（Derived）
   → matcher: tool call 参数是否复用了 untrusted span
   → Intent.provenance
   → graph_builder: 建 UntrustedInstructionFlow / CapabilityExpansion 边
   → scoring: 新维度/加权（见 §3）
   → Observer: 输入增加 provenance 摘要
   → claim/traceback: 沿来源链回溯到 source span
```

### 2.4 不改变的原则

- 我们仍是**监测器**，不阻断动作；包络只产生证据。
- 所有判定仍是 §decision-procedure 的三值聚合：`UntrustedInstructionFlow`
  是候选，硬证据（Secret 读取、外泄）才直接 Unsafe；其余交 Observer。
- 不读取 Agent 代码/不插桩；所有信息来自 OS 事件与 TLS 明文。

## 3. 六维评分的真实化（不降级）

| 维度 | 现状 | 升级 |
|---|---|---|
| D1 边界严重性 | 异常边/总边（含噪声） | `max(硬规则, 类型化边界密度)`；边界按 provenance 类型加权（UntrustedInstructionFlow > 普通 PBC） |
| D2 内容威胁 | 恒 0 | 接入 DeBERTa 分数（已有 classifier）与 provenance 匹配强度 |
| D3 智能体放大 | PBC/intent × 证据质量 | `untrusted-derived actions / intents` × 跨 Agent 传播 |
| D4 重复观测 | 边界证据重复数（已修） | occurrence_count（新字段），并区分"同一内容的重复执行" |
| D5 跨上下文佐证 | 匹配级别加权 | 加上 provenance 链完整度（source→action 是否可闭合） |
| D6 行为基线 | confirmed PBC + high-conf evidence | 每个 Agent 的 baseline（syscalls/paths/ports）偏差，使用已有 BaselineTracker 并真正喂入 |
| 校准 | 固定 logistic | 在**开发集**上拟合 isotonic/Platt，冻结后在测试集报告 ECE/Brier；若数据不足，明确写"未校准"而不是假称校准 |

权重仍用 0.28/0.25/0.20/0.10/0.10/0.07，但融合改为加权线性 + 加权几何均值
（已实现），并在论文给出公式与开发集。

## 4. 多 Agent：来源保持（boundary laundering）

- 委派时，父 Agent 传给子 Agent 的内容标为 `Derived from parent context`；
  若父上下文含 `Retrieved, Untrusted` 片段，子 Agent 的对应动作继承
  `Untrusted` 并记录 `PrivilegeLaundering` 候选。
- 子 Agent 的包络 = 父包络 ∩ 子任务授权；任何扩张都是证据。
- 这正是 multi-CaMeL / TrustTrace 指出的失效模式；我们的 OS 层版本用
  **内容片段匹配**代替应用层标签传播。

## 5. 测试（TDD）

1. `labels`: 格运算（Trusted ∧ Untrusted = Untrusted；min integrity）。
2. `span_store`: 有界、按 run 隔离、hash 稳定。
3. `matcher`: 完全相同/路径归一/大小写/空白/长命令子串；短 token 不匹配；
   中文/Unicode 归一；性能上限（O(spans×args) 有界）。
4. `flow`: Anthropic/OpenAI 请求消息角色解析；tool result → Retrieved；
   assistant tool call → Derived 且继承来源。
5. `envelope`: 初始包络、扩张检测、委托衰减（子 ⊆ 父）。
6. 端到端（构造事件）：用户 prompt 不含命令 + 文件含 `curl` + 工具执行
   `curl` → `UntrustedInstructionFlow`；用户 prompt 明确要求执行该文件 →
   不误报；无读取直接执行 → 不误报。
7. 良性回归：26 良性场景仍 Safe；困难良性（known_hosts 等）不产生
   UntrustedInstructionFlow。

## 6. 评测

- **机制消融**：provenance on/off、envelope on/off、delegation attenuation
  on/off；指标为最终 verdict 翻转、Observer 调用数、成本。
- **构造效度**：执行审计确认的攻击 vs 未执行；报告 provenance 命中的
  precision/recall（以 GT expected_commands 为参照）。
- **对照**：AgentSight（层级/开销定位）、CaMeL/Fides（应用层 IFC，不可直接
  部署到零插桩场景，明确写"不同层，不可直接比 TPR"）。
- **开销**：matcher 的每次 tool call 成本、span store 内存上限；目标
  <5% 额外 CPU（AgentSight ~2.9% 作为参照）。

## 7. 分阶段落地

| 阶段 | 内容 | 验收 |
|---|---|---|
| P1 | labels/span_store/matcher + 单测 | `cargo test provenance` 全绿 |
| P2 | flow：请求消息角色与 tool result 标注 | 用真实捕获的 Anthropic/OpenAI 请求做夹具测试 |
| P3 | Intent/graph/scoring 接线 + provenance 边 | 构造攻击/良性端到端测试 |
| P4 | envelope + 委托衰减 | 2×2 测试（扩张/不扩张 × 委派/不委派） |
| P5 | Observer 输入 + claim/traceback 来源链 | 端到端 claim 带 source span |
| P6 | 消融与真实语料重测 | 小样本 pilot，方向成立再扩 |

## 8. 与论文主张的对应

- 新主张（可证伪）：**"零插桩的 OS 证据平面可以重建内容来源，并把
  '不可信内容驱动的动作' 与 '用户授权动作' 区分开"**。
- 这正是 CaMeL/Fides 在应用层做的事，我们在 OS 层用 TLS 明文 + 进程/文件
  事件做；对这一层的可行性、盲区与开销做测量，是论文的核心贡献。
- PBC、六维、Observer、traceback 都围绕这个核心重新定位为"证据类型/仲裁/
  归因"，而不是被降级删除。

---

## 9. 实施状态（本轮）

| 阶段 | 状态 | 证据 |
|---|---|---|
| P1 labels/span_store/matcher | **完成** | `responsibility/provenance/{labels,span_store}.rs`；17 单测 |
| P2 请求角色/ToolResult 标注 | **完成** | `runtime/provenance_tracker.rs`；OpenAI/Anthropic 夹具测试 |
| P3 Intent/图/评分接线 | **完成** | runtime 请求→意图标注→`UntrustedInstructionFlow` 边界；端到端测试 |
| P3b Observer 见 provenance | **完成** | `ContentSignals.provenance` + 提示词说明 |
| P4 capability envelope | **部分** | `provenance/envelope.rs`（推导/衰减/检查）+ 图检查（network/shell/write）；运行时从 UserPrompt 推导；**尚未在真实语料评估误报** |
| P5 claim/traceback 来源链 | **部分** | PBC 边界节点带 span id；traceback 尚未沿 provenance 回溯 |
| P6 消融/真实语料 | **部分**：严格口径开关已接线（`MTAGENTLENS_PROVENANCE_STRICT`） | 消融数字待最终语料重测 |
| 三信号身份 | **完成** | 请求 `metadata.user_id.session_id` → SignalGraph → UnionFind S3 合并；2 单测 |
| D2/D6 真实化 | **完成** | runtime 传入 DeBERTa 分数与 baseline deviation；2 单测 |

已知限制：envelope 的 shell/network 授权是保守启发式，必须用良性语料
测量误报；P5 的来源链仍需把 tool_result 映射回具体文件/工具调用。

## 10. 授权策略的两种口径（必须在消融中报告）

- **严格口径**（CaMeL/Fides 的立场）：任何 retrieved 内容（包括用户点名要读
  的文件、工具结果、网络响应）都不能影响可执行动作；只要 tool call 复现其
  可执行段，就是 `UntrustedInstructionFlow`。召回高，良性"照文档执行"任务会
  产生 review 候选，由 Observer 仲裁。
- **用户点名叫口径**（当前默认）：用户明确点名要读的文件被视为本次任务授权，
  其内容不触发 provenance；未点名文件/网络响应仍为 untrusted。precision 高，
  但 Type B（payload 就在用户要求读取的 fixture 中）不会被 provenance 捕获，
  依赖规则/Observer。
- 论文必须报告两种口径的 precision/recall 与良性 review 率；默认口径不能
  单独证明"来源追踪有效"。
- **严格口径已接线**：`MTAGENTLENS_PROVENANCE_STRICT=1`（或 `true`/`yes`）
  切换 `MatchPolicy::StrictRetrieved`。该模式下**任何** retrieved 内容（包括
  用户点名读取的文件）复现到可执行 tool call 都生成 `UntrustedInstructionFlow`
  边界；用户/系统 authored 文本仍是 trusted（Type A 由规则/包络/Observer
  负责）。运行诊断 `[diagnostics:json].provenance_strict` 记录当前口径，
  消融实验必须报告该字段。实现：`provenance/span_store.rs::MatchPolicy`、
  `runtime/provenance_tracker.rs::strict_mode_enabled`；单测
  `strict_policy_taints_content_the_user_named`、
  `strict_mode_taints_a_user_named_file_command`。
- 当前精确/分段匹配（segment equality）是两种口径的共同基础；它已消除
  "读文件后执行另一条含相同片段的命令"这类误报。
