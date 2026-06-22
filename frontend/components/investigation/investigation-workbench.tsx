'use client';

import Link from 'next/link';
import type { ResponsibilityAlert } from '@/lib/types';
import type { InvestigationModel } from '@/lib/investigation';
import PrismButton from '@/components/ui/prism-button';
import PrismPanel from '@/components/ui/prism-panel';
import PrismStatCard from '@/components/ui/prism-stat-card';
import ForensicTopologyAtlas from '@/components/ui/forensic-topology-atlas';
import ObservatoryHeader from '@/components/system/observatory-header';
import { buildClusterHref } from '@/lib/cluster-route';
import { buildAgentHref } from '@/lib/agent-route';
import { getSeverityPalette } from '@/lib/severity-palette';
import { ArrowLeft, Clock3, FileSearch, ShieldAlert, Sparkles, GitBranch } from 'lucide-react';

function formatTimelineOffset(timestampNs: number, startNs: number) {
  const deltaMs = Math.max(0, Math.round((timestampNs - startNs) / 1_000_000));
  if (deltaMs < 1000) return `T+${deltaMs}ms`;
  return `T+${(deltaMs / 1000).toFixed(1)}s`;
}

function formatEvidenceMaterial(
  matchedFields: {
    canonical_path: string | null;
    command: string | null;
    host: string | null;
    port: number | null;
  },
) {
  if (matchedFields.canonical_path) return matchedFields.canonical_path;
  if (matchedFields.command) return matchedFields.command;
  if (matchedFields.host) return matchedFields.port ? `${matchedFields.host}:${matchedFields.port}` : matchedFields.host;
  return 'No materialized field';
}

export interface InvestigationWorkbenchProps {
  alert: ResponsibilityAlert | null;
  model: InvestigationModel | null;
  activeRunId: string | null;
  availableAlerts: ResponsibilityAlert[];
  onSelectAlert: (alertId: string) => void;
  evidenceLoading: boolean;
  evidenceError: string | null;
}

export default function InvestigationWorkbench({
  alert,
  model,
  activeRunId,
  availableAlerts,
  onSelectAlert,
  evidenceLoading,
  evidenceError,
}: InvestigationWorkbenchProps) {
  if (!alert || !model) {
    return (
      <div
        className="investigation-empty-shell"
        style={{
          minHeight: '100vh',
          background: 'var(--investigation-stage-bg)',
          color: 'var(--text-primary)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          padding: 32,
        }}
      >
        <PrismPanel className="investigation-hero-panel" size="hero" shimmer style={{ width: 'min(560px, 100%)' }}>
          <div style={{ fontSize: 12, letterSpacing: '0.18em', textTransform: 'uppercase', color: 'var(--text-quaternary)' }}>
            Investigation
          </div>
          <h1 style={{ margin: '12px 0 0', fontSize: 30, lineHeight: 1.04 }}>当前没有可打开的风险事件</h1>
          <p style={{ margin: '14px 0 0', color: 'var(--text-secondary)', lineHeight: 1.7, fontSize: 14 }}>
            首页或实时态势接入真实观测会话后，这里会承接风险事件调查。现在你也可以先回到首页，加载示例场景验证责任链、时间线和证据链。
          </p>
          <Link href="/" style={{ marginTop: 20, display: 'inline-flex', textDecoration: 'none' }}>
            <PrismButton variant="secondary" size="md">
              <ArrowLeft size={14} />
              返回首页
            </PrismButton>
          </Link>
        </PrismPanel>
      </div>
    );
  }

  const tone = getSeverityPalette(alert.severity).color;
  const timelineStartNs = model.timelineSteps[0]?.timestamp_ns ?? alert.timestamp_ns;
  const leadEvidenceMaterial = model.linkedEvidence[0]
    ? formatEvidenceMaterial(model.linkedEvidence[0].matched_fields)
    : '证据链已建立，等待进一步展开物化字段';
  const incidentSpatialCues = [
    {
      label: 'Authority Vector',
      value: `${model.authority} -> ${alert.decision}`,
      hint: '把声称的 authority 与最终决策拉成一条可以被回放的责任向量。',
    },
    {
      label: 'Evidence Arc',
      value: `${model.linkedEvidence.length} linked material`,
      hint: `首个可物化对象是 ${leadEvidenceMaterial}，后续线索沿时间轴持续展开。`,
    },
    {
      label: 'Fracture Window',
      value: `${model.timelineSteps.length} scene steps`,
      hint: '每一步都不是普通日志，而是可以推进镜头和归因判断的案卷节点。',
    },
  ];

  return (
    <div
      className="investigation-shell prism-grid-surface"
      style={{
        minHeight: '100vh',
        background: 'var(--investigation-stage-bg)',
        color: 'var(--text-primary)',
        position: 'relative',
        overflowX: 'hidden',
      }}
    >
      <div
        className="prism-ambient-orb"
        style={{
          top: 84,
          right: '14%',
          width: 300,
          height: 300,
          background: 'radial-gradient(circle, rgba(84, 190, 255, 0.14) 0%, rgba(84, 190, 255, 0.02) 68%, transparent 80%)',
        }}
      />
      <div
        className="prism-ambient-orb"
        style={{
          top: 280,
          left: '-4%',
          width: 240,
          height: 240,
          animationDelay: '-7s',
          background: 'radial-gradient(circle, rgba(140, 102, 255, 0.12) 0%, rgba(140, 102, 255, 0.02) 66%, transparent 80%)',
        }}
      />
      <div className="prism-scanline" style={{ inset: 'auto 0 22% 0' }} />

      <div style={{ padding: '18px 24px 0' }}>
        <ObservatoryHeader
          eyebrow="Case Theatre"
          title="把风险事件压成一张可以直接定责的法证舞台"
          description="调查页不再只是信息汇总，它把 causality chain、timeline cadence、evidence voltage 与 verdict 收束成同一条可辩护的判断路径。"
          activeSection="investigations"
          backHref="/"
          backLabel="返回首页"
          sticky
          stats={[
            activeRunId ? `观测会话 ${activeRunId.slice(0, 12)}...` : '示例场景',
            `${model.timelineSteps.length} Steps`,
            `${model.linkedEvidence.length} Evidence`,
          ]}
        />
      </div>

      <div className="investigation-page-shell" style={{ padding: 24 }}>
        <PrismPanel className="investigation-hero-panel" size="hero" grid shimmer>
            <div className="investigation-hero-main" style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 18, flexWrap: 'wrap' }}>
              <div style={{ maxWidth: 760 }}>
                <div
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 8,
                    padding: '7px 11px',
                    borderRadius: 999,
                    background: 'var(--surface-soft)',
                    border: `1px solid ${tone}30`,
                    color: tone,
                    fontSize: 11,
                    letterSpacing: '0.14em',
                    textTransform: 'uppercase',
                  }}
                >
                  <ShieldAlert size={14} />
                  {alert.severity} Case Signal
                </div>
              <h2 className="prism-display--hero" style={{ margin: '14px 0 0', fontSize: 'clamp(32px, 4vw, 50px)', lineHeight: 0.95, letterSpacing: '-0.05em' }}>
                  {alert.title}
                </h2>
                <p style={{ margin: '14px 0 0', fontSize: 14, lineHeight: 1.8, color: 'var(--text-secondary)', maxWidth: 760 }}>
                  {alert.summary} 这一层的目标不是让你阅读更多记录，而是让你更快确认谁触发了跨越、哪一步让责任发生了偏移、哪一份证据足以托住结论。
                </p>

                <div
                  className="investigation-case-strip"
                  style={{
                    display: 'grid',
                    gridTemplateColumns: 'repeat(3, minmax(0, 1fr))',
                    gap: 10,
                    marginTop: 16,
                  }}
                >
                  {[
                    {
                      label: 'Claimed Authority',
                      value: model.authority,
                      hint: '先确认谁声称自己有权做出这一步决策。',
                    },
                    {
                      label: 'Observed Action',
                      value: alert.decision,
                      hint: alert.action_hint || '把决策与实际行为绑定到同一个调查现场。',
                    },
                    {
                      label: 'First Materialized Evidence',
                      value: leadEvidenceMaterial,
                      hint: '结论必须被真实文件、命令或网络目标托住。',
                    },
                  ].map((item, index) => (
                    <div
                      key={item.label}
                      className="investigation-record-chip"
                      style={{
                        padding: '12px 12px 11px',
                        borderRadius: 18,
                        background: 'var(--surface-soft)',
                        border: `1px solid ${index === 0 ? `${tone}24` : 'var(--border-soft)'}`,
                      }}
                    >
                      <div style={{ fontSize: 10, letterSpacing: '0.12em', textTransform: 'uppercase', color: 'var(--text-quaternary)' }}>
                        {item.label}
                      </div>
                      <div style={{ marginTop: 7, fontSize: 13, lineHeight: 1.42, color: 'var(--text-primary)', fontWeight: 600 }}>
                        {item.value}
                      </div>
                      <div style={{ marginTop: 5, fontSize: 11, lineHeight: 1.56, color: 'var(--text-secondary)' }}>
                        {item.hint}
                      </div>
                    </div>
                  ))}
                </div>

                  <div
                    style={{
                      display: 'grid',
                      gridTemplateColumns: 'repeat(3, minmax(0, 1fr))',
                      gap: 10,
                      marginTop: 12,
                    }}
                  >
                    {incidentSpatialCues.map((item, index) => (
                      <div
                        key={item.label}
                        style={{
                          position: 'relative',
                          padding: '13px 13px 12px',
                          borderRadius: 18,
                          background: 'linear-gradient(180deg, rgba(8, 14, 28, 0.72) 0%, rgba(8, 14, 28, 0.32) 100%)',
                          border: `1px solid ${index === 0 ? `${tone}2e` : 'var(--border-soft)'}`,
                          boxShadow: '0 18px 38px rgba(0,0,0,0.12)',
                          overflow: 'hidden',
                        }}
                      >
                        <div
                          style={{
                            position: 'absolute',
                            inset: 0,
                            background: index === 0
                              ? 'radial-gradient(circle at 0% 0%, rgba(96, 165, 250, 0.12), transparent 46%)'
                              : index === 1
                                ? 'radial-gradient(circle at 100% 0%, rgba(168, 85, 247, 0.12), transparent 46%)'
                                : 'radial-gradient(circle at 50% 100%, rgba(251, 191, 36, 0.10), transparent 52%)',
                            pointerEvents: 'none',
                          }}
                        />
                        <div style={{ position: 'relative', zIndex: 1 }}>
                          <div style={{ fontSize: 10, letterSpacing: '0.14em', textTransform: 'uppercase', color: 'var(--text-quaternary)' }}>
                            {item.label}
                          </div>
                          <div className="prism-display" style={{ marginTop: 8, fontSize: 15, lineHeight: 1.18, color: 'var(--text-primary)' }}>
                            {item.value}
                          </div>
                          <div style={{ marginTop: 7, fontSize: 11, lineHeight: 1.62, color: 'var(--text-secondary)' }}>
                            {item.hint}
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
              </div>

              <ForensicTopologyAtlas
                model={model}
                tone={tone}
                leadEvidenceMaterial={leadEvidenceMaterial}
              />

              <div className="investigation-hero-stats" style={{ display: 'grid', gap: 8, minWidth: 240 }}>
                {[
                  ['决策', alert.decision],
                  ['可信度', alert.confidence.toFixed(2)],
                  ['边界穿越', `${model.boundaryCrossingCount}`],
                  ['证据引用', `${model.evidenceRefs.length}`],
                ].map(([label, value]) => (
                  <PrismStatCard
                    key={label}
                    label={label}
                    value={value}
                    toneColor={label === '边界穿越' ? tone : undefined}
                  />
                ))}
              </div>
            </div>

            <div
              className="investigation-hero-followups"
              style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(3, minmax(0, 1fr))',
                gap: 12,
                marginTop: 18,
              }}
            >
              <PrismPanel className="investigation-note-panel" size="md" shimmer>
                <div style={{ fontSize: 10, letterSpacing: '0.14em', color: 'var(--text-quaternary)', textTransform: 'uppercase' }}>
                  Case Narrative
                </div>
                <div style={{ marginTop: 8, fontSize: 13, lineHeight: 1.72, color: 'var(--text-secondary)' }}>
                  {alert.reasoning || '当前事件已经具备完整的委派、执行与证据链，可直接进入归因调查。'}
                </div>
              </PrismPanel>

              <PrismPanel className="investigation-note-panel" size="md" shimmer>
                <div style={{ fontSize: 10, letterSpacing: '0.14em', color: 'var(--text-quaternary)', textTransform: 'uppercase' }}>
                  Next Action
                </div>
                <div style={{ marginTop: 8, fontSize: 13, lineHeight: 1.72, color: 'var(--text-secondary)' }}>
                  {alert.action_hint || '优先核验主体身份绑定，再检查委派路径与边界策略是否被错误放宽。'}
                </div>
              </PrismPanel>

              <PrismPanel className="investigation-note-panel" size="md" shimmer>
                <div style={{ fontSize: 10, letterSpacing: '0.14em', color: 'var(--text-quaternary)', textTransform: 'uppercase' }}>
                  Narrative Bridge
                </div>
                <div style={{ marginTop: 8, fontSize: 13, lineHeight: 1.72, color: 'var(--text-secondary)' }}>
                  调查页负责把单个风险事件定责；如果你需要继续确认它是否扩散成委托拓扑级问题，就进入 handoff 工作台看 fanout、boundary 与 orphaned artifacts。
                </div>
                <div style={{ marginTop: 12, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                  <Link href="/handoff" style={{ textDecoration: 'none' }}>
                    <PrismButton variant="secondary" size="sm">
                      <GitBranch size={13} />
                      查看 Handoff
                    </PrismButton>
                  </Link>
                  <Link href="/" aria-label="返回主舞台" style={{ textDecoration: 'none' }}>
                    <PrismButton variant="ghost" size="sm">
                      <ArrowLeft size={13} />
                      返回主舞台
                    </PrismButton>
                  </Link>
                </div>
              </PrismPanel>
            </div>
        </PrismPanel>

        <div
          className="investigation-grid"
          style={{
            display: 'grid',
            gridTemplateColumns: '300px minmax(0, 1fr) 340px',
            gap: 18,
            marginTop: 18,
            alignItems: 'start',
          }}
        >
            <aside
              className="investigation-sticky-column"
              style={{
                display: 'grid',
                gap: 14,
                position: 'sticky',
                top: 92,
              }}
            >
              <PrismPanel className="investigation-section-panel" size="lg" grid shimmer>
                <div style={{ fontSize: 11, letterSpacing: '0.16em', color: 'var(--text-quaternary)', textTransform: 'uppercase' }}>
                  Event Summary
                </div>
                <div style={{ display: 'grid', gap: 10, marginTop: 14 }}>
                  <div>
                    <div style={{ fontSize: 10, color: 'var(--text-quaternary)', textTransform: 'uppercase', letterSpacing: '0.12em' }}>Primary Agent</div>
                    <div style={{ marginTop: 4, fontSize: 14, fontWeight: 600 }}>
                      {model.primaryAgentId ? (
                        <Link href={buildAgentHref(model.primaryAgentId)} style={{ color: 'inherit', textDecoration: 'none' }}>
                          {model.primaryAgentId}
                        </Link>
                      ) : (
                        'N/A'
                      )}
                    </div>
                  </div>
                  <div>
                    <div style={{ fontSize: 10, color: 'var(--text-quaternary)', textTransform: 'uppercase', letterSpacing: '0.12em' }}>Authority</div>
                    <div style={{ marginTop: 4, fontSize: 14, fontWeight: 600 }}>{model.authority}</div>
                  </div>
                  <div>
                    <div style={{ fontSize: 10, color: 'var(--text-quaternary)', textTransform: 'uppercase', letterSpacing: '0.12em' }}>Timeline Window</div>
                    <div style={{ marginTop: 4, fontSize: 14, fontWeight: 600 }}>{model.timelineDurationMs}ms</div>
                  </div>
                  <div>
                    <div style={{ fontSize: 10, color: 'var(--text-quaternary)', textTransform: 'uppercase', letterSpacing: '0.12em' }}>Participants</div>
                    <div style={{ marginTop: 6, display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                      {model.involvedAgentIds.map((agentId) => (
                        <Link
                          key={agentId}
                          href={buildAgentHref(agentId)}
                          style={{
                            textDecoration: 'none',
                            padding: '6px 9px',
                            borderRadius: 999,
                            fontSize: 11,
                            background: 'var(--surface-soft)',
                            border: '1px solid var(--border-soft)',
                            color: 'var(--text-secondary)',
                          }}
                        >
                          {agentId}
                        </Link>
                      ))}
                    </div>
                  </div>
                </div>
              </PrismPanel>

              <PrismPanel className="investigation-section-panel" size="lg" grid shimmer>
                <div style={{ fontSize: 11, letterSpacing: '0.16em', color: 'var(--text-quaternary)', textTransform: 'uppercase' }}>
                  More Risk Events
                </div>
                <div className="investigation-event-list" style={{ display: 'grid', gap: 8, marginTop: 14 }}>
                  {availableAlerts.slice(0, 5).map((item) => {
                    const selected = item.alert_id === alert.alert_id;
                    return (
                      <PrismButton
                        key={item.alert_id}
                        onClick={() => onSelectAlert(item.alert_id)}
                        style={{
                          textAlign: 'left',
                          padding: '10px 11px',
                          borderRadius: 16,
                          cursor: 'pointer',
                          background: selected ? 'rgba(59, 130, 246, 0.16)' : 'var(--surface-soft)',
                          border: selected ? '1px solid rgba(96, 165, 250, 0.28)' : '1px solid var(--border-soft)',
                          color: 'var(--text-primary)',
                          justifyContent: 'flex-start',
                          minHeight: 'unset',
                        }}
                        variant="ghost"
                      >
                        <div style={{ fontSize: 12, fontWeight: 600, lineHeight: 1.4 }}>{item.title}</div>
                        <div style={{ marginTop: 4, fontSize: 11, lineHeight: 1.5, color: 'var(--text-secondary)' }}>{item.summary}</div>
                      </PrismButton>
                    );
                  })}
                </div>
              </PrismPanel>
            </aside>

            <main style={{ display: 'grid', gap: 18 }}>
              <PrismPanel className="investigation-section-panel" size="lg" grid shimmer>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 14 }}>
                  <Sparkles size={16} color="rgba(147, 197, 253, 0.92)" />
                  <div style={{ fontSize: 11, letterSpacing: '0.16em', color: 'var(--text-quaternary)', textTransform: 'uppercase' }}>
                    Responsibility Chain
                  </div>
                </div>
                <div style={{ display: 'grid', gap: 10 }}>
                  {model.pathEdges.map((edge) => (
                    <div
                      key={edge.edge_id}
                      style={{
                        padding: '13px 14px',
                        borderRadius: 18,
                        background: 'var(--surface-soft)',
                        border: '1px solid var(--border-soft)',
                      }}
                    >
                      <div style={{ fontSize: 10, letterSpacing: '0.12em', color: 'var(--text-quaternary)', textTransform: 'uppercase' }}>
                        {edge.edge_type}
                      </div>
                      <div style={{ marginTop: 6, fontSize: 13, lineHeight: 1.6, color: 'var(--text-primary)' }}>
                        {`${edge.source} -> ${edge.target}`}
                      </div>
                      <div style={{ marginTop: 5, fontSize: 11, color: 'var(--text-tertiary)' }}>
                        Confidence {edge.confidence.toFixed(2)} · Evidence {(edge.evidence_refs ?? []).length}
                      </div>
                    </div>
                  ))}
                </div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 14 }}>
                  {model.pathNodes.map((node) => (
                    <span
                      key={node.nodeId}
                      style={{
                        padding: '7px 10px',
                        borderRadius: 999,
                        fontSize: 11,
                        color: 'var(--text-secondary)',
                        background: 'var(--surface-soft)',
                        border: '1px solid var(--border-soft)',
                      }}
                    >
                      {node.label}
                    </span>
                  ))}
                </div>
                <div
                  className="investigation-chain-stats"
                  style={{
                    marginTop: 14,
                    display: 'grid',
                    gridTemplateColumns: 'repeat(3, minmax(0, 1fr))',
                    gap: 8,
                  }}
                >
                  {[
                    ['链路边', `${model.pathEdges.length}`],
                    ['涉及节点', `${model.pathNodes.length}`],
                    ['参与主体', `${model.involvedAgentIds.length}`],
                  ].map(([label, value]) => (
                    <PrismStatCard
                      key={label}
                      label={label}
                      value={value}
                    />
                  ))}
                </div>
                {model.primaryAgentId ? (
                  <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginTop: 14 }}>
                    <Link href={buildClusterHref(model.primaryAgentId)} style={{ textDecoration: 'none' }}>
                      <PrismButton variant="secondary" size="sm">查看所属 Cluster</PrismButton>
                    </Link>
                    <Link href={buildAgentHref(model.primaryAgentId)} style={{ textDecoration: 'none' }}>
                      <PrismButton variant="ghost" size="sm">查看 Agent Cockpit</PrismButton>
                    </Link>
                  </div>
                ) : null}
              </PrismPanel>

              <PrismPanel className="investigation-section-panel" size="lg" grid shimmer>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 14 }}>
                  <Clock3 size={16} color="rgba(147, 197, 253, 0.92)" />
                  <div style={{ fontSize: 11, letterSpacing: '0.16em', color: 'var(--text-quaternary)', textTransform: 'uppercase' }}>
                    Investigation Timeline · {model.timelineDurationMs}ms
                  </div>
                </div>
                <div style={{ display: 'grid', gap: 14, borderLeft: '2px solid rgba(96,165,250,0.18)', paddingLeft: 18 }}>
                  {model.timelineSteps.map((step, index) => (
                    <div key={`${step.timestamp_ns}-${index}`} style={{ position: 'relative' }}>
                      <div
                        style={{
                          position: 'absolute',
                          left: -24,
                          top: 7,
                          width: 10,
                          height: 10,
                          borderRadius: '50%',
                          background: step.operation === 'FileRead' ? '#60a5fa' : step.operation === 'Delegation' ? '#a78bfa' : '#fb7185',
                          border: '2px solid rgba(7,11,20,0.95)',
                        }}
                      />
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                        <div style={{ fontSize: 11, color: 'var(--text-quaternary)', fontFamily: 'monospace' }}>
                          {formatTimelineOffset(step.timestamp_ns, timelineStartNs)}
                        </div>
                        <span
                          style={{
                            padding: '3px 7px',
                            borderRadius: 999,
                            fontSize: 10,
                            background: 'var(--surface-soft)',
                            border: '1px solid var(--border-soft)',
                            color: 'var(--text-secondary)',
                          }}
                        >
                          {step.event_type}
                        </span>
                      </div>
                      <div style={{ marginTop: 6, fontSize: 14, lineHeight: 1.72, color: 'var(--text-primary)' }}>{step.description}</div>
                      <div style={{ marginTop: 4, display: 'flex', gap: 10, flexWrap: 'wrap', color: 'var(--text-tertiary)', fontSize: 11 }}>
                        {step.resource ? <span>{step.resource}</span> : null}
                        {step.evidence_ref ? <span>Evidence {step.evidence_ref}</span> : null}
                        {step.agent_id ? <span>{step.agent_id}</span> : null}
                      </div>
                    </div>
                  ))}
                </div>
              </PrismPanel>
            </main>

            <aside className="investigation-sticky-column" style={{ display: 'grid', gap: 18, position: 'sticky', top: 92 }}>
              <PrismPanel className="investigation-section-panel" size="lg" grid shimmer>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <FileSearch size={16} color="rgba(147, 197, 253, 0.92)" />
                  <div style={{ fontSize: 11, letterSpacing: '0.16em', color: 'var(--text-quaternary)', textTransform: 'uppercase' }}>
                    Evidence Chain
                  </div>
                </div>
                <div style={{ marginTop: 8, fontSize: 11, lineHeight: 1.6, color: 'var(--text-tertiary)' }}>
                  直接呈现当前事件命中的原始物料，优先把路径、命令与网络目标收拢到同一个证据侧栏。
                </div>
                {evidenceLoading ? (
                  <div style={{ marginTop: 14, fontSize: 12, color: 'var(--text-tertiary)' }}>正在加载证据链...</div>
                ) : evidenceError ? (
                  <div style={{ marginTop: 14, fontSize: 12, color: '#fca5a5' }}>{evidenceError}</div>
                ) : model.linkedEvidence.length > 0 ? (
                  <div className="prism-premium-scroll investigation-evidence-list" style={{ display: 'grid', gap: 8, marginTop: 14, maxHeight: '60vh', overflowY: 'auto', paddingRight: 4 }}>
                    {model.linkedEvidence.map((item) => (
                      <div
                        key={item.evidence_id}
                        style={{
                          padding: '11px 12px',
                          borderRadius: 16,
                          background: 'var(--surface-soft)',
                          border: '1px solid var(--border-soft)',
                        }}
                      >
                        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 10 }}>
                          <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-primary)' }}>{`${alert.alert_id} / ${item.evidence_id}`}</div>
                          <div
                            style={{
                              padding: '4px 7px',
                              borderRadius: 999,
                              fontSize: 10,
                              background: 'var(--surface-strong)',
                              border: '1px solid var(--border-soft)',
                              color: 'var(--text-secondary)',
                              whiteSpace: 'nowrap',
                            }}
                          >
                            {item.match_level}
                          </div>
                        </div>
                        <div style={{ marginTop: 6, fontSize: 12, lineHeight: 1.6, color: 'var(--text-primary)', wordBreak: 'break-word' }}>
                          {formatEvidenceMaterial(item.matched_fields)}
                        </div>
                        <div style={{ marginTop: 5, fontSize: 11, lineHeight: 1.55, color: 'var(--text-tertiary)' }}>
                          Confidence {item.confidence.toFixed(2)} · {item.derivation_mode}
                        </div>
                        <div style={{ marginTop: 5, fontSize: 11, lineHeight: 1.55, color: 'var(--text-tertiary)', wordBreak: 'break-word' }}>
                          {item.matched_fields.command || item.intent_id}
                        </div>
                        <div style={{ marginTop: 5, display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                          {[item.action_id, item.intent_id].map((value) => (
                            <span
                              key={value}
                              style={{
                                padding: '4px 7px',
                                borderRadius: 999,
                                fontSize: 10,
                                background: 'var(--surface-soft)',
                                border: '1px solid var(--border-soft)',
                                color: 'var(--text-tertiary)',
                              }}
                            >
                              {value}
                            </span>
                          ))}
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div style={{ marginTop: 14, fontSize: 12, color: 'var(--text-tertiary)', lineHeight: 1.6 }}>
                    当前会话还没有单独返回证据详情，先使用风险事件中内联的证据引用完成首轮调查。
                  </div>
                )}
              </PrismPanel>

              <PrismPanel className="investigation-section-panel" size="lg" grid shimmer>
                <div style={{ fontSize: 11, letterSpacing: '0.16em', color: 'var(--text-quaternary)', textTransform: 'uppercase' }}>
                  Rule & Verdict
                </div>
                <div style={{ display: 'grid', gap: 10, marginTop: 14 }}>
                  <div className="investigation-rule-grid" style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 10 }}>
                    <PrismStatCard label="Rules" value={model.ruleCount} />
                    <PrismStatCard label="Patterns" value={model.patternCount} />
                  </div>

                  {alert.engine.rule_verdicts.map((rule) => (
                    <div
                      key={rule.rule_id}
                      style={{
                        padding: '11px 12px',
                        borderRadius: 16,
                        background: 'var(--surface-soft)',
                        border: '1px solid var(--border-soft)',
                      }}
                    >
                      <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-primary)' }}>{rule.rule_id}</div>
                      <div style={{ marginTop: 4, fontSize: 11, lineHeight: 1.55, color: 'var(--text-secondary)' }}>
                        {rule.description}
                      </div>
                      <div style={{ marginTop: 4, fontSize: 11, lineHeight: 1.55, color: 'var(--text-tertiary)' }}>
                        {rule.matched_resource}
                      </div>
                    </div>
                  ))}

                  {alert.engine.pattern_verdicts.map((pattern) => (
                    <div
                      key={pattern.template_id}
                      style={{
                        padding: '11px 12px',
                        borderRadius: 16,
                        background: 'var(--surface-soft)',
                        border: '1px solid var(--border-soft)',
                      }}
                    >
                      <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-primary)' }}>{pattern.template_id}</div>
                      <div style={{ marginTop: 4, fontSize: 11, lineHeight: 1.55, color: 'var(--text-secondary)' }}>
                        Match confidence {pattern.match_confidence.toFixed(2)}
                      </div>
                    </div>
                  ))}

                  <div style={{ padding: '11px 12px', borderRadius: 16, background: 'var(--surface-soft)', border: '1px solid var(--border-soft)' }}>
                    <div style={{ fontSize: 10, color: 'var(--text-quaternary)', letterSpacing: '0.12em', textTransform: 'uppercase' }}>行动建议</div>
                    <div style={{ marginTop: 4, fontSize: 12, lineHeight: 1.7, color: 'var(--text-secondary)' }}>
                      {alert.action_hint || alert.reasoning}
                    </div>
                  </div>
                </div>
              </PrismPanel>
            </aside>
          </div>
      </div>

    </div>
  );
}
