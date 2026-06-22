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

async function fetchJson<T>(url: string): Promise<T> {
  const res = await fetch(`${BASE}${url}`);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ message: res.statusText }));
    throw new Error(err.message || `HTTP ${res.status}`);
  }
  const body = await res.json();
  return body.data ?? body;
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
  getRuns: (): Promise<RunSummary[]> =>
    fetchJson<RunSummary[]>('/api/runs'),

  getActiveRun: (): Promise<RunSummary | null> =>
    fetchJson<RunSummary | null>('/api/runs/active'),

  getRun: (id: string): Promise<RunSummary> =>
    fetchJson<RunSummary>(`/api/runs/${id}`),

  getGraph: (id: string) =>
    fetchJson<GraphData>(`/api/runs/${id}/graph`),

  getAlerts: (id: string) =>
    fetchJson<ResponsibilityAlert[]>(`/api/runs/${id}/alerts`),

  getEvidence: (id: string) =>
    fetchJson<{
      evidence: CorrelationEvidence[];
      intents: Intent[];
      actions: Action[];
      non_links: NonLinkDiagnostic[];
    }>(`/api/runs/${id}/evidence`),

  getSnapshots: (id: string) =>
    fetchJson<GraphSnapshot[]>(`/api/runs/${id}/snapshots`),

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
