'use client';

import Link from 'next/link';
import { useCallback, useMemo } from 'react';
import { useSearchParams } from 'next/navigation';
import { Activity, ArrowLeft, ArrowRight, GitBranch, Radar, Sparkles, Target, Waypoints } from 'lucide-react';
import PrismPanel from '@/components/ui/prism-panel';
import PrismButton from '@/components/ui/prism-button';
import ObservatoryHeader from '@/components/system/observatory-header';
import ObservatoryPageHero from '@/components/system/observatory-page-hero';
import { useAppStore } from '@/lib/store';
import { deriveClusterIds } from '@/lib/stellar-directory';
import { buildClusterModel, compareClusterModels } from '@/lib/cluster-model';
import { buildClusterHref, parseClusterId } from '@/lib/cluster-route';
import { buildAgentHref } from '@/lib/agent-route';
import { buildInvestigationHref } from '@/lib/investigation-route';
import type { ResponsibilityAlert } from '@/lib/types';
import { useRunEvidence } from '@/lib/use-run-evidence';
import { useHashRouteState } from '@/lib/use-hash-route-state';

function getSeverityTone(severity: string | null) {
  if (severity === 'Critical') return 'danger';
  if (severity === 'Warning') return 'warning';
  return 'info';
}

function formatConfidence(value: number) {
  return `${Math.round(value * 100)}%`;
}

function countAlertEvidence(alert: ResponsibilityAlert) {
  return new Set(
    alert.timeline.steps
      .map((step) => step.evidence_ref)
      .filter((ref): ref is string => !!ref),
  ).size;
}

function buildSparklineValues(seed: number, amplitude: number) {
  return Array.from({ length: 24 }).map((_, index) => {
    const wave = Math.sin((seed + index * 1.7) / 3.1) * amplitude;
    const drift = Math.cos((seed + index) / 5.2) * (amplitude * 0.6);
    return Math.round(Math.max(18, Math.min(96, 44 + wave + drift + index * 0.35)));
  });
}

export default function ClustersPageClient() {
  const searchParams = useSearchParams();
  const activeRunId = useAppStore((state) => state.activeRunId);
  const alerts = useAppStore((state) => state.alerts);
  const graph = useAppStore((state) => state.graph);
  const { selectedValue: hashClusterId, setRouteValue: setHashClusterId } = useHashRouteState({
    searchParams,
    parseValue: parseClusterId,
    buildHref: buildClusterHref,
  });

  const { evidenceItems } = useRunEvidence(activeRunId);

  const clusterIds = useMemo(() => deriveClusterIds(alerts, graph), [alerts, graph]);
  const selectedClusterId = useMemo(() => {
    return hashClusterId;
  }, [hashClusterId]);

  const clusterModels = useMemo(
    () =>
      clusterIds
        .map((clusterId) => buildClusterModel(clusterId, graph, alerts, evidenceItems))
        .sort(compareClusterModels),
    [alerts, clusterIds, evidenceItems, graph],
  );

  const selectedModel = useMemo(
    () => clusterModels.find((model) => model.clusterId === selectedClusterId) ?? clusterModels[0] ?? null,
    [clusterModels, selectedClusterId],
  );
  const totalPressure = clusterModels.reduce((sum, model) => sum + model.pressureScore, 0);
  const avgPressure = clusterModels.length ? Math.round(totalPressure / clusterModels.length) : 0;
  const criticalClusterCount = clusterModels.filter((model) => model.highestSeverity === 'Critical').length;

  const handleSelectCluster = useCallback((clusterId: string) => {
    setHashClusterId(clusterId);
  }, [setHashClusterId]);

  if (!selectedModel) {
    return (
      <div className="stellar-page">
        <PrismPanel size="hero" shimmer style={{ maxWidth: 640, margin: '10vh auto 0' }}>
          <div className="stellar-section-label">Cluster Observatory</div>
          <h1 style={{ margin: '12px 0 0', fontSize: 30, lineHeight: 1.04 }}>当前没有可钻取的 Agent 集群</h1>
          <p style={{ marginTop: 14, lineHeight: 1.72, color: 'var(--text-secondary)' }}>
            等首页接入真实观测会话或示例场景后，这里会承接责任星图中的 cluster 深钻路径。
          </p>
          <Link href="/" style={{ marginTop: 18, display: 'inline-flex', textDecoration: 'none' }}>
            <PrismButton variant="secondary" size="md">
              <ArrowLeft size={14} />
              返回首页
            </PrismButton>
          </Link>
        </PrismPanel>
      </div>
    );
  }

  const selectedSignalCount = selectedModel.surfacedResources.length;
  const leadLane = selectedModel.agentLanes[0] ?? null;
  const selectedAlerts = alerts.filter((alert) => selectedModel.alertIds.includes(alert.alert_id));
  const selectedTrend = selectedModel.pressureScore - avgPressure;
  const controlStats = [
    {
      label: 'Critical Density',
      value: `${selectedAlerts.filter((alert) => alert.severity === 'Critical').length}.${selectedModel.boundaryCrossingCount}`,
      delta: selectedModel.highestSeverity === 'Critical' ? 'High' : 'Watching',
    },
    {
      label: 'Evidence Load',
      value: `${Math.round((selectedModel.linkedEvidence.length / Math.max(selectedModel.linkedEvidence.length + selectedSignalCount, 1)) * 100)}%`,
      delta: `+${selectedModel.linkedEvidence.length * 6}%`,
    },
    {
      label: 'Path Exposure',
      value: `${Math.min(99, selectedModel.delegationEdgeCount * 18 + selectedModel.boundaryCrossingCount * 22)}%`,
      delta: `+${selectedModel.highlightEdgeIds.length}%`,
    },
    {
      label: 'Surfaced Signals',
      value: `${selectedSignalCount}`,
      delta: `+${selectedModel.agentLanes.length * 4}%`,
    },
    {
      label: 'Cluster Score',
      value: `${selectedModel.pressureScore}`,
      delta: `${selectedTrend >= 0 ? '+' : ''}${selectedTrend}`,
    },
  ];
  const orbitModels = [selectedModel, ...clusterModels.filter((model) => model.clusterId !== selectedModel.clusterId)].slice(0, 5);
  const orbitPositions = [
    { left: '18%', top: '34%' },
    { left: '34%', top: '13%' },
    { left: '70%', top: '14%' },
    { left: '78%', top: '48%' },
    { left: '26%', top: '70%' },
  ];
  const surfacedSignals = [
    ...selectedAlerts.map((alert) => ({
      title: alert.title,
      meta: `${alert.involved_agents?.length ?? 0} agents · ${countAlertEvidence(alert)} evidence`,
      severity: alert.severity,
    })),
    ...selectedModel.surfacedResources.map((resource) => ({
      title: resource.label,
      meta: `${resource.bucket} · ${resource.evidenceCount} evidence`,
      severity: selectedModel.highestSeverity ?? 'Info',
    })),
  ].slice(0, 3);
  const criticalSeams = selectedModel.agentLanes
    .slice(0, 3)
    .map((lane) => ({
      title: `${lane.label} -> ${selectedModel.title}`,
      meta: lane.boundaryCrossingCount > 0
        ? `${lane.boundaryCrossingCount} boundary seams`
        : `${Math.max(1, lane.highlightedEdgeCount)} high-pressure paths`,
      tone: lane.boundaryCrossingCount > 0 ? 'danger' : lane.primaryAlertCount > 0 ? 'warning' : 'info',
    }));

  return (
    <div className="stellar-page">
      <ObservatoryHeader
        eyebrow="L1 Cluster Workbench"
        title="责任集群的扩散压力、边界撕裂与主 Agent"
        description="把多 agent 委托链、PolicyBoundaryCrossing、证据饱和度和资源触点并排呈现，先判断 cluster 是否值得直接进入 investigation。"
        activeSection="clusters"
        backHref="/overview"
        backLabel="返回总览"
        stats={[
          activeRunId ? `观测会话 ${activeRunId.slice(0, 12)}...` : '示例场景',
          `${clusterModels.length} clusters`,
          `${criticalClusterCount} critical`,
          `avg pressure ${avgPressure}`,
        ]}
      />

      <ObservatoryPageHero
        eyebrow="Cluster Observatory"
        title={`先判断 ${selectedModel.title} 是否值得直接立案`}
        description="参考 OpenObserve 的统一遥测节奏与 OpenSearch Security Analytics 的 findings workflow，把 cluster pressure、边界撕裂、主 Agent 与推荐动作收成同一层责任工作台。"
        statusLabel={selectedModel.highestSeverity === 'Critical' ? 'critical cluster live' : 'watching cluster drift'}
        chips={[
          `${selectedModel.alertIds.length} alert links`,
          `${selectedModel.boundaryCrossingCount} boundary seams`,
          `${selectedModel.surfacedResources.length} surfaced signals`,
        ]}
        stats={[
          {
            label: 'Lead Agent',
            value: selectedModel.leadAgentId ?? 'awaiting focus',
            hint: '当前最适合继续下钻的责任主体',
          },
          {
            label: 'Pressure',
            value: `${selectedModel.pressureScore}`,
            hint: `${selectedModel.relatedAgentIds.length} related agents in field`,
          },
          {
            label: 'Evidence',
            value: `${selectedModel.linkedEvidence.length}`,
            hint: `${evidenceItems.length} total materials available`,
          },
        ]}
        actions={[
          {
            label: '进入主调查',
            href: selectedModel.leadAlertId ? buildInvestigationHref(selectedModel.leadAlertId) : '/investigations',
            hint: '直接跳到该集群最值得裁决的案件',
          },
          {
            label: '打开主 Agent',
            href: selectedModel.leadAgentId ? buildAgentHref(selectedModel.leadAgentId) : '/agents',
            hint: '转到主体画像继续看单点责任面',
          },
        ]}
      />

      <section className="cluster-control-strip" aria-label="Cluster command metrics">
        <div className="cluster-control-strip__headline">
          <span>Cluster Control Strip</span>
          <strong>{selectedModel.title}</strong>
        </div>
        <div className="cluster-control-strip__grid">
          {controlStats.map((item) => (
            <div key={item.label} className="cluster-strip-card">
              <span>{item.label}</span>
              <strong>{item.value}</strong>
              <small>{item.delta}</small>
            </div>
          ))}
        </div>
        <div className="cluster-control-strip__window">
          <span>Time Window (UTC)</span>
          <strong>{activeRunId ? 'Current Run' : 'Last 30 min'}</strong>
          <small>Live</small>
        </div>
      </section>

      <div className="stellar-workbench stellar-workbench--cluster">
        <aside className="stellar-rail stellar-rail--sticky" aria-label="Cluster directory">
          <PrismPanel grid className="stellar-rail-panel">
            <div className="stellar-panel-heading">
              <div>
                <div className="stellar-section-label">Cluster Watchlist</div>
                <h3>Cluster Watchlist</h3>
              </div>
              <span>{clusterModels.length} Clusters</span>
            </div>
            <div className="stellar-directory-list">
              {clusterModels.map((model, index) => {
                const sparkline = buildSparklineValues(model.pressureScore + index * 9, Math.max(10, model.pressureScore / 9));
                return (
                  <button
                    key={model.clusterId}
                    type="button"
                    className="stellar-directory-row"
                    data-active={model.clusterId === selectedModel.clusterId}
                    data-tone={getSeverityTone(model.highestSeverity)}
                    onClick={() => handleSelectCluster(model.clusterId)}
                  >
                    <span className="stellar-directory-row__index">{index + 1}</span>
                    <span className="stellar-directory-row__body">
                      <div className="stellar-directory-row__topline">
                        <strong>{model.title}</strong>
                        <em>{model.highestSeverity ?? 'Info'}</em>
                      </div>
                      <div className="stellar-directory-row__sparkline" aria-hidden="true">
                        {sparkline.map((value, sparkIndex) => (
                          <span key={`${model.clusterId}-${sparkIndex}`} style={{ height: `${value}%` }} />
                        ))}
                      </div>
                      <div className="stellar-directory-row__meta">
                        <small>Pressure</small>
                        <small>{model.pressureScore}</small>
                      </div>
                      <div className="stellar-directory-row__meta">
                        <small>{model.boundaryCrossingCount} seams</small>
                        <small>{model.surfacedResources.length} signals</small>
                      </div>
                      {model.leadAgentId ? <small>lead agent: {model.leadAgentId}</small> : null}
                    </span>
                  </button>
                );
              })}
            </div>
            <div className="cluster-watchlist-action" aria-label="Cluster intake state">
              <span>Responsibility field</span>
              <strong>{clusterModels.length}</strong>
              <small>live clusters</small>
            </div>
          </PrismPanel>
        </aside>

        <main className="stellar-core">
          <PrismPanel size="lg" grid shimmer className="cluster-stage-panel">
            <div className="cluster-stage-panel__header">
              <div>
                <div className="stellar-section-label">Cluster Observatory Space</div>
                <h2>{selectedModel.title}</h2>
                <p>{selectedModel.summary}</p>
              </div>
              <div className="cluster-stage-panel__legend">
                <span data-tone="danger">Very High</span>
                <span data-tone="warning">High</span>
                <span data-tone="info">Moderate</span>
                <span data-tone="success">Low</span>
              </div>
            </div>

            <div className="cluster-stage-cosmos">
              <div className="cluster-stage-cosmos__grid" />
              <div className="cluster-stage-axis cluster-stage-axis--vertical">
                <span>High Influence</span>
                <span>Low Influence</span>
              </div>
              <div className="cluster-stage-axis cluster-stage-axis--horizontal">
                <span>Upstream Pull</span>
                <span>Downstream Reach</span>
              </div>

              {orbitModels.slice(1).map((model, index) => (
                <button
                  key={model.clusterId}
                  type="button"
                  className="cluster-stage-node"
                  data-tone={getSeverityTone(model.highestSeverity)}
                  style={orbitPositions[index]}
                  onClick={() => handleSelectCluster(model.clusterId)}
                >
                  <span className="cluster-stage-node__rank">{index + 2}</span>
                  <strong>{model.title}</strong>
                  <small>P{model.pressureScore}</small>
                </button>
              ))}

              <div className="cluster-stage-core" data-tone={getSeverityTone(selectedModel.highestSeverity)}>
                <div className="cluster-stage-core__halo" />
                <span className="cluster-stage-core__rank">1</span>
                <strong>{selectedModel.title}</strong>
                <small>Lead Cluster</small>
                <em>{selectedModel.pressureScore}</em>
              </div>

              <div className="cluster-stage-tooltip">Responsibility field · Click any cluster to inspect</div>
            </div>
          </PrismPanel>

          <div className="cluster-insight-grid">
            <PrismPanel grid className="cluster-insight-card">
              <div className="stellar-section-label">Signal Contour</div>
              <div className="cluster-insight-card__title">Where signals concentrate</div>
              <div className="cluster-contour-map">
                {orbitModels.map((model, index) => (
                  <span key={model.clusterId} style={orbitPositions[index] ?? { left: '50%', top: '50%' }}>
                    {index + 1}
                  </span>
                ))}
              </div>
            </PrismPanel>

            <PrismPanel grid className="cluster-insight-card">
              <div className="stellar-section-label">Boundary Seams</div>
              <div className="cluster-insight-card__title">Where responsibility meets friction</div>
              <div className="cluster-ring-metric">
                <strong>{selectedModel.boundaryCrossingCount + selectedModel.delegationEdgeCount}</strong>
                <span>Total Seams</span>
              </div>
              <div className="cluster-insight-list">
                <div><span>Critical</span><strong>{selectedModel.highestSeverity === 'Critical' ? selectedModel.boundaryCrossingCount : 0}</strong></div>
                <div><span>High</span><strong>{selectedModel.delegationEdgeCount}</strong></div>
                <div><span>Medium</span><strong>{Math.max(1, selectedModel.agentLanes.length - 1)}</strong></div>
              </div>
            </PrismPanel>

            <PrismPanel grid className="cluster-insight-card">
              <div className="stellar-section-label">Evidence Saturation</div>
              <div className="cluster-insight-card__title">How complete is our view</div>
              <div className="cluster-ring-metric cluster-ring-metric--cool">
                <strong>{Math.round((selectedModel.linkedEvidence.length / Math.max(selectedSignalCount + selectedModel.linkedEvidence.length, 1)) * 100)}%</strong>
                <span>Evidence Load</span>
              </div>
              <div className="cluster-insight-list">
                <div><span>Collected</span><strong>{selectedModel.linkedEvidence.length}</strong></div>
                <div><span>Expected</span><strong>{selectedSignalCount + selectedModel.linkedEvidence.length}</strong></div>
                <div><span>Gaps</span><strong>{Math.max(0, selectedSignalCount - selectedModel.linkedEvidence.length)}</strong></div>
              </div>
            </PrismPanel>

            <PrismPanel grid className="cluster-insight-card">
              <div className="stellar-section-label">Reading Cues</div>
              <div className="cluster-insight-card__title">How to interpret this view</div>
              <div className="cluster-reading-list">
                <div><Target size={14} /><span>{selectedModel.title} 是当前责任引力中心。</span></div>
                <div><GitBranch size={14} /><span>{selectedModel.agentLanes.length} 条 delegation lanes 把压力推向它。</span></div>
                <div><Sparkles size={14} /><span>证据仍需补足时，优先从边界撕裂最大的缝隙进入。</span></div>
              </div>
            </PrismPanel>
          </div>

          <PrismPanel grid className="stellar-table-panel">
            <div className="stellar-panel-heading">
              <div>
                <div className="stellar-section-label">Cluster Watch Queue</div>
                <h3>责任集群队列</h3>
              </div>
              <span>{clusterModels.length} entries</span>
            </div>
            <div className="stellar-data-table" role="table" aria-label="Cluster pressure queue">
              <div className="stellar-data-table__head" role="row">
                <span role="columnheader">Cluster</span>
                <span role="columnheader">Pressure</span>
                <span role="columnheader">Boundary</span>
                <span role="columnheader">Lead</span>
                <span role="columnheader">Next</span>
              </div>
              {clusterModels.map((model) => (
                <button
                  key={model.clusterId}
                  type="button"
                  className="stellar-data-table__row"
                  data-selected={model.clusterId === selectedModel.clusterId}
                  onClick={() => handleSelectCluster(model.clusterId)}
                  role="row"
                >
                  <span role="cell">
                    <strong>{model.title}</strong>
                    <small>{model.highestSeverity ?? 'Info'} · {model.linkedEvidence.length} evidence</small>
                  </span>
                  <span role="cell" data-tone={model.pressureScore >= 80 ? 'danger' : 'warning'}>{model.pressureScore}</span>
                  <span role="cell">{model.boundaryCrossingCount}</span>
                  <span role="cell">{model.leadAgentId ?? 'pending'}</span>
                  <span role="cell"><ArrowRight size={13} /></span>
                </button>
              ))}
            </div>
          </PrismPanel>

          <div className="stellar-lower-grid">
            <PrismPanel grid>
              <div className="stellar-section-label">Delegation Lanes</div>
              <div className="stellar-chip-stack">
                {selectedModel.agentLanes.slice(0, 4).map((lane) => (
                  <Link key={lane.agentId} href={buildAgentHref(lane.agentId)} className="stellar-chip-row stellar-chip-row--link">
                    <span>
                      <strong>{lane.label}</strong>
                      <small>{lane.primaryAlertCount} primary alerts · {lane.linkedEvidenceCount} evidence</small>
                    </span>
                    <ArrowRight size={13} />
                  </Link>
                ))}
              </div>
            </PrismPanel>

            <PrismPanel grid>
              <div className="stellar-section-label">Materialized Surface</div>
              <div className="stellar-chip-stack">
                {selectedModel.surfacedResources.slice(0, 5).map((resource) => (
                  <div key={resource.nodeId} className="stellar-chip-row">
                    <span>
                      <strong>{resource.label}</strong>
                      <small>{resource.bucket} · {resource.evidenceCount} evidence refs</small>
                    </span>
                  </div>
                ))}
                {!selectedModel.surfacedResources.length ? (
                  <div className="stellar-chip-row">等待真实资源接触轨迹接入</div>
                ) : null}
              </div>
            </PrismPanel>
          </div>
        </main>

        <aside className="stellar-inspector" aria-label="Selected cluster inspector">
          <PrismPanel grid className="stellar-inspector-panel">
            <div className="stellar-panel-heading">
              <div>
                <div className="stellar-section-label">Decision Deck</div>
                <h3>Decision Deck</h3>
              </div>
              <span>{selectedSignalCount} signals</span>
            </div>

            <div className="cluster-focus-card">
              <div className="cluster-focus-card__header">
                <div>
                  <span>Focus: {selectedModel.title}</span>
                  <small>Lead Cluster</small>
                </div>
                <strong>{selectedTrend >= 0 ? '+' : ''}{selectedTrend}</strong>
              </div>
              <div className="cluster-focus-card__score">
                <div>
                  <span>Pressure Score</span>
                  <strong>{selectedModel.pressureScore}</strong>
                </div>
                <div className="cluster-focus-card__spark" aria-hidden="true">
                  {buildSparklineValues(selectedModel.pressureScore, 14).map((value, index) => (
                    <span key={`focus-${index}`} style={{ height: `${value}%` }} />
                  ))}
                </div>
              </div>
              <p>Highest critical density and strongest upstream pull across {selectedModel.relatedAgentIds.length} agents.</p>
            </div>

            <div className="cluster-decision-section">
              <div className="stellar-panel-heading">
                <div>
                  <div className="stellar-section-label">Surfaced Signals</div>
                  <h3>Surfaced Signals</h3>
                </div>
                <span>Top {surfacedSignals.length}</span>
              </div>
              <div className="stellar-evidence-list">
                {surfacedSignals.map((item) => (
                  <div key={`${item.title}-${item.meta}`} className="cluster-decision-row">
                    <span className="cluster-decision-row__tone" data-tone={getSeverityTone(item.severity)} />
                    <div>
                      <strong>{item.title}</strong>
                      <small>{item.meta}</small>
                    </div>
                    <em>{item.severity}</em>
                  </div>
                ))}
              </div>
            </div>

            <div className="cluster-decision-section">
              <div className="stellar-panel-heading">
                <div>
                  <div className="stellar-section-label">Critical Seams</div>
                  <h3>Critical Seams</h3>
                </div>
                <span>{criticalSeams.length}</span>
              </div>
              <div className="stellar-evidence-list">
                {criticalSeams.map((item) => (
                  <div key={item.title} className="cluster-decision-row">
                    <span className="cluster-decision-row__tone" data-tone={item.tone} />
                    <div>
                      <strong>{item.title}</strong>
                      <small>{item.meta}</small>
                    </div>
                    <em>{item.tone === 'danger' ? 'Critical Path' : item.tone === 'warning' ? 'High Pressure' : 'Watch'}</em>
                  </div>
                ))}
              </div>
            </div>

            <div className="cluster-decision-section">
              <div className="stellar-panel-heading">
                <div>
                  <div className="stellar-section-label">Recommended Next Steps</div>
                  <h3>Recommended Next Steps</h3>
                </div>
              </div>
              <div className="cluster-recommended-grid">
                {selectedModel.leadAlertId ? (
                  <Link href={buildInvestigationHref(selectedModel.leadAlertId)} className="cluster-recommended-action">
                    <Radar size={15} />
                    <span>Investigate {selectedModel.title}</span>
                  </Link>
                ) : null}
                {selectedModel.leadAgentId ? (
                  <Link href={buildAgentHref(selectedModel.leadAgentId)} className="cluster-recommended-action">
                    <GitBranch size={15} />
                    <span>Deep dive lead cluster</span>
                  </Link>
                ) : null}
                <button type="button" className="cluster-recommended-action">
                  <Activity size={15} />
                  <span>Review surfaced signals</span>
                </button>
                <Link href="/handoff" className="cluster-recommended-action">
                  <Waypoints size={15} />
                  <span>Review handoff lanes</span>
                </Link>
              </div>
            </div>

            <div className="cluster-decision-section">
              <div className="stellar-panel-heading">
                <div>
                  <div className="stellar-section-label">Pressure Ledger</div>
                  <h3>Pressure Ledger</h3>
                </div>
                <span>Ranking</span>
              </div>
              <div className="cluster-pressure-ledger">
                {clusterModels.map((model, index) => (
                  <div key={model.clusterId} className="cluster-pressure-ledger__row">
                    <span>{index + 1}</span>
                    <strong>{model.title}</strong>
                    <em>{model.pressureScore}</em>
                    <div className="cluster-pressure-ledger__bar">
                      <span style={{ width: `${model.pressureScore}%` }} data-tone={getSeverityTone(model.highestSeverity)} />
                    </div>
                  </div>
                ))}
              </div>
            </div>

            <div className="stellar-section-label">Investigation Aperture</div>
            <div className="stellar-action-column">
              {selectedModel.leadAgentId ? (
                <Link href={buildAgentHref(selectedModel.leadAgentId)}>
                  <PrismButton variant="secondary" size="sm">打开主 Agent</PrismButton>
                </Link>
              ) : null}
              {selectedModel.leadAlertId ? (
                <Link href={buildInvestigationHref(selectedModel.leadAlertId)}>
                  <PrismButton variant="primary" size="sm">进入主调查</PrismButton>
                </Link>
              ) : null}
              <div className="stellar-evidence-row">
                <strong>{leadLane?.label ?? '等待主 Agent 形成'}</strong>
                <span>
                  {leadLane
                    ? `${leadLane.primaryAlertCount} primary alerts · ${leadLane.linkedEvidenceCount} evidence refs`
                    : 'collector 继续汇聚 agent lane 后会在这里显示'}
                </span>
              </div>
              {selectedModel.linkedEvidence.slice(0, 2).map((item) => (
                <div key={item.evidence_id} className="stellar-evidence-row">
                  <strong>{item.evidence_id}</strong>
                  <span>{item.match_level} · {formatConfidence(item.confidence)} confidence</span>
                </div>
              ))}
            </div>
          </PrismPanel>
        </aside>
      </div>
    </div>
  );
}
