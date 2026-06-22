// AgentLoopDetector Dashboard — 可视化契约
// 对应 collector/src/framework/analyzers/agent_loop_detector 的输出事件格式
//
// 两种输出事件 source:
//   "agent_loop"          — 检测到工具调用循环
//   "agent_loop_resolved" — 循环已解除
//
// 与 ToolExecutionTracker 的 tool_call 事件联动的 key:
//   tool_name — 关联到具体的工具调用
//   affected_pids — 关联到 Agent

import type { AnomalySeverity } from "./data-contract";

// ═══════════════════════════════════════════════════════════════════════════
// 循环类型（与 Rust LoopType 对齐）
// ═══════════════════════════════════════════════════════════════════════════

export type LoopType =
  | "ExactRepeat"   // 完全相同参数的重复调用
  | "FuzzyRepeat"   // 参数仅微小变化（编辑距离 < 阈值）
  | "PingPong"      // A→B→A→B 交替模式
  | "RetryStorm";   // 快速连续重试（间隔 < 500ms 且无中间修改）

// ═══════════════════════════════════════════════════════════════════════════
// 循环严重度（与 Rust LoopSeverity 对齐）
// ═══════════════════════════════════════════════════════════════════════════

export type LoopSeverity =
  | "Warning"    // 重复 3-5 次：需要关注但可能自愈
  | "Critical"   // 重复 6-10 次：明确循环，建议干预
  | "Emergency"; // 重复 > 10 次：紧急，agent 已失控

// ═══════════════════════════════════════════════════════════════════════════
// 循环解决方式
// ═══════════════════════════════════════════════════════════════════════════

export type LoopResolution =
  | "self_recovery"       // agent 自行打破循环
  | "user_intervention"   // 用户干预
  | "timeout"             // 超时终止
  | "new_tool_invoked"    // 调用了新工具打破循环
  | "unknown";

// ═══════════════════════════════════════════════════════════════════════════
// 样本载荷
// ═══════════════════════════════════════════════════════════════════════════

export interface LoopSamplePayload {
  /** 工具参数 */
  args: Record<string, unknown>;
  /** 该次调用的时间戳 (epoch ns) */
  timestamp_ns: number;
}

// ═══════════════════════════════════════════════════════════════════════════
// 循环上下文
// ═══════════════════════════════════════════════════════════════════════════

export interface LoopContext {
  /** 循环期间是否有其他工具调用打破连续 */
  interleaved_different_calls: number;
  /** 循环中有多少次调用返回了错误 */
  error_responses: number;
  /** 循环期间是否修改了文件（区分合理重试） */
  file_modifications: number;
}

// ═══════════════════════════════════════════════════════════════════════════
// 核心数据结构
// ═══════════════════════════════════════════════════════════════════════════

/** agent_loop 事件（source: "agent_loop"） */
export interface AgentLoopEvent {
  /** 循环唯一标识 (UUID v4) */
  loop_id: string;
  /** 循环类型 */
  loop_type: LoopType;
  /** 陷入循环的工具名称 */
  tool_name: string;
  /** 重复次数 */
  repeat_count: number;
  /** 检测窗口时长 (ms) */
  window_duration_ms: number;
  /** 严重度 */
  severity: LoopSeverity;
  /** 首次发生时间戳 (epoch ns) */
  first_occurrence_ns: number;
  /** 最近发生时间戳 (epoch ns) */
  last_occurrence_ns: number;
  /** 涉及的 Agent PID 列表 */
  affected_pids: number[];
  /** 采样载荷（用于前端展示循环示例） */
  sample_payloads: LoopSamplePayload[];
  /** 循环上下文（辅助判断是否为合理重试） */
  context: LoopContext;
}

/** agent_loop_resolved 事件（source: "agent_loop_resolved"） */
export interface AgentLoopResolvedEvent {
  /** 对应的原始循环 ID */
  loop_id: string;
  /** 解决方式 */
  resolution: LoopResolution;
  /** 总重复次数 */
  total_repeats: number;
  /** 循环持续时长 (ms) */
  duration_ms: number;
  /** 最终结果 */
  final_outcome: "task_completed" | "abandoned" | "error";
  /** 解决时间戳 (epoch ns) */
  resolved_at_ns: number;
}

// ═══════════════════════════════════════════════════════════════════════════
// 循环摘要（合并 agent_loop + agent_loop_resolved 的 UI 视图模型）
// ═══════════════════════════════════════════════════════════════════════════

export interface LoopSummary {
  loop_id: string;
  loop_type: LoopType;
  tool_name: string;
  repeat_count: number;
  duration_ms: number;
  severity: LoopSeverity;
  affected_pids: number[];
  /** 是否已解决 */
  resolved: boolean;
  /** 解决信息（仅 resolved = true 时有效） */
  resolution?: LoopResolution;
  final_outcome?: "task_completed" | "abandoned" | "error";
  first_occurrence_ns: number;
  last_occurrence_ns: number;
  resolved_at_ns?: number;
  /** 采样载荷 */
  sample_payloads: LoopSamplePayload[];
  /** 上下文 */
  context: LoopContext;
}

// ═══════════════════════════════════════════════════════════════════════════
// SSE 流集成 — 扩展 StreamEnvelope 的 stream_type
// ═══════════════════════════════════════════════════════════════════════════

export type AgentLoopStreamType =
  | "agent_loop"
  | "agent_loop_resolved";

/** 扩展后的完整 stream_type 集合 */
export type ExtendedLoopStreamType =
  | import("./data-contract").UnifiedStreamEvent["stream_type"]
  | AgentLoopStreamType;

// ═══════════════════════════════════════════════════════════════════════════
// 仪表板 UI 状态
// ═══════════════════════════════════════════════════════════════════════════

/** 仪表板视图模式 */
export type LoopDashboardViewMode = "overview" | "timeline" | "tools";

/** 仪表板全局状态 */
export interface AgentLoopDashboardState {
  /** 当前活跃（未解决）的循环列表 */
  active_loops: LoopSummary[];
  /** 已解决的循环列表（最多保留 100 条） */
  resolved_loops: LoopSummary[];
  /** 当前视图模式 */
  view_mode: LoopDashboardViewMode;
  /** 选中的循环 ID（联动 DetailPanel） */
  selected_loop_id: string | null;
  /** 按循环类型过滤 */
  type_filter: LoopType | null;
  /** 按严重度过滤 */
  severity_filter: LoopSeverity | null;
  /** 按工具名过滤 */
  tool_filter: string | null;
  /** 按 Agent PID 过滤 */
  pid_filter: number | null;
  /** 是否仅显示活跃循环 */
  show_active_only: boolean;
  /** 最后更新时间 (epoch ms) */
  last_update_ms: number;
}

// ═══════════════════════════════════════════════════════════════════════════
// 组件 Props
// ═══════════════════════════════════════════════════════════════════════════

export interface AgentLoopDashboardProps {
  /** 仪表板状态 */
  state: AgentLoopDashboardState;
  /** 切换视图模式 */
  onViewModeChange: (mode: LoopDashboardViewMode) => void;
  /** 选中循环（联动 DetailPanel） */
  onLoopSelect: (loop: LoopSummary | null) => void;
  /** 切换循环类型过滤 */
  onTypeFilterChange: (type: LoopType | null) => void;
  /** 切换严重度过滤 */
  onSeverityFilterChange: (severity: LoopSeverity | null) => void;
  /** 切换工具名过滤 */
  onToolFilterChange: (tool_name: string | null) => void;
  /** 切换 Agent PID 过滤 */
  onPidFilterChange: (pid: number | null) => void;
  /** 切换仅活跃模式 */
  onShowActiveOnlyChange: (active_only: boolean) => void;
  /** 是否紧凑模式 */
  compact?: boolean;
}

export interface LoopCardProps {
  loop: LoopSummary;
  selected?: boolean;
  onSelect: () => void;
}

export interface LoopDetailPanelProps {
  loop: LoopSummary;
  /** 点击工具名跳转到 ToolExecutionDashboard */
  onToolNameClick?: (tool_name: string) => void;
}

export interface LoopSeverityBadgeProps {
  severity: LoopSeverity;
  /** 是否显示文本标签 */
  showLabel?: boolean;
}

export interface LoopTypeIconProps {
  loop_type: LoopType;
  size?: "sm" | "md" | "lg";
}

export interface LoopToolFrequencyChartProps {
  /** tool_name → 循环次数 */
  frequency: Record<string, number>;
  selected_tool: string | null;
  onToolSelect: (tool_name: string | null) => void;
}

// ═══════════════════════════════════════════════════════════════════════════
// 工具函数
// ═══════════════════════════════════════════════════════════════════════════

/** 循环类型 → 人类可读标签 */
export const LOOP_TYPE_LABEL: Record<LoopType, string> = {
  ExactRepeat: "精确重复",
  FuzzyRepeat: "模糊重复",
  PingPong: "乒乓切换",
  RetryStorm: "重试风暴",
};

/** 循环类型 → 图标（emoji） */
export const LOOP_TYPE_ICON: Record<LoopType, string> = {
  ExactRepeat: "🔄",
  FuzzyRepeat: "🔀",
  PingPong: "🏓",
  RetryStorm: "⛈️",
};

/** 严重度 → 人类可读标签 */
export const SEVERITY_LABEL: Record<LoopSeverity, string> = {
  Warning: "警告",
  Critical: "严重",
  Emergency: "紧急",
};

/** 解决方式 → 人类可读标签 */
export const RESOLUTION_LABEL: Record<LoopResolution, string> = {
  self_recovery: "自行恢复",
  user_intervention: "用户干预",
  timeout: "超时终止",
  new_tool_invoked: "新工具调用",
  unknown: "未知",
};

/** 最终结果 → 人类可读标签 */
export const OUTCOME_LABEL: Record<string, string> = {
  task_completed: "任务完成",
  abandoned: "已放弃",
  error: "错误终止",
};

/** 循环严重度 → AnomalySeverity 映射（用于着色系统） */
export function loopSeverityToAnomalySeverity(severity: LoopSeverity): AnomalySeverity {
  switch (severity) {
    case "Warning":
      return "warning";
    case "Critical":
      return "critical";
    case "Emergency":
      return "critical";
  }
}

/** 循环类型 → 简要描述 */
export function describeLoopType(type: LoopType): string {
  switch (type) {
    case "ExactRepeat":
      return "相同参数反复调用同一工具";
    case "FuzzyRepeat":
      return "参数微小变化的重复调用";
    case "PingPong":
      return "在两个工具/文件之间反复切换";
    case "RetryStorm":
      return "短时间内密集重试失败操作";
  }
}

/** 计算循环持续时间 (ms) */
export function calcLoopDurationMs(loop: LoopSummary): number {
  const end = loop.resolved_at_ns ?? loop.last_occurrence_ns;
  return (end - loop.first_occurrence_ns) / 1_000_000;
}

/** 格式化持续时间为人类可读字符串 */
export function formatDurationMs(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  return `${(ms / 60_000).toFixed(1)}min`;
}

/** 从 agent_loop 事件创建 LoopSummary（resolved = false） */
export function createLoopSummaryFromEvent(event: AgentLoopEvent): LoopSummary {
  return {
    loop_id: event.loop_id,
    loop_type: event.loop_type,
    tool_name: event.tool_name,
    repeat_count: event.repeat_count,
    duration_ms: event.window_duration_ms,
    severity: event.severity,
    affected_pids: event.affected_pids,
    resolved: false,
    first_occurrence_ns: event.first_occurrence_ns,
    last_occurrence_ns: event.last_occurrence_ns,
    sample_payloads: event.sample_payloads,
    context: event.context,
  };
}

/** 将 agent_loop_resolved 事件应用到 LoopSummary */
export function applyResolution(
  summary: LoopSummary,
  resolved: AgentLoopResolvedEvent,
): LoopSummary {
  return {
    ...summary,
    resolved: true,
    repeat_count: resolved.total_repeats, // 用 resolved 中的最终计数覆盖
    resolution: resolved.resolution,
    final_outcome: resolved.final_outcome,
    duration_ms: resolved.duration_ms,
    resolved_at_ns: resolved.resolved_at_ns,
  };
}

/** 按工具名聚合循环频率 */
export function aggregateLoopFrequency(loops: LoopSummary[]): Record<string, number> {
  const freq: Record<string, number> = {};
  for (const loop of loops) {
    freq[loop.tool_name] = (freq[loop.tool_name] || 0) + 1;
  }
  return freq;
}

/** 按 PID 聚合循环频率 */
export function aggregateLoopFrequencyByPid(loops: LoopSummary[]): Record<number, number> {
  const freq: Record<number, number> = {};
  for (const loop of loops) {
    for (const pid of loop.affected_pids) {
      freq[pid] = (freq[pid] || 0) + 1;
    }
  }
  return freq;
}

/** 按严重度分组统计 */
export function countBySeverity(loops: LoopSummary[]): Record<LoopSeverity, number> {
  const counts: Record<LoopSeverity, number> = { Warning: 0, Critical: 0, Emergency: 0 };
  for (const loop of loops) {
    counts[loop.severity]++;
  }
  return counts;
}

/** 按循环类型分组统计 */
export function countByLoopType(loops: LoopSummary[]): Record<LoopType, number> {
  const counts: Record<LoopType, number> = {
    ExactRepeat: 0,
    FuzzyRepeat: 0,
    PingPong: 0,
    RetryStorm: 0,
  };
  for (const loop of loops) {
    counts[loop.loop_type]++;
  }
  return counts;
}

/** 获取最高的严重度级别 */
export function maxSeverity(loops: LoopSummary[]): LoopSeverity | null {
  if (loops.some((l) => l.severity === "Emergency")) return "Emergency";
  if (loops.some((l) => l.severity === "Critical")) return "Critical";
  if (loops.some((l) => l.severity === "Warning")) return "Warning";
  return null;
}

/** 合并 agent_loop 和 agent_loop_resolved 事件为 LoopSummary 列表 */
export function mergeLoopEvents(
  loopEvents: AgentLoopEvent[],
  resolvedEvents: AgentLoopResolvedEvent[],
): LoopSummary[] {
  const resolvedMap = new Map<string, AgentLoopResolvedEvent>();
  for (const r of resolvedEvents) {
    resolvedMap.set(r.loop_id, r);
  }

  const summaries: LoopSummary[] = [];
  for (const event of loopEvents) {
    let summary = createLoopSummaryFromEvent(event);
    const resolution = resolvedMap.get(event.loop_id);
    if (resolution) {
      summary = applyResolution(summary, resolution);
    }
    summaries.push(summary);
  }

  // 降级处理：仅存在于 resolved_events 但无对应 loop_event 的孤立解决事件
  for (const r of resolvedEvents) {
    if (!loopEvents.some((l) => l.loop_id === r.loop_id)) {
      summaries.push({
        loop_id: r.loop_id,
        loop_type: "ExactRepeat", // 未知类型，保守默认
        tool_name: "unknown",
        repeat_count: r.total_repeats,
        duration_ms: r.duration_ms,
        severity: "Warning",
        affected_pids: [],
        resolved: true,
        resolution: r.resolution,
        final_outcome: r.final_outcome,
        first_occurrence_ns: r.resolved_at_ns - r.duration_ms * 1_000_000,
        last_occurrence_ns: r.resolved_at_ns,
        resolved_at_ns: r.resolved_at_ns,
        sample_payloads: [],
        context: { interleaved_different_calls: 0, error_responses: 0, file_modifications: 0 },
      });
    }
  }

  return summaries;
}
