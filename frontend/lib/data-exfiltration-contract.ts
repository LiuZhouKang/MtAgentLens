// DataExfiltrationDetector Dashboard — 可视化契约
// 对应 collector/src/framework/analyzers/data_exfiltration_detector 的输出事件格式
//
// 输出事件 source tag: "data_exfil"
// 四种外泄模式: ReadThenSend | LargeUpload | BatchExport | CovertChannel
// 四级严重度:    Low → Medium → High → Critical

import type { AnomalySeverity } from "./data-contract";

// ═══════════════════════════════════════════════════════════════════════════
// 外泄类型（与 Rust ExfilType 对齐）
// ═══════════════════════════════════════════════════════════════════════════

/** 外泄检测模式 */
export type ExfilType =
  | "ReadThenSend"   // 读取敏感文件后短时间内向外部发送内容匹配的请求
  | "LargeUpload"    // 单次 POST 请求体超过阈值
  | "BatchExport"    // 短时间内向同一外部端点累计发送超过阈值
  | "CovertChannel"; // 检测到分片/编码等隐蔽传输模式

/** 外泄类型的人类可读标签 */
export const EXFIL_TYPE_LABEL: Record<ExfilType, string> = {
  ReadThenSend: "读后即发",
  LargeUpload: "大容量上载",
  BatchExport: "批量导出",
  CovertChannel: "隐蔽通道",
};

/** 外泄类型的描述 */
export const EXFIL_TYPE_DESC: Record<ExfilType, string> = {
  ReadThenSend: "Agent 读取敏感文件后，短时间内向外部发送了内容匹配的请求",
  LargeUpload: "单次 POST 请求体超过阈值大小，可能包含敏感数据",
  BatchExport: "短时间内向同一外部端点累计发送大量数据",
  CovertChannel: "检测到分片传输、编码伪装等隐蔽数据外传模式",
};

// ═══════════════════════════════════════════════════════════════════════════
// 严重程度（与 Rust ExfilSeverity 对齐）
// ═══════════════════════════════════════════════════════════════════════════

/** 外泄告警严重程度 */
export type ExfilSeverity = "Low" | "Medium" | "High" | "Critical";

/** 严重程度标签 */
export const EXFIL_SEVERITY_LABEL: Record<ExfilSeverity, string> = {
  Low: "低",
  Medium: "中",
  High: "高",
  Critical: "严重",
};

/** ExfilSeverity → AnomalySeverity 映射（用于与现有异常系统互操作） */
export function exfilSeverityToAnomalySeverity(s: ExfilSeverity): AnomalySeverity {
  switch (s) {
    case "Low":
      return "info";
    case "Medium":
      return "warning";
    case "High":
      return "critical";
    case "Critical":
      return "critical";
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// 核心数据结构
// ═══════════════════════════════════════════════════════════════════════════

/** 外泄目标端点 */
export interface ExfilDestination {
  /** 目标主机 */
  host: string;
  /** 目标路径 */
  path: string;
  /** 目标端口 */
  port: number;
}

/** 关联事件引用（指向 file_access / http_parser 原始事件） */
export interface ExfilRelatedEvent {
  /** 事件来源标签 */
  source: string;
  /** 时间戳 (epoch ns) */
  timestamp_ns: number;
  /** 来源特定字段 */
  [key: string]: unknown;
}

/** 外泄证据摘要 */
export interface ExfilEvidence {
  /** 时间窗口内读取的敏感文件数 */
  file_read_count: number;
  /** 时间窗口内的对外请求数 */
  outbound_request_count: number;
  /** 去重后的目标端点数 */
  unique_destinations: number;
  /** 是否已排除 LLM API 流量 */
  llm_api_excluded: boolean;
}

/**
 * 单次外泄告警（source: "data_exfil"）
 * 与 Rust DataExfiltrationDetector 产出的 Event.data 结构对齐
 */
export interface DataExfilAlert {
  /** 告警唯一 ID (UUID v4) */
  alert_id: string;
  /** 外泄模式类型 */
  exfil_type: ExfilType;
  /** 严重程度 */
  severity: ExfilSeverity;
  /** 触发告警的 Agent root PID */
  agent_root_pid: number;
  /** 被读取的源文件路径（ReadThenSend 模式） */
  source_file: string | null;
  /** 外泄目标端点 */
  destination: ExfilDestination;
  /** 单次请求体大小 (bytes) */
  payload_size_bytes: number;
  /** 时间窗口内累计外传量 (bytes，批量导出时使用) */
  total_exported_bytes: number;
  /** 外泄行为的时间窗口 (ms) */
  time_window_ms: number;
  /** 内容匹配的摘要文本 */
  matched_content_excerpt: string | null;
  /** 内容匹配的长度 (bytes) */
  matched_content_length: number;
  /** 读取-发送关联置信度 (0–1) */
  correlation_score: number;
  /** 关联的原始事件列表 */
  related_events: ExfilRelatedEvent[];
  /** 证据摘要 */
  evidence: ExfilEvidence;

  // ── UI 层计算字段（客户端填充）──
  /** 告警触发时间 (epoch ms)，从 related_events 最早时间戳推导 */
  timestamp_epoch_ms: number;
  /** 是否仍处于活跃窗口内（告警未过期） */
  is_active: boolean;
}

// ═══════════════════════════════════════════════════════════════════════════
// 聚合统计数据（客户端计算）
// ═══════════════════════════════════════════════════════════════════════════

/** 每 Agent 的外泄统计 */
export interface AgentExfilStats {
  /** Agent root PID */
  agent_root_pid: number;
  /** 总告警数 */
  total_alerts: number;
  /** 按外泄类型的告警分布 */
  alerts_by_type: Record<ExfilType, number>;
  /** 按严重程度的告警分布 */
  alerts_by_severity: Record<ExfilSeverity, number>;
  /** 累计外传字节数 */
  total_exported_bytes: number;
  /** 去重后的目标端点列表 */
  unique_destinations: string[];
  /** 平均关联置信度 */
  avg_correlation_score: number;
  /** 最高严重程度 */
  max_severity: ExfilSeverity | null;
}

/** 每目标端点的外泄统计 */
export interface DestinationStats {
  /** 目标主机 */
  host: string;
  /** 该目标的告警数 */
  alert_count: number;
  /** 该目标的累计传输字节数 */
  total_bytes: number;
  /** 涉及的外泄类型 */
  exfil_types: ExfilType[];
  /** 最高严重程度 */
  max_severity: ExfilSeverity;
  /** 涉及的 Agent PID 列表 */
  affected_agents: number[];
}

// ═══════════════════════════════════════════════════════════════════════════
// SSE 流集成 — 扩展 StreamEnvelope 的 stream_type
// ═══════════════════════════════════════════════════════════════════════════

/** data_exfil 流类型 */
export type DataExfilStreamType = "data_exfil";

/** 扩展后的完整 stream_type（包含 data_exfil） */
export type ExtendedExfilStreamType =
  | import("./data-contract").UnifiedStreamEvent["stream_type"]
  | DataExfilStreamType;

// ═══════════════════════════════════════════════════════════════════════════
// 仪表板 UI 状态
// ═══════════════════════════════════════════════════════════════════════════

/** 仪表板视图模式 */
export type ExfilDashboardViewMode =
  | "overview"      // 概览：严重度分布 + 类型分布 + 统计摘要
  | "alerts"        // 告警列表：可过滤、可排序的告警卡片网格
  | "destinations"  // 目标分析：按目标端点聚合的统计视图
  | "timeline";     // 时间线：按时间排序的告警流

/** 仪表板全局状态 */
export interface DataExfilDashboardState {
  /** 当前活跃告警列表（环形缓冲区，最多保留 200 条） */
  alerts: DataExfilAlert[];
  /** 当前视图模式 */
  view_mode: ExfilDashboardViewMode;
  /** 选中的告警 ID（null = 无选中） */
  selected_alert_id: string | null;
  /** 外泄类型过滤器 */
  type_filter: ExfilType | null;
  /** 严重程度过滤器 */
  severity_filter: ExfilSeverity | null;
  /** Agent PID 过滤器 */
  agent_filter: number | null;
  /** 目标主机过滤器 */
  destination_filter: string | null;
  /** 最后更新时间 (epoch ms) */
  last_update_ms: number;
}

// ═══════════════════════════════════════════════════════════════════════════
// 组件 Props
// ═══════════════════════════════════════════════════════════════════════════

/** 主仪表板 Props */
export interface DataExfilDashboardProps {
  /** 仪表板状态 */
  state: DataExfilDashboardState;
  /** 切换视图模式 */
  onViewModeChange: (mode: ExfilDashboardViewMode) => void;
  /** 选中/取消选中告警 */
  onAlertSelect: (alert: DataExfilAlert | null) => void;
  /** 切换外泄类型过滤 */
  onTypeFilterChange: (type: ExfilType | null) => void;
  /** 切换严重程度过滤 */
  onSeverityFilterChange: (severity: ExfilSeverity | null) => void;
  /** 切换 Agent 过滤 */
  onAgentFilterChange: (pid: number | null) => void;
  /** 切换目标主机过滤 */
  onDestinationFilterChange: (host: string | null) => void;
  /** 从告警跳转到 ToolExecutionDashboard（通过 agent_pid） */
  onNavigateToAgent: (pid: number) => void;
  /** 紧凑模式（嵌入侧面板时使用） */
  compact?: boolean;
}

/** 告警卡片 Props */
export interface ExfilAlertCardProps {
  alert: DataExfilAlert;
  selected?: boolean;
  onSelect: () => void;
}

/** 告警详情面板 Props */
export interface ExfilAlertDetailProps {
  alert: DataExfilAlert;
  /** 点击 Agent PID 跳转 */
  onAgentClick: (pid: number) => void;
}

/** 外泄严重度徽章 Props */
export interface ExfilSeverityBadgeProps {
  severity: ExfilSeverity;
  /** 是否显示文字标签 */
  showLabel?: boolean;
}

/** 外泄类型图标 Props */
export interface ExfilTypeIconProps {
  exfil_type: ExfilType;
  size?: "sm" | "md";
}

/** 目标分布图 Props */
export interface DestinationChartProps {
  destinations: DestinationStats[];
  selected_host: string | null;
  onHostSelect: (host: string | null) => void;
}

/** 类型分布图 Props */
export interface ExfilTypeDistributionProps {
  distribution: Record<ExfilType, number>;
  selected_type: ExfilType | null;
  onTypeSelect: (type: ExfilType | null) => void;
}

/** Agent 统计摘要 Props */
export interface AgentExfilSummaryProps {
  stats: AgentExfilStats;
  highlighted?: boolean;
  onClick: () => void;
}

// ═══════════════════════════════════════════════════════════════════════════
// 工具函数
// ═══════════════════════════════════════════════════════════════════════════

/** 格式化字节数为人类可读字符串 */
export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

/** 格式化时间窗口为人类可读字符串 */
export function formatTimeWindow(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  return `${(ms / 60_000).toFixed(1)}min`;
}

/** 计算告警的时间戳 (epoch ms)，从 related_events 中提取最早时间戳 */
export function extractAlertTimestamp(alert: DataExfilAlert): number {
  if (alert.related_events.length === 0) return 0;
  const earliest = alert.related_events.reduce((min, e) =>
    e.timestamp_ns < min ? e.timestamp_ns : min,
    Infinity,
  );
  return Math.floor(earliest / 1_000_000); // ns → ms
}

/** 按 agent_root_pid 聚合告警统计 */
export function aggregateByAgent(alerts: DataExfilAlert[]): AgentExfilStats[] {
  const map = new Map<number, {
    total: number;
    byType: Record<ExfilType, number>;
    bySeverity: Record<ExfilSeverity, number>;
    totalBytes: number;
    destinations: Set<string>;
    corrScores: number[];
    maxSev: ExfilSeverity | null;
  }>();

  for (const alert of alerts) {
    const pid = alert.agent_root_pid;
    if (!map.has(pid)) {
      map.set(pid, {
        total: 0,
        byType: { ReadThenSend: 0, LargeUpload: 0, BatchExport: 0, CovertChannel: 0 },
        bySeverity: { Low: 0, Medium: 0, High: 0, Critical: 0 },
        totalBytes: 0,
        destinations: new Set(),
        corrScores: [],
        maxSev: null,
      });
    }
    const agg = map.get(pid)!;
    agg.total++;
    agg.byType[alert.exfil_type]++;
    agg.bySeverity[alert.severity]++;
    agg.totalBytes += alert.total_exported_bytes;
    agg.destinations.add(alert.destination.host);
    if (alert.correlation_score > 0) {
      agg.corrScores.push(alert.correlation_score);
    }
    // 跟踪最高严重程度
    const sevOrder: ExfilSeverity[] = ["Low", "Medium", "High", "Critical"];
    if (!agg.maxSev || sevOrder.indexOf(alert.severity) > sevOrder.indexOf(agg.maxSev)) {
      agg.maxSev = alert.severity;
    }
  }

  return Array.from(map.entries()).map(([pid, agg]) => ({
    agent_root_pid: pid,
    total_alerts: agg.total,
    alerts_by_type: agg.byType,
    alerts_by_severity: agg.bySeverity,
    total_exported_bytes: agg.totalBytes,
    unique_destinations: Array.from(agg.destinations).sort(),
    avg_correlation_score:
      agg.corrScores.length > 0
        ? agg.corrScores.reduce((a, b) => a + b, 0) / agg.corrScores.length
        : 0,
    max_severity: agg.maxSev,
  })).sort((a, b) => b.total_alerts - a.total_alerts);
}

/** 按目标主机聚合告警统计 */
export function aggregateByDestination(alerts: DataExfilAlert[]): DestinationStats[] {
  const map = new Map<string, {
    count: number;
    bytes: number;
    types: Set<ExfilType>;
    sevs: ExfilSeverity[];
    agents: Set<number>;
  }>();

  for (const alert of alerts) {
    const host = alert.destination.host;
    if (!map.has(host)) {
      map.set(host, {
        count: 0,
        bytes: 0,
        types: new Set(),
        sevs: [],
        agents: new Set(),
      });
    }
    const agg = map.get(host)!;
    agg.count++;
    agg.bytes += alert.total_exported_bytes;
    agg.types.add(alert.exfil_type);
    agg.sevs.push(alert.severity);
    agg.agents.add(alert.agent_root_pid);
  }

  const sevOrder: ExfilSeverity[] = ["Low", "Medium", "High", "Critical"];

  return Array.from(map.entries()).map(([host, agg]) => ({
    host,
    alert_count: agg.count,
    total_bytes: agg.bytes,
    exfil_types: Array.from(agg.types),
    max_severity: agg.sevs.reduce((max, s) =>
      sevOrder.indexOf(s) > sevOrder.indexOf(max) ? s : max,
      "Low" as ExfilSeverity,
    ),
    affected_agents: Array.from(agg.agents).sort((a, b) => a - b),
  })).sort((a, b) => b.total_bytes - a.total_bytes);
}

/** 计算严重程度分布百分比 */
export function severityDistribution(alerts: DataExfilAlert[]): Record<ExfilSeverity, { count: number; pct: number }> {
  const total = alerts.length || 1;
  const counts: Record<ExfilSeverity, number> = { Low: 0, Medium: 0, High: 0, Critical: 0 };
  for (const a of alerts) counts[a.severity]++;
  return {
    Low: { count: counts.Low, pct: counts.Low / total },
    Medium: { count: counts.Medium, pct: counts.Medium / total },
    High: { count: counts.High, pct: counts.High / total },
    Critical: { count: counts.Critical, pct: counts.Critical / total },
  };
}

/** 从告警列表中提取所有唯一的 Agent PID */
export function extractAgentPids(alerts: DataExfilAlert[]): number[] {
  return [...new Set(alerts.map((a) => a.agent_root_pid))].sort((a, b) => a - b);
}

/** 从告警列表中提取所有唯一的目标主机 */
export function extractDestinations(alerts: DataExfilAlert[]): string[] {
  return [...new Set(alerts.map((a) => a.destination.host))].sort();
}

/** 获取选中的告警对象 */
export function findSelectedAlert(
  alerts: DataExfilAlert[],
  selected_id: string | null,
): DataExfilAlert | null {
  if (!selected_id) return null;
  return alerts.find((a) => a.alert_id === selected_id) ?? null;
}

/** 创建空的仪表板状态 */
export function createDefaultExfilState(): DataExfilDashboardState {
  return {
    alerts: [],
    view_mode: "overview",
    selected_alert_id: null,
    type_filter: null,
    severity_filter: null,
    agent_filter: null,
    destination_filter: null,
    last_update_ms: Date.now(),
  };
}
