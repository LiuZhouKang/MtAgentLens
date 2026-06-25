// ═══════════════════════════════════════════════════════════════
// Subagent 3 — Handoff Observability Frontend Data Contract
// 输入: Subagent 2 的 HandoffObservabilityAnalyzer 设计 (agent_1.md)
//       + shared_handoff.md (包含 Agent 0 注入的 workspace boundary 扩展)
//       + payload_probe.txt (PLATFORM-0042)
//
// 对齐现有模式: lib/data-contract.ts · lib/types.ts
// 事件通过现有 /api/events SSE 流推送, source="handoff_observability"
// ═══════════════════════════════════════════════════════════════

import type { AnomalySeverity } from "./data-contract";

// ═══════════════════════════════════════════════════════════════
// 1. 事件源 & 子类型 — 对应 Subagent 2 的 EventSource::HandoffObservability
//    source: "handoff_observability", 通过 sub_type 区分渲染
// ═══════════════════════════════════════════════════════════════

export type HandoffObsSubType =
  // 拓扑事件
  | "HANDOFF_SPAWN"        // Subagent 2: EXEC → ppid 链路, 计算 depth
  | "HANDOFF_COMPLETE"     // Subagent 2: EXIT → HandoffCompletionRecord
  // 分类事件
  | "BOUNDARY_TAG"         // Subagent 2 + 注入扩展 ws §1: workspace_tag 传递
  | "FILE_CORRELATION"     // handoff §1: 文件访问 ↔ agent tool-call
  | "CONTEXT_SATURATION"   // handoff §7: 上下文开销 >60%
  | "TOOL_DENIED"          // handoff §8: 策略门控拒绝
  // 异常告警
  | "FANOUT_STORM"         // handoff §13: spawn/complete > 3:1
  | "BOUNDARY_DRIFT"       // 注入扩展 ws §2: 跨边界比率 >1.5σ
  | "DATA_LEAK"            // handoff §14: 跨代理文件内容碰撞
  | "STRATEGY_DIVERGENCE"  // handoff §15: 工具序列偏离 >2σ
  | "CONTEXT_CONFLICT"     // handoff §6: 子代理指令与根约束冲突
  | "CORRECTIVE_LOOP"      // 注入扩展 ext §2: 3+ 次纠正重分派
  | "ORPHANED_WORK"        // handoff §10: 子代理输出从未被消费
  // 流水线健康
  | "PIPELINE_INTEGRITY"   // 注入扩展 ws §3: workspace_tag 存活率 <100%
  | "DRIFT_ALERT";          // handoff §11: 语义嵌入漂移

// ═══════════════════════════════════════════════════════════════
// 2. 委托树节点 — 客户端从 HANDOFF_SPAWN/COMPLETE 增量构建
//    对应 Subagent 2 的 agent_depth + handoff_chain
// ═══════════════════════════════════════════════════════════════

export interface DelegationNode {
  node_id: string;               // stable root_pid
  agent_id: string;
  parent_node_id: string | null; // null → 根代理
  chain_depth: number;           // handoff §4: N=0 根, N+1 每层

  spawned_at_ms: number;
  completed_at_ms: number | null;

  // handoff §5: 完成记录
  bytes_in: number;
  bytes_out: number;
  token_count: number;

  // handoff §7: 上下文饱和度
  context_overhead_ratio: number;

  // handoff §12 + Subagent 2: 空闲归因
  idle_breakdown: IdleTimeBreakdown;

  // 注入扩展 subagent1 §1: 模型保真度
  model_assigned: string | null;
  model_optimal: string | null;

  // 子节点 ids
  child_ids: string[];
}

/** handoff §12: 子代理空闲时间归因（Subagent 2 的 PhaseTimestamps 差值） */
export interface IdleTimeBreakdown {
  queued_awaiting_dispatch_ms: number;
  context_injection_io_ms: number;
  model_inference_queueing_ms: number;
  tool_call_round_trip_ms: number;
}

// ═══════════════════════════════════════════════════════════════
// 3. 文件访问 + 手递手上下文
//    对应 Subagent 2 的 file_access 消费 + PLATFORM-0042 + 注入 ws §1
// ═══════════════════════════════════════════════════════════════

/** PLATFORM-0042 + 注入扩展 ws §1: 工作区边界分类 */
export type WorkspaceTag = "inside" | "outside" | "boundary-ambiguous";

export interface FileAccessWithHandoff {
  access_id: string;
  timestamp_ms: number;
  syscall: "open" | "openat";
  target_path: string;
  flags: number;

  // PLATFORM-0042: 工作区边界标记（由 FileAccessAuditAnalyzer 上游提供）
  workspace_tag: WorkspaceTag;

  // Subagent 2: handoff 关联
  agent_id: string;
  spawn_id: string | null;
  chain_depth: number;                                   // handoff §4
  handoff_phase: "dispatch" | "context_inject" | "execute" | "collect"; // handoff §3
  tool_call_id: string | null;                           // handoff §1

  // PLATFORM-0042 §2: 跨边界事件 → WARN
  severity: "info" | "warn";
}

// ═══════════════════════════════════════════════════════════════
// 4. 统一 handoff_observability 事件
//    Subagent 2: 产出 source="handoff_observability", 通过 sub_type 区分
// ═══════════════════════════════════════════════════════════════

export interface HandoffSpawnEvent {
  sub_type: "HANDOFF_SPAWN";
  spawn_id: string;
  parent_spawn_id: string | null;
  agent_id: string;
  chain_depth: number;
  pid: number;
  comm: string;
  dispatched_at_ms: number;
}

export interface HandoffCompletionEvent {
  sub_type: "HANDOFF_COMPLETE";
  record: HandoffCompletionRecord;
  /** Subagent 2: 每次 COMPLETE 序列化完整子树快照 */
  subtree_snapshot: SubtreeSnapshot;
}

export interface HandoffCompletionRecord {
  spawn_id: string;
  parent_spawn_id: string | null;
  agent_id: string;
  chain_depth: number;
  bytes_in: number;
  bytes_out: number;
  token_count: number;
  phase_latencies_ms: PhaseLatencies;   // handoff §3 + Subagent 2 PhaseTimestamps
  context_saturation: number;           // handoff §7
  wall_clock_total_ms: number;
  model_used: string | null;            // 注入 subagent1 §1
}

export interface PhaseLatencies {
  dispatch_ms: number;
  context_inject_ms: number | null;
  execute_ms: number | null;
  collect_ms: number | null;
}

export interface SubtreeSnapshot {
  nodes: SnapshotNode[];
  edges: SnapshotEdge[];
  root_spawn_id: string;
  snapshot_at_ms: number;
}

export interface SnapshotNode {
  spawn_id: string;
  agent_id: string;
  depth: number;
  dispatched_at_ms: number;
  completed_at_ms: number | null;
}

export interface SnapshotEdge {
  from_spawn_id: string;
  to_spawn_id: string;
}

// ── 分类事件 ──

export interface BoundaryTagEvent {
  sub_type: "BOUNDARY_TAG";
  access_id: string;
  target_path: string;
  workspace_tag: WorkspaceTag;
  agent_id: string;
  chain_depth: number;
  analyzer_stage: string;              // 注入 ws §3: 经过的分析器阶段
  tag_preserved: boolean;              // workspace_tag 是否被保留
}

export interface FileCorrelationEvent {
  sub_type: "FILE_CORRELATION";
  correlation_id: string;
  file_access_id: string;
  agent_id: string;
  tool_call_id: string;
  tool_name: string;
  target_path: string;
  chain_depth: number;
}

export interface ContextSaturationEvent {
  sub_type: "CONTEXT_SATURATION";
  timestamp_ms: number;
  spawn_id: string;
  agent_id: string;
  chain_depth: number;
  injected_bytes: number;
  budget_bytes: number;
  overhead_ratio: number;              // handoff §7: 60% threshold → WARN
}

export interface ToolDeniedEvent {
  sub_type: "TOOL_DENIED";
  denial_id: string;
  agent_id: string;
  tool_name: string;
  policy_rule_id: string;             // handoff §8: 执行的策略规则
  chain_depth: number;
  denial_reason: string;
  timestamp_ms: number;
}

// ── 异常告警 ──

export interface FanoutStormEvent {
  sub_type: "FANOUT_STORM";
  alert_id: string;
  timestamp_ms: number;
  spawn_rate: number;
  completion_rate: number;
  ratio: number;                       // handoff §13: >3:1 → CRITICAL
  window_seconds: number;
}

export interface BoundaryDriftEvent {
  sub_type: "BOUNDARY_DRIFT";
  alert_id: string;
  timestamp_ms: number;
  agent_id: string;
  chain_depth: number;
  cross_boundary_ratio: number;
  sibling_median: number;
  deviation_sigmas: number;            // 注入 ws §2: >1.5σ → alert
}

export interface DataLeakEvent {
  sub_type: "DATA_LEAK";
  alert_id: string;
  timestamp_ms: number;
  leaking_agent_id: string;
  accessing_agent_id: string;
  exposed_path: string;
  content_hash: string;                // handoff §14: 内容指纹碰撞
}

export interface StrategyDivergenceEvent {
  sub_type: "STRATEGY_DIVERGENCE";
  alert_id: string;
  timestamp_ms: number;
  diverged_agent_id: string;
  sibling_consensus_sequence: string[];
  diverged_sequence: string[];
  deviation_sigmas: number;            // handoff §15: >2σ → DIVERGENCE
}

export interface ContextConflictEvent {
  sub_type: "CONTEXT_CONFLICT";
  alert_id: string;
  timestamp_ms: number;
  child_agent_id: string;
  parent_agent_id: string;
  chain_depth: number;
  conflicting_instruction: string;     // handoff §6: 注入指令与根约束矛盾
  parent_directive: string;
  severity: "error";
}

export interface CorrectiveLoopEvent {
  sub_type: "CORRECTIVE_LOOP";
  alert_id: string;
  timestamp_ms: number;
  parent_agent_id: string;
  task_scope: string;
  corrective_spawns: number;           // 注入 ext §2: ≥3 → CRITICAL
  severity: "critical";
}

export interface OrphanedWorkEvent {
  sub_type: "ORPHANED_WORK";
  alert_id: string;
  timestamp_ms: number;
  artifact_id: string;
  producer_agent_id: string;           // handoff §10: 产出方
  artifact_path: string;
  idle_since_ms: number;               // 从未被消费的时间
}

// ── 流水线健康 ──

export interface PipelineIntegrityEvent {
  sub_type: "PIPELINE_INTEGRITY";
  alert_id: string;
  timestamp_ms: number;
  tag_survival_rate: number;           // 注入 ws §3: <100% → alert
  failed_analyzer_stage: string | null; // 标签丢失的分析器阶段
}

export interface DriftAlertEvent {
  sub_type: "DRIFT_ALERT";
  alert_id: string;
  timestamp_ms: number;
  agent_id: string;
  drift_score: number;                 // handoff §11: 语义嵌入距离
  drift_severity: "low" | "medium" | "high" | "critical";
  root_task_hash: string;
  subagent_task_hash: string;
}

// ── 联合类型 ──

export type HandoffObservabilityEvent =
  | HandoffSpawnEvent
  | HandoffCompletionEvent
  | BoundaryTagEvent
  | FileCorrelationEvent
  | ContextSaturationEvent
  | ToolDeniedEvent
  | FanoutStormEvent
  | BoundaryDriftEvent
  | DataLeakEvent
  | StrategyDivergenceEvent
  | ContextConflictEvent
  | CorrectiveLoopEvent
  | OrphanedWorkEvent
  | PipelineIntegrityEvent
  | DriftAlertEvent;

// ═══════════════════════════════════════════════════════════════
// 5. SSE 流扩展 — 扩展现有 UnifiedStreamEvent
// ═══════════════════════════════════════════════════════════════

export interface DelegationTreeUpdate {
  tree_id: string;
  update_type: "node_spawned" | "node_completed" | "node_failed";
  timestamp_ms: number;
  node: DelegationNode;
}

/** 与现有 data-contract.ts 共存：新增 3 个 stream_type */
export type HandoffExtendedStreamEvent =
  | { stream_type: "delegation_tree"; data: DelegationTreeUpdate }
  | { stream_type: "handoff_observability"; data: HandoffObservabilityEvent }
  | { stream_type: "file_access"; data: FileAccessWithHandoff };

// ═══════════════════════════════════════════════════════════════
// 6. 客户端聚合状态
// ═══════════════════════════════════════════════════════════════

export interface HandoffObservabilityState {
  /** 完整委托树 */
  delegation_tree: DelegationNode | null;

  /** 活跃 handoff (spawned 但未 completed) */
  active_handoffs: Map<string, HandoffSpawnEvent>;

  /** 按深度分组的文件访问 */
  file_access_by_depth: Record<number, FileAccessWithHandoff[]>;

  /** 最近工具门控拒绝记录 */
  recent_denials: ToolDeniedEvent[];

  /** 运行指标 */
  metrics: HandoffObsMetrics;
}

export interface HandoffObsMetrics {
  total_spawned: number;
  total_completed: number;
  active_count: number;
  max_chain_depth: number;
  fan_out_ratio: number;                // spawned/completed (handoff §13)
  fan_out_critical: boolean;            // ratio > 3:1
  avg_context_overhead: number;         // handoff §7
  orphaned_artifacts: number;           // handoff §10
  workspace_tag_survival_rate: number;  // 注入 ws §3
}

// ═══════════════════════════════════════════════════════════════
// 7. 组件 Props
// ═══════════════════════════════════════════════════════════════

/** 委托树视图 — handoff §9: 时间索引树 + 注入 ws §2 边界漂移覆盖 */
export interface DelegationTreeViewProps {
  tree: DelegationNode | null;
  metrics: HandoffObsMetrics;
  selectedNodeId: string | null;
  onSelectNode: (nodeId: string) => void;
  /** null = 实时, 值 = 回放到指定时间点的快照 */
  replayTimestampMs: number | null;
  /** handoff §10: 代理间依赖边覆盖在树上 */
  dependencyEdges: Array<{
    from_spawn_id: string;
    to_spawn_id: string;
    artifact_path: string;
    is_orphaned: boolean;
  }>;
  colorByDepth?: boolean;
}

/** 子代理详情面板 — handoff §3, §5, §12 + 注入 subagent1 §1 */
export interface DelegationNodeDetailProps {
  node: DelegationNode;
  completion: HandoffCompletionRecord | null;
  phase_latencies: PhaseLatencies;
  file_accesses: FileAccessWithHandoff[];
  alerts: HandoffObservabilityEvent[];
  denial_records: ToolDeniedEvent[];
  onClose: () => void;
}

/** 文件访问时间线行 — handoff §1, §4 + PLATFORM-0042 */
export interface FileAccessRowProps {
  access: FileAccessWithHandoff;
  is_highlighted: boolean;
  highlight_agent_id: string | null;
  onSelect: (access: FileAccessWithHandoff) => void;
  /** PLATFORM-0042: 客户端过滤开关（不修改后端数据） */
  show_cross_boundary?: boolean;
}

/** 阶段延迟堆叠图 — handoff §3, §12 */
export interface PhaseLatencyChartProps {
  latencies: PhaseLatencies;
  peer_average?: PhaseLatencies;   // 同深度兄弟平均, 用于对比
  compact?: boolean;
}

/** 上下文饱和度仪表 — handoff §7 + 注入 ext §1 */
export interface SaturationGaugeProps {
  overhead_ratio: number;
  injected_bytes: number;
  budget_bytes: number;
  warn_threshold?: number;          // 默认 0.6
  critical_threshold?: number;      // 默认 0.85
  compact?: boolean;
}

/** 工具门控审计日志 — handoff §8 */
export interface ToolGateAuditLogProps {
  denials: ToolDeniedEvent[];
  filter_by_rule_id?: string;
  filter_by_agent_id?: string;
  onSelectDenial: (denialId: string) => void;
}

/** Fan-out 比率监控 — handoff §13 */
export interface FanOutMonitorProps {
  metrics: HandoffObsMetrics;
  history: Array<{
    timestamp_ms: number;
    spawned: number;
    completed: number;
    ratio: number;
  }>;
  critical_ratio?: number;          // 默认 3.0
}

/** 工作区边界漂移面板 — 注入 ws §2 */
export interface BoundaryDriftPanelProps {
  agents: Array<{
    agent_id: string;
    chain_depth: number;
    cross_boundary_ratio: number;
    sibling_median: number;
    deviation_sigmas: number;
    is_anomalous: boolean;           // >1.5σ
  }>;
  onSelectAgent: (agentId: string) => void;
}

/** 流水线完整性仪表 — 注入 ws §3 */
export interface PipelineIntegrityPanelProps {
  tag_survival_rate: number;
  failed_stage: string | null;
  history: Array<{
    timestamp_ms: number;
    survival_rate: number;
    stage: string;
  }>;
}

// ═══════════════════════════════════════════════════════════════
// 8. 类型守卫
// ═══════════════════════════════════════════════════════════════

export function isHandoffSpawn(e: HandoffObservabilityEvent): e is HandoffSpawnEvent {
  return e.sub_type === "HANDOFF_SPAWN";
}
export function isHandoffComplete(e: HandoffObservabilityEvent): e is HandoffCompletionEvent {
  return e.sub_type === "HANDOFF_COMPLETE";
}
export function isBoundaryTag(e: HandoffObservabilityEvent): e is BoundaryTagEvent {
  return e.sub_type === "BOUNDARY_TAG";
}
export function isFileCorrelation(e: HandoffObservabilityEvent): e is FileCorrelationEvent {
  return e.sub_type === "FILE_CORRELATION";
}
export function isContextSaturation(e: HandoffObservabilityEvent): e is ContextSaturationEvent {
  return e.sub_type === "CONTEXT_SATURATION";
}
export function isToolDenied(e: HandoffObservabilityEvent): e is ToolDeniedEvent {
  return e.sub_type === "TOOL_DENIED";
}
export function isFanoutStorm(e: HandoffObservabilityEvent): e is FanoutStormEvent {
  return e.sub_type === "FANOUT_STORM";
}
export function isBoundaryDrift(e: HandoffObservabilityEvent): e is BoundaryDriftEvent {
  return e.sub_type === "BOUNDARY_DRIFT";
}
export function isDataLeak(e: HandoffObservabilityEvent): e is DataLeakEvent {
  return e.sub_type === "DATA_LEAK";
}
export function isStrategyDivergence(e: HandoffObservabilityEvent): e is StrategyDivergenceEvent {
  return e.sub_type === "STRATEGY_DIVERGENCE";
}
export function isContextConflict(e: HandoffObservabilityEvent): e is ContextConflictEvent {
  return e.sub_type === "CONTEXT_CONFLICT";
}
export function isCorrectiveLoop(e: HandoffObservabilityEvent): e is CorrectiveLoopEvent {
  return e.sub_type === "CORRECTIVE_LOOP";
}
export function isOrphanedWork(e: HandoffObservabilityEvent): e is OrphanedWorkEvent {
  return e.sub_type === "ORPHANED_WORK";
}
export function isPipelineIntegrity(e: HandoffObservabilityEvent): e is PipelineIntegrityEvent {
  return e.sub_type === "PIPELINE_INTEGRITY";
}
export function isDriftAlert(e: HandoffObservabilityEvent): e is DriftAlertEvent {
  return e.sub_type === "DRIFT_ALERT";
}
