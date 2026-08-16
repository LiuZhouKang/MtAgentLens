'use client';

import { useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import { useGSAP } from '@gsap/react';
import gsap from 'gsap';
import {
  ArrowRight,
  Bot,
  ChevronDown,
  FileSearch,
  Gavel,
  Globe,
  ShieldAlert,
  Sparkles,
  Wrench,
} from 'lucide-react';
import { MetricCard } from '@/components/ui/linear/metric-card';
import { SectionPanel } from '@/components/ui/linear/section-panel';
import { StatusBadge, type StatusTone } from '@/components/ui/linear/status-badge';
import VirtualList from '@/components/ui/virtual-list';
import AgentAvatar from '@/components/ui/agent-avatar';
import ClaimList from '@/components/investigation/claim-list';
import type { TraceEventKind } from '@/lib/trace-workbench';
import { buildInvestigationTimeline } from '@/lib/investigation-timeline';
import { authorityLabel, decisionLabel, severityLabel, TONE_COLOR } from '@/lib/investigation-labels';
import { shortAgentId } from '@/lib/agent-identity';
import type { ResponsibilityAlert } from '@/lib/types';
import type { InvestigationModel } from '@/lib/investigation';
import { collectAlertEvidenceRefs } from '@/lib/investigation';
import { useAppStore } from '@/lib/store';
import { buildTracebacksByClaimId } from '@/lib/traceback-index';

const T = { xs: 9.5, sm: 10.5, md: 12, lg: 14, xl: 16 };
const S = { xs: 2, sm: 4, md: 8, lg: 12, xl: 16 };

function fmtMs(ms: number) {
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`;
}

/** epoch 纳秒 → HH:MM:SS（告警时间线时间戳为 epoch）。 */
function fmtClock(ns: number): string {
  return new Date(ns / 1_000_000).toISOString().slice(11, 19);
}

// 事件 kind → 图标（与 traces 页 classifyEventKind 分类对齐，权威统一）
const KIND_ICONS: Record<TraceEventKind, typeof FileSearch> = {
  agent: Bot,
  llm: Sparkles,
  tool: Wrench,
  file: FileSearch,
  network: Globe,
  boundary: ShieldAlert,
  verdict: Gavel,
};
// kind → ln token 色（克制，无光晕）
const KIND_COLOR: Record<TraceEventKind, string> = {
  agent: 'var(--ln-info)',
  llm: 'var(--ln-accent)',
  tool: 'var(--ln-success)',
  file: 'var(--ln-text-muted)',
  network: 'var(--ln-info)',
  boundary: 'var(--ln-danger)',
  verdict: 'var(--ln-warning)',
};

/** 动作类型 → 行为徽章 tone（与进程谱系同语义）。 */
function actionTone(actionType: string): string {
  if (actionType === 'FileRead') return 'read';
  if (actionType === 'FileWrite') return 'write';
  if (actionType === 'FileOverwrite') return 'overwrite';
  if (actionType === 'ProcessExec') return 'exec';
  return 'other';
}

interface Props {
  alert: ResponsibilityAlert;
  model: InvestigationModel;
  availableAlerts: ResponsibilityAlert[];
  onSelectAlert: (id: string) => void;
}

export default function InvestigationWorkbench({
  alert,
  model,
  availableAlerts,
  onSelectAlert,
}: Props) {
  const claims = useAppStore((s) => s.claims);
  const tracebacks = useAppStore((s) => s.tracebacks);
  const graph = useAppStore((s) => s.graph);
  const dbAssets = useAppStore((s) => s.dbAssets);
  const storeActions = useAppStore((s) => s.actions);
  const tracebacksByClaimId = useMemo(() => buildTracebacksByClaimId(tracebacks), [tracebacks]);
  const alertEvidenceRefs = useMemo(() => collectAlertEvidenceRefs(alert, graph), [alert, graph]);
  // 富化时间线：折叠 SSL 噪声 + 解析 pid + 关联真实数据（process_events/actions/evidence）
  const timelineModel = useMemo(
    () =>
      buildInvestigationTimeline({
        steps: model.timelineSteps,
        evidence: model.linkedEvidence,
        totalDurationMs: model.timelineDurationMs,
        alertSeverity: alert.severity,
        processEvents: dbAssets?.processEvents ?? [],
        actions: storeActions,
      }),
    [
      model.timelineSteps,
      model.linkedEvidence,
      model.timelineDurationMs,
      alert.severity,
      dbAssets?.processEvents,
      storeActions,
    ],
  );
  const [expandedTl, setExpandedTl] = useState<Set<string>>(new Set());
  const toggleTl = (id: string) =>
    setExpandedTl((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const rootRef = useRef<HTMLDivElement>(null);
  const queueRef = useRef<HTMLDivElement>(null);
  const timelineRef = useRef<HTMLDivElement>(null);

  // GSAP entrance（与全站语言统一）：队列左滑 → 时间线左滑 → 判决错峰
  useGSAP(
    () => {
      if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
      const tl = gsap.timeline({ defaults: { ease: 'power3.out' } });
      if (queueRef.current) {
        tl.fromTo(
          '.ln-alert-queue__btn',
          { opacity: 0, x: -8 },
          { opacity: 1, x: 0, duration: 0.35, stagger: 0.03, clearProps: 'transform' },
          0.1,
        );
      }
      if (timelineRef.current) {
        tl.fromTo(
          '.tl-row',
          { opacity: 0, x: -10 },
          { opacity: 1, x: 0, duration: 0.4, stagger: 0.05, clearProps: 'transform' },
          0.15,
        );
      }
      tl.fromTo(
        '.verdict-panel > *',
        { opacity: 0, y: 8 },
        { opacity: 1, y: 0, duration: 0.35, stagger: 0.06, clearProps: 'transform' },
        0.4,
      );
    },
    { scope: rootRef },
  );

  const kpi = [
    {
      label: '时间线事件',
      value: model.timelineSteps.length,
      delta: `${model.boundaryCrossingCount} 边界穿越`,
      deltaTone: 'info' as StatusTone,
    },
    {
      label: '证据链',
      value: model.evidenceRefs.length,
      delta: `${model.linkedEvidence.length} 关联`,
      deltaTone: 'success' as StatusTone,
    },
    {
      label: '置信度',
      value: `${(alert.confidence * 100).toFixed(0)}%`,
      delta: authorityLabel(alert.authority).label,
      deltaTone: sevTone(alert.severity),
    },
    {
      label: '涉及主体',
      value: model.involvedAgentIds.length,
      delta: model.primaryAgentId ?? '—',
      deltaTone: 'info' as StatusTone,
    },
  ];

  return (
    <div ref={rootRef} style={{ display: 'flex', flexDirection: 'column', gap: S.lg }}>
      {/* KPI row —— 与 MetricCard 严格同构（DigitReel + label + delta） */}
      <div className="ln-kpi-strip">
        {kpi.map((k) => (
          <MetricCard key={k.label} label={k.label} value={k.value} delta={k.delta} deltaTone={k.deltaTone} />
        ))}
      </div>

      {/* main layout */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'minmax(200px, 260px) minmax(0, 1fr) minmax(280px, 320px)',
          gap: S.lg,
          alignItems: 'start',
          overflowX: 'auto',
        }}
      >
        {/* ── LEFT: 告警队列（SentinelOne-style）── */}
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            gap: S.sm,
            position: 'sticky',
            top: 44,
          }}
        >
          <span className="ln-eyebrow">告警队列</span>
          <div ref={queueRef} className="ln-alert-queue">
            {availableAlerts.map((a) => {
              const active = a.alert_id === alert.alert_id;
              const sev = severityLabel(a.severity);
              const dec = decisionLabel(a.decision);
              return (
                <button
                  key={a.alert_id}
                  type="button"
                  className="ln-alert-queue__btn"
                  data-active={active ? 'true' : 'false'}
                  aria-pressed={active}
                  onClick={() => onSelectAlert(a.alert_id)}
                >
                  <span className="ln-alert-queue__head">
                    <span className="ln-alert-queue__dot" style={{ background: TONE_COLOR[sev.tone ?? 'info'] }} />
                    <span className="ln-alert-queue__title">{a.title}</span>
                    <StatusBadge tone={sevTone(a.severity)}>{sev.label}</StatusBadge>
                  </span>
                  <span className="ln-alert-queue__meta">
                    <AgentAvatar agentId={a.primary_agent.agent_id} size={14} />
                    <span className="ln-alert-queue__agent">{a.primary_agent.agent_id}</span>
                    <StatusBadge tone={sevTone(a.decision)}>{dec.label}</StatusBadge>
                  </span>
                </button>
              );
            })}
          </div>
        </div>

        {/* ── CENTER: 时间线 + 证据链 + 责任声明 + 取证链 ── */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: S.lg }}>
          {/* 事件时间线 —— 富化责任瀑布：折叠 SSL 噪声 + pid/命令/证据 + 真实时间跨度 */}
          <SectionPanel
            eyebrow="事件时间线"
            title={`${timelineModel.events.length} 步`}
            meta={
              timelineModel.foldedSignals > 0
                ? `折叠 ${timelineModel.foldedSignals} 条 SSL 信号 · ${fmtMs(timelineModel.totalMs)}`
                : fmtMs(timelineModel.totalMs)
            }
            bodyClassName="ln-panel-body"
          >
            <div ref={timelineRef}>
              {timelineModel.events.length === 0 ? (
                <div
                  style={{
                    textAlign: 'center',
                    padding: '24px 0',
                    color: 'var(--ln-text-muted)',
                    fontSize: T.sm,
                  }}
                >
                  暂无时间线数据
                </div>
              ) : (
                <div>
                  {/* 时间刻度尺：整条时间线 0 → 100% */}
                  <div className="ln-inv-tl-scale">
                    {[0, 0.25, 0.5, 0.75, 1].map((f) => (
                      <span key={f} className="ln-inv-tl-scale__tick">
                        {f === 0 ? 'T+0' : `T+${fmtMs(Math.round(timelineModel.totalMs * f))}`}
                      </span>
                    ))}
                  </div>
                  <div style={{ height: 420, paddingRight: 4 }}>
                    <VirtualList
                      items={timelineModel.events}
                      estimateSize={88}
                      dynamic
                      renderRow={(ev, i) => {
                        const isLast = i === timelineModel.events.length - 1;
                        const color = KIND_COLOR[ev.kind] ?? 'var(--ln-text-muted)';
                        const Icon = KIND_ICONS[ev.kind] ?? FileSearch;
                        const expanded = expandedTl.has(ev.id);
                        // 瀑布条：事件在整条时间线上的起止占比
                        const barLeft = Math.min((ev.offsetMs / timelineModel.totalMs) * 100, 100);
                        const barWidth = Math.max(
                          (ev.durationMs / timelineModel.totalMs) * 100,
                          0.4,
                        );
                        return (
                          <div
                            key={ev.id}
                            className="tl-row"
                            style={{ display: 'flex', gap: 0, position: 'relative' }}
                          >
                            {/* 时间轴：T+ 偏移 + kind 色点 + 连接线 */}
                            <div className="ln-inv-tl-axis">
                              <span className="ln-inv-tl-time">T+{fmtMs(ev.offsetMs)}</span>
                              <span className="ln-inv-tl-dot" style={{ background: color }} />
                              {!isLast && <span className="ln-inv-tl-line" />}
                            </div>
                            {/* 事件体：头部 + 标题 + 瀑布条 + 元信息 + 展开详情 */}
                            <div className="ln-inv-tl-card" data-expanded={expanded ? 'true' : 'false'}>
                              <button
                                type="button"
                                className="ln-inv-tl-btn"
                                aria-expanded={expanded}
                                onClick={() => toggleTl(ev.id)}
                              >
                                <Icon size={13} style={{ color, flexShrink: 0 }} />
                                <span className="ln-inv-tl-label">{ev.label}</span>
                                {/* 真实进程身份（process_events 精确关联；聚合信号无单一进程） */}
                                {ev.aggregated === 0 && (ev.realComm || ev.pid != null) && (
                                  <span className="ln-inv-tl-proc" title={ev.agentId ?? undefined}>
                                    {ev.realComm ?? ''}
                                    {ev.pid != null ? ` · pid ${ev.pid}` : ''}
                                    {ev.agentId ? ` · ${shortAgentId(ev.agentId)}` : ''}
                                  </span>
                                )}
                                {ev.aggregated > 1 && (
                                  <span className="ln-chip" data-tone="muted">
                                    ×{ev.aggregated}
                                  </span>
                                )}
                                {ev.isBoundary && (
                                  <span className="ln-chip" data-tone="danger">
                                    边界
                                  </span>
                                )}
                                {ev.evidenceLevel === 'L1Exact' && (
                                  <span className="ln-chip" data-tone="success">
                                    L1
                                  </span>
                                )}
                                <span
                                  className="ln-inv-tl-clock"
                                  title={`真实时钟时间 · ${new Date(ev.tsNs / 1_000_000).toISOString().slice(0, 19).replace('T', ' ')} UTC`}
                                >
                                  {fmtClock(ev.tsNs)}
                                </span>
                                <span
                                  className="ln-inv-tl-dur"
                                  title="距下一事件的真实时间间隔（后端未提供事件实际耗时）"
                                >
                                  +{fmtMs(ev.durationMs)}
                                </span>
                                <ChevronDown
                                  size={11}
                                  className="ln-inv-tl-chev"
                                  style={{
                                    transform: expanded ? 'rotate(180deg)' : 'none',
                                  }}
                                />
                              </button>
                              <div className="ln-inv-tl-title">{ev.description}</div>
                              {/* 瀑布条：事件在时间线上的真实位置与跨度 */}
                              <div className="ln-inv-tl-bar">
                                <span
                                  className="ln-inv-tl-bar__fill"
                                  style={{ left: `${barLeft}%`, width: `${barWidth}%`, background: color }}
                                />
                              </div>
                              {/* 真实行为：该进程在 actions 表中的真实操作（文件/命令） */}
                              {ev.aggregated === 0 && ev.actions.length > 0 && (
                                <div className="ln-inv-tl-acts">
                                  {ev.actions.slice(0, 3).map((a, ai) => (
                                    <span key={ai} className="ln-inv-tl-act" title={a.target ?? undefined}>
                                      <span
                                        className="ln-inv-tl-act__type"
                                        data-tone={actionTone(a.actionType)}
                                      >
                                        {a.actionLabel}
                                      </span>
                                      {a.target && <span className="ln-inv-tl-act__target">{a.target}</span>}
                                    </span>
                                  ))}
                                  {ev.actions.length > 3 && (
                                    <span className="ln-inv-tl-acts__more">
                                      +{ev.actions.length - 3} 行为
                                    </span>
                                  )}
                                </div>
                              )}
                              {/* 元信息：进程 / 命令 / 路径 / 置信度 */}
                              <div className="ln-inv-tl-meta">
                                {ev.matchedCommand && (
                                  <span className="ln-inv-tl-meta__item" title={ev.matchedCommand}>
                                    {ev.matchedCommand}
                                  </span>
                                )}
                                {ev.matchedCommand && (
                                  <span className="ln-inv-tl-meta__item" title={ev.matchedCommand}>
                                    {ev.matchedCommand}
                                  </span>
                                )}
                                {ev.matchedPath && (
                                  <span className="ln-inv-tl-meta__item" title={ev.matchedPath}>
                                    {ev.matchedPath}
                                  </span>
                                )}
                                {ev.matchedHost && (
                                  <span className="ln-inv-tl-meta__item" title={ev.matchedHost}>
                                    {ev.matchedHost}
                                  </span>
                                )}
                                {ev.confidence != null && (
                                  <span
                                    className="ln-inv-tl-meta__item ln-inv-tl-meta__conf"
                                    style={{ color }}
                                  >
                                    {Math.round(ev.confidence * 100)}%
                                  </span>
                                )}
                              </div>
                              {/* 展开详情 */}
                              {expanded && (
                                <div className="ln-inv-tl-detail">
                                  {ev.aggregated > 1 && (
                                    <div className="ln-inv-tl-detail__row">
                                      <span className="ln-inv-tl-detail__k">折叠</span>
                                      <span className="ln-inv-tl-detail__v">
                                        聚合 {ev.aggregated} 条重复 SSL/TLS 连接信号
                                      </span>
                                    </div>
                                  )}
                                  {ev.tsNs > 0 && (
                                    <div className="ln-inv-tl-detail__row">
                                      <span className="ln-inv-tl-detail__k">时间</span>
                                      <span className="ln-inv-tl-detail__v">
                                        {new Date(ev.tsNs / 1_000_000)
                                          .toISOString()
                                          .slice(0, 19)
                                          .replace('T', ' ')}{' '}
                                        UTC
                                      </span>
                                    </div>
                                  )}
                                  {ev.agentId && (
                                    <div className="ln-inv-tl-detail__row">
                                      <span className="ln-inv-tl-detail__k">主体</span>
                                      <span className="ln-inv-tl-detail__v">{ev.agentId}</span>
                                    </div>
                                  )}
                                  {ev.sourceEventId && (
                                    <div className="ln-inv-tl-detail__row">
                                      <span className="ln-inv-tl-detail__k">事件源</span>
                                      <span className="ln-inv-tl-detail__v">{ev.sourceEventId}</span>
                                    </div>
                                  )}
                                  {/* 该进程的完整真实行为列表（actions 表，不编造） */}
                                  {ev.aggregated === 0 && ev.actions.length > 0 && (
                                    <div className="ln-inv-tl-detail__acts">
                                      <div className="ln-inv-tl-detail__row">
                                        <span className="ln-inv-tl-detail__k">行为</span>
                                        <span className="ln-inv-tl-detail__v">
                                          {ev.actions.length} 条真实操作
                                        </span>
                                      </div>
                                      {ev.actions.map((a, ai) => (
                                        <div key={ai} className="ln-inv-tl-detail__row">
                                          <span className="ln-inv-tl-detail__k ln-inv-tl-detail__k--act">
                                            {a.actionLabel}
                                          </span>
                                          <span
                                            className="ln-inv-tl-detail__v"
                                            title={a.target ?? undefined}
                                          >
                                            {a.target ?? '—'}
                                          </span>
                                        </div>
                                      ))}
                                    </div>
                                  )}
                                  {ev.rawArgs.length > 0 && (
                                    <div className="ln-inv-tl-detail__row">
                                      <span className="ln-inv-tl-detail__k">args</span>
                                      <span className="ln-inv-tl-detail__v">
                                        {ev.rawArgs.join(' ')}
                                      </span>
                                    </div>
                                  )}
                                  {ev.exitCode != null && (
                                    <div className="ln-inv-tl-detail__row">
                                      <span className="ln-inv-tl-detail__k">退出码</span>
                                      <span className="ln-inv-tl-detail__v">{ev.exitCode}</span>
                                    </div>
                                  )}
                                  {ev.evidenceRef && (
                                    <div className="ln-inv-tl-detail__row">
                                      <span className="ln-inv-tl-detail__k">证据</span>
                                      <span className="ln-inv-tl-detail__v">{ev.evidenceRef}</span>
                                    </div>
                                  )}
                                  {ev.ruleIds.length > 0 && (
                                    <div className="ln-inv-tl-detail__row">
                                      <span className="ln-inv-tl-detail__k">规则</span>
                                      <span className="ln-inv-tl-detail__v">
                                        {ev.ruleIds.join(', ')}
                                      </span>
                                    </div>
                                  )}
                                  {ev.patternId && (
                                    <div className="ln-inv-tl-detail__row">
                                      <span className="ln-inv-tl-detail__k">模式</span>
                                      <span className="ln-inv-tl-detail__v">{ev.patternId}</span>
                                    </div>
                                  )}
                                </div>
                              )}
                            </div>
                          </div>
                        );
                      }}
                    />
                  </div>
                </div>
              )}
            </div>
          </SectionPanel>

          <SectionPanel
            eyebrow="责任声明"
            title={`${claims.length} 条`}
            bodyClassName="ln-panel-body"
          >
            <ClaimList
              claims={claims}
              tracebacksByClaimId={tracebacksByClaimId}
              alertEvidenceRefs={alertEvidenceRefs}
            />
          </SectionPanel>
        </div>

        {/* ── RIGHT: 判决 + 主体 + 操作 ── */}
        <div
          className="verdict-panel"
          style={{
            display: 'flex',
            flexDirection: 'column',
            gap: S.lg,
            position: 'sticky',
            top: 44,
          }}
        >
          {/* 判决 —— decision 中文标题 + 克制卡 */}
          <SectionPanel
            eyebrow="判决"
            title={decisionLabel(alert.decision).label}
            meta={authorityLabel(alert.authority).label}
            bodyClassName="ln-panel-body"
          >
            <div style={{ display: 'flex', flexDirection: 'column', gap: S.md }}>
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                <StatusBadge tone={sevTone(alert.severity)}>{severityLabel(alert.severity).label}</StatusBadge>
                <StatusBadge tone={sevTone(alert.decision)}>{decisionLabel(alert.decision).label}</StatusBadge>
              </div>

              <div className="ln-verdict-box">
                {alert.summary}
              </div>

              {alert.reasoning && (
                <div className="ln-verdict-box ln-verdict-box--accent">
                  <div className="ln-verdict-box__label">AI 推理</div>
                  <div className="ln-verdict-box__body">{alert.reasoning}</div>
                </div>
              )}

              {alert.action_hint && (
                <div className="ln-verdict-box ln-verdict-box--danger">
                  <div className="ln-verdict-box__label">建议处置</div>
                  <div className="ln-verdict-box__body">{alert.action_hint}</div>
                </div>
              )}
            </div>
          </SectionPanel>

          {/* 主体信息 —— AgentAvatar + 权威度 */}
          <SectionPanel
            eyebrow="主体信息"
            title={alert.primary_agent.agent_id}
            bodyClassName="ln-panel-body"
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: S.md, marginBottom: S.md }}>
              <AgentAvatar agentId={alert.primary_agent.agent_id} size={34} />
              <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                <span className="ln-panel__meta">身份权威度</span>
                <div style={{ fontSize: T.sm, color: 'var(--ln-text)' }}>
                  {authorityLabel(alert.authority).label}
                </div>
              </div>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: S.md }}>
              <div>
                <span className="ln-panel__meta">角色</span>
                <div style={{ fontSize: T.sm, color: 'var(--ln-text)' }}>
                  {alert.primary_agent.claimed_role ?? '—'}
                </div>
              </div>
              <div>
                <span className="ln-panel__meta">权限</span>
                <div style={{ fontSize: T.sm, color: 'var(--ln-text)' }}>{alert.authority}</div>
              </div>
              <div>
                <span className="ln-panel__meta">关联主体</span>
                <div
                  style={{ fontSize: T.sm, color: 'var(--ln-text)', fontFamily: 'var(--ln-mono)' }}
                >
                  {model.involvedAgentIds.length}
                </div>
              </div>
              <div>
                <span className="ln-panel__meta">边界穿越</span>
                <div
                  style={{ fontSize: T.sm, color: 'var(--ln-text)', fontFamily: 'var(--ln-mono)' }}
                >
                  {model.boundaryCrossingCount}
                </div>
              </div>
            </div>
          </SectionPanel>

          {/* 操作 */}
          <SectionPanel eyebrow="操作" title="后续步骤" bodyClassName="ln-panel-body">
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              <Link
                href="/traces"
                className="ln-btn"
                style={{ justifyContent: 'space-between', textDecoration: 'none' }}
              >
                追踪完整链路
                <ArrowRight size={13} />
              </Link>
              <Link
                href="/overview"
                className="ln-btn"
                style={{ justifyContent: 'space-between', textDecoration: 'none' }}
              >
                返回仪表盘
                <ArrowRight size={13} />
              </Link>
            </div>
          </SectionPanel>
        </div>
      </div>
    </div>
  );
}

function sevTone(s: string): StatusTone {
  if (s === 'Critical' || s === 'Unsafe') return 'danger';
  if (s === 'Warning' || s === 'Uncertain') return 'warning';
  return 'info';
}
