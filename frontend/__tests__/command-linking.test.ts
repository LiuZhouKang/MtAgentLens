import { describe, expect, it } from 'vitest';
import { buildTraceHref, parseTraceAlertId } from '@/lib/trace-route';

describe('trace route', () => {
  it('builds and parses alert deep links', () => {
    expect(buildTraceHref('demo-alert-1')).toBe('/traces#alertId=demo-alert-1');
    expect(parseTraceAlertId(new URLSearchParams(), '#alertId=demo-alert-1')).toBe('demo-alert-1');
  });

  it('prefers query params over hash when parsing', () => {
    const params = new URLSearchParams('alertId=query-alert');
    expect(parseTraceAlertId(params, '#alertId=hash-alert')).toBe('query-alert');
  });
});
