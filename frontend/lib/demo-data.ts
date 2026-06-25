// Demo data simulating a real AgentSight monitoring session.
// Used when /api/demo backend endpoint is unavailable.

import type { CorrelationEvidence, ResponsibilityAlert, GraphData, GraphSnapshot } from './types';

// Keep demo fixtures deterministic across SSR and CSR to avoid hydration drift.
const now = Date.parse('2026-06-20T09:30:00.000Z') * 1_000_000;

function snapshot(nodes: any[], edges: any[]): GraphSnapshot {
  return {
    snapshot_id: 'demo-snap-1',
    timestamp_ns: now - 5_000_000_000,
    nodes,
    edges,
    metrics: {
      node_counts: { Agent: 3, Resource: 4, PolicyBoundary: 2 },
      edge_counts: { IntentCausesAction: 3, AgentDelegatesTo: 1, ContextFlowsFrom: 1, TaintPropagatesTo: 1, PolicyBoundaryCrossing: 1, CompetingExplanation: 0 },
      agent_count: 3,
      cross_agent_edges: 1,
      delegation_depth: 1,
      taint_chains: 1,
      avg_confidence: 0.73,
    },
  };
}

const demoNodes = [
  { node_id: 'agent-frontend', node_type: 'Agent' as const, agent_id: 'agent-frontend', resource: null, evidence_refs: ['ev-1'], timestamp_ns: now - 30_000_000_000, phase: { Authoritative: { timestamp_ns: now - 30_000_000_000, overrides: null } } },
  { node_id: 'agent-security', node_type: 'Agent' as const, agent_id: 'agent-security', resource: null, evidence_refs: ['ev-4'], timestamp_ns: now - 20_000_000_000, phase: { Authoritative: { timestamp_ns: now - 20_000_000_000, overrides: null } } },
  { node_id: 'agent-builder', node_type: 'Agent' as const, agent_id: 'agent-builder', resource: null, evidence_refs: [], timestamp_ns: now - 25_000_000_000, phase: { Authoritative: { timestamp_ns: now - 25_000_000_000, overrides: null } } },
  { node_id: 'res-components', node_type: 'Resource' as const, agent_id: null, resource: { File: { canonical_path: '/src/components/Button.tsx' } }, evidence_refs: ['ev-1'], timestamp_ns: now - 28_000_000_000, phase: { Authoritative: { timestamp_ns: now - 28_000_000_000, overrides: null } } },
  { node_id: 'res-shadow', node_type: 'Resource' as const, agent_id: null, resource: { File: { canonical_path: '/etc/shadow' } }, evidence_refs: ['ev-3', 'ev-4'], timestamp_ns: now - 15_000_000_000, phase: { Authoritative: { timestamp_ns: now - 15_000_000_000, overrides: null } } },
  { node_id: 'res-package', node_type: 'Resource' as const, agent_id: null, resource: { File: { canonical_path: '/package.json' } }, evidence_refs: ['ev-2'], timestamp_ns: now - 22_000_000_000, phase: { Authoritative: { timestamp_ns: now - 22_000_000_000, overrides: null } } },
  { node_id: 'res-api', node_type: 'Resource' as const, agent_id: null, resource: { Network: { host: 'api.anthropic.com', port: 443, scheme: 'https' } }, evidence_refs: ['ev-1'], timestamp_ns: now - 29_000_000_000, phase: { Authoritative: { timestamp_ns: now - 29_000_000_000, overrides: null } } },
  { node_id: 'boundary-workspace', node_type: 'PolicyBoundary' as const, agent_id: null, resource: null, evidence_refs: [], timestamp_ns: now - 30_000_000_000, phase: { Authoritative: { timestamp_ns: now - 30_000_000_000, overrides: null } } },
  { node_id: 'boundary-system', node_type: 'PolicyBoundary' as const, agent_id: null, resource: null, evidence_refs: ['ev-3'], timestamp_ns: now - 15_000_000_000, phase: { Authoritative: { timestamp_ns: now - 15_000_000_000, overrides: null } } },
];

const demoEdges = [
  { edge_id: 'e-intent-1', edge_type: 'IntentCausesAction' as const, source: 'agent-frontend', target: 'res-components', evidence_refs: ['ev-1'], confidence: 0.95, phase: { Authoritative: { timestamp_ns: now - 28_000_000_000, overrides: null } } },
  { edge_id: 'e-intent-2', edge_type: 'IntentCausesAction' as const, source: 'agent-frontend', target: 'res-package', evidence_refs: ['ev-2'], confidence: 0.88, phase: { Authoritative: { timestamp_ns: now - 22_000_000_000, overrides: null } } },
  { edge_id: 'e-delegate', edge_type: 'AgentDelegatesTo' as const, source: 'agent-frontend', target: 'agent-security', evidence_refs: ['ev-4'], confidence: 0.72, phase: { Authoritative: { timestamp_ns: now - 20_000_000_000, overrides: null } } },
  { edge_id: 'e-taint', edge_type: 'TaintPropagatesTo' as const, source: 'agent-security', target: 'res-shadow', evidence_refs: ['ev-4'], confidence: 0.65, phase: { Authoritative: { timestamp_ns: now - 15_000_000_000, overrides: null } } },
  { edge_id: 'e-boundary', edge_type: 'PolicyBoundaryCrossing' as const, source: 'agent-security', target: 'boundary-system', evidence_refs: ['ev-3'], confidence: 0.89, phase: { Authoritative: { timestamp_ns: now - 15_000_000_000, overrides: null } } },
  { edge_id: 'e-context', edge_type: 'ContextFlowsFrom' as const, source: 'agent-frontend', target: 'agent-builder', evidence_refs: [], confidence: 0.55, phase: { Authoritative: { timestamp_ns: now - 24_000_000_000, overrides: null } } },
  { edge_id: 'e-intent-3', edge_type: 'IntentCausesAction' as const, source: 'agent-frontend', target: 'res-api', evidence_refs: ['ev-1'], confidence: 0.91, phase: { Authoritative: { timestamp_ns: now - 29_000_000_000, overrides: null } } },
];

export const demoGraph: GraphData = {
  nodes: demoNodes,
  edges: demoEdges,
};

export const demoEvidence: CorrelationEvidence[] = [
  {
    evidence_id: 'ev-1',
    intent_id: 'intent-frontend-read',
    action_id: 'action-frontend-read',
    match_level: 'L1Exact',
    confidence: 0.95,
    matched_fields: {
      canonical_path: '/src/components/Button.tsx',
      host: null,
      port: null,
      command: 'read Button.tsx',
      raw_args: ['Button.tsx'],
    },
    derivation_mode: 'demo',
    phase: { Authoritative: { timestamp_ns: now - 29_000_000_000, overrides: null } },
    timestamp_ns: now - 29_000_000_000,
  },
  {
    evidence_id: 'ev-2',
    intent_id: 'intent-frontend-write',
    action_id: 'action-frontend-write',
    match_level: 'L1Exact',
    confidence: 0.88,
    matched_fields: {
      canonical_path: '/package.json',
      host: null,
      port: null,
      command: 'write package.json',
      raw_args: ['package.json'],
    },
    derivation_mode: 'demo',
    phase: { Authoritative: { timestamp_ns: now - 22_000_000_000, overrides: null } },
    timestamp_ns: now - 22_000_000_000,
  },
  {
    evidence_id: 'ev-3',
    intent_id: 'intent-shadow-read',
    action_id: 'action-shadow-read',
    match_level: 'L1Exact',
    confidence: 0.91,
    matched_fields: {
      canonical_path: '/etc/shadow',
      host: null,
      port: null,
      command: 'cat /etc/shadow',
      raw_args: ['/etc/shadow'],
    },
    derivation_mode: 'demo',
    phase: { Authoritative: { timestamp_ns: now - 15_000_000_000, overrides: null } },
    timestamp_ns: now - 15_000_000_000,
  },
  {
    evidence_id: 'ev-4',
    intent_id: 'intent-delegation-security',
    action_id: 'action-delegation-security',
    match_level: 'L1Exact',
    confidence: 0.77,
    matched_fields: {
      canonical_path: null,
      host: null,
      port: null,
      command: 'delegate security-auditor',
      raw_args: ['security-auditor'],
    },
    derivation_mode: 'demo',
    phase: { Authoritative: { timestamp_ns: now - 20_000_000_000, overrides: null } },
    timestamp_ns: now - 20_000_000_000,
  },
];

export const demoAlerts: ResponsibilityAlert[] = [
  {
    alert_id: 'demo-alert-1',
    timestamp_ns: now - 15_000_000_000,
    display_profile: 'ObserverJudged',
    decision: 'Unsafe',
    severity: 'Critical',
    confidence: 0.89,
    authority: 'Authoritative',
    reasoning: 'security-auditor agent accessed /etc/shadow which is outside its workspace boundary. The agent claimed role "security-auditor" but its actual process identity does not match. This is a clear policy boundary violation.',
    title: 'Agent "security-auditor" 尝试权限提升',
    summary: 'frontend-expert 将任务委派给 security-auditor，后者读取了 /etc/shadow 密码文件。Agent 声称的角色与实际身份不一致，目标文件超出其合法权限范围。',
    action_hint: '验证 frontend-expert 的身份绑定，检查为何被授予 security-auditor 角色',
    primary_agent: { agent_id: 'agent-security', claimed_role: 'security-auditor' },
    involved_agents: [
      { agent_id: 'agent-security', claimed_role: 'security-auditor' },
      { agent_id: 'agent-frontend', claimed_role: 'frontend-developer' },
    ],
    engine: {
      rule_verdicts: [
        { rule_id: 'unsafe.shadow', description: '密码文件被非特权角色读取', matched_resource: '/etc/shadow', llm_agreed: true, llm_comment: null },
        { rule_id: 'credential_pattern', description: '路径检测到凭据模式', matched_resource: '/etc/shadow', llm_agreed: false, llm_comment: '路径巧合，非真实凭据泄漏' },
      ],
      pattern_verdicts: [
        { template_id: 'privilege_escalation', match_confidence: 0.85, llm_agreed: true, llm_comment: null },
      ],
      classification_score: 0.78,
      classification_route: 'observer',
      score_breakdown: { rule_score: 0.35, pattern_score: 0.26, boundary_score: 0.12, evidence_score: 0.0, role_score: 0.0, repetition_score: 0.0, total: 0.78 },
    },
    timeline: {
      steps: [
        { timestamp_ns: now - 25_000_000_000, event_type: 'agent_start', description: 'frontend-expert 声称角色 "frontend-developer"', resource: null, evidence_ref: null, agent_id: 'agent-frontend', operation: null, evidence_level: null, triggering_rule_ids: [], triggering_pattern_id: null, source_event_id: null },
        { timestamp_ns: now - 20_000_000_000, event_type: 'delegation', description: 'frontend-expert 委派任务给 security-auditor', resource: null, evidence_ref: 'ev-4', agent_id: 'agent-frontend', operation: 'Delegation', evidence_level: 'L1Exact', triggering_rule_ids: [], triggering_pattern_id: null, source_event_id: 'evt-001' },
        { timestamp_ns: now - 18_000_000_000, event_type: 'role_claim', description: 'security-auditor 声称角色 "security-auditor"', resource: null, evidence_ref: null, agent_id: 'agent-security', operation: null, evidence_level: null, triggering_rule_ids: [], triggering_pattern_id: null, source_event_id: null },
        { timestamp_ns: now - 15_000_000_000, event_type: 'file_read', description: 'security-auditor 读取 /etc/shadow', resource: '/etc/shadow', evidence_ref: 'ev-3', agent_id: 'agent-security', operation: 'FileRead', evidence_level: 'L1Exact', triggering_rule_ids: ['unsafe.shadow', 'credential_pattern'], triggering_pattern_id: 'privilege_escalation', source_event_id: 'evt-4a2c' },
        { timestamp_ns: now - 14_500_000_000, event_type: 'boundary_cross', description: '策略边界突破', resource: 'boundary-system', evidence_ref: 'ev-3', agent_id: 'agent-security', operation: null, evidence_level: 'L1Exact', triggering_rule_ids: [], triggering_pattern_id: 'privilege_escalation', source_event_id: 'evt-8f1b' },
      ],
      total_duration_ms: 10500,
    },
    graph_snapshot: snapshot(demoNodes, demoEdges),
    phase: { Authoritative: { timestamp_ns: now - 10_000_000_000, overrides: null } },
    provisional_ref: null,
  },
  {
    alert_id: 'demo-alert-2',
    timestamp_ns: now - 28_000_000_000,
    display_profile: 'SafeBaseline',
    decision: 'Safe',
    severity: 'Info',
    confidence: 0.95,
    authority: 'Authoritative',
    reasoning: '',
    title: 'Agent "frontend-expert" 正常操作',
    summary: 'frontend-expert 对项目内文件执行了正常读写操作，未触发任何不安全规则，无策略边界突破。',
    action_hint: null,
    primary_agent: { agent_id: 'agent-frontend', claimed_role: 'frontend-developer' },
    involved_agents: [{ agent_id: 'agent-frontend', claimed_role: 'frontend-developer' }],
    engine: {
      rule_verdicts: [],
      pattern_verdicts: [],
      classification_score: 0.05,
      classification_route: 'safe',
      score_breakdown: { rule_score: 0.0, pattern_score: 0.0, boundary_score: 0.0, evidence_score: 0.0, role_score: 0.0, repetition_score: 0.0, total: 0.05 },
    },
    timeline: {
      steps: [
        { timestamp_ns: now - 30_000_000_000, event_type: 'file_read', description: '读取 src/components/Button.tsx', resource: 'src/components/Button.tsx', evidence_ref: 'ev-1', agent_id: 'agent-frontend', operation: 'FileRead', evidence_level: 'L1Exact', triggering_rule_ids: [], triggering_pattern_id: null, source_event_id: 'evt-0001' },
        { timestamp_ns: now - 28_000_000_000, event_type: 'file_write', description: '写入 src/components/Button.tsx', resource: 'src/components/Button.tsx', evidence_ref: 'ev-2', agent_id: 'agent-frontend', operation: 'FileWrite', evidence_level: 'L1Exact', triggering_rule_ids: [], triggering_pattern_id: null, source_event_id: 'evt-0002' },
      ],
      total_duration_ms: 2000,
    },
    graph_snapshot: snapshot(demoNodes, demoEdges),
    phase: { Authoritative: { timestamp_ns: now - 28_000_000_000, overrides: null } },
    provisional_ref: null,
  },
];
