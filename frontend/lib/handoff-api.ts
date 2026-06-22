// Handoff Observability — REST API client
// Matches HANDOFF_API_CONTRACT.md v3.1 endpoints

const BASE = '';

async function fetchJson<T>(url: string): Promise<T> {
  const res = await fetch(`${BASE}${url}`);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ message: res.statusText }));
    throw new Error(err.message || `HTTP ${res.status}`);
  }
  const body = await res.json();
  // Backend wraps responses in ApiResponse { data, authority }
  // Some endpoints return ApiResponse, others return data directly.
  return (body.data ?? body) as T;
}

// ── Typed response interfaces (matching backend Rust structs) ──

export interface DelegationNode {
  node_id: string;
  agent_id: string;
  parent_node_id: string | null;
  children?: DelegationNode[];
  chain_depth: number;
  spawned_at_ms: number;
  completed_at_ms: number | null;
  bytes_in: number;
  bytes_out: number;
  token_count: number;
  context_overhead_ratio: number;
  idle_breakdown: IdleTimeBreakdown;
  model_assigned: string | null;
  model_optimal: string | null;
  child_ids: string[];
}

export interface IdleTimeBreakdown {
  queued_awaiting_dispatch_ms: number;
  context_injection_io_ms: number;
  model_inference_queueing_ms: number;
  tool_call_round_trip_ms: number;
}

export interface HandoffObsMetrics {
  total_spawned: number;
  total_completed: number;
  active_count: number;
  max_chain_depth: number;
  fan_out_ratio: number;
  fan_out_critical: boolean;
  avg_context_overhead: number;
  orphaned_artifacts: number;
  workspace_tag_survival_rate: number;
}

export interface DelegationTreeResponse {
  tree: DelegationNode | null;
  tree_id: string;
  built_at_ms: number;
  metrics: HandoffObsMetrics;
}

export interface HandoffEventsResponse {
  events: Record<string, unknown>[];
  total: number;
  has_more: boolean;
}

export interface DependencyEdge {
  edge_id: string;
  from_spawn_id: string;
  to_spawn_id: string;
  artifact_path: string;
  is_orphaned: boolean;
  idle_ms: number;
  producer_agent_id: string;
  consumer_agent_id: string | null;
}

export interface DependencyEdgesResponse {
  edges: DependencyEdge[];
}

export interface HandoffProbeRequest {
  scope: 'probes' | 'stream' | 'runtime' | 'all';
  pid?: number | null;
  port?: number;
}

export interface HandoffProbeResponse {
  run_id: string;
  started_at_ms: number;
}

// ── API methods ──

export const handoffApi = {
  /** GET /api/handoff/delegation-tree — 完整委托树快照 */
  getDelegationTree: (): Promise<DelegationTreeResponse> =>
    fetchJson<DelegationTreeResponse>('/api/handoff/delegation-tree'),

  /** GET /api/handoff/metrics — 聚合指标 */
  getMetrics: (): Promise<HandoffObsMetrics> =>
    fetchJson<HandoffObsMetrics>('/api/handoff/metrics'),

  /** GET /api/handoff/events — 历史事件查询 */
  getEvents: (params?: {
    limit?: number;
    since_ms?: number;
    sub_type?: string;
  }): Promise<HandoffEventsResponse> => {
    const query = new URLSearchParams();
    if (params?.limit) query.set('limit', String(params.limit));
    if (params?.since_ms) query.set('since_ms', String(params.since_ms));
    if (params?.sub_type) query.set('sub_type', params.sub_type);
    const qs = query.toString();
    return fetchJson<HandoffEventsResponse>(`/api/handoff/events${qs ? `?${qs}` : ''}`);
  },

  /** GET /api/handoff/events/:id — 单条事件详情 */
  getEventById: (eventId: string): Promise<Record<string, unknown>> =>
    fetchJson<Record<string, unknown>>(`/api/handoff/events/${eventId}`),

  /** GET /api/handoff/dependency-edges — 跨代理依赖边 */
  getDependencyEdges: (): Promise<DependencyEdgesResponse> =>
    fetchJson<DependencyEdgesResponse>('/api/handoff/dependency-edges'),

  /** POST /api/handoff/probe — 启动诊断探测 */
  postProbe: (req: HandoffProbeRequest): Promise<HandoffProbeResponse> =>
    fetch(`${BASE}/api/handoff/probe`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(req),
    }).then(async (res) => {
      if (!res.ok) {
        const err = await res.json().catch(() => ({ message: res.statusText }));
        throw new Error(err.message || `HTTP ${res.status}`);
      }
      const body = await res.json();
      return (body.data ?? body) as HandoffProbeResponse;
    }),
};
