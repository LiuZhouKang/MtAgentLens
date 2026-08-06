import { create } from 'zustand';
import { api } from './api';
import type { ResponsibilityAlert, GraphData, GraphMetrics, AppState, ZoomLevel } from './types';
import { demoAlerts, demoGraph } from './demo-data';
import { loadSampleData } from './load-sample-data';
import {
  DEFAULT_INVESTIGATION_CONTEXT,
  buildInvestigationContextFromAlert,
  mergeInvestigationContext,
  type InvestigationContext,
} from './investigation-context';

function deriveGraphMetrics(graph: GraphData): GraphMetrics {
  const node_counts = graph.nodes.reduce<Record<string, number>>((acc, node) => {
    acc[node.node_type] = (acc[node.node_type] ?? 0) + 1;
    return acc;
  }, {});

  const edge_counts = graph.edges.reduce<Record<string, number>>((acc, edge) => {
    acc[edge.edge_type] = (acc[edge.edge_type] ?? 0) + 1;
    return acc;
  }, {});

  const cross_agent_edges = graph.edges.filter((edge) => {
    const source = graph.nodes.find((node) => node.node_id === edge.source);
    const target = graph.nodes.find((node) => node.node_id === edge.target);
    return source?.agent_id && target?.agent_id && source.agent_id !== target.agent_id;
  }).length;

  const avg_confidence = graph.edges.length
    ? graph.edges.reduce((sum, edge) => sum + edge.confidence, 0) / graph.edges.length
    : 0;

  return {
    node_counts,
    edge_counts,
    agent_count: node_counts.Agent ?? 0,
    cross_agent_edges,
    delegation_depth: edge_counts.AgentDelegatesTo ?? 0,
    taint_chains: edge_counts.TaintPropagatesTo ?? 0,
    avg_confidence,
  };
}

interface EngineAPI {
  setVisualizationMode(mode: 'globe' | 'graph3d' | 'graph2d' | 'map' | 'hero' | 'focus'): void;
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
  investigationContext: InvestigationContext;
  errorMessage: string | null;
  engineRef: { current: EngineAPI | null };

  setAppState: (s: AppState) => void;
  setActiveRunId: (runId: string | null) => void;
  setZoomLevel: (l: ZoomLevel) => void;
  setGraph: (nodes: GraphData['nodes'], edges: GraphData['edges']) => void;
  setAlerts: (alerts: ResponsibilityAlert[]) => void;
  selectNode: (id: string | null) => void;
  deselectNode: () => void;
  expandNode: (id: string) => void;
  collapseNode: (id: string) => void;
  setTimelinePosition: (pos: number) => void;
  setInvestigationContext: (patch: Partial<InvestigationContext>) => void;
  focusAlert: (alert: ResponsibilityAlert) => void;
  setError: (msg: string | null) => void;
  flyToAgent: (id: string) => void;
  flyToNode: (id: string) => void;
  flyToLevel: (l: ZoomLevel) => void;
  setVisualizationMode: (m: 'globe' | 'graph3d' | 'graph2d' | 'map' | 'hero' | 'focus') => void;
  highlightPath: (edgeIds: string[]) => void;
  clearHighlights: () => void;
  loadRun: (runId: string) => Promise<void>;
  onAlertReceived: (alert: ResponsibilityAlert) => void;
  onRunActiveChanged: (active: boolean) => void;
}

// ── Live-stream coalescing ──
// High-frequency alerts from live sessions are buffered and flushed at most
// once per animation frame, so the store never re-renders the app per event.
// Where requestAnimationFrame is unavailable (jsdom tests) the buffer flushes
// synchronously so store assertions keep their synchronous semantics.
let alertBuffer: ResponsibilityAlert[] = [];
let flushHandle: number | null = null;

function flushAlertBuffer(get: () => AppStore, set: (partial: Partial<AppStore>) => void): void {
  flushHandle = null;
  if (alertBuffer.length === 0) return;
  const batch = alertBuffer;
  alertBuffer = [];
  const state = get();
  const existing = new Set(state.alerts.map((a) => a.alert_id));
  const fresh = batch.filter((a) => !existing.has(a.alert_id));
  if (fresh.length === 0) return;
  set({ alerts: [...fresh, ...state.alerts].slice(0, 200) });
  fresh.forEach((a) => state.engineRef.current?.showAlertPulse(a));
}

function scheduleAlertFlush(get: () => AppStore, set: (partial: Partial<AppStore>) => void): void {
  if (typeof requestAnimationFrame === 'function') {
    flushHandle = requestAnimationFrame(() => flushAlertBuffer(get, set)) as unknown as number;
  } else {
    // No rAF (jsdom / old environments) → flush synchronously.
    flushAlertBuffer(get, set);
  }
}

export const useAppStore = create<AppStore>((set, get) => ({
  appState: 'replay',
  activeRunId: null,
  zoomLevel: 0,
  selectedNodeId: null,
  expandedNodeIds: new Set(),
  alerts: [],
  graph: { nodes: [], edges: [] },
  graphMetrics: null,
  timelinePosition: 0,
  investigationContext: DEFAULT_INVESTIGATION_CONTEXT,
  errorMessage: null,
  engineRef: { current: null },

  setAppState: (s) => set({ appState: s }),
  setActiveRunId: (runId) => set({ activeRunId: runId }),
  setZoomLevel: (l) => set({ zoomLevel: l }),
  setGraph: (nodes, edges) => {
    const graph = { nodes, edges };
    set({ graph, graphMetrics: deriveGraphMetrics(graph) });
  },
  setAlerts: (alerts) => {
    const snapshotMetrics = alerts[0]?.graph_snapshot?.metrics ?? null;
    set({ alerts, graphMetrics: snapshotMetrics ?? get().graphMetrics });
  },
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
  setInvestigationContext: (patch) => {
    set({ investigationContext: mergeInvestigationContext(get().investigationContext, patch) });
  },
  focusAlert: (alert) => {
    const investigationContext = buildInvestigationContextFromAlert(alert);
    set({ investigationContext });
    if (investigationContext.highlightedPathEdgeIds.length) {
      get().engineRef.current?.highlightPath(investigationContext.highlightedPathEdgeIds);
    }
  },
  setError: (msg) => set({ errorMessage: msg }),

  // Engine bridge methods — require engineRef to be set by the React component.
  // Silently no-op if engine is not yet mounted (expected during initial render).
  flyToAgent: (id) => get().engineRef.current?.flyToAgent(id),
  flyToNode: (id) => get().engineRef.current?.flyToNode(id),
  flyToLevel: (l) => get().engineRef.current?.flyToLevel(l),
  setVisualizationMode: (m) => get().engineRef.current?.setVisualizationMode(m),
  highlightPath: (edgeIds) => get().engineRef.current?.highlightPath(edgeIds),
  clearHighlights: () => get().engineRef.current?.clearHighlights(),
  loadRun: async (runId) => {
    if (!runId) return;
    set({ activeRunId: runId, appState: 'replay', errorMessage: null });

    try {
      const [graph, alerts] = await Promise.all([api.getGraph(runId), api.getAlerts(runId)]);

      // Strip graph_snapshot from alerts
      const leanAlerts = alerts.map(({ graph_snapshot: _graph_snapshot, ...rest }: any) => rest);

      set({
        graph: { nodes: graph.nodes, edges: graph.edges },
        graphMetrics: deriveGraphMetrics({ nodes: graph.nodes, edges: graph.edges }),
        alerts: leanAlerts,
        selectedNodeId: null,
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      set({ errorMessage: message, appState: 'error' });
    }
  },

  onAlertReceived: (alert) => {
    // Alerts are coalesced per animation frame; the graph snapshot below is
    // applied immediately so the live network stays responsive.
    alertBuffer.push(alert);
    if (flushHandle == null) scheduleAlertFlush(get, set);

    // Extract graph_snapshot and update GraphRenderer (Live mode)
    if (alert.graph_snapshot?.nodes?.length) {
      const engine = get().engineRef.current;
      if (engine) {
        engine.updateGraph({
          nodes: alert.graph_snapshot.nodes,
          edges: alert.graph_snapshot.edges || [],
        });
      }
      set({
        graph: {
          nodes: alert.graph_snapshot.nodes,
          edges: alert.graph_snapshot.edges || [],
        },
        graphMetrics:
          alert.graph_snapshot.metrics ??
          deriveGraphMetrics({
            nodes: alert.graph_snapshot.nodes,
            edges: alert.graph_snapshot.edges || [],
          }),
      });
    }
  },
  onRunActiveChanged: (active) => {
    if (!active && get().appState === 'live') {
      set({ appState: 'replay' });
    }
  },
}));

// ── Data loader: API (live) → sample JSON → demo fixtures ──
async function initData() {
  try {
    // Try runs list — works with both Rust backend and Python data server
    const runs = await api.getRuns();
    const runList = (runs as any)?.runs ?? runs ?? [];
    if (runList.length > 0) {
      // Find first run with data, preferring the latest
      const firstWithData = runList.find((r: any) => r.has_data !== false) ?? runList[0];
      const runId = firstWithData.run_id;
      const [graphData, alertList] = await Promise.all([
        api.getGraph(runId),
        api.getAlerts(runId),
      ]);
      if (graphData.nodes?.length > 0) {
        // Strip graph_snapshot from alerts to save memory (~800KB for 3 alerts)
        const leanAlerts = alertList.map(({ graph_snapshot: _graph_snapshot, ...rest }: any) => rest);
        console.log(
          `[MtAgentLens] API: ${graphData.nodes.length} nodes, ${graphData.edges.length} edges, ${leanAlerts.length} alerts (${runId})`
        );
        useAppStore.setState({
          graph: graphData,
          alerts: leanAlerts,
          graphMetrics: deriveGraphMetrics(graphData),
          activeRunId: runId,
          appState: 'replay',
        });
        return;
      }
    }
  } catch (_err) {
    console.log('[MtAgentLens] API unavailable, trying static data...');
  }

  // 3. Fall back to static JSON sample
  try {
    const data = await loadSampleData();
    if (data) {
      const leanAlerts = data.alerts.map(({ graph_snapshot: _graph_snapshot, ...rest }: any) => rest);
      console.log(
        `[MtAgentLens] Sample data: ${data.graph.nodes.length} nodes, ${data.graph.edges.length} edges, ${leanAlerts.length} alerts`
      );
      useAppStore.setState({
        graph: data.graph,
        alerts: leanAlerts,
        graphMetrics: data.metrics,
      });
      return;
    }
  } catch {
    // ignore
  }

  // 4. Ultimate fallback — demo fixtures (silent, prevents infinite loading)
  console.log('[MtAgentLens] Using demo fixtures.');
  useAppStore.setState({
    graph: demoGraph,
    alerts: demoAlerts,
    graphMetrics: deriveGraphMetrics(demoGraph),
  });
}

// Only run on client — SSR would fail and block rendering
if (typeof window !== 'undefined') {
  initData();
}
