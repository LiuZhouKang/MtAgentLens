// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import type { ResponsibilityClaim, ResponsibilityTraceback } from '@/lib/types';
import ClaimList from '@/components/investigation/claim-list';

const claims: ResponsibilityClaim[] = [
  {
    claim_id: 'claim-1',
    claim_type: 'TaintedContextUse',
    subject_agent_id: 'agent-a',
    policy_id: 'POLICY-1',
    boundary_id: 'BOUNDARY-1',
    claim_state: 'Inferred',
    severity: 'Critical',
    confidence: 0.9,
    evidence_refs: ['ev-1'],
    competing_explanations: [],
    traceback_ref: null,
    phase: { Provisional: { timestamp_ns: 1000 } },
    timestamp_ns: 1000,
  },
  {
    claim_id: 'claim-2',
    claim_type: 'PolicyViolation',
    subject_agent_id: 'agent-a',
    policy_id: 'POLICY-2',
    boundary_id: null,
    claim_state: 'Confirmed',
    severity: 'Warning',
    confidence: 0.7,
    evidence_refs: [],
    competing_explanations: [
      {
        label: 'Alt path',
        evidence_refs: [],
        rule_id: 'R-ALT',
        rationale: 'Alternative',
        aligned_pattern: null,
      },
    ],
    traceback_ref: null,
    phase: { Authoritative: { timestamp_ns: 1200, overrides: null } },
    timestamp_ns: 1200,
  },
];

const tracebacks: ResponsibilityTraceback[] = [
  {
    traceback_id: 'tb-1',
    claim_id: 'claim-1',
    chain_node_ids: [],
    chain_edge_ids: [],
    root_cause_node_id: 'root',
    explanation_summary: [
      'Traceback for claim claim-1: Tainted Context Use (confidence 90%)',
      'Causal chain:',
      '  [start] Agent(agent-a)',
      '  crossed boundary→ Resource(File { canonical_path: "/tmp/x.sh" })',
      'Root cause: Agent(agent-a)',
      'Supporting evidence: 3 record(s)',
    ].join('\n'),
    confidence: 0.9,
  },
];

const tracebacksByClaimId = new Map(tracebacks.map((t) => [t.claim_id, t]));

describe('ClaimList — 责任声明 + 回溯链', () => {
  it('renders claims as FrictionRows sorted by timestamp then claim_id', () => {
    render(
      <ClaimList
        claims={claims}
        tracebacksByClaimId={tracebacksByClaimId}
        alertEvidenceRefs={[]}
      />,
    );
    const titles = screen.getAllByText(/污染上下文使用|策略违规/);
    expect(titles.length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText('claim-1')).toBeDefined();
    expect(screen.getByText('claim-2')).toBeDefined();
  });

  it('展开声明渲染可视化因果链（可读标签 + 边界 + 根因 + 证据数）', async () => {
    render(
      <ClaimList
        claims={claims}
        tracebacksByClaimId={tracebacksByClaimId}
        alertEvidenceRefs={[]}
      />,
    );
    const firstRow = screen.getByText('claim-1').closest('[data-claim-row]')!;
    (firstRow as HTMLElement).click();
    // 因果链起始 Agent + 根因 Agent（可读 short id）
    expect((await screen.findAllByText('agent-a')).length).toBeGreaterThanOrEqual(1);
    // 边界穿越文件路径（可读，非 Rust debug 格式）
    expect(screen.getByText('/tmp/x.sh')).toBeDefined();
    expect(screen.getByText(/边界穿越/)).toBeDefined();
    expect(screen.getByText(/根因/)).toBeDefined();
    expect(screen.getByText(/支持证据 3 条/)).toBeDefined();
    // 不再显示 Rust debug 原文
    expect(screen.queryByText(/Resource\(File/)).toBeNull();
  });

  it('无共享证据时不显示重复证据 chip（去重后仅保留共享计数）', () => {
    render(
      <ClaimList
        claims={claims}
        tracebacksByClaimId={tracebacksByClaimId}
        alertEvidenceRefs={[]}
      />,
    );
    // 证据清单已由上方证据链面板承载，声明行不再平铺 evidence refs
    expect(screen.queryByText('ev-1')).toBeNull();
  });

  it('shows competing explanations when present', async () => {
    render(
      <ClaimList
        claims={claims}
        tracebacksByClaimId={tracebacksByClaimId}
        alertEvidenceRefs={[]}
      />,
    );
    const secondRow = screen.getByText('claim-2').closest('[data-claim-row]')!;
    (secondRow as HTMLElement).click();
    expect(await screen.findByText('Alt path')).toBeDefined();
  });

  it('shows an empty state when there are no claims', () => {
    render(
      <ClaimList
        claims={[]}
        tracebacksByClaimId={new Map()}
        alertEvidenceRefs={[]}
      />,
    );
    expect(screen.getByText(/暂无责任声明/)).toBeDefined();
  });

  it('counts shared evidence with the alert (weak link via graph edges)', () => {
    render(
      <ClaimList
        claims={claims}
        tracebacksByClaimId={tracebacksByClaimId}
        alertEvidenceRefs={['ev-1', 'ev-9']}
      />,
    );
    // claim-1 的 evidence_refs=['ev-1'] 与告警侧交叠 1 条 → 共享徽章
    expect(screen.getByText(/共享证据 ×1/)).toBeDefined();
  });
});
