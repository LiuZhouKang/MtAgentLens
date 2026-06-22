// SessionHealthMonitor Dashboard — 可视化契约
// 对应 collector/src/framework/analyzers/session_health_monitor 的输出事件格式
//
// 两种输出事件 source:
//   "session_health"       — 定期健康快照（默认每 10s 一次）
//   "session_health_alert" — 健康分数跨越阈值时触发的告警
//
// 五个评分维度:
//   latency       — 请求延迟（p50/p95/p99）
//   error_rate    — 错误率（工具失败 + 5xx + 进程崩溃）
//   tool_health   — 工具健康度（成功率 + 活跃循环惩罚）
//   api_health    — API 健康度（5xx/429 比例）
//   resource      — 资源健康度（进程崩溃次数）
//
// 跨分析器联动 key:
//   agent_root_pid — 连接到 ToolExecutionDashboard / AgentLoopDashboard / 所有其他仪表板
//   session_id     — 连接到 SessionBoundaryDashboard

import type { AnomalySeverity } from "./data-contract";

// ═══════════════════════════════════════════════════════════════════════════
// 维度定义
// ═══════════════════════════════════════════════════════════════════════════

/** 五个健康维度的标识 */
export type HealthDimension =
  | "latency"
  | "error_rate"
  | "tool_health"
  | "api_health"
  | "resource";

export const HEALTH_DIMENSION_LABEL: Record<HealthDimension, string> = {
  latency: "延迟",
  error_rate: "错误率",
  tool_health: "工具健康",
  api_health: "API 健康",
  resource: "资源",
};

export const HEALTH_DIMENSION_ICON: Record<HealthDimension, string> = {
  latency: "⏱️",
  error_rate: "❌",
  tool_health: "🔧",
  api_health: "🌐",
  resource: "💻",
};

export const HEALTH_DIMENSION_DESC: Record<HealthDimension, string> = {
  latency: "LLM API 请求-响应延迟分布，p95 越低越好",
  error_rate: "工具失败、5xx 错误、进程崩溃的综合错误率",
  tool_health: "工具调用成功率，活跃循环会显著扣分",
  api_health: "LLM API 返回 5xx/429 状态码的比例",
  resource: "Agent 进程崩溃次数，每次崩溃扣 30% 分数",
};

/** 默认维度权重（与 Rust SessionHealthMonitorConfig 默认值对齐） */
export const DEFAULT_HEALTH_WEIGHTS: Record<HealthDimension, number> = {
  latency: 0.25,
  error_rate: 0.30,
  tool_health: 0.20,
  api_health: 0.15,
  resource: 0.10,
};

// ═══════════════════════════════════════════════════════════════════════════
// 告警类型与严重程度
// ═══════════════════════════════════════════════════════════════════════════

/** 健康告警类型（与 Rust alert_type 对齐） */
export type HealthAlertType =
  | "health_degraded"   // 分数下降超过 degradation_min_drop
  | "health_warning"    // 分数低于 warning_threshold (60)
  | "health_critical";  // 分数低于 critical_threshold (30)

export const HEALTH_ALERT_TYPE_LABEL: Record<HealthAlertType, string> = {
  health_degraded: "健康下降",
  health_warning: "健康警告",
  health_critical: "健康严重",
};

export const HEALTH_ALERT_TYPE_ICON: Record<HealthAlertType, string> = {
  health_degraded: "📉",
  health_warning: "⚠️",
  health_critical: "🚨",
};

/** 健康告警严重程度（与 Rust severity 对齐） */
export type HealthAlertSeverity = "warning" | "critical";

export function healthSeverityToAnomalySeverity(s: HealthAlertSeverity): AnomalySeverity {
  return s === "critical" ? "critical" : "warning";
}

// ═══════════════════════════════════════════════════════════════════════════
// 核心数据结构 — 维度评分
// ═══════════════════════════════════════════════════════════════════════════

/** 延迟维度的详细数据 */
export interface LatencyDimensionData {
  /** 维度分数 (0.0–1.0) */
  score: number;
  /** 配置权重 */
  weight: number;
  /** P50 延迟 (微秒) */
  p50_us: number;
  /** P95 延迟 (微秒) */
  p95_us: number;
  /** P99 延迟 (微秒) */
  p99_us: number;
  /** 采样数量 */
  samples: number;
}

/** 错误率维度的详细数据 */
export interface ErrorRateDimensionData {
  score: number;
  weight: number;
  /** 窗口内总操作数 */
  total_operations: number;
  /** 失败操作数 */
  failed_operations: number;
  /** 错误率 (0.0–1.0) */
  error_rate: number;
  /** 最近 5xx 错误数 */
  recent_5xx_count: number;
  /** 最近 429 错误数 */
  recent_429_count: number;
}

/** 工具健康维度的详细数据 */
export interface ToolHealthDimensionData {
  score: number;
  weight: number;
  /** 窗口内工具调用总数 */
  total_calls: number;
  /** 成功次数 */
  successful: number;
  /** 失败次数 */
  failed: number;
  /** 超时次数 */
  timed_out: number;
  /** 当前活跃循环数 */
  active_loops: number;
}

/** API 健康维度的详细数据 */
export interface ApiHealthDimensionData {
  score: number;
  weight: number;
  /** 窗口内 API 请求总数 */
  total_requests: number;
  /** 5xx 状态码次数 */
  status_5xx: number;
  /** 429 状态码次数 */
  status_429: number;
  /** 平均响应体大小 (bytes) */
  avg_response_bytes: number;
}

/** 资源健康维度的详细数据 */
export interface ResourceHealthDimensionData {
  score: number;
  weight: number;
  /** 窗口内进程崩溃次数 */
  process_crashes: number;
  /** 当前内存使用 (MB) */
  memory_mb: number | null;
  /** 当前 CPU 使用率 (%) */
  cpu_percent: number | null;
}

/** 所有维度的联合类型 */
export type DimensionData =
  | { dimension: "latency"; data: LatencyDimensionData }
  | { dimension: "error_rate"; data: ErrorRateDimensionData }
  | { dimension: "tool_health"; data: ToolHealthDimensionData }
  | { dimension: "api_health"; data: ApiHealthDimensionData }
  | { dimension: "resource"; data: ResourceHealthDimensionData };

/** 五维度评分总览 */
export interface HealthDimensions {
  latency: LatencyDimensionData;
  error_rate: ErrorRateDimensionData;
  tool_health: ToolHealthDimensionData;
  api_health: ApiHealthDimensionData;
  resource: ResourceHealthDimensionData;
}

// ═══════════════════════════════════════════════════════════════════════════
// 核心数据结构 — 事件
// ═══════════════════════════════════════════════════════════════════════════

/**
 * SessionHealth 定期快照（source: "session_health"）
 * 与 Rust SessionHealthMonitor 的 session_health 输出对齐
 */
export interface SessionHealthSnapshot {
  /** 会话唯一标识 */
  session_id: string;
  /** Agent root PID */
  agent_root_pid: number;
  /** 进程名 */
  comm: string;
  /** 综合健康分数 (0–100) */
  health_score: number;
  /** 快照观测窗口时长 (ms) */
  window_duration_ms: number;
  /** 窗口内观测到的事件总数 */
  events_observed: number;
  /** 五维度详细评分 */
  dimensions: HealthDimensions;
  /** 当前活跃的子告警类型列表 */
  active_alerts: string[];
  /** 快照时间戳 (epoch ns) */
  timestamp_ns: number;
}

/**
 * SessionHealth 告警（source: "session_health_alert"）
 * 与 Rust SessionHealthMonitor 的 session_health_alert 输出对齐
 */
export interface SessionHealthAlert {
  /** 会话唯一标识 */
  session_id: string;
  /** Agent root PID */
  agent_root_pid: number;
  /** 进程名 */
  comm: string;
  /** 告警类型 */
  alert_type: HealthAlertType;
  /** 严重程度 */
  severity: HealthAlertSeverity;
  /** 触发告警时的健康分数 */
  health_score: number;
  /** 上一次快照的健康分数（degraded 告警时有效） */
  previous_score: number | null;
  /** 分数下降最大的维度列表（degraded 告警时有效） */
  degraded_dimensions: HealthDimension[];
  /** 人类可读告警消息 */
  message: string;
  /** 告警时间戳 (epoch ns) */
  timestamp_ns: number;
}

// ═══════════════════════════════════════════════════════════════════════════
// 聚合统计 — Fleet 与 Agent 级别
// ═══════════════════════════════════════════════════════════════════════════

/** 单 Agent 会话的健康摘要 */
export interface AgentHealthSummary {
  /** Agent root PID */
  agent_root_pid: number;
  /** 进程名 */
  comm: string;
  /** 会话 ID */
  session_id: string;
  /** 最近健康分数 */
  current_score: number;
  /** 最高健康分数（窗口内） */
  max_score: number;
  /** 最低健康分数（窗口内） */
  min_score: number;
  /** 平均健康分数（窗口内） */
  avg_score: number;
  /** 健康趋势方向 (正数 = 改善, 负数 = 恶化) */
  score_trend: number;
  /** 分数波动标准差 */
  score_stddev: number;
  /** 各维度当前分数 */
  dimension_scores: Record<HealthDimension, number>;
  /** 维度中最低分的那个 */
  weakest_dimension: HealthDimension;
  /** 活跃告警数 */
  active_alert_count: number;
  /** 历史告警总数 */
  total_alert_count: number;
  /** 首次观测时间 (epoch ns) */
  first_seen_ns: number;
  /** 最后活跃时间 (epoch ns) */
  last_active_ns: number;
  /** 是否在线 */
  is_active: boolean;
  /** 最近 N 次快照（用于趋势图） */
  recent_snapshots: SessionHealthSnapshot[];
}

/** Fleet 级别全景快照 */
export interface FleetHealthOverview {
  /** 活跃会话总数 */
  active_session_count: number;
  /** 总会话数（含已断开的） */
  total_session_count: number;
  /** Fleet 平均健康分数 */
  fleet_avg_score: number;
  /** 健康分数分布 */
  score_distribution: {
    /** 优秀：> 80 */
    excellent: number;
    /** 良好：60–80 */
    good: number;
    /** 警告：30–60 */
    warning: number;
    /** 严重：< 30 */
    critical: number;
  };
  /** 需要关注的 Agent 列表（分数 < 60） */
  attention_needed: AgentHealthSummary[];
  /** 按维度聚合的 Fleet 平均分数 */
  fleet_dimension_scores: Record<HealthDimension, number>;
  /** 当前活跃告警总数 */
  total_active_alerts: number;
  /** 按告警类型的分布 */
  alerts_by_type: Record<HealthAlertType, number>;
}

/** 健康分数历史数据点（用于趋势图） */
export interface HealthScoreDataPoint {
  /** 时间戳 (epoch ns) */
  timestamp_ns: number;
  /** 综合健康分数 */
  health_score: number;
  /** 各维度分数 */
  dimension_scores: Record<HealthDimension, number>;
}

/** 单个会话的完整健康档案 */
export interface SessionHealthProfile {
  session_id: string;
  agent_root_pid: number;
  comm: string;
  /** 摘要 */
  summary: AgentHealthSummary;
  /** 快照历史（完整维度数据，用于图表渲染） */
  snapshot_history: SessionHealthSnapshot[];
  /** 告警历史 */
  alert_history: SessionHealthAlert[];
}

// ═══════════════════════════════════════════════════════════════════════════
// 仪表板 UI 状态
// ═══════════════════════════════════════════════════════════════════════════

export type SessionHealthViewMode =
  | "overview"       // Fleet 概况
  | "fleet"          // Fleet 网格视图
  | "session_detail" // 单个会话的深入分析
  | "alerts"         // 告警列表
  | "trends";        // 趋势图

export interface SessionHealthDashboardState {
  /** 最近 N 次健康快照（所有会话的最近快照合并） */
  recent_snapshots: SessionHealthSnapshot[];
  /** Fleet 级别全景 */
  fleet_overview: FleetHealthOverview | null;
  /** 按 agent_root_pid 的会话摘要 Map */
  agent_summaries: Map<number, AgentHealthSummary>;
  /** 历史告警列表 */
  alerts: SessionHealthAlert[];
  /** 当前视图模式 */
  view_mode: SessionHealthViewMode;
  /** 选中的告警 ID */
  selected_alert_id: string | null;
  /** 选中的会话 key (session_id) */
  selected_session_id: string | null;
  /** 选中的 agent PID */
  selected_agent_pid: number | null;
  /** 所选会话的完整档案（用于 session_detail 视图） */
  selected_profile: SessionHealthProfile | null;
  /** 告警类型过滤器 */
  alert_type_filter: HealthAlertType | null;
  /** 严重程度过滤器 */
  severity_filter: HealthAlertSeverity | null;
  /** agent PID 过滤器 */
  agent_filter: number | null;
  /** 最低健康分数过滤器（仅显示分数 < 此值的会话） */
  max_health_score_filter: number | null;
  /** 维度权重配置（用于加权分数计算） */
  weights: Record<HealthDimension, number>;
  /** 最后更新时间 (epoch ms) */
  last_update_ms: number;
}

// ═══════════════════════════════════════════════════════════════════════════
// 组件 Props
// ═══════════════════════════════════════════════════════════════════════════

export interface SessionHealthDashboardProps {
  /** 仪表板状态 */
  state: SessionHealthDashboardState;
  /** 切换视图模式 */
  onViewModeChange: (mode: SessionHealthViewMode) => void;
  /** 选择告警 */
  onAlertSelect: (alert: SessionHealthAlert | null) => void;
  /** 选择会话（跳转到 session_detail 视图） */
  onSessionSelect: (session_id: string) => void;
  /** 选择 Agent（跳转到 Agent 的 session_detail） */
  onAgentSelect: (pid: number | null) => void;
  /** 告警类型过滤 */
  onAlertTypeFilterChange: (type: HealthAlertType | null) => void;
  /** 严重程度过滤 */
  onSeverityFilterChange: (severity: HealthAlertSeverity | null) => void;
  /** Agent PID 过滤 */
  onAgentFilterChange: (pid: number | null) => void;
  /** 健康分数上限过滤 */
  onMaxHealthScoreFilterChange: (score: number | null) => void;
  /** 跨视图导航：跳转到 ToolExecution（查看具体工具调用） */
  onNavigateToToolExecution: (pid: number) => void;
  /** 跨视图导航：跳转到 AgentLoop（查看活跃循环） */
  onNavigateToAgentLoop: (pid: number) => void;
  /** 跨视图导航：跳转到 TokenCost（查看 token 消耗） */
  onNavigateToTokenCost: (pid: number) => void;
  /** 是否紧凑模式 */
  compact?: boolean;
}

export interface FleetGridProps {
  /** 所有 Agent 健康摘要 */
  agents: AgentHealthSummary[];
  /** 选中的 agent PID */
  selected_pid: number | null;
  /** 选中 Agent */
  onAgentClick: (pid: number) => void;
  /** 当前 fleet 平均分数 */
  fleet_avg_score: number;
  compact?: boolean;
}

export interface HealthScoreGaugeProps {
  /** 健康分数 (0–100) */
  score: number;
  /** 之前的分数（用于显示变化） */
  previous_score?: number;
  /** 显示大小 */
  size?: "sm" | "md" | "lg";
  /** 是否显示分数标签 */
  showLabel?: boolean;
}

export interface DimensionBarChartProps {
  /** 维度数据 */
  dimensions: HealthDimensions;
  /** 权重配置 */
  weights: Record<HealthDimension, number>;
  /** 高亮指定维度 */
  highlight_dimension?: HealthDimension;
  compact?: boolean;
}

export interface HealthTrendChartProps {
  /** 趋势数据点 */
  data: HealthScoreDataPoint[];
  /** Agent PID（用于标签） */
  agent_pid: number;
  /** 高亮指定维度（null = 综合分数） */
  highlight_dimension?: HealthDimension | null;
  compact?: boolean;
}

/** 从 SessionHealthSnapshot 提取各维度的标量分数 */
export function extractDimensionScores(snapshot: SessionHealthSnapshot): Record<HealthDimension, number> {
  return {
    latency: Math.round(snapshot.dimensions.latency.score * 100),
    error_rate: Math.round(snapshot.dimensions.error_rate.score * 100),
    tool_health: Math.round(snapshot.dimensions.tool_health.score * 100),
    api_health: Math.round(snapshot.dimensions.api_health.score * 100),
    resource: Math.round(snapshot.dimensions.resource.score * 100),
  };
}

/** 将 SessionHealthSnapshot[] 转为 HealthScoreDataPoint[]（用于图表组件） */
export function snapshotsToDataPoints(snapshots: SessionHealthSnapshot[]): HealthScoreDataPoint[] {
  return snapshots.map((s) => ({
    timestamp_ns: s.timestamp_ns,
    health_score: s.health_score,
    dimension_scores: extractDimensionScores(s),
  }));
}

export interface HealthAlertCardProps {
  alert: SessionHealthAlert;
  selected?: boolean;
  onSelect: () => void;
}

// ═══════════════════════════════════════════════════════════════════════════
// SSE 流集成
// ═══════════════════════════════════════════════════════════════════════════

export type SessionHealthStreamType = "session_health" | "session_health_alert";

export type ExtendedHealthStreamType =
  | import("./data-contract").UnifiedStreamEvent["stream_type"]
  | SessionHealthStreamType;

// ═══════════════════════════════════════════════════════════════════════════
// 工具函数
// ═══════════════════════════════════════════════════════════════════════════

/** 健康分数 → 颜色等级 */
export type HealthScoreTier = "excellent" | "good" | "warning" | "critical";

export function healthScoreToTier(score: number): HealthScoreTier {
  if (score > 80) return "excellent";
  if (score > 60) return "good";
  if (score > 30) return "warning";
  return "critical";
}

export const HEALTH_TIER_COLORS: Record<HealthScoreTier, { bg: string; text: string; ring: string; dot: string }> = {
  excellent: { bg: "bg-emerald-50", text: "text-emerald-700", ring: "ring-emerald-400", dot: "bg-emerald-500" },
  good: { bg: "bg-blue-50", text: "text-blue-700", ring: "ring-blue-400", dot: "bg-blue-500" },
  warning: { bg: "bg-amber-50", text: "text-amber-700", ring: "ring-amber-400", dot: "bg-amber-500" },
  critical: { bg: "bg-red-50", text: "text-red-700", ring: "ring-red-600", dot: "bg-red-600" },
};

export const HEALTH_TIER_LABEL: Record<HealthScoreTier, string> = {
  excellent: "优秀",
  good: "良好",
  warning: "警告",
  critical: "严重",
};

/** 计算每个维度的加权贡献值 (0–100) */
export function computeDimensionContribution(
  dimensions: HealthDimensions,
  weights: Record<HealthDimension, number>,
): Record<HealthDimension, number> {
  const total = Object.entries(weights).reduce((sum, [, w]) => sum + w, 0) || 1;
  return {
    latency: (dimensions.latency.score * (weights.latency / total)) * 100,
    error_rate: (dimensions.error_rate.score * (weights.error_rate / total)) * 100,
    tool_health: (dimensions.tool_health.score * (weights.tool_health / total)) * 100,
    api_health: (dimensions.api_health.score * (weights.api_health / total)) * 100,
    resource: (dimensions.resource.score * (weights.resource / total)) * 100,
  };
}

/** 从快照列表计算 AgentHealthSummary */
export function computeAgentHealthSummary(
  agent_root_pid: number,
  comm: string,
  session_id: string,
  snapshots: SessionHealthSnapshot[],
  alerts: SessionHealthAlert[],
): AgentHealthSummary {
  if (snapshots.length === 0) {
    return {
      agent_root_pid,
      comm,
      session_id,
      current_score: 100,
      max_score: 100,
      min_score: 100,
      avg_score: 100,
      score_trend: 0,
      score_stddev: 0,
      dimension_scores: { latency: 100, error_rate: 100, tool_health: 100, api_health: 100, resource: 100 },
      weakest_dimension: "latency",
      active_alert_count: 0,
      total_alert_count: 0,
      first_seen_ns: 0,
      last_active_ns: 0,
      is_active: false,
      recent_snapshots: [],
    };
  }

  const scores = snapshots.map((s) => s.health_score);
  const current = snapshots[snapshots.length - 1];
  const max = Math.max(...scores);
  const min = Math.min(...scores);
  const avg = scores.reduce((a, b) => a + b, 0) / scores.length;
  const mean = avg;
  const variance = scores.reduce((s, v) => s + (v - mean) ** 2, 0) / scores.length;

  // 趋势：取最近 3 个点的斜率
  const recent = snapshots.slice(-3);
  const trend =
    recent.length >= 2
      ? (recent[recent.length - 1].health_score - recent[0].health_score) / recent.length
      : 0;

  // 各维度分数（取最近快照）
  const dimension_scores: Record<HealthDimension, number> = {
    latency: Math.round(current.dimensions.latency.score * 100),
    error_rate: Math.round(current.dimensions.error_rate.score * 100),
    tool_health: Math.round(current.dimensions.tool_health.score * 100),
    api_health: Math.round(current.dimensions.api_health.score * 100),
    resource: Math.round(current.dimensions.resource.score * 100),
  };

  // 最弱维度
  let weakest: HealthDimension = "latency";
  let lowest = dimension_scores.latency;
  for (const [dim, score] of Object.entries(dimension_scores)) {
    if (score < lowest) {
      lowest = score;
      weakest = dim as HealthDimension;
    }
  }

  const session_alerts = alerts.filter((a) => a.session_id === session_id);
  const active_alerts = session_alerts.filter((a) => {
    const age = (current.timestamp_ns - a.timestamp_ns) / 1_000_000_000;
    return age < 60; // 60 秒内视为活跃
  });

  return {
    agent_root_pid,
    comm,
    session_id,
    current_score: current.health_score,
    max_score: max,
    min_score: min,
    avg_score: Math.round(avg),
    score_trend: Math.round(trend * 10) / 10,
    score_stddev: Math.round(Math.sqrt(variance) * 10) / 10,
    dimension_scores,
    weakest_dimension: weakest,
    active_alert_count: active_alerts.length,
    total_alert_count: session_alerts.length,
    first_seen_ns: snapshots[0].timestamp_ns,
    last_active_ns: current.timestamp_ns,
    is_active: (current.timestamp_ns - snapshots[snapshots.length - 1].timestamp_ns) / 1_000_000 < 120,
    recent_snapshots: snapshots.slice(-10),
  };
}

/** 从所有快照和告警计算 Fleet 全景 */
export function computeFleetOverview(
  agentSummaries: Map<number, AgentHealthSummary>,
  alerts: SessionHealthAlert[],
): FleetHealthOverview {
  const summaries = Array.from(agentSummaries.values());
  const active = summaries.filter((s) => s.is_active);

  const fleet_scores = active.map((s) => s.current_score);
  const fleet_avg = fleet_scores.length > 0
    ? Math.round(fleet_scores.reduce((a, b) => a + b, 0) / fleet_scores.length)
    : 100;

  const distribution = { excellent: 0, good: 0, warning: 0, critical: 0 };
  for (const score of fleet_scores) {
    const tier = healthScoreToTier(score);
    distribution[tier]++;
  }

  const attention_needed = active
    .filter((s) => s.current_score < 60)
    .sort((a, b) => a.current_score - b.current_score);

  // 按维度聚合 fleet 平均
  const fleet_dims: Record<HealthDimension, number> = {
    latency: 0, error_rate: 0, tool_health: 0, api_health: 0, resource: 0,
  };
  for (const s of active) {
    for (const dim of Object.keys(fleet_dims) as HealthDimension[]) {
      fleet_dims[dim] += s.dimension_scores[dim];
    }
  }
  for (const dim of Object.keys(fleet_dims) as HealthDimension[]) {
    fleet_dims[dim] = active.length > 0 ? Math.round(fleet_dims[dim] / active.length) : 100;
  }

  const active_alerts = alerts.filter((a) => {
    const now = Date.now() * 1_000_000;
    return (now - a.timestamp_ns) / 1_000_000_000 < 60;
  });
  const alerts_by_type: Record<HealthAlertType, number> = {
    health_degraded: 0,
    health_warning: 0,
    health_critical: 0,
  };
  for (const a of active_alerts) {
    alerts_by_type[a.alert_type]++;
  }

  return {
    active_session_count: active.length,
    total_session_count: summaries.length,
    fleet_avg_score: fleet_avg,
    score_distribution: distribution,
    attention_needed,
    fleet_dimension_scores: fleet_dims,
    total_active_alerts: active_alerts.length,
    alerts_by_type,
  };
}

/** 格式化延迟 (微秒 → 人类可读) */
export function formatLatencyUs(us: number): string {
  if (us < 1_000) return `${Math.round(us)}μs`;
  if (us < 1_000_000) return `${(us / 1_000).toFixed(1)}ms`;
  return `${(us / 1_000_000).toFixed(2)}s`;
}

/** 创建默认仪表板状态 */
export function createDefaultSessionHealthState(): SessionHealthDashboardState {
  return {
    recent_snapshots: [],
    fleet_overview: null,
    agent_summaries: new Map(),
    alerts: [],
    view_mode: "overview",
    selected_alert_id: null,
    selected_session_id: null,
    selected_agent_pid: null,
    selected_profile: null,
    alert_type_filter: null,
    severity_filter: null,
    agent_filter: null,
    max_health_score_filter: null,
    weights: { ...DEFAULT_HEALTH_WEIGHTS },
    last_update_ms: Date.now(),
  };
}
