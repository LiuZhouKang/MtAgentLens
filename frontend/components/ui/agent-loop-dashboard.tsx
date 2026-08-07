'use client';

import React, { useMemo } from 'react';
import type {
  AgentLoopDashboardProps,
  AgentLoopDashboardState,
  LoopCardProps,
  LoopDetailPanelProps,
  LoopSeverityBadgeProps,
  LoopTypeIconProps,
  LoopToolFrequencyChartProps,
  LoopSummary,
  LoopType,
  LoopSeverity,
  LoopDashboardViewMode,
} from '@/lib/agent-loop-contract';
import {
  LOOP_TYPE_LABEL,
  LOOP_TYPE_ICON,
  SEVERITY_LABEL,
  RESOLUTION_LABEL,
  OUTCOME_LABEL,
  loopSeverityToAnomalySeverity,
  describeLoopType,
  calcLoopDurationMs,
  formatDurationMs,
  countBySeverity,
  countByLoopType,
  aggregateLoopFrequency,
  maxSeverity,
} from '@/lib/agent-loop-contract';

// ═══════════════════════════════════════════════════════════════════════════
// 常量
// ═══════════════════════════════════════════════════════════════════════════

const VIEW_LABELS: { mode: LoopDashboardViewMode; label: string }[] = [
  { mode: 'overview', label: '概览' },
  { mode: 'timeline', label: '时间线' },
  { mode: 'tools', label: '工具焦点' },
];

const SEVERITY_RING: Record<LoopSeverity, string> = {
  Warning: 'ring-amber-400',
  Critical: 'ring-orange-500',
  Emergency: 'ring-red-600',
};

const SEVERITY_BG: Record<LoopSeverity, string> = {
  Warning: 'bg-amber-50 border-amber-200',
  Critical: 'bg-orange-50 border-orange-200',
  Emergency: 'bg-red-50 border-red-200',
};

const SEVERITY_DOT: Record<LoopSeverity, string> = {
  Warning: 'bg-amber-400',
  Critical: 'bg-orange-500 animate-pulse',
  Emergency: 'bg-red-600 animate-pulse',
};

const SEVERITY_BORDER: Record<LoopSeverity, string> = {
  Warning: 'border-l-amber-400',
  Critical: 'border-l-orange-500',
  Emergency: 'border-l-red-600',
};

const LOOP_TYPE_COLOR: Record<LoopType, string> = {
  ExactRepeat: 'text-indigo-600 bg-indigo-50',
  FuzzyRepeat: 'text-violet-600 bg-violet-50',
  PingPong: 'text-teal-600 bg-teal-50',
  RetryStorm: 'text-rose-600 bg-rose-50',
};

// ═══════════════════════════════════════════════════════════════════════════
// 子组件: LoopSeverityBadge — 严重度徽章
// ═══════════════════════════════════════════════════════════════════════════

/** @deprecated 阶段二实时视角（SSE 接入后迁移至运行监控区块），现无生产者，仅存档。 */
export const LoopSeverityBadge: React.FC<LoopSeverityBadgeProps> = ({
  severity,
  showLabel = true,
}) => {
  const colors: Record<LoopSeverity, string> = {
    Warning: 'bg-amber-100 text-amber-800',
    Critical: 'bg-orange-100 text-orange-800',
    Emergency: 'bg-red-100 text-red-800',
  };

  return (
    <span
      className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium ${colors[severity]}`}
    >
      <span className={`inline-block h-1.5 w-1.5 rounded-full ${SEVERITY_DOT[severity]}`} />
      {showLabel && SEVERITY_LABEL[severity]}
    </span>
  );
};

// ═══════════════════════════════════════════════════════════════════════════
// 子组件: LoopTypeIcon — 循环类型图标
// ═══════════════════════════════════════════════════════════════════════════

export const LoopTypeIcon: React.FC<LoopTypeIconProps> = ({ loop_type, size = 'md' }) => {
  const sizeClass = size === 'sm' ? 'text-sm' : size === 'lg' ? 'text-xl' : 'text-base';
  return (
    <span className={sizeClass} title={LOOP_TYPE_LABEL[loop_type]}>
      {LOOP_TYPE_ICON[loop_type]}
    </span>
  );
};

// ═══════════════════════════════════════════════════════════════════════════
// 子组件: LoopCard — 单次循环卡片
// ═══════════════════════════════════════════════════════════════════════════

export const LoopCard: React.FC<LoopCardProps> = ({ loop, selected, onSelect }) => {
  const duration = calcLoopDurationMs(loop);
  const anomalySeverity = loopSeverityToAnomalySeverity(loop.severity);
  const borderColor =
    anomalySeverity === 'critical'
      ? 'border-red-300'
      : anomalySeverity === 'warning'
        ? 'border-yellow-300'
        : 'border-gray-200';

  return (
    <button
      onClick={onSelect}
      className={`w-full cursor-pointer rounded border border-l-4 p-3 text-left transition-all ${SEVERITY_BORDER[loop.severity]} ${selected ? 'bg-blue-50 ring-2 ring-blue-400' : 'bg-white hover:bg-slate-50'} `}
    >
      {/* 顶部行：类型 + 工具名 + 严重度 */}
      <div className="mb-1.5 flex items-center gap-2">
        <LoopTypeIcon loop_type={loop.loop_type} size="sm" />
        <span className="truncate font-mono text-sm font-semibold">{loop.tool_name}</span>
        <LoopSeverityBadge severity={loop.severity} />
        {loop.resolved && (
          <span className="ml-auto flex-shrink-0 text-xs font-medium text-emerald-600">
            ✓ 已解决
          </span>
        )}
        {!loop.resolved && (
          <span className="ml-auto flex-shrink-0 text-xs font-medium text-amber-600">● 活跃中</span>
        )}
      </div>

      {/* 中间行：描述 + 重复次数 */}
      <div className="mb-1.5 text-xs text-gray-500">
        {describeLoopType(loop.loop_type)} · 重复{' '}
        <span className="font-semibold text-gray-700">{loop.repeat_count}</span> 次
      </div>

      {/* 底部行：时长 + Agent */}
      <div className="flex items-center gap-3 text-xs text-gray-400">
        <span>⏱ {formatDurationMs(duration)}</span>
        {loop.affected_pids.length > 0 && (
          <span className="font-mono">PID: {loop.affected_pids.join(', ')}</span>
        )}
      </div>

      {/* 解决信息 */}
      {loop.resolved && loop.resolution && (
        <div className="mt-1.5 border-t border-slate-100 pt-1.5 text-xs text-gray-400">
          解除: {RESOLUTION_LABEL[loop.resolution]}
          {loop.final_outcome && ` → ${OUTCOME_LABEL[loop.final_outcome]}`}
        </div>
      )}
    </button>
  );
};

// ═══════════════════════════════════════════════════════════════════════════
// 子组件: LoopDetailPanel — 循环详情面板
// ═══════════════════════════════════════════════════════════════════════════

export const LoopDetailPanel: React.FC<LoopDetailPanelProps> = ({ loop, onToolNameClick }) => {
  const duration = calcLoopDurationMs(loop);

  return (
    <div className={`rounded-lg border p-4 ${SEVERITY_BG[loop.severity]} space-y-4`}>
      {/* 标题行 */}
      <div className="flex items-center gap-2">
        <LoopTypeIcon loop_type={loop.loop_type} size="lg" />
        <div>
          <h3 className="text-sm font-semibold">{LOOP_TYPE_LABEL[loop.loop_type]}</h3>
          <button
            onClick={() => onToolNameClick?.(loop.tool_name)}
            className="cursor-pointer font-mono text-sm text-blue-600 hover:underline"
          >
            {loop.tool_name}
          </button>
        </div>
        <div className="ml-auto flex items-center gap-2">
          <LoopSeverityBadge severity={loop.severity} />
          {loop.resolved ? (
            <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-xs font-medium text-emerald-700">
              已解决
            </span>
          ) : (
            <span className="rounded bg-amber-100 px-1.5 py-0.5 text-xs font-medium text-amber-700">
              活跃中
            </span>
          )}
        </div>
      </div>

      {/* 关键指标网格 */}
      <div className="grid grid-cols-2 gap-2 text-xs">
        <div className="rounded bg-white/60 p-2">
          <div className="text-gray-500">重复次数</div>
          <div className="font-mono text-lg font-semibold">{loop.repeat_count}</div>
        </div>
        <div className="rounded bg-white/60 p-2">
          <div className="text-gray-500">持续时长</div>
          <div className="font-mono text-lg font-semibold">{formatDurationMs(duration)}</div>
        </div>
        <div className="rounded bg-white/60 p-2">
          <div className="text-gray-500">涉及 Agent</div>
          <div className="font-mono font-semibold">
            {loop.affected_pids.length > 0 ? loop.affected_pids.join(', ') : '未知'}
          </div>
        </div>
        <div className="rounded bg-white/60 p-2">
          <div className="text-gray-500">返回错误</div>
          <div className="font-mono font-semibold">
            {loop.context.error_responses} / {loop.repeat_count}
          </div>
        </div>
      </div>

      {/* 上下文详情 */}
      <div className="space-y-1 text-xs text-gray-600">
        <div className="flex justify-between">
          <span>穿插的不同工具调用</span>
          <span className="font-mono">{loop.context.interleaved_different_calls}</span>
        </div>
        <div className="flex justify-between">
          <span>文件修改次数</span>
          <span className="font-mono">{loop.context.file_modifications}</span>
        </div>
        {loop.resolved && loop.resolution && (
          <>
            <div className="flex justify-between">
              <span>解决方式</span>
              <span className="font-medium">{RESOLUTION_LABEL[loop.resolution]}</span>
            </div>
            {loop.final_outcome && (
              <div className="flex justify-between">
                <span>最终结果</span>
                <span className="font-medium">{OUTCOME_LABEL[loop.final_outcome]}</span>
              </div>
            )}
          </>
        )}
      </div>

      {/* 采样载荷 */}
      {loop.sample_payloads.length > 0 && (
        <div>
          <h4 className="mb-2 text-xs font-semibold tracking-wide text-gray-500 uppercase">
            调用采样
          </h4>
          <div className="space-y-1.5">
            {loop.sample_payloads.slice(0, 5).map((p, i) => (
              <div
                key={i}
                className="rounded border border-slate-100 bg-white/80 p-2 font-mono text-xs text-gray-700"
              >
                <div className="mb-0.5 text-gray-400">
                  #{i + 1} · {new Date(p.timestamp_ns / 1_000_000).toLocaleTimeString()}
                </div>
                {Object.entries(p.args)
                  .slice(0, 3)
                  .map(([k, v]) => (
                    <div key={k} className="truncate">
                      <span className="text-gray-400">{k}</span>
                      <span className="text-gray-600"> = </span>
                      <span>{JSON.stringify(v)}</span>
                    </div>
                  ))}
                {Object.keys(p.args).length > 3 && (
                  <div className="text-gray-400">
                    ... 还有 {Object.keys(p.args).length - 3} 个参数
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════
// 子组件: LoopToolFrequencyChart — 工具循环频率图（纯 CSS 条形图）
// ═══════════════════════════════════════════════════════════════════════════

export const LoopToolFrequencyChart: React.FC<LoopToolFrequencyChartProps> = ({
  frequency,
  selected_tool,
  onToolSelect,
}) => {
  const entries = Object.entries(frequency).sort((a, b) => b[1] - a[1]);
  const maxCount = entries[0]?.[1] ?? 1;

  if (entries.length === 0) {
    return <div className="p-4 text-sm text-gray-400">暂无工具循环数据</div>;
  }

  return (
    <div className="space-y-2">
      <h4 className="text-xs font-semibold tracking-wide text-gray-500 uppercase">循环频率排行</h4>
      {entries.map(([name, count]) => {
        const isSelected = selected_tool === name;
        return (
          <button
            key={name}
            onClick={() => onToolSelect(isSelected ? null : name)}
            className="group w-full text-left"
          >
            <div className="mb-1 flex items-center justify-between text-xs">
              <span
                className={`truncate font-mono ${
                  isSelected ? 'font-semibold text-blue-600' : 'text-gray-700'
                }`}
              >
                {name}
              </span>
              <span className="ml-2 flex-shrink-0 text-gray-400">{count} 次</span>
            </div>
            <div className="h-1.5 overflow-hidden rounded-full bg-slate-100">
              <div
                className={`h-full rounded-full transition-all ${
                  isSelected ? 'bg-blue-500' : 'bg-slate-400'
                }`}
                style={{ width: `${(count / maxCount) * 100}%` }}
              />
            </div>
          </button>
        );
      })}
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════
// 子组件: SeverityDistribution — 严重度分布图（环形或条形）
// ═══════════════════════════════════════════════════════════════════════════

const SeverityDistribution: React.FC<{
  activeCounts: Record<LoopSeverity, number>;
  resolvedCounts: Record<LoopSeverity, number>;
}> = ({ activeCounts, resolvedCounts }) => {
  const severities: { key: LoopSeverity; color: string; bg: string }[] = [
    { key: 'Emergency', color: 'bg-red-500', bg: 'bg-red-50' },
    { key: 'Critical', color: 'bg-orange-500', bg: 'bg-orange-50' },
    { key: 'Warning', color: 'bg-amber-400', bg: 'bg-amber-50' },
  ];

  const totalActive = Object.values(activeCounts).reduce((a, b) => a + b, 0);
  const totalResolved = Object.values(resolvedCounts).reduce((a, b) => a + b, 0);

  return (
    <div className="space-y-3">
      {/* 活跃循环分布 */}
      <div>
        <div className="mb-1.5 flex items-center justify-between">
          <h4 className="text-xs font-semibold tracking-wide text-gray-500 uppercase">
            活跃循环 ({totalActive})
          </h4>
        </div>
        <div className="flex h-2 overflow-hidden rounded-full bg-slate-100">
          {severities.map(({ key, color }) => {
            const pct = totalActive > 0 ? (activeCounts[key] / totalActive) * 100 : 0;
            return pct > 0 ? (
              <div
                key={key}
                className={`h-full ${color} transition-all`}
                style={{ width: `${pct}%` }}
                title={`${SEVERITY_LABEL[key]}: ${activeCounts[key]}`}
              />
            ) : null;
          })}
        </div>
        <div className="mt-1.5 flex gap-3 text-xs text-gray-500">
          {severities.map(({ key, color }) => (
            <div key={key} className="flex items-center gap-1">
              <span className={`inline-block h-2 w-2 rounded-full ${color}`} />
              {SEVERITY_LABEL[key]} {activeCounts[key]}
            </div>
          ))}
        </div>
      </div>

      {/* 已解决统计 */}
      {totalResolved > 0 && (
        <div className="text-xs text-gray-400">已解决: {totalResolved} 个循环</div>
      )}
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════
// 子组件: LoopTimeline — 循环时间线
// ═══════════════════════════════════════════════════════════════════════════

const LoopTimeline: React.FC<{
  loops: LoopSummary[];
  selected_loop_id: string | null;
  onSelect: (loop: LoopSummary) => void;
}> = ({ loops, selected_loop_id, onSelect }) => {
  const sorted = useMemo(
    () => [...loops].sort((a, b) => b.last_occurrence_ns - a.last_occurrence_ns),
    [loops],
  );

  if (sorted.length === 0) {
    return <div className="p-4 text-sm text-gray-400">暂无循环检测记录</div>;
  }

  return (
    <div className="max-h-[60vh] space-y-2 overflow-y-auto">
      {sorted.map((loop) => (
        <LoopCard
          key={loop.loop_id}
          loop={loop}
          selected={loop.loop_id === selected_loop_id}
          onSelect={() => onSelect(loop)}
        />
      ))}
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════
// 主组件: AgentLoopDashboard
// ═══════════════════════════════════════════════════════════════════════════

export const AgentLoopDashboard: React.FC<AgentLoopDashboardProps> = ({
  state,
  onViewModeChange,
  onLoopSelect,
  onTypeFilterChange,
  onSeverityFilterChange,
  onToolFilterChange,
  onPidFilterChange,
  onShowActiveOnlyChange,
  compact = false,
}) => {
  const {
    active_loops,
    resolved_loops,
    view_mode,
    selected_loop_id,
    type_filter,
    severity_filter,
    tool_filter,
    pid_filter,
    show_active_only,
  } = state;

  // ── 过滤后的循环 ──
  const filteredLoops = useMemo(() => {
    let loops = show_active_only ? active_loops : [...active_loops, ...resolved_loops];
    if (type_filter) loops = loops.filter((l) => l.loop_type === type_filter);
    if (severity_filter) loops = loops.filter((l) => l.severity === severity_filter);
    if (tool_filter) loops = loops.filter((l) => l.tool_name === tool_filter);
    if (pid_filter != null) loops = loops.filter((l) => l.affected_pids.includes(pid_filter));
    return loops;
  }, [
    active_loops,
    resolved_loops,
    show_active_only,
    type_filter,
    severity_filter,
    tool_filter,
    pid_filter,
  ]);

  // ── 选中的循环 ──
  const selectedLoop = useMemo(
    () =>
      selected_loop_id ? (filteredLoops.find((l) => l.loop_id === selected_loop_id) ?? null) : null,
    [filteredLoops, selected_loop_id],
  );

  // ── 统计数据 ──
  const activeSeverityCounts = useMemo(() => countBySeverity(active_loops), [active_loops]);
  const resolvedSeverityCounts = useMemo(() => countBySeverity(resolved_loops), [resolved_loops]);
  const loopTypeCounts = useMemo(() => countByLoopType(filteredLoops), [filteredLoops]);
  const overallSeverity = useMemo(() => maxSeverity(active_loops), [active_loops]);
  const toolFrequency = useMemo(() => aggregateLoopFrequency(filteredLoops), [filteredLoops]);

  // ── 工具名列表（用于过滤器）──
  const toolNames = useMemo(() => {
    const names = new Set<string>();
    for (const l of [...active_loops, ...resolved_loops]) names.add(l.tool_name);
    return Array.from(names).sort();
  }, [active_loops, resolved_loops]);

  // ── PID 列表（用于过滤器）──
  const allPids = useMemo(() => {
    const pids = new Set<number>();
    for (const l of [...active_loops, ...resolved_loops]) {
      for (const pid of l.affected_pids) pids.add(pid);
    }
    return Array.from(pids).sort((a, b) => a - b);
  }, [active_loops, resolved_loops]);

  // ── 空状态 ──
  if (active_loops.length === 0 && resolved_loops.length === 0) {
    return (
      <div className="p-6 text-center text-gray-400">
        <div className="mb-2 text-2xl">✅</div>
        <div className="text-sm">未检测到工具循环</div>
        <div className="mt-1 text-xs">AgentLoopDetector 正在监控中...</div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* ═══ 顶部状态栏 ═══ */}
      {overallSeverity && active_loops.length > 0 && (
        <div
          className={`flex items-center gap-3 rounded-lg border p-3 text-sm ${
            SEVERITY_BG[overallSeverity]
          }`}
        >
          <span className="text-lg">⚠️</span>
          <div className="flex-1">
            <span className="font-semibold">{active_loops.length} 个活跃循环</span>
            <span className="ml-2 text-gray-500">
              · 最高严重度: {SEVERITY_LABEL[overallSeverity]}
            </span>
          </div>
          <span className="text-xs text-gray-400">已解决 {resolved_loops.length} 个</span>
        </div>
      )}

      {/* ═══ 顶部工具栏 ═══ */}
      <div className="flex flex-wrap items-center gap-3">
        {/* 视图切换 */}
        <div className="flex rounded-lg bg-slate-100 p-0.5">
          {VIEW_LABELS.map(({ mode, label }) => (
            <button
              key={mode}
              onClick={() => onViewModeChange(mode)}
              className={`rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
                view_mode === mode
                  ? 'bg-white text-slate-900 shadow-sm'
                  : 'text-slate-500 hover:text-slate-700'
              }`}
            >
              {label}
            </button>
          ))}
        </div>

        {/* 活跃/全部 切换 */}
        <label className="flex cursor-pointer items-center gap-1.5 text-xs text-gray-600">
          <input
            type="checkbox"
            checked={show_active_only}
            onChange={(e) => onShowActiveOnlyChange(e.target.checked)}
            className="rounded border-slate-300"
          />
          仅活跃
        </label>

        {/* 循环类型过滤 */}
        <select
          className="rounded border border-slate-300 bg-white px-2 py-1.5 text-xs"
          value={type_filter ?? ''}
          onChange={(e) => {
            const v = e.target.value;
            onTypeFilterChange(v ? (v as LoopType) : null);
          }}
        >
          <option value="">全部类型</option>
          {Object.entries(LOOP_TYPE_LABEL).map(([k, v]) => (
            <option key={k} value={k}>
              {LOOP_TYPE_ICON[k as LoopType]} {v}
            </option>
          ))}
        </select>

        {/* 严重度过滤 */}
        <select
          className="rounded border border-slate-300 bg-white px-2 py-1.5 text-xs"
          value={severity_filter ?? ''}
          onChange={(e) => {
            const v = e.target.value;
            onSeverityFilterChange(v ? (v as LoopSeverity) : null);
          }}
        >
          <option value="">全部严重度</option>
          {Object.entries(SEVERITY_LABEL).map(([k, v]) => (
            <option key={k} value={k}>
              {v}
            </option>
          ))}
        </select>

        {/* 工具名过滤 */}
        {toolNames.length > 0 && (
          <select
            className="rounded border border-slate-300 bg-white px-2 py-1.5 text-xs"
            value={tool_filter ?? ''}
            onChange={(e) => {
              const v = e.target.value;
              onToolFilterChange(v || null);
            }}
          >
            <option value="">全部工具</option>
            {toolNames.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        )}

        {/* PID 过滤 */}
        {allPids.length > 0 && (
          <select
            className="rounded border border-slate-300 bg-white px-2 py-1.5 text-xs"
            value={pid_filter ?? ''}
            onChange={(e) => {
              const v = e.target.value;
              onPidFilterChange(v ? Number(v) : null);
            }}
          >
            <option value="">全部 Agent</option>
            {allPids.map((pid) => (
              <option key={pid} value={pid}>
                PID {pid}
              </option>
            ))}
          </select>
        )}
      </div>

      {/* ═══ 概览视图 ═══ */}
      {view_mode === 'overview' && (
        <div className={`grid gap-4 ${compact ? 'grid-cols-1' : 'grid-cols-1 md:grid-cols-2'}`}>
          {/* 严重度分布 */}
          <div className="rounded-lg border border-slate-200 bg-white p-4">
            <SeverityDistribution
              activeCounts={activeSeverityCounts}
              resolvedCounts={resolvedSeverityCounts}
            />
          </div>

          {/* 循环类型分布 */}
          <div className="rounded-lg border border-slate-200 bg-white p-4">
            <h4 className="mb-3 text-xs font-semibold tracking-wide text-gray-500 uppercase">
              循环类型分布
            </h4>
            <div className="space-y-2">
              {(Object.entries(loopTypeCounts) as [LoopType, number][]).map(([type, count]) => (
                <div key={type} className="flex items-center gap-2 text-xs">
                  <LoopTypeIcon loop_type={type} size="sm" />
                  <span className="flex-1 text-gray-600">{LOOP_TYPE_LABEL[type]}</span>
                  <span className="font-mono font-semibold">{count}</span>
                  <div className="h-1.5 w-20 overflow-hidden rounded-full bg-slate-100">
                    <div
                      className="h-full rounded-full bg-slate-400"
                      style={{
                        width: `${filteredLoops.length > 0 ? (count / filteredLoops.length) * 100 : 0}%`,
                      }}
                    />
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* 选中的循环详情 */}
          {selectedLoop && (
            <div className={compact ? '' : 'md:col-span-2'}>
              <LoopDetailPanel
                loop={selectedLoop}
                onToolNameClick={(name) => onToolFilterChange(name)}
              />
            </div>
          )}
        </div>
      )}

      {/* ═══ 时间线视图 ═══ */}
      {view_mode === 'timeline' && (
        <div
          className={`grid gap-4 ${
            compact || !selectedLoop ? 'grid-cols-1' : 'grid-cols-1 md:grid-cols-2'
          }`}
        >
          <LoopTimeline
            loops={filteredLoops}
            selected_loop_id={selected_loop_id}
            onSelect={onLoopSelect}
          />
          {selectedLoop && !compact && (
            <LoopDetailPanel
              loop={selectedLoop}
              onToolNameClick={(name) => onToolFilterChange(name)}
            />
          )}
        </div>
      )}

      {/* ═══ 工具焦点视图 ═══ */}
      {view_mode === 'tools' && (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <div className="rounded-lg border border-slate-200 bg-white p-4">
            <LoopToolFrequencyChart
              frequency={toolFrequency}
              selected_tool={tool_filter}
              onToolSelect={onToolFilterChange}
            />
          </div>
          {selectedLoop && (
            <LoopDetailPanel
              loop={selectedLoop}
              onToolNameClick={(name) => onToolFilterChange(name)}
            />
          )}
        </div>
      )}

      {/* ═══ 底部状态栏 ═══ */}
      <div className="flex items-center justify-between border-t border-slate-100 pt-2 text-xs text-gray-400">
        <span>
          活跃 {active_loops.length} · 已解决 {resolved_loops.length}
        </span>
        <span>
          显示 {filteredLoops.length} 条
          {(type_filter || severity_filter || tool_filter || pid_filter != null) && ' (已过滤)'}
        </span>
      </div>
    </div>
  );
};
