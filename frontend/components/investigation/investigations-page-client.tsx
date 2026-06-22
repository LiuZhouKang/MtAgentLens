'use client';

import { useMemo, useCallback, useEffect, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { useQuery } from '@tanstack/react-query';
import InvestigationWorkbench from './investigation-workbench';
import { useAppStore } from '@/lib/store';
import { api } from '@/lib/api';
import { buildInvestigationModel } from '@/lib/investigation';
import { buildInvestigationHref, parseInvestigationAlertId } from '@/lib/investigation-route';
import { demoEvidence } from '@/lib/demo-data';

export default function InvestigationsPageClient() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const activeRunId = useAppStore((state) => state.activeRunId);
  const alerts = useAppStore((state) => state.alerts);
  const graph = useAppStore((state) => state.graph);
  const [hashAlertId, setHashAlertId] = useState<string | null>(null);

  useEffect(() => {
    const syncHashAlert = () => {
      setHashAlertId(parseInvestigationAlertId(new URLSearchParams(), window.location.hash));
    };

    syncHashAlert();
    window.addEventListener('hashchange', syncHashAlert);
    return () => window.removeEventListener('hashchange', syncHashAlert);
  }, []);

  const alertId = useMemo(
    () => parseInvestigationAlertId(searchParams, hashAlertId ? `#alertId=${encodeURIComponent(hashAlertId)}` : null),
    [hashAlertId, searchParams],
  );

  const selectedAlert = useMemo(() => {
    if (!alerts.length) return null;
    return alerts.find((item) => item.alert_id === alertId) ?? alerts[0];
  }, [alertId, alerts]);

  const evidenceQuery = useQuery({
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

  const model = useMemo(() => {
    if (!selectedAlert) return null;
    return buildInvestigationModel(selectedAlert, graph, evidenceItems);
  }, [evidenceItems, graph, selectedAlert]);

  const handleSelectAlert = useCallback((nextAlertId: string) => {
    const nextHref = buildInvestigationHref(nextAlertId);
    setHashAlertId(nextAlertId);

    if (typeof window !== 'undefined') {
      window.history.replaceState(window.history.state, '', nextHref);
      return;
    }

    router.replace(nextHref);
  }, [router]);

  return (
    <InvestigationWorkbench
      alert={selectedAlert}
      model={model}
      activeRunId={activeRunId}
      availableAlerts={alerts}
      onSelectAlert={handleSelectAlert}
      evidenceLoading={!!activeRunId && evidenceQuery.isLoading}
      evidenceError={activeRunId && evidenceQuery.error instanceof Error ? evidenceQuery.error.message : null}
    />
  );
}
