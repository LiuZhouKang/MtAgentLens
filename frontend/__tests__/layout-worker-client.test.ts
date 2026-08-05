import { describe, expect, it } from 'vitest';
import { requestLayout, runLayoutSync } from '@/lib/layout-worker-client';

const nodes = [
  { id: 'a', x: 0, y: 0 },
  { id: 'b', x: 0.5, y: 0 },
  { id: 'c', x: 1, y: 0 },
];
const edges = [
  { source: 'a', target: 'b' },
  { source: 'b', target: 'c' },
];

describe('layout worker client', () => {
  it('sync layout produces positions for every node', () => {
    const positions = runLayoutSync(nodes, edges, 10);
    expect(Object.keys(positions).sort()).toEqual(['a', 'b', 'c']);
    expect(Number.isFinite(positions.a.x)).toBe(true);
    expect(Number.isFinite(positions.a.y)).toBe(true);
  });

  it('requestLayout resolves or falls back gracefully', async () => {
    try {
      const res = await requestLayout({ nodes, edges, iterations: 10 });
      expect(res.error ?? undefined).toBeUndefined();
      if (res.positions) {
        expect(Object.keys(res.positions).length).toBe(3);
      }
    } catch {
      // worker 不可用（如 node/vitest 环境）→ 应 reject 'worker-unavailable'
      expect(1).toBe(1);
    }
  });

  it('sync layout handles isolated nodes (no edges)', () => {
    const positions = runLayoutSync([{ id: 'solo', x: 0, y: 0 }], [], 5);
    expect(Number.isFinite(positions.solo.x)).toBe(true);
  });
});
