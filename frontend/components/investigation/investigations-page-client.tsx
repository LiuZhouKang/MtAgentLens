'use client';

import { useCallback, useEffect, useMemo, useRef } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { useGSAP } from '@gsap/react';
import gsap from 'gsap';
import InvestigationWorkbench from './investigation-workbench';
import AlertEmptyState from './alert-empty-state';
import WhyAttribution, { WhyFacetEmptyState } from './why-attribution';
import LoadingIndicator from '@/components/ui/loading-indicator';
import { LiveRunningBadge } from '@/components/live/live-running-badge';
import { useAppStore } from '@/lib/store';
import { useAlertModels } from '@/lib/use-alert-models';
import {
  buildAlertHrefWithQuery,
  parseInvestigationAlertId,
  parseFacetSelection,
  buildFacetHref,
} from '@/lib/investigation-route';
import { buildWhyFacets, filterAlertsByFacet, EMPTY_WHY } from '@/lib/why-facets';
import { useRunEvidence } from '@/lib/use-run-evidence';
import { useHashRouteState } from '@/lib/use-hash-route-state';

export default function InvestigationsPageClient() {
  const sp = useSearchParams();
  const router = useRouter();
  const activeRunId = useAppStore((s) => s.activeRunId);
  const alerts = useAppStore((s) => s.alerts);
  const graph = useAppStore((s) => s.graph);
  const evidence = useAppStore((s) => s.evidence);
  const intents = useAppStore((s) => s.intents);
  const dbAssets = useAppStore((s) => s.dbAssets);
  const dataSource = useAppStore((s) => s.dataSource);
  const setPageReady = useAppStore((s) => s.setPageReady);
  // 分面内点选告警不丢分面：告警深链保留当前 query（?facet=）
  const buildAlertHref = useCallback(
    (id?: string | null) => buildAlertHrefWithQuery(id, sp.toString()),
    [sp],
  );
  const { selectedValue: hashAlertId, setRouteValue: setHashAlertId } = useHashRouteState({
    searchParams: sp,
    parseValue: parseInvestigationAlertId,
    buildHref: buildAlertHref,
  });
  const { evidenceItems } = useRunEvidence(activeRunId);

  // 分面：live（无 db）时强制 all，避免「残留 facet 参数 → 空态」
  const facet = parseFacetSelection(sp);
  const effectiveFacet = dbAssets ? facet : 'all';

  const why = useMemo(() => {
    if (!dbAssets) return EMPTY_WHY;
    return buildWhyFacets({
      alerts,
      pollutionEvents: dbAssets.pollutionEvents,
      policyEvaluations: dbAssets.policyEvaluations,
      nonLinkDiagnostics: dbAssets.nonLinkDiagnostics,
      intents,
      evidence,
      graph,
    });
  }, [dbAssets, alerts, intents, evidence, graph]);

  const filteredAlerts = useMemo(
    () => filterAlertsByFacet(alerts, why, effectiveFacet),
    [alerts, why, effectiveFacet],
  );

  const setFacet = useCallback(
    (next: typeof facet) => {
      router.replace(buildFacetHref(next, sp.toString(), window.location.hash), { scroll: false });
    },
    [router, sp],
  );

  const models = useAlertModels(filteredAlerts, graph, evidenceItems);
  const selected = useMemo(
    () => models.find((m) => m.alert.alert_id === hashAlertId) ?? models[0] ?? null,
    [models, hashAlertId],
  );

  // 页面内容就绪信号（供布局层协调加载层）：无告警 → 空态即内容；有告警 → 等待
  // 选中告警的 InvestigationModel 计算完成（否则页面显示「正在构建调查模型…」迷你
  // Orbit，会被全屏加载层覆盖——布局层据此保持覆盖直到真正就绪，杜绝第二加载动画）。
  useEffect(() => {
    setPageReady(alerts.length === 0 || selected?.model != null);
  }, [alerts.length, selected?.model, setPageReady]);

  const handleSelectAlert = useCallback(
    (alertId: string) => {
      setHashAlertId(alertId);
    },
    [setHashAlertId],
  );

  useEffect(() => {
    if (!hashAlertId && models[0]) setHashAlertId(models[0].alert.alert_id);
  }, [models, hashAlertId, setHashAlertId]);

  // 分面切换后，若 hash 指向的告警不在过滤集内，回写为首条（buildAlertHrefWithQuery 保留分面 query）。
  // 否则 UI 选中 models[0] 而 URL hash 仍指向被过滤掉的告警 → 选择状态与深链脱节。
  useEffect(() => {
    if (hashAlertId && models.length > 0 && !models.some((m) => m.alert.alert_id === hashAlertId)) {
      setHashAlertId(models[0].alert.alert_id);
    }
  }, [models, hashAlertId, setHashAlertId]);

  const pageRef = useRef<HTMLDivElement>(null);
  useGSAP(
    () => {
      if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
      const tl = gsap.timeline({ defaults: { ease: 'power3.out' } });
      tl.fromTo(
        '.ln-kpi-strip > *',
        { opacity: 0, y: 10 },
        { opacity: 1, y: 0, duration: 0.4, stagger: 0.05, clearProps: 'transform' },
        0,
      );
    },
    { scope: pageRef },
  );

  return (
    <div
      className="ln-page__inner"
      ref={pageRef}
      style={{ maxWidth: 1300, margin: '0 auto', padding: '12px 14px', position: 'relative' }}
    >
      <div style={{ position: 'absolute', top: 10, right: 12, zIndex: 5 }}>
        <LiveRunningBadge />
      </div>
      <div style={{ marginBottom: 12 }}>
        <WhyAttribution
          why={why}
          facet={effectiveFacet}
          onFacetChange={setFacet}
          alertCount={alerts.length}
          dbPresent={!!dbAssets}
        />
      </div>
      {selected ? (
        selected.model ? (
          <InvestigationWorkbench
            alert={selected.alert}
            model={selected.model}
            availableAlerts={filteredAlerts}
            onSelectAlert={handleSelectAlert}
          />
        ) : (
          <div
            style={{
              display: 'flex',
              justifyContent: 'center',
              alignItems: 'center',
              minHeight: '60vh',
            }}
          >
            <LoadingIndicator size={44} label="正在构建调查模型…" />
          </div>
        )
      ) : effectiveFacet !== 'all' ? (
        <WhyFacetEmptyState
          facet={effectiveFacet}
          filteredCount={alerts.length - filteredAlerts.length}
          onClear={() => setFacet('all')}
        />
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
