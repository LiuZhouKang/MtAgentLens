import { beforeEach, describe, expect, it, vi } from 'vitest';
import { demoAlerts, demoGraph } from '@/lib/demo-data';

vi.mock('@/lib/api', () => ({
  api: {
    getGraph: vi.fn(),
    getAlerts: vi.fn(),
  },
}));

type StoreModule = typeof import('@/lib/store');
type ApiModule = typeof import('@/lib/api');

describe('AppStore runtime paths', () => {
  let useAppStore: StoreModule['useAppStore'];
  let api: ApiModule['api'];

  beforeEach(async () => {
    vi.resetModules();
    vi.clearAllMocks();

    const apiModule = await import('@/lib/api');
    api = apiModule.api;

    const storeModule = await import('@/lib/store');
    useAppStore = storeModule.useAppStore;

    useAppStore.setState({
      appState: 'init',
      activeRunId: null,
      zoomLevel: 0,
      selectedNodeId: 'stale-node',
      expandedNodeIds: new Set<string>(),
      alerts: [],
      graph: { nodes: [], edges: [] },
      graphMetrics: null,
      timelinePosition: 0,
      errorMessage: null,
    });
  });

  it('loads a run successfully and resets stale selection', async () => {
    vi.mocked(api.getGraph).mockResolvedValue({
      nodes: demoGraph.nodes,
      edges: demoGraph.edges,
    });
    vi.mocked(api.getAlerts).mockResolvedValue(demoAlerts);

    await useAppStore.getState().loadRun('run-42');

    const state = useAppStore.getState();
    expect(state.activeRunId).toBe('run-42');
    expect(state.appState).toBe('replay');
    expect(state.errorMessage).toBeNull();
    expect(state.selectedNodeId).toBeNull();
    expect(state.graph.nodes).toHaveLength(demoGraph.nodes.length);
    expect(state.alerts).toHaveLength(demoAlerts.length);
  });

  it('surfaces run loading failures into the error state', async () => {
    vi.mocked(api.getGraph).mockRejectedValue(new Error('graph unavailable'));
    vi.mocked(api.getAlerts).mockResolvedValue(demoAlerts);

    await useAppStore.getState().loadRun('run-fail');

    const state = useAppStore.getState();
    expect(state.activeRunId).toBe('run-fail');
    expect(state.appState).toBe('error');
    expect(state.errorMessage).toBe('graph unavailable');
  });

  it('ignores empty run ids without mutating state', async () => {
    await useAppStore.getState().loadRun('');

    const state = useAppStore.getState();
    expect(state.activeRunId).toBeNull();
    expect(state.graph.nodes).toEqual([]);
    expect(api.getGraph).not.toHaveBeenCalled();
    expect(api.getAlerts).not.toHaveBeenCalled();
  });

  it('updates the graph subscribed by the production graph stage on live alerts', () => {
    useAppStore.setState({ alerts: [] });

    const liveAlert = {
      ...demoAlerts[0],
      alert_id: 'live-alert-1',
      graph_snapshot: {
        ...demoAlerts[0].graph_snapshot,
        nodes: demoGraph.nodes.slice(0, 2),
        edges: demoGraph.edges.slice(0, 1),
      },
    };

    useAppStore.getState().onAlertReceived(liveAlert);

    expect(useAppStore.getState().graph).toEqual({
      nodes: liveAlert.graph_snapshot.nodes,
      edges: liveAlert.graph_snapshot.edges,
    });
    expect(useAppStore.getState().alerts[0]?.alert_id).toBe('live-alert-1');
  });
});
