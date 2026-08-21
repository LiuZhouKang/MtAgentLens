'use client';

import { createPortal } from 'react-dom';
import { useEffect, useMemo, useRef, useState } from 'react';
import { motion } from 'motion/react';
import VirtualList from '@/components/ui/virtual-list';
import { Codicon } from '@/components/ui/codicon';
import {
  availableKindsForAgent,
  buildRawEventRows,
  buildTimeRanges,
  filterRawEventRows,
  summarizeRawEvents,
  type RawEventKind,
  type RawEventRow,
} from '@/lib/raw-event-browser';
import type {
  Action,
  ContextPollutionEvent,
  IdentityProcessMapEntry,
  Intent,
  ProcessEvent,
  TlsSession,
} from '@/lib/types';

export type RawEventBrowserProps = {
  processEvents: ProcessEvent[];
  tlsSessions: TlsSession[];
  intents: Intent[];
  actions: Action[];
  pollutionEvents: ContextPollutionEvent[];
  /** pid+start_time_ns → agent_id：intents/actions 的真实归属解析（不臆测）。 */
  identityProcessMap: IdentityProcessMapEntry[];
  /** process/tls 导入是否被 cap 截断（大库诚实标注，展示「已截断」）。 */
  processEventTruncated?: boolean;
  tlsSessionTruncated?: boolean;
  onClose: () => void;
};

const STORAGE_KEY = 'mta.raw-event-browser';
const INITIAL_VISIBLE = 300;
const STEP = 300;

const KIND_ORDER: RawEventKind[] = ['process', 'tls', 'intent', 'action', 'pollution'];

// 标签与采集器/Rust 原生命名一致：process_events / tls_sessions / intents / actions /
// context_pollution_events。中文即其表语义，非自定义。
const KIND_META: Record<RawEventKind, { label: string; color: string }> = {
  process: { label: '进程事件', color: '#4ade80' },
  tls: { label: 'TLS 会话', color: '#60a5fa' },
  intent: { label: '意图', color: '#fbbf24' },
  action: { label: '动作', color: '#f472b6' },
  pollution: { label: '上下文污染', color: '#fb7185' },
};

type PersistedFilters = {
  activeKinds?: RawEventKind[];
  agentId?: string | null;
  text?: string;
  rangeWindow?: number | null;
};

function loadPersisted(): PersistedFilters {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as PersistedFilters;
    if (typeof parsed !== 'object' || parsed === null) return {};
    return parsed;
  } catch {
    return {}; // 隐私模式 / SSR 等场景静默回退
  }
}

function formatTs(ns: number): string {
  return new Date(ns / 1_000_000).toISOString().slice(11, 23); // HH:MM:SS.mmm
}

/** 单行原始事件：kind 徽章 + 标题 + agent 标注 + 时间 + 截断详情；点击展开完整 JSON。 */
function EventRow({
  row,
  expanded,
  onToggle,
}: {
  row: RawEventRow;
  expanded: boolean;
  onToggle: () => void;
}) {
  const meta = KIND_META[row.kind];
  return (
    <div className="px-5 py-1">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={expanded}
        className="w-full rounded-lg border border-white/5 bg-white/[0.02] px-3 py-2 text-left transition-all hover:border-white/15 hover:bg-white/[0.05]"
      >
        <div className="flex items-center gap-2">
          <span
            className="shrink-0 rounded-full px-2 py-0.5 text-[9px] font-semibold tracking-wider uppercase"
            style={{ color: meta.color, background: `${meta.color}1a` }}
          >
            {meta.label}
          </span>
          <span className="truncate font-mono text-xs text-white/90">{row.title}</span>
          {row.agentId && (
            <span className="shrink-0 rounded border border-white/10 bg-white/[0.03] px-1.5 py-0.5 font-mono text-[9px] text-cyan-300/80">
              {row.agentId}
            </span>
          )}
          <span className="ml-auto shrink-0 font-mono text-[10px] text-white/35 tabular-nums">
            {formatTs(row.timestampNs)}
          </span>
        </div>
        <div className="mt-1 truncate font-mono text-[10px] text-white/45">{row.detail}</div>
      </button>
      {expanded && (
        <pre className="mt-1 overflow-auto rounded-lg border border-white/10 bg-black/40 px-3 py-2 font-mono text-[10px] leading-relaxed break-words whitespace-pre-wrap text-emerald-200/80">
          {JSON.stringify(row.raw, null, 2)}
        </pre>
      )}
    </div>
  );
}

/**
 * 原始事件流抽屉——四类原始事件（进程/TLS/意图/动作）的统一浏览与多维筛选。
 * 全部数据经纯函数从 dbAssets 派生；kind 徽章仅渲染有计数的种类（诚实）；
 * agent 选中后 kind 筛选级联收窄；筛选状态持久化到 localStorage。
 * 本组件是覆盖层 drawer，由父层 AnimatePresence 控制进出场。
 */
/** Agent 选择器：可搜索组合框（170+ agent 用原生 select 不可用）。输入可过滤 ID，
 *  选项带真实事件计数；点击外部 / Escape 关闭。 */
function AgentSelect({
  agents,
  value,
  onChange,
}: {
  agents: { id: string; count: number }[];
  value: string | null;
  onChange: (id: string | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const rootRef = useRef<HTMLDivElement>(null);

  // 点击外部关闭
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    const key = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', handler);
    document.addEventListener('keydown', key);
    return () => {
      document.removeEventListener('mousedown', handler);
      document.removeEventListener('keydown', key);
    };
  }, [open]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return agents;
    return agents.filter((a) => a.id.toLowerCase().includes(q));
  }, [agents, query]);

  const selected = value ? agents.find((a) => a.id === value) : null;
  const summaryLabel = agents.length > 0 ? `${agents.reduce((s, a) => s + a.count, 0)} 条` : '';

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label="按 Agent 筛选"
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1.5 rounded-lg border border-white/5 bg-white/[0.02] px-2.5 py-1.5 font-mono text-[11px] text-white/70 transition-all outline-none hover:border-white/15 focus:border-white/25"
      >
        <Codicon name="organization" size={12} className="text-white/35" />
        <span className="max-w-[140px] truncate">
          {selected ? `${selected.id.slice(0, 8)}… (${selected.count})` : `全部 Agent${summaryLabel ? `（${summaryLabel}）` : ''}`}
        </span>
        <Codicon name="chevron-down" size={10} className="shrink-0 text-white/30" />
      </button>

      {open && (
        <div
          role="listbox"
          aria-label="Agent 列表"
          className="absolute top-full left-0 z-20 mt-1 w-72 overflow-hidden rounded-xl border border-white/10 bg-[#0d1117] shadow-2xl"
        >
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="搜索 Agent ID…"
            aria-label="搜索 Agent"
            autoFocus
            className="w-full border-b border-white/10 bg-transparent px-3 py-2 font-mono text-[11px] text-white/80 outline-none placeholder:text-white/30"
          />
          <div className="max-h-64 overflow-y-auto p-1">
            <button
              type="button"
              role="option"
              aria-selected={value === null}
              onClick={() => {
                onChange(null);
                setOpen(false);
                setQuery('');
              }}
              className="flex w-full items-center justify-between rounded-md px-2 py-1.5 text-left font-mono text-[11px] text-white/70 transition-colors hover:bg-white/[0.06] hover:text-white"
            >
              <span>全部 Agent</span>
              <span className="text-white/35 tabular-nums">{agents.length} 个</span>
            </button>
            {filtered.map((a) => (
              <button
                key={a.id}
                type="button"
                role="option"
                aria-selected={a.id === value}
                onClick={() => {
                  onChange(a.id);
                  setOpen(false);
                  setQuery('');
                }}
                className="flex w-full items-center justify-between gap-2 rounded-md px-2 py-1.5 text-left font-mono text-[11px] text-white/70 transition-colors hover:bg-white/[0.06] hover:text-white"
              >
                <span className="truncate">{a.id}</span>
                <span className="shrink-0 text-white/35 tabular-nums">{a.count}</span>
              </button>
            ))}
            {filtered.length === 0 && (
              <div className="px-2 py-2 text-[11px] text-white/40">无匹配 Agent</div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export default function RawEventBrowser({
  processEvents,
  tlsSessions,
  intents,
  actions,
  pollutionEvents,
  identityProcessMap,
  processEventTruncated = false,
  tlsSessionTruncated = false,
  onClose,
}: RawEventBrowserProps) {
  const [initialFilters] = useState(loadPersisted);
  const [activeKinds, setActiveKinds] = useState<RawEventKind[]>(initialFilters.activeKinds ?? []);
  const [agentId, setAgentId] = useState<string | null>(initialFilters.agentId ?? null);
  const [text, setText] = useState(initialFilters.text ?? '');
  const [textQuery, setTextQuery] = useState(initialFilters.text ?? '');
  const [rangeWindow, setRangeWindow] = useState<number | null>(initialFilters.rangeWindow ?? null);
  const [visibleCount, setVisibleCount] = useState(INITIAL_VISIBLE);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const rows = useMemo(
    () =>
      buildRawEventRows({
        processEvents,
        tlsSessions,
        intents,
        actions,
        pollutionEvents,
        identityProcessMap,
      }),
    [processEvents, tlsSessions, intents, actions, pollutionEvents, identityProcessMap],
  );
  // Agent 列表按事件数降序（最活跃在前）+ 携带真实计数，供选择器展示。
  const agentStats = useMemo(() => {
    const counts = new Map<string, number>();
    for (const row of rows) if (row.agentId) counts.set(row.agentId, (counts.get(row.agentId) ?? 0) + 1);
    return Array.from(counts.entries())
      .map(([id, count]) => ({ id, count }))
      .sort((a, b) => b.count - a.count);
  }, [rows]);
  const minTs = useMemo(() => (rows.length > 0 ? rows[0].timestampNs : 0), [rows]);
  const maxTs = useMemo(() => (rows.length > 0 ? rows[rows.length - 1].timestampNs : 0), [rows]);
  // 自适应时间档位：只提供真正能切中数据跨度的「最近 X」——固定档位（如最近 1 小时）
  // 对短 run 等于全选，点选无变化（用户报告缺陷）。跨度 146s → 仅 10s/30s/1m/2m 档。
  const timeRanges = useMemo(() => buildTimeRanges(minTs, maxTs), [minTs, maxTs]);

  // 持久化的 rangeWindow 可能来自旧档位/不同数据源：不在当前自适应档位内则重置为全部时间。
  useEffect(() => {
    if (rangeWindow != null && !timeRanges.some((r) => r.window === rangeWindow)) {
      setRangeWindow(null);
    }
  }, [timeRanges, rangeWindow]);

  // 级联纪律：选 agent 后 kind 筛选自动收窄到该 agent 实际拥有的 kind
  const availableKinds = useMemo(() => availableKindsForAgent(rows, agentId), [rows, agentId]);
  useEffect(() => {
    if (agentId != null) setActiveKinds(availableKinds);
  }, [agentId, availableKinds]);

  // 文本筛选 350ms 防抖（useRef 定时器，输入流畅 + 大列表不反复过滤）
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => setTextQuery(text), 350);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [text]);

  // 筛选变化后回到顶部批次
  useEffect(() => {
    setVisibleCount(INITIAL_VISIBLE);
    setExpandedId(null);
  }, [activeKinds, agentId, textQuery, rangeWindow]);

  // 筛选状态持久化（隐私模式等异常静默）
  useEffect(() => {
    try {
      window.localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({ activeKinds, agentId, text, rangeWindow }),
      );
    } catch {
      /* ignore */
    }
  }, [activeKinds, agentId, text, rangeWindow]);

  const sinceNs = rangeWindow == null ? null : Math.max(0, maxTs - rangeWindow);

  // kind 徽章计数随当前筛选（agent / 时间 / 文本）联动——展示「在该筛选下各类还有多少条」，
  // 而非全局总数（选 agent 后仍显示全局 1067 条 TLS 会误导）。0 计数的 kind 徽章隐藏。
  const summary = useMemo(
    () =>
      summarizeRawEvents(
        filterRawEventRows(rows, { kinds: [], agentId, text: textQuery, sinceNs }),
      ),
    [rows, agentId, textQuery, sinceNs],
  );

  const filtered = useMemo(
    () => filterRawEventRows(rows, { kinds: activeKinds, agentId, text: textQuery, sinceNs }),
    [rows, activeKinds, agentId, textQuery, sinceNs],
  );
  const visible = filtered.slice(0, visibleCount);

  // 当前筛选结果覆盖的时间窗（全部时间 = 全量跨度；选范围 = 该范围跨度）
  const timeSpan = useMemo(() => {
    if (filtered.length === 0) return null;
    return { from: formatTs(filtered[0].timestampNs), to: formatTs(filtered[filtered.length - 1].timestampNs) };
  }, [filtered]);

  // 任一筛选是否处于非默认态（用于「重置」入口）
  const hasActiveFilters =
    agentId != null || textQuery !== '' || rangeWindow != null || activeKinds.length > 0;

  function resetFilters() {
    setAgentId(null);
    setText('');
    setTextQuery('');
    setRangeWindow(null);
    setActiveKinds([]);
  }

  function toggleKind(kind: RawEventKind) {
    setActiveKinds((prev) => {
      if (prev.length === 0) return [kind];
      if (prev.includes(kind)) return prev.filter((x) => x !== kind); // 空 = 全部
      return [...prev, kind];
    });
  }

  // Portal 到 document.body：抽屉是固定定位的全屏 modal，必须逃逸父级
  // 堆叠上下文。否则 /console 上 rail 包装容器（z-index:10）会把抽屉的
  // z-[80] 钳制在 10，导致右上角实时告警流（z-20）/ 节点详情（z-20）/
  // 时间滑杆（z-60）绘制在抽屉之上 —— 关闭按钮被实时告警流盖住、不可点击。
  // Portal 后 z-[80] 在根堆叠上下文生效：高于内容覆盖层，低于顶栏（z-1000）
  // 与命令面板（z-10000）。
  return createPortal(
    <motion.aside
      aria-label="原始事件流抽屉"
      // 抽屉必须位于 32px 顶栏之下（top-[32px]），否则 inset-y-0 会把它顶到
      // 视口最顶部、标题与关闭按钮被 z-1000 的不透明顶栏盖住 —— 用户看到抽屉
      // 「顶出屏幕」。overflow-hidden：任何内容都不得视觉溢出抽屉（虚拟列表
      // 自身的 overflow:auto 兜底，此处再硬性裁剪，杜绝行内容流出底部）。
      className="fixed top-[32px] bottom-0 right-0 z-[80] flex w-[720px] max-w-[92vw] flex-col overflow-hidden border-l border-white/10 bg-[#0b0f1a]/95 shadow-2xl backdrop-blur-xl"
      // 双保险：Tailwind 任意值背景 + 内联底色兜底 —— 即使 oklab/任意值类在极端环境失效，
      // 抽屉也绝不透出白底（历史出现过「展开后全白」）。文字色同理固定为浅色。
      // colorScheme:dark：原生 <select>/<input> 的系统默认外观强制深色 —— 否则浅色系统下
      // 原生控件渲染白底，「全部时间」等下拉变成白块 + 白字（用户报告的「全白」根因）。
      style={{
        backgroundColor: '#0b0f1a',
        color: 'rgba(235,238,245,0.92)',
        colorScheme: 'dark',
      }}
      initial={{ x: 720, opacity: 0.6 }}
      animate={{ x: 0, opacity: 1 }}
      exit={{ x: 720, opacity: 0 }}
      transition={{ type: 'spring', stiffness: 320, damping: 34 }}
    >
      <div className="flex items-center gap-3 border-b border-white/10 px-5 py-4">
        <div className="flex items-center gap-2">
          <Codicon name="list-unordered" size={14} className="text-white/40" />
          <h2 className="text-sm font-semibold tracking-wide text-white/90">原始事件流</h2>
        </div>
        <span className="rounded-full border border-white/10 bg-white/[0.04] px-2 py-0.5 font-mono text-[10px] text-white/50 tabular-nums">
          已展示 {visible.length} / {filtered.length}
        </span>
        {(processEventTruncated || tlsSessionTruncated) && (
          <span
            title="进程 / TLS 原始数据超过导入上限，已按真实时间序截断展示（计数为该上限内的行数）"
            className="rounded-full border border-amber-400/25 bg-amber-400/10 px-2 py-0.5 text-[9px] font-medium tracking-widest text-amber-300/80 uppercase"
          >
            已截断
          </span>
        )}
        <button
          type="button"
          onClick={onClose}
          aria-label="关闭事件流"
          className="ml-auto flex h-8 w-8 items-center justify-center rounded-lg border border-white/5 bg-white/[0.02] text-white/60 transition-all hover:bg-white/[0.08] hover:text-white"
        >
          {/* 关闭图标用内联 SVG：本项目 codicon 字体的 `close` 码点渲染为暗淡菱形
              （字体/码点错位），内联 X 保证任何环境下都是清晰、亮色的标准关闭图标，
              且继承按钮 text-white/60 → hover:text-white。 */}
          <svg
            width="14"
            height="14"
            viewBox="0 0 14 14"
            fill="none"
            aria-hidden="true"
          >
            <path
              d="M3.5 3.5l7 7M10.5 3.5l-7 7"
              stroke="currentColor"
              strokeWidth="1.4"
              strokeLinecap="round"
            />
          </svg>
        </button>
      </div>

      <div className="flex flex-col gap-3 border-b border-white/10 px-5 py-3">
        <div className="flex flex-wrap items-center gap-2">
          {KIND_ORDER.filter((k) => summary[k] > 0).map((k) => {
            const meta = KIND_META[k];
            const active = activeKinds.length === 0 || activeKinds.includes(k);
            return (
              <button
                key={k}
                type="button"
                onClick={() => toggleKind(k)}
                aria-pressed={active}
                className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium tabular-nums transition-all ${
                  active
                    ? 'border-white/20 bg-white/[0.08] text-white'
                    : 'border-white/5 bg-white/[0.02] text-white/40 hover:bg-white/[0.06] hover:text-white/70'
                }`}
              >
                <span style={{ color: active ? meta.color : undefined }}>{meta.label}</span>
                <span>{summary[k]}</span>
              </button>
            );
          })}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <AgentSelect
            agents={agentStats}
            value={agentId}
            onChange={setAgentId}
          />
          <input
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="搜索 comm / id / args…"
            aria-label="文本筛选"
            className="min-w-0 flex-1 rounded-lg border border-white/10 bg-[#0d1117] px-2.5 py-1.5 font-mono text-[11px] text-white/80 transition-all outline-none placeholder:text-white/30 focus:border-white/25"
          />
          <select
            value={rangeWindow ?? ''}
            onChange={(e) => setRangeWindow(e.target.value === '' ? null : Number(e.target.value))}
            aria-label="时间范围"
            // 原生 <select> 用实色深底（#0d1117）而非近透明 bg-white/[0.02] —— 浅色系统下
            // 原生控件会渲染白底，白底+白字 = 全白。实色底 + colorScheme:dark 双保险。
            className="rounded-lg border border-white/10 bg-[#0d1117] px-2.5 py-1.5 font-mono text-[11px] text-white/80 transition-all outline-none focus:border-white/25"
            style={{ colorScheme: 'dark' }}
          >
            {timeRanges.map((r) => (
              <option key={r.label} value={r.window ?? ''}>
                {r.label}
              </option>
            ))}
          </select>
          {hasActiveFilters && (
            <button
              type="button"
              onClick={resetFilters}
              aria-label="重置筛选"
              className="inline-flex items-center gap-1 rounded-lg border border-white/10 bg-white/[0.04] px-2 py-1.5 text-[11px] text-white/60 transition-all hover:bg-white/[0.08] hover:text-white"
            >
              <Codicon name="clear-all" size={12} />
              重置
            </button>
          )}
        </div>
        {timeSpan && (
          <div className="flex items-center gap-1.5 font-mono text-[9px] tracking-wide text-white/30">
            <Codicon name="history" size={10} className="text-white/25" />
            {timeSpan.from} → {timeSpan.to}
          </div>
        )}
      </div>

      <div className="min-h-0 flex-1">
        {rows.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-2 text-white/40">
            <Codicon name="info" size={18} className="text-white/25" />
            <span className="text-sm">暂无原始事件</span>
            <span className="text-[11px] text-white/25">导入含 process / tls / intent / action / 污染事件的数据库后即可浏览</span>
          </div>
        ) : filtered.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-2 text-white/40">
            <Codicon name="filter" size={18} className="text-white/25" />
            <span className="text-sm">无匹配事件</span>
            <button
              type="button"
              onClick={resetFilters}
              className="mt-1 inline-flex items-center gap-1.5 rounded-lg border border-white/10 bg-white/[0.04] px-3 py-1.5 text-xs text-white/70 transition-all hover:bg-white/[0.08] hover:text-white"
            >
              <Codicon name="clear-all" size={12} />
              重置全部筛选
            </button>
          </div>
        ) : (
          <VirtualList
            className="h-full"
            items={visible}
            dynamic
            estimateSize={52}
            onScroll={(e) => {
              const el = e.currentTarget;
              if (el.scrollHeight - el.scrollTop - el.clientHeight < 100) {
                setVisibleCount((c) => (filtered.length > c ? c + STEP : c));
              }
            }}
            renderRow={(row) => (
              <EventRow
                key={row.id}
                row={row}
                expanded={expandedId === row.id}
                onToggle={() => setExpandedId((cur) => (cur === row.id ? null : row.id))}
              />
            )}
          />
        )}
      </div>
    </motion.aside>,
    document.body,
  );
}
