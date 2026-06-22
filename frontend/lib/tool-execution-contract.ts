// ToolExecutionTracker Dashboard — 可视化契约
// 对应 collector/src/framework/analyzers/tool_execution_tracker 的输出事件格式
//
// 三种输出事件 source:
//   "tool_definition" — LLM API 请求中携带的工具定义快照
//   "tool_call"       — 单次工具调用的完整生命周期
//   "tool_stats"      — drain() 时发出的每 Agent 聚合统计

import type { AnomalySeverity } from "./data-contract";

// ═══════════════════════════════════════════════════════════════════════════
// 工具调用状态（与 Rust ToolCallStatus 对齐）
// ═══════════════════════════════════════════════════════════════════════════

export type ToolCallStatus =
  | "Pending"
  | "Success"
  | "Failed"
  | "Timeout"
  | "Cancelled";

// ═══════════════════════════════════════════════════════════════════════════
// 工具调用结果（与 Rust ToolCallResult 对齐）
// ═══════════════════════════════════════════════════════════════════════════

export interface ToolCallResult {
  /** 工具返回的文本/JSON 内容 */
  content: string;
  /** 工具层 is_error 标志 */
  is_error: boolean;
  /** 结果长度 (字节) */
  content_length: number;
}

// ═══════════════════════════════════════════════════════════════════════════
// 核心数据结构
// ═══════════════════════════════════════════════════════════════════════════

/** 单次工具调用记录（source: "tool_call"） */
export interface ToolCallRecord {
  /** 唯一标识符: "{request_id}:{tool_call_index}" */
  call_id: string;
  /** 所属请求 ID */
  request_id: string;
  /** 工具名称 (e.g., "read_file", "execute_command") */
  tool_name: string;
  /** 工具参数 (JSON object) */
  arguments: Record<string, unknown>;
  /** 在同一次请求中的序号 (0-based) */
  call_index: number;
  /** 发起调用的 Agent PID */
  agent_pid: number;
  /** 请求时间戳 (epoch ns) */
  request_timestamp_ns: number;
  /** 响应时间戳 (epoch ns) — 完成后填充 */
  response_timestamp_ns: number | null;
  /** 调用状态 */
  status: ToolCallStatus;
  /** 工具执行结果 */
  result: ToolCallResult | null;
}

/** 单次 LLM API 请求中携带的工具定义快照（source: "tool_definition"） */
export interface ToolDefinitionSnapshot {
  /** 请求 ID */
  request_id: string;
  /** 该请求声明的工具名称列表 */
  tool_names: string[];
  /** 工具定义总数 */
  tool_count: number;
  /** tool_choice 设置 (Anthropic: {type: "auto"}, OpenAI: "auto") */
  tool_choice: string | Record<string, unknown> | null;
  /** 使用的模型 ID */
  model: string | null;
}

/** 每 Agent 的工具统计（source: "tool_stats"） */
export interface AgentToolStats {
  /** Agent PID */
  agent_pid: number;
  /** 总调用次数 */
  total_calls: number;
  /** 成功次数 */
  successful_calls: number;
  /** 失败次数 */
  failed_calls: number;
  /** 超时次数 */
  timed_out_calls: number;
  /** 被取消次数 */
  cancelled_calls: number;
  /** 使用的唯一工具列表 */
  unique_tools_used: string[];
  /** 每种工具的调用次数 */
  tool_frequency: Record<string, number>;
  /** 每种工具的平均延迟 (微秒) */
  avg_latency_us: Record<string, number>;
  /** 每种工具的 P95 延迟 (微秒) */
  p95_latency_us: Record<string, number>;
}

// ═══════════════════════════════════════════════════════════════════════════
// SSE 流集成 — 扩展 StreamEnvelope 的 stream_type
// ═══════════════════════════════════════════════════════════════════════════

/**
 * 扩展的 stream_type 判别联合，新增 tool_call / tool_definition / tool_stats。
 * 向后兼容现有 data-contract 中的 timeline / process_tree / anomaly。
 */
export type ToolExecutionStreamType =
  | "tool_call"
  | "tool_definition"
  | "tool_stats";

/** 扩展后的完整 stream_type 集合 */
export type ExtendedStreamType =
  | import("./data-contract").UnifiedStreamEvent["stream_type"]
  | ToolExecutionStreamType;

// ═══════════════════════════════════════════════════════════════════════════
// 仪表板 UI 状态
// ═══════════════════════════════════════════════════════════════════════════

/** 仪表板视图模式 */
export type DashboardViewMode = "overview" | "tools" | "timeline";

/** 工具调用的 UI 级严重程度（用于着色和过滤） */
export function toolStatusToSeverity(status: ToolCallStatus): AnomalySeverity {
  switch (status) {
    case "Success":
      return "info";
    case "Pending":
      return "warning";
    case "Failed":
      return "critical";
    case "Timeout":
      return "critical";
    case "Cancelled":
      return "warning";
  }
}

/** 仪表板全局状态 */
export interface ToolExecutionDashboardState {
  /** 当前活跃的 Agent PID 列表 */
  agent_pids: number[];
  /** 选中的 Agent PID（null = 所有 Agent 聚合） */
  selected_agent_pid: number | null;
  /** 当前视图模式 */
  view_mode: DashboardViewMode;
  /** 每个 Agent 的工具统计数据 */
  stats_by_agent: Map<number, AgentToolStats>;
  /** 最近的工具调用记录（环形缓冲区，最多保留 500 条） */
  recent_calls: ToolCallRecord[];
  /** 最近的工具定义快照（最多保留 50 条） */
  recent_definitions: ToolDefinitionSnapshot[];
  /** 工具调用时间线过滤 — 按状态 */
  status_filter: ToolCallStatus | null;
  /** 工具调用时间线过滤 — 按工具名 */
  tool_filter: string | null;
  /** 最后更新时间 (epoch ms) */
  last_update_ms: number;
}

// ═══════════════════════════════════════════════════════════════════════════
// 组件 Props
// ═══════════════════════════════════════════════════════════════════════════

export interface ToolExecutionDashboardProps {
  /** 仪表板状态 */
  state: ToolExecutionDashboardState;
  /** 切换视图模式 */
  onViewModeChange: (mode: DashboardViewMode) => void;
  /** 选择 Agent */
  onAgentSelect: (pid: number | null) => void;
  /** 选中具体工具调用（联动 DetailPanel） */
  onCallSelect: (call: ToolCallRecord) => void;
  /** 切换状态过滤 */
  onStatusFilterChange: (status: ToolCallStatus | null) => void;
  /** 切换工具名过滤 */
  onToolFilterChange: (tool_name: string | null) => void;
  /** 是否紧凑模式 */
  compact?: boolean;
}

export interface ToolCallCardProps {
  call: ToolCallRecord;
  selected?: boolean;
  onSelect: () => void;
}

export interface ToolStatsSummaryProps {
  stats: AgentToolStats;
  agent_pid: number;
  /** 是否高亮（当前选中） */
  highlighted?: boolean;
}

export interface ToolFrequencyChartProps {
  frequency: Record<string, number>;
  selected_tool: string | null;
  onToolSelect: (tool_name: string | null) => void;
}

export interface ToolLatencyChartProps {
  avg_latency_us: Record<string, number>;
  p95_latency_us: Record<string, number>;
  selected_tool: string | null;
}

// ═══════════════════════════════════════════════════════════════════════════
// 工具函数
// ═══════════════════════════════════════════════════════════════════════════

/** 计算成功率 (0–1) */
export function calcSuccessRate(stats: AgentToolStats): number {
  if (stats.total_calls === 0) return 1;
  return stats.successful_calls / stats.total_calls;
}

/** 计算失败率 (0–1) */
export function calcFailureRate(stats: AgentToolStats): number {
  if (stats.total_calls === 0) return 0;
  return (stats.failed_calls + stats.timed_out_calls) / stats.total_calls;
}

/** 工具调用延迟 (微秒)，null 表示尚未完成 */
export function calcLatencyUs(call: ToolCallRecord): number | null {
  if (call.response_timestamp_ns == null) return null;
  return (call.response_timestamp_ns - call.request_timestamp_ns) / 1000;
}

/** 格式化延迟为人类可读字符串 */
export function formatLatency(latencyUs: number): string {
  if (latencyUs < 1000) return `${Math.round(latencyUs)}μs`;
  if (latencyUs < 1_000_000) return `${(latencyUs / 1000).toFixed(1)}ms`;
  return `${(latencyUs / 1_000_000).toFixed(2)}s`;
}

/** 合并两个 AgentToolStats（用于多 Agent 聚合视图） */
export function mergeStats(all: AgentToolStats[]): AgentToolStats {
  const merged: AgentToolStats = {
    agent_pid: 0,
    total_calls: 0,
    successful_calls: 0,
    failed_calls: 0,
    timed_out_calls: 0,
    cancelled_calls: 0,
    unique_tools_used: [],
    tool_frequency: {},
    avg_latency_us: {},
    p95_latency_us: {},
  };

  const toolSet = new Set<string>();
  const allLatencies: Record<string, number[]> = {};

  for (const s of all) {
    merged.total_calls += s.total_calls;
    merged.successful_calls += s.successful_calls;
    merged.failed_calls += s.failed_calls;
    merged.timed_out_calls += s.timed_out_calls;
    merged.cancelled_calls += (s.cancelled_calls ?? 0);

    for (const tn of s.unique_tools_used) {
      toolSet.add(tn);
      merged.tool_frequency[tn] =
        (merged.tool_frequency[tn] || 0) + (s.tool_frequency[tn] || 0);
    }

    // 加权平均延迟
    for (const [tn, avg] of Object.entries(s.avg_latency_us)) {
      const count = s.tool_frequency[tn] || 0;
      if (!allLatencies[tn]) allLatencies[tn] = [];
      // 近似：用 avg * count 重建总延迟
      if (!merged.avg_latency_us[tn]) merged.avg_latency_us[tn] = 0;
      merged.avg_latency_us[tn] += avg * count;
    }
    for (const [tn, p95] of Object.entries(s.p95_latency_us)) {
      const prev = merged.p95_latency_us[tn] || 0;
      merged.p95_latency_us[tn] = Math.max(prev, p95);
    }
  }

  // 归一化加权平均
  for (const tn of Object.keys(merged.avg_latency_us)) {
    const count = merged.tool_frequency[tn] || 1;
    merged.avg_latency_us[tn] /= count;
  }

  merged.unique_tools_used = Array.from(toolSet).sort();
  return merged;
}

/** 从未定义过但被调用过的工具（orphaned tool calls） */
export function findOrphanedTools(
  calls: ToolCallRecord[],
  definitions: ToolDefinitionSnapshot[],
): string[] {
  const defined = new Set(definitions.flatMap((d) => d.tool_names));
  const called = new Set(calls.map((c) => c.tool_name));
  return Array.from(called).filter((t) => !defined.has(t)).sort();
}

/** 定义了但从未被调用的工具（unused tools） */
export function findUnusedTools(
  calls: ToolCallRecord[],
  definitions: ToolDefinitionSnapshot[],
): string[] {
  const defined = new Set(definitions.flatMap((d) => d.tool_names));
  const called = new Set(calls.map((c) => c.tool_name));
  return Array.from(defined).filter((t) => !called.has(t)).sort();
}
