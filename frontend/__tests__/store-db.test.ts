import { beforeEach, describe, expect, test, vi } from 'vitest';
import { readFileSync } from 'node:fs';

// Isolate the store: no backend, no sample data.
vi.mock('@/lib/api', () => ({
  api: {
    getRuns: vi.fn().mockRejectedValue(new Error('no backend')),
    getActiveRun: vi.fn().mockResolvedValue(null),
    getGraph: vi.fn().mockResolvedValue({ nodes: [], edges: [] }),
    getAlerts: vi.fn().mockResolvedValue([]),
    getEvidence: vi
      .fn()
      .mockResolvedValue({ evidence: [], intents: [], actions: [], non_links: [] }),
    getRunDb: vi.fn().mockRejectedValue(new Error('no db endpoint')),
  },
}));
vi.mock('@/lib/load-sample-data', () => ({
  loadSampleData: vi.fn().mockResolvedValue(null),
}));

import { useAppStore } from '@/lib/store';
import { api } from '@/lib/api';
import { loadDatabase as loadDatabaseFromBytes } from '@/lib/db-import';
import type { ResponsibilityAlert } from '@/lib/types';

const fixtureBytes = (): ArrayBuffer => {
  const buf = readFileSync(new URL('./fixtures/db-sample.sqlite', import.meta.url));
  return buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength) as ArrayBuffer;
};

const graph = {
  nodes: [
    {
      node_id: 'n1',
      node_type: 'Agent',
      agent_id: 'a',
      resource: null,
      evidence_refs: [],
      timestamp_ns: 0,
      phase: { Provisional: { timestamp_ns: 0 } },
    },
  ],
  edges: [],
} as never;

const alert = {
  alert_id: 'a1',
  timestamp_ns: 0,
  title: '测试',
  severity: 'Critical',
  decision: 'Unsafe',
  confidence: 0.9,
  authority: 'Authoritative',
  reasoning: 'r',
  summary: 's',
  action_hint: 'h',
  display_profile: 'UnsafeByEngine',
  primary_agent: { agent_id: 'a', claimed_role: null },
  involved_agents: [],
  engine: { rule_verdicts: [], pattern_verdicts: [] },
  timeline: { steps: [], total_duration_ms: 0 },
  graph_snapshot: { snapshot_id: '', timestamp_ns: 0, nodes: [], edges: [], metrics: {} },
  phase: { Provisional: { timestamp_ns: 0 } },
  provisional_ref: null,
} as unknown as ResponsibilityAlert;

beforeEach(() => {
  vi.clearAllMocks();
  useAppStore.setState({
    dataSource: null,
    activeRunId: null,
    graph: { nodes: [], edges: [] },
    alerts: [],
    evidence: [],
    claims: [],
    tracebacks: [],
    intents: [],
    actions: [],
    overview: null,
    monitoring: null,
    errorMessage: null,
  });
});

describe('store — SQLite db data source', () => {
  test('loadDatabase applies graph/alerts/evidence from a db import', async () => {
    const dbImport = await loadDatabaseFromBytes(new Uint8Array(fixtureBytes()), 'run-42.db');
    useAppStore.getState().loadDatabase(dbImport);

    const s = useAppStore.getState();
    expect(s.graph.nodes).toHaveLength(3);
    expect(s.alerts).toHaveLength(2);
    expect(s.evidence).toHaveLength(3);
    expect(s.dataSource).toEqual({ kind: 'db', label: 'run-42.db' });
    expect(s.activeRunId).toBeNull();
    expect(s.appState).toBe('replay');
    // graph_snapshot is stripped from stored alerts (memory).
    expect(s.alerts[0]).not.toHaveProperty('graph_snapshot');

    // 全表消费：新表数据随 db import 一并写入 store
    expect(s.claims).toHaveLength(2);
    expect(s.tracebacks).toHaveLength(1);
    expect(s.intents).toHaveLength(3);
    expect(s.actions).toHaveLength(3);
    expect(s.overview).not.toBeNull();
    expect(s.overview?.claims.count).toBe(2);
    expect(s.overview?.intents.count).toBe(3);
  });

  test('loadRun downloads the db and applies it when the endpoint exists', async () => {
    vi.mocked(api.getRunDb).mockResolvedValueOnce(fixtureBytes());

    await useAppStore.getState().loadRun('run-1');

    const s = useAppStore.getState();
    expect(api.getRunDb).toHaveBeenCalledWith('run-1');
    expect(api.getGraph).not.toHaveBeenCalled();
    expect(s.graph.nodes).toHaveLength(3);
    expect(s.alerts).toHaveLength(2);
    expect(s.evidence).toHaveLength(3);
    expect(s.dataSource).toEqual({ kind: 'db', label: 'run-1', runId: 'run-1' });
    expect(s.activeRunId).toBe('run-1');
    expect(s.appState).toBe('replay');
  });

  test('loadRun falls back to the API contract when the db endpoint is missing', async () => {
    vi.mocked(api.getRunDb).mockRejectedValueOnce(new Error('HTTP 404'));
    vi.mocked(api.getGraph).mockResolvedValueOnce(graph);
    vi.mocked(api.getAlerts).mockResolvedValueOnce([alert] as never);

    await useAppStore.getState().loadRun('run-1');

    const s = useAppStore.getState();
    expect(api.getGraph).toHaveBeenCalledWith('run-1');
    expect(s.dataSource).toEqual({ kind: 'run', runId: 'run-1' });
    expect(s.graph.nodes).toHaveLength(1);
    expect(s.alerts).toHaveLength(1);
    expect(s.evidence).toEqual([]);
  });

  test('switching from a db import to demo clears db-only fields', async () => {
    // 1. Import a db — this populates claims/tracebacks/intents/actions/overview
    const dbImport = await loadDatabaseFromBytes(new Uint8Array(fixtureBytes()), 'run-42.db');
    useAppStore.getState().loadDatabase(dbImport);
    expect(useAppStore.getState().claims).toHaveLength(2);
    expect(useAppStore.getState().overview).not.toBeNull();

    // 2. Switch to demo data — should clear the db-only fields
    useAppStore.getState().loadDemo();

    const s = useAppStore.getState();
    expect(s.claims).toEqual([]);
    expect(s.tracebacks).toEqual([]);
    expect(s.intents).toEqual([]);
    expect(s.actions).toEqual([]);
    expect(s.overview).toBeNull();
    // Non-db fields are still populated from demo
    expect(s.graph.nodes.length).toBeGreaterThan(0);
    expect(s.alerts.length).toBeGreaterThan(0);
  });

  test('loadRun reports an error when both the db and API paths fail', async () => {
    vi.mocked(api.getRunDb).mockRejectedValueOnce(new Error('HTTP 404'));
    vi.mocked(api.getGraph).mockRejectedValueOnce(new Error('HTTP 404'));
    // 先注入 db（monitoring 有值），验证错误路径同样清空——错误态不残留上一次运行监控信号
    const dbImport = await loadDatabaseFromBytes(new Uint8Array(fixtureBytes()), 'run-42.db');
    useAppStore.getState().loadDatabase(dbImport);
    expect(useAppStore.getState().monitoring).not.toBeNull();

    await useAppStore.getState().loadRun('run-1');

    const s = useAppStore.getState();
    expect(s.appState).toBe('error');
    expect(s.errorMessage).toBeTruthy();
    expect(s.monitoring).toBeNull();
  });

  test('loadDatabase 派生并写入 monitoring（含 6 视角 health）', async () => {
    const dbImport = await loadDatabaseFromBytes(new Uint8Array(fixtureBytes()), 'run-42.db');
    useAppStore.getState().loadDatabase(dbImport);

    const m = useAppStore.getState().monitoring;
    expect(m).not.toBeNull();
    expect(m?.sourceLabel).toBe('run-42.db');
    expect(typeof m?.derivedAtMs).toBe('number');
    expect(m?.health.topologyDrift.status).toBe('warning'); // fixture 漂移 0.5
    expect(m?.health.policyCompliance.available).toBe(true);
    expect(m?.topologyDrift?.frames).toHaveLength(3);
  });

  test('loadRun 走 db 分支同样写入 monitoring', async () => {
    vi.mocked(api.getRunDb).mockResolvedValueOnce(fixtureBytes());
    await useAppStore.getState().loadRun('run-1');
    expect(useAppStore.getState().monitoring?.sourceLabel).toBe('run-1');
  });

  test('loadRun API 回退分支置 monitoring 为 null', async () => {
    vi.mocked(api.getRunDb).mockRejectedValueOnce(new Error('HTTP 404'));
    vi.mocked(api.getGraph).mockResolvedValueOnce(graph);
    vi.mocked(api.getAlerts).mockResolvedValueOnce([alert] as never);
    // 先注入 db，再走 API 回退，验证残留被清空
    const dbImport = await loadDatabaseFromBytes(new Uint8Array(fixtureBytes()), 'run-42.db');
    useAppStore.getState().loadDatabase(dbImport);
    expect(useAppStore.getState().monitoring).not.toBeNull();

    await useAppStore.getState().loadRun('run-1');
    expect(useAppStore.getState().monitoring).toBeNull();
  });

  test('db 导入后切 demo 清空 monitoring 防残留', async () => {
    const dbImport = await loadDatabaseFromBytes(new Uint8Array(fixtureBytes()), 'run-42.db');
    useAppStore.getState().loadDatabase(dbImport);
    expect(useAppStore.getState().monitoring).not.toBeNull();

    useAppStore.getState().loadDemo();
    expect(useAppStore.getState().monitoring).toBeNull();
  });

  test('loadDatabase 填充 dbAssets 并初始化 snapshotFrameIndex 到最新帧', async () => {
    const dbImport = await loadDatabaseFromBytes(new Uint8Array(fixtureBytes()), 'run-42.db');
    useAppStore.getState().loadDatabase(dbImport);

    const s = useAppStore.getState();
    expect(s.dbAssets).not.toBeNull();
    expect(s.dbAssets?.processEvents).toHaveLength(7);
    expect(s.dbAssets?.snapshotFrames).toHaveLength(4);
    // 帧索引指向最新帧（frames.length-1），与 store.graph（最新帧）一致
    expect(s.snapshotFrameIndex).toBe(3);
  });

  test('setSnapshotFrameIndex 更新帧索引', async () => {
    const dbImport = await loadDatabaseFromBytes(new Uint8Array(fixtureBytes()), 'run-42.db');
    useAppStore.getState().loadDatabase(dbImport);
    useAppStore.getState().setSnapshotFrameIndex(1);
    expect(useAppStore.getState().snapshotFrameIndex).toBe(1);
  });

  test('db 导入后切 demo 清空 dbAssets 并复位帧索引', async () => {
    const dbImport = await loadDatabaseFromBytes(new Uint8Array(fixtureBytes()), 'run-42.db');
    useAppStore.getState().loadDatabase(dbImport);
    expect(useAppStore.getState().dbAssets).not.toBeNull();

    useAppStore.getState().loadDemo();
    expect(useAppStore.getState().dbAssets).toBeNull();
    expect(useAppStore.getState().snapshotFrameIndex).toBe(0);
  });
});
