'use client';

import React, { useMemo } from 'react';
import type {
  DataExfilDashboardProps,
  ExfilAlertCardProps,
  ExfilAlertDetailProps,
  ExfilSeverityBadgeProps,
  ExfilTypeIconProps,
  DestinationChartProps,
  ExfilTypeDistributionProps,
  AgentExfilSummaryProps,
  DataExfilAlert,
  ExfilType,
  ExfilSeverity,
  ExfilDashboardViewMode,
} from '@/lib/data-exfiltration-contract';
import {
  EXFIL_TYPE_LABEL,
  EXFIL_TYPE_DESC,
  EXFIL_SEVERITY_LABEL,
  formatBytes,
  formatTimeWindow,
  aggregateByAgent,
  aggregateByDestination,
  severityDistribution,
  extractAgentPids,
  extractDestinations,
  findSelectedAlert,
} from '@/lib/data-exfiltration-contract';

// ═══════════════════════════════════════════════════════════════════════════
// 常量
// ═══════════════════════════════════════════════════════════════════════════

/** 严重程度 → 颜色映射（Tailwind CSS 类） */
const SEVERITY_COLORS: Record<
  ExfilSeverity,
  {
    ring: string;
    border: string;
    bg: string;
    text: string;
    dot: string;
    bar: string;
  }
> = {
  Low: {
    ring: 'ring-emerald-400',
    border: 'border-emerald-300',
    bg: 'bg-emerald-50',
    text: 'text-emerald-700',
    dot: 'bg-emerald-400',
    bar: 'bg-emerald-400',
  },
  Medium: {
    ring: 'ring-amber-400',
    border: 'border-amber-300',
    bg: 'bg-amber-50',
    text: 'text-amber-700',
    dot: 'bg-amber-400',
    bar: 'bg-amber-400',
  },
  High: {
    ring: 'ring-orange-400',
    border: 'border-orange-300',
    bg: 'bg-orange-50',
    text: 'text-orange-700',
    dot: 'bg-orange-500 animate-pulse',
    bar: 'bg-orange-500',
  },
  Critical: {
    ring: 'ring-red-400',
    border: 'border-red-300',
    bg: 'bg-red-50',
    text: 'text-red-700',
    dot: 'bg-red-500 animate-pulse',
    bar: 'bg-red-500',
  },
};

/** 外泄类型 → 图标 */
const EXFIL_TYPE_ICON: Record<ExfilType, string> = {
  ReadThenSend: '🔗',
  LargeUpload: '📤',
  BatchExport: '📦',
  CovertChannel: '🕵️',
};

/** 视图模式标签 */
const VIEW_LABELS: { mode: ExfilDashboardViewMode; label: string }[] = [
  { mode: 'overview', label: '概览' },
  { mode: 'alerts', label: '告警' },
  { mode: 'destinations', label: '目标' },
  { mode: 'timeline', label: '时间线' },
];

// ═══════════════════════════════════════════════════════════════════════════
// 子组件: ExfilSeverityBadge — 严重度徽章
// ═══════════════════════════════════════════════════════════════════════════

/** @deprecated 阶段二实时视角（SSE 接入后迁移至运行监控区块），现无生产者，仅存档。 */
export const ExfilSeverityBadge: React.FC<ExfilSeverityBadgeProps> = ({
  severity,
  showLabel = true,
}) => {
  const c = SEVERITY_COLORS[severity];
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${c.bg} ${c.text}`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${c.dot}`} />
      {showLabel && EXFIL_SEVERITY_LABEL[severity]}
    </span>
  );
};

// ═══════════════════════════════════════════════════════════════════════════
// 子组件: ExfilTypeIcon — 外泄类型图标
// ═══════════════════════════════════════════════════════════════════════════

export const ExfilTypeIcon: React.FC<ExfilTypeIconProps> = ({ exfil_type, size = 'md' }) => (
  <span
    className={`inline-flex items-center justify-center ${size === 'sm' ? 'text-sm' : 'text-lg'}`}
    title={EXFIL_TYPE_DESC[exfil_type]}
  >
    {EXFIL_TYPE_ICON[exfil_type]}
  </span>
);

// ═══════════════════════════════════════════════════════════════════════════
// 子组件: ExfilAlertCard — 单条外泄告警卡片
// ═══════════════════════════════════════════════════════════════════════════

export const ExfilAlertCard: React.FC<ExfilAlertCardProps> = ({
  alert,
  selected = false,
  onSelect,
}) => {
  const c = SEVERITY_COLORS[alert.severity];

  return (
    <button
      onClick={onSelect}
      className={`w-full cursor-pointer rounded border-l-4 p-3 text-left transition-all ${c.border} ${selected ? `ring-2 ${c.ring} ${c.bg}` : 'bg-white hover:bg-slate-50'} `}
    >
      {/* 头部：类型图标 + 类型标签 + 严重度 */}
      <div className="mb-2 flex items-center gap-2">
        <ExfilTypeIcon exfil_type={alert.exfil_type} size="sm" />
        <span className="text-xs font-medium text-gray-600">
          {EXFIL_TYPE_LABEL[alert.exfil_type]}
        </span>
        <ExfilSeverityBadge severity={alert.severity} />
        {alert.is_active && (
          <span className="ml-auto animate-pulse text-xs text-blue-500">● 活跃</span>
        )}
      </div>

      {/* 目标信息 */}
      <div className="mb-1 truncate font-mono text-xs text-gray-500">
        → {alert.destination.host}:{alert.destination.port}
        {alert.destination.path}
      </div>

      {/* 数据量 + 源文件 */}
      <div className="flex items-center gap-3 text-xs text-gray-400">
        <span className="font-mono">{formatBytes(alert.payload_size_bytes)}</span>
        {alert.source_file && (
          <span className="truncate" title={alert.source_file}>
            📄 {alert.source_file.split('/').pop()}
          </span>
        )}
        {alert.correlation_score > 0 && (
          <span className="ml-auto">置信度: {(alert.correlation_score * 100).toFixed(0)}%</span>
        )}
      </div>

      {/* 关联置信度进度条 */}
      {alert.correlation_score > 0 && (
        <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-slate-100">
          <div
            className={`h-full rounded-full transition-all ${c.bar}`}
            style={{ width: `${alert.correlation_score * 100}%` }}
          />
        </div>
      )}
    </button>
  );
};

// ═══════════════════════════════════════════════════════════════════════════
// 子组件: ExfilAlertDetail — 告警详情面板
// ═══════════════════════════════════════════════════════════════════════════

export const ExfilAlertDetail: React.FC<ExfilAlertDetailProps> = ({ alert, onAgentClick }) => {
  const c = SEVERITY_COLORS[alert.severity];

  return (
    <div className={`rounded-lg border-2 p-4 ${c.border} ${c.bg}`}>
      {/* 标题 */}
      <div className="mb-3 flex items-center gap-2">
        <ExfilTypeIcon exfil_type={alert.exfil_type} />
        <h3 className="text-sm font-semibold">{EXFIL_TYPE_LABEL[alert.exfil_type]}</h3>
        <ExfilSeverityBadge severity={alert.severity} />
      </div>

      {/* 目标端点详情 */}
      <div className="mb-3 rounded bg-white/60 p-3 font-mono text-xs">
        <div className="mb-1 text-gray-500">外泄目标</div>
        <div>
          <span className="text-gray-400">host: </span>
          <span className="font-semibold text-gray-800">{alert.destination.host}</span>
        </div>
        <div>
          <span className="text-gray-400">path: </span>
          <span className="text-gray-800">{alert.destination.path}</span>
        </div>
        <div>
          <span className="text-gray-400">port: </span>
          <span className="text-gray-800">{alert.destination.port}</span>
        </div>
      </div>

      {/* 关键指标网格 */}
      <div className="mb-3 grid grid-cols-2 gap-2 text-xs">
        <div className="rounded bg-white/60 p-2">
          <div className="text-gray-500">载荷大小</div>
          <div className="font-mono font-semibold">{formatBytes(alert.payload_size_bytes)}</div>
        </div>
        <div className="rounded bg-white/60 p-2">
          <div className="text-gray-500">累计外传</div>
          <div className="font-mono font-semibold">{formatBytes(alert.total_exported_bytes)}</div>
        </div>
        <div className="rounded bg-white/60 p-2">
          <div className="text-gray-500">时间窗口</div>
          <div className="font-mono font-semibold">{formatTimeWindow(alert.time_window_ms)}</div>
        </div>
        <div className="rounded bg-white/60 p-2">
          <div className="text-gray-500">关联置信度</div>
          <div className="font-mono font-semibold">
            {(alert.correlation_score * 100).toFixed(0)}%
          </div>
        </div>
      </div>

      {/* 证据摘要 */}
      <div className="mb-3 rounded bg-white/60 p-3 text-xs">
        <div className="mb-2 font-medium text-gray-500">证据摘要</div>
        <div className="grid grid-cols-2 gap-1.5">
          <div className="flex justify-between">
            <span className="text-gray-400">文件读取次数</span>
            <span className="font-mono font-semibold">{alert.evidence.file_read_count}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-gray-400">对外请求次数</span>
            <span className="font-mono font-semibold">{alert.evidence.outbound_request_count}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-gray-400">去重目标数</span>
            <span className="font-mono font-semibold">{alert.evidence.unique_destinations}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-gray-400">LLM API 已排除</span>
            <span className="font-mono font-semibold">
              {alert.evidence.llm_api_excluded ? '✅' : '❌'}
            </span>
          </div>
        </div>
      </div>

      {/* 源文件（ReadThenSend 模式） */}
      {alert.source_file && (
        <div className="mb-3 rounded bg-white/60 p-3 text-xs">
          <div className="mb-1 text-gray-500">源文件</div>
          <div className="font-mono break-all text-gray-800">{alert.source_file}</div>
        </div>
      )}

      {/* 内容匹配摘要 */}
      {alert.matched_content_excerpt && (
        <div className="mb-3 rounded bg-white/60 p-3 text-xs">
          <div className="mb-1 text-gray-500">
            内容匹配 ({formatBytes(alert.matched_content_length)})
          </div>
          <pre className="max-h-24 overflow-y-auto font-mono break-all whitespace-pre-wrap text-gray-700">
            {alert.matched_content_excerpt}
          </pre>
        </div>
      )}

      {/* Agent PID 链接 */}
      <div className="mb-3">
        <button
          onClick={() => onAgentClick(alert.agent_root_pid)}
          className="font-mono text-xs text-blue-600 underline hover:text-blue-800"
        >
          Agent PID: {alert.agent_root_pid} →
        </button>
      </div>

      {/* 关联事件列表 */}
      {alert.related_events.length > 0 && (
        <div className="text-xs">
          <div className="mb-1.5 font-medium text-gray-500">
            关联事件 ({alert.related_events.length})
          </div>
          <div className="max-h-32 space-y-1 overflow-y-auto">
            {alert.related_events.map((ev, idx) => (
              <div
                key={idx}
                className="flex items-center gap-2 rounded bg-white/40 p-1.5 font-mono"
              >
                <span className="w-20 flex-shrink-0 truncate text-gray-400">{ev.source}</span>
                <span className="flex-1 truncate text-gray-600">
                  {new Date(Math.floor(ev.timestamp_ns / 1_000_000)).toISOString()}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════
// 子组件: ExfilTypeDistribution — 类型分布图
// ═══════════════════════════════════════════════════════════════════════════

export const ExfilTypeDistribution: React.FC<ExfilTypeDistributionProps> = ({
  distribution,
  selected_type,
  onTypeSelect,
}) => {
  const total = Object.values(distribution).reduce((a, b) => a + b, 0);
  const types: ExfilType[] = ['ReadThenSend', 'LargeUpload', 'BatchExport', 'CovertChannel'];
  const typeBarColors: Record<ExfilType, string> = {
    ReadThenSend: 'bg-violet-500',
    LargeUpload: 'bg-cyan-500',
    BatchExport: 'bg-amber-500',
    CovertChannel: 'bg-rose-500',
  };

  if (total === 0) {
    return <div className="p-4 text-sm text-gray-400">暂无外泄告警数据</div>;
  }

  return (
    <div className="space-y-2">
      <h4 className="text-xs font-semibold tracking-wide text-gray-500 uppercase">外泄类型分布</h4>

      {/* 分段进度条 */}
      <div className="flex h-2.5 overflow-hidden rounded-full">
        {types.map((type) => {
          const pct = (distribution[type] || 0) / total;
          return pct > 0 ? (
            <div
              key={type}
              className={`h-full transition-all ${typeBarColors[type]} ${
                selected_type === type ? 'ring-2 ring-blue-400 ring-offset-1' : ''
              }`}
              style={{ width: `${pct * 100}%` }}
            />
          ) : null;
        })}
      </div>

      {/* 图例 */}
      <div className="grid grid-cols-2 gap-1.5">
        {types.map((type) => {
          const count = distribution[type] || 0;
          return (
            <button
              key={type}
              onClick={() => onTypeSelect(selected_type === type ? null : type)}
              className={`flex items-center gap-1.5 rounded p-1.5 text-xs transition-colors ${
                selected_type === type ? 'bg-slate-100 ring-1 ring-blue-300' : 'hover:bg-slate-50'
              }`}
            >
              <span className={`h-2.5 w-2.5 rounded-sm ${typeBarColors[type]} flex-shrink-0`} />
              <ExfilTypeIcon exfil_type={type} size="sm" />
              <span className="truncate text-gray-600">{EXFIL_TYPE_LABEL[type]}</span>
              <span className="ml-auto font-mono text-gray-400">{count}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════
// 子组件: DestinationChart — 目标端点分布（纯 CSS 水平条形图）
// ═══════════════════════════════════════════════════════════════════════════

export const DestinationChart: React.FC<DestinationChartProps> = ({
  destinations,
  selected_host,
  onHostSelect,
}) => {
  if (destinations.length === 0) {
    return <div className="p-4 text-sm text-gray-400">暂无目标端点数据</div>;
  }

  const maxBytes = destinations[0]?.total_bytes ?? 1;

  return (
    <div className="space-y-2">
      <h4 className="text-xs font-semibold tracking-wide text-gray-500 uppercase">
        目标端点 Top {destinations.length}
      </h4>
      {destinations.slice(0, 10).map((dest) => {
        const sevColors = SEVERITY_COLORS[dest.max_severity];
        const isSelected = selected_host === dest.host;

        return (
          <button
            key={dest.host}
            onClick={() => onHostSelect(isSelected ? null : dest.host)}
            className={`group w-full text-left`}
          >
            <div className="mb-1 flex items-center justify-between text-xs">
              <span
                className={`truncate font-mono ${
                  isSelected ? 'font-semibold text-blue-600' : 'text-gray-700'
                }`}
                title={dest.host}
              >
                {dest.host}
              </span>
              <span className="ml-2 flex-shrink-0 text-gray-400">
                {dest.alert_count} 告警 · {formatBytes(dest.total_bytes)}
              </span>
            </div>
            <div className="h-1.5 overflow-hidden rounded-full bg-slate-100">
              <div
                className={`h-full rounded-full transition-all ${
                  isSelected ? 'bg-blue-500' : sevColors.bar
                }`}
                style={{ width: `${Math.min((dest.total_bytes / maxBytes) * 100, 100)}%` }}
              />
            </div>
            {/* Agent 标签 */}
            <div className="mt-0.5 flex flex-wrap gap-1">
              {dest.affected_agents.slice(0, 3).map((pid) => (
                <span
                  key={pid}
                  className="rounded bg-slate-100 px-1 font-mono text-[10px] text-gray-400"
                >
                  PID:{pid}
                </span>
              ))}
              {dest.affected_agents.length > 3 && (
                <span className="text-[10px] text-gray-400">
                  +{dest.affected_agents.length - 3}
                </span>
              )}
            </div>
          </button>
        );
      })}
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════
// 子组件: AgentExfilSummary — Agent 外泄统计摘要卡片
// ═══════════════════════════════════════════════════════════════════════════

export const AgentExfilSummary: React.FC<AgentExfilSummaryProps> = ({
  stats,
  highlighted = false,
  onClick,
}) => {
  const maxSevColors = stats.max_severity
    ? SEVERITY_COLORS[stats.max_severity]
    : SEVERITY_COLORS['Low'];

  return (
    <button
      onClick={onClick}
      className={`w-full rounded-lg border-2 p-4 text-left transition-colors ${
        highlighted ? `border-blue-400 bg-blue-50` : `border-slate-200 bg-white hover:bg-slate-50`
      }`}
    >
      {/* Agent 标识 + 最高严重度 */}
      <div className="mb-3 flex items-center justify-between">
        <h3 className="font-mono text-sm font-semibold">Agent PID: {stats.agent_root_pid}</h3>
        {stats.max_severity && <ExfilSeverityBadge severity={stats.max_severity} />}
      </div>

      {/* 核心指标网格 */}
      <div className="mb-3 grid grid-cols-2 gap-2 text-xs">
        <div className="rounded bg-slate-50 p-2">
          <div className="text-gray-500">总告警</div>
          <div className="font-mono font-semibold">{stats.total_alerts}</div>
        </div>
        <div className="rounded bg-slate-50 p-2">
          <div className="text-gray-500">累计外传</div>
          <div className="font-mono font-semibold">{formatBytes(stats.total_exported_bytes)}</div>
        </div>
        <div className="rounded bg-slate-50 p-2">
          <div className="text-gray-500">涉及目标</div>
          <div className="font-mono font-semibold">{stats.unique_destinations.length}</div>
        </div>
        <div className="rounded bg-slate-50 p-2">
          <div className="text-gray-500">平均置信度</div>
          <div className="font-mono font-semibold">
            {(stats.avg_correlation_score * 100).toFixed(0)}%
          </div>
        </div>
      </div>

      {/* 类型分布迷你条 */}
      <div className="flex h-1.5 overflow-hidden rounded-full">
        {(
          [
            ['ReadThenSend', 'bg-violet-400'],
            ['LargeUpload', 'bg-cyan-400'],
            ['BatchExport', 'bg-amber-400'],
            ['CovertChannel', 'bg-rose-400'],
          ] as [ExfilType, string][]
        ).map(([type, barColor]) => {
          const count = stats.alerts_by_type[type] || 0;
          const pct = stats.total_alerts > 0 ? count / stats.total_alerts : 0;
          return pct > 0 ? (
            <div
              key={type}
              className={`h-full ${barColor}`}
              style={{ width: `${pct * 100}%` }}
              title={`${EXFIL_TYPE_LABEL[type]}: ${count}`}
            />
          ) : null;
        })}
      </div>
    </button>
  );
};

// ═══════════════════════════════════════════════════════════════════════════
// 子组件: SeverityGrid — 严重度分布四格
// ═══════════════════════════════════════════════════════════════════════════

const SeverityGrid: React.FC<{
  dist: ReturnType<typeof severityDistribution>;
  selected_severity: ExfilSeverity | null;
  onSelect: (s: ExfilSeverity | null) => void;
}> = ({ dist, selected_severity, onSelect }) => {
  const sevs: ExfilSeverity[] = ['Critical', 'High', 'Medium', 'Low'];

  return (
    <div className="space-y-2">
      <h4 className="text-xs font-semibold tracking-wide text-gray-500 uppercase">严重度分布</h4>
      <div className="grid grid-cols-2 gap-2">
        {sevs.map((sev) => {
          const c = SEVERITY_COLORS[sev];
          const isSelected = selected_severity === sev;
          return (
            <button
              key={sev}
              onClick={() => onSelect(isSelected ? null : sev)}
              className={`rounded-lg border-2 p-3 text-center transition-all ${
                isSelected
                  ? `ring-2 ring-blue-400 ${c.bg} ${c.border}`
                  : `border-slate-200 bg-white hover:bg-slate-50`
              }`}
            >
              <div className={`font-mono text-2xl font-bold ${c.text}`}>{dist[sev].count}</div>
              <div className="mt-0.5 text-xs text-gray-500">{EXFIL_SEVERITY_LABEL[sev]}</div>
              <div className={`text-[10px] ${c.text} mt-0.5`}>
                {(dist[sev].pct * 100).toFixed(0)}%
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════
// 子组件: ExfilTimeline — 告警时间线
// ═══════════════════════════════════════════════════════════════════════════

const ExfilTimeline: React.FC<{
  alerts: DataExfilAlert[];
  selected_id: string | null;
  onSelect: (alert: DataExfilAlert) => void;
}> = ({ alerts, selected_id, onSelect }) => {
  const sorted = useMemo(
    () => [...alerts].sort((a, b) => b.timestamp_epoch_ms - a.timestamp_epoch_ms),
    [alerts],
  );

  if (sorted.length === 0) {
    return <div className="p-4 text-sm text-gray-400">暂无外泄告警记录</div>;
  }

  return (
    <div className="max-h-[600px] space-y-2 overflow-y-auto">
      {sorted.map((alert) => (
        <div key={alert.alert_id}>
          {/* 时间戳 */}
          <div className="mb-1 ml-1 font-mono text-[10px] text-gray-400">
            {new Date(alert.timestamp_epoch_ms).toLocaleTimeString('zh-CN', {
              hour: '2-digit',
              minute: '2-digit',
              second: '2-digit',
            })}
          </div>
          <ExfilAlertCard
            alert={alert}
            selected={alert.alert_id === selected_id}
            onSelect={() => onSelect(alert)}
          />
        </div>
      ))}
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════
// 主组件: DataExfilDashboard
// ═══════════════════════════════════════════════════════════════════════════

export const DataExfilDashboard: React.FC<DataExfilDashboardProps> = ({
  state,
  onViewModeChange,
  onAlertSelect,
  onTypeFilterChange,
  onSeverityFilterChange,
  onAgentFilterChange,
  onDestinationFilterChange,
  onNavigateToAgent,
  compact = false,
}) => {
  const {
    alerts,
    view_mode,
    selected_alert_id,
    type_filter,
    severity_filter,
    agent_filter,
    destination_filter,
  } = state;

  // ── 过滤后的告警 ──
  const filteredAlerts = useMemo(() => {
    let result = alerts;
    if (type_filter) result = result.filter((a) => a.exfil_type === type_filter);
    if (severity_filter) result = result.filter((a) => a.severity === severity_filter);
    if (agent_filter) result = result.filter((a) => a.agent_root_pid === agent_filter);
    if (destination_filter)
      result = result.filter((a) => a.destination.host === destination_filter);
    return result;
  }, [alerts, type_filter, severity_filter, agent_filter, destination_filter]);

  // ── 选中告警 ──
  const selectedAlert = useMemo(
    () => findSelectedAlert(alerts, selected_alert_id),
    [alerts, selected_alert_id],
  );

  // ── 聚合数据 ──
  const agentStats = useMemo(() => aggregateByAgent(alerts), [alerts]);
  const destStats = useMemo(() => aggregateByDestination(alerts), [alerts]);
  const sevDist = useMemo(() => severityDistribution(alerts), [alerts]);
  const agentPids = useMemo(() => extractAgentPids(alerts), [alerts]);
  const destHosts = useMemo(() => extractDestinations(alerts), [alerts]);

  // ── 类型分布 ──
  const typeDist = useMemo(() => {
    const dist: Record<ExfilType, number> = {
      ReadThenSend: 0,
      LargeUpload: 0,
      BatchExport: 0,
      CovertChannel: 0,
    };
    for (const a of alerts) dist[a.exfil_type]++;
    return dist;
  }, [alerts]);

  // ── 空状态 ──
  if (alerts.length === 0) {
    return (
      <div className="p-6 text-center text-gray-400">
        <div className="mb-2 text-2xl">🛡️</div>
        <div className="text-sm">暂无数据外泄告警</div>
        <div className="mt-1 text-xs">DataExfiltrationDetector 尚未检测到异常</div>
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

        {/* 严重度过滤器 */}
        <select
          className="rounded border border-slate-300 bg-white px-2 py-1.5 text-xs"
          value={severity_filter ?? ''}
          onChange={(e) => {
            const v = e.target.value;
            onSeverityFilterChange(v ? (v as ExfilSeverity) : null);
          }}
        >
          <option value="">全部严重度</option>
          {Object.entries(EXFIL_SEVERITY_LABEL).map(([k, v]) => (
            <option key={k} value={k}>
              {v}
            </option>
          ))}
        </select>

        {/* 类型过滤器 */}
        <select
          className="rounded border border-slate-300 bg-white px-2 py-1.5 text-xs"
          value={type_filter ?? ''}
          onChange={(e) => {
            const v = e.target.value;
            onTypeFilterChange(v ? (v as ExfilType) : null);
          }}
        >
          <option value="">全部类型</option>
          {Object.entries(EXFIL_TYPE_LABEL).map(([k, v]) => (
            <option key={k} value={k}>
              {v}
            </option>
          ))}
        </select>

        {/* Agent PID 过滤器 */}
        {agentPids.length > 1 && (
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

        {/* 目标主机过滤器 */}
        {destHosts.length > 1 && (
          <select
            className="max-w-[200px] rounded border border-slate-300 bg-white px-2 py-1.5 text-xs"
            value={destination_filter ?? ''}
            onChange={(e) => {
              const v = e.target.value;
              onDestinationFilterChange(v || null);
            }}
          >
            <option value="">全部目标</option>
            {destHosts.map((host) => (
              <option key={host} value={host}>
                {host}
              </option>
            ))}
          </select>
        )}
      </div>

      {/* ═══ 概览视图 ═══ */}
      {view_mode === 'overview' && (
        <div className={`grid gap-4 ${compact ? 'grid-cols-1' : 'grid-cols-1 md:grid-cols-2'}`}>
          {/* 严重度分布 */}
          <SeverityGrid
            dist={sevDist}
            selected_severity={severity_filter}
            onSelect={onSeverityFilterChange}
          />

          {/* 类型分布 */}
          <ExfilTypeDistribution
            distribution={typeDist}
            selected_type={type_filter}
            onTypeSelect={onTypeFilterChange}
          />

          {/* 目标端点 Top */}
          <div className={compact ? '' : 'md:col-span-2'}>
            <DestinationChart
              destinations={destStats}
              selected_host={destination_filter}
              onHostSelect={onDestinationFilterChange}
            />
          </div>

          {/* Agent 摘要卡片 */}
          {agentStats.length > 0 && (
            <div className={compact ? '' : 'md:col-span-2'}>
              <h4 className="mb-2 text-xs font-semibold tracking-wide text-gray-500 uppercase">
                Agent 外泄摘要
              </h4>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
                {agentStats.slice(0, compact ? 3 : 6).map((stats) => (
                  <AgentExfilSummary
                    key={stats.agent_root_pid}
                    stats={stats}
                    highlighted={stats.agent_root_pid === agent_filter}
                    onClick={() =>
                      onAgentFilterChange(
                        agent_filter === stats.agent_root_pid ? null : stats.agent_root_pid,
                      )
                    }
                  />
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* ═══ 告警列表视图 ═══ */}
      {view_mode === 'alerts' && (
        <div className={`grid gap-4 ${compact ? 'grid-cols-1' : 'grid-cols-1 lg:grid-cols-3'}`}>
          {/* 告警卡片列表 */}
          <div
            className={`max-h-[600px] space-y-2 overflow-y-auto ${compact ? '' : 'lg:col-span-2'}`}
          >
            {filteredAlerts.length === 0 ? (
              <div className="p-4 text-sm text-gray-400">当前过滤条件下无匹配告警</div>
            ) : (
              filteredAlerts.map((alert) => (
                <ExfilAlertCard
                  key={alert.alert_id}
                  alert={alert}
                  selected={alert.alert_id === selected_alert_id}
                  onSelect={() => onAlertSelect(alert)}
                />
              ))
            )}
          </div>

          {/* 详情面板 */}
          {!compact && selectedAlert && (
            <div className="max-h-[600px] overflow-y-auto">
              <ExfilAlertDetail alert={selectedAlert} onAgentClick={onNavigateToAgent} />
            </div>
          )}
          {!compact && !selectedAlert && filteredAlerts.length > 0 && (
            <div className="self-center p-4 text-center text-sm text-gray-400">
              点击左侧告警卡片查看详情
            </div>
          )}
        </div>
      )}

      {/* ═══ 目标分析视图 ═══ */}
      {view_mode === 'destinations' && (
        <div className="space-y-4">
          <DestinationChart
            destinations={
              destination_filter
                ? destStats.filter((d) => d.host === destination_filter)
                : destStats
            }
            selected_host={destination_filter}
            onHostSelect={onDestinationFilterChange}
          />

          {/* 选中目标下的告警列表 */}
          {destination_filter && (
            <div>
              <h4 className="mb-2 text-xs font-semibold tracking-wide text-gray-500 uppercase">
                目标 {destination_filter} 的告警详情
              </h4>
              <div className="max-h-[400px] space-y-2 overflow-y-auto">
                {alerts
                  .filter((a) => a.destination.host === destination_filter)
                  .map((alert) => (
                    <ExfilAlertCard
                      key={alert.alert_id}
                      alert={alert}
                      selected={alert.alert_id === selected_alert_id}
                      onSelect={() => onAlertSelect(alert)}
                    />
                  ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* ═══ 时间线视图 ═══ */}
      {view_mode === 'timeline' && (
        <ExfilTimeline
          alerts={filteredAlerts}
          selected_id={selected_alert_id}
          onSelect={onAlertSelect}
        />
      )}

      {/* ═══ 底部状态栏 ═══ */}
      <div className="flex items-center justify-between border-t border-slate-100 pt-2 text-xs text-gray-400">
        <span>
          共 {alerts.length} 条告警 · {agentPids.length} 个 Agent · {destHosts.length} 个目标
        </span>
        <span>
          {filteredAlerts.length !== alerts.length && `过滤后 ${filteredAlerts.length} 条 · `}
          总计 {formatBytes(alerts.reduce((sum, a) => sum + a.total_exported_bytes, 0))}
        </span>
      </div>
    </div>
  );
};
