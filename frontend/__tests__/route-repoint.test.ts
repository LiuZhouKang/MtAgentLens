import { describe, expect, it } from 'vitest';
import { buildAgentHref, parseAgentId } from '@/lib/agent-route';
import { buildClusterHref, parseClusterId } from '@/lib/cluster-route';

describe('repointed route builders', () => {
  it('routes agent deep links to the overview dashboard', () => {
    expect(buildAgentHref('agent-security')).toBe('/overview#agentId=agent-security');
    expect(buildAgentHref()).toBe('/overview');
    expect(parseAgentId(new URLSearchParams(), '#agentId=agent-security')).toBe('agent-security');
  });

  it('routes cluster deep links to the overview dashboard', () => {
    expect(buildClusterHref('cluster-security-anchor')).toBe(
      '/overview#clusterId=cluster-security-anchor',
    );
    expect(buildClusterHref()).toBe('/overview');
    expect(parseClusterId(new URLSearchParams(), '#clusterId=cluster-security-anchor')).toBe(
      'cluster-security-anchor',
    );
  });
});
