'use client';

import { useDeferredValue, useEffect, useMemo, useRef, useState } from 'react';
import dynamic from 'next/dynamic';
import Link from 'next/link';
import { useGSAP } from '@gsap/react';
import gsap from 'gsap';
import { ScrollTrigger } from 'gsap/ScrollTrigger';
import { SectionPanel } from '@/components/ui/linear/section-panel';
import { LiveRunningBadge } from '@/components/live/live-running-badge';
import { StatusBadge, type StatusTone } from '@/components/ui/linear/status-badge';
import OverviewDatasetPanel from '@/components/overview/dataset-overview-panel';
import MonitoringSection from '@/components/overview/monitoring/monitoring-section';
import AgentProcessTree from '@/components/overview/agent-process-tree';
import AgentFamilyTree from '@/components/overview/agent-family-tree';
import { TrendChart } from '@/components/ui/linear/trend-chart';
import LoadingScreen from '@/components/ui/loading-screen';
import NetworkLoading from '@/components/ui/network-loading';
import { CollapsibleSection } from '@/components/ui/collapsible-section';
import { MetricCard } from '@/components/ui/linear/metric-card';

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
import { buildResponsibilityUnits } from '@/lib/responsibility-unit';
import { buildInvestigationHref } from '@/lib/investigation-route';
import {
  parseOverviewFocus,
  resolveOverviewFocus,
  type OverviewFocus,
} from '@/lib/overview-focus-route';
import type { GraphData, ResponsibilityAlert } from '@/lib/types';
import { useRunEvidence } from '@/lib/use-run-evidence';
import {
  deriveEvidenceMatchDistribution,
  deriveSeverityBreakdown,
  deriveTopologyTrend,
  EVIDENCE_MATCH_MEANING,
  EVIDENCE_MATCH_COLORS,
  EVIDENCE_MATCH_ORDER,
} from '@/lib/overview-metrics';

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
  const setPageReady = useAppStore((state) => state.setPageReady);
  const dbAssets = useAppStore((state) => state.dbAssets);
  const actions = useAppStore((state) => state.actions);
  const liveSnapshotFrames = useAppStore((state) => state.liveSnapshotFrames);
  const liveEventCount = useAppStore((state) => state.live.stats.eventCount);
  const [selectedAlertId, setSelectedAlertId] = useState<string | null>(null);
  const [selectedSigmaNodeId, setSelectedSigmaNodeId] = useState<string | null>(null);

  const { evidenceItems } = useRunEvidence(activeRunId);

  // Defer heavy responsibility-unit modeling — lets the page paint a skeleton first,
  // then computes in the background. Prevents mount-time thread blocking.
  const deferredGraph = useDeferredValue(graph);
  const deferredAlerts = useDeferredValue(alerts);

  const responsibilityUnits = useMemo(
    () => buildResponsibilityUnits(deferredGraph, deferredAlerts, evidenceItems),
    [deferredAlerts, deferredGraph, evidenceItems],
  );

  const leadUnit = responsibilityUnits[0] ?? null;
  const criticalUnits = responsibilityUnits.filter((model) => model.highestSeverity === 'Critical');

  // ── 真实数据派生（铁律：算不出的诚实空态，不造数）──
  // 快照帧 = live 实时帧优先，回退 db 导入帧
  const snapshotFrames = useMemo(
    () => (liveSnapshotFrames.length > 0 ? liveSnapshotFrames : (dbAssets?.snapshotFrames ?? [])),
    [liveSnapshotFrames, dbAssets],
  );
  const topologyTrend = useMemo(() => deriveTopologyTrend(snapshotFrames), [snapshotFrames]);
  const severityBreakdown = useMemo(() => deriveSeverityBreakdown(alerts), [alerts]);
  // 证据精确度分布：按全量关联证据 match_level（L1 精确 → L5 子串）分桶 —— 直接反映
  // 归因证据的可靠度。消费全量 correlation_evidence（完整利用真实数据，非仅责任单元子集）。
  const evidenceMatch = useMemo(
    () => deriveEvidenceMatchDistribution(evidenceItems),
    [evidenceItems],
  );
  // 真实 KPI sparkline：有快照帧时按帧节点/agent 数派生；无帧诚实不画（顶级规范允许
  // 卡内一个 sparkline，但绝不用伪造曲线）。
  const nodeSpark = topologyTrend.length > 0 ? topologyTrend.map((p) => p.nodes) : undefined;
  const agentSpark = topologyTrend.length > 0 ? topologyTrend.map((p) => p.agents) : undefined;

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
  const selectedUnit = useMemo(() => {
    if (!selectedAlert) return leadUnit;
    return (
      responsibilityUnits.find((model) => model.alertIds.includes(selectedAlert.alert_id)) ?? leadUnit
    );
  }, [responsibilityUnits, leadUnit, selectedAlert]);

  // KPI stat 卡（顶级规范：大数字 28-32px + 一个对比 + 一个真实 sparkline）。
  // 与运行监控「丰富健康卡」的区分在于密度：KPI 卡是 stat（干净数字+spark），
  // 监控卡是 detail（状态色条+评分+趋势+信号），同 chrome、不同层级。
  const graphStats = [
    {
      label: 'Active Agents',
      value: countNodeType(graph, 'Agent'),
      delta: `${countNodeType(graph, 'Agent')} 个主体`,
      tone: 'success' as StatusTone,
      spark: agentSpark,
    },
    {
      label: 'Graph Nodes',
      value: overview ? overview.graph.nodes : graph.nodes.length,
      delta: overview ? `${overview.graph.edges} 条边` : `${graph.edges.length} 条边`,
      tone: 'info' as StatusTone,
      spark: nodeSpark,
    },
    {
      label: 'Critical Claims',
      value: overview ? (overview.claims.by_severity.Critical ?? 0) : severityBreakdown.critical,
      delta: overview ? `共 ${overview.claims.count} 声明` : `${alerts.length} 告警`,
      tone:
        (overview && (overview.claims.by_severity.Critical ?? 0) > 0) ||
        severityBreakdown.critical > 0
          ? ('danger' as StatusTone)
          : ('info' as StatusTone),
    },
    {
      label: 'Evidence',
      value: overview ? overview.evidence.count : evidenceMatch.total,
      delta: overview
        ? `L5 ${overview.evidence.by_match_level.L5Substring ?? 0}`
        : `${evidenceMatch.total} 条关联`,
      tone: 'success' as StatusTone,
    },
    {
      label: 'Intents',
      value: overview ? overview.intents.count : 0,
      delta: overview ? `${overview.actions.count} 动作` : '—',
      tone: 'info' as StatusTone,
    },
    {
      label: 'Critical Alerts',
      value: severityBreakdown.critical,
      delta: `共 ${alerts.length} 告警`,
      tone: severityBreakdown.critical > 0 ? ('danger' as StatusTone) : ('info' as StatusTone),
    },
  ];

  const actionLinks = leadUnit
    ? [
        leadUnit.leadAlertId
          ? {
              label: '进入告警详情',
              href: buildInvestigationHref(leadUnit.leadAlertId),
              hint: `${leadUnit.linkedEvidence.length} 份可落证据`,
            }
          : null,
      ].filter((item): item is { label: string; href: string; hint: string } => item != null)
    : [];

  useEffect(() => {
    if (!selectedAlertId) {
      setSelectedAlertId(leadUnit?.leadAlertId ?? alertModels[0]?.alert.alert_id ?? null);
    }
  }, [alertModels, leadUnit, selectedAlertId]);

  // ── Deep-link focus ──
  // /overview#agentId=<id> and /overview#unitId=<id> (from the consolidated
  // agent/unit affordances) resolve to the owning alert and reuse the
  // existing focus chain — no new state beyond the selected alert id.
  const [focus, setFocus] = useState<OverviewFocus | null>(null);
  useEffect(() => {
    const syncFocus = () => {
      setFocus(
        parseOverviewFocus(
          new URLSearchParams(window.location.search),
          window.location.hash || null,
        ),
      );
    };
    syncFocus();
    window.addEventListener('hashchange', syncFocus);
    return () => window.removeEventListener('hashchange', syncFocus);
  }, []);

  useEffect(() => {
    if (!focus) return;
    const alertId = resolveOverviewFocus(focus, alertModels, responsibilityUnits);
    if (alertId) setSelectedAlertId(alertId);
  }, [focus, alertModels, responsibilityUnits]);

  useEffect(() => {
    if (!selectedAlert || !selectedModel) return;
    setInvestigationContext({
      activeUnitId: selectedUnit?.unitId ?? leadUnit?.unitId ?? null,
      activeIncidentId: selectedAlert.alert_id,
      activeAgentId: selectedModel.primaryAgentId,
      highlightedPathEdgeIds: selectedUnit?.highlightEdgeIds ?? [],
      graphFocus: {
        reason: 'incident',
        label: selectedAlert.title,
        nodeId: null,
        edgeId: selectedModel.pathEdges[0]?.edge_id ?? selectedUnit?.highlightEdgeIds[0] ?? null,
        agentId: selectedModel.primaryAgentId,
      },
    });
  }, [leadUnit, selectedAlert, selectedUnit, selectedModel, setInvestigationContext]);

  // ── Recharts data derived from real responsibility-unit models ──
  const unitDonut = useMemo(
    () =>
      responsibilityUnits.slice(0, 6).map((m) => ({
        name: m.unitId,
        value: m.alertIds.length,
      })),
    [responsibilityUnits],
  );

  // 拓扑演化趋势已由真实快照帧派生（topologyTrend）；伪造的 Math.sin driftArea / 假 Coverage 已移除。

  const focusUnit = selectedUnit ?? leadUnit;

  // ── Layered loading ──
  // The full-page brand loader shows ONLY while there is no data at all
  // (initial fetch). Once ANY data arrives the page shell renders immediately
  // — KPI cards, panels and the network area — and only the responsibility
  // network shows its skeleton (NetworkLoading) while Sigma builds. Heavy
  // responsibility-unit/investigation modeling never gates first paint.
  const hasData = graph.nodes.length > 0 || alerts.length > 0;
  const dataLoading = useAppStore((state) => state.dataLoading);

  // 页面内容就绪信号：加载结束即就绪。把“有没有数据”当成就绪条件会让
  // 无数据的数据源（例如只有 system 的 live run）永久停在品牌加载层。
  useEffect(() => {
    setPageReady(!dataLoading);
  }, [dataLoading, setPageReady]);

  const pageRef = useRef<HTMLDivElement>(null);

  // ── GSAP animations — Linear-style: fast, restrained, purposeful ──
  useGSAP(
    () => {
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

      // 3. Chart sections — ScrollTrigger fade-up on scroll.
      // scroller 指向页面自滚动容器（ln-page__inner）：页面在 div 内滚动而非 window，
      // 默认 scroller 会让下方面板的 once 揭示永不触发（保持 opacity:0 不可见）。
      const scroller = pageRef.current;
      const chartPanels = gsap.utils.toArray<HTMLElement>('.ln-stage + div > .ln-panel');
      chartPanels.forEach((panel) => {
        gsap.from(panel, {
          opacity: 0,
          y: 20,
          duration: 0.55,
          ease: 'power3.out',
          scrollTrigger: { trigger: panel, start: 'top 85%', once: true, scroller },
        });
      });

      // 4. Rail sections — ScrollTrigger fade-up + 列表项错峰揭示
      const railPanels = gsap.utils.toArray<HTMLElement>('.ln-rail > .ln-panel');
      railPanels.forEach((panel) => {
        gsap.from(panel, {
          opacity: 0,
          y: 16,
          duration: 0.5,
          ease: 'power3.out',
          scrollTrigger: { trigger: panel, start: 'top 88%', once: true, scroller },
        });
      });
      // rail 列表项错峰揭示（面板入场后 stagger）——独立 timeline 更稳，无 onEnter 嵌套
      const railItems = gsap.utils.toArray<HTMLElement>('.ln-rail .ln-rail-item');
      if (railItems.length > 0) {
        gsap.from(railItems, {
          opacity: 0,
          x: -8,
          duration: 0.35,
          stagger: 0.05,
          ease: 'power2.out',
          delay: 0.55,
          scrollTrigger: { trigger: '.ln-rail', start: 'top 90%', once: true, scroller },
          clearProps: 'transform',
        });
      }

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

      // 6. 监控/深链分节 — 滚动渐入（scroller = 页面自滚动容器），错峰揭示
      const belowPanels = gsap.utils.toArray<HTMLElement>('.ln-main > .ln-panel');
      belowPanels.forEach((panel) => {
        gsap.from(panel, {
          opacity: 0,
          y: 24,
          duration: 0.55,
          ease: 'power3.out',
          scrollTrigger: { trigger: panel, start: 'top 90%', once: true, scroller },
        });
      });
    },
    { scope: pageRef, dependencies: [hasData] },
  );

  if (!hasData) {
    if (dataLoading) {
      return (
        <div ref={pageRef} style={{ height: '100%', position: 'relative' }}>
          <LoadingScreen show />
        </div>
      );
    }
    return (
      <div ref={pageRef} className="ln-page__inner" style={{ maxWidth: 720, margin: '0 auto' }}>
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            justifyContent: 'center',
            alignItems: 'center',
            gap: 8,
            minHeight: '60vh',
            textAlign: 'center',
          }}
        >
          <h2 style={{ fontSize: 16, fontWeight: 600 }}>暂无责任数据</h2>
          <p className="ln-panel__meta" style={{ maxWidth: 460 }}>
            当前数据源没有图节点或告警。导入 responsibility.db，或连接一个实时 run 后，
            责任网络与 KPI 会显示在这里。
          </p>
        </div>
      </div>
    );
  }

  const selectedSigmaNode = selectedSigmaNodeId
    ? graph.nodes.find((n) => n.node_id === selectedSigmaNodeId)
    : null;

  return (
    <div className="ln-page__inner" ref={pageRef} style={{ position: 'relative' }}>
      {/* ── 实时运行徽章（直播中显示，空态不占布局） ── */}
      <div style={{ position: 'absolute', top: 0, right: 0, zIndex: 5 }}>
        <LiveRunningBadge />
      </div>
      {/* ── KPI stat 卡（大数字 + 真实 sparkline + delta；与监控丰富健康卡同 chrome 不同密度） ── */}
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
                height={480}
                onNodeClick={(nodeId) => {
                  // click-to-select: toggle current, or select new
                  setSelectedSigmaNodeId((prev) => (prev === nodeId ? null : nodeId));
                  const node = graph.nodes.find((n) => n.node_id === nodeId);
                  if (node?.agent_id) {
                    setInvestigationContext({
                      activeUnitId: focusUnit?.unitId ?? null,
                      activeIncidentId: selectedAlert?.alert_id ?? '',
                      activeAgentId: node.agent_id,
                      highlightedPathEdgeIds: [],
                      graphFocus: {
                        reason: 'resource',
                        label: node.node_id,
                        nodeId,
                        edgeId: null,
                        agentId: node.agent_id,
                      },
                    });
                  }
                }}
              />
            </div>

            <div className="ln-stage__focus">
              <SectionPanel
                eyebrow="Lead Unit"
                title={focusUnit?.title ?? 'awaiting focus'}
                meta={focusUnit ? `${focusUnit.alertIds.length} alerts` : undefined}
                bodyClassName="ln-panel-body"
              >
                <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <StatusBadge
                      tone={
                        (focusUnit?.highestSeverity ?? 'Info') === 'Critical'
                          ? 'danger'
                          : focusUnit?.highestSeverity === 'Warning'
                            ? 'warning'
                            : 'info'
                      }
                    >
                      {focusUnit?.highestSeverity ?? 'Info'}
                    </StatusBadge>
                    <span className="ln-panel__meta">
                      {focusUnit?.boundaryCrossingCount ?? 0} boundary seams
                    </span>
                  </div>
                  <div className="ln-panel__meta" style={{ lineHeight: 1.6 }}>
                    {focusUnit
                      ? `${focusUnit.boundaryCrossingCount} 次边界穿越 · ${focusUnit.linkedEvidence.length} 份证据 · ${focusUnit.relatedAgentIds.length} 个相关主体`
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
                      {selectedSigmaNode.agent_id
                        ? `agent: ${selectedSigmaNode.agent_id}`
                        : 'unassigned'}{' '}
                      · {selectedSigmaNode.evidence_refs.length} evidence refs
                    </span>
                    <button
                      type="button"
                      onClick={() => setSelectedSigmaNodeId(null)}
                      style={{
                        background: 'none',
                        border: '1px solid var(--ln-border)',
                        borderRadius: 6,
                        cursor: 'pointer',
                        color: 'var(--ln-text-secondary)',
                        fontSize: 11,
                        padding: '4px 10px',
                        marginTop: 4,
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
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))',
              gap: 12,
            }}
          >
            {/* 拓扑演化趋势 —— 真实快照帧派生；无帧诚实空态（不造曲线） */}
            <SectionPanel
              eyebrow="Topology"
              title="拓扑演化趋势"
              meta={snapshotFrames.length > 0 ? `${snapshotFrames.length} 帧` : '实时' }
              bodyClassName="ln-panel-body"
            >
              {topologyTrend.length >= 2 ? (
                <>
                  <TrendChart
                    type="area"
                    data={topologyTrend}
                    xKey="name"
                    seriesKey="nodes"
                    height={160}
                  />
                  <div style={{ display: 'flex', gap: 24, marginTop: 12 }}>
                    <div>
                      <span className="ln-metric__label">节点</span>
                      <div className="ln-metric__value" style={{ fontSize: 18 }}>
                        {topologyTrend[topologyTrend.length - 1].nodes}
                      </div>
                    </div>
                    <div>
                      <span className="ln-metric__label">边</span>
                      <div className="ln-metric__value" style={{ fontSize: 18 }}>
                        {topologyTrend[topologyTrend.length - 1].edges}
                      </div>
                    </div>
                    <div>
                      <span className="ln-metric__label">Δ节点</span>
                      <div
                        className="ln-metric__delta"
                        data-tone={
                          topologyTrend[topologyTrend.length - 1].nodes -
                            topologyTrend[0].nodes >=
                          0
                            ? 'success'
                            : 'warning'
                        }
                      >
                        {topologyTrend[topologyTrend.length - 1].nodes -
                          topologyTrend[0].nodes >=
                        0
                          ? '+'
                          : ''}
                        {topologyTrend[topologyTrend.length - 1].nodes -
                          topologyTrend[0].nodes}
                      </div>
                    </div>
                  </div>
                </>
              ) : (
                <p
                  style={{
                    fontSize: 12,
                    color: 'var(--ln-text-muted)',
                    padding: '28px 0',
                    textAlign: 'center',
                  }}
                >
                  暂无演化帧——直播或导入含快照帧的 run 后展示真实拓扑演化
                </p>
              )}
            </SectionPanel>

            {/* 证据精确度分布 —— 真实关联证据按 match_level（官方枚举）分桶。
                始终展示完整 L1→L5 全谱（缺失等级如实为 0）；语义配色 L1 精确(绿)→L5 子串(红)
                传达质量梯度；图例两列紧凑单行，悬浮显示完整释义（渐进披露，不拉高面板）。 */}
            <SectionPanel
              eyebrow="Evidence"
              title="证据精确度分布"
              meta={`${evidenceMatch.total} 条关联证据`}
              bodyClassName="ln-panel-body"
            >
              <TrendChart
                type="bar"
                data={evidenceMatch.byLevel}
                xKey="short"
                seriesKey="value"
                colors={EVIDENCE_MATCH_ORDER.map((l) => EVIDENCE_MATCH_COLORS[l])}
                height={150}
              />
              <div
                style={{
                  display: 'grid',
                  gridTemplateColumns: '1fr 1fr',
                  gap: '4px 14px',
                  marginTop: 10,
                }}
              >
                {evidenceMatch.byLevel.map((l) => (
                  <div
                    key={l.name}
                    title={`${l.name}：${EVIDENCE_MATCH_MEANING[l.name] ?? l.name}`}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 6,
                      fontSize: 10.5,
                      minWidth: 0,
                      cursor: 'help',
                    }}
                  >
                    <span
                      style={{
                        width: 7,
                        height: 7,
                        borderRadius: 2,
                        background: EVIDENCE_MATCH_COLORS[l.name],
                        flexShrink: 0,
                      }}
                    />
                    <span
                      className="ln-mono"
                      style={{ color: 'var(--ln-text)', flexShrink: 0, whiteSpace: 'nowrap' }}
                    >
                      {l.name}
                    </span>
                    <span
                      className="ln-mono"
                      style={{
                        color: 'var(--ln-text-muted)',
                        flex: 1,
                        textAlign: 'right',
                        whiteSpace: 'nowrap',
                      }}
                    >
                      {l.value} · {l.percent.toFixed(0)}%
                    </span>
                  </div>
                ))}
              </div>
            </SectionPanel>

            {/* 责任单元分布 —— 真实责任单元 */}
            <SectionPanel
              eyebrow="Units"
              title="责任单元分布"
              meta={`${responsibilityUnits.length} 责任单元`}
              bodyClassName="ln-panel-body"
            >
              <TrendChart
                type="donut"
                data={unitDonut}
                xKey="name"
                seriesKey="value"
                colors={CHART_COLORS}
                height={160}
              />
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 12 }}>
                <span className="ln-metric__label">高危责任单元</span>
                <strong
                  className="ln-metric__delta"
                  data-tone={criticalUnits.length > 0 ? 'danger' : 'success'}
                  style={{ marginLeft: 'auto', fontSize: 13 }}
                >
                  {criticalUnits.length}
                </strong>
              </div>
            </SectionPanel>
          </div>
          <MonitoringSection />

          {/* 身份血缘 —— 进程树 + 委派树（折叠分节，默认收起，首屏不信息过载） */}
          <CollapsibleSection eyebrow="深度调查" title="身份血缘" meta="进程树 · 委派树">
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              <AgentProcessTree
                provisionalIdentities={dbAssets?.provisionalIdentities ?? []}
                agentIdentities={dbAssets?.agentIdentities ?? []}
                identityProcessMap={dbAssets?.identityProcessMap ?? []}
                processEvents={dbAssets?.processEvents ?? []}
                actions={actions}
                processEventCount={dbAssets?.processEventCount}
                processEventTruncated={dbAssets?.processEventTruncated}
              />
              <AgentFamilyTree subagentBoundaries={dbAssets?.subagentBoundaries ?? []} />
            </div>
          </CollapsibleSection>

          {/* 运行概览（折叠分节）——db 代表一次采集 run */}
          {overview && (
            <CollapsibleSection
              eyebrow="Run Summary"
              title="运行概览"
              meta={`${overview.graph.nodes} 节点 · ${overview.graph.edges} 边`}
            >
              <OverviewDatasetPanel overview={overview} />
            </CollapsibleSection>
          )}
        </main>

        <aside className="ln-rail">
          <SectionPanel
            eyebrow="Responsibility Graph"
            title="责任单元压力"
            meta="Live"
            bodyClassName="ln-panel-body"
          >
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {responsibilityUnits.slice(0, 6).map((model) => (
                <div
                  key={model.unitId}
                  className="ln-rail-item"
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
                    {model.unitId}
                  </span>
                  <strong className="ln-mono" style={{ fontSize: 12, color: 'var(--ln-text)' }}>
                    {model.pressureScore}
                  </strong>
                </div>
              ))}
            </div>
          </SectionPanel>

          <SectionPanel
            eyebrow="Claims & Alerts"
            title="责任告警"
            meta="View all"
            bodyClassName="ln-panel-body"
          >
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              {alerts.slice(0, 5).map((alert) => {
                const tone = severityTone(alert.severity);
                return (
                  <button
                    key={alert.alert_id}
                    type="button"
                    onClick={() => setSelectedAlertId(alert.alert_id)}
                    className="ln-rail-item"
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
                      <strong
                        style={{
                          fontSize: 12,
                          fontWeight: 500,
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          whiteSpace: 'nowrap',
                        }}
                      >
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

          <SectionPanel
            eyebrow="Live Event Stream"
            title="事件流"
            meta={liveEventCount > 0 ? `${formatCompactNumber(liveEventCount)} 事件` : '—'}
            bodyClassName="ln-panel-body"
          >
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              {graph.edges.slice(0, 6).map((edge) => (
                <div
                  key={edge.edge_id}
                  className="ln-rail-item"
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    gap: 8,
                    padding: '6px 8px',
                    borderRadius: 6,
                    fontSize: 11,
                  }}
                >
                  <span
                    className="ln-panel__meta"
                    style={{ textTransform: 'uppercase', letterSpacing: '0.04em' }}
                  >
                    {edge.edge_type}
                  </span>
                  <span
                    className="ln-mono"
                    style={{
                      color: 'var(--ln-text-secondary)',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                    }}
                  >
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
