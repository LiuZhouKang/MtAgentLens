import { describe, expect, test } from 'vitest';
import { demoAlerts, demoEvidence } from '@/lib/demo-data';
import { buildTraceRunModel } from '@/lib/trace-workbench';

describe('trace workbench model', () => {
  test('turns a responsibility alert into a readable investigation trace', () => {
    const model = buildTraceRunModel(demoAlerts[0], demoEvidence);

    expect(model.title).toContain('权限提升');
    expect(model.primaryAgent).toBe('agent-security');
    expect(model.timeline.length).toBeGreaterThanOrEqual(5);
    expect(model.boundaryEvents).toBeGreaterThan(0);
    expect(model.verdict.evidence).toBe('/etc/shadow');
    expect(model.timeline.map((event) => event.kind)).toContain('boundary');
    expect(model.timeline.map((event) => event.material)).toContain('/etc/shadow');
  });
});
