import { beforeEach, describe, expect, it, vi } from 'vitest';
import { DEFAULT_LIVE_STATE } from '@/lib/live/live-types';

// 阻止 store 模块加载时 initData 触发真实网络请求
vi.mock('@/lib/api', () => ({
  api: {
    getRuns: vi.fn().mockResolvedValue([]),
    getActiveRun: vi.fn().mockResolvedValue(null),
    getRunDb: vi.fn().mockRejectedValue(new Error('no db')),
    getGraph: vi.fn().mockRejectedValue(new Error('no graph')),
    getAlerts: vi.fn().mockRejectedValue(new Error('no alerts')),
  },
}));

type StoreModule = typeof import('@/lib/store');

describe('AppStore live 切片', () => {
  let useAppStore: StoreModule['useAppStore'];

  beforeEach(async () => {
    vi.resetModules();
    vi.clearAllMocks();
    const storeModule = await import('@/lib/store');
    useAppStore = storeModule.useAppStore;
    useAppStore.setState({
      appState: 'replay',
      live: DEFAULT_LIVE_STATE,
    });
  });

  it('starting another live run clears evidence from the previous source', () => {
    useAppStore.setState({
      activeRunId: 'archive',
      alerts: [{ alert_id: 'archive-alert' }] as never,
    });
    useAppStore
      .getState()
      .setLiveRun({ runId: 'new-live', startedAtNs: 1, endedAtNs: null, isActive: true });
    useAppStore.getState().onRunActiveChanged(true);
    expect(useAppStore.getState().activeRunId).toBe('new-live');
    expect(useAppStore.getState().alerts).toEqual([]);
    expect(useAppStore.getState().dataSource).toEqual({ kind: 'run', runId: 'new-live' });
  });

  it('live 切片默认态：idle 离线', () => {
    const live = useAppStore.getState().live;
    expect(live.status).toBe('idle');
    expect(live.run.isActive).toBe(false);
    expect(live.run.runId).toBeNull();
  });

  it('setLiveStatus/setLiveRun/setLiveStats 更新切片', () => {
    useAppStore.getState().setLiveStatus('connected');
    useAppStore
      .getState()
      .setLiveRun({ runId: 'r1', startedAtNs: 1000, endedAtNs: null, isActive: true });
    useAppStore.getState().setLiveStats({ eventCount: 5 });

    const s = useAppStore.getState();
    expect(s.live.status).toBe('connected');
    expect(s.live.run.runId).toBe('r1');
    expect(s.live.run.isActive).toBe(true);
    expect(s.live.stats.eventCount).toBe(5);
  });

  it('setLiveStats 部分合并不清空其他统计字段', () => {
    useAppStore.getState().setLiveStats({ eventCount: 3 });
    useAppStore.getState().setLiveStats({ droppedCount: 2 });
    const stats = useAppStore.getState().live.stats;
    expect(stats.eventCount).toBe(3);
    expect(stats.droppedCount).toBe(2);
    expect(stats.alertCount).toBe(0);
  });

  it('markRunEnded 在 live 态下转回 replay', () => {
    useAppStore.setState({ appState: 'live' });
    useAppStore.getState().markRunEnded();
    expect(useAppStore.getState().appState).toBe('replay');
  });

  it('markRunEnded 在非 live 态下不动 appState', () => {
    useAppStore.setState({ appState: 'replay' });
    useAppStore.getState().markRunEnded();
    expect(useAppStore.getState().appState).toBe('replay');
  });

  it('setMonitoring 更新运行监控派生', () => {
    useAppStore.getState().setMonitoring(null);
    expect(useAppStore.getState().monitoring).toBeNull();
  });

  it('onRunActiveChanged(true) 进入实时态；false 转回 replay', () => {
    useAppStore.getState().onRunActiveChanged(true);
    expect(useAppStore.getState().appState).toBe('live');
    useAppStore.getState().onRunActiveChanged(false);
    expect(useAppStore.getState().appState).toBe('replay');
  });

  it('loadRun 重载当前直播 run 不把 appState 打回 replay', async () => {
    useAppStore.getState().onRunActiveChanged(true);
    useAppStore
      .getState()
      .setLiveRun({ runId: 'live', startedAtNs: null, endedAtNs: null, isActive: true });

    const loading = useAppStore.getState().loadRun('live');
    // 初始 set 同步执行：live 必须保留（LIVE-04 竞态）
    expect(useAppStore.getState().appState).toBe('live');
    await loading.catch(() => undefined);
  });

  it('loadRun 加载其他 run 仍转 replay', async () => {
    useAppStore.getState().onRunActiveChanged(true);
    useAppStore
      .getState()
      .setLiveRun({ runId: 'live', startedAtNs: null, endedAtNs: null, isActive: true });

    const loading = useAppStore.getState().loadRun('archive-run');
    expect(useAppStore.getState().appState).toBe('replay');
    await loading.catch(() => undefined);
  });

  it('setLiveSnapshotFrames 封顶 200 帧（防无界内存）', () => {
    const metrics = {
      node_counts: {},
      edge_counts: {},
      agent_count: 0,
      cross_agent_edges: 0,
      delegation_depth: 0,
      taint_chains: 0,
      avg_confidence: 0,
    };
    const frames = Array.from({ length: 250 }, (_, i) => ({
      snapshot_id: `s${i}`,
      timestamp_ns: i,
      nodes: [],
      edges: [],
      metrics,
    }));
    useAppStore.getState().setLiveSnapshotFrames(frames);
    expect(useAppStore.getState().liveSnapshotFrames.length).toBe(200);
    expect(useAppStore.getState().liveSnapshotFrames[0].snapshot_id).toBe('s50');
  });

  it('setLiveSnapshotFrames 无新帧时不重置 index（不覆盖手动拖动）', () => {
    const metrics = {
      node_counts: {},
      edge_counts: {},
      agent_count: 0,
      cross_agent_edges: 0,
      delegation_depth: 0,
      taint_chains: 0,
      avg_confidence: 0,
    };
    const f1 = { snapshot_id: 's1', timestamp_ns: 1, nodes: [], edges: [], metrics };
    useAppStore.getState().setLiveSnapshotFrames([f1]);
    useAppStore.setState({ snapshotFrameIndex: 0 });
    // 重复同步同一帧 → 无新增 → index 保持 0
    useAppStore.getState().setLiveSnapshotFrames([f1]);
    expect(useAppStore.getState().snapshotFrameIndex).toBe(0);
    // 新帧 → 推进到最新
    const f2 = { snapshot_id: 's2', timestamp_ns: 2, nodes: [], edges: [], metrics };
    useAppStore.getState().setLiveSnapshotFrames([f1, f2]);
    expect(useAppStore.getState().snapshotFrameIndex).toBe(1);
  });

  it('advances a full live timeline and replaces refreshed frames in timestamp order', () => {
    const metrics = {
      node_counts: {},
      edge_counts: {},
      agent_count: 0,
      cross_agent_edges: 0,
      delegation_depth: 0,
      taint_chains: 0,
      avg_confidence: 0,
    };
    const frame = (i: number) => ({
      snapshot_id: `s${i}`,
      timestamp_ns: i,
      nodes: [],
      edges: [],
      metrics,
    });
    useAppStore.getState().setLiveSnapshotFrames(Array.from({ length: 200 }, (_, i) => frame(i)));
    useAppStore.setState({ snapshotFrameIndex: 3 });
    useAppStore.getState().setLiveSnapshotFrames([frame(201), frame(200)]);
    expect(useAppStore.getState().snapshotFrameIndex).toBe(199);
    expect(useAppStore.getState().liveSnapshotFrames.at(-1)?.snapshot_id).toBe('s201');
    const updated = { ...frame(201), metrics: { ...metrics, agent_count: 7 } };
    useAppStore.setState({ snapshotFrameIndex: 4 });
    useAppStore.getState().setLiveSnapshotFrames([updated]);
    expect(useAppStore.getState().liveSnapshotFrames.at(-1)?.metrics.agent_count).toBe(7);
    expect(useAppStore.getState().snapshotFrameIndex).toBe(4);
  });

  it('同帧多告警按时间降序前插（最新置顶）', () => {
    const a1 = { alert_id: 'a1', timestamp_ns: 100 };
    const a2 = { alert_id: 'a2', timestamp_ns: 200 };
    useAppStore.getState().onAlertReceived(a1 as never);
    useAppStore.getState().onAlertReceived(a2 as never);
    const ids = useAppStore.getState().alerts.map((a) => a.alert_id);
    expect(ids[0]).toBe('a2'); // 最新在前
    expect(ids[1]).toBe('a1');
  });

  it('onAlertReceived 入 alerts 时剥离 graph_snapshot（防内存驻留完整图）', () => {
    const withSnap = {
      alert_id: 'a1',
      graph_snapshot: { nodes: [{ node_id: 'n1' }], edges: [] },
    };
    useAppStore.getState().onAlertReceived(withSnap as never);
    const stored = useAppStore.getState().alerts[0];
    expect(stored).toBeDefined();
    expect(stored).not.toHaveProperty('graph_snapshot');
  });

  it('setLiveSnapshotFrames 按 id 去重合并 + 指向最新帧', () => {
    const metrics = {
      node_counts: {},
      edge_counts: {},
      agent_count: 0,
      cross_agent_edges: 0,
      delegation_depth: 0,
      taint_chains: 0,
      avg_confidence: 0,
    };
    const f1 = { snapshot_id: 's1', timestamp_ns: 1, nodes: [], edges: [], metrics };
    const f2 = { snapshot_id: 's2', timestamp_ns: 2, nodes: [], edges: [], metrics };
    useAppStore.getState().setLiveSnapshotFrames([f1]);
    useAppStore.getState().setLiveSnapshotFrames([f1, f2]); // s1 去重
    expect(useAppStore.getState().liveSnapshotFrames.length).toBe(2);
    expect(useAppStore.getState().snapshotFrameIndex).toBe(1);
  });
});
