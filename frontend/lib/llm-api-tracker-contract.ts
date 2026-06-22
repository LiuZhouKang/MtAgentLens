// LLM API Tracker Dashboard — 可视化契约
// 对应 collector/src/framework/analyzers/llm_api_tracker 的输出事件格式
//
// 输出事件 source tag: "llm_tracker"
// LLMApiTracker 将 HTTP 请求-响应对合并为单个富事件，包含：
//   provider, model, tokens, latency, status (complete/orphaned/error), streaming 标识
//
// 联动 key:
//   pid + tid        — 连接 ProcessTree（定位调用线程）
//   model            — 连接 TokenCostDashboard（按模型查看成本）
//   request_path     — 连接 TimelinePanel（定位 API 调用的原始 HTTP 事件）
//   timestamp_ns     — 连接 TimelinePanel（时间定位）
//   provider         — 连接 LlmApiAnomalyDashboard（按 provider 过滤异常）

import type { AnomalySeverity } from "./data-contract";

// ═══════════════════════════════════════════════════════════════════════════════
// Provider 与状态枚举
// ═══════════════════════════════════════════════════════════════════════════════

/** LLM API Provider 标识 */
export type LlmProvider =
  | "openai"
  | "anthropic"
  | "google"
  | "openrouter"
  | "openai-legacy"
  | "unknown";

/** 已知 provider 的 Logo/颜色映射 */
export const LLM_PROVIDER_LABEL: Record<LlmProvider, string> = {
  openai: "OpenAI",
  anthropic: "Anthropic",
  google: "Google",
  openrouter: "OpenRouter",
  "openai-legacy": "OpenAI (Legacy)",
  unknown: "未知",
};

export const LLM_PROVIDER_COLOR: Record<LlmProvider, string> = {
  openai: "text-emerald-600 bg-emerald-50 border-emerald-200",
  anthropic: "text-amber-600 bg-amber-50 border-amber-200",
  google: "text-blue-600 bg-blue-50 border-blue-200",
  openrouter: "text-purple-600 bg-purple-50 border-purple-200",
  "openai-legacy": "text-emerald-700 bg-emerald-100/50 border-emerald-300",
  unknown: "text-slate-500 bg-slate-50 border-slate-200",
};

export const LLM_PROVIDER_DOT: Record<LlmProvider, string> = {
  openai: "bg-emerald-400",
  anthropic: "bg-amber-400",
  google: "bg-blue-400",
  openrouter: "bg-purple-400",
  "openai-legacy": "bg-emerald-500",
  unknown: "bg-slate-400",
};

/** API 调用状态 — 对应 Rust LLMCallEvent.status */
export type LlmCallStatus =
  | "complete"   // 请求-响应成功配对
  | "orphaned"   // 超时未收到响应的请求
  | "error";     // 非 2xx 响应的调用

export const LLM_CALL_STATUS_LABEL: Record<LlmCallStatus, string> = {
  complete: "完成",
  orphaned: "孤立请求",
  error: "错误",
};

export const LLM_CALL_STATUS_ICON: Record<LlmCallStatus, string> = {
  complete: "✅",
  orphaned: "👻",
  error: "❌",
};

export const LLM_CALL_STATUS_COLOR: Record<LlmCallStatus, string> = {
  complete: "text-emerald-600 bg-emerald-50 border-emerald-200",
  orphaned: "text-amber-600 bg-amber-50 border-amber-200",
  error: "text-red-600 bg-red-50 border-red-200",
};

// ═══════════════════════════════════════════════════════════════════════════════
// 核心数据结构 — 对应 Rust LLMCallEvent
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * 单次 LLM API 调用追踪事件（source: "llm_tracker"）
 * 与 Rust LLMCallEvent 的 serialized JSON 结构对齐
 */
export interface LlmCallRecord {
  // ── 请求侧 ──
  /** LLM provider 标识 */
  provider: LlmProvider;
  /** 模型名称（如 "gpt-4", "claude-sonnet-4-6"），可能为 null */
  model: string | null;
  /** API 请求路径 */
  request_path: string;
  /** 请求时间戳 (ns) */
  request_timestamp_ns: number;
  /** 是否为流式请求 */
  is_streaming: boolean;
  /** 请求体中预估的 prompt tokens（Anthropic 等会在请求中携带） */
  prompt_tokens: number | null;

  // ── 响应侧 ──
  /** HTTP 状态码（orphaned 时为 null） */
  status_code: number | null;
  /** 响应时间戳 (ns)（orphaned 时为 null） */
  response_timestamp_ns: number | null;
  /** 请求-响应延迟 (ms)（orphaned 时为 null） */
  latency_ms: number | null;
  /** 响应体中的 completion tokens */
  completion_tokens: number | null;
  /** total tokens（prompt + completion） */
  total_tokens: number | null;

  // ── 元数据 ──
  /** HTTP 方法（通常为 POST） */
  http_method: string;
  /** 进程 PID */
  pid: number;
  /** 线程 TID */
  tid: number;
  /** 调用状态 */
  status: LlmCallStatus;
  /** 错误信息（status === "error" 时填充） */
  error_message: string | null;

  // ── UI 层计算字段 ──
  /** 记录唯一 ID（客户端生成） */
  record_id: string;
  /** 请求时间的 epoch ms（用于时间轴） */
  request_timestamp_ms: number;
  /** 格式化的延迟字符串（如 "1.2s", "340ms"） */
  latency_formatted: string;
}

// ═══════════════════════════════════════════════════════════════════════════════
// 聚合统计
// ═══════════════════════════════════════════════════════════════════════════════

/** 单 provider 的调用统计摘要 */
export interface ProviderCallStats {
  provider: LlmProvider;
  /** 总调用次数 */
  total_calls: number;
  /** 成功配对次数 */
  complete_calls: number;
  /** 孤立请求次数 */
  orphaned_calls: number;
  /** 错误次数 */
  error_calls: number;
  /** 流式请求比例 */
  streaming_ratio: number;
  /** 模型列表 */
  models: string[];
  /** 平均延迟 (ms) */
  avg_latency_ms: number;
  /** P50/P95/P99 延迟 (ms) */
  latency_p50_ms: number;
  latency_p95_ms: number;
  latency_p99_ms: number;
  /** 总 token 消耗 */
  total_input_tokens: number;
  total_output_tokens: number;
}

/** 单模型的调用统计摘要 */
export interface ModelCallStats {
  model: string;
  provider: LlmProvider;
  total_calls: number;
  complete_calls: number;
  orphaned_calls: number;
  error_calls: number;
  avg_latency_ms: number;
  total_input_tokens: number;
  total_output_tokens: number;
}

/** 单 agent 的 API 调用摘要 */
export interface AgentApiCallSummary {
  /** 关联的 agent root_pid（通过 process 事件推断或直接用 pid） */
  agent_root_pid: number;
  /** 进程名 */
  comm: string;
  /** 总调用次数 */
  total_calls: number;
  /** 按 provider 分布 */
  by_provider: Record<string, number>;
  /** 使用的模型列表 */
  models: string[];
  /** 平均延迟 (ms) */
  avg_latency_ms: number;
  /** 错误率 */
  error_rate: number;
  /** 孤立请求率 */
  orphan_rate: number;
  /** 总 token 消耗 */
  total_tokens: number;
  /** 最近调用时间 (epoch ms) */
  last_call_ms: number;
}

/** 全局 API 调用快照 */
export interface LlmApiTrackerSnapshot {
  /** 总记录数 */
  total_records: number;
  /** 按 provider 统计 */
  by_provider: Record<string, ProviderCallStats>;
  /** 按模型统计 */
  by_model: Record<string, ModelCallStats>;
  /** 按 agent 统计 */
  by_agent: AgentApiCallSummary[];
  /** 全局完成率 */
  global_completion_rate: number;
  /** 全局错误率 */
  global_error_rate: number;
  /** 全局孤立率 */
  global_orphan_rate: number;
  /** 全局平均延迟 (ms) */
  global_avg_latency_ms: number;
  /** 全局总 token 消耗 */
  global_total_tokens: number;
  /** 流式请求占比 */
  global_streaming_ratio: number;
}

// ═══════════════════════════════════════════════════════════════════════════════
// 仪表板视图模式
// ═══════════════════════════════════════════════════════════════════════════════

export type LlmTrackerDashboardViewMode =
  | "overview"        // 概览：统计卡片 + provider 分布 + 最近调用
  | "calls"           // 调用列表：可筛选、可排序的调用记录
  | "by_provider";    // Provider 分析：按 provider 聚合的延迟/错误/token 图表

// ═══════════════════════════════════════════════════════════════════════════════
// 仪表板全局状态
// ═══════════════════════════════════════════════════════════════════════════════

export interface LlmApiTrackerDashboardState {
  /** 当前视图模式 */
  view_mode: LlmTrackerDashboardViewMode;
  /** 全局快照 */
  snapshot: LlmApiTrackerSnapshot | null;
  /** 最近 N 条调用记录（环形缓冲区，最多 500 条） */
  recent_calls: LlmCallRecord[];
  /** 全局记录总数（包括被环形缓冲区淘汰的） */
  total_record_count: number;
  /** 当前选中的记录 ID */
  selected_record_id: string | null;
  /** provider 过滤器 */
  provider_filter: LlmProvider | null;
  /** 模型过滤器 */
  model_filter: string | null;
  /** agent PID 过滤器 */
  agent_filter: number | null;
  /** 状态过滤器 */
  status_filter: LlmCallStatus | null;
  /** 最低延迟过滤器 (ms)，null = 不过滤 */
  min_latency_filter: number | null;
  /** 流式/非流式过滤器 */
  streaming_filter: boolean | null;
  /** 最后更新时间 (epoch ms) */
  last_update_ms: number;
}

// ═══════════════════════════════════════════════════════════════════════════════
// 组件 Props
// ═══════════════════════════════════════════════════════════════════════════════

/** 主仪表板 Props */
export interface LlmApiTrackerDashboardProps {
  state: LlmApiTrackerDashboardState;
  /** 切换视图模式 */
  onViewModeChange: (mode: LlmTrackerDashboardViewMode) => void;
  /** 选中/取消选中记录 */
  onRecordSelect: (record: LlmCallRecord | null) => void;
  /** provider 过滤 */
  onProviderFilterChange: (provider: LlmProvider | null) => void;
  /** 模型过滤 */
  onModelFilterChange: (model: string | null) => void;
  /** agent 过滤 */
  onAgentFilterChange: (pid: number | null) => void;
  /** 状态过滤 */
  onStatusFilterChange: (status: LlmCallStatus | null) => void;
  /** 流式过滤切换 */
  onStreamingFilterChange: (streaming: boolean | null) => void;
  /** 联动跳转时间轴 */
  onNavigateToTimeline: (timestamp_ns: number) => void;
  /** 联动跳转进程树 */
  onNavigateToProcessTree: (pid: number) => void;
  /** 联动跳转 token 成本仪表板 */
  onNavigateToTokenCost: (model: string) => void;
  /** 联动跳转 LLM API 异常仪表板 */
  onNavigateToLlmApiAnomaly: (provider: string) => void;
  /** 紧凑模式（嵌入侧面板时使用） */
  compact?: boolean;
}

// ── 子组件 Props ──

/** 统计摘要卡片 */
export interface TrackerSummaryCardProps {
  label: string;
  value: number | string;
  unit?: string;
  highlight?: boolean;
  icon?: string;
  onClick?: () => void;
}

/** 单条调用记录卡片 */
export interface LlmCallCardProps {
  record: LlmCallRecord;
  /** 是否选中 */
  isSelected: boolean;
  /** 选中回调 */
  onSelect: () => void;
  /** 跳转时间轴 */
  onNavigateToTimeline: (timestamp_ns: number) => void;
  /** 紧凑模式 */
  compact?: boolean;
}

/** 调用记录详情面板 */
export interface LlmCallDetailProps {
  record: LlmCallRecord;
  /** 关闭面板 */
  onClose: () => void;
  /** 跳转时间轴 */
  onNavigateToTimeline: (timestamp_ns: number) => void;
  /** 跳转进程树 */
  onNavigateToProcessTree: (pid: number) => void;
  /** 跳转 token 成本 */
  onNavigateToTokenCost: (model: string) => void;
}

/** Provider 分布条形图 */
export interface ProviderDistributionProps {
  by_provider: Record<string, ProviderCallStats>;
  /** 当前选中的 provider */
  selected_provider: LlmProvider | null;
  /** provider 点击回调 */
  onProviderSelect: (provider: LlmProvider | null) => void;
}

/** 延迟分布直方图 */
export interface LatencyHistogramProps {
  records: LlmCallRecord[];
  /** 桶数量 */
  bucketCount?: number;
}

/** 筛选栏 */
export interface LlmTrackerFilterBarProps {
  provider_filter: LlmProvider | null;
  model_filter: string | null;
  status_filter: LlmCallStatus | null;
  streaming_filter: boolean | null;
  /** 可选的 provider 列表 */
  available_providers: LlmProvider[];
  /** 可选的模型列表 */
  available_models: string[];
  onProviderFilterChange: (provider: LlmProvider | null) => void;
  onModelFilterChange: (model: string | null) => void;
  onStatusFilterChange: (status: LlmCallStatus | null) => void;
  onStreamingFilterChange: (streaming: boolean | null) => void;
}

// ═══════════════════════════════════════════════════════════════════════════════
// 工具函数
// ═══════════════════════════════════════════════════════════════════════════════

/** 将 provider 字符串规范化为 LlmProvider */
export function normalizeProvider(raw: string): LlmProvider {
  const lower = raw.toLowerCase();
  if (lower.includes("openai") && lower.includes("legacy")) return "openai-legacy";
  if (lower.includes("openai") || lower.includes("chat/completions")) return "openai";
  if (lower.includes("anthropic") || lower.includes("messages")) return "anthropic";
  if (lower.includes("google") || lower.includes("gemini")) return "google";
  if (lower.includes("openrouter")) return "openrouter";
  return "unknown";
}

/** 格式化延迟 (ms) 为人类可读字符串 */
export function formatLatency(ms: number): string {
  if (ms < 1) return `${(ms * 1000).toFixed(0)}µs`;
  if (ms < 1000) return `${ms.toFixed(0)}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

/** 格式化纳秒为 epoch ms */
export function nsToEpochMs(ns: number): number {
  return Math.floor(ns / 1_000_000);
}

/** 格式化 token 数（大数用 k/M 缩写） */
export function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return `${n}`;
}

/** 从原始事件 payload 构建前端 LlmCallRecord（由 SSE 消费者调用） */
export function buildLlmCallRecord(
  raw: {
    provider: string;
    model?: string | null;
    request_path: string;
    request_timestamp_ns: number;
    is_streaming: boolean;
    prompt_tokens?: number | null;
    status_code?: number | null;
    response_timestamp_ns?: number | null;
    latency_ms?: number | null;
    completion_tokens?: number | null;
    total_tokens?: number | null;
    http_method: string;
    pid: number;
    tid: number;
    status: string;
    error_message?: string | null;
  },
  index: number,
): LlmCallRecord {
  const latency = raw.latency_ms ?? null;
  const requestTsMs = nsToEpochMs(raw.request_timestamp_ns);

  return {
    provider: normalizeProvider(raw.provider),
    model: raw.model ?? null,
    request_path: raw.request_path,
    request_timestamp_ns: raw.request_timestamp_ns,
    is_streaming: raw.is_streaming,
    prompt_tokens: raw.prompt_tokens ?? null,
    status_code: raw.status_code ?? null,
    response_timestamp_ns: raw.response_timestamp_ns ?? null,
    latency_ms: latency,
    completion_tokens: raw.completion_tokens ?? null,
    total_tokens: raw.total_tokens ?? null,
    http_method: raw.http_method,
    pid: raw.pid,
    tid: raw.tid,
    status: raw.status as LlmCallStatus,
    error_message: raw.error_message ?? null,
    record_id: `llm-call-${raw.pid}-${raw.tid}-${raw.request_timestamp_ns}-${index}`,
    request_timestamp_ms: requestTsMs,
    latency_formatted: latency !== null ? formatLatency(latency) : "—",
  };
}

/** 按 provider 聚合调用记录 */
export function aggregateByProvider(records: LlmCallRecord[]): Record<string, ProviderCallStats> {
  const map = new Map<string, {
    total: number;
    complete: number;
    orphaned: number;
    errors: number;
    streaming: number;
    models: Set<string>;
    latencies: number[];
    inputTokens: number;
    outputTokens: number;
  }>();

  for (const r of records) {
    const key = r.provider;
    if (!map.has(key)) {
      map.set(key, {
        total: 0, complete: 0, orphaned: 0, errors: 0,
        streaming: 0, models: new Set(),
        latencies: [], inputTokens: 0, outputTokens: 0,
      });
    }
    const agg = map.get(key)!;
    agg.total++;
    if (r.status === "complete") agg.complete++;
    if (r.status === "orphaned") agg.orphaned++;
    if (r.status === "error") agg.errors++;
    if (r.is_streaming) agg.streaming++;
    if (r.model) agg.models.add(r.model);
    if (r.latency_ms !== null) agg.latencies.push(r.latency_ms);
    if (r.prompt_tokens !== null) agg.inputTokens += r.prompt_tokens;
    if (r.completion_tokens !== null) agg.outputTokens += r.completion_tokens;
  }

  const result: Record<string, ProviderCallStats> = {};
  for (const [provider, agg] of map.entries()) {
    const sortedLat = agg.latencies.sort((a, b) => a - b);
    const p50 = sortedLat[Math.floor(sortedLat.length * 0.5)] ?? 0;
    const p95 = sortedLat[Math.floor(sortedLat.length * 0.95)] ?? 0;
    const p99 = sortedLat[Math.floor(sortedLat.length * 0.99)] ?? 0;
    const avg = sortedLat.length > 0
      ? sortedLat.reduce((a, b) => a + b, 0) / sortedLat.length
      : 0;

    result[provider] = {
      provider: normalizeProvider(provider),
      total_calls: agg.total,
      complete_calls: agg.complete,
      orphaned_calls: agg.orphaned,
      error_calls: agg.errors,
      streaming_ratio: agg.total > 0 ? agg.streaming / agg.total : 0,
      models: Array.from(agg.models).sort(),
      avg_latency_ms: avg,
      latency_p50_ms: p50,
      latency_p95_ms: p95,
      latency_p99_ms: p99,
      total_input_tokens: agg.inputTokens,
      total_output_tokens: agg.outputTokens,
    };
  }
  return result;
}

/** 按模型聚合调用记录 */
export function aggregateByModel(records: LlmCallRecord[]): Record<string, ModelCallStats> {
  const map = new Map<string, {
    provider: LlmProvider;
    total: number;
    complete: number;
    orphaned: number;
    errors: number;
    latencies: number[];
    inputTokens: number;
    outputTokens: number;
  }>();

  for (const r of records) {
    const key = r.model ?? "(unknown)";
    if (!map.has(key)) {
      map.set(key, {
        provider: r.provider,
        total: 0, complete: 0, orphaned: 0, errors: 0,
        latencies: [], inputTokens: 0, outputTokens: 0,
      });
    }
    const agg = map.get(key)!;
    agg.total++;
    if (r.status === "complete") agg.complete++;
    if (r.status === "orphaned") agg.orphaned++;
    if (r.status === "error") agg.errors++;
    if (r.latency_ms !== null) agg.latencies.push(r.latency_ms);
    if (r.prompt_tokens !== null) agg.inputTokens += r.prompt_tokens;
    if (r.completion_tokens !== null) agg.outputTokens += r.completion_tokens;
  }

  const result: Record<string, ModelCallStats> = {};
  for (const [model, agg] of map.entries()) {
    const avg = agg.latencies.length > 0
      ? agg.latencies.reduce((a, b) => a + b, 0) / agg.latencies.length
      : 0;

    result[model] = {
      model,
      provider: agg.provider,
      total_calls: agg.total,
      complete_calls: agg.complete,
      orphaned_calls: agg.orphaned,
      error_calls: agg.errors,
      avg_latency_ms: avg,
      total_input_tokens: agg.inputTokens,
      total_output_tokens: agg.outputTokens,
    };
  }
  return result;
}

/** 按 agent pid 聚合调用记录 */
export function aggregateByAgent(records: LlmCallRecord[]): AgentApiCallSummary[] {
  const map = new Map<number, {
    total: number;
    providerCounts: Map<string, number>;
    models: Set<string>;
    latencies: number[];
    errors: number;
    orphaned: number;
    totalTokens: number;
    lastMs: number;
  }>();

  for (const r of records) {
    const pid = r.pid;
    if (!map.has(pid)) {
      map.set(pid, {
        total: 0, providerCounts: new Map(), models: new Set(),
        latencies: [], errors: 0, orphaned: 0, totalTokens: 0, lastMs: 0,
      });
    }
    const agg = map.get(pid)!;
    agg.total++;
    agg.providerCounts.set(r.provider, (agg.providerCounts.get(r.provider) ?? 0) + 1);
    if (r.model) agg.models.add(r.model);
    if (r.latency_ms !== null) agg.latencies.push(r.latency_ms);
    if (r.status === "error") agg.errors++;
    if (r.status === "orphaned") agg.orphaned++;
    agg.totalTokens += (r.prompt_tokens ?? 0) + (r.completion_tokens ?? 0);
    if (r.request_timestamp_ms > agg.lastMs) agg.lastMs = r.request_timestamp_ms;
  }

  return Array.from(map.entries()).map(([pid, agg]) => ({
    agent_root_pid: pid,
    comm: `pid:${pid}`, // 可由调用方通过 process 事件补充实际 comm
    total_calls: agg.total,
    by_provider: Object.fromEntries(agg.providerCounts),
    models: Array.from(agg.models).sort(),
    avg_latency_ms: agg.latencies.length > 0
      ? agg.latencies.reduce((a, b) => a + b, 0) / agg.latencies.length
      : 0,
    error_rate: agg.total > 0 ? agg.errors / agg.total : 0,
    orphan_rate: agg.total > 0 ? agg.orphaned / agg.total : 0,
    total_tokens: agg.totalTokens,
    last_call_ms: agg.lastMs,
  })).sort((a, b) => b.total_calls - a.total_calls);
}

/** 构建全局快照 */
export function buildTrackerSnapshot(records: LlmCallRecord[]): LlmApiTrackerSnapshot {
  const byProvider = aggregateByProvider(records);
  const byModel = aggregateByModel(records);
  const byAgent = aggregateByAgent(records);

  const total = records.length;
  const complete = records.filter((r) => r.status === "complete").length;
  const errors = records.filter((r) => r.status === "error").length;
  const orphaned = records.filter((r) => r.status === "orphaned").length;
  const streaming = records.filter((r) => r.is_streaming).length;

  const allLatencies = records
    .filter((r) => r.latency_ms !== null)
    .map((r) => r.latency_ms!);

  return {
    total_records: total,
    by_provider: byProvider,
    by_model: byModel,
    by_agent: byAgent,
    global_completion_rate: total > 0 ? complete / total : 1,
    global_error_rate: total > 0 ? errors / total : 0,
    global_orphan_rate: total > 0 ? orphaned / total : 0,
    global_avg_latency_ms: allLatencies.length > 0
      ? allLatencies.reduce((a, b) => a + b, 0) / allLatencies.length
      : 0,
    global_total_tokens: records.reduce(
      (sum, r) => sum + (r.prompt_tokens ?? 0) + (r.completion_tokens ?? 0), 0,
    ),
    global_streaming_ratio: total > 0 ? streaming / total : 0,
  };
}

/** 从记录列表中提取所有唯一的 provider */
export function extractProviders(records: LlmCallRecord[]): LlmProvider[] {
  return [...new Set(records.map((r) => r.provider))].sort();
}

/** 从记录列表中提取所有唯一的模型 */
export function extractModels(records: LlmCallRecord[]): string[] {
  return [...new Set(records.map((r) => r.model).filter(Boolean) as string[])].sort();
}

/** 获取选中的记录 */
export function findSelectedRecord(
  records: LlmCallRecord[],
  selected_id: string | null,
): LlmCallRecord | null {
  if (!selected_id) return null;
  return records.find((r) => r.record_id === selected_id) ?? null;
}

/** 创建默认仪表板状态 */
export function createDefaultLlmApiTrackerState(): LlmApiTrackerDashboardState {
  return {
    view_mode: "overview",
    snapshot: null,
    recent_calls: [],
    total_record_count: 0,
    selected_record_id: null,
    provider_filter: null,
    model_filter: null,
    agent_filter: null,
    status_filter: null,
    min_latency_filter: null,
    streaming_filter: null,
    last_update_ms: Date.now(),
  };
}
