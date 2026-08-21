import { describe, it, expect, beforeEach } from 'vitest';
import { useAppStore } from '@/lib/store';

const initState = {
  appState: 'init' as const,
  activeRunId: null as string | null,
  zoomLevel: 0 as const,
  selectedNodeId: null as string | null,
  expandedNodeIds: new Set<string>(),
  alerts: [] as any[],
  graph: { nodes: [], edges: [] },
  graphMetrics: null,
  timelinePosition: 0,
  errorMessage: null as string | null,
};

beforeEach(() => {
  useAppStore.setState({ ...initState, expandedNodeIds: new Set() });
});

describe('Store — unique integration tests', () => {
  // Tests NOT already covered in store.test.ts

  it('setGraph updates graph state with nodes and edges', () => {
    const nodes = [
      {
        node_id: 'n1',
        node_type: 'Agent' as const,
        agent_id: 'a1',
        resource: null,
        evidence_refs: [],
        timestamp_ns: 100,
        phase: { Provisional: { timestamp_ns: 100 } },
      },
    ];
    const edges = [
      {
        edge_id: 'e1',
        edge_type: 'IntentCausesAction' as const,
        source: 'n1',
        target: 'n2',
        evidence_refs: [],
        confidence: 0.9,
        phase: { Provisional: { timestamp_ns: 100 } },
      },
    ];
    useAppStore.getState().setGraph(nodes, edges);
    expect(useAppStore.getState().graph.nodes).toHaveLength(1);
    expect(useAppStore.getState().graph.edges).toHaveLength(1);
  });

  it('selectNode and deselectNode update selectedNodeId', () => {
    useAppStore.getState().selectNode('n1');
    expect(useAppStore.getState().selectedNodeId).toBe('n1');
    useAppStore.getState().deselectNode();
    expect(useAppStore.getState().selectedNodeId).toBeNull();
  });

  it('setError and setTimelinePosition update respective fields', () => {
    useAppStore.getState().setError('test error');
    expect(useAppStore.getState().errorMessage).toBe('test error');
    useAppStore.getState().setError(null);
    expect(useAppStore.getState().errorMessage).toBeNull();

    useAppStore.getState().setTimelinePosition(42);
    expect(useAppStore.getState().timelinePosition).toBe(42);
  });
});
