'use client';

import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import VirtualList from '@/components/ui/virtual-list';
import {
  getLiveRawEvents,
  subscribeLiveRawEvents,
} from '@/lib/live/live-raw-events';
import type { LiveRawEvent } from '@/lib/live/live-types';

/** 事件源配色（与采集器 source 对应）。 */
const SOURCE_COLORS: Record<string, string> = {
  tls_capture: '#4ade80',
  process: '#60a5fa',
  stdiocap: '#a78bfa',
  system: '#fbbf24',
};

const ROW_HEIGHT = 26;

function fmtTime(ms: number): string {
  if (!ms) return '';
  const d = new Date(ms);
  return d.toTimeString().slice(0, 8);
}

/** 距滚动底不足 threshold 视为「接近底部」——自动跟随判定（纯函数，便于测试）。 */
export function isNearBottom(
  el: { scrollHeight: number; scrollTop: number; clientHeight: number },
  threshold = 24,
): boolean {
  return el.scrollHeight - el.scrollTop - el.clientHeight < threshold;
}

/**
 * 原始事件实时滚动（live tail）——高频数据走 LiveSession 环形缓冲 + uSES。
 *
 * - 虚拟化列表（只挂载可见行）；自动跟随最新，上滚暂停、回底恢复。
 * - 按 source 过滤 + 来源色彩徽章。
 * - 点击行展开 `data` 载荷（原始事件含 TLS 明文，默认折叠）。
 */
export function LiveEventTail() {
  const events = useSyncExternalStore(subscribeLiveRawEvents, getLiveRawEvents);
  const [filter, setFilter] = useState<string>('all');
  const [follow, setFollow] = useState(true);
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set());
  const scrollRef = useRef<HTMLDivElement | null>(null);

  const sources = useMemo(() => Array.from(new Set(events.map((e) => e.source))), [events]);
  const filtered = useMemo(
    () => (filter === 'all' ? events : events.filter((e) => e.source === filter)),
    [events, filter],
  );

  // 自动跟随：follow 时新事件滚到底（离屏暂停由 LiveSession 环形缓冲天然承担）。
  // 依赖末元素 id 而非 length——环形缓冲满后 length 不再变化，length 依赖会失效（审查 MEDIUM-4）。
  const lastEventId = filtered.length > 0 ? filtered[filtered.length - 1].id : null;
  useEffect(() => {
    if (!follow) return;
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [lastEventId, follow]);

  const handleScroll = (e: React.UIEvent<HTMLDivElement>) => {
    setFollow(isNearBottom(e.currentTarget));
  };

  const toggleRow = (id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const renderRow = (ev: LiveRawEvent) => {
    const color = SOURCE_COLORS[ev.source] ?? '#6b7280';
    const isExpanded = expanded.has(ev.id);
    return (
      <div
        role="button"
        tabIndex={0}
        aria-expanded={isExpanded}
        aria-label={`${ev.source} ${ev.comm} pid ${ev.pid} ${fmtTime(ev.timestampMs)}`}
        onClick={() => toggleRow(ev.id)}
        onKeyDown={(e) => {
          // H2：WAI-ARIA 按钮须 Enter + Space 都能激活
          if (e.key === 'Enter' || e.key === ' ' || e.key === 'Spacebar') {
            e.preventDefault();
            toggleRow(ev.id);
          }
        }}
        style={{
          display: 'flex',
          alignItems: isExpanded ? 'flex-start' : 'center',
          gap: 8,
          height: isExpanded ? 'auto' : ROW_HEIGHT,
          padding: `0 10px`,
          borderBottom: '1px solid rgba(255,255,255,0.04)',
          cursor: 'pointer',
          fontSize: 11,
          fontFamily: 'var(--ln-mono, monospace)',
          color: 'rgba(255,255,255,0.72)',
        }}
        title={isExpanded ? '收起载荷' : '展开载荷'}
      >
        <span
          style={{
            color,
            fontSize: 9,
            letterSpacing: '0.04em',
            minWidth: 72,
            flexShrink: 0,
          }}
        >
          {ev.source}
        </span>
        <span style={{ minWidth: 90, flexShrink: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {ev.comm}
        </span>
        <span style={{ minWidth: 56, flexShrink: 0, color: 'rgba(255,255,255,0.55)' }}>
          pid {ev.pid}
        </span>
        <span style={{ minWidth: 58, flexShrink: 0, color: 'rgba(255,255,255,0.55)' }}>
          {fmtTime(ev.timestampMs)}
        </span>
        {isExpanded && (
          <pre
            aria-hidden
            style={{
              margin: 0,
              padding: '6px 8px',
              background: 'rgba(0,0,0,0.35)',
              borderRadius: 6,
              fontSize: 10,
              lineHeight: 1.6,
              color: '#c4b5fd',
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
              flex: 1,
            }}
          >
            {JSON.stringify(ev.data, null, 2)}
          </pre>
        )}
      </div>
    );
  };

  return (
    <div
      data-testid="live-event-tail"
      role="region"
      aria-label="实时事件流"
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        maxHeight: '100%',
        minHeight: 0,
        borderRadius: 12,
        border: '1px solid rgba(255,255,255,0.08)',
        background: 'rgba(6,6,10,0.85)',
        overflow: 'hidden',
      }}
    >
      {/* header：跟随开关 + 事件计数 + source 过滤 */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          padding: '8px 10px',
          borderBottom: '1px solid rgba(255,255,255,0.06)',
          flexShrink: 0,
        }}
      >
        <button
          type="button"
          data-testid="live-follow-toggle"
          onClick={() => setFollow((f) => !f)}
          aria-pressed={follow}
          title={follow ? '自动跟随最新（点按暂停）' : '已暂停跟随（点按恢复）'}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 5,
            fontSize: 9,
            letterSpacing: '0.06em',
            textTransform: 'uppercase',
            color: follow ? '#4ade80' : 'rgba(255,255,255,0.55)',
            background: follow ? 'rgba(74,222,128,0.1)' : 'rgba(255,255,255,0.05)',
            border: `1px solid ${follow ? 'rgba(74,222,128,0.3)' : 'rgba(255,255,255,0.1)'}`,
            borderRadius: 999,
            padding: '2px 8px',
            cursor: 'pointer',
          }}
        >
          {follow ? '跟随' : '已暂停'}
        </button>
        <span style={{ fontSize: 9, color: 'rgba(255,255,255,0.55)', fontFamily: 'var(--ln-mono, monospace)' }}>
          {filtered.length} 条
        </span>
        <span style={{ flex: 1 }} />
        <label style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 9, color: 'rgba(255,255,255,0.45)' }}>
          来源
          <select
            data-testid="live-source-filter"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            style={{
              // 原生 <select> 用实色深底而非近透明背景 —— 浅色系统/忽略 color-scheme 的
              // 浏览器会渲染白底，白底+白字=全白。实色底 + colorScheme:dark 双保险。
              background: '#0d1117',
              colorScheme: 'dark',
              border: '1px solid rgba(255,255,255,0.12)',
              borderRadius: 6,
              color: 'rgba(255,255,255,0.8)',
              fontSize: 9,
              padding: '2px 4px',
            }}
          >
            <option value="all">全部</option>
            {sources.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
      </div>

      {/* 虚拟化滚动列表 */}
      <div style={{ flex: 1, minHeight: 0 }}>
        <VirtualList
          items={filtered}
          estimateSize={ROW_HEIGHT}
          overscan={12}
          dynamic
          scrollRef={scrollRef}
          onScroll={handleScroll}
          emptyLabel="等待实时事件……"
          renderRow={renderRow}
        />
      </div>
    </div>
  );
}
