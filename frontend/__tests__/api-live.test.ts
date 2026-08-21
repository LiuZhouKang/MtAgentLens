import { afterEach, describe, expect, it, vi } from 'vitest';
import { LIVE_EVENTS_URL, api } from '@/lib/api';

const calls: string[] = [];

function stubFetch() {
  global.fetch = vi.fn(async (input: unknown) => {
    calls.push(String(input));
    return {
      ok: true,
      json: async () => ({ data: [{ alert_id: 'a1', graph_snapshot: { nodes: [] } }] }),
    };
  }) as unknown as typeof fetch;
}

afterEach(() => {
  calls.length = 0;
  vi.restoreAllMocks();
});

describe('api 实时扩展', () => {
  it('LIVE_EVENTS_URL 指向统一实时 SSE 端点', () => {
    expect(LIVE_EVENTS_URL).toBe('/api/live/events');
  });

  it('getAlertsFull 请求全量告警（含 graph_snapshot，不带 lean）', async () => {
    stubFetch();
    const alerts = await api.getAlertsFull('r1');
    expect(alerts.length).toBe(1);
    expect(alerts[0].graph_snapshot.nodes).toEqual([]);
    expect(calls[0]).toContain('/api/runs/r1/alerts');
    expect(calls[0]).not.toContain('lean=1');
  });

  it('getAlerts 保持 lean=1（列表轻量语义不变）', async () => {
    stubFetch();
    await api.getAlerts('r1');
    expect(calls[0]).toContain('lean=1');
  });
});
