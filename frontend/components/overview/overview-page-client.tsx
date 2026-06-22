'use client';

import { useMemo } from 'react';
import Link from 'next/link';
import { useQuery } from '@tanstack/react-query';
import PrismPanel from '@/components/ui/prism-panel';
import PrismButton from '@/components/ui/prism-button';
import PrismStatCard from '@/components/ui/prism-stat-card';
import ClusterSignalAtlas from '@/components/ui/cluster-signal-atlas';
import ObservatoryHeader from '@/components/system/observatory-header';
import { api } from '@/lib/api';
import { useAppStore } from '@/lib/store';
import { demoEvidence } from '@/lib/demo-data';
import { deriveClusterIds } from '@/lib/stellar-directory';
import { buildClusterModel } from '@/lib/cluster-model';
import { buildClusterHref } from '@/lib/cluster-route';
import { buildInvestigationHref } from '@/lib/investigation-route';
import { buildAgentHref } from '@/lib/agent-route';
import { Orbit, ShieldAlert, Telescope } from 'lucide-react';

const severityWeight = {
  Critical: 3,
  Warning: 2,
  Info: 1,
  null: 0,
} as const;

export default function OverviewPageClient() {
  const activeRunId = useAppStore((state) => state.activeRunId);
  const alerts = useAppStore((state) => state.alerts);
  const graph = useAppStore((state) => state.graph);

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

  const clusterModels = useMemo(() => {
    return deriveClusterIds(alerts, graph)
      .map((clusterId) => buildClusterModel(clusterId, graph, alerts, evidenceItems))
      .sort((left, right) => {
        const leftSeverity = severityWeight[left.highestSeverity ?? 'null'];
        const rightSeverity = severityWeight[right.highestSeverity ?? 'null'];
        if (leftSeverity !== rightSeverity) {
          return rightSeverity - leftSeverity;
        }
        return right.linkedEvidence.length - left.linkedEvidence.length;
      });
  }, [alerts, evidenceItems, graph]);

  const criticalCount = useMemo(
    () => clusterModels.filter((model) => model.highestSeverity === 'Critical').length,
    [clusterModels],
  );
  const totalBoundaryCrossings = useMemo(
    () => clusterModels.reduce((sum, model) => sum + model.boundaryCrossingCount, 0),
    [clusterModels],
  );
  const totalEvidence = useMemo(
    () => clusterModels.reduce((sum, model) => sum + model.linkedEvidence.length, 0),
    [clusterModels],
  );
  const totalHighlightedPaths = useMemo(
    () => clusterModels.reduce((sum, model) => sum + model.highlightEdgeIds.length, 0),
    [clusterModels],
  );
  const leadCluster = clusterModels[0] ?? null;
  const leadAgentId = leadCluster?.relatedAgentIds[0] ?? null;

  return (
    <div
      style={{
        minHeight: '100vh',
        background: 'var(--investigation-stage-bg)',
        color: 'var(--text-primary)',
        padding: 24,
      }}
    >
      <ObservatoryHeader
        eyebrow="Stellar Overview"
        title="把责任星群读成一张可以下判断的信号总谱"
        description="总览页不负责展示所有细节，它负责先给出结构性判断：哪里正在升温、哪一条 delegation seam 正在外扩、哪些 cluster 已具备直接进入调查的证据饱和度。"
        activeSection="overview"
        backHref="/"
        backLabel="返回首页"
        stats={[
          activeRunId ? `观测会话 ${activeRunId.slice(0, 12)}...` : '示例场景',
          `${criticalCount} critical clusters`,
          `${clusterModels.length} signal entries`,
        ]}
      />

      <PrismPanel size="hero" grid shimmer style={{ marginTop: 18, marginBottom: 18 }}>
        <div style={{ display: 'grid', gap: 18 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 18, flexWrap: 'wrap', alignItems: 'flex-start' }}>
            <div style={{ maxWidth: 760 }}>
              <div style={{ fontSize: 12, letterSpacing: '0.16em', textTransform: 'uppercase', color: 'var(--text-quaternary)' }}>
                Responsibility Signal Atlas
              </div>
              <h2
                className="prism-display"
                style={{ margin: '12px 0 0', fontSize: 'clamp(30px, 3.6vw, 46px)', lineHeight: 0.94, letterSpacing: '-0.05em', maxWidth: 760 }}
              >
                {leadCluster
                  ? `${leadCluster.title} 正在牵引当前最需要先下判断的责任密度`
                  : '等待责任星群进入可判定状态'}
              </h2>
              <p style={{ margin: '14px 0 0', maxWidth: 720, color: 'var(--text-secondary)', lineHeight: 1.78, fontSize: 14 }}>
                {leadCluster
                  ? `${leadCluster.summary} 在这一层，我们不再用传统列表去扫信息，而是把风险压力、边界缝合和证据饱和度直接压进同一张判断界面。`
                  : '接入真实会话后，这里会把最危险的 cluster、最活跃的 delegation path 和最有价值的调查入口压成一张可进入的责任总谱。'}
              </p>
              <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginTop: 18 }}>
                {leadCluster ? (
                  <Link href={buildClusterHref(leadCluster.clusterId)} style={{ textDecoration: 'none' }}>
                    <PrismButton variant="primary" size="sm">进入热点 Cluster</PrismButton>
                  </Link>
                ) : null}
                {leadAgentId ? (
                  <Link href={buildAgentHref(leadAgentId)} style={{ textDecoration: 'none' }}>
                    <PrismButton variant="secondary" size="sm">查看主 Agent</PrismButton>
                  </Link>
                ) : null}
                {leadCluster?.leadAlertId ? (
                  <Link href={buildInvestigationHref(leadCluster.leadAlertId)} style={{ textDecoration: 'none' }}>
                    <PrismButton variant="ghost" size="sm">进入主调查</PrismButton>
                  </Link>
                ) : (
                  <Link href="/handoff" style={{ textDecoration: 'none' }}>
                    <PrismButton variant="ghost" size="sm">查看 Handoff</PrismButton>
                  </Link>
                )}
              </div>
            </div>

            <div style={{ display: 'grid', gap: 10, minWidth: 260, flex: '0 0 260px' }}>
              <div className="prism-editorial-note">
                <div className="prism-editorial-note__label">Curatorial Note</div>
                <div className="prism-editorial-note__copy">
                  顶级总览不应让人“看到很多模块”，而应让人一眼知道先看哪里、为什么先看那里。
                </div>
              </div>
              <div className="prism-meta-inline__item">
                <Telescope size={12} />
                {leadCluster?.title ?? '等待 cluster'}
              </div>
              <div className="prism-meta-inline__item">
                <ShieldAlert size={12} />
                {leadCluster?.alertIds.length ?? 0} related alerts
              </div>
              <div className="prism-meta-inline__item">
                <Orbit size={12} />
                {leadCluster?.highlightEdgeIds.length ?? 0} highlighted paths
              </div>
            </div>
          </div>

          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
              gap: 10,
            }}
          >
            <PrismStatCard
              label="Critical Density"
              value={`${criticalCount}/${clusterModels.length || 1}`}
              hint="最高危险 cluster 数量与当前总谱规模"
              toneColor="#fb7185"
            />
            <PrismStatCard
              label="Boundary Seams"
              value={totalBoundaryCrossings}
              hint="跨边界压力总和"
              toneColor="#fbbf24"
            />
            <PrismStatCard
              label="Materialized Evidence"
              value={totalEvidence}
              hint="当前可直接支撑调查的物化线索"
              toneColor="#7dd3fc"
            />
            <PrismStatCard
              label="Path Exposure"
              value={totalHighlightedPaths}
              hint="已被拉出判断价值的链路数量"
              toneColor="#a78bfa"
            />
          </div>

          <ClusterSignalAtlas clusters={clusterModels} />
        </div>
      </PrismPanel>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: 14 }}>
        {clusterModels.map((model) => {
          const leadRelatedAgentId = model.relatedAgentIds[0] ?? null;
          const evidenceFill = Math.min(100, 18 + model.linkedEvidence.length * 14);
          const seamFill = Math.min(100, 12 + model.boundaryCrossingCount * 24);

          return (
            <PrismPanel key={model.clusterId} grid style={{ display: 'grid', gap: 14 }}>
              <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12 }}>
                <div>
                  <div style={{ fontSize: 11, letterSpacing: '0.14em', textTransform: 'uppercase', color: 'var(--text-quaternary)' }}>
                    Cluster Anchor
                  </div>
                  <div className="prism-display" style={{ marginTop: 8, fontSize: 26, lineHeight: 0.98 }}>{model.title}</div>
                </div>
                <div className="prism-meta-inline__item">{model.highestSeverity ?? 'No Alert'}</div>
              </div>

              <div style={{ color: 'var(--text-secondary)', lineHeight: 1.8, fontSize: 13 }}>
                {model.summary}
              </div>

              <div style={{ display: 'grid', gap: 10 }}>
                <div>
                  <div className="prism-atlas-row">
                    <span className="prism-atlas-mini-copy">Evidence Saturation</span>
                    <span className="prism-atlas-mini-copy">{model.linkedEvidence.length} material</span>
                  </div>
                  <div className="prism-atlas-track" style={{ marginTop: 6 }}>
                    <div className="prism-atlas-fill" style={{ width: `${evidenceFill}%`, background: 'linear-gradient(90deg, #7dd3fc, rgba(125,211,252,0.18))' }} />
                  </div>
                </div>
                <div>
                  <div className="prism-atlas-row">
                    <span className="prism-atlas-mini-copy">Boundary Tension</span>
                    <span className="prism-atlas-mini-copy">{model.boundaryCrossingCount} seams</span>
                  </div>
                  <div className="prism-atlas-track" style={{ marginTop: 6 }}>
                    <div className="prism-atlas-fill" style={{ width: `${seamFill}%`, background: 'linear-gradient(90deg, #fbbf24, rgba(251,191,36,0.18))' }} />
                  </div>
                </div>
              </div>

              <div className="prism-meta-inline">
                <span className="prism-meta-inline__item">{model.alertIds.length} alerts</span>
                <span className="prism-meta-inline__item">{model.relatedAgentIds.length} agents</span>
                <span className="prism-meta-inline__item">{model.highlightEdgeIds.length} highlighted paths</span>
              </div>

              <div className="prism-editorial-note">
                <div className="prism-editorial-note__label">Reading Cue</div>
                <div className="prism-editorial-note__copy">
                  {model.boundaryCrossingCount > 0
                    ? '先看边界缝合，再看证据饱和度；这类 cluster 往往更快进入归因调查。'
                    : '这类 cluster 更适合先看 delegation path 和参与主体，而不是过早下结论。'}
                </div>
              </div>

              <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                <Link href={buildClusterHref(model.clusterId)} style={{ textDecoration: 'none' }}>
                  <PrismButton variant="primary" size="sm">进入 Cluster</PrismButton>
                </Link>
                {leadRelatedAgentId ? (
                  <Link href={buildAgentHref(leadRelatedAgentId)} style={{ textDecoration: 'none' }}>
                    <PrismButton variant="secondary" size="sm">查看主 Agent</PrismButton>
                  </Link>
                ) : null}
                {model.leadAlertId ? (
                  <Link href={buildInvestigationHref(model.leadAlertId)} style={{ textDecoration: 'none' }}>
                    <PrismButton variant="ghost" size="sm">进入调查</PrismButton>
                  </Link>
                ) : null}
              </div>
            </PrismPanel>
          );
        })}
      </div>
    </div>
  );
}
