# 数据库全表充分消费设计 — 让 Claude Code 不再是黑盒子

**日期**: 2026-08-07
**状态**: 已批准（brainstorming 完成）
**范围**: 单 db 任意选择导入 + 充分展示；全表充分消费（claims / tracebacks / intents / actions / 各表概要，agentsight 风格组织）

---

## 一、目标

用户导入真实 `collector.responsibility.db` 后，前端当前只消费 3 张表（graph_snapshots / responsibility_alerts / correlation_evidence），其余数据被丢弃——Claude Code 的行为对用户是黑盒子。本设计将：

1. **单 db 任意选择导入 + 导入后充分展示**：保持单数据源架构，导入入口顺手，导入后七项统计与全表数据全部可见。
2. **全表充分消费**：新增提取 claims / tracebacks / intents / actions + 各表概要，按 agentsight 的「列表 → 展开详情」模式组织，让 Claude Code 的每个行为（想做什么 → 实际做了什么 → 责任判定 → 因果链）对用户清晰可见。
3. **「不幻觉」哲学（agentsight「Nothing is invented」）**：所有 KPI/分布由**纯函数**从导入数据派生；任何无法从数据计算的面板诚实留空/标注（如 token——db 无此数据）。这是本设计的质量底线。

## 二、三个边界事实（诚实声明，杜绝幻觉）

| # | 事实 | 对设计的影响 |
|---|------|-------------|
| 1 | **db 无 token 数据**：`collector.responsibility.db` 不持久化 LLM token 用量 | agentsight 的 token 统计不可做；追踪页 LLM 事件仍有 prompt/response 文本可展示，不做 token 数字 |
| 2 | **`responsibility_claims` 无 `alert_id` 列**：claim↔alert 无外键 | 前端只做**弱关联**（共享 `evidence_refs` 交叠 + 主体 agent 匹配，仿 collector 的 `find_boundary_edge_for_claim` 评分策略），不伪造强绑定 |
| 3 | **`traceback.claim_id` 是真实外键**；`correlation_evidence` 同时含 `intent_id`+`action_id` | 回溯链与意图动作链的关联是数据库真实关系，是设计地基 |

## 三、真实表结构基线（前端消费依据）

取自真实 run `20260716-114859-ecb03ced/collector/collector.responsibility.db`（**27 张用户表**，另有 `sqlite_*` 内部表）。下方为核心可消费表：

| 表 | COUNT | 关键列 | 前端角色 |
|----|-------|--------|---------|
| `graph_snapshots` | 55 | snapshot_id / timestamp_ns / nodes_json / edges_json / metrics_json | 图（已消费）+ 帧时序 |
| `graph_nodes` / `graph_edges` | 1461 / 3300 | node_id / node_type / edge_type / source / target | 图（已消费）|
| `responsibility_alerts` | 13 | title / severity / decision / confidence / authority / engine_section_json / timeline_json / graph_metrics_json | 告警（已消费）|
| `correlation_evidence` | 1823 | evidence_id / intent_id / action_id / match_level / confidence / matched_fields_json | 证据（已消费）+ 意图动作链 |
| `responsibility_claims` | 22 | claim_type / subject_agent_id / policy_id / boundary_id / claim_state / severity / confidence / evidence_refs_json / competing_explanations_json / traceback_ref / phase | **新增**：责任声明 |
| `responsibility_tracebacks` | 22 | traceback_id / claim_id / chain_node_ids_json / chain_edge_ids_json / root_cause_node_id / explanation_summary / confidence | **新增**：回溯链 |
| `intents` | 23 | intent_id / pid / process_start_time_ns / message_id / tool_name / tool_args_json / intent_class / parse_state / primary_matching_eligible / authority_source / timestamp_ns | **新增**：工具意图 |
| `actions` | 474 | action_id / pid / process_start_time_ns / action_type / normalized_args_json / derivation_mode / signal_class / timestamp_ns | **新增**：系统动作 |
| `context_pollution_events` | 219 | pollution_id / source_agent_id / contaminated_artifact / propagation_path_json / detected_at_ns | **新增**：污染事件概要 |
| `policy_evaluations` | 755 | policy_id / boundary / judgment_json / subject_agent_id / resource_json / evidence_refs_json / confidence / reason | **新增**：策略评估概要 |
| `process_events` | 160 | pid / parent_pid / comm / start_time_ns / timestamp_ns | **新增**：进程概要 |
| `tls_sessions` | 1067 | tls_session_id / pid / start_time_ns / timestamp_ns | **新增**：TLS 会话概要 |
| `identity_process_map` | 196 | agent_id / pid / start_time_ns | **新增**：身份映射概要 |
| `provisional_identities` | 196 | — | **新增**：身份概要（真实数据存在）|
| `agent_identities` / `message_bindings` / `delegation_records` / `subagent_boundaries` | 0（真实 db 为空） | — | **新增**：概览计数（诚实显示 0）|
| `non_link_diagnostics` | 2223 | — | 内部诊断：仅概览计数 |
| `runs` | 1 | run_id / started_at_ns / ended_at_ns（真实 NULL）/ scenario_id / mode / collector_version | **新增**：运行元信息 |

> 说明（真实数据实测）：`agent_identities`/`message_bindings`/`delegation_records`/`subagent_boundaries` 与 refinements 系列在真实 db 均为 **0 行**——概览计数诚实显示 0（保留计数逻辑，未来 run 可能有值）；`non_link_diagnostics`（2223 行）为内部诊断，仅计数不展开。

## 四、数据层设计（types.ts + db-import.ts + store.ts）

### 4.1 类型设计（`lib/types.ts`）——新增 2 个 + 复用 2 个

> **复用现有类型，不新增**：`Intent`（`lib/types.ts:182`）与 `Action`（`lib/types.ts:195`）**已存在**——它们是后端 `/api/runs/:id/evidence` 的响应类型（`lib/api.ts:82`），且已被 `evidence-panel.tsx`/`IntentActionMatch` 使用。本设计**不新增也不修改** Action/Intent 接口——真实 db 的 `tool_args_json`/`normalized_args_json` 均为 `Array<{Path|Content|Command}>` 形态，与现有 `tool_args: unknown[]`/`normalized_args: unknown[]` **天然匹配**，无需扩展类型：
> - 受影响消费方（改这些类型会牵连，故保持不动）：`evidence-panel.tsx:3,12`、`lib/api.ts:82-83`
> - **真正新增的类型仅两个**：`ResponsibilityClaim` 与 `ResponsibilityTraceback`（现有 types.ts 无此二者）

```ts
export type ClaimType =
  | 'PolicyViolation' | 'AccountabilityAnomaly' | 'SuspiciousDelegation'
  | 'TaintedContextUse' | 'UnauthorizedCapabilityUse' | 'BlockedAttempt';
/** 真实数据实测（22 条）claim_state 分布：Inferred 18 / Confirmed 3 / Ambiguous 1。
 * 联合类型覆盖真实取值；渲染时未知值落入灰显兜底。 */
export type ClaimState =
  | 'Inferred' | 'Confirmed' | 'Ambiguous';

export interface ResponsibilityClaim {
  claim_id: string;
  claim_type: ClaimType;
  subject_agent_id: string | null;
  policy_id: string | null;
  boundary_id: string | null;
  claim_state: ClaimState;
  severity: AlertSeverity;              // Info | Warning | Critical
  confidence: number;
  evidence_refs: string[];
  competing_explanations: Array<{ label: string; evidence_refs: string[]; rule_id: string; rationale: string; aligned_pattern: string | null }>;
  traceback_ref: string | null;
  phase: PhaseIndicator;
  timestamp_ns: number;                 // 由 phase 提取，用于排序
}

export interface ResponsibilityTraceback {
  traceback_id: string;
  claim_id: string;
  chain_node_ids: string[];
  chain_edge_ids: string[];
  root_cause_node_id: string;
  explanation_summary: string;
  confidence: number;
}

/** 各表概要——"Claude Code 全景"统计卡片数据 */
export interface DatasetOverview {
  run: { run_id: string | null; started_at_ns: number | null; ended_at_ns: number | null; scenario_id: string | null; mode: string | null; collector_version: string | null } | null;
  snapshots: { count: number };
  graph: { nodes: number; edges: number };
  alerts: { count: number };
  evidence: { count: number; by_match_level: Record<string, number> };
  claims: { count: number; by_type: Record<string, number>; by_severity: Record<string, number> };
  tracebacks: { count: number };
  intents: { count: number; by_tool_name: Record<string, number>; by_authority_source: Record<string, number> };
  actions: { count: number; by_action_type: Record<string, number>; by_signal_class: Record<string, number> };
  pollution: { count: number };
  policy_evaluations: { count: number };
  processes: { count: number };
  tls_sessions: { count: number };
  identity_process_map: { count: number };
  provisional_identities: { count: number };
  agent_identities: { count: number };
  message_bindings: { count: number };
  delegation_records: { count: number };
  subagent_boundaries: { count: number };
  refinements: { claim: number; classification: number; evidence: number; graph: number; identity: number };
  non_link_diagnostics: { count: number };
}
```

### 4.2 新增提取器（`lib/db-import.ts`）

| 提取器 | SELECT 源 | 解析要点 |
|--------|-----------|---------|
| `extractClaimsFromDb` | `responsibility_claims` | `evidence_refs_json`/`competing_explanations_json` 用现有 `parseJsonArray`/`parseJsonObject`；`timestamp_ns` 从 `phase` 提取（现有 `parsePhaseIndicator`）；表缺失返回 `[]` |
| `extractTracebacksFromDb` | `responsibility_tracebacks` | chain node/edge ids 解析为字符串数组 |
| `extractIntentsFromDb` | `intents` | 映射到**现有 `Intent`**（`lib/types.ts:182`）：`pid`+`process_start_time_ns` → `process_id: { pid, start_time_ns }` 嵌套；`primary_matching_eligible` INTEGER → boolean；`tool_args_json` → `tool_args: unknown[]`（JSON.parse，真实形态 `Array<{Path\|Content\|Command}>`；**禁用** `parseMatchedFields`——它只认 NormalizedArgs 对象形态，会清空全部 Path）|
| `extractActionsFromDb` | `actions` | 映射到**现有 `Action`**（`lib/types.ts:195`）：`normalized_args_json` → `normalized_args: unknown[]`（JSON.parse，真实形态 `Array<{Path}>`，与现有类型天然匹配；**禁用** `parseMatchedFields`）|
| `extractDatasetOverviewFromDb` | 全部概览表 | 每表 `COUNT(*)` + 关键分布 `GROUP BY`（match_level / claim_type / severity / tool_name / authority_source / action_type / signal_class）；单表缺失跳过；全部 try/catch |

> **落点**：5 个新提取器与 `parseJsonArray`/`parseJsonObject` 同放 `db-import.ts`（后二者是模块私有、未 export，新提取器留在同文件即可复用，无需导出）。

### 4.3 `DbImport` 与 store 扩展

```ts
// DbImport 新增字段
claims: ResponsibilityClaim[];
tracebacks: ResponsibilityTraceback[];
intents: Intent[];
actions: Action[];
overview: DatasetOverview;
```

- `AppStore` 新增 `claims` / `tracebacks` / `intents` / `actions` / `overview`（默认空数组 / 空概览）。
- `applyDbImport` 一并填充；`loadRun` 的 db 路径自动获得新字段。
- 作用域：db 导入路径通过新提取器填充 `intents`/`actions`；实时/后端 run 路径若已有 `getEvidence`（`lib/api.ts:79`）返回的 `intents`/`actions`，可一并存入 store（前端复用，不改后端）；后端无 db 时保持 `[]` + 空概览，UI 空态兜底。
- `claims`/`tracebacks`/`overview` 目前仅 db 导入路径有数据（后端 API 无对应端点），实时路径空态兜底。
- **`hasConsumableData` 语义更新**（`db-import.ts:444`）：可消费判定纳入新表——`graph.nodes.length > 0 || alerts.length > 0 || evidence.length > 0 || claims.length > 0 || intents.length > 0 || actions.length > 0`；否则仅新表有数据的 db 会被误判「不可消费」而 fallback 到 API。

## 五、UI 落位设计（agentsight 风格「列表 → 展开详情」）

### 5.1 告警工作台（investigations 页）— 责任声明 + 回溯链

中栏「证据链」面板下方新增 **「责任声明」** 面板（`SectionPanel`）：

- **列表态**：每张 claim 一行可展开条目，采用 agentsight **FrictionRow 视觉**（severity 图标 + category + title + detail + count）——severity 图标（Critical/Warning/Info 着色）+ `claim_type` 类别徽章（category 标签）+ **title**（`subject_agent_id` AgentAvatar + shortAgentId + 行为摘要）+ **detail**（`policy_id`/`boundary_id` mono 截断 + `claim_state` + 置信度进度条）+ 证据数/回溯标记。按 `timestamp_ns` 排序（同时间戳以 `claim_id` 次键，实测 19 distinct phase / 22 条），虚拟列表。
- **展开态**（内联）：
  - **回溯链**：**反向索引**——真实外键是 `traceback.claim_id → claim`（实测 22 条 claim 的 `traceback_ref` **全为 NULL**，spec 边界事实 #3 亦然，5.1 不得按 `claim.traceback_ref` 取链）；提取后构建 `Map<claim_id, ResponsibilityTraceback>`（`tracebacksByClaimId`）按 claim_id 取链，`claim.traceback_ref` 仅作未来 run 的次选 hint。用已加载 graph 解析 `chain_node_ids`（**去重**，实测 4/22 链含重复 UUID）→ 渲染 **Agent → Resource → PolicyBoundary** 节点链（复用时间线/证据链的编号 + 连接线视觉），根因节点（`root_cause_node_id`）高亮标「根因」；下方显示 `explanation_summary`（多行文本）与链置信度。
  - **证据引用**：`evidence_refs` 映射到已加载 evidence 显示路径摘要（`matched_fields.canonical_path` 等），无法解析的 id 原样展示。
  - **竞争解释**：`competing_explanations` 存在时折叠展示（label + rationale + rule_id）。
- **弱关联**：告警侧引用集合 = 已加载图 `graph.edges[].evidence_refs` 的**并集**（用现有 `collectAlertEvidenceRefs`，`lib/investigation.ts:49`；实测本 run 的 alert timeline 侧 `evidence_ref` 全为 None，弱关联**主走图边**），与 claim.evidence_refs 交叠计数，显示「与该告警共享 N 条证据」（只做展示，不做跳转强绑定）。

### 5.2 追踪页（traces 页）— 意图动作链（OTel execute_tool 语义）

**升级现有 `IntentActionMatch`**（`components/ui/intent-action-match.tsx`，现仅显示 `intent_id→action_id` 8 位片段——正是黑盒子）为完整 OTel `execute_tool` 卡片，并在追踪页新增 **「意图动作链」** 视图（`SectionPanel`，置于「责任链路」下方或页尾）。组织遵循 [OTel GenAI](https://opentelemetry.io/docs/specs/semconv/gen-ai/) 的 `execute_tool` 树语义——**Claude 的工具意图（intent）→ 系统边界实际效应（action）→ 匹配证据（evidence）**，让「Claude Code 想做什么 vs 系统实际做了什么」清晰可见：

- **数据**：db 提取的 `intents`/`actions` + `correlation_evidence` 内存 join。关联是**数据库真实外键**：1823 条 evidence 全部含 `intent_id`+`action_id`，但**多条 evidence 关联同一 intent/action**（真实数据：22 distinct intent / 174 distinct action，每条 evidence 对应 2–22 条 evidence 的 action）——按 **intent 分组聚合**（≈22 组），组内列出该 intent 触发的 actions 与 evidence；**孤儿条目**——无 evidence 关联的 intent/action（实测 300 actions / 1 intent）灰显列出，不丢弃。
- **intent 组头**（`execute_tool` span）：`tool_name` + `tool_args`（mono `<pre>` 截断）+ `intent_class` 徽章 + `parse_state` + `authority_source`（意图权威来源——SseResponseToolUse / TextFallback）+ 组内 evidence 计数。
- **action 卡片**（系统效应）：`action_type` + `signal_class`（HighSignal 着色）+ `derivation_mode` + `normalized_args` 路径/命令摘要（mono，短路径）。
- **关联证据**：`match_level`（L1–L5 配色）+ 置信度进度条。
- **升级后的 `IntentActionMatch`**（证据 dock 与追踪页共用）从 id 片段升级为 tool_name + action_type + normalized_args 摘要。
- **规模处理**：1823 条 evidence / 174 条 action 用现有 `VirtualList` 虚拟化。
- **空态**：无意图动作数据时显示「此数据源未提取到意图/动作关联」。

### 5.3 概览页（overview 页）—「数据源详情」agentsight 风格组合 + 去演示化

新增 **「数据源详情」** 区（置于图表行下方），采用 agentsight Dashboard 的组合形态，全部数值由纯函数 `deriveDatasetSummary` 从 `DatasetOverview` 派生（不幻觉：算不出的留空）：

- **概要带（SummaryBand 借鉴）**：
  - 头部：数据源 label + **健康徽章**（存在 Critical claim/alert ? 「需关注」 : 「正常」）+ 身份行（`runs.scenario_id` / `runs.mode` / collector_version）
  - 元信息：时间范围（`runs.started_at_ns`→`ended_at_ns`）、时长、`identity_process_map` 映射数
  - **6× KPI tile**（各带 hint）：
    1. 进程 `process_events`（hint：—）
    2. 文件活动 `actions` 中 File* 数（hint：FileRead/FileWrite 分解）
    3. 工具调用 `intents`（hint：按 tool_name 分解）
    4. 网络 `tls_sessions`（hint：—）
    5. 责任声明 `claims`（hint：按 severity 分解；Critical 时 tile 变红）
    6. 告警 `alerts`（hint：按 severity 分解）
- **信号面板（FrictionSignals 借鉴）**：从数据 derive 的异常列表，**按规则聚合**——一条规则一行，×count 徽章显示命中数（agentsight FrictionRow 视觉），不逐条列出；每项 = severity 图标 + category + title + detail + ×count——
  - 上下文污染事件 `context_pollution_events`（Critical：source_agent + contaminated_artifact 摘要，真实数据 219 条聚合）
  - Critical 责任声明 `claims.severity = Critical`（Warning：claim_type + subject，真实数据 3 条聚合）
  - 弱匹配证据 `evidence.match_level = L5Substring`（Info：真实数据 830/1823 = 45.5% 占比）
  - 策略违规 `policy_evaluations.judgment_json.result === 'Violation'`（Warning：真实数据 755 条全 Violation，聚合为单条 ×755；detail 按 boundary 聚合 top N）
- **活动时间线（ActivityChart 借鉴）**：intents / actions / claims / pollution 按时间分桶的堆叠分布（复用现有 `TrendChart` 或纯 CSS）。
- **效果画像（EffectProfile 借鉴）**：动作类型分布（FileRead/FileWrite/ProcessExec…）+ top 文件（从 `actions.normalized_args[i].Path` 统计——真实形态是数组对象，**不存在** `canonical_path`）+ 相对混合条。
- **统计卡组**（补全各表 count）：图（快照帧/节点/边）、责任（告警/声明/回溯）、意图动作（意图/动作/证据分布）、上下文（污染/策略评估）、运行时（进程/TLS/身份映射/身份/委派/边界）。
- **去演示化**：替换现有**人造 KPI**——`graph.edges.length * 72`（Live Events/s）、`* 41`（Responsibility Edges）、`72 + totalEvidence * 4.3`%（Evidence Coverage）、写死的 spark 数组——全部改为真实 counts。这是「不幻觉」的直接兑现。

### 5.4 数据源选择器 — 七项统计

- 数据源 trigger 与来源条目（`run-selector.tsx`）现有 `节点 · 告警 · 证据` meta 扩展为七项：**节点 · 告警 · 证据 · 声明 · 回溯 · 意图 · 动作**（复用 `DbImport` 的 counts + 新 overview）。
- 「任意目录导入」已由 `input[type=file]` 支持（`accept` 含 `.db/.sqlite/.sqlite3`），保持单选（多 db 合并已砍）。

## 六、测试计划（TDD）

### 6.1 修正 fixture（`scripts/gen-db-fixture.mjs`）— 消除幻觉

- `responsibility_claims` 改为**真实 schema**：`claim_id, claim_type, subject_agent_id, policy_id, boundary_id, claim_state, severity, confidence, evidence_refs_json, competing_explanations_json, traceback_ref, phase, run_id`（删除错误简化的 `alert_id` 列）。
- 新增 `intents` / `actions` 表（真实 schema）与样例行——样例行须覆盖既有 `correlation_evidence` 的 `int-1`/`int-2`/`act-1`/`act-2` 引用。
- `responsibility_tracebacks` 保持真实 schema（已正确）；加一条 `traceback.claim_id ↔ claim` 双向链接样本（验证 🔴-2 反向映射）。
- 更新后重新生成 `__tests__/fixtures/db-sample.sqlite`。

### 6.2 单测

| 层 | 文件 | 用例 |
|----|------|------|
| db-import | `__tests__/db-import.test.ts` | 5 个新提取器解析真实结构；表缺失返回空；phase→timestamp_ns 提取 |
| db-import | 同上 | `loadDatabase` 产出含 claims/tracebacks/intents/actions/overview |
| overview | `__tests__/overview-data.test.ts` | `extractDatasetOverviewFromDb` 分布聚合正确（by_type/by_severity/by_match_level/by_tool_name/by_action_type 等）；**`deriveDatasetSummary` 纯函数派生正确**——健康徽章（Critical claim/alert 判定）、6× KPI tile（进程/文件/工具/网络/声明/告警）、信号面板（污染/Critical/L5 弱匹配/策略异常四项规则）、统计卡组全部从 `DatasetOverview` 计算，算不出的字段返回空（不幻觉）|
| store | `__tests__/store-db.test.ts` | `applyDbImport` 填充全部新字段 |
| db-import | 同上 | `tool_args_json`/`normalized_args_json` **真实数组形态** `[{Path|Content|Command}]` 解析（断言**未**用 `parseMatchedFields`）；traceback **反向索引** `tracebacksByClaimId`（`claim_id` 主键，`traceback_ref` NULL 兜底）；`hasConsumableData` 纳入新表 |
| 组件 | `__tests__/claim-list.test.tsx` | 责任声明列表 + 展开回溯链渲染（反向映射取链 + 根因节点高亮 + 链节点去重）；弱关联交叠计数（`collectAlertEvidenceRefs`）；空态；evidence_refs 不可解析引用原样展示；排序平局以 claim_id 次键 |
| 组件 | `__tests__/intent-action-chain.test.tsx` | 意图动作链 join 渲染（多 evidence 聚合到同一 action；孤儿 intent/action 灰显；空态） |
| 组件 | `__tests__/dataset-overview-panel.test.tsx` | 概览统计卡片渲染 |

### 6.3 e2e（`e2e/db-import.spec.ts` 扩展）

- 导入真实 db（`20260716-114859`，22 claims / 22 tracebacks / 23 intents / 474 actions）后：
  - trigger 七项统计出现
  - **同步既有断言**：`EXPECTED.meta`（`e2e/db-import.spec.ts:63`）由三项精确字符串 `'1461 节点 · 13 告警 · 1823 证据'` 更新为七项（节点·告警·证据·声明·回溯·意图·动作）
  - 告警页责任声明区出现 `responsibility_claims` 数据、展开渲染回溯链（经 `tracebacksByClaimId` 反向映射）
  - 追踪页意图动作链出现 intents/actions
  - overview 数据源详情出现真实 counts

## 七、实施顺序

1. **数据层**：types.ts 新增类型 → db-import 5 提取器 → store 扩展 → 修正 fixture → 单测（TDD 红绿）
2. **概览层**：overview 去演示化 + 数据源详情面板 → 单测
3. **责任层**：告警工作台责任声明 + 回溯链 → 组件单测
4. **意图层**：追踪页意图动作链 → 组件单测
5. **入口层**：数据源选择器七项统计
6. **回归**：`npx tsc --noEmit` + `npx eslint` + `npm run build` + vitest 全绿 + e2e chromium/firefox

每阶段 TDD：先写失败测试 → 最小实现 → 重构。

## 八、非目标

- 不做多 db 合并（用户已确认砍掉）。
- 不做 token 统计（db 无此数据）。
- 不做 claim↔alert 强绑定（无外键）。
- 不改 Rust collector / 后端 API。
- 不改 3D 首页与侧边栏导航结构。
- refinements 系列、`non_link_diagnostics`、`sqlite_*` 仅概览计数，不新增详情页。
