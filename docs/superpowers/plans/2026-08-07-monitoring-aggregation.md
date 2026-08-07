# 运行监控聚合实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 10 个从未接数据的 analyzer 监控视角做成产品——阶段一从责任 db 诚实派生 6 个监控信号（拓扑漂移/拓扑健康/意图-动作失配/策略合规/工具足迹/网络会话），以暗色 ln 设计系统并入 /overview 仪表盘；阶段二 8 个实时视角作 SSE 占位。

**Architecture:** 数据层在 `db-import.ts` 内、db 句柄打开期间新增 5 个聚合提取器（各带 `available` 标志）+ 纯函数 `deriveMonitoringState` 派生 `MonitoringState`，随 DbImport 存入 store；UI 层新增 `monitoring-section`（健康总览条 + 实时占位行 + tab + 卡片网格 + `PerspectiveDetailShell` 工作台）插入 overview 图表行之后。铁律：杜绝数据臆造——状态成分全为比率、缺表≠正常、算不出诚实留空。

**Tech Stack:** React 19 + TypeScript + zustand + recharts + @tanstack/react-virtual（复用既有 ln 原语 SectionPanel/TrendChart/StatusBadge/VirtualList 与 `ln-panel`/`ln-btn`/`ln-badge`/`ln-eyebrow`/`ln-metric__label` 工具类）。测试：vitest（node + jsdom docblock）+ @testing-library/react + sql.js 合成 fixture。

## Global Constraints

- **杜绝数据臆造**：任何 KPI/状态/分布由纯函数从真实数据派生；无法从数据计算的内容诚实留空/标注（spec §一.3）。
- 状态词表统一 `'critical' | 'warning' | 'normal'`；UI 经 `statusToTone` 映射到 StatusBadge 的 `'danger'|'warning'|'success'|'muted'`（缺表 → muted），不另造词表（spec §4.1）。
- **状态成分全用比率**，禁止绝对数阈值（跨 run 不稳定，spec §4.3）；真实 run 计数仅作 e2e 冒烟参考、不作单测硬断言。
- 缺表（旧版数据）→ `available:false` 灰态，**不等于**「正常」；空表 → `available:true, status:'normal'`「无信号」（spec §5.5）。
- 所有新提取器逐表 try/catch 降级，**绝不抛错拖垮导入**。
- 新组件文件以 `'use client';` 开头；新 .ts 文件按 dataset-summary.ts 的无分号风格；编辑已有文件（db-import.ts/store.ts）保持该文件现有风格。
- 每个 `.tsx` 测试文件第一行必须是 `// @vitest-environment jsdom`；非 jsdom 测试默认 node 环境。
- 测试命令统一 `cd frontend && npx vitest run <file>`（部分文件用 `npx vitest run`）。每任务提交前：`npm run type-check` 0 错误、`npm run lint` 0 警告、相关测试全绿。
- 工作区隔离不可用（CLAUDE.md 约束）——直接在 `lzk` 分支内联执行。
- 每任务独立提交，commit message 用自然中文、无 conventional 前缀、技术细节真实、不得出现 "agentsight"；`git add` 只加本任务文件。
- **契约细化（对 spec §4.2 的明确化）**：5 个新提取器每个返回对象带 `available: boolean`（表存在与否，try 成功即 true，即使 0 行）。spec §4.3 已要求「每个提取结果先校验 available（源表是否存在）」，此处落地为提取器返回标志。`toolFootprint` 复用既有 intents/actions 数组（无 available），`available` 恒为 true，缺表与空表同样显示「无信号」（其源表由主产品 hasConsumableData 消费，缺表场景已另行处理）。

---

### Task 1: 合成 fixture 扩展（5 张监控表 + 漂移帧）

**Files:**
- Modify: `scripts/gen-db-fixture.mjs`（CREATE TABLE 块 + 数据插入）
- Regenerate: `__tests__/fixtures/db-sample.sqlite`（运行生成器产物）
- Modify: `__tests__/db-import.test.ts`（概览计数黄金断言更新）

**Interfaces:**
- Consumes: 现有 fixture 结构（`nodesLatest`/`nodesOld`/`metrics`/`alert()`/`evidence()` 帮助函数、`db.run` 插入模式）。
- Produces: 含 5 张新表 + 4 个快照帧的确定性小 db，供 Task 2/3 提取器与派生测试使用。新表行数基线：`non_link_diagnostics`=3、`context_pollution_events`=2、`policy_evaluations`=3（其中 1 条 Violation）、`tls_sessions`=4（跨 2 个时间桶）；`graph_snapshots`=4 帧（snap-zero 全零 / snap-mid 部分 / snap-old 无 metrics / snap-latest 满）。

- [ ] **Step 1: 在生成器 CREATE TABLE 块追加 4 张新表定义**

在 `scripts/gen-db-fixture.mjs` 第 156 行 `runs` 表定义之后、第 157 行反引号结束前追加（保持现有无 PRIMARY KEY 的简化风格）：

```js
    CREATE TABLE non_link_diagnostics (
      diagnostic_id TEXT, intent_id TEXT, action_id TEXT, reason TEXT,
      timestamp_ns INTEGER, run_id TEXT
    );
    CREATE TABLE context_pollution_events (
      pollution_id INTEGER, source_agent_id TEXT, contaminated_artifact TEXT,
      propagation_path_json TEXT, detected_at_ns INTEGER, run_id TEXT
    );
    CREATE TABLE policy_evaluations (
      policy_id TEXT, boundary TEXT, judgment_json TEXT, subject_agent_id TEXT,
      resource_json TEXT, evidence_refs_json TEXT, confidence REAL, reason TEXT, run_id TEXT
    );
    CREATE TABLE tls_sessions (
      tls_session_id TEXT, pid INTEGER, start_time_ns INTEGER, timestamp_ns INTEGER, run_id TEXT
    );
```

- [ ] **Step 2: 追加漂移帧定义**

在 `metrics` 常量（第 48-56 行）之后追加两个帧的 metrics：

```js
// 漂移帧 1：全零（早期帧），漂移基线不取它
const metricsZero = {
  node_counts: {},
  edge_counts: {},
  agent_count: 0,
  cross_agent_edges: 0,
  delegation_depth: 0,
  taint_chains: 0,
  avg_confidence: 0,
};
// 漂移帧 2：中间态（首个拓扑非零帧，漂移基线）。nodeCount=2（Agent1+Resource1），
// edgeCount=2（IntentCausesAction2）。相对 snap-latest(nodeCount=3) → driftRatio=|3-2|/2=0.5 → warning。
const metricsMid = {
  node_counts: { Agent: 1, Resource: 1 },
  edge_counts: { IntentCausesAction: 2 },
  agent_count: 1,
  cross_agent_edges: 0,
  delegation_depth: 0,
  taint_chains: 0,
  avg_confidence: 0.6,
};
```

在 `snap-old` 插入（第 165-167 行）**之前**插入两帧（时间戳必须早于 snap-old 的 900，确保 `extractGraphFromDb` 仍取 snap-latest）：

```js
  db.run('INSERT INTO graph_snapshots VALUES (?,?,?,?,?,?)', [
    'snap-zero', 800, JSON.stringify(nodesOld), JSON.stringify(edgesOld),
    JSON.stringify(metricsZero), 'run-1',
  ]);
  db.run('INSERT INTO graph_snapshots VALUES (?,?,?,?,?,?)', [
    'snap-mid', 850, JSON.stringify(nodesOld), JSON.stringify(edgesOld),
    JSON.stringify(metricsMid), 'run-1',
  ]);
```

- [ ] **Step 3: 插入 4 张新表数据**

在 `actions` 插入循环（第 279-284 行）之后、`db.export()` 之前追加：

```js
  // ── 监控表（Task 1 新增）──
  // non_link_diagnostics：3 行，reason 分布 BelowConfidenceCutoff×2 + NoArgumentMatch×1
  for (const [i, reason] of [
    ['nld-1', 'int-1', 'BelowConfidenceCutoff'],
    ['nld-2', 'int-2', 'BelowConfidenceCutoff'],
    ['nld-3', 'int-3', 'NoArgumentMatch'],
  ]) {
    db.run('INSERT INTO non_link_diagnostics VALUES (?,?,?,?,?,?)', [
      i[0], i[1], null, i[2], 1000, 'run-1',
    ]);
  }
  // context_pollution_events：2 行，detected_at_ns 为时间列
  for (const [pid, source, artifact, ts] of [
    [1, 'agent-a', '/tmp/leaked.txt', 1000],
    [2, 'agent-b', '/tmp/context.json', 1100],
  ]) {
    db.run(
      'INSERT INTO context_pollution_events VALUES (?,?,?,?,?,?)',
      [pid, source, artifact, JSON.stringify([]), ts, 'run-1'],
    );
  }
  // policy_evaluations：3 行，其中 1 条 judgment_json.result=Violation
  const judgment = (result) =>
    JSON.stringify({ judgment_id: 'j-' + result, policy_id: 'pol-' + result, result });
  for (const [policyId, boundary, subject, result] of [
    ['pol-1', 'Capability', 'agent-a', 'Violation'],
    ['pol-2', 'Capability', 'agent-a', 'Compliant'],
    ['pol-3', 'Capability', 'agent-b', 'Compliant'],
  ]) {
    db.run(
      'INSERT INTO policy_evaluations VALUES (?,?,?,?,?,?,?,?,?)',
      [policyId, boundary, judgment(result), subject, null, '[]', 0.85, '', 'run-1'],
    );
  }
  // tls_sessions：4 行跨 2 个时间桶（桶宽 60s = 60_000_000_000 ns）
  for (const [sid, pid, ts] of [
    ['s-1', 42, 1000],
    ['s-2', 42, 5000],
    ['s-3', 43, 61_000_000_000],
    ['s-4', 43, 61_005_000_000],
  ]) {
    db.run(
      'INSERT INTO tls_sessions VALUES (?,?,?,?,?)',
      [sid, pid, 1000, ts, 'run-1'],
    );
  }
```

- [ ] **Step 4: 重新生成 fixture 并跑既有测试确认预期失败**

Run: `cd frontend && node scripts/gen-db-fixture.mjs`
Expected: `fixture written: <n> bytes`（体积增大）。

Run: `cd frontend && npx vitest run __tests__/db-import.test.ts`
Expected: 概要黄金断言 FAIL（snapshots.count 2→4、pollution 0→2、policy 0→3、tls 0→4、nld 0→3）。

- [ ] **Step 5: 更新 db-import.test.ts 黄金断言**

在 `__tests__/db-import.test.ts` 第 263 行附近，把 `expect(overview.snapshots.count).toBe(2)` 改为 `.toBe(4)`；第 276-283 行的四个零断言改为：

```ts
    expect(overview.pollution.count).toBe(2);
    expect(overview.policy_evaluations.count).toBe(3);
    expect(overview.processes.count).toBe(0);
    expect(overview.tls_sessions.count).toBe(4);
    expect(overview.identity_process_map.count).toBe(0);
    expect(overview.provisional_identities.count).toBe(0);
    expect(overview.refinements).toEqual({ claim: 0, classification: 0, evidence: 0, graph: 0, identity: 0 });
    expect(overview.non_link_diagnostics.count).toBe(3);
```

（`processes`/`identity_process_map`/`provisional_identities` 保持 0，fixture 未建这些表。）

- [ ] **Step 6: 跑通全部相关测试**

Run: `cd frontend && npx vitest run __tests__/db-import.test.ts __tests__/overview-data.test.ts`
Expected: PASS。

- [ ] **Step 7: 提交**

```bash
git add scripts/gen-db-fixture.mjs __tests__/fixtures/db-sample.sqlite __tests__/db-import.test.ts
git commit -m "合成 fixture 补全监控表：新增 non_link_diagnostics/context_pollution_events/policy_evaluations/tls_sessions 四表与全零/中间态漂移帧，重新生成 db-sample.sqlite，概览计数黄金断言随表补齐更新"
```

---

### Task 2: 监控数据层类型 + 5 个聚合提取器

**Files:**
- Create: `lib/monitoring/monitoring-types.ts`
- Create: `__tests__/monitoring-extractors.test.ts`
- Modify: `lib/types.ts`（`DatasetOverview.policy_evaluations` 增加 `violations`）
- Modify: `lib/db-import.ts`（新增 5 提取器 + `sumValues` 帮助函数 + `extractDatasetOverviewFromDb` 复用政策提取器）
- Modify: `__tests__/overview-data.test.ts`（`sampleOverview` 的 `policy_evaluations` 补 `violations: 0`）

**Interfaces:**
- Consumes: `types.ts` 的 `Intent/Action/ResponsibilityAlert/DatasetOverview`；`db-import.ts` 的 `queryRows`（第 169 行，`(db, sql, params?) => Record<string, unknown>[]`，缺表抛错）、`parseJsonObject`（第 227 行，坏 JSON 返回 null）。
- Produces: 五个提取器（均带 `available` 标志，供 Task 3 `deriveMonitoringState` 直接消费）：

```ts
// lib/monitoring/monitoring-types.ts 顶部导出
export interface SnapshotMetricsExtract { available: boolean; frames: SnapshotMetricFrame[] }
export interface MismatchExtract { available: boolean; count: number; byReason: Record<string, number>; weak: number; strong: number }
export interface PollutionExtract { available: boolean; count: number; bySource: Record<string, number> }
export interface PolicyExtract { available: boolean; total: number; violations: number; byBoundary: Record<string, number> }
export interface TlsExtract { available: boolean; total: number; byPid: Record<string, number>; trend: Array<{ t: number; count: number }> }
```

- [ ] **Step 1: 写失败测试（monitoring-extractors.test.ts，全 5 提取器）**

新建 `__tests__/monitoring-extractors.test.ts`，首行 `// @vitest-environment node`（node 环境即可，sql.js 已由 test/setup.ts 注入 `globalThis.initSqlJs`）。提取器接收的是**原始 sql.js `Database` 句柄**（`loadDatabase` 会关闭句柄，不可复用），所以测试用 `initSqlJs()` 直接打开 fixture：

```ts
// @vitest-environment node
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import initSqlJs from 'sql.js';
import {
  extractSnapshotMetricsFromDb,
  extractMismatchAggregateFromDb,
  extractPollutionAggregateFromDb,
  extractPolicyComplianceFromDb,
  extractTlsSessionAggregateFromDb,
} from '@/lib/db-import';
import type { Database } from 'sql.js';

async function openFixtureDb(): Promise<Database> {
  const buf = readFileSync(new URL('./fixtures/db-sample.sqlite', import.meta.url));
  const bytes = new Uint8Array(buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength));
  const SQL = await initSqlJs();
  return new SQL.Database(bytes);
}

describe('监控聚合提取器', () => {
  it('extractSnapshotMetricsFromDb 返回非空帧并按时间升序，跳过 null metrics 帧', async () => {
    const db = await openFixtureDb();
    const r = extractSnapshotMetricsFromDb(db);
    db.close();
    expect(r.available).toBe(true);
    expect(r.frames.map((f) => f.snapshot_id)).toEqual(['snap-zero', 'snap-mid', 'snap-latest']);
    expect(r.frames[0].nodeCount).toBe(0); // snap-zero 全零
    expect(r.frames[1].nodeCount).toBe(2); // snap-mid Agent1+Resource1
    expect(r.frames[2].nodeCount).toBe(3); // snap-latest Agent+Resource+Process
  });

  it('extractMismatchAggregateFromDb 统计 reason 分布与弱证据', async () => {
    const db = await openFixtureDb();
    const r = extractMismatchAggregateFromDb(db);
    db.close();
    expect(r.available).toBe(true);
    expect(r.count).toBe(3);
    expect(r.byReason).toEqual({ BelowConfidenceCutoff: 2, NoArgumentMatch: 1 });
    expect(r.weak).toBe(1); // ev-3 L5Substring
    expect(r.strong).toBe(2); // ev-1 L1Exact + ev-2 L3Containment
  });

  it('extractPollutionAggregateFromDb 按 source_agent_id 聚合', async () => {
    const db = await openFixtureDb();
    const r = extractPollutionAggregateFromDb(db);
    db.close();
    expect(r.available).toBe(true);
    expect(r.count).toBe(2);
    expect(r.bySource).toEqual({ 'agent-a': 1, 'agent-b': 1 });
  });

  it('extractPolicyComplianceFromDb 解析 judgment_json.result 得真实违规数', async () => {
    const db = await openFixtureDb();
    const r = extractPolicyComplianceFromDb(db);
    db.close();
    expect(r.available).toBe(true);
    expect(r.total).toBe(3);
    expect(r.violations).toBe(1); // 仅 pol-1 是 Violation
    expect(r.byBoundary).toEqual({ Capability: 3 });
  });

  it('extractTlsSessionAggregateFromDb 按 60s 桶聚合', async () => {
    const db = await openFixtureDb();
    const r = extractTlsSessionAggregateFromDb(db);
    db.close();
    expect(r.available).toBe(true);
    expect(r.total).toBe(4);
    expect(r.byPid).toEqual({ '42': 2, '43': 2 });
    expect(r.trend.map((b) => b.count)).toEqual([2, 2]);
    expect(r.trend[1].t - r.trend[0].t).toBe(60_000_000_000);
  });

  it('缺表提取器降级：available=false 且聚合归零', async () => {
    const SQL = await initSqlJs();
    const db = new SQL.Database(); // 空 db，无任何表
    const snapshots = extractSnapshotMetricsFromDb(db);
    const mismatch = extractMismatchAggregateFromDb(db);
    const pollution = extractPollutionAggregateFromDb(db);
    const policy = extractPolicyComplianceFromDb(db);
    const tls = extractTlsSessionAggregateFromDb(db);
    db.close();
    expect(snapshots.available).toBe(false);
    expect(snapshots.frames).toEqual([]);
    expect(mismatch).toEqual({ available: false, count: 0, byReason: {}, weak: 0, strong: 0 });
    expect(pollution).toEqual({ available: false, count: 0, bySource: {} });
    expect(policy).toEqual({ available: false, total: 0, violations: 0, byBoundary: {} });
    expect(tls).toEqual({ available: false, total: 0, byPid: {}, trend: [] });
  });
});
```

> 注：`Database` 类型从 `sql.js` 直接导入（`db-import.ts` 已 re-export，但测试直接引 `sql.js` 更清晰）。

- [ ] **Step 2: 运行确认失败**

Run: `cd frontend && npx vitest run __tests__/monitoring-extractors.test.ts`
Expected: FAIL —— `extractSnapshotMetricsFromDb` 等 5 个函数不存在（undefined），且 `DatasetOverview` 尚无 `violations` 字段（类型错误）。

- [ ] **Step 3: 新建 monitoring-types.ts（完整类型）**

`lib/monitoring/monitoring-types.ts`：

```ts
// 运行监控聚合——阶段一 6 个 db 派生视角的类型契约。
// 铁律：任何 KPI/状态/分布由纯函数从真实数据派生；算不出的诚实留空（null）。
// 状态词表统一 critical|warning|normal，UI 层经 StatusBadge tone 映射。

export type MonitoringStatus = 'critical' | 'warning' | 'normal'

export type PerspectiveKey =
  | 'topologyDrift'
  | 'topologyHealth'
  | 'intentMismatch'
  | 'policyCompliance'
  | 'toolFootprint'
  | 'networkSessions'

/** 每视角健康信号：available 独立于计数——缺表=false，空表=true+normal */
export interface PerspectiveHealth {
  available: boolean
  status: MonitoringStatus
  /** 0-100 比率派生分；无自然比率（存在性视角）或无法计算 = null */
  score: number | null
  /** 贡献计数摘要，UI 与状态并排展示（"失配 3 · 密度 1.0"） */
  signals: string[]
}

export interface SnapshotMetricFrame {
  snapshot_id: string
  timestamp_ns: number
  /** nodeCount = Σ(node_counts map 值)，edgeCount = Σ(edge_counts map 值) */
  nodeCount: number
  edgeCount: number
  agentCount: number
  crossAgentEdges: number
  delegationDepth: number
  taintChains: number
  avgConfidence: number
}

export interface TopologyDriftState {
  frames: SnapshotMetricFrame[]
  latest: SnapshotMetricFrame | null
  /** 最新帧 - 首个拓扑非零帧 的拓扑差分（诚实演变量） */
  delta: { nodes: number; edges: number; agents: number } | null
}

export interface TopologyHealthState {
  latest: SnapshotMetricFrame | null
  /** 大写枚举 Critical/Warning/Info → 小写词表 */
  alertSeverity: { critical: number; warning: number; info: number }
  pollutionCount: number
  pollutionBySource: Record<string, number>
}

export interface IntentMismatchState {
  diagnosticsCount: number
  byReason: Record<string, number>
  intentsCount: number
  /** 失配密度 = diagnostics / max(1, intents) */
  mismatchDensity: number | null
  evidenceMatch: { weak: number; strong: number; total: number } // L5Substring 弱 / 其余强
}

export interface PolicyComplianceState {
  evaluationsTotal: number
  violations: number
  violationRatio: number | null
  byBoundary: Record<string, number>
}

export interface ToolFootprintState {
  intentsCount: number
  actionsCount: number
  byTool: Record<string, number>
  byActionType: Record<string, number>
  bySignalClass: Record<string, number>
  topFiles: Array<{ path: string; count: number }>
}

export interface NetworkSessionsState {
  total: number
  byPid: Record<string, number>
  trend: Array<{ t: number; count: number }>
}

export interface MonitoringState {
  derivedAtMs: number
  sourceLabel: string
  health: Record<PerspectiveKey, PerspectiveHealth>
  topologyDrift: TopologyDriftState | null
  topologyHealth: TopologyHealthState | null
  intentMismatch: IntentMismatchState | null
  policyCompliance: PolicyComplianceState | null
  toolFootprint: ToolFootprintState | null
  networkSessions: NetworkSessionsState | null
}

// ── 提取器返回契约（Task 2 产出，Task 3 消费）──
export interface SnapshotMetricsExtract {
  available: boolean
  frames: SnapshotMetricFrame[]
}
export interface MismatchExtract {
  available: boolean
  count: number
  byReason: Record<string, number>
  weak: number
  strong: number
}
export interface PollutionExtract {
  available: boolean
  count: number
  bySource: Record<string, number>
}
export interface PolicyExtract {
  available: boolean
  total: number
  violations: number
  byBoundary: Record<string, number>
}
export interface TlsExtract {
  available: boolean
  total: number
  byPid: Record<string, number>
  trend: Array<{ t: number; count: number }>
}
```

- [ ] **Step 4: 修改 lib/types.ts（DatasetOverview.violations）**

`lib/types.ts` 第 266 行 `policy_evaluations: { count: number };` 改为：

```ts
  /** count=评估总数，violations=judgment_json.result === 'Violation' 的真实违规数 */
  policy_evaluations: { count: number; violations: number };
```

- [ ] **Step 5: 在 db-import.ts 实现 5 个提取器**

在 `extractCountsFromDb`（第 622-639 行）之前插入 `sumValues` 帮助函数，随后插入 5 个提取器（保持文件的分号风格）：

```ts
const sumValues = (record: Record<string, number>): number =>
  Object.values(record).reduce((sum, n) => sum + n, 0);

/** 快照帧：仅读 metrics_json（绝不解析 nodes/edges_json）。null metrics 行跳过。 */
export function extractSnapshotMetricsFromDb(
  db: Database,
): SnapshotMetricsExtract {
  let rows: Record<string, unknown>[];
  try {
    rows = queryRows(
      db,
      'SELECT snapshot_id, timestamp_ns, metrics_json FROM graph_snapshots ORDER BY timestamp_ns ASC',
    );
  } catch {
    return { available: false, frames: [] }; // Table absent.
  }
  const frames: SnapshotMetricFrame[] = [];
  for (const r of rows) {
    const m = parseJsonObject<{
      node_counts?: Record<string, number>;
      edge_counts?: Record<string, number>;
      agent_count?: number;
      cross_agent_edges?: number;
      delegation_depth?: number;
      taint_chains?: number;
      avg_confidence?: number;
    }>(r.metrics_json);
    if (!m) continue; // null/坏 JSON 帧跳过（如 fixture snap-old）
    frames.push({
      snapshot_id: String(r.snapshot_id ?? ''),
      timestamp_ns: Number(r.timestamp_ns ?? 0),
      nodeCount: sumValues(m.node_counts ?? {}),
      edgeCount: sumValues(m.edge_counts ?? {}),
      agentCount: Number(m.agent_count ?? 0),
      crossAgentEdges: Number(m.cross_agent_edges ?? 0),
      delegationDepth: Number(m.delegation_depth ?? 0),
      taintChains: Number(m.taint_chains ?? 0),
      avgConfidence: Number(m.avg_confidence ?? 0),
    });
  }
  return { available: true, frames };
}

/** 意图-动作失配：nld 按 reason + 证据按 match_level（L5Substring 弱 / 其余强）。 */
export function extractMismatchAggregateFromDb(db: Database): MismatchExtract {
  let byReason: Record<string, number> = {};
  let count = 0;
  try {
    const rows = queryRows(
      db,
      'SELECT reason AS k, COUNT(*) AS n FROM non_link_diagnostics GROUP BY reason',
    );
    for (const r of rows) {
      const n = Number(r.n ?? 0);
      byReason[String(r.k ?? '')] = n;
      count += n;
    }
  } catch {
    return { available: false, count: 0, byReason: {}, weak: 0, strong: 0 };
  }
  let weak = 0;
  let strong = 0;
  try {
    const rows = queryRows(
      db,
      'SELECT match_level AS k, COUNT(*) AS n FROM correlation_evidence GROUP BY match_level',
    );
    for (const r of rows) {
      const n = Number(r.n ?? 0);
      if (String(r.k) === 'L5Substring') weak += n;
      else strong += n;
    }
  } catch {
    // correlation_evidence 缺表 → weak/strong 归零（证据不是本视角的主信号）
  }
  return { available: true, count, byReason, weak, strong };
}

/** 上下文污染：按 source_agent_id 聚合。 */
export function extractPollutionAggregateFromDb(db: Database): PollutionExtract {
  let bySource: Record<string, number> = {};
  let count = 0;
  try {
    const rows = queryRows(
      db,
      'SELECT source_agent_id AS k, COUNT(*) AS n FROM context_pollution_events GROUP BY source_agent_id',
    );
    for (const r of rows) {
      const n = Number(r.n ?? 0);
      bySource[String(r.k ?? '')] = n;
      count += n;
    }
  } catch {
    return { available: false, count: 0, bySource: {} };
  }
  return { available: true, count, bySource };
}

/** 策略合规：逐行解析 judgment_json.result === 'Violation'。无时间戳，不做时间趋势。 */
export function extractPolicyComplianceFromDb(db: Database): PolicyExtract {
  let byBoundary: Record<string, number> = {};
  let total = 0;
  let violations = 0;
  let rows: Record<string, unknown>[];
  try {
    rows = queryRows(db, 'SELECT boundary, judgment_json FROM policy_evaluations');
  } catch {
    return { available: false, total: 0, violations: 0, byBoundary: {} };
  }
  for (const r of rows) {
    total++;
    const b = r.boundary == null || r.boundary === '' ? 'null' : String(r.boundary);
    byBoundary[b] = (byBoundary[b] ?? 0) + 1;
    const judgment = parseJsonObject<{ result?: string }>(r.judgment_json);
    if (judgment?.result === 'Violation') violations++;
  }
  return { available: true, total, violations, byBoundary };
}

/** 网络会话：total + byPid + 60s 时间桶趋势（timestamp_ns）。 */
export function extractTlsSessionAggregateFromDb(db: Database): TlsExtract {
  let byPid: Record<string, number> = {};
  let total = 0;
  let rows: Record<string, unknown>[];
  try {
    rows = queryRows(db, 'SELECT pid, timestamp_ns FROM tls_sessions ORDER BY timestamp_ns ASC');
  } catch {
    return { available: false, total: 0, byPid: {}, trend: [] };
  }
  const BUCKET_NS = 60_000_000_000;
  const buckets = new Map<number, number>();
  for (const r of rows) {
    total++;
    const pid = String(r.pid ?? '');
    byPid[pid] = (byPid[pid] ?? 0) + 1;
    const ts = Number(r.timestamp_ns ?? 0);
    if (ts > 0) {
      const t = Math.floor(ts / BUCKET_NS) * BUCKET_NS;
      buckets.set(t, (buckets.get(t) ?? 0) + 1);
    }
  }
  const trend = [...buckets.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([t, count]) => ({ t, count }));
  return { available: true, total, byPid, trend };
}
```

在 `extractDatasetOverviewFromDb` 第 442 行 `policy_evaluations: { count: count('policy_evaluations') },` 改为复用提取器（函数声明提升，顺序无碍）：

```ts
    policy_evaluations: (() => {
      const c = extractPolicyComplianceFromDb(db);
      return { count: c.total, violations: c.violations };
    })(),
```

在 db-import.ts 顶部 import 块加入类型导入：

```ts
import type {
  MismatchExtract,
  PollutionExtract,
  PolicyExtract,
  SnapshotMetricsExtract,
  SnapshotMetricFrame,
  TlsExtract,
} from './monitoring/monitoring-types';
```

- [ ] **Step 6: 更新全部 DatasetOverview 字面量（violations 必填字段波及）**

`DatasetOverview.policy_evaluations` 改为 `{ count, violations }` 后，代码库中全部 4 处构造点必须补齐 `violations`（已 grep 核实清单，遗漏则 type-check 失败）：

| 文件:行 | 现值 | 改为 |
|---|---|---|
| `__tests__/overview-data.test.ts:18` | `policy_evaluations: { count: 0 },` | `{ count: 0, violations: 0 },` |
| `__tests__/overview-data.test.ts:83` | `policy_evaluations: { count: 755 },` | `{ count: 755, violations: 12 },` |
| `__tests__/dataset-overview-panel.test.tsx:18` | `policy_evaluations: { count: 755 },` | `{ count: 755, violations: 12 },` |
| `__tests__/overview-page-client.test.tsx:81` | `policy_evaluations: { count: 0 },` | `{ count: 0, violations: 0 },` |

（Task 5 会真正消费 violations，此处先让类型通过。）

- [ ] **Step 7: 确认测试文件无占位残留**

Step 1 已写就完整测试（非占位）。检查 `__tests__/monitoring-extractors.test.ts` 不含 "placeholder" 字样、6 条用例全部为真实断言（可从 Step 8 的全绿结果反证）。

- [ ] **Step 8: 跑通全部相关测试**

Run: `cd frontend && npx vitest run __tests__/monitoring-extractors.test.ts __tests__/db-import.test.ts __tests__/overview-data.test.ts __tests__/dataset-overview-panel.test.tsx __tests__/overview-page-client.test.tsx`
Expected: 全 PASS。随后 `npm run type-check` 0 错误、`npm run lint` 0 警告。

- [ ] **Step 9: 提交**

```bash
git add lib/monitoring/monitoring-types.ts lib/types.ts lib/db-import.ts __tests__/monitoring-extractors.test.ts __tests__/overview-data.test.ts __tests__/dataset-overview-panel.test.tsx __tests__/overview-page-client.test.tsx
git commit -m "监控数据层类型与聚合提取器：新增 6 视角类型契约与 5 个聚合提取器（快照帧/失配/污染/策略合规/TLS 会话，均带 available 缺表标志），policy_evaluations 概要补 judgment_json 真实违规数"
```

---

### Task 3: deriveMonitoringState 纯函数（6 视角健康派生）

**Files:**
- Create: `lib/monitoring/derive-monitoring.ts`
- Create: `__tests__/derive-monitoring.test.ts`

**Interfaces:**
- Consumes: `monitoring-types.ts` 全部类型；5 提取器返回契约（Task 2）；`Intent/Action/ResponsibilityAlert`。
- Produces: `deriveMonitoringState(input: MonitoringDeriveInput): MonitoringState`。`MonitoringDeriveInput` 定义见下，`loadDatabase`（Task 4）与测试均按此调用。

- [ ] **Step 1: 写失败测试**

新建 `__tests__/derive-monitoring.test.ts`（node 环境）。用 fixture 提取器输出做**黄金断言**，另用合成输入覆盖阈值边界：

```ts
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import initSqlJs from 'sql.js';
import { loadDatabase } from '@/lib/db-import';
import { deriveMonitoringState, type MonitoringDeriveInput } from '@/lib/monitoring/derive-monitoring';
import {
  extractSnapshotMetricsFromDb,
  extractMismatchAggregateFromDb,
  extractPollutionAggregateFromDb,
  extractPolicyComplianceFromDb,
  extractTlsSessionAggregateFromDb,
} from '@/lib/db-import';
import type { Database } from 'sql.js';

async function openFixtureDb(): Promise<Database> {
  const buf = readFileSync(new URL('./fixtures/db-sample.sqlite', import.meta.url));
  const bytes = new Uint8Array(buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength));
  const SQL = await initSqlJs();
  return new SQL.Database(bytes);
}

describe('deriveMonitoringState — 6 视角比率派生', () => {
  it('fixture 派生：漂移 warning、健康 warning、合规 warning、其余 normal，全 available', async () => {
    const db = await openFixtureDb();
    const input: MonitoringDeriveInput = {
      sourceLabel: 'fixture',
      derivedAtMs: 0,
      snapshots: extractSnapshotMetricsFromDb(db),
      alerts: [],
      pollution: extractPollutionAggregateFromDb(db),
      mismatch: extractMismatchAggregateFromDb(db),
      policy: extractPolicyComplianceFromDb(db),
      tls: extractTlsSessionAggregateFromDb(db),
      intents: [],
      actions: [],
    };
    db.close();
    const m = deriveMonitoringState(input);
    expect(m.health.topologyDrift.status).toBe('warning'); // driftRatio 0.5
    expect(m.health.topologyDrift.score).toBe(50);
    expect(m.health.topologyHealth.status).toBe('warning'); // pollutionCount 2
    expect(m.health.intentMismatch.status).toBe('normal'); // density 3/3=1
    expect(m.health.policyCompliance.status).toBe('warning'); // 1/3≈0.33 > 0.05
    expect(m.health.toolFootprint.status).toBe('normal'); // 空数组 → 无信号
    expect(m.health.networkSessions.status).toBe('normal');
    for (const key of Object.keys(m.health) as (keyof typeof m.health)[]) {
      expect(m.health[key].available).toBe(true);
    }
    expect(m.topologyDrift?.delta).toEqual({ nodes: 1, edges: 0, agents: 0 });
  });

  it('漂移阈值边界：driftRatio≥2 → critical、≥0.5 → warning、否则 normal', () => {
    const base: MonitoringDeriveInput = {
      sourceLabel: 't', derivedAtMs: 0,
      snapshots: { available: true, frames: [] },
      alerts: [], pollution: { available: true, count: 0, bySource: {} },
      mismatch: { available: true, count: 0, byReason: {}, weak: 0, strong: 0 },
      policy: { available: true, total: 0, violations: 0, byBoundary: {} },
      tls: { available: true, total: 0, byPid: {}, trend: [] },
      intents: [], actions: [],
    };
    const withNodes = (a: number, b: number): MonitoringDeriveInput => ({
      ...base,
      snapshots: {
        available: true,
        frames: [
          { snapshot_id: 'f0', timestamp_ns: 0, nodeCount: a, edgeCount: a, agentCount: a, crossAgentEdges: 0, delegationDepth: 0, taintChains: 0, avgConfidence: 0 },
          { snapshot_id: 'f1', timestamp_ns: 1, nodeCount: b, edgeCount: b, agentCount: b, crossAgentEdges: 0, delegationDepth: 0, taintChains: 0, avgConfidence: 0 },
        ],
      },
    });
    expect(deriveMonitoringState(withNodes(10, 10)).health.topologyDrift.status).toBe('normal'); // 0
    expect(deriveMonitoringState(withNodes(10, 15)).health.topologyDrift.status).toBe('warning'); // 0.5
    expect(deriveMonitoringState(withNodes(10, 30)).health.topologyDrift.status).toBe('critical'); // 2.0
    // 全零帧 → normal（无基线）
    expect(deriveMonitoringState(base).health.topologyDrift.status).toBe('normal');
  });

  it('缺表视角：available=false，对应 state 为 null，score null', () => {
    const m = deriveMonitoringState({
      sourceLabel: 't', derivedAtMs: 0,
      snapshots: { available: false, frames: [] },
      alerts: [],
      pollution: { available: false, count: 0, bySource: {} },
      mismatch: { available: false, count: 0, byReason: {}, weak: 0, strong: 0 },
      policy: { available: false, total: 0, violations: 0, byBoundary: {} },
      tls: { available: false, total: 0, byPid: {}, trend: [] },
      intents: [], actions: [],
    });
    expect(m.health.topologyDrift.available).toBe(false);
    expect(m.health.topologyDrift.status).toBe('normal');
    expect(m.health.topologyDrift.score).toBe(null);
    expect(m.topologyDrift).toBeNull();
    expect(m.health.policyCompliance.available).toBe(false);
    expect(m.policyCompliance).toBeNull();
  });

  it('拓扑健康按告警严重度 + 污染计数派生状态', () => {
    const base = (alerts: MonitoringDeriveInput['alerts'], pollutionCount: number): MonitoringDeriveInput => ({
      sourceLabel: 't', derivedAtMs: 0,
      snapshots: { available: true, frames: [] },
      alerts,
      pollution: { available: true, count: pollutionCount, bySource: {} },
      mismatch: { available: true, count: 0, byReason: {}, weak: 0, strong: 0 },
      policy: { available: true, total: 0, violations: 0, byBoundary: {} },
      tls: { available: true, total: 0, byPid: {}, trend: [] },
      intents: [], actions: [],
    });
    const critical = { severity: 'Critical', primary_agent: { agent_id: 'a', claimed_role: null } };
    const warning = { severity: 'Warning', primary_agent: { agent_id: 'a', claimed_role: null } };
    expect(deriveMonitoringState(base([critical as never], 0)).health.topologyHealth.status).toBe('critical');
    expect(deriveMonitoringState(base([warning as never], 0)).health.topologyHealth.status).toBe('warning');
    expect(deriveMonitoringState(base([], 3)).health.topologyHealth.status).toBe('warning');
    expect(deriveMonitoringState(base([], 0)).health.topologyHealth.status).toBe('normal');
  });
});
```

> 注：`alerts` 数组在派生中只用 `severity` 字段，测试里传最小对象并用 `as never` 收窄即可；实现按 `severity === 'Critical' | 'Warning' | else` 计数。

- [ ] **Step 2: 运行确认失败**

Run: `cd frontend && npx vitest run __tests__/derive-monitoring.test.ts`
Expected: FAIL —— `derive-monitoring` 模块不存在。

- [ ] **Step 3: 实现 derive-monitoring.ts**

`lib/monitoring/derive-monitoring.ts`（无分号风格，纯函数，不含任何 UI 依赖）：

```ts
import type { Action, Intent, ResponsibilityAlert } from '../types'
import type {
  MonitoringState,
  MonitoringStatus,
  PerspectiveHealth,
  SnapshotMetricsExtract,
  MismatchExtract,
  PollutionExtract,
  PolicyExtract,
  TlsExtract,
  TopologyDriftState,
  TopologyHealthState,
  IntentMismatchState,
  PolicyComplianceState,
  ToolFootprintState,
  NetworkSessionsState,
} from './monitoring-types'

export interface MonitoringDeriveInput {
  sourceLabel: string
  derivedAtMs: number
  snapshots: SnapshotMetricsExtract
  alerts: ResponsibilityAlert[]
  pollution: PollutionExtract
  mismatch: MismatchExtract
  policy: PolicyExtract
  tls: TlsExtract
  intents: Intent[]
  actions: Action[]
}

const clampScore = (n: number): number => Math.max(0, Math.min(100, Math.round(n)))

/** 状态派生唯一入口：每个视角的状态/分数/信号由同一纯函数产出。 */
export function deriveMonitoringState(input: MonitoringDeriveInput): MonitoringState {
  const { sourceLabel, derivedAtMs, snapshots, alerts, pollution, mismatch, policy, tls, intents, actions } = input
  const frames = snapshots.frames

  // ── 拓扑漂移 ──
  const driftFirst = frames.find((f) => f.agentCount + f.nodeCount > 0) ?? null
  const driftLatest = frames.length > 0 ? frames[frames.length - 1] : null
  const driftDelta =
    driftFirst && driftLatest && driftFirst.snapshot_id !== driftLatest.snapshot_id
      ? {
          nodes: driftLatest.nodeCount - driftFirst.nodeCount,
          edges: driftLatest.edgeCount - driftFirst.edgeCount,
          agents: driftLatest.agentCount - driftFirst.agentCount,
        }
      : null
  const driftRatio =
    driftFirst && driftLatest
      ? Math.abs(driftLatest.nodeCount - driftFirst.nodeCount) / Math.max(1, driftFirst.nodeCount)
      : null
  const driftStatus: MonitoringStatus =
    driftRatio == null ? 'normal' : driftRatio >= 2 ? 'critical' : driftRatio >= 0.5 ? 'warning' : 'normal'
  const driftHealth: PerspectiveHealth = {
    available: snapshots.available,
    status: driftStatus,
    score: snapshots.available && driftRatio != null ? clampScore(driftRatio * 100) : null,
    signals: snapshots.available
      ? [
          `${frames.length} 帧`,
          driftLatest ? `${driftLatest.nodeCount} 节点` : '无帧',
          driftDelta ? `Δ${driftDelta.nodes >= 0 ? '+' : ''}${driftDelta.nodes} 节点` : '单帧无差分',
        ]
      : ['无快照表'],
  }
  const topologyDrift: TopologyDriftState = { frames, latest: driftLatest, delta: driftDelta }

  // ── 拓扑健康 ──
  const alertSeverity = { critical: 0, warning: 0, info: 0 }
  for (const a of alerts) {
    if (a.severity === 'Critical') alertSeverity.critical++
    else if (a.severity === 'Warning') alertSeverity.warning++
    else alertSeverity.info++
  }
  const healthStatus: MonitoringStatus =
    alertSeverity.critical > 0 ? 'critical' : pollution.count > 0 || alertSeverity.warning > 0 ? 'warning' : 'normal'
  const healthAvailable = snapshots.available || pollution.available
  const topologyHealth: TopologyHealthState = {
    latest: driftLatest,
    alertSeverity,
    pollutionCount: pollution.count,
    pollutionBySource: pollution.bySource,
  }
  const healthSignals = healthAvailable
    ? [
        `告警 ${alerts.length}（严重 ${alertSeverity.critical}）`,
        `污染 ${pollution.count}`,
        driftLatest ? `链 ${driftLatest.taintChains}` : '无最新帧',
      ]
    : ['无快照/污染表']

  // ── 意图-动作失配 ──
  const mismatchDensity = mismatch.available ? mismatch.count / Math.max(1, intents.length) : null
  const mismatchStatus: MonitoringStatus =
    !mismatch.available || intents.length === 0 || mismatchDensity == null
      ? 'normal'
      : mismatchDensity > 10
        ? 'warning'
        : 'normal'
  const intentMismatch: IntentMismatchState = {
    diagnosticsCount: mismatch.count,
    byReason: mismatch.byReason,
    intentsCount: intents.length,
    mismatchDensity,
    evidenceMatch: { weak: mismatch.weak, strong: mismatch.strong, total: mismatch.weak + mismatch.strong },
  }

  // ── 策略合规 ──
  const violationRatio = policy.available && policy.total > 0 ? policy.violations / policy.total : null
  const complianceStatus: MonitoringStatus =
    violationRatio == null ? 'normal' : violationRatio > 0.5 ? 'critical' : violationRatio > 0.05 ? 'warning' : 'normal'
  const policyCompliance: PolicyComplianceState = {
    evaluationsTotal: policy.total,
    violations: policy.violations,
    violationRatio,
    byBoundary: policy.byBoundary,
  }

  // ── 工具足迹（复用既有 intents/actions，available 恒 true）──
  const byTool: Record<string, number> = {}
  for (const i of intents) {
    const t = i.tool_name || '未知'
    byTool[t] = (byTool[t] ?? 0) + 1
  }
  const byActionType: Record<string, number> = {}
  const bySignalClass: Record<string, number> = {}
  const fileCounts = new Map<string, number>()
  for (const a of actions) {
    byActionType[a.action_type] = (byActionType[a.action_type] ?? 0) + 1
    bySignalClass[a.signal_class] = (bySignalClass[a.signal_class] ?? 0) + 1
    for (const arg of a.normalized_args ?? []) {
      if (arg && typeof arg === 'object' && 'Path' in arg) {
        const p = String((arg as { Path?: unknown }).Path ?? '')
        if (p) fileCounts.set(p, (fileCounts.get(p) ?? 0) + 1)
      }
    }
  }
  const topFiles = [...fileCounts.entries()]
    .map(([path, count]) => ({ path, count }))
    .sort((a, b) => b.count - a.count)
    .slice(0, 5)
  const toolFootprint: ToolFootprintState = {
    intentsCount: intents.length,
    actionsCount: actions.length,
    byTool,
    byActionType,
    bySignalClass,
    topFiles,
  }
  const footprintHasSignal = intents.length > 0 || actions.length > 0

  // ── 网络会话 ──
  const networkSessions: NetworkSessionsState = { total: tls.total, byPid: tls.byPid, trend: tls.trend }

  return {
    derivedAtMs,
    sourceLabel,
    health: {
      topologyDrift: driftHealth,
      topologyHealth: {
        available: healthAvailable,
        status: healthStatus,
        score: null, // 无自然比率（存在性视角）——诚实留空，靠 signals 展示原始计数
        signals: healthSignals,
      },
      intentMismatch: {
        available: mismatch.available,
        status: mismatchStatus,
        score: mismatchDensity != null ? clampScore(mismatchDensity * 10) : null,
        signals: mismatch.available
          ? [
              `失配 ${mismatch.count}`,
              mismatchDensity != null ? `密度 ${mismatchDensity.toFixed(1)}` : '无意图',
              `弱证据 ${mismatch.weak}/${mismatch.weak + mismatch.strong}`,
            ]
          : ['无失配表'],
      },
      policyCompliance: {
        available: policy.available,
        status: complianceStatus,
        score: violationRatio != null ? clampScore(violationRatio * 100) : null,
        signals: policy.available
          ? [
              `违规 ${policy.violations}/${policy.total}`,
              violationRatio != null ? `率 ${(violationRatio * 100).toFixed(1)}%` : '无评估',
            ]
          : ['无策略评估表'],
      },
      toolFootprint: {
        available: true,
        status: footprintHasSignal ? 'normal' : 'normal',
        score: null,
        signals: footprintHasSignal
          ? [`工具 ${intents.length}`, `动作 ${actions.length}`, `文件 ${topFiles.length}`]
          : ['无意图/动作'],
      },
      networkSessions: {
        available: tls.available,
        status: tls.total > 0 ? 'normal' : 'normal',
        score: null,
        signals: tls.available ? [`会话 ${tls.total}`, `进程 ${Object.keys(tls.byPid).length}`] : ['无 TLS 会话表'],
      },
    },
    topologyDrift,
    topologyHealth,
    intentMismatch,
    policyCompliance,
    toolFootprint,
    networkSessions,
  }
}
```

> 设计说明（提交时并入 commit message）：
> - `topologyHealth/toolFootprint/networkSessions` 的 `score` 恒为 null——它们是存在性视角，无自然比率；诚实留空、以 `signals` 承载原始计数（spec §4.1「无自然比率或无法计算 = null」）。
> - `toolFootprint/networkSessions` 无绝对数阈值触发 warning/critical——存在性视角的职责是展示分布而非评级（spec §4.3）。
> - `intentMismatch.score = density × 10`、`policyCompliance.score = ratio × 100`、`topologyDrift.score = driftRatio × 100`，均为文档化纯函数，与各自状态阈值同源。

- [ ] **Step 4: 运行测试确认通过**

Run: `cd frontend && npx vitest run __tests__/derive-monitoring.test.ts`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add lib/monitoring/derive-monitoring.ts __tests__/derive-monitoring.test.ts
git commit -m "监控派生纯函数：6 视角健康信号比率派生。漂移/失配/合规按文档化阈值评级并产出同源分数，健康/足迹/会话为存在性视角诚实留空分数，缺表 available=false 且状态归零，杜绝数据臆造"
```

---

### Task 4: store 接入 monitoring（字段 + 全 set 点置 null）

**Files:**
- Modify: `lib/db-import.ts`（`DbImport` 类型加 `monitoring`；`loadDatabase` 计算并返回）
- Modify: `lib/store.ts`（`AppStore` 加 `monitoring`；`applyDbImport` 写入；`applySnapshot`/`loadRun` API 分支/`initData` 两处 setState 置 null；初始值 null）
- Modify: `__tests__/store-db.test.ts`（beforeEach 补 reset；新增 4 条用例）

**Interfaces:**
- Consumes: `deriveMonitoringState` + `MonitoringDeriveInput`（Task 3）；`DbImport`（db-import.ts）。
- Produces: `useAppStore().monitoring: MonitoringState | null`。监控区块（Task 6）订阅此字段。

- [ ] **Step 1: 写失败测试（store-db.test.ts）**

在 `__tests__/store-db.test.ts` 的 `describe` 内追加 4 条用例，并在 `beforeEach` 的 `useAppStore.setState({...})`（第 67-79 行）里补 `monitoring: null`：

```ts
    monitoring: null,
```

新用例（追加在现有 `loadRun reports an error...` 用例之后、describe 结束前）：

```ts
  test('loadDatabase 派生并写入 monitoring（含 6 视角 health）', async () => {
    const dbImport = await loadDatabaseFromBytes(new Uint8Array(fixtureBytes()), 'run-42.db');
    useAppStore.getState().loadDatabase(dbImport);

    const m = useAppStore.getState().monitoring;
    expect(m).not.toBeNull();
    expect(m?.sourceLabel).toBe('run-42.db');
    expect(typeof m?.derivedAtMs).toBe('number');
    expect(m?.health.topologyDrift.status).toBe('warning'); // fixture 漂移 0.5
    expect(m?.health.policyCompliance.available).toBe(true);
    expect(m?.topologyDrift?.frames).toHaveLength(3);
  });

  test('loadRun 走 db 分支同样写入 monitoring', async () => {
    vi.mocked(api.getRunDb).mockResolvedValueOnce(fixtureBytes());
    await useAppStore.getState().loadRun('run-1');
    expect(useAppStore.getState().monitoring?.sourceLabel).toBe('run-1');
  });

  test('loadRun API 回退分支置 monitoring 为 null', async () => {
    vi.mocked(api.getRunDb).mockRejectedValueOnce(new Error('HTTP 404'));
    vi.mocked(api.getGraph).mockResolvedValueOnce(graph);
    vi.mocked(api.getAlerts).mockResolvedValueOnce([alert] as never);
    // 先注入 db，再走 API 回退，验证残留被清空
    const dbImport = await loadDatabaseFromBytes(new Uint8Array(fixtureBytes()), 'run-42.db');
    useAppStore.getState().loadDatabase(dbImport);
    expect(useAppStore.getState().monitoring).not.toBeNull();

    await useAppStore.getState().loadRun('run-1');
    expect(useAppStore.getState().monitoring).toBeNull();
  });

  test('db 导入后切 demo 清空 monitoring 防残留', async () => {
    const dbImport = await loadDatabaseFromBytes(new Uint8Array(fixtureBytes()), 'run-42.db');
    useAppStore.getState().loadDatabase(dbImport);
    expect(useAppStore.getState().monitoring).not.toBeNull();

    useAppStore.getState().loadDemo();
    expect(useAppStore.getState().monitoring).toBeNull();
  });
```

- [ ] **Step 2: 运行确认失败**

Run: `cd frontend && npx vitest run __tests__/store-db.test.ts`
Expected: FAIL —— `useAppStore.getState().monitoring` 为 undefined（字段未定义）；`dbImport.monitoring` 不存在。

- [ ] **Step 3: db-import.ts 增加 monitoring 字段**

`lib/db-import.ts` 第 42-66 行的 `DbImport` 类型，在 `overview: DatasetOverview;` 之后加：

```ts
  /** 运行监控派生结果（Task 3 deriveMonitoringState 产出）。 */
  monitoring: MonitoringState;
```

顶部 import 补：

```ts
import { deriveMonitoringState } from './monitoring/derive-monitoring';
import type { MonitoringState } from './monitoring/monitoring-types';
```

`loadDatabase`（第 645-698 行）在 `const counts = extractCountsFromDb(db);` 之后、`return {...}` 里加：

```ts
    const monitoring = deriveMonitoringState({
      sourceLabel: label,
      derivedAtMs: Date.now(),
      snapshots: extractSnapshotMetricsFromDb(db),
      alerts,
      pollution: extractPollutionAggregateFromDb(db),
      mismatch: extractMismatchAggregateFromDb(db),
      policy: extractPolicyComplianceFromDb(db),
      tls: extractTlsSessionAggregateFromDb(db),
      intents,
      actions,
    });
```

return 对象加 `monitoring,`。

- [ ] **Step 4: store.ts 接入**

`lib/store.ts`：
1. 顶部 import 补 `import type { MonitoringState } from './monitoring/monitoring-types';`
2. `AppStore` 接口在 `overview: DatasetOverview | null;`（第 76 行）后加 `monitoring: MonitoringState | null;`
3. 初始 state（第 213 行 `overview: null,`）后加 `monitoring: null,`
4. `applyDbImport`（第 165-187 行）set 对象在 `overview: dbImport.overview,` 后加 `monitoring: dbImport.monitoring,`
5. `applySnapshot`（第 133-159 行）set 对象在 `overview: null,` 后加 `monitoring: null,`
6. `loadRun` API 回退 set（第 288-299 行）在 `overview: null,` 后加 `monitoring: null,`
7. `initData` 两个 setState（第 415-420 行 sample、第 429-434 行 demo）各加 `monitoring: null,`

- [ ] **Step 5: 运行测试确认通过 + 回归**

Run: `cd frontend && npx vitest run __tests__/store-db.test.ts __tests__/db-import.test.ts __tests__/store.test.ts`
Expected: 全 PASS。随后 `npm run type-check`、`npm run lint` 0 错误。

- [ ] **Step 6: 提交**

```bash
git add lib/db-import.ts lib/store.ts __tests__/store-db.test.ts
git commit -m "store 接入运行监控：db 导入随 DbImport 派生 monitoring 并写入，applySnapshot/loadRun API 回退/initData 全部 set 点置 null 防切换数据源残留，回归用例补齐"
```

---

### Task 5: 修复 dataset-summary 策略违规假阳性（M1）

**Files:**
- Modify: `lib/dataset-summary.ts`（policy-violations 信号改消费 `violations`）
- Modify: `__tests__/overview-data.test.ts`（更新 signals 断言 + 新增零违规用例）

**Interfaces:**
- Consumes: `DatasetOverview['policy_evaluations']`（Task 2 已加 `violations`）。
- Produces: 无新导出；`deriveDatasetSummary` 的 policy-violations 信号 count = 真实违规数。

- [ ] **Step 1: 写失败测试（overview-data.test.ts）**

在 `describe` 末尾追加两条用例：

```ts
  it('策略违规信号使用 judgment_json 真实违规数而非全量评估数（M1 修复）', () => {
    const s = deriveDatasetSummary(
      sampleOverview({
        policy_evaluations: { count: 755, violations: 12 },
        claims: { count: 0, by_type: {}, by_severity: {} },
      }),
    )
    const policy = s.signals.find((x) => x.category === 'policy-violations')
    expect(policy).toBeTruthy()
    expect(policy!.count).toBe(12)
    expect(policy!.detail).toContain('Violation')
  })

  it('无违规时不产生策略信号（nothing is invented）', () => {
    const s = deriveDatasetSummary(
      sampleOverview({ policy_evaluations: { count: 755, violations: 0 } }),
    )
    expect(s.signals.find((x) => x.category === 'policy-violations')).toBeUndefined()
  })
```

- [ ] **Step 2: 运行确认失败**

Run: `cd frontend && npx vitest run __tests__/overview-data.test.ts`
Expected: 第一条 FAIL —— 当前实现 `count: overview.policy_evaluations.count`（755）≠ 12；第二条 FAIL —— 当前 `count>0` 会误报信号。

- [ ] **Step 3: 修复 dataset-summary.ts**

`lib/dataset-summary.ts` 第 135-143 行的 policy-violations 信号改为：

```ts
  const policyViolations = overview.policy_evaluations.violations ?? 0
  if (policyViolations > 0) {
    signals.push({
      severity: 'Warning',
      category: 'policy-violations',
      title: '策略违规',
      detail: 'judgment_json.result = Violation',
      count: policyViolations,
    })
  }
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd frontend && npx vitest run __tests__/overview-data.test.ts __tests__/dataset-overview-panel.test.tsx`
Expected: 全 PASS。

- [ ] **Step 5: 提交**

```bash
git add lib/dataset-summary.ts __tests__/overview-data.test.ts
git commit -m "修复概览策略违规假阳性：违规信号改为消费 judgment_json 解析的真实 Violation 计数，零违规不再误报，概览信号与监控合规视角同源"
```

---

### Task 6: 运行监控区块骨架（健康总览条 + 实时占位 + tab + 卡片网格 + 详情外壳）

**Files:**
- Create: `components/overview/monitoring/monitoring-section.tsx`
- Create: `components/overview/monitoring/monitoring-card.tsx`
- Create: `components/overview/monitoring/perspective-config.ts`
- Create: `components/overview/monitoring/perspective-detail-shell.tsx`
- Create: `__tests__/monitoring-section.test.tsx`
- Modify: `components/overview/overview-page-client.tsx`（插入 `<MonitoringSection />`）

**Interfaces:**
- Consumes: `useAppStore().monitoring`（Task 4）；`monitoring-types` 的 `PerspectiveKey/MonitoringState/PerspectiveHealth`；`PERSPECTIVE_ORDER` 导出（perspective-config）；`StatusBadge/SectionPanel/TrendChart`。
- Produces: `perspective-config.ts` 导出 `PERSPECTIVE_META`（label/description）、`PERSPECTIVE_ORDER: PerspectiveKey[]`、`PHASE2_PILLS: string[]`；`perspective-views.tsx`（Task 7）按此渲染详情。

- [ ] **Step 1: 写失败组件测试（monitoring-section.test.tsx）**

新建 `__tests__/monitoring-section.test.tsx`，首行 `// @vitest-environment jsdom`。用 `useAppStore.setState` 注入 hand-built `MonitoringState`：

```tsx
// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import MonitoringSection from '@/components/overview/monitoring/monitoring-section';
import { useAppStore } from '@/lib/store';
import type { MonitoringState, PerspectiveHealth, PerspectiveKey } from '@/lib/monitoring/monitoring-types';

const KEYS: PerspectiveKey[] = ['topologyDrift', 'topologyHealth', 'intentMismatch', 'policyCompliance', 'toolFootprint', 'networkSessions'];

/** 默认全部 normal/available，可按 key 覆盖——与 PerspectiveHealth 类型严格一致。 */
function health(over: Partial<Record<PerspectiveKey, Partial<PerspectiveHealth>>> = {}): MonitoringState['health'] {
  const base: PerspectiveHealth = { status: 'normal', available: true, score: 0, signals: ['—'] };
  const out = {} as MonitoringState['health'];
  for (const k of KEYS) out[k] = { ...base, ...(over[k] ?? {}) };
  return out;
}

function makeMonitoring(over: Partial<MonitoringState> = {}): MonitoringState {
  return {
    derivedAtMs: 0,
    sourceLabel: 'run-42.db',
    health: health(over.health),
    topologyDrift: null,
    topologyHealth: null,
    intentMismatch: null,
    policyCompliance: null,
    toolFootprint: null,
    networkSessions: null,
    ...over,
  };
}

afterEach(() => {
  cleanup();
  useAppStore.setState({ monitoring: null });
});

describe('MonitoringSection', () => {
  it('渲染区块头、6 个健康格、实时占位行与 tab', () => {
    useAppStore.setState({ monitoring: makeMonitoring() });
    render(<MonitoringSection />);
    expect(screen.getByText('运行监控')).toBeTruthy();
    for (const label of ['拓扑漂移', '拓扑健康', '意图-动作失配', '策略合规', '工具足迹', '网络会话']) {
      // 每视角出现在健康格 + tab + 卡片各一次
      expect(screen.getAllByText(label).length).toBeGreaterThan(0);
    }
    expect(screen.getByText(/实时视角 · 阶段二/)).toBeTruthy();
    expect(screen.getByText('LLM API 追踪')).toBeTruthy();
    expect(screen.getByRole('button', { name: '全部' })).toBeTruthy();
  });

  it('缺表视角（available=false）显示「无此表」而非并入健康聚合', () => {
    useAppStore.setState({
      monitoring: makeMonitoring({ health: health({ topologyDrift: { available: false } }) }),
    });
    render(<MonitoringSection />);
    // 缺表格显示无此表（文字而非颜色）
    expect(screen.getByText('无此表')).toBeTruthy();
  });

  it('点击 tab 就地切换到详情外壳，返回全部恢复卡片网格', () => {
    useAppStore.setState({ monitoring: makeMonitoring() });
    render(<MonitoringSection />);
    // 默认全部 → 卡片网格可见
    expect(screen.getByText('返回全部')).toBeTruthy(); // 全部态渲染返回锚（禁用态由实现决定）
    // 点击「拓扑漂移」健康格（name 为按钮完整可访问名，用正则子串匹配）
    const driftCell = screen.getAllByRole('button', { name: /拓扑漂移/ })[0];
    driftCell.click();
    // 详情外壳出现唯一描述文案（card grid 已替换为外壳）
    expect(screen.getByText(/逐帧演进/)).toBeTruthy();
  });
});
```

> 注：第 3 条断言锚点 = 详情外壳的**唯一描述文案**（`graph_snapshots.metrics_json 逐帧演进…`，仅外壳渲染，健康格/tab 不重复该文案），避免 `getByText(/拓扑漂移/)` 因健康格/tab/面包屑多处匹配抛错。测试以「能验证意图」为纲，不强绑实现细节。

- [ ] **Step 2: 运行确认失败**

Run: `cd frontend && npx vitest run __tests__/monitoring-section.test.tsx`
Expected: FAIL —— 模块不存在。

- [ ] **Step 3: 实现 perspective-config.ts**

`components/overview/monitoring/perspective-config.ts`（纯数据，无组件）：

```ts
import type { PerspectiveKey } from '@/lib/monitoring/monitoring-types'

export const PERSPECTIVE_META: Record<PerspectiveKey, { label: string; description: string }> = {
  topologyDrift: {
    label: '拓扑漂移',
    description: 'graph_snapshots.metrics_json 逐帧演进：节点/边/agent 数随快照帧的变化与首末差分。',
  },
  topologyHealth: {
    label: '拓扑健康',
    description: '最新帧拓扑足迹 + 告警严重度 + 上下文污染，度量拓扑生长而非时延健康。',
  },
  intentMismatch: {
    label: '意图-动作失配',
    description: 'non_link_diagnostics 诊断量、按 reason 分布与证据匹配强度（L5Substring 为弱证据）。',
  },
  policyCompliance: {
    label: '策略合规',
    description: 'policy_evaluations 中 judgment_json.result=Violation 的真实违规率与边界分布。',
  },
  toolFootprint: {
    label: '工具足迹',
    description: '意图工具调用 / 动作类型 / 信号类 / top 文件路径（normalized_args.Path）。',
  },
  networkSessions: {
    label: '网络会话',
    description: 'tls_sessions 会话计数、60s 时间分桶与按 pid 分布。',
  },
}

export const PERSPECTIVE_ORDER: PerspectiveKey[] = [
  'topologyDrift',
  'topologyHealth',
  'intentMismatch',
  'policyCompliance',
  'toolFootprint',
  'networkSessions',
]

/** 阶段二实时视角（SSE 占位）——静态文案，不接入数据。 */
export const PHASE2_PILLS = [
  'LLM API 追踪',
  'LLM API 异常',
  '数据外泄',
  'Agent 循环',
  '会话健康（实时五维）',
  'SSE 内容扫描',
  '去重诊断',
  '分析器流水线',
]
```

- [ ] **Step 4: 实现 monitoring-card.tsx**

`components/overview/monitoring/monitoring-card.tsx`：

```tsx
'use client'

import type { PerspectiveKey, MonitoringState, PerspectiveHealth } from '@/lib/monitoring/monitoring-types'
import { StatusBadge, type StatusTone } from '@/components/ui/linear/status-badge'
import { TrendChart } from '@/components/ui/linear/trend-chart'
import { PERSPECTIVE_META } from './perspective-config'

export function statusToTone(h: PerspectiveHealth): StatusTone {
  if (!h.available) return 'muted'
  return h.status === 'critical' ? 'danger' : h.status === 'warning' ? 'warning' : 'success'
}

/** 只有存在时间序列的视角提供迷你趋势；其余为 null（诚实，不造曲线）。 */
export function cardTrend(key: PerspectiveKey, monitoring: MonitoringState): Array<Record<string, number | string>> | null {
  if (key === 'topologyDrift') {
    const frames = monitoring.topologyDrift?.frames ?? []
    return frames.map((f, i) => ({ name: String(i + 1), value: f.nodeCount }))
  }
  if (key === 'networkSessions') {
    const trend = monitoring.networkSessions?.trend ?? []
    return trend.map((b, i) => ({ name: String(i + 1), value: b.count }))
  }
  return null
}

type Props = {
  perspectiveKey: PerspectiveKey
  health: PerspectiveHealth
  monitoring: MonitoringState
  onOpen: () => void
}

export function MonitoringCard({ perspectiveKey, health, monitoring, onOpen }: Props) {
  const meta = PERSPECTIVE_META[perspectiveKey]
  const trend = cardTrend(perspectiveKey, monitoring)
  return (
    <button
      type="button"
      onClick={onOpen}
      aria-label={`${meta.label}：${health.available ? health.status : '无此表'}${health.score != null ? `，${health.score} 分` : ''}`}
      className="ln-panel"
      style={{ textAlign: 'left', display: 'flex', flexDirection: 'column', gap: 10, padding: 14, cursor: 'pointer', width: '100%' }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <span style={{ fontWeight: 600, fontSize: 13 }}>{meta.label}</span>
        <StatusBadge tone={statusToTone(health)}>
          {health.available ? (health.status === 'critical' ? '严重' : health.status === 'warning' ? '关注' : '正常') : '无此表'}
        </StatusBadge>
      </div>
      {health.score != null ? (
        <div style={{ fontSize: 24, fontWeight: 700, fontVariantNumeric: 'tabular-nums' }}>{health.score}</div>
      ) : (
        <div style={{ fontSize: 24, fontWeight: 700, color: 'var(--ln-text-muted)' }}>—</div>
      )}
      {trend && trend.length > 1 ? (
        <TrendChart type="area" data={trend} xKey="name" seriesKey="value" height={64} />
      ) : null}
      <ul style={{ display: 'flex', flexDirection: 'column', gap: 2, margin: 0, padding: 0, listStyle: 'none' }}>
        {health.signals.map((s, i) => (
          <li key={i} className="ln-metric__label" style={{ fontSize: 11 }}>
            {s}
          </li>
        ))}
      </ul>
    </button>
  )
}
```

- [ ] **Step 5: 实现 monitoring-section.tsx**

`components/overview/monitoring/monitoring-section.tsx`：

```tsx
'use client'

import { useState } from 'react'
import { useAppStore } from '@/lib/store'
import type { PerspectiveKey, MonitoringStatus, PerspectiveHealth } from '@/lib/monitoring/monitoring-types'
import { SectionPanel } from '@/components/ui/linear/section-panel'
import { StatusBadge } from '@/components/ui/linear/status-badge'
import { PERSPECTIVE_META, PERSPECTIVE_ORDER, PHASE2_PILLS } from './perspective-config'
import { MonitoringCard } from './monitoring-card'
import { PerspectiveDetailShell } from './perspective-detail-shell'

const STATUS_PRIORITY: Record<MonitoringStatus, number> = { normal: 0, warning: 1, critical: 2 }

/** 6 个可用（available=true）视角中最差状态——健康聚合不含缺表视角。 */
export function aggregateHealth(health: Record<PerspectiveKey, PerspectiveHealth>): MonitoringStatus {
  let worst: MonitoringStatus = 'normal'
  for (const key of PERSPECTIVE_ORDER) {
    const h = health[key]
    if (h && h.available && STATUS_PRIORITY[h.status] > STATUS_PRIORITY[worst]) worst = h.status
  }
  return worst
}

const STATUS_LABEL: Record<MonitoringStatus, string> = { critical: '严重', warning: '关注', normal: '正常' }

export default function MonitoringSection() {
  const monitoring = useAppStore((s) => s.monitoring)
  const [selected, setSelected] = useState<PerspectiveKey | null>(null)
  const [focusHint, setFocusHint] = useState<string | null>(null)

  if (!monitoring) return null

  const overall = aggregateHealth(monitoring.health)
  const timeStr = monitoring.derivedAtMs > 0
    ? new Date(monitoring.derivedAtMs).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
    : ''

  /** 交叉导航：目标 agent 有告警才写 #agentId=，否则降级提示（少死胡同）。 */
  const focusAgent = (agentId: string) => {
    const owned = useAppStore.getState().alerts.some((a) => a.primary_agent.agent_id === agentId)
    if (!owned) {
      setFocusHint(`${agentId.slice(0, 8)}… 无告警，已跳过聚焦`)
      return
    }
    setFocusHint(null)
    window.location.hash = `agentId=${agentId}`
  }

  return (
    <SectionPanel
      eyebrow="Monitoring"
      title="运行监控"
      meta={`${monitoring.sourceLabel}${timeStr ? ` · ${timeStr}` : ''}`}
      bodyClassName="ln-panel-body"
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
        <StatusBadge tone={overall === 'critical' ? 'danger' : overall === 'warning' ? 'warning' : 'success'}>
          总览 {STATUS_LABEL[overall]}
        </StatusBadge>
        {focusHint ? <span className="ln-metric__label" style={{ fontSize: 11 }}>{focusHint}</span> : null}
      </div>

      <div style={{ maxHeight: 520, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 12 }}>
        {/* 健康总览条：6 个可用派生视角（缺表灰态不并入聚合） */}
        <div role="group" aria-label="监控健康总览" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 8 }}>
          {PERSPECTIVE_ORDER.map((key) => {
            const h = monitoring.health[key]
            return (
              <button
                key={key}
                type="button"
                onClick={() => setSelected(key)}
                aria-pressed={selected === key}
                className="ln-panel"
                style={{ textAlign: 'left', display: 'flex', flexDirection: 'column', gap: 6, padding: 12, cursor: 'pointer' }}
              >
                <StatusBadge tone={h.available ? (h.status === 'critical' ? 'danger' : h.status === 'warning' ? 'warning' : 'success') : 'muted'}>
                  {h.available ? STATUS_LABEL[h.status] : '无此表'}
                </StatusBadge>
                <span style={{ fontWeight: 600, fontSize: 12 }}>{PERSPECTIVE_META[key].label}</span>
                <span className="ln-metric__label" style={{ fontVariantNumeric: 'tabular-nums' }}>
                  {h.score != null ? h.score : '—'}
                </span>
              </button>
            )
          })}
        </div>

        {/* 实时视角行（阶段二 SSE 占位，不与派生视角混入聚合） */}
        <div role="group" aria-label="实时视角（阶段二）" style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8 }}>
          <span className="ln-eyebrow">实时视角 · 阶段二 SSE</span>
          {PHASE2_PILLS.map((p) => (
            <span key={p} aria-disabled="true" title="阶段二经 SSE 接入" style={{ opacity: 0.45, fontSize: 12, display: 'inline-flex', alignItems: 'center', gap: 4 }}>
              <span aria-hidden="true" style={{ fontSize: 8, color: 'var(--ln-warning)' }}>●</span>
              {p}
            </span>
          ))}
        </div>

        {/* 视角 tab */}
        <nav aria-label="监控视角切换" style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          <button type="button" onClick={() => setSelected(null)} aria-current={selected === null ? 'page' : undefined} className="ln-btn">
            全部
          </button>
          {PERSPECTIVE_ORDER.map((key) => (
            <button
              key={key}
              type="button"
              onClick={() => setSelected(key)}
              aria-current={selected === key ? 'page' : undefined}
              className="ln-btn"
            >
              {PERSPECTIVE_META[key].label}
            </button>
          ))}
        </nav>

        {/* 主体：全部 → 卡片网格；单选 → 详情工作台 */}
        {selected === null ? (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 10 }}>
            {PERSPECTIVE_ORDER.map((key) => (
              <MonitoringCard key={key} perspectiveKey={key} health={monitoring.health[key]} monitoring={monitoring} onOpen={() => setSelected(key)} />
            ))}
          </div>
        ) : (
          <PerspectiveDetailShell perspectiveKey={selected} monitoring={monitoring} onBack={() => setSelected(null)} onFocusAgent={focusAgent} />
        )}
      </div>
    </SectionPanel>
  )
}
```

- [ ] **Step 6: 实现 perspective-detail-shell.tsx**

`components/overview/monitoring/perspective-detail-shell.tsx`（骨架 + 摘要统计行 + 面包屑；详情内容由 Task 7 的 `PerspectiveView` 渲染）：

```tsx
'use client'

import type { ReactNode } from 'react'
import type { PerspectiveKey, MonitoringState } from '@/lib/monitoring/monitoring-types'
import { PERSPECTIVE_META } from './perspective-config'

type Props = {
  perspectiveKey: PerspectiveKey
  monitoring: MonitoringState
  onBack: () => void
  onFocusAgent: (agentId: string) => void
  children?: ReactNode
}

/** 详情工作台统一外壳：面包屑 + 标题/描述 + 摘要统计行 + 视图内容。 */
export function PerspectiveDetailShell({ perspectiveKey, monitoring, onBack, onFocusAgent, children }: Props) {
  const meta = PERSPECTIVE_META[perspectiveKey]
  const h = monitoring.health[perspectiveKey]
  const summary = children == null ? null : (null as never) // 摘要行由 Task 7 视图经 stats 提供

  return (
    <div aria-label={`${meta.label}详情`}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
        <button type="button" onClick={onBack} className="ln-btn" aria-label="返回全部">
          ← 返回全部
        </button>
        <span className="ln-eyebrow">运行监控 / {meta.label}</span>
      </div>
      <p style={{ margin: '0 0 10px', fontSize: 12, color: 'var(--ln-text-muted)', lineHeight: 1.6 }}>{meta.description}</p>
      {h.available ? (
        <div role="group" aria-label="摘要统计" style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 10 }}>
          {h.signals.map((s, i) => (
            <span key={i} className="ln-badge" data-tone={i === 0 ? 'info' : 'muted'}>
              {s}
            </span>
          ))}
        </div>
      ) : (
        <div className="ln-badge" data-tone="muted" style={{ marginBottom: 10 }}>
          无此表（旧版数据）——本视角无可用信号
        </div>
      )}
      {children}
    </div>
  )
}
```

> 注：`summary` 变量与 `onFocusAgent` prop 当前未在骨架内使用——Task 7 视图通过 children 接收 `onFocusAgent` 渲染 agent 行时用到；`summary` 为占位说明，实现时直接删除该行（避免 eslint 未用变量错误）。

- [ ] **Step 7: 接入 overview-page-client.tsx**

`components/overview/overview-page-client.tsx` 第 10 行附近 import 区加入：

```tsx
import MonitoringSection from '@/components/overview/monitoring/monitoring-section';
```

在图表行 `</div>`（第 547 行）之后、`{overview && (`（第 548 行）之前插入：

```tsx
            <MonitoringSection />
```

> MonitoringSection 内部对 `monitoring === null` 返回 null（demo/样例源不渲染），插入处无需条件包裹。

- [ ] **Step 8: 修正组件测试断言 + 全量回归**

按实现实际 DOM 完善 `__tests__/monitoring-section.test.tsx` 断言（锚点用确定文案：详情外壳标题 + 描述；`返回全部` 按钮 `getByRole('button', { name: '返回全部' })`）。

Run: `cd frontend && npx vitest run __tests__/monitoring-section.test.tsx __tests__/overview-page-client.test.tsx`
Expected: 全 PASS。随后 `npm run type-check`、`npm run lint` 0 错误。

- [ ] **Step 9: 提交**

```bash
git add components/overview/monitoring/monitoring-section.tsx components/overview/monitoring/monitoring-card.tsx components/overview/monitoring/perspective-config.ts components/overview/monitoring/perspective-detail-shell.tsx __tests__/monitoring-section.test.tsx components/overview/overview-page-client.tsx
git commit -m "运行监控区块骨架并入仪表盘：健康总览条仅聚合 6 个可用派生视角、实时视角阶段二占位行、视角 tab 与卡片网格、详情工作台统一外壳，缺表视角灰态区分旧版数据"
```

---

### Task 7: 6 个监控视角详情视图

**Files:**
- Create: `components/overview/monitoring/perspective-views.tsx`
- Modify: `components/overview/monitoring/perspective-detail-shell.tsx`（渲染 `PerspectiveView`，移除占位 `summary`）
- Create: `__tests__/perspective-views.test.tsx`

**Interfaces:**
- Consumes: `monitoring-types` 各视角 state；`MonitoringCard` 的 `statusToTone`；`PERSPECTIVE_META`；`TrendChart`/`VirtualList`。
- Produces: `renderPerspectiveView(key: PerspectiveKey, monitoring: MonitoringState, onFocusAgent): ReactNode` —— Task 6 外壳 children 用它填充。

- [ ] **Step 1: 写失败视图测试**

新建 `__tests__/perspective-views.test.tsx`（首行 jsdom docblock），复用 Task 6 的 `makeMonitoring` 思路（复制 `health()` + `makeMonitoring()` 帮助函数到本文件），对两个代表性视图断言：

```tsx
// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { renderPerspectiveView } from '@/components/overview/monitoring/perspective-views';
import type { MonitoringState } from '@/lib/monitoring/monitoring-types';

// … makeMonitoring 帮助函数（复制自 monitoring-section.test.tsx，见 Task 6 Step 1）……

describe('PerspectiveView — 六个视角详情', () => {
  it('拓扑漂移视图展示帧线图与首末差分', () => {
    const monitoring = makeMonitoring({
      topologyDrift: {
        frames: [
          { snapshot_id: 'f0', timestamp_ns: 0, nodeCount: 2, edgeCount: 2, agentCount: 1, crossAgentEdges: 0, delegationDepth: 0, taintChains: 0, avgConfidence: 0.6 },
          { snapshot_id: 'f1', timestamp_ns: 1, nodeCount: 3, edgeCount: 2, agentCount: 1, crossAgentEdges: 0, delegationDepth: 0, taintChains: 0, avgConfidence: 0.85 },
        ],
        latest: null,
        delta: { nodes: 1, edges: 0, agents: 0 },
      },
    });
    render(<>{renderPerspectiveView('topologyDrift', monitoring, () => {})}</>);
    expect(screen.getByText(/Δ\+1 节点/)).toBeTruthy();
  });

  it('拓扑健康视图展示污染源 agent，点击走 focusAgent', () => {
    const onFocus = () => {};
    const monitoring = makeMonitoring({
      topologyHealth: {
        latest: null,
        alertSeverity: { critical: 0, warning: 1, info: 1 },
        pollutionCount: 2,
        pollutionBySource: { 'agent-a': 1, 'agent-b': 1 },
      },
    });
    render(<>{renderPerspectiveView('topologyHealth', monitoring, onFocus)}</>);
    expect(screen.getByText('agent-a')).toBeTruthy();
    expect(screen.getByText('agent-b')).toBeTruthy();
  });
});
```

> 注：这两个用例锚点（`Δ+1 节点`、污染源 agent 文本）是方向的起点，实现时按实际渲染文案校准。

- [ ] **Step 2: 运行确认失败**

Run: `cd frontend && npx vitest run __tests__/perspective-views.test.tsx`
Expected: FAIL —— `perspective-views` 模块不存在。

- [ ] **Step 3: 实现 perspective-views.tsx**

`components/overview/monitoring/perspective-views.tsx`：

```tsx
'use client'

import type { ReactNode } from 'react'
import type { PerspectiveKey, MonitoringState, SnapshotMetricFrame } from '@/lib/monitoring/monitoring-types'
import { TrendChart } from '@/components/ui/linear/trend-chart'
import VirtualList from '@/components/ui/virtual-list'
import { PERSPECTIVE_META } from './perspective-config'

// ── 共享小件 ──

function ChartCard({ eyebrow, title, children }: { eyebrow: string; title: string; children: ReactNode }) {
  return (
    <div className="ln-panel" style={{ padding: 12, display: 'flex', flexDirection: 'column', gap: 8 }}>
      <span className="ln-eyebrow">{eyebrow}</span>
      <h4 style={{ margin: 0, fontSize: 13 }}>{title}</h4>
      {children}
    </div>
  )
}

function DistributionRows({ record, formatter = (k) => k }: { record: Record<string, number>; formatter?: (k: string) => string }) {
  const entries = Object.entries(record).sort((a, b) => b[1] - a[1]).slice(0, 10)
  const max = Math.max(1, ...entries.map(([, n]) => n))
  if (entries.length === 0) return <div className="ln-metric__label">无数据</div>
  return (
    <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 6 }}>
      {entries.map(([k, n]) => (
        <li key={k} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12 }}>
          <span style={{ width: 120, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={k}>
            {formatter(k)}
          </span>
          <span style={{ flex: 1, height: 6, borderRadius: 999, background: 'var(--ln-surface-3)' }}>
            <span style={{ display: 'block', height: '100%', width: `${Math.round((n / max) * 100)}%`, borderRadius: 999, background: 'var(--ln-accent)' }} />
          </span>
          <span className="ln-metric__label" style={{ fontVariantNumeric: 'tabular-nums' }}>{n}</span>
        </li>
      ))}
    </ul>
  )
}

function frameTrend(frames: SnapshotMetricFrame[]) {
  return frames.map((f, i) => ({ name: String(i + 1), node: f.nodeCount, edge: f.edgeCount, agent: f.agentCount }))
}

// ── 六个视图 ──

function TopologyDriftView({ monitoring }: { monitoring: MonitoringState }) {
  const s = monitoring.topologyDrift
  if (!s) return <EmptyView label="拓扑漂移" />
  const sampled = s.frames.length > 50 ? s.frames.filter((_, i) => i % Math.ceil(s.frames.length / 50) === 0) : s.frames
  return (
    <div style={{ display: 'grid', gap: 10 }}>
      <ChartCard eyebrow="Frames" title="节点/边/Agent 随快照帧演进">
        <TrendChart type="area" data={frameTrend(sampled)} xKey="name" seriesKey="node" height={180} />
      </ChartCard>
      {s.delta ? (
        <div style={{ display: 'flex', gap: 8 }}>
          {([
            ['Δ节点', s.delta.nodes],
            ['Δ边', s.delta.edges],
            ['ΔAgent', s.delta.agents],
          ] as const).map(([label, v]) => (
            <span key={label} className="ln-badge" data-tone={v !== 0 ? 'warning' : 'muted'}>
              {label} {v >= 0 ? '+' : ''}{v}
            </span>
          ))}
        </div>
      ) : null}
      {/* VirtualList 根容器是 height:100%，必须包在定高容器内否则塌缩为 0 */}
      <div style={{ height: 240 }} className="ln-panel" aria-label="快照帧明细">
        <VirtualList
          items={s.frames}
          estimateSize={30}
          className="scrollbar-none"
          renderRow={(f) => (
            <div style={{ display: 'flex', gap: 12, fontSize: 12, padding: '4px 8px' }}>
              <span className="ln-metric__label">{f.snapshot_id}</span>
              <span>节点 {f.nodeCount}</span>
              <span>边 {f.edgeCount}</span>
              <span>链 {f.taintChains}</span>
            </div>
          )}
        />
      </div>
    </div>
  )
}

function TopologyHealthView({ monitoring, onFocusAgent }: { monitoring: MonitoringState; onFocusAgent: (id: string) => void }) {
  const s = monitoring.topologyHealth
  if (!s) return <EmptyView label="拓扑健康" />
  const sev = s.alertSeverity
  const sevData = [
    { name: '严重', value: sev.critical },
    { name: '关注', value: sev.warning },
    { name: '信息', value: sev.info },
  ]
  return (
    <div style={{ display: 'grid', gap: 10 }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 8 }}>
        {[
          ['Agent', s.latest?.agentCount ?? '—'],
          ['跨 Agent 边', s.latest?.crossAgentEdges ?? '—'],
          ['污染链', s.latest?.taintChains ?? '—'],
          ['平均置信', s.latest ? s.latest.avgConfidence.toFixed(2) : '—'],
        ].map(([label, value]) => (
          <div key={label as string} className="ln-panel" style={{ padding: 10 }}>
            <div className="ln-metric__label">{label}</div>
            <div style={{ fontSize: 18, fontWeight: 700, fontVariantNumeric: 'tabular-nums' }}>{value}</div>
          </div>
        ))}
      </div>
      <ChartCard eyebrow="Alerts" title="告警严重度">
        <TrendChart type="bar" data={sevData} xKey="name" seriesKey="value" height={120} />
      </ChartCard>
      <ChartCard eyebrow="Pollution" title="上下文污染来源（点击聚焦 Agent）">
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          {Object.entries(s.pollutionBySource).map(([agentId, count]) => (
            <button key={agentId} type="button" onClick={() => onFocusAgent(agentId)} className="ln-btn" title="聚焦该 agent 的告警">
              {agentId.slice(0, 8)}… × {count}
            </button>
          ))}
          {s.pollutionCount === 0 ? <span className="ln-metric__label">无污染事件</span> : null}
        </div>
      </ChartCard>
    </div>
  )
}

function IntentMismatchView({ monitoring }: { monitoring: MonitoringState }) {
  const s = monitoring.intentMismatch
  if (!s) return <EmptyView label="意图-动作失配" />
  const matchData = [
    { name: '强证据', value: s.evidenceMatch.strong },
    { name: '弱证据 L5', value: s.evidenceMatch.weak },
  ]
  return (
    <div style={{ display: 'grid', gap: 10 }}>
      <div style={{ display: 'flex', gap: 16 }}>
        <div className="ln-panel" style={{ padding: 10 }}>
          <div className="ln-metric__label">失配诊断</div>
          <div style={{ fontSize: 22, fontWeight: 700, fontVariantNumeric: 'tabular-nums' }}>{s.diagnosticsCount}</div>
        </div>
        <div className="ln-panel" style={{ padding: 10 }}>
          <div className="ln-metric__label">失配密度</div>
          <div style={{ fontSize: 22, fontWeight: 700, fontVariantNumeric: 'tabular-nums' }}>{s.mismatchDensity?.toFixed(1) ?? '—'}</div>
        </div>
        <div className="ln-panel" style={{ padding: 10 }}>
          <div className="ln-metric__label">弱证据率</div>
          <div style={{ fontSize: 22, fontWeight: 700, fontVariantNumeric: 'tabular-nums' }}>
            {s.evidenceMatch.total > 0 ? `${Math.round((s.evidenceMatch.weak / s.evidenceMatch.total) * 100)}%` : '—'}
          </div>
        </div>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 10 }}>
        <ChartCard eyebrow="Reason" title="诊断原因分布">
          <DistributionRows record={s.byReason} />
        </ChartCard>
        <ChartCard eyebrow="Evidence" title="证据匹配强度">
          <TrendChart type="bar" data={matchData} xKey="name" seriesKey="value" height={140} />
        </ChartCard>
      </div>
    </div>
  )
}

function PolicyComplianceView({ monitoring }: { monitoring: MonitoringState }) {
  const s = monitoring.policyCompliance
  if (!s) return <EmptyView label="策略合规" />
  return (
    <div style={{ display: 'grid', gap: 10 }}>
      <div style={{ display: 'flex', gap: 16 }}>
        <div className="ln-panel" style={{ padding: 10 }}>
          <div className="ln-metric__label">违规率</div>
          <div style={{ fontSize: 22, fontWeight: 700, fontVariantNumeric: 'tabular-nums' }}>
            {s.violationRatio != null ? `${(s.violationRatio * 100).toFixed(1)}%` : '—'}
          </div>
        </div>
        <div className="ln-panel" style={{ padding: 10 }}>
          <div className="ln-metric__label">违规 / 评估</div>
          <div style={{ fontSize: 22, fontWeight: 700, fontVariantNumeric: 'tabular-nums' }}>{s.violations} / {s.evaluationsTotal}</div>
        </div>
      </div>
      <ChartCard eyebrow="Boundary" title="边界分布">
        <DistributionRows record={s.byBoundary} />
      </ChartCard>
    </div>
  )
}

function ToolFootprintView({ monitoring }: { monitoring: MonitoringState }) {
  const s = monitoring.toolFootprint
  if (!s) return <EmptyView label="工具足迹" />
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 10 }}>
      <ChartCard eyebrow="Tools" title="工具调用分布">
        <DistributionRows record={s.byTool} />
      </ChartCard>
      <ChartCard eyebrow="Actions" title="动作类型分布">
        <DistributionRows record={s.byActionType} />
      </ChartCard>
      <ChartCard eyebrow="Signals" title="信号类">
        <DistributionRows record={s.bySignalClass} />
      </ChartCard>
      <ChartCard eyebrow="Files" title="Top 文件路径">
        <DistributionRows
          record={Object.fromEntries(s.topFiles.map((f) => [f.path, f.count]))}
          formatter={(p) => p.replace(/^.*\/([^/]+)$/, '…/$1')}
        />
      </ChartCard>
    </div>
  )
}

function NetworkSessionsView({ monitoring }: { monitoring: MonitoringState }) {
  const s = monitoring.networkSessions
  if (!s) return <EmptyView label="网络会话" />
  const trend = s.trend.map((b, i) => ({ name: String(i + 1), value: b.count }))
  return (
    <div style={{ display: 'grid', gap: 10 }}>
      <div style={{ display: 'flex', gap: 16 }}>
        <div className="ln-panel" style={{ padding: 10 }}>
          <div className="ln-metric__label">会话总数</div>
          <div style={{ fontSize: 22, fontWeight: 700, fontVariantNumeric: 'tabular-nums' }}>{s.total}</div>
        </div>
        <div className="ln-panel" style={{ padding: 10 }}>
          <div className="ln-metric__label">进程数</div>
          <div style={{ fontSize: 22, fontWeight: 700, fontVariantNumeric: 'tabular-nums' }}>{Object.keys(s.byPid).length}</div>
        </div>
      </div>
      <ChartCard eyebrow="Trend" title="60s 时间分桶">
        <TrendChart type="area" data={trend} xKey="name" seriesKey="value" height={160} />
      </ChartCard>
      <ChartCard eyebrow="Pids" title="按 PID 分布">
        <DistributionRows record={s.byPid} />
      </ChartCard>
    </div>
  )
}

function EmptyView({ label }: { label: string }) {
  return <div className="ln-metric__label" style={{ padding: 16 }}>{label}：无此表（旧版数据）</div>
}

// ── 统一出口 ──

export function renderPerspectiveView(
  key: PerspectiveKey,
  monitoring: MonitoringState,
  onFocusAgent: (agentId: string) => void,
): ReactNode {
  switch (key) {
    case 'topologyDrift':
      return <TopologyDriftView monitoring={monitoring} />
    case 'topologyHealth':
      return <TopologyHealthView monitoring={monitoring} onFocusAgent={onFocusAgent} />
    case 'intentMismatch':
      return <IntentMismatchView monitoring={monitoring} />
    case 'policyCompliance':
      return <PolicyComplianceView monitoring={monitoring} />
    case 'toolFootprint':
      return <ToolFootprintView monitoring={monitoring} />
    case 'networkSessions':
      return <NetworkSessionsView monitoring={monitoring} />
  }
}
```

- [ ] **Step 4: 让 PerspectiveDetailShell 渲染视图**

`perspective-detail-shell.tsx`：移除未用的 `summary` 占位行与 `children` prop，改为直接渲染 `renderPerspectiveView` 的输出：

```tsx
import type { PerspectiveKey, MonitoringState } from '@/lib/monitoring/monitoring-types'
import { renderPerspectiveView } from './perspective-views'

type Props = {
  perspectiveKey: PerspectiveKey
  monitoring: MonitoringState
  onBack: () => void
  onFocusAgent: (agentId: string) => void
}

export function PerspectiveDetailShell({ perspectiveKey, monitoring, onBack, onFocusAgent }: Props) {
  const meta = PERSPECTIVE_META[perspectiveKey]
  const h = monitoring.health[perspectiveKey]
  return (
    <div aria-label={`${meta.label}详情`}>
      {/* 面包屑 + 返回（保留 Task 6 原样）… */}
      {/* 描述 + 摘要 signals（保留 Task 6 原样）… */}
      {renderPerspectiveView(perspectiveKey, monitoring, onFocusAgent)}
    </div>
  )
}
```

- [ ] **Step 5: 校准视图测试断言 + 全量回归**

按实现实际文案校准 `__tests__/perspective-views.test.tsx` 断言（`Δ+1 节点` 徽章、污染源 agent 行）。

Run: `cd frontend && npx vitest run __tests__/perspective-views.test.tsx __tests__/monitoring-section.test.tsx`
Expected: 全 PASS。随后 `npm run type-check`、`npm run lint` 0 错误。

- [ ] **Step 6: 提交**

```bash
git add components/overview/monitoring/perspective-views.tsx components/overview/monitoring/perspective-detail-shell.tsx __tests__/perspective-views.test.tsx
git commit -m "六个监控视角详情视图：漂移帧演进线图与首末差分、健康告警严重度与污染源聚焦、失配原因与证据强度、合规违规率与边界分布、工具足迹四联分布、会话时间桶与 PID 分布，明细走虚拟化列表"
```

---

### Task 8: 交叉导航 + 集成 + 旧组件标注 + 全量回归

**Files:**
- Modify: `components/overview/monitoring/monitoring-section.tsx`（agent 聚焦可解析性校验已有——见 Task 6 Step 5 `focusAgent`）
- Modify: `components/ui/` 下 10 个孤儿 dashboard 组件（顶部加 `@deprecated` JSDoc）
- Modify（回归验证）: 无代码改动，仅跑通全部门禁

**Interfaces:**
- Consumes: Task 1-7 全部产物。
- Produces: 分支可合并状态（全量回归绿 + 工作区干净）。

- [ ] **Step 1: 校验交叉导航实现已完整**

确认 `monitoring-section.tsx` 的 `focusAgent` 已满足 spec §5.4：点击前用 `useAppStore.getState().alerts` 校验目标 agent 是否有告警；有则写 `window.location.hash = 'agentId=<id>'`（复用既有 overview 聚焦机制），无则 `setFocusHint` 降级提示。补充一个单元断言（在 `monitoring-section.test.tsx` 加一条）：

```tsx
  it('聚焦无告警 agent 时降级提示而非死链接', () => {
    useAppStore.setState({
      monitoring: makeMonitoring({
        health: health({ topologyHealth: { available: true } }),
        topologyHealth: {
          latest: null,
          alertSeverity: { critical: 0, warning: 0, info: 0 },
          pollutionCount: 1,
          pollutionBySource: { 'ghost-agent': 1 },
        },
      }),
      alerts: [], // ghost-agent 无告警 → 降级
    });
    render(<MonitoringSection />);
    // 打开拓扑健康详情（点击健康格）
    screen.getAllByRole('button', { name: /拓扑健康/ })[0].click();
    // 点击污染源 chip → focusAgent('ghost-agent') 因无告警而降级
    screen.getByRole('button', { name: /ghost-ag/ }).click();
    expect(screen.getByText(/无告警，已跳过聚焦/)).toBeTruthy();
  });
```

> 注：污染源 chip 的 `onClick` 由 Task 7 的 TopologyHealthView 实现（`onFocusAgent` 透传自 section 的 `focusAgent`）；chip 文案为 `agentId.slice(0, 8)… × count`，`/ghost-ag/` 可匹配。

- [ ] **Step 2: 给 11 个孤儿 dashboard 加 @deprecated 标注**

对以下文件，在首个 `export default function` / `export function` 前插入一行 JSDoc：

```ts
/** @deprecated 阶段二实时视角（SSE 接入后迁移至运行监控区块），现无生产者，仅存档。 */
```

文件清单（已 grep 核实引用关系，11 个均从产品不可达）：
`components/ui/agent-drift-dashboard.tsx`、`agent-loop-dashboard.tsx`、`analyzer-dashboard.tsx`、`data-exfiltration-dashboard.tsx`、`dedup-diagnostics-panel.tsx`、`intent-verifier-dashboard.tsx`、`llm-api-anomaly-dashboard.tsx`、`llm-api-tracker-dashboard.tsx`、`session-health-dashboard.tsx`、`sse-content-scanner-dashboard.tsx`，以及 **`tool-execution-dashboard.tsx`**（仅被同样孤儿的 `analyzer-dashboard.tsx` 引用，传递孤儿）。排除 `prism-panel.tsx`（被 active 的 observatory-page-hero 引用）。

- [ ] **Step 3: 全量回归门禁**

Run 以下命令，全部必须通过：
1. `cd frontend && npm run type-check` → 0 errors
2. `cd frontend && npm run lint` → 0 warnings
3. `cd frontend && npx vitest run` → 全部测试绿（308 + 本计划新增）
4. `cd frontend && npm run build` → 成功
5. `git status --short` → 除本计划产物外无意外改动

- [ ] **Step 4: e2e 冒烟（可选，需真实环境）**

若可访问 `env/runs/*/collector.responsibility.db`：导入真实 db 后验证 overview 出现「运行监控」区块与 6 视角信号（仅冒烟，不断言具体计数——跨 run 不稳定）。此步为手动验证，记录在分支完成报告中。

- [ ] **Step 5: 提交**

```bash
git add components/ui/agent-drift-dashboard.tsx components/ui/agent-loop-dashboard.tsx components/ui/analyzer-dashboard.tsx components/ui/data-exfiltration-dashboard.tsx components/ui/dedup-diagnostics-panel.tsx components/ui/intent-verifier-dashboard.tsx components/ui/llm-api-anomaly-dashboard.tsx components/ui/llm-api-tracker-dashboard.tsx components/ui/session-health-dashboard.tsx components/ui/sse-content-scanner-dashboard.tsx components/ui/tool-execution-dashboard.tsx __tests__/monitoring-section.test.tsx
git commit -m "运行监控交叉导航与收尾：污染源 agent 聚焦前做告警可解析校验、无告警降级提示防死链接，10 个旧 analyzer dashboard 标记 deprecated 存档，全量回归通过"
```

---

## Self-Review 记录

**Spec 覆盖核对**（spec → task）：
- §一.1 阶段一 6 信号 → Task 2/3/6/7
- §一.2 阶段二占位 → Task 6（PHASE2_PILLS 行）
- §二 边界事实（action_type/node_type/latency 缺失等）→ 全部落实为「不可派生视角不做的理由」，Task 3 派生仅消费 6 个诚实信号
- §三 真实表结构基线 → Task 2 提取器列名逐一对齐 schema
- §4.1 类型 → Task 2 monitoring-types（含 available/status 分离、score null 语义）
- §4.2 提取器 → Task 2（5 个提取器 + available 契约细化 + toolFootprint 零新提取）
- §4.3 派生规则 → Task 3（比率阈值 + 缺表归零 + 分数同源）
- §4.4 store → Task 4（monitoring 字段 + 全 set 点置 null）
- §5.1 位置/容器 → Task 6（图表行后插入、自滚动容器）
- §5.2 区块结构 → Task 6（健康条/实时行/tab/卡片）
- §5.3 详情外壳 + 配置表 → Task 6/7（PerspectiveDetailShell + perspective-config）
- §5.4 交叉导航 → Task 8（focusAgent 可解析校验）
- §5.5 空态规范 → Task 3（available 分级）+ Task 6/7（缺表灰态「无此表」）
- §5.6 M1 修复 → Task 5
- §6 测试计划 → Task 1（fixture）/2（提取器）/3（派生）/4（store）/5（M1）/6/7（组件）
- §7 实施顺序 → 1→8 顺序一致
- §8 非目标 → 全部未触碰（不做 LLM API/外泄/五维、不读 nodes_json、不改 Rust、不删旧组件仅标注）

**类型一致性**：`MonitoringDeriveInput`（Task 3）字段与 5 提取器返回（Task 2）逐字段对应；`DbImport.monitoring`（Task 4）类型为 `MonitoringState`；store 三处 set 点同字段。Task 6/7 消费 `PerspectiveKey/MonitoringState/PerspectiveHealth` 与 Task 2 定义一致。

**占位说明**：测试中少量「方向性断言」以 `> 注：` 标注，要求实现者按实际 DOM 校准——这是 TDD 中对尚未存在组件的锚点策略，非计划缺口；断言以「能验证意图」为纲。
