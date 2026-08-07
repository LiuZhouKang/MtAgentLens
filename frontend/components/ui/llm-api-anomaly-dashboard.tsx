'use client';

import React, { useMemo } from 'react';
import type {
  LlmApiAnomalyDashboardProps,
  LlmApiAnomalyDashboardState,
  AnomalyAlertCardProps,
  AnomalyAlertDetailProps,
  AnomalyTypeIconProps,
  LlmApiSeverityBadgeProps,
  AnomalyTypeDistributionProps,
  AnomalyType,
  LlmApiSeverity,
  LlmApiDashboardViewMode,
  LlmApiAnomalyAlert,
} from '@/lib/llm-api-anomaly-contract';
import {
  ANOMALY_TYPE_LABEL,
  ANOMALY_TYPE_DESC,
  ANOMALY_TYPE_ICON,
  LLM_API_SEVERITY_LABEL,
  severityDistribution,
  anomalyTypeCounts,
  aggregateByAgent,
  aggregateByEndpoint,
  extractAgentPids,
  extractEndpoints,
  findSelectedAlert,
  maxSeverity,
  hasCritical,
  calcWindowDurationMs,
  formatWindowDuration,
  formatWindowUs,
} from '@/lib/llm-api-anomaly-contract';

// ═══════════════════════════════════════════════════════════════════════════
// 常量
// ═══════════════════════════════════════════════════════════════════════════

const VIEW_LABELS: { mode: LlmApiDashboardViewMode; label: string }[] = [
  { mode: 'overview', label: '概览' },
  { mode: 'alerts', label: '告警列表' },
  { mode: 'endpoints', label: '端点分析' },
];

const SEVERITY_RING: Record<LlmApiSeverity, string> = {
  Info: 'ring-blue-400',
  Warning: 'ring-amber-400',
  Critical: 'ring-red-600',
};

const SEVERITY_BG: Record<LlmApiSeverity, string> = {
  Info: 'bg-blue-50 border-blue-200',
  Warning: 'bg-amber-50 border-amber-200',
  Critical: 'bg-red-50 border-red-200',
};

const SEVERITY_DOT: Record<LlmApiSeverity, string> = {
  Info: 'bg-blue-400',
  Warning: 'bg-amber-400 animate-pulse',
  Critical: 'bg-red-600 animate-pulse',
};

const SEVERITY_BORDER: Record<LlmApiSeverity, string> = {
  Info: 'border-l-blue-400',
  Warning: 'border-l-amber-400',
  Critical: 'border-l-red-600',
};

const ANOMALY_TYPE_COLOR: Record<AnomalyType, string> = {
  RateBurst: 'text-amber-600 bg-amber-50',
  RepeatedIdenticalRequest: 'text-indigo-600 bg-indigo-50',
  OversizedRequest: 'text-purple-600 bg-purple-50',
  NewDestination: 'text-teal-600 bg-teal-50',
  SustainedHighFrequency: 'text-orange-600 bg-orange-50',
  ErrorStorm: 'text-rose-600 bg-rose-50',
};

// ═══════════════════════════════════════════════════════════════════════════
// 子组件: AnomalyTypeIcon — 异常类型图标
// ═══════════════════════════════════════════════════════════════════════════

export const AnomalyTypeIcon: React.FC<AnomalyTypeIconProps> = ({ anomaly_type, size = 'md' }) => {
  const sizeClass = size === 'sm' ? 'text-sm' : size === 'lg' ? 'text-xl' : 'text-base';
  return (
    <span className={sizeClass} title={ANOMALY_TYPE_DESC[anomaly_type]}>
      {ANOMALY_TYPE_ICON[anomaly_type]}
    </span>
  );
};

// ═══════════════════════════════════════════════════════════════════════════
// 子组件: LlmApiSeverityBadge — 严重度徽章
// ═══════════════════════════════════════════════════════════════════════════

export const LlmApiSeverityBadge: React.FC<LlmApiSeverityBadgeProps> = ({
  severity,
  showLabel = true,
}) => {
  const colors: Record<LlmApiSeverity, string> = {
    Info: 'bg-blue-100 text-blue-800',
    Critical: 'bg-red-100 text-red-800',
    Warning: 'bg-amber-100 text-amber-800',
  };

  return (
    <span
      className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium ${colors[severity]}`}
    >
      <span className={`inline-block h-1.5 w-1.5 rounded-full ${SEVERITY_DOT[severity]}`} />
      {showLabel && LLM_API_SEVERITY_LABEL[severity]}
    </span>
  );
};

// ═══════════════════════════════════════════════════════════════════════════
// 子组件: AnomalyAlertCard — 异常告警卡片
// ═══════════════════════════════════════════════════════════════════════════

export const AnomalyAlertCard: React.FC<AnomalyAlertCardProps> = ({
  alert,
  selected,
  onSelect,
}) => {
  const windowMs = calcWindowDurationMs(alert);

  return (
    <button
      onClick={onSelect}
      className={`w-full cursor-pointer rounded border border-l-4 p-3 text-left transition-all ${SEVERITY_BORDER[alert.severity]} ${selected ? 'bg-blue-50 ring-2 ring-blue-400' : 'bg-white hover:bg-slate-50'} `}
    >
      {/* 顶部行：类型图标 + 异常类型 + 严重度 */}
      <div className="mb-1.5 flex items-center gap-2">
        <AnomalyTypeIcon anomaly_type={alert.anomaly_type} size="sm" />
        <span className="text-sm font-semibold">{ANOMALY_TYPE_LABEL[alert.anomaly_type]}</span>
        <LlmApiSeverityBadge severity={alert.severity} />
        {alert.is_cooldown && (
          <span className="ml-auto flex-shrink-0 text-xs text-gray-400">冷却中</span>
        )}
      </div>

      {/* 描述行 */}
      <div className="mb-1.5 line-clamp-2 text-xs text-gray-500">{alert.description}</div>

      {/* 关键指标行 */}
      <div className="flex items-center gap-3 text-xs text-gray-400">
        <span>
          📞 {alert.call_count} 次调用{' '}
          <span className="text-gray-300">/ 阈值 {alert.threshold}</span>
        </span>
        <span>⏱ {formatWindowDuration(alert.window_end_ns - alert.window_start_ns)}</span>
        <span className="font-mono">PID {alert.agent_root_pid}</span>
      </div>

      {/* 涉及端点 */}
      {alert.affected_endpoints.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-1">
          {alert.affected_endpoints.slice(0, 3).map((ep) => (
            <span
              key={ep}
              className="max-w-[200px] truncate rounded bg-slate-100 px-1.5 py-0.5 font-mono text-xs text-slate-500"
            >
              {ep}
            </span>
          ))}
          {alert.affected_endpoints.length > 3 && (
            <span className="text-xs text-gray-400">
              +{alert.affected_endpoints.length - 3} 个端点
            </span>
          )}
        </div>
      )}
    </button>
  );
};

// ═══════════════════════════════════════════════════════════════════════════
// 子组件: AnomalyAlertDetail — 异常告警详情面板
// ═══════════════════════════════════════════════════════════════════════════

export const AnomalyAlertDetail: React.FC<AnomalyAlertDetailProps> = ({
  alert,
  onAgentClick,
  onEndpointClick,
}) => {
  const windowMs = calcWindowDurationMs(alert);

  return (
    <div className={`rounded-lg border p-4 ${SEVERITY_BG[alert.severity]} space-y-4`}>
      {/* 标题行 */}
      <div className="flex items-center gap-2">
        <AnomalyTypeIcon anomaly_type={alert.anomaly_type} size="lg" />
        <div>
          <h3 className="text-sm font-semibold">{ANOMALY_TYPE_LABEL[alert.anomaly_type]}</h3>
          <p className="text-xs text-gray-500">{ANOMALY_TYPE_DESC[alert.anomaly_type]}</p>
        </div>
        <div className="ml-auto">
          <LlmApiSeverityBadge severity={alert.severity} />
        </div>
      </div>

      {/* 描述 */}
      <div className="rounded bg-white/60 p-2 text-sm text-gray-700">{alert.description}</div>

      {/* 关键指标网格 */}
      <div className="grid grid-cols-2 gap-2 text-xs">
        <div className="rounded bg-white/60 p-2">
          <div className="text-gray-500">API 调用次数</div>
          <div className="font-mono text-lg font-semibold">
            {alert.call_count}
            <span className="ml-1 text-xs text-gray-400">/ {alert.threshold} 阈值</span>
          </div>
        </div>
        <div className="rounded bg-white/60 p-2">
          <div className="text-gray-500">检测窗口</div>
          <div className="font-mono text-lg font-semibold">
            {formatWindowDuration(alert.window_end_ns - alert.window_start_ns)}
          </div>
        </div>
        <div className="rounded bg-white/60 p-2">
          <div className="text-gray-500">平均调用间隔</div>
          <div className="font-mono font-semibold">{formatWindowUs(alert.avg_interval_us)}</div>
        </div>
        <div className="rounded bg-white/60 p-2">
          <div className="text-gray-500">触发 Agent</div>
          <button
            onClick={() => onAgentClick(alert.agent_root_pid)}
            className="cursor-pointer font-mono font-semibold text-blue-600 hover:underline"
          >
            PID {alert.agent_root_pid}
          </button>
        </div>
      </div>

      {/* 涉及端点 */}
      {alert.affected_endpoints.length > 0 && (
        <div>
          <h4 className="mb-2 text-xs font-semibold tracking-wide text-gray-500 uppercase">
            涉及端点 ({alert.affected_endpoints.length})
          </h4>
          <div className="space-y-1">
            {alert.affected_endpoints.map((ep) => (
              <button
                key={ep}
                onClick={() => onEndpointClick(ep)}
                className="block w-full cursor-pointer rounded bg-white/60 px-2 py-1 text-left font-mono text-xs text-blue-600 hover:underline"
              >
                {ep}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Payload 指纹 */}
      {alert.sample_payload_hash && (
        <div>
          <h4 className="mb-1 text-xs font-semibold tracking-wide text-gray-500 uppercase">
            采样 Payload 指纹
          </h4>
          <div className="rounded bg-white/60 p-2 font-mono text-xs break-all text-gray-600">
            {alert.sample_payload_hash}
          </div>
        </div>
      )}

      {/* 时间信息 */}
      <div className="border-t border-white/40 pt-2 text-xs text-gray-400">
        <div>
          窗口范围: {new Date(alert.window_start_ns / 1_000_000).toLocaleTimeString()} →{' '}
          {new Date(alert.window_end_ns / 1_000_000).toLocaleTimeString()}
        </div>
      </div>
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════
// 子组件: AnomalyTypeDistribution — 异常类型分布图（纯 CSS 条形图）
// ═══════════════════════════════════════════════════════════════════════════

const AnomalyTypeDistribution: React.FC<AnomalyTypeDistributionProps> = ({
  distribution,
  selected_type,
  onTypeSelect,
}) => {
  const entries = (Object.entries(distribution) as [AnomalyType, number][])
    .filter(([, count]) => count > 0)
    .sort((a, b) => b[1] - a[1]);
  const total = entries.reduce((sum, [, c]) => sum + c, 0);
  const maxCount = entries[0]?.[1] ?? 1;

  if (entries.length === 0) {
    return <div className="p-4 text-sm text-gray-400">暂无异常记录</div>;
  }

  return (
    <div className="space-y-2">
      {entries.map(([type, count]) => {
        const isSelected = selected_type === type;
        return (
          <button
            key={type}
            onClick={() => onTypeSelect(isSelected ? null : type)}
            className="group w-full text-left"
          >
            <div className="mb-1 flex items-center justify-between text-xs">
              <span className="flex items-center gap-1.5">
                <AnomalyTypeIcon anomaly_type={type} size="sm" />
                <span className={`${isSelected ? 'font-semibold text-blue-600' : 'text-gray-700'}`}>
                  {ANOMALY_TYPE_LABEL[type]}
                </span>
              </span>
              <span className="ml-2 flex-shrink-0 text-gray-400">
                {count} 次 · {total > 0 ? Math.round((count / total) * 100) : 0}%
              </span>
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
// 子组件: SeverityDistribution — 严重度分布图（堆叠条形图）
// ═══════════════════════════════════════════════════════════════════════════

const SeverityDistributionChart: React.FC<{
  distribution: Record<LlmApiSeverity, { count: number; pct: number }>;
}> = ({ distribution }) => {
  const severities: { key: LlmApiSeverity; color: string }[] = [
    { key: 'Critical', color: 'bg-red-500' },
    { key: 'Warning', color: 'bg-amber-400' },
    { key: 'Info', color: 'bg-blue-400' },
  ];

  const total = Object.values(distribution).reduce((s, v) => s + v.count, 0);

  return (
    <div>
      <div className="mb-2 flex h-2 overflow-hidden rounded-full bg-slate-100">
        {severities.map(({ key, color }) => {
          const pct = distribution[key].pct * 100;
          return pct > 0 ? (
            <div
              key={key}
              className={`h-full ${color} transition-all`}
              style={{ width: `${pct}%` }}
              title={`${LLM_API_SEVERITY_LABEL[key]}: ${distribution[key].count}`}
            />
          ) : null;
        })}
      </div>
      <div className="flex gap-3 text-xs text-gray-500">
        {severities.map(({ key, color }) => (
          <div key={key} className="flex items-center gap-1">
            <span className={`inline-block h-2 w-2 rounded-full ${color}`} />
            {LLM_API_SEVERITY_LABEL[key]} {distribution[key].count}
          </div>
        ))}
      </div>
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════
// 子组件: AlertsTimeline — 告警时间线列表
// ═══════════════════════════════════════════════════════════════════════════

const AlertsTimeline: React.FC<{
  alerts: LlmApiAnomalyAlert[];
  selected_alert_uid: string | null;
  onSelect: (alert: LlmApiAnomalyAlert) => void;
}> = ({ alerts, selected_alert_uid, onSelect }) => {
  const sorted = useMemo(
    () => [...alerts].sort((a, b) => b.timestamp_epoch_ms - a.timestamp_epoch_ms),
    [alerts],
  );

  if (sorted.length === 0) {
    return <div className="p-4 text-sm text-gray-400">暂无告警记录</div>;
  }

  return (
    <div className="max-h-[60vh] space-y-2 overflow-y-auto">
      {sorted.map((alert) => (
        <AnomalyAlertCard
          key={alert.alert_uid}
          alert={alert}
          selected={alert.alert_uid === selected_alert_uid}
          onSelect={() => onSelect(alert)}
        />
      ))}
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════
// 子组件: EndpointStatsTable — 端点统计表
// ═══════════════════════════════════════════════════════════════════════════

const EndpointStatsTable: React.FC<{
  alerts: LlmApiAnomalyAlert[];
  selected_endpoint: string | null;
  onEndpointSelect: (endpoint: string | null) => void;
}> = ({ alerts, selected_endpoint, onEndpointSelect }) => {
  const stats = useMemo(() => aggregateByEndpoint(alerts), [alerts]);

  if (stats.length === 0) {
    return <div className="p-4 text-sm text-gray-400">暂无端点异常数据</div>;
  }

  return (
    <div className="max-h-[60vh] space-y-2 overflow-y-auto">
      {stats.map((ep) => {
        const isSelected = selected_endpoint === ep.endpoint;
        return (
          <button
            key={ep.endpoint}
            onClick={() => onEndpointSelect(isSelected ? null : ep.endpoint)}
            className={`w-full cursor-pointer rounded border p-3 text-left transition-all ${isSelected ? 'border-blue-300 bg-blue-50 ring-2 ring-blue-400' : 'border-slate-200 bg-white hover:bg-slate-50'} `}
          >
            <div className="mb-1.5 flex items-center justify-between">
              <span className="max-w-[300px] truncate font-mono text-sm font-semibold text-gray-800">
                {ep.endpoint}
              </span>
              <LlmApiSeverityBadge severity={ep.max_severity} showLabel={false} />
            </div>

            <div className="flex items-center gap-3 text-xs text-gray-500">
              <span>告警 {ep.alert_count} 次</span>
              <span>涉及 {ep.affected_agents.length} 个 Agent</span>
              {ep.new_destination_alerts > 0 && (
                <span className="font-medium text-teal-600">
                  新发现 ({ep.new_destination_alerts})
                </span>
              )}
            </div>

            {/* 异常类型标签 */}
            <div className="mt-1.5 flex flex-wrap gap-1">
              {ep.anomaly_types.map((type) => (
                <span
                  key={type}
                  className={`rounded px-1.5 py-0.5 text-xs ${ANOMALY_TYPE_COLOR[type]}`}
                >
                  {ANOMALY_TYPE_ICON[type]} {ANOMALY_TYPE_LABEL[type]}
                </span>
              ))}
            </div>

            {/* Agent PID 列表 */}
            <div className="mt-1.5 font-mono text-xs text-gray-400">
              PID: {ep.affected_agents.join(', ')}
            </div>
          </button>
        );
      })}
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════
// 主组件: LlmApiAnomalyDashboard
// ═══════════════════════════════════════════════════════════════════════════

export const LlmApiAnomalyDashboard: React.FC<LlmApiAnomalyDashboardProps> = ({
  state,
  onViewModeChange,
  onAlertSelect,
  onTypeFilterChange,
  onSeverityFilterChange,
  onAgentFilterChange,
  onEndpointFilterChange,
  onNavigateToAgent,
  onNavigateToEndpoint,
  compact = false,
}) => {
  const {
    alerts,
    view_mode,
    selected_alert_uid,
    type_filter,
    severity_filter,
    agent_filter,
    endpoint_filter,
  } = state;

  // ── 过滤后的告警 ──
  const filteredAlerts = useMemo(() => {
    let result = alerts;
    if (type_filter) result = result.filter((a) => a.anomaly_type === type_filter);
    if (severity_filter) result = result.filter((a) => a.severity === severity_filter);
    if (agent_filter != null) result = result.filter((a) => a.agent_root_pid === agent_filter);
    if (endpoint_filter)
      result = result.filter((a) => a.affected_endpoints.includes(endpoint_filter));
    return result;
  }, [alerts, type_filter, severity_filter, agent_filter, endpoint_filter]);

  // ── 选中的告警 ──
  const selectedAlert = useMemo(
    () => findSelectedAlert(filteredAlerts, selected_alert_uid),
    [filteredAlerts, selected_alert_uid],
  );

  // ── 统计数据 ──
  const sevDist = useMemo(() => severityDistribution(filteredAlerts), [filteredAlerts]);
  const typeCounts = useMemo(() => anomalyTypeCounts(filteredAlerts), [filteredAlerts]);
  const overallSeverity = useMemo(() => maxSeverity(filteredAlerts), [filteredAlerts]);
  const hasCriticalAnomaly = useMemo(() => hasCritical(filteredAlerts), [filteredAlerts]);

  // ── 过滤选项 ──
  const agentPids = useMemo(() => extractAgentPids(alerts), [alerts]);
  const endpoints = useMemo(() => extractEndpoints(alerts), [alerts]);

  // ── 空状态 ──
  if (alerts.length === 0) {
    return (
      <div className="p-6 text-center text-gray-400">
        <div className="mb-2 text-2xl">📡</div>
        <div className="text-sm">未检测到 LLM API 用量异常</div>
        <div className="mt-1 text-xs">LLMApiUsageAnomalyAnalyzer 正在监控中...</div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* ═══ 顶部状态栏 ═══ */}
      {overallSeverity && (
        <div
          className={`flex items-center gap-3 rounded-lg border p-3 text-sm ${
            hasCriticalAnomaly ? 'border-red-200 bg-red-50' : SEVERITY_BG[overallSeverity]
          }`}
        >
          <span className="text-lg">{hasCriticalAnomaly ? '🚨' : '⚠️'}</span>
          <div className="flex-1">
            <span className="font-semibold">{alerts.length} 个 API 异常告警</span>
            <span className="ml-2 text-gray-500">
              · 最高严重度: {LLM_API_SEVERITY_LABEL[overallSeverity]}
            </span>
          </div>
          <span className="text-xs text-gray-400">
            {filteredAlerts.length !== alerts.length &&
              `显示 ${filteredAlerts.length}/${alerts.length}`}
          </span>
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

        {/* 异常类型过滤 */}
        <select
          className="rounded border border-slate-300 bg-white px-2 py-1.5 text-xs"
          value={type_filter ?? ''}
          onChange={(e) => {
            const v = e.target.value;
            onTypeFilterChange(v ? (v as AnomalyType) : null);
          }}
        >
          <option value="">全部类型</option>
          {(Object.entries(ANOMALY_TYPE_LABEL) as [AnomalyType, string][]).map(([k, v]) => (
            <option key={k} value={k}>
              {ANOMALY_TYPE_ICON[k]} {v}
            </option>
          ))}
        </select>

        {/* 严重度过滤 */}
        <select
          className="rounded border border-slate-300 bg-white px-2 py-1.5 text-xs"
          value={severity_filter ?? ''}
          onChange={(e) => {
            const v = e.target.value;
            onSeverityFilterChange(v ? (v as LlmApiSeverity) : null);
          }}
        >
          <option value="">全部严重度</option>
          {(Object.entries(LLM_API_SEVERITY_LABEL) as [LlmApiSeverity, string][]).map(([k, v]) => (
            <option key={k} value={k}>
              {v}
            </option>
          ))}
        </select>

        {/* Agent PID 过滤 */}
        {agentPids.length > 0 && (
          <select
            className="rounded border border-slate-300 bg-white px-2 py-1.5 text-xs"
            value={agent_filter ?? ''}
            onChange={(e) => {
              const v = e.target.value;
              onAgentFilterChange(v ? Number(v) : null);
            }}
          >
            <option value="">全部 Agent</option>
            {agentPids.map((pid) => (
              <option key={pid} value={pid}>
                PID {pid}
              </option>
            ))}
          </select>
        )}

        {/* 端点过滤 */}
        {endpoints.length > 0 && (
          <select
            className="max-w-[200px] rounded border border-slate-300 bg-white px-2 py-1.5 text-xs"
            value={endpoint_filter ?? ''}
            onChange={(e) => {
              const v = e.target.value;
              onEndpointFilterChange(v || null);
            }}
          >
            <option value="">全部端点</option>
            {endpoints.map((ep) => (
              <option key={ep} value={ep}>
                {ep}
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
            <h4 className="mb-3 text-xs font-semibold tracking-wide text-gray-500 uppercase">
              严重度分布
            </h4>
            <SeverityDistributionChart distribution={sevDist} />
          </div>

          {/* 异常类型分布 */}
          <div className="rounded-lg border border-slate-200 bg-white p-4">
            <h4 className="mb-3 text-xs font-semibold tracking-wide text-gray-500 uppercase">
              异常类型分布
            </h4>
            <AnomalyTypeDistribution
              distribution={typeCounts}
              selected_type={type_filter}
              onTypeSelect={onTypeFilterChange}
            />
          </div>

          {/* 快速统计卡片 */}
          <div className={compact ? '' : 'md:col-span-2'}>
            <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
              <div className="rounded-lg border border-slate-200 bg-white p-3 text-center">
                <div className="font-mono text-2xl font-bold text-gray-800">{alerts.length}</div>
                <div className="text-xs text-gray-500">总告警数</div>
              </div>
              <div className="rounded-lg border border-slate-200 bg-white p-3 text-center">
                <div className="font-mono text-2xl font-bold text-gray-800">{agentPids.length}</div>
                <div className="text-xs text-gray-500">涉及 Agent</div>
              </div>
              <div className="rounded-lg border border-slate-200 bg-white p-3 text-center">
                <div className="font-mono text-2xl font-bold text-gray-800">{endpoints.length}</div>
                <div className="text-xs text-gray-500">涉及端点</div>
              </div>
              <div className="rounded-lg border border-slate-200 bg-white p-3 text-center">
                <div className="font-mono text-2xl font-bold text-red-600">
                  {sevDist.Critical.count}
                </div>
                <div className="text-xs text-gray-500">严重异常</div>
              </div>
            </div>
          </div>

          {/* 选中的告警详情 */}
          {selectedAlert && (
            <div className={compact ? '' : 'md:col-span-2'}>
              <AnomalyAlertDetail
                alert={selectedAlert}
                onAgentClick={onNavigateToAgent}
                onEndpointClick={onNavigateToEndpoint}
              />
            </div>
          )}
        </div>
      )}

      {/* ═══ 告警列表视图 ═══ */}
      {view_mode === 'alerts' && (
        <div
          className={`grid gap-4 ${
            compact || !selectedAlert ? 'grid-cols-1' : 'grid-cols-1 md:grid-cols-2'
          }`}
        >
          <AlertsTimeline
            alerts={filteredAlerts}
            selected_alert_uid={selected_alert_uid}
            onSelect={onAlertSelect}
          />
          {selectedAlert && !compact && (
            <AnomalyAlertDetail
              alert={selectedAlert}
              onAgentClick={onNavigateToAgent}
              onEndpointClick={onNavigateToEndpoint}
            />
          )}
        </div>
      )}

      {/* ═══ 端点分析视图 ═══ */}
      {view_mode === 'endpoints' && (
        <div
          className={`grid gap-4 ${
            compact || !selectedAlert ? 'grid-cols-1' : 'grid-cols-1 md:grid-cols-2'
          }`}
        >
          <div className="rounded-lg border border-slate-200 bg-white p-4">
            <h4 className="mb-3 text-xs font-semibold tracking-wide text-gray-500 uppercase">
              LLM API 端点异常统计
            </h4>
            <EndpointStatsTable
              alerts={filteredAlerts}
              selected_endpoint={endpoint_filter}
              onEndpointSelect={onEndpointFilterChange}
            />
          </div>
          {selectedAlert && (
            <AnomalyAlertDetail
              alert={selectedAlert}
              onAgentClick={onNavigateToAgent}
              onEndpointClick={onNavigateToEndpoint}
            />
          )}
        </div>
      )}

      {/* ═══ 底部状态栏 ═══ */}
      <div className="flex items-center justify-between border-t border-slate-100 pt-2 text-xs text-gray-400">
        <span>共 {alerts.length} 条告警</span>
        <span>
          显示 {filteredAlerts.length} 条
          {(type_filter || severity_filter || agent_filter != null || endpoint_filter) &&
            ' (已过滤)'}
        </span>
      </div>
    </div>
  );
};
