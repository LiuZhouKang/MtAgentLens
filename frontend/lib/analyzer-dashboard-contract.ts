// Analyzer Dashboard — 集成式仪表板契约
//
// 将五个专用分析器仪表板（AgentLoop / ToolExec / Dedup / DataExfil / LlmApiAnomaly）整合为
// 统一的 Dashboard Shell，提供：
//   1. PipelineOverview  — 6 阶段流水线状态 + 关键指标一览
//   2. AnalyzerDetail    — 各分析器的专业视图（完整嵌入子仪表板）
//   3. CrossAnalysis     — 跨分析器关联视图
//
// 跨分析器联动 key:
//   tool_name   — 连接 AgentLoopDashboard ↔ ToolExecutionDashboard
//   agent_pid   — 连接 AgentLoopDashboard / ToolExecutionDashboard / DataExfilDashboard / LlmApiAnomalyDashboard ↔ ProcessTree
//   source_tag  — 连接 DedupDiagnosticsPanel ↔ TimelinePanel
//   destination_host — 连接 DataExfilDashboard ↔ TimelinePanel
//   endpoint    — 连接 LlmApiAnomalyDashboard ↔ TimelinePanel

import type { LoopSeverity, LoopType, LoopSummary } from "./agent-loop-contract";
import type { ToolCallStatus, ToolCallRecord, AgentToolStats } from "./tool-execution-contract";
import type { DedupMetricsSnapshot, DedupDroppedEvent } from "./dedup-diagnostics-contract";
import type { DataExfilAlert, ExfilSeverity, ExfilType } from "./data-exfiltration-contract";
import type { LlmApiAnomalyAlert, AnomalyType, LlmApiSeverity } from "./llm-api-anomaly-contract";
import type { FileAccessRecord, FileAccessType, FileAccessRiskLevel, WorkspaceBoundary } from "./file-access-audit-contract";
import type { TokenAnomalyAlert, TokenAnomalyType, TokenAnomalySeverity, TokenCountSource } from "./token-cost-contract";
import type { SessionBoundaryAlert, SessionBoundaryAnomalyType, SessionBoundarySeverity } from "./session-boundary-contract";
import type { SessionHealthAlert, SessionHealthSnapshot, HealthAlertType, HealthAlertSeverity, HealthDimension } from "./session-health-contract";
import type { SseScanSignal, SseScannerStats, ScannerViewMode, DetectionLayer, ScannedEventType } from "./sse-content-scanner-contract";
import { createDefaultScannerState } from "./sse-content-scanner-contract";
import type {
  MismatchType,
  MismatchSeverity,
  MismatchSummary,
  IntentVerifierDashboardViewMode,
} from "./intent-verifier-contract";
import { createDefaultIntentVerifierState } from "./intent-verifier-contract";
import type { AnomalySeverity } from "./data-contract";

// ═══════════════════════════════════════════════════════════════════════════════
// 流水线阶段定义
// ═══════════════════════════════════════════════════════════════════════════════

/** 6 阶段分析器流水线 */
export type PipelineStage =
  | "Preprocessing"      // Stage 0: TimestampNormalizer, SEDA, AuthHeaderRemover
  | "Parsing"            // Stage 1: SSEProcessor, HTTPParser
  | "CausalLinking"      // Stage 2: HTTPFilter, SSLFilter, ToolExecutionTracker
  | "InjectionDetection" // Stage 3: HandoffInjectionAnalyzer
  | "AnomalyDetection"   // Stage 4: MultiAgentAnomalyAnalyzer, AgentLoopDetector, DataExfiltrationDetector, LLMApiUsageAnomalyAnalyzer
  | "AlertEnrichment"    // Stage 5: Alert enrichment, FileLogger, Output;

/** 流水线阶段的人类可读标签 */
export const PIPELINE_STAGE_LABEL: Record<PipelineStage, string> = {
  Preprocessing: "预处理",
  Parsing: "解析",
  CausalLinking: "因果关联",
  InjectionDetection: "注入检测",
  AnomalyDetection: "异常检测",
  AlertEnrichment: "告警丰富",
};

/** 每个阶段注册的分析器 */
export interface StageAnalyzerInfo {
  /** 分析器唯一标识 */
  id: string;
  /** 人类可读名称 */
  name: string;
  /** 是否活跃 */
  active: boolean;
  /** 处理的输入源标签 (source tags) */
  consumes: string[];
  /** 产出的输出源标签 */
  produces: string[];
  /** 该分析器的专用仪表板类型（null = 使用通用指标视图） */
  dashboard_type: "agent_loop" | "tool_execution" | "dedup_diagnostics" | "data_exfiltration" | "llm_api_anomaly" | "file_access_audit" | "token_cost" | "session_boundary" | "session_health" | "sse_content_scanner" | "intent_verifier" | "generic" | null;
}

/** 流水线阶段的状态快照 */
export interface PipelineStageState {
  stage: PipelineStage;
  /** 该阶段注册的分析器列表 */
  analyzers: StageAnalyzerInfo[];
  /** 事件吞吐量 (events/sec, 最近窗口) */
  throughput: number;
  /** 该阶段是否产生告警 */
  has_alerts: boolean;
}

/** 完整流水线状态 */
export interface PipelineStatus {
  stages: Record<PipelineStage, PipelineStageState>;
  /** 全流水线总吞吐量 (events/sec) */
  total_throughput: number;
  /** 最后一次状态更新时间 (epoch ms) */
  last_update_ms: number;
}

// ═══════════════════════════════════════════════════════════════════════════════
// 集成视图模式
// ═══════════════════════════════════════════════════════════════════════════════

export type IntegratedViewMode =
  | "pipeline_overview"   // 流水线概览：阶段状态 + 关键指标
  | "agent_loop"          // AgentLoopDetector 专用视图
  | "tool_execution"      // ToolExecutionTracker 专用视图
  | "dedup_diagnostics"   // SEDA 去重诊断视图
  | "data_exfiltration"   // DataExfiltrationDetector 专用视图
  | "llm_api_anomaly"     // LLMApiUsageAnomalyAnalyzer 专用视图
  | "file_access_audit"   // FileAccessAuditAnalyzer 专用视图
  | "token_cost"          // TokenCostAnalyzer 专用视图
  | "session_boundary"    // SessionBoundaryAnalyzer 专用视图
  | "session_health"      // SessionHealthMonitor 专用视图
  | "sse_content_scanner" // SseContentScanner SSE 注入扫描视图
  | "intent_verifier"     // AgentIntentVerifier 意图-行为不匹配视图
  | "cross_analysis";     // 跨分析器关联视图

// ═══════════════════════════════════════════════════════════════════════════════
// 交叉分析数据
// ═══════════════════════════════════════════════════════════════════════════════

/** 循环与工具调用的关联 */
export interface LoopToolCorrelation {
  /** 关联的工具名 */
  tool_name: string;
  /** 工具调用总数 */
  total_calls: number;
  /** 该工具的循环次数 */
  loop_count: number;
  /** 该工具的循环率 (loop_count / total_calls) */
  loop_rate: number;
  /** 循环类型分布 */
  loop_types: Record<LoopType, number>;
  /** 循环严重度分布 */
  loop_severities: Record<LoopSeverity, number>;
  /** 工具成功率 */
  tool_success_rate: number;
  /** 关联的 Agent PID */
  agent_pids: number[];
}

/** 去重效果按 source 类型分析 */
export interface DedupBySource {
  source: string;
  processed: number;
  suppressed: number;
  dedup_rate: number;
}

/** 交叉分析聚合数据 */
export interface CrossAnalysisData {
  /** 循环-工具关联列表 */
  loop_tool_correlations: LoopToolCorrelation[];
  /** 去重效果按 source 类型 */
  dedup_by_source: DedupBySource[];
  /** 全局事件缩减率 (去重后 / 去重前) */
  global_event_reduction: number;
  /** 循环相关的工具调用占所有工具调用的比例 */
  loop_affected_call_ratio: number;
}

// ═══════════════════════════════════════════════════════════════════════════════
// 集成仪表板全局状态
// ═══════════════════════════════════════════════════════════════════════════════

export interface IntegratedAnalyzerDashboardState {
  /** 当前视图模式 */
  view_mode: IntegratedViewMode;
  /** 流水线状态 */
  pipeline: PipelineStatus | null;

  // ── 子仪表板状态（由各子仪表板自行管理）──
  /** AgentLoop 仪表板状态 */
  agent_loop_state: import("./agent-loop-contract").AgentLoopDashboardState;
  /** ToolExecution 仪表板状态 */
  tool_exec_state: import("./tool-execution-contract").ToolExecutionDashboardState;
  /** Dedup 诊断状态 */
  dedup_state: import("./dedup-diagnostics-contract").DedupDiagnosticsState;
  /** DataExfil 外泄检测仪表板状态 */
  data_exfil_state: import("./data-exfiltration-contract").DataExfilDashboardState;
  /** LLM API 用量异常仪表板状态 */
  llm_api_anomaly_state: import("./llm-api-anomaly-contract").LlmApiAnomalyDashboardState;
  /** FileAccessAudit 文件审计仪表板状态 */
  file_access_audit_state: import("./file-access-audit-contract").FileAccessDashboardState;
  /** TokenCost 用量成本仪表板状态 */
  token_cost_state: import("./token-cost-contract").TokenCostDashboardState;
  /** SessionBoundary 会话边界仪表板状态 */
  session_boundary_state: import("./session-boundary-contract").SessionBoundaryDashboardState;
  /** SessionHealth 健康监控仪表板状态 */
  session_health_state: import("./session-health-contract").SessionHealthDashboardState;
  /** SseContentScanner SSE 注入扫描仪表板状态 */
  sse_scanner_state: import("./sse-content-scanner-contract").SseContentScannerDashboardState;
  /** AgentIntentVerifier 意图-行为不匹配仪表板状态 */
  intent_verifier_state: import("./intent-verifier-contract").IntentVerifierDashboardState;

  // ── 交叉分析数据 ──
  cross_analysis: CrossAnalysisData | null;

  /** 全局最后更新时间 (epoch ms) */
  last_update_ms: number;
}

// ═══════════════════════════════════════════════════════════════════════════════
// 组件 Props
// ═══════════════════════════════════════════════════════════════════════════════

export interface IntegratedAnalyzerDashboardProps {
  /** 全局状态 */
  state: IntegratedAnalyzerDashboardState;
  /** 切换视图模式 */
  onViewModeChange: (mode: IntegratedViewMode) => void;

  // ── AgentLoop 回调 ──
  onLoopViewModeChange: (mode: import("./agent-loop-contract").LoopDashboardViewMode) => void;
  onLoopSelect: (loop: LoopSummary | null) => void;
  onLoopTypeFilterChange: (type: LoopType | null) => void;
  onLoopSeverityFilterChange: (severity: LoopSeverity | null) => void;
  onLoopToolFilterChange: (tool_name: string | null) => void;
  onLoopPidFilterChange: (pid: number | null) => void;
  onShowActiveOnlyChange: (active_only: boolean) => void;

  // ── ToolExecution 回调 ──
  onToolViewModeChange: (mode: import("./tool-execution-contract").DashboardViewMode) => void;
  onAgentSelect: (pid: number | null) => void;
  onCallSelect: (call: ToolCallRecord) => void;
  onToolStatusFilterChange: (status: ToolCallStatus | null) => void;
  onToolFilterChange: (tool_name: string | null) => void;

  // ── DataExfil 回调 ──
  onExfilViewModeChange: (mode: import("./data-exfiltration-contract").ExfilDashboardViewMode) => void;
  onExfilAlertSelect: (alert: DataExfilAlert | null) => void;
  onExfilTypeFilterChange: (type: ExfilType | null) => void;
  onExfilSeverityFilterChange: (severity: ExfilSeverity | null) => void;
  onExfilAgentFilterChange: (pid: number | null) => void;
  onExfilDestinationFilterChange: (host: string | null) => void;

  // ── LLM API Anomaly 回调 ──
  onLlmApiViewModeChange: (mode: import("./llm-api-anomaly-contract").LlmApiDashboardViewMode) => void;
  onLlmApiAlertSelect: (alert: LlmApiAnomalyAlert | null) => void;
  onLlmApiTypeFilterChange: (type: AnomalyType | null) => void;
  onLlmApiSeverityFilterChange: (severity: LlmApiSeverity | null) => void;
  onLlmApiAgentFilterChange: (pid: number | null) => void;
  onLlmApiEndpointFilterChange: (endpoint: string | null) => void;

  // ── FileAccessAudit 回调 ──
  onFileAccessViewModeChange: (mode: import("./file-access-audit-contract").FileAccessViewMode) => void;
  onFileAccessRecordSelect: (record: FileAccessRecord | null) => void;
  onFileAccessTypeFilterChange: (type: FileAccessType | null) => void;
  onFileAccessRiskFilterChange: (risk: FileAccessRiskLevel | null) => void;
  onFileAccessBoundaryFilterChange: (boundary: WorkspaceBoundary | null) => void;
  onFileAccessAgentFilterChange: (pid: number | null) => void;
  onFileAccessShowCrossBoundaryOnlyChange: (show: boolean) => void;

  // ── TokenCost 回调 ──
  onTokenCostViewModeChange: (mode: import("./token-cost-contract").TokenCostViewMode) => void;
  onTokenCostAlertSelect: (alert: TokenAnomalyAlert | null) => void;
  onTokenCostTypeFilterChange: (type: TokenAnomalyType | null) => void;
  onTokenCostSeverityFilterChange: (severity: TokenAnomalySeverity | null) => void;
  onTokenCostAgentFilterChange: (pid: number | null) => void;
  onTokenCostProviderFilterChange: (provider: string | null) => void;

  // ── SessionBoundary 回调 ──
  onSessionBoundaryViewModeChange: (mode: import("./session-boundary-contract").SessionBoundaryViewMode) => void;
  onSessionBoundaryAlertSelect: (alert: SessionBoundaryAlert | null) => void;
  onSessionBoundaryTypeFilterChange: (type: SessionBoundaryAnomalyType | null) => void;
  onSessionBoundarySeverityFilterChange: (severity: SessionBoundarySeverity | null) => void;
  onSessionBoundaryAgentFilterChange: (pid: number | null) => void;
  onSessionBoundarySessionFilterChange: (session_key: string | null) => void;

  // ── SessionHealth 回调 ──
  onSessionHealthViewModeChange: (mode: import("./session-health-contract").SessionHealthViewMode) => void;
  onSessionHealthAlertSelect: (alert: SessionHealthAlert | null) => void;
  onSessionHealthSessionSelect: (session_id: string) => void;
  onSessionHealthAgentSelect: (pid: number | null) => void;
  onSessionHealthAlertTypeFilterChange: (type: HealthAlertType | null) => void;
  onSessionHealthSeverityFilterChange: (severity: HealthAlertSeverity | null) => void;
  onSessionHealthAgentFilterChange: (pid: number | null) => void;
  onSessionHealthMaxScoreFilterChange: (score: number | null) => void;
  onSessionHealthDimensionWeightsChange: (weights: Record<HealthDimension, number>) => void;

  // ── SseContentScanner 回调 ──
  onScannerViewModeChange: (mode: ScannerViewMode) => void;
  onScannerSignalSelect: (signal: SseScanSignal | null) => void;
  onScannerLayerFilterChange: (layer: DetectionLayer | null) => void;
  onScannerEventTypeFilterChange: (type: ScannedEventType | null) => void;
  onScannerToolFilterChange: (tool: string | null) => void;
  onScannerMinConfidenceChange: (confidence: number) => void;

  // ── IntentVerifier 回调 ──
  onIntentVerifierViewModeChange: (mode: IntentVerifierDashboardViewMode) => void;
  onIntentVerifierMismatchSelect: (mismatch: MismatchSummary | null) => void;
  onIntentVerifierTypeFilterChange: (type: MismatchType | null) => void;
  onIntentVerifierSeverityFilterChange: (severity: MismatchSeverity | null) => void;
  onIntentVerifierToolFilterChange: (tool_name: string | null) => void;
  onIntentVerifierPidFilterChange: (pid: number | null) => void;
  onIntentVerifierShowUnacknowledgedOnlyChange: (unacknowledged_only: boolean) => void;
  onIntentVerifierAcknowledgeMismatch?: (intent_id: string) => void;

  // ── 跨分析器导航回调 ──
  /** 从 AgentLoop 跳转到 ToolExecution（通过 tool_name） */
  onNavigateFromLoopToTool: (tool_name: string) => void;
  /** 从 DataExfil 跳转到 Agent（通过 agent_root_pid） */
  onNavigateFromExfilToAgent: (pid: number) => void;
  /** 从 LLM API Anomaly 跳转到 Agent（通过 agent_root_pid） */
  onNavigateFromLlmApiToAgent: (pid: number) => void;
  /** 从 LLM API Anomaly 跳转到端点过滤视图 */
  onNavigateFromLlmApiToEndpoint: (endpoint: string) => void;
  /** 从 SessionHealth 跳转到 ToolExecution（查看具体工具调用） */
  onNavigateFromSessionHealthToTool: (pid: number) => void;
  /** 从 SessionHealth 跳转到 AgentLoop（查看活跃循环） */
  onNavigateFromSessionHealthToLoop: (pid: number) => void;
  /** 从 SessionHealth 跳转到 TokenCost（查看 token 消耗） */
  onNavigateFromSessionHealthToTokenCost: (pid: number) => void;
  /** 从 SseContentScanner 跳转到时间线（定位原始 SSE 事件） */
  onNavigateFromScannerToTimeline: (timestamp_ns: number) => void;
  /** 从 SseContentScanner 跳转到工具执行仪表板 */
  onNavigateFromScannerToToolExecution: (tool_name: string) => void;
  /** 从任何位置跳转到指定视图 */
  onNavigateToView: (mode: IntegratedViewMode) => void;

  /** 紧凑模式（嵌入侧面板时使用） */
  compact?: boolean;
}

// ═══════════════════════════════════════════════════════════════════════════════
// 子组件 Props
// ═══════════════════════════════════════════════════════════════════════════════

/** 流水线概览 Props */
export interface PipelineOverviewProps {
  pipeline: PipelineStatus;
  /** 点击分析器跳转到其详情视图 */
  onAnalyzerClick: (analyzer: StageAnalyzerInfo) => void;
  compact?: boolean;
}

/** 交叉分析 Props */
export interface CrossAnalysisProps {
  data: CrossAnalysisData;
  /** 点击工具名跳转到 ToolExecutionDashboard */
  onToolNameClick: (tool_name: string) => void;
  compact?: boolean;
}

// ═══════════════════════════════════════════════════════════════════════════════
// 工具函数
// ═══════════════════════════════════════════════════════════════════════════════

/** 根据分析器 dashboard_type 映射到 IntegratedViewMode */
export function analyzerTypeToViewMode(
  dashboard_type: StageAnalyzerInfo["dashboard_type"],
): IntegratedViewMode {
  switch (dashboard_type) {
    case "agent_loop":
      return "agent_loop";
    case "tool_execution":
      return "tool_execution";
    case "dedup_diagnostics":
      return "dedup_diagnostics";
    case "data_exfiltration":
      return "data_exfiltration";
    case "llm_api_anomaly":
      return "llm_api_anomaly";
    case "file_access_audit":
      return "file_access_audit";
    case "token_cost":
      return "token_cost";
    case "session_boundary":
      return "session_boundary";
    case "session_health":
      return "session_health";
    case "sse_content_scanner":
      return "sse_content_scanner";
    case "intent_verifier":
      return "intent_verifier";
    default:
      return "pipeline_overview";
  }
}

/** 构建默认的流水线状态 */
export function createDefaultPipelineStatus(): PipelineStatus {
  return {
    stages: {
      Preprocessing: {
        stage: "Preprocessing",
        analyzers: [
          {
            id: "timestamp_normalizer",
            name: "TimestampNormalizer",
            active: true,
            consumes: ["ssl", "process", "system", "stdio"],
            produces: [],
            dashboard_type: null,
          },
          {
            id: "event_deduplication",
            name: "StreamingDedupAnalyzer",
            active: true,
            consumes: ["ssl", "process", "system", "stdio"],
            produces: ["dedup_dropped"],
            dashboard_type: "dedup_diagnostics",
          },
          {
            id: "auth_header_remover",
            name: "AuthHeaderRemover",
            active: true,
            consumes: ["http_parser"],
            produces: [],
            dashboard_type: null,
          },
          {
            id: "file_access_audit",
            name: "FileAccessAuditAnalyzer",
            active: true,
            consumes: ["process"],
            produces: ["file_access"],
            dashboard_type: "file_access_audit",
          },
        ],
        throughput: 0,
        has_alerts: false,
      },
      Parsing: {
        stage: "Parsing",
        analyzers: [
          {
            id: "sse_processor",
            name: "SSEProcessor",
            active: true,
            consumes: ["ssl"],
            produces: ["sse_processor"],
            dashboard_type: null,
          },
          {
            id: "http_parser",
            name: "HTTPParser",
            active: true,
            consumes: ["ssl"],
            produces: ["http_parser"],
            dashboard_type: null,
          },
        ],
        throughput: 0,
        has_alerts: false,
      },
      CausalLinking: {
        stage: "CausalLinking",
        analyzers: [
          {
            id: "http_filter",
            name: "HTTPFilter",
            active: true,
            consumes: ["http_parser"],
            produces: [],
            dashboard_type: null,
          },
          {
            id: "tool_execution_tracker",
            name: "ToolExecutionTracker",
            active: true,
            consumes: ["http_parser"],
            produces: ["tool_call", "tool_definition", "tool_stats"],
            dashboard_type: "tool_execution",
          },
        ],
        throughput: 0,
        has_alerts: false,
      },
      InjectionDetection: {
        stage: "InjectionDetection",
        analyzers: [
          {
            id: "handoff_injection",
            name: "HandoffInjectionAnalyzer",
            active: true,
            consumes: ["http_parser", "process"],
            produces: ["handoff_injection"],
            dashboard_type: null,
          },
          {
            id: "sse_content_scanner",
            name: "SseContentScanner",
            active: true,
            consumes: ["sse_processor", "http_parser"],
            produces: ["sse_scan_signal"],
            dashboard_type: "sse_content_scanner",
          },
        ],
        throughput: 0,
        has_alerts: false,
      },
      AnomalyDetection: {
        stage: "AnomalyDetection",
        analyzers: [
          {
            id: "multi_agent_anomaly",
            name: "MultiAgentAnomalyAnalyzer",
            active: true,
            consumes: ["handoff_injection", "file_access"],
            produces: ["anomaly"],
            dashboard_type: null,
          },
          {
            id: "agent_loop_detector",
            name: "AgentLoopDetector",
            active: true,
            consumes: ["http_parser", "sse_processor", "tool_call"],
            produces: ["agent_loop", "agent_loop_resolved"],
            dashboard_type: "agent_loop",
          },
          {
            id: "data_exfiltration_detector",
            name: "DataExfiltrationDetector",
            active: true,
            consumes: ["http_parser", "file_access", "ssl"],
            produces: ["data_exfil"],
            dashboard_type: "data_exfiltration",
          },
          {
            id: "llm_api_anomaly",
            name: "LLMApiUsageAnomalyAnalyzer",
            active: true,
            consumes: ["http_parser"],
            produces: ["llm_api_anomaly"],
            dashboard_type: "llm_api_anomaly",
          },
          {
            id: "token_cost",
            name: "TokenCostAnalyzer",
            active: true,
            consumes: ["http_parser", "sse_processor"],
            produces: ["token_cost_anomaly"],
            dashboard_type: "token_cost",
          },
          {
            id: "session_boundary",
            name: "SessionBoundaryAnalyzer",
            active: true,
            consumes: ["ssl", "sse_processor", "http_parser", "handoff_injection"],
            produces: ["session_boundary"],
            dashboard_type: "session_boundary",
          },
          {
            id: "session_health_monitor",
            name: "SessionHealthMonitor",
            active: true,
            consumes: ["latency", "tool_call", "http_parser", "process", "agent_loop", "credential_leak"],
            produces: ["session_health", "session_health_alert"],
            dashboard_type: "session_health",
          },
          {
            id: "agent_intent_verifier",
            name: "AgentIntentVerifier",
            active: true,
            consumes: ["tool_call", "http_parser", "file_access", "process", "ssl"],
            produces: ["intent_mismatch", "intent_verifier_summary"],
            dashboard_type: "intent_verifier",
          },
        ],
        throughput: 0,
        has_alerts: false,
      },
      AlertEnrichment: {
        stage: "AlertEnrichment",
        analyzers: [
          {
            id: "alert_enricher",
            name: "AlertEnrichmentAnalyzer",
            active: true,
            consumes: ["anomaly", "agent_loop", "llm_api_anomaly", "token_cost_anomaly", "session_boundary", "session_health_alert", "tool_stats"],
            produces: ["enriched_alert"],
            dashboard_type: null,
          },
          {
            id: "file_logger",
            name: "FileLogger",
            active: true,
            consumes: ["*"],
            produces: [],
            dashboard_type: null,
          },
        ],
        throughput: 0,
        has_alerts: false,
      },
    },
    total_throughput: 0,
    last_update_ms: Date.now(),
  };
}

/** 计算交叉分析数据 */
export function computeCrossAnalysis(
  loops: LoopSummary[],
  statsByPid: Map<number, AgentToolStats>,
  dedupMetrics: DedupMetricsSnapshot | null,
): CrossAnalysisData {
  // ── 循环-工具关联 ──
  const toolCallCounts: Record<string, number> = {};
  const toolSuccessCounts: Record<string, number> = {};
  for (const stats of statsByPid.values()) {
    for (const [tool, count] of Object.entries(stats.tool_frequency)) {
      toolCallCounts[tool] = (toolCallCounts[tool] || 0) + count;
    }
  }
  // 从 stats 推导每个工具的成功调用数（总调用 * 成功率）
  for (const stats of statsByPid.values()) {
    const successRate = stats.total_calls > 0 ? stats.successful_calls / stats.total_calls : 1;
    for (const [tool, count] of Object.entries(stats.tool_frequency)) {
      toolSuccessCounts[tool] = (toolSuccessCounts[tool] || 0) + Math.round(count * successRate);
    }
  }

  const loopByTool: Record<string, { count: number; types: Record<LoopType, number>; severities: Record<LoopSeverity, number>; pids: Set<number> }> = {};
  for (const loop of loops) {
    if (!loopByTool[loop.tool_name]) {
      loopByTool[loop.tool_name] = {
        count: 0,
        types: { ExactRepeat: 0, FuzzyRepeat: 0, PingPong: 0, RetryStorm: 0 },
        severities: { Warning: 0, Critical: 0, Emergency: 0 },
        pids: new Set(),
      };
    }
    loopByTool[loop.tool_name].count++;
    loopByTool[loop.tool_name].types[loop.loop_type]++;
    loopByTool[loop.tool_name].severities[loop.severity]++;
    for (const pid of loop.affected_pids) {
      loopByTool[loop.tool_name].pids.add(pid);
    }
  }

  const loop_tool_correlations: LoopToolCorrelation[] = Object.entries(loopByTool).map(
    ([tool_name, data]) => ({
      tool_name,
      total_calls: toolCallCounts[tool_name] || 0,
      loop_count: data.count,
      loop_rate: toolCallCounts[tool_name] ? data.count / toolCallCounts[tool_name] : 0,
      loop_types: data.types,
      loop_severities: data.severities,
      tool_success_rate: toolCallCounts[tool_name]
        ? (toolSuccessCounts[tool_name] || 0) / toolCallCounts[tool_name]
        : 0,
      agent_pids: Array.from(data.pids).sort((a, b) => a - b),
    }),
  );

  // ── 去重按 source ──
  const dedup_by_source: DedupBySource[] = [];
  if (dedupMetrics) {
    // SEDA 不按 source 分组暴露，这里用全量作为单个条目
    dedup_by_source.push({
      source: "all",
      processed: dedupMetrics.events_processed,
      suppressed: dedupMetrics.duplicates_suppressed,
      dedup_rate:
        dedupMetrics.events_processed + dedupMetrics.duplicates_suppressed > 0
          ? dedupMetrics.duplicates_suppressed /
            (dedupMetrics.events_processed + dedupMetrics.duplicates_suppressed)
          : 0,
    });
  }

  // ── 全局事件缩减率 ──
  const global_event_reduction = dedupMetrics
    ? dedupMetrics.duplicates_suppressed /
      (dedupMetrics.events_processed + dedupMetrics.duplicates_suppressed)
    : 0;

  // ── 循环影响比例 ──
  const allLoopCalls = loops.reduce((sum, l) => sum + l.repeat_count, 0);
  const allToolCalls = Object.values(toolCallCounts).reduce((a, b) => a + b, 0);
  const loop_affected_call_ratio = allToolCalls > 0 ? allLoopCalls / (allLoopCalls + allToolCalls) : 0;

  return {
    loop_tool_correlations,
    dedup_by_source,
    global_event_reduction,
    loop_affected_call_ratio,
  };
}

/** 创建空的集成状态 */
export function createDefaultIntegratedState(): IntegratedAnalyzerDashboardState {
  return {
    view_mode: "pipeline_overview",
    pipeline: createDefaultPipelineStatus(),
    agent_loop_state: {
      active_loops: [],
      resolved_loops: [],
      view_mode: "overview",
      selected_loop_id: null,
      type_filter: null,
      severity_filter: null,
      tool_filter: null,
      pid_filter: null,
      show_active_only: true,
      last_update_ms: Date.now(),
    },
    tool_exec_state: {
      agent_pids: [],
      selected_agent_pid: null,
      view_mode: "overview",
      stats_by_agent: new Map(),
      recent_calls: [],
      recent_definitions: [],
      status_filter: null,
      tool_filter: null,
      last_update_ms: Date.now(),
    },
    dedup_state: {
      current: null,
      history: [],
      recent_dropped: [],
      last_update_ms: Date.now(),
    },
    data_exfil_state: {
      alerts: [],
      view_mode: "overview",
      selected_alert_id: null,
      type_filter: null,
      severity_filter: null,
      agent_filter: null,
      destination_filter: null,
      last_update_ms: Date.now(),
    },
    llm_api_anomaly_state: {
      alerts: [],
      view_mode: "overview",
      selected_alert_uid: null,
      type_filter: null,
      severity_filter: null,
      agent_filter: null,
      endpoint_filter: null,
      last_update_ms: Date.now(),
    },
    file_access_audit_state: {
      recent_records: [],
      workspace_snapshot: null,
      view_mode: "overview",
      selected_record_id: null,
      type_filter: null,
      risk_filter: null,
      boundary_filter: null,
      agent_filter: null,
      show_cross_boundary_only: false,
      workspace_roots: [],
      last_update_ms: Date.now(),
    },
    token_cost_state: {
      alerts: [],
      global_snapshot: null,
      trend_data: [],
      view_mode: "overview",
      selected_alert_id: null,
      selected_agent_pid: null,
      type_filter: null,
      severity_filter: null,
      agent_filter: null,
      provider_filter: null,
      pricing_table: [
        { provider: "openai", input_price_per_1k: 0.0025, output_price_per_1k: 0.01, cache_input_price_per_1k: 0.00125 },
        { provider: "anthropic", input_price_per_1k: 0.003, output_price_per_1k: 0.015, cache_input_price_per_1k: 0.0003 },
        { provider: "google", input_price_per_1k: 0.00125, output_price_per_1k: 0.005, cache_input_price_per_1k: null },
      ],
      last_update_ms: Date.now(),
    },
    session_boundary_state: {
      alerts: [],
      snapshot: null,
      view_mode: "overview",
      selected_alert_id: null,
      selected_session_key: null,
      type_filter: null,
      severity_filter: null,
      agent_filter: null,
      session_filter: null,
      last_update_ms: Date.now(),
    },
    session_health_state: {
      recent_snapshots: [],
      fleet_overview: null,
      agent_summaries: new Map(),
      alerts: [],
      view_mode: "overview",
      selected_alert_id: null,
      selected_session_id: null,
      selected_agent_pid: null,
      selected_profile: null,
      alert_type_filter: null,
      severity_filter: null,
      agent_filter: null,
      max_health_score_filter: null,
      weights: { latency: 0.25, error_rate: 0.30, tool_health: 0.20, api_health: 0.15, resource: 0.10 },
      last_update_ms: Date.now(),
    },
    sse_scanner_state: createDefaultScannerState(),
    intent_verifier_state: createDefaultIntentVerifierState(),
    cross_analysis: null,
    last_update_ms: Date.now(),
  };
}
