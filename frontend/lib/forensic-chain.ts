import type {
  ContextPollutionEvent,
  NonLinkDiagnostic,
  PolicyEvaluation,
  ResponsibilityAlert,
  ResponsibilityClaim,
} from './types';

// ═══ 告警取证链（M1）——纯函数派生，无造数 ═══

export interface EngineVerdict {
  kind: 'rule' | 'pattern';
  id: string;                // rule_id / template_id
  score: number;
  reason: string;            // 从 verdict 结构尽力提取；无 → ''
  targets: string[];         // rule 的 matched_resource；PatternVerdict 无此字段 → 恒 []
}

export interface PollutionChain {
  pollution_id: number;      // 渲染层 React key（同 agent 可多次污染同 artifact，(source, artifact) 不可作 key）
  source_agent_id: string;
  contaminated_artifact: string;
  hops: string[];            // propagation_path；写者 → 中间 → 读者
  detected_at_ns: number;
  /** 表无逐 hop 时间戳，时延不可算 → 恒 null（不幻觉）。 */
  dt_ns: number | null;
}

export interface PolicyVerdictRow {
  policy_id: string;         // 渲染层 React key（boundary 可重复，不能作 key）
  boundary: string;
  result: string | null;     // 'Violation' | 'Allow' | judgment 缺省 → null
  confidence: number;
  reason: string;
}

export interface ForensicChain {
  verdicts: EngineVerdict[];             // alert.engine.rule_verdicts + pattern_verdicts
  pollutionChains: PollutionChain[];     // 按 involved_agents ∩ source_agent_id
  policyRows: PolicyVerdictRow[];        // 按 subject_agent_id ∈ involved_agents
  diagnostics: NonLinkDiagnostic[];      // 按 intent_id ∈ intentIds
  claimRefs: string[];                   // claims evidence_refs ∩ alertEvidenceRefs
}

/** involved_agents 的 agent_id 集合（含 primary）。弱关联匹配用。 */
function involvedAgentSet(alert: ResponsibilityAlert): Set<string> {
  const set = new Set<string>();
  if (alert.primary_agent?.agent_id) set.add(alert.primary_agent.agent_id);
  for (const agent of alert.involved_agents ?? []) {
    if (agent.agent_id) set.add(agent.agent_id);
  }
  return set;
}

export function buildForensicChain(input: {
  alert: ResponsibilityAlert;
  pollutionEvents: ContextPollutionEvent[];
  policyEvaluations: PolicyEvaluation[];
  nonLinkDiagnostics: NonLinkDiagnostic[];
  claims: ResponsibilityClaim[];
  /** alert 证据关联的 intent_id（组件层由 collectAlertEvidenceRefs + evidence 桥接）。 */
  intentIds: string[];
  /** alert 证据引用（组件层 collectAlertEvidenceRefs 计算）。 */
  alertEvidenceRefs: string[];
}): ForensicChain {
  const { alert, pollutionEvents, policyEvaluations, nonLinkDiagnostics, claims, intentIds, alertEvidenceRefs } = input;
  const involved = involvedAgentSet(alert);
  const intentIdSet = new Set(intentIds);
  const evidenceRefSet = new Set(alertEvidenceRefs);

  const verdicts: EngineVerdict[] = [
    ...(alert.engine?.rule_verdicts ?? []).map((v) => ({
      kind: 'rule' as const,
      id: v.rule_id,
      score: 1, // 命中即 1（rule_verdicts 无 score 字段）
      reason: v.description ?? '',
      targets: v.matched_resource ? [v.matched_resource] : [],
    })),
    ...(alert.engine?.pattern_verdicts ?? []).map((v) => ({
      kind: 'pattern' as const,
      id: v.template_id,
      score: v.match_confidence ?? 0,
      reason: '', // PatternVerdict 无描述字段
      targets: [],
    })),
  ];

  const pollutionChains: PollutionChain[] = pollutionEvents
    .filter((p) => involved.has(p.source_agent_id))
    .map((p) => ({
      pollution_id: p.pollution_id,
      source_agent_id: p.source_agent_id,
      contaminated_artifact: p.contaminated_artifact,
      hops: p.propagation_path,
      detected_at_ns: p.detected_at_ns,
      dt_ns: null,
    }));

  const policyRows: PolicyVerdictRow[] = policyEvaluations
    .filter((p) => involved.has(p.subject_agent_id))
    .map((p) => ({
      policy_id: p.policy_id,
      boundary: p.boundary,
      result: p.judgment?.result ?? null,
      confidence: p.confidence,
      reason: p.reason,
    }));

  const diagnostics = nonLinkDiagnostics.filter((d) => d.intent_id != null && intentIdSet.has(d.intent_id));

  const claimRefSet = new Set<string>();
  for (const c of claims) {
    for (const ref of c.evidence_refs ?? []) {
      if (evidenceRefSet.has(ref)) claimRefSet.add(ref);
    }
  }

  return {
    verdicts,
    pollutionChains,
    policyRows,
    diagnostics,
    claimRefs: Array.from(claimRefSet).sort(),
  };
}
