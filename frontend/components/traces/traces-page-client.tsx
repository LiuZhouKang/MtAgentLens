'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { useGSAP } from '@gsap/react';
import gsap from 'gsap';
import Link from 'next/link';
import { useSearchParams } from 'next/navigation';
import {
  ArrowRight, CheckCircle2, Check, Clipboard, ChevronDown, ChevronRight,
  FileSearch, GitBranch, Network, Server, ShieldAlert, Sparkles, TerminalSquare, X,
} from 'lucide-react';
import { SectionPanel } from '@/components/ui/linear/section-panel';
import { StatusBadge, type StatusTone } from '@/components/ui/linear/status-badge';
import VirtualList from '@/components/ui/virtual-list';
import { demoAlerts } from '@/lib/demo-data';
import { useAppStore } from '@/lib/store';
import { buildInvestigationHref } from '@/lib/investigation-route';
import { buildTraceHref, parseTraceAlertId } from '@/lib/trace-route';
import { buildTraceRunModel, type TraceEventKind, type TraceEventModel, type TraceRunModel } from '@/lib/trace-workbench';
import { useRunEvidence } from '@/lib/use-run-evidence';
import { useHashRouteState } from '@/lib/use-hash-route-state';

// ── tokens ──
const T = { xs: 9.5, sm: 10.5, md: 12, lg: 14, xl: 16, h: 20 };
const S = { xs: 2, sm: 4, md: 8, lg: 12, xl: 16 };

type AnyIcon = React.FC<{ size?: number }>;
const kindIcon: Record<TraceEventKind, AnyIcon> = { agent: GitBranch, llm: Sparkles, tool: TerminalSquare, file: FileSearch, network: Network, boundary: ShieldAlert, verdict: CheckCircle2 };
const kindLabel: Record<TraceEventKind, string> = { agent: 'Agent', llm: 'LLM', tool: 'Tool', file: '文件', network: '网络', boundary: '边界', verdict: '判定' };
const kindColor: Record<TraceEventKind, string> = { agent: '#60a5fa', llm: '#a78bfa', tool: '#fb923c', file: '#4ade80', network: '#22d3ee', boundary: '#f87171', verdict: '#fbbf24' };
const FILTERS: Array<TraceEventKind | 'all'> = ['all', 'agent', 'llm', 'tool', 'file', 'network', 'boundary', 'verdict'];

function fmtMs(ms: number) { return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`; }
function sevTone(s: TraceRunModel['severity'] | 'Normal'): StatusTone {
  if (s === 'Critical') return 'danger'; if (s === 'Warning') return 'warning'; return s === 'Info' ? 'info' : 'success';
}

// ── CodeBlock ──
function CodeBlock({ children, color = 'var(--ln-border)', maxH = 300 }: { children: string; color?: string; maxH?: number }) {
  const [copied, setCopied] = useState(false);
  const copy = () => { navigator.clipboard.writeText(children).then(() => { setCopied(true); setTimeout(() => setCopied(false), 2000); }); };
  const lines = children.split('\n');
  return (
    <div style={{ background: '#0a0a0f', borderRadius: S.sm + 2, border: `1px solid ${color}22`, overflow: 'hidden' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: `${S.xs}px ${S.sm + 2}px`, borderBottom: `1px solid ${color}14`, background: 'rgba(0,0,0,0.3)' }}>
        <span style={{ fontSize: T.xs, color: 'var(--ln-text-muted)', letterSpacing: '0.04em' }}>{lines.length} lines</span>
        <button type="button" onClick={copy} style={{ display: 'flex', alignItems: 'center', gap: 3, background: 'none', border: 'none', color: copied ? '#4ade80' : 'var(--ln-text-muted)', fontSize: T.xs, cursor: 'pointer', padding: '1px 4px', borderRadius: 4 }}>
          {copied ? <><Check size={10} /> 已复制</> : <><Clipboard size={10} /> 复制</>}
        </button>
      </div>
      <div style={{ maxHeight: maxH, overflowY: 'auto', fontFamily: 'var(--ln-mono)', fontSize: 10.5, lineHeight: 1.7, color: '#c4b5fd', padding: `${S.sm}px 0`, counterReset: 'line' }}>
        {lines.map((line, i) => (
          <div key={i} style={{ display: 'flex', minHeight: 18, padding: `0 ${S.sm}px` }}>
            <span style={{ width: 32, minWidth: 32, textAlign: 'right', color: 'rgba(255,255,255,0.13)', fontSize: 9, userSelect: 'none', paddingRight: S.sm, flexShrink: 0 }}>{i + 1}</span>
            <span style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', paddingRight: 4 }}>{line || ' '}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function SectionLabel({ color, label }: { color: string; label: string }) {
  return <div style={{ display: 'flex', alignItems: 'center', gap: S.xs + 1, marginBottom: S.xs + 2 }}><span style={{ width: 5, height: 5, borderRadius: 999, background: color, flexShrink: 0 }} /><span style={{ fontSize: T.xs, fontWeight: 600, color, letterSpacing: '0.05em', textTransform: 'uppercase' }}>{label}</span></div>;
}

// ── SpanRow ──
const ROW_H = 34;
const LEFT_W = 320;
const BAR_H = 20;

function SpanRow({ event, maxOff, maxDur, depth, isSelected, expanded, onToggle }: {
  event: TraceEventModel; maxOff: number; maxDur: number; depth: number;
  isSelected: boolean; expanded: boolean; onToggle: () => void;
}) {
  const c = kindColor[event.kind] ?? '#6b7280';
  const Icon = kindIcon[event.kind] ?? FileSearch;
  const leftPct = (event.offsetMs / Math.max(maxOff, 1)) * 100;
  const wPct = Math.max((event.durationMs / Math.max(maxDur, 1)) * 100, 1.5);
  return (
    <div style={{ marginBottom: S.xs }}>
      <div role="button" tabIndex={0} aria-expanded={expanded} onClick={onToggle} onKeyDown={e => { if (e.key === 'Enter') onToggle(); }}
        style={{ display: 'flex', alignItems: 'center', height: ROW_H, cursor: 'pointer', borderRadius: 6, paddingLeft: S.sm,
          background: isSelected ? `${c}0e` : 'transparent', border: isSelected ? `1px solid ${c}35` : '1px solid transparent',
          outline: 'none', transition: 'background 150ms ease, border-color 150ms ease' }}
        onMouseEnter={e => { e.currentTarget.style.background = 'var(--ln-surface-2)'; e.currentTarget.style.borderColor = 'var(--ln-border)'; }}
        onMouseLeave={e => { if (!isSelected) { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.borderColor = 'transparent'; } }}>
        <div style={{ width: LEFT_W, minWidth: LEFT_W, maxWidth: LEFT_W, display: 'flex', alignItems: 'center', gap: 0, paddingLeft: depth * 18 + S.sm, flexShrink: 0 }}>
          {depth > 0 && <span style={{ display: 'inline-block', width: 14, height: ROW_H, position: 'relative', flexShrink: 0, marginRight: 2 }}><span style={{ position: 'absolute', left: 6, top: 0, width: 8, height: ROW_H / 2, borderLeft: `1.5px solid ${c}30`, borderBottom: `1.5px solid ${c}30`, borderRadius: '0 0 0 5px' }} /></span>}
          <span style={{ color: c, flexShrink: 0, display: 'flex', marginRight: 5 }}><Icon size={14} /></span>
          <span style={{ flex: 1, fontSize: T.sm, fontWeight: 500, color: 'var(--ln-text)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', minWidth: 0 }} title={event.title}>{event.title}</span>
          <span style={{ display: 'flex', alignItems: 'center', gap: 3, flexShrink: 0, marginLeft: 5 }}>
            <span style={{ fontSize: T.xs, fontFamily: 'var(--ln-mono)', color: 'var(--ln-text-muted)', whiteSpace: 'nowrap' }}>{fmtMs(event.durationMs)}</span>
            <span style={{ padding: '1px 5px', borderRadius: 3, background: `${c}16`, color: c, fontSize: T.xs, fontWeight: 600, fontFamily: 'var(--ln-mono)', letterSpacing: '0.02em', whiteSpace: 'nowrap' }}>{kindLabel[event.kind]}</span>
            {event.severity !== 'Normal' && <span style={{ padding: '1px 4px', borderRadius: 3, fontSize: T.xs, fontWeight: 600, whiteSpace: 'nowrap', background: event.severity === 'Critical' ? 'rgba(248,113,113,0.14)' : 'rgba(251,191,36,0.14)', color: event.severity === 'Critical' ? '#f87171' : '#fbbf24' }}>{event.severity}</span>}
            {event.evidenceRef && <span style={{ padding: '1px 4px', borderRadius: 3, fontSize: T.xs, fontWeight: 500, whiteSpace: 'nowrap', background: 'rgba(74,222,128,0.14)', color: '#4ade80' }}>证据</span>}
          </span>
        </div>
        <div style={{ flex: 1, position: 'relative', height: BAR_H, minWidth: 40 }}>
          <div style={{ position: 'absolute', top: '50%', left: 0, right: 0, height: 1, background: 'var(--ln-border)', opacity: 0.15 }} />
          <div style={{ position: 'absolute', left: `${leftPct}%`, width: `max(${wPct}%, 3px)`, minWidth: 3, top: 3, bottom: 3, borderRadius: 4, background: c, opacity: isSelected ? 0.92 : 0.65, boxShadow: isSelected ? `0 0 8px ${c}40` : 'none' }} />
        </div>
      </div>

      {/* expand — CSS grid-rows transition, no JS height calc */}
      <div className="trace-expand" data-open={expanded ? 'true' : 'false'}>
        <div style={{ overflow: 'hidden' }}>
          <div style={{ padding: S.lg, borderRadius: S.md, margin: `${S.xs}px 0 ${S.md}px 28px`, background: `linear-gradient(135deg, var(--ln-surface-0), var(--ln-surface-1))`, border: '1px solid var(--ln-border)', display: 'flex', flexDirection: 'column', gap: S.lg, fontSize: T.sm }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: S.sm }}><span style={{ width: 3, height: 18, borderRadius: 2, background: c, flexShrink: 0 }} /><span style={{ fontSize: T.md, fontWeight: 600, color: 'var(--ln-text)' }}>{event.title}</span><span style={{ fontSize: T.xs, fontFamily: 'var(--ln-mono)', color: 'var(--ln-text-muted)', marginLeft: 'auto' }}>T+{fmtMs(event.offsetMs)} · {fmtMs(event.durationMs)}</span></div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))', gap: S.sm }}>
              {[
                ['主体', event.actor],
                ['偏移', `T+${fmtMs(event.offsetMs)}`],
                ['耗时', fmtMs(event.durationMs)],
                ['置信度', `${Math.round(event.confidence * 100)}%`],
                event.operation ? ['操作', event.operation] : null,
                event.evidenceLevel ? ['证据等级', event.evidenceLevel] : null,
              ].filter((x): x is [string, string] => x !== null).map(([label, value]) => (
                <div key={label}>
                  <div style={{ fontSize: T.xs, color: 'var(--ln-text-muted)', letterSpacing: '0.03em' }}>{label}</div>
                  <div style={{ fontSize: T.sm, color: 'var(--ln-text-secondary)', fontFamily: 'var(--ln-mono)', marginTop: 1, overflow: 'hidden', textOverflow: 'ellipsis' }} title={value}>{value}</div>
                </div>
              ))}
            </div>
            {event.prompt && <><SectionLabel color="#a78bfa" label="LLM Prompt" /><CodeBlock color="#a78bfa">{event.prompt}</CodeBlock></>}
            {event.response && <><SectionLabel color="#a78bfa" label="LLM Response" /><CodeBlock color="#a78bfa">{event.response}</CodeBlock></>}
            {event.toolCall && <><SectionLabel color="#fb923c" label="Tool Call" /><CodeBlock color="#fb923c">{event.toolCall}</CodeBlock></>}
            {event.toolResult && <><SectionLabel color="#fb923c" label="Tool Result" /><CodeBlock color="#fb923c">{event.toolResult}</CodeBlock></>}
            {event.processTree && <><SectionLabel color="#60a5fa" label="Process Chain" /><div style={{ background: '#0a0a0f', borderRadius: S.sm, padding: S.sm + 4, border: '1px solid rgba(96,165,250,0.15)', fontFamily: 'var(--ln-mono)', fontSize: 10.5, lineHeight: 1.65, color: '#bfdbfe', whiteSpace: 'pre-wrap' }}>{event.processTree}</div></>}
            {event.rawEvidence && Object.keys(event.rawEvidence).length > 0 && <><SectionLabel color="var(--ln-text-muted)" label="Raw Evidence" /><div style={{ display: 'flex', flexWrap: 'wrap', gap: S.xs }}>{Object.entries(event.rawEvidence).slice(0, 16).map(([k, v]) => <span key={k} style={{ padding: '3px 7px', borderRadius: 4, fontSize: T.xs, background: 'var(--ln-surface-0)', border: '1px solid var(--ln-border)', fontFamily: 'var(--ln-mono)', color: 'var(--ln-text-secondary)', maxWidth: 320, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={String(v)}><span style={{ color: 'var(--ln-text-muted)' }}>{k}</span><span style={{ margin: '0 3px', color: 'var(--ln-border)' }}>=</span><span>{String(v)}</span></span>)}</div></>}
            {event.ruleIds.length > 0 && <><SectionLabel color="#fbbf24" label="Trigger Rules" /><div style={{ display: 'flex', gap: S.xs, flexWrap: 'wrap' }}>{event.ruleIds.map(r => <span key={r} style={{ padding: '3px 7px', borderRadius: 4, fontSize: T.xs, background: 'var(--ln-surface-0)', border: '1px solid var(--ln-border)', fontFamily: 'var(--ln-mono)', color: '#fbbf24' }}>{r}</span>)}</div></>}
          </div>
        </div>
      </div>
    </div>
  );
}

export default function TracesPageClient() {
  const sp = useSearchParams();
  const storeAlerts = useAppStore(s => s.alerts);
  const { selectedValue: routeAlertId, setRouteValue: setHashAlertId } = useHashRouteState({ searchParams: sp, parseValue: parseTraceAlertId, buildHref: buildTraceHref });
  const alerts = storeAlerts.length ? storeAlerts : demoAlerts;
  const currentRunId = useAppStore(s => s.activeRunId);
  const { evidenceItems } = useRunEvidence(currentRunId);
  const runs = useMemo(() => alerts.map(a => buildTraceRunModel(a, evidenceItems)), [alerts, evidenceItems]);
  const [tid, setTid] = useState(routeAlertId ?? runs[0]?.id ?? '');
  const [filter, setFilter] = useState<TraceEventKind | 'all'>('all');
  const active = runs.find(r => r.id === tid) ?? runs[0];
  const [sel, setSel] = useState<TraceEventModel | null>(null);
  const [expandedRows, setExpandedRows] = useState<Set<string>>(new Set());
  const [treeExp, setTreeExp] = useState<Set<string>>(new Set());

  useEffect(() => { const n = routeAlertId ?? runs[0]?.id ?? ''; if (n && n !== tid) setTid(n); }, [routeAlertId, runs, tid]);
  useEffect(() => { setFilter('all'); setSel(null); setExpandedRows(new Set()); }, [active?.id]);

  const tl = useMemo(() => filter === 'all' ? active.timeline : active.timeline.filter(e => e.kind === filter), [filter, active.timeline]);
  const maxOff = Math.max(...active.timeline.map(e => e.offsetMs), 1);
  const maxDur = Math.max(...active.timeline.map(e => Math.max(e.durationMs, 1)), 1);

  // precompute actor groups (once, not per render) for tree + waterfall
  const actorGroups = useMemo(() => {
    const groups = new Map<string, TraceEventModel[]>();
    for (const e of tl) { const a = e.actor || 'unknown'; if (!groups.has(a)) groups.set(a, []); groups.get(a)!.push(e); }
    return groups;
  }, [tl]);

  const spansWithDepth = useMemo(() => {
    const result: { event: TraceEventModel; depth: number; isGroupHead: boolean; groupActor: string }[] = [];
    for (const [actor, events] of actorGroups) {
      const sorted = events.sort((a, b) => a.offsetMs - b.offsetMs);
      result.push({ event: sorted[0], depth: 0, isGroupHead: true, groupActor: actor });
      for (let i = 1; i < sorted.length; i++) result.push({ event: sorted[i], depth: 1, isGroupHead: false, groupActor: actor });
    }
    return result;
  }, [actorGroups]);

  // actor event counts (avoid O(n²) in render)
  const actorCounts = useMemo(() => {
    const m = new Map<string, number>();
    for (const [actor, events] of actorGroups) m.set(actor, events.length);
    return m;
  }, [actorGroups]);

  const evidenceEvents = active.timeline.filter(e => e.evidenceRef);
  const waterfallHeight = 'calc(100dvh - 340px)';
  const toggleRow = (id: string) => setExpandedRows(p => { const n = new Set(p); if (n.has(id)) n.delete(id); else n.add(id); return n; });

  const pageRef = useRef<HTMLDivElement>(null);

  // ── GSAP page entrance ──
  useGSAP(() => {
    gsap.fromTo('.ln-kpi-strip > *', { opacity: 0, y: 10 }, { opacity: 1, y: 0, duration: 0.4, stagger: 0.05, ease: 'power3.out', delay: 0.05, clearProps: 'transform' });
  }, { scope: pageRef });

  if (!active) return <div className="ln-page__inner" />;

  const kpi = [
    { label: 'Span 数', value: active.timeline.length, delta: `${evidenceEvents.length} 有证据`, tone: 'info' as StatusTone },
    { label: '总耗时', value: fmtMs(active.durationMs), delta: `${new Set(tl.map(e => e.actor)).size} 进程源`, tone: 'info' as StatusTone },
    { label: '边界穿越', value: active.boundaryEvents, delta: active.boundaryEvents > 0 ? '⚠️' : '安全', tone: (active.boundaryEvents > 0 ? 'danger' : 'success') as StatusTone },
    { label: '置信度', value: `${Math.round(active.confidence * 100)}%`, delta: active.severity === 'Critical' ? '需复核' : '判定中', tone: sevTone(active.severity) },
    { label: '判决', value: active.decision, delta: active.verdict.headline, tone: (active.decision === 'Unsafe' ? 'danger' : 'info') as StatusTone },
  ];

  return (
    <div className="ln-page__inner" ref={pageRef} style={{ maxWidth: 1500, margin: '0 auto', padding: `${S.lg}px ${S.sm + 6}px` }}>
      <div className="ln-kpi-strip" style={{ marginBottom: S.lg }}>
        {kpi.map(i => <div key={i.label} className="ln-panel ln-metric"><span className="ln-metric__label">{i.label}</span><strong className="ln-metric__value" style={{ fontSize: 20 }}>{i.value}</strong><span className="ln-metric__delta" data-tone={i.tone}>{i.delta}</span></div>)}
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '200px minmax(0, 1fr) 320px', gap: S.lg, alignItems: 'start' }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6, position: 'sticky', top: 44, maxHeight: waterfallHeight, overflowY: 'auto', paddingRight: 4 }}>
          <span className="ln-eyebrow">Trace</span>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 2, maxHeight: 200, overflowY: 'auto' }}>
            {runs.map(run => <button key={run.id} onClick={() => { setTid(run.id); setHashAlertId(run.id); }} style={{ display: 'block', width: '100%', textAlign: 'left', padding: '5px 9px', borderRadius: 5, border: '1px solid var(--ln-border)', background: run.id === tid ? 'var(--ln-accent-soft)' : 'transparent', color: run.id === tid ? 'var(--ln-accent)' : 'var(--ln-text-secondary)', fontSize: T.sm, cursor: 'pointer', fontFamily: 'var(--ln-mono)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', transition: 'all 120ms ease' }} title={run.id}>{run.id}</button>)}
          </div>
          <span className="ln-eyebrow" style={{ marginTop: 10 }}>进程树</span>
          {Array.from(new Set(spansWithDepth.map(s => s.groupActor))).map(actor => (<div key={actor} style={{ fontSize: T.xs }}><div onClick={() => setTreeExp(p => { const n = new Set(p); if (n.has(actor)) n.delete(actor); else n.add(actor); return n; })} style={{ display: 'flex', alignItems: 'center', gap: 5, padding: '4px 7px', borderRadius: 5, cursor: 'pointer', color: 'var(--ln-text-secondary)' }}>{treeExp.has(actor) ? <ChevronDown size={11} /> : <ChevronRight size={11} />}<Server size={11} style={{ color: '#60a5fa' }} /><span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{actor}</span><span style={{ fontSize: 9, color: 'var(--ln-text-muted)', fontFamily: 'var(--ln-mono)' }}>{actorCounts.get(actor) ?? 0}</span></div>{treeExp.has(actor) && (actorGroups.get(actor) ?? []).slice(0, 8).map(e => <div key={e.id} onClick={() => setSel(p => p?.id === e.id ? null : e)} style={{ padding: '3px 5px 3px 24px', cursor: 'pointer', color: sel?.id === e.id ? 'var(--ln-text)' : 'var(--ln-text-muted)', fontSize: T.xs, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', borderRadius: 3, background: sel?.id === e.id ? 'var(--ln-accent-soft)' : 'transparent', transition: 'background 80ms ease' }} title={e.title}>{e.title}</div>)}</div>))}
          <span className="ln-eyebrow" style={{ marginTop: 10 }}>过滤</span>
          <div style={{ display: 'flex', gap: 3, flexWrap: 'wrap' }}>{FILTERS.map(f => <button key={f} onClick={() => setFilter(f)} style={{ padding: '3px 7px', borderRadius: 4, border: '1px solid var(--ln-border)', background: filter === f ? `${kindColor[f as TraceEventKind] ?? 'var(--ln-accent)'}16` : 'transparent', color: filter === f ? (kindColor[f as TraceEventKind] ?? 'var(--ln-accent)') : 'var(--ln-text-muted)', fontSize: T.xs, fontWeight: filter === f ? 600 : 400, cursor: 'pointer', transition: 'all 80ms ease' }}>{f === 'all' ? '全部' : kindLabel[f as TraceEventKind]}</button>)}</div>
        </div>
        <SectionPanel eyebrow="责任链路" title={`${tl.length} 个事件`} meta={fmtMs(active.durationMs)} bodyClassName="ln-panel-body">
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: S.sm, fontSize: T.xs, color: 'var(--ln-text-muted)', fontFamily: 'var(--ln-mono)', paddingLeft: LEFT_W + S.sm }}>{['T+0', `T+${Math.round(maxOff * 0.25)}ms`, `T+${Math.round(maxOff * 0.5)}ms`, `T+${Math.round(maxOff * 0.75)}ms`, `T+${Math.round(maxOff)}ms`].map(t => <span key={t}>{t}</span>)}</div>
          <div style={{ height: waterfallHeight, overflow: 'hidden' }}>
            {tl.length === 0 ? (
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: 120, color: 'var(--ln-text-muted)', fontSize: T.sm }}>当前筛选条件无匹配事件</div>
            ) : (
              <VirtualList
                items={spansWithDepth}
                estimateSize={48}
                dynamic
                renderRow={({ event, depth, isGroupHead, groupActor }) => (
                  <div key={event.id}>
                    {isGroupHead && <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '3px 10px', marginTop: S.sm, marginBottom: 2, borderRadius: 4, background: 'var(--ln-surface-0)', border: '1px solid var(--ln-border)' }}><Server size={11} style={{ color: '#60a5fa' }} /><span style={{ fontSize: T.sm, fontWeight: 600, fontFamily: 'var(--ln-mono)', color: 'var(--ln-text)' }}>{groupActor}</span></div>}
                    <SpanRow event={event} maxOff={maxOff} maxDur={maxDur} depth={isGroupHead ? 0 : depth} isSelected={sel?.id === event.id} expanded={expandedRows.has(event.id)} onToggle={() => toggleRow(event.id)} />
                  </div>
                )}
              />
            )}
          </div>
        </SectionPanel>
        <div className="trace-detail-panel" style={{ display: 'flex', flexDirection: 'column', gap: S.lg, position: 'sticky', top: 44 }}>
          {sel ? (<SectionPanel eyebrow={kindLabel[sel.kind]} title={sel.title} meta={sel.operation ?? undefined} bodyClassName="ln-panel-body"><div style={{ display: 'flex', flexDirection: 'column', gap: S.sm, maxHeight: 420, overflowY: 'auto' }}><div style={{ display: 'flex', gap: S.xs + 2, flexWrap: 'wrap' }}><StatusBadge tone={sevTone(sel.severity)}>{sel.severity}</StatusBadge>{sel.evidenceRef && <StatusBadge tone="success">证据链</StatusBadge>}{sel.evidenceLevel && <span style={{ fontSize: T.sm, color: 'var(--ln-accent)', background: 'var(--ln-accent-soft)', padding: '2px 7px', borderRadius: 5 }}>{sel.evidenceLevel}</span>}</div><div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: S.xs, fontSize: T.xs }}>{['主体', '偏移', '耗时', '置信度'].map((k, i) => { const v = [sel.actor, `T+${fmtMs(sel.offsetMs)}`, fmtMs(sel.durationMs), `${Math.round(sel.confidence * 100)}%`][i]; return <div key={k}><span className="ln-panel__meta">{k}</span><div style={{ color: 'var(--ln-text)', fontFamily: 'var(--ln-mono)', fontSize: T.xs, marginTop: 1 }}>{v}</div></div>; })}</div>{sel.prompt && <><SectionLabel color="#a78bfa" label="Prompt" /><CodeBlock color="#a78bfa" maxH={160}>{sel.prompt}</CodeBlock></>}{sel.response && <><SectionLabel color="#a78bfa" label="Response" /><CodeBlock color="#a78bfa" maxH={160}>{sel.response}</CodeBlock></>}{sel.toolCall && <><SectionLabel color="#fb923c" label="Tool Call" /><CodeBlock color="#fb923c" maxH={140}>{sel.toolCall}</CodeBlock></>}{sel.toolResult && <><SectionLabel color="#fb923c" label="Tool Result" /><CodeBlock color="#fb923c" maxH={140}>{sel.toolResult}</CodeBlock></>}{sel.material && sel.material !== 'No linked material' && <div><span className="ln-panel__meta">物化路径</span><div style={{ fontSize: T.xs, color: 'var(--ln-text-secondary)', fontFamily: 'var(--ln-mono)', marginTop: 1 }}>{sel.material}</div></div>}{sel.ruleIds.length > 0 && <div><span className="ln-panel__meta">触发规则</span><div style={{ display: 'flex', gap: 3, flexWrap: 'wrap', marginTop: 3 }}>{sel.ruleIds.map(r => <span key={r} style={{ padding: '2px 5px', borderRadius: 3, background: 'var(--ln-surface-0)', border: '1px solid var(--ln-border)', fontSize: 9, fontFamily: 'var(--ln-mono)', color: '#fbbf24' }}>{r}</span>)}</div></div>}<button type="button" onClick={() => setSel(null)} style={{ background: 'none', border: '1px solid var(--ln-border)', borderRadius: 7, padding: '6px 12px', color: 'var(--ln-text-muted)', fontSize: T.sm, cursor: 'pointer', marginTop: S.xs }}><X size={13} style={{ verticalAlign: 'middle', marginRight: 5 }} />关闭</button></div></SectionPanel>) : (<SectionPanel eyebrow="事件详情" title="选择事件" bodyClassName="ln-panel-body"><p style={{ fontSize: T.sm, color: 'var(--ln-text-muted)', margin: 0, lineHeight: 1.6 }}>点击链路中的事件行展开完整上下文，或从左侧进程树选择查看详情。</p></SectionPanel>)}
          <SectionPanel eyebrow="快速操作" title="调查入口" bodyClassName="ln-panel-body"><div style={{ display: 'flex', flexDirection: 'column', gap: S.xs + 2 }}><Link href={buildInvestigationHref(tid)} className="ln-btn" style={{ justifyContent: 'space-between', textDecoration: 'none' }}>进入告警详情<ArrowRight size={14} /></Link><Link href="/overview" className="ln-btn" style={{ justifyContent: 'space-between', textDecoration: 'none' }}>返回仪表盘<ArrowRight size={14} /></Link></div></SectionPanel>
        </div>
      </div>
    </div>
  );
}
