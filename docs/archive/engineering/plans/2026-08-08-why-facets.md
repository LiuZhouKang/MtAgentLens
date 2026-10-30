# M5「为什么」告警分面 + 全局归因 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 responsibility.db 的三张「为什么」表（污染传播 / 策略判定 / 失配诊断）变成 `/investigations` 告警队列的分面过滤维度 + 可折叠全局归因面板。

**Architecture:** 纯函数层（`lib/why-facets.ts`）从导入数据派生分面计数、每告警分面映射、三份全局摘要；URL 分面状态（`lib/investigation-route.ts` 扩展）经 search param `?facet=` 持久化；展示层（`components/investigation/why-attribution.tsx`）为可折叠面板，分面过滤后把 `filteredAlerts` 传给现有 `InvestigationWorkbench`。

**Tech Stack:** TypeScript 5 / React 19 / Next.js 16 / zustand / vitest / jsdom。无新依赖。

## Global Constraints

- 无分号 + `"use client"` 风格（shadcn copy-paste 规范）。
- **zustand selector 稳定性**：先选稳定字段（`dbAssets` 整体），再本地派生；selector 里禁用 `?? []`。
- **诚实性原则**：所有 KPI/分布/排行纯函数派生；无法计算字段置 null/0；空表「无数据」；dbAssets null「导入后启用」；绝不硬编码演示值。
- 不新增页面（统一 4 页导航）；不引入新依赖。
- 全量测试保持绿：`cd frontend && npm run type-check && npm run lint && npx vitest run && npm run build`。
- 代码/commit 用英文；与用户交互用中文。
- worktree 隔离不可用——全部内联执行。

---
## 文件结构

| 文件 | 责任 | 动作 |
|---|---|---|
| `frontend/lib/why-facets.ts` | 分面纯函数 + 三份摘要 + `EMPTY_WHY` | 新建 |
| `frontend/lib/investigation-route.ts` | `parseFacetSelection` / `buildFacetHref` | 修改 |
| `frontend/components/investigation/why-attribution.tsx` | 可折叠归因面板 + `WhyFacetEmptyState` | 新建 |
| `frontend/components/investigation/investigations-page-client.tsx` | URL 分面状态 + 过滤接线 + 空态 | 修改 |
| `frontend/__tests__/why-facets.test.ts` | 纯函数测试（node） | 新建 |
| `frontend/__tests__/why-attribution.test.tsx` | 组件测试（jsdom） | 新建 |

接口依赖链：Task 1 产出 `WhyFacets`/`FacetSelection`/`EMPTY_WHY` → Task 2 依赖其类型 → Task 3 消费两者 → Task 4 接线。

---

### Task 1: 分面纯函数层 `lib/why-facets.ts`

**Files:**
- Create: `frontend/lib/why-facets.ts`
- Test: `frontend/__tests__/why-facets.test.ts`

**Interfaces:**
- Consumes: `types.ts` 的 `ContextPollutionEvent`/`PolicyEvaluation`/`NonLinkDiagnostic`/`Intent`/`CorrelationEvidence`/`ResponsibilityAlert`/`GraphData`；`investigation.ts` 的 `collectAlertEvidenceRefs`。
- Produces: `WhyFacetKind`/`FacetSelection`/`PollutionSummary`/`PolicySummary`/`DiagnosticSummary`/`WhyFacets`/`EMPTY_WHY`/`buildWhyFacets`/`filterAlertsByFacet`。

- [x] **Step 1: 写失败测试**

`frontend/__tests__/why-facets.test.ts`（node 环境，无需 pragma）：

```ts
import { describe, expect, it } from 'vitest';
import { buildWhyFacets, filterAlertsByFacet, EMPTY_WHY, type WhyFacetKind } from '@/lib/why-facets';
import type {
  ContextPollutionEvent, CorrelationEvidence, GraphData, Intent,
  NonLinkDiagnostic, PolicyEvaluation, ResponsibilityAlert,
} from '@/lib/types';

// ── 构造辅助 ──
const alert = (over: Partial<ResponsibilityAlert> & { alert_id: string }): ResponsibilityAlert => ({
  alert_id: over.alert_id,
  timestamp_ns: over.timestamp_ns ?? 1000,
  display_profile: over.display_profile ?? 'UnsafeByEngine',
  decision: over.decision ?? 'Unsafe',
  severity: over.severity ?? 'Warning',
  confidence: over.confidence ?? 0.85,
  authority: over.authority ?? 'Authoritative',
  reasoning: over.reasoning ?? '',
  title: over.title ?? '',
  summary: over.summary ?? '',
  action_hint: over.action_hint ?? null,
  primary_agent: over.primary_agent ?? { agent_id: 'agent-a', claimed_role: null },
  involved_agents: over.involved_agents ?? [{ agent_id: 'agent-a', claimed_role: null }],
  engine: over.engine ?? { rule_verdicts: [], pattern_verdicts: [], classification_score: 0, classification_route: '', score_breakdown: { rule_score: 0, pattern_score: 0, boundary_score: 0, evidence_score: 0, role_score: 0, repetition_score: 0, total: 0 } },
  timeline: over.timeline ?? { steps: [], total_duration_ms: 0 },
  graph_snapshot: over.graph_snapshot ?? { snapshot_id: 's', timestamp_ns: 1000, nodes: [], edges: [], metrics: { node_counts: {}, edge_counts: {}, agent_count: 0, cross_agent_edges: 0, delegation_depth: 0, taint_chains: 0, avg_confidence: 0 } },
  phase: over.phase ?? { Provisional: { timestamp_ns: 1000 } },
  provisional_ref: over.provisional_ref ?? null,
});

const pollution = (o: Partial<ContextPollutionEvent> & { pollution_id: number }): ContextPollutionEvent => ({
  pollution_id: o.pollution_id,
  source_agent_id: o.source_agent_id ?? 'agent-a',
  contaminated_artifact: o.contaminated_artifact ?? '/tmp/a',
  propagation_path: o.propagation_path ?? [],
  detected_at_ns: o.detected_at_ns ?? 1000,
  run_id: o.run_id ?? 'run-1',
});

const policy = (o: Partial<PolicyEvaluation> & { policy_id: string }): PolicyEvaluation => ({
  policy_id: o.policy_id,
  boundary: o.boundary ?? 'Capability',
  subject_agent_id: o.subject_agent_id ?? 'agent-a',
  // 关键：`??` 会把显式 null 也兜底成默认值（null ?? x → x），无法表达「判定缺失」。
  // 用 `in` 守卫区分「未传」与「显式 null」；`?? null` 归一化可选属性的 undefined（in 不窄化它）。
  judgment: 'judgment' in o ? (o.judgment ?? null) : { result: 'Compliant' },
  resource: o.resource ?? null,
  evidence_refs: o.evidence_refs ?? [],
  confidence: o.confidence ?? 0.85,
  reason: o.reason ?? '',
  run_id: o.run_id ?? 'run-1',
});

const diag = (o: Partial<NonLinkDiagnostic> & { diagnostic_id: string }): NonLinkDiagnostic => ({
  diagnostic_id: o.diagnostic_id,
  intent_id: o.intent_id ?? null,
  action_id: o.action_id ?? null,
  reason: o.reason ?? 'AmbiguousMatch',
  timestamp_ns: o.timestamp_ns ?? 1000,
  run_id: o.run_id ?? 'run-1',
});

const intent = (id: string, toolName: string): Intent => ({
  intent_id: id,
  process_id: { pid: 42, start_time_ns: 1000 },
  message_id: 'm-1',
  tool_name: toolName,
  tool_args: [],
  intent_class: 'SideEffect',
  parse_state: 'Complete',
  primary_matching_eligible: true,
  authority_source: 'http_response_tool_use',
  timestamp_ns: 1000,
});

const evidence = (id: string, intentId: string): CorrelationEvidence => ({
  evidence_id: id, intent_id: intentId, action_id: 'act-1', match_level: 'L1Exact',
  confidence: 0.9,
  matched_fields: { canonical_path: null, host: null, port: null, command: null, raw_args: [] },
  derivation_mode: 'Direct', phase: { Provisional: { timestamp_ns: 1000 } },
  timestamp_ns: 1000,
});

const graph: GraphData = { nodes: [], edges: [] };

describe('buildWhyFacets — 分面计数与映射', () => {
  it('pollution/policy 分面按 involved_agents 命中', () => {
    // 注意：alert() 的 primary_agent 默认 agent-a，a2 必须整体覆盖为 agent-b，
    // 否则 {agent-a(primary), agent-b} ∩ pollutionAgents{agent-a} ≠ ∅ → a2 也会命中 pollution。
    const alerts = [
      alert({ alert_id: 'a1' }),
      alert({
        alert_id: 'a2',
        primary_agent: { agent_id: 'agent-b', claimed_role: null },
        involved_agents: [{ agent_id: 'agent-b', claimed_role: null }],
      }),
    ];
    const pollutionEvents = [pollution({ pollution_id: 1, source_agent_id: 'agent-a' })];
    const policyEvaluations = [policy({ policy_id: 'p1', subject_agent_id: 'agent-b', judgment: { result: 'Violation' } })];
    const why = buildWhyFacets({ alerts, pollutionEvents, policyEvaluations, nonLinkDiagnostics: [], intents: [], evidence: [], graph });
    expect(why.facetCounts).toEqual({ pollution: 1, policy: 1, diagnostic: 0 });
    expect(why.alertFacets.a1).toEqual(['pollution']);
    expect(why.alertFacets.a2).toEqual(['policy']);
  });

  it('diagnostic 分面经 evidence 桥接命中（timeline evidence_ref → evidence → intent → 诊断）', () => {
    const alerts = [alert({
      alert_id: 'a1',
      timeline: { steps: [{ timestamp_ns: 900, event_type: 'EXEC', description: 'x', resource: null, evidence_ref: 'ev-1', agent_id: 'agent-a', operation: null, evidence_level: null, triggering_rule_ids: [], triggering_pattern_id: null, source_event_id: null }], total_duration_ms: 0 },
    })];
    const why = buildWhyFacets({
      alerts, pollutionEvents: [], policyEvaluations: [], nonLinkDiagnostics: [diag({ diagnostic_id: 'd1', intent_id: 'int-1' })],
      intents: [intent('int-1', 'Bash')], evidence: [evidence('ev-1', 'int-1')], graph,
    });
    expect(why.facetCounts).toEqual({ pollution: 0, policy: 0, diagnostic: 1 });
    expect(why.alertFacets.a1).toEqual(['diagnostic']);
  });

  it('无 involved_agents 的告警不命中污染/违规；证据桥接为空不命中失配', () => {
    const alerts = [alert({ alert_id: 'a1', primary_agent: { agent_id: '', claimed_role: null }, involved_agents: [] })];
    const pollutionEvents = [pollution({ pollution_id: 1, source_agent_id: 'agent-a' })];
    const why = buildWhyFacets({ alerts, pollutionEvents, policyEvaluations: [], nonLinkDiagnostics: [], intents: [], evidence: [], graph });
    expect(why.facetCounts).toEqual({ pollution: 0, policy: 0, diagnostic: 0 });
  });

  it('fixture 校准：复刻 db-sample.sqlite 三表真值（spec §十）', () => {
    // 两告警 involved 均 {agent-a}；timeline 无 evidence_ref → 证据桥接为空
    const alerts = [alert({ alert_id: 'alert-1' }), alert({ alert_id: 'alert-2' })];
    const pollutionEvents = [
      pollution({ pollution_id: 1, source_agent_id: 'agent-a', propagation_path: [] }),
      pollution({ pollution_id: 2, source_agent_id: 'agent-b', propagation_path: [] }),
      pollution({ pollution_id: 3, source_agent_id: 'agent-a', propagation_path: ['agent-a', 'agent-b', 'agent-c'] }),
    ];
    const policyEvaluations = [
      policy({ policy_id: 'p1', boundary: 'Capability', subject_agent_id: 'agent-a', judgment: { result: 'Compliant' } }),
      policy({ policy_id: 'p2', boundary: 'Capability', subject_agent_id: 'agent-a', judgment: { result: 'Compliant' } }),
      policy({ policy_id: 'p3', boundary: 'Capability', subject_agent_id: 'agent-a', judgment: { result: 'Compliant' } }),
      policy({ policy_id: 'p4', boundary: 'Capability', subject_agent_id: 'agent-a', judgment: { result: 'Compliant' } }),
      policy({ policy_id: 'p5', boundary: 'DataFlow', subject_agent_id: 'agent-b', judgment: { result: 'Allow' } }),
      policy({ policy_id: 'p6', boundary: 'Capability', subject_agent_id: 'agent-a', judgment: { result: 'Violation' } }),
    ];
    const nonLinkDiagnostics = [
      diag({ diagnostic_id: 'nld-1', intent_id: 'int-1', reason: 'BelowConfidenceCutoff' }),
      diag({ diagnostic_id: 'nld-2', intent_id: 'int-2', reason: 'BelowConfidenceCutoff' }),
      diag({ diagnostic_id: 'nld-3', intent_id: 'int-3', reason: 'NoArgumentMatch' }),
      diag({ diagnostic_id: 'nld-4', intent_id: 'int-2', reason: 'AmbiguousMatch' }),
    ];
    const why = buildWhyFacets({
      alerts, pollutionEvents, policyEvaluations, nonLinkDiagnostics,
      intents: [intent('int-1', 'Bash'), intent('int-2', 'Read'), intent('int-3', 'Glob')],
      evidence: [], graph,
    });
    expect(why.facetCounts).toEqual({ pollution: 2, policy: 2, diagnostic: 0 });
    expect(why.pollution.bySource).toEqual([{ agentId: 'agent-a', count: 2 }, { agentId: 'agent-b', count: 1 }]);
    expect(why.pollution.hubAgents).toEqual(['agent-a', 'agent-b']);
    expect(why.pollution.multiHopCount).toBe(1);
    expect(why.pollution.maxHops).toBe(3);
    expect(why.pollution.avgHops).toBe(1.0);
    // byJudgment 降序：Compliant×4 居首；Allow/Violation 平票按 result 升序
    expect(why.policy.byJudgment).toEqual([
      { result: 'Compliant', count: 4 }, { result: 'Allow', count: 1 }, { result: 'Violation', count: 1 },
    ]);
    expect(why.policy.violationCount).toBe(1);
    expect(why.policy.violationRate).toBeCloseTo(1 / 6, 5);
    // byReason 降序；byIntentId 按 count 降序、平票按 intentId 升序
    expect(why.diagnostic.byReason).toEqual([
      { reason: 'BelowConfidenceCutoff', count: 2 }, { reason: 'AmbiguousMatch', count: 1 }, { reason: 'NoArgumentMatch', count: 1 },
    ]);
    expect(why.diagnostic.byIntentId).toEqual([
      { intentId: 'int-2', toolName: 'Read', count: 2 },
      { intentId: 'int-1', toolName: 'Bash', count: 1 },
      { intentId: 'int-3', toolName: 'Glob', count: 1 },
    ]);
    expect(why.diagnostic.intentsAffected).toBe(3);
  });
});

describe('filterAlertsByFacet', () => {
  const alerts = [alert({ alert_id: 'a1' }), alert({ alert_id: 'a2' })];
  // 变量声明无上下文类型化 → 必须显式标注（否则推断为 string[]，不匹配 Record<string, WhyFacetKind[]>）
  const alertFacets: Record<string, WhyFacetKind[]> = { a1: ['pollution'] };

  it("'all' 原样返回", () => {
    expect(filterAlertsByFacet(alerts, alertFacets, 'all')).toHaveLength(2);
  });
  it('按分面过滤；无映射告警不命中', () => {
    expect(filterAlertsByFacet(alerts, alertFacets, 'pollution').map((a) => a.alert_id)).toEqual(['a1']);
    expect(filterAlertsByFacet(alerts, alertFacets, 'diagnostic')).toHaveLength(0);
  });
});

describe('buildWhyFacets — 摘要', () => {
  it('污染摘要：bySource 降序 / hubAgents（≥2 链）/ 多跳 / maxHops / avgHops', () => {
    const events = [
      pollution({ pollution_id: 1, source_agent_id: 'agent-a', propagation_path: [] }),
      pollution({ pollution_id: 2, source_agent_id: 'agent-b', propagation_path: [] }),
      pollution({ pollution_id: 3, source_agent_id: 'agent-a', propagation_path: ['agent-a', 'agent-b', 'agent-c'] }),
    ];
    const why = buildWhyFacets({ alerts: [], pollutionEvents: events, policyEvaluations: [], nonLinkDiagnostics: [], intents: [], evidence: [], graph });
    expect(why.pollution.total).toBe(3);
    expect(why.pollution.bySource).toEqual([{ agentId: 'agent-a', count: 2 }, { agentId: 'agent-b', count: 1 }]);
    expect(why.pollution.byArtifact).toEqual([{ artifact: '/tmp/a', count: 3 }]);
    expect(why.pollution.hubAgents).toEqual(['agent-a', 'agent-b']);
    expect(why.pollution.multiHopCount).toBe(1);
    expect(why.pollution.maxHops).toBe(3);
    expect(why.pollution.avgHops).toBe(1.0);
  });

  it('策略摘要：byJudgment（含 unknown）/ violationCount / violationRate / violationsByBoundary', () => {
    const evals = [
      policy({ policy_id: 'p1', boundary: 'Capability', judgment: { result: 'Violation' } }),
      policy({ policy_id: 'p2', boundary: 'Capability', judgment: { result: 'Compliant' } }),
      policy({ policy_id: 'p3', boundary: 'DataFlow', subject_agent_id: 'agent-b', judgment: { result: 'Allow' } }),
      policy({ policy_id: 'p4', boundary: 'DataFlow', subject_agent_id: 'agent-b', judgment: null }),
    ];
    const why = buildWhyFacets({ alerts: [], pollutionEvents: [], policyEvaluations: evals, nonLinkDiagnostics: [], intents: [], evidence: [], graph });
    expect(why.policy.total).toBe(4);
    // 平票（各 1）按 result 升序：'Allow'(A) < 'Compliant'(C) < 'Violation'(V) < 'unknown'(u)
    expect(why.policy.byJudgment).toEqual([
      { result: 'Allow', count: 1 }, { result: 'Compliant', count: 1 },
      { result: 'Violation', count: 1 }, { result: 'unknown', count: 1 },
    ]);
    expect(why.policy.violationCount).toBe(1);
    expect(why.policy.violationRate).toBeCloseTo(0.25, 5);
    expect(why.policy.violationsByBoundary).toEqual([
      { boundary: 'Capability', violationCount: 1, total: 2 },
      { boundary: 'DataFlow', violationCount: 0, total: 2 },
    ]);
  });

  it('诊断摘要：byReason 降序 / byIntentId 带 tool_name（缺失→null）/ intentsAffected（null 不计）', () => {
    const diags = [
      diag({ diagnostic_id: 'd1', intent_id: 'int-1', reason: 'BelowConfidenceCutoff' }),
      diag({ diagnostic_id: 'd2', intent_id: 'int-2', reason: 'BelowConfidenceCutoff' }),
      diag({ diagnostic_id: 'd3', intent_id: 'int-3', reason: 'NoArgumentMatch' }),
      diag({ diagnostic_id: 'd4', intent_id: null, reason: 'AmbiguousMatch' }),
    ];
    const why = buildWhyFacets({ alerts: [], pollutionEvents: [], policyEvaluations: [], nonLinkDiagnostics: diags, intents: [intent('int-1', 'Bash'), intent('int-3', 'Read')], evidence: [], graph });
    expect(why.diagnostic.total).toBe(4);
    // BelowConfidenceCutoff×2 居首；NoArgumentMatch/AmbiguousMatch 平票按 reason 升序（'AmbiguousMatch' < 'NoArgumentMatch'）
    expect(why.diagnostic.byReason).toEqual([
      { reason: 'BelowConfidenceCutoff', count: 2 }, { reason: 'AmbiguousMatch', count: 1 }, { reason: 'NoArgumentMatch', count: 1 },
    ]);
    expect(why.diagnostic.byIntentId).toEqual([
      { intentId: 'int-1', toolName: 'Bash', count: 1 },
      { intentId: 'int-2', toolName: null, count: 1 },
      { intentId: 'int-3', toolName: 'Read', count: 1 },
    ]);
    expect(why.diagnostic.intentsAffected).toBe(3);
  });

  it('空输入 → 全零不抛错', () => {
    const why = buildWhyFacets({ alerts: [], pollutionEvents: [], policyEvaluations: [], nonLinkDiagnostics: [], intents: [], evidence: [], graph });
    expect(why.facetCounts).toEqual({ pollution: 0, policy: 0, diagnostic: 0 });
    expect(why.pollution.avgHops).toBe(0);
    expect(why.policy.violationRate).toBe(0);
    expect(EMPTY_WHY).toEqual(why);
  });
});
```

- [x] **Step 2: 运行测试确认失败**

Run: `cd frontend && npx vitest run __tests__/why-facets.test.ts`
Expected: FAIL——`why-facets.ts` 不存在，模块解析错误。

- [x] **Step 3: 写最小实现**

`frontend/lib/why-facets.ts`：

```ts
import { collectAlertEvidenceRefs } from './investigation';
import type {
  ContextPollutionEvent, CorrelationEvidence, GraphData, Intent,
  NonLinkDiagnostic, PolicyEvaluation, ResponsibilityAlert,
} from './types';

export type WhyFacetKind = 'pollution' | 'policy' | 'diagnostic';
export type FacetSelection = 'all' | WhyFacetKind;

export interface PollutionSummary {
  total: number;
  bySource: Array<{ agentId: string; count: number }>;
  byArtifact: Array<{ artifact: string; count: number }>;
  hubAgents: string[];
  multiHopCount: number;
  maxHops: number;
  avgHops: number;
}

export interface PolicySummary {
  total: number;
  byJudgment: Array<{ result: string; count: number }>;
  byBoundary: Array<{ boundary: string; count: number }>;
  bySubject: Array<{ subjectAgentId: string; count: number }>;
  violationCount: number;
  violationRate: number;
  violationsByBoundary: Array<{ boundary: string; violationCount: number; total: number }>;
}

export interface DiagnosticSummary {
  total: number;
  byReason: Array<{ reason: string; count: number }>;
  byIntentId: Array<{ intentId: string; toolName: string | null; count: number }>;
  intentsAffected: number;
}

export interface WhyFacets {
  facetCounts: Record<WhyFacetKind, number>;
  alertFacets: Record<string, WhyFacetKind[]>;
  pollution: PollutionSummary;
  policy: PolicySummary;
  diagnostic: DiagnosticSummary;
}

function involvedAgentSet(alert: ResponsibilityAlert): Set<string> {
  const set = new Set<string>();
  if (alert.primary_agent?.agent_id) set.add(alert.primary_agent.agent_id);
  for (const a of alert.involved_agents ?? []) {
    if (a.agent_id) set.add(a.agent_id);
  }
  return set;
}

/** 归组计数 → 降序（count），同值按 key 升序。 */
function sortRows<T>(rows: T[], countOf: (r: T) => number, keyOf: (r: T) => string): T[] {
  return [...rows].sort((a, b) => countOf(b) - countOf(a) || (keyOf(a) < keyOf(b) ? -1 : keyOf(a) > keyOf(b) ? 1 : 0));
}

function summarizePollution(events: ContextPollutionEvent[]): PollutionSummary {
  const sourceMap = new Map<string, number>();
  const artifactMap = new Map<string, number>();
  const membershipMap = new Map<string, number>();
  let multiHopCount = 0;
  let maxHops = 0;
  let hopsSum = 0;

  for (const e of events) {
    const path = e.propagation_path ?? [];
    sourceMap.set(e.source_agent_id, (sourceMap.get(e.source_agent_id) ?? 0) + 1);
    artifactMap.set(e.contaminated_artifact, (artifactMap.get(e.contaminated_artifact) ?? 0) + 1);
    maxHops = Math.max(maxHops, path.length);
    hopsSum += path.length;
    if (path.length >= 2) multiHopCount += 1;
    const members = new Set([e.source_agent_id, ...path]);
    for (const m of members) membershipMap.set(m, (membershipMap.get(m) ?? 0) + 1);
  }

  const bySource = sortRows(
    Array.from(sourceMap, ([agentId, count]) => ({ agentId, count })),
    (r) => r.count, (r) => r.agentId,
  );
  const byArtifact = sortRows(
    Array.from(artifactMap, ([artifact, count]) => ({ artifact, count })),
    (r) => r.count, (r) => r.artifact,
  );
  const hubAgents = Array.from(membershipMap.entries())
    .filter(([, n]) => n >= 2)
    .map(([agentId]) => agentId)
    .sort();

  return {
    total: events.length,
    bySource,
    byArtifact,
    hubAgents,
    multiHopCount,
    maxHops,
    avgHops: events.length ? Number((hopsSum / events.length).toFixed(1)) : 0,
  };
}

function summarizePolicy(evals: PolicyEvaluation[]): PolicySummary {
  const judgmentMap = new Map<string, number>();
  const boundaryMap = new Map<string, number>();
  const subjectMap = new Map<string, number>();
  const boundaryStats = new Map<string, { violationCount: number; total: number }>();
  let violationCount = 0;

  for (const p of evals) {
    const result = p.judgment?.result ?? 'unknown';
    judgmentMap.set(result, (judgmentMap.get(result) ?? 0) + 1);
    boundaryMap.set(p.boundary, (boundaryMap.get(p.boundary) ?? 0) + 1);
    subjectMap.set(p.subject_agent_id, (subjectMap.get(p.subject_agent_id) ?? 0) + 1);
    const stats = boundaryStats.get(p.boundary) ?? { violationCount: 0, total: 0 };
    stats.total += 1;
    if (result === 'Violation') {
      stats.violationCount += 1;
      violationCount += 1;
    }
    boundaryStats.set(p.boundary, stats);
  }

  return {
    total: evals.length,
    byJudgment: sortRows(
      Array.from(judgmentMap, ([result, count]) => ({ result, count })),
      (r) => r.count, (r) => r.result,
    ),
    byBoundary: sortRows(
      Array.from(boundaryMap, ([boundary, count]) => ({ boundary, count })),
      (r) => r.count, (r) => r.boundary,
    ),
    bySubject: sortRows(
      Array.from(subjectMap, ([subjectAgentId, count]) => ({ subjectAgentId, count })),
      (r) => r.count, (r) => r.subjectAgentId,
    ),
    violationCount,
    violationRate: evals.length ? violationCount / evals.length : 0,
    violationsByBoundary: sortRows(
      Array.from(boundaryStats, ([boundary, stats]) => ({ boundary, ...stats })),
      (r) => r.violationCount, (r) => r.boundary,
    ),
  };
}

function summarizeDiagnostics(diags: NonLinkDiagnostic[], intents: Intent[]): DiagnosticSummary {
  const reasonMap = new Map<string, number>();
  const intentMap = new Map<string, number>();
  const intentById = new Map(intents.map((i) => [i.intent_id, i]));

  for (const d of diags) {
    reasonMap.set(d.reason, (reasonMap.get(d.reason) ?? 0) + 1);
    if (d.intent_id != null) {
      intentMap.set(d.intent_id, (intentMap.get(d.intent_id) ?? 0) + 1);
    }
  }

  return {
    total: diags.length,
    byReason: sortRows(
      Array.from(reasonMap, ([reason, count]) => ({ reason, count })),
      (r) => r.count, (r) => r.reason,
    ),
    byIntentId: sortRows(
      Array.from(intentMap, ([intentId, count]) => ({ intentId, toolName: intentById.get(intentId)?.tool_name ?? null, count })),
      (r) => r.count, (r) => r.intentId,
    ),
    intentsAffected: intentMap.size,
  };
}

export function buildWhyFacets(input: {
  alerts: ResponsibilityAlert[];
  pollutionEvents: ContextPollutionEvent[];
  policyEvaluations: PolicyEvaluation[];
  nonLinkDiagnostics: NonLinkDiagnostic[];
  intents: Intent[];
  evidence: CorrelationEvidence[];
  graph: GraphData;
}): WhyFacets {
  const { alerts, pollutionEvents, policyEvaluations, nonLinkDiagnostics, intents, evidence, graph } = input;

  const pollutionAgents = new Set<string>();
  for (const p of pollutionEvents) {
    pollutionAgents.add(p.source_agent_id);
    for (const m of p.propagation_path ?? []) pollutionAgents.add(m);
  }

  const violationSubjects = new Set<string>();
  for (const p of policyEvaluations) {
    if (p.judgment?.result === 'Violation') violationSubjects.add(p.subject_agent_id);
  }

  const diagnosedIntents = new Set<string>();
  for (const d of nonLinkDiagnostics) {
    if (d.intent_id != null) diagnosedIntents.add(d.intent_id);
  }

  const evidenceIntent = new Map(evidence.map((e) => [e.evidence_id, e.intent_id]));
  const alertFacets: Record<string, WhyFacetKind[]> = {};
  const facetCounts: Record<WhyFacetKind, number> = { pollution: 0, policy: 0, diagnostic: 0 };

  for (const alert of alerts) {
    const involved = involvedAgentSet(alert);
    const matches: WhyFacetKind[] = [];
    if (involved.size > 0 && Array.from(involved).some((a) => pollutionAgents.has(a))) {
      matches.push('pollution');
    }
    if (involved.size > 0 && Array.from(involved).some((a) => violationSubjects.has(a))) {
      matches.push('policy');
    }
    const linkedIntents = new Set<string>();
    for (const ref of collectAlertEvidenceRefs(alert, graph)) {
      const intentId = evidenceIntent.get(ref);
      if (intentId != null) linkedIntents.add(intentId);
    }
    if (linkedIntents.size > 0 && Array.from(linkedIntents).some((id) => diagnosedIntents.has(id))) {
      matches.push('diagnostic');
    }

    if (matches.length > 0) {
      alertFacets[alert.alert_id] = matches;
      for (const m of matches) facetCounts[m] += 1;
    }
  }

  return {
    facetCounts,
    alertFacets,
    pollution: summarizePollution(pollutionEvents),
    policy: summarizePolicy(policyEvaluations),
    diagnostic: summarizeDiagnostics(nonLinkDiagnostics, intents),
  };
}

export function filterAlertsByFacet(
  alerts: ResponsibilityAlert[],
  alertFacets: Record<string, WhyFacetKind[]>,
  facet: FacetSelection,
): ResponsibilityAlert[] {
  if (facet === 'all') return alerts;
  return alerts.filter((a) => (alertFacets[a.alert_id] ?? []).includes(facet));
}

export const EMPTY_WHY: WhyFacets = {
  facetCounts: { pollution: 0, policy: 0, diagnostic: 0 },
  alertFacets: {},
  pollution: { total: 0, bySource: [], byArtifact: [], hubAgents: [], multiHopCount: 0, maxHops: 0, avgHops: 0 },
  policy: { total: 0, byJudgment: [], byBoundary: [], bySubject: [], violationCount: 0, violationRate: 0, violationsByBoundary: [] },
  diagnostic: { total: 0, byReason: [], byIntentId: [], intentsAffected: 0 },
};
```

- [x] **Step 4: 运行测试确认通过**

Run: `cd frontend && npx vitest run __tests__/why-facets.test.ts`
Expected: PASS（10 个用例）。

- [x] **Step 5: 四门禁子集校验**

Run: `cd frontend && npx tsc --noEmit && npm run lint`
Expected: 无类型错误、lint 0 警告。

---

### Task 2: URL 分面状态 `investigation-route.ts`

**Files:**
- Modify: `frontend/lib/investigation-route.ts`
- Test: `frontend/__tests__/why-facets.test.ts`（追加 describe）

**Interfaces:**
- Consumes: Task 1 的 `FacetSelection`/`WhyFacetKind`。
- Produces: `FACET_PARAM`/`parseFacetSelection`/`buildFacetHref`。

- [x] **Step 1: 追加失败测试**（`__tests__/why-facets.test.ts` 末尾，同一文件、同 describe 风格）

```ts
import { parseFacetSelection, buildFacetHref, FACET_PARAM } from '@/lib/investigation-route';

describe('parseFacetSelection', () => {
  it('合法值解析', () => {
    expect(parseFacetSelection({ get: () => 'pollution' })).toBe('pollution');
    expect(parseFacetSelection({ get: () => 'policy' })).toBe('policy');
    expect(parseFacetSelection({ get: () => 'diagnostic' })).toBe('diagnostic');
  });
  it('非法/缺失 → all', () => {
    expect(parseFacetSelection({ get: () => 'nonsense' })).toBe('all');
    expect(parseFacetSelection({ get: () => null })).toBe('all');
    expect(parseFacetSelection(null)).toBe('all');
  });
});

describe('buildFacetHref', () => {
  it('写入 facet 并保留 hash', () => {
    expect(buildFacetHref('pollution', 'alertId=abc', '#alertId=abc')).toBe('/investigations?alertId=abc&facet=pollution#alertId=abc');
  });
  it("'all' 移除 facet", () => {
    expect(buildFacetHref('all', 'alertId=abc&facet=pollution', '#alertId=abc')).toBe('/investigations?alertId=abc#alertId=abc');
  });
  it('空 query 且 all → 仅 hash', () => {
    expect(buildFacetHref('all', '', '#alertId=abc')).toBe('/investigations#alertId=abc');
  });
  it('FACET_PARAM 常量 === "facet"', () => {
    expect(FACET_PARAM).toBe('facet');
  });
});
```

- [x] **Step 2: 运行测试确认失败**

Run: `cd frontend && npx vitest run __tests__/why-facets.test.ts`
Expected: FAIL——`parseFacetSelection`/`buildFacetHref` 未定义。

- [x] **Step 3: 实现**

`frontend/lib/investigation-route.ts` 追加（保留现有 `buildInvestigationHref`/`parseInvestigationAlertId`）：

```ts
import type { FacetSelection, WhyFacetKind } from './why-facets';

export const FACET_PARAM = 'facet';
const VALID_FACETS: WhyFacetKind[] = ['pollution', 'policy', 'diagnostic'];

export function parseFacetSelection(searchParams: { get(k: string): string | null } | null): FacetSelection {
  const raw = searchParams?.get(FACET_PARAM);
  return (VALID_FACETS as readonly string[]).includes(raw ?? '') ? (raw as WhyFacetKind) : 'all';
}

export function buildFacetHref(facet: FacetSelection, currentSearch: string, currentHash: string): string {
  const params = new URLSearchParams(currentSearch);
  if (facet === 'all') params.delete(FACET_PARAM);
  else params.set(FACET_PARAM, facet);
  const qs = params.toString();
  const hash = currentHash.startsWith('#') ? currentHash : currentHash ? `#${currentHash}` : '';
  return qs ? `/investigations?${qs}${hash}` : `/investigations${hash}`;
}
```

> 注：`currentSearch` 传入时带不带前导 `?` 均可（`URLSearchParams` 兼容）；`currentHash` 建议传 `window.location.hash`（含 `#`）。

- [x] **Step 4: 运行测试确认通过**

Run: `cd frontend && npx vitest run __tests__/why-facets.test.ts`
Expected: PASS（追加 5 个用例，累计 15）。

- [x] **Step 5: lint 校验**

Run: `cd frontend && npm run lint`
Expected: 0 警告。

---

### Task 3: 归因面板组件 `why-attribution.tsx`

**Files:**
- Create: `frontend/components/investigation/why-attribution.tsx`
- Test: `frontend/__tests__/why-attribution.test.tsx`

**Interfaces:**
- Consumes: Task 1 的 `WhyFacets`/`FacetSelection`；Task 2 的 `FACET_PARAM`（无需）。
- Produces: `WhyAttribution`（default export）、`WhyFacetEmptyState`（named export）。

- [x] **Step 1: 写失败测试**（`@vitest-environment jsdom` pragma 必须）

`frontend/__tests__/why-attribution.test.tsx`：

```tsx
// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import WhyAttribution, { WhyFacetEmptyState } from '@/components/investigation/why-attribution';
import { EMPTY_WHY } from '@/lib/why-facets';

afterEach(() => {
  cleanup();
});

describe('WhyAttribution', () => {
  const why = { ...EMPTY_WHY, facetCounts: { pollution: 2, policy: 0, diagnostic: 1 } };
  const onFacetChange = vi.fn();

  it('0 命中的分面 chip 禁用；生效 chip aria-pressed', () => {
    render(<WhyAttribution why={why} facet="all" onFacetChange={onFacetChange} alertCount={5} dbPresent />);
    const pollution = screen.getByRole('button', { name: /污染 2/ });
    const policy = screen.getByRole('button', { name: /违规 0/ });
    expect(pollution).toBeEnabled();
    expect(policy).toBeDisabled();
    expect(pollution).toHaveAttribute('aria-pressed', 'false');
  });

  it('生效分面 chip aria-pressed=true 且可一键清除', () => {
    render(<WhyAttribution why={why} facet="pollution" onFacetChange={onFacetChange} alertCount={5} dbPresent />);
    const active = screen.getByRole('button', { name: /污染 2/ });
    expect(active).toHaveAttribute('aria-pressed', 'true');
    const clear = screen.getByRole('button', { name: /清除分面/ });
    clear.click();
    expect(onFacetChange).toHaveBeenCalledWith('all');
  });

  it('dbPresent=false 时 chips 禁用且显示导入提示', () => {
    render(<WhyAttribution why={EMPTY_WHY} facet="all" onFacetChange={onFacetChange} alertCount={0} dbPresent={false} />);
    expect(screen.getByText(/导入 responsibility\.db 后启用/)).toBeTruthy();
  });
});

describe('WhyFacetEmptyState', () => {
  it('显示被过滤数与清除按钮', () => {
    const onClear = vi.fn();
    render(<WhyFacetEmptyState facet="pollution" filteredCount={3} onClear={onClear} />);
    expect(screen.getByText(/该分面无告警命中/)).toBeTruthy();
    expect(screen.getByText(/已过滤 3 条告警/)).toBeTruthy();
    screen.getByRole('button', { name: /清除分面/ }).click();
    expect(onClear).toHaveBeenCalled();
  });
});
```

- [x] **Step 2: 运行测试确认失败**

Run: `cd frontend && npx vitest run __tests__/why-attribution.test.tsx`
Expected: FAIL——组件不存在。

- [x] **Step 3: 写最小实现**

`frontend/components/investigation/why-attribution.tsx`：

```tsx
'use client';

import { useState } from 'react';
import { ChevronDown } from 'lucide-react';
import { SectionPanel } from '@/components/ui/linear/section-panel';
import type { FacetSelection, WhyFacets } from '@/lib/why-facets';

const FACET_LABELS: Record<FacetSelection, string> = {
  all: '全部', pollution: '污染', policy: '违规', diagnostic: '失配',
};
const FACET_ORDER: Array<Exclude<FacetSelection, 'all'>> = ['pollution', 'policy', 'diagnostic'];

const T = { xs: 9.5, sm: 10.5, md: 12 };

function Bar({ value, color, label }: { value: number; color: string; label: string }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <span style={{ fontSize: T.xs, color: 'var(--ln-text-muted)', minWidth: 64, flexShrink: 0 }}>{label}</span>
      <div style={{ flex: 1, height: 4, borderRadius: 999, background: 'var(--ln-surface-2)', overflow: 'hidden' }}>
        <div style={{ height: '100%', width: `${Math.min(100, value * 100)}%`, borderRadius: 999, background: color }} />
      </div>
    </div>
  );
}

function Chip({
  label, count, active, disabled, onClick,
}: { label: string; count: number; active: boolean; disabled: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      aria-pressed={active}
      disabled={disabled}
      onClick={onClick}
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 6,
        padding: '5px 12px', borderRadius: 999, cursor: disabled ? 'not-allowed' : 'pointer',
        fontFamily: 'var(--ln-mono)', fontSize: T.xs,
        border: active ? '1px solid var(--ln-accent)' : '1px solid var(--ln-border)',
        background: active ? 'var(--ln-accent-soft)' : 'var(--ln-surface-0)',
        color: disabled ? 'var(--ln-text-muted)' : active ? 'var(--ln-accent)' : 'var(--ln-text)',
        opacity: disabled ? 0.5 : 1,
        transition: 'all 120ms ease',
      }}
    >
      {label} <span style={{ color: disabled ? 'var(--ln-text-muted)' : 'var(--ln-text-secondary)' }}>{count}</span>
    </button>
  );
}

export default function WhyAttribution({
  why, facet, onFacetChange, alertCount, dbPresent,
}: {
  why: WhyFacets;
  facet: FacetSelection;
  onFacetChange: (f: FacetSelection) => void;
  alertCount: number;
  dbPresent: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const { pollution, policy, diagnostic } = why;

  return (
    <SectionPanel eyebrow="全局归因" title={dbPresent ? `${alertCount} 条告警 · ${why.facetCounts.pollution} 污染 / ${why.facetCounts.policy} 违规 / ${why.facetCounts.diagnostic} 失配` : '待 db 数据'}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        <div role="group" aria-label="告警分面" style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <Chip label="全部" count={alertCount} active={facet === 'all'} disabled={!dbPresent} onClick={() => onFacetChange('all')} />
          {FACET_ORDER.map((k) => (
            <Chip
              key={k}
              label={FACET_LABELS[k]}
              count={why.facetCounts[k]}
              active={facet === k}
              disabled={!dbPresent || why.facetCounts[k] === 0}
              onClick={() => onFacetChange(k)}
            />
          ))}
          <span aria-live="polite" style={{ fontSize: T.xs, color: 'var(--ln-text-muted)', marginLeft: 'auto' }}>
            {facet === 'all' ? `${alertCount} 条告警` : `${why.facetCounts[facet]} 条告警 · ${FACET_LABELS[facet]}分面`}
          </span>
          <button
            type="button"
            aria-expanded={expanded}
            onClick={() => setExpanded((v) => !v)}
            style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: T.xs, color: 'var(--ln-text-muted)', cursor: 'pointer', background: 'transparent', border: 'none', padding: '4px 6px' }}
          >
            归因<ChevronDown size={12} style={{ transform: expanded ? 'rotate(180deg)' : 'none', transition: 'transform 150ms ease' }} />
          </button>
        </div>

        {facet !== 'all' && (
          <button
            type="button"
            aria-label="清除分面"
            onClick={() => onFacetChange('all')}
            style={{ alignSelf: 'flex-start', display: 'inline-flex', alignItems: 'center', gap: 6, padding: '3px 10px', borderRadius: 999, fontSize: T.xs, cursor: 'pointer', border: '1px solid var(--ln-accent)', background: 'rgba(94,106,210,0.08)', color: 'var(--ln-accent)', fontFamily: 'var(--ln-mono)' }}
          >
            分面：{FACET_LABELS[facet]} ×
          </button>
        )}

        {!dbPresent ? (
          <div style={{ fontSize: T.sm, color: 'var(--ln-text-muted)' }}>导入 responsibility.db 后启用全局归因</div>
        ) : expanded ? (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 14 }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              <div style={{ fontSize: T.xs, fontWeight: 600, color: 'var(--ln-text-muted)', textTransform: 'uppercase', letterSpacing: '0.03em' }}>污染传播</div>
              {pollution.total === 0 ? (
                <div style={{ fontSize: T.xs, color: 'var(--ln-text-muted)' }}>无数据</div>
              ) : (
                <>
                  <div style={{ fontSize: T.sm, color: 'var(--ln-text)' }}>{pollution.total} 链 · 多跳 {pollution.multiHopCount} · 最长 {pollution.maxHops} · 平均 {pollution.avgHops}</div>
                  {pollution.bySource.slice(0, 4).map((s) => (
                    <div key={s.agentId} style={{ display: 'flex', justifyContent: 'space-between', gap: 8, fontSize: T.xs }}>
                      <span style={{ fontFamily: 'var(--ln-mono)', color: 'var(--ln-text-secondary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.agentId}</span>
                      <span style={{ color: '#f87171' }}>×{s.count}</span>
                    </div>
                  ))}
                  {pollution.hubAgents.length > 0 && (
                    <div style={{ display: 'flex', alignItems: 'center', gap: 4, flexWrap: 'wrap' }}>
                      <span style={{ fontSize: T.xs, color: 'var(--ln-text-muted)' }}>枢纽</span>
                      {pollution.hubAgents.map((h) => (
                        <span key={h} style={{ fontSize: T.xs, padding: '2px 6px', borderRadius: 999, background: 'rgba(248,113,113,0.12)', color: '#f87171', fontFamily: 'var(--ln-mono)' }}>{h}</span>
                      ))}
                    </div>
                  )}
                </>
              )}
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              <div style={{ fontSize: T.xs, fontWeight: 600, color: 'var(--ln-text-muted)', textTransform: 'uppercase', letterSpacing: '0.03em' }}>策略判定</div>
              {policy.total === 0 ? (
                <div style={{ fontSize: T.xs, color: 'var(--ln-text-muted)' }}>无数据</div>
              ) : (
                <>
                  <div style={{ fontSize: T.sm, color: 'var(--ln-text)' }}>{policy.total} 判定 · 违规 {policy.violationCount}（{Math.round(policy.violationRate * 100)}%）</div>
                  {policy.byJudgment.slice(0, 4).map((j) => (
                    <Bar key={j.result} label={j.result} value={policy.total ? j.count / policy.total : 0} color={j.result === 'Violation' ? '#f87171' : j.result === 'Allow' ? '#4ade80' : 'var(--ln-accent)'} />
                  ))}
                  {policy.violationsByBoundary.slice(0, 3).map((b) => (
                    <div key={b.boundary} style={{ display: 'flex', justifyContent: 'space-between', gap: 8, fontSize: T.xs }}>
                      <span style={{ fontFamily: 'var(--ln-mono)', color: 'var(--ln-text-secondary)' }}>{b.boundary}</span>
                      <span style={{ color: b.violationCount > 0 ? '#f87171' : 'var(--ln-text-muted)' }}>{b.violationCount}/{b.total}</span>
                    </div>
                  ))}
                </>
              )}
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              <div style={{ fontSize: T.xs, fontWeight: 600, color: 'var(--ln-text-muted)', textTransform: 'uppercase', letterSpacing: '0.03em' }}>失配诊断</div>
              {diagnostic.total === 0 ? (
                <div style={{ fontSize: T.xs, color: 'var(--ln-text-muted)' }}>无数据</div>
              ) : (
                <>
                  <div style={{ fontSize: T.sm, color: 'var(--ln-text)' }}>{diagnostic.total} 诊断 · 涉及 {diagnostic.intentsAffected} 意图</div>
                  {diagnostic.byReason.slice(0, 3).map((r) => (
                    <Bar key={r.reason} label={r.reason} value={diagnostic.total ? r.count / diagnostic.total : 0} color="#fbbf24" />
                  ))}
                  {diagnostic.byIntentId.slice(0, 3).map((i) => (
                    <div key={i.intentId} style={{ display: 'flex', justifyContent: 'space-between', gap: 8, fontSize: T.xs, overflow: 'hidden' }}>
                      <span style={{ fontFamily: 'var(--ln-mono)', color: 'var(--ln-text-secondary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{i.toolName ?? i.intentId}</span>
                      <span style={{ color: '#fbbf24' }}>×{i.count}</span>
                    </div>
                  ))}
                </>
              )}
            </div>
          </div>
        ) : null}
      </div>
    </SectionPanel>
  );
}

export function WhyFacetEmptyState({
  facet, filteredCount, onClear,
}: { facet: Exclude<FacetSelection, 'all'>; filteredCount: number; onClear: () => void }) {
  return (
    <div style={{ textAlign: 'center', padding: '36px 0', display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div style={{ fontSize: T.md, color: 'var(--ln-text)' }}>该分面无告警命中</div>
      <div style={{ fontSize: T.sm, color: 'var(--ln-text-muted)' }}>已过滤 {filteredCount} 条告警 · 分面：{FACET_LABELS[facet]}</div>
      <button
        type="button"
        onClick={onClear}
        style={{ alignSelf: 'center', marginTop: 8, padding: '6px 14px', borderRadius: 999, fontSize: T.sm, cursor: 'pointer', border: '1px solid var(--ln-accent)', background: 'var(--ln-accent-soft)', color: 'var(--ln-accent)', fontFamily: 'var(--ln-mono)' }}
      >
        清除分面
      </button>
    </div>
  );
}
```

- [x] **Step 4: 运行测试确认通过**

Run: `cd frontend && npx vitest run __tests__/why-attribution.test.tsx`
Expected: PASS（4 个用例）。

- [x] **Step 5: 四门禁子集校验**

Run: `cd frontend && npx tsc --noEmit && npm run lint`
Expected: 无类型错误、lint 0 警告（注意 `FACET_LABELS[facet]` 当 facet 为 `FacetSelection` 全量时类型安全）。

---

### Task 4: 页面接线 `investigations-page-client.tsx`

**Files:**
- Modify: `frontend/components/investigation/investigations-page-client.tsx`

**Interfaces:**
- Consumes: Task 1 `buildWhyFacets`/`filterAlertsByFacet`/`EMPTY_WHY`；Task 2 `parseFacetSelection`/`buildFacetHref`；Task 3 `WhyAttribution`/`WhyFacetEmptyState`。

- [x] **Step 1: 接线修改**

`investigations-page-client.tsx` 全量修改为：

```tsx
'use client';

import { useCallback, useEffect, useMemo, useRef } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { useGSAP } from '@gsap/react';
import gsap from 'gsap';
import InvestigationWorkbench from './investigation-workbench';
import AlertEmptyState from './alert-empty-state';
import WhyAttribution, { WhyFacetEmptyState } from './why-attribution';
import LoadingIndicator from '@/components/ui/loading-indicator';
import { useAppStore } from '@/lib/store';
import { useAlertModels } from '@/lib/use-alert-models';
import { buildInvestigationHref, parseInvestigationAlertId, parseFacetSelection, buildFacetHref } from '@/lib/investigation-route';
import { buildWhyFacets, filterAlertsByFacet, EMPTY_WHY } from '@/lib/why-facets';
import { useRunEvidence } from '@/lib/use-run-evidence';
import { useHashRouteState } from '@/lib/use-hash-route-state';

export default function InvestigationsPageClient() {
  const sp = useSearchParams();
  const router = useRouter();
  const activeRunId = useAppStore(s => s.activeRunId);
  const alerts = useAppStore(s => s.alerts);
  const graph = useAppStore(s => s.graph);
  const evidence = useAppStore(s => s.evidence);
  const intents = useAppStore(s => s.intents);
  const dbAssets = useAppStore(s => s.dbAssets);
  const dataSource = useAppStore(s => s.dataSource);
  const { selectedValue: hashAlertId, setRouteValue: setHashAlertId } = useHashRouteState({ searchParams: sp, parseValue: parseInvestigationAlertId, buildHref: buildInvestigationHref });
  const { evidenceItems, evidenceLoading, evidenceError } = useRunEvidence(activeRunId);

  // 分面：live（无 db）时强制 all，避免「残留 facet 参数 → 空态」
  const facet = parseFacetSelection(sp);
  const effectiveFacet = dbAssets ? facet : 'all';

  const why = useMemo(() => {
    if (!dbAssets) return EMPTY_WHY;
    return buildWhyFacets({
      alerts,
      pollutionEvents: dbAssets.pollutionEvents,
      policyEvaluations: dbAssets.policyEvaluations,
      nonLinkDiagnostics: dbAssets.nonLinkDiagnostics,
      intents,
      evidence,
      graph,
    });
  }, [dbAssets, alerts, intents, evidence, graph]);

  const filteredAlerts = useMemo(
    () => filterAlertsByFacet(alerts, why.alertFacets, effectiveFacet),
    [alerts, why, effectiveFacet],
  );

  const setFacet = useCallback((next: typeof facet) => {
    router.replace(buildFacetHref(next, sp.toString(), window.location.hash), { scroll: false });
  }, [router, sp]);

  const models = useAlertModels(filteredAlerts, graph, evidenceItems);
  const selected = useMemo(() => models.find(m => m.alert.alert_id === hashAlertId) ?? models[0] ?? null, [models, hashAlertId]);

  const handleSelectAlert = useCallback((alertId: string) => { setHashAlertId(alertId); }, [setHashAlertId]);

  useEffect(() => { if (!hashAlertId && models[0]) setHashAlertId(models[0].alert.alert_id); }, [models, hashAlertId, setHashAlertId]);

  const pageRef = useRef<HTMLDivElement>(null);
  useGSAP(() => {
    const tl = gsap.timeline({ defaults: { ease: 'power3.out' } });
    tl.fromTo('.ln-kpi-strip > *', { opacity: 0, y: 10 }, { opacity: 1, y: 0, duration: 0.4, stagger: 0.05, clearProps: 'transform' }, 0);
    tl.fromTo('.alert-list-btn', { opacity: 0, x: -8 }, { opacity: 1, x: 0, duration: 0.35, stagger: 0.04, clearProps: 'transform' }, 0.15);
  }, { scope: pageRef });

  return (
    <div className="ln-page__inner" ref={pageRef} style={{ maxWidth: 1300, margin: '0 auto', padding: '12px 14px' }}>
      <div style={{ marginBottom: 12 }}>
        <WhyAttribution
          why={why}
          facet={effectiveFacet}
          onFacetChange={setFacet}
          alertCount={alerts.length}
          dbPresent={!!dbAssets}
        />
      </div>
      {selected ? (
        selected.model ? (
          <InvestigationWorkbench alert={selected.alert} model={selected.model} activeRunId={activeRunId} availableAlerts={filteredAlerts} onSelectAlert={handleSelectAlert} evidenceLoading={evidenceLoading} evidenceError={evidenceError} />
        ) : (
          <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', minHeight: '60vh' }}>
            <LoadingIndicator size={44} label="正在构建调查模型…" />
          </div>
        )
      ) : effectiveFacet !== 'all' ? (
        <WhyFacetEmptyState facet={effectiveFacet} filteredCount={alerts.length - filteredAlerts.length} onClear={() => setFacet('all')} />
      ) : (
        <AlertEmptyState
          dataSource={dataSource}
          graphNodeCount={graph.nodes.length}
          evidenceCount={evidenceItems.length}
        />
      )}
    </div>
  );
}
```

- [x] **Step 2: 类型与 lint**

Run: `cd frontend && npx tsc --noEmit && npm run lint`
Expected: 无类型错误、lint 0 警告。

- [x] **Step 3: 现有测试回归**

Run: `cd frontend && npx vitest run`
Expected: 全绿（新增 19 个用例，总量 ≥ 312）。

> 注：`dbAssets` 初值（store 默认 null）→ `effectiveFacet='all'` → 未导入时行为与改造前一致；`WhyAttribution` 面板显示「待 db 数据」+ 提示行。

---

### Task 5: 四门禁全量 + 手动验证

- [x] **Step 1: 全量四门禁**

Run:
```bash
cd frontend && npm run type-check && npm run lint && npx vitest run && npm run build
```
Expected: type-check ✅ / lint 0 警告 ✅ / vitest 全绿 ✅ / build ✅。
**实际**：四门全绿；vitest **314** 通过（新增 21 用例）。另新增常驻集成测试 `why-facets-db-integration.test.ts`——真实 `db-sample.sqlite` → `loadDatabase` 导入管线 → `buildWhyFacets`，全量复现 spec §十真值（facetCounts {2,2,0} / bySource / hubAgents / violationRate 1/6 / byIntentId），端到端封堵「fixture 校准 vs 真实导入」断层。校准 fixture 的 int-3 toolName 由 'Write' 修正为 'Glob'（真实 db 值）。

- [x] **Step 2: 手动验证**（dev server 冒烟已完成；交互流程待人工 QA）

已完成：`npm run dev`（7395）→ `curl /investigations` HTTP 200、dev 日志无编译/运行时错误、标题正确。页面为 `dynamic(…, { ssr: false })` 客户端渲染，面板 DOM 仅水合后出现，curl 无法断言交互。
**待人工（浏览器）**：
1. 导入 `frontend/__tests__/fixtures/db-sample.sqlite`。
2. /investigations 顶部「全局归因」面板：`全部 N · 污染 / 违规 / 失配` chip 计数正确。
3. 点「污染」→ 队列收缩、URL 变 `?facet=pollution`、刷新保持；「清除分面」恢复。
4. 展开归因 → 三列摘要正确（污染源头/枢纽、策略判定/违规、失配 reason/意图）。
5. live 模式（无 db）→ 面板「导入 responsibility.db 后启用」，chips 禁用。

---

## Self-Review 记录

**Spec 覆盖**：
- 分面模型（§四）→ Task 1 `buildWhyFacets`/`filterAlertsByFacet` ✓
- 三摘要（§五）→ Task 1 `summarizePollution`/`summarizePolicy`/`summarizeDiagnostics` ✓
- URL 状态（§六）→ Task 2 ✓
- 组件（§七）→ Task 3 ✓
- 页面接线 + live effectiveFacet（§八）→ Task 4 ✓
- 测试矩阵（§十）→ Task 1（纯函数 10，含 fixture 校准真值）/ Task 2（路由 5）/ Task 3（组件 4）✓

**占位扫描**：无 TBD/TODO；每步含完整代码。

**类型一致性**：
- `WhyFacetKind`/`FacetSelection` 在 Task 1 定义，Task 2/3/4 引用一致。
- `WhyFacets` 字段 `facetCounts`/`alertFacets`/`pollution`/`policy`/`diagnostic` 在 Task 1 产出、Task 3/4 消费一致。
- `buildFacetHref(facet, currentSearch, currentHash)` 在 Task 2 定义、Task 4 以 `(next, sp.toString(), window.location.hash)` 调用一致。
- `WhyFacetEmptyState` 的 `facet` 类型为 `Exclude<FacetSelection,'all'>`，Task 4 传 `effectiveFacet`（已保证非 'all'）一致。

---

## M5 审查轮记录（pr-review-toolkit 双 agent + 裁决）

**派发**：`code-reviewer`（正确性/a11y/诚实性）+ `code-simplifier` + `silent-failure-hunter` + `type-design-analyzer`（后三者合并一轮）。全部基于磁盘当前版本复核。

### 修复项（均已实现 + 回归测试）

| 来源 | 级别 | 问题 | 修法 |
|------|------|------|------|
| code-reviewer | **Important** | diagnostic 分面经「全图边证据」过桥误报：`collectAlertEvidenceRefs` 在相关边集为空时回退收集**全图所有边**的 evidence_refs，使任何含诊断意图证据的图边都能把无关告警桥进 diagnostic 分面 | `collectAlertEvidenceRefs` 增加 `opts.skipAllEdgesFallback`（严格模式），`buildWhyFacets` 过桥时以 `{ skipAllEdgesFallback: true }` 调用；全图回退仅保留给证据面板的 store 告警联动（`investigation.test.ts` 既有测试锁定该行为）。新增红测试复现 → 绿 |
| silent-failure | **Important** | facet 切换后 URL hash 仍指向被过滤掉的告警 → UI 选中 models[0] 与深链脱节 | 页面加同步 effect：`hashAlertId` 不在过滤集内时回写为首条（`buildAlertHrefWithQuery` 保留分面 query，无循环） |
| 双 agent | Minor | `buildAlertHrefWithQuery` 保留陈旧 `?alertId=` query → 外部深链与 hash 冲突时 `parseInvestigationAlertId` 优先 query 把选中弹回旧告警 | 构建时 `params.delete('alertId')`；新增测试 |
| type-design | Minor | `filterAlertsByFacet` 接收独立 `alertFacets` 映射，可传入与分面计数不一致的数据 | 改为接收 `WhyFacets`，直接消费 `why.alertFacets`；更新页面调用与测试 |
| code-reviewer | Minor | fixture 注释「复刻 db-sample.sqlite 三表真值」过度承诺（byArtifact/byBoundary 等未复刻） | 注释改为「仅对已断言字段校准」，并指明全量真值以集成测试为准 |
| code-reviewer | Minor | 归因折叠钮缺 `aria-controls`；`!dbPresent` 时可点击但无可见效果 | 加 `aria-controls="why-attribution-body"`（正文容器常驻 + `hidden`），`!dbPresent` 时按钮 `disabled` |
| code-simplifier | Important | 三列重复网格（污染/策略/诊断）逐字重复 | 抽 `FacetColumn` 共享骨架 + `colWrap/colHead/mutedXs/summarySm/rowBase/monoLabel/hubChip` 样式常量；`monoLabel` 补 `minWidth:0` 使 ellipsis 真正生效（原为死代码） |

### 裁决为「接受」的 Minor

- **live 模式残留 `?facet=` 参数**：`effectiveFacet='all'` 已防死锁（无 db 时不进入分面空态）；修法需在渲染中做 router.replace 副作用，收益低于风险。接受。
- **`EMPTY_WHY` 可变共享常量**：手动 EMPTY_WHY + `toEqual(buildWhyFacets(空输入))` 是比 `Object.freeze` 更强的空态守卫（冻结不深、且会消解该断言价值）。接受。
- **chip 数（告警）vs 面板数（事件）单位差异**：面板摘要行已带单位（链/判定/诊断），spec §七按设计。接受。

### 最终复核

- 四门禁全绿：`tsc --noEmit` ✅ / `eslint .` 0 警告 ✅ / `vitest run` **319** 通过（53 文件）✅ / `npm run build` ✅。
- 真实 db 集成测试（db-sample.sqlite → buildWhyFacets）在严格过桥改动后仍通过 → 修复未破坏 spec §十真值。

### 浏览器 QA 收尾（Task #72/#73）

- **why-facets e2e 6/6 绿**（chromium + firefox，`e2e/why-facets.spec.ts`）：live 禁用态 / 真实 db 导入三列真值 / 分面交互+URL 深链+刷新保持+重新导入恢复。
- **真实 db（59MB）暴露 forensic-chain duplicate-key 真 bug**：同一 `(source_agent_id, contaminated_artifact)` 污染事件多条（db 实测 7 组，如 agent 污染 `..` 41 次），`key={`${source}-${artifact}`}` 碰撞 → React 警告且每 rIC 分块重渲染反复触发、加剧主线程 churn。按 policy_id 同一模式修复：`PollutionChain` 增 `pollution_id`，key 改用它。TDD 红→绿（+2 测试）。
- **visual-regression 基线更新**：M5 在 /investigations 顶部加归因面板，`investigations.png` 基线预期变更，`--update-snapshots` 更新 chromium+firefox。
- **全量 e2e 并行 flaky（预存、与 M5 正交）**：三次全量运行各失败一个不同时序敏感测试（db-import evDock 60s / a11y-navigation URL 5s），每次单独跑均绿。根因 = `fullyParallel` 多浏览器 + 59MB db 解析重负载下时序断言脆弱（db-import 测试注释已记录 rIC 饿死场景与 60s 缓解）。M5 面板实测 `buildWhyFacets` 全量仅 12ms，非成因。M5 自身测试在全量套件中稳定通过，不扩大修复范围。
- **最终四门禁**：`tsc` ✅ / `eslint .` 0 警告 ✅ / `vitest run` **321** 通过（53 文件，含新增 2 个 forensic 测试）✅ / `npm run build` ✅。
