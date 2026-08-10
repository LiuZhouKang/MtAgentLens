import { create } from 'zustand';
import { api } from './api';
import type {
  Action,
  AppState,
  CorrelationEvidence,
  DatasetOverview,
  GraphData,
  GraphMetrics,
  GraphSnapshot,
  Intent,
  ResponsibilityAlert,
  ResponsibilityClaim,
  ResponsibilityTraceback,
  ZoomLevel,
} from './types';
import { demoAlerts, demoGraph } from './demo-data';
import { loadSampleData } from './load-sample-data';
import { deriveGraphMetrics } from './graph-metrics';
import type { SnapshotImport } from './snapshot-import';
import { loadDatabase as loadDatabaseFromBytes, type DbImport, type DbAssets } from './db-import';
import type { MonitoringState } from './monitoring/monitoring-types';
import {
  DEFAULT_INVESTIGATION_CONTEXT,
  buildInvestigationContextFromAlert,
  mergeInvestigationContext,
  type InvestigationContext,
} from './investigation-context';
import {
  DEFAULT_LIVE_STATE,
  type LiveConnectionStatus,
  type LiveRunMeta,
  type LiveState,
  type LiveStats,
} from './live/live-types';

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

/**
 * Which data source is currently shown. Backend runs are consumed from the
 * run's SQLite db (via /api/runs/:id/db) when available, otherwise via the
 * API contract — so a run may surface as kind 'run' (API) or kind 'db'
 * (db-consumed, runId attached so the run selector still highlights it).
 * Local sources (sample/demo/file/db import) carry no activeRunId unless the
 * imported db came from a backend run.
 */
export type DataSourceMeta =
  | { kind: 'run'; runId: string }
  | { kind: 'db'; label: string; runId?: string | null }
  | { kind: 'sample' | 'demo' | 'file'; label: string };

interface AppStore {
  appState: AppState;
  activeRunId: string | null;
  dataSource: DataSourceMeta | null;
  zoomLevel: ZoomLevel;
  selectedNodeId: string | null;
  expandedNodeIds: Set<string>;
  alerts: ResponsibilityAlert[];
  graph: GraphData;
  graphMetrics: GraphMetrics | null;
  /** Correlation evidence from the active db (import or backend download). */
  evidence: CorrelationEvidence[];
  /** Claims / tracebacks / intents / actions from the active db (import or backend download). */
  claims: ResponsibilityClaim[];
  tracebacks: ResponsibilityTraceback[];
  intents: Intent[];
  actions: Action[];
  overview: DatasetOverview | null;
  monitoring: MonitoringState | null;
  /** M0 全行深链资产（db 导入时填充；sample/demo/file → null，四功能空态兜底）。 */
  dbAssets: DbAssets | null;
  /** 图时间演化当前帧索引（db 导入时指向最新帧，保持态势页初始视图不变）。 */
  snapshotFrameIndex: number;
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
  setSnapshotFrameIndex: (index: number) => void;
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
  loadSample: () => Promise<void>;
  loadDemo: () => void;
  loadSnapshot: (snapshot: SnapshotImport) => void;
  /** Apply a SQLite db import (from a local .db file) to the store. */
  loadDatabase: (dbImport: DbImport) => void;
  onAlertReceived: (alert: ResponsibilityAlert) => void;
  onRunActiveChanged: (active: boolean) => void;
  /** 实时层切片（LiveSession 驱动；高频原始事件不入 store，走 LiveSession 缓冲）。 */
  live: LiveState;
  setLiveStatus: (status: LiveConnectionStatus) => void;
  setLiveRun: (run: LiveRunMeta) => void;
  setLiveStats: (patch: Partial<LiveStats>) => void;
  /** run 结束：live 态转回 replay（LiveSession 检测 active→null 时调用）。 */
  markRunEnded: () => void;
  /** 运行监控派生结果（直播中周期重派生覆盖；导入/回放时 db 派生写入）。 */
  setMonitoring: (monitoring: MonitoringState | null) => void;
  /** 图时间演化实时帧（直播中由 LiveSession 周期拉取 append；db 导入时用 dbAssets.snapshotFrames）。 */
  liveSnapshotFrames: GraphSnapshot[];
  setLiveSnapshotFrames: (frames: GraphSnapshot[]) => void;
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
  // LOW-8：同帧突发多告警时按时间降序前插，最新置顶（前端 list 约定最新在前）
  const sorted = [...fresh].sort((a, b) => b.timestamp_ns - a.timestamp_ns);
  // 剥离 graph_snapshot 后再入 alerts——图已同步应用到 graph（onAlertReceived），
  // 驻留完整图快照浪费内存（审查 LOW-10），与 db 导入路径一致。
  const lean = sorted.map(({ graph_snapshot: _graph_snapshot, ...rest }: any) => rest);
  set({ alerts: [...lean, ...state.alerts].slice(0, 200) });
  fresh.forEach((a) => state.engineRef.current?.showAlertPulse(a));
}

/** Apply a local (non-API) data snapshot to the store. */
function applySnapshot(
  set: (partial: Partial<AppStore>) => void,
  opts: {
    graph: GraphData;
    alerts: ResponsibilityAlert[];
    metrics: GraphMetrics;
    source: Extract<DataSourceMeta, { kind: 'sample' | 'demo' | 'file' }>;
  },
): void {
  const leanAlerts = opts.alerts.map(({ graph_snapshot: _graph_snapshot, ...rest }: any) => rest);
  set({
    graph: opts.graph,
    alerts: leanAlerts,
    graphMetrics: opts.metrics,
    evidence: [],
    claims: [],
    tracebacks: [],
    intents: [],
    actions: [],
    overview: null,
    monitoring: null,
    dbAssets: null,
    snapshotFrameIndex: 0,
    dataSource: opts.source,
    activeRunId: null,
    selectedNodeId: null,
    appState: 'replay',
    errorMessage: null,
    liveSnapshotFrames: [], // 直播帧仅在 live 会话期间有效
  });
}

/**
 * Apply a SQLite db import. The db is the source of truth for evidence too
 * (correlation_evidence), so it populates `evidence` alongside graph/alerts.
 */
function applyDbImport(
  set: (partial: Partial<AppStore>) => void,
  dbImport: DbImport,
  source: Extract<DataSourceMeta, { kind: 'db' }>,
): void {
  const leanAlerts = dbImport.alerts.map(
    ({ graph_snapshot: _graph_snapshot, ...rest }: any) => rest,
  );
  set({
    graph: dbImport.graph,
    alerts: leanAlerts,
    graphMetrics: dbImport.metrics,
    evidence: dbImport.evidence,
    claims: dbImport.claims,
    tracebacks: dbImport.tracebacks,
    intents: dbImport.intents,
    actions: dbImport.actions,
    overview: dbImport.overview,
    monitoring: dbImport.monitoring,
    dbAssets: dbImport.dbAssets,
    snapshotFrameIndex: Math.max(0, dbImport.dbAssets.snapshotFrames.length - 1),
    dataSource: source,
    activeRunId: source.runId ?? null,
    selectedNodeId: null,
    appState: 'replay',
    errorMessage: null,
    // 直播帧随 run 结束/导入清空，防陈旧帧遮蔽导入的时间演化帧（审查 MEDIUM-3）
    liveSnapshotFrames: [],
  });
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
  dataSource: null,
  zoomLevel: 0,
  selectedNodeId: null,
  expandedNodeIds: new Set(),
  alerts: [],
  graph: { nodes: [], edges: [] },
  graphMetrics: null,
  evidence: [],
  claims: [],
  tracebacks: [],
  intents: [],
  actions: [],
  overview: null,
  monitoring: null,
  dbAssets: null,
  snapshotFrameIndex: 0,
  timelinePosition: 0,
  investigationContext: DEFAULT_INVESTIGATION_CONTEXT,
  errorMessage: null,
  engineRef: { current: null },
  live: DEFAULT_LIVE_STATE,

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
  setSnapshotFrameIndex: (index) => set({ snapshotFrameIndex: index }),
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
    set({
      activeRunId: runId,
      dataSource: { kind: 'run', runId },
      appState: 'replay',
      errorMessage: null,
    });

    // The real run artifacts live in collector/collector.responsibility.db, so
    // the preferred path downloads the raw db and consumes it with sql.js (the
    // same code path as a local .db import). Fall back to the API contract when
    // the backend lacks the db endpoint or the db yields nothing consumable.
    try {
      const buffer = await api.getRunDb(runId);
      const dbImport = await loadDatabaseFromBytes(new Uint8Array(buffer), runId);
      if (dbImport.hasConsumableData) {
        applyDbImport(set, dbImport, { kind: 'db', label: runId, runId });
        return;
      }
    } catch (_dbError) {
      // No db artifact on this backend — fall through to the API path.
    }

    try {
      const [graph, alerts] = await Promise.all([api.getGraph(runId), api.getAlerts(runId)]);

      // Strip graph_snapshot from alerts
      const leanAlerts = alerts.map(({ graph_snapshot: _graph_snapshot, ...rest }: any) => rest);

      set({
        graph: { nodes: graph.nodes, edges: graph.edges },
        graphMetrics: deriveGraphMetrics({ nodes: graph.nodes, edges: graph.edges }),
        alerts: leanAlerts,
        evidence: [],
        claims: [],
        tracebacks: [],
        intents: [],
        actions: [],
        overview: null,
        monitoring: null,
        dbAssets: null,
        snapshotFrameIndex: 0,
        selectedNodeId: null,
        liveSnapshotFrames: [],
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      // 数据源不可用也清空 monitoring，防错误态残留上一次导入的运行监控信号
      set({ errorMessage: message, appState: 'error', monitoring: null, liveSnapshotFrames: [] });
    }
  },

  loadSample: async () => {
    const data = await loadSampleData();
    if (!data) throw new Error('样例数据不可用（sample-run.json 未找到）');

    // 内置样例 db：让「样例运行」同时携带 db 资产（dbAssets/actions/intents/evidence 等），
    // 使 db 驱动视图（仪表盘/告警/追踪等）开箱可见。加载失败静默降级为纯图快照（与旧行为一致）。
    let dbImport: DbImport | null = null;
    try {
      const res = await fetch('/data/db-sample.sqlite');
      if (res.ok) {
        const parsed = await loadDatabaseFromBytes(
          new Uint8Array(await res.arrayBuffer()),
          '样例运行.db',
        );
        if (parsed.hasConsumableData) dbImport = parsed;
      }
    } catch {
      dbImport = null;
    }

    // 样例 = 态势演示图快照（sample-run.json）+ 内置真实 db 资产（db 驱动视图）。
    // 两者 agent 命名空间互不相交（UUID vs agent-a…d），why facets 不会跨 run 错配。
    const leanAlerts = data.alerts.map(({ graph_snapshot: _graph_snapshot, ...rest }: any) => rest);
    set({
      graph: data.graph,
      alerts: leanAlerts,
      graphMetrics: data.metrics,
      evidence: dbImport?.evidence ?? [],
      claims: dbImport?.claims ?? [],
      tracebacks: dbImport?.tracebacks ?? [],
      intents: dbImport?.intents ?? [],
      actions: dbImport?.actions ?? [],
      overview: dbImport?.overview ?? null,
      monitoring: dbImport?.monitoring ?? null,
      dbAssets: dbImport?.dbAssets ?? null,
      snapshotFrameIndex: dbImport ? Math.max(0, dbImport.dbAssets.snapshotFrames.length - 1) : 0,
      dataSource: { kind: 'sample', label: '样例运行' },
      activeRunId: null,
      selectedNodeId: null,
      appState: 'replay',
      errorMessage: null,
      // MEDIUM-3：样例加载同样清空直播帧（此前唯一漏清的 loader，陈旧帧遮蔽样例图）
      liveSnapshotFrames: [],
    });
  },
  loadDemo: () => {
    applySnapshot(set, {
      graph: demoGraph,
      alerts: demoAlerts,
      metrics: deriveGraphMetrics(demoGraph),
      source: { kind: 'demo', label: '演示数据' },
    });
  },
  loadSnapshot: (snapshot) => {
    applySnapshot(set, {
      graph: snapshot.graph,
      alerts: snapshot.alerts,
      metrics: snapshot.metrics,
      source: { kind: 'file', label: snapshot.label },
    });
  },
  loadDatabase: (dbImport) => {
    applyDbImport(set, dbImport, { kind: 'db', label: dbImport.label });
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
    // 活跃 run 进入实时态（徽章/事件流自动打开依赖 appState==='live'）
    if (active && get().appState !== 'live') set({ appState: 'live' });
    if (!active && get().appState === 'live') set({ appState: 'replay' });
  },
  setLiveStatus: (status) => set((s) => ({ live: { ...s.live, status } })),
  setLiveRun: (run) => set((s) => ({ live: { ...s.live, run } })),
  setLiveStats: (patch) =>
    set((s) => ({ live: { ...s.live, stats: { ...s.live.stats, ...patch } } })),
  markRunEnded: () => {
    if (get().appState === 'live') set({ appState: 'replay' });
  },
  setMonitoring: (monitoring) => set({ monitoring }),
  liveSnapshotFrames: [],
  setLiveSnapshotFrames: (frames) => {
    // 按 snapshot_id 去重 + 时间正序合并；默认指向最新帧（时间轴直播推进）。
    // 容量封顶 200 帧——长直播防无界内存（审查 MEDIUM-3）。
    const seen = new Set<string>();
    const merged = [...get().liveSnapshotFrames, ...frames].filter((f) => {
      if (seen.has(f.snapshot_id)) return false;
      seen.add(f.snapshot_id);
      return true;
    });
    const capped = merged.slice(-200);
    // LOW-5：无新帧（合并后数量不变）时不重置 index——不覆盖用户手动拖动的时间轴位置
    const added = capped.length !== get().liveSnapshotFrames.length;
    set({
      liveSnapshotFrames: capped,
      ...(added ? { snapshotFrameIndex: Math.max(0, capped.length - 1) } : {}),
    });
  },
}));

// ── Data loader: API (live) → sample JSON → demo fixtures ──
async function initData() {
  try {
    // Try runs list — works with both Rust backend and Python data server
    const runs = await api.getRuns();
    const runList = (runs as any)?.runs ?? runs ?? [];
    if (runList.length > 0) {
      // Find first run with data, preferring the latest. The collector's run
      // summaries expose artifact_availability (a real SQLite db counts as
      // usable data); fall back to the legacy has_data field when absent.
      const runHasData = (r: any) => {
        const avail = r.artifact_availability;
        if (!avail) return r.has_data !== false;
        return (
          avail.alerts === true ||
          avail.correlation_evidence === true ||
          avail.events === true ||
          avail.database === true
        );
      };
      const firstWithData = runList.find(runHasData) ?? runList[0];
      await useAppStore.getState().loadRun(firstWithData.run_id);
      const state = useAppStore.getState();
      if (state.graph.nodes.length > 0 || state.alerts.length > 0) {
        console.log(
          `[MtAgentLens] Loaded run ${firstWithData.run_id}: ${state.graph.nodes.length} nodes, ` +
            `${state.graph.edges.length} edges, ${state.alerts.length} alerts (${state.dataSource?.kind})`,
        );
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
      const leanAlerts = data.alerts.map(
        ({ graph_snapshot: _graph_snapshot, ...rest }: any) => rest,
      );
      console.log(
        `[MtAgentLens] Sample data: ${data.graph.nodes.length} nodes, ${data.graph.edges.length} edges, ${leanAlerts.length} alerts`,
      );
      useAppStore.setState({
        graph: data.graph,
        alerts: leanAlerts,
        graphMetrics: data.metrics,
        monitoring: null,
        dataSource: { kind: 'sample', label: '样例运行' },
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
    monitoring: null,
    dataSource: { kind: 'demo', label: '演示数据' },
  });
}

// Only run on client — SSR would fail and block rendering
if (typeof window !== 'undefined') {
  initData();
}

// Dev-only test hook — lets Playwright drive the store for live-update
// simulation (push alerts → sidebar/reactivity recomputes). Stripped from
// production builds by NODE_ENV, so it never ships.
if (typeof window !== 'undefined' && process.env.NODE_ENV !== 'production') {
  (window as unknown as { __MTA_LENS_STORE__?: typeof useAppStore }).__MTA_LENS_STORE__ =
    useAppStore;
}
