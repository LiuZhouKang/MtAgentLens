import { describe, expect, it } from 'vitest';
import { pickLodLevel, summarizeToLod } from '@/lib/graph-lod';
import { runLayoutSync } from '@/lib/layout-worker-client';

// Synthetic 100k-node / 500k-edge graph — validates the large-data pipeline
// stays bounded (aggregation + layout) and produces a small renderable summary.
function makeGraph(nNodes: number, nEdges: number) {
  const nodes = Array.from({ length: nNodes }, (_, i) => ({
    node_id: `n${i}`,
    node_type: (i % 10 === 0 ? 'Agent' : 'Resource') as 'Agent' | 'Resource',
    agent_id: i % 10 === 0 ? `agent-${Math.floor(i / 10)}` : null,
    resource: null,
    evidence_refs: [] as string[],
    timestamp_ns: 0,
    phase: { Provisional: { timestamp_ns: 0 } },
  }));
  const edges = Array.from({ length: nEdges }, (_, i) => ({
    edge_id: `e${i}`,
    source: `n${i % nNodes}`,
    target: `n${(i * 7 + 1) % nNodes}`,
    edge_type: 'IntentCausesAction' as const,
    confidence: 0.5,
    evidence_refs: [] as string[],
    phase: { Authoritative: { timestamp_ns: 0, overrides: null } },
  }));
  return { nodes, edges };
}

describe('bench large data', () => {
  it('LOD + layout on 100k nodes stays bounded', () => {
    const graph = makeGraph(100_000, 500_000);
    expect(pickLodLevel(graph.nodes.length)).toBe('cluster');

    const t0 = performance.now();
    const lod = summarizeToLod(graph, 'cluster');
    const t1 = performance.now();

    const t2 = performance.now();
    const positions = runLayoutSync(
      lod.nodes.map((n) => ({ id: n.id, x: 0, y: 0 })),
      lod.edges.map((e) => ({ source: e.source, target: e.target })),
      20,
    );
    const t3 = performance.now();

    console.log(
      `[bench] 100k→cluster: summarize=${(t1 - t0).toFixed(0)}ms layout=${(t3 - t2).toFixed(0)}ms lodNodes=${lod.nodes.length} lodEdges=${lod.edges.length}`,
    );

    // Cluster LOD collapses 100k nodes into ~24 buckets
    expect(lod.nodes.length).toBeLessThan(100);
    expect(lod.nodes.length).toBeGreaterThan(0);
    // Layout produces positions for every summary node
    expect(Object.keys(positions).length).toBe(lod.nodes.length);
  });

  it('picks raw/agent/cluster levels by scale', () => {
    expect(pickLodLevel(3_000)).toBe('raw');
    expect(pickLodLevel(10_000)).toBe('agent');
    expect(pickLodLevel(30_000)).toBe('cluster');
  });
});
