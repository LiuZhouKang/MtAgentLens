import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { DEFAULT_LIVE_STATE } from '@/lib/live/live-types';
import type { LiveSessionDeps } from '@/lib/live/live-session';

vi.mock('@/lib/api', () => ({
  LIVE_EVENTS_URL: '/api/live/events',
  api: {
    getRuns: vi.fn().mockResolvedValue([]),
    getActiveRun: vi.fn().mockResolvedValue(null),
    getAlertsFull: vi.fn(),
  },
}));

// 可手动触发的 Fake EventSource
class FakeES {
  static instances: FakeES[] = [];
  listeners = new Map<string, (e: { data: string; lastEventId?: string }) => void>();
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  constructor(public url: string) {
    FakeES.instances.push(this);
  }
  addEventListener(type: string, cb: (e: { data: string; lastEventId?: string }) => void) {
    this.listeners.set(type, cb);
  }
  close() {
    this.closed = true;
  }
  emit(type: string, data: string, lastEventId?: string) {
    this.listeners.get(type)?.({ data, lastEventId });
  }
}

type Deps = LiveSessionDeps;

function makeDeps(overrides: Partial<Deps> = {}): Deps {
  return {
    fetchActiveRun: vi.fn().mockResolvedValue({ run_id: 'r1', started_at_ns: 1000 }),
    fetchResync: vi.fn().mockResolvedValue({ alerts: [] }),
    openEventSource: ((url: string) => new FakeES(url) as unknown as EventSource) as Deps['openEventSource'],
    onAlert: vi.fn(),
    onLoadRun: vi.fn(),
    onRunActiveChanged: vi.fn(),
    fetchSnapshots: vi.fn().mockResolvedValue([]),
    fetchEvidence: vi.fn().mockResolvedValue({ intents: [], actions: [], non_links: [] }),
    ...overrides,
  };
}

type SessionModule = typeof import('@/lib/live/live-session');
type StoreModule = typeof import('@/lib/store');
type RawModule = typeof import('@/lib/live/live-raw-events');

describe('LiveSession —— 实时层核心', () => {
  let createLiveSession: SessionModule['createLiveSession'];
  let useAppStore: StoreModule['useAppStore'];
  let getLiveRawEvents: RawModule['getLiveRawEvents'];

  beforeEach(async () => {
    vi.resetModules();
    vi.clearAllMocks();
    vi.useFakeTimers();
    FakeES.instances = [];

    const raw = await import('@/lib/live/live-raw-events');
    getLiveRawEvents = raw.getLiveRawEvents;

    const storeModule = await import('@/lib/store');
    useAppStore = storeModule.useAppStore;
    useAppStore.setState({
      appState: 'replay',
      live: DEFAULT_LIVE_STATE,
    });

    const sessionModule = await import('@/lib/live/live-session');
    createLiveSession = sessionModule.createLiveSession;
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('发现活跃 run → 连接 SSE，URL 指向 /api/live/events', async () => {
    const s = createLiveSession(makeDeps());
    s.start();
    await Promise.resolve();
    await Promise.resolve();
    expect(FakeES.instances.length).toBe(1);
    expect(FakeES.instances[0].url).toBe('/api/live/events');
    expect(useAppStore.getState().live.run.runId).toBe('r1');
    expect(useAppStore.getState().live.run.isActive).toBe(true);
    s.stop();
  });

  it('alert 事件 → onAlert + store 统计 alertCount', async () => {
    const deps = makeDeps();
    const s = createLiveSession(deps);
    s.start();
    await Promise.resolve();
    await Promise.resolve();
    const es = FakeES.instances[0];
    es.emit('alert', JSON.stringify({ alert_id: 'a1', severity: 'Critical' }));
    expect(deps.onAlert).toHaveBeenCalledWith({ alert_id: 'a1', severity: 'Critical' });
    expect(useAppStore.getState().live.stats.alertCount).toBe(1);
    s.stop();
  });

  it('原始 event → 环形缓冲 + eventCount 统计', async () => {
    const s = createLiveSession(makeDeps());
    s.start();
    await Promise.resolve();
    await Promise.resolve();
    FakeES.instances[0].emit(
      'event',
      JSON.stringify({ timestamp: 1, timestamp_ns: 1000, source: 'tls_capture', pid: 8120, comm: 'claude', data: { len: 1200 } }),
      '7',
    );
    const buf = getLiveRawEvents();
    expect(buf.length).toBe(1);
    expect(buf[0].id).toBe('7');
    expect(buf[0].source).toBe('tls_capture');
    expect(buf[0].timestampNs).toBe(1000);
    expect(useAppStore.getState().live.stats.eventCount).toBe(1);
    s.stop();
  });

  it('lag 事件 → droppedCount 累计', async () => {
    const s = createLiveSession(makeDeps());
    s.start();
    await Promise.resolve();
    await Promise.resolve();
    FakeES.instances[0].emit('lag', '{"dropped":3}');
    expect(useAppStore.getState().live.stats.droppedCount).toBe(3);
    s.stop();
  });

  it('onopen → connected + resync 补漏', async () => {
    const alerts = [{ alert_id: 'h1' }, { alert_id: 'h2' }];
    const deps = makeDeps({
      fetchResync: vi.fn().mockResolvedValue({ alerts }),
    });
    const s = createLiveSession(deps);
    s.start();
    await Promise.resolve();
    await Promise.resolve();
    const es = FakeES.instances[0];
    es.onopen?.();
    await Promise.resolve();
    await Promise.resolve();
    expect(useAppStore.getState().live.status).toBe('connected');
    expect(deps.fetchResync).toHaveBeenCalledWith('r1');
    expect(deps.onAlert).toHaveBeenCalledTimes(2);
    s.stop();
  });

  it('run 结束（active→null）→ onLoadRun + 断流 + 转回放信号', async () => {
    let active: { run_id: string; started_at_ns: number } | null = {
      run_id: 'r1',
      started_at_ns: 1000,
    };
    const deps = makeDeps({ fetchActiveRun: vi.fn().mockImplementation(async () => active) });
    const s = createLiveSession(deps);
    s.start();
    await Promise.resolve();
    await Promise.resolve();
    const es = FakeES.instances[0];
    expect(es.closed).toBe(false);

    active = null;
    await vi.advanceTimersByTimeAsync(2000);
    expect(deps.onRunActiveChanged).toHaveBeenCalledWith(false);
    expect(deps.onLoadRun).toHaveBeenCalledWith('r1');
    expect(es.closed).toBe(true);
    expect(useAppStore.getState().live.run.isActive).toBe(false);
    s.stop();
  });

  it('fetchActiveRun 抛错且无活跃 run → 静默不崩、状态保持', async () => {
    const deps = makeDeps({ fetchActiveRun: vi.fn().mockRejectedValue(new Error('net')) });
    const s = createLiveSession(deps);
    s.start();
    await Promise.resolve();
    await Promise.resolve();
    expect(FakeES.instances.length).toBe(0); // 探测失败不连接
    expect(useAppStore.getState().live.status).toBe('idle');
    s.stop();
  });

  it('stop 后不再轮询（防僵尸会话）', async () => {
    const deps = makeDeps();
    const s = createLiveSession(deps);
    s.start();
    await Promise.resolve();
    await Promise.resolve();
    const fetchMock = deps.fetchActiveRun as unknown as ReturnType<typeof vi.fn>;
    const callsBefore = fetchMock.mock.calls.length;
    s.stop();
    await vi.advanceTimersByTimeAsync(6000);
    const callsAfter = fetchMock.mock.calls.length;
    expect(callsAfter).toBe(callsBefore); // stop 后无新轮询
  });

  it('直播中连续 fetch 失败才宣告 run 结束，且不重新拉取不可达后端', async () => {
    // A single 5s poll failure is not proof the run ended. Only a sustained
    // failure ends the live view, and it must keep the rendered data instead
    // of refetching the same unreachable backend (which turned the whole view
    // into an error banner).
    let mode: 'ok' | 'fail' = 'ok';
    const deps = makeDeps({
      fetchActiveRun: vi
        .fn()
        .mockImplementation(async () => (mode === 'ok' ? { run_id: 'r1', started_at_ns: 1000 } : Promise.reject(new Error('conn refused')))),
    });
    const s = createLiveSession(deps);
    s.start();
    await Promise.resolve();
    await Promise.resolve();
    expect(FakeES.instances.length).toBe(1);

    mode = 'fail';
    await vi.advanceTimersByTimeAsync(2000);
    await Promise.resolve();
    expect(deps.onRunActiveChanged).not.toHaveBeenCalledWith(false);
    expect(deps.onLoadRun).not.toHaveBeenCalled();
    expect(FakeES.instances[0].closed).toBe(false);

    // Backoff doubles after each failure (2s → 4s → 8s); one wide advance
    // drains the remaining attempts.
    await vi.advanceTimersByTimeAsync(60_000);
    await Promise.resolve();
    expect(deps.onRunActiveChanged).toHaveBeenCalledWith(false);
    expect(deps.onLoadRun).not.toHaveBeenCalled();
    expect(FakeES.instances[0].closed).toBe(true);
    expect(useAppStore.getState().live.status).toBe('idle');
    s.stop();
  });

  it('onerror → reconnecting，持续失败 → disconnected', async () => {
    const s = createLiveSession(makeDeps());
    s.start();
    await Promise.resolve();
    await Promise.resolve();
    FakeES.instances[0].onerror?.();
    expect(useAppStore.getState().live.status).toBe('reconnecting');
    FakeES.instances[0].onerror?.();
    FakeES.instances[0].onerror?.();
    expect(useAppStore.getState().live.status).toBe('disconnected');
    s.stop();
  });

  it('stop → 关闭 SSE 与轮询，状态回 idle', async () => {
    const s = createLiveSession(makeDeps());
    s.start();
    await Promise.resolve();
    await Promise.resolve();
    s.stop();
    expect(FakeES.instances[0].closed).toBe(true);
    expect(useAppStore.getState().live.status).toBe('idle');
  });

  it('周期同步：快照帧推进 + 运行监控重派生', async () => {
    useAppStore.setState({ appState: 'live', activeRunId: 'r1' });
    const snapshots = [
      {
        snapshot_id: 's1',
        timestamp_ns: 1,
        nodes: [],
        edges: [],
        metrics: {
          node_counts: { Agent: 3 },
          edge_counts: { IntentCausesAction: 2 },
          agent_count: 1,
          cross_agent_edges: 0,
          delegation_depth: 0,
          taint_chains: 0,
          avg_confidence: 0.8,
        },
      },
    ];
    const deps = makeDeps({
      fetchSnapshots: vi.fn().mockResolvedValue(snapshots),
      fetchEvidence: vi.fn().mockResolvedValue({ intents: [], actions: [], non_links: [] }),
    });
    const s = createLiveSession(deps);
    s.start();
    await Promise.resolve();
    await Promise.resolve();
    await vi.advanceTimersByTimeAsync(10000);
    await Promise.resolve();
    await Promise.resolve();
    expect(useAppStore.getState().liveSnapshotFrames.length).toBe(1);
    expect(useAppStore.getState().snapshotFrameIndex).toBe(0);
    expect(useAppStore.getState().monitoring?.sourceLabel).toBe('实时运行');
    s.stop();
  });

  it('does not replace archive monitoring with a background live run', async () => {
    const deps = makeDeps({ fetchSnapshots: vi.fn().mockResolvedValue([{ snapshot_id: 'live-only' }]) });
    const session = createLiveSession(deps);
    session.start();
    await Promise.resolve();
    await Promise.resolve();
    useAppStore.setState({ appState: 'replay', activeRunId: 'archive', liveSnapshotFrames: [], monitoring: null });
    await vi.advanceTimersByTimeAsync(10000);
    expect(useAppStore.getState().liveSnapshotFrames).toEqual([]);
    expect(useAppStore.getState().monitoring).toBeNull();
    session.stop();
  });
});
