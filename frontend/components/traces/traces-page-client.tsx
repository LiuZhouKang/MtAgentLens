'use client';

import { useDeferredValue, useEffect, useMemo, useRef, useState } from 'react';
import { useGSAP } from '@gsap/react';
import gsap from 'gsap';
import { useSearchParams } from 'next/navigation';
import { LiveEventTail } from '@/components/live/live-event-tail';
import { LiveRunningBadge } from '@/components/live/live-running-badge';
import {
  CheckCircle2,
  Check,
  Clipboard,
  ChevronDown,
  ChevronRight,
  FileSearch,
  GitBranch,
  Network,
  ShieldAlert,
  Sparkles,
  TerminalSquare,
  X,
} from 'lucide-react';
import { SectionPanel } from '@/components/ui/linear/section-panel';
import type { StatusTone } from '@/components/ui/linear/status-badge';
import { MetricCard } from '@/components/ui/linear/metric-card';
import AgentAvatar from '@/components/ui/agent-avatar';
import VirtualList from '@/components/ui/virtual-list';
import { decisionLabel, matchLevelLabel } from '@/lib/investigation-labels';
import { agentColor, shortAgentId } from '@/lib/agent-identity';
import { useAppStore } from '@/lib/store';
import { buildTraceHref, parseTraceAlertId } from '@/lib/trace-route';
import {
  buildTraceRunModel,
  summarizeActorEvents,
  type TraceEventKind,
  type TraceEventModel,
  type TraceRunModel,
} from '@/lib/trace-workbench';
import { useRunEvidence } from '@/lib/use-run-evidence';
import EvidenceNotice from '@/components/investigation/evidence-notice';
import { useHashRouteState } from '@/lib/use-hash-route-state';

// ── tokens ──
// Stable empty list: a fresh `[]` each render would invalidate every memo.
const NO_EVENTS: TraceEventModel[] = [];

const T = { xs: 9.5, sm: 10.5, md: 12, lg: 14, xl: 16, h: 20 };
const S = { xs: 2, sm: 4, md: 8, lg: 12, xl: 16 };

const kindIcon: Record<TraceEventKind, typeof FileSearch> = {
  agent: GitBranch,
  llm: Sparkles,
  tool: TerminalSquare,
  file: FileSearch,
  network: Network,
  boundary: ShieldAlert,
  verdict: CheckCircle2,
};
const kindLabel: Record<TraceEventKind, string> = {
  agent: '主体',
  llm: 'LLM',
  tool: '工具',
  file: '文件',
  network: '网络',
  boundary: '边界',
  verdict: '判定',
};
const kindColor: Record<TraceEventKind, string> = {
  agent: 'var(--ln-info)',
  llm: 'var(--ln-accent)',
  tool: 'var(--ln-success)',
  file: 'var(--ln-text-muted)',
  network: 'var(--ln-info)',
  boundary: 'var(--ln-danger)',
  verdict: 'var(--ln-warning)',
};
/** 追踪点 severity → ln token 色（左侧列表点） */
const sevDotColor: Record<string, string> = {
  Critical: 'var(--ln-danger)',
  Warning: 'var(--ln-warning)',
  Info: 'var(--ln-info)',
};
const FILTERS: Array<TraceEventKind | 'all'> = [
  'all',
  'agent',
  'llm',
  'tool',
  'file',
  'network',
  'boundary',
  'verdict',
];

function fmtMs(ms: number) {
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`;
}
/** 墙钟毫秒 → HH:MM:SS（span 时钟） */
function fmtClock(wallMs: number): string {
  return new Date(wallMs).toISOString().slice(11, 19);
}
function sevTone(s: TraceRunModel['severity'] | 'Normal'): StatusTone {
  if (s === 'Critical') return 'danger';
  if (s === 'Warning') return 'warning';
  return s === 'Info' ? 'info' : 'success';
}

// ── CodeBlock ──
function CodeBlock({
  children,
  color = 'var(--ln-border)',
  maxH = 300,
}: {
  children: string;
  color?: string;
  maxH?: number;
}) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    navigator.clipboard.writeText(children).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };
  const lines = children.split('\n');
  return (
    <div
      style={{
        background: '#0a0a0f',
        borderRadius: S.sm + 2,
        border: `1px solid ${color}22`,
        overflow: 'hidden',
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: `${S.xs}px ${S.sm + 2}px`,
          borderBottom: `1px solid ${color}14`,
          background: 'rgba(0,0,0,0.3)',
        }}
      >
        <span style={{ fontSize: T.xs, color: 'var(--ln-text-muted)', letterSpacing: '0.04em' }}>
          {lines.length} lines
        </span>
        <button
          type="button"
          onClick={copy}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 3,
            background: 'none',
            border: 'none',
            color: copied ? '#4ade80' : 'var(--ln-text-muted)',
            fontSize: T.xs,
            cursor: 'pointer',
            padding: '1px 4px',
            borderRadius: 4,
          }}
        >
          {copied ? (
            <>
              <Check size={10} /> 已复制
            </>
          ) : (
            <>
              <Clipboard size={10} /> 复制
            </>
          )}
        </button>
      </div>
      <div
        style={{
          maxHeight: maxH,
          overflowY: 'auto',
          fontFamily: 'var(--ln-mono)',
          fontSize: 10.5,
          lineHeight: 1.7,
          color: '#c4b5fd',
          padding: `${S.sm}px 0`,
          counterReset: 'line',
        }}
      >
        {lines.map((line, i) => (
          <div key={i} style={{ display: 'flex', minHeight: 18, padding: `0 ${S.sm}px` }}>
            <span
              style={{
                width: 32,
                minWidth: 32,
                textAlign: 'right',
                color: 'rgba(255,255,255,0.13)',
                fontSize: 9,
                userSelect: 'none',
                paddingRight: S.sm,
                flexShrink: 0,
              }}
            >
              {i + 1}
            </span>
            <span style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', paddingRight: 4 }}>
              {line || ' '}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

function SectionLabel({ color, label }: { color: string; label: string }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: S.xs + 1, marginBottom: S.xs + 2 }}>
      <span style={{ width: 5, height: 5, borderRadius: 999, background: color, flexShrink: 0 }} />
      <span
        style={{
          fontSize: T.xs,
          fontWeight: 600,
          color,
          letterSpacing: '0.05em',
          textTransform: 'uppercase',
        }}
      >
        {label}
      </span>
    </div>
  );
}

// ── SpanRow ──
const LEFT_W = 320;

function SpanRow({
  event,
  maxOff,
  maxDur,
  depth,
  isSelected,
  onSelect,
  wallStartMs,
}: {
  event: TraceEventModel;
  maxOff: number;
  maxDur: number;
  depth: number;
  isSelected: boolean;
  onSelect: () => void;
  wallStartMs: number;
}) {
  const c = kindColor[event.kind] ?? '#6b7280';
  const Icon = kindIcon[event.kind] ?? FileSearch;
  const leftPct = (event.offsetMs / Math.max(maxOff, 1)) * 100;
  const wPct = Math.max((event.durationMs / Math.max(maxDur, 1)) * 100, 0.6);
  // 真实时钟 HH:MM:SS = 时间线起点墙钟 + 相对偏移
  const clock = new Date(wallStartMs + event.offsetMs).toISOString().slice(11, 19);
  const sevTone =
    event.severity === 'Critical'
      ? ('danger' as const)
      : event.severity === 'Warning'
        ? ('warning' as const)
        : ('info' as const);
  return (
    <div
      role="button"
      tabIndex={0}
      aria-pressed={isSelected}
      onClick={onSelect}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onSelect();
        }
      }}
      className="ln-tr-row"
      data-selected={isSelected ? 'true' : 'false'}
      style={{ paddingLeft: depth * 18 + 6 }}
    >
      {/* 左侧：图标 + kind 中文 + 进程身份 + 描述 + actor */}
      <div className="ln-tr-left">
        <Icon size={14} style={{ color: c, flexShrink: 0 }} />
        <span className="ln-tr-kind" style={{ color: c }}>
          {kindLabel[event.kind]}
        </span>
        {event.processComm && (
          <span className="ln-tr-proc" title={event.processAgent ?? undefined}>
            {event.processComm}
            {event.pid != null ? ` · ${event.pid}` : ''}
          </span>
        )}
        <span className="ln-tr-title" title={event.title}>
          {event.title}
        </span>
        <span className="ln-tr-actor" title={event.actor} style={{ background: agentColor(event.actor) }} />
      </div>
      {/* 瀑布区：时间线背景 + span 条 + 元信息 */}
      <div className="ln-tr-body">
        <span className="ln-tr-track" aria-hidden />
        <span
          className="ln-tr-bar"
          style={{ left: `${leftPct}%`, width: `max(${wPct}%, 3px)`, background: c }}
        />
        <span className="ln-tr-meta">
          {event.severity !== 'Normal' && (
            <span className="ln-chip" data-tone={sevTone}>
              {event.severity}
            </span>
          )}
          {event.evidenceRef && (
            <span className="ln-chip" data-tone="success">
              证据
            </span>
          )}
          <span className="ln-tr-clock">{clock}</span>
          <span className="ln-tr-dur">{fmtMs(event.durationMs)}</span>
        </span>
      </div>
    </div>
  );
}

export default function TracesPageClient() {
  const sp = useSearchParams();
  const storeAlerts = useAppStore((s) => s.alerts);
  const isLive = useAppStore((s) => s.appState === 'live');
  const setPageReady = useAppStore((s) => s.setPageReady);
  const { selectedValue: routeAlertId, setRouteValue: setHashAlertId } = useHashRouteState({
    searchParams: sp,
    parseValue: parseTraceAlertId,
    buildHref: buildTraceHref,
  });
  // Deferred — big runs keep the first paint free; trace models fill in as a
  // low-priority update behind the skeleton.
  const deferredAlerts = useDeferredValue(storeAlerts);
  // Never substitute demo alerts for a real run with no findings: a
  // fabricated trace is worse than an empty state.
  const alerts = deferredAlerts;
  const currentRunId = useAppStore((s) => s.activeRunId);
  const { evidenceItems, evidenceLoading, evidenceError } = useRunEvidence(currentRunId);
  const dbAssets = useAppStore((s) => s.dbAssets);
  const storeActions = useAppStore((s) => s.actions);
  const storeIntents = useAppStore((s) => s.intents);
  const runs = useMemo(
    () =>
      alerts.map((a) =>
        buildTraceRunModel(
          a,
          evidenceItems,
          dbAssets?.processEvents ?? [],
          storeActions,
          storeIntents,
          dbAssets?.policyEvaluations ?? [],
          dbAssets?.pollutionEvents ?? [],
        ),
      ),
    [
      alerts,
      evidenceItems,
      dbAssets?.processEvents,
      dbAssets?.policyEvaluations,
      dbAssets?.pollutionEvents,
      storeActions,
      storeIntents,
    ],
  );
  const [tid, setTid] = useState(routeAlertId ?? runs[0]?.id ?? '');
  const [filter, setFilter] = useState<TraceEventKind | 'all'>('all');
  const active = runs.find((r) => r.id === tid) ?? runs[0];
  // 页面内容就绪信号（供布局层协调加载层）：active 就绪即渲染追踪工作台。
  // （数据加载期由布局层 dataLoading 门保持覆盖，避免 demo 回退 → 真实数据切换跳变。）
  useEffect(() => {
    setPageReady(active != null);
  }, [active, setPageReady]);
  const [sel, setSel] = useState<TraceEventModel | null>(null);
  const [treeExp, setTreeExp] = useState<Set<string>>(new Set());

  useEffect(() => {
    const n = routeAlertId ?? runs[0]?.id ?? '';
    if (n && n !== tid) setTid(n);
  }, [routeAlertId, runs, tid]);
  useEffect(() => {
    setFilter('all');
    setSel(null);
  }, [active?.id]);

  // `active` is undefined until a run with a timeline exists (e.g. a run that
  // produced zero alerts). Reading `active.timeline` here crashed the page on
  // those runs; the null guard below runs after these hooks.
  const timeline = active?.timeline ?? NO_EVENTS;
  const tl = useMemo(
    () => (filter === 'all' ? timeline : timeline.filter((e) => e.kind === filter)),
    [filter, timeline],
  );
  const maxOff = Math.max(...timeline.map((e) => e.offsetMs), 1);
  const maxDur = Math.max(...timeline.map((e) => Math.max(e.durationMs, 1)), 1);

  // precompute actor groups (once, not per render) for tree + waterfall
  const actorGroups = useMemo(() => {
    const groups = new Map<string, TraceEventModel[]>();
    for (const e of tl) {
      const a = e.actor || 'unknown';
      if (!groups.has(a)) groups.set(a, []);
      groups.get(a)!.push(e);
    }
    return groups;
  }, [tl]);

  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set());
  const toggleGroup = (actor: string) =>
    setCollapsedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(actor)) next.delete(actor);
      else next.add(actor);
      return next;
    });

  const spansWithDepth = useMemo(() => {
    const result: {
      event: TraceEventModel;
      depth: number;
      isGroupHead: boolean;
      groupActor: string;
    }[] = [];
    for (const [actor, events] of actorGroups) {
      const sorted = events.sort((a, b) => a.offsetMs - b.offsetMs);
      result.push({ event: sorted[0], depth: 0, isGroupHead: true, groupActor: actor });
      // 折叠的服务组：只保留组头（顶级展开/折叠交互）
      if (collapsedGroups.has(actor)) continue;
      for (let i = 1; i < sorted.length; i++)
        result.push({ event: sorted[i], depth: 1, isGroupHead: false, groupActor: actor });
    }
    return result;
  }, [actorGroups, collapsedGroups]);

  // actor event counts (avoid O(n²) in render)
  const actorCounts = useMemo(() => {
    const m = new Map<string, number>();
    for (const [actor, events] of actorGroups) m.set(actor, events.length);
    return m;
  }, [actorGroups]);

  // per-actor kind distribution, ordered like the filter strip — powers the
  // group-header badges so you can see at a glance what kind of work each agent did.
  const actorKindDots = useMemo(() => {
    const m = new Map<string, Array<[TraceEventKind, number]>>();
    for (const [actor, events] of actorGroups) {
      const counts = new Map<TraceEventKind, number>();
      for (const e of events) counts.set(e.kind, (counts.get(e.kind) ?? 0) + 1);
      m.set(
        actor,
        [...counts.entries()].sort((a, b) => FILTERS.indexOf(a[0]) - FILTERS.indexOf(b[0])),
      );
    }
    return m;
  }, [actorGroups]);

  const evidenceEvents = timeline.filter((e) => e.evidenceRef);
  const waterfallHeight = 'calc(100dvh - 340px)';

  const pageRef = useRef<HTMLDivElement>(null);

  // ── GSAP 顶级入场：KPI 翻牌 → 组头错峰 → 瀑布行左滑 ──
  useGSAP(
    () => {
      if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
      const tl = gsap.timeline({ defaults: { ease: 'power3.out' } });
      tl.fromTo(
        '.ln-kpi-strip > *',
        { opacity: 0, y: 10 },
        { opacity: 1, y: 0, duration: 0.4, stagger: 0.05, clearProps: 'transform' },
        0,
      );
      tl.fromTo(
        '.ln-tr-group-head',
        { opacity: 0, y: 8 },
        { opacity: 1, y: 0, duration: 0.35, stagger: 0.06, clearProps: 'transform' },
        0.15,
      );
      tl.fromTo(
        '.ln-tr-row',
        { opacity: 0, x: -10 },
        { opacity: 1, x: 0, duration: 0.35, stagger: 0.02, clearProps: 'transform' },
        0.25,
      );
    },
    { scope: pageRef, dependencies: [active?.id, filter, collapsedGroups] },
  );

  if (!active) {
    // Data loading is covered by the layout's data-loading overlay. Reaching
    // this branch means loading finished with nothing to show; an endless
    // spinner would claim work is still happening.
    return (
      <div className="ln-page__inner" style={{ maxWidth: 1500, margin: '0 auto' }}>
        <EvidenceNotice loading={evidenceLoading} error={evidenceError} />
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
          <h2 style={{ fontSize: 16, fontWeight: 600 }}>暂无追踪数据</h2>
          <p className="ln-panel__meta" style={{ maxWidth: 460 }}>
            当前数据源没有可解析的告警与证据。导入 responsibility.db，或连接一个实时 run 后，
            责任链会显示在这里。
          </p>
        </div>
      </div>
    );
  }

  const kpi = [
    {
      label: 'Span 数',
      value: active.timeline.length,
      delta: `${evidenceEvents.length} 有证据`,
      deltaTone: 'info' as StatusTone,
    },
    {
      label: '总耗时',
      value: fmtMs(active.durationMs),
      delta: `${new Set(tl.map((e) => e.actor)).size} 进程源`,
      deltaTone: 'info' as StatusTone,
    },
    {
      label: '边界穿越',
      value: active.boundaryEvents,
      delta: active.boundaryEvents > 0 ? '需关注' : '安全',
      deltaTone: (active.boundaryEvents > 0 ? 'danger' : 'success') as StatusTone,
    },
    {
      label: '置信度',
      value: `${Math.round(active.confidence * 100)}%`,
      delta: active.severity === 'Critical' ? '需复核' : '判定中',
      deltaTone: sevTone(active.severity),
    },
    {
      label: '判决',
      value: decisionLabel(active.decision).label,
      delta: active.verdict.headline,
      deltaTone: (active.decision === 'Unsafe' ? 'danger' : 'info') as StatusTone,
    },
  ];

  return (
    <div
      className="ln-page__inner"
      ref={pageRef}
      style={{ maxWidth: 1500, margin: '0 auto', padding: `${S.lg}px ${S.sm + 6}px`, position: 'relative' }}
    >
      <div style={{ position: 'absolute', top: S.sm, right: S.sm, zIndex: 5 }}>
        <LiveRunningBadge />
      </div>
      {/* 实时原始事件流入口（直播中自动跟随） */}
      {isLive && (
        <div style={{ height: 220, marginBottom: S.lg, overflow: 'hidden' }}>
          <LiveEventTail />
        </div>
      )}
      <div className="ln-kpi-strip" style={{ marginBottom: S.lg }}>
        {kpi.map((k) => (
          <MetricCard
            key={k.label}
            label={k.label}
            value={k.value}
            delta={k.delta}
            deltaTone={k.deltaTone}
          />
        ))}
      </div>
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'minmax(160px, 200px) minmax(0, 1fr) minmax(280px, 320px)',
          gap: S.lg,
          alignItems: 'start',
          overflowX: 'auto',
        }}
      >
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            gap: 6,
            position: 'sticky',
            top: 44,
            maxHeight: waterfallHeight,
            overflowY: 'auto',
            paddingRight: 4,
          }}
        >
          <span className="ln-eyebrow">追踪</span>
          <div
            style={{
              display: 'flex',
              flexDirection: 'column',
              gap: 3,
              maxHeight: 200,
              overflowY: 'auto',
            }}
          >
            {runs.map((run) => (
              <button
                key={run.id}
                type="button"
                className="ln-tr-trace-btn"
                data-active={run.id === tid ? 'true' : 'false'}
                aria-pressed={run.id === tid}
                onClick={() => {
                  setTid(run.id);
                  setHashAlertId(run.id);
                }}
                title={run.title}
              >
                <span className="ln-tr-trace-row">
                  <span
                    className="ln-tr-trace-dot"
                    style={{ background: sevDotColor[run.severity] ?? 'var(--ln-info)' }}
                  />
                  <span className="ln-tr-trace-id">{shortAgentId(run.id)}</span>
                  <span className="ln-tr-trace-conf">{Math.round(run.confidence * 100)}%</span>
                </span>
                <span className="ln-tr-trace-title">{run.title}</span>
              </button>
            ))}
          </div>
          <span className="ln-eyebrow" style={{ marginTop: 10 }}>
            进程树
          </span>
          {Array.from(new Set(spansWithDepth.map((s) => s.groupActor))).map((actor) => (
            <div key={actor} style={{ fontSize: T.xs }}>
              <div
                role="button"
                tabIndex={0}
                aria-expanded={treeExp.has(actor)}
                onClick={() =>
                  setTreeExp((p) => {
                    const n = new Set(p);
                    if (n.has(actor)) n.delete(actor);
                    else n.add(actor);
                    return n;
                  })
                }
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ' || e.key === 'Spacebar') {
                    e.preventDefault();
                    setTreeExp((p) => {
                      const n = new Set(p);
                      if (n.has(actor)) n.delete(actor);
                      else n.add(actor);
                      return n;
                    });
                  }
                }}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 5,
                  padding: '4px 7px',
                  borderRadius: 5,
                  cursor: 'pointer',
                  color: 'var(--ln-text-secondary)',
                }}
              >
                {treeExp.has(actor) ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
                <AgentAvatar agentId={actor} size={14} />
                <span
                  style={{
                    flex: 1,
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {shortAgentId(actor)}
                </span>
                <span
                  style={{
                    fontSize: 9,
                    color: 'var(--ln-text-muted)',
                    fontFamily: 'var(--ln-mono)',
                  }}
                >
                  {actorCounts.get(actor) ?? 0}
                </span>
              </div>
              {treeExp.has(actor) &&
                (actorGroups.get(actor) ?? []).slice(0, 8).map((e) => (
                  <div
                    key={e.id}
                    onClick={() => setSel((p) => (p?.id === e.id ? null : e))}
                    style={{
                      padding: '3px 5px 3px 24px',
                      cursor: 'pointer',
                      color: sel?.id === e.id ? 'var(--ln-text)' : 'var(--ln-text-muted)',
                      fontSize: T.xs,
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                      borderRadius: 3,
                      background: sel?.id === e.id ? 'var(--ln-accent-soft)' : 'transparent',
                      transition: 'background 80ms ease',
                    }}
                    title={e.title}
                  >
                    {e.title}
                  </div>
                ))}
            </div>
          ))}
          <span className="ln-eyebrow" style={{ marginTop: 10 }}>
            过滤
          </span>
          <div style={{ display: 'flex', gap: 3, flexWrap: 'wrap' }}>
            {FILTERS.map((f) => {
              const isAll = f === 'all';
              const count = isAll
                ? active.timeline.length
                : (active.kindCounts[f as TraceEventKind] ?? 0);
              const isActive = filter === f;
              const isEmpty = !isAll && count === 0;
              const color = isAll
                ? 'var(--ln-accent)'
                : (kindColor[f as TraceEventKind] ?? 'var(--ln-accent)');
              return (
                <button
                  key={f}
                  onClick={() => setFilter(f)}
                  disabled={isEmpty}
                  aria-label={
                    isAll ? `筛选 全部 ${count}` : `筛选 ${kindLabel[f as TraceEventKind]} ${count}`
                  }
                  title={
                    isEmpty ? '此类型暂无事件' : `${kindLabel[f as TraceEventKind]} × ${count}`
                  }
                  aria-pressed={isActive}
                  style={{
                    padding: '3px 7px',
                    borderRadius: 4,
                    border: '1px solid var(--ln-border)',
                    background: isActive ? `${color}16` : 'transparent',
                    color: isActive ? color : 'var(--ln-text-muted)',
                    fontSize: T.xs,
                    fontWeight: isActive ? 600 : 400,
                    cursor: isEmpty ? 'not-allowed' : 'pointer',
                    opacity: isEmpty ? 0.4 : 1,
                    fontFamily: 'var(--ln-mono)',
                    transition: 'all 80ms ease',
                  }}
                >
                  {isAll ? '全部' : kindLabel[f as TraceEventKind]}{' '}
                  <span style={{ opacity: 0.72 }}>{count}</span>
                </button>
              );
            })}
          </div>
        </div>
        <SectionPanel
          eyebrow="责任链路"
          title={`${tl.length} 个事件`}
          meta={fmtMs(active.durationMs)}
          bodyClassName="ln-panel-body"
        >
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              marginBottom: 4,
              paddingBottom: 4,
              borderBottom: '1px solid var(--ln-border)',
              fontSize: 9.5,
              color: 'var(--ln-text-muted)',
              fontFamily: 'var(--ln-mono)',
              paddingLeft: LEFT_W + S.sm,
            }}
          >
            {[
              'T+0',
              `T+${fmtMs(Math.round(maxOff * 0.25))}`,
              `T+${fmtMs(Math.round(maxOff * 0.5))}`,
              `T+${fmtMs(Math.round(maxOff * 0.75))}`,
              `T+${fmtMs(maxOff)}`,
            ].map((t) => (
              <span key={t}>{t}</span>
            ))}
          </div>
          <div style={{ height: waterfallHeight, overflow: 'hidden' }}>
            {tl.length === 0 ? (
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  height: 120,
                  color: 'var(--ln-text-muted)',
                  fontSize: T.sm,
                }}
              >
                当前筛选条件无匹配事件
              </div>
            ) : (
              <VirtualList
                items={spansWithDepth}
                estimateSize={48}
                dynamic
                renderRow={({ event, depth, isGroupHead, groupActor }) => (
                  <div key={event.id}>
                    {isGroupHead &&
                      (() => {
                        const role = active.actorRoles[groupActor];
                        const summary = summarizeActorEvents(actorGroups.get(groupActor) ?? []);
                        return (
                          <div
                            className="ln-tr-group-head"
                            role="button"
                            tabIndex={0}
                            aria-expanded={!collapsedGroups.has(groupActor)}
                            onClick={() => toggleGroup(groupActor)}
                            onKeyDown={(e) => {
                              if (e.key === 'Enter' || e.key === ' ') {
                                e.preventDefault();
                                toggleGroup(groupActor);
                              }
                            }}
                            style={{
                              display: 'flex',
                              alignItems: 'center',
                              gap: 6,
                              padding: '4px 10px',
                              marginTop: S.sm,
                              marginBottom: 2,
                              borderRadius: 6,
                              background: 'var(--ln-surface-0)',
                              border: '1px solid var(--ln-border)',
                              borderLeft: `2px solid ${agentColor(groupActor)}`,
                              cursor: 'pointer',
                              transition: 'border-color 150ms ease, background 150ms ease',
                            }}
                          >
                            <ChevronDown
                              size={12}
                              style={{
                                color: 'var(--ln-text-muted)',
                                flexShrink: 0,
                                transform: collapsedGroups.has(groupActor) ? 'rotate(-90deg)' : 'none',
                                transition: 'transform 150ms ease',
                              }}
                            />
                            <AgentAvatar agentId={groupActor} size={18} />
                            <span
                              style={{
                                fontSize: T.sm,
                                fontWeight: 600,
                                fontFamily: 'var(--ln-mono)',
                                color: agentColor(groupActor),
                                overflow: 'hidden',
                                textOverflow: 'ellipsis',
                                whiteSpace: 'nowrap',
                              }}
                            >
                              {shortAgentId(groupActor)}
                            </span>
                            {role && (
                              <span
                                style={{
                                  padding: '1px 7px',
                                  borderRadius: 999,
                                  fontSize: 9,
                                  background: 'rgba(255,255,255,0.05)',
                                  border: '1px solid rgba(255,255,255,0.08)',
                                  color: '#d0d6e0',
                                  fontFamily: 'var(--ln-mono)',
                                  whiteSpace: 'nowrap',
                                }}
                              >
                                {role}
                              </span>
                            )}
                            <span
                              style={{
                                display: 'flex',
                                alignItems: 'center',
                                gap: 4,
                                marginLeft: 'auto',
                                flexWrap: 'wrap',
                                justifyContent: 'flex-end',
                              }}
                            >
                              {(actorKindDots.get(groupActor) ?? [])
                                .filter(([, n]) => n > 0)
                                .map(([kind, n], idx) => (
                                  <span
                                    key={kind}
                                    title={`${groupActor} · ${kindLabel[kind]} ${n}`}
                                    style={{
                                      display: 'inline-flex',
                                      alignItems: 'center',
                                      gap: 3,
                                      fontSize: 9,
                                      fontFamily: 'var(--ln-mono)',
                                      color: kindColor[kind],
                                      whiteSpace: 'nowrap',
                                    }}
                                  >
                                    {idx > 0 && (
                                      <span style={{ color: 'var(--ln-text-muted)' }}>·</span>
                                    )}
                                    <span
                                      style={{
                                        width: 4,
                                        height: 4,
                                        borderRadius: 999,
                                        background: kindColor[kind],
                                        flexShrink: 0,
                                      }}
                                    />
                                    {kindLabel[kind]} {n}
                                  </span>
                                ))}
                              <span
                                style={{
                                  fontSize: 9,
                                  fontFamily: 'var(--ln-mono)',
                                  color: 'var(--ln-text-muted)',
                                }}
                              >
                                共 {summary.spans} 事件
                              </span>
                            </span>
                            {summary.highestSeverity !== 'Normal' && (
                              <span
                                style={{
                                  width: 6,
                                  height: 6,
                                  borderRadius: 999,
                                  background:
                                    summary.highestSeverity === 'Critical' ? '#dc2626' : '#eab308',
                                  flexShrink: 0,
                                }}
                                title={`最高 ${summary.highestSeverity}`}
                              />
                            )}
                          </div>
                        );
                      })()}
                    <SpanRow
                      event={event}
                      maxOff={maxOff}
                      maxDur={maxDur}
                      depth={isGroupHead ? 0 : depth}
                      isSelected={sel?.id === event.id}
                      onSelect={() => setSel(event)}
                      wallStartMs={active.startWallMs}
                    />
                  </div>
                )}
              />
            )}
          </div>
        </SectionPanel>
        <div
          className="trace-detail-panel"
          style={{
            display: 'flex',
            flexDirection: 'column',
            gap: S.lg,
            position: 'sticky',
            top: 44,
          }}
        >
          {sel ? (
            <SectionPanel
              eyebrow={kindLabel[sel.kind]}
              title={sel.title}
              meta={
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                  {sel.operation && (
                    <span className="ln-chip" data-tone="muted" title={sel.operation}>
                      {sel.operation}
                    </span>
                  )}
                  <button
                    type="button"
                    className="ln-tr-close"
                    aria-label="关闭详情"
                    onClick={() => setSel(null)}
                  >
                    <X size={14} />
                  </button>
                </span>
              }
              bodyClassName="ln-panel-body"
            >
              <div
                style={{
                  display: 'flex',
                  flexDirection: 'column',
                  gap: S.sm,
                  maxHeight: 420,
                  overflowY: 'auto',
                }}
              >
                {/* 头部：actor 完整身份 */}
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: S.sm,
                    paddingBottom: S.sm,
                    borderBottom: '1px solid var(--ln-border)',
                  }}
                >
                  <AgentAvatar agentId={sel.actor} size={28} />
                  <div style={{ minWidth: 0 }}>
                    <div
                      style={{
                        fontSize: T.sm,
                        fontWeight: 600,
                        color: agentColor(sel.actor),
                        fontFamily: 'var(--ln-mono)',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                      }}
                    >
                      {sel.actor}
                    </div>
                    {sel.actorRole && (
                      <div style={{ fontSize: T.xs, color: 'var(--ln-text-muted)', marginTop: 1 }}>
                        {sel.actorRole}
                      </div>
                    )}
                  </div>
                </div>
                {/* 状态徽章 */}
                <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                  <span className="ln-chip" data-tone={sevTone(sel.severity)}>
                    {sel.severity}
                  </span>
                  {sel.evidenceRef && (
                    <span className="ln-chip" data-tone="success">
                      证据链
                    </span>
                  )}
                  {sel.evidenceLevel && (
                    <span className="ln-chip" data-tone="info">
                      {matchLevelLabel(sel.evidenceLevel).label}
                    </span>
                  )}
                </div>
                {/* 结构化属性表 */}
                <div
                  style={{
                    display: 'grid',
                    gridTemplateColumns: '1fr 1fr',
                    gap: S.xs,
                    fontSize: T.xs,
                  }}
                >
                  {[
                    ['时钟', fmtClock(active.startWallMs + sel.offsetMs)],
                    ['偏移', `T+${fmtMs(sel.offsetMs)}`],
                    ['耗时', fmtMs(sel.durationMs)],
                    // 仅展示真实置信度（intent/action/pollution 无该字段 → 诚实「—」）
                    [
                      '置信度',
                      sel.confidence > 0 ? `${Math.round(sel.confidence * 100)}%` : '—',
                    ],
                    ['类型', kindLabel[sel.kind]],
                    ['主体', shortAgentId(sel.actor)],
                    ...(sel.processComm ? ([['进程', sel.processComm]] as [string, string][]) : []),
                    ...(sel.pid != null ? ([['pid', String(sel.pid)]] as [string, string][]) : []),
                    ...(sel.processAgent
                      ? ([['归属 agent', shortAgentId(sel.processAgent)]] as [string, string][])
                      : []),
                  ].map(([k, v]) => (
                    <div key={k}>
                      <span className="ln-panel__meta">{k}</span>
                      <div
                        style={{
                          color: 'var(--ln-text)',
                          fontFamily: 'var(--ln-mono)',
                          fontSize: T.xs,
                          marginTop: 1,
                        }}
                      >
                        {v}
                      </div>
                    </div>
                  ))}
                </div>

                {/* 真实行为：该进程在 actions 表中的真实操作（不编造） */}
                {sel.actions.length > 0 && (
                  <div>
                    <SectionLabel color="var(--ln-info)" label="真实行为" />
                    <div className="ln-tr-acts">
                      {sel.actions.slice(0, 10).map((a, i) => (
                        <div key={i} className="ln-tr-act">
                          <span className="ln-tr-act__type">{a.actionLabel}</span>
                          <span className="ln-tr-act__target" title={a.target ?? undefined}>
                            {a.target ?? '—'}
                          </span>
                        </div>
                      ))}
                      {sel.actions.length > 10 && (
                        <div className="ln-tr-acts__more">
                          +{sel.actions.length - 10} 条行为
                        </div>
                      )}
                    </div>
                  </div>
                )}
                {/* span I/O */}
                {sel.input && (
                  <>
                    <SectionLabel color="var(--ln-info)" label="Input" />
                    <CodeBlock color="var(--ln-info)" maxH={140}>
                      {sel.input}
                    </CodeBlock>
                  </>
                )}
                {sel.output && (
                  <>
                    <SectionLabel color="var(--ln-success)" label="Output" />
                    <CodeBlock color="var(--ln-success)" maxH={140}>
                      {sel.output}
                    </CodeBlock>
                  </>
                )}
                {sel.prompt && (
                  <>
                    <SectionLabel color="#a78bfa" label="LLM Prompt" />
                    <CodeBlock color="#a78bfa" maxH={160}>
                      {sel.prompt}
                    </CodeBlock>
                  </>
                )}
                {sel.response && (
                  <>
                    <SectionLabel color="#a78bfa" label="LLM Response" />
                    <CodeBlock color="#a78bfa" maxH={160}>
                      {sel.response}
                    </CodeBlock>
                  </>
                )}
                {sel.toolCall && (
                  <>
                    <SectionLabel color="#fb923c" label="Tool Call" />
                    <CodeBlock color="#fb923c" maxH={140}>
                      {sel.toolCall}
                    </CodeBlock>
                  </>
                )}
                {sel.toolResult && (
                  <>
                    <SectionLabel color="#fb923c" label="Tool Result" />
                    <CodeBlock color="#fb923c" maxH={140}>
                      {sel.toolResult}
                    </CodeBlock>
                  </>
                )}
                {sel.processTree && (
                  <>
                    <SectionLabel color="#60a5fa" label="进程链" />
                    <CodeBlock color="#60a5fa" maxH={140}>
                      {sel.processTree}
                    </CodeBlock>
                  </>
                )}
                {sel.rawEvidence && Object.keys(sel.rawEvidence).length > 0 && (
                  <>
                    <SectionLabel color="var(--ln-text-muted)" label="原始证据" />
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: S.xs }}>
                      {Object.entries(sel.rawEvidence)
                        .slice(0, 12)
                        .map(([k, v]) => (
                          <span
                            key={k}
                            title={String(v)}
                            style={{
                              padding: '3px 7px',
                              borderRadius: 4,
                              fontSize: T.xs,
                              background: 'var(--ln-surface-0)',
                              border: '1px solid var(--ln-border)',
                              fontFamily: 'var(--ln-mono)',
                              color: 'var(--ln-text-secondary)',
                              maxWidth: 260,
                              overflow: 'hidden',
                              textOverflow: 'ellipsis',
                              whiteSpace: 'nowrap',
                            }}
                          >
                            <span style={{ color: 'var(--ln-text-muted)' }}>{k}</span>
                            <span style={{ margin: '0 3px', color: 'var(--ln-border)' }}>=</span>
                            <span>{String(v)}</span>
                          </span>
                        ))}
                    </div>
                  </>
                )}
                {sel.material && sel.material !== 'No linked material' && (
                  <div>
                    <span className="ln-panel__meta">物化路径</span>
                    <div
                      style={{
                        fontSize: T.xs,
                        color: 'var(--ln-text-secondary)',
                        fontFamily: 'var(--ln-mono)',
                        marginTop: 1,
                      }}
                    >
                      {sel.material}
                    </div>
                  </div>
                )}
                {sel.ruleIds.length > 0 && (
                  <div>
                    <span className="ln-panel__meta">触发规则</span>
                    <div style={{ display: 'flex', gap: 3, flexWrap: 'wrap', marginTop: 3 }}>
                      {sel.ruleIds.map((r) => (
                        <span
                          key={r}
                          style={{
                            padding: '2px 5px',
                            borderRadius: 3,
                            background: 'var(--ln-surface-0)',
                            border: '1px solid var(--ln-border)',
                            fontSize: 9,
                            fontFamily: 'var(--ln-mono)',
                            color: 'var(--ln-warning)',
                          }}
                        >
                          {r}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </SectionPanel>
          ) : (
            <SectionPanel eyebrow="事件详情" title="选择事件" bodyClassName="ln-panel-body">
              <p
                style={{
                  fontSize: T.sm,
                  color: 'var(--ln-text-muted)',
                  margin: 0,
                  lineHeight: 1.6,
                }}
              >
                从责任链路或左侧进程树选择事件，查看完整上下文（I/O、进程链、原始证据、触发规则）。
              </p>
            </SectionPanel>
          )}        </div>
      </div>
    </div>
  );
}
