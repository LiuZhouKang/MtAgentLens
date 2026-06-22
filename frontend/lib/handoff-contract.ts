// ═══════════════════════════════════════════════════════════════
// Handoff Observability v2 — Frontend Data Contract
// 目标：匹配 shared_handoff.md 所有 15+3+3+3 个可观测性扩展项，并
//       与现有 `lib/data-contract.ts` 和 `lib/types.ts` 共存。
// ═══════════════════════════════════════════════════════════════

// ── 版本标识 ──
export const HANDOFF_CONTRACT_VERSION = "2.0.0" as const;

// ═══════════════════════════════════════════════════════════════
// 1. 委派树节点（对应于需求 #9、#13、#15）
// ═══════════════════════════════════════════════════════════════

/** 手递手生命周期阶段 */
export type HandoffPhase =
  | "dispatch"       // 调度：父代理决定生成子代理
  | "context-inject" // 上下文注入：系统提示词 + 任务约束写入
  | "execute"        // 执行：子代理运行工具调用
  | "collect";       // 收集：父代理读取子代理输出

/** 每个代理在委派树中的一个节点 */
export interface DelegationTreeNode {
  node_id: string;
  agent_id: string;
  parent_agent_id: string | null;      // 根节点为 null
  depth: number;                        // N=0 根代理，N+1 层的子代理
  spawn_timestamp_ns: number;
  complete_timestamp_ns: number | null; // 仍在运行时为 null

  // 手递手记录（对应于需求 #5）
  handoff_record: HandoffRecord | null;

  // 延迟细分（对应于需求 #12）
  latency_breakdown: HandoffLatencyBreakdown;

  // 上下文窗口饱和度（对应于需求 #7）
  context_saturation: ContextSaturation | null;

  // 模型保真度（对应于 Subagent1 #1）
  model_assignment: ModelAssignment | null;

  // 子节点 ID，用于树形渲染
  child_ids: string[];

  // 执行策略收敛（对应于需求 #15）
  strategy_similarity: StrategyConvergence | null;
}

// ═══════════════════════════════════════════════════════════════
// 2. 手递手记录（对应于需求 #5）
// ═══════════════════════════════════════════════════════════════

export interface HandoffRecord {
  handoff_id: string;
  from_agent_id: string;
  to_agent_id: string;
  timestamp_ns: number;

  // 用于成本归因的字节/令牌数据
  bytes_in: number;       // 注入的上下文大小（字节）
  bytes_out: number;      // 收集到的结果大小（字节）
  token_count: number;    // 最终结果令牌数（0 表示非 LLM 代理）

  // 序列化的系统提示词是否可用，用于差异对比（需求 #6）
  injected_prompt_hash: string | null;

  // 手递手结果工件（需求 #10 的消费追踪）
  output_artifacts: HandoffArtifact[];
}

export interface HandoffArtifact {
  artifact_id: string;
  artifact_type: "file" | "tool_result" | "handoff_summary";
  resource_path: string | null;   // 文件路径
  content_hash: string;
  size_bytes: number;
  consumed_by: string[];          // 读取此工件的代理 ID
  is_orphaned: boolean;           // 需求 #10：从未被任何代理回读则为 true
}

// ═══════════════════════════════════════════════════════════════
// 3. 延迟细分（对应于需求 #12）
// ═══════════════════════════════════════════════════════════════

export interface HandoffLatencyBreakdown {
  queued_awaiting_dispatch_ms: number;
  context_injection_io_ms: number;
  model_inference_queueing_ms: number;
  tool_call_round_trip_ms: number;
  total_idle_wall_clock_ms: number;
}

// ═══════════════════════════════════════════════════════════════
// 4. 上下文窗口饱和度（对应于需求 #7、Subagent1-ext #1）
// ═══════════════════════════════════════════════════════════════

export interface ContextSaturation {
  /** 注入的手递手内容大小（字节） */
  injected_size_bytes: number;
  /** 子代理的总上下文预算（字节） */
  total_context_budget_bytes: number;
  /** 开销比率 = injected / budget */
  overhead_ratio: number;
  /** 系统开销（预留令牌、工具架构等） */
  system_overhead_bytes: number;
  /** 有效推理预算（budget - system_overhead） */
  effective_reasoning_budget_bytes: number;
  /** 压缩是否已删除关键任务约束 */
  semantic_integrity_threshold_breached: boolean;
}

// ═══════════════════════════════════════════════════════════════
// 5. 模型保真度（对应于 Subagent1 #1）
// ═══════════════════════════════════════════════════════════════

export interface ModelAssignment {
  /** 分配给此代理的实际模型 */
  assigned_model: string;
  /** 路由器日志中此任务类型的最优模型 */
  task_optimal_model: string;
  /** 该任务是否需要大型推理（路由器逻辑的提示） */
  requires_heavy_reasoning: boolean;
  /** 如果分配不是最优的则为 true */
  is_suboptimal: boolean;
}

// ═══════════════════════════════════════════════════════════════
// 6. 策略收敛（对应于需求 #15）
// ═══════════════════════════════════════════════════════════════

export interface StrategyConvergence {
  /** 与其他兄弟代理的滚动相似度分数 */
  rolling_similarity_score: number;
  /** 距兄弟共识的标准差数（超过 2 → DIVERGENCE） */
  deviation_sigmas: number;
  /** 如果 deviation_sigmas > 2 则为 true */
  is_diverged: boolean;
  /** 兄弟代理数量 */
  sibling_count: number;
}

// ═══════════════════════════════════════════════════════════════
// 7. 集成到现有 UnifiedStreamEvent（新增 stream_type）
// ═══════════════════════════════════════════════════════════════

import type { TimelineStep, ProcessTreeUpdate, AnomalyStreamEvent } from "./data-contract";

/** 扩展 UnifiedStreamEvent（来自 data-contract.ts），新增 3 个流 */
export type HandoffUnifiedStreamEvent =
  | { stream_type: "timeline"; data: TimelineStep }
  | { stream_type: "process_tree"; data: ProcessTreeUpdate }
  | { stream_type: "anomaly"; data: AnomalyStreamEvent }
  | { stream_type: "delegation_tree"; data: DelegationTreeUpdate }
  | { stream_type: "handoff_alert"; data: HandoffAlertEvent }
  | { stream_type: "file_access"; data: FileAccessWithHandoff };

// ═══════════════════════════════════════════════════════════════
// 8. 委派树更新（需求 #9 的增量更新）
// ═══════════════════════════════════════════════════════════════

export interface DelegationTreeUpdate {
  tree_id: string;
  update_type: "node_spawned" | "node_completed" | "node_failed";
  timestamp_ns: number;
  node: DelegationTreeNode; // 完整节点快照
}

// ═══════════════════════════════════════════════════════════════
// 9. 手递手警报事件（需求 #6, #7, #11, #13, #14）
// ═══════════════════════════════════════════════════════════════

export type HandoffAlertType =
  | "context_contradiction"        // 需求 #6：系统提示词冲突
  | "context_saturation"           // 需求 #7：开销大于 60%
  | "prompt_injection_drift"       // 需求 #11：语义漂移
  | "fan_out_storm"                // 需求 #13：生成-完成比率 > 3:1
  | "data_leak"                    // 需求 #14：跨代理文件泄漏
  | "strategy_divergence"          // 需求 #15：代理探索路径偏离
  | "orphaned_work"                // 需求 #10：输出从未被消费
  | "result_staleness"             // Subagent1 #2：在最新变更前生成的结果
  | "failure_mode_cluster"         // Subagent1 #3：重复的分派失败模式
  | "delegation_corrective_loop"   // Subagent1-ext #2：3+ 次同一任务的纠正尝试
  | "artifact_leakage"             // Subagent1-ext #3：跨工作区工件泄漏
  | "ssl_reassembly_degraded"      // Subagent1-pipe #1：SSL 重组置信度 < 95%
  | "ebpf_cpu_overhead"            // Subagent1-pipe #2：eBPF CPU 时间 > 5%
  | "claim_completion_ratio_low"   // Subagent1-pipe #3：声明完成比率 < 80%
  | "suboptimal_model_assignment"; // Subagent1 #1：模型路由未对齐

export type HandoffDriftSeverity = "none" | "low" | "medium" | "high" | "critical";

export interface HandoffAlertEvent {
  alert_id: string;
  timestamp_ns: number;
  alert_type: HandoffAlertType;
  severity: import("./data-contract").AnomalySeverity;
  confidence: number; // 0.0 - 1.0

  // 告警上下文（根据 alert_type 选择性填充）
  primary_agent_id: string;
  involved_agent_ids: string[];
  chain_depth: number | null; // 违规发生时的树深度（需求 #4）

  // 告警特定载荷（各类型至少填充一个）
  payload: HandoffAlertPayload;

  // 用于仪表盘渲染的摘要
  title: string;
  description: string;
  remediation_hint: string | null;
}

/** 每种告警类型的独立载荷 */
export type HandoffAlertPayload =
  | { type: "context_contradiction"; conflicting_constraint: string; parent_directive: string; injected_instruction: string }
  | { type: "context_saturation"; overhead_ratio: number; budget_bytes: number; consumed_bytes: number }
  | { type: "prompt_injection_drift"; drift_score: number; drift_severity: HandoffDriftSeverity; root_embedding_distance: number }
  | { type: "fan_out_storm"; spawn_rate: number; completion_rate: number; ratio: number; window_seconds: number }
  | { type: "data_leak"; leaking_agent_id: string; accessing_agent_id: string; exposed_file_path: string; content_hash: string }
  | { type: "strategy_divergence"; diverged_agent_id: string; sibling_consensus_sequence: string[]; diverged_sequence: string[]; deviation_sigmas: number }
  | { type: "orphaned_work"; artifact_id: string; producer_agent_id: string; artifact_size_bytes: number; idle_since_ns: number }
  | { type: "result_staleness"; artifact_id: string; produced_at_ns: number; workspace_mutated_at_ns: number; parent_read_at_ns: number }
  | { type: "failure_mode_cluster"; failure_category: string; count: number; affected_task_types: string[] }
  | { type: "delegation_corrective_loop"; task_scope: string; corrective_spawns: number; parent_agent_id: string }
  | { type: "artifact_leakage"; producer_agent_id: string; artifact_path: string; accessing_sibling_ids: string[]; content_hash_similarity: number }
  | { type: "ssl_reassembly_degraded"; chunk_count: number; corrupted_count: number; confidence: number }
  | { type: "ebpf_cpu_overhead"; ebpf_cpu_time_us: number; process_wall_time_us: number; overhead_percent: number }
  | { type: "claim_completion_ratio_low"; handoff_events_count: number; completed_claims_count: number; completion_ratio: number }
  | { type: "suboptimal_model_assignment"; assigned_model: string; optimal_model: string; task_category: string };

// ═══════════════════════════════════════════════════════════════
// 10. 文件访问 + 手递手元数据（需求 #1, #2, #4）
// ═══════════════════════════════════════════════════════════════

export interface FileAccessWithHandoff {
  access_id: string;
  timestamp_ns: number;
  agent_id: string;
  operation: "open" | "read" | "write" | "close";
  file_path: string;
  bytes_transferred: number;

  // 手递手相关字段
  chain_depth: number;             // 需求 #4：N=0 表示根代理
  handoff_phase: HandoffPhase;     // 文件访问期间的代理当前阶段
  tool_call_id: string | null;     // 需求 #2：与工具调用关联
  is_outside_workspace: boolean;   // 需求 #3：工作区边界违规
}

// ═══════════════════════════════════════════════════════════════
// 11. 手递手安全审计流（需求 #8）
// ═══════════════════════════════════════════════════════════════

export interface PolicyDenialRecord {
  denial_id: string;
  timestamp_ns: number;
  agent_id: string;
  tool_name: string;
  enforcing_policy_rule_id: string;
  chain_depth: number;
  denial_reason: string;
}

// ═══════════════════════════════════════════════════════════════
// 12. 手递手失败模式（Subagent1 #3）
// ═══════════════════════════════════════════════════════════════

export type HandoffFailureCategory =
  | "timeout"
  | "tool_permission_denied"
  | "model_capacity_rejected"
  | "context_overflow";

export interface HandoffFailureRecord {
  failure_id: string;
  agent_id: string;
  task_type: string;
  failure_category: HandoffFailureCategory;
  timestamp_ns: number;
  retry_count: number;
  resolved: boolean;
}

// ═══════════════════════════════════════════════════════════════
// 13. 仪表盘聚合（用于顶层视图）
// ═══════════════════════════════════════════════════════════════

export interface HandoffDashboardSnapshot {
  /** 完整委派树 */
  delegation_tree: DelegationTreeNode | null;

  /** 按类型分类的有效告警 */
  active_alerts: HandoffAlertEvent[];

  /** 按深度层级分组的文件访问模式（需求 #4） */
  file_access_by_depth: Record<number, FileAccessWithHandoff[]>;

  /** 运行指标 */
  metrics: HandoffRunMetrics;

  /** 最近 N 次策略拒绝（需求 #8） */
  recent_denials: PolicyDenialRecord[];

  /** 手递手失败的直方图（Subagent1 #3） */
  failure_histogram: Record<HandoffFailureCategory, number>;

  /** 跨代理数据泄漏事件（需求 #14 + Subagent1-ext #3） */
  leak_events: HandoffAlertEvent[]; // data_leak + artifact_leakage 类型
}

export interface HandoffRunMetrics {
  total_agents_spawned: number;
  total_agents_completed: number;
  max_delegation_depth: number;
  orphaned_artifacts_count: number;
  fan_out_ratio: number;            // 已生成 / 已完成（需求 #13 的关键指标）
  avg_context_overhead_ratio: number;
  ssl_reassembly_confidence: number; // Subagent1-pipe #1
  ebpf_cpu_overhead_percent: number; // Subagent1-pipe #2
  claim_completion_ratio: number;    // Subagent1-pipe #3
}

// ═══════════════════════════════════════════════════════════════
// 14. 组件属性接口
// ═══════════════════════════════════════════════════════════════

// ── DelegationTreeView ──
// 时间索引的树形可视化（需求 #9）
export interface DelegationTreeViewProps {
  tree: DelegationTreeNode | null;
  metrics: HandoffRunMetrics;
  selectedNodeId: string | null;
  onSelectNode: (nodeId: string) => void;
  /** 回放模式：在时间点 T 重建委派拓扑 */
  replayTimestampNs: number | null;
  /** 按深度着色 */
  colorByDepth?: boolean;
}

// ── DelegationNodeDetail ──
// 每个子代理的详情面板（需求 #12）
export interface DelegationNodeDetailProps {
  node: DelegationTreeNode;
  fileAccesses: FileAccessWithHandoff[];
  alerts: HandoffAlertEvent[];
  denialRecords: PolicyDenialRecord[];
  onClose: () => void;
  onTraceback: (nodeId: string) => void;
}

// ── HandoffAlertFeed ──
// 手递手特定告警的专用 alert feed
export interface HandoffAlertFeedProps {
  alerts: HandoffAlertEvent[];
  onSelectAlert: (alertId: string) => void;
  filterByType?: HandoffAlertType[];
  filterBySeverity?: import("./data-contract").AnomalySeverity[];
}

// ── HandoffAlertCard ──
// 单个手递手告警详情卡片
export interface HandoffAlertCardProps {
  alert: HandoffAlertEvent;
  onDismiss: () => void;
  onTraceback: (alertId: string) => void;
  showPayload?: boolean;
}

// ── LatencyBreakdownChart ──
// 每个手递手生命周期阶段的堆叠延迟栏（需求 #12）
export interface LatencyBreakdownChartProps {
  breakdown: HandoffLatencyBreakdown;
  /** 可选：与兄弟代理比较 */
  siblingAvg?: HandoffLatencyBreakdown;
  compact?: boolean;
}

// ── ContextSaturationGauge ──
// 每个子代理上下文窗口消耗的可视化仪表盘（需求 #7）
export interface ContextSaturationGaugeProps {
  saturation: ContextSaturation;
  warningThreshold?: number;  // 默认 0.6（60%）
  criticalThreshold?: number; // 默认 0.85
  compact?: boolean;
}

// ── FileAccessTimeline ──
// 文件访问与手递手阶段叠加的时间线（需求 #1, #2, #4）
export interface FileAccessTimelineProps {
  accesses: FileAccessWithHandoff[];
  /** 可选择按 [depth, agent_id, handoff_phase] 分组 */
  groupBy?: "depth" | "agent" | "phase" | "none";
  highlightedAgentId?: string | null;
  onSelectAccess: (accessId: string) => void;
}

// ── PolicyDenialAuditLog ──
// 所有被策略拒绝的工具调用的可审计安全日志（需求 #8）
export interface PolicyDenialAuditLogProps {
  denials: PolicyDenialRecord[];
  filterByRule?: string | null;
  filterByAgent?: string | null;
  onSelectDenial: (denialId: string) => void;
}

// ── FailureModeHeatmap ──
// 按任务类型显示重复失败模式的矩阵（Subagent1 #3）
export interface FailureModeHeatmapProps {
  failures: HandoffFailureRecord[];
  onSelectCell: (taskType: string, category: HandoffFailureCategory) => void;
}

// ── FanOutMonitor ──
// 实时生成-完成比率监控（需求 #13）
export interface FanOutMonitorProps {
  metrics: HandoffRunMetrics;
  /** 生成比率与完成比率的历史时间序列 */
  spawnHistory: Array<{ timestamp_ns: number; spawn_rate: number }>;
  completionHistory: Array<{ timestamp_ns: number; completion_rate: number }>;
  criticalRatio?: number; // 默认 3.0
}

// ── StrategyDivergencePanel ──
// 显示哪些兄弟代理偏离了共识（需求 #15）
export interface StrategyDivergencePanelProps {
  siblingGroup: DelegationTreeNode[];
  convergenceData: Map<string, StrategyConvergence>;
  onSelectDivergent: (agentId: string) => void;
}

// ── PipelineHealthDashboard ──
// 流水线自身健康度的子面板（Subagent1-pipe #1-3）
export interface PipelineHealthDashboardProps {
  sslReassemblyConfidence: number;
  ebpfCpuOverheadPercent: number;
  claimCompletionRatio: number;
  degradationAlerts: HandoffAlertEvent[]; // ssl_reassembly_degraded + ebpf_cpu_overhead + claim_completion_ratio_low 类型
}

// ── CorrectionLoopDetector ──
// 委派纠正循环的可视化展示（Subagent1-ext #2）
export interface CorrectionLoopDetectorProps {
  loops: Array<{
    parent_agent_id: string;
    task_scope: string;
    corrective_spawns: number;
    timestamps_ns: number[];
    alert: HandoffAlertEvent | null;
  }>;
  onSelectLoop: (parentAgentId: string) => void;
}

// ═══════════════════════════════════════════════════════════════
// 15. 用新字段扩展现有的 ResponsibilityEdge（与 types.ts 共存）
// ═══════════════════════════════════════════════════════════════

/** 手递手特定的边元数据，附加到 AgentDelegatesTo 变体 */
export interface HandoffEdgeMetadata {
  handoff_id: string;
  chain_depth: number;
  handoff_phase_at_creation: HandoffPhase;
  context_size_bytes: number;
  token_count: number | null;
  is_corrective: boolean; // Subagent1-ext #2：这是纠正性重新分派吗
}

// ═══════════════════════════════════════════════════════════════
// 16. 向后兼容的类型守卫
// ═══════════════════════════════════════════════════════════════

export function isDelegationTreeUpdate(
  event: HandoffUnifiedStreamEvent
): event is { stream_type: "delegation_tree"; data: DelegationTreeUpdate } {
  return event.stream_type === "delegation_tree";
}

export function isHandoffAlertEvent(
  event: HandoffUnifiedStreamEvent
): event is { stream_type: "handoff_alert"; data: HandoffAlertEvent } {
  return event.stream_type === "handoff_alert";
}

export function isFileAccessWithHandoff(
  event: HandoffUnifiedStreamEvent
): event is { stream_type: "file_access"; data: FileAccessWithHandoff } {
  return event.stream_type === "file_access";
}
