# LLM Agent 可观测性最佳实践调研（agentsight 源码深读 + 行业标杆）

**日期**: 2026-08-07
**用途**: 为「数据库全表充分消费」spec 提供权威参考；让 Claude Code 不再是黑盒子
**方法**: 本机深读 `eunomia-bpf/AgentSight`（v1.0.3，与本项目同构的 eBPF 零插桩观测工具）+ 论文 + WebSearch 全网顶级项目（Langfuse/LangSmith/Phoenix/AgentOps/OpenTelemetry GenAI）

---

## 一、核心结论（一句话版）

把「真实数据组织成全景」的行业权威做法是：**单一快照 + 纯函数派生 + 算不出的留空**（agentsight 的「Nothing is invented」哲学）；组织形态是 **SummaryBand（概要带）→ 会话/信号列表 → 活动时间线 → 面板网格**（agentsight Dashboard）；语义模型采用 **OTel GenAI 三层 span 树（model → agent → tool）**。

## 二、agentsight 源码深读（与本项目最同构的标杆）

**来源**: `/home/lzk/agentsight`（论文 [AgentSight: System-Level Observability for AI Agents Using eBPF](https://arxiv.org/abs/2508.02736)，[GitHub](https://github.com/eunomia-bpf/AgentSight)）

### 2.1 最核心哲学 —— 「Nothing is invented」

`frontend/src/utils/dashboard.ts` 开头原文：

> "Every value here is derived solely from the materialized-view snapshot served by the collector. **Nothing is invented: if a panel cannot be computed from the rows the collector emits, it is left empty.**"

- 全部 KPI 由纯函数 `deriveSummary` / `deriveEffects` / `deriveFriction` / `deriveActivity` 从**同一个**快照派生。
- 算不出的面板诚实留空（如 cost：`"Cost is intentionally NOT derived: the collector emits no price field."`）。
- **这是「不幻觉」的权威实现范式，必须写进本项目 spec。**

### 2.2 单一快照数据模型

`AgentSightSnapshot`（`types/event.ts`）是前端唯一数据来源（`GET /api/v1/snapshot?audit_limit=50000`）：

| 组 | 内容 | 本项目对应 |
|----|------|-----------|
| `summary` | 聚合计数 + 时间范围（start/end/audit_limit） | `DatasetOverview.run` + counts |
| `token_summary` | 按 model 的 token 分解（input/output/cache_*) | 无（我们无 token 数据） |
| `audit_events` | 有界事件流（audit_type: process/file/llm/network + action/target/status） | `actions` + `intents` |
| `process_nodes` | 进程层级（pid/ppid/command/argv/status） | `process_events` + `identity_process_map` |
| `network_targets` | 网络目标（host/path/count/error_count） | `tls_sessions` + actions 网络类 |
| `resource_samples` | CPU/RSS 时序 | 无（system 数据） |
| `sessions` | 会话摘要（agent_type/model/status/tokens/duration） | `runs` + `identity_process_map` |
| `tool_calls` | 工具调用（tool_name/input/output/status/related_pid） | `intents` + `actions` |

### 2.3 Dashboard 布局（要借鉴的组织形态）

```
SummaryBand（概要带）
  ├─ 头部：标题 + 健康徽章（hasFailures ? Needs attention : Healthy）+ 身份行（agent · model）
  ├─ 元信息：时长 / 起止时钟 / cwd
  └─ 6× KPI tile（tokens / llmCalls / processes / files / network / tools，各带 hint）
Sessions 面板（多 session 时：swatch + agentType·model / duration·status / token 数）
ActivityChart（事件密度堆叠面积图，按 audit_type 分桶）
2×2 网格：TokensPanel | EffectProfile（效果画像）| ResourceShape | FrictionSignals（摩擦信号）
```

### 2.4 可借鉴的具体模式

| 模式 | agentsight 实现 | 本项目应用 |
|------|----------------|-----------|
| **KPI tile** | `KpiTile(label, value, hint, tone)`；tone 让关键指标变红 | 数据源详情 6 tile（进程/文件/工具/网络/声明/告警）|
| **摩擦信号列表** | `FrictionSignals`：category 标签 + severity 徽章(×count) + title + detail + 图标；规则：失败命令/重复命令/重复工具调用/思考间隙 | **新增信号面板**：污染事件 + Critical claims + L5 弱匹配 + 策略评估异常 |
| **效果画像** | `EffectProfile`：进程/文件/网络计数 + top 6 命令/文件/主机 + 相对混合条(segmented bar) | 动作类型分布（FileRead/FileWrite/ProcessExec）+ top 文件 + top 命令 |
| **活动时间线** | `ActivityChart`：按 audit_type 分 48 桶堆叠面积图 | intents/actions/claims/pollution 按时间分布 |
| **健康徽章** | `SummaryBand` 顶部 `hasFailures ? critical : good` | 数据源头部：有 Critical claim/alert ? 需关注 : 正常 |
| **诚实空态** | `EmptyState(message, hint)`；"Cost is not shown" | token 同理 "Token is not shown" |
| **数据渲染** | `font-mono` 全部 id/pid/路径 + `tabular-nums` 数字对齐 + `<pre>` 内容 | 贯穿三个页面 |
| **面板容器** | title/subtitle/action + PanelDrillLink | 复用现有 `SectionPanel` |
| **进程树** | ProcessNode 展开 + 事件徽章 + UnifiedBlock 渐变类型 | 可映射 traces 页（已有）|

### 2.5 agentsight 的诚实边界（同样适用我们）

- 不做 cost（collector 无 price 字段）→ **我们：不做 token（db 无 token 数据）**
- 不做 per-session filter（audit 无 session_id）→ **我们：claim↔alert 无外键只做弱关联**
- 不是聊天 UI（无 chat 气泡）→ 我们是观测工具

## 三、行业标准：OpenTelemetry GenAI 语义约定

**来源**: [open-telemetry/semantic-conventions-genai](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-agent-spans.md)、[OTel GenAI docs](https://opentelemetry.io/docs/specs/semconv/gen-ai/)、Microsoft [agent-traces 蒸馏参考](https://github.com/microsoft/skills)

### 3.1 三层 span 树

```
user request
 └─ invoke_agent: orchestrator      ← agent 运行/回合
     ├─ chat: LLM call             ← model span（token 用量）
     ├─ execute_tool: file_read    ← 工具调用（每调用一个 span）
     │    ├─ gen_ai.tool.name / call.arguments / call.result
     └─ invoke_agent: sub-agent    ← 子代理交接
```

### 3.2 关键属性映射到本项目

| OTel 语义 | 本项目数据 |
|-----------|-----------|
| `gen_ai.operation.name` = `execute_tool` | `intents.tool_name` + `intents.tool_args_json` |
| `gen_ai.tool.call.arguments/result` | intents.tool_args + actions.normalized_args |
| `gen_ai.agent.id/name` | `identity_process_map.agent_id` + `provisional_identities` |
| `gen_ai.conversation.id` | `intents.message_id`（会话内消息归属）|
| trace 树（spans 父子关系） | `responsibility_tracebacks`（因果链）+ 意图动作链 |

### 3.3 对「意图动作链」的启示

业界把工具调用组织为「**agent span → execute_tool span 树**」，每个 tool 卡片显示 name + arguments + result。我们的意图动作链（intent → action → evidence）正是 OTel `execute_tool` 树在系统边界的延伸——**用 OTel 术语命名组织**（intent=工具意图、action=系统效应、evidence=匹配证据）。

## 四、成熟平台组织方式（Langfuse / LangSmith / Phoenix / AgentOps）

**来源**: [Langfuse vs LangSmith](https://langfuse.com/resources/engineering/langsmith-alternative)、[Phoenix](https://github.com/Arize-ai/phoenix)、AgentOps 对比

- **trace/execution tree**：spans 父子关系树（Phoenix 的 execution tree：agent spans / LLM generations / tool executions）。
- **trace explorer**：project + session 组织，全文搜索（token-based）。
- **drill-down**：展开到完整 inputs/outputs、system prompts、tool arguments/outputs、timings、errors。
- **会话组织**：sessionId/userId 组织多轮对话；评估（evaluator）输出为分数/判定。
- **prompt playground**：重放真实 trace 对比 prompt。
- 共同形态：**列表 → 展开详情 → 完整输入输出**（正是我们「claim 列表 → 展开回溯链」的方向）。

## 五、落地映射：本项目每张表的「agentsight 式」消费

| 表 | agentsight 对应 | 展示形态 |
|----|----------------|---------|
| `graph_snapshots` | 快照（时序） | 帧数 + 节点类型分布 |
| `responsibility_alerts` | audit/session 摘要 | 告警列表（已有）+ 健康徽章输入 |
| `correlation_evidence` | audit 关联 | 意图动作链 + match_level 分布 |
| `responsibility_claims` | evaluator/judge 输出 | 责任声明列表 + 展开回溯链 + 信号源 |
| `responsibility_tracebacks` | trace 树 | claim 内联因果链 |
| `intents` | tool_calls | 意图动作链（execute_tool 卡片）|
| `actions` | audit_events(process/file/network) | 意图动作链 + EffectProfile 分布 |
| `context_pollution_events` | friction 信号 | **信号面板**（污染条目）|
| `policy_evaluations` | 评估 | 信号面板 + 概要计数 |
| `process_events` | process_nodes | KPI「进程」+ 进程树（已有 traces 页）|
| `tls_sessions` | network_targets | KPI「网络」|
| `identity_process_map` / `provisional_identities` | sessions/agent id | 身份映射列表 |
| `runs` | summary | 数据源头部元信息 |

## 六、对 spec 的修订建议（已并入）

1. **「不幻觉」哲学显式化**：所有 KPI/分布用**纯函数**从导入数据 derive；算不出的留空（token 同理 "未采集"）。
2. **overview「数据源详情」升级为 agentsight 风格组合**：SummaryBand（健康徽章 + 身份行 + 6 KPI tile）→ **信号面板**（污染/风险/弱匹配）→ ActivityChart（活动时间线）→ EffectProfile（动作类型分布 + top 文件/命令）。
3. **意图动作链用 OTel execute_tool 语义命名**：intent 卡片 = tool_name + args + 权威来源；action 卡片 = 系统效应 + 信号级别；evidence = 匹配级别。
4. **告警工作台责任声明**采用 FrictionRow 视觉（severity 图标 + category + title + detail + count）。
5. **诚实边界对齐**：token 不显示（db 无数据）；claim↔alert 弱关联；空表显示 0。

## 七、来源

- [AgentSight GitHub](https://github.com/eunomia-bpf/AgentSight) / [论文 arXiv:2508.02736](https://arxiv.org/abs/2508.02736)
- [OTel semantic-conventions-genai](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-agent-spans.md) / [OTel GenAI docs](https://opentelemetry.io/docs/specs/semconv/gen-ai/)
- [Microsoft agent-traces 参考](https://github.com/microsoft/skills)
- [Langfuse vs LangSmith](https://langfuse.com/resources/engineering/langsmith-alternative)
- [Arize Phoenix](https://github.com/Arize-ai/phoenix) / [AgentOps](https://test.aiagentstore.ai/compare-ai-agents/agentops-vs-arize-ai)
