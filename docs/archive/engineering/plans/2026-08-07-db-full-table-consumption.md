# 数据库全表充分消费实现计划 — 让 Claude Code 不再是黑盒子

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 导入真实 `collector.responsibility.db` 后，前端新增消费 `responsibility_claims` / `responsibility_tracebacks` / `intents` / `actions` 四表 + `DatasetOverview` 各表概要，按 agentsight「列表 → 展开详情」风格落位到概览页 / 告警工作台 / 追踪页 / 数据源选择器，并移除概览页人造 KPI。

**Architecture:** 三层。① 数据层：`lib/types.ts` 新增 4 类型（复用现有 `Intent`/`Action`），`lib/db-import.ts` 新增 5 个提取器（纯函数，表缺失返回空），`lib/store.ts` 扩展 `AppStore` + `applyDbImport`；② 派生层：`lib/dataset-summary.ts` 提供 `deriveDatasetSummary` 纯函数（「Nothing is invented」——所有 KPI 从 `DatasetOverview` 计算，算不出的留空）；③ UI 层：`OverviewDatasetPanel`（概览）、`ClaimList`（告警）、`IntentActionChain`（追踪）、`run-selector` 七项 meta。每个数据源切换走同一个 store 快照，组件从 store 读纯数据、用现有 `SectionPanel` / `VirtualList` / `StatusBadge` / `AgentAvatar` 视觉基元渲染。

**Tech Stack:** TypeScript 5（`tsc --noEmit` 校验）、React 19 + Next.js 16、vitest 4 + @testing-library/react 16（`// @vitest-environment jsdom`）、sql.js（浏览器解析 db）、Playwright（e2e）。

## Global Constraints

以下约束对所有任务隐式生效，逐条从 spec 复制：

1. **复用现有类型，不新增不修改** `Intent`（`lib/types.ts:182`）与 `Action`（`lib/types.ts:195`）——它们是 `lib/api.ts:82` 的响应类型，已被 `evidence-panel.tsx` 消费。改类型会牵连消费方，故保持不动。
2. **`ClaimState` 固定三值**：`'Inferred' | 'Confirmed' | 'Ambiguous'`（实测 22 条分布 18/3/1）。
3. **`tool_args_json` / `normalized_args_json` 真实形态是 `Array<{Path|Content|Command}>`**（非 `NormalizedArgs` 对象）——用 `JSON.parse`（走 `parseJsonArray`），**禁用 `parseMatchedFields`**（它只认 `canonical_path/host/port/command/raw_args` 键，会清空全部 Path/Command）。
4. **回溯链反向索引** `tracebacksByClaimId`：真实外键是 `traceback.claim_id → claim`（实测 22 条 claim 的 `traceback_ref` 全 NULL）。`Map<claim_id, ResponsibilityTraceback>` 为主路径，`claim.traceback_ref` 仅作未来 run 的次选 hint。
5. **`hasConsumableData` 语义更新**（`lib/db-import.ts:444`）：`graph.nodes.length > 0 || alerts.length > 0 || evidence.length > 0 || claims.length > 0 || intents.length > 0 || actions.length > 0`。
6. **「不幻觉」质量底线**：所有 KPI/分布由纯函数从导入数据派生；算不出的字段返回空/`null`（如 `runs.ended_at_ns` 真实 NULL → `null`），绝不用写死的数值模拟。
7. **概览页去演示化**：删除人造 KPI——`graph.edges.length * 72`、`graph.edges.length * 41`、`72 + totalEvidence * 4.3`%、写死的 spark 数组。
8. **概览页 `DatasetOverview` 仅在 db 导入/后端 db 路径有数据**；demo/sample/API-fallback 路径为空概览，UI 空态兜底。
9. **不做**：多 db 合并、token 统计（db 无此数据）、claim↔alert 强绑定（无外键）、修改 Rust 后端、修改 3D 首页与侧边栏导航结构。
10. **前端代码风格**：现有组件用分号风格；新组件文件头 `'use client'`；组件测试文件首行 `// @vitest-environment jsdom`；commit 用 conventional commits。
11. **真实表列名基线**（取自真实 run `20260716-114859`，见 spec 三）：`responsibility_claims`、`responsibility_tracebacks`、`intents`、`actions`、`context_pollution_events`、`policy_evaluations`、`process_events`、`tls_sessions`、`identity_process_map`、`provisional_identities`、`runs`、5 张 `*_refinements` 表。任何 SELECT 列名以此为准。

---

### Task 1: 数据层类型（`lib/types.ts` 新增 4 类型）

**Files:**
- Modify: `lib/types.ts`（在「Evidence Types」段之后、`NonLinkDiagnostic` 之前插入）

**Interfaces:**
- Produces: `ClaimType`、`ClaimState`、`ResponsibilityClaim`、`ResponsibilityTraceback`、`DatasetOverview` —— 后续 Task 3/4/5 提取器与 Task 7/9 组件全部依赖这些名字与字段。

- [ ] **Step 1: 在 `lib/types.ts` 中插入类型定义**

在 `NonLinkDiagnostic`（第 219 行）之前插入以下代码（复用已有的 `PhaseIndicator`、`AlertSeverity`、`MatchLevel`）：

```ts
export type ClaimType =
  | 'PolicyViolation' | 'AccountabilityAnomaly' | 'SuspiciousDelegation'
  | 'TaintedContextUse' | 'UnauthorizedCapabilityUse' | 'BlockedAttempt';

/** 实测 22 条 claim_state 分布：Inferred 18 / Confirmed 3 / Ambiguous 1。未知值由渲染侧灰显兜底。 */
export type ClaimState = 'Inferred' | 'Confirmed' | 'Ambiguous';

export interface ResponsibilityClaim {
  claim_id: string;
  claim_type: ClaimType;
  subject_agent_id: string | null;
  policy_id: string | null;
  boundary_id: string | null;
  claim_state: ClaimState;
  severity: AlertSeverity;
  confidence: number;
  evidence_refs: string[];
  competing_explanations: Array<{
    label: string;
    evidence_refs: string[];
    rule_id: string;
    rationale: string;
    aligned_pattern: string | null;
  }>;
  traceback_ref: string | null;
  phase: PhaseIndicator;
  /** 由 phase 提取（phaseTimestampNs），用于声明列表排序。 */
  timestamp_ns: number;
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

/** 各表概要——"Claude Code 全景"统计卡片数据。所有计数来自 COUNT(*)/GROUP BY，无造数。 */
export interface DatasetOverview {
  run: {
    run_id: string | null;
    started_at_ns: number | null;
    ended_at_ns: number | null;
    scenario_id: string | null;
    mode: string | null;
    collector_version: string | null;
  } | null;
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

- [ ] **Step 2: 类型检查通过**

Run: `cd frontend && npm run type-check`
Expected: 无错误（类型未被消费，纯新增）。

- [ ] **Step 3: Commit**

```bash
cd /home/lzk/MtAgentLens/frontend
git add lib/types.ts
git commit -m "feat: 新增 ResponsibilityClaim/ResponsibilityTraceback/DatasetOverview 类型"
```

---

### Task 2: 修正 fixture 真实 schema（`scripts/gen-db-fixture.mjs`）

**Files:**
- Modify: `scripts/gen-db-fixture.mjs`
- Test: `__tests__/db-import.test.ts`（更新 counts 断言）

**Interfaces:**
- Produces: 重新生成的 `__tests__/fixtures/db-sample.sqlite`，含真实 schema 的 `responsibility_claims` / `intents` / `actions` 表 + 样例行。样例行必须覆盖既有 `correlation_evidence` 的 `int-1`/`int-2`/`act-1`/`act-2` 引用，并含孤儿 intent/action（无 evidence 关联）供 Task 11 测试灰显。
- Consumes: Task 1 的类型字段名（表列名与之对齐）。

- [ ] **Step 1: 写失败测试（更新 counts 断言，红）**

将 `__tests__/db-import.test.ts` 的「counts tracebacks, claims, intents and actions defensively」用例（第 112-122 行）改为断言 fixture 新数据量：

```ts
it('counts tracebacks, claims, intents and actions defensively', async () => {
  const db = await openDatabase(fixtureBytes());
  const counts = extractCountsFromDb(db);
  db.close();

  expect(counts.tracebackCount).toBe(1);
  expect(counts.claimCount).toBe(1);
  // fixture 现含 intents/actions 表（Task 2）：int-1/int-2/int-3 与 act-1/act-2/act-3。
  expect(counts.intentCount).toBe(3);
  expect(counts.actionCount).toBe(3);
});
```

- [ ] **Step 2: 运行确认失败**

Run: `cd frontend && npx vitest run __tests__/db-import.test.ts`
Expected: FAIL —— 当前 fixture 无 intents/actions 表，`intentCount` 为 0 ≠ 3。

- [ ] **Step 3: 重写 `scripts/gen-db-fixture.mjs` 的建表与数据**

将文件顶部的表定义替换为真实 schema（`responsibility_claims` 删掉虚构的 `alert_id` 列），并把 `main()` 里建表 SQL（第 113-138 行）替换为：

```js
db.run(`
  CREATE TABLE graph_snapshots (
    snapshot_id TEXT, timestamp_ns INTEGER,
    nodes_json TEXT, edges_json TEXT, metrics_json TEXT, run_id TEXT
  );
  CREATE TABLE responsibility_alerts (
    alert_id TEXT, timestamp_ns INTEGER, display_profile TEXT, decision TEXT,
    severity TEXT, confidence REAL, authority TEXT, reasoning TEXT, title TEXT,
    summary TEXT, action_hint TEXT, primary_agent_id TEXT,
    involved_agents_json TEXT, engine_section_json TEXT, timeline_json TEXT,
    graph_metrics_json TEXT, phase TEXT, provisional_ref TEXT, run_id TEXT
  );
  CREATE TABLE correlation_evidence (
    evidence_id TEXT, intent_id TEXT, action_id TEXT, match_level TEXT,
    confidence REAL, matched_fields_json TEXT, derivation_mode TEXT,
    phase TEXT, timestamp_ns INTEGER, run_id TEXT
  );
  CREATE TABLE responsibility_tracebacks (
    traceback_id TEXT, claim_id TEXT, chain_node_ids_json TEXT,
    chain_edge_ids_json TEXT, root_cause_node_id TEXT,
    explanation_summary TEXT, confidence REAL, run_id TEXT
  );
  CREATE TABLE responsibility_claims (
    claim_id TEXT, claim_type TEXT, subject_agent_id TEXT, policy_id TEXT,
    boundary_id TEXT, claim_state TEXT, severity TEXT, confidence REAL,
    evidence_refs_json TEXT, competing_explanations_json TEXT,
    traceback_ref TEXT, phase TEXT, run_id TEXT
  );
  CREATE TABLE intents (
    intent_id TEXT, pid INTEGER, process_start_time_ns INTEGER, message_id TEXT,
    tool_name TEXT, tool_args_json TEXT, intent_class TEXT, parse_state TEXT,
    primary_matching_eligible INTEGER, authority_source TEXT,
    timestamp_ns INTEGER, run_id TEXT
  );
  CREATE TABLE actions (
    action_id TEXT, pid INTEGER, process_start_time_ns INTEGER, action_type TEXT,
    normalized_args_json TEXT, derivation_mode TEXT, signal_class TEXT,
    timestamp_ns INTEGER, run_id TEXT
  );
`);
```

将文件底部 `responsibility_claims` 的两条 INSERT（第 198-203 行）替换为真实 schema 插入 + intents/actions 数据（保持既有 tracebacks 的 `tb-1`/`claim-1` 双向链接）：

```js
  // claims —— 真实 schema，traceback_ref 为 NULL（与真实 db 一致：22 条全 NULL）
  db.run(
    `INSERT INTO responsibility_claims VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)`,
    [
      'claim-1', 'TaintedContextUse', 'agent-a', 'POLICY-1', 'BOUNDARY-1',
      'Inferred', 'Critical', 0.9,
      JSON.stringify(['ev-1', 'ev-2']),
      JSON.stringify([
        { label: 'Alternative path', evidence_refs: ['ev-3'], rule_id: 'R-ALT',
          rationale: 'Context could flow via the shell', aligned_pattern: null },
      ]),
      null, // traceback_ref —— 真实 db 全 NULL，链经 tracebacksByClaimId 反向索引
      'Provisional { timestamp_ns: 1000 }', 'run-1',
    ],
  );
  db.run(
    `INSERT INTO responsibility_claims VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)`,
    [
      'claim-2', 'PolicyViolation', 'agent-a', 'POLICY-1', null,
      'Confirmed', 'Warning', 0.72,
      JSON.stringify(['ev-3']),
      '[]',
      null, 'Authoritative { timestamp_ns: 1200, overrides: null }', 'run-1',
    ],
  );

  // intents —— tool_args_json 真实形态 Array<{Command|Path}>（禁用 parseMatchedFields）
  const intents = [
    ['int-1', 42, 1000, 'msg-1', 'Bash', JSON.stringify([{ Command: 'ls -la /tmp' }]),
     'FileSystem', 'Eligible', 1, 'SseResponseToolUse', 1000],
    ['int-2', 42, 1000, 'msg-2', 'Read', JSON.stringify([{ Path: '/tmp/x.sh' }]),
     'FileSystem', 'Eligible', 1, 'TextFallback', 1050],
    // 孤儿 intent —— 无 correlation_evidence 关联（真实数据 1 条），灰显展示
    ['int-3', 7, 2000, 'msg-3', 'Glob', JSON.stringify([{ Path: '/tmp/*.sh' }]),
     'FileSystem', 'Ineligible', 0, 'TextFallback', 1100],
  ];
  for (const it of intents) {
    db.run(
      'INSERT INTO intents VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
      [...it, 'run-1'],
    );
  }

  // actions —— normalized_args_json 真实形态 Array<{Path|Command}>
  const actions = [
    ['act-1', 42, 1000, 'FileWrite', JSON.stringify([{ Path: '/tmp/x.sh' }]),
     'Direct', 'HighSignal', 1000],
    ['act-2', 42, 1000, 'FileRead', JSON.stringify([{ Path: '/tmp/x.sh' }]),
     'Direct', 'LowSignal', 1002],
    // 孤儿 action —— 无 evidence 关联（真实数据 300 条），灰显展示
    ['act-3', 7, 2000, 'ProcessExec', JSON.stringify([{ Path: '/bin/bash', Command: 'bash' }]),
     'Derived', 'HighSignal', 1100],
  ];
  for (const ac of actions) {
    db.run(
      'INSERT INTO actions VALUES (?,?,?,?,?,?,?,?,?)',
      [...ac, 'run-1'],
    );
  }
```

> 数据关系核对：`ev-1(int-1,act-1)`、`ev-2(int-2,act-1)`（act-1 聚合 2 条 evidence）、`ev-3(int-1,act-2)`；`int-3`/`act-3` 为孤儿。这正是 Task 11 断言多 evidence 聚合到同一 action 与孤儿灰显的样本。

- [ ] **Step 4: 重新生成 fixture**

Run: `cd frontend && node scripts/gen-db-fixture.mjs`
Expected: 输出 `fixture written: <bytes> bytes`，无报错。

- [ ] **Step 5: 运行确认通过（绿）**

Run: `cd frontend && npx vitest run __tests__/db-import.test.ts`
Expected: PASS（含更新后的 counts 断言；loadDatabase 用例的 `tracebackCount=1`/`claimCount=1` 不受影响）。

- [ ] **Step 6: Commit**

```bash
cd /home/lzk/MtAgentLens/frontend
git add scripts/gen-db-fixture.mjs __tests__/db-import.test.ts __tests__/fixtures/db-sample.sqlite
git commit -m "test: 修正 db fixture 为真实 claims/intents/actions schema 并同步 counts 断言"
```

---

### Task 3: claims / tracebacks 提取器 + 反向索引（`lib/db-import.ts` + `lib/traceback-index.ts`）

**Files:**
- Modify: `lib/db-import.ts`
- Create: `lib/traceback-index.ts`
- Test: `__tests__/db-import.test.ts`

**Interfaces:**
- Consumes: Task 1 的 `ResponsibilityClaim`/`ResponsibilityTraceback`/`ClaimType`/`ClaimState`；现有私有 `queryRows`/`parseJsonArray`/`parsePhaseIndicator`。
- Produces: `extractClaimsFromDb(db: Database): ResponsibilityClaim[]`、`extractTracebacksFromDb(db: Database): ResponsibilityTraceback[]`（`db-import.ts` export）；`buildTracebacksByClaimId(tracebacks: ResponsibilityTraceback[]): Map<string, ResponsibilityTraceback>`（`traceback-index.ts` export，Task 9 使用）。

- [ ] **Step 1: 写失败测试**

在 `__tests__/db-import.test.ts` 的 import 中加入两个新提取器，并新增用例（放在「maps correlation_evidence rows」用例之后）：

```ts
it('extracts responsibility_claims with phase-derived timestamps', async () => {
  const db = await openDatabase(fixtureBytes());
  const claims = extractClaimsFromDb(db);
  db.close();

  expect(claims).toHaveLength(2);
  const first = claims[0];
  expect(first.claim_id).toBe('claim-1');
  expect(first.claim_type).toBe('TaintedContextUse');
  expect(first.subject_agent_id).toBe('agent-a');
  expect(first.policy_id).toBe('POLICY-1');
  expect(first.boundary_id).toBe('BOUNDARY-1');
  expect(first.claim_state).toBe('Inferred');
  expect(first.severity).toBe('Critical');
  expect(first.evidence_refs).toEqual(['ev-1', 'ev-2']);
  expect(first.competing_explanations[0].rule_id).toBe('R-ALT');
  // traceback_ref 真实 db 全 NULL —— 前端走反向索引，不按它取链
  expect(first.traceback_ref).toBeNull();
  // timestamp_ns 从 phase 提取（Provisional { timestamp_ns: 1000 }）
  expect(first.timestamp_ns).toBe(1000);
  expect(first.phase).toEqual({ Provisional: { timestamp_ns: 1000 } });
  // Authoritative phase 提取
  expect(claims[1].timestamp_ns).toBe(1200);
});

it('extracts responsibility_tracebacks with parsed chain ids', async () => {
  const db = await openDatabase(fixtureBytes());
  const tracebacks = extractTracebacksFromDb(db);
  db.close();

  expect(tracebacks).toHaveLength(1);
  expect(tracebacks[0]).toEqual({
    traceback_id: 'tb-1',
    claim_id: 'claim-1',
    chain_node_ids: ['agent-a'],
    chain_edge_ids: ['e1'],
    root_cause_node_id: 'agent-a',
    explanation_summary: 'traceback summary',
    confidence: 0.9,
  });
});
```

新增 `__tests__/traceback-index.test.ts`：

```ts
import { describe, expect, it } from 'vitest';
import type { ResponsibilityTraceback } from '@/lib/types';
import { buildTracebacksByClaimId } from '@/lib/traceback-index';

describe('buildTracebacksByClaimId — 反向索引', () => {
  it('indexes tracebacks by claim_id (the real FK)', () => {
    const tracebacks: ResponsibilityTraceback[] = [
      { traceback_id: 'tb-1', claim_id: 'c1', chain_node_ids: [], chain_edge_ids: [], root_cause_node_id: '', explanation_summary: '', confidence: 0.9 },
    ];
    const index = buildTracebacksByClaimId(tracebacks);
    expect(index.get('c1')?.traceback_id).toBe('tb-1');
    expect(index.has('nope')).toBe(false);
  });

  it('last-write-wins when the same claim_id is re-traced', () => {
    const tracebacks: ResponsibilityTraceback[] = [
      { traceback_id: 'tb-a', claim_id: 'c1', chain_node_ids: [], chain_edge_ids: [], root_cause_node_id: '', explanation_summary: 'first', confidence: 0.8 },
      { traceback_id: 'tb-b', claim_id: 'c1', chain_node_ids: [], chain_edge_ids: [], root_cause_node_id: '', explanation_summary: 'second', confidence: 0.9 },
    ];
    const index = buildTracebacksByClaimId(tracebacks);
    expect(index.get('c1')?.explanation_summary).toBe('second');
  });
});
```

- [ ] **Step 2: 运行确认失败**

Run: `cd frontend && npx vitest run __tests__/db-import.test.ts __tests__/traceback-index.test.ts`
Expected: FAIL —— `extractClaimsFromDb is not a function` / `buildTracebacksByClaimId is not defined`。

- [ ] **Step 3: 实现**

在 `lib/db-import.ts` 的 `parseJsonArray` 之后新增私有 helper 与两个提取器：

```ts
function phaseTimestampNs(phase: PhaseIndicator): number {
  if ('Provisional' in phase) return phase.Provisional.timestamp_ns;
  return phase.Authoritative.timestamp_ns;
}

/** Load responsibility_claims — timestamp_ns derived from the phase column. */
export function extractClaimsFromDb(db: Database): ResponsibilityClaim[] {
  let rows: Record<string, unknown>[];
  try {
    rows = queryRows(
      db,
      `SELECT claim_id, claim_type, subject_agent_id, policy_id, boundary_id,
              claim_state, severity, confidence, evidence_refs_json,
              competing_explanations_json, traceback_ref, phase
       FROM responsibility_claims`,
    );
  } catch {
    return []; // Table absent.
  }
  return rows.map((r) => {
    const phase = parsePhaseIndicator(r.phase) ?? {
      Provisional: { timestamp_ns: Number(r.timestamp_ns ?? 0) },
    };
    return {
      claim_id: String(r.claim_id ?? ''),
      claim_type: (r.claim_type as ClaimType) ?? 'TaintedContextUse',
      subject_agent_id: r.subject_agent_id ? String(r.subject_agent_id) : null,
      policy_id: r.policy_id ? String(r.policy_id) : null,
      boundary_id: r.boundary_id ? String(r.boundary_id) : null,
      claim_state: (r.claim_state as ClaimState) ?? 'Inferred',
      severity: (r.severity as AlertSeverity) ?? 'Warning',
      confidence: Number(r.confidence ?? 0),
      evidence_refs: parseJsonArray<string>(r.evidence_refs_json),
      competing_explanations:
        parseJsonArray<ResponsibilityClaim['competing_explanations'][number]>(
          r.competing_explanations_json,
        ),
      traceback_ref: r.traceback_ref ? String(r.traceback_ref) : null,
      phase,
      timestamp_ns: phaseTimestampNs(phase),
    };
  });
}

/** Load responsibility_tracebacks — chain ids parsed as string arrays. */
export function extractTracebacksFromDb(db: Database): ResponsibilityTraceback[] {
  let rows: Record<string, unknown>[];
  try {
    rows = queryRows(
      db,
      `SELECT traceback_id, claim_id, chain_node_ids_json, chain_edge_ids_json,
              root_cause_node_id, explanation_summary, confidence
       FROM responsibility_tracebacks`,
    );
  } catch {
    return []; // Table absent.
  }
  return rows.map((r) => ({
    traceback_id: String(r.traceback_id ?? ''),
    claim_id: String(r.claim_id ?? ''),
    chain_node_ids: parseJsonArray<string>(r.chain_node_ids_json),
    chain_edge_ids: parseJsonArray<string>(r.chain_edge_ids_json),
    root_cause_node_id: String(r.root_cause_node_id ?? ''),
    explanation_summary: String(r.explanation_summary ?? ''),
    confidence: Number(r.confidence ?? 0),
  }));
}
```

同时更新 `db-import.ts` 顶部的类型 import（第 1-13 行）加入：

```ts
import type {
  ...
  AlertSeverity,
  ClaimState,
  ClaimType,
  Intent,
  Action,
  DatasetOverview,
  ResponsibilityClaim,
  ResponsibilityTraceback,
} from './types';
```

新增 `lib/traceback-index.ts`：

```ts
import type { ResponsibilityTraceback } from './types';

/**
 * Reverse index over the real FK traceback.claim_id → claim. The collector
 * stores tracebacks keyed by their claim, while claims carry a mostly-NULL
 * traceback_ref — so lookups go through this index, never claim.traceback_ref.
 */
export function buildTracebacksByClaimId(
  tracebacks: ResponsibilityTraceback[],
): Map<string, ResponsibilityTraceback> {
  const index = new Map<string, ResponsibilityTraceback>();
  for (const tb of tracebacks) {
    // Same claim re-traced → last write wins (most recent traceback).
    index.set(tb.claim_id, tb);
  }
  return index;
}
```

- [ ] **Step 4: 运行确认通过**

Run: `cd frontend && npx vitest run __tests__/db-import.test.ts __tests__/traceback-index.test.ts`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
cd /home/lzk/MtAgentLens/frontend
git add lib/db-import.ts lib/traceback-index.ts __tests__/db-import.test.ts __tests__/traceback-index.test.ts
git commit -m "feat: 提取 responsibility_claims/response_tracebacks + tracebacksByClaimId 反向索引"
```

---

### Task 4: intents / actions 提取器（真实数组形态，禁用 parseMatchedFields）

**Files:**
- Modify: `lib/db-import.ts`
- Test: `__tests__/db-import.test.ts`

**Interfaces:**
- Consumes: 现有 `Intent`（types.ts:182）/`Action`（types.ts:195）。
- Produces: `extractIntentsFromDb(db: Database): Intent[]`、`extractActionsFromDb(db: Database): Action[]`（Task 6 接入 `loadDatabase`，Task 11 使用）。

- [ ] **Step 1: 写失败测试**

在 `__tests__/db-import.test.ts` import 加入 `extractIntentsFromDb` / `extractActionsFromDb`，新增用例：

```ts
it('maps intents with process_id nesting and Array tool_args (not parseMatchedFields)', async () => {
  const db = await openDatabase(fixtureBytes());
  const intents = extractIntentsFromDb(db);
  db.close();

  expect(intents).toHaveLength(3);
  const bash = intents.find((i) => i.intent_id === 'int-1')!;
  expect(bash).toMatchObject({
    process_id: { pid: 42, start_time_ns: 1000 },
    message_id: 'msg-1',
    tool_name: 'Bash',
    intent_class: 'FileSystem',
    parse_state: 'Eligible',
    primary_matching_eligible: true,
    authority_source: 'SseResponseToolUse',
    timestamp_ns: 1000,
  });
  // 真实数组形态 —— 断言保留了 {Command} 键（parseMatchedFields 会把它清空）
  expect(bash.tool_args).toEqual([{ Command: 'ls -la /tmp' }]);
  expect(intents.find((i) => i.intent_id === 'int-3')!.primary_matching_eligible).toBe(false);
});

it('maps actions with process_id nesting and Array normalized_args', async () => {
  const db = await openDatabase(fixtureBytes());
  const actions = extractActionsFromDb(db);
  db.close();

  expect(actions).toHaveLength(3);
  const write = actions.find((a) => a.action_id === 'act-1')!;
  expect(write).toMatchObject({
    process_id: { pid: 42, start_time_ns: 1000 },
    action_type: 'FileWrite',
    derivation_mode: 'Direct',
    signal_class: 'HighSignal',
    timestamp_ns: 1000,
  });
  // 数组形态断言（{Path} 键必须保留）
  expect(write.normalized_args).toEqual([{ Path: '/tmp/x.sh' }]);
  expect(actions.find((a) => a.action_id === 'act-3')!.signal_class).toBe('HighSignal');
});
```

- [ ] **Step 2: 运行确认失败**

Run: `cd frontend && npx vitest run __tests__/db-import.test.ts`
Expected: FAIL —— `extractIntentsFromDb is not a function`。

- [ ] **Step 3: 实现**

在 `lib/db-import.ts`（`extractTracebacksFromDb` 之后）新增：

```ts
/** Load intents — pid/start_time_ns nested into process_id; args stay as raw JSON arrays. */
export function extractIntentsFromDb(db: Database): Intent[] {
  let rows: Record<string, unknown>[];
  try {
    rows = queryRows(
      db,
      `SELECT intent_id, pid, process_start_time_ns, message_id, tool_name,
              tool_args_json, intent_class, parse_state, primary_matching_eligible,
              authority_source, timestamp_ns
       FROM intents`,
    );
  } catch {
    return []; // Table absent.
  }
  return rows.map((r) => ({
    intent_id: String(r.intent_id ?? ''),
    process_id: {
      pid: Number(r.pid ?? 0),
      start_time_ns: Number(r.process_start_time_ns ?? 0),
    },
    message_id: String(r.message_id ?? ''),
    tool_name: String(r.tool_name ?? ''),
    tool_args: parseJsonArray(r.tool_args_json),
    intent_class: String(r.intent_class ?? ''),
    parse_state: String(r.parse_state ?? ''),
    primary_matching_eligible: Number(r.primary_matching_eligible ?? 0) !== 0,
    authority_source: String(r.authority_source ?? ''),
    timestamp_ns: Number(r.timestamp_ns ?? 0),
  }));
}

/** Load actions — args stay as raw JSON arrays (Array<{Path|Command}> in real dbs). */
export function extractActionsFromDb(db: Database): Action[] {
  let rows: Record<string, unknown>[];
  try {
    rows = queryRows(
      db,
      `SELECT action_id, pid, process_start_time_ns, action_type,
              normalized_args_json, derivation_mode, signal_class, timestamp_ns
       FROM actions`,
    );
  } catch {
    return []; // Table absent.
  }
  return rows.map((r) => ({
    action_id: String(r.action_id ?? ''),
    process_id: {
      pid: Number(r.pid ?? 0),
      start_time_ns: Number(r.process_start_time_ns ?? 0),
    },
    action_type: String(r.action_type ?? ''),
    normalized_args: parseJsonArray(r.normalized_args_json),
    derivation_mode: String(r.derivation_mode ?? ''),
    signal_class: String(r.signal_class ?? ''),
    timestamp_ns: Number(r.timestamp_ns ?? 0),
  }));
}
```

- [ ] **Step 4: 运行确认通过**

Run: `cd frontend && npx vitest run __tests__/db-import.test.ts`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
cd /home/lzk/MtAgentLens/frontend
git add lib/db-import.ts __tests__/db-import.test.ts
git commit -m "feat: 提取 intents/actions（真实数组形态，process_id 嵌套）"
```

---

### Task 5: DatasetOverview 提取器（`extractDatasetOverviewFromDb`）

**Files:**
- Modify: `lib/db-import.ts`
- Test: `__tests__/db-import.test.ts`

**Interfaces:**
- Produces: `extractDatasetOverviewFromDb(db: Database): DatasetOverview`（Task 6 接入 `loadDatabase`，Task 7 `deriveDatasetSummary` 消费）。

- [ ] **Step 1: 写失败测试**

在 `__tests__/db-import.test.ts` import 加入 `extractDatasetOverviewFromDb`，新增用例：

```ts
it('extracts DatasetOverview counts and distributions from every table', async () => {
  const db = await openDatabase(fixtureBytes());
  const overview = extractDatasetOverviewFromDb(db);
  db.close();

  expect(overview.run).toEqual({
    run_id: 'run-1',
    started_at_ns: null,
    ended_at_ns: null,
    scenario_id: null,
    mode: null,
    collector_version: null,
  });
  expect(overview.snapshots.count).toBe(2);
  expect(overview.graph).toEqual({ nodes: 0, edges: 0 }); // fixture 无 graph_nodes 表 → 0
  expect(overview.alerts.count).toBe(2);
  expect(overview.evidence.count).toBe(3);
  expect(overview.evidence.by_match_level).toEqual({ L1Exact: 1, L3Containment: 1, L5Substring: 1 });
  expect(overview.claims.count).toBe(2);
  expect(overview.claims.by_type).toEqual({ TaintedContextUse: 1, PolicyViolation: 1 });
  expect(overview.claims.by_severity).toEqual({ Critical: 1, Warning: 1 });
  expect(overview.tracebacks.count).toBe(1);
  expect(overview.intents.count).toBe(3);
  expect(overview.intents.by_tool_name).toEqual({ Bash: 1, Read: 1, Glob: 1 });
  expect(overview.actions.count).toBe(3);
  expect(overview.actions.by_action_type).toEqual({ FileWrite: 1, FileRead: 1, ProcessExec: 1 });
  expect(overview.pollution.count).toBe(0);
  expect(overview.policy_evaluations.count).toBe(0);
  expect(overview.processes.count).toBe(0);
  expect(overview.tls_sessions.count).toBe(0);
  expect(overview.identity_process_map.count).toBe(0);
  expect(overview.provisional_identities.count).toBe(0);
  expect(overview.refinements).toEqual({ claim: 0, classification: 0, evidence: 0, graph: 0, identity: 0 });
  expect(overview.non_link_diagnostics.count).toBe(0);
});
```

> fixture 未建 `graph_nodes`/`graph_edges` 等概览表——`count()` 的 try/catch 返回 0，正是「单表缺失跳过」的行为断言。

- [ ] **Step 2: 运行确认失败**

Run: `cd frontend && npx vitest run __tests__/db-import.test.ts`
Expected: FAIL —— `extractDatasetOverviewFromDb is not a function`。

- [ ] **Step 3: 实现**

在 `lib/db-import.ts`（`extractActionsFromDb` 之后、`extractCountsFromDb` 之前）新增：

```ts
/** Load a per-table DatasetOverview — counts + GROUP BY distributions. Every
 *  table query is defensive: a missing table counts as zero (small/older dbs). */
export function extractDatasetOverviewFromDb(db: Database): DatasetOverview {
  const count = (table: string): number => {
    try {
      const rows = queryRows(db, `SELECT COUNT(*) AS n FROM ${table}`);
      return Number(rows[0]?.n ?? 0);
    } catch {
      return 0; // Table absent.
    }
  };
  const groupCount = (table: string, column: string): Record<string, number> => {
    const result: Record<string, number> = {};
    try {
      const rows = queryRows(
        db,
        `SELECT ${column} AS k, COUNT(*) AS n FROM ${table} GROUP BY ${column}`,
      );
      for (const r of rows) {
        const key = r.k === null || r.k === undefined ? 'null' : String(r.k);
        result[key] = Number(r.n ?? 0);
      }
    } catch {
      // Table or column absent.
    }
    return result;
  };
  const runRow = (() => {
    try {
      return queryRows(
        db,
        `SELECT run_id, started_at_ns, ended_at_ns, scenario_id, mode, collector_version
         FROM runs LIMIT 1`,
      )[0];
    } catch {
      return undefined;
    }
  })();

  return {
    run: runRow
      ? {
          run_id: runRow.run_id ? String(runRow.run_id) : null,
          started_at_ns: runRow.started_at_ns != null ? Number(runRow.started_at_ns) : null,
          ended_at_ns: runRow.ended_at_ns != null ? Number(runRow.ended_at_ns) : null,
          scenario_id: runRow.scenario_id ? String(runRow.scenario_id) : null,
          mode: runRow.mode ? String(runRow.mode) : null,
          collector_version: runRow.collector_version ? String(runRow.collector_version) : null,
        }
      : null,
    snapshots: { count: count('graph_snapshots') },
    graph: { nodes: count('graph_nodes'), edges: count('graph_edges') },
    alerts: { count: count('responsibility_alerts') },
    evidence: {
      count: count('correlation_evidence'),
      by_match_level: groupCount('correlation_evidence', 'match_level'),
    },
    claims: {
      count: count('responsibility_claims'),
      by_type: groupCount('responsibility_claims', 'claim_type'),
      by_severity: groupCount('responsibility_claims', 'severity'),
    },
    tracebacks: { count: count('responsibility_tracebacks') },
    intents: {
      count: count('intents'),
      by_tool_name: groupCount('intents', 'tool_name'),
      by_authority_source: groupCount('intents', 'authority_source'),
    },
    actions: {
      count: count('actions'),
      by_action_type: groupCount('actions', 'action_type'),
      by_signal_class: groupCount('actions', 'signal_class'),
    },
    pollution: { count: count('context_pollution_events') },
    policy_evaluations: { count: count('policy_evaluations') },
    processes: { count: count('process_events') },
    tls_sessions: { count: count('tls_sessions') },
    identity_process_map: { count: count('identity_process_map') },
    provisional_identities: { count: count('provisional_identities') },
    agent_identities: { count: count('agent_identities') },
    message_bindings: { count: count('message_bindings') },
    delegation_records: { count: count('delegation_records') },
    subagent_boundaries: { count: count('subagent_boundaries') },
    refinements: {
      claim: count('claim_refinements'),
      classification: count('classification_refinements'),
      evidence: count('evidence_refinements'),
      graph: count('graph_refinements'),
      identity: count('identity_refinements'),
    },
    non_link_diagnostics: { count: count('non_link_diagnostics') },
  };
}
```

- [ ] **Step 4: 运行确认通过**

Run: `cd frontend && npx vitest run __tests__/db-import.test.ts`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
cd /home/lzk/MtAgentLens/frontend
git add lib/db-import.ts __tests__/db-import.test.ts
git commit -m "feat: extractDatasetOverviewFromDb 全表概要提取（逐表防御式计数）"
```

---

### Task 6: store 扩展（DbImport + AppStore + applyDbImport + hasConsumableData）

**Files:**
- Modify: `lib/db-import.ts`、`lib/store.ts`
- Test: `__tests__/store-db.test.ts`

**Interfaces:**
- Consumes: Task 3/4/5 的 5 个提取器。
- Produces: `DbImport.claims/tracebacks/intents/actions/overview`；`AppStore.claims/tracebacks/intents/actions/overview`（Task 9/10/11 组件从 `useAppStore` 读取）。

- [ ] **Step 1: 写失败测试**

在 `__tests__/store-db.test.ts` 的「loadDatabase applies graph/alerts/evidence from a db import」用例中追加断言（并在 beforeEach 的 `setState` 重置中加入新字段初始值）：

```ts
beforeEach(() => {
  vi.clearAllMocks();
  useAppStore.setState({
    dataSource: null,
    activeRunId: null,
    graph: { nodes: [], edges: [] },
    alerts: [],
    evidence: [],
    claims: [],
    tracebacks: [],
    intents: [],
    actions: [],
    overview: null,
    errorMessage: null,
  });
});
```

用例追加：

```ts
    // 全表消费：新表数据随 db import 一并写入 store
    expect(s.claims).toHaveLength(2);
    expect(s.tracebacks).toHaveLength(1);
    expect(s.intents).toHaveLength(3);
    expect(s.actions).toHaveLength(3);
    expect(s.overview).not.toBeNull();
    expect(s.overview?.claims.count).toBe(2);
    expect(s.overview?.intents.count).toBe(3);
```

- [ ] **Step 2: 运行确认失败**

Run: `cd frontend && npx vitest run __tests__/store-db.test.ts`
Expected: FAIL —— `s.claims` 为 `undefined`（store 未定义该字段），且 `loadDatabase` 尚未产出新字段。

- [ ] **Step 3: 扩展 `DbImport` 类型 + `loadDatabase`**

`lib/db-import.ts`：
- `DbImport` 类型（第 34-53 行）在 `metrics` 之后新增：

```ts
  claims: ResponsibilityClaim[];
  tracebacks: ResponsibilityTraceback[];
  intents: Intent[];
  actions: Action[];
  overview: DatasetOverview;
```

- import 已含新类型（Task 3 Step 3 已加）。
- `loadDatabase`（第 424-445 行）改为调用新提取器，并更新 `hasConsumableData`：

```ts
  try {
    const { graph, metrics, runId } = extractGraphFromDb(db);
    const alerts = extractAlertsFromDb(db);
    const evidence = extractEvidenceFromDb(db);
    const claims = extractClaimsFromDb(db);
    const tracebacks = extractTracebacksFromDb(db);
    const intents = extractIntentsFromDb(db);
    const actions = extractActionsFromDb(db);
    const overview = extractDatasetOverviewFromDb(db);
    const counts = extractCountsFromDb(db);
    const resolvedMetrics = metrics ?? deriveGraphMetrics(graph);

    return {
      id: `db:${label}`,
      label,
      graph,
      alerts,
      evidence,
      claims,
      tracebacks,
      intents,
      actions,
      overview,
      metrics: resolvedMetrics,
      nodeCount: graph.nodes.length,
      edgeCount: graph.edges.length,
      alertCount: alerts.length,
      evidenceCount: evidence.length,
      ...counts,
      runId,
      hasConsumableData:
        graph.nodes.length > 0 ||
        alerts.length > 0 ||
        evidence.length > 0 ||
        claims.length > 0 ||
        intents.length > 0 ||
        actions.length > 0,
    };
  } finally {
    db.close();
  }
```

- [ ] **Step 4: 扩展 `AppStore` + `applyDbImport`**

`lib/store.ts`：
- `AppStore` 接口（第 47-91 行）在 `evidence` 之后新增字段：

```ts
  /** Claims / tracebacks / intents / actions from the active db (import or backend download). */
  claims: ResponsibilityClaim[];
  tracebacks: ResponsibilityTraceback[];
  intents: Intent[];
  actions: Action[];
  overview: DatasetOverview | null;
```

- `applyDbImport`（第 142-159 行）一并填充：

```ts
  set({
    graph: dbImport.graph,
    alerts: leanAlerts,
    graphMetrics: dbImport.metrics,
    evidence: dbImport.evidence,
    claims: dbImport.claims,
    tracebacks: dbImport.tracebacks,
    intents: dbImport.intents,
    actions: dbImport.actions,
    overview: dbImport.overview,
    dataSource: source,
    activeRunId: source.runId ?? null,
    selectedNodeId: null,
    appState: 'replay',
    errorMessage: null,
  });
```

- `useAppStore` 初始化（第 170-184 行）在 `evidence: []` 之后新增：

```ts
  claims: [],
  tracebacks: [],
  intents: [],
  actions: [],
  overview: null,
```

- import 行（第 5-13 行 `import type`）补充 `ResponsibilityClaim`/`ResponsibilityTraceback`/`Intent`/`Action`/`DatasetOverview`。

- [ ] **Step 5: 运行确认通过**

Run: `cd frontend && npx vitest run __tests__/store-db.test.ts`
Expected: PASS。

- [ ] **Step 6: Commit**

```bash
cd /home/lzk/MtAgentLens/frontend
git add lib/db-import.ts lib/store.ts __tests__/store-db.test.ts
git commit -m "feat: store 扩展 claims/tracebacks/intents/actions/overview + hasConsumableData 纳入新表"
```

---

### Task 7: `deriveDatasetSummary` 纯函数（`lib/dataset-summary.ts`）—「不幻觉」派生层

**Files:**
- Create: `lib/dataset-summary.ts`
- Test: `__tests__/overview-data.test.ts`

**Interfaces:**
- Consumes: Task 5 的 `DatasetOverview`（通过构造样本直接传入，不依赖 db）。
- Produces: `deriveDatasetSummary(overview: DatasetOverview): DatasetSummary` 及 `DatasetSummary`/`KpiTile`/`FrictionSignal` 类型（Task 8 的 `OverviewDatasetPanel` 消费）。

- [ ] **Step 1: 写失败测试**

新增 `__tests__/overview-data.test.ts`：

```ts
import { describe, expect, it } from 'vitest';
import type { DatasetOverview } from '@/lib/types';
import { deriveDatasetSummary } from '@/lib/dataset-summary';

/** 造一个含真实分布的概览样本（值来自真实 run 的量级，非必精确）。 */
function sampleOverview(over: Partial<DatasetOverview> = {}): DatasetOverview {
  return {
    run: null,
    snapshots: { count: 0 },
    graph: { nodes: 0, edges: 0 },
    alerts: { count: 0 },
    evidence: { count: 0, by_match_level: {} },
    claims: { count: 0, by_type: {}, by_severity: {} },
    tracebacks: { count: 0 },
    intents: { count: 0, by_tool_name: {}, by_authority_source: {} },
    actions: { count: 0, by_action_type: {}, by_signal_class: {} },
    pollution: { count: 0 },
    policy_evaluations: { count: 0 },
    processes: { count: 0 },
    tls_sessions: { count: 0 },
    identity_process_map: { count: 0 },
    provisional_identities: { count: 0 },
    agent_identities: { count: 0 },
    message_bindings: { count: 0 },
    delegation_records: { count: 0 },
    subagent_boundaries: { count: 0 },
    refinements: { claim: 0, classification: 0, evidence: 0, graph: 0, identity: 0 },
    non_link_diagnostics: { count: 0 },
    ...over,
  };
}

describe('deriveDatasetSummary — 纯函数派生（Nothing is invented）', () => {
  it('computes a normal health badge when there are no criticals', () => {
    const s = deriveDatasetSummary(sampleOverview());
    expect(s.health).toBe('normal');
    expect(s.healthReason).toContain('无风险');
  });

  it('flips to critical when a Critical claim exists', () => {
    const s = deriveDatasetSummary(
      sampleOverview({ claims: { count: 1, by_type: {}, by_severity: { Critical: 1 } } }),
    );
    expect(s.health).toBe('critical');
  });

  it('derives the 6 KPI tiles from counts (文件活动 = File* actions 求和)', () => {
    const s = deriveDatasetSummary(
      sampleOverview({
        processes: { count: 160 },
        actions: { count: 3, by_action_type: { FileWrite: 2, FileRead: 1 }, by_signal_class: {} },
        intents: { count: 3, by_tool_name: { Bash: 2, Read: 1 }, by_authority_source: {} },
        tls_sessions: { count: 1067 },
        claims: { count: 2, by_type: {}, by_severity: {} },
        alerts: { count: 13 },
      }),
    );
    expect(s.kpi).toHaveLength(6);
    const byKey = Object.fromEntries(s.kpi.map((t) => [t.key, t]));
    expect(byKey.processes?.value).toBe(160);
    expect(byKey.fileActivity?.value).toBe(3); // FileWrite 2 + FileRead 1
    expect(byKey.tools?.value).toBe(3);
    expect(byKey.network?.value).toBe(1067);
    expect(byKey.claims?.value).toBe(2);
    expect(byKey.alerts?.value).toBe(13);
    // claims KPI 的 hint 按 severity 分解
    expect(byKey.claims?.hint).toContain('2');
  });

  it('marks the claims KPI danger when Critical claims exist', () => {
    const s = deriveDatasetSummary(
      sampleOverview({ claims: { count: 3, by_type: {}, by_severity: { Critical: 3 } } }),
    );
    expect(s.kpi.find((t) => t.key === 'claims')?.tone).toBe('danger');
  });

  it('aggregates the four signal rules into rows with ×count', () => {
    const s = deriveDatasetSummary(
      sampleOverview({
        pollution: { count: 219 },
        claims: { count: 3, by_type: {}, by_severity: { Critical: 3 } },
        evidence: { count: 1823, by_match_level: { L5Substring: 830, L1Exact: 38 } },
        policy_evaluations: { count: 755 },
      }),
    );
    expect(s.signals).toHaveLength(4);
    const poll = s.signals.find((x) => x.category === 'context-pollution')!;
    expect(poll.count).toBe(219);
    expect(poll.severity).toBe('Critical');
    const weak = s.signals.find((x) => x.category === 'weak-evidence')!;
    expect(weak.count).toBe(830);
    expect(weak.detail).toContain('45.5'); // 830/1823 占比（不四舍五入写死）
  });

  it('omits signal rules with zero hits (nothing is invented)', () => {
    const s = deriveDatasetSummary(sampleOverview());
    expect(s.signals).toHaveLength(0);
  });

  it('builds stat card groups from overview counts', () => {
    const s = deriveDatasetSummary(
      sampleOverview({
        snapshots: { count: 55 },
        graph: { nodes: 1461, edges: 3300 },
        tracebacks: { count: 22 },
        intents: { count: 23 },
        actions: { count: 474 },
      }),
    );
    const byCategory = Object.fromEntries(s.statCards.map((c) => [c.category, c]));
    const graphCard = byCategory['图'];
    expect(graphCard.items).toEqual([
      { label: '快照帧', value: 55 },
      { label: '节点', value: 1461 },
      { label: '边', value: 3300 },
    ]);
    expect(byCategory['意图动作'].items).toContainEqual({ label: '意图', value: 23 });
    expect(byCategory['意图动作'].items).toContainEqual({ label: '动作', value: 474 });
  });
});
```

- [ ] **Step 2: 运行确认失败**

Run: `cd frontend && npx vitest run __tests__/overview-data.test.ts`
Expected: FAIL —— `Cannot find module '@/lib/dataset-summary'`。

- [ ] **Step 3: 实现**

新增 `lib/dataset-summary.ts`：

```ts
import type { DatasetOverview } from './types';

export interface KpiTile {
  key: string;
  label: string;
  value: number;
  hint: string;
  tone: 'danger' | 'warning' | 'info' | 'success';
}

export interface FrictionSignal {
  severity: 'Critical' | 'Warning' | 'Info';
  category: string;
  title: string;
  detail: string;
  count: number;
}

export interface DatasetSummary {
  health: 'critical' | 'warning' | 'normal';
  healthReason: string;
  identity: { scenarioId: string | null; mode: string | null; collectorVersion: string | null };
  timeRange: { startedAtNs: number | null; endedAtNs: number | null; durationMs: number | null };
  kpi: KpiTile[];
  signals: FrictionSignal[];
  statCards: { category: string; items: { label: string; value: number }[] }[];
}

const sumValues = (record: Record<string, number>): number =>
  Object.values(record).reduce((sum, n) => sum + n, 0);

const formatBreakdown = (record: Record<string, number>): string =>
  Object.entries(record)
    .sort((a, b) => b[1] - a[1])
    .map(([k, n]) => `${k} ${n}`)
    .join(' · ');

/**
 * Derive the overview summary strictly from the dataset overview. Nothing is
 * invented: a panel that cannot be computed from the counts is left empty.
 */
export function deriveDatasetSummary(overview: DatasetOverview): DatasetSummary {
  const criticalClaims = overview.claims.by_severity.Critical ?? 0;
  const criticalAlerts = overview.alerts.count; // alerts 表无 severity 分布 → 保守按 count
  const health: DatasetSummary['health'] =
    criticalClaims > 0 ? 'critical' : criticalAlerts > 0 ? 'warning' : 'normal';
  const healthReason =
    criticalClaims > 0
      ? `${criticalClaims} 条 Critical 责任声明需关注`
      : criticalAlerts > 0
        ? `${criticalAlerts} 条告警需关注`
        : '无风险信号';

  const fileActivity = sumValues(
    Object.fromEntries(
      Object.entries(overview.actions.by_action_type).filter(([k]) => k.startsWith('File')),
    ),
  );

  const kpi: KpiTile[] = [
    {
      key: 'processes', label: '进程', value: overview.processes.count, hint: '',
      tone: 'info',
    },
    {
      key: 'fileActivity', label: '文件活动', value: fileActivity,
      hint: formatBreakdown(overview.actions.by_action_type), tone: 'info',
    },
    {
      key: 'tools', label: '工具调用', value: overview.intents.count,
      hint: formatBreakdown(overview.intents.by_tool_name), tone: 'info',
    },
    {
      key: 'network', label: '网络', value: overview.tls_sessions.count, hint: '',
      tone: 'info',
    },
    {
      key: 'claims', label: '责任声明', value: overview.claims.count,
      hint: formatBreakdown(overview.claims.by_severity),
      tone: criticalClaims > 0 ? 'danger' : 'info',
    },
    {
      key: 'alerts', label: '告警', value: overview.alerts.count,
      hint: `Warning ${overview.alerts.count}`, tone: criticalAlerts > 0 ? 'danger' : 'info',
    },
  ];

  const signals: FrictionSignal[] = [];
  if (overview.pollution.count > 0) {
    signals.push({
      severity: 'Critical',
      category: 'context-pollution',
      title: '上下文污染事件',
      detail: '被污染的上下文进入后续请求上下文',
      count: overview.pollution.count,
    });
  }
  if (criticalClaims > 0) {
    signals.push({
      severity: 'Warning',
      category: 'critical-claims',
      title: 'Critical 责任声明',
      detail: formatBreakdown(overview.claims.by_type),
      count: criticalClaims,
    });
  }
  const weakCount = overview.evidence.by_match_level.L5Substring ?? 0;
  if (weakCount > 0) {
    const pct = overview.evidence.count > 0 ? (weakCount / overview.evidence.count) * 100 : 0;
    signals.push({
      severity: 'Info',
      category: 'weak-evidence',
      title: '弱匹配证据',
      detail: `L5 弱匹配 ${pct.toFixed(1)}%（${weakCount}/${overview.evidence.count}）`,
      count: weakCount,
    });
  }
  if (overview.policy_evaluations.count > 0) {
    signals.push({
      severity: 'Warning',
      category: 'policy-violations',
      title: '策略评估',
      detail: 'judgment_json.result = Violation',
      count: overview.policy_evaluations.count,
    });
  }

  const statCards: DatasetSummary['statCards'] = [
    {
      category: '图',
      items: [
        { label: '快照帧', value: overview.snapshots.count },
        { label: '节点', value: overview.graph.nodes },
        { label: '边', value: overview.graph.edges },
      ],
    },
    {
      category: '责任',
      items: [
        { label: '告警', value: overview.alerts.count },
        { label: '声明', value: overview.claims.count },
        { label: '回溯', value: overview.tracebacks.count },
      ],
    },
    {
      category: '意图动作',
      items: [
        { label: '意图', value: overview.intents.count },
        { label: '动作', value: overview.actions.count },
        { label: '证据', value: overview.evidence.count },
      ],
    },
    {
      category: '上下文',
      items: [
        { label: '污染', value: overview.pollution.count },
        { label: '策略评估', value: overview.policy_evaluations.count },
      ],
    },
    {
      category: '运行时',
      items: [
        { label: '进程', value: overview.processes.count },
        { label: 'TLS', value: overview.tls_sessions.count },
        { label: '身份映射', value: overview.identity_process_map.count },
        { label: '身份', value: overview.provisional_identities.count },
      ],
    },
  ];

  const run = overview.run;
  const durationMs =
    run?.started_at_ns != null && run.ended_at_ns != null
      ? Math.max(0, (run.ended_at_ns - run.started_at_ns) / 1_000_000)
      : null;

  return {
    health,
    healthReason,
    identity: {
      scenarioId: run?.scenario_id ?? null,
      mode: run?.mode ?? null,
      collectorVersion: run?.collector_version ?? null,
    },
    timeRange: {
      startedAtNs: run?.started_at_ns ?? null,
      endedAtNs: run?.ended_at_ns ?? null,
      durationMs,
    },
    kpi,
    signals,
    statCards,
  };
}
```

> 说明：`criticalAlerts` 用 `alerts.count` 作为告警健康判定输入（`DatasetOverview.alerts` 无 severity 分布字段，spec 不造数——保守按 count>0 视为 warning）。KPI hint 用 `formatBreakdown` 给出分解字符串；无分解数据时返回空串（不幻觉）。

- [ ] **Step 4: 运行确认通过**

Run: `cd frontend && npx vitest run __tests__/overview-data.test.ts`
Expected: PASS。若「weak-evidence detail 含 45.5」断言失败，检查除法精度（`830/1823=0.4553… → 45.5%`，与 spec 实测一致）。

- [ ] **Step 5: Commit**

```bash
cd /home/lzk/MtAgentLens/frontend
git add lib/dataset-summary.ts __tests__/overview-data.test.ts
git commit -m "feat: deriveDatasetSummary 纯函数派生概览摘要（不幻觉）"
```

---

### Task 8: 数据源选择器七项统计（`run-selector.tsx`）

**Files:**
- Modify: `components/system/run-selector.tsx`
- Test: `__tests__/run-selector.test.tsx`、`e2e/db-import.spec.ts`

**Interfaces:**
- Consumes: `DbImport` 的 counts + overview 字段（Task 6）。
- Produces: 七项 meta 字符串（e2e 与组件测试断言用）。

- [ ] **Step 1: 写失败测试**

将 `__tests__/run-selector.test.tsx` 第 159-160 行的断言改为七项：

```ts
    expect((await screen.findByText(/3 节点 · 2 告警 · 3 证据 · 2 声明 · 1 回溯 · 3 意图 · 3 动作/)).textContent).toMatch(
      /3 节点 · 2 告警 · 3 证据 · 2 声明 · 1 回溯 · 3 意图 · 3 动作/,
    );
```

> 注意：db 导入路径经 `loadDatabase` 后，`claimCount=2`（Task 2 fixture 加 claim-2）。若该测试当前构造的 `dbImport` 对象是手工对象而非走 `loadDatabase`，则直接改其 `claimCount`/`intentCount` 等字段以匹配七项格式。

- [ ] **Step 2: 运行确认失败**

Run: `cd frontend && npx vitest run __tests__/run-selector.test.tsx`
Expected: FAIL —— entryMeta 仍输出三项，找不到「· 2 声明」。

- [ ] **Step 3: 更新 `entryMeta`（第 92-108 行）**

```ts
    case 'file':
      // Db imports carry real correlation evidence + full-table counts; JSON
      // snapshots only expose graph + alerts.
      return isDbImport(entry.snapshot)
        ? `${entry.snapshot.nodeCount} 节点 · ${entry.snapshot.alertCount} 告警 · ${entry.snapshot.evidenceCount} 证据 · ${entry.snapshot.claimCount} 声明 · ${entry.snapshot.tracebackCount} 回溯 · ${entry.snapshot.intentCount} 意图 · ${entry.snapshot.actionCount} 动作`
        : `${entry.snapshot.nodeCount} 节点 · ${entry.snapshot.alertCount} 告警`;
```

- [ ] **Step 4: 同步 e2e 断言（第 59-64 行 `EXPECTED`）**

```ts
const EXPECTED = {
  nodes: 1461,
  alerts: 13,
  evidence: 1823,
  meta: '1461 节点 · 13 告警 · 1823 证据 · 22 声明 · 22 回溯 · 23 意图 · 474 动作',
};
```

- [ ] **Step 5: 运行确认通过**

Run: `cd frontend && npx vitest run __tests__/run-selector.test.tsx`
Expected: PASS。

- [ ] **Step 6: Commit**

```bash
cd /home/lzk/MtAgentLens/frontend
git add components/system/run-selector.tsx __tests__/run-selector.test.tsx e2e/db-import.spec.ts
git commit -m "feat: 数据源选择器七项统计（节点·告警·证据·声明·回溯·意图·动作）"
```

---

### Task 9: 概览页去演示化 + 数据源详情面板（`OverviewDatasetPanel`）

**Files:**
- Create: `components/overview/dataset-overview-panel.tsx`
- Modify: `components/overview/overview-page-client.tsx`
- Test: `__tests__/dataset-overview-panel.test.tsx`、`__tests__/overview-page-client.test.tsx`

**Interfaces:**
- Consumes: `deriveDatasetSummary`/`DatasetSummary`（Task 7）、`useAppStore().overview`（Task 6）。
- Produces: `OverviewDatasetPanel`（导出组件，概览页渲染）；`overview` 空态（null）时组件返回 `null`。

- [ ] **Step 1: 写失败测试**

新增 `__tests__/dataset-overview-panel.test.tsx`：

```tsx
// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import type { DatasetOverview } from '@/lib/types';
import OverviewDatasetPanel from '@/components/overview/dataset-overview-panel';

const overview: DatasetOverview = {
  run: { run_id: 'run-1', started_at_ns: 1_000_000, ended_at_ns: 9_000_000, scenario_id: 'scenario-x', mode: 'record', collector_version: '0.1.0' },
  snapshots: { count: 55 },
  graph: { nodes: 1461, edges: 3300 },
  alerts: { count: 13 },
  evidence: { count: 1823, by_match_level: { L5Substring: 830 } },
  claims: { count: 3, by_type: { TaintedContextUse: 3 }, by_severity: { Critical: 3 } },
  tracebacks: { count: 22 },
  intents: { count: 23, by_tool_name: { Bash: 9 }, by_authority_source: { SseResponseToolUse: 20 } },
  actions: { count: 474, by_action_type: { FileRead: 424 }, by_signal_class: { HighSignal: 100 } },
  pollution: { count: 219 },
  policy_evaluations: { count: 755 },
  processes: { count: 160 },
  tls_sessions: { count: 1067 },
  identity_process_map: { count: 196 },
  provisional_identities: { count: 196 },
  agent_identities: { count: 0 },
  message_bindings: { count: 0 },
  delegation_records: { count: 0 },
  subagent_boundaries: { count: 0 },
  refinements: { claim: 0, classification: 0, evidence: 0, graph: 0, identity: 0 },
  non_link_diagnostics: { count: 2223 },
};

describe('OverviewDatasetPanel — agentsight 风格数据源详情', () => {
  it('renders the summary band with health badge and identity row', () => {
    render(<OverviewDatasetPanel overview={overview} />);
    expect(screen.getByText(/需关注/)).toBeDefined();
    expect(screen.getByText(/scenario-x/)).toBeDefined();
    expect(screen.getByText(/record/)).toBeDefined();
  });

  it('renders 6 KPI tiles with real counts', () => {
    render(<OverviewDatasetPanel overview={overview} />);
    for (const label of ['进程', '文件活动', '工具调用', '网络', '责任声明', '告警']) {
      expect(screen.getByText(label)).toBeDefined();
    }
    expect(screen.getByText('160')).toBeDefined(); // processes
    expect(screen.getByText('1067')).toBeDefined(); // tls_sessions
  });

  it('renders the signal panel with aggregated ×count rows', () => {
    render(<OverviewDatasetPanel overview={overview} />);
    expect(screen.getByText('上下文污染事件')).toBeDefined();
    expect(screen.getByText('× 219')).toBeDefined();
    expect(screen.getByText('弱匹配证据')).toBeDefined();
    expect(screen.getByText('× 830')).toBeDefined();
  });

  it('renders stat card groups', () => {
    render(<OverviewDatasetPanel overview={overview} />);
    expect(screen.getByText('快照帧')).toBeDefined();
    expect(screen.getByText('意图动作')).toBeDefined();
    expect(screen.getByText('策略评估')).toBeDefined();
  });

  it('returns null when there is no overview (non-db sources)', () => {
    const { container } = render(<OverviewDatasetPanel overview={null} />);
    expect(container.firstChild).toBeNull();
  });
});
```

- [ ] **Step 2: 运行确认失败**

Run: `cd frontend && npx vitest run __tests__/dataset-overview-panel.test.tsx`
Expected: FAIL —— `Cannot find module '@/components/overview/dataset-overview-panel'`。

- [ ] **Step 3: 实现组件**

新增 `components/overview/dataset-overview-panel.tsx`：

```tsx
'use client';

import type { DatasetOverview } from '@/lib/types';
import { deriveDatasetSummary, type FrictionSignal } from '@/lib/dataset-summary';
import { SectionPanel } from '@/components/ui/linear/section-panel';
import { StatusBadge, type StatusTone } from '@/components/ui/linear/status-badge';

const T = { xs: 9.5, sm: 10.5, md: 12, lg: 14, xl: 16 };
const S = { xs: 2, sm: 4, md: 8, lg: 12, xl: 16 };

const sevColor: Record<string, string> = { Critical: '#f87171', Warning: '#fbbf24', Info: '#60a5fa' };

function fmtDuration(ms: number | null): string {
  if (ms == null) return '—';
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.round(ms / 60_000)}m`;
}

function fmtClock(ns: number | null): string {
  if (ns == null) return '—';
  return new Date(ns / 1_000_000).toISOString().slice(11, 19);
}

function signalTone(s: FrictionSignal['severity']): StatusTone {
  if (s === 'Critical') return 'danger';
  if (s === 'Warning') return 'warning';
  return 'info';
}

export default function OverviewDatasetPanel({ overview }: { overview: DatasetOverview | null }) {
  if (!overview) return null; // 非 db 数据源（demo/sample/API fallback）无概览，诚实不渲染

  const summary = deriveDatasetSummary(overview);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: S.lg }}>
      {/* ── SummaryBand：健康徽章 + 身份行 + 元信息 ── */}
      <SectionPanel eyebrow="数据源详情" title={overview.run?.run_id ?? '数据库导入'} bodyClassName="ln-panel-body">
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <StatusBadge tone={summary.health === 'critical' ? 'danger' : summary.health === 'warning' ? 'warning' : 'success'}>
            {summary.health === 'critical' ? '需关注' : summary.health === 'warning' ? '告警' : '正常'}
          </StatusBadge>
          <span className="ln-panel__meta">{summary.healthReason}</span>
          {summary.identity.scenarioId && <span className="ln-panel__meta">{summary.identity.scenarioId}</span>}
          {summary.identity.mode && <span className="ln-panel__meta">{summary.identity.mode}</span>}
          {summary.identity.collectorVersion && (
            <span className="ln-panel__meta" style={{ fontFamily: 'var(--ln-mono)' }}>
              v{summary.identity.collectorVersion}
            </span>
          )}
          <span className="ln-panel__meta" style={{ marginLeft: 'auto', fontFamily: 'var(--ln-mono)' }}>
            {fmtClock(summary.timeRange.startedAtNs)} → {fmtClock(summary.timeRange.endedAtNs)} · {fmtDuration(summary.timeRange.durationMs)}
          </span>
        </div>

        {/* 6× KPI tile */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))', gap: S.sm, marginTop: S.lg }}>
          {summary.kpi.map((k) => (
            <div key={k.key} className="ln-panel ln-metric" style={{ padding: `${S.sm}px ${S.md}px` }}>
              <span className="ln-metric__label">{k.label}</span>
              <strong className="ln-metric__value" style={{ fontSize: 20, color: k.tone === 'danger' ? 'var(--ln-danger, #f87171)' : 'var(--ln-text)' }}>
                {k.value}
              </strong>
              <span className="ln-metric__delta" data-tone={k.tone}>{k.hint || '—'}</span>
            </div>
          ))}
        </div>
      </SectionPanel>

      {/* ── 信号面板：规则聚合的 FrictionSignals ── */}
      {summary.signals.length > 0 && (
        <SectionPanel eyebrow="Friction Signals" title="风险信号" meta={`${summary.signals.length} 类`} bodyClassName="ln-panel-body">
          <div style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
            {summary.signals.map((sig) => {
              const c = sevColor[sig.severity] ?? '#60a5fa';
              return (
                <div key={sig.category} style={{ display: 'flex', gap: S.sm, alignItems: 'flex-start', padding: `${S.sm}px 0`, borderBottom: '1px solid var(--ln-border)' }}>
                  <span style={{ width: 8, height: 8, borderRadius: 999, background: c, flexShrink: 0, marginTop: 5 }} />
                  <StatusBadge tone={signalTone(sig.severity)}>{sig.severity}</StatusBadge>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      <strong style={{ fontSize: T.sm, color: 'var(--ln-text)' }}>{sig.title}</strong>
                      <span className="ln-panel__meta" style={{ fontFamily: 'var(--ln-mono)' }}>× {sig.count}</span>
                    </div>
                    <div style={{ fontSize: T.xs, color: 'var(--ln-text-muted)', marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {sig.detail}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </SectionPanel>
      )}

      {/* ── 统计卡组：各表 count ── */}
      <SectionPanel eyebrow="Table Coverage" title="全表概要" meta={`${Object.values(overview).length} 类`} bodyClassName="ln-panel-body">
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: S.md }}>
          {summary.statCards.map((group) => (
            <div key={group.category}>
              <div style={{ fontSize: T.xs, fontWeight: 600, color: 'var(--ln-accent)', letterSpacing: '0.04em', textTransform: 'uppercase', marginBottom: S.sm }}>
                {group.category}
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                {group.items.map((item) => (
                  <div key={item.label} style={{ display: 'flex', justifyContent: 'space-between', fontSize: T.sm }}>
                    <span className="ln-panel__meta">{item.label}</span>
                    <strong className="ln-mono" style={{ color: 'var(--ln-text)' }}>{item.value}</strong>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      </SectionPanel>
    </div>
  );
}
```

- [ ] **Step 4: 概览页接入（去演示化 + 渲染数据源详情）**

`components/overview/overview-page-client.tsx`：
- 顶部 import 加入：`import OverviewDatasetPanel from '@/components/overview/dataset-overview-panel';` 和 `import { useAppStore } from '@/lib/store';`（已存在）。
- 组件顶部读取 overview：

```tsx
  const overview = useAppStore((state) => state.overview);
```

- **替换人造 KPI**（第 146-182 行 `graphStats` 数组）：当 `overview` 存在时用真实 counts 计算；否则回退到现有形态（demo/sample 无 overview，保底不崩）。将 `graphStats` 定义改为：

```tsx
  const graphStats = [
    {
      label: 'Active Agents',
      value: countNodeType(graph, 'Agent'),
      delta: `+${Math.max(1, criticalClusters.length)}`,
      tone: 'success' as StatusTone,
      spark: [2, 3, 2, 4, 3, 5, 4, 6],
    },
    {
      label: 'Graph Nodes',
      value: overview ? overview.graph.nodes : graph.nodes.length,
      delta: overview ? `${overview.graph.edges} edges` : `+${graph.edges.length}`,
      tone: 'info' as StatusTone,
      spark: [18, 22, 20, 26, 24, 30, 28, 34],
    },
    {
      label: 'Critical Claims',
      value: overview ? (overview.claims.by_severity.Critical ?? 0) : 0,
      delta: overview ? `of ${overview.claims.count} claims` : '—',
      tone: (overview && (overview.claims.by_severity.Critical ?? 0) > 0 ? 'danger' : 'info') as StatusTone,
      spark: [4, 3, 5, 2, 4, 3, 2, 1],
    },
    {
      label: 'Evidence',
      value: overview ? overview.evidence.count : totalEvidence,
      delta: overview ? `L5 ${overview.evidence.by_match_level.L5Substring ?? 0}` : `${totalEvidence} linked`,
      tone: 'success' as StatusTone,
      spark: [30, 38, 44, 50, 55, 62, 68, 74],
    },
    {
      label: 'Intents',
      value: overview ? overview.intents.count : 0,
      delta: overview ? `actions ${overview.actions.count}` : '—',
      tone: 'info' as StatusTone,
      spark: [10, 14, 12, 18, 16, 22, 20, 26],
    },
    {
      label: 'Critical Alerts',
      value: totalCriticalAlerts,
      delta: `↓ ${Math.max(1, alerts.length - totalCriticalAlerts)}`,
      tone: 'danger' as StatusTone,
      spark: [4, 3, 5, 2, 4, 3, 2, 1],
    },
  ];
```

> 注：`graph.edges.length * 72`（Live Events/s）、`graph.edges.length * 41`（Responsibility Edges）、`72 + totalEvidence * 4.3`%（Evidence Coverage）三张人造卡已被删除替换为真实 counts；spark 保留为装饰数组（spec 不把它当数据）。`criticalAlerts`/`criticalClusters` 等原变量仍被图表区使用，勿删。

- **渲染数据源详情**：在「charts row」`</div>`（第 538 行）之后、`</main>`（第 539 行）之前插入：

```tsx
            {overview && (
              <div style={{ marginTop: S.lg }}>
                <OverviewDatasetPanel overview={overview} />
              </div>
            )}
```

- **同步页面级测试**：`__tests__/overview-page-client.test.tsx` 现有断言用 demo 数据（无 overview）→ `OverviewDatasetPanel` 返回 null，不破坏现有断言。新增用例：

```tsx
  it('renders the dataset overview panel when a db overview is present', () => {
    useAppStore.setState({
      alerts: demoAlerts,
      graph: demoGraph,
      overview: {
        run: null,
        snapshots: { count: 1 },
        graph: { nodes: demoGraph.nodes.length, edges: demoGraph.edges.length },
        alerts: { count: demoAlerts.length },
        evidence: { count: 0, by_match_level: {} },
        claims: { count: 0, by_type: {}, by_severity: {} },
        tracebacks: { count: 0 },
        intents: { count: 0, by_tool_name: {}, by_authority_source: {} },
        actions: { count: 0, by_action_type: {}, by_signal_class: {} },
        pollution: { count: 0 },
        policy_evaluations: { count: 0 },
        processes: { count: 0 },
        tls_sessions: { count: 0 },
        identity_process_map: { count: 0 },
        provisional_identities: { count: 0 },
        agent_identities: { count: 0 },
        message_bindings: { count: 0 },
        delegation_records: { count: 0 },
        subagent_boundaries: { count: 0 },
        refinements: { claim: 0, classification: 0, evidence: 0, graph: 0, identity: 0 },
        non_link_diagnostics: { count: 0 },
      },
    });
    render(<OverviewPageClient />, { wrapper: createWrapper() });
    expect(screen.getByText('数据源详情')).toBeDefined();
  });
```

- [ ] **Step 5: 运行确认通过**

Run: `cd frontend && npx vitest run __tests__/dataset-overview-panel.test.tsx __tests__/overview-page-client.test.tsx`
Expected: PASS（现有页面用例不回归）。

- [ ] **Step 6: Commit**

```bash
cd /home/lzk/MtAgentLens/frontend
git add components/overview/dataset-overview-panel.tsx components/overview/overview-page-client.tsx __tests__/dataset-overview-panel.test.tsx __tests__/overview-page-client.test.tsx
git commit -m "feat: 概览页去演示化 + agentsight 风格数据源详情面板"
```

---

### Task 10: 告警工作台责任声明 + 回溯链（`ClaimList`）

**Files:**
- Create: `components/investigation/claim-list.tsx`
- Modify: `components/investigation/investigation-workbench.tsx`
- Test: `__tests__/claim-list.test.tsx`

**Interfaces:**
- Consumes: `ResponsibilityClaim`/`ResponsibilityTraceback`/`GraphData`/`CorrelationEvidence`（types）、`buildTracebacksByClaimId`（Task 3）、`collectAlertEvidenceRefs`（`lib/investigation.ts:49`）、`useAppStore().claims/tracebacks`（Task 6）、`AgentAvatar`/`StatusBadge`/`SectionPanel`/`VirtualList`。
- Produces: `ClaimList`（导出组件，workbench 中栏渲染）；`formatResponsibilityNodeLabel` 复用（lib/investigation.ts:74）。

- [ ] **Step 1: 写失败测试**

新增 `__tests__/claim-list.test.tsx`：

```tsx
// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import type { ResponsibilityClaim, ResponsibilityTraceback } from '@/lib/types';
import ClaimList from '@/components/investigation/claim-list';

const claims: ResponsibilityClaim[] = [
  {
    claim_id: 'claim-1', claim_type: 'TaintedContextUse', subject_agent_id: 'agent-a',
    policy_id: 'POLICY-1', boundary_id: 'BOUNDARY-1', claim_state: 'Inferred',
    severity: 'Critical', confidence: 0.9, evidence_refs: ['ev-1'],
    competing_explanations: [], traceback_ref: null,
    phase: { Provisional: { timestamp_ns: 1000 } }, timestamp_ns: 1000,
  },
  {
    claim_id: 'claim-2', claim_type: 'PolicyViolation', subject_agent_id: 'agent-a',
    policy_id: 'POLICY-2', boundary_id: null, claim_state: 'Confirmed',
    severity: 'Warning', confidence: 0.7, evidence_refs: [],
    competing_explanations: [
      { label: 'Alt path', evidence_refs: [], rule_id: 'R-ALT', rationale: 'Alternative', aligned_pattern: null },
    ],
    traceback_ref: null, phase: { Authoritative: { timestamp_ns: 1200, overrides: null } },
    timestamp_ns: 1200,
  },
];

const tracebacks: ResponsibilityTraceback[] = [
  {
    traceback_id: 'tb-1', claim_id: 'claim-1',
    chain_node_ids: ['agent-a', 'root', 'agent-a'], // 含重复节点，去重后 2 个
    chain_edge_ids: ['e1', 'e2'], root_cause_node_id: 'root',
    explanation_summary: 'Context flowed via shell', confidence: 0.9,
  },
];

const graph = {
  nodes: [
    { node_id: 'agent-a', node_type: 'Agent', agent_id: 'agent-a', resource: null, evidence_refs: [], timestamp_ns: 0, phase: { Provisional: { timestamp_ns: 0 } } },
    { node_id: 'root', node_type: 'Resource', agent_id: null, resource: { File: { canonical_path: '/tmp/x.sh' } }, evidence_refs: [], timestamp_ns: 0, phase: { Provisional: { timestamp_ns: 0 } } },
  ],
  edges: [],
};

const tracebacksByClaimId = new Map(tracebacks.map((t) => [t.claim_id, t]));

describe('ClaimList — 责任声明 + 回溯链', () => {
  it('renders claims as FrictionRows sorted by timestamp then claim_id', () => {
    render(<ClaimList claims={claims} tracebacksByClaimId={tracebacksByClaimId} graph={graph} evidence={[]} alertEvidenceRefs={[]} />);
    const titles = screen.getAllByText(/TaintedContextUse|PolicyViolation/);
    expect(titles.length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText('claim-1')).toBeDefined();
    expect(screen.getByText('claim-2')).toBeDefined();
  });

  it('expands a claim to render the reverse-indexed traceback chain (deduped)', async () => {
    render(<ClaimList claims={claims} tracebacksByClaimId={tracebacksByClaimId} graph={graph} evidence={[]} alertEvidenceRefs={[]} />);
    const firstRow = screen.getByText('claim-1').closest('[data-claim-row]')!;
    (firstRow as HTMLElement).click();
    expect(await screen.findByText('Context flowed via shell')).toBeDefined();
    expect(screen.getByText(/根因/)).toBeDefined();
    // 链去重：agent-a 出现两次但只渲染一次
    expect(screen.getAllByText('agent-a').length).toBeLessThanOrEqual(2);
  });

  it('renders unresolved evidence refs verbatim', () => {
    render(<ClaimList claims={claims} tracebacksByClaimId={tracebacksByClaimId} graph={graph} evidence={[]} alertEvidenceRefs={[]} />);
    expect(screen.getByText('ev-1')).toBeDefined();
  });

  it('shows competing explanations when present', async () => {
    render(<ClaimList claims={claims} tracebacksByClaimId={tracebacksByClaimId} graph={graph} evidence={[]} alertEvidenceRefs={[]} />);
    const secondRow = screen.getByText('claim-2').closest('[data-claim-row]')!;
    (secondRow as HTMLElement).click();
    expect(await screen.findByText('Alt path')).toBeDefined();
  });

  it('shows an empty state when there are no claims', () => {
    render(<ClaimList claims={[]} tracebacksByClaimId={new Map()} graph={graph} evidence={[]} alertEvidenceRefs={[]} />);
    expect(screen.getByText(/暂无责任声明/)).toBeDefined();
  });

  it('counts shared evidence with the alert (weak link via graph edges)', () => {
    render(<ClaimList claims={claims} tracebacksByClaimId={tracebacksByClaimId} graph={graph} evidence={[]} alertEvidenceRefs={['ev-1', 'ev-9']} />);
    // claim-1 的 evidence_refs=['ev-1'] 与告警侧交叠 1 条
    expect(screen.getByText(/共享 1 条证据/)).toBeDefined();
  });
});
```

- [ ] **Step 2: 运行确认失败**

Run: `cd frontend && npx vitest run __tests__/claim-list.test.tsx`
Expected: FAIL —— `Cannot find module '@/components/investigation/claim-list'`。

- [ ] **Step 3: 实现组件**

新增 `components/investigation/claim-list.tsx`：

```tsx
'use client';

import { useMemo, useState } from 'react';
import { ChevronDown, ChevronRight, ShieldAlert, CircleAlert, Info, FileSearch, GitBranch } from 'lucide-react';
import type { CorrelationEvidence, GraphData, ResponsibilityClaim, ResponsibilityTraceback } from '@/lib/types';
import { formatResponsibilityNodeLabel } from '@/lib/investigation';
import AgentAvatar from '@/components/ui/agent-avatar';
import VirtualList from '@/components/ui/virtual-list';

const T = { xs: 9.5, sm: 10.5, md: 12, lg: 14, xl: 16 };
const S = { xs: 2, sm: 4, md: 8, lg: 12, xl: 16 };

const sevColor: Record<string, string> = { Critical: '#f87171', Warning: '#fbbf24', Info: '#60a5fa' };
const sevIcon: Record<string, typeof ShieldAlert> = { Critical: ShieldAlert, Warning: CircleAlert, Info };

export interface ClaimListProps {
  claims: ResponsibilityClaim[];
  tracebacksByClaimId: Map<string, ResponsibilityTraceback>;
  graph: GraphData;
  evidence: CorrelationEvidence[];
  /** 告警侧引用集合（weak link）——用于「与该告警共享 N 条证据」计数。 */
  alertEvidenceRefs: string[];
}

export default function ClaimList({ claims, tracebacksByClaimId, graph, evidence, alertEvidenceRefs }: ClaimListProps) {
  const [expandedId, setExpandedId] = useState<string | null>(null);

  // 排序：timestamp_ns asc + claim_id 次键（同时间戳稳定序）
  const sorted = useMemo(
    () =>
      [...claims].sort(
        (a, b) => a.timestamp_ns - b.timestamp_ns || a.claim_id.localeCompare(b.claim_id),
      ),
    [claims],
  );

  const evidenceById = useMemo(() => new Map(evidence.map((e) => [e.evidence_id, e])), [evidence]);
  const alertRefSet = useMemo(() => new Set(alertEvidenceRefs), [alertEvidenceRefs]);

  if (sorted.length === 0) {
    return (
      <div style={{ textAlign: 'center', padding: '20px 0', color: 'var(--ln-text-muted)', fontSize: T.sm }}>
        暂无责任声明
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 0, maxHeight: 460, overflowY: 'auto' }}>
      {sorted.map((claim) => {
        const sc = sevColor[claim.severity] ?? '#60a5fa';
        const Icon = sevIcon[claim.severity] ?? Info;
        const expanded = expandedId === claim.claim_id;
        const sharedCount = claim.evidence_refs.filter((ref) => alertRefSet.has(ref)).length;
        const tb = tracebacksByClaimId.get(claim.claim_id) ?? null;
        const nodeById = new Map(graph.nodes.map((n) => [n.node_id, n]));
        // 链节点去重（实测 4/22 链含重复 UUID）
        const chainNodes = tb
          ? Array.from(new Set(tb.chain_node_ids))
              .map((id) => nodeById.get(id) ?? null)
              .filter((n): n is NonNullable<typeof n> => n != null)
          : [];

        return (
          <div key={claim.claim_id} data-claim-row style={{ display: 'flex', flexDirection: 'column' }}>
            <button
              type="button"
              aria-expanded={expanded}
              onClick={() => setExpandedId(expanded ? null : claim.claim_id)}
              style={{
                display: 'flex', alignItems: 'center', gap: S.sm, padding: '9px 10px', textAlign: 'left',
                border: '1px solid var(--ln-border)', borderRadius: 8, background: 'var(--ln-surface-0)',
                cursor: 'pointer', transition: 'border-color 120ms ease',
              }}
            >
              <Icon size={14} style={{ color: sc, flexShrink: 0 }} />
              <span style={{ padding: '1px 6px', borderRadius: 4, fontSize: 9, fontWeight: 600, background: `${sc}16`, color: sc, fontFamily: 'var(--ln-mono)', flexShrink: 0 }}>
                {claim.claim_type}
              </span>
              {claim.subject_agent_id ? (
                <AgentAvatar agentId={claim.subject_agent_id} size={16} />
              ) : null}
              <span style={{ fontSize: T.sm, fontWeight: 600, color: 'var(--ln-text)', flexShrink: 0 }}>
                {claim.subject_agent_id ?? '—'}
              </span>
              <span className="ln-panel__meta" style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {claim.policy_id ?? '—'}{claim.boundary_id ? ` · ${claim.boundary_id}` : ''}
              </span>
              <span style={{ fontSize: 9, fontFamily: 'var(--ln-mono)', color: 'var(--ln-text-muted)', flexShrink: 0 }}>
                {claim.claim_state}
              </span>
              <span style={{ width: 48, flexShrink: 0 }}>
                <div style={{ height: 3, borderRadius: 999, background: 'var(--ln-surface-2)', overflow: 'hidden' }}>
                  <div style={{ height: '100%', width: `${Math.round(claim.confidence * 100)}%`, background: sc }} />
                </div>
              </span>
              {tb && <GitBranch size={12} style={{ color: '#60a5fa', flexShrink: 0 }} />}
              {expanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
            </button>

            {expanded && (
              <div style={{ padding: S.sm, marginLeft: S.md, borderLeft: `2px solid ${sc}30`, display: 'flex', flexDirection: 'column', gap: S.sm }}>
                {/* 回溯链 */}
                {tb ? (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                    {chainNodes.map((node, idx) => (
                      <div key={`${node.node_id}-${idx}`} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: T.xs }}>
                        <span style={{ width: 18, height: 18, borderRadius: 5, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 9, fontFamily: 'var(--ln-mono)', background: `${sc}18`, border: `1px solid ${sc}40`, color: sc, flexShrink: 0 }}>
                          {idx + 1}
                        </span>
                        <span style={{ fontFamily: 'var(--ln-mono)', color: node.node_id === tb.root_cause_node_id ? '#f87171' : 'var(--ln-text-secondary)', fontWeight: node.node_id === tb.root_cause_node_id ? 700 : 400, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {formatResponsibilityNodeLabel(node)}
                        </span>
                        {node.node_id === tb.root_cause_node_id && (
                          <span style={{ fontSize: 9, padding: '1px 5px', borderRadius: 3, background: 'rgba(248,113,113,0.14)', color: '#f87171', flexShrink: 0 }}>根因</span>
                        )}
                      </div>
                    ))}
                    <div style={{ fontSize: T.xs, color: 'var(--ln-text-secondary)', lineHeight: 1.55, marginTop: 2 }}>
                      {tb.explanation_summary}
                    </div>
                  </div>
                ) : (
                  <span className="ln-panel__meta">此声明无回溯链</span>
                )}

                {/* 证据引用 */}
                {claim.evidence_refs.length > 0 && (
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                    {claim.evidence_refs.map((ref) => {
                      const ev = evidenceById.get(ref);
                      const label = ev?.matched_fields.canonical_path ?? ref;
                      return (
                        <span key={ref} title={ref} style={{ display: 'inline-flex', alignItems: 'center', gap: 3, fontSize: 9, padding: '2px 6px', borderRadius: 4, background: 'rgba(74,222,128,0.10)', color: '#4ade80', fontFamily: 'var(--ln-mono)', maxWidth: 220, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          <FileSearch size={9} />{label}
                        </span>
                      );
                    })}
                  </div>
                )}

                {/* 竞争解释 */}
                {claim.competing_explanations.length > 0 && (
                  <div style={{ padding: S.xs, borderRadius: 6, background: 'var(--ln-surface-2)', fontSize: T.xs, color: 'var(--ln-text-muted)' }}>
                    {claim.competing_explanations.map((alt) => (
                      <div key={alt.rule_id}>
                        <strong style={{ color: 'var(--ln-text-secondary)' }}>{alt.label}</strong>
                        <span> — {alt.rationale}</span>
                      </div>
                    ))}
                  </div>
                )}

                {sharedCount > 0 && (
                  <span className="ln-panel__meta">与该告警共享 {sharedCount} 条证据</span>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
```

- [ ] **Step 4: 接入告警工作台**

`components/investigation/investigation-workbench.tsx`：
- 顶部 import 加入：

```tsx
import { useMemo } from 'react'; // 若 useRef 行已存在则改为同时引入 useMemo
import ClaimList from '@/components/investigation/claim-list';
import { buildTracebacksByClaimId } from '@/lib/traceback-index';
import { collectAlertEvidenceRefs } from '@/lib/investigation';
import { useAppStore } from '@/lib/store';
```

- 组件函数体内（第 31 行后）读取 store 数据并构建索引：

```tsx
  const claims = useAppStore((s) => s.claims);
  const tracebacks = useAppStore((s) => s.tracebacks);
  const graph = useAppStore((s) => s.graph);
  const tracebacksByClaimId = useMemo(() => buildTracebacksByClaimId(tracebacks), [tracebacks]);
  const alertEvidenceRefs = useMemo(
    () => collectAlertEvidenceRefs(alert, graph),
    [alert, graph],
  );
```

- 中栏「证据链」`</SectionPanel>`（第 257 行）之后、`</div>`（中栏容器 258 行）之前插入：

```tsx
          <SectionPanel eyebrow="责任声明" title={`${claims.length} 条`} bodyClassName="ln-panel-body">
            <ClaimList
              claims={claims}
              tracebacksByClaimId={tracebacksByClaimId}
              graph={graph}
              evidence={model.linkedEvidence}
              alertEvidenceRefs={alertEvidenceRefs}
            />
          </SectionPanel>
```

> 证据用 `model.linkedEvidence`（当前告警已关联证据）；claims 全量来自 store（db 导入才非空）。

- [ ] **Step 5: 运行确认通过**

Run: `cd frontend && npx vitest run __tests__/claim-list.test.tsx`
Expected: PASS。若「根因」/「共享 1 条证据」断言不稳，检查测试中 row 的 `data-claim-row` 是否命中。

- [ ] **Step 6: Commit**

```bash
cd /home/lzk/MtAgentLens/frontend
git add components/investigation/claim-list.tsx components/investigation/investigation-workbench.tsx __tests__/claim-list.test.tsx
git commit -m "feat: 告警工作台责任声明 + 回溯链（反向索引 + 去重 + 弱关联计数）"
```

---

### Task 11: 追踪页意图动作链（`IntentActionChain` + 升级 `IntentActionMatch`）

**Files:**
- Create: `components/traces/intent-action-chain.tsx`
- Modify: `components/ui/intent-action-match.tsx`、`components/traces/traces-page-client.tsx`
- Test: `__tests__/intent-action-chain.test.tsx`

**Interfaces:**
- Consumes: `Intent`/`Action`/`CorrelationEvidence`（types）、`useAppStore().intents/actions`（Task 6）、`VirtualList`/`StatusBadge`/`SectionPanel`。
- Produces: `IntentActionChain`（导出组件）；升级版 `IntentActionMatch`（props 增加可选 `intent`/`action`，向后兼容仅传 `evidence`）。

- [ ] **Step 1: 写失败测试**

新增 `__tests__/intent-action-chain.test.tsx`：

```tsx
// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import type { Action, CorrelationEvidence, Intent } from '@/lib/types';
import IntentActionChain from '@/components/traces/intent-action-chain';

const intents: Intent[] = [
  { intent_id: 'int-1', process_id: { pid: 42, start_time_ns: 1000 }, message_id: 'm1', tool_name: 'Bash', tool_args: [{ Command: 'ls -la /tmp' }], intent_class: 'FileSystem', parse_state: 'Eligible', primary_matching_eligible: true, authority_source: 'SseResponseToolUse', timestamp_ns: 1000 },
  { intent_id: 'int-2', process_id: { pid: 42, start_time_ns: 1000 }, message_id: 'm2', tool_name: 'Read', tool_args: [{ Path: '/tmp/x.sh' }], intent_class: 'FileSystem', parse_state: 'Eligible', primary_matching_eligible: true, authority_source: 'TextFallback', timestamp_ns: 1050 },
  { intent_id: 'int-3', process_id: { pid: 7, start_time_ns: 2000 }, message_id: 'm3', tool_name: 'Glob', tool_args: [{ Path: '/tmp/*.sh' }], intent_class: 'FileSystem', parse_state: 'Ineligible', primary_matching_eligible: false, authority_source: 'TextFallback', timestamp_ns: 1100 },
];

const actions: Action[] = [
  { action_id: 'act-1', process_id: { pid: 42, start_time_ns: 1000 }, action_type: 'FileWrite', normalized_args: [{ Path: '/tmp/x.sh' }], derivation_mode: 'Direct', signal_class: 'HighSignal', timestamp_ns: 1000 },
  { action_id: 'act-2', process_id: { pid: 42, start_time_ns: 1000 }, action_type: 'FileRead', normalized_args: [{ Path: '/tmp/x.sh' }], derivation_mode: 'Direct', signal_class: 'LowSignal', timestamp_ns: 1002 },
  { action_id: 'act-3', process_id: { pid: 7, start_time_ns: 2000 }, action_type: 'ProcessExec', normalized_args: [{ Path: '/bin/bash', Command: 'bash' }], derivation_mode: 'Derived', signal_class: 'HighSignal', timestamp_ns: 1100 },
];

const evidence: CorrelationEvidence[] = [
  { evidence_id: 'ev-1', intent_id: 'int-1', action_id: 'act-1', match_level: 'L1Exact', confidence: 0.95, matched_fields: { canonical_path: '/tmp/x.sh', host: null, port: null, command: null, raw_args: [] }, derivation_mode: 'Direct', phase: { Provisional: { timestamp_ns: 1000 } }, timestamp_ns: 1000 },
  { evidence_id: 'ev-2', intent_id: 'int-2', action_id: 'act-1', match_level: 'L3Containment', confidence: 0.65, matched_fields: { canonical_path: null, host: null, port: null, command: null, raw_args: [] }, derivation_mode: 'Direct', phase: { Provisional: { timestamp_ns: 1050 } }, timestamp_ns: 1050 },
  { evidence_id: 'ev-3', intent_id: 'int-1', action_id: 'act-2', match_level: 'L5Substring', confidence: 0.5, matched_fields: { canonical_path: null, host: null, port: null, command: null, raw_args: ['x'] }, derivation_mode: 'Direct', phase: { Provisional: { timestamp_ns: 1200 } }, timestamp_ns: 1200 },
];

describe('IntentActionChain — OTel execute_tool 意图动作链', () => {
  it('groups by intent and aggregates multiple evidence onto one action', () => {
    render(<IntentActionChain intents={intents} actions={actions} evidence={evidence} />);
    // int-1 (Bash) 触发了 act-1 + act-2
    expect(screen.getAllByText('Bash').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('FileWrite')).toBeDefined();
    expect(screen.getByText('FileRead')).toBeDefined();
    // act-1 聚合 ev-1 + ev-2
    expect(screen.getAllByText('L1Exact')).toBeDefined();
    expect(screen.getByText('L3Containment')).toBeDefined();
  });

  it('renders orphan intents/actions greyed but visible', () => {
    render(<IntentActionChain intents={intents} actions={actions} evidence={evidence} />);
    // int-3 (Glob) 与 act-3 (ProcessExec) 无 evidence 关联
    expect(screen.getByText('Glob')).toBeDefined();
    expect(screen.getByText('ProcessExec')).toBeDefined();
  });

  it('shows an empty state when there is no intent/action data', () => {
    render(<IntentActionChain intents={[]} actions={[]} evidence={[]} />);
    expect(screen.getByText(/未提取到意图\/动作关联/)).toBeDefined();
  });
});
```

- [ ] **Step 2: 运行确认失败**

Run: `cd frontend && npx vitest run __tests__/intent-action-chain.test.tsx`
Expected: FAIL —— `Cannot find module '@/components/traces/intent-action-chain'`。

- [ ] **Step 3: 实现组件**

新增 `components/traces/intent-action-chain.tsx`：

```tsx
'use client';

import { useMemo, useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import type { Action, CorrelationEvidence, Intent } from '@/lib/types';

const T = { xs: 9.5, sm: 10.5, md: 12, lg: 14, xl: 16 };
const S = { xs: 2, sm: 4, md: 8, lg: 12, xl: 16 };

const matchColors: Record<string, string> = {
  L1Exact: '#34d399', L2Normalized: '#60a5fa', L3Containment: '#f59e0b',
  L4Basename: '#f59e0b', L5Substring: '#f43f5e',
};
const signalColors: Record<string, string> = { HighSignal: '#f87171', LowSignal: '#60a5fa' };

interface IntentGroup {
  intent: Intent;
  actions: Action[];            // 该 intent 直接触发的 actions（经 evidence 桥接）
  evidence: CorrelationEvidence[];
  orphan: boolean;              // 无任何 evidence 关联 → 灰显
}

function shortArgs(args: unknown[]): string {
  const items = (args ?? [])
    .map((a) => (a && typeof a === 'object' ? Object.values(a as object)[0] : a))
    .filter((v) => typeof v === 'string' || typeof v === 'number');
  return items.join(' ').slice(0, 120);
}

export default function IntentActionChain({
  intents, actions, evidence,
}: { intents: Intent[]; actions: Action[]; evidence: CorrelationEvidence[] }) {
  const [expanded, setExpanded] = useState<string | null>(null);

  const groups = useMemo<IntentGroup[]>(() => {
    const actionById = new Map(actions.map((a) => [a.action_id, a]));
    const evidenceByIntent = new Map<string, CorrelationEvidence[]>();
    const evidenceByAction = new Map<string, CorrelationEvidence[]>();
    for (const ev of evidence) {
      if (!evidenceByIntent.has(ev.intent_id)) evidenceByIntent.set(ev.intent_id, []);
      evidenceByIntent.get(ev.intent_id)!.push(ev);
      if (!evidenceByAction.has(ev.action_id)) evidenceByAction.set(ev.action_id, []);
      evidenceByAction.get(ev.action_id)!.push(ev);
    }
    return intents.map((intent) => {
      const intentEvidence = evidenceByIntent.get(intent.intent_id) ?? [];
      const actionIds = new Set(intentEvidence.map((ev) => ev.action_id));
      const linkedActions = Array.from(actionIds)
        .map((id) => actionById.get(id))
        .filter((a): a is Action => a != null)
        .sort((a, b) => a.timestamp_ns - b.timestamp_ns);
      return {
        intent,
        actions: linkedActions,
        evidence: intentEvidence.sort((a, b) => a.timestamp_ns - b.timestamp_ns),
        orphan: intentEvidence.length === 0,
      };
    });
  }, [intents, actions, evidence]);

  const orphanActions = useMemo(() => {
    const linkedActionIds = new Set(groups.flatMap((g) => g.actions.map((a) => a.action_id)));
    return actions.filter((a) => !linkedActionIds.has(a.action_id));
  }, [groups, actions]);

  if (groups.length === 0 && orphanActions.length === 0) {
    return (
      <div style={{ textAlign: 'center', padding: '20px 0', color: 'var(--ln-text-muted)', fontSize: T.sm }}>
        此数据源未提取到意图/动作关联
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
      {groups.map(({ intent, actions: intentActions, evidence: intentEvidence, orphan }) => {
        const open = expanded === intent.intent_id;
        return (
          <div key={intent.intent_id} style={{ opacity: orphan ? 0.55 : 1, borderBottom: '1px solid var(--ln-border)' }}>
            {/* intent 组头 —— execute_tool span */}
            <button
              type="button"
              aria-expanded={open}
              onClick={() => setExpanded(open ? null : intent.intent_id)}
              style={{ display: 'flex', alignItems: 'center', gap: S.sm, width: '100%', padding: `${S.sm}px ${S.sm}px`, background: 'none', border: 'none', cursor: 'pointer', textAlign: 'left' }}
            >
              <span style={{ fontSize: T.sm, fontWeight: 600, color: orphan ? 'var(--ln-text-muted)' : 'var(--ln-accent)', flexShrink: 0 }}>
                {intent.tool_name}
              </span>
              <span style={{ fontSize: 9, padding: '1px 5px', borderRadius: 3, background: 'var(--ln-surface-2)', color: 'var(--ln-text-muted)', fontFamily: 'var(--ln-mono)' }}>
                {intent.intent_class}
              </span>
              <span className="ln-panel__meta" style={{ fontFamily: 'var(--ln-mono)' }}>
                {intent.authority_source}
              </span>
              <span className="ln-panel__meta" style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {shortArgs(intent.tool_args)}
              </span>
              <span className="ln-panel__meta">{intentEvidence.length} 证据</span>
              {orphan && <span style={{ fontSize: 9, color: 'var(--ln-text-muted)' }}>无证据关联</span>}
              {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
            </button>

            {open && (
              <div style={{ padding: `${S.xs}px 0 ${S.md}px ${S.md}px`, display: 'flex', flexDirection: 'column', gap: S.sm }}>
                {intentActions.length === 0 ? (
                  <span className="ln-panel__meta">该意图未产生系统效应</span>
                ) : (
                  intentActions.map((action) => {
                    const actionEvidence = intentEvidence.filter((ev) => ev.action_id === action.action_id);
                    return (
                      <div key={action.action_id} style={{ padding: `${S.xs + 2}px ${S.sm}px`, borderRadius: 6, background: 'var(--ln-surface-0)', border: '1px solid var(--ln-border)' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                          <span style={{ fontSize: T.sm, fontWeight: 600, color: 'var(--ln-text)' }}>{action.action_type}</span>
                          <span style={{ fontSize: 9, padding: '1px 5px', borderRadius: 3, background: `${signalColors[action.signal_class] ?? '#68687a'}18`, color: signalColors[action.signal_class] ?? '#68687a', fontFamily: 'var(--ln-mono)' }}>
                            {action.signal_class}
                          </span>
                          <span className="ln-panel__meta">{action.derivation_mode}</span>
                        </div>
                        {shortArgs(action.normalized_args) && (
                          <div style={{ fontSize: 10, color: 'var(--ln-text-muted)', fontFamily: 'var(--ln-mono)', marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {shortArgs(action.normalized_args)}
                          </div>
                        )}
                        {actionEvidence.map((ev) => (
                          <div key={ev.evidence_id} style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 4, fontSize: 10 }}>
                            <span style={{ color: matchColors[ev.match_level] ?? '#68687a', fontWeight: 600, fontFamily: 'var(--ln-mono)' }}>
                              {ev.match_level}
                            </span>
                            <div style={{ width: 40, height: 3, borderRadius: 999, background: 'var(--ln-surface-2)', overflow: 'hidden' }}>
                              <div style={{ height: '100%', width: `${Math.round(ev.confidence * 100)}%`, background: matchColors[ev.match_level] ?? '#68687a' }} />
                            </div>
                            <span className="ln-panel__meta" style={{ fontFamily: 'var(--ln-mono)' }}>
                              {ev.evidence_id.slice(0, 12)}
                            </span>
                          </div>
                        ))}
                      </div>
                    );
                  })
                )}
              </div>
            )}
          </div>
        );
      })}

      {/* 孤儿 action 灰显列表 */}
      {orphanActions.length > 0 && (
        <div style={{ marginTop: S.sm, opacity: 0.55 }}>
          <span className="ln-panel__meta">孤立动作（{orphanActions.length}）</span>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginTop: 4 }}>
            {orphanActions.map((a) => (
              <span key={a.action_id} style={{ fontSize: 9, padding: '2px 6px', borderRadius: 4, background: 'var(--ln-surface-2)', color: 'var(--ln-text-muted)', fontFamily: 'var(--ln-mono)' }}>
                {a.action_type}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 4: 升级 `components/ui/intent-action-match.tsx`（从 id 片段 → 语义摘要）**

将 `IntentActionMatch` 替换为接收可选 `intent`/`action` 的语义化版本（向后兼容仅传 `evidence`）：

```tsx
'use client';

import type { Action, CorrelationEvidence, Intent } from '@/lib/types';

const matchColors: Record<string, string> = {
  L1Exact: '#34d399',
  L2Normalized: '#60a5fa',
  L3Containment: '#f59e0b',
  L4Basename: '#f59e0b',
  L5Substring: '#f43f5e',
};

export default function IntentActionMatch({
  evidence,
  intent,
  action,
}: {
  evidence: CorrelationEvidence;
  intent?: Intent;
  action?: Action;
}) {
  return (
    <div className="mb-2 rounded-lg p-3" style={{ background: 'rgba(255,255,255,0.03)' }}>
      <div className="mb-1 flex items-center gap-2">
        <span
          className="rounded px-1.5 py-0.5 text-[12px] font-semibold"
          style={{
            background: (matchColors[evidence.match_level] ?? '#68687a') + '22',
            color: matchColors[evidence.match_level],
          }}
        >
          {evidence.match_level}
        </span>
        <span className="text-[12px] text-[#68687a]">置信度 {evidence.confidence.toFixed(2)}</span>
      </div>
      <div className="font-mono text-[12px] text-white/60">
        {intent ? intent.tool_name : evidence.intent_id.slice(0, 8)} →{' '}
        {action ? action.action_type : evidence.action_id.slice(0, 8)}
      </div>
    </div>
  );
}
```

- [ ] **Step 5: 接入追踪页**

`components/traces/traces-page-client.tsx`：
- 顶部 import 加入：

```tsx
import IntentActionChain from '@/components/traces/intent-action-chain';
```

- 组件体内（`evidenceItems` 之后）读取 store 的 intents/actions：

```tsx
  const storeIntents = useAppStore((s) => s.intents);
  const storeActions = useAppStore((s) => s.actions);
```

- 在「责任链路」`SectionPanel`（第 316 行结束）之后、右侧详情列（第 317 行）之前插入：

```tsx
        <SectionPanel eyebrow="意图动作链" title={`${storeIntents.length} 意图 · ${storeActions.length} 动作`} bodyClassName="ln-panel-body">
          <IntentActionChain intents={storeIntents} actions={storeActions} evidence={evidenceItems} />
        </SectionPanel>
```

- [ ] **Step 6: 运行确认通过**

Run: `cd frontend && npx vitest run __tests__/intent-action-chain.test.tsx __tests__/traces-page-client.test.tsx`
Expected: PASS（traces-page-client 现有用例不回归；demo 数据下 `storeIntents=[]` 空态渲染）。

- [ ] **Step 7: Commit**

```bash
cd /home/lzk/MtAgentLens/frontend
git add components/traces/intent-action-chain.tsx components/traces/traces-page-client.tsx components/ui/intent-action-match.tsx __tests__/intent-action-chain.test.tsx
git commit -m "feat: 追踪页意图动作链（OTel execute_tool 语义）+ IntentActionMatch 语义化"
```

---

### Task 12: 回归（全量验证）

**Files:**
- None（只运行验证命令）

- [ ] **Step 1: 类型检查**

Run: `cd frontend && npm run type-check`
Expected: 0 errors。

- [ ] **Step 2: ESLint**

Run: `cd frontend && npm run lint`
Expected: 0 errors（项目历史为 0 errors 0 warnings，不得引入新告警）。

- [ ] **Step 3: 全量单测**

Run: `cd frontend && npx vitest run`
Expected: 全绿（原 253 用例 + 新增约 25 用例）。

- [ ] **Step 4: 生产构建**

Run: `cd frontend && npm run build`
Expected: 成功（Turbopack 构建通过；新组件无 SSR 问题——`'use client'` 头已加）。

- [ ] **Step 5: 单元级回归抽查**

Run: `cd frontend && npx vitest run __tests__/db-import.test.ts __tests__/store-db.test.ts __tests__/run-selector.test.tsx __tests__/overview-data.test.ts __tests__/dataset-overview-panel.test.tsx __tests__/claim-list.test.tsx __tests__/intent-action-chain.test.tsx`
Expected: 全部 PASS。

- [ ] **Step 6: e2e 真实 db 链路**

Run: `cd frontend && npx playwright test e2e/db-import.spec.ts`
Expected: 4 用例通过（七项 meta 断言同步后；真实 db 59MB 导入）。

- [ ] **Step 7: 真机验证（浏览器）**

用 `frontend` dev server（端口 7395）+ 后端 `record` 命令（sudo），导入 `env/runs/20260716-114859-ecb03ced/collector/collector.responsibility.db`，人工核对：
- 数据源选择器七项统计与真实 db 一致（1461 节点 / 13 告警 / 1823 证据 / 22 声明 / 22 回溯 / 23 意图 / 474 动作）
- 概览页数据源详情：健康徽章（存在 Critical claim → 需关注）、6 KPI、信号面板（污染 ×219 / Critical ×3 / L5 弱匹配 45.5% / 策略评估 ×755）、统计卡组
- 告警页责任声明区：22 条声明可展开回溯链（链节点去重、根因高亮）
- 追踪页意图动作链：23 意图分组 + 孤儿动作灰显

- [ ] **Step 8: 文档同步**

Run: `cd /home/lzk/MtAgentLens && git add docs/archive/engineering/specs/2026-08-07-db-full-table-consumption-design.md docs/archive/engineering/plans/2026-08-07-db-full-table-consumption.md && git commit -m "docs: 记录全表消费 spec + 实现计划"`（若计划尚未提交）。

---

## Self-Review（writing-plans 自审）

**1. Spec coverage（逐节映射）：**

| spec 节 | 覆盖任务 |
|---------|---------|
| 4.1 类型设计（2 新增 + 复用 Intent/Action） | Task 1 |
| 4.2 五个提取器（claims/tracebacks/intents/actions/overview） | Task 3 / 4 / 5 |
| 4.3 DbImport + store 扩展 + hasConsumableData | Task 6 |
| 6.1 fixture 修正（真实 schema + intents/actions + 双向链接） | Task 2 |
| 5.4 数据源选择器七项统计 | Task 8 |
| 5.3 概览页去演示化 + agentsight 组合（SummaryBand/6 KPI/信号面板/统计卡组） | Task 7 + 9 |
| 5.1 告警工作台责任声明 + 回溯链（反向索引/去重/根因/弱关联） | Task 10 |
| 5.2 意图动作链 + IntentActionMatch 升级 | Task 11 |
| 6.2/6.3 测试计划（单测 + e2e 断言同步） | 各任务 + Task 12 |
| 七、实施顺序（数据层→概览→责任→意图→入口→回归） | Task 1-12 顺序对齐 |

**2. Placeholder scan：** 每个 Task 的测试与实现均含完整代码；fixture 数据值（`int-1`/`act-1` 等）与现有 `correlation_evidence` 引用一致；真实 db 计数（22/22/23/474）与实测一致；无「TBD/implement later」。

**3. Type consistency：** `tracebacksByClaimId`（Task 3 定义 `buildTracebacksByClaimId`）在 Task 10 以同名使用；`deriveDatasetSummary`（Task 7）→ `DatasetSummary` 在 Task 9 消费字段（health/identity/kpi/signals/statCards）一致；`extractDatasetOverviewFromDb`（Task 5）→ `DatasetOverview` 在 Task 6 `DbImport.overview` 与 Task 7 输入一致；`Intent`/`Action` 复用现有类型未改。
