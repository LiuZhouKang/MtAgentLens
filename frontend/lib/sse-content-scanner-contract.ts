// SSE Content Scanner Dashboard — 契约定义
//
// SseContentScanner 扫描 SSE 文本事件（tool_result, content_block_delta, text_delta）
// 检测间接注入载荷，补充 ContentBoundary 只能看到 Intent.tool_args 的盲区。
//
// 双重检测层：
//   Layer 1 — 模式匹配（复用 scan_text_layer1 的已知注入模式）
//   Layer 4 — OOD 异常检测（OnlineOodDetector 语义离群）
//
// 联动 key:
//   tool_name   — 连接 ToolExecutionDashboard（查看触发工具的具体调用）
//   timestamp_ns — 连接 TimelinePanel（定位原始 SSE 事件）
//   signal_id   — 连接 EvidencePanel（查看证据详情）

import type { AnomalySeverity } from './data-contract';

// ═══════════════════════════════════════════════════════════════════════════════
// 检测层类型
// ═══════════════════════════════════════════════════════════════════════════════

/** 检测层：对应 Rust 侧 MatchLayer */
export type DetectionLayer =
  | 'pattern' // Layer1: 已知模式匹配（Regex）
  | 'ood'; // Layer4: OOD 语义离群检测

/** SSE 事件类型（Rust 侧匹配的分支） */
export type ScannedEventType =
  | 'tool_result' // 工具执行结果
  | 'content_block_delta' // 内容块增量
  | 'text_delta'; // 文本增量（仅 >50 字符时扫描）

// ═══════════════════════════════════════════════════════════════════════════════
// 信号数据
// ═══════════════════════════════════════════════════════════════════════════════

/** Layer 1 模式匹配详情 */
export interface PatternMatchDetail {
  layer: 'pattern';
  /** 匹配到的正则模式名称 */
  pattern_name: string;
}

/** Layer 4 OOD 检测详情 */
export interface OodMatchDetail {
  layer: 'ood';
  /** 异常分数 (0.0–1.0, 阈值 > ANOMALY_THRESHOLD) */
  anomaly_score: number;
}

/** 单次扫描信号 */
export interface SseScanSignal {
  /** 信号唯一标识（去重用） */
  signal_id: string;
  /** 检测时间戳 (ns) */
  timestamp_ns: number;
  /** 触发扫描的 SSE 事件类型 */
  event_type: ScannedEventType;
  /** 关联的工具名称 */
  tool_name: string;
  /** 使用的检测层 */
  detection_layer: DetectionLayer;
  /** 检测方法描述 */
  detection_method: string;
  /** 置信度 (0.0–1.0) */
  confidence: number;
  /** 检测理由（人类可读） */
  reason: string;
  /** 原始文本片段（截断至 200 字符） */
  snippet_preview: string;
  /** 检测层详细信息 */
  detail: PatternMatchDetail | OodMatchDetail;
}

// ═══════════════════════════════════════════════════════════════════════════════
// 统计聚合
// ═══════════════════════════════════════════════════════════════════════════════

/** 扫描统计快照 */
export interface SseScannerStats {
  /** 累计扫描事件数 */
  events_scanned: number;
  /** 累计产生信号数 */
  signals_produced: number;
  /** 信号产出率 (signals / events_scanned) */
  signal_rate: number;
  /** 按检测层分布 */
  by_layer: { pattern: number; ood: number };
  /** 按事件类型分布 */
  by_event_type: Record<ScannedEventType, number>;
  /** 信号数最多的 Top-N 工具 */
  top_tools: Array<{ tool_name: string; signal_count: number }>;
  /** 最近一次信号的时间戳 (ns) */
  last_signal_ns: number | null;
}

// ═══════════════════════════════════════════════════════════════════════════════
// 仪表板状态
// ═══════════════════════════════════════════════════════════════════════════════

export type ScannerViewMode = 'overview' | 'signals' | 'tools';

export interface SseContentScannerDashboardState {
  /** 近期信号列表（最近 N 条） */
  recent_signals: SseScanSignal[];
  /** 聚合统计 */
  stats: SseScannerStats;
  /** 当前视图模式 */
  view_mode: ScannerViewMode;
  /** 选中的信号 ID */
  selected_signal_id: string | null;
  /** 检测层筛选 */
  layer_filter: DetectionLayer | null;
  /** 事件类型筛选 */
  event_type_filter: ScannedEventType | null;
  /** 工具名筛选 */
  tool_filter: string | null;
  /** 最低置信度筛选 (0.0–1.0) */
  min_confidence_filter: number;
  /** 上次更新时间 (epoch ms) */
  last_update_ms: number;
}

// ═══════════════════════════════════════════════════════════════════════════════
// 组件 Props
// ═══════════════════════════════════════════════════════════════════════════════

export interface SseContentScannerDashboardProps {
  state: SseContentScannerDashboardState;
  /** 切换视图模式 */
  onViewModeChange: (mode: ScannerViewMode) => void;
  /** 选中/取消信号 */
  onSignalSelect: (signal: SseScanSignal | null) => void;
  /** 检测层筛选 */
  onLayerFilterChange: (layer: DetectionLayer | null) => void;
  /** 事件类型筛选 */
  onEventTypeFilterChange: (type: ScannedEventType | null) => void;
  /** 工具名筛选 */
  onToolFilterChange: (tool: string | null) => void;
  /** 置信度阈值调整 */
  onMinConfidenceChange: (confidence: number) => void;
  /** 跳转到时间线定位原始事件 */
  onNavigateToTimeline: (timestamp_ns: number) => void;
  /** 跳转到工具执行仪表板 */
  onNavigateToToolExecution: (tool_name: string) => void;
  compact?: boolean;
}

/** 信号详情面板 Props */
export interface SseSignalDetailPanelProps {
  signal: SseScanSignal;
  onClose: () => void;
  onNavigateToTimeline: (timestamp_ns: number) => void;
  onNavigateToToolExecution: (tool_name: string) => void;
}

/** 工具-信号关联行 Props */
export interface ToolSignalRowProps {
  tool_name: string;
  signal_count: number;
  pattern_count: number;
  ood_count: number;
  avg_confidence: number;
  latest_signal_ns: number;
  isSelected: boolean;
  onSelect: () => void;
}

// ═══════════════════════════════════════════════════════════════════════════════
// 工具函数
// ═══════════════════════════════════════════════════════════════════════════════

/** 检测层人类可读标签 */
export const DETECTION_LAYER_LABEL: Record<DetectionLayer, string> = {
  pattern: '模式匹配',
  ood: '离群检测',
};

/** 检测层颜色 */
export const DETECTION_LAYER_COLOR: Record<DetectionLayer, string> = {
  pattern: 'text-indigo-600 bg-indigo-50 border-indigo-200',
  ood: 'text-amber-600 bg-amber-50 border-amber-200',
};

/** 事件类型人类可读标签 */
export const EVENT_TYPE_LABEL: Record<ScannedEventType, string> = {
  tool_result: '工具结果',
  content_block_delta: '内容块',
  text_delta: '文本增量',
};

/** 信号置信度转严重度等级 */
export function signalConfidenceToSeverity(confidence: number): AnomalySeverity {
  if (confidence >= 0.9) return 'critical';
  if (confidence >= 0.7) return 'warning';
  if (confidence >= 0.5) return 'warning';
  return 'info';
}

/** 信号置信度对应颜色 */
export function signalConfidenceColor(confidence: number): string {
  if (confidence >= 0.9) return 'text-red-600';
  if (confidence >= 0.7) return 'text-orange-500';
  if (confidence >= 0.5) return 'text-amber-500';
  return 'text-slate-400';
}

/** 信号置信度对应背景色 */
export function signalConfidenceBg(confidence: number): string {
  if (confidence >= 0.9) return 'bg-red-50 border-red-200';
  if (confidence >= 0.7) return 'bg-orange-50 border-orange-200';
  if (confidence >= 0.5) return 'bg-amber-50 border-amber-200';
  return 'bg-slate-50 border-slate-200';
}

/** 创建空的仪表板状态 */
export function createDefaultScannerState(): SseContentScannerDashboardState {
  return {
    recent_signals: [],
    stats: {
      events_scanned: 0,
      signals_produced: 0,
      signal_rate: 0,
      by_layer: { pattern: 0, ood: 0 },
      by_event_type: { tool_result: 0, content_block_delta: 0, text_delta: 0 },
      top_tools: [],
      last_signal_ns: null,
    },
    view_mode: 'overview',
    selected_signal_id: null,
    layer_filter: null,
    event_type_filter: null,
    tool_filter: null,
    min_confidence_filter: 0,
    last_update_ms: Date.now(),
  };
}
