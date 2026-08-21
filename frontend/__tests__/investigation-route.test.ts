import { describe, expect, it } from 'vitest';
import { buildInvestigationHref, parseInvestigationAlertId } from '@/lib/investigation-route';

describe('investigation route helpers', () => {
  it('builds a hash-based href for export-safe deep links', () => {
    expect(buildInvestigationHref('demo-alert-1')).toBe('/investigations#alertId=demo-alert-1');
    expect(buildInvestigationHref('alert id/with spaces')).toBe(
      '/investigations#alertId=alert%20id%2Fwith%20spaces',
    );
  });

  it('falls back to the investigations root when no alert id is provided', () => {
    expect(buildInvestigationHref()).toBe('/investigations');
    expect(buildInvestigationHref(null)).toBe('/investigations');
  });

  it('parses alert id from query params before hash params', () => {
    const searchParams = new URLSearchParams('alertId=query-alert');

    expect(parseInvestigationAlertId(searchParams, '#alertId=hash-alert')).toBe('query-alert');
  });

  it('parses alert id from hash params when query params are missing', () => {
    const searchParams = new URLSearchParams();

    expect(parseInvestigationAlertId(searchParams, '#alertId=demo-alert-2')).toBe('demo-alert-2');
    expect(parseInvestigationAlertId(searchParams, 'alertId=demo-alert-3')).toBe('demo-alert-3');
  });

  it('returns null when neither query nor hash carries an alert id', () => {
    expect(parseInvestigationAlertId(new URLSearchParams(), '')).toBeNull();
    expect(parseInvestigationAlertId(new URLSearchParams(), '#view=summary')).toBeNull();
  });
});
