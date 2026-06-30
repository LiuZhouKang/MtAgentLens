// ═══════════════════════════════════════════════════════════════
// Subagent 3 — Frontend Data Contract: 工作空间边界 + 跨边界事件
// 输入: shared_handoff_supplement.md (产品团队补充需求 A/B/C)
//      + Subagent 2 HandoffObservabilityAnalyzer 设计
//      + shared_handoff.md (MtAgentLens 可观测性综述)
//
// 对齐: lib/data-contract.ts (UnifiedStreamEvent)
//       lib/types.ts (AnomalySeverity)
//       lib/handoff-observability-contract.ts (HandoffObsEnvelope)
//
// 本文件仅覆盖产品团队补充需求的 3 个新增项目:
//   A: workspace_boundary 标签传递
//   B: cross_boundary_read 告警事件
//   C: allow_filter: false 防过滤约束
// ═══════════════════════════════════════════════════════════════

import type { AnomalySeverity } from "./data-contract";
import type { HandoffObsEnvelope } from "./handoff-dashboard-contract";

// ═══════════════════════════════════════════════════════════════
// A. 工作空间边界标记 (补充需求 §A)
//    所有文件事件必须包含 workspace_boundary 标记
//    用于区分设计产出归属到正确的工作空间范围
// ═══════════════════════════════════════════════════════════════

/** 工作空间边界分类 (PLATFORM-0042 + 补充需求 §A) */
export type WorkspaceBoundary = "inside" | "outside";

/** 给现有 FileAccessWithHandoff 增加补充需求 §A 要求的字段 */
export interface WorkspaceBoundaryTag {
  /** 事件元数据中的 workspace_boundary 标记 */
  workspace_boundary: boolean;          // 补充需求 §A: 必须为 true
  /** 目标文件路径 */
  target_path: string;
  /** 产生该事件的代理 ID */
  agent_id: string;
  /** 工作空间归属判定 */
  boundary: WorkspaceBoundary;
}

/** 工作空间边界统计 — 用于 DashboardShell 中的边界状态摘要条 */
export interface WorkspaceBoundaryStats {
  total_events: number;
  inside_count: number;
  outside_count: number;
  /** 标记被保留下来的事件数 (补充需求 §C: 未被过滤层丢弃的事件) */
  tag_preserved_count: number;
  /** 标记丢失率 (< 100% 表示过滤层违规丢弃了边界事件) */
  tag_drop_rate: number;
}

// ═══════════════════════════════════════════════════════════════
// B. 跨边界读取事件 (补充需求 §B)
//    当子代理读取上游子代理的输出时，必须发出
//    WARN 级别事件，category="cross_boundary_read"
// ═══════════════════════════════════════════════════════════════

/** 跨边界读取事件 — 补充需求 §B 的直接映射 */
export interface CrossBoundaryReadEvent {
  /** 事件唯一 ID */
  event_id: string;
  /** epoch 毫秒时间戳 */
  timestamp_ms: number;
  /** 事件类别，固定值 */
  category: "cross_boundary_read";
  /** 补充需求 §B: WARN 级别 */
  level: "WARN";

  /** 发起读取的代理 ID */
  reader_agent_id: string;
  /** 被读取源头的代理 ID (上游子代理) */
  source_agent_id: string;
  /** 被读取的目标文件路径 */
  target_file_path: string;

  /** 读取方在委托链中的深度 */
  reader_depth: number;
  /** 源代理在委托链中的深度 */
  source_depth: number;

  /** 该跨边界读取是否跨越了工作空间边界 */
  is_cross_workspace: boolean;

  /** 补充需求 §C: 该事件必须绕过过滤层 */
  allow_filter: false;
}

/** 跨边界读取时序数据 — 用于时间线面板渲染 */
export interface CrossBoundaryTimelineEntry {
  event: CrossBoundaryReadEvent;
  /** 源代理与读取方代理的关系: sibling / parent_child / unrelated */
  relation: "sibling" | "parent_child" | "unrelated";
  /** 读取发生时的 handoff 阶段 */
  phase: "dispatch" | "context_inject" | "execute" | "collect";
}

// ═══════════════════════════════════════════════════════════════
// C. 反过滤约束元数据 (补充需求 §C)
//    所有跨边界事件的 allow_filter 必须设为 false
//    防止安全流水线意外丢弃关键的可追溯性数据
// ═══════════════════════════════════════════════════════════════

/** 事件过滤层元数据 — 附加到所有跨边界事件 */
export interface AntiFilteringMetadata {
  /** 补充需求 §C: 永远为 false，禁止过滤 */
  allow_filter: false;
  /** 标记来源: 补充需求 §C */
  constraint_source: "handoff_supplement_req_c";
  /** 事件被标记的时间 */
  tagged_at_ms: number;
}

/** 过滤层完整性快照 — 仪表盘实时监控 */
export interface FilterIntegritySnapshot {
  /** 最近一秒内通过过滤层的跨边界事件数 */
  boundary_events_passed: number;
  /** 最近一秒内被过滤层拦截的跨边界事件数 (应为 0) */
  boundary_events_blocked: number;
  /** 过滤层是否遵守了 allow_filter=false 约束 */
  constraint_violated: boolean;
  /** 被违规丢弃的事件 ID 列表 */
  dropped_event_ids: string[];
}

// ═══════════════════════════════════════════════════════════════
// 2. SSE 流扩展 — 三个新 stream_type
//    与现有 data-contract.ts + handoff-observability-contract.ts 共存
// ═══════════════════════════════════════════════════════════════

/** 三个补充需求对应的 SSE 事件 */
export type HandoffBoundaryStreamEvent =
  | { stream_type: "workspace_boundary_tag"; data: WorkspaceBoundaryTag }
  | { stream_type: "cross_boundary_read"; data: CrossBoundaryReadEvent }
  | { stream_type: "filter_integrity"; data: FilterIntegritySnapshot };

// ═══════════════════════════════════════════════════════════════
// 3. 组件 Props
//    遵循现有模式: 'use client' 组件, 通过 zustand store 消费状态
// ═══════════════════════════════════════════════════════════════

// ── 工作空间边界状态栏 (DashboardShell 顶部/侧栏)
//    显示当前边界事件统计和过滤层健康状况
export interface WorkspaceBoundaryStatusBarProps {
  stats: WorkspaceBoundaryStats;
  integrity: FilterIntegritySnapshot;
  /** 是否有任何跨边界违规 */
  has_violation: boolean;
  onExpand: () => void;          // 点击展开详细面板
}

// ── 跨边界读取事件卡片 (告警 feed 或侧栏)
//    每个 CrossBoundaryReadEvent 的渲染卡片
export interface CrossBoundaryReadCardProps {
  event: CrossBoundaryReadEvent;
  relation: "sibling" | "parent_child" | "unrelated";
  /** 点击 source agent 导航到委托树对应节点 */
  onNavigateToSource: (agentId: string) => void;
  /** 点击 reader agent 导航到委托树对应节点 */
  onNavigateToReader: (agentId: string) => void;
  onDismiss: () => void;
}

// ── 跨边界读取时间线行
//    在现有 TimelinePanel 中作为新行类型注册
export interface CrossBoundaryTimelineRowProps {
  entry: CrossBoundaryTimelineEntry;
  isHighlighted: boolean;
  onSelect: (event: CrossBoundaryReadEvent) => void;
  onNavigateToAgent: (agentId: string) => void;
}

// ── 过滤层完整性面板
//    实时监控过滤层是否违规丢弃跨边界事件
export interface FilterIntegrityPanelProps {
  integrity: FilterIntegritySnapshot;
  /** 历史数据点 (最近 60 秒, 每秒一个快照) */
  history: FilterIntegritySnapshot[];
  /** 违规丢弃的详细列表 */
  droppedEvents: CrossBoundaryReadEvent[];
  onInvestigate: (eventId: string) => void;
}

// ── 工作空间边界散点图
//    按深度分组的 inside/outside 文件访问比例
export interface WorkspaceBoundaryScatterProps {
  /** 每个代理的边界统计 */
  agents: Array<{
    agent_id: string;
    depth: number;
    inside_count: number;
    outside_count: number;
    /** outside 比例是否异常 (> 兄弟节点均值 + 1.5σ) */
    is_anomalous: boolean;
  }>;
  selectedAgentId: string | null;
  onSelectAgent: (agentId: string) => void;
}

// ── 阶段一侧边栏中的边界过滤器开关 (PLATFORM-0042 §3)
//    客户端过滤，不修改后端数据流
export interface BoundaryFilterToggleProps {
  /** 是否显示跨边界事件 */
  showCrossBoundary: boolean;
  onToggle: (show: boolean) => void;
  /** 跨边界事件总数 */
  crossBoundaryCount: number;
  /** 是否检测到过滤层违规 */
  hasIntegrityViolation: boolean;
}

// ═══════════════════════════════════════════════════════════════
// 4. 与现有 HandoffObsEnvelope 的集成
//    当 HandoffObsEnvelope.event === "BOUNDARY_TAG" 时，
//    其 evidence 载荷映射到 WorkspaceBoundaryTag
// ═══════════════════════════════════════════════════════════════

/** 将 HandoffObsEnvelope (BOUNDARY_TAG 信号) 转换为 WorkspaceBoundaryTag */
export function toWorkspaceBoundaryTag(
  envelope: HandoffObsEnvelope & { event: "BOUNDARY_TAG" }
): WorkspaceBoundaryTag {
  const ev = envelope.evidence;
  // ev 是 { signal: "BOUNDARY_TAG"; access_id; target_path; workspace_tag; tag_preserved }
  return {
    workspace_boundary: true, // 补充需求 §A: 始终为 true
    target_path: (ev as any).target_path,
    agent_id: envelope.agent.agent_id,
    boundary: (ev as any).workspace_tag === "outside" ? "outside" : "inside",
  };
}

// ═══════════════════════════════════════════════════════════════
// 5. 类型守卫
// ═══════════════════════════════════════════════════════════════

export function isCrossBoundaryRead(
  event: HandoffBoundaryStreamEvent
): event is { stream_type: "cross_boundary_read"; data: CrossBoundaryReadEvent } {
  return event.stream_type === "cross_boundary_read";
}

export function isWorkspaceBoundaryTag(
  event: HandoffBoundaryStreamEvent
): event is { stream_type: "workspace_boundary_tag"; data: WorkspaceBoundaryTag } {
  return event.stream_type === "workspace_boundary_tag";
}

export function isFilterIntegrity(
  event: HandoffBoundaryStreamEvent
): event is { stream_type: "filter_integrity"; data: FilterIntegritySnapshot } {
  return event.stream_type === "filter_integrity";
}

/** 检查 HandoffObsEnvelope 的事件是否有 allow_filter=false 约束 */
export function hasAntiFilteringConstraint(
  envelope: HandoffObsEnvelope
): boolean {
  // 补充需求 §C: 所有跨边界事件不允许过滤
  const crossBoundarySignals = [
    "BOUNDARY_TAG",
    "DATA_LEAK",
    "BOUNDARY_DRIFT",
  ];
  return crossBoundarySignals.includes(envelope.event);
}
