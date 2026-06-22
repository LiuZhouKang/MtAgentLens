"use client";

import React, { useEffect, useMemo, useState } from "react";
import type { DelegationNode, HandoffObsMetrics } from "@/lib/handoff-observability-contract";
import { ChevronRight, Circle, Layers, Users, GitForkIcon, AlertTriangle } from "lucide-react";
import PrismPanel from "@/components/ui/prism-panel";

// ═══════════════════════════════════════════════════════════════════════════════
// 常量：深度色彩映射
// ═══════════════════════════════════════════════════════════════════════════════

const DEPTH_COLORS: Record<number, { dot: string; bg: string; border: string; text: string }> = {
  0: { dot: "#818cf8", bg: "rgba(129, 140, 248, 0.12)", border: "rgba(129, 140, 248, 0.24)", text: "#a5b4fc" },
  1: { dot: "#60a5fa", bg: "rgba(96, 165, 250, 0.12)", border: "rgba(96, 165, 250, 0.24)", text: "#93c5fd" },
  2: { dot: "#22d3ee", bg: "rgba(34, 211, 238, 0.12)", border: "rgba(34, 211, 238, 0.24)", text: "#67e8f9" },
  3: { dot: "#2dd4bf", bg: "rgba(45, 212, 191, 0.12)", border: "rgba(45, 212, 191, 0.24)", text: "#5eead4" },
  4: { dot: "#34d399", bg: "rgba(52, 211, 153, 0.12)", border: "rgba(52, 211, 153, 0.24)", text: "#6ee7b7" },
  5: { dot: "#fbbf24", bg: "rgba(251, 191, 36, 0.12)", border: "rgba(251, 191, 36, 0.24)", text: "#fcd34d" },
};

function depthColor(depth: number) {
  return DEPTH_COLORS[depth] ?? { dot: "#94a3b8", bg: "rgba(148, 163, 184, 0.12)", border: "rgba(148, 163, 184, 0.2)", text: "var(--text-secondary)" };
}

// ═══════════════════════════════════════════════════════════════════════════════
// 局部类型扩展: 为 DelegationNode 增加运行时 children 字段
// 后端应返回嵌套结构，此处支持从 flat child_ids 构建 + 直接接收嵌套 children
// ═══════════════════════════════════════════════════════════════════════════════

interface TreeNode extends DelegationNode {
  children: TreeNode[];
}

interface FlatRow {
  node: TreeNode;
  depth: number;
  isLastChild: boolean;
  hasChildren: boolean;
}

/**
 * 将可能带 child_ids 的根节点展开为嵌套 TreeNode（含 children）。
 * 如果节点已带 children 属性（后端嵌套返回），则直接使用；
 * 否则只展示单层（仅 root），子节点通过 empty children 跳过。
 */
function buildTree(root: DelegationNode | null, allNodes?: Map<string, DelegationNode>): TreeNode | null {
  if (!root) return null;
  const nodeMap = allNodes ?? new Map();

  function toTreeNode(node: DelegationNode): TreeNode {
    // 如果节点已带嵌套 children（后端嵌套返回格式），直接递归使用
    const existingChildren = (node as unknown as { children?: unknown[] }).children;
    if (existingChildren && existingChildren.length > 0) {
      const children: TreeNode[] = existingChildren.map((child) =>
        toTreeNode(child as DelegationNode)
      );
      return { ...node, children };
    }
    // 否则从 child_ids 查找 nodeMap 构建（前端扁平模式）
    const children: TreeNode[] = [];
    for (const childId of node.child_ids) {
      const child = nodeMap.get(childId);
      if (child) {
        children.push(toTreeNode(child));
      }
    }
    return { ...node, children };
  }

  return toTreeNode(root);
}

/**
 * 将嵌套 TreeNode 展平为行列表（用于列表渲染）
 */
function flattenTree(node: TreeNode | null): FlatRow[] {
  if (!node) return [];
  const result: FlatRow[] = [];

  function walk(n: TreeNode, depth: number, isLastChild: boolean) {
    const hasChildren = n.children.length > 0;
    result.push({ node: n, depth, isLastChild, hasChildren });

    if (depth > 20) return; // 安全限制
    for (let i = 0; i < n.children.length; i++) {
      walk(n.children[i], depth + 1, i === n.children.length - 1);
    }
  }

  walk(node, 0, true);
  return result;
}

// ═══════════════════════════════════════════════════════════════════════════════
// 子组件: TreeNodeRow — 单行树节点渲染
// ═══════════════════════════════════════════════════════════════════════════════

const TreeNodeRow: React.FC<{
  node: TreeNode;
  depth: number;
  isSelected: boolean;
  onSelect: (nodeId: string) => void;
}> = ({ node, depth, isSelected, onSelect }) => {
  const color = depthColor(depth);
  const isCompleted = node.completed_at_ms != null;
  const contextOk = node.context_overhead_ratio < 0.6;
  const hasChildren = node.child_ids.length > 0;

  return (
    <button
      onClick={() => onSelect(node.node_id)}
      className="prism-tree-row"
      data-active={isSelected}
      style={{
        color: "var(--text-primary)",
      }}
    >
      <span className="flex items-center gap-0.5 shrink-0">
        {Array.from({ length: depth }).map((_, i) => (
          <span
            key={i}
            style={{
              width: 12,
              height: 1,
              background: "var(--border-soft)",
              opacity: i === depth - 1 ? 0.35 : 0.72,
            }}
          />
        ))}
      </span>

      <span
        className="w-4 h-4 shrink-0 flex items-center justify-center"
        style={{ color: "var(--text-tertiary)" }}
      >
        {hasChildren ? (
          <ChevronRight
            size={12}
            style={{
              transform: isSelected ? "rotate(90deg)" : "rotate(0deg)",
              transition: "transform 160ms ease",
            }}
          />
        ) : (
          <span style={{ width: 8, height: 1, background: "var(--text-tertiary)", opacity: 0.8 }} />
        )}
      </span>

      <span
        className="shrink-0 rounded-full"
        style={{
          width: 8,
          height: 8,
          background: isCompleted ? "var(--tone-success-text)" : "var(--tone-warning-text)",
          boxShadow: isCompleted
            ? "0 0 0 4px rgba(52, 211, 153, 0.12)"
            : "0 0 0 4px rgba(251, 191, 36, 0.12)",
        }}
      />

      <span
        className="font-mono truncate max-w-[120px]"
        style={{ color: color.text, fontSize: 12, fontWeight: 600 }}
      >
        {node.agent_id || node.node_id.slice(0, 8)}
      </span>

      <span
        className="shrink-0"
        style={{
          padding: "3px 7px",
          borderRadius: 999,
          fontSize: 10,
          fontWeight: 600,
          background: color.bg,
          border: `1px solid ${color.border}`,
          color: color.text,
        }}
      >
        L{depth}
      </span>

      {!contextOk && (
        <span
          className="shrink-0 flex items-center gap-0.5"
          style={{ color: "var(--tone-warning-text)" }}
          title={`上下文饱和度: ${(node.context_overhead_ratio * 100).toFixed(0)}%`}
        >
          <AlertTriangle size={10} />
        </span>
      )}

      <span
        className="ml-auto shrink-0 font-mono"
        style={{ fontSize: 10, color: "var(--text-tertiary)" }}
      >
        {isCompleted ? `✓ ${Math.round((node.completed_at_ms! - node.spawned_at_ms) / 1000)}s` : "▶ 运行中"}
      </span>
    </button>
  );
};

// ═══════════════════════════════════════════════════════════════════════════════
// 子组件: MetricsBar — 指标汇总条
// ═══════════════════════════════════════════════════════════════════════════════

const MetricsBar: React.FC<{ metrics: HandoffObsMetrics }> = ({ metrics }) => {
  const items = [
    { label: "生成", value: metrics.total_spawned, icon: GitForkIcon, color: "#a5b4fc", tone: "info" },
    { label: "完成", value: metrics.total_completed, icon: Users, color: "var(--tone-success-text)", tone: "success" },
    { label: "活跃", value: metrics.active_count, icon: Circle, color: "var(--tone-warning-text)", tone: "warning" },
    { label: "最大深度", value: metrics.max_chain_depth, icon: Layers, color: "#93c5fd", tone: "info" },
  ];

  return (
    <div className="prism-pill-row">
      {items.map((item) => {
        const Icon = item.icon;
        return (
          <div key={item.label} className="prism-pill" data-tone={item.tone}>
            <Icon size={12} style={{ color: item.color }} />
            <span>{item.label}</span>
            <span className="font-mono" style={{ fontSize: 12, fontWeight: 700, color: item.color }}>
              {item.value}
            </span>
          </div>
        );
      })}
      {metrics.fan_out_critical && (
        <span className="prism-pill" data-tone="danger">
          <AlertTriangle size={12} />
          扇出风暴: {metrics.fan_out_ratio.toFixed(1)}:1
        </span>
      )}
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════════
// 子组件: NodeDetailPanel — 选中节点的详情面板
// ═══════════════════════════════════════════════════════════════════════════════

const NodeDetailPanel: React.FC<{
  node: TreeNode;
  onClose: () => void;
}> = ({ node, onClose }) => {
  const [liveNowMs, setLiveNowMs] = useState<number | null>(null);
  const color = depthColor(node.chain_depth);
  const isCompleted = node.completed_at_ms != null;
  const latencyMs = isCompleted
    ? node.completed_at_ms! - node.spawned_at_ms
    : Math.max(0, (liveNowMs ?? node.spawned_at_ms) - node.spawned_at_ms);
  const idle = node.idle_breakdown;

  const contextPct = (node.context_overhead_ratio * 100).toFixed(1);
  const contextColor =
    node.context_overhead_ratio > 0.85
      ? "var(--tone-danger-text)"
      : node.context_overhead_ratio > 0.6
        ? "var(--tone-warning-text)"
        : "var(--tone-success-text)";

  useEffect(() => {
    if (isCompleted) return;
    setLiveNowMs(Date.now());
    const timer = window.setInterval(() => {
      setLiveNowMs(Date.now());
    }, 1000);
    return () => window.clearInterval(timer);
  }, [isCompleted]);

  return (
    <div className="prism-detail-card">
      <div className="flex items-center justify-between gap-10">
        <div className="flex items-center gap-2">
          <span
            style={{
              width: 10,
              height: 10,
              borderRadius: "50%",
              background: isCompleted ? "var(--tone-success-text)" : "var(--tone-warning-text)",
            }}
          />
          <span className="font-mono font-semibold text-sm" style={{ color: color.text }}>
            {node.agent_id || node.node_id.slice(0, 12)}
          </span>
          <span
            style={{
              padding: "3px 7px",
              borderRadius: 999,
              fontSize: 10,
              fontWeight: 600,
              background: color.bg,
              border: `1px solid ${color.border}`,
              color: color.text,
            }}
          >
            L{node.chain_depth}
          </span>
        </div>
        <button
          onClick={onClose}
          style={{
            border: "1px solid var(--border-soft)",
            background: "var(--surface-soft)",
            color: "var(--text-secondary)",
            borderRadius: 999,
            minWidth: 28,
            height: 28,
            cursor: "pointer",
          }}
        >
          ✕
        </button>
      </div>

      <div className="prism-detail-grid">
        <div className="prism-detail-metric">
          <div className="prism-detail-metric__label">延迟</div>
          <div className="prism-detail-metric__value font-mono">
            {latencyMs > 60_000 ? `${(latencyMs / 60_000).toFixed(1)}m` : `${(latencyMs / 1000).toFixed(1)}s`}
          </div>
        </div>
        <div className="prism-detail-metric">
          <div className="prism-detail-metric__label">上下文饱和度</div>
          <div className="prism-detail-metric__value font-mono" style={{ color: contextColor }}>
            {contextPct}%
          </div>
        </div>
        <div className="prism-detail-metric">
          <div className="prism-detail-metric__label">输入/输出</div>
          <div className="prism-detail-metric__value font-mono">
            {(node.bytes_in / 1024).toFixed(0)}k / {(node.bytes_out / 1024).toFixed(0)}k
          </div>
        </div>
        <div className="prism-detail-metric">
          <div className="prism-detail-metric__label">Token 数</div>
          <div className="prism-detail-metric__value font-mono">
            {node.token_count.toLocaleString()}
          </div>
        </div>
      </div>

      {idle && (
        <div>
          <div className="prism-detail-metric__label" style={{ marginBottom: 8 }}>空闲归因 (ms)</div>
          <div style={{ display: "grid", gap: 8 }}>
            <IdleBar label="排队等待派发" value={idle.queued_awaiting_dispatch_ms} max={4096} color="rgba(148, 163, 184, 0.92)" />
            <IdleBar label="上下文注入 I/O" value={idle.context_injection_io_ms} max={4096} color="rgba(96, 165, 250, 0.92)" />
            <IdleBar label="模型推理排队" value={idle.model_inference_queueing_ms} max={4096} color="rgba(167, 139, 250, 0.92)" />
            <IdleBar label="工具调用往返" value={idle.tool_call_round_trip_ms} max={4096} color="rgba(52, 211, 153, 0.92)" />
          </div>
        </div>
      )}

      {node.model_assigned && (
        <div className="prism-pill-row" style={{ gap: 6 }}>
          <span style={{ fontSize: 11, color: "var(--text-quaternary)", letterSpacing: "0.12em", textTransform: "uppercase" }}>
            模型
          </span>
          <code className="prism-pill" style={{ fontFamily: "var(--font-geist-mono), monospace" }}>{node.model_assigned}</code>
          {node.model_optimal && node.model_optimal !== node.model_assigned && (
            <>
              <span style={{ color: "var(--text-tertiary)", fontSize: 11 }}>→ 推荐</span>
              <code className="prism-pill" data-tone="warning" style={{ fontFamily: "var(--font-geist-mono), monospace" }}>
                {node.model_optimal}
              </code>
              <span style={{ color: "var(--tone-warning-text)" }} title="非最优模型分配">⚠</span>
            </>
          )}
        </div>
      )}

      {node.child_ids.length > 0 && (
        <div style={{ fontSize: 12, color: "var(--text-secondary)" }}>
          子代理: <span className="font-mono" style={{ fontWeight: 700, color: "var(--text-primary)" }}>{node.child_ids.length}</span> 个
        </div>
      )}
    </div>
  );
};

/** 空闲归因进度条 */
const IdleBar: React.FC<{ label: string; value: number; max: number; color: string }> = ({ label, value, max, color }) => {
  const pct = Math.min((value / max) * 100, 100);
  return (
    <div className="prism-meter">
      <span style={{ width: 96, flexShrink: 0, fontSize: 10, color: "var(--text-secondary)" }}>{label}</span>
      <div className="prism-meter__track">
        <div className="prism-meter__fill" style={{ width: `${pct}%`, background: color }} />
      </div>
      <span
        className="font-mono"
        style={{ width: 42, textAlign: "right", fontSize: 10, color: "var(--text-secondary)" }}
      >
        {value.toFixed(0)}
      </span>
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════════
// 子组件: TreeConnector — 树连接线
// ═══════════════════════════════════════════════════════════════════════════════

const TreeConnector: React.FC<{ depth: number; isLast: boolean }> = ({ depth, isLast }) => {
  if (depth === 0) return null;
  return (
    <span className="shrink-0 flex items-center" style={{ width: depth * 16 }}>
      {Array.from({ length: depth - 1 }).map((_, i) => (
        <span key={i} className="w-4 h-full flex items-center">
          <span style={{ width: 16, height: 1, background: "var(--border-soft)" }} />
        </span>
      ))}
      <span className={`w-4 flex items-center ${isLast ? "h-px" : "h-4"}`}>
        <span
          style={{
            width: 8,
            height: 1,
            background: "var(--text-tertiary)",
            transform: isLast ? undefined : "translateY(-50%)",
          }}
        />
      </span>
    </span>
  );
};

// ═══════════════════════════════════════════════════════════════════════════════
// 主组件: HandoffDelegationTree
// ═══════════════════════════════════════════════════════════════════════════════

export interface HandoffDelegationTreeProps {
  /** 委托树根节点 */
  tree: DelegationNode | null;
  /** 聚合指标 */
  metrics: HandoffObsMetrics;
  /** 当前选中的节点 ID */
  selectedNodeId: string | null;
  /** 节点选择回调 */
  onSelectNode: (nodeId: string | null) => void;
  /** 时间线回放时间戳 (ms, null = 实时) */
  replayTimestampMs: number | null;
  /** 依赖边 */
  dependencyEdges?: Array<{
    from_spawn_id: string;
    to_spawn_id: string;
    artifact_path: string;
    is_orphaned: boolean;
  }>;
}

export const HandoffDelegationTree: React.FC<HandoffDelegationTreeProps> = ({
  tree: rootNode,
  metrics,
  selectedNodeId,
  onSelectNode,
  replayTimestampMs,
  dependencyEdges = [],
}) => {
  const [showDetail, setShowDetail] = useState(true);

  // 构建内部树结构 + 展平行列表
  const { tree, flatRows } = useMemo(() => {
    if (!rootNode) return { tree: null as TreeNode | null, flatRows: [] as FlatRow[] };
    const built = buildTree(rootNode);
    return { tree: built, flatRows: flattenTree(built) };
  }, [rootNode]);

  const selectedNode = useMemo(() => {
    const effectiveSelectedNodeId = selectedNodeId ?? (showDetail ? tree?.node_id ?? null : null);
    if (!effectiveSelectedNodeId || !tree) return null;
    function find(n: TreeNode): TreeNode | null {
      if (n.node_id === effectiveSelectedNodeId) return n;
      for (const c of n.children) {
        const found = find(c);
        if (found) return found;
      }
      return null;
    }
    return find(tree);
  }, [tree, selectedNodeId, showDetail]);

  const effectiveSelectedNodeId = selectedNodeId ?? (showDetail ? tree?.node_id ?? null : null);

  const orphanCount = dependencyEdges.filter((e) => e.is_orphaned).length;

  // 空状态
  if (!tree) {
    return (
      <PrismPanel size="lg" grid shimmer style={{ textAlign: "center" }}>
        <div style={{ fontSize: 24, marginBottom: 6 }}>🕸️</div>
        <div style={{ fontSize: 14, color: "var(--text-primary)" }}>无委托数据</div>
        <div style={{ fontSize: 12, marginTop: 4, color: "var(--text-tertiary)" }}>
          等待 AgentSight 后端推送 `delegation_tree` 事件
        </div>
      </PrismPanel>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <h3 style={{ margin: 0, fontSize: 14, fontWeight: 600, color: "var(--text-primary)" }}>委托树</h3>
          <span className="prism-pill" style={{ minHeight: 26, padding: "4px 8px", fontFamily: "var(--font-geist-mono), monospace" }}>
            {metrics.total_spawned} 节点
          </span>
          {orphanCount > 0 && (
            <span className="prism-pill" data-tone="danger" style={{ minHeight: 26, padding: "4px 8px" }}>
              {orphanCount} 孤立
            </span>
          )}
          {replayTimestampMs && (
            <span className="prism-pill" data-tone="info" style={{ minHeight: 26, padding: "4px 8px" }}>
              回放 T+{(replayTimestampMs / 1000).toFixed(0)}s
            </span>
          )}
        </div>
        <span style={{ fontSize: 11, color: "var(--text-tertiary)" }}>
          {metrics.fan_out_critical ? (
            <span style={{ color: "var(--tone-danger-text)", fontWeight: 600, display: "inline-flex", alignItems: "center", gap: 5 }}>
              <AlertTriangle size={10} /> 扇出 {metrics.fan_out_ratio.toFixed(1)}:1
            </span>
          ) : (
            `扇出 ${metrics.fan_out_ratio.toFixed(1)}:1`
          )}
        </span>
      </div>

      <MetricsBar metrics={metrics} />

      <div className="prism-tree-shell">
        <div className="prism-tree-shell__inner prism-premium-scroll">
          {flatRows.map((row, i) => (
            <div key={row.node.node_id || i} className="flex" style={{ marginBottom: 6 }}>
              <TreeConnector depth={row.depth} isLast={row.isLastChild} />
              <div className="flex-1 min-w-0">
                <TreeNodeRow
                  node={row.node}
                  depth={row.depth}
                  isSelected={row.node.node_id === effectiveSelectedNodeId}
                  onSelect={(id) => {
                    onSelectNode(id);
                    setShowDetail(true);
                  }}
                />
              </div>
            </div>
          ))}
          {flatRows.length === 0 && (
            <div style={{ padding: 24, textAlign: "center", color: "var(--text-tertiary)", fontSize: 12 }}>
              委托树为空
            </div>
          )}
        </div>
      </div>

      {dependencyEdges.filter((e) => e.is_orphaned).length > 0 && (
        <div
          style={{
            padding: 14,
            borderRadius: 18,
            border: "1px solid var(--tone-warning-border)",
            background: "var(--tone-warning-bg)",
          }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 6,
              marginBottom: 8,
              fontSize: 12,
              fontWeight: 600,
              color: "var(--tone-warning-text)",
            }}
          >
            <AlertTriangle size={12} />
            孤立工作
          </div>
          <div style={{ display: "grid", gap: 6 }}>
            {dependencyEdges.filter((e) => e.is_orphaned).slice(0, 5).map((edge, i) => (
              <div
                key={i}
                className="font-mono truncate"
                style={{ fontSize: 11, color: "var(--tone-warning-text)" }}
              >
                {edge.from_spawn_id.slice(0, 8)} → {edge.to_spawn_id.slice(0, 8)}: {edge.artifact_path}
              </div>
            ))}
          </div>
        </div>
      )}

      {showDetail && selectedNode && (
        <NodeDetailPanel
          node={selectedNode}
          onClose={() => {
            setShowDetail(false);
            onSelectNode(null);
          }}
        />
      )}
    </div>
  );
};

export default HandoffDelegationTree;
