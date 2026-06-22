// StreamingEventDeduplicationAnalyzer (SEDA) — 去重诊断面板契约
// 对应 collector/src/framework/analyzers/event_deduplication 的 metrics 输出
//
// SEDA 不产生需要可视化的事件流（duplicate events 被静默丢弃），
// 而是通过 ObservabilityMetricsAnalyzer 暴露 5 个指标：
//   dedup_events_processed     Counter  — 检查的事件总数
//   dedup_duplicates_suppressed Counter  — 丢弃的重复事件数
//   dedup_cache_size           Gauge    — 当前指纹缓存条目数
//   dedup_unparseable          Counter  — 无法解析 data 字段的事件数
//   dedup_passthrough_unknown_source Counter — 未知 source 透传的事件数
//
// 可选开启 emit_diagnostics = true 时，会额外产生：
//   dedup_dropped              Event    — 被丢弃的重复事件（批量发出，带 suppressed_count）

// ═══════════════════════════════════════════════════════════════════════════
// 核心数据结构
// ═══════════════════════════════════════════════════════════════════════════

/** SEDA 运行时指标快照 */
export interface DedupMetricsSnapshot {
  /** 检查的事件总数 */
  events_processed: number;
  /** 丢弃的重复事件数 */
  duplicates_suppressed: number;
  /** 当前指纹缓存条目数 */
  cache_size: number;
  /** 缓存容量上限 */
  cache_capacity: number;
  /** 无法解析 data 字段的事件数 */
  unparseable: number;
  /** 未知 source 透传的事件数 */
  passthrough_unknown_source: number;
  /** 快照时间戳 (epoch ms) */
  timestamp_ms: number;
}

/** dedup_dropped 诊断事件（仅 emit_diagnostics = true 时产生） */
export interface DedupDroppedEvent {
  /** 被丢弃的原始事件时间戳 (epoch ns) */
  timestamp_ns: number;
  /** 本次批量丢弃的重复事件数 */
  suppressed_count: number;
  /** 触发去重的指纹 source 类型 */
  source: string;
}

// ═══════════════════════════════════════════════════════════════════════════
// SSE 流集成
// ═══════════════════════════════════════════════════════════════════════════

export type DedupStreamType = "dedup_metrics" | "dedup_dropped";

// ═══════════════════════════════════════════════════════════════════════════
// 仪表板 UI 状态
// ═══════════════════════════════════════════════════════════════════════════

export interface DedupDiagnosticsState {
  /** 最近一次指标快照 */
  current: DedupMetricsSnapshot | null;
  /** 历史指标快照（环形缓冲区，最多保留 60 条 = 1 分钟 @1Hz） */
  history: DedupMetricsSnapshot[];
  /** 最近的 dedup_dropped 诊断事件（最多 100 条） */
  recent_dropped: DedupDroppedEvent[];
  /** 最后更新时间 (epoch ms) */
  last_update_ms: number;
}

// ═══════════════════════════════════════════════════════════════════════════
// 组件 Props
// ═══════════════════════════════════════════════════════════════════════════

export interface DedupDiagnosticsProps {
  /** 诊断状态 */
  state: DedupDiagnosticsState;
  /** 是否紧凑模式 */
  compact?: boolean;
  /** 是否显示诊断事件列表 */
  showDroppedEvents?: boolean;
}

// ═══════════════════════════════════════════════════════════════════════════
// 工具函数
// ═══════════════════════════════════════════════════════════════════════════

/** 计算去重率 (0–1) */
export function calcDedupRate(m: DedupMetricsSnapshot): number {
  if (m.events_processed === 0) return 0;
  return m.duplicates_suppressed / (m.events_processed + m.duplicates_suppressed);
}

/** 计算缓存使用率 (0–1) */
export function calcCacheUsage(m: DedupMetricsSnapshot): number {
  if (m.cache_capacity === 0) return 0;
  return m.cache_size / m.cache_capacity;
}

/** 计算错误率 (0–1) */
export function calcParseErrorRate(m: DedupMetricsSnapshot): number {
  const total = m.events_processed + m.duplicates_suppressed;
  if (total === 0) return 0;
  return m.unparseable / total;
}

/** 格式化百分比 */
export function formatPercent(rate: number): string {
  return `${(rate * 100).toFixed(1)}%`;
}

/** 创建空的指标快照 */
export function emptyMetricsSnapshot(cache_capacity: number = 10_000): DedupMetricsSnapshot {
  return {
    events_processed: 0,
    duplicates_suppressed: 0,
    cache_size: 0,
    cache_capacity,
    unparseable: 0,
    passthrough_unknown_source: 0,
    timestamp_ms: Date.now(),
  };
}
