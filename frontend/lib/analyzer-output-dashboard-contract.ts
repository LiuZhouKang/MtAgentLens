// AnalyzerOutputDashboard — 通用分析器输出可视化契约
//
// 为任意新增分析器提供即插即用的仪表板视图。分析器只需声明其输出
// source tag 和事件 schema，即可获得完整的实时流可视化、统计摘要、
// 过滤钻取能力。无需为每个分析器单独编写仪表板代码。
//
// 适用场景：
//   - 新分析器原型阶段快速可视化验证
//   - 轻量级分析器（不 warrant 专用仪表板）
//   - 作为专用仪表板的基础模板
//
// 对应 Rust 侧：collector/src/framework/analyzers/ 中任意 Analyzer 实现

import type { AnomalySeverity } from './data-contract';

// ═══════════════════════════════════════════════════════════════════════════
// 分析器输出事件（通用信封）
// ═══════════════════════════════════════════════════════════════════════════

/**
 * 分析器产出的单条输出事件。
 * 由 Rust 侧 Analyzer::process() 产出的 Event { source, data } 映射而来。
 */
export interface AnalyzerOutputEvent {
  /** 事件唯一标识（全局递增序号） */
  event_id: number;
  /** 产出此事件的分析器 ID（如 "agent_loop_detector"） */
  analyzer_id: string;
  /** 事件的 source tag（如 "agent_loop", "data_exfil"） */
  source_tag: string;
  /** 事件发生时间戳 (epoch ms) */
  timestamp_ms: number;
  /** 关联的 Agent PID */
  agent_pid: number | null;
  /** 事件严重程度 */
  severity: AnalyzerEventSeverity;
  /** 人类可读的摘要（一行文本，用于列表展示） */
  summary: string;
  /** 事件分类标签 */
  category: string | null;
  /** 完整事件载荷（任意 JSON，用于详情展开） */
  payload: Record<string, unknown>;
  /** 关联的其他事件 ID（用于联动跳转） */
  related_event_ids: number[];
}

/** 分析器事件的 UI 严重程度 */
export type AnalyzerEventSeverity = 'info' | 'warning' | 'critical';

/** 严重程度到 AnomalySeverity 的映射（统一着色） */
export function analyzerSeverityToAnomalySeverity(s: AnalyzerEventSeverity): AnomalySeverity {
  switch (s) {
    case 'info':
      return 'info';
    case 'warning':
      return 'warning';
    case 'critical':
      return 'critical';
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// 分析器元信息（声明式注册）
// ═══════════════════════════════════════════════════════════════════════════

/** 单个分析器的注册信息 */
export interface AnalyzerRegistration {
  /** 分析器唯一标识（与 Rust Analyzer::name() 对齐） */
  id: string;
  /** 人类可读名称（中文） */
  display_name: string;
  /** 所属流水线阶段 */
  stage: PipelineStageId;
  /** 产出的 source tag 列表 */
  produces_tags: string[];
  /** 该分析器的描述 */
  description: string;
  /** 是否为活跃状态 */
  active: boolean;
}

/** 流水线阶段标识（与 PipelineStage 对齐） */
export type PipelineStageId =
  | 'preprocessing'
  | 'parsing'
  | 'causal_linking'
  | 'injection_detection'
  | 'anomaly_detection'
  | 'alert_enrichment';

/** 阶段中文标签 */
export const PIPELINE_STAGE_LABEL_CN: Record<PipelineStageId, string> = {
  preprocessing: '预处理',
  parsing: '解析',
  causal_linking: '因果关联',
  injection_detection: '注入检测',
  anomaly_detection: '异常检测',
  alert_enrichment: '告警丰富',
};

// ═══════════════════════════════════════════════════════════════════════════
// 分析器运行统计
// ═══════════════════════════════════════════════════════════════════════════

/** 单个分析器的实时运行统计 */
export interface AnalyzerRuntimeStats {
  /** 分析器 ID */
  analyzer_id: string;
  /** 已处理事件总数 */
  events_processed: number;
  /** 已产出事件总数 */
  events_produced: number;
  /** 当前吞吐量 (events/sec, 最近 5s 滑动窗口) */
  throughput: number;
  /** 按 source tag 分组的产出计数 */
  output_by_tag: Record<string, number>;
  /** 按严重程度分组的产出计数 */
  output_by_severity: Record<AnalyzerEventSeverity, number>;
  /** 平均处理延迟 (微秒) */
  avg_latency_us: number;
  /** P95 处理延迟 (微秒) */
  p95_latency_us: number;
  /** 错误计数 */
  error_count: number;
  /** 最后更新时间 (epoch ms) */
  last_update_ms: number;
}

/** 全流水线统计快照 */
export interface PipelineStatsSnapshot {
  /** 各分析器统计 */
  analyzers: Record<string, AnalyzerRuntimeStats>;
  /** 全流水线总吞吐量 */
  total_throughput: number;
  /** 各阶段吞吐量 */
  stage_throughput: Record<PipelineStageId, number>;
  /** 总事件产出数 */
  total_events_produced: number;
  /** 快照时间戳 */
  snapshot_ms: number;
}

// ═══════════════════════════════════════════════════════════════════════════
// 事件过滤与搜索
// ═══════════════════════════════════════════════════════════════════════════

/** 事件过滤器 */
export interface AnalyzerEventFilter {
  /** 按分析器 ID 过滤 */
  analyzer_ids: string[] | null;
  /** 按 source tag 过滤 */
  source_tags: string[] | null;
  /** 按严重程度过滤 */
  severities: AnalyzerEventSeverity[] | null;
  /** 按 Agent PID 过滤 */
  agent_pids: number[] | null;
  /** 按分类标签过滤 */
  categories: string[] | null;
  /** 时间窗口开始 (epoch ms, null = 无下限) */
  time_from_ms: number | null;
  /** 时间窗口结束 (epoch ms, null = 无上限) */
  time_to_ms: number | null;
  /** 文本搜索（匹配 summary 和 category） */
  text_search: string | null;
}

/** 创建默认（不过滤）的事件过滤器 */
export function createDefaultEventFilter(): AnalyzerEventFilter {
  return {
    analyzer_ids: null,
    source_tags: null,
    severities: null,
    agent_pids: null,
    categories: null,
    time_from_ms: null,
    time_to_ms: null,
    text_search: null,
  };
}

// ═══════════════════════════════════════════════════════════════════════════
// 仪表板视图模式
// ═══════════════════════════════════════════════════════════════════════════

export type AnalyzerOutputViewMode =
  | 'pipeline_overview' // 流水线总览：各分析器吞吐量 + 健康状态
  | 'event_feed' // 实时事件流：滚动列表
  | 'analyzer_detail' // 单分析器详情：统计 + 产出分布
  | 'statistics'; // 聚合统计图表

// ═══════════════════════════════════════════════════════════════════════════
// 仪表板全局状态
// ═══════════════════════════════════════════════════════════════════════════

export interface AnalyzerOutputDashboardState {
  /** 当前视图模式 */
  view_mode: AnalyzerOutputViewMode;
  /** 注册的分析器列表 */
  registered_analyzers: AnalyzerRegistration[];
  /** 各分析器实时统计 */
  runtime_stats: Record<string, AnalyzerRuntimeStats>;
  /** 全流水线快照 */
  pipeline_snapshot: PipelineStatsSnapshot | null;
  /** 事件环形缓冲区（最多保留 1000 条） */
  recent_events: AnalyzerOutputEvent[];
  /** 事件总数（包括已被环形缓冲区淘汰的） */
  total_event_count: number;
  /** 当前选中查看详情的分析器 ID */
  selected_analyzer_id: string | null;
  /** 当前选中查看详情的事件 */
  selected_event: AnalyzerOutputEvent | null;
  /** 活跃的事件过滤器 */
  event_filter: AnalyzerEventFilter;
  /** 是否暂停实时滚动（用于仔细查看某条事件） */
  feed_paused: boolean;
  /** 显示的 source tag 列表（用于图例/过滤面板） */
  available_source_tags: string[];
  /** 最后更新时间 (epoch ms) */
  last_update_ms: number;
}

// ═══════════════════════════════════════════════════════════════════════════
// 组件 Props
// ═══════════════════════════════════════════════════════════════════════════

export interface AnalyzerOutputDashboardProps {
  /** 仪表板状态 */
  state: AnalyzerOutputDashboardState;
  /** 切换视图模式 */
  onViewModeChange: (mode: AnalyzerOutputViewMode) => void;
  /** 选中分析器查看详情 */
  onAnalyzerSelect: (analyzer_id: string | null) => void;
  /** 选中事件查看详情（联动 DetailPanel） */
  onEventSelect: (event: AnalyzerOutputEvent | null) => void;
  /** 更新事件过滤器 */
  onFilterChange: (filter: Partial<AnalyzerEventFilter>) => void;
  /** 重置事件过滤器 */
  onFilterReset: () => void;
  /** 切换实时滚动暂停 */
  onFeedPauseToggle: () => void;
  /** 从事件跳转到关联的事件（通过 related_event_ids） */
  onNavigateToRelatedEvent: (event_id: number) => void;
  /** 从事件跳转到 Agent 视图 */
  onNavigateToAgent: (pid: number) => void;
  /** 紧凑模式 */
  compact?: boolean;
}

// ═══════════════════════════════════════════════════════════════════════════
// 子组件 Props
// ═══════════════════════════════════════════════════════════════════════════

/** 流水线概览面板 Props */
export interface PipelineOverviewPanelProps {
  /** 各阶段及其分析器 */
  stages: Record<
    PipelineStageId,
    {
      analyzers: AnalyzerRegistration[];
      throughput: number;
    }
  >;
  /** 各分析器的运行时统计 */
  runtime_stats: Record<string, AnalyzerRuntimeStats>;
  /** 点击分析器 */
  onAnalyzerClick: (analyzer_id: string) => void;
  compact?: boolean;
}

/** 分析器详情面板 Props */
export interface AnalyzerDetailPanelProps {
  /** 分析器注册信息 */
  analyzer: AnalyzerRegistration;
  /** 运行时统计 */
  stats: AnalyzerRuntimeStats;
  /** 该分析器的最近产出事件 */
  recent_events: AnalyzerOutputEvent[];
  /** 点击事件 */
  onEventClick: (event: AnalyzerOutputEvent) => void;
  /** 返回流水线概览 */
  onBack: () => void;
}

/** 实时事件流 Props */
export interface EventFeedPanelProps {
  /** 事件列表 */
  events: AnalyzerOutputEvent[];
  /** 事件总数 */
  total_count: number;
  /** 是否暂停 */
  paused: boolean;
  /** 当前过滤器 */
  filter: AnalyzerEventFilter;
  /** 可用的 source tag 列表 */
  available_source_tags: string[];
  /** 可用的分类标签 */
  available_categories: string[];
  /** 选中的事件 */
  selected_event_id: number | null;
  /** 事件点击 */
  onEventClick: (event: AnalyzerOutputEvent) => void;
  /** 暂停切换 */
  onPauseToggle: () => void;
  /** 过滤变更 */
  onFilterChange: (filter: Partial<AnalyzerEventFilter>) => void;
  /** 过滤重置 */
  onFilterReset: () => void;
  compact?: boolean;
}

/** 统计图表面板 Props */
export interface StatisticsPanelProps {
  /** 流水线快照 */
  snapshot: PipelineStatsSnapshot;
  /** 选中的分析器（高亮） */
  selected_analyzer_id: string | null;
  /** 点击分析器 */
  onAnalyzerClick: (analyzer_id: string) => void;
  compact?: boolean;
}

/** 单条事件卡片 Props */
export interface AnalyzerEventCardProps {
  event: AnalyzerOutputEvent;
  /** 是否选中 */
  selected?: boolean;
  /** 点击事件 */
  onSelect: () => void;
  /** 点击关联事件 */
  onRelatedEventClick: (event_id: number) => void;
  /** 点击 Agent PID */
  onAgentClick: (pid: number) => void;
  compact?: boolean;
}

/** 吞吐量迷你图 Props */
export interface ThroughputSparklineProps {
  /** 数据点 (timestamp_ms, value) */
  data: Array<[number, number]>;
  /** 图表宽度 */
  width: number;
  /** 图表高度 */
  height: number;
  /** 线条颜色 */
  color: string;
  /** 是否显示填充区域 */
  fill?: boolean;
}

// ═══════════════════════════════════════════════════════════════════════════
// 工具函数
// ═══════════════════════════════════════════════════════════════════════════

/** 从 AnalyzerRuntimeStats 计算生产/消费比率 */
export function calcProductionRatio(stats: AnalyzerRuntimeStats): number {
  if (stats.events_processed === 0) return 0;
  return stats.events_produced / stats.events_processed;
}

/** 从 AnalyzerRuntimeStats 计算错误率 */
export function calcErrorRate(stats: AnalyzerRuntimeStats): number {
  if (stats.events_processed === 0) return 0;
  return stats.error_count / stats.events_processed;
}

/** 格式化吞吐量为人类可读字符串 */
export function formatThroughput(eps: number): string {
  if (eps < 1) return '< 1/s';
  if (eps < 1000) return `${Math.round(eps)}/s`;
  return `${(eps / 1000).toFixed(1)}K/s`;
}

/** 格式化延迟 */
export function formatLatency(us: number): string {
  if (us < 1000) return `${Math.round(us)}μs`;
  if (us < 1_000_000) return `${(us / 1000).toFixed(1)}ms`;
  return `${(us / 1_000_000).toFixed(2)}s`;
}

/** 按严重程度排序权重（critical > warning > info） */
export function severitySortWeight(s: AnalyzerEventSeverity): number {
  switch (s) {
    case 'critical':
      return 0;
    case 'warning':
      return 1;
    case 'info':
      return 2;
  }
}

/** 判断事件是否匹配过滤器 */
export function eventMatchesFilter(
  event: AnalyzerOutputEvent,
  filter: AnalyzerEventFilter,
): boolean {
  if (filter.analyzer_ids !== null && !filter.analyzer_ids.includes(event.analyzer_id))
    return false;
  if (filter.source_tags !== null && !filter.source_tags.includes(event.source_tag)) return false;
  if (filter.severities !== null && !filter.severities.includes(event.severity)) return false;
  if (
    filter.agent_pids !== null &&
    (event.agent_pid === null || !filter.agent_pids.includes(event.agent_pid))
  )
    return false;
  if (
    filter.categories !== null &&
    (event.category === null || !filter.categories.includes(event.category))
  )
    return false;
  if (filter.time_from_ms !== null && event.timestamp_ms < filter.time_from_ms) return false;
  if (filter.time_to_ms !== null && event.timestamp_ms > filter.time_to_ms) return false;
  if (filter.text_search !== null) {
    const q = filter.text_search.toLowerCase();
    if (
      !event.summary.toLowerCase().includes(q) &&
      (event.category === null || !event.category.toLowerCase().includes(q))
    )
      return false;
  }
  return true;
}

/** 按 source tag 对事件分组 */
export function groupEventsBySourceTag(
  events: AnalyzerOutputEvent[],
): Map<string, AnalyzerOutputEvent[]> {
  const groups = new Map<string, AnalyzerOutputEvent[]>();
  for (const e of events) {
    const list = groups.get(e.source_tag) || [];
    list.push(e);
    groups.set(e.source_tag, list);
  }
  return groups;
}

/** 按严重程度对事件分组 */
export function groupEventsBySeverity(
  events: AnalyzerOutputEvent[],
): Record<AnalyzerEventSeverity, AnalyzerOutputEvent[]> {
  return {
    info: events.filter((e) => e.severity === 'info'),
    warning: events.filter((e) => e.severity === 'warning'),
    critical: events.filter((e) => e.severity === 'critical'),
  };
}

/** 创建空的仪表板状态 */
export function createDefaultAnalyzerOutputDashboardState(): AnalyzerOutputDashboardState {
  return {
    view_mode: 'pipeline_overview',
    registered_analyzers: [],
    runtime_stats: {},
    pipeline_snapshot: null,
    recent_events: [],
    total_event_count: 0,
    selected_analyzer_id: null,
    selected_event: null,
    event_filter: createDefaultEventFilter(),
    feed_paused: false,
    available_source_tags: [],
    last_update_ms: Date.now(),
  };
}
