'use client';

import { useRef } from 'react';
import Link from 'next/link';
import { useGSAP } from '@gsap/react';
import gsap from 'gsap';
import { ArrowRight, Clock, FileSearch, GitBranch, ShieldAlert, TerminalSquare } from 'lucide-react';
import { SectionPanel } from '@/components/ui/linear/section-panel';
import { StatusBadge, type StatusTone } from '@/components/ui/linear/status-badge';
import VirtualList from '@/components/ui/virtual-list';
import type { ResponsibilityAlert } from '@/lib/types';
import type { InvestigationModel } from '@/lib/investigation';

const T = { xs: 9.5, sm: 10.5, md: 12, lg: 14, xl: 16 };
const S = { xs: 2, sm: 4, md: 8, lg: 12, xl: 16 };

function fmtMs(ms: number) { return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`; }
function sevTone(s: string): StatusTone { if (s === 'Critical') return 'danger'; if (s === 'Warning') return 'warning'; return 'info'; }
function decisionTone(d: string): StatusTone { if (d === 'Unsafe') return 'danger'; if (d === 'Uncertain') return 'warning'; return 'success'; }
function statusLabel(d: string) { if (d === 'Unsafe') return '需处置'; if (d === 'Uncertain') return '待复核'; return '已受控'; }

const sevColor: Record<string, string> = { Critical: '#f87171', Warning: '#fbbf24', Info: '#60a5fa' };
const evKindIcons: Record<string, typeof FileSearch> = {
  role: ShieldAlert, delegation: GitBranch, boundary: ShieldAlert,
  file: FileSearch, exec: TerminalSquare, default: Clock,
};

interface Props { alert: ResponsibilityAlert; model: InvestigationModel; activeRunId: string | null; availableAlerts: ResponsibilityAlert[]; onSelectAlert: (id: string) => void; evidenceLoading: boolean; evidenceError: string | null; }

export default function InvestigationWorkbench({ alert, model, availableAlerts, onSelectAlert }: Props) {
  const timelineRef = useRef<HTMLDivElement>(null);
  const evidenceRef = useRef<HTMLDivElement>(null);

  // GSAP entrance: timeline + evidence + verdict staggered
  useGSAP(() => {
    const tl = gsap.timeline({ defaults: { ease: 'power3.out' } });
    if (timelineRef.current) {
      tl.fromTo('.tl-row', { opacity: 0, x: -10 }, { opacity: 1, x: 0, duration: 0.4, stagger: 0.05, clearProps: 'transform' }, 0.1);
    }
    if (evidenceRef.current) {
      tl.fromTo('.ev-card', { opacity: 0, y: 14 }, { opacity: 1, y: 0, duration: 0.4, stagger: 0.06, clearProps: 'transform' }, 0.3);
    }
    tl.fromTo('.verdict-panel > *', { opacity: 0, y: 8 }, { opacity: 1, y: 0, duration: 0.35, stagger: 0.06, clearProps: 'transform' }, 0.4);
  }, { scope: timelineRef });

  const kpi = [
    { label: '时间线事件', value: model.timelineSteps.length, delta: `${model.boundaryCrossingCount} 边界穿越`, tone: 'info' as StatusTone },
    { label: '证据链', value: model.evidenceRefs.length, delta: `${model.linkedEvidence.length} 关联`, tone: 'success' as StatusTone },
    { label: '置信度', value: `${(alert.confidence * 100).toFixed(0)}%`, delta: alert.authority, tone: sevTone(alert.severity) },
    { label: '涉及主体', value: model.involvedAgentIds.length, delta: model.primaryAgentId ?? '—', tone: 'info' as StatusTone },
  ];

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: S.lg }}>
      {/* KPI row */}
      <div className="ln-kpi-strip">
        {kpi.map(i => (
          <div key={i.label} className="ln-panel ln-metric">
            <span className="ln-metric__label">{i.label}</span>
            <strong className="ln-metric__value" style={{ fontSize: 20 }}>{i.value}</strong>
            <span className="ln-metric__delta" data-tone={i.tone}>{i.delta}</span>
          </div>
        ))}
      </div>

      {/* main layout */}
      <div style={{ display: 'grid', gridTemplateColumns: '260px minmax(0, 1fr) 320px', gap: S.lg, alignItems: 'start' }}>
        {/* ── LEFT: SentinelOne-style alert list ── */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: S.sm, position: 'sticky', top: 44 }}>
          <span className="ln-eyebrow">告警队列</span>
          {availableAlerts.map((a) => {
            const active = a.alert_id === alert.alert_id;
            const sc = sevColor[a.severity] ?? sevColor.Info;
            return (
              <button key={a.alert_id} className="alert-list-btn" onClick={() => onSelectAlert(a.alert_id)} style={{
                display: 'flex', flexDirection: 'column', gap: 3, width: '100%', textAlign: 'left',
                padding: '10px 12px', borderRadius: 7, cursor: 'pointer',
                border: active ? `1px solid var(--ln-accent)` : `1px solid var(--ln-border)`,
                borderLeft: `3px solid ${active ? 'var(--ln-accent)' : sc}`,
                background: active ? 'var(--ln-accent-soft)' : 'transparent',
                transition: 'all 120ms ease',
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <span style={{ fontSize: T.sm, fontWeight: 600, color: active ? 'var(--ln-text)' : 'var(--ln-text-secondary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>{a.title}</span>
                  <StatusBadge tone={sevTone(a.severity)}>{a.severity}</StatusBadge>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: T.xs, color: 'var(--ln-text-muted)' }}>
                  <span style={{ fontFamily: 'var(--ln-mono)' }}>{a.primary_agent.agent_id}</span>
                  <StatusBadge tone={decisionTone(a.decision)}>{statusLabel(a.decision)}</StatusBadge>
                </div>
              </button>
            );
          })}
        </div>

        {/* ── CENTER: Datadog-style timeline + evidence ── */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: S.lg }}>
          {/* timeline — premium visual design */}
          <SectionPanel eyebrow="事件时间线" title={`${model.timelineSteps.length} 步`} meta={model.timelineSteps.length > 0 ? fmtMs(Math.max(0, (model.timelineSteps[model.timelineSteps.length - 1].timestamp_ns - model.timelineSteps[0].timestamp_ns) / 1_000_000)) : undefined} bodyClassName="ln-panel-body">
            <div ref={timelineRef}>
              {model.timelineSteps.length === 0 ? (
                <div style={{ textAlign: 'center', padding: '24px 0', color: 'var(--ln-text-muted)', fontSize: T.sm }}>暂无时间线数据</div>
              ) : (
                <div style={{ height: 420, paddingRight: 4 }}>
                  <VirtualList
                    items={model.timelineSteps}
                    estimateSize={72}
                    dynamic
                    renderRow={(step, i) => {
                    const offset = i === 0 ? 0 : Math.max(0, Math.round((step.timestamp_ns - model.timelineSteps[0].timestamp_ns) / 1_000_000));
                    const isLast = i === model.timelineSteps.length - 1;
                    const evType = step.event_type?.toLowerCase() ?? '';
                    const sc = sevColor[step.evidence_level === 'L1Exact' ? 'Critical' : 'Info'] ?? '#60a5fa';
                    const Icon = Object.entries(evKindIcons).find(([k]) => evType.includes(k))?.[1] ?? evKindIcons.default;
                    return (
                      <div key={i} className="tl-row" style={{ display: 'flex', gap: 0, position: 'relative', minHeight: 56 }}>
                        {/* timeline axis */}
                        <div style={{ width: 64, minWidth: 64, display: 'flex', flexDirection: 'column', alignItems: 'center', position: 'relative', paddingTop: 12 }}>
                          <span style={{ fontSize: 10, fontFamily: 'var(--ln-mono)', color: 'var(--ln-text-muted)', lineHeight: 1, background: 'var(--ln-surface-0)', padding: '2px 6px', borderRadius: 4, border: '1px solid var(--ln-border)' }}>T+{fmtMs(offset)}</span>
                          {/* glowing dot */}
                          <div style={{
                            width: 8, height: 8, borderRadius: 999, background: sc,
                            boxShadow: `0 0 8px ${sc}60`, marginTop: 8, flexShrink: 0,
                            transition: 'box-shadow 200ms ease',
                          }} />
                          {!isLast && (
                            <div style={{
                              flex: 1, width: 1.5, minHeight: 16,
                              background: `linear-gradient(180deg, ${sc}40, var(--ln-border))`,
                            }} />
                          )}
                        </div>
                        {/* event card */}
                        <div style={{
                          flex: 1, padding: '10px 14px', marginBottom: S.xs, marginLeft: S.sm,
                          borderRadius: 8, background: 'var(--ln-surface-0)',
                          border: '1px solid var(--ln-border)',
                          transition: 'border-color 150ms ease, box-shadow 150ms ease',
                        }}
                          onMouseEnter={e => { e.currentTarget.style.borderColor = sc + '50'; e.currentTarget.style.boxShadow = `0 2px 12px ${sc}14`; }}
                          onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--ln-border)'; e.currentTarget.style.boxShadow = 'none'; }}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                            <Icon size={13} style={{ color: sc, flexShrink: 0 }} />
                            <span style={{ fontSize: T.sm, fontWeight: 600, color: 'var(--ln-text)' }}>{step.event_type}</span>
                            <div style={{ display: 'flex', gap: 4, marginLeft: 'auto', flexShrink: 0 }}>
                              {step.evidence_ref && <span style={{ fontSize: 9, padding: '2px 6px', borderRadius: 4, background: 'rgba(74,222,128,0.12)', color: '#4ade80', fontFamily: 'var(--ln-mono)' }}>{step.evidence_ref.slice(0, 14)}</span>}
                              {step.evidence_level === 'L1Exact' && <span style={{ fontSize: 9, padding: '2px 5px', borderRadius: 4, background: 'var(--ln-accent-soft)', color: 'var(--ln-accent)', fontFamily: 'var(--ln-mono)', fontWeight: 600 }}>L1</span>}
                            </div>
                          </div>
                          <div style={{ fontSize: T.sm, color: 'var(--ln-text-secondary)', lineHeight: 1.6 }}>{step.description}</div>
                          {step.resource && <div style={{ fontSize: 10, color: 'var(--ln-text-muted)', fontFamily: 'var(--ln-mono)', marginTop: 4, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{step.resource}</div>}
                        </div>
                      </div>
                    );
                  }}
                  />
                </div>
              )}
            </div>
          </SectionPanel>

          {/* evidence chain — premium cards */}
          <SectionPanel eyebrow="证据链" title={`${model.linkedEvidence.length} 条`} bodyClassName="ln-panel-body">
            <div ref={evidenceRef}>
              {model.linkedEvidence.length === 0 ? (
                <div style={{ textAlign: 'center', padding: '20px 0', color: 'var(--ln-text-muted)', fontSize: T.sm }}>暂无关联证据</div>
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 0, maxHeight: 400, overflowY: 'auto', position: 'relative' }}>
                  {model.linkedEvidence.slice(0, 12).map((ev, idx) => {
                    const confPct = Math.round(ev.confidence * 100);
                    const confColor = ev.confidence > 0.8 ? '#4ade80' : ev.confidence > 0.5 ? '#fbbf24' : '#f87171';
                    const isLast = idx === Math.min(model.linkedEvidence.length, 12) - 1;
                    return (
                      <div key={ev.evidence_id} className="ev-card" style={{ display: 'flex', gap: 0, position: 'relative' }}>
                        {/* exhibit number + connector */}
                        <div style={{ width: 40, minWidth: 40, display: 'flex', flexDirection: 'column', alignItems: 'center', paddingTop: 14 }}>
                          <span style={{
                            width: 24, height: 24, borderRadius: 6, display: 'flex', alignItems: 'center', justifyContent: 'center',
                            background: `${confColor}18`, border: `1.5px solid ${confColor}40`,
                            fontSize: 10, fontWeight: 700, fontFamily: 'var(--ln-mono)', color: confColor,
                          }}>{idx + 1}</span>
                          {!isLast && (
                            <div style={{
                              flex: 1, width: 1.5, minHeight: 12,
                              background: `linear-gradient(180deg, ${confColor}30, var(--ln-border))`,
                            }} />
                          )}
                        </div>
                        {/* card */}
                        <div style={{
                          flex: 1, padding: '10px 14px', marginBottom: 2,
                          borderRadius: 8, background: 'var(--ln-surface-0)', border: '1px solid var(--ln-border)',
                          transition: 'border-color 150ms ease, box-shadow 150ms ease',
                        }}
                          onMouseEnter={e => { e.currentTarget.style.borderColor = confColor + '50'; e.currentTarget.style.boxShadow = `0 0 16px ${confColor}10`; }}
                          onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--ln-border)'; e.currentTarget.style.boxShadow = 'none'; }}>
                          {/* header */}
                          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                            <span style={{ fontSize: 11, fontFamily: 'var(--ln-mono)', color: 'var(--ln-accent)', fontWeight: 600, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{ev.evidence_id}</span>
                            <div style={{ display: 'flex', gap: 4, flexShrink: 0 }}>
                              <span style={{ fontSize: 9, padding: '2px 6px', borderRadius: 4, background: `${confColor}16`, color: confColor, fontWeight: 600, fontFamily: 'var(--ln-mono)' }}>{confPct}%</span>
                              <span style={{ fontSize: 9, padding: '2px 6px', borderRadius: 4, background: 'var(--ln-surface-2)', color: 'var(--ln-text-muted)', fontFamily: 'var(--ln-mono)' }}>{ev.match_level}</span>
                            </div>
                          </div>
                          {/* confidence bar */}
                          <div style={{ height: 3, borderRadius: 999, background: 'var(--ln-surface-2)', marginBottom: 8, overflow: 'hidden' }}>
                            <div style={{
                              height: '100%', width: `${confPct}%`, borderRadius: 999,
                              background: `linear-gradient(90deg, ${confColor}88, ${confColor})`,
                              transition: 'width 300ms ease',
                            }} />
                          </div>
                          {/* material — structured key:value rows */}
                          <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                            {ev.matched_fields.canonical_path && (
                              <div style={{ display: 'flex', gap: 6, fontSize: 10, overflow: 'hidden' }}>
                                <span style={{ color: '#4ade80', flexShrink: 0, fontWeight: 600, fontFamily: 'var(--ln-mono)', width: 36 }}>path</span>
                                <span style={{ color: 'var(--ln-text-secondary)', fontFamily: 'var(--ln-mono)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{ev.matched_fields.canonical_path}</span>
                              </div>
                            )}
                            {ev.matched_fields.command && (
                              <div style={{ display: 'flex', gap: 6, fontSize: 10, overflow: 'hidden' }}>
                                <span style={{ color: '#60a5fa', flexShrink: 0, fontWeight: 600, fontFamily: 'var(--ln-mono)', width: 36 }}>cmd</span>
                                <span style={{ color: 'var(--ln-text-secondary)', fontFamily: 'var(--ln-mono)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{ev.matched_fields.command}</span>
                              </div>
                            )}
                            {ev.matched_fields.host && (
                              <div style={{ display: 'flex', gap: 6, fontSize: 10, overflow: 'hidden' }}>
                                <span style={{ color: '#a78bfa', flexShrink: 0, fontWeight: 600, fontFamily: 'var(--ln-mono)', width: 36 }}>host</span>
                                <span style={{ color: 'var(--ln-text-secondary)', fontFamily: 'var(--ln-mono)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{ev.matched_fields.host}{ev.matched_fields.port ? `:${ev.matched_fields.port}` : ''}</span>
                              </div>
                            )}
                            {ev.matched_fields.raw_args?.length ? (
                              <div style={{ display: 'flex', gap: 6, fontSize: 10, overflow: 'hidden' }}>
                                <span style={{ color: '#fb923c', flexShrink: 0, fontWeight: 600, fontFamily: 'var(--ln-mono)', width: 36 }}>args</span>
                                <span style={{ color: 'var(--ln-text-secondary)', fontFamily: 'var(--ln-mono)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{ev.matched_fields.raw_args.join(' ')}</span>
                              </div>
                            ) : null}
                            {!ev.matched_fields.canonical_path && !ev.matched_fields.command && !ev.matched_fields.host && !ev.matched_fields.raw_args?.length && (
                              <span style={{ fontSize: 10, color: 'var(--ln-text-muted)', fontFamily: 'var(--ln-mono)' }}>{ev.evidence_id}</span>
                            )}
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </SectionPanel>
        </div>

        {/* ── RIGHT: Verdict + reasoning + actions ── */}
        <div className="verdict-panel" style={{ display: 'flex', flexDirection: 'column', gap: S.lg, position: 'sticky', top: 44 }}>
          {/* verdict */}
          <SectionPanel eyebrow="判决" title={alert.decision} meta={alert.authority} bodyClassName="ln-panel-body">
            <div style={{ display: 'flex', flexDirection: 'column', gap: S.md }}>
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                <StatusBadge tone={sevTone(alert.severity)}>{alert.severity}</StatusBadge>
                <StatusBadge tone={decisionTone(alert.decision)}>{statusLabel(alert.decision)}</StatusBadge>
              </div>

              {/* summary */}
              <div style={{ fontSize: T.sm, color: 'var(--ln-text-secondary)', lineHeight: 1.65, padding: S.md, borderRadius: 7, background: 'var(--ln-surface-0)', border: '1px solid var(--ln-border)' }}>
                {alert.summary}
              </div>

              {/* reasoning */}
              {alert.reasoning && (
                <div style={{ padding: S.md, borderRadius: 7, background: 'rgba(94,106,210,0.05)', border: '1px solid rgba(94,106,210,0.14)' }}>
                  <div style={{ fontSize: T.xs, fontWeight: 600, color: 'var(--ln-accent)', marginBottom: 4, letterSpacing: '0.03em', textTransform: 'uppercase' }}>AI 推理</div>
                  <div style={{ fontSize: T.xs, color: 'var(--ln-text-secondary)', lineHeight: 1.6 }}>{alert.reasoning}</div>
                </div>
              )}

              {/* action hint */}
              {alert.action_hint && (
                <div style={{ padding: S.md, borderRadius: 7, background: 'rgba(248,113,113,0.05)', border: '1px solid rgba(248,113,113,0.15)' }}>
                  <div style={{ fontSize: T.xs, fontWeight: 600, color: '#f87171', marginBottom: 4, letterSpacing: '0.03em', textTransform: 'uppercase' }}>建议处置</div>
                  <div style={{ fontSize: T.sm, color: 'var(--ln-text-secondary)', lineHeight: 1.55 }}>{alert.action_hint}</div>
                </div>
              )}
            </div>
          </SectionPanel>

          {/* agent info */}
          <SectionPanel eyebrow="主体信息" title={alert.primary_agent.agent_id} bodyClassName="ln-panel-body">
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: S.md }}>
              <div><span className="ln-panel__meta">角色</span><div style={{ fontSize: T.sm, color: 'var(--ln-text)' }}>{alert.primary_agent.claimed_role ?? '—'}</div></div>
              <div><span className="ln-panel__meta">权限</span><div style={{ fontSize: T.sm, color: 'var(--ln-text)' }}>{alert.authority}</div></div>
              <div><span className="ln-panel__meta">关联主体</span><div style={{ fontSize: T.sm, color: 'var(--ln-text)', fontFamily: 'var(--ln-mono)' }}>{model.involvedAgentIds.length}</div></div>
              <div><span className="ln-panel__meta">边界穿越</span><div style={{ fontSize: T.sm, color: 'var(--ln-text)', fontFamily: 'var(--ln-mono)' }}>{model.boundaryCrossingCount}</div></div>
            </div>
          </SectionPanel>

          {/* actions */}
          <SectionPanel eyebrow="操作" title="后续步骤" bodyClassName="ln-panel-body">
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              <Link href="/traces" className="ln-btn" style={{ justifyContent: 'space-between', textDecoration: 'none' }}>追踪完整链路<ArrowRight size={13} /></Link>
              <Link href="/overview" className="ln-btn" style={{ justifyContent: 'space-between', textDecoration: 'none' }}>返回仪表盘<ArrowRight size={13} /></Link>
            </div>
          </SectionPanel>
        </div>
      </div>
    </div>
  );
}
