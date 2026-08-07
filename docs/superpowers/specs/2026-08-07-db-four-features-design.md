# 数据库深链消费四功能设计 — 取证链 / 身份血缘 / 原始事件浏览器 / 图时间演化

**日期**: 2026-08-07
**状态**: 已实现（M0–M4 全绿：type-check / lint 0 警告 / vitest 293 / build）
**范围**: 在已完成的「数据库全表消费第一波」（claims/tracebacks/intents/actions/DatasetOverview/运行监控，commit a6cd724）之上，对剩余 12 张表的**全行深链消费**，织入四个前端功能。
**前置**: 第一波已实现并全绿（db-import.ts 现有 874 行，store 已含 claims/tracebacks/intents/actions/overview/monitoring）。

---

## 〇、本设计的质量底线

沿用第一波的「**不幻觉**」哲学：所有 KPI、链、树、帧 diff 均由**纯函数**从导入数据派生；任何算不出的字段诚实留空/标注。本设计新增第二条底线——**数据量防线**：真实 run 的 `process_events`/`tls_sessions`/`graph_snapshots` 可能达数十万行/数十帧，任何全量读入浏览器内存的操作都必须有上限 + 「已展示 X / total」诚实标注。

## 一、现状核验与关键事实（已实测）

| # | 事实 | 对设计的影响 |
|---|------|-------------|
| 1 | **前端只能读 `collector.responsibility.db`**（`/api/runs/<id>/db` 打开 responsibility.db，`schema.rs` 27 表）。`violation_pattern_matches`/`intent_events`/`action_events` 只在 causal.db（`trace.log.causal.db`，causal_schema.rs）——**前端不可达** | 取证链的「引擎判定」改用 alert 已有的 `engine_section_json`（rule_verdicts/pattern_verdicts 已在其中），不引入 causal 表 |
| 2 | **第一波只读了 6 张表的全行**（graph_snapshots 最新帧 / alerts / evidence / claims / tracebacks / intents / actions）；**12 张表只剩聚合或 count**：`context_pollution_events`（按 source 聚合）、`policy_evaluations`（只 boundary+judgment）、`non_link_diagnostics`（只按 reason 聚合）、`process_events`（只 count）、`tls_sessions`（只聚合）、`agent_identities`/`provisional_identities`/`identity_process_map`/`message_bindings`/`delegation_records`/`subagent_boundaries`（只 count）、`graph_snapshots`（只最新帧 + metrics 帧序） | 本设计的 11 个新全行提取器 |
| 3 | **真实 run 实测行数**（旧 spec 附录）：`context_pollution_events` 219 / `policy_evaluations` 755 / `non_link_diagnostics` 2223 / `process_events` 160 / `tls_sessions` 1067 / `provisional_identities` 196 / `identity_process_map` 196 / `graph_snapshots` 55；`agent_identities`/`subagent_boundaries`/`delegation_records`/`message_bindings` **0 行** | 四个功能的真实数据支撑度不同（见 §二），空表必须设计空态而非伪造 |
| 4 | `graph_snapshots` 已含 `extractSnapshotMetricsFromDb`（只读 metrics_json，**绝不解析 nodes/edges_json**）——帧时序的骨架已有 | 图演化在其上新增「全量帧」提取，须**沿用同样的纪律**：仅在帧激活时解析该帧的 nodes/edges |
| 5 | alert.`engine_section_json` 已解析进 `alert.engine`（`EngineSection`：rule_verdicts + pattern_verdicts + score_breakdown） | 取证链的「引擎判定」**零成本取用**，无需新表 |

## 二、四个功能 × 真实数据支撑度（诚实矩阵）

| 功能 | 消费表（全行） | 真实 run 支撑 | 核心价值 |
|------|---------------|--------------|---------|
| **A 告警取证链** | pollution(219) + policy(755) + nld(2223) + 复用 engine/alerts/claims/evidence | **满** | 引擎判定→污染传播→策略判定→证据声明 全链可溯 |
| **B 身份血缘** | provisional(196) + identity_map(196) + process_events(160) | **中**（家族树/升级表恒空→空态） | 进程→身份归属谱系（内核级血缘）+ 委派家族树（代理级，when-data-exists） |
| **C 原始事件浏览器** | process(160) + tls(1067) + intents(23) + actions(474) | **满** | 真实事件流可筛可查可展开 |
| **D 图时间演化** | graph_snapshots 全帧(55) | **满**（55 帧） | 责任图随事件演化的时间滑杆 |

> **B 的诚实重构**：真实 run 的 `subagent_boundaries`/`agent_identities` 为 0 行，「委派家族树」「临时→正式升级」在真实数据下恒空——设计以**进程谱系**为核心（有真实数据），家族树/升级为 when-data-exists 的扩展 + 空态。这与第一波「agent_identities 等概览计数诚实显示 0」一致。

## 三、里程碑 0 · 数据地基 — 11 个全行提取器

### 3.1 新增类型（`lib/types.ts`）

```ts
/** context_pollution_events 全行 */
export interface ContextPollutionEvent {
  pollution_id: number;
  source_agent_id: string;
  contaminated_artifact: string;
  propagation_path: string[];      // propagation_path_json，agent 序列
  detected_at_ns: number;
  run_id: string | null;
}

/** policy_evaluations 全行 — judgment_json 解析为 { result, ... } */
export interface PolicyEvaluation {
  policy_id: string;
  boundary: string;
  subject_agent_id: string;
  judgment: { result?: string; [k: string]: unknown } | null;
  resource: NormalizedArgs | null;  // resource_json；非 NormalizedArgs 形态时 null
  evidence_refs: string[];
  confidence: number;
  reason: string;
  run_id: string | null;
}

/** non_link_diagnostics 全行 */
export interface NonLinkDiagnostic {
  diagnostic_id: string;
  intent_id: string | null;
  action_id: string | null;
  reason: string;
  timestamp_ns: number;
  run_id: string | null;
}

/** agent_identities 全行 */
export interface AgentIdentity {
  agent_id: string;
  process_ids: Array<{ pid: number; start_time_ns: number }>;
  message_ids: string[];
  tls_sessions: string[];
  confidence: string;              // 'Authoritative' 等
  provisional_refs: string[];
  run_id: string | null;
}

/** provisional_identities 全行 */
export interface ProvisionalIdentity {
  agent_id: string;
  process_ids: Array<{ pid: number; start_time_ns: number }>;
  primary_signal: string;          // 'TlsSessionNew' 等
  confidence: number;
  run_id: string | null;
}

/** identity_process_map 全行 */
export interface IdentityProcessMapEntry {
  agent_id: string;
  pid: number;
  start_time_ns: number;
}

/** process_events 全行 + 身份标注（JOIN identity_process_map） */
export interface ProcessEvent {
  process_event_id: number;
  pid: number;
  start_time_ns: number;
  parent_pid: number | null;
  parent_start_time_ns: number | null;
  comm: string;
  timestamp_ns: number;
  agent_id: string | null;         // JOIN 标注；无映射时 null
}

/** tls_sessions 全行 + 身份标注 */
export interface TlsSession {
  tls_session_id: string;
  pid: number;
  start_time_ns: number;
  timestamp_ns: number;
  agent_id: string | null;         // JOIN 标注
}

/** subagent_boundaries 全行 */
export interface SubagentBoundary {
  boundary_id: string;
  parent_agent_id: string;
  child_agent_id: string;
  parent_pid: number;
  parent_start_time_ns: number;
  child_pid: number;
  child_start_time_ns: number;
  reason: string;
  run_id: string | null;
}

/** graph_snapshots 演化帧（kept 帧提取时即解析 nodes/edges） */
export interface GraphSnapshotFrame {
  snapshot_id: string;
  timestamp_ns: number;
  graph: GraphData;                // 提取时即解析（loadDatabase 关闭 db 后无法再懒加载）
  metrics: GraphMetrics | null;
}
```

### 3.2 新增提取器（`lib/db-import.ts`）

| 提取器 | SELECT 源 | 解析要点 | 数据量防线 |
|--------|-----------|---------|-----------|
| `extractPollutionEventsFromDb` | `context_pollution_events` 全行 | `propagation_path_json` → string[]（JSON.parse，坏 JSON → []） | 无（219 行） |
| `extractPolicyEvaluationsFromDb` | `policy_evaluations` 全行 | `judgment_json` → object；`resource_json` 按 NormalizedArgs 形态解析，不匹配 → null；`evidence_refs_json` → string[] | 无（755 行） |
| `extractNonLinkDiagnosticsFromDb` | `non_link_diagnostics` 全行 | 全列直映 | 无（2223 行） |
| `extractAgentIdentitiesFromDb` | `agent_identities` 全行 | `process_ids_json`/`message_ids_json`/`tls_sessions_json`/`provisional_refs_json` → 数组 | 无（真实 0 行） |
| `extractProvisionalIdentitiesFromDb` | `provisional_identities` 全行 | `process_ids_json` → 数组 | 无（196 行） |
| `extractIdentityProcessMapFromDb` | `identity_process_map` 全行 | 直映 | 无（196 行） |
| `extractProcessEventsFromDb(db, cap=25000)` | `process_events` 全行 | 先 JOIN identity_process_map 标注 `agent_id`；`ORDER BY (agent_id IS NOT NULL) DESC, timestamp_ns ASC LIMIT cap`（**已识别进程优先入 cap**，保证血缘树完整） | **cap 25k + totalCount + truncated** |
| `extractTlsSessionsFromDb(db, cap=25000)` | `tls_sessions` 全行 | 同上 JOIN 标注 | **cap 25k + totalCount + truncated** |
| `extractSubagentBoundariesFromDb` | `subagent_boundaries` 全行 | 直映 | 无（真实 0 行） |
| `extractSnapshotFramesFromDb(db, maxFrames=30)` | `graph_snapshots` 全帧 | **连续相同 metrics 帧去重**；**超过 maxFrames 时均匀降采样**（复用本文件导出的 `pickRepresentativeFrames`，见 §7.1 同源）；**kept 帧的 nodes_json/edges_json 提取时即解析为 `graph`**（db 关闭后无法懒加载；坏 JSON → 空图），内存由 ≤30 帧 + 去重限界 | 帧数 ≤ 30 + 去重 |

> **process_events/tls_sessions 的 JOIN 标注**：提取时一次性 `LEFT JOIN identity_process_map ON pid+start_time_ns`（start_time_ns 作次键），命中即 `agent_id`，未命中 null。**血缘树的完整性由「已识别进程优先入 cap」保证**——身份相关进程不因 cap 被截断。

### 3.3 `DbImport` / store 扩展

```ts
// lib/db-import.ts
export interface DbAssets {
  pollutionEvents: ContextPollutionEvent[];
  policyEvaluations: PolicyEvaluation[];
  nonLinkDiagnostics: NonLinkDiagnostic[];
  agentIdentities: AgentIdentity[];
  provisionalIdentities: ProvisionalIdentity[];
  identityProcessMap: IdentityProcessMapEntry[];
  processEvents: ProcessEvent[];           // 已 cap
  processEventCount: number;               // totalCount
  processEventTruncated: boolean;
  tlsSessions: TlsSession[];               // 已 cap
  tlsSessionCount: number;
  tlsSessionTruncated: boolean;
  subagentBoundaries: SubagentBoundary[];
  snapshotFrames: GraphSnapshotFrame[];    // 去重 + 降采样
}
// DbImport 新增字段
dbAssets: DbAssets;
```

- `AppStore` 新增 `dbAssets: DbAssets | null`（默认 null）+ `snapshotFrameIndex: number`（默认 0）+ `setSnapshotFrameIndex(index: number)` action。
- `applyDbImport` 一并填充 `dbAssets`；`loadSample`/`loadDemo` 置 null（无 db 数据，四功能空态兜底）。
- **`hasConsumableData` 语义更新**：纳入 `processEvents.length > 0 || tlsSessions.length > 0`（仅 process/tls 有数据的 db 也应可消费）。

### 3.4 fixture 造数（`scripts/gen-db-fixture.mjs`）

现有 4 帧 graph_snapshots（已够 D）、1 行 pollution（扩到 3 行、含 `propagation_path` 数组）、nld/policy 各 1 行（扩到 4/6 行）。**新增**（当前 0 行）：

- `process_events` ~8 行父子进程链（2 组：claude → bash → sh → 工具；claude → node → script），`identity_process_map` 相应标注 agent
- `provisional_identities` 3 行（含 `TlsSessionNew` 等 primary_signal）；`agent_identities` 2 行（1 个「已升级」agent 同时出现在两张表）
- `subagent_boundaries` 2 行（parent→child agent 委派）+ `delegation_records` 2 行（带 timestamp_ns 供诞生时序）
- `tls_sessions` 扩到 6 行（多个 pid）
- `context_pollution_events` 扩到 3 行，其中 1 行的 `propagation_path` 覆盖 ≥2 hop（writer→middle→reader）
- `policy_evaluations` 扩到 6 行，含 1 行 `result: 'Violation'` + 1 行 `result: 'Allow'`

重新生成 `__tests__/fixtures/db-sample.sqlite`。

### 3.5 测试（`__tests__/db-import.test.ts` 扩展）

| 用例 | 断言 |
|------|------|
| pollution 全行解析 | 3 行；`propagation_path` 数组正确（含 2 hop 行）；坏 JSON → [] |
| policy 全行解析 | 6 行；`judgment.result` Violation/Allow；`resource` 非 NormalizedArgs → null |
| nld 全行解析 | 4 行全列 |
| identity 三表解析 | provisional 3 / agent_identities 2（升级交集 1）/ identity_map 行数 |
| process JOIN 标注 | 已识别 pid 的 `agent_id` 正确；未识别 → null；**已识别进程优先于 cap**（造 >cap 的合成 db 验证） |
| tls JOIN 标注 + cap | 同上 |
| subagent/delegation | 2/2 行直映 |
| snapshotFrames | 去重后帧数 ≤ 30；连续相同帧被合并；>maxFrames 均匀降采样；kept 帧 `graph` 提取时即解析（坏 JSON → 空图） |
| `loadDatabase` | 产出 `dbAssets` 全字段；`hasConsumableData` 纳入 process/tls |
| store | `applyDbImport` 填充 `dbAssets` + `snapshotFrameIndex`；sample/demo → null |

## 四、里程碑 1 · 告警取证链（investigations 工作台）

### 4.1 `lib/forensic-chain.ts`（纯函数）

```ts
export interface EngineVerdict {
  kind: 'rule' | 'pattern';
  id: string;                // rule_id / template_id
  score: number;
  reason: string;            // 从 verdict 结构尽力提取；无 → ''
  targets: string[];         // matched targets（pattern 时）
}
export interface PollutionChain {
  source_agent_id: string;
  contaminated_artifact: string;
  hops: string[];            // propagation_path；写者 → 中间 → 读者
  detected_at_ns: number;
  dt_ns: number;             // 该 alert 主体参与 hop 的最晚检测距 writer 首写的时延（尽力）
}
export interface PolicyVerdictRow {
  boundary: string;
  result: string | null;     // 'Violation' | 'Allow' | null
  confidence: number;
  reason: string;
}
export interface ForensicChain {
  verdicts: EngineVerdict[];             // alert.engine.rule_verdicts + pattern_verdicts
  pollutionChains: PollutionChain[];     // 按 involved_agents ∩ source_agent_id
  policyRows: PolicyVerdictRow[];        // 按 subject_agent_id ∈ involved_agents
  diagnostics: NonLinkDiagnostic[];      // 按 intent_id ∈ alert timeline 的意图
  claimRefs: string[];                   // claims evidence_refs ∩ alert 证据
}
export function buildForensicChain(input: {
  alert: ResponsibilityAlert;
  pollutionEvents: ContextPollutionEvent[];
  policyEvaluations: PolicyEvaluation[];
  nonLinkDiagnostics: NonLinkDiagnostic[];
  claims: ResponsibilityClaim[];
  intents: Intent[];
}): ForensicChain;
```

> **证据/声明跳转的取用源**：`buildForensicChain` 只管四区（判定/污染/策略/诊断）的**数据组装**，不接 evidence。workbench 组件层已有现成证据索引 `collectAlertEvidenceRefs`（`lib/investigation.ts:49`，图边 evidence_refs 并集）+ `buildTracebacksByClaimId`（`lib/traceback-index.ts`），取证链组件直接复用它们做「verdict → evidence → claim → traceback」的跳转渲染——不重复建证据管道。
- **弱关联纪律**：pollution/policy 按 `involved_agents`（含 primary_agent）匹配 `source_agent_id`/`subject_agent_id`；**无强外键则做集合交叠，不做强绑定**（沿用第一波 §边界事实 2）。
- **空态诚实**：某源无数据则该项显示「无」而非伪造。

### 4.2 `components/investigation/forensic-chain.tsx`

在 workbench 中栏新增「**取证链**」SectionPanel（置于「责任声明」之后），四个子区：
1. **引擎判定**（判定卡 grid）：rule/pattern 徽章 + id mono + score 进度条 + reason；pattern 的 targets 摘要
2. **污染传播链**（线性链视觉，复用证据链的编号+连接线）：写者 agent → artifact → 读者 agent，hop 间标注 dt；涉及 alert 主体的 hop 高亮
3. **策略判定**（边界矩阵，仿 kill-chain 完成度矩阵）：boundary × result（Violation 红 / Allow 绿 / null 灰）+ confidence + reason
4. **失配诊断**（列表）：nld reason + intent 关联 + timestamp，与「事件时间线」互链（点击定位）

测试 `__tests__/forensic-chain.test.tsx`：buildForensicChain 四区正确；无 pollution 数据 → `pollutionChains: []`；verdict 结构缺 reason → ''；弱关联交叠计数。

## 五、里程碑 2 · 身份血缘（overview）

### 5.1 `lib/identity-lineage.ts`（纯函数）

```ts
export interface ProcessTreeNode {
  pid: number;
  start_time_ns: number;
  comm: string;
  agentId: string | null;
  children: ProcessTreeNode[];
}
export interface AgentProcessTree {
  agentId: string;
  primarySignal: string | null;    // provisional.primary_signal
  confidence: number | null;       // provisional.confidence
  status: 'provisional' | 'authoritative';  // 是否出现在 agent_identities
  roots: ProcessTreeNode[];        // 该 agent 的进程树（已识别进程优先完整）
  orphanPids: number[];            // identity_map 有但 process_events 无记录的 pid
}
export interface AgentFamilyNode {
  agentId: string;
  children: Array<{ childAgentId: string; reason: string; childPid: number }>;
}
export function buildAgentProcessTrees(input: {
  provisionalIdentities: ProvisionalIdentity[];
  agentIdentities: AgentIdentity[];
  identityProcessMap: IdentityProcessMapEntry[];
  processEvents: ProcessEvent[];
}): Map<string, AgentProcessTree>;
export function buildAgentFamilyTree(subagentBoundaries: SubagentBoundary[]): Map<string, AgentFamilyNode>;
```

设计要点（来自 span-tree 调研）：
- **进程树**：`buildAgentProcessTrees` 按 agent 分组 identity_map 的 pid → 用 process_events 的 parent_pid 组树（parent 不在该 agent 时挂到根）；`status` 由「agent_id 是否出现在 agent_identities」判定（provisional→authoritative 升级即两表交集）。
- **家族树**：`buildAgentFamilyTree` 由 subagent_boundaries 组 parent→child 委派树，按需展开。
- **级联过滤**：overview 页选中 agent → 高亮其进程树 + 其事件（复用 store 的 alert/evidence，事件过滤进里程碑 3 的浏览器）。

### 5.2 组件

- `components/overview/agent-process-tree.tsx`：overview 新增「**进程谱系**」面板，agent 列表（provisional 徽章 / authoritative 徽章）→ 点选展开该 agent 的进程树（**默认折叠**，展开按需渲染，防嵌套爆炸——调研教训：嵌套渲染在负载下崩）。进程节点显示 pid + comm + 归属 agent 徽章。
- `components/overview/agent-family-tree.tsx`：委派家族树（when-data-exists；空态「此数据源无委派边界」）。

测试 `__tests__/identity-lineage.test.ts`：进程树组树（父子嵌套正确、跨 agent 父挂根）、orphanPids、升级交集 status、家族树父子映射、空数据空态。

## 六、里程碑 3 · 原始事件浏览器（侧边栏「事件」dock）

### 6.1 `lib/raw-event-browser.ts`（纯函数）

```ts
export type RawEventKind = 'process' | 'tls' | 'intent' | 'action';
export interface RawEventRow {
  id: string;
  kind: RawEventKind;
  timestampNs: number;
  agentId: string | null;
  title: string;     // comm / tls_session_id / tool_name / action_type
  detail: string;    // args/路径 摘要（截断）
  raw: unknown;      // 展开时显示的结构化 JSON
}
export interface RawEventFilters {
  kinds: RawEventKind[];         // 空 = 全部
  agentId: string | null;        // 级联：选 agent → kinds 可用集合收窄
  text: string;                  // 防抖全文（title/detail/comm/args）
  sinceNs: number | null;        // 时间范围
}
export function buildRawEventRows(input: {
  processEvents: ProcessEvent[];
  tlsSessions: TlsSession[];
  intents: Intent[];
  actions: Action[];
}): RawEventRow[];                       // 按 timestampNs 升序
export function filterRawEventRows(rows: RawEventRow[], filters: RawEventFilters): RawEventRow[];
export function summarizeRawEvents(rows: RawEventRow[]): Record<RawEventKind, number>;
export function availableKindsForAgent(rows: RawEventRow[], agentId: string): RawEventKind[];
```

设计要点（来自 log-explorer 调研，吸收最全）：
- **多维度筛选**：kind 徽章多选 + agent 下拉（级联收窄可用 kind）+ 防抖文本搜索（350ms）+ 时间范围；`filterRawEventRows` 纯函数、消费方 useMemo。
- **级联过滤纪律**（调研确认的 Langfuse bug 教训）：选 agent 后 kind 徽章只显示该 agent 实际有的种类，避免「选 agent 后 kind 筛选结果为空」的信任侵蚀。
- **虚拟化**：复用现有 `VirtualList`（已被 workbench/traces 使用）。
- **增量展示**：初始渲染 300 行，滚动近底（`scrollHeight - scrollTop - clientHeight < 100`）「加载更多」+300，直到耗尽；头部「已展示 X / total(+截断标记)」。
- **展开行**：点击展开显示结构化 JSON（args/tool_args/matched_fields），mono 格式化。
- **过滤状态持久化**：localStorage（dock 是 layout 全局，不宜 URL 参数）。

### 6.2 `components/command-center/raw-event-browser.tsx`

- **入口**：incident-rail「事件」dock 头部新增「原始事件流」动作 → 打开 **drawer 覆盖层**（右侧 720px，可关），**保留 3-dock 统一**（不改 dock 隐喻）。
- drawer 内：筛选条（kind pills / agent / 搜索 / 时间）+ 计数徽章 + 虚拟化列表 + 加载更多 + 展开行。
- 真实数据 0 类 kind 时该 kind 徽章隐藏（诚实）。

测试 `__tests__/raw-event-browser.test.ts`：buildRawEventRows 4 kind 归一化；filter（kind/agent/text/time 各维度 + 组合）；availableKindsForAgent 级联收窄；summarize。

## 七、里程碑 4 · 图时间演化（态势页 3D stage）

### 7.1 `lib/snapshot-evolution.ts`（纯函数）

```ts
export interface FrameDiff {
  addedNodes: string[];
  removedNodes: string[];
  addedEdges: string[];
  removedEdges: string[];
}
export function buildFrameDiff(prev: GraphSnapshotFrame, next: GraphSnapshotFrame): FrameDiff;
export function summarizeFrameChange(diff: FrameDiff): string;   // '+N 节点 / −M 边'
// 说明：pickRepresentativeFrames 在 M0 随 extractSnapshotFramesFromDb 落地于 db-import.ts
// （§3.2），snapshot-evolution.ts 从 db-import 导入复用，不在本文件重复定义。
```

设计要点（来自 temporal-graph 调研，吸收最全）：
- **提取即解析纪律**：`snapshotFrames[].graph` 在 M0 提取时即解析（db 关闭后无法懒加载）；内存上限由「去重 + ≤30 帧降采样」在提取期锁死。**`buildFrameDiff` 直接吃已解析的相邻帧 graph**（`index-1` 与 `index`），scrubber 切帧时无需再激活。
- **动画而非跳切**：react-force-graph-3d 以 node id 作 key，切帧时新增节点淡入（opacity 过渡）、移除节点淡出——**天然满足对象恒常**；**反向拖动逆行动画**由「id 恒常 + 数据替换」获得。
- **帧头时间戳**：scrubber 显示当前帧 `timestamp_ns` 格式化（相对 run 起点），随时间更新（调研：动态标题是必要）。
- **delta 摘要**：每帧显示「+N / −M」，新增节点高亮（pulse）。
- **性能**：react-force-graph-3d 为 WebGL canvas，>1000 节点无压力；帧数已被里程碑 0 降采样到 ≤30。

### 7.2 `components/graph/graph-time-scrubber.tsx`

态势页（command-center-page.tsx，MtAgentLensGraphStage 所在）在 stage 下方渲染 scrubber：
- **1D 时间轴**（hover 显示关键帧点）+ 播放/暂停 + 前后步进 + 帧索引「5 / 30」+ 当前帧时间戳 + delta 徽章。
- 驱动：`store.snapshotFrameIndex` + `setSnapshotFrameIndex`；stage 的 `graphData` 来自 `dbAssets.snapshotFrames[index].graph`（激活帧解析）。
- **仅当 `snapshotFrames.length > 1` 时显示**；0/1 帧隐藏（常见：单帧 db 不打扰）。

测试 `__tests__/snapshot-evolution.test.ts`：buildFrameDiff 增删正确（构造 prev/next 合成帧）；summarize 文案；pickRepresentativeFrames 均匀降采样（n>max 与 n≤max 两分支）。

## 八、实施顺序与里程碑划分

| 里程碑 | 内容 | 独立可测产物 | 提交 |
|--------|------|-------------|------|
| **M0 数据地基** | types + 11 提取器 + DbAssets + store + fixture + 单测 | db-import.test.ts 扩展 + store-db.test.ts | 一 |
| **M1 取证链** | forensic-chain.ts + forensic-chain.tsx + workbench 集成 | forensic-chain.test.tsx | 二 |
| **M2 身份血缘** | identity-lineage.ts + process-tree/family-tree 组件 + overview 集成 | identity-lineage.test.ts | 三 |
| **M3 原始事件浏览器** | raw-event-browser.ts + drawer 组件 + incident-rail 集成 | raw-event-browser.test.ts | 四 |
| **M4 图时间演化** | snapshot-evolution.ts + scrubber + 态势页集成 | snapshot-evolution.test.ts | 五 |

每里程碑独立 TDD：先写失败测试 → 最小实现 → 重构 → 四门全绿。**每个里程碑完成后停下验收，commit message 由用户亲自定稿**。

## 九、全局约束与验收

- **四门全绿**（每里程碑）：`npm run type-check` + `npm run lint`（0 警告）+ `npx vitest run` + `npm run build`。
- **不幻觉**：所有展示由纯函数从导入数据派生；算不出 → 留空/标注，绝不硬编码 demo 值。
- **数据量防线**：任何全量读入浏览器内存的数据有 cap + 「已展示 X / total」。
- **惰性纪律**：graph_snapshots 帧仅在激活时解析 nodes/edges；`intents`/`actions`/`correlation_evidence` 已全量读，不重复读。
- **不改 Rust collector / 后端 API**；不改 3-dock 侧边栏隐喻；不改 store 已有字段语义（只新增 `dbAssets`/`snapshotFrameIndex`）。
- **真实数据空表**：B 的家族树/升级、A 的任一空源 → 空态，不伪造。
- 新增类型在 `lib/types.ts`；派生模型在各自 lib（forensic-chain / identity-lineage / raw-event-browser / snapshot-evolution），遵循现有「lib 纯函数 + 单测」模式。

## 十、风险与已知限制

| 风险 | 缓解 |
|------|------|
| process/tls 全量读内存（真实 run 可能 10 万+） | cap 25k + totalCount + truncated 标注 + 虚拟化 |
| 血缘树在 cap 下父进程缺失 | 已识别进程优先入 cap（§3.2 ORDER BY）；父跨 cap 时挂根并标「未知父」 |
| graph 帧全解析内存峰值（真实 run 55 帧） | 去重 + ≤30 均匀降采样在提取期锁死帧数；逐帧 graph 解析（约 15MB 上限，与已加载图/告警/证据共处一内存预算） |
| 弱关联误绑（pollution/policy 无外键） | 只做 involved_agents 集合交叠，不做强绑定；空态诚实 |
| 家族树/升级表真实 run 恒空 | 设计以进程谱系为核心；家族树/升级 when-data-exists + 空态 |
| PollutionChain.dt_ns 不可算（表无「首个 writer 的检测时延」列） | 类型 `number \| null`，实现恒 `null`；渲染「—」，不臆测数值 |
| PatternVerdict 无 targets（schema 无 pattern matched targets 列） | `EngineVerdict.targets` rule 取 matched_resource、pattern 恒 `[]`；UI 摘要仅在有值时显示 |
| intents/actions 无 agent 标注（无外键 JOIN） | 原始事件浏览器中这两类行的 agentId 恒 null；按 agent 筛选级联收窄时如实收窄，不伪造归属 |

## 十一、非目标

- 不做 causal.db（`violation_pattern_matches`/`intent_events`/`action_events`）消费——前端不可达，改动需 Rust 侧，超出本次范围。
- 不做实时/SSE 流式原始事件（本次只消费已导入 db 的静态全行）。
- 不做 claim↔alert 强绑定（无外键，沿用第一波弱关联）。
- 不改 store 现有字段语义；不做多 db 合并。
