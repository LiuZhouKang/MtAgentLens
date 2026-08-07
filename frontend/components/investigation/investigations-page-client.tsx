'use client';

import { useCallback, useEffect, useMemo, useRef } from 'react';
import { useSearchParams } from 'next/navigation';
import { useGSAP } from '@gsap/react';
import gsap from 'gsap';
import InvestigationWorkbench from './investigation-workbench';
import AlertEmptyState from './alert-empty-state';
import LoadingIndicator from '@/components/ui/loading-indicator';
import { useAppStore } from '@/lib/store';
import { useAlertModels } from '@/lib/use-alert-models';
import { buildInvestigationHref, parseInvestigationAlertId } from '@/lib/investigation-route';
import { useRunEvidence } from '@/lib/use-run-evidence';
import { useHashRouteState } from '@/lib/use-hash-route-state';

export default function InvestigationsPageClient() {
  const sp = useSearchParams();
  const activeRunId = useAppStore(s => s.activeRunId);
  const alerts = useAppStore(s => s.alerts);
  const graph = useAppStore(s => s.graph);
  const dataSource = useAppStore(s => s.dataSource);
  const { selectedValue: hashAlertId, setRouteValue: setHashAlertId } = useHashRouteState({ searchParams: sp, parseValue: parseInvestigationAlertId, buildHref: buildInvestigationHref });
  const { evidenceItems, evidenceLoading, evidenceError } = useRunEvidence(activeRunId);

  const models = useAlertModels(alerts, graph, evidenceItems);
  const selected = useMemo(() => models.find(m => m.alert.alert_id === hashAlertId) ?? models[0] ?? null, [models, hashAlertId]);

  const handleSelectAlert = useCallback((alertId: string) => { setHashAlertId(alertId); }, [setHashAlertId]);

  useEffect(() => { if (!hashAlertId && models[0]) setHashAlertId(models[0].alert.alert_id); }, [models, hashAlertId, setHashAlertId]);

  const pageRef = useRef<HTMLDivElement>(null);
  useGSAP(() => {
    const tl = gsap.timeline({ defaults: { ease: 'power3.out' } });
    tl.fromTo('.ln-kpi-strip > *', { opacity: 0, y: 10 }, { opacity: 1, y: 0, duration: 0.4, stagger: 0.05, clearProps: 'transform' }, 0);
    tl.fromTo('.alert-list-btn', { opacity: 0, x: -8 }, { opacity: 1, x: 0, duration: 0.35, stagger: 0.04, clearProps: 'transform' }, 0.15);
  }, { scope: pageRef });

  return (
    <div className="ln-page__inner" ref={pageRef} style={{ maxWidth: 1300, margin: '0 auto', padding: '12px 14px' }}>
      {selected ? (
        selected.model ? (
          <InvestigationWorkbench alert={selected.alert} model={selected.model} activeRunId={activeRunId} availableAlerts={alerts} onSelectAlert={handleSelectAlert} evidenceLoading={evidenceLoading} evidenceError={evidenceError} />
        ) : (
          <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', minHeight: '60vh' }}>
            <LoadingIndicator size={44} label="正在构建调查模型…" />
          </div>
        )
      ) : (
        <AlertEmptyState
          dataSource={dataSource}
          graphNodeCount={graph.nodes.length}
          evidenceCount={evidenceItems.length}
        />
      )}
    </div>
  );
}
