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
  cameraResetRequest: number;
  resetCamera: () => void;
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
  /** 数据加载/导入进行中（init/loadRun/loadSample/importFile 期间 true，数据落库后 false）。
   *  全屏 Observation Orbit 据此保持覆盖，直到真实数据就绪——消除「加载器先淡出、数据后切换」的跳变。 */
  dataLoading: boolean;
  /** 当前页面内容渲染就绪（页面在内容提交后置 true；路由切换时布局层重置 false）。
   *  全屏加载层据此等待页面真正渲染完成，而非只看数据存在。 */
  pageReady: boolean;
  setDataLoading: (v: boolean) => void;
  setPageReady: (v: boolean) => void;

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
let loadGeneration = 0;
let alertBuffer: ResponsibilityAlert[] = [];
let flushHandle: number | null = null;

function discardPendingAlerts(): void {
  alertBuffer = [];
  if (flushHandle !== null && typeof cancelAnimationFrame === 'function')
    cancelAnimationFrame(flushHandle);
  flushHandle = null;
}

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
  // No hard cap: the DB-import path keeps every alert, so truncating the live
  // path made the same run show different counts in live and replay views.
  // The lean projection drops graph snapshots, which is where the memory was.
  set({ alerts: [...lean, ...state.alerts] });
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
    investigationContext: DEFAULT_INVESTIGATION_CONTEXT,
    expandedNodeIds: new Set(),
    selectedNodeId: null,
    appState: 'replay',
    errorMessage: null,
    liveSnapshotFrames: [], // 直播帧仅在 live 会话期间有效
    dataLoading: false, // 数据已落库 → 全屏 Orbit 可就绪淡出（仍等 pageReady/sidebarReady）
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
  appState: 'live' | 'replay' = 'replay',
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
    investigationContext: DEFAULT_INVESTIGATION_CONTEXT,
    expandedNodeIds: new Set(),
    selectedNodeId: null,
    appState,
    errorMessage: null,
    // 直播帧随 run 结束/导入清空，防陈旧帧遮蔽导入的时间演化帧（审查 MEDIUM-3）
    liveSnapshotFrames: [],
    dataLoading: false, // db 解析完成、数据落库 → 全屏 Orbit 可就绪淡出
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
  cameraResetRequest: 0,
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
  // 初始为「加载中」：initData 在客户端启动时异步加载数据，全屏 Orbit 覆盖首载
  dataLoading: false,
  pageReady: false,
  live: DEFAULT_LIVE_STATE,

  setAppState: (s) => set({ appState: s }),
  setDataLoading: (v) => set({ dataLoading: v }),
  setPageReady: (v) => set({ pageReady: v }),
  setActiveRunId: (runId) => set({ activeRunId: runId }),
  setZoomLevel: (l) => set({ zoomLevel: l }),
  resetCamera: () =>
    set((state) => ({
      cameraResetRequest: state.cameraResetRequest + 1,
      selectedNodeId: null,
      investigationContext: DEFAULT_INVESTIGATION_CONTEXT,
    })),
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
    const investigationContext = buildInvestigationContextFromAlert(alert, get().graph);
    set({ investigationContext });
  },
  setError: (msg) => set({ errorMessage: msg }),

  loadRun: async (runId) => {
    if (!runId) return;
    discardPendingAlerts();
    const generation = ++loadGeneration;
    const isCurrent = () => generation === loadGeneration && get().activeRunId === runId;
    const currentMode = () =>
      get().live.run.isActive && get().live.run.runId === runId
        ? ('live' as const)
        : ('replay' as const);
    // Re-loading the run that is currently live (e.g. initData racing the
    // LiveSession poll) must not flip the UI back to replay: the session
    // early-returns on the same run id and would never restore 'live'.
    const liveRun = get().live.run;
    const keepLive =
      get().appState === 'live' && liveRun?.isActive === true && liveRun.runId === runId;
    set({
      activeRunId: runId,
      graph: { nodes: [], edges: [] },
      graphMetrics: null,
      alerts: [],
      evidence: [],
      claims: [],
      tracebacks: [],
      intents: [],
      actions: [],
      overview: null,
      monitoring: null,
      dbAssets: null,
      liveSnapshotFrames: [],
      selectedNodeId: null,
      snapshotFrameIndex: 0,
      expandedNodeIds: new Set(),
      investigationContext: DEFAULT_INVESTIGATION_CONTEXT,
      dataSource: { kind: 'run', runId },
      ...(keepLive ? {} : { appState: 'replay' as const }),
      errorMessage: null,
      // 数据加载中：下载 db / sql.js 解析期间全屏 Orbit 保持覆盖
      dataLoading: true,
    });

    // The real run artifacts live in collector/collector.responsibility.db, so
    // the preferred path downloads the raw db and consumes it with sql.js (the
    // same code path as a local .db import). Fall back to the API contract when
    // the backend lacks the db endpoint or the db yields nothing consumable.
    try {
      const buffer = await api.getRunDb(runId);
      if (!isCurrent()) return;
      const dbImport = await loadDatabaseFromBytes(new Uint8Array(buffer), runId);
      if (!isCurrent()) return;
      if (dbImport.hasConsumableData) {
        applyDbImport(set, dbImport, { kind: 'db', label: runId, runId }, currentMode());
        return;
      }
    } catch (_dbError) {
      // No db artifact on this backend — fall through to the API path.
    }

    if (!isCurrent()) return;
    try {
      const [graph, alerts] = await Promise.all([api.getGraph(runId), api.getAlerts(runId)]);
      if (!isCurrent()) return;

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
        dataLoading: false, // API 回退路径同样结束加载
      });
    } catch (error) {
      if (!isCurrent()) return;
      const message = error instanceof Error ? error.message : String(error);
      // 数据源不可用也清空 monitoring，防错误态残留上一次导入的运行监控信号
      set({
        errorMessage: message,
        appState: 'error',
        monitoring: null,
        liveSnapshotFrames: [],
        dataLoading: false, // 失败也结束加载，避免全屏 Orbit 永久滞留
      });
    }
  },

  loadSample: async () => {
    discardPendingAlerts();
    const generation = ++loadGeneration;
    set({ dataLoading: true }); // 样例加载（含内置样例 db 解析）期间全屏 Orbit 覆盖
    const data = await loadSampleData();
    if (generation !== loadGeneration) return;
    if (!data) {
      set({ dataLoading: false });
      throw new Error('样例数据不可用（sample-run.json 未找到）');
    }

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

    if (generation !== loadGeneration) return;
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
      investigationContext: DEFAULT_INVESTIGATION_CONTEXT,
      expandedNodeIds: new Set(),
      appState: 'replay',
      errorMessage: null,
      // MEDIUM-3：样例加载同样清空直播帧（此前唯一漏清的 loader，陈旧帧遮蔽样例图）
      liveSnapshotFrames: [],
      dataLoading: false, // 样例数据落库 → 加载结束
    });
  },
  loadDemo: () => {
    discardPendingAlerts();
    ++loadGeneration;
    applySnapshot(set, {
      graph: demoGraph,
      alerts: demoAlerts,
      metrics: deriveGraphMetrics(demoGraph),
      source: { kind: 'demo', label: '演示数据' },
    });
  },
  loadSnapshot: (snapshot) => {
    discardPendingAlerts();
    ++loadGeneration;
    applySnapshot(set, {
      graph: snapshot.graph,
      alerts: snapshot.alerts,
      metrics: snapshot.metrics,
      source: { kind: 'file', label: snapshot.label },
    });
  },
  loadDatabase: (dbImport) => {
    discardPendingAlerts();
    ++loadGeneration;
    applyDbImport(set, dbImport, { kind: 'db', label: dbImport.label });
  },

  onAlertReceived: (alert) => {
    // Alerts are coalesced per animation frame; the graph snapshot below is
    // applied immediately so the live network stays responsive.
    alertBuffer.push(alert);
    if (flushHandle == null) scheduleAlertFlush(get, set);

    // The graph stage subscribes to graph and investigationContext directly.
    if (alert.graph_snapshot?.nodes?.length) {
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
    const runId = get().live.run.runId;
    if (active && runId && get().activeRunId !== runId) {
      ++loadGeneration;
      discardPendingAlerts();
      set({
        activeRunId: runId,
        dataSource: { kind: 'run', runId },
        appState: 'live',
        graph: { nodes: [], edges: [] },
        graphMetrics: null,
        alerts: [],
        evidence: [],
        claims: [],
        tracebacks: [],
        intents: [],
        actions: [],
        overview: null,
        monitoring: null,
        dbAssets: null,
        liveSnapshotFrames: [],
        selectedNodeId: null,
        snapshotFrameIndex: 0,
        investigationContext: DEFAULT_INVESTIGATION_CONTEXT,
        errorMessage: null,
        dataLoading: false,
      });
    } else if (active && get().appState !== 'live') set({ appState: 'live' });
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
    const previous = get().liveSnapshotFrames;
    const previousIds = new Set(previous.map((f) => f.snapshot_id));
    const byId = new Map(previous.map((f) => [f.snapshot_id, f]));
    for (const frame of frames) byId.set(frame.snapshot_id, frame);
    const merged = [...byId.values()].sort((a, b) => a.timestamp_ns - b.timestamp_ns);
    const capped = merged.slice(-200);
    // LOW-5：无新帧（合并后数量不变）时不重置 index——不覆盖用户手动拖动的时间轴位置
    const added = capped.some((f) => !previousIds.has(f.snapshot_id));
    set({
      liveSnapshotFrames: capped,
      ...(added ? { snapshotFrameIndex: Math.max(0, capped.length - 1) } : {}),
    });
  },
}));

// ── Data loader: API (live) → sample JSON → demo fixtures ──
export async function initializeData() {
  const generation = ++loadGeneration;
  const isCurrent = () => generation === loadGeneration;
  useAppStore.setState({ dataLoading: true });
  try {
    const activeRun = await api.getActiveRun().catch(() => null);
    if (!isCurrent()) return;
    if (activeRun?.run_id) {
      await useAppStore.getState().loadRun(activeRun.run_id);
      return;
    }
    const runs = await api.getRuns();
    if (!isCurrent()) return;
    const runList = (runs as any)?.runs ?? runs ?? [];
    if (runList.length > 0) {
      const usable = (run: any) => {
        const availability = run.artifact_availability;
        return !availability
          ? run.has_data !== false
          : Object.values(availability).some((value) => value === true);
      };
      const selected = runList.find(usable) ?? runList[0];
      await useAppStore.getState().loadRun(selected.run_id);
      return;
    }
  } catch {
    if (!isCurrent()) return;
  }
  try {
    const data = await loadSampleData();
    if (!isCurrent()) return;
    if (data) {
      applySnapshot((partial) => useAppStore.setState(partial), {
        graph: data.graph,
        alerts: data.alerts,
        metrics: data.metrics,
        source: { kind: 'sample', label: '样例运行' },
      });
      return;
    }
  } catch {
    if (!isCurrent()) return;
  }
  if (isCurrent()) useAppStore.getState().loadDemo();
}

// Only run on client — SSR would fail and block rendering
if (typeof window !== 'undefined') {
  void initializeData();
}

// Dev-only test hook — lets Playwright drive the store for live-update
// simulation (push alerts → sidebar/reactivity recomputes). Stripped from
// production builds by NODE_ENV, so it never ships.
if (typeof window !== 'undefined' && process.env.NODE_ENV !== 'production') {
  (window as unknown as { __MTA_LENS_STORE__?: typeof useAppStore }).__MTA_LENS_STORE__ =
    useAppStore;
}
