'use client';

import React, { useMemo } from 'react';
import type {
  SessionHealthDashboardProps,
  SessionHealthViewMode,
  FleetGridProps,
  HealthScoreGaugeProps,
  DimensionBarChartProps,
  HealthTrendChartProps,
  HealthAlertCardProps,
  SessionHealthSnapshot,
  SessionHealthAlert,
  AgentHealthSummary,
  HealthDimension,
  HealthAlertType,
  HealthAlertSeverity,
  HealthScoreTier,
} from '@/lib/session-health-contract';
import {
  HEALTH_DIMENSION_LABEL,
  HEALTH_DIMENSION_ICON,
  HEALTH_ALERT_TYPE_LABEL,
  HEALTH_ALERT_TYPE_ICON,
  HEALTH_TIER_COLORS,
  HEALTH_TIER_LABEL,
  healthScoreToTier,
  computeDimensionContribution,
  formatLatencyUs,
  snapshotsToDataPoints,
} from '@/lib/session-health-contract';

// ═══════════════════════════════════════════════════════════════════════════
// 常量
// ═══════════════════════════════════════════════════════════════════════════

const VIEW_LABELS: { mode: SessionHealthViewMode; label: string; icon: string }[] = [
  { mode: 'overview', label: '概览', icon: '📊' },
  { mode: 'fleet', label: 'Fleet', icon: '🖥️' },
  { mode: 'alerts', label: '告警', icon: '🔔' },
  { mode: 'trends', label: '趋势', icon: '📈' },
];

const DIMENSION_ORDER: HealthDimension[] = [
  'latency',
  'error_rate',
  'tool_health',
  'api_health',
  'resource',
];

const ALERT_TYPE_COLOR: Record<HealthAlertType, string> = {
  health_degraded: 'border-l-amber-400 bg-amber-50',
  health_warning: 'border-l-orange-400 bg-orange-50',
  health_critical: 'border-l-red-500 bg-red-50',
};

const ALERT_TYPE_DOT: Record<HealthAlertType, string> = {
  health_degraded: 'bg-amber-400',
  health_warning: 'bg-orange-500',
  health_critical: 'bg-red-600 animate-pulse',
};

// DIMENSION_COLORS 用于柱状图和多维度可视化
const DIMENSION_COLORS: Record<HealthDimension, { bar: string; text: string; bg: string }> = {
  latency: { bar: 'bg-violet-500', text: 'text-violet-700', bg: 'bg-violet-50' },
  error_rate: { bar: 'bg-red-500', text: 'text-red-700', bg: 'bg-red-50' },
  tool_health: { bar: 'bg-emerald-500', text: 'text-emerald-700', bg: 'bg-emerald-50' },
  api_health: { bar: 'bg-blue-500', text: 'text-blue-700', bg: 'bg-blue-50' },
  resource: { bar: 'bg-slate-500', text: 'text-slate-700', bg: 'bg-slate-50' },
};

// ═══════════════════════════════════════════════════════════════════════════
// 子组件: HealthScoreGauge — 健康分数仪表盘
// ═══════════════════════════════════════════════════════════════════════════

/** @deprecated 阶段二实时视角（SSE 接入后迁移至运行监控区块），现无生产者，仅存档。 */
export const HealthScoreGauge: React.FC<HealthScoreGaugeProps> = ({
  score,
  previous_score,
  size = 'md',
  showLabel = true,
}) => {
  const tier = healthScoreToTier(score);
  const colors = HEALTH_TIER_COLORS[tier];

  const sizeClasses = {
    sm: { ring: 'w-12 h-12', text: 'text-sm', sub: 'text-[10px]' },
    md: { ring: 'w-20 h-20', text: 'text-xl', sub: 'text-xs' },
    lg: { ring: 'w-28 h-28', text: 'text-3xl', sub: 'text-sm' },
  }[size];

  // SVG 环形进度条参数
  const radius = size === 'sm' ? 20 : size === 'md' ? 34 : 48;
  const circumference = 2 * Math.PI * radius;
  const progress = (score / 100) * circumference;
  const svgSize = size === 'sm' ? 52 : size === 'md' ? 84 : 108;

  // 渐变色: 红 → 橙 → 黄 → 绿
  const barColor =
    score > 80 ? '#10b981' : score > 60 ? '#3b82f6' : score > 30 ? '#f59e0b' : '#ef4444';

  const delta = previous_score != null ? score - previous_score : null;

  return (
    <div className="flex flex-col items-center gap-1">
      <div className={`relative ${sizeClasses.ring}`}>
        <svg className="h-full w-full -rotate-90" viewBox={`0 0 ${svgSize} ${svgSize}`}>
          {/* 背景环 */}
          <circle
            cx={svgSize / 2}
            cy={svgSize / 2}
            r={radius}
            fill="none"
            stroke="#e5e7eb"
            strokeWidth={size === 'sm' ? 4 : 6}
          />
          {/* 进度环 */}
          <circle
            cx={svgSize / 2}
            cy={svgSize / 2}
            r={radius}
            fill="none"
            stroke={barColor}
            strokeWidth={size === 'sm' ? 4 : 6}
            strokeLinecap="round"
            strokeDasharray={circumference}
            strokeDashoffset={circumference - progress}
            className="transition-all duration-700 ease-out"
          />
        </svg>
        {/* 中心分数 */}
        <div className="absolute inset-0 flex items-center justify-center">
          <span className={`font-bold ${sizeClasses.text} ${colors.text}`}>{score}</span>
        </div>
      </div>

      {showLabel && (
        <div className="text-center">
          <span className={`${sizeClasses.sub} font-medium ${colors.text}`}>
            {HEALTH_TIER_LABEL[tier]}
          </span>
          {delta != null && delta !== 0 && (
            <span
              className={`${sizeClasses.sub} ml-1 ${
                delta > 0 ? 'text-emerald-600' : 'text-red-600'
              }`}
            >
              {delta > 0 ? `↑${delta}` : `↓${Math.abs(delta)}`}
            </span>
          )}
        </div>
      )}
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════
// 子组件: DimensionBarChart — 维度分数柱状图
// ═══════════════════════════════════════════════════════════════════════════

export const DimensionBarChart: React.FC<DimensionBarChartProps> = ({
  dimensions,
  weights,
  highlight_dimension,
  compact = false,
}) => {
  const contributions = useMemo(
    () => computeDimensionContribution(dimensions, weights),
    [dimensions, weights],
  );

  return (
    <div className={compact ? 'space-y-1' : 'space-y-2'}>
      {DIMENSION_ORDER.map((dim) => {
        const data = dimensions[dim];
        const rawScore = Math.round(data.score * 100);
        const contrib = Math.round(contributions[dim]);
        const colors = DIMENSION_COLORS[dim];
        const isHighlighted = highlight_dimension === dim;

        return (
          <div
            key={dim}
            className={`rounded p-2 ${compact ? 'p-1.5' : ''} ${
              isHighlighted ? 'bg-blue-50/50 ring-2 ring-blue-400' : ''
            }`}
          >
            {/* 维度标题行 */}
            <div className="mb-1 flex items-center gap-1.5">
              <span className="text-xs">{HEALTH_DIMENSION_ICON[dim]}</span>
              <span className={`text-xs font-medium ${colors.text}`}>
                {HEALTH_DIMENSION_LABEL[dim]}
              </span>
              <span className="ml-auto text-xs text-gray-400">
                权重: {(weights[dim] * 100).toFixed(0)}%
              </span>
            </div>

            {/* 进度条 */}
            <div className="flex items-center gap-2">
              <div className="h-2 flex-1 overflow-hidden rounded-full bg-slate-100">
                <div
                  className={`h-full rounded-full transition-all duration-500 ${colors.bar}`}
                  style={{ width: `${rawScore}%` }}
                />
              </div>
              <span className="w-8 text-right font-mono text-xs font-semibold">{rawScore}</span>
            </div>

            {/* 贡献值（加权后） */}
            {!compact && (
              <div className="mt-0.5 text-[10px] text-gray-400">
                加权贡献: {contrib} 分
                {dim === 'latency' && dimensions.latency.samples > 0 && (
                  <span className="ml-2">P95: {formatLatencyUs(dimensions.latency.p95_us)}</span>
                )}
                {dim === 'error_rate' && dimensions.error_rate.total_operations > 0 && (
                  <span className="ml-2">
                    错误率: {(dimensions.error_rate.error_rate * 100).toFixed(1)}%
                  </span>
                )}
                {dim === 'tool_health' && dimensions.tool_health.total_calls > 0 && (
                  <span className="ml-2">
                    成功 {dimensions.tool_health.successful}/{dimensions.tool_health.total_calls}
                    {dimensions.tool_health.active_loops > 0 && (
                      <span className="text-red-500">
                        {' '}
                        · {dimensions.tool_health.active_loops} 循环
                      </span>
                    )}
                  </span>
                )}
                {dim === 'api_health' && dimensions.api_health.total_requests > 0 && (
                  <span className="ml-2">
                    5xx: {dimensions.api_health.status_5xx} · 429:{' '}
                    {dimensions.api_health.status_429}
                  </span>
                )}
                {dim === 'resource' && (
                  <span className="ml-2">崩溃: {dimensions.resource.process_crashes} 次</span>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════
// 子组件: HealthTrendChart — 历史趋势迷你图
// ═══════════════════════════════════════════════════════════════════════════

export const HealthTrendChart: React.FC<HealthTrendChartProps> = ({
  data,
  agent_pid,
  highlight_dimension,
  compact = false,
}) => {
  if (data.length < 2) {
    return (
      <div className="p-4 text-center text-xs text-gray-400">需要至少 2 个数据点才能显示趋势</div>
    );
  }

  const height = compact ? 60 : 120;
  const width = 300;
  const padding = { top: 8, right: 8, bottom: 16, left: 8 };
  const chartW = width - padding.left - padding.right;
  const chartH = height - padding.top - padding.bottom;

  // 提取目标数据系列
  const series = data.map((d) =>
    highlight_dimension ? d.dimension_scores[highlight_dimension] : d.health_score,
  );
  const timestamps = data.map((d) => d.timestamp_ns);

  const maxVal = Math.max(...series, 100);
  const minVal = Math.min(...series, 0);
  const valRange = maxVal - minVal || 1;

  const xs = timestamps.map((_, i) => padding.left + (i / (timestamps.length - 1)) * chartW);
  const ys = series.map((v) => padding.top + chartH - ((v - minVal) / valRange) * chartH);

  // SVG path
  const pathD = ys.map((y, i) => `${i === 0 ? 'M' : 'L'}${xs[i]},${y}`).join(' ');
  const areaD = `${pathD} L${xs[xs.length - 1]},${padding.top + chartH} L${xs[0]},${padding.top + chartH} Z`;

  const lineColor = highlight_dimension
    ? DIMENSION_COLORS[highlight_dimension].bar.replace('bg-', '#')
    : healthScoreToTier(series[series.length - 1]) === 'critical'
      ? '#ef4444'
      : healthScoreToTier(series[series.length - 1]) === 'warning'
        ? '#f59e0b'
        : '#3b82f6';

  // 阈值参考线
  const warningY = padding.top + chartH - ((60 - minVal) / valRange) * chartH;
  const criticalY = padding.top + chartH - ((30 - minVal) / valRange) * chartH;

  return (
    <div className={compact ? '' : 'rounded-lg border border-slate-200 bg-white p-3'}>
      {!compact && (
        <div className="mb-2 text-xs text-gray-500">
          Agent PID {agent_pid} ·{' '}
          {highlight_dimension ? HEALTH_DIMENSION_LABEL[highlight_dimension] : '综合健康'} 趋势
        </div>
      )}
      <svg viewBox={`0 0 ${width} ${height}`} className="w-full" style={{ maxHeight: height }}>
        {/* 阈值参考线 */}
        {minVal <= 60 && maxVal >= 60 && (
          <line
            x1={padding.left}
            y1={warningY}
            x2={padding.left + chartW}
            y2={warningY}
            stroke="#f59e0b"
            strokeWidth="0.5"
            strokeDasharray="3 3"
            opacity="0.5"
          />
        )}
        {minVal <= 30 && maxVal >= 30 && (
          <line
            x1={padding.left}
            y1={criticalY}
            x2={padding.left + chartW}
            y2={criticalY}
            stroke="#ef4444"
            strokeWidth="0.5"
            strokeDasharray="3 3"
            opacity="0.5"
          />
        )}
        {/* 面积填充 */}
        <path d={areaD} fill={lineColor} fillOpacity="0.1" />
        {/* 折线 */}
        <path d={pathD} fill="none" stroke={lineColor} strokeWidth="1.5" strokeLinejoin="round" />
        {/* 最后一个数据点 */}
        <circle
          cx={xs[xs.length - 1]}
          cy={ys[ys.length - 1]}
          r="3"
          fill={lineColor}
          stroke="white"
          strokeWidth="1.5"
        />
      </svg>
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════
// 子组件: HealthAlertCard — 告警卡片
// ═══════════════════════════════════════════════════════════════════════════

export const HealthAlertCard: React.FC<HealthAlertCardProps> = ({
  alert,
  selected = false,
  onSelect,
}) => {
  return (
    <button
      onClick={onSelect}
      className={`w-full cursor-pointer rounded-lg border border-l-4 p-3 text-left transition-all ${ALERT_TYPE_COLOR[alert.alert_type]} ${selected ? 'shadow-sm ring-2 ring-blue-400' : 'hover:shadow-sm'} `}
    >
      <div className="mb-1 flex items-center gap-2">
        <span
          className={`inline-block h-2 w-2 flex-shrink-0 rounded-full ${ALERT_TYPE_DOT[alert.alert_type]}`}
        />
        <span className="text-xs font-semibold">
          {HEALTH_ALERT_TYPE_ICON[alert.alert_type]} {HEALTH_ALERT_TYPE_LABEL[alert.alert_type]}
        </span>
        <span className="ml-auto font-mono text-xs text-gray-500">
          {new Date(alert.timestamp_ns / 1_000_000).toLocaleTimeString()}
        </span>
      </div>

      <div className="mb-1 flex items-center gap-2">
        <span className="font-mono text-xs text-gray-600">PID: {alert.agent_root_pid}</span>
        <span className="text-xs text-gray-400">{alert.comm}</span>
        <span className="ml-auto font-mono text-xs font-bold">分数: {alert.health_score}</span>
      </div>

      <p className="mt-1 line-clamp-2 text-xs text-gray-600">{alert.message}</p>

      {alert.degraded_dimensions.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-1">
          {alert.degraded_dimensions.map((dim) => (
            <span
              key={dim}
              className={`rounded px-1.5 py-0.5 text-[10px] ${DIMENSION_COLORS[dim].bg} ${DIMENSION_COLORS[dim].text}`}
            >
              {HEALTH_DIMENSION_ICON[dim]} {HEALTH_DIMENSION_LABEL[dim]}
            </span>
          ))}
        </div>
      )}
    </button>
  );
};

// ═══════════════════════════════════════════════════════════════════════════
// 子组件: FleetGrid — Fleet 网格视图
// ═══════════════════════════════════════════════════════════════════════════

export const FleetGrid: React.FC<FleetGridProps> = ({
  agents,
  selected_pid,
  onAgentClick,
  fleet_avg_score,
  compact = false,
}) => {
  // 按分数升序排列（最不健康的在最前面）
  const sorted = useMemo(
    () => [...agents].sort((a, b) => a.current_score - b.current_score),
    [agents],
  );

  return (
    <div
      className={`grid gap-3 ${
        compact
          ? 'grid-cols-2 md:grid-cols-3'
          : 'grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4'
      }`}
    >
      {sorted.map((agent) => {
        const tier = healthScoreToTier(agent.current_score);
        const colors = HEALTH_TIER_COLORS[tier];
        const isSelected = selected_pid === agent.agent_root_pid;
        const isBelowFleetAvg = agent.current_score < fleet_avg_score;

        return (
          <button
            key={agent.agent_root_pid}
            onClick={() => onAgentClick(agent.agent_root_pid)}
            className={`cursor-pointer rounded-lg border p-3 text-left transition-all ${isSelected ? 'shadow-md ring-2 ring-blue-400' : 'hover:shadow-sm'} ${colors.bg} ${colors.ring.replace('ring-', 'border-')} `}
          >
            {/* Agent 标识行 */}
            <div className="mb-2 flex items-center gap-2">
              <span className={`inline-block h-2 w-2 rounded-full ${colors.dot}`} />
              <span className="truncate font-mono text-xs font-bold">
                PID {agent.agent_root_pid}
              </span>
              {agent.is_active && (
                <span className="ml-auto inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-500" />
              )}
            </div>
            <div className="mb-2 truncate text-xs text-gray-500">{agent.comm}</div>

            {/* 健康分数 + 仪表盘 */}
            <div className="flex items-center gap-3">
              <HealthScoreGauge score={agent.current_score} size="sm" showLabel={false} />
              <div className="min-w-0 flex-1">
                <div className="flex items-baseline gap-1">
                  <span className={`text-lg font-bold ${colors.text}`}>{agent.current_score}</span>
                  {agent.score_trend !== 0 && (
                    <span
                      className={`text-xs ${
                        agent.score_trend > 0 ? 'text-emerald-600' : 'text-red-600'
                      }`}
                    >
                      {agent.score_trend > 0 ? '↑' : '↓'}
                      {Math.abs(agent.score_trend)}
                    </span>
                  )}
                </div>
                {/* 最弱维度指示 */}
                <div className="mt-0.5 text-[10px] text-gray-400">
                  最弱: {HEALTH_DIMENSION_ICON[agent.weakest_dimension]}{' '}
                  {HEALTH_DIMENSION_LABEL[agent.weakest_dimension]}{' '}
                  <span className={DIMENSION_COLORS[agent.weakest_dimension].text}>
                    {agent.dimension_scores[agent.weakest_dimension]}
                  </span>
                </div>
                {isBelowFleetAvg && (
                  <div className="mt-0.5 text-[10px] text-amber-600">
                    ⚠ 低于 Fleet 平均 ({fleet_avg_score})
                  </div>
                )}
              </div>
            </div>

            {/* 告警计数 */}
            {agent.active_alert_count > 0 && (
              <div className="mt-2 text-xs">
                <span
                  className={`inline-flex items-center rounded-full px-1.5 py-0.5 ${
                    agent.active_alert_count > 2
                      ? 'bg-red-100 text-red-700'
                      : 'bg-amber-100 text-amber-700'
                  }`}
                >
                  {agent.active_alert_count} 活跃告警
                </span>
              </div>
            )}
          </button>
        );
      })}
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════
// 主组件: SessionHealthDashboard
// ═══════════════════════════════════════════════════════════════════════════

export const SessionHealthDashboard: React.FC<SessionHealthDashboardProps> = ({
  state,
  onViewModeChange,
  onAlertSelect,
  onSessionSelect,
  onAgentSelect,
  onAlertTypeFilterChange,
  onSeverityFilterChange,
  onAgentFilterChange,
  onMaxHealthScoreFilterChange,
  onNavigateToToolExecution,
  onNavigateToAgentLoop,
  onNavigateToTokenCost,
  compact = false,
}) => {
  const {
    view_mode,
    fleet_overview,
    agent_summaries,
    alerts,
    selected_alert_id,
    selected_session_id,
    selected_agent_pid,
    selected_profile,
    alert_type_filter,
    severity_filter,
    agent_filter,
    max_health_score_filter,
    weights,
  } = state;

  // ── 过滤后的告警 ──
  const filtered_alerts = useMemo(() => {
    return alerts.filter((a) => {
      if (alert_type_filter && a.alert_type !== alert_type_filter) return false;
      if (severity_filter && a.severity !== severity_filter) return false;
      if (agent_filter && a.agent_root_pid !== agent_filter) return false;
      return true;
    });
  }, [alerts, alert_type_filter, severity_filter, agent_filter]);

  // ── 过滤后的 Agent 摘要 ──
  const filtered_agents = useMemo(() => {
    const list = Array.from(agent_summaries.values());
    return list.filter((a) => {
      if (agent_filter && a.agent_root_pid !== agent_filter) return false;
      if (max_health_score_filter != null && a.current_score > max_health_score_filter)
        return false;
      return true;
    });
  }, [agent_summaries, agent_filter, max_health_score_filter]);

  // ── Fleet 摘要卡片数据 ──
  const fleetAvg = fleet_overview?.fleet_avg_score ?? 100;
  const fleetTier = healthScoreToTier(fleetAvg);

  return (
    <div className="space-y-4">
      {/* ═══ 视图导航 ═══ */}
      <div className="flex flex-wrap items-center gap-1 border-b border-slate-200 pb-2">
        {VIEW_LABELS.map(({ mode, label, icon }) => {
          const hasData =
            mode === 'overview'
              ? fleet_overview != null
              : mode === 'fleet'
                ? agent_summaries.size > 0
                : mode === 'alerts'
                  ? alerts.length > 0
                  : selected_profile != null;

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
              {/* 徽章 */}
              {mode === 'fleet' && agent_summaries.size > 0 && (
                <span
                  className={`inline-flex h-5 w-5 items-center justify-center rounded-full text-[10px] font-bold ${
                    view_mode === mode ? 'bg-white/20 text-white' : 'bg-blue-100 text-blue-700'
                  }`}
                >
                  {agent_summaries.size}
                </span>
              )}
              {mode === 'alerts' && alerts.length > 0 && (
                <span
                  className={`inline-flex h-5 w-5 items-center justify-center rounded-full text-[10px] font-bold ${
                    view_mode === mode ? 'bg-white/20 text-white' : 'bg-red-100 text-red-700'
                  }`}
                >
                  {alerts.length}
                </span>
              )}
            </button>
          );
        })}
      </div>

      {/* ═══ 视图内容 ═══ */}
      <div className={compact ? 'max-h-[70vh] overflow-y-auto' : ''}>
        {/* ── Overview: Fleet 总览 ── */}
        {view_mode === 'overview' && fleet_overview && (
          <div className="space-y-4">
            {/* 顶部摘要卡片 */}
            <div className="grid grid-cols-2 gap-2 md:grid-cols-5">
              <SummaryCard
                label="Fleet 健康均分"
                value={fleet_overview.fleet_avg_score}
                unit="/ 100"
                highlight={fleetTier !== 'excellent'}
              />
              <SummaryCard
                label="活跃会话"
                value={fleet_overview.active_session_count}
                unit={`/ ${fleet_overview.total_session_count}`}
              />
              <SummaryCard
                label="活跃告警"
                value={fleet_overview.total_active_alerts}
                unit=""
                highlight={fleet_overview.total_active_alerts > 0}
              />
              <SummaryCard
                label="需关注"
                value={fleet_overview.attention_needed.length}
                unit="Agent"
                highlight={fleet_overview.attention_needed.length > 0}
              />
              <SummaryCard
                label="Fleet 分布"
                value={
                  <span className="flex gap-1">
                    <span className="text-emerald-600">
                      {fleet_overview.score_distribution.excellent}
                    </span>
                    <span className="text-slate-300">/</span>
                    <span className="text-blue-600">{fleet_overview.score_distribution.good}</span>
                    <span className="text-slate-300">/</span>
                    <span className="text-amber-600">
                      {fleet_overview.score_distribution.warning}
                    </span>
                    <span className="text-slate-300">/</span>
                    <span className="text-red-600">
                      {fleet_overview.score_distribution.critical}
                    </span>
                  </span>
                }
                unit="优/良/警/危"
                raw
              />
            </div>

            {/* Fleet 维度雷达 + 需关注列表 */}
            <div className={`grid gap-4 ${compact ? 'grid-cols-1' : 'grid-cols-1 lg:grid-cols-2'}`}>
              {/* Fleet 维度概览 */}
              <div className="rounded-lg border border-slate-200 bg-white p-4">
                <h4 className="mb-3 text-xs font-semibold tracking-wide text-gray-500 uppercase">
                  Fleet 维度平均分
                </h4>
                {DIMENSION_ORDER.map((dim) => (
                  <div key={dim} className="mb-2 flex items-center gap-2">
                    <span className="w-16 text-xs">
                      {HEALTH_DIMENSION_ICON[dim]} {HEALTH_DIMENSION_LABEL[dim]}
                    </span>
                    <div className="h-2 flex-1 overflow-hidden rounded-full bg-slate-100">
                      <div
                        className={`h-full rounded-full transition-all ${DIMENSION_COLORS[dim].bar}`}
                        style={{ width: `${fleet_overview.fleet_dimension_scores[dim]}%` }}
                      />
                    </div>
                    <span className="w-8 text-right font-mono text-xs">
                      {fleet_overview.fleet_dimension_scores[dim]}
                    </span>
                  </div>
                ))}
              </div>

              {/* 需要关注的 Agent */}
              <div className="rounded-lg border border-slate-200 bg-white p-4">
                <h4 className="mb-3 text-xs font-semibold tracking-wide text-gray-500 uppercase">
                  ⚠ 需要关注
                </h4>
                {fleet_overview.attention_needed.length === 0 ? (
                  <div className="py-4 text-center text-xs text-gray-400">
                    所有 Agent 健康状态正常 🎉
                  </div>
                ) : (
                  <div className="space-y-2">
                    {fleet_overview.attention_needed.slice(0, 5).map((agent) => (
                      <button
                        key={agent.agent_root_pid}
                        onClick={() => onAgentSelect(agent.agent_root_pid)}
                        className="flex w-full cursor-pointer items-center gap-3 rounded border border-slate-200 p-2 text-left hover:bg-slate-50"
                      >
                        <HealthScoreGauge score={agent.current_score} size="sm" showLabel={false} />
                        <div className="min-w-0 flex-1">
                          <div className="font-mono text-xs font-semibold">
                            PID {agent.agent_root_pid}
                          </div>
                          <div className="truncate text-[10px] text-gray-400">
                            {agent.comm} · 最弱: {HEALTH_DIMENSION_LABEL[agent.weakest_dimension]} (
                            {agent.dimension_scores[agent.weakest_dimension]})
                          </div>
                        </div>
                        <span className="text-xs text-blue-500">详情 →</span>
                      </button>
                    ))}
                    {fleet_overview.attention_needed.length > 5 && (
                      <div className="text-center text-xs text-gray-400">
                        +{fleet_overview.attention_needed.length - 5} 更多...
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>
          </div>
        )}

        {view_mode === 'overview' && !fleet_overview && (
          <div className="p-6 text-center text-gray-400">
            <div className="mb-1 text-lg">📊</div>
            <div className="text-xs">等待 Fleet 健康数据...</div>
            <div className="mt-1 text-xs text-gray-300">
              需要 SessionHealthMonitor 输出 session_health 事件
            </div>
          </div>
        )}

        {/* ── Fleet: 网格视图 ── */}
        {view_mode === 'fleet' && (
          <>
            {/* Filter bar */}
            <div className="mb-3 flex flex-wrap items-center gap-2">
              <span className="text-xs text-gray-500">过滤:</span>
              {max_health_score_filter != null && (
                <button
                  onClick={() => onMaxHealthScoreFilterChange(null)}
                  className="rounded bg-blue-100 px-2 py-1 text-xs text-blue-700 hover:bg-blue-200"
                >
                  分数 ≤ {max_health_score_filter} ✕
                </button>
              )}
              <button
                onClick={() => onMaxHealthScoreFilterChange(60)}
                className={`rounded px-2 py-1 text-xs ${
                  max_health_score_filter === 60
                    ? 'bg-amber-100 text-amber-700'
                    : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
                }`}
              >
                &lt; 60 警告
              </button>
              <button
                onClick={() => onMaxHealthScoreFilterChange(30)}
                className={`rounded px-2 py-1 text-xs ${
                  max_health_score_filter === 30
                    ? 'bg-red-100 text-red-700'
                    : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
                }`}
              >
                &lt; 30 严重
              </button>
            </div>

            {filtered_agents.length > 0 ? (
              <FleetGrid
                agents={filtered_agents}
                selected_pid={selected_agent_pid}
                onAgentClick={(pid) => {
                  onAgentSelect(pid);
                  onSessionSelect(agent_summaries.get(pid)?.session_id ?? '');
                }}
                fleet_avg_score={fleetAvg}
                compact={compact}
              />
            ) : (
              <div className="p-6 text-center text-gray-400">
                <div className="mb-1 text-lg">🖥️</div>
                <div className="text-xs">无匹配的 Agent 会话</div>
              </div>
            )}
          </>
        )}

        {/* ── Session Detail ── */}
        {view_mode === 'session_detail' && selected_profile && (
          <div className="space-y-4">
            {/* 返回按钮 */}
            <button
              onClick={() => onViewModeChange('fleet')}
              className="text-xs text-blue-600 hover:text-blue-800"
            >
              ← 返回 Fleet
            </button>

            {/* 会话头部 */}
            <div className="flex items-center gap-4 rounded-lg border border-slate-200 bg-white p-4">
              <HealthScoreGauge
                score={selected_profile.summary.current_score}
                previous_score={
                  selected_profile.snapshot_history.length >= 2
                    ? selected_profile.snapshot_history[
                        selected_profile.snapshot_history.length - 2
                      ].health_score
                    : undefined
                }
                size="lg"
              />
              <div>
                <h3 className="font-mono text-sm font-bold">
                  PID {selected_profile.agent_root_pid}
                </h3>
                <p className="text-xs text-gray-500">{selected_profile.comm}</p>
                <p className="mt-1 font-mono text-xs text-gray-400">
                  Session: {selected_profile.session_id.slice(0, 12)}...
                </p>
                <div className="mt-2 flex gap-2">
                  <button
                    onClick={() => onNavigateToToolExecution(selected_profile.agent_root_pid)}
                    className="rounded bg-blue-50 px-2 py-1 text-xs text-blue-600 hover:bg-blue-100"
                  >
                    🔧 工具调用
                  </button>
                  <button
                    onClick={() => onNavigateToAgentLoop(selected_profile.agent_root_pid)}
                    className="rounded bg-violet-50 px-2 py-1 text-xs text-violet-600 hover:bg-violet-100"
                  >
                    🔄 循环检测
                  </button>
                  <button
                    onClick={() => onNavigateToTokenCost(selected_profile.agent_root_pid)}
                    className="rounded bg-emerald-50 px-2 py-1 text-xs text-emerald-600 hover:bg-emerald-100"
                  >
                    💰 Token 消耗
                  </button>
                </div>
              </div>
            </div>

            {/* 维度详情 + 趋势图 */}
            <div className={`grid gap-4 ${compact ? 'grid-cols-1' : 'grid-cols-1 lg:grid-cols-2'}`}>
              {/* 维度柱状图 */}
              <div className="rounded-lg border border-slate-200 bg-white p-4">
                <h4 className="mb-3 text-xs font-semibold tracking-wide text-gray-500 uppercase">
                  维度评分
                </h4>
                {selected_profile.snapshot_history.length > 0 && (
                  <DimensionBarChart
                    dimensions={
                      selected_profile.snapshot_history[
                        selected_profile.snapshot_history.length - 1
                      ].dimensions
                    }
                    weights={weights}
                    highlight_dimension={selected_profile.summary.weakest_dimension}
                    compact={compact}
                  />
                )}
              </div>

              {/* 趋势图 */}
              <div className="rounded-lg border border-slate-200 bg-white p-4">
                <h4 className="mb-3 text-xs font-semibold tracking-wide text-gray-500 uppercase">
                  健康趋势
                </h4>
                <HealthTrendChart
                  data={snapshotsToDataPoints(selected_profile.snapshot_history)}
                  agent_pid={selected_profile.agent_root_pid}
                  compact={compact}
                />
              </div>
            </div>

            {/* 告警历史 */}
            {selected_profile.alert_history.length > 0 && (
              <div className="rounded-lg border border-slate-200 bg-white p-4">
                <h4 className="mb-3 text-xs font-semibold tracking-wide text-gray-500 uppercase">
                  告警历史 ({selected_profile.alert_history.length})
                </h4>
                <div className="space-y-2">
                  {selected_profile.alert_history
                    .slice(-10)
                    .reverse()
                    .map((alert) => (
                      <HealthAlertCard
                        key={alert.session_id + alert.timestamp_ns}
                        alert={alert}
                        selected={false}
                        onSelect={() => onAlertSelect(alert)}
                      />
                    ))}
                </div>
              </div>
            )}
          </div>
        )}

        {view_mode === 'session_detail' && !selected_profile && (
          <div className="p-6 text-center text-gray-400">
            <div className="mb-1 text-lg">🔍</div>
            <div className="text-xs">请从 Fleet 视图选择一个 Agent 查看详情</div>
          </div>
        )}

        {/* ── Alerts: 告警列表 ── */}
        {view_mode === 'alerts' && (
          <>
            {/* Filter bar */}
            <div className="mb-3 flex flex-wrap items-center gap-2">
              <span className="text-xs text-gray-500">过滤:</span>
              {(['health_degraded', 'health_warning', 'health_critical'] as HealthAlertType[]).map(
                (type) => (
                  <button
                    key={type}
                    onClick={() =>
                      onAlertTypeFilterChange(alert_type_filter === type ? null : type)
                    }
                    className={`rounded px-2 py-1 text-xs ${
                      alert_type_filter === type
                        ? `${ALERT_TYPE_COLOR[type].split(' ')[0]} ${ALERT_TYPE_COLOR[type].split(' ')[1]}`
                        : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
                    }`}
                  >
                    {HEALTH_ALERT_TYPE_ICON[type]} {HEALTH_ALERT_TYPE_LABEL[type]}
                  </button>
                ),
              )}
              {(['warning', 'critical'] as HealthAlertSeverity[]).map((sev) => (
                <button
                  key={sev}
                  onClick={() => onSeverityFilterChange(severity_filter === sev ? null : sev)}
                  className={`rounded px-2 py-1 text-xs ${
                    severity_filter === sev
                      ? 'bg-blue-100 text-blue-700'
                      : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
                  }`}
                >
                  {sev === 'warning' ? '⚠ 警告' : '🚨 严重'}
                </button>
              ))}
            </div>

            {filtered_alerts.length > 0 ? (
              <div className="space-y-2">
                {filtered_alerts.map((alert) => (
                  <HealthAlertCard
                    key={alert.session_id + alert.timestamp_ns}
                    alert={alert}
                    selected={selected_alert_id === alert.session_id + alert.timestamp_ns}
                    onSelect={() => {
                      onAlertSelect(alert);
                      // 点击告警卡片时导航到对应 Agent 的详情
                      onSessionSelect(alert.session_id);
                    }}
                  />
                ))}
              </div>
            ) : (
              <div className="p-6 text-center text-gray-400">
                <div className="mb-1 text-lg">🔔</div>
                <div className="text-xs">无匹配的告警</div>
              </div>
            )}
          </>
        )}

        {/* ── Trends: 趋势视图 ── */}
        {view_mode === 'trends' && selected_profile && (
          <div className="space-y-4">
            <button
              onClick={() => onViewModeChange('session_detail')}
              className="text-xs text-blue-600 hover:text-blue-800"
            >
              ← 返回详情
            </button>

            {/* 综合趋势 */}
            <div className="rounded-lg border border-slate-200 bg-white p-4">
              <h4 className="mb-3 text-xs font-semibold tracking-wide text-gray-500 uppercase">
                综合健康趋势
              </h4>
              <HealthTrendChart
                data={snapshotsToDataPoints(selected_profile.snapshot_history)}
                agent_pid={selected_profile.agent_root_pid}
                compact={false}
              />
            </div>

            {/* 各维度独立趋势 */}
            <div className={`grid gap-3 ${compact ? 'grid-cols-1' : 'grid-cols-1 md:grid-cols-2'}`}>
              {DIMENSION_ORDER.map((dim) => (
                <div key={dim} className="rounded-lg border border-slate-200 bg-white p-3">
                  <h5 className="mb-2 text-xs font-semibold text-gray-500">
                    {HEALTH_DIMENSION_ICON[dim]} {HEALTH_DIMENSION_LABEL[dim]}
                  </h5>
                  <HealthTrendChart
                    data={snapshotsToDataPoints(selected_profile.snapshot_history)}
                    agent_pid={selected_profile.agent_root_pid}
                    highlight_dimension={dim}
                    compact
                  />
                </div>
              ))}
            </div>
          </div>
        )}

        {view_mode === 'trends' && !selected_profile && (
          <div className="p-6 text-center text-gray-400">
            <div className="mb-1 text-lg">📈</div>
            <div className="text-xs">请从 Fleet 视图选择一个 Agent 查看趋势</div>
          </div>
        )}
      </div>

      {/* ═══ 底部状态栏 ═══ */}
      <div className="flex items-center justify-between border-t border-slate-100 pt-2 text-xs text-gray-400">
        <span>
          {view_mode === 'overview' &&
            `${fleet_overview?.active_session_count ?? 0} Active · ${fleet_overview?.total_active_alerts ?? 0} Alerts`}
          {view_mode === 'fleet' && `${agent_summaries.size} Agent 会话`}
          {view_mode === 'session_detail' &&
            selected_profile &&
            `会话 ${selected_profile.session_id.slice(0, 8)}... · ${selected_profile.snapshot_history.length} 快照`}
          {view_mode === 'alerts' && `${alerts.length} 告警`}
          {view_mode === 'trends' &&
            selected_profile &&
            `${selected_profile.snapshot_history.length} 数据点`}
        </span>
        <span>{new Date(state.last_update_ms).toLocaleTimeString()}</span>
      </div>
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════
// 工具组件: SummaryCard
// ═══════════════════════════════════════════════════════════════════════════

const SummaryCard: React.FC<{
  label: string;
  value: number | string | React.ReactNode;
  unit: string;
  highlight?: boolean;
  raw?: boolean;
}> = ({ label, value, unit, highlight = false, raw = false }) => (
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
      <span className="text-xs font-normal text-gray-400">{unit}</span>
    </div>
  </div>
);
