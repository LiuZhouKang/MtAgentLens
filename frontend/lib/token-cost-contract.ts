// TokenCostAnalyzer Dashboard — 可视化契约
// 对应 collector/src/framework/analyzers/token_cost_analyzer 的输出事件格式
//
// 输出事件 source tag: "token_cost_anomaly"
// 四种异常类型: LargeRequest | OutputBloat | CumulativeOverrun | GlobalRateSpike
// 三级严重度:   Info → Warning → Critical
// 两种 token 来源: Header | BodyEstimate

import type { AnomalySeverity } from "./data-contract";

// ═══════════════════════════════════════════════════════════════════════════
// 异常类型（与 Rust anomaly_type 对齐）
// ═══════════════════════════════════════════════════════════════════════════

/** Token 用量异常类型 */
export type TokenAnomalyType =
  | "LargeRequest"       // 单次请求输入 token 超过阈值
  | "OutputBloat"        // 输出/输入 token 比例异常
  | "CumulativeOverrun"  // 滑动窗口内累计 token 消耗超过阈值
  | "GlobalRateSpike";   // 全局 token 消耗速率超过阈值

export const TOKEN_ANOMALY_TYPE_LABEL: Record<TokenAnomalyType, string> = {
  LargeRequest: "超大请求",
  OutputBloat: "输出膨胀",
  CumulativeOverrun: "累计超限",
  GlobalRateSpike: "全局速率激增",
};

export const TOKEN_ANOMALY_TYPE_DESC: Record<TokenAnomalyType, string> = {
  LargeRequest: "单次 LLM API 请求的输入 token 数超过阈值，可能为上下文污染或 prompt 注入",
  OutputBloat: "输出 token 数远超输入 token 数，Agent 可能在输出中泄露大量内部状态",
  CumulativeOverrun: "在观测窗口内累计 token 消耗超过阈值，Agent 可能陷入推理循环",
  GlobalRateSpike: "全局 token 消耗速率异常飙升，可能为多 Agent 并行失控",
};

export const TOKEN_ANOMALY_TYPE_ICON: Record<TokenAnomalyType, string> = {
  LargeRequest: "🐘",
  OutputBloat: "💥",
  CumulativeOverrun: "📈",
  GlobalRateSpike: "🚨",
};

// ═══════════════════════════════════════════════════════════════════════════
// 严重程度与来源
// ═══════════════════════════════════════════════════════════════════════════

/** Token 告警严重程度（与 Rust severity 对齐） */
export type TokenAnomalySeverity = "info" | "warning" | "critical";

export const TOKEN_SEVERITY_LABEL: Record<TokenAnomalySeverity, string> = {
  info: "信息",
  warning: "警告",
  critical: "严重",
};

/** Token 计数来源（与 Rust TokenCountSource 对齐） */
export type TokenCountSource = "Header" | "BodyEstimate";

export const TOKEN_SOURCE_LABEL: Record<TokenCountSource, string> = {
  Header: "Header 精确提取",
  BodyEstimate: "Body 估算",
};

/** TokenCountSource → 置信度 (0-1) */
export function tokenSourceConfidence(source: TokenCountSource): number {
  return source === "Header" ? 1.0 : 0.8;
}

// ═══════════════════════════════════════════════════════════════════════════
// 核心数据结构
// ═══════════════════════════════════════════════════════════════════════════

/**
 * 单次 Token 用量告警（source: "token_cost_anomaly"）
 * 与 Rust TokenCostAnalyzer 产出的 Event.data 结构对齐
 */
export interface TokenAnomalyAlert {
  /** 告警唯一 ID */
  alert_id: string;
  /** 异常类型 */
  anomaly_type: TokenAnomalyType;
  /** 严重程度 */
  severity: TokenAnomalySeverity;
  /** 触发告警的 Agent root PID */
  agent_root_pid: number;
  /** 输入 token 数 */
  input_tokens: number;
  /** 输出 token 数 */
  output_tokens: number;
  /** 触发告警的阈值 */
  threshold: number;
  /** API provider 标识（openai / anthropic / google / ...） */
  provider: string;
  /** 模型名称（如 "gpt-4", "claude-sonnet-4-6"） */
  model: string | null;
  /** 本次调用估算成本 (USD) */
  estimated_cost_usd: number;
  /** Token 计数来源 */
  token_source: TokenCountSource;
  /** 端点路径 */
  endpoint: string;
  /** HTTP 状态码 */
  status_code: number;
  /** 当前会话累计输入 token 数 */
  cumulative_session_input_tokens: number;
  /** 当前会话累计成本 (USD) */
  cumulative_session_cost_usd: number;
  /** 人类可读描述 */
  description: string;
  /** 进程名 */
  comm: string;
  /** 时间戳 (epoch ns) */
  timestamp_ns: number;
}

// ═══════════════════════════════════════════════════════════════════════════
// 聚合统计
// ═══════════════════════════════════════════════════════════════════════════

/** 单个 agent 的 token 用量摘要 */
export interface AgentTokenUsageSummary {
  agent_root_pid: number;
  /** 累计输入 token */
  total_input_tokens: number;
  /** 累计输出 token */
  total_output_tokens: number;
  /** 累计成本 (USD) */
  total_cost_usd: number;
  /** API 调用次数 */
  call_count: number;
  /** 最近 token 速率 (tok/s) */
  recent_token_rate: number;
  /** 按 provider 分组的统计 */
  by_provider: Record<string, ProviderTokenSummary>;
  /** 告警次数 */
  alert_count: number;
  /** 最近活跃时间 (epoch ns) */
  last_active_ns: number;
}

/** 按 provider 的 token 摘要 */
export interface ProviderTokenSummary {
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  call_count: number;
}

/** 全局 token 用量快照 */
export interface GlobalTokenSnapshot {
  /** 累计输入 token */
  total_input_tokens: number;
  /** 累计输出 token */
  total_output_tokens: number;
  /** 累计成本 (USD) */
  total_cost_usd: number;
  /** 总 API 调用次数 */
  total_calls: number;
  /** 当前全局 token 速率 (tok/s) */
  recent_token_rate: number;
  /** 按 provider 分组的全局统计 */
  by_provider: Record<string, ProviderTokenSummary>;
  /** 活跃 agent 数 */
  active_agent_count: number;
  /** 按 agent 的摘要列表 */
  by_agent: AgentTokenUsageSummary[];
}

/** LLM API provider 定价信息 */
export interface ProviderPricingInfo {
  /** provider 标识 */
  provider: string;
  /** 输入价格 (USD per 1K tokens) */
  input_price_per_1k: number;
  /** 输出价格 (USD per 1K tokens) */
  output_price_per_1k: number;
  /** 缓存命中输入价格（可选） */
  cache_input_price_per_1k: number | null;
}

/** Token 用量趋势数据点 */
export interface TokenTrendDataPoint {
  /** 时间戳 (epoch ns) */
  timestamp_ns: number;
  /** 此时间点的输入 token 累计 */
  cumulative_input: number;
  /** 此时间点的输出 token 累计 */
  cumulative_output: number;
  /** 此时间点的成本累计 (USD) */
  cumulative_cost: number;
  /** 此窗口的 token 速率 (tok/s) */
  token_rate: number;
}

// ═══════════════════════════════════════════════════════════════════════════
// 仪表板状态
// ═══════════════════════════════════════════════════════════════════════════

export type TokenCostViewMode = "overview" | "alerts" | "by_agent" | "by_provider" | "trends";

export interface TokenCostDashboardState {
  /** 告警列表 */
  alerts: TokenAnomalyAlert[];
  /** 全局快照 */
  global_snapshot: GlobalTokenSnapshot | null;
  /** 趋势数据点（最近 N 个窗口） */
  trend_data: TokenTrendDataPoint[];
  /** 当前视图模式 */
  view_mode: TokenCostViewMode;
  /** 选中的告警 ID */
  selected_alert_id: string | null;
  /** 选中的 agent PID */
  selected_agent_pid: number | null;
  /** 异常类型过滤器 */
  type_filter: TokenAnomalyType | null;
  /** 严重程度过滤器 */
  severity_filter: TokenAnomalySeverity | null;
  /** agent 过滤器 */
  agent_filter: number | null;
  /** provider 过滤器 */
  provider_filter: string | null;
  /** 已知的 provider 定价表 */
  pricing_table: ProviderPricingInfo[];
  /** 最后更新时间 (epoch ms) */
  last_update_ms: number;
}

/** 创建默认状态 */
export function createDefaultTokenCostState(): TokenCostDashboardState {
  return {
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
  };
}
