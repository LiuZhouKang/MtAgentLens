import { describe, it, expect, beforeEach } from 'vitest';
import { useAppStore } from '@/lib/store';
import { demoAlerts } from '@/lib/demo-data';
import { DEFAULT_INVESTIGATION_CONTEXT } from '@/lib/investigation-context';

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
  investigationContext: DEFAULT_INVESTIGATION_CONTEXT,
  errorMessage: null as string | null,
};

beforeEach(() => {
  useAppStore.setState({ ...initState, expandedNodeIds: new Set() });
});

describe('AppStore', () => {
  it('initializes with init appState', () => {
    expect(useAppStore.getState().appState).toBe('init');
  });

  it('transitions init → idle', () => {
    useAppStore.getState().setAppState('idle');
    expect(useAppStore.getState().appState).toBe('idle');
  });

  it('supports all 5 app states', () => {
    for (const s of ['init', 'idle', 'live', 'replay', 'error'] as const) {
      useAppStore.getState().setAppState(s);
      expect(useAppStore.getState().appState).toBe(s);
    }
  });

  it('deduplicates alerts by alert_id', () => {
    const alert = { alert_id: 'a1', timestamp_ns: 1000, title: 'test' } as any;
    useAppStore.getState().onAlertReceived(alert);
    useAppStore.getState().onAlertReceived(alert);
    expect(useAppStore.getState().alerts).toHaveLength(1);
  });

  it('caps alerts at 200', () => {
    for (let i = 0; i < 250; i++) {
      useAppStore.getState().onAlertReceived({ alert_id: `a${i}`, timestamp_ns: i } as any);
    }
    expect(useAppStore.getState().alerts).toHaveLength(200);
  });

  it('onRunActiveChanged transitions live → replay', () => {
    useAppStore.getState().setAppState('live');
    useAppStore.getState().onRunActiveChanged(false);
    expect(useAppStore.getState().appState).toBe('replay');
  });

  it('onRunActiveChanged does nothing in replay state', () => {
    useAppStore.getState().setAppState('replay');
    useAppStore.getState().onRunActiveChanged(false);
    expect(useAppStore.getState().appState).toBe('replay');
  });

  it('expands and collapses nodes', () => {
    useAppStore.getState().expandNode('node-1');
    expect(useAppStore.getState().expandedNodeIds.has('node-1')).toBe(true);
    useAppStore.getState().collapseNode('node-1');
    expect(useAppStore.getState().expandedNodeIds.has('node-1')).toBe(false);
  });

  it('node expand is idempotent', () => {
    useAppStore.getState().expandNode('node-1');
    useAppStore.getState().expandNode('node-1');
    expect(useAppStore.getState().expandedNodeIds.size).toBe(1);
  });

  it('selectNode and deselectNode work', () => {
    useAppStore.getState().selectNode('n1');
    expect(useAppStore.getState().selectedNodeId).toBe('n1');
    useAppStore.getState().deselectNode();
    expect(useAppStore.getState().selectedNodeId).toBeNull();
  });

  it('focuses an alert into the shared investigation context', () => {
    const alert = demoAlerts[0];

    useAppStore.getState().focusAlert(alert);

    const context = useAppStore.getState().investigationContext;
    expect(context.activeIncidentId).toBe(alert.alert_id);
    expect(context.activeAgentId).toBe(alert.primary_agent.agent_id);
    expect(context.highlightedPathEdgeIds).toEqual(
      alert.graph_snapshot.edges.map((edge) => edge.edge_id),
    );
  });
});
