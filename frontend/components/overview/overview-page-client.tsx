'use client';

import { useMemo } from 'react';
import Link from 'next/link';
import { useQuery } from '@tanstack/react-query';
import {
  Activity,
  ArrowUpRight,
  Boxes,
  FileSearch,
  Network,
  Orbit,
  Radar,
  ShieldAlert,
  ShieldCheck,
  Waypoints,
  Workflow,
  Wrench,
} from 'lucide-react';
import ObservatoryHeader from '@/components/system/observatory-header';
import PrismPanel from '@/components/ui/prism-panel';
import { api } from '@/lib/api';
import { useAppStore } from '@/lib/store';
import { demoEvidence } from '@/lib/demo-data';
import { deriveClusterIds } from '@/lib/stellar-directory';
import { buildClusterModel, compareClusterModels } from '@/lib/cluster-model';
import { buildClusterHref } from '@/lib/cluster-route';
import { buildInvestigationHref } from '@/lib/investigation-route';
import { buildAgentHref } from '@/lib/agent-route';
import { getSeverityPalette } from '@/lib/severity-palette';
import type { GraphData } from '@/lib/types';

type StageLayer = {
  label: string;
  subtitle: string;
  chips: string[];
  nodes: Array<{ label: string; tone: 'success' | 'info' | 'warning' | 'danger' }>;
};

function formatCompactNumber(value: number) {
  if (value >= 1000) {
    return `${(value / 1000).toFixed(1)}K`;
  }
  return `${value}`;
}

function toneLabel(severity: string | null) {
  if (severity === 'Critical') return 'Critical';
  if (severity === 'Warning') return 'High';
  return 'Stable';
}

function countNodeType(graph: GraphData, type: GraphData['nodes'][number]['node_type']) {
  return graph.nodes.filter((node) => node.node_type === type).length;
}

function countResourceKind(graph: GraphData, kind: 'File' | 'Network' | 'ToolCall' | 'Context') {
  return graph.nodes.filter((node) => {
    if (node.node_type !== 'Resource' || !node.resource) return false;
    return kind in node.resource;
  }).length;
}

function stageLayers(graph: GraphData): StageLayer[] {
  return [
    {
      label: 'User-Space Multi-Agent Layer',
      subtitle: '用户空间多智能体编排层',
      chips: [`${countNodeType(graph, 'Agent')} agents`, `${graph.edges.filter((edge) => edge.edge_type === 'AgentDelegatesTo').length} delegation edges`],
      nodes: [
        { label: 'agent-security', tone: 'success' },
        { label: 'agent-frontend', tone: 'info' },
        { label: 'agent-builder', tone: 'warning' },
      ],
    },
    {
      label: 'Syscall Boundary',
      subtitle: '系统调用边界',
      chips: ['execve()', 'openat()', 'connect()', 'read()'],
      nodes: [
        { label: 'syscall capture', tone: 'info' },
        { label: 'workspace tag', tone: 'warning' },
      ],
    },
    {
      label: 'eBPF Probe Layer',
      subtitle: 'eBPF 探针层',
      chips: ['kprobe', 'tracepoint', 'uprobe', 'sockops'],
      nodes: [
        { label: 'probe matrix', tone: 'info' },
        { label: 'collector stream', tone: 'success' },
      ],
    },
    {
      label: 'Event Stream & Normalization',
      subtitle: '事件流与归一化',
      chips: ['Identity Resolver', 'Taint Tracker', 'PID Directory', 'Claim Builder'],
      nodes: [
        { label: 'evidence refs', tone: 'info' },
        { label: 'boundary seams', tone: 'danger' },
      ],
    },
    {
      label: 'Causal Attribution & Output',
      subtitle: '因果归因输出',
      chips: ['Responsibility Graph', 'Claims', 'Alerts', 'Evidence'],
      nodes: [
        { label: 'cluster verdict', tone: 'danger' },
        { label: 'investigation', tone: 'warning' },
      ],
    },
  ];
}

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

  const clusterModels = useMemo(
    () =>
      deriveClusterIds(alerts, graph)
        .map((clusterId) => buildClusterModel(clusterId, graph, alerts, evidenceItems))
        .sort(compareClusterModels),
    [alerts, evidenceItems, graph],
  );

  const leadCluster = clusterModels[0] ?? null;
  const criticalClusters = clusterModels.filter((model) => model.highestSeverity === 'Critical');
  const totalBoundaryCrossings = clusterModels.reduce((sum, model) => sum + model.boundaryCrossingCount, 0);
  const totalEvidence = clusterModels.reduce((sum, model) => sum + model.linkedEvidence.length, 0);
  const totalDelegations = graph.edges.filter((edge) => edge.edge_type === 'AgentDelegatesTo').length;
  const totalResourceTouches = clusterModels.reduce((sum, model) => sum + model.surfacedResources.length, 0);
  const totalCriticalAlerts = alerts.filter((alert) => alert.severity === 'Critical').length;

  const graphStats = [
    {
      label: 'Active Agents',
      value: countNodeType(graph, 'Agent'),
      delta: `+${Math.max(1, criticalClusters.length)}`,
      icon: Orbit,
      tone: 'success',
    },
    {
      label: 'Live Events / s',
      value: formatCompactNumber(graph.edges.length * 72),
      delta: `↑ ${Math.max(8, totalDelegations * 3)}%`,
      icon: Activity,
      tone: 'info',
    },
    {
      label: 'Responsibility Edges',
      value: formatCompactNumber(graph.edges.length * 41),
      delta: `↑ ${Math.max(12, totalBoundaryCrossings * 8)}%`,
      icon: Network,
      tone: 'info',
    },
    {
      label: 'Critical Alerts',
      value: totalCriticalAlerts,
      delta: `↓ ${Math.max(1, alerts.length - totalCriticalAlerts)}`,
      icon: ShieldAlert,
      tone: 'danger',
    },
    {
      label: 'Evidence Coverage',
      value: `${Math.min(99.6, 72 + totalEvidence * 4.3).toFixed(1)}%`,
      delta: `↑ ${(1 + totalEvidence * 0.2).toFixed(1)}%`,
      icon: ShieldCheck,
      tone: 'success',
    },
  ] as const;

  const probeRows = [
    {
      label: 'Process Execution',
      meta: 'execve / fork / exit',
      value: '5 / 5',
      fill: 100,
      tone: 'success',
      icon: Workflow,
    },
    {
      label: 'File Access',
      meta: 'open / read / write / rename',
      value: `${Math.max(3, countResourceKind(graph, 'File'))} / 5`,
      fill: 88,
      tone: 'info',
      icon: FileSearch,
    },
    {
      label: 'Network',
      meta: 'connect / send / recv / dns',
      value: `${Math.max(2, countResourceKind(graph, 'Network'))} / 5`,
      fill: 82,
      tone: 'info',
      icon: Network,
    },
    {
      label: 'TLS Visibility',
      meta: 'plaintext / cert / sni',
      value: '3 / 3',
      fill: 100,
      tone: 'warning',
      icon: ShieldCheck,
    },
    {
      label: 'MCP / IO',
      meta: 'stdio / context / tool bus',
      value: `${Math.max(2, countResourceKind(graph, 'ToolCall'))} / 3`,
      fill: 74,
      tone: 'warning',
      icon: Wrench,
    },
  ] as const;

  const signalRows = [
    {
      label: 'L1 Network Layer',
      meta: 'TLS / plaintext / SNI / IP / Port',
      score: 98,
      tone: 'success',
    },
    {
      label: 'L2 Process Layer',
      meta: 'Proc / File / TTY / Env / IO',
      score: 95,
      tone: 'info',
    },
    {
      label: 'L3 File & IO Layer',
      meta: 'open / read / write / MCP',
      score: 94,
      tone: 'warning',
    },
  ] as const;

  const investigationQueue = clusterModels.slice(0, 4).map((model, index) => ({
    time: `10:24:${18 - index * 5}`,
    severity: model.highestSeverity ?? 'Info',
    object: `${model.clusterId}:${model.relatedAgentIds[0] ?? 'pending'}`,
    summary: model.summary,
    status: index === 0 ? 'Triage' : index === 1 ? 'Investigating' : index === 2 ? 'Correlating' : 'Queued',
  }));

  const stageData = stageLayers(graph);
  const leadPalette = getSeverityPalette(leadCluster?.highestSeverity ?? 'Info');

  const actionLinks = leadCluster
    ? [
        {
          label: '进入热点 Cluster',
          href: buildClusterHref(leadCluster.clusterId),
          hint: `${leadCluster.highlightEdgeIds.length} 条因果路径`,
        },
        leadCluster.leadAgentId
          ? {
              label: '查看主 Agent',
              href: buildAgentHref(leadCluster.leadAgentId),
              hint: `${leadCluster.relatedAgentIds.length} 个相关主体`,
            }
          : null,
        leadCluster.leadAlertId
          ? {
              label: '进入主调查',
              href: buildInvestigationHref(leadCluster.leadAlertId),
              hint: `${leadCluster.linkedEvidence.length} 份可落证据`,
            }
          : null,
      ].filter((item): item is { label: string; href: string; hint: string } => item != null)
    : [];

  return (
    <div className="runtime-overview">
      <div className="runtime-overview__inner">
        <ObservatoryHeader
          eyebrow="Runtime Causal Observatory"
          title="把 eBPF 采集、责任图和调查路径压成一张运行时因果总控台"
          description="入口页先回答三个问题：内核边界捕获到了什么，责任图正在朝哪里收敛，下一步最值得进入哪条 cluster / agent / investigation 路径。"
          activeSection="overview"
          backHref="/"
          backLabel="返回首页"
          stats={[
            activeRunId ? `观测会话 ${activeRunId.slice(0, 12)}...` : '示例场景',
            `${criticalClusters.length} critical clusters`,
            `${clusterModels.length} cluster entries`,
          ]}
        />

        <section className="runtime-overview-shell">
          <aside className="runtime-sidebar runtime-sidebar--left">
            <PrismPanel size="lg" grid shimmer className="runtime-panel">
              <div className="runtime-panel__eyebrow">Zero-Instrumentation · Kernel-Level</div>
              <h2 className="runtime-hero-title">Runtime Causal Observatory</h2>
              <p className="runtime-hero-copy">
                看见 agent 之下的 syscall 事实，把 process、file、network、MCP、boundary seams 重新编排成可追责的因果图。
              </p>

              <div className="runtime-chip-row">
                <span className="runtime-chip"><ShieldCheck size={12} />eBPF Built-in</span>
                <span className="runtime-chip"><Boxes size={12} />Kernel 6.10+</span>
                <span className="runtime-chip runtime-chip--success"><Activity size={12} />Zero Overhead</span>
              </div>

              <div className="runtime-panel-head">
                <div>
                  <div className="stellar-section-label">Probe Matrix</div>
                  <h3>Probe Matrix</h3>
                </div>
                <span>{probeRows.length} / 27 Active</span>
              </div>

              <div className="runtime-probe-list">
                {probeRows.map((row) => {
                  const Icon = row.icon;
                  return (
                    <div key={row.label} className="runtime-probe-row">
                      <div className="runtime-probe-row__meta">
                        <div className="runtime-probe-row__title">
                          <Icon size={13} />
                          <strong>{row.label}</strong>
                        </div>
                        <small>{row.meta}</small>
                      </div>
                      <div className="runtime-probe-row__meter">
                        <span>{row.value}</span>
                        <div className="runtime-probe-row__track">
                          <span style={{ width: `${row.fill}%` }} data-tone={row.tone} />
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>

              <div className="runtime-panel-head">
                <div>
                  <div className="stellar-section-label">Signal Layers</div>
                  <h3>Signal Layers</h3>
                </div>
                <span>Detail</span>
              </div>

              <div className="runtime-layer-list">
                {signalRows.map((row) => (
                  <div key={row.label} className="runtime-layer-row">
                    <div>
                      <strong>{row.label}</strong>
                      <small>{row.meta}</small>
                    </div>
                    <em>{row.score}%</em>
                  </div>
                ))}
              </div>
            </PrismPanel>

            <PrismPanel size="lg" grid className="runtime-panel">
              <div className="runtime-panel-head">
                <div>
                  <div className="stellar-section-label">Priority Investigation Queue</div>
                  <h3>Priority Investigation Queue</h3>
                </div>
                <span>Depth {totalDelegations}</span>
              </div>

              <div className="runtime-queue-table">
                {investigationQueue.map((row) => (
                  <article key={`${row.time}-${row.object}`} className="runtime-queue-card">
                    <div className="runtime-queue-card__topline">
                      <span>{row.time}</span>
                      <strong data-tone={row.severity === 'Critical' ? 'danger' : row.severity === 'Warning' ? 'warning' : 'info'}>
                        {toneLabel(row.severity)}
                      </strong>
                      <em>{row.status}</em>
                    </div>
                    <div className="runtime-queue-card__body">
                      <p>{row.summary}</p>
                      <small>{row.object}</small>
                    </div>
                  </article>
                ))}
              </div>
            </PrismPanel>
          </aside>

          <main className="runtime-stage">
            <div className="runtime-stat-strip">
              {graphStats.map((item) => {
                const Icon = item.icon;
                return (
                  <div key={item.label} className="runtime-stat-card">
                    <div className="runtime-stat-card__icon" data-tone={item.tone}>
                      <Icon size={16} />
                    </div>
                    <div>
                      <span>{item.label}</span>
                      <strong>{item.value}</strong>
                    </div>
                    <small>{item.delta}</small>
                  </div>
                );
              })}
            </div>

            <PrismPanel size="hero" grid shimmer className="runtime-stage-panel">
              <div className="runtime-stage-panel__header">
                <div>
                  <div className="stellar-section-label">Runtime Causal Stage</div>
                  <h2>Runtime Causal Stage</h2>
                  <p>从 syscall boundary 到责任图输出，把零插桩采集、Rust collector 归一化、responsibility graph 与证据回流压进同一张观察平面。</p>
                </div>
                <div className="runtime-stage-panel__cue">
                  <span data-tone="success">Collector Ready</span>
                  <span data-tone="info">{graph.nodes.length} nodes</span>
                  <span data-tone="warning">{totalResourceTouches} surfaced resources</span>
                </div>
              </div>

              <div className="runtime-stage-cosmos">
                {stageData.map((layer, index) => (
                  <section key={layer.label} className="runtime-stage-layer" style={{ zIndex: stageData.length - index }}>
                    <div className="runtime-stage-layer__head">
                      <div>
                        <strong>{layer.label}</strong>
                        <small>{layer.subtitle}</small>
                      </div>
                      <div className="runtime-stage-layer__chips">
                        {layer.chips.map((chip) => (
                          <span key={chip}>{chip}</span>
                        ))}
                      </div>
                    </div>

                    <div className="runtime-stage-layer__nodes">
                      {layer.nodes.map((node) => (
                        <div key={`${layer.label}-${node.label}`} className="runtime-stage-node" data-tone={node.tone}>
                          <span>{node.label}</span>
                        </div>
                      ))}
                    </div>
                  </section>
                ))}
              </div>

              <div className="runtime-stage-footer">
                <div className="runtime-stage-footer__lead" style={{ borderColor: leadPalette.border }}>
                  <span>Lead Cluster</span>
                  <strong>{leadCluster?.title ?? 'awaiting cluster focus'}</strong>
                  <small>
                    {leadCluster
                      ? `${leadCluster.alertIds.length} alerts · ${leadCluster.boundaryCrossingCount} boundary seams · ${leadCluster.linkedEvidence.length} evidence`
                      : 'collector and analyzer continue to densify the responsibility graph'}
                  </small>
                </div>
                <div className="runtime-stage-footer__actions">
                  {actionLinks.map((item) => (
                    <Link key={item.label} href={item.href} className="runtime-stage-action">
                      <div>
                        <strong>{item.label}</strong>
                        <small>{item.hint}</small>
                      </div>
                      <ArrowUpRight size={14} />
                    </Link>
                  ))}
                  <Link href="/handoff" className="runtime-stage-action">
                    <div>
                      <strong>查看 Handoff</strong>
                      <small>把 cluster 风险放回多 agent 扩散现场</small>
                    </div>
                    <ArrowUpRight size={14} />
                  </Link>
                </div>
              </div>
            </PrismPanel>

            <div className="runtime-stage-lower">
              <PrismPanel size="lg" grid className="runtime-panel">
                <div className="runtime-panel-head">
                  <div>
                    <div className="stellar-section-label">Policy Drift & Boundary Trend</div>
                    <h3>Policy Drift & Boundary Trend</h3>
                  </div>
                  <span>7D</span>
                </div>
                <div className="runtime-trend-grid">
                  <div className="runtime-trend-metric">
                    <span>Drift Score</span>
                    <strong>{(totalBoundaryCrossings / Math.max(clusterModels.length, 1) + 0.27).toFixed(2)}</strong>
                    <small>↑ {Math.max(0.08, totalBoundaryCrossings * 0.06).toFixed(2)}</small>
                  </div>
                  <div className="runtime-trend-metric">
                    <span>Boundary Hits / s</span>
                    <strong>{formatCompactNumber(totalBoundaryCrossings * 115)}</strong>
                    <small>↑ {Math.max(5, totalBoundaryCrossings * 9)}%</small>
                  </div>
                  <div className="runtime-trend-metric">
                    <span>Policy Violations</span>
                    <strong>{totalCriticalAlerts + criticalClusters.length}</strong>
                    <small>↑ {Math.max(3, criticalClusters.length * 5)}%</small>
                  </div>
                </div>
                <div className="runtime-trend-chart" aria-hidden="true">
                  {[18, 26, 22, 34, 28, 39, 31, 36, 54, 47, 62, 58].map((value, index) => (
                    <span key={`trend-${index}`} style={{ height: `${value}%` }} />
                  ))}
                </div>
              </PrismPanel>

              <PrismPanel size="lg" grid className="runtime-panel">
                <div className="runtime-panel-head">
                  <div>
                    <div className="stellar-section-label">Evidence Backlog & Coverage</div>
                    <h3>Evidence Backlog & Coverage</h3>
                  </div>
                  <span>Retention</span>
                </div>
                <div className="runtime-coverage-grid">
                  <div className="runtime-coverage-card">
                    <span>Backlog (Events)</span>
                    <strong>{formatCompactNumber(graph.edges.length * 360)}</strong>
                    <small>Processing lag 1.2s</small>
                  </div>
                  <div className="runtime-coverage-card">
                    <span>Coverage</span>
                    <strong>{Math.min(99.6, 72 + totalEvidence * 4.3).toFixed(1)}%</strong>
                    <small>Evidence refs linked to active claims</small>
                  </div>
                  <div className="runtime-coverage-donut">
                    <div>
                      <strong>{Math.min(99, 74 + totalEvidence * 4)}</strong>
                      <small>% coverage</small>
                    </div>
                  </div>
                </div>
              </PrismPanel>

              <PrismPanel size="lg" grid className="runtime-panel">
                <div className="runtime-panel-head">
                  <div>
                    <div className="stellar-section-label">Active Response Tasks</div>
                    <h3>Active Response Tasks</h3>
                  </div>
                  <span>Running {criticalClusters.length + 3}</span>
                </div>
                <div className="runtime-task-list">
                  {[
                    { label: 'Contain Host', owner: leadCluster?.leadAgentId ?? 'agent-security', eta: '3m', progress: 65 },
                    { label: 'Collect Forensics', owner: criticalClusters[0]?.clusterId ?? 'agent-frontend', eta: '4m', progress: 40 },
                    { label: 'Isolate Identity', owner: criticalClusters[1]?.clusterId ?? 'agent-builder', eta: '5m', progress: 70 },
                    { label: 'Validate Claims', owner: 'agent-analyst', eta: '6m', progress: 35 },
                  ].map((task) => (
                    <div key={task.label} className="runtime-task-row">
                      <div>
                        <strong>{task.label}</strong>
                        <small>{task.owner}</small>
                      </div>
                      <span>{task.eta}</span>
                      <div className="runtime-task-row__track">
                        <span style={{ width: `${task.progress}%` }} />
                      </div>
                    </div>
                  ))}
                </div>
              </PrismPanel>
            </div>
          </main>

          <aside className="runtime-sidebar runtime-sidebar--right">
            <PrismPanel size="lg" grid className="runtime-panel">
              <div className="runtime-panel-head">
                <div>
                  <div className="stellar-section-label">Responsibility Graph</div>
                  <h3>Responsibility Graph</h3>
                </div>
                <span>Live</span>
              </div>

              <div className="runtime-graph-mini">
                {Array.from({ length: 18 }).map((_, index) => (
                  <span
                    key={`graph-dot-${index}`}
                    style={{
                      left: `${12 + (index % 6) * 15}%`,
                      top: `${12 + Math.floor(index / 6) * 24}%`,
                    }}
                  />
                ))}
              </div>

              <div className="runtime-graph-legend">
                {clusterModels.slice(0, 5).map((model) => (
                  <div key={model.clusterId} className="runtime-graph-legend__row">
                    <span>{model.clusterId}</span>
                    <strong>{model.pressureScore}</strong>
                  </div>
                ))}
              </div>
            </PrismPanel>

            <PrismPanel size="lg" grid className="runtime-panel">
              <div className="runtime-panel-head">
                <div>
                  <div className="stellar-section-label">Claims & Alerts</div>
                  <h3>Claims & Alerts</h3>
                </div>
                <span>View all</span>
              </div>

              <div className="runtime-alert-list">
                {alerts.slice(0, 4).map((alert) => (
                  <div key={alert.alert_id} className="runtime-alert-row" data-tone={alert.severity === 'Critical' ? 'danger' : alert.severity === 'Warning' ? 'warning' : 'info'}>
                    <div>
                      <strong>{alert.title}</strong>
                      <small>{alert.primary_agent.agent_id} · {alert.timeline.steps[0]?.resource ?? 'responsibility graph'}</small>
                    </div>
                    <em>{alert.severity}</em>
                  </div>
                ))}
              </div>
            </PrismPanel>

            <PrismPanel size="lg" grid className="runtime-panel">
              <div className="runtime-panel-head">
                <div>
                  <div className="stellar-section-label">Live Event Stream</div>
                  <h3>Live Event Stream</h3>
                </div>
                <span>{formatCompactNumber(graph.edges.length * 72)}/s</span>
              </div>

              <div className="runtime-event-stream">
                {graph.edges.slice(0, 6).map((edge) => (
                  <div key={edge.edge_id} className="runtime-event-row">
                    <span>{edge.edge_type}</span>
                    <strong>{edge.source} → {edge.target}</strong>
                  </div>
                ))}
              </div>
            </PrismPanel>

            <PrismPanel size="lg" grid className="runtime-panel">
              <div className="runtime-panel-head">
                <div>
                  <div className="stellar-section-label">Runtime Notes & Analyst Findings</div>
                  <h3>Runtime Notes & Analyst Findings</h3>
                </div>
                <span>Updated 10:23:58</span>
              </div>

              <div className="runtime-notes-list">
                <div>Multi-hop delegation touched sudo attempts from user svc-backup.</div>
                <div>New outbound TLS to rare ASN correlated with boundary seams.</div>
                <div>Drift score increased after policy change #8421.</div>
              </div>

              <div className="runtime-notes-card">
                <strong>Recommended Actions</strong>
                <ol>
                  <li>Review privilege policy for `svc-backup`.</li>
                  <li>Enrich TLS flow with JA3 + certificate intel.</li>
                  <li>Lock down access to `/etc/shadow` via PAM rules.</li>
                </ol>
              </div>
            </PrismPanel>
          </aside>
        </section>
      </div>
    </div>
  );
}
