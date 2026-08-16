import { describe, expect, it } from 'vitest';
import { buildAgentHref, parseAgentId } from '@/lib/agent-route';
import { buildResponsibilityUnitHref, parseUnitId } from '@/lib/responsibility-unit-route';

describe('repointed route builders', () => {
  it('routes agent deep links to the overview dashboard', () => {
    expect(buildAgentHref('agent-security')).toBe('/overview#agentId=agent-security');
    expect(buildAgentHref()).toBe('/overview');
    expect(parseAgentId(new URLSearchParams(), '#agentId=agent-security')).toBe('agent-security');
  });

  it('routes responsibility-unit deep links to the overview dashboard', () => {
    expect(buildResponsibilityUnitHref('unit-security-anchor')).toBe(
      '/overview#unitId=unit-security-anchor',
    );
    expect(buildResponsibilityUnitHref()).toBe('/overview');
    expect(parseUnitId(new URLSearchParams(), '#unitId=unit-security-anchor')).toBe(
      'unit-security-anchor',
    );
  });
});
