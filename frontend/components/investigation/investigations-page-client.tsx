'use client';

import { useMemo, useCallback, useEffect, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import InvestigationWorkbench from './investigation-workbench';
import { useAppStore } from '@/lib/store';
import { buildInvestigationModel } from '@/lib/investigation';
import { buildInvestigationHref, parseInvestigationAlertId } from '@/lib/investigation-route';
import { useRunEvidence } from '@/lib/use-run-evidence';
import { useHashRouteState } from '@/lib/use-hash-route-state';

export default function InvestigationsPageClient() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const activeRunId = useAppStore((state) => state.activeRunId);
  const alerts = useAppStore((state) => state.alerts);
  const graph = useAppStore((state) => state.graph);
  const { selectedValue: alertId, setRouteValue: setHashAlertId } = useHashRouteState({
    searchParams,
    parseValue: parseInvestigationAlertId,
    buildHref: buildInvestigationHref,
  });

  const selectedAlert = useMemo(() => {
    if (!alerts.length) return null;
    return alerts.find((item) => item.alert_id === alertId) ?? alerts[0];
  }, [alertId, alerts]);

  const { evidenceItems, evidenceLoading, evidenceError } = useRunEvidence(activeRunId);

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
      evidenceLoading={evidenceLoading}
      evidenceError={evidenceError}
    />
  );
}
