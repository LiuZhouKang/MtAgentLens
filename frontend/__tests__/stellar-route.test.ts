import { describe, expect, it } from 'vitest';
import { buildResponsibilityUnitHref, parseUnitId } from '@/lib/responsibility-unit-route';
import { buildAgentHref, parseAgentId } from '@/lib/agent-route';

describe('stellar route helpers', () => {
  it('builds responsibility-unit hrefs with hash-based deep links', () => {
    expect(buildResponsibilityUnitHref('unit-alpha')).toBe('/overview#unitId=unit-alpha');
    expect(buildResponsibilityUnitHref('unit id/with spaces')).toBe(
      '/overview#unitId=unit%20id%2Fwith%20spaces',
    );
  });

  it('falls back to the overview root when no unit id is provided', () => {
    expect(buildResponsibilityUnitHref()).toBe('/overview');
    expect(buildResponsibilityUnitHref(null)).toBe('/overview');
  });

  it('parses unit id from query params before hash params', () => {
    expect(
      parseUnitId(new URLSearchParams('unitId=query-unit'), '#unitId=hash-unit'),
    ).toBe('query-unit');
  });

  it('parses unit id from hash params when query params are missing', () => {
    expect(parseUnitId(new URLSearchParams(), '#unitId=unit-security')).toBe(
      'unit-security',
    );
    expect(parseUnitId(new URLSearchParams(), 'unitId=unit-runtime')).toBe(
      'unit-runtime',
    );
  });

  it('returns null when unit id is missing from both query and hash', () => {
    expect(parseUnitId(new URLSearchParams(), '')).toBeNull();
    expect(parseUnitId(new URLSearchParams(), '#view=hero')).toBeNull();
  });

  it('backward-compat: 仍解析重构前的旧 clusterId 深链', () => {
    expect(parseUnitId(new URLSearchParams('clusterId=legacy'), '')).toBe('legacy');
    expect(parseUnitId(new URLSearchParams(), '#clusterId=legacy-hash')).toBe('legacy-hash');
    // 新 unitId 优先于旧 clusterId
    expect(parseUnitId(new URLSearchParams('unitId=new'), '#clusterId=old')).toBe('new');
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
