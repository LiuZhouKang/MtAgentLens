import { beforeEach, describe, expect, test, vi } from 'vitest';
import { readFileSync } from 'node:fs';

// Isolate the store: initData falls through to demo fixtures with no network.
vi.mock('@/lib/api', () => ({
  api: {
    getRuns: vi.fn().mockRejectedValue(new Error('no backend')),
    getActiveRun: vi.fn().mockResolvedValue(null),
    getGraph: vi.fn().mockResolvedValue({ nodes: [], edges: [] }),
    getAlerts: vi.fn().mockResolvedValue([]),
  },
}));
vi.mock('@/lib/load-sample-data', () => ({
  loadSampleData: vi.fn().mockResolvedValue(null),
}));

import { useAppStore } from '@/lib/store';
import { loadSampleData } from '@/lib/load-sample-data';
import { demoAlerts } from '@/lib/demo-data';
import type { SnapshotImport } from '@/lib/snapshot-import';
import type { ResponsibilityAlert } from '@/lib/types';

const graph = {
  nodes: [
    {
      node_id: 'n1',
      node_type: 'Agent',
      agent_id: 'a',
      resource: null,
      evidence_refs: [],
      timestamp_ns: 0,
      phase: 'Provisional',
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
  primary_agent: { agent_id: 'a', confidence: 0.9, role: null },
  involved_agents: [],
  rule_verdicts: [],
  pattern_verdicts: [],
  evidence: [],
  behavior_timeline: [],
  engine: { rule_verdicts: [], pattern_verdicts: [] },
} as unknown as ResponsibilityAlert;

const snapshot: SnapshotImport = {
  id: 'file:test.json',
  label: 'test.json',
  graph,
  alerts: [alert],
  metrics: {
    node_counts: { Agent: 1 },
    edge_counts: {},
    agent_count: 1,
    cross_agent_edges: 0,
    delegation_depth: 0,
    taint_chains: 0,
    avg_confidence: 0,
  },
  nodeCount: 1,
  edgeCount: 0,
  alertCount: 1,
};

/** loadSampleData() 返回的样例图快照（仅 graph/alerts/metrics）。 */
const sampleData = {
  graph,
  alerts: [alert],
  metrics: {
    node_counts: { Agent: 1 },
    edge_counts: {},
    agent_count: 1,
    cross_agent_edges: 0,
    delegation_depth: 0,
    taint_chains: 0,
    avg_confidence: 0,
  },
};

/** 内置样例 db（public/data/db-sample.sqlite 的同源 fixture）。 */
const sampleDbBytes = (): ArrayBuffer => {
  const buf = readFileSync(new URL('./fixtures/db-sample.sqlite', import.meta.url));
  return buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength) as ArrayBuffer;
};

beforeEach(() => {
  useAppStore.setState({
    dataSource: null,
    activeRunId: null,
    graph: { nodes: [], edges: [] },
    alerts: [],
    errorMessage: null,
  });
});

describe('store — data sources', () => {
  test('dataSource starts as null', () => {
    expect(useAppStore.getState().dataSource).toBeNull();
  });

  test('loadDemo loads demo fixtures and marks the source as demo', () => {
    useAppStore.getState().loadDemo();
    const s = useAppStore.getState();
    expect(s.alerts.length).toBe(demoAlerts.length);
    expect(s.dataSource).toEqual({ kind: 'demo', label: '演示数据' });
    expect(s.activeRunId).toBeNull();
  });

  test('loadSnapshot applies an imported file snapshot', () => {
    useAppStore.getState().loadSnapshot(snapshot);
    const s = useAppStore.getState();
    expect(s.graph.nodes).toHaveLength(1);
    expect(s.alerts).toHaveLength(1);
    expect(s.dataSource).toEqual({ kind: 'file', label: 'test.json' });
    expect(s.activeRunId).toBeNull();
  });

  test('loadRun marks the source as a backend run', async () => {
    await useAppStore.getState().loadRun('run-1');
    const s = useAppStore.getState();
    expect(s.activeRunId).toBe('run-1');
    expect(s.dataSource).toEqual({ kind: 'run', runId: 'run-1' });
  });

  test('loadSample 附带内置样例 db 资产（dbAssets/actions/intents）→ db 驱动视图开箱可见', async () => {
    vi.mocked(loadSampleData).mockResolvedValueOnce(sampleData as never);
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        arrayBuffer: async () => sampleDbBytes(),
      }) as unknown as typeof fetch,
    );
    try {
      await useAppStore.getState().loadSample();
      const s = useAppStore.getState();
      // db 资产来自内置样例 db（3 actions / 3 intents / 全量 dbAssets）
      expect(s.dbAssets).not.toBeNull();
      expect(s.actions).toHaveLength(3);
      expect(s.intents).toHaveLength(3);
      expect(s.dbAssets!.snapshotFrames.length).toBeGreaterThan(0);
      // 态势演示图快照不变（不退化）
      expect(s.graph.nodes).toHaveLength(1);
      expect(s.dataSource).toEqual({ kind: 'sample', label: '样例运行' });
      expect(s.activeRunId).toBeNull();
    } finally {
      vi.unstubAllGlobals();
    }
  });

  test('内置样例 db 不可用时 loadSample 降级为纯图快照（旧行为）', async () => {
    vi.mocked(loadSampleData).mockResolvedValueOnce(sampleData as never);
    vi.stubGlobal(
      'fetch',
      vi.fn().mockRejectedValue(new Error('network')) as unknown as typeof fetch,
    );
    try {
      await useAppStore.getState().loadSample();
      const s = useAppStore.getState();
      expect(s.dbAssets).toBeNull();
      expect(s.actions).toHaveLength(0);
      expect(s.intents).toHaveLength(0);
      expect(s.graph.nodes).toHaveLength(1);
      expect(s.dataSource).toEqual({ kind: 'sample', label: '样例运行' });
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
