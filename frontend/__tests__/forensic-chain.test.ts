import { describe, expect, it } from 'vitest';
import { buildForensicChain } from '@/lib/forensic-chain';
import type {
  ContextPollutionEvent,
  NonLinkDiagnostic,
  PolicyEvaluation,
  ResponsibilityAlert,
  ResponsibilityClaim,
} from '@/lib/types';

const baseAlert = (over: Partial<ResponsibilityAlert> = {}): ResponsibilityAlert =>
  ({
    alert_id: 'alert-1',
    timestamp_ns: 1100,
    display_profile: 'UnsafeByEngine',
    decision: 'Unsafe',
    severity: 'Warning',
    confidence: 0.85,
    authority: 'Authoritative',
    reasoning: 'r',
    title: 't',
    summary: 's',
    action_hint: null,
    primary_agent: { agent_id: 'agent-a', claimed_role: null },
    involved_agents: [
      { agent_id: 'agent-a', claimed_role: null },
      { agent_id: 'agent-b', claimed_role: null },
    ],
    engine: {
      rule_verdicts: [
        { rule_id: 'R-BOUNDARY-1', description: 'Boundary crossing', matched_resource: '/tmp/x.sh', llm_agreed: null, llm_comment: null },
      ],
      pattern_verdicts: [
        { template_id: 'PT-DELEG', match_confidence: 0.8, llm_agreed: null, llm_comment: null },
      ],
      classification_score: 0.9,
      classification_route: 'engine_unsafe',
      score_breakdown: { rule_score: 1, pattern_score: 0, boundary_score: 0.9, evidence_score: 0.8, role_score: 0, repetition_score: 0, total: 0.9 },
    },
    timeline: { steps: [], total_duration_ms: 0 },
    graph_snapshot: { snapshot_id: '', timestamp_ns: 0, nodes: [], edges: [], metrics: { node_counts: {}, edge_counts: {}, agent_count: 0, cross_agent_edges: 0, delegation_depth: 0, taint_chains: 0, avg_confidence: 0 } },
    phase: { Provisional: { timestamp_ns: 1100 } },
    provisional_ref: null,
    ...over,
  });

const pollution = (over: Partial<ContextPollutionEvent>): ContextPollutionEvent => ({
  pollution_id: 1, source_agent_id: 'agent-a', contaminated_artifact: '/tmp/leaked.txt',
  propagation_path: [], detected_at_ns: 1000, run_id: 'run-1', ...over,
});

const policy = (over: Partial<PolicyEvaluation>): PolicyEvaluation => ({
  policy_id: 'pol-1', boundary: 'Capability', subject_agent_id: 'agent-a',
  judgment: { result: 'Violation' }, resource: null, evidence_refs: ['ev-1'],
  confidence: 0.85, reason: '', run_id: 'run-1', ...over,
});

const diag = (over: Partial<NonLinkDiagnostic>): NonLinkDiagnostic => ({
  diagnostic_id: 'nld-1', intent_id: 'int-1', action_id: null,
  reason: 'BelowConfidenceCutoff', timestamp_ns: 1000, run_id: 'run-1', ...over,
});

const claim = (over: Partial<ResponsibilityClaim> = {}): ResponsibilityClaim => ({
  claim_id: 'claim-1', claim_type: 'TaintedContextUse', subject_agent_id: 'agent-a',
  policy_id: 'POLICY-1', boundary_id: 'BOUNDARY-1', claim_state: 'Inferred',
  severity: 'Critical', confidence: 0.9, evidence_refs: ['ev-1', 'ev-2'],
  competing_explanations: [], traceback_ref: null,
  phase: { Provisional: { timestamp_ns: 1000 } }, timestamp_ns: 1000, ...over,
});

describe('buildForensicChain', () => {
  it('四区组装：判定（rule/pattern）、污染（交集）、策略（交集）、诊断（intentIds）、claimRefs（证据交集）', () => {
    const chain = buildForensicChain({
      alert: baseAlert(),
      pollutionEvents: [pollution({}), pollution({ source_agent_id: 'agent-x' })],
      policyEvaluations: [policy({}), policy({ subject_agent_id: 'agent-x' })],
      nonLinkDiagnostics: [diag({}), diag({ intent_id: 'int-99' })],
      claims: [claim(), claim({ evidence_refs: ['ev-9'] })],
      intentIds: ['int-1'],
      alertEvidenceRefs: ['ev-1', 'ev-2'],
    });

    // 判定：rule 用 matched_resource 作 targets，reason=description；pattern 无 reason/targets
    expect(chain.verdicts).toHaveLength(2);
    expect(chain.verdicts[0]).toEqual({
      kind: 'rule', id: 'R-BOUNDARY-1', score: 1,
      reason: 'Boundary crossing', targets: ['/tmp/x.sh'],
    });
    expect(chain.verdicts[1]).toEqual({
      kind: 'pattern', id: 'PT-DELEG', score: 0.8, reason: '', targets: [],
    });

    // 污染：仅 involved_agents 交集内的 source 命中
    expect(chain.pollutionChains).toHaveLength(1);
    expect(chain.pollutionChains[0].source_agent_id).toBe('agent-a');
    // 污染行必须携带唯一 pollution_id：渲染层 React key 的稳定来源
    // （同一 agent 可能多次污染同一 artifact —— 真实 db 已证实 —— (source, artifact) 不可作 key）
    expect(chain.pollutionChains[0].pollution_id).toBe(1);
    // dt_ns 无可算来源 → null（不幻觉）
    expect(chain.pollutionChains[0].dt_ns).toBeNull();

    // 策略：仅 involved_agents 交集内的 subject 命中
    expect(chain.policyRows).toHaveLength(1);
    expect(chain.policyRows[0].boundary).toBe('Capability');
    expect(chain.policyRows[0].result).toBe('Violation');
    // policyRow 必须携带 policy_id：渲染层以它为 React key（boundary 可重复，不能作 key）
    expect(chain.policyRows[0].policy_id).toBe('pol-1');

    // 诊断：仅 intent_id ∈ intentIds
    expect(chain.diagnostics).toHaveLength(1);
    expect(chain.diagnostics[0].diagnostic_id).toBe('nld-1');

    // claimRefs：claims evidence_refs ∩ alertEvidenceRefs
    expect(chain.claimRefs).toEqual(['ev-1', 'ev-2']);
  });

  it('同一 (source, artifact) 多条污染事件：pollutionRow 带唯一 pollution_id（React key 可稳定，避免 key 碰撞告警）', () => {
    const chain = buildForensicChain({
      alert: baseAlert(),
      // 同 agent 同 artifact 污染两次（真实 db：agent 多次访问同一路径）→ (source, artifact) 组合 key 会碰撞
      pollutionEvents: [
        pollution({ pollution_id: 7 }),
        pollution({ pollution_id: 42 }),
      ],
      policyEvaluations: [],
      nonLinkDiagnostics: [],
      claims: [],
      intentIds: [],
      alertEvidenceRefs: [],
    });
    expect(chain.pollutionChains.map((r) => r.pollution_id)).toEqual([7, 42]);
  });

  it('同 boundary 多条判定：policyRow 带唯一 policy_id（React key 可稳定，避免 key 碰撞告警）', () => {
    const chain = buildForensicChain({
      alert: baseAlert(),
      pollutionEvents: [],
      policyEvaluations: [policy({ policy_id: 'pol-1' }), policy({ policy_id: 'pol-2' })],
      nonLinkDiagnostics: [],
      claims: [],
      intentIds: [],
      alertEvidenceRefs: [],
    });
    expect(chain.policyRows.map((r) => r.policy_id)).toEqual(['pol-1', 'pol-2']);
  });

  it('空源诚实：无 pollution / 无交集 → 空数组', () => {
    const chain = buildForensicChain({
      alert: baseAlert(),
      pollutionEvents: [],
      policyEvaluations: [],
      nonLinkDiagnostics: [],
      claims: [],
      intentIds: [],
      alertEvidenceRefs: [],
    });
    expect(chain.verdicts).toHaveLength(2); // 判定恒来自 alert.engine
    expect(chain.pollutionChains).toEqual([]);
    expect(chain.policyRows).toEqual([]);
    expect(chain.diagnostics).toEqual([]);
    expect(chain.claimRefs).toEqual([]);
  });

  it('pattern 无描述字段 → reason 空串；rule 无 matched_resource → targets 空数组', () => {
    const chain = buildForensicChain({
      alert: baseAlert({
        engine: {
          ...baseAlert().engine,
          rule_verdicts: [{ rule_id: 'R-X', description: '', matched_resource: '', llm_agreed: null, llm_comment: null }],
          pattern_verdicts: [],
        },
      }),
      pollutionEvents: [], policyEvaluations: [], nonLinkDiagnostics: [], claims: [],
      intentIds: [], alertEvidenceRefs: [],
    });
    expect(chain.verdicts[0].targets).toEqual([]);
  });
});
