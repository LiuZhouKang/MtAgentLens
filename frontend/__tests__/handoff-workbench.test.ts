import { describe, expect, it } from 'vitest';
import { buildHandoffWorkbenchModel } from '@/lib/handoff-workbench';
import { getDemoDelegationTreeResponse, demoEvents, demoMetrics } from '@/lib/handoff-demo-data';

describe('handoff workbench model', () => {
  it('surfaces the highest-pressure lane and most actionable focus signal', () => {
    const model = buildHandoffWorkbenchModel(
      getDemoDelegationTreeResponse().tree,
      demoMetrics,
      demoEvents,
    );

    expect(model.leadLane?.agentId).toBe('planner-agent');
    expect(model.leadFocus).toEqual(
      expect.objectContaining({
        title: 'planner-agent 与 security-auditor 之间出现内容碰撞',
        bucket: 'boundary',
        severity: 'critical',
      }),
    );
    expect(model.focusEvents).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ title: expect.stringContaining('上下文接近饱和') }),
        expect.objectContaining({ bucket: 'debris' }),
        expect.objectContaining({ bucket: 'integrity' }),
      ]),
    );
    expect(model.signalCounts.boundary).toBeGreaterThan(0);
    expect(model.signalCounts.orchestration).toBeGreaterThan(0);
    expect(model.signalCounts.debris).toBeGreaterThan(0);
  });

  it('keeps boundary-critical focus ahead of fanout alerts', () => {
    const model = buildHandoffWorkbenchModel(
      getDemoDelegationTreeResponse().tree,
      demoMetrics,
      demoEvents,
    );

    expect(model.focusEvents.slice(0, 3).map((event) => event.title)).toEqual([
      'planner-agent 与 security-auditor 之间出现内容碰撞',
      '委托扇出速度明显偏离完成速度',
      'writer-agent 上下文接近饱和',
    ]);
  });
});
