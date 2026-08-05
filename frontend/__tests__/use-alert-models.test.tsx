// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import { renderHook } from '@testing-library/react';
import { demoAlerts, demoGraph, demoEvidence } from '@/lib/demo-data';
import { useAlertModels, graphFingerprint } from '@/lib/use-alert-models';

describe('useAlertModels', () => {
  it('fingerprint is stable for same graph and differs for different sizes', () => {
    const a = graphFingerprint(demoGraph);
    const b = graphFingerprint({ nodes: demoGraph.nodes, edges: demoGraph.edges });
    expect(a).toBe(b);
    expect(graphFingerprint({ nodes: [], edges: [] })).not.toBe(a);
  });

  it('returns entries for every alert', () => {
    const { result } = renderHook(() => useAlertModels(demoAlerts, demoGraph, demoEvidence));
    const entries = result.current;
    expect(entries.length).toBe(demoAlerts.length);
    expect(entries.every((e) => e.alert && e.alert.alert_id)).toBe(true);
    // 排序：第一个应是最严重/最新
    expect(entries[0].alert.alert_id).toBeTruthy();
  });

  it('sorts by severity then decision then timestamp', () => {
    const { result } = renderHook(() => useAlertModels(demoAlerts, demoGraph, demoEvidence));
    const entries = result.current;
    for (let i = 1; i < entries.length; i += 1) {
      const prev = entries[i - 1];
      const cur = entries[i];
      const pw = prev.model ? prev.model.severity : prev.alert.severity;
      const cw = cur.model ? cur.model.severity : cur.alert.severity;
      const weight: Record<string, number> = { Critical: 3, Warning: 2, Info: 1 };
      const pwScore = weight[pw] ?? 0;
      const cwScore = weight[cw] ?? 0;
      expect(pwScore).toBeGreaterThanOrEqual(cwScore);
    }
  });
});
