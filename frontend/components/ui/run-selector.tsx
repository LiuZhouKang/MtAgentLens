'use client';

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { useAppStore } from '@/lib/store';
import PrismButton from '@/components/ui/prism-button';

export default function RunSelector() {
  const store = useAppStore();
  const [queryEnabled, setQueryEnabled] = useState(false);

  const runsQuery = useQuery({
    queryKey: ['runs'],
    queryFn: async () => {
      try {
        return await api.getRuns();
      } catch {
        return [];
      }
    },
    retry: false,
    enabled: queryEnabled,
    refetchInterval: (query) => ((query.state.data as unknown[] | undefined)?.length ? 5000 : false),
  });

  // When a run is selected, load its data
  const handleRunSelect = async (runId: string) => {
    if (!runId) return;
    await store.loadRun(runId);
  };

  const runs = runsQuery.data ?? [];
  const hasRuns = runs.length > 0;
  const shouldShowSelect = hasRuns || !!store.activeRunId;

  if (!shouldShowSelect) {
    return (
      <PrismButton
        className="pointer-events-auto"
        onClick={() => setQueryEnabled(true)}
        size="sm"
        variant="secondary"
      >
        {queryEnabled ? '无历史会话' : '观测会话'}
      </PrismButton>
    );
  }

  return (
    <div className="pointer-events-auto">
      <select
        className="prism-select"
        value={store.activeRunId ?? ''}
        onChange={(e) => handleRunSelect(e.target.value)}
        onFocus={() => setQueryEnabled(true)}
        onPointerDown={() => setQueryEnabled(true)}
      >
        <option value="" disabled>
          {runs.length > 0 ? '选择观测会话...' : '无历史会话'}
        </option>
        {runs.map((r: { run_id: string }) => (
          <option key={r.run_id} value={r.run_id}>
            会话 {r.run_id.slice(0, 12)}...
          </option>
        ))}
      </select>
    </div>
  );
}
