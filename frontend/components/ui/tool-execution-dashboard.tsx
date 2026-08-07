'use client';

import React, { useMemo } from 'react';
import type {
  ToolExecutionDashboardProps,
  ToolCallCardProps,
  ToolStatsSummaryProps,
  ToolFrequencyChartProps,
  ToolLatencyChartProps,
  ToolCallRecord,
  ToolCallStatus,
  DashboardViewMode,
} from '@/lib/tool-execution-contract';
import {
  toolStatusToSeverity,
  calcSuccessRate,
  calcLatencyUs,
  formatLatency,
  mergeStats,
} from '@/lib/tool-execution-contract';

// ═══════════════════════════════════════════════════════════════════════════
// 常量
// ═══════════════════════════════════════════════════════════════════════════

const STATUS_LABEL: Record<ToolCallStatus, string> = {
  Pending: '等待中',
  Success: '成功',
  Failed: '失败',
  Timeout: '超时',
  Cancelled: '已取消',
};

const STATUS_DOT: Record<ToolCallStatus, string> = {
  Pending: 'bg-yellow-400 animate-pulse',
  Success: 'bg-emerald-500',
  Failed: 'bg-red-500',
  Timeout: 'bg-orange-500',
  Cancelled: 'bg-gray-400',
};

const VIEW_LABELS: { mode: DashboardViewMode; label: string }[] = [
  { mode: 'overview', label: '概览' },
  { mode: 'tools', label: '工具' },
  { mode: 'timeline', label: '时间线' },
];

// ═══════════════════════════════════════════════════════════════════════════
// 子组件: ToolCallCard — 单次工具调用卡片
// ═══════════════════════════════════════════════════════════════════════════

export const ToolCallCard: React.FC<ToolCallCardProps> = ({ call, selected = false, onSelect }) => {
  const latency = calcLatencyUs(call);
  const severity = toolStatusToSeverity(call.status);

  const borderColor =
    severity === 'critical'
      ? 'border-red-300'
      : severity === 'warning'
        ? 'border-yellow-300'
        : 'border-emerald-300';

  return (
    <button
      onClick={onSelect}
      className={`w-full cursor-pointer rounded border p-3 text-left transition-all ${borderColor} ${selected ? 'bg-blue-50 ring-2 ring-blue-400' : 'bg-white hover:bg-slate-50'} `}
    >
      <div className="mb-1 flex items-center gap-2">
        {/* 状态指示灯 */}
        <span
          className={`inline-block h-2 w-2 flex-shrink-0 rounded-full ${STATUS_DOT[call.status]}`}
        />
        {/* 工具名称 */}
        <span className="truncate font-mono text-sm font-semibold">{call.tool_name}</span>
        {/* 状态标签 */}
        <span className="ml-auto flex-shrink-0 text-xs text-gray-500">
          {STATUS_LABEL[call.status]}
        </span>
      </div>

      {/* 参数摘要 */}
      {call.arguments && Object.keys(call.arguments).length > 0 && (
        <div className="mt-1 truncate font-mono text-xs text-gray-500">
          {Object.entries(call.arguments)
            .slice(0, 2)
            .map(([k, v]) => `${k}=${JSON.stringify(v)}`)
            .join(', ')}
        </div>
      )}

      {/* 延迟 */}
      {latency != null && (
        <div className="mt-1 text-xs text-gray-400">延迟: {formatLatency(latency)}</div>
      )}

      {/* 错误标记 */}
      {call.result?.is_error && (
        <div className="mt-1 text-xs font-medium text-red-500">⚠ 工具返回错误</div>
      )}
    </button>
  );
};

// ═══════════════════════════════════════════════════════════════════════════
// 子组件: ToolStatsSummary — Agent 统计摘要
// ═══════════════════════════════════════════════════════════════════════════

export const ToolStatsSummary: React.FC<ToolStatsSummaryProps> = ({
  stats,
  agent_pid,
  highlighted = false,
}) => {
  const successRate = calcSuccessRate(stats);
  const rateColor =
    successRate >= 0.95
      ? 'text-emerald-600'
      : successRate >= 0.8
        ? 'text-yellow-600'
        : 'text-red-600';

  return (
    <div
      className={`rounded-lg border-2 p-4 transition-colors ${
        highlighted ? 'border-blue-400 bg-blue-50' : 'border-slate-200 bg-white'
      }`}
    >
      {/* Agent 标识 */}
      <div className="mb-3 flex items-center justify-between">
        <h3 className="font-mono text-sm font-semibold">Agent PID: {agent_pid}</h3>
        <span className={`text-lg font-bold ${rateColor}`}>{(successRate * 100).toFixed(1)}%</span>
      </div>

      {/* 核心指标网格 */}
      <div className="grid grid-cols-2 gap-2 text-xs">
        <div className="rounded bg-slate-50 p-2">
          <div className="text-gray-500">总调用</div>
          <div className="font-mono font-semibold">{stats.total_calls}</div>
        </div>
        <div className="rounded bg-emerald-50 p-2">
          <div className="text-gray-500">成功</div>
          <div className="font-mono font-semibold text-emerald-700">{stats.successful_calls}</div>
        </div>
        <div className="rounded bg-red-50 p-2">
          <div className="text-gray-500">失败</div>
          <div className="font-mono font-semibold text-red-700">{stats.failed_calls}</div>
        </div>
        <div className="rounded bg-orange-50 p-2">
          <div className="text-gray-500">超时</div>
          <div className="font-mono font-semibold text-orange-700">{stats.timed_out_calls}</div>
        </div>
      </div>

      {/* 唯一工具数 */}
      <div className="mt-3 text-xs text-gray-500">
        使用 <span className="font-semibold text-gray-700">{stats.unique_tools_used.length}</span>{' '}
        种工具
      </div>
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════
// 子组件: ToolFrequencyChart — 工具频率条形图（纯 CSS）
// ═══════════════════════════════════════════════════════════════════════════

export const ToolFrequencyChart: React.FC<ToolFrequencyChartProps> = ({
  frequency,
  selected_tool,
  onToolSelect,
}) => {
  const entries = Object.entries(frequency).sort((a, b) => b[1] - a[1]);
  const maxCount = entries[0]?.[1] ?? 1;

  if (entries.length === 0) {
    return <div className="p-4 text-sm text-gray-400">暂无工具调用数据</div>;
  }

  return (
    <div className="space-y-2">
      <h4 className="text-xs font-semibold tracking-wide text-gray-500 uppercase">调用频率</h4>
      {entries.map(([name, count]) => (
        <button
          key={name}
          onClick={() => onToolSelect(selected_tool === name ? null : name)}
          className={`group w-full text-left`}
        >
          <div className="mb-1 flex items-center justify-between text-xs">
            <span
              className={`truncate font-mono ${
                selected_tool === name ? 'font-semibold text-blue-600' : 'text-gray-700'
              }`}
            >
              {name}
            </span>
            <span className="ml-2 flex-shrink-0 text-gray-400">{count}</span>
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-slate-100">
            <div
              className={`h-full rounded-full transition-all ${
                selected_tool === name ? 'bg-blue-500' : 'bg-slate-400'
              }`}
              style={{ width: `${(count / maxCount) * 100}%` }}
            />
          </div>
        </button>
      ))}
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════
// 子组件: ToolLatencyChart — 延迟分布（纯 CSS 水平条形图）
// ═══════════════════════════════════════════════════════════════════════════

export const ToolLatencyChart: React.FC<ToolLatencyChartProps> = ({
  avg_latency_us,
  p95_latency_us,
  selected_tool,
}) => {
  const entries = Object.entries(avg_latency_us).sort((a, b) => b[1] - a[1]);
  const maxP95 = Math.max(...entries.map(([name]) => p95_latency_us[name] ?? 0), 1);

  if (entries.length === 0) {
    return <div className="p-4 text-sm text-gray-400">暂无延迟数据</div>;
  }

  return (
    <div className="space-y-2">
      <h4 className="text-xs font-semibold tracking-wide text-gray-500 uppercase">
        延迟分布 (avg / P95)
      </h4>
      {entries.map(([name, avg]) => {
        const p95 = p95_latency_us[name] ?? avg;
        const isSelected = selected_tool === name;

        return (
          <div key={name} className="text-xs">
            <div className="mb-0.5 flex items-center justify-between">
              <span
                className={`truncate font-mono ${
                  isSelected ? 'font-semibold text-blue-600' : 'text-gray-600'
                }`}
              >
                {name}
              </span>
              <span className="ml-2 flex-shrink-0 text-gray-400">
                {formatLatency(avg)} / {formatLatency(p95)}
              </span>
            </div>
            <div className="flex h-2 overflow-hidden rounded-full bg-slate-100">
              {/* avg 部分 */}
              <div
                className={`h-full transition-all ${isSelected ? 'bg-blue-400' : 'bg-slate-300'}`}
                style={{
                  width: `${Math.min((avg / maxP95) * 100, 100)}%`,
                }}
              />
              {/* P95 延长线 */}
              {p95 > avg && (
                <div
                  className="h-full bg-orange-200"
                  style={{
                    width: `${Math.min(((p95 - avg) / maxP95) * 100, 100)}%`,
                  }}
                />
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════
// 子组件: ToolTimeline — 工具调用时间线
// ═══════════════════════════════════════════════════════════════════════════

const ToolTimeline: React.FC<{
  calls: ToolCallRecord[];
  selected_call_id: string | null;
  onCallSelect: (call: ToolCallRecord) => void;
}> = ({ calls, selected_call_id, onCallSelect }) => {
  const sorted = useMemo(
    () => [...calls].sort((a, b) => b.request_timestamp_ns - a.request_timestamp_ns),
    [calls],
  );

  if (sorted.length === 0) {
    return <div className="p-4 text-sm text-gray-400">暂无工具调用记录</div>;
  }

  return (
    <div className="max-h-96 space-y-2 overflow-y-auto">
      {sorted.map((call) => (
        <ToolCallCard
          key={call.call_id}
          call={call}
          selected={call.call_id === selected_call_id}
          onSelect={() => onCallSelect(call)}
        />
      ))}
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════
// 主组件: ToolExecutionDashboard
// ═══════════════════════════════════════════════════════════════════════════

export const ToolExecutionDashboard: React.FC<ToolExecutionDashboardProps> = ({
  state,
  onViewModeChange,
  onAgentSelect,
  onCallSelect,
  onStatusFilterChange,
  onToolFilterChange,
  compact = false,
}) => {
  const {
    agent_pids,
    selected_agent_pid,
    view_mode,
    stats_by_agent,
    recent_calls,
    status_filter,
    tool_filter,
  } = state;

  // ── 过滤后的工具调用 ──
  const filteredCalls = useMemo(() => {
    let calls = recent_calls;
    if (selected_agent_pid != null) {
      calls = calls.filter((c) => c.agent_pid === selected_agent_pid);
    }
    if (status_filter) {
      calls = calls.filter((c) => c.status === status_filter);
    }
    if (tool_filter) {
      calls = calls.filter((c) => c.tool_name === tool_filter);
    }
    return calls;
  }, [recent_calls, selected_agent_pid, status_filter, tool_filter]);

  // ── 当前 Agent 的统计（单选时）或聚合统计（全选时）──
  const currentStats = useMemo(() => {
    if (selected_agent_pid != null) {
      return stats_by_agent.get(selected_agent_pid) ?? null;
    }
    const all = Array.from(stats_by_agent.values());
    return all.length > 0 ? mergeStats(all) : null;
  }, [stats_by_agent, selected_agent_pid]);

  // ── 工具名称列表（用于过滤器）──
  const toolNames = useMemo(() => {
    const names = new Set<string>();
    for (const s of stats_by_agent.values()) {
      for (const tn of s.unique_tools_used) names.add(tn);
    }
    return Array.from(names).sort();
  }, [stats_by_agent]);

  // ── 空状态 ──
  if (agent_pids.length === 0 && recent_calls.length === 0) {
    return (
      <div className="p-6 text-center text-gray-400">
        <div className="mb-2 text-2xl">🔧</div>
        <div className="text-sm">等待工具调用事件...</div>
        <div className="mt-1 text-xs">ToolExecutionTracker 尚未接收到数据</div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
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

        {/* Agent 选择器 */}
        {agent_pids.length > 1 && (
          <select
            className="rounded border border-slate-300 bg-white px-2 py-1.5 text-xs"
            value={selected_agent_pid ?? ''}
            onChange={(e) => {
              const v = e.target.value;
              onAgentSelect(v ? Number(v) : null);
            }}
          >
            <option value="">全部 Agent</option>
            {agent_pids.map((pid) => (
              <option key={pid} value={pid}>
                PID {pid}
              </option>
            ))}
          </select>
        )}

        {/* 状态过滤器 */}
        <select
          className="rounded border border-slate-300 bg-white px-2 py-1.5 text-xs"
          value={status_filter ?? ''}
          onChange={(e) => {
            const v = e.target.value;
            onStatusFilterChange(v ? (v as ToolCallStatus) : null);
          }}
        >
          <option value="">全部状态</option>
          {Object.entries(STATUS_LABEL).map(([k, v]) => (
            <option key={k} value={k}>
              {v}
            </option>
          ))}
        </select>

        {/* 工具名过滤器 */}
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
      </div>

      {/* ═══ 概览视图 ═══ */}
      {view_mode === 'overview' && (
        <div className={`grid gap-3 ${compact ? 'grid-cols-1' : 'grid-cols-1 md:grid-cols-2'}`}>
          {selected_agent_pid != null
            ? currentStats && (
                <ToolStatsSummary stats={currentStats} agent_pid={selected_agent_pid} highlighted />
              )
            : Array.from(stats_by_agent.entries()).map(([pid, stats]) => (
                <button key={pid} onClick={() => onAgentSelect(pid)} className="text-left">
                  <ToolStatsSummary stats={stats} agent_pid={pid} />
                </button>
              ))}

          {/* 全局聚合卡片（多 Agent 时显示） */}
          {selected_agent_pid == null && stats_by_agent.size > 1 && currentStats && (
            <div className="rounded-lg border-2 border-dashed border-slate-300 bg-slate-50 p-4">
              <h3 className="mb-2 text-sm font-semibold">全部 Agent 聚合</h3>
              <div className="grid grid-cols-2 gap-2 text-xs">
                <div>
                  <span className="text-gray-500">总调用: </span>
                  <span className="font-mono font-semibold">{currentStats.total_calls}</span>
                </div>
                <div>
                  <span className="text-gray-500">成功率: </span>
                  <span className="font-mono font-semibold">
                    {(calcSuccessRate(currentStats) * 100).toFixed(1)}%
                  </span>
                </div>
                <div>
                  <span className="text-gray-500">唯一工具: </span>
                  <span className="font-mono font-semibold">
                    {currentStats.unique_tools_used.length}
                  </span>
                </div>
                <div>
                  <span className="text-gray-500">Agent 数: </span>
                  <span className="font-mono font-semibold">{stats_by_agent.size}</span>
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {/* ═══ 工具视图 ═══ */}
      {view_mode === 'tools' && (
        <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
          <ToolFrequencyChart
            frequency={currentStats?.tool_frequency ?? {}}
            selected_tool={tool_filter}
            onToolSelect={onToolFilterChange}
          />
          <ToolLatencyChart
            avg_latency_us={currentStats?.avg_latency_us ?? {}}
            p95_latency_us={currentStats?.p95_latency_us ?? {}}
            selected_tool={tool_filter}
          />
        </div>
      )}

      {/* ═══ 时间线视图 ═══ */}
      {view_mode === 'timeline' && (
        <ToolTimeline calls={filteredCalls} selected_call_id={null} onCallSelect={onCallSelect} />
      )}

      {/* ═══ 底部状态栏 ═══ */}
      <div className="flex items-center justify-between border-t border-slate-100 pt-2 text-xs text-gray-400">
        <span>
          共追踪 {agent_pids.length} 个 Agent · {currentStats?.total_calls ?? 0} 次调用
        </span>
        <span>
          最近 {recent_calls.length} 条记录
          {filteredCalls.length !== recent_calls.length && ` (显示 ${filteredCalls.length})`}
        </span>
      </div>
    </div>
  );
};
