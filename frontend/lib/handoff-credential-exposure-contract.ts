// ═══════════════════════════════════════════════════════════════
// Subagent 3 — Handoff Credential Exposure Frontend Data Contract
// 输入: shared_handoff.md (AgentSight 可观测性 + 补充需求 A/B/C)
//       + Subagent 2 安全审查输出 (agent_B.md): 凭据泄漏发现
//
// 场景: API_KEY / DEPLOY_CMD 等敏感凭据通过子代理 handoff 文档
//       在 Agent A → B → C 之间传播。前端需可视化:
//       1. 凭据首次出现位置 (source agent + file path)
//       2. 跨代理传播链 (A→B→C 的读取/复制路径)
//       3. 补充需求要求的 workspace_boundary 标记 + cross_boundary_read 告警
//       4. 过滤层完整性: allow_filter=false 约束是否被遵守
//
// 对齐: lib/types.ts · lib/data-contract.ts · lib/handoff-boundary-contract.ts
// ═══════════════════════════════════════════════════════════════

import type { AnomalySeverity } from "./data-contract";
import type { PhaseIndicator, NormalizedResource } from "./types";

// ═══════════════════════════════════════════════════════════════
// 1. 凭据模式匹配 — 对应 Subagent 2 发现
//    Agent A 输出中检测到的敏感凭据
// ═══════════════════════════════════════════════════════════════

/** 凭据类型 — 从 handoff 内容中模式匹配检测 */
export type CredentialKind =
  | "api_key"           // 匹配: /sk-[a-z]+-[A-Za-z0-9+/=]{32,}/
  | "bearer_token"      // 匹配: Authorization: Bearer ...
  | "deploy_command"    // 匹配: curl ... Authorization ... --data-binary
  | "private_key"       // 匹配: -----BEGIN ... PRIVATE KEY-----
  | "connection_string"  // 匹配: mongodb://user:pass@ / postgresql://
  | "internal_endpoint"; // 匹配: *.prod.internal 等内网地址

/** 单个凭据发现 — Subagent 2 安全审查的输出单元 */
export interface CredentialFinding {
  finding_id: string;
  timestamp_ns: number;

  /** 凭据类型 */
  kind: CredentialKind;
  /** 匹配到的模式正则摘要 (不暴露实际密钥) */
  pattern_summary: string;
  /** 凭据内容的 SHA-256 (用于跨代理碰撞检测，不暴露原文) */
  content_hash: string;
  /** 脱敏后的凭据预览 (前 6 + 后 4 字符) */
  redacted_preview: string;

  /** 首次发现位置 */
  source_agent_id: string;
  source_file_path: string;      // 如 shared_handoff.md
  source_line: number | null;

  /** 严重程度 */
  severity: "critical" | "warning";
}

/** 凭据传播节点 — 每个读取/复制该凭据的代理 */
export interface CredentialPropagationHop {
  hop_order: number;             // 0 = source, 1 = 第一个读取者...
  agent_id: string;
  chain_depth: number;
  access_timestamp_ns: number;
  file_path: string;              // 代理读取的包含凭据的文件
  access_type: "read" | "copy" | "execute";

  /** 补充需求 §A: 工作空间边界标记 */
  workspace_boundary: "inside" | "outside";

  /** 补充需求 §B: 是否触发跨边界读取告警 */
  triggered_cross_boundary_alert: boolean;
}

/** 完整凭据传播链 — 从首次泄漏到最后一次访问 */
export interface CredentialPropagationChain {
  chain_id: string;
  finding: CredentialFinding;
  hops: CredentialPropagationHop[];

  /** 传播深度 */
  propagation_depth: number;
  /** 跨越工作空间边界的跳数 */
  cross_boundary_hop_count: number;
  /** 总暴露时长 (ns) */
  total_exposure_duration_ns: number;

  /** 补充需求 §C: 此链中是否有事件被过滤层违规丢弃 */
  filter_integrity_violated: boolean;
  dropped_event_ids: string[];
}

// ═══════════════════════════════════════════════════════════════
// 2. 补充需求 A/B/C 的事件类型 (精简版，引用 handoff-boundary-contract.ts)
// ═══════════════════════════════════════════════════════════════

import type {
  WorkspaceBoundaryTag,
  CrossBoundaryReadEvent,
  FilterIntegritySnapshot,
} from "./handoff-boundary-contract";

export type {
  WorkspaceBoundaryTag,
  CrossBoundaryReadEvent,
  FilterIntegritySnapshot,
};

// ═══════════════════════════════════════════════════════════════
// 3. 仪表盘聚合快照 — 凭据暴露专用视图
// ═══════════════════════════════════════════════════════════════

export interface CredentialExposureDashboardState {
  /** 所有已发现的凭据传播链 */
  propagation_chains: CredentialPropagationChain[];

  /** 按凭据类型分组的发现 */
  findings_by_kind: Record<CredentialKind, CredentialFinding[]>;

  /** 跨代理凭据共享统计 */
  sharing_stats: CredentialSharingStats;

  /** 补充需求 §A: 工作空间边界统计 */
  boundary_stats: WorkspaceBoundaryStats;

  /** 补充需求 §C: 过滤层完整性 */
  filter_integrity: FilterIntegritySnapshot;
}

export interface CredentialSharingStats {
  total_findings: number;
  total_propagation_hops: number;
  max_propagation_depth: number;
  /** 跨工作空间边界的传播次数 */
  cross_boundary_propagations: number;
  /** 违反过滤约束的事件数 (补充需求 §C) */
  filter_violations: number;
  /** 按代理统计的凭据持有数 */
  credentials_by_agent: Record<string, number>;
}

export interface WorkspaceBoundaryStats {
  total_file_events: number;
  inside_workspace_events: number;
  outside_workspace_events: number;
  /** 跨边界读取事件数 (补充需求 §B) */
  cross_boundary_read_events: number;
  /** 标记存活率: 未被过滤层丢弃的边界事件比例 */
  tag_survival_rate: number;
}

// ═══════════════════════════════════════════════════════════════
// 4. 组件 Props
//    'use client' 组件, 通过 zustand store 消费状态
// ═══════════════════════════════════════════════════════════════

// ── PropagationChainView ──
// 凭据传播链可视化: 水平/垂直流向图
// Agent A → shared_handoff.md → Agent B → shared_handoff.md → Agent C
export interface PropagationChainViewProps {
  chain: CredentialPropagationChain;
  /** 是否高亮跨边界跳 */
  highlight_cross_boundary: boolean;
  /** 补充需求 §C: 是否标注过滤层违规丢弃的事件 */
  show_filter_violations: boolean;
  onSelectHop: (hop: CredentialPropagationHop) => void;
  onNavigateToAgent: (agentId: string) => void;
}

// ── CredentialExposureFeed ──
// 凭据发现/传播的实时告警流
export interface CredentialExposureFeedProps {
  chains: CredentialPropagationChain[];
  filter_by_kind?: CredentialKind[];
  filter_by_severity?: AnomalySeverity[];
  onSelectChain: (chainId: string) => void;
}

// ── CredentialFindingCard ──
// 单个凭据发现卡片 (在 feed 中或详情面板中使用)
export interface CredentialFindingCardProps {
  finding: CredentialFinding;
  propagation_depth: number;
  total_hops: number;
  has_cross_boundary_leak: boolean;    // 补充需求 §A
  has_filter_violation: boolean;       // 补充需求 §C
  onExpand: (findingId: string) => void;
  onDismiss: () => void;
}

// ── CrossBoundaryReadIndicator ──
// 跨边界读取事件状态指示器 (DashboardShell 状态栏)
// 补充需求 §B: WARN-level, category=cross_boundary_read
export interface CrossBoundaryReadIndicatorProps {
  /** 活跃的跨边界读取事件列表 */
  events: CrossBoundaryReadEvent[];
  /** 刷新频率 (ms) */
  poll_interval_ms: number;
  /** 点击后展开详情面板 */
  onExpand: () => void;
}

// ── FilterIntegrityBadge ──
// 过滤层完整性徽章 (DashboardShell 顶栏)
// 补充需求 §C: allow_filter=false 约束验证
export interface FilterIntegrityBadgeProps {
  integrity: FilterIntegritySnapshot;
  /** 约束违规详情 */
  onInvestigate: () => void;
}

// ── WorkspaceBoundarySummary ──
// 工作空间边界摘要条 (侧栏或 Dashboard 顶部)
export interface WorkspaceBoundarySummaryProps {
  stats: WorkspaceBoundaryStats;
  /** 跨边界事件的 timeline sparkline (最近 N 秒) */
  cross_boundary_trend: Array<{ timestamp_ns: number; count: number }>;
  onFilterByBoundary: (boundary: "inside" | "outside" | "all") => void;
}

// ── CredentialPropagationTimelineRow ──
// TimelinePanel 中注册的新行类型: 凭据传播跳
export interface CredentialPropagationTimelineRowProps {
  hop: CredentialPropagationHop;
  finding: CredentialFinding;
  is_source: boolean;           // hop_order === 0 时高亮
  is_cross_boundary: boolean;   // 补充需求 §A
  onSelect: (hop: CredentialPropagationHop) => void;
  onNavigateToAgent: (agentId: string) => void;
}

// ═══════════════════════════════════════════════════════════════
// 5. 与现有类型系统的集成
// ═══════════════════════════════════════════════════════════════

/** 凭据暴露专用的 SSE stream_type */
export type CredentialExposureStreamEvent =
  | { stream_type: "credential_finding"; data: CredentialFinding }
  | { stream_type: "credential_propagation"; data: CredentialPropagationHop }
  | { stream_type: "cross_boundary_read"; data: CrossBoundaryReadEvent }
  | { stream_type: "filter_integrity"; data: FilterIntegritySnapshot };

// ═══════════════════════════════════════════════════════════════
// 6. 类型守卫
// ═══════════════════════════════════════════════════════════════

export function isCredentialFinding(
  e: CredentialExposureStreamEvent
): e is { stream_type: "credential_finding"; data: CredentialFinding } {
  return e.stream_type === "credential_finding";
}

export function isCredentialPropagation(
  e: CredentialExposureStreamEvent
): e is { stream_type: "credential_propagation"; data: CredentialPropagationHop } {
  return e.stream_type === "credential_propagation";
}

/** 检测凭据是否跨工作空间边界传播 (补充需求 §A + §B) */
export function isCrossBoundaryLeak(chain: CredentialPropagationChain): boolean {
  return chain.hops.some(
    hop => hop.workspace_boundary === "outside" && hop.triggered_cross_boundary_alert
  );
}

/** 检测传播链中是否有被违规过滤的事件 (补充需求 §C) */
export function hasFilterViolation(chain: CredentialPropagationChain): boolean {
  return chain.filter_integrity_violated && chain.dropped_event_ids.length > 0;
}
