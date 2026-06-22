// Handoff Observability — Zustand store
// Manages delegation tree, metrics, events, and dependency edges.
// Uses types from handoff-observability-contract.ts for component compatibility.

import { create } from 'zustand';
import { handoffApi } from './handoff-api';
import { getDemoDelegationTreeResponse, demoDependencyEdges } from './handoff-demo-data';
import type {
  DelegationNode,
  HandoffObsMetrics,
  HandoffObservabilityEvent,
} from './handoff-observability-contract';

// ── Store types ──

export interface HandoffStore {
  // Data — using contract types for direct component compatibility
  delegationTree: DelegationNode | null;
  metrics: HandoffObsMetrics;
  recentEvents: HandoffObservabilityEvent[];
  dependencyEdges: Array<{
    from_spawn_id: string;
    to_spawn_id: string;
    artifact_path: string;
    is_orphaned: boolean;
  }>;
  selectedNodeId: string | null;

  // UI state
  loading: boolean;
  error: string | null;
  useBackend: boolean; // true when connected to live backend

  // Actions
  fetchFromApi: () => Promise<void>;
  loadDemoData: () => void;
  selectNode: (nodeId: string | null) => void;
  pushEvent: (event: HandoffObservabilityEvent) => void;
  handleSseEvent: (payload: unknown) => void;
  setUseBackend: (v: boolean) => void;
  reset: () => void;
}

const demoSeed = getDemoDelegationTreeResponse();

const initialState = {
  delegationTree: demoSeed.tree as DelegationNode | null,
  metrics: demoSeed.metrics as HandoffObsMetrics,
  recentEvents: [] as HandoffObservabilityEvent[],
  dependencyEdges: demoDependencyEdges as Array<{ from_spawn_id: string; artifact_path: string; is_orphaned: boolean; to_spawn_id: string }>,
  selectedNodeId: null as string | null,
  loading: false,
  error: null as string | null,
  useBackend: false,
};

export const useHandoffStore = create<HandoffStore>((set, get) => ({
  ...initialState,

  /** Fetch delegation tree and metrics from the backend API. */
  fetchFromApi: async () => {
    set({ loading: true, error: null, useBackend: true });
    try {
      const [treeResp, eventsResp, edgesResp] = await Promise.all([
        handoffApi.getDelegationTree(),
        handoffApi.getEvents({ limit: 50 }),
        handoffApi.getDependencyEdges(),
      ]);

      // Cast API response to contract types (shapes are compatible)
      const tree = treeResp.tree as unknown as DelegationNode | null;
      const metrics = treeResp.metrics as unknown as HandoffObsMetrics;
      const events = (eventsResp.events ?? []) as unknown as HandoffObservabilityEvent[];
      const edges = (edgesResp.edges ?? []).map((e) => ({
        from_spawn_id: String(e.from_spawn_id ?? ''),
        to_spawn_id: String(e.to_spawn_id ?? ''),
        artifact_path: String(e.artifact_path ?? ''),
        is_orphaned: Boolean(e.is_orphaned),
      }));

      set({
        delegationTree: tree,
        metrics,
        recentEvents: events,
        dependencyEdges: edges,
        loading: false,
        useBackend: true,
      });
    } catch (err) {
      console.warn('Handoff API unavailable, falling back to demo data:', err);
      get().loadDemoData();
    }
  },

  /** Load demo data for standalone development. */
  loadDemoData: () => {
    const demo = getDemoDelegationTreeResponse();
    set({
      delegationTree: demo.tree as unknown as DelegationNode,
      metrics: demo.metrics as unknown as HandoffObsMetrics,
      dependencyEdges: demoDependencyEdges,
      selectedNodeId: null,
      loading: false,
      error: null,
      useBackend: false,
    });
  },

  selectNode: (nodeId) => {
    set({ selectedNodeId: nodeId });
  },

  pushEvent: (event) => {
    set({ recentEvents: [event, ...get().recentEvents].slice(0, 200) });
  },

  handleSseEvent: (payload) => {
    const data = (payload ?? {}) as Record<string, unknown>;

    if (data.tree || data.metrics || data.edges) {
      set((state) => ({
        delegationTree: (data.tree as DelegationNode | undefined) ?? state.delegationTree,
        metrics: (data.metrics as HandoffObsMetrics | undefined) ?? state.metrics,
        dependencyEdges: (data.edges as typeof state.dependencyEdges | undefined) ?? state.dependencyEdges,
        loading: false,
        error: null,
        useBackend: true,
      }));
      return;
    }

    if (Array.isArray(data.events)) {
      set({
        recentEvents: (data.events as HandoffObservabilityEvent[]).slice(0, 200),
        loading: false,
        error: null,
        useBackend: true,
      });
      return;
    }

    if (typeof data.sub_type === 'string') {
      set({
        recentEvents: [data as unknown as HandoffObservabilityEvent, ...get().recentEvents].slice(0, 200),
        loading: false,
        error: null,
        useBackend: true,
      });
    }
  },

  setUseBackend: (v) => set({ useBackend: v }),

  reset: () => set({ ...initialState }),
}));
