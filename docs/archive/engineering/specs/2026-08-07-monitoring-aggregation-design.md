# 运行监控聚合设计 — 把散落的监控视角做成仪表盘产品

**日期**: 2026-08-07
**状态**: 草案（架构审查修订版，待用户审阅）
**范围**: 将 10 个此前从未接入数据的 analyzer 监控视角，聚合为仪表盘（/overview）的「运行监控」区块；阶段一从责任 db 诚实派生 6 个监控信号，全部按暗色 ln 设计系统重建；阶段二 8 个实时视角（analyzer 产品）经 SSE 接入（本 spec 仅做占位与空态）。**铁律：杜绝数据臆造（Nothing is invented）。**

---

## 一、目标

前端存在 10 个 analyzer 监控视角组件（tool-execution / agent-loop / intent-verifier / session-health / agent-drift / data-exfiltration / llm-api-tracker / llm-api-anomaly / sse-content-scanner / dedup-diagnostics），约 8,000 行，但**全部是 0 引用的孤儿**——契约先行、从未接上数据。本设计将其做成产品：

1. **阶段一（本次）**：从责任 db 诚实派生 **6 个监控信号**，聚合为「运行监控」区块并入 /overview，全部用既有暗色 ln 设计系统重建。
2. **阶段二（后续）**：analyzer 实时视角经 SSE 接入（本 spec 定义占位与空态，不实现实时链路）。
3. **质量底线**：任何 KPI/状态/分布由纯函数从真实数据派生；无法从数据计算的内容诚实留空/标注——这是「不幻觉」哲学的直接兑现。

---

## 二、边界事实（诚实声明，杜绝幻觉）

以下事实经真实 db（`env/runs/20260716-114859-ecb03ced`）实测确认，是阶段一视角重定界的依据：

| # | 事实 | 对设计的影响 |
|---|------|-------------|
| 1 | `actions.action_type` 枚举仅 `FileRead / FileOverwrite / FileWrite / ProcessExec`，**无任何 LLM/API/网络类型** | LLM API 追踪、数据外泄等视角**不可从 db 派生**，阶段一不得做 |
| 2 | `graph_nodes.node_type` 仅 `Agent / PolicyBoundary / Resource`，**无 Credential / ToolCall** | 外泄视角不能基于 Credential 节点；资源足迹仅限既有三种节点 |
| 3 | db 全库**无 latency/duration 列** | 阶段一不做 RED 方法 P50/P95；时延类指标推迟阶段二 SSE |
| 4 | `policy_evaluations` **无时间戳列**（PK = policy_id + subject_agent_id） | 策略违规**无法做时间趋势**；阶段一只做比率与分布 |
| 5 | `context_pollution_events` 时间列是 **`detected_at_ns`**（非 `timestamp_ns`） | 提取器列名以 schema 唯一真源为准 |
| 6 | `graph_snapshots.nodes_json/edges_json` 单帧可到百 KB（53 帧合计 ≈ 9.4MB），早期帧 `metrics_json` 全零 | 漂移**只读 `metrics_json`**（小），绝不解析 nodes/edges_json；容忍空帧 |
| 7 | `tls_sessions` 仅 `tls_session_id / pid / start_time_ns / timestamp_ns`，**无 host/方向/字节** | 网络会话视角只做计数与时间分布，**不冒充 LLM API 调用** |
| 8 | 跨 run 各表计数差异巨大（实测 non_link_diagnostics 379→6895、pollution 0→768） | **不得用绝对数做状态阈值或测试硬断言**；状态成分全部用比率，测试用确定性合成 fixture |

---

## 三、真实表结构基线（阶段一消费依据）

> 列名以 `collector/src/responsibility/persistence/schema.rs` 为唯一真源。所有表可能缺失（旧版/小 db），提取一律 try/catch 降级。

| 视角 | 源表 | 关键列（schema 真源） | 阶段一使用 |
|------|------|----------------------|-----------|
| 拓扑漂移 | `graph_snapshots` | snapshot_id / timestamp_ns / **metrics_json** | 仅读 metrics_json：node_counts / edge_counts（均为按类型 map，nodeCount=Σ值）/ agent_count / cross_agent_edges / delegation_depth / taint_chains / avg_confidence |
| 拓扑健康 | `graph_snapshots`(最新帧) + `responsibility_alerts` + `context_pollution_events` | 同上 + severity（枚举 `Critical/Warning/Info`）+ pollution_id / source_agent_id / contaminated_artifact / **detected_at_ns** | 拓扑健康足迹（非 latency/error 五维） |
| 意图-动作失配 | `non_link_diagnostics` + `correlation_evidence` | diagnostic_id / intent_id / action_id / reason / timestamp_ns + intent_id / action_id / match_level（枚举 `L1Exact/L3Containment/L4Basename/L5Substring`） | 失配总数 / 按 reason 分布 / 证据匹配分布 |
| 策略合规 | `policy_evaluations` | policy_id / boundary / **judgment_json** / subject_agent_id / confidence / reason | **解析 judgment_json.result** 统计真实违规数；byBoundary 用 boundary 列；无时间趋势 |
| 工具足迹 | `intents` + `actions` | tool_name / tool_args_json / intent_class / authority_source + action_type / normalized_args_json / signal_class | 工具调用分布 / 动作类型分布 / top 文件路径 |
| 网络会话 | `tls_sessions` | tls_session_id / pid / start_time_ns / timestamp_ns | 会话计数 / 按时间分桶趋势 / 按 pid 分布 |

---

## 四、数据层设计

### 4.1 类型设计（`lib/monitoring/monitoring-types.ts`，全部新增）

```ts
export type MonitoringStatus = 'critical' | 'warning' | 'normal'; // 复用 deriveDatasetSummary 词表

export type PerspectiveKey =
  | 'topologyDrift' | 'topologyHealth' | 'intentMismatch'
  | 'policyCompliance' | 'toolFootprint' | 'networkSessions';

/** 每视角健康信号：available 独立于计数——缺表=false，空表=true+normal */
export interface PerspectiveHealth {
  available: boolean;
  status: MonitoringStatus;
  /** 0-100 比率派生分；缺表/无法计算 = null */
  score: number | null;
  /** 贡献计数摘要，UI 与状态并排展示（"失配 2223 · 率 68%"） */
  signals: string[];
}

export interface SnapshotMetricFrame {
  snapshot_id: string;
  timestamp_ns: number;
  /** nodeCount = Σ(node_counts map 值)，edgeCount = Σ(edge_counts map 值) */
  nodeCount: number;
  edgeCount: number;
  agentCount: number;
  crossAgentEdges: number;
  delegationDepth: number;
  taintChains: number;
  avgConfidence: number;
}

export interface TopologyDriftState {
  frames: SnapshotMetricFrame[];          // 全部帧（小，metrics 仅）
  latest: SnapshotMetricFrame | null;
  /** 最新帧 - 最早非空帧 的拓扑差分（诚实演变量） */
  delta: { nodes: number; edges: number; agents: number } | null;
}

export interface TopologyHealthState {
  latest: SnapshotMetricFrame | null;
  /** 大写枚举 Critical/Warning/Info → 小写词表 critical/warning/info */
  alertSeverity: { critical: number; warning: number; info: number };
  pollutionCount: number;
  pollutionBySource: Record<string, number>; // top N 即可
}

export interface IntentMismatchState {
  diagnosticsCount: number;                 // non_link_diagnostics 行数
  byReason: Record<string, number>;
  intentsCount: number;
  /** 失配密度 = diagnostics / intents（intents=0 → null） */
  mismatchDensity: number | null;
  evidenceMatch: { weak: number; strong: number; total: number }; // L5Substring 弱 / 其余强
}

export interface PolicyComplianceState {
  evaluationsTotal: number;
  violations: number;                       // 解析 judgment_json.result === 'Violation'
  violationRatio: number | null;            // violations / evaluationsTotal
  byBoundary: Record<string, number>;
}

export interface ToolFootprintState {
  intentsCount: number;
  actionsCount: number;
  byTool: Record<string, number>;
  byActionType: Record<string, number>;
  bySignalClass: Record<string, number>;
  topFiles: Array<{ path: string; count: number }>; // 从 normalized_args[i].Path 统计
}

export interface NetworkSessionsState {
  total: number;
  byPid: Record<string, number>;            // top N
  trend: Array<{ t: number; count: number }>; // 时间分桶（timestamp_ns）
}

export interface MonitoringState {
  derivedAtMs: number;
  sourceLabel: string;
  health: Record<PerspectiveKey, PerspectiveHealth>;
  topologyDrift: TopologyDriftState | null;
  topologyHealth: TopologyHealthState | null;
  intentMismatch: IntentMismatchState | null;
  policyCompliance: PolicyComplianceState | null;
  toolFootprint: ToolFootprintState | null;
  networkSessions: NetworkSessionsState | null;
}
```

**类型设计说明**：
- `available` 与 `status` 分离：缺表（旧版数据）→ `available:false`，UI 显示「无此表」灰态，**不并入健康状态聚合**；空表 → `available:true, status:'normal'`，显示「无信号」。
- 状态词表统一 `'critical'|'warning'|'normal'`，复用 `deriveDatasetSummary` 与 `StatusBadge` tone 映射（danger/warning/info/success），不另造大写词表。

### 4.2 提取器（`lib/db-import.ts` 新增，全部返回小聚合，不驻留原始行）

| 提取器 | SELECT 源 | 输出 | 降级 |
|--------|-----------|------|------|
| `extractSnapshotMetricsFromDb` | `graph_snapshots` → snapshot_id, timestamp_ns, metrics_json | `SnapshotMetricFrame[]` | 表缺失/坏 JSON → `[]` |
| `extractMismatchAggregateFromDb` | `non_link_diagnostics` → GROUP BY reason；`correlation_evidence` → GROUP BY match_level | `{ count, byReason, weak, strong }` | 表缺失 → 全 0 |
| `extractPollutionAggregateFromDb` | `context_pollution_events` → GROUP BY source_agent_id | `{ count, bySource }` | 表缺失 → 0 + {} |
| `extractPolicyComplianceFromDb` | `policy_evaluations` → 逐行解析 `judgment_json.result`，GROUP BY boundary | `{ total, violations, byBoundary }` | 表缺失 → 全 0 |
| `extractTlsSessionAggregateFromDb` | `tls_sessions` → 时间分桶 + GROUP BY pid | `{ total, byPid, trend }` | 表缺失 → 全 0 |

> **零新增提取的视角**：`toolFootprint` 复用既有 `extractIntentsFromDb`/`extractActionsFromDb`（完整行已驻留 DbImport，实测 23/474 行）在 `deriveMonitoringState` 内聚合；`topologyHealth` 复用既有 `extractAlertsFromDb`（大写 severity 枚举→小写词表映射）。二者不新增提取器。
>
> **为何在 loadDatabase 内一次派生而非惰性**：5 个查询在 db 句柄打开期间执行（`loadDatabase` 关闭句柄后无法再查）；输出全为小聚合（≤55 帧 / byReason map / top-N），相对现有 59MB 解析开销可忽略；`deriveMonitoringState` 仍为纯函数、可独立测试。此决策偏离审查建议 I7 的「惰性」方向，理由已在此写明（聚合成本可忽略 + 句柄生命周期约束）。

### 4.3 `deriveMonitoringState`（`lib/monitoring/derive-monitoring.ts`，纯函数）

**成分全为比率，禁止绝对数阈值**（跨 run 不稳定）：

| 视角 | 状态派生（文档化纯函数） | 阈值（比率，跨 run 稳定） |
|------|-------------------------|--------------------------|
| 拓扑漂移 | `driftRatio = \|latest.nodes - earliest.nodes\| / max(1, earliest.nodes)`；`agents` 同理 | ≥0.5 → warning；≥2.0 → critical（基线取**首个拓扑非零帧** agentCount+nodeCount>0；全部帧为零 → normal） |
| 拓扑健康 | 最新帧有污染链或 Critical 告警 → 按严重度 | `taintChains>0` 或 `alertSeverity.critical>0` → critical；`pollutionCount>0` 或 `alertSeverity.warning>0` → warning；否则 normal |
| 意图-动作失配 | `mismatchDensity = diagnostics / max(1, intents)`；`weakEvidenceRatio = weak / total` 仅展示 | >10 → warning（intents=0 → normal，无意图即无失配） |
| 策略合规 | `violationRatio = violations / max(1, evaluationsTotal)` | 0 → normal；>0.05 → warning；>0.5 → critical |
| 工具足迹 | 存在性 + 异常信号类占比 | 无 intents/actions → normal（无信号）；其余正常 |
| 网络会话 | 存在性 | 无会话 → normal（无信号） |

- `score`（0-100）与 `status` 由同一纯函数产出；`signals` 存原始贡献计数，UI **原始计数与分数并排展示**，防分数被误当 analyzer 产品指标。
- 每个提取结果先校验 `available`（源表存在与否），缺表视角 `health[key] = { available:false, status:'normal', score:null }` 且对应 state 为 null。
- 逐表 try/catch：坏 JSON / 缺表降级，**绝不抛错拖垮导入**。

### 4.4 store 扩展（`lib/store.ts`）

- `AppStore` 新增 `monitoring: MonitoringState | null`（默认 null）。
- `loadDatabase`：`deriveMonitoringState(extractors...)` 后写入。
- **置 null 必须覆盖全部 set 点**（防切源残留）：`applySnapshot`（demo/sample/file JSON）、`loadRun` 的 **API 分支**（`set({...})` 无 db 时）、`initData` 的 setState 分支、`loadDemo` / `loadSample`。
- 派生在 `loadDatabase` 内完成，监控区块首次挂载即已有数据（无重复解析）。

---

## 五、UI 落位设计（暗色 ln 设计系统）

### 5.1 区块位置与容器

- **位置**：overview 主列「图表行」之后、「数据集详情面板」**之前**，全宽（Grafana general→specific 层级：运营视图高于元信息；审查 I4 修正初稿的「面板之后」）。
- **容器**：`components/overview/monitoring/monitoring-section.tsx`。区块内部**自滚动**（固定高度 + overflow），避免长滚动页内再出现「页中页」。
- **文案规范**：eyebrow 英文 `Monitoring` + 标题中文「运行监控」；视角名中文标签（拓扑漂移/拓扑健康/意图-动作失配/策略合规/工具足迹/网络会话），type 键英文。

### 5.2 区块结构（自上而下）

1. **区块头**：eyebrow「Monitoring」+「运行监控」+ 数据源 label + 派生时间（`derivedAtMs`）。
2. **健康总览条**（Datadog Row-1 模式）：**仅聚合 6 个可比派生视角**（审查 I3）——每格 = 状态色点 + 图标 + 视角名 + 分数，配文字/图标保证 a11y（不只靠颜色，M2）。**缺表视角不并入**（available=false 灰态）。迷你趋势留给卡片，格内不放。
3. **实时视角行（阶段二占位）**：独立一行，live 徽章 + 「阶段二 SSE」标签，列出 analyzer 产品：LLM API 追踪 / LLM API 异常 / 数据外泄 / Agent 循环 / 会话健康（实时五维）/ SSE 内容扫描 / 去重诊断 / 分析器流水线。置灰禁用，**不与派生视角混入健康聚合**（静态快照 ≠ 实时流，类别错误）。
4. **视角 tab 胶囊**：全部 / 6 个派生视角。默认「全部」= **响应式卡片网格**；点卡片或 tab → 区块内**就地切换**为该视角详情工作台（面包屑「运行监控 / 视角名 · 返回全部」）。
5. **卡片**（`monitoring-card.tsx`）：图标 + 状态徽章（danger/warning/info 映射）+ 分数 + 迷你面积趋势（复用 `TrendChart`）+ ≤3 条 `signals` 子指标。无数据视角置灰「无信号·正常」；缺表视角「无此表（旧版数据）」。

### 5.3 详情工作台 — 单一 `PerspectiveDetailShell` + 配置表（审查 I5）

**不建 7 个各自脚手架的工作台**。`perspective-detail-shell.tsx` 提供统一骨架（摘要统计行 + 分布图 + 明细列表(虚拟化) + 面包屑），`perspective-config.ts` 每视角提供差异配置（metricDefs / 聚合数据 / 明细列）。6 个视图组件集中在 `perspective-views.tsx`，每个只渲染自己的数据形状：

| 视角 | 详情内容（阶段一，诚实） |
|------|--------------------------|
| 拓扑漂移 | 节点/边/agent 数随帧的线图（采样 ≤50 点）+ 首末帧差分摘要 + 全部帧表（虚拟化） |
| 拓扑健康 | 最新帧指标卡（agents/cross_agent_edges/taint_chains/avg_confidence）+ 告警严重度分解 + 污染计数与 top 污染源 |
| 意图-动作失配 | 失配总数 + 按 reason 柱图（BelowConfidenceCutoff/NoArgumentMatch/IntentNotPrimaryEligible）+ 证据匹配分布（L1Exact/L3Containment/L4Basename/L5Substring）+ 弱证据率（L5Substring） |
| 策略合规 | 违规比率大数 + 按 boundary 分解 + 违规 Top N 明细（解析 judgment_json） |
| 工具足迹 | 工具分布柱图 + 动作类型分布 + top 文件列表 + 信号类分解 |
| 网络会话 | 会话总数 + 时间分桶线图 + 按 pid top N |

### 5.4 交叉导航（少死胡同，审查 I4）

- 视角间：tab 胶囊即跨视角切换（区块内）。
- 区块外：复用既有 `lib/overview-focus-route.ts` 的 `#agentId=` 深链与 `investigationContext.activeAgentId`——从任一详情点击 agent 行 → 写 `#agentId=` 聚焦（既有机制）。
- **可解析性校验**：跳转前解析目标，解析不出降级为「该 agent 无告警」提示，**不空转不死胡同**。
- 阶段一**不做** diagnostic→evidence→claim/alert 的臆造关联链（db 无此直接外键）。

### 5.5 空态规范（Grafana 条件渲染，审查 I6）

| 场景 | 显示 |
|------|------|
| 源表缺失（旧版 db） | `available:false` 灰态「无此表（旧版数据）」——**不等于**「正常」 |
| 表存在但 0 行 | `available:true, status:'normal'`，「无信号」 |
| 阶段二视角 | live 徽章 + 「阶段二 SSE」占位，不并入健康聚合 |

### 5.6 顺带修复：`lib/dataset-summary.ts` 策略违规假阳性（审查 M1）

既有 `deriveDatasetSummary` 把 `policy_evaluations.count`（全量评估数）当违规数展示，未真正解析 `judgment_json`。本次修复为解析 `judgment_json.result === 'Violation'` 统计真实违规数（新增提取器 `extractPolicyComplianceFromDb` 复用于两处）。此修复独立于监控区块，一并纳入本 spec 测试范围。

---

## 六、测试计划（TDD）

### 6.1 合成 fixture（`scripts/gen-db-fixture.mjs` 扩展）

新增 5 张表到 fixture 生成器，值**确定性小样本**，覆盖黄金断言：`non_link_diagnostics`（3 行：BelowConfidenceCutoff×2 + NoArgumentMatch×1）、`correlation_evidence` 补 match_level 分布（含 L5Substring 弱证据）、`context_pollution_events`（2 行含 detected_at_ns）、`policy_evaluations`（3 行：1 条 `judgment_json.result="Violation"` + 2 条其余，judgment_json 完整）、`tls_sessions`（4 行跨 2 个时间桶）、`graph_snapshots`（3 帧：1 全零 + 1 中间态 + 1 满，metrics_json 用真实 node_counts/edge_counts map 结构）。重新生成 `__tests__/fixtures/db-sample.sqlite`。

### 6.2 单测

| 层 | 文件 | 用例 |
|----|------|------|
| db-import | `__tests__/db-import.test.ts` | 5 个新提取器解析真实结构；表缺失→降级；`metrics_json` 坏 JSON 容忍；policy 从 judgment_json 解析 Violation |
| monitoring | `__tests__/derive-monitoring.test.ts` | 6 视角派生规则（比率/阈值）；缺表→available:false；空表→normal；**不变量**：`loadDatabase` 产出的 monitoring == 直接调 `deriveMonitoringState` 结果 |
| store | `__tests__/store-db.test.ts` | `loadDatabase` 写入 monitoring；demo/sample/API-fallback 路径**置 null 无残留**（含 `loadRun` API 分支） |
| dataset-summary | `__tests__/overview-data.test.ts` | 策略违规修复：真实 Violation 计数而非全量评估数 |
| 组件 | `__tests__/monitoring-section.test.tsx` | 健康总览条 6 格状态渲染；缺表视角灰态不并入；卡片网格；tab↔就地切换；阶段二占位行 |

> 真实 run 计数（如 2223 失配）**仅作 e2e 冒烟参考，不作单测硬断言**（跨 run 不稳定，审查 I1）。

### 6.3 回归

- 既有 308 测试全绿；`npx tsc --noEmit` 0 错误；`npx eslint` 0 警告；`npm run build` 成功。
- e2e：导入真实 db 后 overview 监控区块出现 6 视角信号（冒烟，不断言具体计数）。

---

## 七、实施顺序

1. **合成 fixture**：gen-db-fixture.mjs 加 5 表 → 重新生成 db-sample.sqlite（TDD）
2. **数据层**：monitoring-types.ts → db-import 5 提取器 → derive-monitoring.ts → 单测红绿
3. **store**：monitoring 字段 + 全 set 点置 null → 单测
4. **修复 M1**：dataset-summary 策略违规假阳性 → 单测
5. **UI 骨架**：monitoring-section（健康总览条 + tab + 卡片网格 + 空态）+ PerspectiveDetailShell → 组件单测
6. **6 个详情视图**：perspective-views.tsx 逐视角 → 组件单测
7. **交叉导航 + 阶段二占位行** → 组件单测
8. **回归**：type-check / eslint / vitest / build / e2e 冒烟

每阶段 TDD：先写失败测试 → 最小实现 → 重构。

---

## 八、非目标

- 阶段一**不做** LLM API / 数据外泄 / Agent 循环 / 会话健康五维（db 无此数据，阶段二 SSE 接 analyzer 产品）。
- **不读** `graph_snapshots.nodes_json/edges_json`（性能 + 无意义）。
- **不**做 claim↔alert / diagnostic→alert 臆造关联。
- **不**用绝对数做状态阈值或测试断言。
- **不改** Rust collector / 后端 API。
- **不删除**旧 10 个亮色 dashboard 组件（标记 `@deprecated` 存档，阶段二参考其数据整形逻辑；最终审查再裁决去留）。
- **不**为阶段一新增页面路由（监控区是 overview 区块，非独立页面）。
