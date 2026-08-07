// SessionBoundaryAnalyzer Dashboard — 可视化契约
// 对应 collector/src/framework/analyzers/session_boundary_analyzer 的输出事件格式
//
// 输出事件 source tag: "session_boundary"
// 三种异常类型: CrossUser | ContextMutation | UnauthorizedHandoff
// 三级严重度:   Info → Warning → Critical

import type { AnomalySeverity } from './data-contract';

// ═══════════════════════════════════════════════════════════════════════════
// 异常类型（与 Rust anomaly_type 对齐）
// ═══════════════════════════════════════════════════════════════════════════

/** 会话边界异常类型 */
export type SessionBoundaryAnomalyType =
  | 'CrossUser' // 同一 TLS session 中出现多个不同 user_id
  | 'ContextMutation' // SSE 流中 conversation_id 发生突变
  | 'UnauthorizedHandoff'; // Agent 接受了不是发给它的 handoff 消息

export const SESSION_ANOMALY_TYPE_LABEL: Record<SessionBoundaryAnomalyType, string> = {
  CrossUser: '跨用户混入',
  ContextMutation: '上下文突变',
  UnauthorizedHandoff: '未授权 handoff',
};

export const SESSION_ANOMALY_TYPE_DESC: Record<SessionBoundaryAnomalyType, string> = {
  CrossUser: '同一 TLS 会话中出现了多个不同 user_id 的请求，可能存在攻击者走私恶意请求进入合法会话',
  ContextMutation: 'SSE 流中 conversation_id 在单个连接内发生突变，会话上下文可能被劫持',
  UnauthorizedHandoff: 'Agent 接收了不是发给它的 handoff 消息，子 Agent 信任链可能被污染',
};

export const SESSION_ANOMALY_TYPE_ICON: Record<SessionBoundaryAnomalyType, string> = {
  CrossUser: '👥',
  ContextMutation: '🦠',
  UnauthorizedHandoff: '🔓',
};

// ═══════════════════════════════════════════════════════════════════════════
// 严重程度
// ═══════════════════════════════════════════════════════════════════════════

/** 会话边界告警严重程度 */
export type SessionBoundarySeverity = 'info' | 'warning' | 'high';

export const SESSION_SEVERITY_LABEL: Record<SessionBoundarySeverity, string> = {
  info: '信息',
  warning: '警告',
  high: '高危',
};

export function sessionSeverityToAnomalySeverity(s: SessionBoundarySeverity): AnomalySeverity {
  switch (s) {
    case 'info':
      return 'info';
    case 'warning':
      return 'warning';
    case 'high':
      return 'critical';
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// 核心数据结构
// ═══════════════════════════════════════════════════════════════════════════

/**
 * 单次会话边界告警（source: "session_boundary"）
 * 与 Rust SessionBoundaryAnalyzer 产出的 Event.data 结构对齐
 */
export interface SessionBoundaryAlert {
  /** 告警唯一 ID */
  alert_id: string;
  /** 异常类型 */
  anomaly_type: SessionBoundaryAnomalyType;
  /** 严重程度 */
  severity: SessionBoundarySeverity;
  /** 触发告警的 Agent root PID */
  agent_root_pid: number;

  // ── CrossUser 特有字段 ──
  /** TLS session 标识符 */
  tls_session_id: string | null;
  /** 触发告警的 user_id */
  offending_user_id: string | null;
  /** 之前已出现的 user_id 列表 */
  previously_seen_users: string[];

  // ── ContextMutation 特有字段 ──
  /** 突变前的 conversation_id */
  previous_conversation_id: string | null;
  /** 突变后的 conversation_id */
  new_conversation_id: string | null;
  /** 当前会话累计突变次数 */
  mutation_count: number;

  // ── UnauthorizedHandoff 特有字段 ──
  /** handoff 目标 agent root_pid */
  target_agent_root_pid: number | null;
  /** handoff 来源 agent root_pid */
  source_agent_root_pid: number | null;

  /** 人类可读描述 */
  detail: string;
  /** 进程名 */
  comm: string;
  /** 时间戳 (epoch ns) */
  timestamp_ns: number;
}

// ═══════════════════════════════════════════════════════════════════════════
// 聚合统计
// ═══════════════════════════════════════════════════════════════════════════

/** 单会话的边界状态摘要 */
export interface SessionBoundarySummary {
  /** session key: "{pid}:{tls_session_id}" */
  session_key: string;
  /** TLS session ID */
  tls_session_id: string;
  /** 关联的 agent root_pid */
  agent_root_pid: number;
  /** 会话中已出现的 user_id 集合 */
  seen_user_ids: string[];
  /** 当前 conversation_id */
  current_conversation_id: string | null;
  /** conversation_id 突变次数 */
  conversation_mutations: number;
  /** 此会话关联的 Agent root_pid 集合 */
  agent_roots: number[];
  /** 该会话产生的告警数 */
  alert_count: number;
  /** 首次观测时间 (epoch ns) */
  first_seen_ns: number;
  /** 最后活跃时间 (epoch ns) */
  last_active_ns: number;
  /** 是否活跃 */
  is_active: boolean;
}

/** 全局会话边界快照 */
export interface SessionBoundarySnapshot {
  /** 活跃会话数 */
  active_session_count: number;
  /** 总告警数 */
  total_alerts: number;
  /** 按异常类型分布 */
  alerts_by_type: Record<SessionBoundaryAnomalyType, number>;
  /** 所有活跃会话摘要 */
  active_sessions: SessionBoundarySummary[];
  /** 最近告警列表 */
  recent_alerts: SessionBoundaryAlert[];
}

// ═══════════════════════════════════════════════════════════════════════════
// 仪表板状态
// ═══════════════════════════════════════════════════════════════════════════

export type SessionBoundaryViewMode = 'overview' | 'alerts' | 'sessions';

export interface SessionBoundaryDashboardState {
  /** 告警列表 */
  alerts: SessionBoundaryAlert[];
  /** 全局快照 */
  snapshot: SessionBoundarySnapshot | null;
  /** 当前视图模式 */
  view_mode: SessionBoundaryViewMode;
  /** 选中的告警 ID */
  selected_alert_id: string | null;
  /** 选中的会话 key */
  selected_session_key: string | null;
  /** 异常类型过滤器 */
  type_filter: SessionBoundaryAnomalyType | null;
  /** 严重程度过滤器 */
  severity_filter: SessionBoundarySeverity | null;
  /** agent 过滤器 */
  agent_filter: number | null;
  /** session 过滤器 */
  session_filter: string | null;
  /** 最后更新时间 (epoch ms) */
  last_update_ms: number;
}

/** 创建默认状态 */
export function createDefaultSessionBoundaryState(): SessionBoundaryDashboardState {
  return {
    alerts: [],
    snapshot: null,
    view_mode: 'overview',
    selected_alert_id: null,
    selected_session_key: null,
    type_filter: null,
    severity_filter: null,
    agent_filter: null,
    session_filter: null,
    last_update_ms: Date.now(),
  };
}
