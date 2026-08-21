// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import { sanitizeMonitoringDetail } from '@/components/system/frontend-observer';

describe('sanitizeMonitoringDetail', () => {
  it('redacts emails, card-like numbers and secret assignments', () => {
    const input =
      'user=alice@example.com token=abc123 password:super-secret card=4111 1111 1111 1111';

    const output = sanitizeMonitoringDetail(input);

    expect(output).toContain('[redacted-email]');
    expect(output).toContain('token=[redacted]');
    expect(output).toContain('password=[redacted]');
    expect(output).toContain('[redacted-card]');
  });

  it('keeps ordinary route details intact', () => {
    const input = '/investigations?alertId=demo-alert-1';
    expect(sanitizeMonitoringDetail(input)).toBe(input);
  });
});
