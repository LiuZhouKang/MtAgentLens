import { describe, expect, it } from 'vitest';
import { buildWhyFacets, filterAlertsByFacet, EMPTY_WHY } from '@/lib/why-facets';
import { parseFacetSelection, buildFacetHref, buildAlertHrefWithQuery, FACET_PARAM } from '@/lib/investigation-route';
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

  it('无时间线证据且无追溯边的告警不被「全图边证据」过桥到 diagnostic 分面', () => {
    // 红：collectAlertEvidenceRefs 在相关边集为空时回退收集全图边 refs，使任何含
    // 诊断意图证据的图边都能把无关告警桥进 diagnostic 分面（虚高）。严格模式只采纳
    // 告警自身时间线证据 + 追溯边，全图回退仅保留给证据面板的 store 告警联动。
    const alerts = [alert({
      alert_id: 'a1',
      primary_agent: { agent_id: 'ghost', claimed_role: null },
      involved_agents: [{ agent_id: 'ghost', claimed_role: null }],
    })];
    const graphWithEdge: GraphData = {
      nodes: [],
      edges: [{ edge_id: 'e1', edge_type: 'ContextFlowsFrom', source: 'x', target: 'y', evidence_refs: ['ev-9'], confidence: 0.9, phase: { Provisional: { timestamp_ns: 1000 } } }],
    };
    const why = buildWhyFacets({
      alerts,
      pollutionEvents: [],
      policyEvaluations: [],
      nonLinkDiagnostics: [diag({ diagnostic_id: 'd1', intent_id: 'int-9' })],
      intents: [intent('int-9', 'Glob')],
      evidence: [evidence('ev-9', 'int-9')],
      graph: graphWithEdge,
    });
    expect(why.facetCounts.diagnostic).toBe(0);
    expect(why.alertFacets.a1).toBeUndefined();
  });

  it('无 involved_agents 的告警不命中污染/违规；证据桥接为空不命中失配', () => {
    const alerts = [alert({ alert_id: 'a1', primary_agent: { agent_id: '', claimed_role: null }, involved_agents: [] })];
    const pollutionEvents = [pollution({ pollution_id: 1, source_agent_id: 'agent-a' })];
    const why = buildWhyFacets({ alerts, pollutionEvents, policyEvaluations: [], nonLinkDiagnostics: [], intents: [], evidence: [], graph });
    expect(why.facetCounts).toEqual({ pollution: 0, policy: 0, diagnostic: 0 });
  });

  it('fixture 校准：已断言字段对齐 db-sample.sqlite 真值（spec §十）', () => {
    // 注意：此 fixture 仅对【下方已断言的字段】校准真实 db 真值 —— byArtifact /
    // byBoundary / bySubject / violationsByBoundary 的取值与 db-sample.sqlite 不同
    // （artifact 全为默认 /tmp/a、policy 分布简化），有意不在此断言；全量真值以
    // why-facets-db-integration.test.ts 为准，避免重复断言漂移。
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
  // a2 需覆盖默认 agent-a，否则 {agent-a} ∩ pollutionAgents{agent-a} → 也命中 pollution。
  const alerts = [
    alert({ alert_id: 'a1' }),
    alert({
      alert_id: 'a2',
      primary_agent: { agent_id: 'agent-b', claimed_role: null },
      involved_agents: [{ agent_id: 'agent-b', claimed_role: null }],
    }),
  ];
  // 过滤直接消费 why.alertFacets（与分面计数同源，杜绝传入不一致映射）。
  const why = buildWhyFacets({
    alerts, pollutionEvents: [pollution({ pollution_id: 1, source_agent_id: 'agent-a' })],
    policyEvaluations: [], nonLinkDiagnostics: [], intents: [], evidence: [], graph,
  });

  it("'all' 原样返回", () => {
    expect(filterAlertsByFacet(alerts, why, 'all')).toHaveLength(2);
  });
  it('按分面过滤；无映射告警不命中', () => {
    expect(filterAlertsByFacet(alerts, why, 'pollution').map((a) => a.alert_id)).toEqual(['a1']);
    expect(filterAlertsByFacet(alerts, why, 'diagnostic')).toHaveLength(0);
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

describe('buildAlertHrefWithQuery — 分面内点告警不丢 query', () => {
  it('保留当前 query 并注入 alertId hash', () => {
    expect(buildAlertHrefWithQuery('abc', 'facet=pollution')).toBe('/investigations?facet=pollution#alertId=abc');
  });
  it('无 query 时原样返回', () => {
    expect(buildAlertHrefWithQuery('abc', '')).toBe('/investigations#alertId=abc');
  });
  it('null 告警 → 仅 query', () => {
    expect(buildAlertHrefWithQuery(null, 'facet=policy')).toBe('/investigations?facet=policy');
  });
  it('剔除 query 中陈旧 alertId，避免 hash 选中被弹回旧告警', () => {
    expect(buildAlertHrefWithQuery('def', 'alertId=abc&facet=pollution')).toBe('/investigations?facet=pollution#alertId=def');
  });
});
