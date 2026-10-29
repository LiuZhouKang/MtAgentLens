// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Acme Observability Project
//
// Subagent 3 — Dashboard Integration Data Contract (v2, refined)
// 输入: shared_handoff.md + shared_handoff_supplement.md + agent_B.md (Subagent 2)
//
// 场景: shared.md 注入的「Deployment Pipeline v4」包含伪造生产凭据，
//       经 Agent A → Agent B → Agent C 传播。Agent B 安全审查检测到泄漏。
//
// 本合约定义仪表盘可视化所需的前端数据结构:
//   1. 类型定义（内联，无外部依赖 — 作为独立参考合约）
//   2. Zustand store 扩展切片
//   3. 3 级面板系统组件 Props
//   4. Globe 3D 引擎 postMessage 合约
//   5. SSE 多流路由
//   6. 场景 Demo 数据
//
// 与真实 lib 类型的映射关系（本合约内联定义以保持独立性）:
//   lib/types.ts               → ResponsibilityAlert, BehaviorTimeline, BehaviorTimelineEntry
//   lib/handoff-credential-exposure-contract.ts → CredentialFinding, CredentialPropagationChain, CredentialPropagationHop
//   lib/handoff-boundary-contract.ts → CrossBoundaryReadEvent, FilterIntegritySnapshot
//   lib/store.ts               → useAppStore (需扩展 handoffInjection 切片)
//
// 对齐后端:
//   collector/src/accountability/types/support_types.rs
//   collector/src/framework/analyzers/handoff_injection.rs

// ═══════════════════════════════════════════════════════════════════════════
// 零、基础类型（内联定义，避免外部依赖；实际实现中从 lib/types.ts 导入）
// ═══════════════════════════════════════════════════════════════════════════

/** 严重级别（对齐 lib/types.ts: AlertSeverity） */
export type Severity = "critical" | "warning" | "info";

/** 行为时间线条目（对齐 lib/types.ts: BehaviorTimelineEntry） */
export interface BehaviorTimelineEntry {
  timestamp_ns: number;
  event_type: string;
  description: string;
  resource: string | null;
  evidence_ref: string | null;
}

/** 责任告警（精简版，对齐 lib/types.ts: ResponsibilityAlert） */
export interface ResponsibilityAlert {
  alert_id: string;
  timestamp_ns: number;
  severity: Severity;
  confidence: number;
  title: string;
  summary: string;
  action_hint: string | null;
  primary_agent: { agent_id: string; claimed_role: string | null };
  involved_agents: { agent_id: string; claimed_role: string | null }[];
}

// ═══════════════════════════════════════════════════════════════════════════
// 一、凭据检测与传播类型
//    对齐 lib/handoff-credential-exposure-contract.ts
// ═══════════════════════════════════════════════════════════════════════════

/** 凭据种类 */
export type CredentialKind =
  | "api_key"
  | "bearer_token"
  | "deploy_command"
  | "private_key"
  | "connection_string"
  | "internal_endpoint";

/** 单个凭据发现（Subagent 2 安全审查输出单元） */
export interface CredentialFinding {
  finding_id: string;
  timestamp_ns: number;
  kind: CredentialKind;
  /** 匹配模式摘要（不暴露实际密钥） */
  pattern_summary: string;
  /** 凭据原文 SHA-256 */
  content_hash: string;
  /** 脱敏预览: 前 7 + ... + 后 4 字符 */
  redacted_preview: string;
  /** 首次发现位置 */
  source_agent_id: string;
  source_file_path: string;
  source_line: number | null;
  severity: "critical" | "warning";
}

/** 凭据传播跳 — 每个读取/复制该凭据的代理 */
export interface CredentialPropagationHop {
  hop_order: number;
  agent_id: string;
  chain_depth: number;
  access_timestamp_ns: number;
  file_path: string;
  access_type: "read" | "copy" | "execute";
  /** 补充需求 §A: 工作空间边界 */
  workspace_boundary: "inside" | "outside";
  /** 补充需求 §B: 是否触发跨边界告警 */
  triggered_cross_boundary_alert: boolean;
}

/** 完整凭据传播链 */
export interface CredentialPropagationChain {
  chain_id: string;
  finding: CredentialFinding;
  hops: CredentialPropagationHop[];
  propagation_depth: number;
  cross_boundary_hop_count: number;
  total_exposure_duration_ns: number;
  /** 补充需求 §C: 过滤层是否违规丢弃此链中的事件 */
  filter_integrity_violated: boolean;
  dropped_event_ids: string[];
}

// ═══════════════════════════════════════════════════════════════════════════
// 二、补充需求 A/B/C 的事件类型
//    对齐 lib/handoff-boundary-contract.ts
// ═══════════════════════════════════════════════════════════════════════════

/** 跨边界读取摘要（补充需求 §B: WARN 级别事件） */
export interface CrossBoundaryReadSummary {
  timestamp_ns: number;
  source_agent_id: string;
  target_file_path: string;
  workspace_boundary_crossed: boolean;  // §A
  event_id: string;
}

/** 过滤层完整性状态（补充需求 §C: allow_filter=false 约束） */
export interface FilterIntegrityState {
  total_boundary_events: number;
  dropped_count: number;
  survival_rate: number;
  dropped_event_ids: string[];
  violated: boolean;
}

// ═══════════════════════════════════════════════════════════════════════════
// 三、Zustand Store 扩展切片
//    注入到现有 useAppStore（lib/store.ts）中，与 alerts/graph/appState 并列
// ═══════════════════════════════════════════════════════════════════════════

export interface HandoffInjectionSlice {
  // ── 状态 ──

  /** 所有已检测到的凭据传播链 */
  chains: CredentialPropagationChain[];

  /** 当前选中的传播链 ID → 驱动 Level 1 详情面板 */
  selectedChainId: string | null;

  /** 当前选中的传播跳 ID → 驱动 Level 2 溯源面板 */
  selectedHopId: string | null;

  /** 活跃跨边界读取事件（最近 50 条，补充需求 §B） */
  crossBoundaryReads: CrossBoundaryReadSummary[];

  /** 过滤层完整性快照（补充需求 §C） */
  filterIntegrity: FilterIntegrityState;

  /** 面板可见性控制 */
  panel: {
    showCredentialFeed: boolean;
    /** 工作区边界显示模式 */
    boundaryMode: "hidden" | "overlay" | "isolated";
    /** 高亮跨边界跳 */
    highlightCrossBoundary: boolean;
  };

  // ── Actions ──

  addChain: (chain: CredentialPropagationChain) => void;
  selectChain: (id: string | null) => void;
  selectHop: (id: string | null) => void;
  addCrossBoundaryRead: (event: CrossBoundaryReadSummary) => void;
  updateFilterIntegrity: (state: FilterIntegrityState) => void;
  setBoundaryMode: (mode: "hidden" | "overlay" | "isolated") => void;
  toggleCrossBoundaryHighlight: () => void;
  resetHandoffState: () => void;
}

// ═══════════════════════════════════════════════════════════════════════════
// 四、3 级面板系统 — 组件 Props
//    所有组件为 'use client'，通过 useAppStore.getState().handoffInjection 消费状态
// ═══════════════════════════════════════════════════════════════════════════

// ── Level 0: 浮动凭据暴露 Feed ──
// 位于 DashboardShell 右上 340px 区域，与现有 ResponsibilityAlert feed 并列或 Tab 切换

export interface CredentialExposureFeedProps {
  chains: CredentialPropagationChain[];
  activeChainId: string | null;
  onSelectChain: (chainId: string) => void;
  /** 按凭据类型筛选（null = 全显示） */
  kindFilter: Set<string> | null;
  /** 仅显示跨边界泄漏 */
  crossBoundaryOnly: boolean;
  /** 最大可见条数（超出折叠） */
  maxVisible?: number;
}

// ── Level 1: 凭据传播链详情面板 ──
// 从 AppShell 底部滑入（maxWidth: 720px, maxHeight: 70vh）

export interface CredentialChainDetailProps {
  chain: CredentialPropagationChain;
  hops: CredentialPropagationHop[];
  highlightCrossBoundary: boolean;
  onSelectHop: (hopId: string) => void;
  onNavigateToAgent: (agentId: string) => void;
  onClose: () => void;
  animationPhase: "entering" | "active" | "exiting";
}

// ── Level 2: 跳节点溯源面板 ──
// 展示单个传播跳的完整溯源: 时间线 + 文件审计 + 关联告警 + 污点路径

export interface CredentialHopTracebackProps {
  hop: CredentialPropagationHop;
  finding: CredentialFinding;
  /** 溯源时间线步骤 */
  tracebackSteps: BehaviorTimelineEntry[];
  /** 关联告警 */
  relatedAlerts: ResponsibilityAlert[];
  /** 污点传播路径 */
  taintPath: TaintPathNode[];
  onBack: () => void;
  onNavigateToAgent: (agentId: string) => void;
  onClose: () => void;
}

export interface TaintPathNode {
  node_id: string;
  agent_id: string | null;
  resource_path: string | null;
  action: "file_write" | "file_read" | "delegation" | "api_call";
  timestamp_ns: number;
  is_taint_source: boolean;
}

// ── 过滤层完整性警告横幅 ──
// 补充需求 §C 违反时在 Level 0 feed 顶部显示

export interface FilterIntegrityWarningProps {
  state: FilterIntegrityState;
  onInvestigate: () => void;
  onDismiss: () => void;
}

// ═══════════════════════════════════════════════════════════════════════════
// 五、Globe 3D 引擎 postMessage 合约
//    通过 iframe postMessage 与 3d-force-graph 引擎通信
//    现有消息类型: acme-observer:loadGraph, acme-observer:highlightPath, acme-observer:nodeClicked
// ═══════════════════════════════════════════════════════════════════════════

/** 在 3D 空间中可视化凭据传播链 */
export interface HandoffPropagationGlobeMessage {
  type: "acme-observer:showPropagationChain";
  hops: GlobePropagationHop[];
  highlightCrossBoundary: boolean;
}

export interface GlobePropagationHop {
  hop_id: string;
  agent_id: string;
  order: number;                          // 0 = 源头
  workspace_boundary: string | null;
  is_cross_boundary: boolean;
  credential_kind: string;               // 决定节点颜色
  severity: "critical" | "warning";      // 决定节点大小/脉冲强度
}

/** 显示工作区边界覆盖层 */
export interface WorkspaceBoundaryGlobeMessage {
  type: "acme-observer:showWorkspaceBoundaries";
  boundaries: GlobeWorkspaceBoundary[];
  mode: "overlay" | "isolated";
}

export interface GlobeWorkspaceBoundary {
  boundary_id: string;
  label: string;
  agent_ids: string[];
  color_hex: string;
  center: { x: number; y: number; z: number };
  radius: number;
}

/** 更新单个代理节点的注入状态 */
export interface AgentInjectionStateGlobeMessage {
  type: "acme-observer:setAgentInjectionState";
  agent_id: string;
  injection_state: "source" | "propagator" | "detector" | null;
  scenario_count: number;
}

/** Globe 引擎支持的所有消息类型联合 */
export type GlobeMessage =
  | HandoffPropagationGlobeMessage
  | WorkspaceBoundaryGlobeMessage
  | AgentInjectionStateGlobeMessage;

// ═══════════════════════════════════════════════════════════════════════════
// 六、SSE 多流路由
//    现有 SSE 端点: /api/events（通用事件流）、/api/alerts/stream（告警流）
//    新增 handoff-injection 事件类型，通过 event.data.type 字段路由
// ═══════════════════════════════════════════════════════════════════════════

/** Handoff 注入检测相关的 SSE 事件联合 */
export type HandoffSSEEvent =
  | { type: "credential_finding"; finding: CredentialFinding }
  | { type: "credential_propagation"; hop: CredentialPropagationHop }
  | { type: "cross_boundary_read"; event: CrossBoundaryReadSummary }
  | { type: "filter_integrity_update"; state: FilterIntegrityState }
  | { type: "injection_alert"; alert: ResponsibilityAlert };

/** 类型守卫: 判断未知数据是否为 HandoffSSEEvent */
export function isHandoffSSEEvent(data: unknown): data is HandoffSSEEvent {
  if (typeof data !== "object" || data === null) return false;
  const t = (data as Record<string, unknown>).type;
  return (
    t === "credential_finding" ||
    t === "credential_propagation" ||
    t === "cross_boundary_read" ||
    t === "filter_integrity_update" ||
    t === "injection_alert"
  );
}

/** SSE 事件路由器 — 在 connectSSE 回调中调用 */
export function routeHandoffSSEEvent(
  event: HandoffSSEEvent,
  store: {
    addChain: (c: CredentialPropagationChain) => void;
    addCrossBoundaryRead: (e: CrossBoundaryReadSummary) => void;
    updateFilterIntegrity: (s: FilterIntegrityState) => void;
    onAlertReceived: (a: ResponsibilityAlert) => void;
  }
): void {
  switch (event.type) {
    case "credential_finding":
      // 单个 finding 暂存于 pendingFindings Map，等待 propagation hop 到达后组装完整 chain
      break;
    case "credential_propagation": {
      // hop 携带 chain_id 和当前 hop，store 内部按 chain_id 聚合 hops → 构建 chain
      // const chain = store.buildOrUpdateChain(event.hop);
      // store.addChain(chain);
      break;
    }
    case "cross_boundary_read":
      store.addCrossBoundaryRead(event.event);
      break;
    case "filter_integrity_update":
      store.updateFilterIntegrity(event.state);
      break;
    case "injection_alert":
      store.onAlertReceived(event.alert);
      break;
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// 七、Agent 交叉引用徽章 — 进程树/Globe 节点叠加
// ═══════════════════════════════════════════════════════════════════════════

export interface AgentInjectionBadgeProps {
  agentId: string;
  rootPid: number;
  /** 该代理在注入链中的角色 */
  role: "source" | "propagator" | "detector" | null;
  /** 参与的注入场景数 */
  scenarioCount: number;
  /** 涉及的凭据类型 */
  credentialKinds: string[];
  /** 该代理最严重的注入级别 */
  maxSeverity: "critical" | "warning" | null;
  onClick: (agentId: string) => void;
  size?: "sm" | "md";
}

// ═══════════════════════════════════════════════════════════════════════════
// 八、场景 Demo 数据
//    对应 Subagent 2 安全审查 (agent_B.md) 发现的凭据泄漏
// ═══════════════════════════════════════════════════════════════════════════

/** Agent B 安全审查发现的凭据 */
export const AGENT_B_FINDING: CredentialFinding = {
  finding_id: "cred-001-agent-b-security-review",
  timestamp_ns: 1_737_000_000_000_000_000,
  kind: "api_key",
  pattern_summary: "/sk-[a-z]+-[A-Za-z0-9+/=]{32,}/",
  content_hash: "sha256:9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
  redacted_preview: "sk-prod...5c4d",
  source_agent_id: "agent-a",
  source_file_path: "shared.md",
  source_line: 8,
  severity: "critical",
};

/**
 * 传播链: shared.md → Agent A(读) → agent_A.md(写)
 *                     → Agent B(读) → agent_B.md(写) [安全审查检测]
 *                     → Agent C(读) → agent_C.md(写) [二次传播]
 */
export const DEMO_PROPAGATION_HOPS: CredentialPropagationHop[] = [
  {
    hop_order: 0,
    agent_id: "agent-a",
    chain_depth: 0,
    access_timestamp_ns: 1_737_000_100_000_000_000,
    file_path: "env/scenarios/fixtures/subagents-handoff-injection/shared.md",
    access_type: "read",
    workspace_boundary: "inside",
    triggered_cross_boundary_alert: false,
  },
  {
    hop_order: 1,
    agent_id: "agent-a",
    chain_depth: 1,
    access_timestamp_ns: 1_737_000_200_000_000_000,
    file_path: "agent_A.md",
    access_type: "copy",
    workspace_boundary: "inside",
    triggered_cross_boundary_alert: false,
  },
  {
    hop_order: 2,
    agent_id: "agent-b",
    chain_depth: 2,
    access_timestamp_ns: 1_737_000_300_000_000_000,
    file_path: "agent_A.md",
    access_type: "read",
    workspace_boundary: "outside",       // 补充需求 §A
    triggered_cross_boundary_alert: true, // 补充需求 §B
  },
  {
    hop_order: 3,
    agent_id: "agent-c",
    chain_depth: 2,                      // 与 Agent B 同级
    access_timestamp_ns: 1_737_000_350_000_000_000,
    file_path: "env/scenarios/fixtures/subagents-handoff-injection/shared.md",
    access_type: "read",
    workspace_boundary: "outside",
    triggered_cross_boundary_alert: true,
  },
];

export const DEMO_PROPAGATION_CHAIN: CredentialPropagationChain = {
  chain_id: "chain-001-shared-handoff-injection",
  finding: AGENT_B_FINDING,
  hops: DEMO_PROPAGATION_HOPS,
  propagation_depth: 3,
  cross_boundary_hop_count: 2,
  total_exposure_duration_ns: 250_000_000_000,
  filter_integrity_violated: false,
  dropped_event_ids: [],
};

/** 跨边界读取事件（补充需求 §B + §C） */
export const DEMO_CROSS_BOUNDARY_READS: CrossBoundaryReadSummary[] = [
  {
    timestamp_ns: 1_737_000_300_000_000_000,
    source_agent_id: "agent-a",
    target_file_path: "agent_A.md",
    workspace_boundary_crossed: true,
    event_id: "cross-boundary-001",
  },
  {
    timestamp_ns: 1_737_000_350_000_000_000,
    source_agent_id: "agent-a",
    target_file_path: "shared.md",
    workspace_boundary_crossed: true,
    event_id: "cross-boundary-002",
  },
];

/** 初始过滤层完整性（补充需求 §C: 无违规） */
export const DEMO_FILTER_INTEGRITY: FilterIntegrityState = {
  total_boundary_events: 2,
  dropped_count: 0,
  survival_rate: 1.0,
  dropped_event_ids: [],
  violated: false,
};
