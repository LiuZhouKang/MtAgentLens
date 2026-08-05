import { describe, expect, it } from 'vitest';
import { pickLodLevel, summarizeToLod } from '@/lib/graph-lod';
import { demoGraph } from '@/lib/demo-data';

describe('graph-lod', () => {
  it('picks LOD level by raw node count', () => {
    expect(pickLodLevel(1000)).toBe('raw');
    expect(pickLodLevel(10_000)).toBe('agent');
    expect(pickLodLevel(100_000)).toBe('cluster');
  });

  it('summarizes demo graph to agent level', () => {
    const lod = summarizeToLod(demoGraph, 'agent');
    expect(lod.rawNodeCount).toBe(demoGraph.nodes.length);
    expect(lod.rawEdgeCount).toBe(demoGraph.edges.length);
    expect(lod.nodes.length).toBeGreaterThan(0);
    expect(lod.nodes.length).toBeLessThanOrEqual(demoGraph.nodes.length);
    // 所有节点 id 唯一
    expect(new Set(lod.nodes.map((n) => n.id)).size).toBe(lod.nodes.length);
    // 边端点引用存在的节点
    for (const e of lod.edges) {
      expect(lod.nodes.some((n) => n.id === e.source)).toBe(true);
      expect(lod.nodes.some((n) => n.id === e.target)).toBe(true);
    }
  });

  it('cluster level is deterministic and reduces node count', () => {
    const a = summarizeToLod(demoGraph, 'cluster');
    const b = summarizeToLod(demoGraph, 'cluster');
    expect(a.nodes.map((n) => n.id)).toEqual(b.nodes.map((n) => n.id));
    expect(a.nodes.length).toBeLessThanOrEqual(a.rawNodeCount);
    // 边端点必须引用存在的节点（cluster 模式边已重映射到桶）
    for (const e of a.edges) {
      expect(a.nodes.some((n) => n.id === e.source)).toBe(true);
      expect(a.nodes.some((n) => n.id === e.target)).toBe(true);
    }
  });
});
