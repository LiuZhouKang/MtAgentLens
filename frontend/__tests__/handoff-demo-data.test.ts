import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

describe('handoff demo data', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.resetModules();
  });

  it('stays deterministic across module reloads', async () => {
    vi.setSystemTime(new Date('2026-06-23T09:00:00.000Z'));
    const firstModule = await import('@/lib/handoff-demo-data');
    const firstTree = firstModule.getDemoDelegationTreeResponse();
    const firstLeakEvent = firstModule.demoEvents.find((event) => event.sub_type === 'DATA_LEAK');

    vi.resetModules();

    vi.setSystemTime(new Date('2026-06-23T11:30:00.000Z'));
    const secondModule = await import('@/lib/handoff-demo-data');
    const secondTree = secondModule.getDemoDelegationTreeResponse();
    const secondLeakEvent = secondModule.demoEvents.find((event) => event.sub_type === 'DATA_LEAK');

    expect(secondTree.built_at_ms).toBe(firstTree.built_at_ms);
    expect(secondTree.tree.spawned_at_ms).toBe(firstTree.tree.spawned_at_ms);
    expect(secondLeakEvent && 'timestamp_ms' in secondLeakEvent ? secondLeakEvent.timestamp_ms : null).toBe(
      firstLeakEvent && 'timestamp_ms' in firstLeakEvent ? firstLeakEvent.timestamp_ms : null,
    );
  });
});
