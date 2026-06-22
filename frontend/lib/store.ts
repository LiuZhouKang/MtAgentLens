import { create } from 'zustand';
import type { ResponsibilityAlert, GraphData, GraphMetrics, AppState, ZoomLevel } from './types';

interface EngineAPI {
  setVisualizationMode(mode: 'globe' | 'graph3d' | 'graph2d' | 'map'): void;
  flyToAgent(agentId: string, duration?: number): void;
  flyToNode(nodeId: string, duration?: number): void;
  flyToLevel(level: number, duration?: number): void;
  resetCamera(duration?: number): void;
  updateGraph(graph: GraphData): void;
  highlightPath(edgeIds: string[]): void;
  clearHighlights(): void;
  showAlertPulse(alert: ResponsibilityAlert): void;
  clearAlertPulses(): void;
  orbit(deltaX: number, deltaY: number): void;
  zoom(delta: number): void;
  onNodeClicked(cb: (nodeId: string) => void): void;
  onZoomLevelChanged(cb: (level: number) => void): void;
  dispose(): void;
}

interface AppStore {
  appState: AppState;
  activeRunId: string | null;
  zoomLevel: ZoomLevel;
  selectedNodeId: string | null;
  expandedNodeIds: Set<string>;
  alerts: ResponsibilityAlert[];
  graph: GraphData;
  graphMetrics: GraphMetrics | null;
  timelinePosition: number;
  errorMessage: string | null;
  engineRef: { current: EngineAPI | null };

  setAppState: (s: AppState) => void;
  setZoomLevel: (l: ZoomLevel) => void;
  setGraph: (nodes: GraphData['nodes'], edges: GraphData['edges']) => void;
  setAlerts: (alerts: ResponsibilityAlert[]) => void;
  selectNode: (id: string | null) => void;
  deselectNode: () => void;
  expandNode: (id: string) => void;
  collapseNode: (id: string) => void;
  setTimelinePosition: (pos: number) => void;
  setError: (msg: string | null) => void;
  flyToAgent: (id: string) => void;
  flyToNode: (id: string) => void;
  flyToLevel: (l: ZoomLevel) => void;
  setVisualizationMode: (m: 'globe' | 'graph3d' | 'graph2d' | 'map') => void;
  highlightPath: (edgeIds: string[]) => void;
  clearHighlights: () => void;
  onAlertReceived: (alert: ResponsibilityAlert) => void;
  onRunActiveChanged: (active: boolean) => void;
}

export const useAppStore = create<AppStore>((set, get) => ({
  appState: 'init',
  activeRunId: null,
  zoomLevel: 0,
  selectedNodeId: null,
  expandedNodeIds: new Set(),
  alerts: [],
  graph: { nodes: [], edges: [] },
  graphMetrics: null,
  timelinePosition: 0,
  errorMessage: null,
  engineRef: { current: null },

  setAppState: (s) => set({ appState: s }),
  setZoomLevel: (l) => set({ zoomLevel: l }),
  setGraph: (nodes, edges) => set({ graph: { nodes, edges } }),
  setAlerts: (alerts) => set({ alerts }),
  selectNode: (id) => set({ selectedNodeId: id }),
  deselectNode: () => set({ selectedNodeId: null }),
  expandNode: (id) => {
    const next = new Set(get().expandedNodeIds);
    next.add(id);
    set({ expandedNodeIds: next });
  },
  collapseNode: (id) => {
    const next = new Set(get().expandedNodeIds);
    next.delete(id);
    set({ expandedNodeIds: next });
  },
  setTimelinePosition: (pos) => set({ timelinePosition: pos }),
  setError: (msg) => set({ errorMessage: msg }),

  // Engine bridge methods — require engineRef to be set by the React component.
  // Silently no-op if engine is not yet mounted (expected during initial render).
  flyToAgent: (id) => get().engineRef.current?.flyToAgent(id),
  flyToNode: (id) => get().engineRef.current?.flyToNode(id),
  flyToLevel: (l) => get().engineRef.current?.flyToLevel(l),
  setVisualizationMode: (m) => get().engineRef.current?.setVisualizationMode(m),
  highlightPath: (edgeIds) => get().engineRef.current?.highlightPath(edgeIds),
  clearHighlights: () => get().engineRef.current?.clearHighlights(),

  onAlertReceived: (alert) => {
    const existing = get().alerts.find((a) => a.alert_id === alert.alert_id);
    if (existing) return;
    set({ alerts: [alert, ...get().alerts].slice(0, 200) });
    get().engineRef.current?.showAlertPulse(alert);

    // Extract graph_snapshot and update GraphRenderer (Live mode)
    if (alert.graph_snapshot?.nodes?.length) {
      const engine = get().engineRef.current;
      if (engine) {
        engine.updateGraph({
          nodes: alert.graph_snapshot.nodes,
          edges: alert.graph_snapshot.edges || [],
        });
      }
    }
  },
  onRunActiveChanged: (active) => {
    if (!active && get().appState === 'live') {
      set({ appState: 'replay' });
    }
  },
}));
