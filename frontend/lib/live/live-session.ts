'use client';

// LiveSession —— 前端实时层核心单例。
//
// 职责：
//  1. 轮询 /api/runs/active 发现活跃 run，驱动生命周期（live ↔ replay）。
//  2. 连接统一实时 SSE /api/live/events，分派 alert / 原始 event / lag。
//  3. 断线后 EventSource 自动重连；onopen 时拉 REST 快照 resync 补漏。
//  4. 原始事件 rAF 合帧写入环形缓冲（不入 zustand），统计摘要入 store。
//
// 可测性：`createLiveSession(deps)` 注入 IO（fetchActiveRun/fetchResync/
// openEventSource）与语义回调（onAlert/onLoadRun/onRunActiveChanged）；
// 低频状态（status/run/stats）直接写 store live 切片。

import { useAppStore } from '../store';
import { LIVE_EVENTS_URL, api } from '../api';
import type { RunSummary } from '../api';
import type { Action, ApiNonLinkDiagnostic, GraphSnapshot, Intent, ResponsibilityAlert } from '../types';
import { deriveLiveMonitoring } from './live-monitoring';
import {
  getLiveRawEvents,
  pushRawEvents,
  resetRawEvents,
} from './live-raw-events';
import type {
  LiveConnectionStatus,
  LiveRunMeta,
  LiveStats,
  LiveRawEvent,
} from './live-types';

/** 活跃 run 轮询间隔。 */
export const RUN_POLL_INTERVAL_MS = 2000;
/** 直播周期同步间隔（快照帧推进 + 运行监控重派生）。 */
export const LIVE_SYNC_INTERVAL_MS = 10000;

export interface LiveSessionDeps {
  fetchActiveRun: () => Promise<Pick<RunSummary, 'run_id' | 'started_at_ns'> | null>;
  /** 断线重连后的快照 resync：返回全量告警（含 graph_snapshot）用于补漏。 */
  fetchResync: (runId: string) => Promise<{ alerts: unknown[] }>;
  openEventSource: (url: string) => EventSource;
  /** 收到实时告警（含 graph_snapshot → 图演化）。 */
  onAlert: (alert: unknown) => void;
  /** run 结束后触发完整回放加载（下载 db 深调）。 */
  onLoadRun: (runId: string) => void;
  /** run 活跃态变化（true 进入实时 / false 转回放）。 */
  onRunActiveChanged: (active: boolean) => void;
  /** 直播周期同步：拉取图演化快照帧。 */
  fetchSnapshots: (runId: string) => Promise<GraphSnapshot[]>;
  /** 直播周期同步：拉取意图/动作/失配诊断（运行监控重派生）。 */
  fetchEvidence: (
    runId: string,
  ) => Promise<{ intents: Intent[]; actions: Action[]; non_links: ApiNonLinkDiagnostic[] }>;
}

export interface LiveSession {
  start(): void;
  stop(): void;
  getStatus(): LiveConnectionStatus;
  getStats(): LiveStats;
}

export function createLiveSession(deps: LiveSessionDeps): LiveSession {
  let es: EventSource | null = null;
  let pollTimer: ReturnType<typeof setTimeout> | null = null;
  let activeRunId: string | null = null;
  let stopped = false;

  let status: LiveConnectionStatus = 'idle';
  let stats: LiveStats = {
    connectedAtMs: null,
    eventRate: 0,
    eventCount: 0,
    alertCount: 0,
    droppedCount: 0,
    lastEventAtMs: null,
  };

  // 原始事件 rAF 合帧缓冲
  let eventBatch: LiveRawEvent[] = [];
  let rafHandle: number | null = null;
  let rateWindowStart = 0;
  let rateWindowCount = 0;
  let lastStatsPushMs = 0;

  function setStatus(s: LiveConnectionStatus): void {
    status = s;
    useAppStore.getState().setLiveStatus(s);
  }

  function setRunMeta(run: LiveRunMeta): void {
    useAppStore.getState().setLiveRun(run);
  }

  function pushStats(): void {
    // 节流到 1s：stats 属低频展示，避免 60fps 重渲染 LiveStatusBar（审查 LOW-6）
    const now = Date.now();
    if (now - lastStatsPushMs < 1000) return;
    lastStatsPushMs = now;
    useAppStore.getState().setLiveStats({ ...stats });
  }

  function noteEvents(count: number): void {
    const now = Date.now();
    stats.eventCount += count;
    stats.lastEventAtMs = now;
    if (rateWindowStart === 0) rateWindowStart = now;
    rateWindowCount += count;
    const elapsed = (now - rateWindowStart) / 1000;
    if (elapsed >= 1) {
      stats.eventRate = Math.round(rateWindowCount / Math.max(elapsed, 0.001));
      rateWindowStart = now;
      rateWindowCount = 0;
    }
    pushStats();
  }

  function flushRawBatch(): void {
    rafHandle = null;
    if (eventBatch.length === 0) return;
    const batch = eventBatch;
    eventBatch = [];
    pushRawEvents(batch);
    noteEvents(batch.length);
  }

  function enqueueRawEvent(ev: LiveRawEvent): void {
    eventBatch.push(ev);
    // 封顶兜底：后台标签页 rAF 被暂停时防 eventBatch 无界累积（审查 MEDIUM-5）
    if (eventBatch.length >= 256) {
      flushRawBatch();
      return;
    }
    if (rafHandle === null) {
      if (typeof requestAnimationFrame === 'function') {
        rafHandle = requestAnimationFrame(flushRawBatch) as unknown as number;
      } else {
        flushRawBatch(); // jsdom / node：无 rAF 时同步合帧
      }
    }
  }

  function resetForNewRun(): void {
    resetRawEvents();
    eventBatch = [];
    if (rafHandle !== null && typeof cancelAnimationFrame === 'function') {
      cancelAnimationFrame(rafHandle as number);
    }
    rafHandle = null;
    // MEDIUM-2：切 run 清空直播演化帧，防旧 run 帧混入新 run 时间轴
    useAppStore.getState().setLiveSnapshotFrames([]);
    stats = {
      connectedAtMs: null,
      eventRate: 0,
      eventCount: 0,
      alertCount: 0,
      droppedCount: 0,
      lastEventAtMs: null,
    };
    // 速率滑动窗口一并重置——切 run 后首个 1s 的事件速率不被旧窗口拉低（审查 LOW-7）
    rateWindowStart = 0;
    rateWindowCount = 0;
    lastStatsPushMs = 0;
    pushStats();
  }

  async function resync(): Promise<void> {
    if (!activeRunId) return;
    const runId = activeRunId;
    try {
      const { alerts } = await deps.fetchResync(runId);
      if (stopped || activeRunId !== runId) return; // MEDIUM-1/4：在途续体复核
      alerts.forEach((a) => deps.onAlert(a));
    } catch {
      // resync 失败静默——下次 onopen 再补
    }
  }

  function connect(): void {
    if (es) {
      es.close();
      es = null;
    }
    if (stopped) return;
    setStatus('connecting');
    es = deps.openEventSource(LIVE_EVENTS_URL);

    es.addEventListener('alert', (e) => {
      try {
        const alert = JSON.parse(e.data);
        stats.alertCount += 1;
        pushStats();
        deps.onAlert(alert);
      } catch {
        // 单条解析失败丢弃，不崩流
      }
    });

    es.addEventListener('event', (e) => {
      try {
        const raw = JSON.parse(e.data);
        enqueueRawEvent({
          id: e.lastEventId || '',
          timestampMs: raw.timestamp,
          timestampNs: raw.timestamp_ns,
          source: raw.source,
          pid: raw.pid,
          comm: raw.comm,
          data: raw.data,
        });
      } catch {
        // 丢弃
      }
    });

    es.addEventListener('lag', (e) => {
      try {
        const n = JSON.parse(e.data).dropped ?? 0;
        stats.droppedCount += n;
        pushStats();
      } catch {
        // ignore
      }
    });

    es.onopen = () => {
      stats.connectedAtMs = Date.now();
      setStatus('connected');
      void resync();
    };

    es.onerror = () => {
      setStatus('reconnecting');
    };
  }

  function disconnect(): void {
    if (es) {
      es.close();
      es = null;
    }
    setStatus('idle');
  }

  async function tick(): Promise<void> {
    let run: Pick<RunSummary, 'run_id' | 'started_at_ns'> | null = null;
    try {
      run = await deps.fetchActiveRun();
    } catch {
      // MEDIUM-1：stop 后在途续体不得再写 store / 触发副作用
      if (stopped) return;
      // 服务器不可达（record 进程退出 / 断网）：若曾有活跃 run，视为 run 结束转回放。
      // 否则静默保持现状（审查 HIGH-1：此前 fetch 失败被吞掉 → 永远卡重连）。
      if (activeRunId) {
        const ended = activeRunId;
        activeRunId = null;
        deps.onRunActiveChanged(false);
        deps.onLoadRun(ended);
        disconnect();
        setRunMeta({ runId: null, startedAtNs: null, endedAtNs: Date.now(), isActive: false });
      }
      return;
    }
    if (stopped) return; // MEDIUM-1

    if (!run) {
      // 活跃 run 结束 → 转回放深调 + 断流
      const ended = activeRunId;
      activeRunId = null;
      deps.onRunActiveChanged(false);
      if (ended) deps.onLoadRun(ended);
      disconnect();
      // LOW-7：run 结束清空原始事件缓冲，防回放态残留直播事件
      resetRawEvents();
      setRunMeta({ runId: null, startedAtNs: null, endedAtNs: Date.now(), isActive: false });
      return;
    }

    // 新 run 或 run 切换（run 非空 ⇒ runId 非空）
    const runId = run.run_id;
    if (runId === activeRunId) return;
    const isNew = activeRunId === null;
    activeRunId = runId;
    if (!isNew) resetForNewRun();
    setRunMeta({ runId, startedAtNs: run.started_at_ns ?? null, endedAtNs: null, isActive: true });
    deps.onRunActiveChanged(true);
    connect();
  }

  // ── 直播周期同步：图演化快照帧推进 + 运行监控派生视角重派生 ──
  let syncTimer: ReturnType<typeof setTimeout> | null = null;

  async function syncLiveRun(): Promise<void> {
    if (!activeRunId) return;
    const runId = activeRunId;
    try {
      const snapshots = await deps.fetchSnapshots(runId);
      if (stopped || activeRunId !== runId) return; // MEDIUM-1/4：stop 或 run 切换中止
      if (snapshots.length > 0) {
        useAppStore.getState().setLiveSnapshotFrames(snapshots);
      }
      const { intents, actions, non_links } = await deps.fetchEvidence(runId);
      if (stopped || activeRunId !== runId) return; // MEDIUM-4：证据与快照不得跨 run 混搭
      const monitoring = deriveLiveMonitoring({
        snapshots,
        alerts: useAppStore.getState().alerts,
        intents,
        actions,
        nonLinks: non_links,
      });
      useAppStore.getState().setMonitoring(monitoring);
    } catch {
      // 周期同步失败静默——下次再试
    }
  }

  function scheduleSync(): void {
    if (stopped) return; // MEDIUM-1：stop 后不得再排
    syncTimer = setTimeout(() => {
      void syncLiveRun().finally(() => {
        if (!stopped) scheduleSync();
      });
    }, LIVE_SYNC_INTERVAL_MS);
  }

  function schedulePoll(): void {
    if (stopped) return; // MEDIUM-1：stop 后不得再排
    pollTimer = setTimeout(() => {
      void tick().finally(() => {
        if (!stopped) schedulePoll();
      });
    }, RUN_POLL_INTERVAL_MS);
  }

  function start(): void {
    if (pollTimer !== null) return; // 已启动
    stopped = false;
    void tick().finally(() => {
      if (!stopped) schedulePoll();
    });
    scheduleSync();
  }

  function stop(): void {
    stopped = true;
    if (pollTimer !== null) {
      clearTimeout(pollTimer);
      pollTimer = null;
    }
    if (syncTimer !== null) {
      clearTimeout(syncTimer);
      syncTimer = null;
    }
    disconnect();
    activeRunId = null;
  }

  return {
    start,
    stop,
    getStatus: () => status,
    getStats: () => ({ ...stats }),
  };
}

// ── 生产单例：真实 deps 接线 ──────────────────────────────────────────────

let instance: LiveSession | null = null;

/** 全局启动实时会话（主布局挂载时调用）。 */
export function startLiveSession(): void {
  if (instance) return;
  instance = createLiveSession({
    fetchActiveRun: async () => {
      const run = await api.getActiveRun();
      if (!run) return null;
      return { run_id: run.run_id, started_at_ns: run.started_at_ns };
    },
    fetchResync: async (runId) => {
      const alerts = await api.getAlertsFull(runId);
      return { alerts };
    },
    openEventSource: (url) => new EventSource(url),
    onAlert: (alert) => {
      // 实时告警 → store 接收（rAF 合帧 + graph_snapshot 图演化）
      useAppStore.getState().onAlertReceived(alert as ResponsibilityAlert);
    },
    onLoadRun: (runId) => {
      void useAppStore.getState().loadRun(runId);
    },
    onRunActiveChanged: (active) => {
      useAppStore.getState().onRunActiveChanged(active);
    },
    fetchSnapshots: (runId) => api.getSnapshots(runId),
    fetchEvidence: (runId) => api.getEvidence(runId),
  });
  instance.start();
}

/** 全局停止实时会话（主布局卸载时调用）。 */
export function stopLiveSession(): void {
  instance?.stop();
  instance = null;
}

// 便于测试/调试读取
export { getLiveRawEvents };
