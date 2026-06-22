// ═══════════════════════════════════════════════════════════════
// Handoff Dashboard — Frontend Data Contract v3 (Subagent 3)
// 输入: shared_handoff.md + Subagent 2 HandoffObservabilityAnalyzer 设计
// 对齐: lib/data-contract.ts · lib/types.ts · 现有组件模式
//
// Subagent 2 输出信封:
//   { source: "handoff_observability", event: "<SIGNAL>", severity, rule_id,
//     agent: { root_pid, depth, agent_id },
//     delegation_tree: { parent_root_pid, depth, siblings },
//     evidence: { ... }, source_event: { source, event } }
// ═══════════════════════════════════════════════════════════════

import type { AnomalySeverity } from "./data-contract";

// ═══════════════════════════════════════════════════════════════
// 一、文件访问事件（Feature #1-3 + PLATFORM-0042）
//    消费 Subagent 2 打标的 file_access 事件
// ═══════════════════════════════════════════════════════════════

export interface FileAccessEvent {
  access_id: string;
  timestamp_ns: number;
  syscall: "open" | "openat";
  target_path: string;
  flags: number;
  pid: number;
  comm: string;

  // PLATFORM-0042: 工作空间边界（上游 FileAccessAuditAnalyzer 提供）
  workspace_tag: "inside" | "outside" | "boundary-ambiguous";

  // Feature #1, #4: Subagent 2 关联标记
  agent_id: string;
  chain_depth: number;                            // N=0 根, N+1 每层
  handoff_phase: HandoffPhase;
  tool_call_id: string | null;

  severity: "info" | "warn";                      // PLATFORM-0042 §2: outside → warn
}

export type HandoffPhase = "dispatch" | "context_inject" | "execute" | "collect";

// ═══════════════════════════════════════════════════════════════
// 二、手递手可观测性事件（Handoff #1-15 + Subagent1 扩展）
//    消费 Subagent 2 source="handoff_observability" 事件
// ═══════════════════════════════════════════════════════════════

export type HandoffObsSignal =
  // 拓扑
  | "HANDOFF_SPAWN"         // Feature #5, #9: 子代理生成
  | "HANDOFF_COMPLETE"      // Feature #5, #9: 子代理完成（含子树快照）
  // 阶段标记
  | "FILE_CORRELATION"      // Feature #1: 文件访问 ↔ agent tool-call
  | "CONTEXT_SATURATION"    // Feature #7: 上下文开销 >60%
  | "TOOL_DENIED"           // Feature #8: 策略门控拒绝
  | "BOUNDARY_TAG"          // PLATFORM-0042 + inject ws§1: workspace_tag 传递
  // 告警
  | "CONTEXT_CONFLICT"      // Feature #6: 子代理指令与根约束冲突
  | "PROMPT_DRIFT"          // Feature #11: 语义嵌入漂移
  | "FANOUT_STORM"          // Feature #13: spawn/complete > 3:1
  | "DATA_LEAK"             // Feature #14: 跨代理文件泄漏
  | "STRATEGY_DIVERGENCE"   // Feature #15: 工具序列偏离 >2σ
  | "ORPHANED_WORK"         // Feature #10: 输出从未被消费
  | "CORRECTIVE_LOOP"       // inject ext§2: 3+ 次纠正重分派
  | "RESULT_STALENESS"      // subagent1 #2: 结果在最新变更前生成
  | "SUBMODEL_ASSIGNMENT";  // subagent1 #1: 模型路由不匹配

/** Subagent 2 输出信封 */
export interface HandoffObsEnvelope {
  source: "handoff_observability";
  event: HandoffObsSignal;
  severity: AnomalySeverity;
  rule_id: string;
  agent: {
    root_pid: number;
    depth: number;
    agent_id: string;
  };
  delegation_tree: {
    parent_root_pid: number | null;
    depth: number;
    siblings: number[];
  };
  evidence: HandoffEvidence;
  source_event: {
    source: string;
    event: string;
  };
}

/** 按信号类型区分的证据载荷 */
export type HandoffEvidence =
  // HANDOFF_SPAWN
  | { signal: "HANDOFF_SPAWN"; spawn_id: string; parent_spawn_id: string | null; pid: number; comm: string; dispatched_at_ns: number }
  // HANDOFF_COMPLETE
  | { signal: "HANDOFF_COMPLETE"; record: HandoffCompletionRecord; subtree_snapshot: SubtreeSnapshot }
  // FILE_CORRELATION
  | { signal: "FILE_CORRELATION"; correlation_id: string; file_access_id: string; tool_call_id: string; tool_name: string; target_path: string }
  // CONTEXT_SATURATION
  | { signal: "CONTEXT_SATURATION"; spawn_id: string; injected_bytes: number; budget_bytes: number; overhead_ratio: number }
  // TOOL_DENIED
  | { signal: "TOOL_DENIED"; denial_id: string; tool_name: string; policy_rule_id: string; denial_reason: string }
  // BOUNDARY_TAG
  | { signal: "BOUNDARY_TAG"; access_id: string; target_path: string; workspace_tag: string; tag_preserved: boolean }
  // CONTEXT_CONFLICT
  | { signal: "CONTEXT_CONFLICT"; child_agent_id: string; parent_agent_id: string; conflicting_instruction: string; parent_directive: string }
  // PROMPT_DRIFT
  | { signal: "PROMPT_DRIFT"; drift_score: number; drift_severity: "low" | "medium" | "high" | "critical"; root_task_hash: string }
  // FANOUT_STORM
  | { signal: "FANOUT_STORM"; spawn_rate: number; completion_rate: number; ratio: number; window_s: number }
  // DATA_LEAK
  | { signal: "DATA_LEAK"; leaking_agent_id: string; accessing_agent_id: string; exposed_path: string; content_hash: string }
  // STRATEGY_DIVERGENCE
  | { signal: "STRATEGY_DIVERGENCE"; diverged_agent_id: string; sibling_consensus: string[]; diverged_sequence: string[]; deviation_sigmas: number }
  // ORPHANED_WORK
  | { signal: "ORPHANED_WORK"; artifact_id: string; producer_agent_id: string; artifact_path: string; idle_since_ns: number }
  // CORRECTIVE_LOOP
  | { signal: "CORRECTIVE_LOOP"; parent_agent_id: string; task_scope: string; corrective_spawns: number }
  // RESULT_STALENESS
  | { signal: "RESULT_STALENESS"; artifact_id: string; produced_at_ns: number; workspace_mutated_at_ns: number }
  // SUBMODEL_ASSIGNMENT
  | { signal: "SUBMODEL_ASSIGNMENT"; assigned_model: string; optimal_model: string; task_category: string };

export interface HandoffCompletionRecord {
  spawn_id: string;
  parent_spawn_id: string | null;
  agent_id: string;
  depth: number;
  bytes_in: number;
  bytes_out: number;
  token_count: number;
  phase_latencies_ns: PhaseLatencies;
  context_saturation: number;       // 0.0–1.0
  wall_clock_total_ns: number;
  model_used: string | null;
}

export interface PhaseLatencies {
  dispatch_ns: number;
  context_inject_ns: number | null;
  execute_ns: number | null;
  collect_ns: number | null;
}

export interface SubtreeSnapshot {
  nodes: SnapshotNode[];
  edges: SnapshotEdge[];
  root_spawn_id: string;
  snapshot_at_ns: number;
}

export interface SnapshotNode {
  spawn_id: string;
  agent_id: string;
  depth: number;
  dispatched_at_ns: number;
  completed_at_ns: number | null;
  model_used: string | null;
}

export interface SnapshotEdge {
  from_spawn_id: string;
  to_spawn_id: string;
}

// ═══════════════════════════════════════════════════════════════
// 三、委托树（客户端增量构建 + Feature #9 时间索引树可视化）
//    从 HANDOFF_SPAWN ↔ HANDOFF_COMPLETE 事件对构建
// ═══════════════════════════════════════════════════════════════

export interface DelegationNode {
  node_id: string;
  agent_id: string;
  parent_node_id: string | null;
  chain_depth: number;

  spawned_at_ns: number;
  completed_at_ns: number | null;

  // Feature #5: 成本归因
  bytes_in: number;
  bytes_out: number;
  token_count: number;

  // Feature #7: 上下文饱和度
  context_overhead_ratio: number;

  // Feature #12: 空闲归因
  idle_breakdown: IdleBreakdown;

  // Subagent1 #1: 模型保真度
  model_assigned: string | null;
  model_optimal: string | null;

  children: DelegationNode[];
}

export interface IdleBreakdown {
  queued_awaiting_dispatch_ms: number;
  context_injection_io_ms: number;
  model_inference_queueing_ms: number;
  tool_call_round_trip_ms: number;
}

// ═══════════════════════════════════════════════════════════════
// 四、仪表盘聚合快照
//    客户端状态：从事件流增量聚合
// ═══════════════════════════════════════════════════════════════

export interface HandoffDashboardState {
  delegation_tree: DelegationNode | null;
  file_access_by_depth: Record<number, FileAccessEvent[]>;
  recent_obs_events: HandoffObsEnvelope[];    // 最近 N 条可观测性事件
  recent_denials: HandoffObsEnvelope[];        // TOOL_DENIED 类型
  metrics: HandoffMetrics;
}

export interface HandoffMetrics {
  total_spawned: number;
  total_completed: number;
  active_count: number;
  max_chain_depth: number;
  fan_out_ratio: number;                // Feature #13: spawned / completed
  fan_out_critical: boolean;            // ratio > 3:1
  avg_context_overhead: number;         // Feature #7
  orphaned_artifacts: number;           // Feature #10
  workspace_tag_survival_rate: number;  // inject ws§3
}

// ═══════════════════════════════════════════════════════════════
// 五、组件 Props（遵循现有模式：inline type annotation 优先）
//    所有组件均为 'use client'，通过 useAppStore 消费状态
// ═══════════════════════════════════════════════════════════════

// ── 文件访问时间线行（Feature #1-3 + PLATFORM-0042）
//    扩展现有 TimelinePanel 的 event_type 匹配，新增 'FileAccess' 变体
export interface FileAccessTimelineRowProps {
  access: FileAccessEvent;
  is_highlighted: boolean;
  highlight_agent_id: string | null;
  onSelect: (access: FileAccessEvent) => void;
  /** PLATFORM-0042 §3: 客户端过滤开关，不过滤后端数据 */
  show_cross_boundary?: boolean;
}

// ── 委派树面板（Feature #9: 时间索引树）
//    替代/补充现有 ProcessTreeView
export interface DelegationTreePanelProps {
  tree: DelegationNode | null;
  metrics: HandoffMetrics;
  selected_node_id: string | null;
  onSelectNode: (nodeId: string) => void;
  /** null=实时, 值=回放到指定时间点 */
  replay_at_ns: number | null;
  color_by_depth?: boolean;
}

// ── 委派节点详情面板（Feature #3, #5, #12）
//    点击树节点时在 DetailPanel 中展示
export interface DelegationNodeDetailProps {
  node: DelegationNode;
  completion: HandoffCompletionRecord | null;
  file_accesses: FileAccessEvent[];
  obs_events: HandoffObsEnvelope[];
  denials: HandoffObsEnvelope[];
  onClose: () => void;
}

// ── 手递手告警卡片
//    每个 HandoffObsEnvelope (severity≥warn) 的渲染卡片
export interface HandoffAlertCardProps {
  envelope: HandoffObsEnvelope;
  onDismiss: () => void;
  onNavigate: (agentId: string) => void;
}

// ── 上下文饱和度仪表（Feature #7）
//    inline 图表，不依赖重型图表库
export interface SaturationGaugeProps {
  overhead_ratio: number;           // 0.0–1.0
  injected_bytes: number;
  budget_bytes: number;
  warn_threshold?: number;          // 默认 0.6
  critical_threshold?: number;      // 默认 0.85
  compact?: boolean;                // 缩略模式用于树节点 hover
}

// ── 阶段延迟堆叠图（Feature #12）
//    四个阶段: dispatch → context_inject → execute → collect
export interface PhaseLatencyChartProps {
  latencies: PhaseLatencies;
  peer_average?: PhaseLatencies;    // 同深度兄弟节点平均, null=无对比数据
  compact?: boolean;
}

// ── Fan-out 监控面板（Feature #13）
//    spawn/complete 比率 + 滑动窗口历史
export interface FanOutMonitorProps {
  metrics: HandoffMetrics;
  history: Array<{
    timestamp_ns: number;
    spawned: number;
    completed: number;
    ratio: number;
  }>;
  critical_ratio?: number;          // 默认 3.0
}

// ── 工具门控审计日志（Feature #8）
//    不可变拒绝记录专用面板
export interface ToolGateAuditLogProps {
  denials: HandoffObsEnvelope[];    // TOOL_DENIED 类型
  filter_rule_id?: string;
  filter_agent_id?: string;
  onSelectDenial: (denialId: string) => void;
}

// ═══════════════════════════════════════════════════════════════
// 六、SSE 流扩展
//    在现有 UnifiedStreamEvent（data-contract.ts）基础上新增
// ═══════════════════════════════════════════════════════════════

/** Subagent 2 源 → 前端路由 */
export type HandoffStreamEvent =
  | { stream_type: "handoff_observability"; data: HandoffObsEnvelope }
  | { stream_type: "file_access"; data: FileAccessEvent };

// ═══════════════════════════════════════════════════════════════
// 七、类型守卫
// ═══════════════════════════════════════════════════════════════

export function isHandoffObsEnvelope(
  e: HandoffStreamEvent
): e is { stream_type: "handoff_observability"; data: HandoffObsEnvelope } {
  return e.stream_type === "handoff_observability";
}

export function isFileAccessEvent(
  e: HandoffStreamEvent
): e is { stream_type: "file_access"; data: FileAccessEvent } {
  return e.stream_type === "file_access";
}

/** 按 HandoffObsSignal 过滤信封 */
export function isObsSignal<T extends HandoffObsSignal>(
  envelope: HandoffObsEnvelope,
  signal: T
): envelope is HandoffObsEnvelope & { event: T } {
  return envelope.event === signal;
}
