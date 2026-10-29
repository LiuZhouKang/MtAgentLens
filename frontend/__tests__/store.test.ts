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
  it('requests a camera reset through state consumed by the graph stage', () => {
    const before = useAppStore.getState().cameraResetRequest;
    useAppStore.getState().resetCamera();
    useAppStore.getState().resetCamera();
    expect(useAppStore.getState().cameraResetRequest).toBe(before + 2);
    expect(useAppStore.getState().selectedNodeId).toBeNull();
  });
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

  it('keeps every live alert so live and replay counts agree', () => {
    // A 200-item cap made the live view and the DB replay of the same run
    // disagree past 200 alerts. Alerts are stored lean (no graph snapshot),
    // so the cap is not needed for memory.
    for (let i = 0; i < 250; i++) {
      useAppStore.getState().onAlertReceived({ alert_id: `a${i}`, timestamp_ns: i } as any);
    }
    expect(useAppStore.getState().alerts).toHaveLength(250);
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
      ['e-delegate', 'e-taint', 'e-boundary'],
    );
  });
});
