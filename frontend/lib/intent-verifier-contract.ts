// AgentIntentVerifier Dashboard — 可视化契约
// 对应 collector/src/framework/analyzers/agent_intent_verifier_design.md 的输出事件格式
//
// 两种输出事件 source:
//   "intent_mismatch"           — 检测到意图-行为不一致
//   "intent_verifier_summary"   — drain 阶段的聚合统计摘要
//
// 与其他仪表板联动的 key:
//   agent_pid   — 关联到 ProcessTree / AgentLoopDashboard / ToolExecutionDashboard
//   tool_name   — 关联到 ToolExecutionDashboard
//   file_path   — 关联到 FileAccessAudit 面板

import type { AnomalySeverity } from './data-contract';

// ═══════════════════════════════════════════════════════════════════════════
// 不匹配类型（与 Rust MismatchType 对齐）
// ═══════════════════════════════════════════════════════════════════════════

export type MismatchType =
  | 'ToolMismatch' // 工具名不匹配：声称调用 A 工具，行为匹配到 B 工具
  | 'FilePathEscalation' // 文件路径越权：声称读 A，实际访问敏感路径 B
  | 'NetworkDeviation' // 网络目标偏离：声称访问域名 A，实际连接到 B
  | 'CommandDeviation' // 命令注入/偏离：声称执行命令 A，实际包含额外参数
  | 'UndeclaredBehavior' // 未声明的额外行为：无对应工具调用的系统操作
  | 'DeclaredButNotExecuted'; // 声明但未执行：声称要做但未观测到对应行为

// ═══════════════════════════════════════════════════════════════════════════
// 不匹配严重度（与 Rust MismatchSeverity 对齐）
// ═══════════════════════════════════════════════════════════════════════════

export type MismatchSeverity =
  | 'Info' // 信息级别：轻微偏差，可能是 API 格式差异
  | 'Warning' // 警告级别：中度偏差，需要关注
  | 'Critical'; // 严重：明显行为偏离，可能是攻击迹象

// ═══════════════════════════════════════════════════════════════════════════
// 意图目标类型（与 Rust IntentTarget 对齐）
// ═══════════════════════════════════════════════════════════════════════════

export type FileOperation = 'Read' | 'Write' | 'ReadWrite' | 'Delete';

export interface FilePathTarget {
  type: 'FilePath';
  path: string;
  operation: FileOperation;
}

export interface NetworkTargetTarget {
  type: 'NetworkTarget';
  host: string;
  port?: number;
  scheme: string;
}

export interface CommandTarget {
  type: 'Command';
  binary: string;
  args_prefix: string[];
}

export type IntentTarget = FilePathTarget | NetworkTargetTarget | CommandTarget;

// ═══════════════════════════════════════════════════════════════════════════
// 声明与观测载荷
// ═══════════════════════════════════════════════════════════════════════════

/** Agent 声明的意图内容 */
export interface DeclaredIntentPayload {
  /** 工具名称 */
  tool_name: string;
  /** 提取的操作目标 */
  target: IntentTarget;
  /** 可选：原始工具参数 */
  raw_args?: Record<string, unknown>;
}

/** eBPF 实际观测的行为内容 */
export interface ObservedBehaviorPayload {
  /** 事件来源 (e.g., "file_access", "process", "ssl", "http_parser") */
  source: string;
  /** 观测路径（文件访问时） */
  path?: string;
  /** 操作类型 */
  operation?: string;
  /** 事件类型 */
  event_type?: string;
  /** 主机名（网络连接时） */
  host?: string;
  /** 端口（网络连接时） */
  port?: number;
  /** 命令行（进程 exec 时） */
  command?: string;
  /** 二进制名称 */
  binary?: string;
  /** 完整参数字符串 */
  full_command?: string;
  /** 额外上下文 */
  extra?: Record<string, unknown>;
}

// ═══════════════════════════════════════════════════════════════════════════
// 核心数据结构
// ═══════════════════════════════════════════════════════════════════════════

/** intent_mismatch 事件（source: "intent_mismatch"） */
export interface IntentMismatchEvent {
  /** 关联的意图 ID: "{request_id}:{tool_call_index}" */
  intent_id: string;
  /** 不匹配类型 */
  mismatch_type: MismatchType;
  /** 严重级别 */
  severity: MismatchSeverity;
  /** Agent 声明的内容 */
  declared: DeclaredIntentPayload;
  /** eBPF 实际观测的内容 */
  observed: ObservedBehaviorPayload;
  /** 人类可读的不匹配描述 */
  description: string;
  /** 涉事 Agent PID */
  agent_pid: number;
  /** 检测时间 (epoch ns) */
  detected_at_ns: number;
  /** 匹配到的敏感路径模式（FilePathEscalation 时） */
  sensitive_path_match?: string;
}

/** intent_verifier_summary 事件（source: "intent_verifier_summary"，drain 时输出） */
export interface IntentVerifierSummaryEvent {
  /** 总意图声明数 */
  total_intents_declared: number;
  /** 匹配成功数 */
  matched: number;
  /** 不匹配数 */
  mismatched: number;
  /** 过期未解决数 */
  expired_unresolved: number;
  /** 按不匹配类型分布 */
  mismatches_by_type: Record<MismatchType, number>;
  /** 按严重度分布 */
  mismatches_by_severity: Record<MismatchSeverity, number>;
  /** 按 Agent PID 的明细统计 */
  per_agent: Record<number, PerAgentMismatchStats>;
  /** 摘要输出时间 (epoch ns) */
  generated_at_ns?: number;
}

/** 单个 Agent 的不匹配统计 */
export interface PerAgentMismatchStats {
  /** 该 Agent 的总意图数 */
  total_intents: number;
  /** 不匹配数 */
  mismatches: number;
  /** 未声明行为数 */
  undeclared_behaviors: number;
}

// ═══════════════════════════════════════════════════════════════════════════
// 合并视图模型（合并 mismatch 事件 + summary 统计的 UI 视图模型）
// ═══════════════════════════════════════════════════════════════════════════

export interface MismatchSummary {
  /** 不匹配唯一标识（复用 intent_id） */
  intent_id: string;
  /** 不匹配类型 */
  mismatch_type: MismatchType;
  /** 严重级别 */
  severity: MismatchSeverity;
  /** 声明的工具名 */
  tool_name: string;
  /** Agent PID */
  agent_pid: number;
  /** 描述 */
  description: string;
  /** 声明内容 */
  declared: DeclaredIntentPayload;
  /** 观测内容 */
  observed: ObservedBehaviorPayload;
  /** 检测时间 (epoch ns) */
  detected_at_ns: number;
  /** 敏感路径匹配 */
  sensitive_path_match?: string;
  /** 是否已确认（用户操作） */
  acknowledged?: boolean;
  /** 确认时间 (epoch ms) */
  acknowledged_at_ms?: number;
}

// ═══════════════════════════════════════════════════════════════════════════
// SSE 流集成 — 扩展 stream_type
// ═══════════════════════════════════════════════════════════════════════════

export type IntentVerifierStreamType = 'intent_mismatch' | 'intent_verifier_summary';

// ═══════════════════════════════════════════════════════════════════════════
// 仪表板 UI 状态
// ═══════════════════════════════════════════════════════════════════════════

/** 仪表板视图模式 */
export type IntentVerifierDashboardViewMode = 'overview' | 'timeline' | 'agents';

/** 仪表板全局状态 */
export interface IntentVerifierDashboardState {
  /** 当前不匹配列表（普通 + Critical 排前面） */
  mismatches: MismatchSummary[];
  /** 最新统计摘要 */
  latest_summary: IntentVerifierSummaryEvent | null;
  /** 统计摘要历史 */
  summary_history: IntentVerifierSummaryEvent[];
  /** 当前视图模式 */
  view_mode: IntentVerifierDashboardViewMode;
  /** 选中的不匹配 ID（联动 DetailPanel） */
  selected_mismatch_id: string | null;
  /** 按不匹配类型过滤 */
  type_filter: MismatchType | null;
  /** 按严重度过滤 */
  severity_filter: MismatchSeverity | null;
  /** 按工具名过滤 */
  tool_filter: string | null;
  /** 按 Agent PID 过滤 */
  pid_filter: number | null;
  /** 仅显示未确认 */
  show_unacknowledged_only: boolean;
  /** 最后更新时间 (epoch ms) */
  last_update_ms: number;
}

// ═══════════════════════════════════════════════════════════════════════════
// 组件 Props
// ═══════════════════════════════════════════════════════════════════════════

export interface IntentVerifierDashboardProps {
  /** 仪表板状态 */
  state: IntentVerifierDashboardState;
  /** 切换视图模式 */
  onViewModeChange: (mode: IntentVerifierDashboardViewMode) => void;
  /** 选中不匹配 */
  onMismatchSelect: (mismatch: MismatchSummary | null) => void;
  /** 切换类型过滤 */
  onTypeFilterChange: (type: MismatchType | null) => void;
  /** 切换严重度过滤 */
  onSeverityFilterChange: (severity: MismatchSeverity | null) => void;
  /** 切换工具名过滤 */
  onToolFilterChange: (tool_name: string | null) => void;
  /** 切换 Agent PID 过滤 */
  onPidFilterChange: (pid: number | null) => void;
  /** 切换仅未确认模式 */
  onShowUnacknowledgedOnlyChange: (unacknowledged_only: boolean) => void;
  /** 确认不匹配（用户标记已阅） */
  onAcknowledgeMismatch?: (intent_id: string) => void;
  /** 是否紧凑模式 */
  compact?: boolean;
}

export interface MismatchCardProps {
  mismatch: MismatchSummary;
  selected?: boolean;
  onSelect: () => void;
  onAcknowledge?: () => void;
}

export interface MismatchDetailPanelProps {
  mismatch: MismatchSummary;
  /** 点击工具名跳转到 ToolExecutionDashboard */
  onToolNameClick?: (tool_name: string) => void;
  /** 点击 Agent PID 跳转到 Agent 视图 */
  onAgentPidClick?: (pid: number) => void;
  /** 确认 */
  onAcknowledge?: () => void;
}

export interface MismatchSeverityBadgeProps {
  severity: MismatchSeverity;
  showLabel?: boolean;
}

export interface MismatchTypeIconProps {
  mismatch_type: MismatchType;
  size?: 'sm' | 'md' | 'lg';
}

export interface AgentMismatchChartProps {
  /** agent_pid → 不匹配数 */
  frequency: Record<number, number>;
  selected_pid: number | null;
  onPidSelect: (pid: number | null) => void;
}

// ═══════════════════════════════════════════════════════════════════════════
// 工具函数
// ═══════════════════════════════════════════════════════════════════════════

/** 不匹配类型 → 人类可读标签 */
export const MISMATCH_TYPE_LABEL: Record<MismatchType, string> = {
  ToolMismatch: '工具不匹配',
  FilePathEscalation: '文件越权',
  NetworkDeviation: '网络偏离',
  CommandDeviation: '命令偏离',
  UndeclaredBehavior: '未声明行为',
  DeclaredButNotExecuted: '声明未执行',
};

/** 不匹配类型 → 图标（emoji） */
export const MISMATCH_TYPE_ICON: Record<MismatchType, string> = {
  ToolMismatch: '🔧',
  FilePathEscalation: '📁',
  NetworkDeviation: '🌐',
  CommandDeviation: '⚡',
  UndeclaredBehavior: '👻',
  DeclaredButNotExecuted: '💤',
};

/** 严重度 → 人类可读标签 */
export const MISMATCH_SEVERITY_LABEL: Record<MismatchSeverity, string> = {
  Info: '信息',
  Warning: '警告',
  Critical: '严重',
};

/** 文件操作 → 人类可读标签 */
export const FILE_OPERATION_LABEL: Record<FileOperation, string> = {
  Read: '读取',
  Write: '写入',
  ReadWrite: '读写',
  Delete: '删除',
};

/** 不匹配类型 → 简要描述 */
export function describeMismatchType(type: MismatchType): string {
  switch (type) {
    case 'ToolMismatch':
      return 'Agent 声称使用某工具，行为特征匹配到另一工具';
    case 'FilePathEscalation':
      return 'Agent 声称操作某文件，实际访问了不同的敏感路径';
    case 'NetworkDeviation':
      return 'Agent 声称访问某域名，实际连接到不同目标';
    case 'CommandDeviation':
      return 'Agent 声称执行某命令，实际执行的命令包含额外参数';
    case 'UndeclaredBehavior':
      return 'Agent 执行了未通过任何工具调用声明的系统操作';
    case 'DeclaredButNotExecuted':
      return 'Agent 声称要执行某操作，但在有效窗口内未观测到';
  }
}

/** 不匹配严重度 → AnomalySeverity 映射（用于着色系统） */
export function mismatchSeverityToAnomalySeverity(severity: MismatchSeverity): AnomalySeverity {
  switch (severity) {
    case 'Info':
      return 'info';
    case 'Warning':
      return 'warning';
    case 'Critical':
      return 'critical';
  }
}

/** 从 IntentMismatchEvent 创建 MismatchSummary */
export function createMismatchSummaryFromEvent(event: IntentMismatchEvent): MismatchSummary {
  return {
    intent_id: event.intent_id,
    mismatch_type: event.mismatch_type,
    severity: event.severity,
    tool_name: event.declared.tool_name,
    agent_pid: event.agent_pid,
    description: event.description,
    declared: event.declared,
    observed: event.observed,
    detected_at_ns: event.detected_at_ns,
    sensitive_path_match: event.sensitive_path_match,
    acknowledged: false,
  };
}

/** 按不匹配类型分组统计 */
export function countByMismatchType(mismatches: MismatchSummary[]): Record<MismatchType, number> {
  const counts: Record<MismatchType, number> = {
    ToolMismatch: 0,
    FilePathEscalation: 0,
    NetworkDeviation: 0,
    CommandDeviation: 0,
    UndeclaredBehavior: 0,
    DeclaredButNotExecuted: 0,
  };
  for (const m of mismatches) {
    counts[m.mismatch_type]++;
  }
  return counts;
}

/** 按严重度分组统计 */
export function countByMismatchSeverity(
  mismatches: MismatchSummary[],
): Record<MismatchSeverity, number> {
  const counts: Record<MismatchSeverity, number> = { Info: 0, Warning: 0, Critical: 0 };
  for (const m of mismatches) {
    counts[m.severity]++;
  }
  return counts;
}

/** 按 Agent PID 聚合不匹配频率 */
export function aggregateMismatchByPid(mismatches: MismatchSummary[]): Record<number, number> {
  const freq: Record<number, number> = {};
  for (const m of mismatches) {
    freq[m.agent_pid] = (freq[m.agent_pid] || 0) + 1;
  }
  return freq;
}

/** 按工具名聚合不匹配频率 */
export function aggregateMismatchByTool(mismatches: MismatchSummary[]): Record<string, number> {
  const freq: Record<string, number> = {};
  for (const m of mismatches) {
    freq[m.tool_name] = (freq[m.tool_name] || 0) + 1;
  }
  return freq;
}

/** 获取最高的严重度级别 */
export function maxMismatchSeverity(mismatches: MismatchSummary[]): MismatchSeverity | null {
  if (mismatches.some((m) => m.severity === 'Critical')) return 'Critical';
  if (mismatches.some((m) => m.severity === 'Warning')) return 'Warning';
  if (mismatches.some((m) => m.severity === 'Info')) return 'Info';
  return null;
}

/** 获取 Critical 级别的不匹配数 */
export function countCritical(mismatches: MismatchSummary[]): number {
  return mismatches.filter((m) => m.severity === 'Critical').length;
}

/** 格式化纳秒为相对时间 */
export function formatTimestampNs(ns: number): string {
  const ms = ns / 1_000_000;
  const now = Date.now();
  const diff = now - ms;
  if (diff < 1000) return '刚刚';
  if (diff < 60_000) return `${Math.round(diff / 1000)}秒前`;
  if (diff < 3600_000) return `${Math.round(diff / 60_000)}分钟前`;
  return new Date(ms).toLocaleTimeString();
}

/** 获取声明目标的简短摘要文本 */
export function describeDeclaredTarget(declared: DeclaredIntentPayload): string {
  switch (declared.target.type) {
    case 'FilePath':
      return `${FILE_OPERATION_LABEL[declared.target.operation]} ${declared.target.path}`;
    case 'NetworkTarget':
      return `访问 ${declared.target.host}${declared.target.port ? `:${declared.target.port}` : ''}`;
    case 'Command':
      return `执行 ${[declared.target.binary, ...declared.target.args_prefix].join(' ')}`;
  }
}

/** 获取观测行为的简短摘要文本 */
export function describeObservedBehavior(observed: ObservedBehaviorPayload): string {
  if (observed.path) {
    return `${observed.operation ?? '访问'}: ${observed.path}`;
  }
  if (observed.host) {
    return `连接: ${observed.host}${observed.port ? `:${observed.port}` : ''}`;
  }
  if (observed.command || observed.binary) {
    return `执行: ${observed.command ?? observed.binary}`;
  }
  return `${observed.source}: ${observed.event_type ?? '未知操作'}`;
}

/** 创建空的仪表板状态 */
export function createDefaultIntentVerifierState(): IntentVerifierDashboardState {
  return {
    mismatches: [],
    latest_summary: null,
    summary_history: [],
    view_mode: 'overview',
    selected_mismatch_id: null,
    type_filter: null,
    severity_filter: null,
    tool_filter: null,
    pid_filter: null,
    show_unacknowledged_only: false,
    last_update_ms: Date.now(),
  };
}
