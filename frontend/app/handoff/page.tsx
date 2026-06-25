'use client';

import { useCallback, useEffect, useMemo, useRef } from 'react';
import Link from 'next/link';
import {
  Activity,
  ArrowRight,
  FileSearch,
  GitBranch,
  ShieldAlert,
  Waypoints,
  Zap,
} from 'lucide-react';
import ObservatoryHeader from '@/components/system/observatory-header';
import { HandoffDashboardShell } from '@/components/ui/handoff-dashboard-shell';
import PrismButton from '@/components/ui/prism-button';
import PrismPanel from '@/components/ui/prism-panel';
import { useHandoffStore } from '@/lib/handoff-store';
import { buildHandoffWorkbenchModel, inferHandoffEventSeverity } from '@/lib/handoff-workbench';
import { connectUnifiedSSE } from '@/lib/sse';

const handoffBackendEnabled = process.env.NEXT_PUBLIC_ENABLE_HANDOFF_BACKEND === 'true';

function getSeverityTone(severity: 'info' | 'warning' | 'critical') {
  if (severity === 'critical') return 'danger';
  if (severity === 'warning') return 'warning';
  return 'info';
}

function formatPercent(value: number) {
  return `${Math.round(value * 100)}%`;
}

function formatEventRelativeMinutes(referenceMs: number, eventMs: number | undefined) {
  if (!eventMs) return '--';
  const deltaMinutes = Math.max(1, Math.round((referenceMs - eventMs) / 60000));
  return `${deltaMinutes}m ago`;
}

function eventTimestampMs(event: Record<string, unknown>) {
  const timestamp = event.timestamp_ms ?? event.dispatched_at_ms;
  return typeof timestamp === 'number' ? timestamp : undefined;
}

function sparklineBars(seed: number, count = 18) {
  return Array.from({ length: count }).map((_, index) => {
    const height = 28 + Math.sin((seed + index * 1.9) / 2.7) * 18 + Math.cos((seed + index) / 4.2) * 9 + index * 0.5;
    return Math.max(18, Math.min(84, Math.round(height)));
  });
}

export default function HandoffPage() {
  const store = useHandoffStore();
  const initialized = useRef(false);

  const handoffModel = useMemo(
    () => buildHandoffWorkbenchModel(store.delegationTree, store.metrics, store.recentEvents),
    [store.delegationTree, store.metrics, store.recentEvents],
  );

  const criticalEventCount = useMemo(
    () =>
      store.recentEvents.filter((event) => inferHandoffEventSeverity(event) === 'critical').length,
    [store.recentEvents],
  );

  const referenceTimeMs = useMemo(() => {
    const first = store.recentEvents
      .map((event) => eventTimestampMs(event as unknown as Record<string, unknown>))
      .find((value): value is number => typeof value === 'number');

    return first ?? Date.now();
  }, [store.recentEvents]);

  const leadLane = handoffModel.leadLane;
  const leadFocus = handoffModel.leadFocus;
  const feedEvents = handoffModel.focusEvents.slice(0, 6);
  const laneNodes = handoffModel.activeLanes.slice(0, 5);

  const verdictRows = [
    {
      label: 'Trust Decay',
      value: Math.min(96, 24 + store.metrics.avg_context_overhead * 110),
      tone: leadFocus?.severity === 'critical' ? 'danger' : 'warning',
      meta: leadFocus?.severity === 'critical' ? 'High' : 'Watching',
    },
    {
      label: 'Boundary Drift',
      value: Math.min(96, 18 + handoffModel.signalCounts.boundary * 18),
      tone: handoffModel.signalCounts.boundary > 1 ? 'danger' : 'warning',
      meta: `${handoffModel.signalCounts.boundary} lanes`,
    },
    {
      label: 'Evidence Continuity',
      value: Math.min(96, 36 + store.metrics.workspace_tag_survival_rate * 48),
      tone: 'warning',
      meta: `${formatPercent(store.metrics.workspace_tag_survival_rate)} intact`,
    },
    {
      label: 'Delivery Confidence',
      value: Math.min(96, 32 + Math.max(0, 100 - store.metrics.fan_out_ratio * 16)),
      tone: store.metrics.fan_out_ratio > 2 ? 'warning' : 'info',
      meta: `${store.metrics.total_completed}/${store.metrics.total_spawned}`,
    },
  ];

  const chainStages = [
    {
      label: 'Initiator',
      agent: store.delegationTree?.agent_id ?? 'root-agent',
      meta: `Depth 0 · ${(store.delegationTree?.context_overhead_ratio ?? 0) * 100}% overhead`,
      tone: 'success',
    },
    {
      label: 'Analyzer',
      agent: laneNodes[0]?.agentId ?? 'planner-agent',
      meta: laneNodes[0] ? `L${laneNodes[0].depth} · ${laneNodes[0].childCount} child lanes` : 'awaiting lane',
      tone: 'info',
    },
    {
      label: 'Active',
      agent: leadLane?.agentId ?? 'security-auditor',
      meta: leadLane ? `${leadLane.state} · P${leadLane.pressureScore}` : 'awaiting lead lane',
      tone: leadFocus?.severity === 'critical' ? 'danger' : 'warning',
    },
    {
      label: 'Storage Node',
      agent: leadFocus?.bucket === 'debris' ? 'artifact-spill' : 'workspace-tag',
      meta: `${store.metrics.orphaned_artifacts} orphaned · ${handoffModel.signalCounts.debris} debris`,
      tone: handoffModel.signalCounts.debris > 0 ? 'warning' : 'success',
    },
    {
      label: 'Compliance',
      agent: 'policy-verdict',
      meta: `${handoffModel.signalCounts.boundary} boundary · ${criticalEventCount} critical`,
      tone: criticalEventCount > 0 ? 'danger' : 'info',
    },
  ] as const;

  const quickActions = [
    {
      href: '/investigations',
      label: '进入调查工作台',
      icon: FileSearch,
    },
    {
      href: '/clusters',
      label: '回看 cluster 压力',
      icon: GitBranch,
    },
    {
      href: '/',
      label: '返回总览舞台',
      icon: Waypoints,
    },
  ];

  useEffect(() => {
    if (initialized.current) return;
    initialized.current = true;
    store.loadDemoData();
    if (handoffBackendEnabled) {
      store.fetchFromApi();
    }
  }, [store]);

  useEffect(() => {
    if (!handoffBackendEnabled || !store.useBackend) return;

    const connection = connectUnifiedSSE(
      '/api/events',
      () => {},
      (streamType, data) => {
        if (streamType === 'handoff_observability' || streamType === 'delegation_tree') {
          store.handleSseEvent(data);
        }
      },
      (error) => {
        console.warn('Handoff SSE connection error:', error);
      },
      5,
    );

    return () => connection.close();
  }, [store]);

  const handleSelectNode = useCallback((nodeId: string | null) => {
    store.selectNode(nodeId);
  }, [store]);

  return (
    <div className="handoff-shell handoff-page-shell">
      <div className="handoff-page-shell__inner">
        <ObservatoryHeader
          eyebrow="Delegation Observatory"
          title="把多 Agent 委托现场压成一张可以直接研判的扩散总谱"
          description="这一层承接首页定调与调查页归因，把 delegation chain、fanout pressure、boundary drift、artifact debris 放回同一张 handoff 指挥台。"
          activeSection="handoff"
          backHref="/"
          backLabel="返回主界面"
          stats={[
            `${criticalEventCount} critical signals`,
            `${store.metrics.total_spawned} spawned`,
            store.loading ? '同步 handoff 数据中…' : store.useBackend ? 'live backend' : 'demo fallback',
          ]}
        />

        <div className="handoff-stage-shell">
          <aside className="handoff-stage-feed">
            <PrismPanel size="lg" grid shimmer className="handoff-stage-panel handoff-stage-panel--feed">
              <div className="handoff-panel-head">
                <div>
                  <div className="stellar-section-label">Streaming</div>
                  <h3>Live Handoff Feed</h3>
                </div>
                <span>{feedEvents.length} signals</span>
              </div>

              <div className="handoff-feed-list">
                {feedEvents.map((event, index) => (
                  <div
                    key={event.id}
                    className="handoff-feed-card"
                    data-tone={getSeverityTone(event.severity)}
                  >
                    <div className="handoff-feed-card__topline">
                      <span>{event.bucket}</span>
                      <small>{formatEventRelativeMinutes(referenceTimeMs, eventTimestampMs(store.recentEvents[index] as unknown as Record<string, unknown>))}</small>
                    </div>
                    <strong>{event.title}</strong>
                    <p>{event.detail}</p>
                    <div className="handoff-feed-card__track" aria-hidden="true">
                      {sparklineBars(index * 7 + event.detail.length, 14).map((value, barIndex) => (
                        <span key={`${event.id}-${barIndex}`} style={{ height: `${value}%` }} />
                      ))}
                    </div>
                  </div>
                ))}
              </div>

              <div className="handoff-feed-footer">
                <span>{store.useBackend ? 'collector stream active' : 'demo session seeded'}</span>
                <strong>{store.metrics.total_completed} completed</strong>
              </div>
            </PrismPanel>
          </aside>

          <main className="handoff-stage-core">
            <PrismPanel size="lg" grid shimmer className="handoff-stage-panel handoff-stage-panel--chain">
              <div className="handoff-panel-head">
                <div>
                  <div className="stellar-section-label">Live</div>
                  <h3>Delegation Chain</h3>
                  <p>实时压缩责任、payload 与 trust 在多 agent handoff 链中的漂移方式。</p>
                </div>
                <div className="handoff-stage-filters">
                  <span>Trust Level</span>
                  <div className="handoff-stage-filters__chips">
                    <span data-tone="success">High</span>
                    <span data-tone="warning">Medium</span>
                    <span data-tone="danger">Low</span>
                  </div>
                </div>
              </div>

              <div className="handoff-chain-stage">
                <div className="handoff-chain-stage__grid" />
                <div className="handoff-chain-stage__beam" />
                {chainStages.map((stage, index) => (
                  <div
                    key={stage.label}
                    className="handoff-chain-node"
                    data-tone={stage.tone}
                    style={{ left: `${12 + index * 22}%` }}
                  >
                    <div className="handoff-chain-node__label">{stage.label}</div>
                    <div className="handoff-chain-node__pill">
                      <span>{stage.agent}</span>
                    </div>
                    <small>{stage.meta}</small>
                  </div>
                ))}
                <div className="handoff-chain-stage__caption">Payload in transit · drift accumulates at every handoff seam</div>
              </div>

              <div className="handoff-chain-card">
                <div className="handoff-chain-card__head">
                  <div>
                    <div className="stellar-section-label">Active Handoff</div>
                    <h4>{leadLane ? `${leadLane.agentId} 主导当前 handoff lane` : '等待主导 lane 形成'}</h4>
                  </div>
                  <span data-tone={leadFocus ? getSeverityTone(leadFocus.severity) : 'info'}>
                    {leadFocus?.severity === 'critical' ? 'High Risk' : leadFocus?.severity === 'warning' ? 'Watching' : 'Stable'}
                  </span>
                </div>

                <div className="handoff-chain-card__grid">
                  <div>
                    <span>Initiator</span>
                    <strong>{store.delegationTree?.agent_id ?? 'root-agent'}</strong>
                    <small>{store.metrics.total_spawned} delegated nodes</small>
                  </div>
                  <div>
                    <span>Current Holder</span>
                    <strong>{leadLane?.agentId ?? 'awaiting lead lane'}</strong>
                    <small>{leadLane ? `L${leadLane.depth} · ${leadLane.childCount} child lanes` : 'lane pending'}</small>
                  </div>
                  <div>
                    <span>Next Target</span>
                    <strong>{leadFocus?.bucket ?? 'verdict queue'}</strong>
                    <small>{leadFocus?.detail ?? 'collector continues to densify the chain'}</small>
                  </div>
                </div>

                <div className="handoff-chain-card__rail">
                  {['Initiated', 'Accepted', 'In Transit', 'Delivering', 'Completed'].map((label, index) => (
                    <div key={label}>
                      <span data-active={index < 3 ? 'true' : 'false'} />
                      <small>{label}</small>
                    </div>
                  ))}
                </div>
              </div>

              <div className="handoff-lens-grid">
                <div className="handoff-lens-card">
                  <span>Payload Anatomy</span>
                  <strong>{store.metrics.orphaned_artifacts} orphaned</strong>
                  <small>Artifacts that have slipped away from the main delegation tree.</small>
                </div>
                <div className="handoff-lens-card">
                  <span>Route & Segments</span>
                  <strong>{store.metrics.max_chain_depth} hops</strong>
                  <small>{store.metrics.active_count} active lanes remain in flight.</small>
                </div>
                <div className="handoff-lens-card">
                  <span>Evidence Continuity</span>
                  <strong>{formatPercent(store.metrics.workspace_tag_survival_rate)}</strong>
                  <small>workspace tags and context claims that survived the chain intact.</small>
                </div>
              </div>
            </PrismPanel>
          </main>

          <aside className="handoff-stage-verdict">
            <PrismPanel size="lg" grid shimmer className="handoff-stage-panel handoff-stage-panel--verdict">
              <div className="handoff-panel-head">
                <div>
                  <div className="stellar-section-label">Verdict</div>
                  <h3>Handoff Risk & Verdict</h3>
                </div>
                <span>{criticalEventCount > 0 ? 'High Risk' : 'Watching'}</span>
              </div>

              <div className="handoff-verdict-hero">
                <div>
                  <span>Risk Score</span>
                  <strong>{Math.min(99, 42 + criticalEventCount * 18)}</strong>
                  <small>/100</small>
                </div>
                <div>
                  <span>Verdict</span>
                  <strong>{criticalEventCount > 0 ? 'HIGH RISK' : 'WATCHING'}</strong>
                  <small>{leadFocus?.title ?? 'Requires denser evidence'}</small>
                </div>
              </div>

              <div className="handoff-verdict-section">
                <div className="handoff-panel-head">
                  <div>
                    <div className="stellar-section-label">Key Risk Drivers</div>
                    <h3>Key Risk Drivers</h3>
                  </div>
                </div>
                <div className="handoff-risk-list">
                  {verdictRows.map((row) => (
                    <div key={row.label} className="handoff-risk-row">
                      <div>
                        <strong>{row.label}</strong>
                        <small>{row.meta}</small>
                      </div>
                      <div className="handoff-risk-row__rail">
                        <span style={{ width: `${row.value}%` }} data-tone={row.tone} />
                      </div>
                      <em>{row.value}%</em>
                    </div>
                  ))}
                </div>
              </div>

              <div className="handoff-verdict-section">
                <div className="handoff-panel-head">
                  <div>
                    <div className="stellar-section-label">Quick Actions</div>
                    <h3>Quick Actions</h3>
                  </div>
                </div>
                <div className="handoff-quick-actions">
                  {quickActions.map((action) => {
                    const Icon = action.icon;
                    return (
                      <Link key={action.label} href={action.href} className="handoff-quick-action">
                        <Icon size={14} />
                        <span>{action.label}</span>
                      </Link>
                    );
                  })}
                  <button type="button" className="handoff-quick-action">
                    <Zap size={14} />
                    <span>压实当前 lane 信号</span>
                  </button>
                </div>
              </div>

              <div className="handoff-verdict-section">
                <div className="handoff-panel-head">
                  <div>
                    <div className="stellar-section-label">Lead Lane</div>
                    <h3>Lead Lane</h3>
                  </div>
                  <span>{leadLane ? `L${leadLane.depth}` : '--'}</span>
                </div>
                <div className="handoff-lane-stack">
                  {laneNodes.map((lane) => (
                    <div key={lane.nodeId} className="handoff-lane-card">
                      <div>
                        <strong>{lane.agentId}</strong>
                        <small>{lane.childCount} child lanes · {Math.round(lane.contextOverheadRatio * 100)}% overhead</small>
                      </div>
                      <span data-tone={lane.state === 'active' ? 'warning' : 'success'}>
                        {lane.state === 'active' ? 'active' : 'completed'}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            </PrismPanel>
          </aside>
        </div>

        {store.error ? (
          <div className="handoff-alert-banner">
            <ShieldAlert size={15} />
            {store.error}
          </div>
        ) : null}

        <PrismPanel size="lg" grid shimmer className="handoff-dashboard-panel">
          <div className="handoff-workbench-head">
            <div>
              <div className="stellar-section-label">Deep Drill</div>
              <h3>Handoff Evidence Workbench</h3>
              <p>继续沿委托树、边界卫星与事件流做深钻，直到把当前 handoff 的扩散叙事压实成可归因证据。</p>
            </div>
            <div className="handoff-workbench-actions">
              <Link href="/investigations">
                <PrismButton variant="secondary" size="sm">
                  <FileSearch size={13} />
                  调查工作台
                </PrismButton>
              </Link>
              <Link href="/clusters">
                <PrismButton variant="ghost" size="sm">
                  <ArrowRight size={13} />
                  Cluster 压力场
                </PrismButton>
              </Link>
            </div>
          </div>

          <HandoffDashboardShell
            delegationTree={store.delegationTree}
            metrics={store.metrics}
            recentEvents={store.recentEvents}
            selectedNodeId={store.selectedNodeId}
            dependencyEdges={store.dependencyEdges}
            onSelectNode={handleSelectNode}
          />
        </PrismPanel>
      </div>
    </div>
  );
}
