// 运行监控聚合——阶段一 6 个 db 派生视角的类型契约。
// 铁律：任何 KPI/状态/分布由纯函数从真实数据派生；算不出的诚实留空（null）。
// 状态词表统一 critical|warning|normal，UI 层经 StatusBadge tone 映射。

export type MonitoringStatus = 'critical' | 'warning' | 'normal';

export type PerspectiveKey =
  | 'topologyDrift'
  | 'topologyHealth'
  | 'intentMismatch'
  | 'policyCompliance'
  | 'toolFootprint'
  | 'networkSessions';

/** 每视角健康信号：available 独立于计数——缺表=false，空表=true+normal */
export interface PerspectiveHealth {
  available: boolean;
  status: MonitoringStatus;
  /** 0-100 比率派生分；无自然比率（存在性视角）或无法计算 = null */
  score: number | null;
  /** 贡献计数摘要，UI 与状态并排展示（"失配 3 · 密度 1.0"） */
  signals: string[];
}

export interface SnapshotMetricFrame {
  snapshot_id: string;
  timestamp_ns: number;
  /** nodeCount = Σ(node_counts map 值)，edgeCount = Σ(edge_counts map 值) */
  nodeCount: number;
  edgeCount: number;
  agentCount: number;
  crossAgentEdges: number;
  delegationDepth: number;
  taintChains: number;
  avgConfidence: number;
}

export interface TopologyDriftState {
  frames: SnapshotMetricFrame[];
  latest: SnapshotMetricFrame | null;
  /** 最新帧 - 首个拓扑非零帧 的拓扑差分（诚实演变量） */
  delta: { nodes: number; edges: number; agents: number } | null;
}

export interface TopologyHealthState {
  latest: SnapshotMetricFrame | null;
  /** 大写枚举 Critical/Warning/Info → 小写词表 */
  alertSeverity: { critical: number; warning: number; info: number };
  pollutionCount: number;
  pollutionBySource: Record<string, number>;
}

export interface IntentMismatchState {
  diagnosticsCount: number;
  byReason: Record<string, number>;
  intentsCount: number;
  /** 失配密度 = diagnostics / max(1, intents) */
  mismatchDensity: number | null;
  evidenceMatch: { weak: number; strong: number; total: number }; // L5Substring 弱 / 其余强
}

export interface PolicyComplianceState {
  evaluationsTotal: number;
  violations: number;
  violationRatio: number | null;
  byBoundary: Record<string, number>;
}

export interface ToolFootprintState {
  intentsCount: number;
  actionsCount: number;
  byTool: Record<string, number>;
  byActionType: Record<string, number>;
  bySignalClass: Record<string, number>;
  topFiles: Array<{ path: string; count: number }>;
}

export interface NetworkSessionsState {
  total: number;
  byPid: Record<string, number>;
  trend: Array<{ t: number; count: number }>;
}

export interface MonitoringState {
  derivedAtMs: number;
  sourceLabel: string;
  health: Record<PerspectiveKey, PerspectiveHealth>;
  topologyDrift: TopologyDriftState | null;
  topologyHealth: TopologyHealthState | null;
  intentMismatch: IntentMismatchState | null;
  policyCompliance: PolicyComplianceState | null;
  toolFootprint: ToolFootprintState | null;
  networkSessions: NetworkSessionsState | null;
}

// ── 提取器返回契约（Task 2 产出，Task 3 消费）──
export interface SnapshotMetricsExtract {
  available: boolean;
  frames: SnapshotMetricFrame[];
}
export interface MismatchExtract {
  available: boolean;
  count: number;
  byReason: Record<string, number>;
  weak: number;
  strong: number;
}
export interface PollutionExtract {
  available: boolean;
  count: number;
  bySource: Record<string, number>;
}
export interface PolicyExtract {
  available: boolean;
  total: number;
  violations: number;
  byBoundary: Record<string, number>;
}
export interface TlsExtract {
  available: boolean;
  total: number;
  byPid: Record<string, number>;
  trend: Array<{ t: number; count: number }>;
}
