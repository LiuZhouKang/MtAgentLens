// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

// Isolate the store: initData falls through to demo fixtures with no network.
vi.mock('@/lib/api', () => ({
  api: {
    getRuns: vi.fn().mockRejectedValue(new Error('no backend')),
    getGraph: vi.fn().mockResolvedValue({ nodes: [], edges: [] }),
    getAlerts: vi.fn().mockResolvedValue([]),
    getEvidence: vi.fn().mockResolvedValue({
      evidence: [
        {
          evidence_id: 'ev1',
          intent_id: 'i1',
          action_id: 'act1',
          match_level: 'L1Exact',
          confidence: 0.95,
          matched_fields: { canonical_path: null, host: null, port: null, command: null, raw_args: [] },
          derivation_mode: 'deterministic',
          phase: { Provisional: { timestamp_ns: 0 } },
          timestamp_ns: 0,
        },
      ],
      intents: [],
      actions: [],
      non_links: [],
    }),
  },
}));
vi.mock('@/lib/load-sample-data', () => ({
  loadSampleData: vi.fn().mockResolvedValue(null),
}));

import { useRunEvidence } from '@/lib/use-run-evidence';
import { useAppStore } from '@/lib/store';
import { demoEvidence } from '@/lib/demo-data';
import { api } from '@/lib/api';

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  useAppStore.setState({
    activeRunId: null,
    dataSource: { kind: 'demo', label: '演示数据' },
    graph: { nodes: [], edges: [] },
    alerts: [],
  });
});

afterEach(() => {
  cleanup();
});

describe('useRunEvidence — data-source aware evidence loading', () => {
  it('returns demo fixtures when the data source is demo', () => {
    useAppStore.setState({ dataSource: { kind: 'demo', label: '演示数据' } });
    const { result } = renderHook(() => useRunEvidence(null), { wrapper: createWrapper() });
    expect(result.current.evidenceItems).toEqual(demoEvidence);
    expect(result.current.evidenceLoading).toBe(false);
    expect(result.current.evidenceError).toBeNull();
  });

  it('returns empty evidence for local sample imports (no backend payload)', () => {
    useAppStore.setState({
      dataSource: { kind: 'sample', label: '样例运行' },
      activeRunId: null,
    });
    const { result } = renderHook(() => useRunEvidence(null), { wrapper: createWrapper() });
    expect(result.current.evidenceItems).toEqual([]);
    expect(result.current.evidenceLoading).toBe(false);
  });

  it('returns empty evidence for imported file snapshots', () => {
    useAppStore.setState({
      dataSource: { kind: 'file', label: 'run-42.json' },
      activeRunId: null,
    });
    const { result } = renderHook(() => useRunEvidence(null), { wrapper: createWrapper() });
    expect(result.current.evidenceItems).toEqual([]);
  });

  it('fetches evidence from the backend for an active run', async () => {
    useAppStore.setState({
      dataSource: { kind: 'run', runId: 'run-1' },
      activeRunId: 'run-1',
    });
    const { result } = renderHook(() => useRunEvidence('run-1'), { wrapper: createWrapper() });

    expect(result.current.evidenceLoading).toBe(true);
    await waitFor(() => expect(result.current.evidenceLoading).toBe(false));
    expect(result.current.evidenceItems).toHaveLength(1);
    expect(result.current.evidenceItems[0].evidence_id).toBe('ev1');
    expect(result.current.evidenceError).toBeNull();
  });

  it('does not issue a backend request for local data sources', () => {
    useAppStore.setState({ dataSource: { kind: 'sample', label: '样例运行' }, activeRunId: null });
    const { result } = renderHook(() => useRunEvidence(null), { wrapper: createWrapper() });
    // DemoEvidence import forces the module to exist; assert items came from local branch.
    expect(result.current.evidenceItems).toEqual([]);
    expect(vi.mocked(api.getEvidence)).not.toHaveBeenCalled();
  });
});
