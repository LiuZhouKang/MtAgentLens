import { describe, expect, it } from 'vitest';
import { demoAlerts, demoGraph } from '@/lib/demo-data';
import { deriveTracebackEdgeIds } from '@/lib/traceback';
import { getSeverityPalette } from '@/lib/severity-palette';

describe('deriveTracebackEdgeIds', () => {
  it('prefers edges linked by evidence references', () => {
    const edgeIds = deriveTracebackEdgeIds(demoAlerts[0], demoGraph.edges);
    expect(edgeIds).toEqual(expect.arrayContaining(['e-taint', 'e-boundary']));
  });

  it('falls back to policy boundary edges when evidence refs are absent', () => {
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
    expect(edgeIds).toContain('e-boundary');
  });

  it('falls back to primary agent adjacency when no evidence or boundary edges are available', () => {
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
    expect(edgeIds).toEqual(expect.arrayContaining(['e-delegate', 'e-taint']));
  });
});

describe('getSeverityPalette', () => {
  it('returns stable palette tokens for each severity', () => {
    expect(getSeverityPalette('Critical').color).toBe('#fb7185');
    expect(getSeverityPalette('Warning').color).toBe('#fbbf24');
    expect(getSeverityPalette('Info').color).toBe('#7dd3fc');
  });
});
