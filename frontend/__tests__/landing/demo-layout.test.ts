import { describe, expect, it } from 'vitest';
import { computeDemoLayout } from '@/lib/landing/demo-layout';
import { demoGraph } from '@/lib/landing/landing-copy';

describe('computeDemoLayout — 迷你责任图布局', () => {
  it('positions all demo nodes inside the viewport bounds', () => {
    const { nodes } = computeDemoLayout(1000, 800);
    expect(nodes).toHaveLength(demoGraph.nodes.length);
    for (const n of nodes) {
      expect(n.x).toBeGreaterThanOrEqual(0);
      expect(n.x).toBeLessThanOrEqual(1000);
      expect(n.y).toBeGreaterThanOrEqual(0);
      expect(n.y).toBeLessThanOrEqual(800);
      expect(n.label.length).toBeGreaterThan(0);
    }
  });

  it('builds a curved edge path for every demo edge with valid endpoints', () => {
    const { nodes, edges } = computeDemoLayout(1000, 800);
    const ids = new Set(nodes.map((n) => n.id));
    expect(edges).toHaveLength(demoGraph.edges.length);
    for (const e of edges) {
      expect(ids.has(e.source)).toBe(true);
      expect(ids.has(e.target)).toBe(true);
      expect(e.edgeType.length).toBeGreaterThan(0);
      expect(e.path.startsWith('M')).toBe(true);
      expect(e.path).toContain('Q'); // 二次贝塞尔曲线（源 → 控制点 → 目标）
    }
  });

  it('is deterministic across calls', () => {
    expect(computeDemoLayout(1200, 700)).toEqual(computeDemoLayout(1200, 700));
  });

  it('scales with viewport (fractional positions, absolute output)', () => {
    const small = computeDemoLayout(500, 400);
    const large = computeDemoLayout(1000, 800);
    const ratio = 2;
    for (const n of small.nodes) {
      const match = large.nodes.find((m) => m.id === n.id)!;
      expect(match.x).toBeCloseTo(n.x * ratio, 4);
      expect(match.y).toBeCloseTo(n.y * ratio, 4);
    }
  });
});
