'use client';

import { useEffect, useState, useCallback, useMemo, useRef } from 'react';
import { useAppStore } from '@/lib/store';
import { api } from '@/lib/api';
import { Check, ChevronDown, Database, Loader2, Search } from 'lucide-react';

type RunInfo = {
  run_id: string;
  scenario: string | null;
  has_data: boolean;
  node_count?: number;
  edge_count?: number;
};

export default function RunSelector() {
  const [runs, setRuns] = useState<RunInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [activeIndex, setActiveIndex] = useState(0);
  const activeRunId = useAppStore((s) => s.activeRunId);
  const listRef = useRef<HTMLDivElement>(null);

  // Load run list on mount
  useEffect(() => {
    api
      .getRuns()
      .then((data: any) => {
        const list = data?.runs ?? data ?? [];
        setRuns(list.filter((r: RunInfo) => r.has_data));
      })
      .catch(() => {});
  }, []);

  const handleSelect = useCallback(
    async (runId: string) => {
      setLoading(true);
      setLoadError(null);
      setOpen(false);
      try {
        await useAppStore.getState().loadRun(runId);
      } catch (e) {
        setLoadError(e instanceof Error ? e.message : '加载失败，请重试');
      }
      setLoading(false);
    },
    [],
  );

  // Search filter
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return runs;
    return runs.filter(
      (r) =>
        r.run_id.toLowerCase().includes(q) ||
        (r.scenario ?? '').toLowerCase().includes(q),
    );
  }, [runs, query]);

  // Group by scenario
  const scenarios = useMemo(
    () =>
      filtered.reduce<Record<string, RunInfo[]>>((acc, r) => {
        const key = r.scenario ?? 'unknown';
        (acc[key] = acc[key] ?? []).push(r);
        return acc;
      }, {}),
    [filtered],
  );

  // Flatten list for keyboard nav
  const flatItems = useMemo(() => filtered, [filtered]);

  useEffect(() => {
    setActiveIndex(0);
  }, [query, open]);

  // Scroll active item into view
  useEffect(() => {
    const el = listRef.current?.querySelector(`[data-idx="${activeIndex}"]`);
    el?.scrollIntoView({ block: 'nearest' });
  }, [activeIndex]);

  // Keyboard navigation (Linear / command-palette standard)
  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setActiveIndex((i) => Math.min(i + 1, flatItems.length - 1));
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        setActiveIndex((i) => Math.max(i - 1, 0));
      } else if (e.key === 'Enter' && flatItems[activeIndex]) {
        e.preventDefault();
        handleSelect(flatItems[activeIndex].run_id);
      } else if (e.key === 'Escape') {
        setOpen(false);
      }
    },
    [activeIndex, flatItems, handleSelect],
  );

  const activeRun = runs.find((r) => r.run_id === activeRunId);
  const runCount = runs.length;

  // Flatten items with their index for rendering + data-idx
  let flatIdx = 0;
  const scenarioEntries = Object.entries(scenarios);

  return (
    <div style={{ position: 'relative' }}>
      <button
        type="button"
        onClick={() => {
          setOpen(!open);
          setQuery('');
        }}
        style={{
          display: 'flex', alignItems: 'center', gap: 7,
          padding: '4px 10px', borderRadius: 6,
          border: '1px solid rgba(255,255,255,0.08)',
          background: open ? 'rgba(255,255,255,0.06)' : 'transparent',
          color: loadError ? 'rgba(248,113,113,0.85)' : 'rgba(255,255,255,0.75)',
          fontSize: 12, cursor: 'pointer', whiteSpace: 'nowrap',
          transition: 'background 120ms, border-color 120ms',
        }}
        onMouseEnter={(e) => { e.currentTarget.style.background = 'rgba(255,255,255,0.05)'; }}
        onMouseLeave={(e) => { if (!open) e.currentTarget.style.background = 'transparent'; }}
        title={loadError ?? undefined}
      >
        {loading ? (
          <Loader2 size={15} className="mtl-spinner" style={{ opacity: 0.7 }} />
        ) : (
          <Database size={15} style={{ opacity: 0.75 }} />
        )}
        <span style={{ fontWeight: 500 }}>
          {activeRun ? activeRun.scenario ?? activeRun.run_id.slice(0, 12) : '导入数据'}
        </span>
        <ChevronDown
          size={13}
          style={{ opacity: 0.4, transform: open ? 'rotate(180deg)' : 'none', transition: 'transform 150ms' }}
        />
      </button>

      {open && (
        <>
          <div style={{ position: 'fixed', inset: 0, zIndex: 99 }} onClick={() => setOpen(false)} />
          <div
            style={{
              position: 'absolute', top: 'calc(100% + 6px)', right: 0,
              width: 400, maxHeight: 480,
              background: 'rgba(13,14,15,0.98)',
              backdropFilter: 'blur(20px)',
              border: '1px solid rgba(255,255,255,0.08)',
              borderRadius: 12,
              overflow: 'hidden', zIndex: 100,
              boxShadow: '0 16px 48px rgba(0,0,0,0.4)',
              display: 'flex', flexDirection: 'column',
            }}
            onKeyDown={handleKeyDown}
          >
            {/* Header + search */}
            <div style={{ padding: '12px 12px', borderBottom: '1px solid rgba(255,255,255,0.06)' }}>
              <div
                style={{
                  display: 'flex', alignItems: 'center', gap: 8,
                  padding: '0 10px', height: 30, borderRadius: 6,
                  background: 'rgba(255,255,255,0.04)',
                  border: '1px solid rgba(255,255,255,0.06)',
                  transition: 'border-color 120ms',
                }}
              >
                <Search size={13} style={{ opacity: 0.4, flexShrink: 0 }} />
                <input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder={`搜索 ${runCount} 个运行…`}
                  autoFocus
                  style={{
                    flex: 1, background: 'none', border: 'none', outline: 'none',
                    fontSize: 12, color: 'rgba(255,255,255,0.85)', fontFamily: 'inherit',
                  }}
                />
                <kbd style={{ fontSize: 9, opacity: 0.35, fontFamily: 'var(--ln-mono, monospace)' }}>esc</kbd>
              </div>
            </div>

            {/* List */}
            <div ref={listRef} style={{ maxHeight: 400, overflowY: 'auto', padding: 6 }}>
              {scenarioEntries.map(([scenario, items]) => (
                <div key={scenario}>
                  <div
                    style={{
                      padding: '8px 12px 4px', fontSize: 10, fontWeight: 600,
                      color: 'rgba(255,255,255,0.3)', letterSpacing: '0.08em',
                      textTransform: 'uppercase',
                    }}
                  >
                    {scenario.replace(/-/g, ' ')} · {items.length}
                  </div>
                  {items.slice(0, 4).map((r) => {
                    const idx = flatIdx++;
                    const selected = activeRunId === r.run_id;
                    const highlighted = idx === activeIndex;
                    const scale =
                      r.node_count != null && r.edge_count != null
                        ? `${r.node_count}·${r.edge_count}`
                        : null;
                    return (
                      <button
                        key={r.run_id}
                        type="button"
                        data-idx={idx}
                        onClick={() => handleSelect(r.run_id)}
                        style={{
                          display: 'flex', alignItems: 'center', gap: 8,
                          width: '100%', textAlign: 'left',
                          padding: '7px 12px', border: 'none', borderRadius: 6, cursor: 'pointer',
                          background: selected
                            ? 'rgba(94,106,210,0.12)'
                            : highlighted
                              ? 'rgba(255,255,255,0.05)'
                              : 'transparent',
                          color: selected ? '#fafafa' : 'rgba(255,255,255,0.6)',
                          fontSize: 11, fontFamily: 'var(--ln-mono, monospace)',
                          transition: 'background 80ms',
                        }}
                        onMouseEnter={() => setActiveIndex(idx)}
                      >
                        <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {r.run_id.slice(0, 16)}…
                        </span>
                        {scale && (
                          <span
                            style={{
                              flexShrink: 0, padding: '1px 6px', borderRadius: 4,
                              background: 'rgba(251,191,36,0.08)',
                              border: '1px solid rgba(251,191,36,0.14)',
                              color: 'rgba(251,191,36,0.75)',
                              fontSize: 9, fontFamily: 'var(--ln-mono, monospace)',
                            }}
                          >
                            {scale}
                          </span>
                        )}
                        {selected && <Check size={13} style={{ color: '#5e6ad2', flexShrink: 0 }} />}
                      </button>
                    );
                  })}
                  {items.length > 4 && (
                    <div style={{ padding: '4px 12px', fontSize: 10, color: 'rgba(255,255,255,0.25)' }}>
                      +{items.length - 4} 更多
                    </div>
                  )}
                </div>
              ))}
              {filtered.length === 0 && (
                <div style={{ padding: '24px 12px', textAlign: 'center', fontSize: 12, color: 'rgba(255,255,255,0.3)' }}>
                  未找到匹配的运行数据
                </div>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
