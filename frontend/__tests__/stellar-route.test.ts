import { describe, expect, it } from 'vitest';
import { buildClusterHref, parseClusterId } from '@/lib/cluster-route';
import { buildAgentHref, parseAgentId } from '@/lib/agent-route';

describe('stellar route helpers', () => {
  it('builds cluster hrefs with hash-based deep links', () => {
    expect(buildClusterHref('cluster-alpha')).toBe('/overview#clusterId=cluster-alpha');
    expect(buildClusterHref('cluster id/with spaces')).toBe(
      '/overview#clusterId=cluster%20id%2Fwith%20spaces',
    );
  });

  it('falls back to the overview root when no cluster id is provided', () => {
    expect(buildClusterHref()).toBe('/overview');
    expect(buildClusterHref(null)).toBe('/overview');
  });

  it('parses cluster id from query params before hash params', () => {
    expect(
      parseClusterId(new URLSearchParams('clusterId=query-cluster'), '#clusterId=hash-cluster'),
    ).toBe('query-cluster');
  });

  it('parses cluster id from hash params when query params are missing', () => {
    expect(parseClusterId(new URLSearchParams(), '#clusterId=cluster-security')).toBe(
      'cluster-security',
    );
    expect(parseClusterId(new URLSearchParams(), 'clusterId=cluster-runtime')).toBe(
      'cluster-runtime',
    );
  });

  it('returns null when cluster id is missing from both query and hash', () => {
    expect(parseClusterId(new URLSearchParams(), '')).toBeNull();
    expect(parseClusterId(new URLSearchParams(), '#view=hero')).toBeNull();
  });

  it('builds agent hrefs with hash-based deep links', () => {
    expect(buildAgentHref('agent-security')).toBe('/overview#agentId=agent-security');
    expect(buildAgentHref('agent/security role')).toBe('/overview#agentId=agent%2Fsecurity%20role');
  });

  it('falls back to the overview root when no agent id is provided', () => {
    expect(buildAgentHref()).toBe('/overview');
    expect(buildAgentHref(null)).toBe('/overview');
  });

  it('parses agent id from query params before hash params', () => {
    expect(parseAgentId(new URLSearchParams('agentId=query-agent'), '#agentId=hash-agent')).toBe(
      'query-agent',
    );
  });

  it('parses agent id from hash params when query params are missing', () => {
    expect(parseAgentId(new URLSearchParams(), '#agentId=agent-frontend')).toBe('agent-frontend');
    expect(parseAgentId(new URLSearchParams(), 'agentId=agent-builder')).toBe('agent-builder');
  });

  it('returns null when agent id is missing from both query and hash', () => {
    expect(parseAgentId(new URLSearchParams(), '')).toBeNull();
    expect(parseAgentId(new URLSearchParams(), '#panel=summary')).toBeNull();
  });
});
