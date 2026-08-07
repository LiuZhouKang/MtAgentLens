'use client';

import React, { useMemo } from 'react';
import type { DedupDiagnosticsProps, DedupDroppedEvent } from '@/lib/dedup-diagnostics-contract';
import {
  calcDedupRate,
  calcCacheUsage,
  calcParseErrorRate,
  formatPercent,
} from '@/lib/dedup-diagnostics-contract';

// ═══════════════════════════════════════════════════════════════════════════
// 常量
// ═══════════════════════════════════════════════════════════════════════════

/** SEDA 指标定义：id, label, 格式化函数 */
const METRICS_DEF = [
  { key: 'events_processed', label: '检查事件', fmt: (v: number) => v.toLocaleString() },
  { key: 'duplicates_suppressed', label: '去重丢弃', fmt: (v: number) => v.toLocaleString() },
  { key: 'cache_size', label: '缓存条目', fmt: (v: number) => v.toLocaleString() },
  { key: 'unparseable', label: '解析失败', fmt: (v: number) => v.toLocaleString() },
  {
    key: 'passthrough_unknown_source',
    label: '未知源透传',
    fmt: (v: number) => v.toLocaleString(),
  },
] as const;

// ═══════════════════════════════════════════════════════════════════════════
// 子组件: RateBar — 比率进度条
// ═══════════════════════════════════════════════════════════════════════════

const RateBar: React.FC<{
  label: string;
  rate: number;
  color: 'green' | 'amber' | 'blue';
  detail?: string;
}> = ({ label, rate, color, detail }) => {
  const colorMap = {
    green: 'bg-emerald-500',
    amber: 'bg-amber-500',
    blue: 'bg-blue-500',
  };
  const bgMap = {
    green: 'bg-emerald-50',
    amber: 'bg-amber-50',
    blue: 'bg-blue-50',
  };

  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between text-xs">
        <span className="text-gray-500">{label}</span>
        <span className="font-mono font-semibold">{formatPercent(rate)}</span>
      </div>
      <div className={`h-2 overflow-hidden rounded-full ${bgMap[color]}`}>
        <div
          className={`h-full rounded-full transition-all ${colorMap[color]}`}
          style={{ width: `${Math.min(rate * 100, 100)}%` }}
        />
      </div>
      {detail && <div className="text-xs text-gray-400">{detail}</div>}
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════
// 子组件: MetricCard — 单个指标卡片
// ═══════════════════════════════════════════════════════════════════════════

const MetricCard: React.FC<{
  label: string;
  value: number;
  highlight?: boolean;
}> = ({ label, value, highlight }) => (
  <div
    className={`rounded-lg border p-3 ${
      highlight ? 'border-emerald-200 bg-emerald-50' : 'border-slate-200 bg-white'
    }`}
  >
    <div className="mb-1 text-xs text-gray-500">{label}</div>
    <div className="font-mono text-lg font-semibold">{value.toLocaleString()}</div>
  </div>
);

// ═══════════════════════════════════════════════════════════════════════════
// 子组件: DroppedEventsList — 丢弃事件列表
// ═══════════════════════════════════════════════════════════════════════════

const DroppedEventsList: React.FC<{
  events: DedupDroppedEvent[];
}> = ({ events }) => {
  const sorted = useMemo(
    () => [...events].sort((a, b) => b.timestamp_ns - a.timestamp_ns),
    [events],
  );

  if (sorted.length === 0) {
    return <div className="p-3 text-xs text-gray-400">无诊断事件（需启用 emit_diagnostics）</div>;
  }

  return (
    <div className="max-h-48 space-y-1 overflow-y-auto">
      {sorted.slice(0, 20).map((e, i) => (
        <div key={i} className="flex items-center justify-between rounded bg-slate-50 p-2 text-xs">
          <div className="flex items-center gap-2">
            <span className="font-mono text-gray-400">
              {new Date(e.timestamp_ns / 1_000_000).toLocaleTimeString()}
            </span>
            <span className="text-gray-500">source:</span>
            <span className="font-mono text-gray-700">{e.source}</span>
          </div>
          <span className="font-mono font-semibold text-gray-600">×{e.suppressed_count}</span>
        </div>
      ))}
      {sorted.length > 20 && (
        <div className="py-1 text-center text-xs text-gray-400">
          ... 还有 {sorted.length - 20} 条
        </div>
      )}
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════
// 主组件: DedupDiagnosticsPanel
// ═══════════════════════════════════════════════════════════════════════════

/** @deprecated 阶段二实时视角（SSE 接入后迁移至运行监控区块），现无生产者，仅存档。 */
export const DedupDiagnosticsPanel: React.FC<DedupDiagnosticsProps> = ({
  state,
  compact = false,
  showDroppedEvents = false,
}) => {
  const { current, recent_dropped } = state;

  // ── 空状态 ──
  if (!current) {
    return (
      <div className="p-4 text-center text-gray-400">
        <div className="mb-1 text-lg">📊</div>
        <div className="text-xs">等待 SEDA 指标数据...</div>
      </div>
    );
  }

  const dedupRate = calcDedupRate(current);
  const cacheUsage = calcCacheUsage(current);
  const errorRate = calcParseErrorRate(current);

  const totalEvents = current.events_processed + current.duplicates_suppressed;

  return (
    <div className="space-y-4">
      {/* ═══ 标题栏 ═══ */}
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-gray-700">事件去重诊断</h3>
        <span className="text-xs text-gray-400">
          SEDA · {new Date(current.timestamp_ms).toLocaleTimeString()}
        </span>
      </div>

      {/* ═══ 核心指标网格 ═══ */}
      <div className={`grid gap-2 ${compact ? 'grid-cols-2' : 'grid-cols-2 md:grid-cols-5'}`}>
        {METRICS_DEF.map(({ key, label, fmt }) => (
          <MetricCard
            key={key}
            label={label}
            value={current[key]}
            highlight={key === 'duplicates_suppressed' && current[key] > 0}
          />
        ))}
      </div>

      {/* ═══ 比率指标 ═══ */}
      <div className="space-y-3 rounded-lg border border-slate-200 bg-white p-3">
        <RateBar
          label="去重率"
          rate={dedupRate}
          color="green"
          detail={`总事件 ${totalEvents.toLocaleString()} · 丢弃 ${current.duplicates_suppressed.toLocaleString()}`}
        />
        <RateBar
          label="缓存使用率"
          rate={cacheUsage}
          color="blue"
          detail={`${current.cache_size.toLocaleString()} / ${current.cache_capacity.toLocaleString()} 条目`}
        />
        <RateBar
          label="解析错误率"
          rate={errorRate}
          color="amber"
          detail={`${current.unparseable} 个事件无法解析`}
        />
      </div>

      {/* ═══ 诊断事件列表（可选） ═══ */}
      {showDroppedEvents && recent_dropped.length > 0 && (
        <div className="rounded-lg border border-slate-200 bg-white p-3">
          <h4 className="mb-2 text-xs font-semibold tracking-wide text-gray-500 uppercase">
            最近的丢弃事件
          </h4>
          <DroppedEventsList events={recent_dropped} />
        </div>
      )}

      {/* ═══ 底部提示 ═══ */}
      {!showDroppedEvents && recent_dropped.length === 0 && (
        <div className="text-center text-xs text-gray-400">
          启用 emit_diagnostics 可查看丢弃事件明细
        </div>
      )}
    </div>
  );
};
