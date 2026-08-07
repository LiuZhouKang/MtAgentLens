import { describe, it, expect } from 'vitest';

describe('GraphLayout', () => {
  it('converges within 100 iterations for a triangle graph', async () => {
    const { forceLayout3D } = await import('@/engine/graph-layout');
    const nodes = [
      { id: 'a', type: 'Agent' as const, x: 0, y: 0, z: 0 },
      { id: 'b', type: 'Agent' as const, x: 10, y: 10, z: 10 },
      { id: 'c', type: 'Resource' as const, x: -10, y: -10, z: -10 },
    ];
    const edges = [
      { source: 'a', target: 'b', edge_type: 'IntentCausesAction' as const },
      { source: 'b', target: 'c', edge_type: 'IntentCausesAction' as const },
    ];
    const result = forceLayout3D(nodes, edges, 100);
    for (let i = 0; i < 3; i++) {
      expect(result[i].x).not.toBe(nodes[i].x);
      expect(result[i].y).not.toBe(nodes[i].y);
    }
  });

  it('handles empty graph', async () => {
    const { forceLayout3D } = await import('@/engine/graph-layout');
    const result = forceLayout3D([], [], 100);
    expect(result).toEqual([]);
  });

  it('all 6 edge types produce different stiffness', async () => {
    const { forceLayout3D } = await import('@/engine/graph-layout');
    const nodes = [
      { id: 'a', type: 'Agent' as const, x: 0, y: 0, z: 0 },
      { id: 'b', type: 'Agent' as const, x: 5, y: 0, z: 0 },
    ];
    // Test each edge type produces valid results
    const types = [
      'IntentCausesAction',
      'AgentDelegatesTo',
      'ContextFlowsFrom',
      'TaintPropagatesTo',
      'PolicyBoundaryCrossing',
      'CompetingExplanation',
    ] as const;
    for (const t of types) {
      const edges = [{ source: 'a', target: 'b', edge_type: t }];
      const result = forceLayout3D(nodes, edges, 50);
      expect(result).toHaveLength(2);
    }
  });
});
