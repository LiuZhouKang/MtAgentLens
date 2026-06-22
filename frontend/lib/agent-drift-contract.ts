// Agent Drift Dashboard — 契约定义
//
// AgentDriftAnalyzer 检测 LLM agent 行为随时间的渐进漂移（gradual drift），
// 与 MultiAgentAnomalyAnalyzer 的突发异常检测互补。
//
// 六维漂移检测：
//   ToolUsage              — 工具使用频率分布偏移（KL 散度）
//   Latency                — 请求-响应时延偏移
//   ErrorRate              — HTTP 4xx/5xx 错误率偏移
//   OutputSize             — 响应体积偏移
//   EventFrequency         — 事件频率偏移
//   HttpMethodDistribution — HTTP 方法分布偏移
//
// 联动 key:
//   agent_root_pid — 连接 ProcessTree（定位进程）
//   tool_name      — 连接 ToolExecutionDashboard（查看具体工具调用变化）
//   window_start_ms / window_end_ms — 连接 TimelinePanel（定位漂移发生时间段）

import type { AnomalySeverity } from "./data-contract";

// ═══════════════════════════════════════════════════════════════════════════════
// 漂移维度
// ═══════════════════════════════════════════════════════════════════════════════

/** AgentDriftAnalyzer 的六个检测维度 */
export type DriftDimension =
  | "ToolUsage"               // 工具使用频率分布偏移
  | "Latency"                 // 请求-响应时延偏移
  | "ErrorRate"               // HTTP 错误率偏移
  | "OutputSize"              // 响应体积偏移
  | "EventFrequency"          // 事件频率偏移
  | "HttpMethodDistribution"; // HTTP 方法分布偏移

/** 维度人类可读标签 */
export const DRIFT_DIMENSION_LABEL: Record<DriftDimension, string> = {
  ToolUsage: "工具偏好",
  Latency: "响应延迟",
  ErrorRate: "错误率",
  OutputSize: "输出体积",
  EventFrequency: "事件频率",
  HttpMethodDistribution: "HTTP 方法",
};

/** 维度简短描述（用于 tooltip） */
export const DRIFT_DIMENSION_DESC: Record<DriftDimension, string> = {
  ToolUsage: "工具使用频率分布与基线的 KL 散度",
  Latency: "请求-响应延迟均值相对基线的偏移",
  ErrorRate: "HTTP 4xx/5xx 错误率相对基线的变化",
  OutputSize: "响应体积均值相对基线的偏移",
  EventFrequency: "事件产出速率相对基线的变化",
  HttpMethodDistribution: "HTTP 方法分布与基线的 KL 散度",
};

/** 维度对应图标 */
export const DRIFT_DIMENSION_ICON: Record<DriftDimension, string> = {
  ToolUsage: "🔧",
  Latency: "⏱️",
  ErrorRate: "❌",
  OutputSize: "📦",
  EventFrequency: "📊",
  HttpMethodDistribution: "🌐",
};

/** 维度在综合评分中的默认权重 */
export const DRIFT_DIMENSION_WEIGHT: Record<DriftDimension, number> = {
  ToolUsage: 0.30,
  Latency: 0.20,
  ErrorRate: 0.25,
  OutputSize: 0.10,
  EventFrequency: 0.10,
  HttpMethodDistribution: 0.05,
};

// ═══════════════════════════════════════════════════════════════════════════════
// 漂移方向
// ═══════════════════════════════════════════════════════════════════════════════

/** 漂移趋势方向 */
export type DriftDirection =
  | "Worsening"   // 持续恶化（连续 ≥3 周期递增）
  | "Plateaued"   // 漂移后趋于稳定
  | "Recovering"  // 正在恢复
  | "Uncertain";  // 方向不明（基线学习期或无足够周期）

export const DRIFT_DIRECTION_LABEL: Record<DriftDirection, string> = {
  Worsening: "持续恶化",
  Plateaued: "趋于稳定",
  Recovering: "正在恢复",
  Uncertain: "方向不明",
};

export const DRIFT_DIRECTION_ICON: Record<DriftDirection, string> = {
  Worsening: "📈",
  Plateaued: "➡️",
  Recovering: "📉",
  Uncertain: "❓",
};

/** 方向对应的颜色类 */
export const DRIFT_DIRECTION_COLOR: Record<DriftDirection, string> = {
  Worsening: "text-red-600 bg-red-50 border-red-200",
  Plateaued: "text-amber-600 bg-amber-50 border-amber-200",
  Recovering: "text-emerald-600 bg-emerald-50 border-emerald-200",
  Uncertain: "text-slate-500 bg-slate-50 border-slate-200",
};

// ═══════════════════════════════════════════════════════════════════════════════
// 漂移事件数据（对应 Rust 侧 DriftScore + agent_drift event）
// ═══════════════════════════════════════════════════════════════════════════════

/** 单次漂移检测快照 */
export interface DriftSnapshot {
  /** 快照唯一标识 */
  snapshot_id: string;
  /** 关联的 agent root_pid */
  agent_root_pid: number;
  /** 进程名（comm），用于显示 */
  agent_comm: string;
  /** 综合漂移评分 0.0–1.0 */
  overall_score: number;
  /** 各维度独立评分 */
  dimensions: Partial<Record<DriftDimension, number>>;
  /** 漂移趋势值（正=恶化，负=恢复） */
  trend: number;
  /** 漂移方向 */
  direction: DriftDirection;
  /** 检测窗口起始 (epoch ms) */
  window_start_ms: number;
  /** 检测窗口结束 (epoch ms) */
  window_end_ms: number;
  /** 事件时间戳 (ns) */
  timestamp_ns: number;
  /** 连续漂移周期数（用于方向判定） */
  consecutive_periods: number;
}

// ═══════════════════════════════════════════════════════════════════════════════
// 每-agent 聚合状态
// ═══════════════════════════════════════════════════════════════════════════════

/** 单个 agent 的漂移历史摘要 */
export interface AgentDriftSummary {
  /** agent root_pid */
  agent_root_pid: number;
  /** 进程名 */
  agent_comm: string;
  /** 最近一次综合评分 */
  latest_overall_score: number;
  /** 最近一次漂移方向 */
  latest_direction: DriftDirection;
  /** 连续漂移周期数 */
  consecutive_drift_periods: number;
  /** 各维度最近评分 */
  latest_dimensions: Partial<Record<DriftDimension, number>>;
  /** 历史快照数 */
  snapshot_count: number;
  /** 超过阈值的快照数 */
  threshold_breach_count: number;
  /** 首次漂移时间 (epoch ms) */
  first_drift_ms: number | null;
  /** 最近漂移时间 (epoch ms) */
  last_drift_ms: number | null;
  /** 基线是否已建立 */
  baseline_established: boolean;
  /** 基线事件数 */
  baseline_event_count: number;
  /** 当前环形缓冲区事件数 */
  buffer_event_count: number;
}

// ═══════════════════════════════════════════════════════════════════════════════
// 全局统计聚合
// ═══════════════════════════════════════════════════════════════════════════════

/** 全局漂移统计快照 */
export interface AgentDriftStats {
  /** 活跃监控的 agent 数 */
  monitored_agents: number;
  /** 已建立基线的 agent 数 */
  baselined_agents: number;
  /** 累计产生的漂移快照数 */
  total_snapshots: number;
  /** 超过阈值的快照数 */
  threshold_breach_total: number;
  /** 当前处于恶化状态的 agent 数 */
  worsening_agents: number;
  /** 当前处于恢复状态的 agent 数 */
  recovering_agents: number;
  /** 各维度全局平均评分 */
  dimension_avg: Partial<Record<DriftDimension, number>>;
  /** 综合评分最高的 agent Top-N */
  top_drifting_agents: Array<{
    agent_root_pid: number;
    agent_comm: string;
    overall_score: number;
    direction: DriftDirection;
  }>;
  /** 最近漂移事件时间 (epoch ms) */
  last_drift_ms: number | null;
  /** 漂移告警率 (threshold_breach / total_snapshots) */
  alert_rate: number;
}

// ═══════════════════════════════════════════════════════════════════════════════
// 评分到严重性的映射
// ═══════════════════════════════════════════════════════════════════════════════

/** 漂移评分到严重性的映射 */
export type DriftSeverity = "normal" | "elevated" | "warning" | "critical";

/** 评分阈值 */
export const DRIFT_SEVERITY_THRESHOLDS: Array<{
  min: number;
  severity: DriftSeverity;
  label: string;
}> = [
  { min: 0.0, severity: "normal", label: "正常" },
  { min: 0.15, severity: "elevated", label: "轻微" },
  { min: 0.25, severity: "warning", label: "告警" },
  { min: 0.50, severity: "critical", label: "严重" },
];

export const DRIFT_SEVERITY_COLOR: Record<DriftSeverity, string> = {
  normal: "bg-emerald-100 text-emerald-700 border-emerald-200",
  elevated: "bg-amber-100 text-amber-700 border-amber-200",
  warning: "bg-orange-100 text-orange-700 border-orange-200",
  critical: "bg-red-100 text-red-700 border-red-200",
};

export const DRIFT_SEVERITY_DOT: Record<DriftSeverity, string> = {
  normal: "bg-emerald-400",
  elevated: "bg-amber-400",
  warning: "bg-orange-500 animate-pulse",
  critical: "bg-red-600 animate-pulse",
};

/** 根据评分计算严重性 */
export function driftScoreToSeverity(score: number): DriftSeverity {
  for (let i = DRIFT_SEVERITY_THRESHOLDS.length - 1; i >= 0; i--) {
    if (score >= DRIFT_SEVERITY_THRESHOLDS[i].min) {
      return DRIFT_SEVERITY_THRESHOLDS[i].severity;
    }
  }
  return "normal";
}

// ═══════════════════════════════════════════════════════════════════════════════
// 仪表板视图模式
// ═══════════════════════════════════════════════════════════════════════════════

export type DriftDashboardViewMode =
  | "overview"       // 概览：全局统计 + agent 列表
  | "dimensions"     // 维度雷达：6 维对比视图
  | "agents"         // agent 级：按 agent 查看漂移历史
  | "trend";         // 趋势：时间线漂移曲线

// ═══════════════════════════════════════════════════════════════════════════════
// 仪表板完整状态
// ═══════════════════════════════════════════════════════════════════════════════

export interface AgentDriftDashboardState {
  /** 当前视图模式 */
  view_mode: DriftDashboardViewMode;
  /** 全局统计 */
  stats: AgentDriftStats;
  /** 各 agent 摘要列表 */
  agent_summaries: AgentDriftSummary[];
  /** 近期漂移快照（最近 N 条） */
  recent_snapshots: DriftSnapshot[];
  /** 当前选中的 agent_pid（null = 查看全局） */
  selected_agent_pid: number | null;
  /** 当前选中的快照 ID */
  selected_snapshot_id: string | null;
  /** 当前选中的维度（趋势视图下聚焦） */
  focused_dimension: DriftDimension | null;
  /** agent PID 筛选（null = 显示全部） */
  agent_filter: number | null;
  /** 最低评分筛选 (0.0–1.0) */
  min_score_filter: number;
  /** 方向筛选 */
  direction_filter: DriftDirection | null;
  /** 上次更新时间 (epoch ms) */
  last_update_ms: number;
  /** 当前选中的 agent 历史快照（agent 详情视图用） */
  selected_agent_history: DriftSnapshot[];
}

// ═══════════════════════════════════════════════════════════════════════════════
// Props 定义
// ═══════════════════════════════════════════════════════════════════════════════

export interface AgentDriftDashboardProps {
  state: AgentDriftDashboardState;
  onViewModeChange: (mode: DriftDashboardViewMode) => void;
  onAgentSelect: (agent_pid: number | null) => void;
  onSnapshotSelect: (snapshot_id: string | null) => void;
  onDimensionFocus: (dimension: DriftDimension | null) => void;
  onAgentFilterChange: (agent_pid: number | null) => void;
  onMinScoreChange: (score: number) => void;
  onDirectionFilterChange: (direction: DriftDirection | null) => void;
  /** 联动画布定位时间窗口 */
  onNavigateToTimerange: (start_ms: number, end_ms: number) => void;
  /** 联动跳转进程树 */
  onNavigateToProcessTree: (agent_root_pid: number) => void;
  /** 联动跳转工具调用 */
  onNavigateToToolExecution: (tool_name: string) => void;
  /** 紧凑模式（嵌入集成仪表板时） */
  compact?: boolean;
}

/** 漂移维度条 (单维评分展示) */
export interface DriftDimensionBarProps {
  dimension: DriftDimension;
  score: number;
  weight?: number;
  compact?: boolean;
}

/** 维度雷达图 (六维对比) */
export interface DriftRadarChartProps {
  dimensions: Partial<Record<DriftDimension, number>>;
  baseline?: Partial<Record<DriftDimension, number>>;
  /** 其他 agent 的覆盖层（对比用） */
  overlays?: Array<{
    agent_comm: string;
    dimensions: Partial<Record<DriftDimension, number>>;
    color: string;
  }>;
  size?: number;
}

/** Agent 漂移卡片 */
export interface AgentDriftCardProps {
  summary: AgentDriftSummary;
  isSelected: boolean;
  onSelect: () => void;
  onNavigateToProcessTree: (agent_root_pid: number) => void;
  compact?: boolean;
}

/** 漂移快照卡片 */
export interface DriftSnapshotCardProps {
  snapshot: DriftSnapshot;
  isSelected: boolean;
  onSelect: () => void;
  onNavigateToTimerange: (start_ms: number, end_ms: number) => void;
  compact?: boolean;
}

/** 漂移趋势线 */
export interface DriftTrendChartProps {
  snapshots: DriftSnapshot[];
  focused_dimension: DriftDimension | null;
  drift_threshold: number;
  /** 标注其他 agent 对比 */
  comparison_snapshots?: Array<{
    agent_comm: string;
    snapshots: DriftSnapshot[];
    color: string;
  }>;
  height?: number;
}

/** 快照详情面板 */
export interface DriftSnapshotDetailProps {
  snapshot: DriftSnapshot;
  onClose: () => void;
  onNavigateToTimerange: (start_ms: number, end_ms: number) => void;
  onNavigateToProcessTree: (agent_root_pid: number) => void;
}

// ═══════════════════════════════════════════════════════════════════════════════
// 工厂函数
// ═══════════════════════════════════════════════════════════════════════════════

/** 创建默认仪表板状态 */
export function createDefaultDriftState(): AgentDriftDashboardState {
  return {
    view_mode: "overview",
    stats: {
      monitored_agents: 0,
      baselined_agents: 0,
      total_snapshots: 0,
      threshold_breach_total: 0,
      worsening_agents: 0,
      recovering_agents: 0,
      dimension_avg: {},
      top_drifting_agents: [],
      last_drift_ms: null,
      alert_rate: 0,
    },
    agent_summaries: [],
    recent_snapshots: [],
    selected_agent_pid: null,
    selected_snapshot_id: null,
    focused_dimension: null,
    agent_filter: null,
    min_score_filter: 0,
    direction_filter: null,
    last_update_ms: 0,
    selected_agent_history: [],
  };
}
