'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { motion } from 'motion/react';
import VirtualList from '@/components/ui/virtual-list';
import { Codicon } from '@/components/ui/codicon';
import {
  availableKindsForAgent,
  buildRawEventRows,
  filterRawEventRows,
  summarizeRawEvents,
  type RawEventKind,
  type RawEventRow,
} from '@/lib/raw-event-browser';
import type { Action, Intent, ProcessEvent, TlsSession } from '@/lib/types';

export type RawEventBrowserProps = {
  processEvents: ProcessEvent[];
  tlsSessions: TlsSession[];
  intents: Intent[];
  actions: Action[];
  onClose: () => void;
};

const STORAGE_KEY = 'mta.raw-event-browser';
const INITIAL_VISIBLE = 300;
const STEP = 300;
const MINUTE_NS = 60_000_000_000;

const KIND_ORDER: RawEventKind[] = ['process', 'tls', 'intent', 'action'];

const KIND_META: Record<RawEventKind, { label: string; color: string }> = {
  process: { label: '进程', color: '#4ade80' },
  tls: { label: 'TLS', color: '#60a5fa' },
  intent: { label: '意图', color: '#fbbf24' },
  action: { label: '动作', color: '#f472b6' },
};

const TIME_RANGES: { label: string; window: number | null }[] = [
  { label: '全部时间', window: null },
  { label: '最近 1 分钟', window: MINUTE_NS },
  { label: '最近 10 分钟', window: 10 * MINUTE_NS },
  { label: '最近 1 小时', window: 60 * MINUTE_NS },
];

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
            className="shrink-0 rounded-full px-2 py-0.5 text-[9px] font-semibold uppercase tracking-wider"
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
          <span className="ml-auto shrink-0 font-mono text-[10px] tabular-nums text-white/35">
            {formatTs(row.timestampNs)}
          </span>
        </div>
        <div className="mt-1 truncate font-mono text-[10px] text-white/45">{row.detail}</div>
      </button>
      {expanded && (
        <pre className="mt-1 overflow-auto rounded-lg border border-white/10 bg-black/40 px-3 py-2 font-mono text-[10px] leading-relaxed whitespace-pre-wrap break-words text-emerald-200/80">
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
export default function RawEventBrowser({
  processEvents,
  tlsSessions,
  intents,
  actions,
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
    () => buildRawEventRows({ processEvents, tlsSessions, intents, actions }),
    [processEvents, tlsSessions, intents, actions],
  );
  const summary = useMemo(() => summarizeRawEvents(rows), [rows]);
  const agents = useMemo(() => {
    const set = new Set<string>();
    for (const row of rows) if (row.agentId) set.add(row.agentId);
    return Array.from(set).sort();
  }, [rows]);
  const maxTs = useMemo(() => (rows.length > 0 ? rows[rows.length - 1].timestampNs : 0), [rows]);

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
  const filtered = useMemo(
    () => filterRawEventRows(rows, { kinds: activeKinds, agentId, text: textQuery, sinceNs }),
    [rows, activeKinds, agentId, textQuery, sinceNs],
  );
  const visible = filtered.slice(0, visibleCount);

  function toggleKind(kind: RawEventKind) {
    setActiveKinds((prev) => {
      if (prev.length === 0) return [kind];
      if (prev.includes(kind)) return prev.filter((x) => x !== kind); // 空 = 全部
      return [...prev, kind];
    });
  }

  return (
    <motion.aside
      aria-label="原始事件流抽屉"
      className="fixed inset-y-0 right-0 z-[80] flex w-[720px] max-w-[92vw] flex-col border-l border-white/10 bg-[#0b0f1a]/95 shadow-2xl backdrop-blur-xl"
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
        <span className="rounded-full border border-white/10 bg-white/[0.04] px-2 py-0.5 font-mono text-[10px] tabular-nums text-white/50">
          已展示 {visible.length} / {filtered.length}
        </span>
        <button
          type="button"
          onClick={onClose}
          aria-label="关闭事件流"
          className="ml-auto flex h-8 w-8 items-center justify-center rounded-lg border border-white/5 bg-white/[0.02] text-white/60 transition-all hover:bg-white/[0.08] hover:text-white"
        >
          <Codicon name="close" size={14} />
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
          <select
            value={agentId ?? ''}
            onChange={(e) => setAgentId(e.target.value === '' ? null : e.target.value)}
            aria-label="按 Agent 筛选"
            className="rounded-lg border border-white/5 bg-white/[0.02] px-2.5 py-1.5 font-mono text-[11px] text-white/70 outline-none transition-all focus:border-white/25"
          >
            <option value="">全部 Agent</option>
            {agents.map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </select>
          <input
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="搜索 comm / id / args…"
            aria-label="文本筛选"
            className="min-w-0 flex-1 rounded-lg border border-white/5 bg-white/[0.02] px-2.5 py-1.5 font-mono text-[11px] text-white/70 placeholder:text-white/25 outline-none transition-all focus:border-white/25"
          />
          <select
            value={rangeWindow ?? ''}
            onChange={(e) => setRangeWindow(e.target.value === '' ? null : Number(e.target.value))}
            aria-label="时间范围"
            className="rounded-lg border border-white/5 bg-white/[0.02] px-2.5 py-1.5 font-mono text-[11px] text-white/70 outline-none transition-all focus:border-white/25"
          >
            {TIME_RANGES.map((r) => (
              <option key={r.label} value={r.window ?? ''}>
                {r.label}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="min-h-0 flex-1">
        {rows.length === 0 ? (
          <div className="flex h-full items-center justify-center">
            <span className="text-sm text-white/40">暂无事件</span>
          </div>
        ) : filtered.length === 0 ? (
          <div className="flex h-full items-center justify-center">
            <span className="text-sm text-white/40">无匹配事件（调整筛选）</span>
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
    </motion.aside>
  );
}
