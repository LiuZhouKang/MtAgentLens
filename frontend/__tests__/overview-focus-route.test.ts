import { describe, expect, it } from 'vitest';
import { parseOverviewFocus, resolveOverviewFocus } from '@/lib/overview-focus-route';

describe('overview focus route', () => {
  it('parses an agentId hash focus', () => {
    const focus = parseOverviewFocus(new URLSearchParams(), '#agentId=agent-security');
    expect(focus.agentId).toBe('agent-security');
    expect(focus.clusterId).toBeNull();
  });

  it('parses a clusterId hash focus', () => {
    const focus = parseOverviewFocus(new URLSearchParams(), '#clusterId=cluster-security-anchor');
    expect(focus.clusterId).toBe('cluster-security-anchor');
    expect(focus.agentId).toBeNull();
  });

  it('prefers query params over hash when both present', () => {
    const focus = parseOverviewFocus(
      new URLSearchParams('agentId=query-agent'),
      '#agentId=hash-agent',
    );
    expect(focus.agentId).toBe('query-agent');
  });

  it('returns nulls when no focus is present', () => {
    const focus = parseOverviewFocus(new URLSearchParams(), undefined);
    expect(focus.agentId).toBeNull();
    expect(focus.clusterId).toBeNull();
  });
});

describe('resolveOverviewFocus', () => {
  const alertModels = [
    { alert: { alert_id: 'a1', primary_agent: { agent_id: 'agent-security' } } },
    { alert: { alert_id: 'a2', primary_agent: { agent_id: 'agent-dev' } } },
  ];
  const clusterModels = [
    { clusterId: 'cluster-security-anchor', leadAlertId: 'a1' },
    { clusterId: 'cluster-dev', leadAlertId: 'a2' },
  ];

  it('resolves an agentId to the owning alert', () => {
    expect(
      resolveOverviewFocus(
        { agentId: 'agent-security', clusterId: null },
        alertModels,
        clusterModels,
      ),
    ).toBe('a1');
  });

  it('resolves a clusterId to its lead alert', () => {
    expect(
      resolveOverviewFocus({ agentId: null, clusterId: 'cluster-dev' }, alertModels, clusterModels),
    ).toBe('a2');
  });

  it('returns null when nothing matches', () => {
    expect(
      resolveOverviewFocus({ agentId: 'missing', clusterId: null }, alertModels, clusterModels),
    ).toBeNull();
  });

  it('returns null when focus is empty', () => {
    expect(
      resolveOverviewFocus({ agentId: null, clusterId: null }, alertModels, clusterModels),
    ).toBeNull();
  });
});
