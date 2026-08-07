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

/** 按 keyOf 归组计数并降序；平票按 key 升序。返回 [{ key, count }]。 */
function countRows<T>(items: T[], keyOf: (item: T) => string): Array<{ key: string; count: number }> {
  const map = new Map<string, number>();
  for (const item of items) {
    const key = keyOf(item);
    map.set(key, (map.get(key) ?? 0) + 1);
  }
  return sortRows(
    Array.from(map, ([key, count]) => ({ key, count })),
    (r) => r.count, (r) => r.key,
  );
}

function summarizePollution(events: ContextPollutionEvent[]): PollutionSummary {
  const membershipMap = new Map<string, number>();
  let multiHopCount = 0;
  let maxHops = 0;
  let hopsSum = 0;

  for (const e of events) {
    const path = e.propagation_path ?? [];
    maxHops = Math.max(maxHops, path.length);
    hopsSum += path.length;
    if (path.length >= 2) multiHopCount += 1;
    const members = new Set([e.source_agent_id, ...path]);
    for (const m of members) membershipMap.set(m, (membershipMap.get(m) ?? 0) + 1);
  }

  const bySource = countRows(events, (e) => e.source_agent_id).map(({ key: agentId, count }) => ({ agentId, count }));
  const byArtifact = countRows(events, (e) => e.contaminated_artifact).map(({ key: artifact, count }) => ({ artifact, count }));
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
  const boundaryStats = new Map<string, { violationCount: number; total: number }>();
  let violationCount = 0;

  for (const p of evals) {
    const result = p.judgment?.result ?? 'unknown';
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
    byJudgment: countRows(evals, (p) => p.judgment?.result ?? 'unknown').map(({ key: result, count }) => ({ result, count })),
    byBoundary: countRows(evals, (p) => p.boundary).map(({ key: boundary, count }) => ({ boundary, count })),
    bySubject: countRows(evals, (p) => p.subject_agent_id).map(({ key: subjectAgentId, count }) => ({ subjectAgentId, count })),
    violationCount,
    violationRate: evals.length ? violationCount / evals.length : 0,
    violationsByBoundary: sortRows(
      Array.from(boundaryStats, ([boundary, stats]) => ({ boundary, ...stats })),
      (r) => r.violationCount, (r) => r.boundary,
    ),
  };
}

function summarizeDiagnostics(diags: NonLinkDiagnostic[], intents: Intent[]): DiagnosticSummary {
  const intentById = new Map(intents.map((i) => [i.intent_id, i]));
  const withIntent = (d: NonLinkDiagnostic): d is NonLinkDiagnostic & { intent_id: string } => d.intent_id != null;
  const intentRows = countRows(diags.filter(withIntent), (d) => d.intent_id);

  return {
    total: diags.length,
    byReason: countRows(diags, (d) => d.reason).map(({ key: reason, count }) => ({ reason, count })),
    byIntentId: intentRows.map(({ key: intentId, count }) => ({
      intentId, toolName: intentById.get(intentId)?.tool_name ?? null, count,
    })),
    intentsAffected: intentRows.length,
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
    // 严格过桥：只采纳告警自身证据（时间线 ref + 追溯边），不回退到全图边。
    for (const ref of collectAlertEvidenceRefs(alert, graph, { skipAllEdgesFallback: true })) {
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
  why: WhyFacets,
  facet: FacetSelection,
): ResponsibilityAlert[] {
  if (facet === 'all') return alerts;
  // 直接取 why.alertFacets，避免调用方传入与分面计数不一致的映射。
  return alerts.filter((a) => (why.alertFacets[a.alert_id] ?? []).includes(facet));
}

export const EMPTY_WHY: WhyFacets = {
  facetCounts: { pollution: 0, policy: 0, diagnostic: 0 },
  alertFacets: {},
  pollution: { total: 0, bySource: [], byArtifact: [], hubAgents: [], multiHopCount: 0, maxHops: 0, avgHops: 0 },
  policy: { total: 0, byJudgment: [], byBoundary: [], bySubject: [], violationCount: 0, violationRate: 0, violationsByBoundary: [] },
  diagnostic: { total: 0, byReason: [], byIntentId: [], intentsAffected: 0 },
};
