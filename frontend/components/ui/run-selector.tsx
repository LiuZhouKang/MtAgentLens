'use client';

import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { useAppStore } from '@/lib/store';
import type { GraphData, ResponsibilityAlert } from '@/lib/types';

export default function RunSelector() {
  const store = useAppStore();

  const runsQuery = useQuery({
    queryKey: ['runs'],
    queryFn: api.getRuns,
    refetchInterval: 5000,
  });

  // When a run is selected, load its data
  const handleRunSelect = async (runId: string) => {
    if (!runId) return;

    store.setAppState('replay');

    try {
      // Load graph data
      const graph = await api.getGraph(runId);
      store.setGraph(graph.nodes, graph.edges);

      // Load alerts
      const alerts = await api.getAlerts(runId);
      store.setAlerts(alerts);
    } catch (err) {
      store.setError(String(err));
    }
  };

  const runs = runsQuery.data ?? [];

  return (
    <div className="pointer-events-auto">
      <select
        className="bg-transparent text-xs text-white/60 border border-white/[0.08] rounded-lg px-2 py-1 outline-none focus:border-[#818cf8]/40"
        value={store.activeRunId ?? ''}
        onChange={(e) => handleRunSelect(e.target.value)}
      >
        <option value="" disabled>
          {runs.length > 0 ? '选择 Run...' : '无历史 Run'}
        </option>
        {runs.map((r: { run_id: string }) => (
          <option key={r.run_id} value={r.run_id}>
            {r.run_id.slice(0, 12)}...
          </option>
        ))}
      </select>
    </div>
  );
}
