// ═══════════════════════════════════════════════════════════════
// Subagent 3 — HandoffChain Frontend Data Contract
// 输入: Subagent 2 的 HandoffChainAnalyzer 设计 (agent_1.log)
//       + shared_handoff.md (handoff agent observability 条目 4, 7, 10, 12, 13, 113-118)
// 对齐: lib/types.ts · lib/data-contract.ts · lib/handoff-lifecycle-contract.ts
// ═══════════════════════════════════════════════════════════════

import type { AnomalySeverity } from "./data-contract";
import type { AlertSeverity } from "./types";

// ═══════════════════════════════════════════════════════════════
// 1. 事件源: handoff_chain
//    对应 Subagent 2 Rust 端的 source = "handoff_chain"
//    6 种事件类型: 与 HandoffChainAnalyzer 的 6 个 emit 分支一一对应
// ═══════════════════════════════════════════════════════════════

export type HandoffChainEventType =
  | "HANDOFF_CHAIN_DEPTH"
  | "CONTEXT_SATURATION"
  | "FANOUT_STORM"
  | "CORRECTIVE_LOOP"
  | "ORPHANED_WORK"
  | "LIFECYCLE_BREAKDOWN";

// ═══════════════════════════════════════════════════════════════
// 2. HANDOFF_CHAIN_DEPTH — 链深度标记
//    shared_handoff.md #4 + Subagent 2: FILE_OPEN 事件打 chain_depth 标签
//    "Tag every file-access event with the originating handoff chain depth"
// ═══════════════════════════════════════════════════════════════

export interface HandoffChainDepthEvent {
  event_type: "HANDOFF_CHAIN_DEPTH";
  /** 关联的 FILE_OPEN 事件 ID */
  file_access_id: string;
  /** 发起该文件访问的 handoff 子代理 */
  agent_id: string;
  /** 嵌套深度: N=0 根代理, N+1 每层嵌套 */
  chain_depth: number;
  /** handoff 生命周期阶段 */
  handoff_phase: "dispatch" | "context_inject" | "execute" | "collect";
  /** 目标文件路径 */
  target_path: string;
  timestamp_ms: number;
}

// ═══════════════════════════════════════════════════════════════
// 3. CONTEXT_SATURATION — 上下文窗口饱和度警告
//    shared_handoff.md #7 + Subagent 2: injected_size / context_budget > 0.6
//    "WARNING when overhead exceeds 60% of available budget"
// ═══════════════════════════════════════════════════════════════

export interface ContextSaturationEvent {
  event_type: "CONTEXT_SATURATION";
  handoff_id: string;
  agent_id: string;
  chain_depth: number;
  /** 注入的 handoff 内容字节数 */
  injected_bytes: number;
  /** 子代理上下文预算总字节数 */
  context_budget_bytes: number;
  /** 饱和度比例: 0.0–1.0 */
  saturation_ratio: number;
  /** 默认 0.6, 超过则 WARNING; >0.85 升级为 ERROR */
  warn_threshold: number;
  severity: "warning" | "error";    // >85% → error
  timestamp_ms: number;
}

// ═══════════════════════════════════════════════════════════════
// 4. FANOUT_STORM — 扇出放大风暴
//    shared_handoff.md #13 + Subagent 2: spawn_rate / complete_rate > 3.0
//    "CRITICAL when spawn rate exceeds completion rate by more than 3:1"
// ═══════════════════════════════════════════════════════════════

export interface FanoutStormEvent {
  event_type: "FANOUT_STORM";
  /** 滑动窗口起始时间 (ms) */
  window_start_ms: number;
  /** 滑动窗口结束时间 (ms) */
  window_end_ms: number;
  /** 窗口内的生成数 */
  spawn_count: number;
  /** 窗口内的完成数 */
  completion_count: number;
  /** spawn / completion 比率 */
  storm_ratio: number;
  /** 触发阈值 (默认 3.0) */
  threshold_ratio: number;
  severity: "critical";
  timestamp_ms: number;
}

// ═══════════════════════════════════════════════════════════════
// 5. CORRECTIVE_LOOP — 代理纠正循环
//    shared_handoff.md #113–118 + Subagent 2: 同一 task_scope 内 ≥3 次重派发
//    "≥3 corrective subagents to fix prior outputs in the same task scope"
// ═══════════════════════════════════════════════════════════════

export interface CorrectiveLoopEvent {
  event_type: "CORRECTIVE_LOOP";
  /** 任务作用域标识 */
  task_scope: string;
  /** 父代理 ID (反复派发的代理) */
  parent_agent_id: string;
  /** 触发循环的纠正序列 */
  corrective_chain: CorrectiveSubagentRecord[];
  /** 纠正序列长度 */
  correction_count: number;
  /** 阈值 (默认 3) */
  threshold: number;
  /** 建议: 父代理应重新规划而非迭代修补 */
  recommendation: string;
  severity: "critical";
  timestamp_ms: number;
}

export interface CorrectiveSubagentRecord {
  spawn_id: string;
  agent_id: string;
  dispatched_at_ms: number;
  completed_at_ms: number;
  /** 修正前一个子代理的原因 */
  correction_reason: string | null;
}

// ═══════════════════════════════════════════════════════════════
// 6. ORPHANED_WORK — 孤立工作 (未被消费的子代理产出)
//    shared_handoff.md #10 + Subagent 2: 工件宽限期后仍未被任何下游代理读取
//    "output is never read back by any other agent, flagging wasted compute"
// ═══════════════════════════════════════════════════════════════

export interface OrphanedWorkEvent {
  event_type: "ORPHANED_WORK";
  /** 产物标识 */
  artifact_id: string;
  /** 生产者 */
  producer_agent_id: string;
  producer_spawn_id: string;
  /** 产物路径 (文件) 或 tool_result_id */
  artifact_path_or_id: string;
  /** 产物类型 */
  artifact_type: "file" | "tool_result" | "handoff_summary";
  /** 产出大小 */
  size_bytes: number;
  /** 产出时间 */
  produced_at_ms: number;
  /** 闲置时长 (ms) — 超过宽限期则触发 */
  idle_ms: number;
  /** 宽限期 (默认的 orphaned_work_grace_period_secs 对应毫秒) */
  grace_period_ms: number;
  severity: "warn";
  timestamp_ms: number;
}

// ═══════════════════════════════════════════════════════════════
// 7. LIFECYCLE_BREAKDOWN — 手递生命周期阶段延迟拆解
//    shared_handoff.md #12 + Subagent 2: 堆叠延迟指标
//    "attribute subagent idle wall-clock time to specific lifecycle phases"
// ═══════════════════════════════════════════════════════════════

export interface LifecycleBreakdownEvent {
  event_type: "LIFECYCLE_BREAKDOWN";
  handoff_id: string;
  agent_id: string;
  chain_depth: number;
  /** 各阶段延迟 (ms) */
  latencies: HandoffPhaseBreakdown;
  /** 总墙钟时间 (ms) */
  wall_clock_total_ms: number;
  timestamp_ms: number;
}

/** Rust: PhaseTimestamps 差值 → HandoffPhaseBreakdown */
export interface HandoffPhaseBreakdown {
  /** 排队等待派发 */
  queued_ms: number;
  /** 上下文注入 I/O */
  injection_io_ms: number;
  /** 模型推理时间 (LLM API 往返) */
  inference_ms: number;
  /** 工具调用往返 */
  tool_roundtrip_ms: number;
}

// ═══════════════════════════════════════════════════════════════
// 8. 统一事件联合类型
// ═══════════════════════════════════════════════════════════════

export type HandoffChainEvent =
  | HandoffChainDepthEvent
  | ContextSaturationEvent
  | FanoutStormEvent
  | CorrectiveLoopEvent
  | OrphanedWorkEvent
  | LifecycleBreakdownEvent;

// ═══════════════════════════════════════════════════════════════
// 9. 客户端聚合状态 (存入 zustand AppStore)
// ═══════════════════════════════════════════════════════════════

export interface HandoffChainState {
  /** 按深度分组的链深度事件 */
  chain_depth_events: Record<number, HandoffChainDepthEvent[]>;
  /** 最近的饱和度告警 */
  saturation_alerts: ContextSaturationEvent[];
  /** 扇出风暴事件 */
  fanout_storms: FanoutStormEvent[];
  /** 纠正循环 */
  corrective_loops: CorrectiveLoopEvent[];
  /** 孤立工作 */
  orphaned_works: OrphanedWorkEvent[];
  /** 生命周期延迟记录 */
  lifecycle_breakdowns: Record<string, LifecycleBreakdownEvent>;  // handoff_id → event

  /** 聚合指标 */
  chain_metrics: HandoffChainMetrics;
}

export interface HandoffChainMetrics {
  /** 最大链深度 */
  max_depth: number;
  /** 深度分布: depth → 事件数 */
  depth_distribution: Record<number, number>;
  /** 当前活跃的扇出比例 */
  current_fanout_ratio: number;
  /** 是否处于扇出风暴状态 */
  is_fanout_storm_active: boolean;
  /** 累计孤立工作数 */
  total_orphaned: number;
  /** 累计纠正循环数 */
  total_corrective_loops: number;
  /** 平均上下文饱和度 */
  avg_saturation_ratio: number;
}

// ═══════════════════════════════════════════════════════════════
// 10. 组件 Props
// ═══════════════════════════════════════════════════════════════

/**
 * 链深度分布视图 (需求 #4, #9)
 * 按 handoff 嵌套层级分组展示文件访问模式
 * 暴露深层代理异常 — 叶节点子代理超出典型访问广度时高亮
 */
export interface ChainDepthDistributionProps {
  /** 按深度分组的文件访问事件 */
  depthGroups: Record<number, HandoffChainDepthEvent[]>;
  /** 当前选中的深度层级 */
  selectedDepth: number | null;
  /** 各深度的统计: 深度 → {事件数, 代理数, 唯一路径数} */
  depthStats: Record<number, ChainDepthStats>;
  onSelectDepth: (depth: number) => void;
  onNavigateToAgent: (agentId: string) => void;
}

export interface ChainDepthStats {
  event_count: number;
  unique_agents: number;
  unique_paths: number;
  /** 聚合文件操作类型分布 */
  operation_distribution: Record<string, number>;
}

/**
 * 上下文饱和度仪表 (需求 #7)
 * 实时显示每个活跃 handoff 的上下文空间使用率
 */
export interface ContextSaturationDashboardProps {
  saturationEvents: ContextSaturationEvent[];
  /** 当前最高的饱和度值 */
  maxSaturation: number;
  /** 超过警告阈值的代理数 */
  warningCount: number;
  /** 超过严重阈值的代理数 */
  criticalCount: number;
  warnThreshold?: number;      // 默认 0.6
  criticalThreshold?: number;  // 默认 0.85
  onSelectAgent: (agentId: string) => void;
}

/** 单代理饱和度仪表盘 */
export interface SaturationGaugeProps {
  agentId: string;
  chainDepth: number;
  saturationRatio: number;
  injectedBytes: number;
  budgetBytes: number;
  severity: "warning" | "error";
  warnThreshold: number;
  criticalThreshold: number;
  compact?: boolean;
}

/**
 * 扇出风暴监视器 (需求 #13)
 * 滑动窗口内 spawn/completion 比率追踪
 */
export interface FanoutStormMonitorProps {
  metrics: HandoffChainMetrics;
  /** 比率历史序列 (用于折线图) */
  history: Array<{
    timestamp_ms: number;
    spawn_count: number;
    completion_count: number;
    ratio: number;
  }>;
  criticalRatio?: number;     // 默认 3.0
  windowSizeSecs?: number;    // 默认 fanout_storm_window_secs
}

/**
 * 纠正循环面板 (需求 #113–118)
 * 展示持续修正同一任务的反常模式
 */
export interface CorrectiveLoopPanelProps {
  loops: CorrectiveLoopEvent[];
  selectedLoopIndex: number | null;
  onSelectLoop: (index: number) => void;
  onNavigateToAgent: (agentId: string) => void;
}

/** 纠正循环详情 */
export interface CorrectiveLoopDetailProps {
  loop: CorrectiveLoopEvent;
  onClose: () => void;
  onNavigateToSubagent: (spawnId: string) => void;
}

/**
 * 孤立工作面板 (需求 #10)
 * 未被下游消费的子代理产出列表
 */
export interface OrphanedWorkPanelProps {
  orphanedWorks: OrphanedWorkEvent[];
  /** 筛选 */
  filterArtifactType: "file" | "tool_result" | "handoff_summary" | null;
  filterAgentId: string | null;
  /** 统计 */
  totalOrphaned: number;
  totalWastedBytes: number;
  onSelectOrphan: (artifactId: string) => void;
  onNavigateToProducer: (agentId: string) => void;
}

/**
 * 生命周期延迟堆叠图 (需求 #12)
 * 可视化各阶段延迟占比
 */
export interface LifecycleBreakdownChartProps {
  breakdown: HandoffPhaseBreakdown;
  wallClockTotalMs: number;
  /** 同深度平均延迟 (用于对比) */
  peerAverage?: HandoffPhaseBreakdown;
  agentId: string;
  chainDepth: number;
  compact?: boolean;
}

/**
 * 链深度时间线行 — 在 TimelinePanel 中渲染
 * 文件访问事件附带链深度信息
 */
export interface ChainDepthTimelineRowProps {
  event: HandoffChainDepthEvent;
  isHighlighted: boolean;
  highlightAgentId: string | null;
  onSelect: (event: HandoffChainDepthEvent) => void;
  onFocusAgent: (agentId: string) => void;
}

/**
 * 告警时间线行 — 在告警流中渲染饱和度/风暴/循环告警
 */
export interface HandoffChainAlertRowProps {
  event: ContextSaturationEvent | FanoutStormEvent | CorrectiveLoopEvent | OrphanedWorkEvent;
  onSelect: (event: HandoffChainEvent) => void;
  onDismiss: (eventType: string, eventId: string) => void;
}

// ═══════════════════════════════════════════════════════════════
// 11. 类型守卫
// ═══════════════════════════════════════════════════════════════

export function isChainDepth(e: HandoffChainEvent): e is HandoffChainDepthEvent {
  return e.event_type === "HANDOFF_CHAIN_DEPTH";
}
export function isContextSaturation(e: HandoffChainEvent): e is ContextSaturationEvent {
  return e.event_type === "CONTEXT_SATURATION";
}
export function isFanoutStorm(e: HandoffChainEvent): e is FanoutStormEvent {
  return e.event_type === "FANOUT_STORM";
}
export function isCorrectiveLoop(e: HandoffChainEvent): e is CorrectiveLoopEvent {
  return e.event_type === "CORRECTIVE_LOOP";
}
export function isOrphanedWork(e: HandoffChainEvent): e is OrphanedWorkEvent {
  return e.event_type === "ORPHANED_WORK";
}
export function isLifecycleBreakdown(e: HandoffChainEvent): e is LifecycleBreakdownEvent {
  return e.event_type === "LIFECYCLE_BREAKDOWN";
}

/**
 * 严重度 → 可视化等级
 */
export function chainSeverityToVisual(
  severity: "warning" | "error" | "critical" | "info" | "warn",
): "elevated" | "high" | "urgent" | "normal" {
  switch (severity) {
    case "critical": return "urgent";
    case "error":    return "high";
    case "warning":
    case "warn":     return "elevated";
    default:         return "normal";
  }
}

// ═══════════════════════════════════════════════════════════════
// 12. 集成点
//    - TimelinePanel: 注册 ChainDepthTimelineRow
//    - AlertFeed: 注册 HandoffChainAlertRow
//    - DashboardShell: 新增标签页 "链分析"
//    - DelegationNodeDetail: 嵌入 LifecycleBreakdownChart + SaturationGauge
//    - 新增面板: ChainDepthDistributionPanel, CorrectiveLoopPanel,
//               OrphanedWorkPanel, FanoutStormMonitor
// ═══════════════════════════════════════════════════════════════
