import { beforeEach, describe, expect, test, vi } from 'vitest';
import { readFileSync } from 'node:fs';

// Isolate the store: no backend, no sample data.
vi.mock('@/lib/api', () => ({
  api: {
    getRuns: vi.fn().mockRejectedValue(new Error('no backend')),
    getActiveRun: vi.fn().mockResolvedValue(null),
    getGraph: vi.fn().mockResolvedValue({ nodes: [], edges: [] }),
    getAlerts: vi.fn().mockResolvedValue([]),
    getEvidence: vi.fn().mockResolvedValue({ evidence: [], intents: [], actions: [], non_links: [] }),
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

  test('loadRun reports an error when both the db and API paths fail', async () => {
    vi.mocked(api.getRunDb).mockRejectedValueOnce(new Error('HTTP 404'));
    vi.mocked(api.getGraph).mockRejectedValueOnce(new Error('HTTP 404'));

    await useAppStore.getState().loadRun('run-1');

    const s = useAppStore.getState();
    expect(s.appState).toBe('error');
    expect(s.errorMessage).toBeTruthy();
  });
});
