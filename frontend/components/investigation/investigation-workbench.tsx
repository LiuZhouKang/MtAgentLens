'use client';

import Link from 'next/link';
import type { ResponsibilityAlert } from '@/lib/types';
import type { InvestigationModel } from '@/lib/investigation';
import PrismButton from '@/components/ui/prism-button';
import PrismPanel from '@/components/ui/prism-panel';
import PrismStatCard from '@/components/ui/prism-stat-card';
import ForensicTopologyAtlas from '@/components/ui/forensic-topology-atlas';
import ObservatoryHeader from '@/components/system/observatory-header';
import ObservatoryPageHero from '@/components/system/observatory-page-hero';
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
          <div className="investigation-empty-shell__eyebrow">
            Investigation
          </div>
          <h1 className="investigation-empty-shell__title">当前没有可打开的风险事件</h1>
          <p className="investigation-empty-shell__copy">
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
    >
      <div className="prism-scanline investigation-scanline" />

      <div className="investigation-header-shell">
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

      <div className="px-4 pb-4 sm:px-6 lg:px-8">
        <ObservatoryPageHero
          eyebrow="Case Command Deck"
          title={`先定责 ${alert.title}，再展开完整法证舞台`}
          description="参考 OpenSearch Security Analytics 的 finding workflow 与 SigNoz 的逐层下钻方式，把案件结论、关键证据、主要主体与下一步动作压缩成一条更强的调查入口。"
          statusLabel={alert.severity === 'Critical' ? 'critical case live' : 'forensic review active'}
          chips={[
            `${model.timelineSteps.length} timeline steps`,
            `${model.evidenceRefs.length} evidence refs`,
            `${model.boundaryCrossingCount} boundary crossings`,
          ]}
          stats={[
            {
              label: 'Decision',
              value: alert.decision,
              hint: '先确认结论，再回看谁把边界推开',
            },
            {
              label: 'Primary Agent',
              value: model.primaryAgentId ?? 'awaiting anchor',
              hint: '当前案件最关键的责任主体',
            },
            {
              label: 'Confidence',
              value: alert.confidence.toFixed(2),
              hint: '证据链对结论的支撑强度',
            },
          ]}
          actions={[
            {
              label: '查看所属 Cluster',
              href: buildClusterHref(model.parentClusterId),
              hint: '把案件放回集群扩散压力场',
            },
            {
              label: '查看 Agent Cockpit',
              href: model.primaryAgentId ? buildAgentHref(model.primaryAgentId) : '/agents',
              hint: '继续下钻主体画像与资源暴露面',
            },
          ]}
        />
      </div>

      <div className="investigation-page-shell">
        <PrismPanel className="investigation-hero-panel" size="hero" grid shimmer>
            <div className="investigation-hero-main">
              <div className="investigation-hero-lead">
                <div className="investigation-signal-pill" style={{ ['--investigation-tone' as any]: tone, color: tone }}>
                  <ShieldAlert size={14} />
                  {alert.severity} Case Signal
                </div>
              <h2 className="investigation-hero-title prism-display--hero">
                  {alert.title}
                </h2>
                <p className="investigation-hero-copy">
                  {alert.summary} 这一层的目标不是让你阅读更多记录，而是让你更快确认谁触发了跨越、哪一步让责任发生了偏移、哪一份证据足以托住结论。
                </p>

                <div className="investigation-case-strip">
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
                      data-tone={index === 0 ? 'accent' : 'neutral'}
                      style={index === 0 ? { ['--investigation-tone' as any]: tone } : undefined}
                    >
                      <div className="investigation-record-chip__label">
                        {item.label}
                      </div>
                      <div className="investigation-record-chip__value">
                        {item.value}
                      </div>
                      <div className="investigation-record-chip__hint">
                        {item.hint}
                      </div>
                    </div>
                  ))}
                </div>

                  <div className="investigation-spatial-grid">
                    {incidentSpatialCues.map((item, index) => (
                      <div
                        key={item.label}
                        className="investigation-spatial-card"
                        data-tone={index === 0 ? 'accent' : index === 1 ? 'violet' : 'amber'}
                        style={index === 0 ? { ['--investigation-tone' as any]: tone } : undefined}
                      >
                        <div className="investigation-spatial-card__glow" />
                        <div className="investigation-spatial-card__content">
                          <div className="investigation-spatial-card__label">
                            {item.label}
                          </div>
                          <div className="investigation-spatial-card__value prism-display">
                            {item.value}
                          </div>
                          <div className="investigation-spatial-card__hint">
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

              <div className="investigation-hero-stats">
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

            <div className="investigation-hero-followups">
              <div className="investigation-inline-note">
                <div className="investigation-inline-note__label">Case Narrative</div>
                <div className="investigation-inline-note__copy">
                  {alert.reasoning || '当前事件已经具备完整的委派、执行与证据链，可直接进入归因调查。'}
                </div>
              </div>

              <div className="investigation-inline-note">
                <div className="investigation-inline-note__label">Next Action</div>
                <div className="investigation-inline-note__copy">
                  {alert.action_hint || '优先核验主体身份绑定，再检查委派路径与边界策略是否被错误放宽。'}
                </div>
              </div>

              <div className="investigation-inline-note">
                <div className="investigation-inline-note__label">Narrative Bridge</div>
                <div className="investigation-inline-note__copy">
                  调查页负责把单个风险事件定责；如果你需要继续确认它是否扩散成委托拓扑级问题，就进入 handoff 工作台看 fanout、boundary 与 orphaned artifacts。
                </div>
                  <div className="investigation-bridge-actions">
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
              </div>
            </div>
        </PrismPanel>

        <div className="investigation-grid">
            <aside className="investigation-sticky-column">
              <PrismPanel className="investigation-section-panel" size="lg" grid shimmer>
                <div className="investigation-section-label">
                  Event Summary
                </div>
                <div className="investigation-summary-grid">
                  <div>
                    <div className="investigation-summary-label">Primary Agent</div>
                    <div className="investigation-summary-value">
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
                    <div className="investigation-summary-label">Authority</div>
                    <div className="investigation-summary-value">{model.authority}</div>
                  </div>
                  <div>
                    <div className="investigation-summary-label">Timeline Window</div>
                    <div className="investigation-summary-value">{model.timelineDurationMs}ms</div>
                  </div>
                  <div>
                    <div className="investigation-summary-label">Participants</div>
                    <div className="investigation-chip-stack">
                      {model.involvedAgentIds.map((agentId) => (
                        <Link
                          key={agentId}
                          href={buildAgentHref(agentId)}
                          className="investigation-chip-link"
                        >
                          {agentId}
                        </Link>
                      ))}
                    </div>
                  </div>
                </div>
              </PrismPanel>

              <PrismPanel className="investigation-section-panel" size="lg" grid shimmer>
                <div className="investigation-section-label">
                  More Risk Events
                </div>
                <div className="investigation-event-list investigation-event-list--stack">
                  {availableAlerts.slice(0, 5).map((item) => {
                    const selected = item.alert_id === alert.alert_id;
                    return (
                      <PrismButton
                        key={item.alert_id}
                        onClick={() => onSelectAlert(item.alert_id)}
                        className="investigation-event-button"
                        data-selected={selected}
                        variant="ghost"
                      >
                        <div className="investigation-event-button__title">{item.title}</div>
                        <div className="investigation-event-button__copy">{item.summary}</div>
                      </PrismButton>
                    );
                  })}
                </div>
              </PrismPanel>
            </aside>

            <main className="investigation-main-column">
              <PrismPanel className="investigation-section-panel" size="lg" grid shimmer>
                <div className="investigation-section-kicker">
                  <Sparkles size={16} color="rgba(147, 197, 253, 0.92)" />
                  <div className="investigation-section-label">
                    Responsibility Chain
                  </div>
                </div>
                <div className="investigation-chain-list">
                  {model.pathEdges.map((edge) => (
                    <div
                      key={edge.edge_id}
                      className="investigation-chain-card"
                    >
                      <div className="investigation-chain-card__label">
                        {edge.edge_type}
                      </div>
                      <div className="investigation-chain-card__title">
                        {`${edge.source} -> ${edge.target}`}
                      </div>
                      <div className="investigation-chain-card__meta">
                        Confidence {edge.confidence.toFixed(2)} · Evidence {(edge.evidence_refs ?? []).length}
                      </div>
                    </div>
                  ))}
                </div>
                <div className="investigation-node-chip-row">
                  {model.pathNodes.map((node) => (
                    <span
                      key={node.nodeId}
                      className="investigation-node-chip"
                    >
                      {node.label}
                    </span>
                  ))}
                </div>
                <div className="investigation-chain-stats">
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
                  <div className="investigation-action-row">
                    <Link href={buildClusterHref(model.parentClusterId)} style={{ textDecoration: 'none' }}>
                      <PrismButton variant="secondary" size="sm">查看所属 Cluster</PrismButton>
                    </Link>
                    <Link href={buildAgentHref(model.primaryAgentId)} style={{ textDecoration: 'none' }}>
                      <PrismButton variant="ghost" size="sm">查看 Agent Cockpit</PrismButton>
                    </Link>
                  </div>
                ) : null}
              </PrismPanel>

              <PrismPanel className="investigation-section-panel" size="lg" grid shimmer>
                <div className="investigation-section-kicker">
                  <Clock3 size={16} color="rgba(147, 197, 253, 0.92)" />
                  <div className="investigation-section-label">
                    Investigation Timeline · {model.timelineDurationMs}ms
                  </div>
                </div>
                <div className="investigation-timeline">
                  {model.timelineSteps.map((step, index) => (
                    <div key={`${step.timestamp_ns}-${index}`} className="investigation-timeline__step" data-tone={step.operation === 'FileRead' ? 'info' : step.operation === 'Delegation' ? 'violet' : 'danger'}>
                      <div className="investigation-timeline__dot" />
                      <div className="investigation-timeline__head">
                        <div className="investigation-timeline__time">
                          {formatTimelineOffset(step.timestamp_ns, timelineStartNs)}
                        </div>
                        <span className="investigation-timeline__tag">
                          {step.event_type}
                        </span>
                      </div>
                      <div className="investigation-timeline__copy">{step.description}</div>
                      <div className="investigation-timeline__meta">
                        {step.resource ? <span>{step.resource}</span> : null}
                        {step.evidence_ref ? <span>Evidence {step.evidence_ref}</span> : null}
                        {step.agent_id ? <span>{step.agent_id}</span> : null}
                      </div>
                    </div>
                  ))}
                </div>
              </PrismPanel>
            </main>

            <aside className="investigation-sticky-column investigation-sticky-column--right">
              <PrismPanel className="investigation-section-panel" size="lg" grid shimmer>
                <div className="investigation-section-kicker">
                  <FileSearch size={16} color="rgba(147, 197, 253, 0.92)" />
                  <div className="investigation-section-label">
                    Evidence Chain
                  </div>
                </div>
                <div className="investigation-section-copy">
                  直接呈现当前事件命中的原始物料，优先把路径、命令与网络目标收拢到同一个证据侧栏。
                </div>
                {evidenceLoading ? (
                  <div className="investigation-section-state">正在加载证据链...</div>
                ) : evidenceError ? (
                  <div className="investigation-section-state investigation-section-state--error">{evidenceError}</div>
                ) : model.linkedEvidence.length > 0 ? (
                  <div className="prism-premium-scroll investigation-evidence-list investigation-evidence-list--scroll">
                    {model.linkedEvidence.map((item) => (
                      <article
                        key={item.evidence_id}
                        className="investigation-evidence-card"
                      >
                        <div className="investigation-evidence-card__head">
                          <div className="investigation-evidence-card__title">{`${alert.alert_id} / ${item.evidence_id}`}</div>
                          <div className="investigation-evidence-card__badge">
                            {item.match_level}
                          </div>
                        </div>
                        <div className="investigation-evidence-card__copy">
                          {formatEvidenceMaterial(item.matched_fields)}
                        </div>
                        <div className="investigation-evidence-card__meta">
                          Confidence {item.confidence.toFixed(2)} · {item.derivation_mode}
                        </div>
                        <div className="investigation-evidence-card__meta">
                          {item.matched_fields.command || item.intent_id}
                        </div>
                        <div className="investigation-chip-stack">
                          {[item.action_id, item.intent_id].map((value) => (
                            <span key={value} className="investigation-tag-chip">
                              {value}
                            </span>
                          ))}
                        </div>
                      </article>
                    ))}
                  </div>
                ) : (
                  <div className="investigation-section-state">
                    当前会话还没有单独返回证据详情，先使用风险事件中内联的证据引用完成首轮调查。
                  </div>
                )}
              </PrismPanel>

              <PrismPanel className="investigation-section-panel" size="lg" grid shimmer>
                <div className="investigation-section-label">
                  Rule & Verdict
                </div>
                <div className="investigation-verdict-stack">
                  <div className="investigation-verdict-grid">
                    <PrismStatCard label="Rules" value={model.ruleCount} />
                    <PrismStatCard label="Patterns" value={model.patternCount} />
                  </div>

                  {alert.engine.rule_verdicts.map((rule) => (
                    <article
                      key={rule.rule_id}
                      className="investigation-verdict-card"
                    >
                      <div className="investigation-verdict-card__title">{rule.rule_id}</div>
                      <div className="investigation-verdict-card__copy">
                        {rule.description}
                      </div>
                      <div className="investigation-verdict-card__meta">
                        {rule.matched_resource}
                      </div>
                    </article>
                  ))}

                  {alert.engine.pattern_verdicts.map((pattern) => (
                    <article
                      key={pattern.template_id}
                      className="investigation-verdict-card"
                    >
                      <div className="investigation-verdict-card__title">{pattern.template_id}</div>
                      <div className="investigation-verdict-card__copy">
                        Match confidence {pattern.match_confidence.toFixed(2)}
                      </div>
                    </article>
                  ))}

                  <div className="investigation-verdict-card">
                    <div className="investigation-verdict-card__label">行动建议</div>
                    <div className="investigation-verdict-card__copy">
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
