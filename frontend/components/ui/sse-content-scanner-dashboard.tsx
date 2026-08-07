'use client';

import React, { useMemo } from 'react';
import type {
  SseContentScannerDashboardProps,
  SseContentScannerDashboardState,
  SseScanSignal,
  SseScannerStats,
  ScannerViewMode,
  DetectionLayer,
  ScannedEventType,
  SseSignalDetailPanelProps,
  ToolSignalRowProps,
} from '@/lib/sse-content-scanner-contract';
import {
  DETECTION_LAYER_LABEL,
  DETECTION_LAYER_COLOR,
  EVENT_TYPE_LABEL,
  signalConfidenceToSeverity,
  signalConfidenceColor,
  signalConfidenceBg,
} from '@/lib/sse-content-scanner-contract';

// ═══════════════════════════════════════════════════════════════════════════
// 常量
// ═══════════════════════════════════════════════════════════════════════════

const VIEW_LABELS: { mode: ScannerViewMode; label: string; icon: string }[] = [
  { mode: 'overview', label: '概览', icon: '📊' },
  { mode: 'signals', label: '信号列表', icon: '🚨' },
  { mode: 'tools', label: '工具分布', icon: '🔧' },
];

const LAYER_DOT: Record<DetectionLayer, string> = {
  pattern: 'bg-indigo-400',
  ood: 'bg-amber-400',
};

const LAYER_BORDER: Record<DetectionLayer, string> = {
  pattern: 'border-l-indigo-400',
  ood: 'border-l-amber-400',
};

const SEVERITY_BADGE: Record<string, string> = {
  critical: 'bg-red-100 text-red-700 border-red-200',
  high: 'bg-orange-100 text-orange-700 border-orange-200',
  medium: 'bg-amber-100 text-amber-700 border-amber-200',
  low: 'bg-slate-100 text-slate-600 border-slate-200',
};

const EVENT_TYPE_ICON: Record<ScannedEventType, string> = {
  tool_result: '📋',
  content_block_delta: '📝',
  text_delta: '💬',
};

// ═══════════════════════════════════════════════════════════════════════════
// 子组件: SummaryCard — 概览卡片
// ═══════════════════════════════════════════════════════════════════════════

const SummaryCard: React.FC<{
  label: string;
  value: number | string;
  unit?: string;
  highlight?: boolean;
  raw?: boolean;
}> = ({ label, value, unit = '', highlight = false, raw = false }) => (
  <div
    className={`rounded-lg border p-3 ${
      highlight ? 'border-red-300 bg-red-50' : 'border-slate-200 bg-white'
    }`}
  >
    <div className="mb-1 text-xs text-gray-500">{label}</div>
    <div className="font-mono text-lg font-semibold">
      {raw
        ? value
        : typeof value === 'number'
          ? value >= 1000
            ? `${(value / 1000).toFixed(1)}k`
            : value
          : value}{' '}
      {unit && <span className="text-xs font-normal text-gray-400">{unit}</span>}
    </div>
  </div>
);

// ═══════════════════════════════════════════════════════════════════════════
// 子组件: DistributionBar — 分布比例条
// ═══════════════════════════════════════════════════════════════════════════

const DistributionBar: React.FC<{
  segments: Array<{ label: string; count: number; color: string }>;
  total: number;
}> = ({ segments, total }) => {
  if (total === 0) return null;

  return (
    <div className="space-y-2">
      {/* 彩色条 */}
      <div className="flex h-2 overflow-hidden rounded-full bg-slate-100">
        {segments.map((seg) => {
          const pct = (seg.count / total) * 100;
          return pct > 0 ? (
            <div
              key={seg.label}
              className={`${seg.color} h-full transition-all`}
              style={{ width: `${pct}%` }}
              title={`${seg.label}: ${seg.count} (${pct.toFixed(1)}%)`}
            />
          ) : null;
        })}
      </div>
      {/* 图例 */}
      <div className="flex flex-wrap items-center gap-3 text-xs text-gray-500">
        {segments.map((seg) => (
          <span key={seg.label} className="flex items-center gap-1">
            <span className={`inline-block h-2 w-2 rounded-full ${seg.color}`} />
            {seg.label}: {seg.count}
          </span>
        ))}
      </div>
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════
// 子组件: ConfidenceBadge — 置信度徽章
// ═══════════════════════════════════════════════════════════════════════════

const ConfidenceBadge: React.FC<{ confidence: number }> = ({ confidence }) => {
  const severity = signalConfidenceToSeverity(confidence);
  const colorClass = SEVERITY_BADGE[severity] ?? SEVERITY_BADGE.low;

  return (
    <span
      className={`inline-flex items-center rounded border px-1.5 py-0.5 text-[10px] font-medium ${colorClass}`}
    >
      {(confidence * 100).toFixed(0)}%
    </span>
  );
};

// ═══════════════════════════════════════════════════════════════════════════
// 子组件: ConfidenceBar — 置信度进度条
// ═══════════════════════════════════════════════════════════════════════════

const ConfidenceBar: React.FC<{ confidence: number; compact?: boolean }> = ({
  confidence,
  compact = false,
}) => {
  const pct = (confidence * 100).toFixed(0);
  const color =
    confidence >= 0.9
      ? 'bg-red-500'
      : confidence >= 0.7
        ? 'bg-orange-500'
        : confidence >= 0.5
          ? 'bg-amber-500'
          : 'bg-slate-300';

  return (
    <div className={`flex items-center gap-2 ${compact ? 'w-16' : 'w-20'}`}>
      <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-100">
        <div
          className={`h-full rounded-full ${color} transition-all`}
          style={{ width: `${Math.min(confidence * 100, 100)}%` }}
        />
      </div>
      <span className={`font-mono ${signalConfidenceColor(confidence)} w-8 text-right text-xs`}>
        {pct}%
      </span>
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════
// 子组件: SignalCard — 信号卡片
// ═══════════════════════════════════════════════════════════════════════════

const SignalCard: React.FC<{
  signal: SseScanSignal;
  isSelected: boolean;
  onSelect: () => void;
  onNavigateToTimeline: (timestamp_ns: number) => void;
  onNavigateToToolExecution: (tool_name: string) => void;
  compact?: boolean;
}> = ({
  signal,
  isSelected,
  onSelect,
  onNavigateToTimeline,
  onNavigateToToolExecution,
  compact = false,
}) => {
  const confidenceColor = signalConfidenceColor(signal.confidence);
  const bgClass = signalConfidenceBg(signal.confidence);

  return (
    <div
      onClick={onSelect}
      className={`cursor-pointer rounded-lg border border-l-4 p-3 transition-all ${
        LAYER_BORDER[signal.detection_layer]
      } ${bgClass} ${isSelected ? 'shadow-sm ring-2 ring-blue-400' : 'hover:shadow-sm'}`}
    >
      {/* ── 头部: 检测层 + 置信度 + 时间 ── */}
      <div className="mb-2 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span
            className={`inline-flex items-center rounded border px-1.5 py-0.5 text-[10px] font-medium ${
              DETECTION_LAYER_COLOR[signal.detection_layer]
            }`}
          >
            {DETECTION_LAYER_LABEL[signal.detection_layer]}
          </span>
          <span className="text-xs text-gray-400">
            {EVENT_TYPE_ICON[signal.event_type]} {EVENT_TYPE_LABEL[signal.event_type]}
          </span>
          {signal.detection_layer === 'ood' && signal.detail.layer === 'ood' && (
            <span className="font-mono text-[10px] text-amber-600">
              分数: {signal.detail.anomaly_score.toFixed(3)}
            </span>
          )}
        </div>
        <ConfidenceBadge confidence={signal.confidence} />
      </div>

      {/* ── 工具名 ── */}
      <div className="mb-1.5 font-mono text-xs text-blue-600">{signal.tool_name}</div>

      {/* ── 检测理由 ── */}
      <div
        className={`text-xs leading-relaxed text-gray-600 ${compact ? 'line-clamp-1' : 'line-clamp-2'}`}
      >
        {signal.reason}
      </div>

      {/* ── 文本片段预览 ── */}
      {signal.snippet_preview && (
        <div className="mt-2 line-clamp-2 rounded border border-slate-200 bg-white/60 p-2 font-mono text-[11px] break-all whitespace-pre-wrap text-gray-500">
          {signal.snippet_preview}
        </div>
      )}

      {/* ── 操作按钮 ── */}
      {isSelected && (
        <div className="mt-2 flex items-center gap-2 border-t border-slate-200/50 pt-2">
          <button
            onClick={(e) => {
              e.stopPropagation();
              onNavigateToTimeline(signal.timestamp_ns);
            }}
            className="rounded border border-slate-200 bg-white px-2 py-1 text-[10px] text-gray-600 transition-colors hover:bg-slate-50"
          >
            ⏱ 定位时间线
          </button>
          <button
            onClick={(e) => {
              e.stopPropagation();
              onNavigateToToolExecution(signal.tool_name);
            }}
            className="rounded border border-slate-200 bg-white px-2 py-1 text-[10px] text-gray-600 transition-colors hover:bg-slate-50"
          >
            🔧 查看工具调用
          </button>
        </div>
      )}
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════
// 子组件: SignalDetailPanel — 信号详情面板
// ═══════════════════════════════════════════════════════════════════════════

export const SseSignalDetailPanel: React.FC<SseSignalDetailPanelProps> = ({
  signal,
  onClose,
  onNavigateToTimeline,
  onNavigateToToolExecution,
}) => {
  return (
    <div className="space-y-3 rounded-lg border border-slate-300 bg-white p-4 shadow-sm">
      {/* 头部 */}
      <div className="flex items-center justify-between">
        <h4 className="text-sm font-semibold text-gray-800">信号详情</h4>
        <button onClick={onClose} className="text-xs text-gray-400 hover:text-gray-600">
          ✕ 关闭
        </button>
      </div>

      {/* 信号 ID */}
      <div className="truncate font-mono text-[10px] text-gray-400">ID: {signal.signal_id}</div>

      {/* 属性网格 */}
      <div className="grid grid-cols-2 gap-2 text-xs">
        <DetailRow label="检测层" value={DETECTION_LAYER_LABEL[signal.detection_layer]} />
        <DetailRow label="事件类型" value={EVENT_TYPE_LABEL[signal.event_type]} />
        <DetailRow label="工具" value={signal.tool_name} mono />
        <DetailRow label="置信度" value={`${(signal.confidence * 100).toFixed(1)}%`} />
        <DetailRow label="检测方法" value={signal.detection_method} fullWidth />
      </div>

      {/* 理由 */}
      <div>
        <div className="mb-1 text-[10px] font-medium text-gray-400">检测理由</div>
        <div className="rounded border border-slate-100 bg-slate-50 p-2 text-xs text-gray-700">
          {signal.reason}
        </div>
      </div>

      {/* 片段 */}
      {signal.snippet_preview && (
        <div>
          <div className="mb-1 text-[10px] font-medium text-gray-400">文本片段</div>
          <div className="max-h-32 overflow-y-auto rounded border border-slate-100 bg-slate-50 p-2 font-mono text-[11px] break-all whitespace-pre-wrap text-gray-600">
            {signal.snippet_preview}
          </div>
        </div>
      )}

      {/* 操作按钮 */}
      <div className="flex items-center gap-2 border-t border-slate-100 pt-2">
        <button
          onClick={() => onNavigateToTimeline(signal.timestamp_ns)}
          className="rounded border border-blue-200 bg-blue-50 px-3 py-1.5 text-xs text-blue-700 transition-colors hover:bg-blue-100"
        >
          ⏱ 定位时间线
        </button>
        <button
          onClick={() => onNavigateToToolExecution(signal.tool_name)}
          className="rounded border border-slate-200 bg-slate-50 px-3 py-1.5 text-xs text-slate-700 transition-colors hover:bg-slate-100"
        >
          🔧 查看工具调用
        </button>
      </div>
    </div>
  );
};

/** 详情行 */
const DetailRow: React.FC<{
  label: string;
  value: string;
  mono?: boolean;
  fullWidth?: boolean;
}> = ({ label, value, mono = false, fullWidth = false }) => (
  <div className={fullWidth ? 'col-span-2' : ''}>
    <div className="text-[10px] text-gray-400">{label}</div>
    <div className={`text-gray-700 ${mono ? 'font-mono' : ''}`}>{value}</div>
  </div>
);

// ═══════════════════════════════════════════════════════════════════════════
// 子组件: ToolSignalRow — 工具-信号关联行
// ═══════════════════════════════════════════════════════════════════════════

export const ToolSignalRow: React.FC<ToolSignalRowProps> = ({
  tool_name,
  signal_count,
  pattern_count,
  ood_count,
  avg_confidence,
  isSelected,
  onSelect,
}) => {
  const total = pattern_count + ood_count;
  const patternPct = total > 0 ? (pattern_count / total) * 100 : 0;

  return (
    <div
      onClick={onSelect}
      className={`cursor-pointer rounded-lg border p-3 transition-all ${
        isSelected
          ? 'border-blue-300 bg-blue-50 ring-1 ring-blue-400'
          : 'border-slate-200 bg-white hover:bg-slate-50'
      }`}
    >
      <div className="mb-2 flex items-center justify-between">
        <span className="flex-1 truncate font-mono text-xs font-semibold text-blue-600">
          {tool_name}
        </span>
        <span className="ml-2 font-mono text-xs text-gray-600">{signal_count} 信号</span>
      </div>

      {/* 层分布条 */}
      <div className="mb-2 flex h-1.5 overflow-hidden rounded-full bg-slate-100">
        {pattern_count > 0 && (
          <div className="h-full bg-indigo-400" style={{ width: `${patternPct}%` }} />
        )}
        {ood_count > 0 && (
          <div className="h-full bg-amber-400" style={{ width: `${100 - patternPct}%` }} />
        )}
      </div>

      <div className="flex items-center justify-between text-[10px] text-gray-400">
        <span>
          模式: {pattern_count} · 离群: {ood_count}
        </span>
        <span className="font-mono">均置信度: {(avg_confidence * 100).toFixed(0)}%</span>
      </div>
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════
// 子组件: OverviewTab — 概览视图
// ═══════════════════════════════════════════════════════════════════════════

const OverviewTab: React.FC<{
  stats: SseScannerStats;
  recent_signals: SseScanSignal[];
  onToolClick: (tool: string) => void;
  onSignalSelect: (signal: SseScanSignal | null) => void;
  selected_signal_id: string | null;
  onNavigateToTimeline: (timestamp_ns: number) => void;
  onNavigateToToolExecution: (tool_name: string) => void;
  compact?: boolean;
}> = ({
  stats,
  recent_signals,
  onToolClick,
  onSignalSelect,
  selected_signal_id,
  onNavigateToTimeline,
  onNavigateToToolExecution,
  compact = false,
}) => {
  const hasData = stats.events_scanned > 0;
  const hasSignals = stats.signals_produced > 0;

  return (
    <div className="space-y-4">
      {/* ── 摘要卡片 ── */}
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        <SummaryCard label="已扫描事件" value={stats.events_scanned} unit="个" />
        <SummaryCard
          label="产生信号"
          value={stats.signals_produced}
          unit="个"
          highlight={stats.signal_rate > 0.05}
        />
        <SummaryCard
          label="信号产出率"
          value={(stats.signal_rate * 100).toFixed(1)}
          unit="%"
          highlight={stats.signal_rate > 0.05}
        />
        <SummaryCard
          label="最后信号"
          value={
            stats.last_signal_ns
              ? new Date(stats.last_signal_ns / 1_000_000).toLocaleTimeString()
              : '—'
          }
          raw
        />
      </div>

      {!hasData && (
        <div className="p-6 text-center text-gray-400">
          <div className="mb-1 text-lg">🔍</div>
          <div className="text-xs">扫描器已就绪，等待 SSE 事件...</div>
          <div className="mt-1 text-xs text-gray-300">
            SseContentScanner 正在监测工具结果、内容块和文本增量
          </div>
        </div>
      )}

      {hasData && !hasSignals && (
        <div className="p-6 text-center text-gray-400">
          <div className="mb-1 text-lg">✅</div>
          <div className="text-xs">
            已扫描 {stats.events_scanned.toLocaleString()} 个事件，未检测到注入信号
          </div>
          <div className="mt-1 text-xs text-gray-300">SSE 内容流安全</div>
        </div>
      )}

      {hasSignals && (
        <>
          {/* ── 检测层分布 ── */}
          <div className="rounded-lg border border-slate-200 bg-white p-4">
            <h4 className="mb-3 text-xs font-semibold tracking-wide text-gray-500 uppercase">
              检测层分布
            </h4>
            <DistributionBar
              segments={[
                {
                  label: '模式匹配',
                  count: stats.by_layer.pattern,
                  color: 'bg-indigo-400',
                },
                {
                  label: '离群检测',
                  count: stats.by_layer.ood,
                  color: 'bg-amber-400',
                },
              ]}
              total={stats.by_layer.pattern + stats.by_layer.ood}
            />
          </div>

          {/* ── 事件类型分布 ── */}
          <div className="rounded-lg border border-slate-200 bg-white p-4">
            <h4 className="mb-3 text-xs font-semibold tracking-wide text-gray-500 uppercase">
              事件类型命中分布
            </h4>
            <DistributionBar
              segments={[
                {
                  label: '工具结果',
                  count: stats.by_event_type.tool_result,
                  color: 'bg-emerald-400',
                },
                {
                  label: '内容块',
                  count: stats.by_event_type.content_block_delta,
                  color: 'bg-blue-400',
                },
                {
                  label: '文本增量',
                  count: stats.by_event_type.text_delta,
                  color: 'bg-purple-400',
                },
              ]}
              total={
                stats.by_event_type.tool_result +
                stats.by_event_type.content_block_delta +
                stats.by_event_type.text_delta
              }
            />
          </div>

          {/* ── 高风险工具 Top-N ── */}
          {stats.top_tools.length > 0 && (
            <div className="rounded-lg border border-slate-200 bg-white p-4">
              <h4 className="mb-3 text-xs font-semibold tracking-wide text-gray-500 uppercase">
                信号最多的工具
              </h4>
              <div className="space-y-1">
                {stats.top_tools.slice(0, 8).map((t, i) => (
                  <button
                    key={t.tool_name}
                    onClick={() => onToolClick(t.tool_name)}
                    className="flex w-full items-center justify-between rounded p-2 text-left text-xs transition-colors hover:bg-slate-50"
                  >
                    <span className="flex items-center gap-2">
                      <span className="w-4 font-mono text-gray-400">{i + 1}.</span>
                      <span className="font-mono text-blue-600">{t.tool_name}</span>
                    </span>
                    <span className="font-mono text-gray-600">{t.signal_count}</span>
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* ── 最近信号预览 ── */}
          {recent_signals.length > 0 && (
            <div className="rounded-lg border border-slate-200 bg-white p-4">
              <h4 className="mb-3 text-xs font-semibold tracking-wide text-gray-500 uppercase">
                最近信号
              </h4>
              <div className="space-y-2">
                {recent_signals.slice(0, 5).map((signal) => (
                  <SignalCard
                    key={signal.signal_id}
                    signal={signal}
                    isSelected={selected_signal_id === signal.signal_id}
                    onSelect={() =>
                      onSignalSelect(selected_signal_id === signal.signal_id ? null : signal)
                    }
                    onNavigateToTimeline={onNavigateToTimeline}
                    onNavigateToToolExecution={onNavigateToToolExecution}
                    compact={compact}
                  />
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════
// 子组件: SignalsTab — 信号列表视图
// ═══════════════════════════════════════════════════════════════════════════

const SignalsTab: React.FC<{
  signals: SseScanSignal[];
  selected_signal_id: string | null;
  onSelect: (signal: SseScanSignal | null) => void;
  onNavigateToTimeline: (timestamp_ns: number) => void;
  onNavigateToToolExecution: (tool_name: string) => void;
  compact?: boolean;
}> = ({
  signals,
  selected_signal_id,
  onSelect,
  onNavigateToTimeline,
  onNavigateToToolExecution,
  compact = false,
}) => {
  const selectedSignal = useMemo(
    () => signals.find((s) => s.signal_id === selected_signal_id) ?? null,
    [signals, selected_signal_id],
  );

  if (signals.length === 0) {
    return (
      <div className="p-6 text-center text-gray-400">
        <div className="mb-1 text-lg">🔍</div>
        <div className="text-xs">无匹配信号</div>
        <div className="mt-1 text-xs text-gray-300">尝试调整筛选条件</div>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {/* 选中信号的详情面板 */}
      {selectedSignal && (
        <SseSignalDetailPanel
          signal={selectedSignal}
          onClose={() => onSelect(null)}
          onNavigateToTimeline={onNavigateToTimeline}
          onNavigateToToolExecution={onNavigateToToolExecution}
        />
      )}

      {/* 信号列表 */}
      <div className="space-y-2">
        {signals.map((signal) => (
          <SignalCard
            key={signal.signal_id}
            signal={signal}
            isSelected={selected_signal_id === signal.signal_id}
            onSelect={() => onSelect(selected_signal_id === signal.signal_id ? null : signal)}
            onNavigateToTimeline={onNavigateToTimeline}
            onNavigateToToolExecution={onNavigateToToolExecution}
            compact={compact}
          />
        ))}
      </div>

      {/* 列表总数 */}
      <div className="text-right text-xs text-gray-400">共 {signals.length} 条信号</div>
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════
// 子组件: ToolsTab — 工具分布视图
// ═══════════════════════════════════════════════════════════════════════════

const ToolsTab: React.FC<{
  top_tools: SseScannerStats['top_tools'];
  stats: SseScannerStats;
  selected_tool: string | null;
  onSelectTool: (tool: string | null) => void;
}> = ({ top_tools, stats, selected_tool, onSelectTool }) => {
  // 计算每个工具的模式/离群分布和均置信度
  const toolDetails = useMemo(() => {
    // 从 top_tools 和全局 stats 中推导每工具详情
    // 注：如果后端未提供每工具详情，则仅展示 top_tools 的简单列表
    return top_tools.map((t) => {
      // 估算：假设信号按 by_layer 比例分布
      const total = stats.by_layer.pattern + stats.by_layer.ood;
      const patternRatio = total > 0 ? stats.by_layer.pattern / total : 0;
      return {
        tool_name: t.tool_name,
        signal_count: t.signal_count,
        pattern_count: Math.round(t.signal_count * patternRatio),
        ood_count: Math.round(t.signal_count * (1 - patternRatio)),
        avg_confidence: 0.5, // 默认值，后端可补充真实值
        latest_signal_ns: 0, // 默认值，后端可补充真实值
      };
    });
  }, [top_tools, stats]);

  if (top_tools.length === 0) {
    return (
      <div className="p-6 text-center text-gray-400">
        <div className="mb-1 text-lg">🔧</div>
        <div className="text-xs">暂无工具级分布数据</div>
        <div className="mt-1 text-xs text-gray-300">产生信号后将按工具名聚合展示</div>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {toolDetails.map((tool) => (
        <ToolSignalRow
          key={tool.tool_name}
          {...tool}
          isSelected={selected_tool === tool.tool_name}
          onSelect={() => onSelectTool(selected_tool === tool.tool_name ? null : tool.tool_name)}
        />
      ))}
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════
// 子组件: FilterBar — 信号筛选栏
// ═══════════════════════════════════════════════════════════════════════════

const FilterBar: React.FC<{
  layer_filter: DetectionLayer | null;
  event_type_filter: ScannedEventType | null;
  tool_filter: string | null;
  min_confidence_filter: number;
  availableTools: string[];
  onLayerFilterChange: (layer: DetectionLayer | null) => void;
  onEventTypeFilterChange: (type: ScannedEventType | null) => void;
  onToolFilterChange: (tool: string | null) => void;
  onMinConfidenceChange: (confidence: number) => void;
}> = ({
  layer_filter,
  event_type_filter,
  tool_filter,
  min_confidence_filter,
  availableTools,
  onLayerFilterChange,
  onEventTypeFilterChange,
  onToolFilterChange,
  onMinConfidenceChange,
}) => {
  const hasActiveFilters =
    layer_filter !== null ||
    event_type_filter !== null ||
    tool_filter !== null ||
    min_confidence_filter > 0;

  return (
    <div className="space-y-2 rounded-lg border border-slate-200 bg-white p-3">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-gray-500">筛选</span>
        {hasActiveFilters && (
          <button
            onClick={() => {
              onLayerFilterChange(null);
              onEventTypeFilterChange(null);
              onToolFilterChange(null);
              onMinConfidenceChange(0);
            }}
            className="text-[10px] text-blue-500 hover:text-blue-700"
          >
            清除全部
          </button>
        )}
      </div>

      {/* ── 检测层筛选 ── */}
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-[10px] text-gray-400">检测层:</span>
        {(['pattern', 'ood'] as DetectionLayer[]).map((layer) => (
          <button
            key={layer}
            onClick={() => onLayerFilterChange(layer === layer_filter ? null : layer)}
            className={`rounded border px-2 py-0.5 text-[10px] transition-colors ${
              layer_filter === layer
                ? 'border-blue-600 bg-blue-600 text-white'
                : 'border-slate-200 text-gray-500 hover:border-slate-300'
            }`}
          >
            {DETECTION_LAYER_LABEL[layer]}
          </button>
        ))}
      </div>

      {/* ── 事件类型筛选 ── */}
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-[10px] text-gray-400">事件类型:</span>
        {(['tool_result', 'content_block_delta', 'text_delta'] as ScannedEventType[]).map(
          (type) => (
            <button
              key={type}
              onClick={() => onEventTypeFilterChange(type === event_type_filter ? null : type)}
              className={`rounded border px-2 py-0.5 text-[10px] transition-colors ${
                event_type_filter === type
                  ? 'border-blue-600 bg-blue-600 text-white'
                  : 'border-slate-200 text-gray-500 hover:border-slate-300'
              }`}
            >
              {EVENT_TYPE_LABEL[type]}
            </button>
          ),
        )}
      </div>

      {/* ── 置信度阈值 ── */}
      <div className="flex items-center gap-2">
        <span className="text-[10px] whitespace-nowrap text-gray-400">
          最低置信度: {(min_confidence_filter * 100).toFixed(0)}%
        </span>
        <input
          type="range"
          min={0}
          max={100}
          step={5}
          value={Math.round(min_confidence_filter * 100)}
          onChange={(e) => onMinConfidenceChange(Number(e.target.value) / 100)}
          className="h-1 flex-1 accent-blue-500"
        />
      </div>

      {/* ── 工具筛选 ── */}
      {availableTools.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-[10px] text-gray-400">工具:</span>
          <select
            value={tool_filter ?? ''}
            onChange={(e) => onToolFilterChange(e.target.value || null)}
            className="rounded border border-slate-200 px-1.5 py-0.5 text-[10px] text-gray-600"
          >
            <option value="">全部</option>
            {availableTools.map((tool) => (
              <option key={tool} value={tool}>
                {tool}
              </option>
            ))}
          </select>
        </div>
      )}
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════
// 主组件: SseContentScannerDashboard
// ═══════════════════════════════════════════════════════════════════════════

export const SseContentScannerDashboard: React.FC<SseContentScannerDashboardProps> = ({
  state,
  onViewModeChange,
  onSignalSelect,
  onLayerFilterChange,
  onEventTypeFilterChange,
  onToolFilterChange,
  onMinConfidenceChange,
  onNavigateToTimeline,
  onNavigateToToolExecution,
  compact = false,
}) => {
  const {
    view_mode,
    recent_signals,
    stats,
    selected_signal_id,
    layer_filter,
    event_type_filter,
    tool_filter,
    min_confidence_filter,
  } = state;

  // ── 过滤后的信号列表 ──
  const filteredSignals = useMemo(() => {
    return recent_signals.filter((s) => {
      if (layer_filter && s.detection_layer !== layer_filter) return false;
      if (event_type_filter && s.event_type !== event_type_filter) return false;
      if (tool_filter && s.tool_name !== tool_filter) return false;
      if (s.confidence < min_confidence_filter) return false;
      return true;
    });
  }, [recent_signals, layer_filter, event_type_filter, tool_filter, min_confidence_filter]);

  // ── 可用工具列表（从信号中提取）──
  const availableTools = useMemo(() => {
    const tools = new Set(recent_signals.map((s) => s.tool_name));
    return Array.from(tools).sort();
  }, [recent_signals]);

  // ── 从概览点击工具名 → 切换到 signals 视图 + 设置工具过滤 ──
  const handleOverviewToolClick = (tool: string) => {
    onToolFilterChange(tool);
    onViewModeChange('signals');
  };

  // ── 从工具视图选择工具 ──
  const handleToolSelect = (tool: string | null) => {
    if (tool) {
      onToolFilterChange(tool);
      onViewModeChange('signals');
    } else {
      onToolFilterChange(null);
    }
  };

  return (
    <div className="space-y-4">
      {/* ═══ 顶部 Tab 导航 ═══ */}
      <div className="flex flex-wrap items-center gap-1 border-b border-slate-200 pb-2">
        {VIEW_LABELS.map(({ mode, label, icon }) => {
          const hasData =
            mode === 'overview'
              ? stats.events_scanned > 0
              : mode === 'signals'
                ? recent_signals.length > 0
                : stats.top_tools.length > 0;

          return (
            <button
              key={mode}
              onClick={() => onViewModeChange(mode)}
              className={`flex items-center gap-1.5 rounded-md px-3 py-2 text-xs font-medium transition-all ${
                view_mode === mode
                  ? 'bg-blue-600 text-white shadow-sm'
                  : hasData
                    ? 'text-slate-600 hover:bg-slate-100 hover:text-slate-900'
                    : 'text-slate-400'
              }`}
            >
              <span>{icon}</span>
              <span className={compact ? 'hidden md:inline' : ''}>{label}</span>
              {/* 数据徽章 */}
              {mode === 'signals' && filteredSignals.length > 0 && (
                <span
                  className={`inline-flex h-5 w-5 items-center justify-center rounded-full text-[10px] font-bold ${
                    view_mode === mode ? 'bg-white/20 text-white' : 'bg-red-100 text-red-700'
                  }`}
                >
                  {filteredSignals.length}
                </span>
              )}
              {mode === 'tools' && stats.top_tools.length > 0 && (
                <span
                  className={`inline-flex h-5 w-5 items-center justify-center rounded-full text-[10px] font-bold ${
                    view_mode === mode ? 'bg-white/20 text-white' : 'bg-indigo-100 text-indigo-700'
                  }`}
                >
                  {stats.top_tools.length}
                </span>
              )}
            </button>
          );
        })}
      </div>

      {/* ═══ 筛选栏（仅 signals 视图） ═══ */}
      {view_mode === 'signals' && (
        <FilterBar
          layer_filter={layer_filter}
          event_type_filter={event_type_filter}
          tool_filter={tool_filter}
          min_confidence_filter={min_confidence_filter}
          availableTools={availableTools}
          onLayerFilterChange={onLayerFilterChange}
          onEventTypeFilterChange={onEventTypeFilterChange}
          onToolFilterChange={onToolFilterChange}
          onMinConfidenceChange={onMinConfidenceChange}
        />
      )}

      {/* ═══ 视图内容 ═══ */}
      <div className={compact ? 'max-h-[60vh] overflow-y-auto' : ''}>
        {/* ── 概览视图 ── */}
        {view_mode === 'overview' && (
          <OverviewTab
            stats={stats}
            recent_signals={recent_signals}
            onToolClick={handleOverviewToolClick}
            onSignalSelect={onSignalSelect}
            selected_signal_id={selected_signal_id}
            onNavigateToTimeline={onNavigateToTimeline}
            onNavigateToToolExecution={onNavigateToToolExecution}
            compact={compact}
          />
        )}

        {/* ── 信号列表视图 ── */}
        {view_mode === 'signals' && (
          <SignalsTab
            signals={filteredSignals}
            selected_signal_id={selected_signal_id}
            onSelect={onSignalSelect}
            onNavigateToTimeline={onNavigateToTimeline}
            onNavigateToToolExecution={onNavigateToToolExecution}
            compact={compact}
          />
        )}

        {/* ── 工具分布视图 ── */}
        {view_mode === 'tools' && (
          <ToolsTab
            top_tools={stats.top_tools}
            stats={stats}
            selected_tool={tool_filter}
            onSelectTool={handleToolSelect}
          />
        )}
      </div>

      {/* ═══ 底部状态栏 ═══ */}
      <div className="flex items-center justify-between border-t border-slate-100 pt-2 text-xs text-gray-400">
        <span>
          {view_mode === 'overview' &&
            `已扫描 ${stats.events_scanned.toLocaleString()} · 信号 ${stats.signals_produced.toLocaleString()}`}
          {view_mode === 'signals' &&
            `${filteredSignals.length} 条信号${recent_signals.length !== filteredSignals.length ? ` (共 ${recent_signals.length} 条)` : ''}`}
          {view_mode === 'tools' &&
            `${stats.top_tools.length} 个工具 · ${stats.signals_produced} 条信号`}
        </span>
        <span>{new Date(state.last_update_ms).toLocaleTimeString()}</span>
      </div>
    </div>
  );
};
