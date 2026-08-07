// LLMApiUsageAnomalyAnalyzer Dashboard — 可视化契约
// 对应 collector/src/framework/analyzers/llm_api_anomaly 的输出事件格式
//
// 输出事件 source tag: "llm_api_anomaly"
// 六种异常类型: RateBurst | RepeatedIdenticalRequest | OversizedRequest |
//               NewDestination | SustainedHighFrequency | ErrorStorm
// 三级严重度:   Info → Warning → Critical

import type { AnomalySeverity } from './data-contract';

// ═══════════════════════════════════════════════════════════════════════════
// 异常类型（与 Rust AnomalyType 对齐）
// ═══════════════════════════════════════════════════════════════════════════

/** LLM API 用量异常类型 */
export type AnomalyType =
  | 'RateBurst' // 短时间内调用次数过多（10s 内 >20 次）
  | 'RepeatedIdenticalRequest' // 相同 payload 重复发送（>5 次）
  | 'OversizedRequest' // 单次请求异常庞大（>1MB）
  | 'NewDestination' // 连接了之前未见过的 LLM API 端点
  | 'SustainedHighFrequency' // 持续高频率调用（5min 内 >100 次）
  | 'ErrorStorm'; // 错误状态码比例过高（>50%）

/** 异常类型的人类可读标签 */
export const ANOMALY_TYPE_LABEL: Record<AnomalyType, string> = {
  RateBurst: '速率突发',
  RepeatedIdenticalRequest: '重复请求',
  OversizedRequest: '超大请求',
  NewDestination: '新目标端点',
  SustainedHighFrequency: '持续高频',
  ErrorStorm: '错误风暴',
};

/** 异常类型的描述 */
export const ANOMALY_TYPE_DESC: Record<AnomalyType, string> = {
  RateBurst: '短时间内 LLM API 调用次数超过阈值，可能陷入无退避重试循环',
  RepeatedIdenticalRequest: '相同 payload 被重复发送多次，Agent 可能陷入确定性重试-失败循环',
  OversizedRequest: '单次 API 请求/响应体超过阈值，可能为上下文污染或数据外泄',
  NewDestination: 'Agent 连接了之前未记录过的 LLM API 端点，存在供应链风险',
  SustainedHighFrequency: '长时间窗口内持续高频率调用，Agent 进程可能失控',
  ErrorStorm: 'API 返回错误状态码的比例超过阈值，服务端可能拒绝请求或配置错误',
};

/** 异常类型的 emoji 图标 */
export const ANOMALY_TYPE_ICON: Record<AnomalyType, string> = {
  RateBurst: '⚡',
  RepeatedIdenticalRequest: '🔄',
  OversizedRequest: '📦',
  NewDestination: '🛰️',
  SustainedHighFrequency: '🔥',
  ErrorStorm: '💥',
};

// ═══════════════════════════════════════════════════════════════════════════
// 严重程度（与 Rust Severity 对齐）
// ═══════════════════════════════════════════════════════════════════════════

/** LLM API 异常严重程度 */
export type LlmApiSeverity = 'Info' | 'Warning' | 'Critical';

/** 严重程度标签 */
export const LLM_API_SEVERITY_LABEL: Record<LlmApiSeverity, string> = {
  Info: '信息',
  Warning: '警告',
  Critical: '严重',
};

/** LlmApiSeverity → AnomalySeverity 映射（用于与现有异常系统互操作） */
export function llmApiSeverityToAnomalySeverity(s: LlmApiSeverity): AnomalySeverity {
  switch (s) {
    case 'Info':
      return 'info';
    case 'Warning':
      return 'warning';
    case 'Critical':
      return 'critical';
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// 核心数据结构
// ═══════════════════════════════════════════════════════════════════════════

/**
 * 单次 LLM API 异常告警（source: "llm_api_anomaly"）
 * 与 Rust LLMApiUsageAnomalyAnalyzer 产出的 Event.data 结构对齐
 */
export interface LlmApiAnomalyAlert {
  /** 异常类型 */
  anomaly_type: AnomalyType;
  /** 触发告警的 Agent root PID */
  agent_root_pid: number;
  /** 严重程度 */
  severity: LlmApiSeverity;
  /** 检测窗口起始时间戳 (epoch ns) */
  window_start_ns: number;
  /** 检测窗口结束时间戳 (epoch ns) */
  window_end_ns: number;
  /** 窗口内 API 调用总数 */
  call_count: number;
  /** 触发阈值 */
  threshold: number;
  /** 窗口内平均调用间隔 (微秒) */
  avg_interval_us: number;
  /** 涉及的 LLM API 端点列表 (host/path) */
  affected_endpoints: string[];
  /** 采样 payload 哈希值（用于关联同一类重复请求） */
  sample_payload_hash: string | null;
  /** 人类可读的异常描述 */
  description: string;

  // ── UI 层计算字段（客户端填充）──
  /** 告警触发时间 (epoch ms) */
  timestamp_epoch_ms: number;
  /** 告警唯一 ID（客户端生成，用于选中/追踪） */
  alert_uid: string;
  /** 是否处于冷却期内（同一 agent + 同一 anomaly_type 的后续告警被抑制） */
  is_cooldown: boolean;
}

// ═══════════════════════════════════════════════════════════════════════════
// 聚合统计数据（客户端计算）
// ═══════════════════════════════════════════════════════════════════════════

/** 每 Agent 的 API 异常统计 */
export interface AgentApiAnomalyStats {
  /** Agent root PID */
  agent_root_pid: number;
  /** 总告警数 */
  total_alerts: number;
  /** 按异常类型的告警分布 */
  alerts_by_type: Record<AnomalyType, number>;
  /** 按严重程度的告警分布 */
  alerts_by_severity: Record<LlmApiSeverity, number>;
  /** 累计 API 调用数（所有窗口的 call_count 之和） */
  total_tracked_calls: number;
  /** 涉及的去重端点列表 */
  unique_endpoints: string[];
  /** 最高严重程度 */
  max_severity: LlmApiSeverity | null;
  /** 最近一次告警时间 (epoch ms) */
  last_alert_ms: number;
}

/** 每端点的异常统计 */
export interface EndpointAnomalyStats {
  /** LLM API 端点 (host/path) */
  endpoint: string;
  /** 该端点的告警数 */
  alert_count: number;
  /** 涉及的异常类型 */
  anomaly_types: AnomalyType[];
  /** 最高严重程度 */
  max_severity: LlmApiSeverity;
  /** 涉及的 Agent PID 列表 */
  affected_agents: number[];
  /** 告警中该端点是首次发现的次数 (NewDestination) */
  new_destination_alerts: number;
}

// ═══════════════════════════════════════════════════════════════════════════
// 仪表板 UI 状态
// ═══════════════════════════════════════════════════════════════════════════

/** 仪表板视图模式 */
export type LlmApiDashboardViewMode =
  | 'overview' // 概览：严重度分布 + 类型分布 + 统计摘要卡片
  | 'alerts' // 告警列表：可过滤、可排序的告警卡片 + 详情面板
  | 'endpoints'; // 端点分析：按 LLM API 端点聚合的统计视图

/** 仪表板全局状态 */
export interface LlmApiAnomalyDashboardState {
  /** 当前告警列表（环形缓冲区，最多保留 300 条） */
  alerts: LlmApiAnomalyAlert[];
  /** 当前视图模式 */
  view_mode: LlmApiDashboardViewMode;
  /** 选中的告警 UID（null = 无选中） */
  selected_alert_uid: string | null;
  /** 异常类型过滤器 */
  type_filter: AnomalyType | null;
  /** 严重程度过滤器 */
  severity_filter: LlmApiSeverity | null;
  /** Agent PID 过滤器 */
  agent_filter: number | null;
  /** 端点过滤器 */
  endpoint_filter: string | null;
  /** 最后更新时间 (epoch ms) */
  last_update_ms: number;
}

// ═══════════════════════════════════════════════════════════════════════════
// 组件 Props
// ═══════════════════════════════════════════════════════════════════════════

/** 主仪表板 Props */
export interface LlmApiAnomalyDashboardProps {
  /** 仪表板状态 */
  state: LlmApiAnomalyDashboardState;
  /** 切换视图模式 */
  onViewModeChange: (mode: LlmApiDashboardViewMode) => void;
  /** 选中/取消选中告警 */
  onAlertSelect: (alert: LlmApiAnomalyAlert | null) => void;
  /** 切换异常类型过滤 */
  onTypeFilterChange: (type: AnomalyType | null) => void;
  /** 切换严重程度过滤 */
  onSeverityFilterChange: (severity: LlmApiSeverity | null) => void;
  /** 切换 Agent 过滤 */
  onAgentFilterChange: (pid: number | null) => void;
  /** 切换端点过滤 */
  onEndpointFilterChange: (endpoint: string | null) => void;
  /** 从告警跳转到 ToolExecutionDashboard（通过 agent_pid） */
  onNavigateToAgent: (pid: number) => void;
  /** 从告警跳转到指定端点（在 endpoints 视图中高亮） */
  onNavigateToEndpoint: (endpoint: string) => void;
  /** 紧凑模式（嵌入侧面板时使用） */
  compact?: boolean;
}

/** 异常类型图标 Props */
export interface AnomalyTypeIconProps {
  anomaly_type: AnomalyType;
  size?: 'sm' | 'md' | 'lg';
}

/** 严重度徽章 Props */
export interface LlmApiSeverityBadgeProps {
  severity: LlmApiSeverity;
  showLabel?: boolean;
}

/** 告警卡片 Props */
export interface AnomalyAlertCardProps {
  alert: LlmApiAnomalyAlert;
  selected?: boolean;
  onSelect: () => void;
}

/** 告警详情面板 Props */
export interface AnomalyAlertDetailProps {
  alert: LlmApiAnomalyAlert;
  /** 点击 Agent PID 跳转到 ToolExecutionDashboard */
  onAgentClick: (pid: number) => void;
  /** 点击端点过滤当前视图 */
  onEndpointClick: (endpoint: string) => void;
}

/** 异常类型分布图 Props */
export interface AnomalyTypeDistributionProps {
  distribution: Record<AnomalyType, number>;
  selected_type: AnomalyType | null;
  onTypeSelect: (type: AnomalyType | null) => void;
}

// ═══════════════════════════════════════════════════════════════════════════
// 工具函数
// ═══════════════════════════════════════════════════════════════════════════

/** 格式化时间窗口为人类可读字符串 */
export function formatWindowUs(us: number): string {
  if (us < 1000) return `${us}µs`;
  if (us < 1_000_000) return `${(us / 1000).toFixed(1)}ms`;
  return `${(us / 1_000_000).toFixed(1)}s`;
}

/** 格式化纳秒为人类可读字符串 */
export function formatNs(ns: number): string {
  if (ns < 1000) return `${ns}ns`;
  if (ns < 1_000_000) return `${(ns / 1000).toFixed(1)}µs`;
  if (ns < 1_000_000_000) return `${(ns / 1_000_000).toFixed(1)}ms`;
  return `${(ns / 1_000_000_000).toFixed(1)}s`;
}

/** 格式化窗口时长为人类可读字符串 */
export function formatWindowDuration(ns: number): string {
  if (ns < 1_000_000_000) return `${(ns / 1_000_000).toFixed(0)}ms`;
  if (ns < 60_000_000_000) return `${(ns / 1_000_000_000).toFixed(1)}s`;
  return `${(ns / 60_000_000_000).toFixed(1)}min`;
}

/** 计算告警的窗口时长 (毫秒) */
export function calcWindowDurationMs(alert: LlmApiAnomalyAlert): number {
  return (alert.window_end_ns - alert.window_start_ns) / 1_000_000;
}

/** 按 agent_root_pid 聚合告警统计 */
export function aggregateByAgent(alerts: LlmApiAnomalyAlert[]): AgentApiAnomalyStats[] {
  const map = new Map<
    number,
    {
      total: number;
      byType: Record<AnomalyType, number>;
      bySeverity: Record<LlmApiSeverity, number>;
      totalCalls: number;
      endpoints: Set<string>;
      sevs: LlmApiSeverity[];
      lastMs: number;
    }
  >();

  const emptyTypeCounts = (): Record<AnomalyType, number> => ({
    RateBurst: 0,
    RepeatedIdenticalRequest: 0,
    OversizedRequest: 0,
    NewDestination: 0,
    SustainedHighFrequency: 0,
    ErrorStorm: 0,
  });

  const emptySevCounts = (): Record<LlmApiSeverity, number> => ({
    Info: 0,
    Warning: 0,
    Critical: 0,
  });

  for (const alert of alerts) {
    const pid = alert.agent_root_pid;
    if (!map.has(pid)) {
      map.set(pid, {
        total: 0,
        byType: emptyTypeCounts(),
        bySeverity: emptySevCounts(),
        totalCalls: 0,
        endpoints: new Set(),
        sevs: [],
        lastMs: 0,
      });
    }
    const agg = map.get(pid)!;
    agg.total++;
    agg.byType[alert.anomaly_type]++;
    agg.bySeverity[alert.severity]++;
    agg.totalCalls += alert.call_count;
    for (const ep of alert.affected_endpoints) agg.endpoints.add(ep);
    agg.sevs.push(alert.severity);
    if (alert.timestamp_epoch_ms > agg.lastMs) {
      agg.lastMs = alert.timestamp_epoch_ms;
    }
  }

  const sevOrder: LlmApiSeverity[] = ['Info', 'Warning', 'Critical'];

  return Array.from(map.entries())
    .map(([pid, agg]) => ({
      agent_root_pid: pid,
      total_alerts: agg.total,
      alerts_by_type: agg.byType,
      alerts_by_severity: agg.bySeverity,
      total_tracked_calls: agg.totalCalls,
      unique_endpoints: Array.from(agg.endpoints).sort(),
      max_severity: agg.sevs.reduce(
        (max, s) => (sevOrder.indexOf(s) > sevOrder.indexOf(max) ? s : max),
        'Info' as LlmApiSeverity,
      ),
      last_alert_ms: agg.lastMs,
    }))
    .sort((a, b) => b.total_alerts - a.total_alerts);
}

/** 按端点聚合告警统计 */
export function aggregateByEndpoint(alerts: LlmApiAnomalyAlert[]): EndpointAnomalyStats[] {
  const map = new Map<
    string,
    {
      count: number;
      types: Set<AnomalyType>;
      sevs: LlmApiSeverity[];
      agents: Set<number>;
      newDestCount: number;
    }
  >();

  for (const alert of alerts) {
    for (const endpoint of alert.affected_endpoints) {
      if (!map.has(endpoint)) {
        map.set(endpoint, {
          count: 0,
          types: new Set(),
          sevs: [],
          agents: new Set(),
          newDestCount: 0,
        });
      }
      const agg = map.get(endpoint)!;
      agg.count++;
      agg.types.add(alert.anomaly_type);
      agg.sevs.push(alert.severity);
      agg.agents.add(alert.agent_root_pid);
      if (alert.anomaly_type === 'NewDestination') agg.newDestCount++;
    }
  }

  const sevOrder: LlmApiSeverity[] = ['Info', 'Warning', 'Critical'];

  return Array.from(map.entries())
    .map(([endpoint, agg]) => ({
      endpoint,
      alert_count: agg.count,
      anomaly_types: Array.from(agg.types),
      max_severity: agg.sevs.reduce(
        (max, s) => (sevOrder.indexOf(s) > sevOrder.indexOf(max) ? s : max),
        'Info' as LlmApiSeverity,
      ),
      affected_agents: Array.from(agg.agents).sort((a, b) => a - b),
      new_destination_alerts: agg.newDestCount,
    }))
    .sort((a, b) => b.alert_count - a.alert_count);
}

/** 计算严重程度分布的计数和百分比 */
export function severityDistribution(
  alerts: LlmApiAnomalyAlert[],
): Record<LlmApiSeverity, { count: number; pct: number }> {
  const total = alerts.length || 1;
  const counts: Record<LlmApiSeverity, number> = { Info: 0, Warning: 0, Critical: 0 };
  for (const a of alerts) counts[a.severity]++;
  return {
    Info: { count: counts.Info, pct: counts.Info / total },
    Warning: { count: counts.Warning, pct: counts.Warning / total },
    Critical: { count: counts.Critical, pct: counts.Critical / total },
  };
}

/** 计算异常类型分布的计数 */
export function anomalyTypeCounts(alerts: LlmApiAnomalyAlert[]): Record<AnomalyType, number> {
  const counts: Record<AnomalyType, number> = {
    RateBurst: 0,
    RepeatedIdenticalRequest: 0,
    OversizedRequest: 0,
    NewDestination: 0,
    SustainedHighFrequency: 0,
    ErrorStorm: 0,
  };
  for (const a of alerts) counts[a.anomaly_type]++;
  return counts;
}

/** 获取告警列表中的最高严重程度 */
export function maxSeverity(alerts: LlmApiAnomalyAlert[]): LlmApiSeverity | null {
  if (alerts.length === 0) return null;
  const order: LlmApiSeverity[] = ['Info', 'Warning', 'Critical'];
  return alerts.reduce(
    (max, a) => (order.indexOf(a.severity) > order.indexOf(max) ? a.severity : max),
    'Info' as LlmApiSeverity,
  );
}

/** 检测告警中是否有 Critical 级别的异常 */
export function hasCritical(alerts: LlmApiAnomalyAlert[]): boolean {
  return alerts.some((a) => a.severity === 'Critical');
}

/** 从告警列表中提取所有唯一的 Agent PID */
export function extractAgentPids(alerts: LlmApiAnomalyAlert[]): number[] {
  return [...new Set(alerts.map((a) => a.agent_root_pid))].sort((a, b) => a - b);
}

/** 从告警列表中提取所有唯一的端点 */
export function extractEndpoints(alerts: LlmApiAnomalyAlert[]): string[] {
  const eps = new Set<string>();
  for (const a of alerts) {
    for (const ep of a.affected_endpoints) eps.add(ep);
  }
  return Array.from(eps).sort();
}

/** 获取选中的告警对象 */
export function findSelectedAlert(
  alerts: LlmApiAnomalyAlert[],
  selected_uid: string | null,
): LlmApiAnomalyAlert | null {
  if (!selected_uid) return null;
  return alerts.find((a) => a.alert_uid === selected_uid) ?? null;
}

/** 从原始事件 payload 构建前端告警对象（由 SSE 消费者调用） */
export function buildAnomalyAlert(
  raw: {
    anomaly_type: string;
    agent_root_pid: number;
    severity: string;
    window_start_ns: number;
    window_end_ns: number;
    call_count: number;
    threshold: number;
    avg_interval_us: number;
    affected_endpoints: string[];
    sample_payload_hash?: string | null;
    description: string;
  },
  alertIndex: number,
): LlmApiAnomalyAlert {
  return {
    anomaly_type: raw.anomaly_type as AnomalyType,
    agent_root_pid: raw.agent_root_pid,
    severity: raw.severity as LlmApiSeverity,
    window_start_ns: raw.window_start_ns,
    window_end_ns: raw.window_end_ns,
    call_count: raw.call_count,
    threshold: raw.threshold,
    avg_interval_us: raw.avg_interval_us,
    affected_endpoints: raw.affected_endpoints ?? [],
    sample_payload_hash: raw.sample_payload_hash ?? null,
    description: raw.description,
    timestamp_epoch_ms: Math.floor(raw.window_end_ns / 1_000_000),
    alert_uid: `llm-anomaly-${raw.agent_root_pid}-${raw.window_end_ns}-${alertIndex}`,
    is_cooldown: false,
  };
}

/** 创建空的仪表板状态 */
export function createDefaultLlmApiAnomalyState(): LlmApiAnomalyDashboardState {
  return {
    alerts: [],
    view_mode: 'overview',
    selected_alert_uid: null,
    type_filter: null,
    severity_filter: null,
    agent_filter: null,
    endpoint_filter: null,
    last_update_ms: Date.now(),
  };
}
