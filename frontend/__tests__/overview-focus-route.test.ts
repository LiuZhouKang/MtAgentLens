import { describe, expect, it } from 'vitest';
import { parseOverviewFocus, resolveOverviewFocus } from '@/lib/overview-focus-route';

describe('overview focus route', () => {
  it('parses an agentId hash focus', () => {
    const focus = parseOverviewFocus(new URLSearchParams(), '#agentId=agent-security');
    expect(focus.agentId).toBe('agent-security');
    expect(focus.unitId).toBeNull();
  });

  it('parses a unitId hash focus', () => {
    const focus = parseOverviewFocus(new URLSearchParams(), '#unitId=unit-security-anchor');
    expect(focus.unitId).toBe('unit-security-anchor');
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
    expect(focus.unitId).toBeNull();
  });
});

describe('resolveOverviewFocus', () => {
  const alertModels = [
    { alert: { alert_id: 'a1', primary_agent: { agent_id: 'agent-security' } } },
    { alert: { alert_id: 'a2', primary_agent: { agent_id: 'agent-dev' } } },
  ];
  const responsibilityUnits = [
    { unitId: 'unit-security-anchor', leadAlertId: 'a1' },
    { unitId: 'unit-dev', leadAlertId: 'a2' },
  ];

  it('resolves an agentId to the owning alert', () => {
    expect(
      resolveOverviewFocus(
        { agentId: 'agent-security', unitId: null },
        alertModels,
        responsibilityUnits,
      ),
    ).toBe('a1');
  });

  it('resolves a unitId to its lead alert', () => {
    expect(
      resolveOverviewFocus({ agentId: null, unitId: 'unit-dev' }, alertModels, responsibilityUnits),
    ).toBe('a2');
  });

  it('returns null when nothing matches', () => {
    expect(
      resolveOverviewFocus({ agentId: 'missing', unitId: null }, alertModels, responsibilityUnits),
    ).toBeNull();
  });

  it('returns null when focus is empty', () => {
    expect(
      resolveOverviewFocus({ agentId: null, unitId: null }, alertModels, responsibilityUnits),
    ).toBeNull();
  });
});
