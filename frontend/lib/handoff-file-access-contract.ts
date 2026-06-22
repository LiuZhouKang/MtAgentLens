// ═══════════════════════════════════════════════════════════════
// Subagent 3 — HandoffFileAccessAnalyzer 前端数据契约
// 输入: shared_handoff.md (Observability Pipeline v2)
//      + Subagent 2 HandoffFileAccessAnalyzer 设计概要
//
// 对齐: lib/handoff-dashboard-contract.ts (HandoffObsEnvelope, DelegationNode)
//       lib/handoff-boundary-contract.ts (WorkspaceBoundary, CrossBoundaryRead)
//       lib/file-access-audit-contract.ts (FileAccessRecord)
//       lib/data-contract.ts (UnifiedStreamEvent, AnomalySeverity)
//
// 本文件覆盖 HandoffFileAccessAnalyzer 特有的：
//   1. 富化后的 file_access 事件 schema
//   2. 仪表板聚合状态
//   3. 组件 Props（文件访问时间线行、工作空间边界散点、委托深度热力图）
// ═══════════════════════════════════════════════════════════════

import type { AnomalySeverity } from "./data-contract";
import type { DelegationNode } from "./handoff-dashboard-contract";

// ═══════════════════════════════════════════════════════════════
// 一、富化后的文件访问事件（对应 Subagent 2 输出 schema）
//    HandoffFileAccessAnalyzer 在 FileAccessRecord 基础上附加
//    handoff 和 workspace 元数据
// ═══════════════════════════════════════════════════════════════

/** 交接生命周期阶段（对应 handoff §3 latency 分解） */
export type HandoffLifecyclePhase =
  | "dispatch"
  | "context_inject"
  | "execute"
  | "collect";

/** 工作空间边界判定（HandoffFileAccessAnalyzer 内部判定） */
export type WorkspaceBoundary = "inside" | "outside";

/**
 * HandoffFileAccessAnalyzer 输出的富化事件
 * 在现有 FileAccessRecord 基础上附加 handoff + workspace 字段
 *
 * SSE: stream_type = "file_access"
 * 路由: TimelinePanel → FileAccessTimelineRow
 */
export interface HandoffFileAccessEvent {
  // ── 基础字段（来自上游 FileAccessAuditAnalyzer）──
  access_id: string;
  timestamp_ms: number;
  syscall: "open" | "openat";
  target_path: string;
  flags: number;
  pid: number;
  comm: string;

  // ── Subagent 2: handoff 关联 ──
  /** 归属的 agent ID（从 DelegationTreeStore 查询） */
  agent_id: string | null;
  /** 委托链深度 N=0 根代理, N+1 每层嵌套（handoff §4） */
  chain_depth: number;
  /** 交接生命周期阶段（handoff §3） */
  lifecycle_phase: HandoffLifecyclePhase | null;
  /** 触发此文件访问的 tool_call ID（handoff §1） */
  tool_call_id: string | null;
  /** 父代理 ID（handoff §9 委托树） */
  parent_agent_id: string | null;
  /** 根代理 PID */
  root_pid: number | null;

  // ── Subagent 2: workspace 边界 ──
  /** 工作空间边界判定（handoff §2 + PLATFORM-0042） */
  boundary: WorkspaceBoundary;
  /** 严重级别：outside → "warn"（PLATFORM-0042 §2） */
  severity: "info" | "warn";
  /** workspace_tag 是否被后续 analyzer 保留（注入 ws §3） */
  tag_preserved: boolean;
}

// ═══════════════════════════════════════════════════════════════
// 二、按深度分组的文件访问聚合
//    仪表板中按 chain_depth 分组展示访问模式
// ═══════════════════════════════════════════════════════════════

/** 单个委托深度的文件访问统计 */
export interface DepthLevelStats {
  chain_depth: number;
  /** 该深度的活跃 agent 数 */
  agent_count: number;
  /** 总文件访问次数 */
  total_accesses: number;
  /** 跨边界访问次数 */
  cross_boundary_count: number;
  /** 跨边界比例 */
  cross_boundary_ratio: number;
  /** 按访问路径的 top-5 热点 */
  top_paths: Array<{ path: string; count: number }>;
}

/** 按 agent + 深度的交叉统计（用于散点图） */
export interface AgentFileAccessScatter {
  agent_id: string;
  chain_depth: number;
  inside_count: number;
  outside_count: number;
  /** outside 比例是否异常（> 同深度兄弟均值 + 1.5σ） */
  is_anomalous: boolean;
  /** 异常偏离的 sigma 值（null = 未偏离） */
  deviation_sigmas: number | null;
}

// ═══════════════════════════════════════════════════════════════
// 三、仪表板聚合状态
//    客户端从 SSE 流增量聚合
// ═══════════════════════════════════════════════════════════════

export interface HandoffFileAccessDashboardState {
  /** 最近 N 条富化文件访问事件 */
  recent_events: HandoffFileAccessEvent[];

  /** 按深度分组的统计 */
  depth_stats: DepthLevelStats[];

  /** 按 agent 的散点数据 */
  agent_scatter: AgentFileAccessScatter[];

  /** 当前筛选 */
  filter: HandoffFileAccessFilter;

  /** 委托树引用（与 DelegationTreePanel 共享） */
  delegation_tree: DelegationNode | null;

  /** 指标 */
  metrics: HandoffFileAccessMetrics;
}

export interface HandoffFileAccessFilter {
  /** 委托深度筛选（null = 全部） */
  chain_depth: number | null;
  /** 工作空间边界筛选（null = 全部） */
  boundary: WorkspaceBoundary | null;
  /** Agent ID 筛选（null = 全部） */
  agent_id: string | null;
  /** 是否仅显示跨边界事件 */
  cross_boundary_only: boolean;
}

export interface HandoffFileAccessMetrics {
  total_events: number;
  inside_count: number;
  outside_count: number;
  cross_boundary_ratio: number;
  max_chain_depth: number;
  /** workspace_tag 存活率（注入 ws §3） */
  tag_survival_rate: number;
  /** 有异常的 agent 数量 */
  anomalous_agent_count: number;
}

/** 创建默认状态 */
export function createDefaultHandoffFileAccessState(): HandoffFileAccessDashboardState {
  return {
    recent_events: [],
    depth_stats: [],
    agent_scatter: [],
    filter: {
      chain_depth: null,
      boundary: null,
      agent_id: null,
      cross_boundary_only: false,
    },
    delegation_tree: null,
    metrics: {
      total_events: 0,
      inside_count: 0,
      outside_count: 0,
      cross_boundary_ratio: 0,
      max_chain_depth: 0,
      tag_survival_rate: 1.0,
      anomalous_agent_count: 0,
    },
  };
}

// ═══════════════════════════════════════════════════════════════
// 四、SSE 流扩展
//    在现有 HandoffExtendedStreamEvent 基础上新增
// ═══════════════════════════════════════════════════════════════

/**
 * HandoffFileAccessAnalyzer 的 SSE 事件
 * 与 handoff-observability-contract.ts 的 HandoffExtendedStreamEvent 共存
 */
export type HandoffFileAccessStreamEvent =
  | { stream_type: "file_access"; data: HandoffFileAccessEvent }
  | { stream_type: "file_access_depth_stats"; data: DepthLevelStats };

// ═══════════════════════════════════════════════════════════════
// 五、组件 Props
//    遵循现有模式: 'use client' + zustand store 消费状态
//    与已有 handoff-dashboard-contract.ts 的组件互补
// ═══════════════════════════════════════════════════════════════

// ── 5.1 文件访问时间线行 ──
// 在现有 TimelinePanel 中渲染，替代/补充 FileAccessRowProps
// 每个事件一行，显示路径、边界标记、委托深度、tool_call 关联

export interface HandoffFileAccessRowProps {
  event: HandoffFileAccessEvent;
  is_highlighted: boolean;
  /** 高亮的 agent ID（从树选择联动） */
  highlight_agent_id: string | null;
  onSelect: (event: HandoffFileAccessEvent) => void;
  /** 点击 agent ID 导航到委托树对应节点 */
  onNavigateToAgent: (agentId: string) => void;
  /** PLATFORM-0042 §3: 客户端边界事件过滤开关 */
  show_cross_boundary?: boolean;
}

// ── 5.2 工作空间边界散点图 ──
// 以 chain_depth 为 X 轴，cross_boundary_ratio 为 Y 轴
// 每个 agent 一个散点，异常点高亮

export interface WorkspaceBoundaryScatterChartProps {
  agents: AgentFileAccessScatter[];
  selected_agent_id: string | null;
  onSelectAgent: (agentId: string) => void;
  /** 异常阈值（默认 1.5σ） */
  anomalyThreshold?: number;
}

// ── 5.3 委托深度热力图 ──
// 按 depth 分组的文件访问热点矩阵
// 行 = chain_depth, 列 = lifecycle_phase
// 单元格 = 该深度+阶段的文件访问次数

export interface DepthPhaseHeatmapProps {
  /** 热力图数据: depth × phase → count */
  data: Array<{
    chain_depth: number;
    phase: HandoffLifecyclePhase;
    access_count: number;
    cross_boundary_count: number;
  }>;
  max_depth: number;
  selected_depth: number | null;
  onSelectDepth: (depth: number | null) => void;
  /** 单元格点击 → 筛选该深度+阶段的事件 */
  onSelectCell: (depth: number, phase: HandoffLifecyclePhase) => void;
}

// ── 5.4 文件访问事件详情弹出层 ──
// 点击时间线行后展示的详情卡片

export interface FileAccessEventDetailProps {
  event: HandoffFileAccessEvent;
  /** 同 path 的历史访问记录（最近 5 条） */
  path_history: HandoffFileAccessEvent[];
  /** 关联的 tool_call 信息（如有） */
  tool_call_info: {
    tool_name: string;
    call_id: string;
    args_preview: string;
  } | null;
  onClose: () => void;
  onNavigateToAgent: (agentId: string) => void;
  onNavigateToToolCall: (callId: string) => void;
}

// ── 5.5 委托深度概览面板 ──
// DashboardShell 侧栏中显示按深度分组的概要条
// 与现有 DelegationTreePanel + HandoffMetrics 互补

export interface DepthOverviewPanelProps {
  /** 各深度统计（按 depth 升序） */
  depth_stats: DepthLevelStats[];
  /** 整体指标 */
  metrics: HandoffFileAccessMetrics;
  /** 当前选中深度 */
  selected_depth: number | null;
  onSelectDepth: (depth: number | null) => void;
  /** 委托树总节点数 */
  total_agents: number;
  /** 委托树最大深度 */
  max_depth: number;
}

// ── 5.6 跨边界文件访问时间线面板 ──
// 仅展示 boundary="outside" 的事件，按深度分组的时间线

export interface CrossBoundaryFileAccessTimelineProps {
  /** 跨边界事件（已按时间排序） */
  events: HandoffFileAccessEvent[];
  /** 按深度分组 */
  depth_groups: Map<number, HandoffFileAccessEvent[]>;
  selected_event_id: string | null;
  onSelectEvent: (event: HandoffFileAccessEvent) => void;
  onNavigateToAgent: (agentId: string) => void;
  /** 工作空间根路径列表（用于显示相对路径） */
  workspace_roots: string[];
}

// ═══════════════════════════════════════════════════════════════
// 六、与已有契约的桥接
//    避免重复定义，复用已有类型
// ═══════════════════════════════════════════════════════════════

/**
 * 将 HandoffFileAccessEvent 转换为已有 FileAccessWithHandoff
 * 用于与 handoff-observability-contract.ts 的组件互操作
 */
export function toFileAccessWithHandoff(
  event: HandoffFileAccessEvent,
): import("./handoff-observability-contract").FileAccessWithHandoff {
  return {
    access_id: event.access_id,
    timestamp_ms: event.timestamp_ms,
    syscall: event.syscall,
    target_path: event.target_path,
    flags: event.flags,
    workspace_tag: event.boundary === "outside" ? "outside" : "inside",
    agent_id: event.agent_id ?? "unknown",
    spawn_id: null,
    chain_depth: event.chain_depth,
    handoff_phase: event.lifecycle_phase ?? "execute",
    tool_call_id: event.tool_call_id,
    severity: event.severity,
  };
}

/**
 * 从 HandoffFileAccessEvent 提取严重级别
 * 用于与 data-contract.ts 的 AnomalySeverity 兼容
 */
export function toSeverity(event: HandoffFileAccessEvent): "info" | "warning" {
  return event.severity === "warn" ? "warning" : "info";
}

// ═══════════════════════════════════════════════════════════════
// 七、类型守卫
// ═══════════════════════════════════════════════════════════════

export function isHandoffFileAccessEvent(
  event: HandoffFileAccessStreamEvent,
): event is { stream_type: "file_access"; data: HandoffFileAccessEvent } {
  return event.stream_type === "file_access";
}

export function isDepthStats(
  event: HandoffFileAccessStreamEvent,
): event is { stream_type: "file_access_depth_stats"; data: DepthLevelStats } {
  return event.stream_type === "file_access_depth_stats";
}

/** 判断文件访问是否为跨边界 */
export function isCrossBoundary(event: HandoffFileAccessEvent): boolean {
  return event.boundary === "outside";
}

/** 判断 agent 的跨边界比例是否异常 */
export function isAnomalousAgent(agent: AgentFileAccessScatter): boolean {
  return agent.is_anomalous;
}
