export type FuxiBenchAttackType = 'A' | 'B' | 'C' | 'benign';
export type FuxiBenchVerdict = 'unsafe' | 'review' | 'safe';
export type FuxiBenchSeverity = 'critical' | 'warning' | 'info';
export type FuxiBenchMode = 'single_agent' | 'agent_teams' | 'subagents' | 'unknown';

export type FuxiBenchBreakdown = {
  boundaryRule: number;
  pattern: number;
  roleAmplification: number;
  temporal: number;
  evidenceQuality: number;
  behavioral: number;
  calibrated: number;
  taintBoost: number;
  pollutionBoost: number;
};

export type FuxiBenchCase = {
  id: string;
  title: string;
  attackType: FuxiBenchAttackType;
  verdict: FuxiBenchVerdict;
  severity: FuxiBenchSeverity;
  mode: FuxiBenchMode;
  category: string;
  promptAgents: number | null;
  fixtureBytes: number;
  runtimeAgents: number;
  nodes: number;
  edges: number;
  evidence: number;
  pbc: number;
  taintedClaims: number;
  pollution: number;
  rustScore: number;
  rustRoute: string;
  riskScore: number;
  breakdown: FuxiBenchBreakdown;
};

export type FuxiBenchAtlas = {
  schema: string;
  generatedFrom: {
    evaluation: string;
    manifest: string;
  };
  totals: {
    scenarios: number;
    nodes: number;
    edges: number;
    evidence: number;
    pbc: number;
    unsafe: number;
    review: number;
    safe: number;
    byType: Record<string, number>;
    byMode: Record<string, number>;
  };
  cases: FuxiBenchCase[];
};

export const TYPE_LABELS: Record<FuxiBenchAttackType, string> = {
  A: 'Direct Injection',
  B: 'Fixture Injection',
  C: 'Multi-Agent',
  benign: 'Benign',
};

export const TYPE_SHORT_LABELS: Record<FuxiBenchAttackType, string> = {
  A: 'Type A',
  B: 'Type B',
  C: 'Type C',
  benign: 'Benign',
};

export const MODE_LABELS: Record<FuxiBenchMode, string> = {
  single_agent: 'Single Agent',
  agent_teams: 'Agent Teams',
  subagents: 'Subagents',
  unknown: 'Unknown',
};

export const TYPE_COLORS: Record<FuxiBenchAttackType, string> = {
  A: '#67e8f9',
  B: '#a78bfa',
  C: '#fb7185',
  benign: '#34d399',
};

export const VERDICT_COLORS: Record<FuxiBenchVerdict, string> = {
  unsafe: '#fb7185',
  review: '#fbbf24',
  safe: '#34d399',
};

export function formatCompactNumber(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 10_000) return `${Math.round(value / 1000)}k`;
  if (value >= 1000) return `${(value / 1000).toFixed(1)}k`;
  return String(value);
}

export function formatPercent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

export function rankCases(cases: FuxiBenchCase[]): FuxiBenchCase[] {
  return [...cases].sort(
    (left, right) =>
      right.riskScore - left.riskScore || right.pbc - left.pbc || right.evidence - left.evidence,
  );
}

export function getCaseTopology(caseItem: FuxiBenchCase) {
  if (caseItem.attackType === 'C' && caseItem.mode === 'subagents') {
    return {
      title: 'Delegation Tree',
      steps: [
        'Parent Agent',
        'Spawn Boundary',
        'Child Agent',
        'Context Propagation',
        'Evidence Bundle',
      ],
      emphasis: '多层进程血缘、委托传播和短命子代理是主视角。',
    };
  }

  if (caseItem.attackType === 'C') {
    return {
      title: 'Team Lanes',
      steps: ['Leader', 'Handoff', 'Worker Set', 'Shared Context', 'Boundary Crossing'],
      emphasis: '团队并行、交接文档、投票/令牌/角色漂移需要按泳道阅读。',
    };
  }

  if (caseItem.attackType === 'B') {
    return {
      title: 'Fixture Taint Chain',
      steps: ['Fixture File', 'Agent Process', 'Tool Call', 'Resource Access', 'Policy Boundary'],
      emphasis: '重点是外部文件到进程行为的污染传播。',
    };
  }

  if (caseItem.attackType === 'A') {
    return {
      title: 'Direct Intent Chain',
      steps: ['Task Prompt', 'Agent Intent', 'Tool / Shell', 'Sensitive Resource', 'Verdict'],
      emphasis: '直接 prompt 意图和实际 OS 行为需要并排验证。',
    };
  }

  return {
    title: 'Safe Baseline',
    steps: ['Task', 'Agent', 'Tool', 'Expected Resource', 'No Violation'],
    emphasis: '良性基线用于校准 PBC、evidence 和行为多样性噪声。',
  };
}
