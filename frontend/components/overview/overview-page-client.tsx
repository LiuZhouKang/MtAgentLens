'use client';

import { useDeferredValue, useEffect, useMemo, useRef, useState } from 'react';
import dynamic from 'next/dynamic';
import Link from 'next/link';
import { useGSAP } from '@gsap/react';
import gsap from 'gsap';
import { ScrollTrigger } from 'gsap/ScrollTrigger';
import { MetricCard } from '@/components/ui/linear/metric-card';
import { SectionPanel } from '@/components/ui/linear/section-panel';
import { StatusBadge, type StatusTone } from '@/components/ui/linear/status-badge';
import OverviewDatasetPanel from '@/components/overview/dataset-overview-panel';
import MonitoringSection from '@/components/overview/monitoring/monitoring-section';
import AgentProcessTree from '@/components/overview/agent-process-tree';
import AgentFamilyTree from '@/components/overview/agent-family-tree';
import { TrendChart } from '@/components/ui/linear/trend-chart';
import LoadingScreen from '@/components/ui/loading-screen';
import NetworkLoading from '@/components/ui/network-loading';

gsap.registerPlugin(useGSAP, ScrollTrigger);

// Sigma.js uses WebGL — must load client-side only (no SSR)
const SigmaNetwork = dynamic(
  () => import('@/components/ui/linear/sigma-network').then((m) => m.SigmaNetwork),
  {
    ssr: false,
    loading: () => (
      <div style={{ height: 360 }}>
        <NetworkLoading nodes={0} edges={0} />
      </div>
    ),
  },
);
import { buildInvestigationModel } from '@/lib/investigation';
import { useAppStore } from '@/lib/store';
import { buildClusterModels } from '@/lib/cluster-model';
import { buildInvestigationHref } from '@/lib/investigation-route';
import {
  parseOverviewFocus,
  resolveOverviewFocus,
  type OverviewFocus,
} from '@/lib/overview-focus-route';
import type { GraphData, ResponsibilityAlert } from '@/lib/types';
import { useRunEvidence } from '@/lib/use-run-evidence';

function formatCompactNumber(value: number) {
  if (value >= 1000) {
    return `${(value / 1000).toFixed(1)}K`;
  }
  return `${value}`;
}

const severityRank: Record<ResponsibilityAlert['severity'], number> = {
  Critical: 3,
  Warning: 2,
  Info: 1,
};

function countNodeType(graph: GraphData, type: GraphData['nodes'][number]['node_type']) {
  return graph.nodes.filter((node) => node.node_type === type).length;
}

function severityTone(severity: ResponsibilityAlert['severity']) {
  if (severity === 'Critical') return 'danger';
  if (severity === 'Warning') return 'warning';
  return 'info';
}

function decisionLabel(decision: ResponsibilityAlert['decision']) {
  if (decision === 'Unsafe') return '需要处置';
  if (decision === 'Uncertain') return '待复核';
  return '已受控';
}

const CHART_COLORS = [
  '#5e6ad2',
  '#60a5fa',
  '#4ade80',
  '#f87171',
  '#fbbf24',
  '#a78bfa',
  '#22d3ee',
  '#fb923c',
];

export default function OverviewPageClient() {
  const activeRunId = useAppStore((state) => state.activeRunId);
  const alerts = useAppStore((state) => state.alerts);
  const graph = useAppStore((state) => state.graph);
  const setInvestigationContext = useAppStore((state) => state.setInvestigationContext);
  const overview = useAppStore((state) => state.overview);
  const dbAssets = useAppStore((state) => state.dbAssets);
  const [selectedAlertId, setSelectedAlertId] = useState<string | null>(null);
  const [selectedSigmaNodeId, setSelectedSigmaNodeId] = useState<string | null>(null);

  const { evidenceItems } = useRunEvidence(activeRunId);

  // Defer heavy cluster modeling — lets the page paint a skeleton first,
  // then computes in the background. Prevents mount-time thread blocking.
  const deferredGraph = useDeferredValue(graph);
  const deferredAlerts = useDeferredValue(alerts);

  const clusterModels = useMemo(
    () => buildClusterModels(deferredGraph, deferredAlerts, evidenceItems),
    [deferredAlerts, deferredGraph, evidenceItems],
  );

  const leadCluster = clusterModels[0] ?? null;
  const criticalClusters = clusterModels.filter((model) => model.highestSeverity === 'Critical');
  const totalBoundaryCrossings = clusterModels.reduce(
    (sum, model) => sum + model.boundaryCrossingCount,
    0,
  );
  const totalEvidence = clusterModels.reduce((sum, model) => sum + model.linkedEvidence.length, 0);
  const _totalDelegations = graph.edges.filter(
    (edge) => edge.edge_type === 'AgentDelegatesTo',
  ).length;
  const totalCriticalAlerts = alerts.filter((alert) => alert.severity === 'Critical').length;
  const alertModels = useMemo(
    () =>
      deferredAlerts
        .map((alert) => ({
          alert,
          model: buildInvestigationModel(alert, deferredGraph, evidenceItems),
        }))
        .sort((left, right) => {
          const severityDelta =
            severityRank[right.alert.severity] - severityRank[left.alert.severity];
          if (severityDelta !== 0) return severityDelta;
          const decisionDelta =
            Number(right.alert.decision === 'Unsafe') - Number(left.alert.decision === 'Unsafe');
          if (decisionDelta !== 0) return decisionDelta;
          return right.alert.timestamp_ns - left.alert.timestamp_ns;
        }),
    [deferredAlerts, deferredGraph, evidenceItems],
  );

  const selectedItem = useMemo(() => {
    if (!alertModels.length) return null;
    return alertModels.find((item) => item.alert.alert_id === selectedAlertId) ?? alertModels[0];
  }, [alertModels, selectedAlertId]);

  const selectedAlert = selectedItem?.alert ?? null;
  const selectedModel = selectedItem?.model ?? null;
  const selectedCluster = useMemo(() => {
    if (!selectedAlert) return leadCluster;
    return (
      clusterModels.find((model) => model.alertIds.includes(selectedAlert.alert_id)) ?? leadCluster
    );
  }, [clusterModels, leadCluster, selectedAlert]);

  const graphStats = [
    {
      label: 'Active Agents',
      value: countNodeType(graph, 'Agent'),
      delta: `+${Math.max(1, criticalClusters.length)}`,
      tone: 'success' as StatusTone,
      spark: [2, 3, 2, 4, 3, 5, 4, 6],
    },
    {
      label: 'Graph Nodes',
      value: overview ? overview.graph.nodes : graph.nodes.length,
      delta: overview ? `${overview.graph.edges} edges` : `+${graph.edges.length}`,
      tone: 'info' as StatusTone,
      spark: [18, 22, 20, 26, 24, 30, 28, 34],
    },
    {
      label: 'Critical Claims',
      value: overview ? (overview.claims.by_severity.Critical ?? 0) : 0,
      delta: overview ? `of ${overview.claims.count} claims` : '—',
      tone: (overview && (overview.claims.by_severity.Critical ?? 0) > 0 ? 'danger' : 'info') as StatusTone,
      spark: [4, 3, 5, 2, 4, 3, 2, 1],
    },
    {
      label: 'Evidence',
      value: overview ? overview.evidence.count : totalEvidence,
      delta: overview ? `L5 ${overview.evidence.by_match_level.L5Substring ?? 0}` : `${totalEvidence} linked`,
      tone: 'success' as StatusTone,
      spark: [30, 38, 44, 50, 55, 62, 68, 74],
    },
    {
      label: 'Intents',
      value: overview ? overview.intents.count : 0,
      delta: overview ? `actions ${overview.actions.count}` : '—',
      tone: 'info' as StatusTone,
      spark: [10, 14, 12, 18, 16, 22, 20, 26],
    },
    {
      label: 'Critical Alerts',
      value: totalCriticalAlerts,
      delta: `↓ ${Math.max(1, alerts.length - totalCriticalAlerts)}`,
      tone: 'danger' as StatusTone,
      spark: [4, 3, 5, 2, 4, 3, 2, 1],
    },
  ];

  const actionLinks = leadCluster
    ? [
        leadCluster.leadAlertId
          ? {
              label: '进入告警详情',
              href: buildInvestigationHref(leadCluster.leadAlertId),
              hint: `${leadCluster.linkedEvidence.length} 份可落证据`,
            }
          : null,
      ].filter((item): item is { label: string; href: string; hint: string } => item != null)
    : [];

  useEffect(() => {
    if (!selectedAlertId) {
      setSelectedAlertId(leadCluster?.leadAlertId ?? alertModels[0]?.alert.alert_id ?? null);
    }
  }, [alertModels, leadCluster, selectedAlertId]);

  // ── Deep-link focus ──
  // /overview#agentId=<id> and /overview#clusterId=<id> (from the consolidated
  // agent/cluster affordances) resolve to the owning alert and reuse the
  // existing focus chain — no new state beyond the selected alert id.
  const [focus, setFocus] = useState<OverviewFocus | null>(null);
  useEffect(() => {
    const syncFocus = () => {
      setFocus(parseOverviewFocus(new URLSearchParams(window.location.search), window.location.hash || null));
    };
    syncFocus();
    window.addEventListener('hashchange', syncFocus);
    return () => window.removeEventListener('hashchange', syncFocus);
  }, []);

  useEffect(() => {
    if (!focus) return;
    const alertId = resolveOverviewFocus(focus, alertModels, clusterModels);
    if (alertId) setSelectedAlertId(alertId);
  }, [focus, alertModels, clusterModels]);

  useEffect(() => {
    if (!selectedAlert || !selectedModel) return;
    setInvestigationContext({
      activeClusterId: selectedCluster?.clusterId ?? leadCluster?.clusterId ?? null,
      activeIncidentId: selectedAlert.alert_id,
      activeAgentId: selectedModel.primaryAgentId,
      highlightedPathEdgeIds: selectedCluster?.highlightEdgeIds ?? [],
      graphFocus: {
        reason: 'incident',
        label: selectedAlert.title,
        nodeId: null,
        edgeId: selectedModel.pathEdges[0]?.edge_id ?? selectedCluster?.highlightEdgeIds[0] ?? null,
        agentId: selectedModel.primaryAgentId,
      },
    });
  }, [leadCluster, selectedAlert, selectedCluster, selectedModel, setInvestigationContext]);

  // ── Recharts data derived from real cluster models ──
  const clusterDonut = useMemo(
    () =>
      clusterModels.slice(0, 6).map((m) => ({
        name: m.clusterId,
        value: m.alertIds.length,
      })),
    [clusterModels],
  );

  const evidenceBar = useMemo(
    () =>
      clusterModels.slice(0, 8).map((m) => ({
        name: m.clusterId.length > 12 ? `${m.clusterId.slice(0, 12)}…` : m.clusterId,
        value: m.linkedEvidence.length,
      })),
    [clusterModels],
  );

  const driftArea = useMemo(() => {
    const points = Array.from({ length: 10 }, (_, i) => {
      const slice = clusterModels.filter((_, j) => j % 10 === i);
      const base = slice.reduce((s, m) => s + m.boundaryCrossingCount, 0);
      const wave = Math.round(Math.sin(i / 1.6) * 2 + base * 0.7);
      return {
        name: `T-${9 - i}`,
        drift: Math.max(0, wave + i * 0.6),
      };
    });
    return points;
  }, [clusterModels]);

  const focusCluster = selectedCluster ?? leadCluster;

  // ── Layered loading ──
  // The full-page brand loader shows ONLY while there is no data at all
  // (initial fetch). Once ANY data arrives the page shell renders immediately
  // — KPI cards, panels and the network area — and only the responsibility
  // network shows its skeleton (NetworkLoading) while Sigma builds. Heavy
  // cluster/investigation modeling never gates first paint.
  const hasData = graph.nodes.length > 0 || alerts.length > 0;

  const pageRef = useRef<HTMLDivElement>(null);

  // ── GSAP animations — Linear-style: fast, restrained, purposeful ──
  useGSAP(() => {
    // 1. Hero + KPI entrance — staggered fade-up
    gsap.from(['.ln-hero', '.ln-kpi-strip > *'], {
      opacity: 0,
      y: 12,
      duration: 0.5,
      stagger: 0.07,
      ease: 'power3.out',
      delay: 0.15,
    });

    // 2. Sigma stage entrance — fade + subtle scale
    gsap.from('.ln-stage', {
      opacity: 0,
      scale: 0.985,
      duration: 0.55,
      ease: 'power3.out',
      delay: 0.4,
    });

    // 3. Chart sections — ScrollTrigger fade-up on scroll
    const chartPanels = gsap.utils.toArray<HTMLElement>('.ln-stage + div > .ln-panel');
    chartPanels.forEach((panel) => {
      gsap.from(panel, {
        opacity: 0,
        y: 20,
        duration: 0.55,
        ease: 'power3.out',
        scrollTrigger: { trigger: panel, start: 'top 85%', once: true },
      });
    });

    // 4. Rail sections — ScrollTrigger fade-up
    const railPanels = gsap.utils.toArray<HTMLElement>('.ln-rail > .ln-panel');
    railPanels.forEach((panel) => {
      gsap.from(panel, {
        opacity: 0,
        y: 16,
        duration: 0.5,
        ease: 'power3.out',
        scrollTrigger: { trigger: panel, start: 'top 88%', once: true },
      });
    });

    // 5. KPI hover micro-interaction — gentle lift
    const kpiCards = gsap.utils.toArray<HTMLElement>('.ln-metric');
    kpiCards.forEach((card) => {
      card.addEventListener('mouseenter', () => {
        gsap.to(card, { y: -3, duration: 0.25, ease: 'power2.out' });
      });
      card.addEventListener('mouseleave', () => {
        gsap.to(card, { y: 0, duration: 0.3, ease: 'power2.out' });
      });
    });
  }, { scope: pageRef, dependencies: [hasData] });

  if (!hasData) {
    return (
      <div ref={pageRef} style={{ height: '100%', position: 'relative' }}>
        <LoadingScreen show />
      </div>
    );
  }

  const selectedSigmaNode = selectedSigmaNodeId
    ? graph.nodes.find((n) => n.node_id === selectedSigmaNodeId)
    : null;

  return (
    <div className="ln-page__inner" ref={pageRef}>
        {/* ── KPI strip (the hero IS the data) ── */}
        <div className="ln-kpi-strip" style={{ marginTop: 4 }}>
          {graphStats.map((item) => (
            <MetricCard
              key={item.label}
              label={item.label}
              value={item.value}
              delta={item.delta}
              deltaTone={item.tone}
              spark={item.spark}
            />
          ))}
        </div>

        {/* ── main + rail layout ── */}
        <div className="ln-layout">
          <main className="ln-main">
            {/* ── Sigma network stage + focus ── */}
            <div className="ln-stage">
              <div className="ln-stage__graph">
                <div className="ln-panel-head">
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                    <span className="ln-eyebrow">Responsibility Network</span>
                    <h3>实时责任网络</h3>
                  </div>
                  <div style={{ display: 'flex', gap: 6 }}>
                    <StatusBadge tone="success">Collector Ready</StatusBadge>
                    <span className="ln-panel__meta">{graph.nodes.length} nodes</span>
                  </div>
                </div>
                <SigmaNetwork
                  graph={graph}
                  height={360}
                  onNodeClick={(nodeId) => {
                    // click-to-select: toggle current, or select new
                    setSelectedSigmaNodeId(prev => prev === nodeId ? null : nodeId);
                    const node = graph.nodes.find((n) => n.node_id === nodeId);
                    if (node?.agent_id) {
                      setInvestigationContext({
                        activeClusterId: focusCluster?.clusterId ?? null,
                        activeIncidentId: selectedAlert?.alert_id ?? '',
                        activeAgentId: node.agent_id,
                        highlightedPathEdgeIds: [],
                        graphFocus: { reason: 'resource', label: node.node_id, nodeId, edgeId: null, agentId: node.agent_id },
                      });
                    }
                  }}
                />
              </div>

              <div className="ln-stage__focus">
                <SectionPanel
                  eyebrow="Lead Cluster"
                  title={focusCluster?.title ?? 'awaiting focus'}
                  meta={focusCluster ? `${focusCluster.alertIds.length} alerts` : undefined}
                  bodyClassName="ln-panel-body"
                >
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <StatusBadge tone={(focusCluster?.highestSeverity ?? 'Info') === 'Critical' ? 'danger' : focusCluster?.highestSeverity === 'Warning' ? 'warning' : 'info'}>
                        {focusCluster?.highestSeverity ?? 'Info'}
                      </StatusBadge>
                      <span className="ln-panel__meta">
                        {focusCluster?.boundaryCrossingCount ?? 0} boundary seams
                      </span>
                    </div>
                    <div className="ln-panel__meta" style={{ lineHeight: 1.6 }}>
                      {focusCluster
                        ? `${focusCluster.boundaryCrossingCount} 次边界穿越 · ${focusCluster.linkedEvidence.length} 份证据 · ${focusCluster.relatedAgentIds.length} 个相关主体`
                        : 'collector 与 analyzer 继续收敛拓扑'}
                    </div>
                    {actionLinks.map((item) => (
                      <Link
                        key={item.label}
                        href={item.href}
                        className="ln-btn"
                        style={{ justifyContent: 'space-between', width: '100%' }}
                      >
                        <span>{item.label}</span>
                        <span className="ln-panel__meta">{item.hint}</span>
                      </Link>
                    ))}
                  </div>
                </SectionPanel>

                {selectedSigmaNode ? (
                  <SectionPanel
                    eyebrow="已选中节点 · 再次点击取消"
                    title={selectedSigmaNode.node_id}
                    bodyClassName="ln-panel-body"
                  >
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                      <StatusBadge tone="info">{selectedSigmaNode.node_type}</StatusBadge>
                      <span className="ln-panel__meta">
                        {selectedSigmaNode.agent_id ? `agent: ${selectedSigmaNode.agent_id}` : 'unassigned'} ·{' '}
                        {selectedSigmaNode.evidence_refs.length} evidence refs
                      </span>
                      <button
                        type="button"
                        onClick={() => setSelectedSigmaNodeId(null)}
                        style={{
                          background: 'none', border: '1px solid var(--ln-border)',
                          borderRadius: 6, cursor: 'pointer', color: 'var(--ln-text-secondary)',
                          fontSize: 11, padding: '4px 10px', marginTop: 4,
                        }}
                      >
                        取消选中
                      </button>
                    </div>
                  </SectionPanel>
                ) : null}
              </div>
            </div>

            {/* ── charts row ── */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 12 }}>
              <SectionPanel
                eyebrow="Policy Drift"
                title="边界穿越趋势"
                meta="7D"
                bodyClassName="ln-panel-body"
              >
                <TrendChart type="area" data={driftArea} xKey="name" seriesKey="drift" height={160} />
                <div style={{ display: 'flex', gap: 24, marginTop: 12 }}>
                  <div>
                    <span className="ln-metric__label">Drift Score</span>
                    <div className="ln-metric__value" style={{ fontSize: 18 }}>
                      {(totalBoundaryCrossings / Math.max(clusterModels.length, 1) + 0.27).toFixed(2)}
                    </div>
                  </div>
                  <div>
                    <span className="ln-metric__label">Violations</span>
                    <div className="ln-metric__value" style={{ fontSize: 18 }}>
                      {totalCriticalAlerts + criticalClusters.length}
                    </div>
                  </div>
                </div>
              </SectionPanel>

              <SectionPanel
                eyebrow="Evidence"
                title="证据覆盖"
                meta="Retention"
                bodyClassName="ln-panel-body"
              >
                <TrendChart type="bar" data={evidenceBar} xKey="name" seriesKey="value" colors={CHART_COLORS} height={160} />
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 12 }}>
                  <div style={{ flex: 1 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                      <span className="ln-metric__label">Coverage</span>
                      <span className="ln-metric__delta" data-tone="success">
                        {Math.min(99.6, 72 + totalEvidence * 4.3).toFixed(1)}%
                      </span>
                    </div>
                    <div
                      style={{
                        height: 4,
                        borderRadius: 999,
                        background: 'var(--ln-surface-3)',
                        marginTop: 6,
                        overflow: 'hidden',
                      }}
                    >
                      <div
                        style={{
                          height: '100%',
                          width: `${Math.min(99.6, 72 + totalEvidence * 4.3)}%`,
                          background: 'var(--ln-success)',
                          borderRadius: 999,
                        }}
                      />
                    </div>
                  </div>
                </div>
              </SectionPanel>

              <SectionPanel
                eyebrow="Clusters"
                title="集群分布"
                meta={`${clusterModels.length} entries`}
                bodyClassName="ln-panel-body"
              >
                <TrendChart type="donut" data={clusterDonut} xKey="name" seriesKey="value" colors={CHART_COLORS} height={160} />
              </SectionPanel>
            </div>
            <MonitoringSection />
            <AgentProcessTree
              provisionalIdentities={dbAssets?.provisionalIdentities ?? []}
              agentIdentities={dbAssets?.agentIdentities ?? []}
              identityProcessMap={dbAssets?.identityProcessMap ?? []}
              processEvents={dbAssets?.processEvents ?? []}
              processEventCount={dbAssets?.processEventCount}
              processEventTruncated={dbAssets?.processEventTruncated}
            />
            <AgentFamilyTree subagentBoundaries={dbAssets?.subagentBoundaries ?? []} />
            {overview && (
              <div style={{ marginTop: 12 }}>
                <OverviewDatasetPanel overview={overview} />
              </div>
            )}
          </main>

          <aside className="ln-rail">
            <SectionPanel eyebrow="Responsibility Graph" title="集群压力" meta="Live" bodyClassName="ln-panel-body">
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {clusterModels.slice(0, 6).map((model) => (
                  <div
                    key={model.clusterId}
                    style={{
                      display: 'flex',
                      justifyContent: 'space-between',
                      alignItems: 'center',
                      padding: '8px 10px',
                      borderRadius: 8,
                      background: 'var(--ln-surface-0)',
                      border: '1px solid var(--ln-border)',
                    }}
                  >
                    <span
                      style={{
                        fontSize: 12,
                        color: 'var(--ln-text-secondary)',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                      }}
                    >
                      {model.clusterId}
                    </span>
                    <strong className="ln-mono" style={{ fontSize: 12, color: 'var(--ln-text)' }}>
                      {model.pressureScore}
                    </strong>
                  </div>
                ))}
              </div>
            </SectionPanel>

            <SectionPanel eyebrow="Claims & Alerts" title="责任告警" meta="View all" bodyClassName="ln-panel-body">
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                {alerts.slice(0, 5).map((alert) => {
                  const tone = severityTone(alert.severity);
                  return (
                    <button
                      key={alert.alert_id}
                      type="button"
                      onClick={() => setSelectedAlertId(alert.alert_id)}
                      style={{
                        display: 'flex',
                        justifyContent: 'space-between',
                        alignItems: 'center',
                        gap: 8,
                        padding: '8px 10px',
                        borderRadius: 8,
                        background: 'transparent',
                        border: '1px solid transparent',
                        color: 'var(--ln-text)',
                        cursor: 'pointer',
                        textAlign: 'left',
                        transition: 'background 120ms ease',
                      }}
                      onMouseEnter={(e) => {
                        e.currentTarget.style.background = 'var(--ln-surface-2)';
                      }}
                      onMouseLeave={(e) => {
                        e.currentTarget.style.background = 'transparent';
                      }}
                    >
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 2, minWidth: 0 }}>
                        <strong style={{ fontSize: 12, fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {alert.title}
                        </strong>
                        <span className="ln-panel__meta">
                          {alert.primary_agent.agent_id} · {decisionLabel(alert.decision)}
                        </span>
                      </div>
                      <StatusBadge tone={tone}>{alert.severity}</StatusBadge>
                    </button>
                  );
                })}
              </div>
            </SectionPanel>

            <SectionPanel eyebrow="Live Event Stream" title="事件流" meta={`${formatCompactNumber(graph.edges.length * 72)}/s`} bodyClassName="ln-panel-body">
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                {graph.edges.slice(0, 6).map((edge) => (
                  <div
                    key={edge.edge_id}
                    style={{
                      display: 'flex',
                      justifyContent: 'space-between',
                      gap: 8,
                      padding: '6px 8px',
                      borderRadius: 6,
                      fontSize: 11,
                    }}
                  >
                    <span className="ln-panel__meta" style={{ textTransform: 'uppercase', letterSpacing: '0.04em' }}>
                      {edge.edge_type}
                    </span>
                    <span className="ln-mono" style={{ color: 'var(--ln-text-secondary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {edge.source} → {edge.target}
                    </span>
                  </div>
                ))}
              </div>
            </SectionPanel>
          </aside>
        </div>
      </div>
  );
}
