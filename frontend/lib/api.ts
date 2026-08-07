import type {
  ResponsibilityAlert,
  GraphData,
  GraphSnapshot,
  GraphDiff,
  CorrelationEvidence,
  Intent,
  Action,
  NonLinkDiagnostic,
} from './types';

const BASE = '';

// Bounded fetch — a dead or slow backend must never hang the dashboard's data
// loading indefinitely. On timeout it throws and callers (store.initData) fall
// back to the bundled sample data.
const FETCH_TIMEOUT_MS = 5000;

async function fetchJson<T>(url: string): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
  try {
    const res = await fetch(`${BASE}${url}`, { signal: controller.signal });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ message: res.statusText }));
      throw new Error(err.message || `HTTP ${res.status}`);
    }
    const body = await res.json();
    return body.data ?? body;
  } finally {
    clearTimeout(timer);
  }
}

/** Raw binary fetch — used for the run's SQLite db artifact (up to ~60MB). */
async function fetchBuffer(url: string, timeoutMs = FETCH_TIMEOUT_MS): Promise<ArrayBuffer> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(`${BASE}${url}`, { signal: controller.signal });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ message: res.statusText }));
      throw new Error(err.message || `HTTP ${res.status}`);
    }
    return await res.arrayBuffer();
  } finally {
    clearTimeout(timer);
  }
}

export interface RunSummary {
  run_id: string;
  started_at_ns: number;
  state: string;
  scenario_ids: string[];
}

export interface RunEvent {
  timestamp_ns: number;
  event_type: string;
  data: unknown;
}

export const api = {
  getRuns: (): Promise<RunSummary[]> => fetchJson<RunSummary[]>('/api/runs'),

  getActiveRun: (): Promise<RunSummary | null> => fetchJson<RunSummary | null>('/api/runs/active'),

  getRun: (id: string): Promise<RunSummary> => fetchJson<RunSummary>(`/api/runs/${id}`),

  getGraph: (id: string) => fetchJson<GraphData>(`/api/runs/${id}/graph`),

  /** Raw SQLite artifact (collector.responsibility.db) consumed via sql.js. */
  getRunDb: (id: string) => fetchBuffer(`/api/runs/${id}/db`, 30_000),

  getAlerts: (id: string) =>
    fetchJson<ResponsibilityAlert[]>(`/api/runs/${id}/alerts?lean=1`),

  getEvidence: (id: string) =>
    fetchJson<{
      evidence: CorrelationEvidence[];
      intents: Intent[];
      actions: Action[];
      non_links: NonLinkDiagnostic[];
    }>(`/api/runs/${id}/evidence`),

  getSnapshots: (id: string) => fetchJson<GraphSnapshot[]>(`/api/runs/${id}/snapshots`),

  getDiff: (id: string, against: string) =>
    fetchJson<GraphDiff>(`/api/runs/${id}/diff?against=${against}`),

  getEvents: (id: string, beforeTs?: number): Promise<RunEvent[]> => {
    const params = beforeTs ? `?before_ts=${beforeTs}` : '';
    return fetchJson<RunEvent[]>(`/api/runs/${id}/events${params}`);
  },

  getDemo: () =>
    fetchJson<{
      graph: GraphData;
      alerts: ResponsibilityAlert[];
    }>('/api/demo'),
};
