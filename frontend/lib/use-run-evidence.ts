'use client';

import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { useAppStore } from '@/lib/store';
import { demoEvidence } from '@/lib/demo-data';

type EvidenceResponse = Awaited<ReturnType<typeof api.getEvidence>>;

const EMPTY_EVIDENCE: EvidenceResponse = {
  evidence: [],
  intents: [],
  actions: [],
  non_links: [],
};

/**
 * Evidence comes from three sources: API-backed runs (kind 'run'), SQLite db
 * imports / backend-downloaded dbs (kind 'db' — evidence lives in the
 * correlation_evidence table), and the bundled demo fixtures. Local JSON
 * snapshots (sample / imported file) carry no correlation payload, so they
 * surface an empty list.
 */
export function useRunEvidence(activeRunId: string | null) {
  const dataSource = useAppStore((s) => s.dataSource);
  const appState = useAppStore((s) => s.appState);
  const storeEvidence = useAppStore((s) => s.evidence);
  const isApiBacked = dataSource?.kind === 'run' && !!activeRunId;
  const isDemo = dataSource?.kind === 'demo';
  const isDbBacked = dataSource?.kind === 'db';

  const evidenceQuery = useQuery<EvidenceResponse>({
    queryKey: ['run-evidence', activeRunId],
    enabled: isApiBacked,
    retry: false,
    // Evidence accumulates during a live run. A one-shot snapshot froze the
    // workbench while the monitoring panels refreshed the same data every
    // 10s, so two views of one run could disagree.
    refetchInterval: isApiBacked && appState === 'live' ? 10_000 : false,
    queryFn: async () => {
      if (!activeRunId) {
        return EMPTY_EVIDENCE;
      }

      return api.getEvidence(activeRunId);
    },
  });

  const evidenceItems = useMemo(() => {
    if (isDemo) return demoEvidence;
    if (isDbBacked) return storeEvidence;
    if (!isApiBacked) return EMPTY_EVIDENCE.evidence;
    return evidenceQuery.data?.evidence ?? EMPTY_EVIDENCE.evidence;
  }, [isDemo, isDbBacked, isApiBacked, storeEvidence, evidenceQuery.data?.evidence]);

  return {
    evidenceItems,
    evidenceQuery,
    evidenceLoading: isApiBacked && evidenceQuery.isLoading,
    evidenceError:
      isApiBacked && evidenceQuery.error instanceof Error ? evidenceQuery.error.message : null,
  };
}
