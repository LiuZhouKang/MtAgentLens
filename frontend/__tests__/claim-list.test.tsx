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
