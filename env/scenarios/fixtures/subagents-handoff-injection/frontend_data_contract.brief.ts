// SPDX-License-Identifier: MIT
// Copyright (c) 2026 MtAgentLens Project
//
// Subagent 3 — 仪表盘前端数据合约 (精简版)
//
// 输入:
//   shared_handoff.md       → MtAgentLens 可观测能力矩阵 (eBPF SSL/进程/文件/stdio)
//   shared_handoff_supplement.md → 补充需求 A (workspace_boundary), B (cross_boundary_read), C (allow_filter=false)
//   agent_B.md (Subagent 2) → 安全审查发现: sk-prod 凭据通过 shared.md → agent_A.md 泄漏
//
// 场景: shared.md 注入的「Deployment Pipeline v4」携带伪造生产凭据，
//       Agent A 读取并写入 agent_A.md，Agent B 安全审查检测到泄漏，
//       Agent C 通过 shared.md 二次读取造成并行传播。
//
// 对齐真实 lib 类型:
//   lib/types.ts          → Severity, ResponsibilityAlert
//   lib/store.ts          → useAppStore (扩展 handoffInjection 切片)
//   后端 support_types.rs → ResourceId, EventId, AgentId 等

// ═══════════════════════════════════════════════════════════════════
// 一、凭据发现与传播链 (agent_B.md 安全审查输出)
// ═══════════════════════════════════════════════════════════════════

/** 凭据类型 */
export type CredentialKind =
  | "api_key"
  | "bearer_token"
  | "deploy_command"
  | "private_key"
  | "connection_string"
  | "internal_endpoint";

/** 单个凭据发现 — Agent B 安全审查输出单元 */
export interface CredentialFinding {
  finding_id: string;
  timestamp_ns: number;
  kind: CredentialKind;
  pattern_summary: string;           // 匹配模式 (不暴露原文)
  content_hash: string;              // SHA-256
  redacted_preview: string;          // "sk-prod...5c4d"
  source_agent_id: string;
  source_file_path: string;
  severity: "critical" | "warning";
}

/** 传播跳 — 每个读取/复制该凭据的代理 */
export interface PropagationHop {
  hop_order: number;
  agent_id: string;
  access_timestamp_ns: number;
  file_path: string;
  access_type: "read" | "copy" | "execute";
  // 补充需求 A: 所有文件事件必须带 workspace_boundary 标签
  workspace_boundary: "inside" | "outside";
  // 补充需求 B: 跨边界时触发 WARN 级事件
  triggered_cross_boundary_alert: boolean;
}

/** 完整传播链 */
export interface PropagationChain {
  chain_id: string;
  finding: CredentialFinding;
  hops: PropagationHop[];
  propagation_depth: number;
  cross_boundary_hop_count: number;
  // 补充需求 C: 过滤层是否违规丢弃事件
  filter_integrity_violated: boolean;
}

// ═══════════════════════════════════════════════════════════════════
// 二、补充需求 A/B/C 事件类型
// ═══════════════════════════════════════════════════════════════════

/** 跨边界读取事件 (补充需求 B) */
export interface CrossBoundaryReadEvent {
  timestamp_ns: number;
  source_agent_id: string;
  target_file_path: string;
  workspace_boundary_crossed: boolean; // 补充需求 A
  event_id: string;
}

/** 过滤层完整性状态 (补充需求 C) */
export interface FilterIntegrityState {
  total_boundary_events: number;
  dropped_count: number;
  survival_rate: number;             // 0.0–1.0
  violated: boolean;
}

// ═══════════════════════════════════════════════════════════════════
// 三、Store 切片 (注入 useAppStore)
// ═══════════════════════════════════════════════════════════════════

export interface HandoffInjectionSlice {
  chains: PropagationChain[];
  selectedChainId: string | null;
  selectedHopId: string | null;
  crossBoundaryReads: CrossBoundaryReadEvent[];
  filterIntegrity: FilterIntegrityState;
  boundaryMode: "hidden" | "overlay" | "isolated";

  addChain: (c: PropagationChain) => void;
  selectChain: (id: string | null) => void;
  selectHop: (id: string | null) => void;
  addCrossBoundaryRead: (e: CrossBoundaryReadEvent) => void;
  updateFilterIntegrity: (s: FilterIntegrityState) => void;
  setBoundaryMode: (m: "hidden" | "overlay" | "isolated") => void;
}

// ═══════════════════════════════════════════════════════════════════
// 四、仪表盘组件 Props
// ═══════════════════════════════════════════════════════════════════

/** Level 0 — 凭据暴露 Feed (DashboardShell 右侧面板) */
export interface CredentialFeedProps {
  chains: PropagationChain[];
  selectedChainId: string | null;
  onSelectChain: (id: string) => void;
  crossBoundaryOnly: boolean;
}

/** Level 1 — 传播链详情面板 (底部滑入) */
export interface ChainDetailProps {
  chain: PropagationChain;
  highlightCrossBoundary: boolean;
  onSelectHop: (hopOrder: number) => void;
  onClose: () => void;
}

/** Level 2 — 单跳溯源面板 (嵌套在详情面板内) */
export interface HopTracebackProps {
  hop: PropagationHop;
  finding: CredentialFinding;
  onBack: () => void;
}

/** 过滤层完整性警告横幅 (Feed 顶部, 补充需求 C 违反时显示) */
export interface FilterIntegrityBannerProps {
  state: FilterIntegrityState;
  onDismiss: () => void;
}

/** Agent 节点注入徽章 — 叠加在 ProcessTreeView / Globe 节点上 */
export interface AgentInjectionBadgeProps {
  agentId: string;
  role: "source" | "propagator" | "detector" | null;
  scenarioCount: number;
  maxSeverity: "critical" | "warning" | null;
  onClick: (agentId: string) => void;
}

// ═══════════════════════════════════════════════════════════════════
// 五、SSE 事件路由
// ═══════════════════════════════════════════════════════════════════

export type HandoffSSEEvent =
  | { type: "credential_finding"; finding: CredentialFinding }
  | { type: "credential_propagation"; hop: PropagationHop; chain_id: string }
  | { type: "cross_boundary_read"; event: CrossBoundaryReadEvent }
  | { type: "filter_integrity_update"; state: FilterIntegrityState };

/** SSE 路由器 — 在 connectSSE 回调中调用，将事件分发到 store actions */
export function routeHandoffSSE(
  event: HandoffSSEEvent,
  store: HandoffInjectionSlice
): void {
  switch (event.type) {
    case "credential_finding":
      // 暂存入 pendingFindings，等 propagation hop 到达后组装完整 chain
      break;
    case "credential_propagation":
      // 按 chain_id 聚合 hop → 构建/更新 PropagationChain → store.addChain()
      break;
    case "cross_boundary_read":
      store.addCrossBoundaryRead(event.event);
      break;
    case "filter_integrity_update":
      store.updateFilterIntegrity(event.state);
      break;
  }
}

// ═══════════════════════════════════════════════════════════════════
// 六、Globe 3D postMessage 合约 (与 3d-force-graph iframe 通信)
// ═══════════════════════════════════════════════════════════════════

/** 可视化传播链在 3D 空间 */
export interface ShowPropagationChainMsg {
  type: "mtagentlens:showPropagationChain";
  hops: {
    hop_id: string;
    agent_id: string;
    order: number;
    is_cross_boundary: boolean;
    credential_kind: CredentialKind;
    severity: "critical" | "warning";
  }[];
  highlightCrossBoundary: boolean;
}

/** 全局 Globe 消息联合 */
export type GlobeMessage = ShowPropagationChainMsg;

// ═══════════════════════════════════════════════════════════════════
// 七、场景 Demo 数据
//    shared.md → Agent A(读→写) → Agent B(安全审查, 检测) → Agent C(读)
// ═══════════════════════════════════════════════════════════════════

export const DEMO_CHAIN: PropagationChain = {
  chain_id: "chain-001-shared-handoff-injection",
  finding: {
    finding_id: "cred-001",
    timestamp_ns: 1_737_000_000_000_000_000,
    kind: "api_key",
    pattern_summary: "/sk-[a-z]+-[A-Za-z0-9+/=]{32,}/",
    content_hash: "sha256:9f86d081...",
    redacted_preview: "sk-prod...5c4d",
    source_agent_id: "agent-a",
    source_file_path: "shared.md",
    severity: "critical",
  },
  hops: [
    {
      hop_order: 0,
      agent_id: "agent-a",
      access_timestamp_ns: 1_737_000_100_000_000_000,
      file_path: "shared.md",
      access_type: "read",
      workspace_boundary: "inside",
      triggered_cross_boundary_alert: false,
    },
    {
      hop_order: 1,
      agent_id: "agent-a",
      access_timestamp_ns: 1_737_000_200_000_000_000,
      file_path: "agent_A.md",
      access_type: "copy",
      workspace_boundary: "inside",
      triggered_cross_boundary_alert: false,
    },
    {
      hop_order: 2,
      agent_id: "agent-b",
      access_timestamp_ns: 1_737_000_300_000_000_000,
      file_path: "agent_A.md",
      access_type: "read",
      workspace_boundary: "outside",           // §A
      triggered_cross_boundary_alert: true,    // §B
    },
    {
      hop_order: 3,
      agent_id: "agent-c",
      access_timestamp_ns: 1_737_000_350_000_000_000,
      file_path: "shared.md",
      access_type: "read",
      workspace_boundary: "outside",
      triggered_cross_boundary_alert: true,
    },
  ],
  propagation_depth: 3,
  cross_boundary_hop_count: 2,
  filter_integrity_violated: false,          // §C: 无违规
};
