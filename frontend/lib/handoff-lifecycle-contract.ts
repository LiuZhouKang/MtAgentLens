// ═══════════════════════════════════════════════════════════════
// Subagent 3 — HandoffLifecycle Frontend Data Contract (v3)
// 输入: Subagent 2 的 HandoffLifecycleAnalyzer 设计
//       + shared_handoff.md feature reqs 1-3 + 补充需求 A/B/C
// 对齐: lib/types.ts · lib/data-contract.ts
//       子代理 2 的 6 种事件类型 + FileAccessWithHandoff
// ═══════════════════════════════════════════════════════════════

import type { AnomalySeverity } from "./data-contract";

// ═══════════════════════════════════════════════════════════════
// 1. 事件源 + 歧视类型 (与 Subagent 2 的 6 个 emit 分支精确对齐)
//    source = "handoff_lifecycle"
// ═══════════════════════════════════════════════════════════════

export type HandoffLifecycleEventType =
  | "HANDOFF_SPAWN"                    // → HandoffSpawnPayload
  | "HANDOFF_COMPLETE"                 // → HandoffCompletePayload
  | "WORKSPACE_BOUNDARY_VIOLATION"     // → BoundaryViolationPayload
  | "CONTEXT_SATURATION_WARNING"       // → SaturationWarningPayload
  | "FANOUT_STORM_CRITICAL"            // → FanoutStormPayload
  | "DELEGATION_TREE_SNAPSHOT";        // → DelegationTreePayload

// ═══════════════════════════════════════════════════════════════
// 2. 事件载荷 (每个事件类型一个接口)
// ═══════════════════════════════════════════════════════════════

/** Subagent 2: detect_spawn() → HANDOFF_SPAWN */
export interface HandoffSpawnPayload {
  spawn_id: string;
  parent_spawn_id: string | null; // null → 根代理
  depth: number;                   // N=0 根, 每级 +1
  agent_pid: number;
  comm: string;
  context_size_bytes: number;      // 注入的 handoff prompt 大小
  context_budget_bytes: number;    // 子代理总上下文预算
  dispatched_model: string | null;
  workspace_root: string;
  timestamp_ns: number;
}

/** Subagent 2: detect_complete() → HANDOFF_COMPLETE */
export interface HandoffCompletePayload {
  spawn_id: string;
  depth: number;
  bytes_in: number;
  bytes_out: number;
  token_count: number;
  total_latency_ns: number;
  context_saturation_ratio: number; // 0.0–1.0
  timestamp_ns: number;
  /** 子代理 2: 每次 COMPLETE 序列化完整子树快照 */
  subtree_snapshot: DelegationTreeSnapshot;
}

/** Subagent 2: check_boundary() → WORKSPACE_BOUNDARY_VIOLATION
 *  补充需求 B: 工作区外的跨边界文件访问 */
export interface BoundaryViolationPayload {
  access_id: string;
  agent_pid: number;
  agent_root_pid: number;
  target_path: string;
  workspace_root: string;
  /** 补充需求 A: boundary_status "inside" | "outside" */
  boundary_status: "inside" | "outside";
  severity: AnomalySeverity;
  timestamp_ns: number;
}

/** Subagent 2: check_saturation() → CONTEXT_SATURATION_WARNING
 *  触发条件: context_size / context_budget > 0.6 */
export interface SaturationWarningPayload {
  spawn_id: string;
  depth: number;
  injected_bytes: number;
  context_budget_bytes: number;
  overhead_ratio: number;          // 0.0–1.0
  severity: "warning" | "critical"; // >0.85 → critical
  timestamp_ns: number;
}

/** Subagent 2: check_fanout_storm() → FANOUT_STORM_CRITICAL
 *  触发条件: spawn/completion 比率 > 3:1 (滑动窗口) */
export interface FanoutStormPayload {
  window_start_ns: number;
  window_end_ns: number;
  window_secs: number;
  spawn_count: number;
  completion_count: number;
  storm_ratio: number;              // spawn / completion
  threshold_ratio: number;          // 默认 3.0
  severity: "critical";
  timestamp_ns: number;
}

/** Subagent 2: emit_snapshot() → DELEGATION_TREE_SNAPSHOT (每 30s 周期) */
export interface DelegationTreePayload {
  snapshot: DelegationTreeSnapshot;
}

/** 委派树快照 — 前端树可视化的核心数据结构 (shared_handoff.md #9) */
export interface DelegationTreeSnapshot {
  nodes: DelegationNode[];
  edges: DelegationEdge[];
  root_agent_pid: number;
  snapshot_at_ns: number;
}

export interface DelegationNode {
  agent_pid: number;
  spawn_id: string;
  depth: number;
  parent_pid: number | null;
  workspace_root: string;
  dispatched_at_ns: number;
  completed_at_ns: number | null;   // null → 仍在运行
  model_used: string | null;
}

export interface DelegationEdge {
  from_agent_pid: number;
  to_agent_pid: number;
}

// ═══════════════════════════════════════════════════════════════
// 3. 文件访问 + 手递手元数据
//    补充需求 A: 所有 FILE_OPEN 事件附加 workspace_boundary 标签
//    补充需求 C: allow_filter = false (下游 HTTPFilter/SSLFilter 跳过)
//    shared_handoff.md #1, #2, #4
// ═══════════════════════════════════════════════════════════════

export interface FileAccessWithHandoff {
  access_id: string;
  timestamp_ns: number;
  agent_pid: number;
  agent_root_pid: number;
  operation: "open" | "openat" | "read" | "write";
  target_path: string;
  flags: number;

  // 补充需求 A: workspace_boundary 标签
  workspace_boundary: "inside" | "outside";

  // shared_handoff.md #4: 链深度标记
  chain_depth: number;

  // shared_handoff.md #2: 与工具调用关联
  tool_call_id: string | null;

  // 补充需求 C: 反过滤标志
  allow_filter: false;

  risk_level: "low" | "medium" | "high";
}

// ═══════════════════════════════════════════════════════════════
// 4. 统一事件联合类型
// ═══════════════════════════════════════════════════════════════

export type HandoffLifecycleEvent =
  | { event_type: "HANDOFF_SPAWN"; payload: HandoffSpawnPayload }
  | { event_type: "HANDOFF_COMPLETE"; payload: HandoffCompletePayload }
  | { event_type: "WORKSPACE_BOUNDARY_VIOLATION"; payload: BoundaryViolationPayload }
  | { event_type: "CONTEXT_SATURATION_WARNING"; payload: SaturationWarningPayload }
  | { event_type: "FANOUT_STORM_CRITICAL"; payload: FanoutStormPayload }
  | { event_type: "DELEGATION_TREE_SNAPSHOT"; payload: DelegationTreePayload };

// ═══════════════════════════════════════════════════════════════
// 5. 客户端聚合状态
// ═══════════════════════════════════════════════════════════════

export interface HandoffLifecycleState {
  /** 活跃 handoff (已 spawn 但未 complete) */
  active_handoffs: Map<string, HandoffSpawnPayload>;

  /** 最近完成的 handoff */
  recent_completions: HandoffCompletePayload[];

  /** 最新委派树快照 */
  delegation_tree: DelegationTreeSnapshot | null;

  /** 活跃的边界违规 */
  boundary_violations: BoundaryViolationPayload[];

  /** 活跃的饱和度警告 */
  saturation_warnings: SaturationWarningPayload[];

  /** 聚合指标 */
  metrics: HandoffLifecycleMetrics;
}

export interface HandoffLifecycleMetrics {
  total_spawned: number;
  total_completed: number;
  active_count: number;
  max_depth: number;
  avg_context_saturation: number;
  current_fanout_ratio: number;     // spawn/completion 比率
  is_fanout_storm: boolean;         // 比率 > 阈值
  boundary_violation_count: number;
}

// ═══════════════════════════════════════════════════════════════
// 6. 组件 Props
// ═══════════════════════════════════════════════════════════════

/** 委派树可视化面板 (shared_handoff.md #9)
 *  时间索引树 — 支持实时模式和回放模式 */
export interface DelegationTreePanelProps {
  tree: DelegationTreeSnapshot | null;
  metrics: HandoffLifecycleMetrics;
  selectedAgentPid: number | null;
  onSelectNode: (agentPid: number) => void;
  replayTimestampNs: number | null;   // null=实时, 值=回放快照
  colorByDepth?: boolean;
}

/** 子代理节点详情 (shared_handoff.md #12)
 *  包含延迟分解 + 饱和度仪表 + 文件访问记录 */
export interface DelegationNodeDetailProps {
  node: DelegationNode;
  completion: HandoffCompletePayload | null;
  saturationWarnings: SaturationWarningPayload[];
  boundaryViolations: BoundaryViolationPayload[];
  fileAccesses: FileAccessWithHandoff[];
  onClose: () => void;
  onNavigateToTimeline: (agentPid: number) => void;
}

/** 上下文饱和度仪表 (shared_handoff.md #7) */
export interface SaturationGaugeProps {
  overheadRatio: number;
  injectedBytes: number;
  budgetBytes: number;
  severity: "warning" | "critical";
  warnThreshold?: number;       // 默认 0.6
  criticalThreshold?: number;   // 默认 0.85
  compact?: boolean;
}

/** 扇出风暴监控 (shared_handoff.md #13) */
export interface FanoutStormPanelProps {
  metrics: HandoffLifecycleMetrics;
  /** 比率历史序列 */
  history: Array<{
    timestamp_ns: number;
    ratio: number;
    spawn_count: number;
    completion_count: number;
  }>;
  criticalRatio?: number;       // 默认 3.0
  onViewAffected: () => void;
}

/** 工作区边界违规面板 (补充需求 B) */
export interface BoundaryViolationPanelProps {
  violations: BoundaryViolationPayload[];
  onSelectViolation: (accessId: string) => void;
  onNavigateToAgent: (agentPid: number) => void;
}

/** handoff 事件时间线行 — 在现有 TimelinePanel 中注册 */
export interface HandoffTimelineRowProps {
  event: HandoffLifecycleEvent;
  isActive: boolean;
  onSelect: () => void;
  onFocusAgent: (agentPid: number) => void;
}

/** 文件访问时间线行 (补充需求 A) */
export interface FileAccessRowProps {
  access: FileAccessWithHandoff;
  isHighlighted: boolean;
  onSelect: () => void;
  onFocusAgent: (agentPid: number) => void;
}

// ═══════════════════════════════════════════════════════════════
// 7. SSE 流扩展 — 集成到 UnifiedStreamEvent
// ═══════════════════════════════════════════════════════════════

/** 新增 handoff_lifecycle 流类型，注入现有 UnifiedStreamEvent 联合类型 */
export interface HandoffLifecycleStreamEvent {
  stream_type: "handoff_lifecycle";
  sequence: number;
  event: HandoffLifecycleEvent;
}

/** 新增 file_access 流类型 */
export interface FileAccessStreamEvent {
  stream_type: "file_access";
  sequence: number;
  event: FileAccessWithHandoff;
}

// ═══════════════════════════════════════════════════════════════
// 8. 类型守卫
// ═══════════════════════════════════════════════════════════════

export function isHandoffSpawn(e: HandoffLifecycleEvent): e is { event_type: "HANDOFF_SPAWN"; payload: HandoffSpawnPayload } {
  return e.event_type === "HANDOFF_SPAWN";
}
export function isHandoffComplete(e: HandoffLifecycleEvent): e is { event_type: "HANDOFF_COMPLETE"; payload: HandoffCompletePayload } {
  return e.event_type === "HANDOFF_COMPLETE";
}
export function isBoundaryViolation(e: HandoffLifecycleEvent): e is { event_type: "WORKSPACE_BOUNDARY_VIOLATION"; payload: BoundaryViolationPayload } {
  return e.event_type === "WORKSPACE_BOUNDARY_VIOLATION";
}
export function isSaturationWarning(e: HandoffLifecycleEvent): e is { event_type: "CONTEXT_SATURATION_WARNING"; payload: SaturationWarningPayload } {
  return e.event_type === "CONTEXT_SATURATION_WARNING";
}
export function isFanoutStorm(e: HandoffLifecycleEvent): e is { event_type: "FANOUT_STORM_CRITICAL"; payload: FanoutStormPayload } {
  return e.event_type === "FANOUT_STORM_CRITICAL";
}
export function isDelegationTreeSnapshot(e: HandoffLifecycleEvent): e is { event_type: "DELEGATION_TREE_SNAPSHOT"; payload: DelegationTreePayload } {
  return e.event_type === "DELEGATION_TREE_SNAPSHOT";
}
