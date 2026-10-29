import { describe, expect, it } from 'vitest';
import { demoAlerts, demoGraph } from '@/lib/demo-data';
import { deriveTracebackEdgeIds } from '@/lib/traceback';

describe('deriveTracebackEdgeIds', () => {
  it('prefers edges linked by evidence references', () => {
    const edgeIds = deriveTracebackEdgeIds(demoAlerts[0], demoGraph.edges);
    expect(edgeIds).toEqual(expect.arrayContaining(['e-taint', 'e-boundary']));
  });

  it('does not present unrelated boundary edges as an evidence path', () => {
    const alert = {
      ...demoAlerts[0],
      timeline: { ...demoAlerts[0].timeline, steps: [] },
      graph_snapshot: {
        snapshot_id: 'snapshot-test-1',
        timestamp_ns: demoAlerts[0].timestamp_ns,
        metrics: demoAlerts[0].graph_snapshot.metrics,
        nodes: demoGraph.nodes,
        edges: demoGraph.edges,
      },
    };

    const edgeIds = deriveTracebackEdgeIds(alert, demoGraph.edges);
    expect(edgeIds).toEqual([]);
  });

  it('does not invent an evidence path from primary agent adjacency', () => {
    const edges = demoGraph.edges.filter((edge) => edge.edge_type !== 'PolicyBoundaryCrossing');
    const alert = {
      ...demoAlerts[0],
      timeline: { ...demoAlerts[0].timeline, steps: [] },
      graph_snapshot: {
        snapshot_id: 'snapshot-test-2',
        timestamp_ns: demoAlerts[0].timestamp_ns,
        metrics: demoAlerts[0].graph_snapshot.metrics,
        nodes: demoGraph.nodes,
        edges,
      },
    };

    const edgeIds = deriveTracebackEdgeIds(alert, edges);
    expect(edgeIds).toEqual([]);
  });
});
