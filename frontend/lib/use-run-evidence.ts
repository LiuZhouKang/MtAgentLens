'use client';

import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { demoEvidence } from '@/lib/demo-data';

type EvidenceResponse = Awaited<ReturnType<typeof api.getEvidence>>;

export function useRunEvidence(activeRunId: string | null) {
  const evidenceQuery = useQuery<EvidenceResponse>({
    queryKey: ['run-evidence', activeRunId],
    enabled: !!activeRunId,
    retry: false,
    queryFn: async () => {
      if (!activeRunId) {
        return {
          evidence: [],
          intents: [],
          actions: [],
          non_links: [],
        };
      }

      return api.getEvidence(activeRunId);
    },
  });

  const evidenceItems = useMemo(
    () => (activeRunId ? evidenceQuery.data?.evidence ?? [] : demoEvidence),
    [activeRunId, evidenceQuery.data?.evidence],
  );

  return {
    evidenceItems,
    evidenceQuery,
    evidenceLoading: !!activeRunId && evidenceQuery.isLoading,
    evidenceError: activeRunId && evidenceQuery.error instanceof Error ? evidenceQuery.error.message : null,
  };
}
