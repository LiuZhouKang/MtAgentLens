// =============================================================================
// Subagent 3 — Handoff Pipeline Frontend Data Contract (brief)
// 集成现有 lib/data-contract.ts 的 UnifiedStreamEvent 和 lib/types.ts 的类型系统
// =============================================================================

import type { UnifiedStreamEvent } from "./data-contract";
import type { AlertSeverity } from "./types";

export const HANDOFF_PIPELINE_VERSION = "1.0.0" as const;

// =============================================================================
// 1. 新增 Stream 类型 — 扩展现有 UnifiedStreamEvent
// =============================================================================

/** 工作区边界标记 (PLATFORM-0042) */
export type WorkspaceTag = "inside" | "outside";

/** 文件访问事件 — source: "file_access", 通过 /api/events SSE 推送 */
export interface FileAccessEvent {
  // 标准框架字段
  id: string;
  timestamp_ns: number;
  level: "INFO" | "WARN"; // PLATFORM-0042: WARN 用于跨边界
  source: "file_access";

  // 系统调用数据
  syscall: "open" | "openat";
  target_path: string;
  flags: number;
  pid: number;
  comm: string;

  // PLATFORM-0042: 工作区边界
  workspace_tag: WorkspaceTag;
  workspace_root: string;

  // Handoff #4: 委托链深度
  chain_depth: number; // N=0 root, N+1 per delegation level
}

/** 手递手可观测事件 — source: "handoff_observability", 子类型分发渲染 */
export type HandoffObsSubType =
  | "HANDOFF_SPAWN"          // Handoff #9: 子代理生成事件 → 构建委托树
  | "HANDOFF_COMPLETE"       // Handoff #5/9: 子代理完成记录
  | "FILE_CORRELATION"       // Handoff #1: file_access ↔ tool_call 关联边
  | "BOUNDARY_ALERT"         // Handoff #2: 跨工作区/敏感文件访问告警
  | "LIFECYCLE_TIMING"       // Handoff #3: 四阶段延迟分解
  | "CONTEXT_SATURATION"     // Handoff #7: 上下文窗口饱和度 >60%
  | "TOOL_DENIED"            // Handoff #8: 策略门控拒绝记录
  | "CONTEXT_CONTRADICTION"  // Handoff #6: 子代理指令与根约束冲突
  | "ORPHANED_WORK"          // Handoff #10: 子代理输出从未被消费
  | "PROMPT_DRIFT"           // Handoff #11: 语义嵌入漂移检测
  | "IDLE_TIME"              // Handoff #12: 子代理空闲时间归因
  | "FANOUT_STORM"           // Handoff #13: spawn/complete > 3:1
  | "DATA_LEAK"              // Handoff #14: 跨代理文件内容碰撞
  | "STRATEGY_DIVERGENCE";   // Handoff #15: 工具序列偏离 >2σ

/** 手递手事件载荷 — 字段随 sub_type 不同, 此处为公共字段 */
export interface HandoffObservabilityPayload {
  sub_type: HandoffObsSubType;
  agent_id: string;
  parent_agent_id: string | null;
  chain_depth: number;
  timestamp_ns: number;
  // 各 sub_type 的专用字段由后端 JSON 展开提供
  [key: string]: unknown;
}

// =============================================================================
// 2. 统一事件扩展 — 补齐 existing UnifiedStreamEvent
// =============================================================================

/** 扩展 UnifiedStreamEvent 以包含手递手和文件访问类型 */
export type HandoffUnifiedStreamEvent =
  | UnifiedStreamEvent
  | { stream_type: "file_access"; data: FileAccessEvent }
  | { stream_type: "handoff_observability"; data: HandoffObservabilityPayload };

// =============================================================================
// 3. 客户端聚合状态 — 存入现有 zustand AppStore
// =============================================================================

/** 委托树节点 — 从 HANDOFF_SPAWN / HANDOFF_COMPLETE 对增量构建 */
export interface DelegationTreeNode {
  node_id: string;
  agent_id: string;
  parent_node_id: string | null;
  chain_depth: number;
  spawned_at_ms: number;
  completed_at_ms: number | null;

  // 聚合指标
  bytes_in: number;
  bytes_out: number;
  token_count: number;
  context_overhead_ratio: number; // injected / budget (0–1)
  child_count: number;
  total_duration_ms: number;

  // 告警计数 (用于树节点角标)
  alert_counts: Record<string, number>;
}

/** 手递手仪表板聚合状态 */
export interface HandoffDashboardState {
  delegation_tree: DelegationTreeNode[];
  root_node_id: string | null;

  // 活跃文件访问 (最近 200 条, 环形缓冲)
  recent_file_accesses: FileAccessEvent[];

  // 关键指标
  fanout_ratio: number;   // spawned / completed (Handoff #13)
  max_chain_depth: number;
  orphaned_count: number;
  total_denials: number;
  avg_saturation: number;
}

// =============================================================================
// 4. 组件 Props — 对齐现有模式 (内联类型)
// =============================================================================

/** Timeline 行: 文件访问事件 */
export interface FileAccessTimelineRowProps {
  event: FileAccessEvent;
  isHighlighted: boolean;
  isCrossBoundary: boolean;    // workspace_tag === "outside"
  onSelect: (event: FileAccessEvent) => void;
  onAgentClick: (agentId: string) => void;
}

/** Timeline 行: 手递手生命周期 */
export interface HandoffLifecycleRowProps {
  agentId: string;
  parentAgentId: string;
  chainDepth: number;
  phases: { dispatch_ms: number; context_inject_ms: number; execute_ms: number; collect_ms: number };
  totalMs: number;
  status: "running" | "completed" | "timeout" | "error";
  isHighlighted: boolean;
  onSelect: () => void;
}

/** 委托树视图 — 替代/补充当前 globe 视图 (panelLevel=0) */
export interface DelegationTreeViewProps {
  nodes: DelegationTreeNode[];
  rootNodeId: string;
  selectedNodeId: string | null;
  currentTimeMs: number;       // 对应 TimelineScrubber 位置
  layoutMode: "tree" | "radial";
  onNodeSelect: (nodeId: string) => void;
  onTimeChange: (ms: number) => void;
}

/** 节点详情面板 — panelLevel=1 时展示 */
export interface DelegationNodeDetailProps {
  node: DelegationTreeNode;
  fileAccesses: FileAccessEvent[];    // 该代理的所有文件访问
  lifecycleTimings: HandoffLifecycleRowProps[];
  alertItems: HandoffAlertItem[];
  onClose: () => void;
  onTraceback: (nodeId: string) => void; // 进入 panelLevel=2
}

/** 告警项 — 用于侧栏 feed 和节点详情 */
export interface HandoffAlertItem {
  id: string;
  timestamp_ns: number;
  sub_type: HandoffObsSubType;
  severity: AlertSeverity;
  agent_id: string;
  chain_depth: number;
  title: string;              // 告警摘要
  summary: string;            // 详细描述
  source_paths: string[];     // 相关文件路径
  onFocusAgent: (agentId: string) => void;
  onDismiss: () => void;
}

/** 过滤器栏 (新增 props, 与现有 TimelineScrubber 并列) */
export interface HandoffFilterBarProps {
  showCrossBoundary: boolean;    // PLATFORM-0042: 仅客户端过滤
  showFileAccess: boolean;
  showLifecycle: boolean;
  showAlerts: boolean;
  chainDepthFilter: number | null; // null = 显示全部
  onToggleCrossBoundary: (v: boolean) => void;
  onToggleFileAccess: (v: boolean) => void;
  onToggleLifecycle: (v: boolean) => void;
  onToggleAlerts: (v: boolean) => void;
  onSetChainDepthFilter: (depth: number | null) => void;
}

// =============================================================================
// 5. Type Guard
// =============================================================================

export function isFileAccessEvent(
  event: HandoffUnifiedStreamEvent
): event is { stream_type: "file_access"; data: FileAccessEvent } {
  return event.stream_type === "file_access";
}

export function isHandoffObservabilityEvent(
  event: HandoffUnifiedStreamEvent
): event is { stream_type: "handoff_observability"; data: HandoffObservabilityPayload } {
  return event.stream_type === "handoff_observability";
}
